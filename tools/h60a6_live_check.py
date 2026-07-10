#!/usr/bin/env python3
"""Comprehensive live H60A6 command check — ONE persistent connection.

Unlike the per-test pytest suite (which reconnected between tests), this establishes
a single BLE session (idle-disconnect disabled) and keeps it alive for the whole run,
driving every command group through the real device object (so it exercises the actual
library code paths, incl. the new dialect-B scene upload). Each step relies on the
built-in write ACK (a 0x33 send raises if the device doesn't ack) and then reads state
back via update() (0xAC status burst + mode read) to confirm the change took.

Groups: full-device on/off · brightness · RGB · kelvin hi/lo · main+background zones ·
segments · all scene dialects (graffiti 0xA4, DIY 0xA3, static/activate-only).

    GOVEE_H60A6_ADDRESS=AA:BB:CC:DD:EE:FF python3 tools/h60a6_live_check.py [--capture frames.jsonl]
    python3 tools/h60a6_live_check.py --address AA:BB:CC:DD:EE:FF   # else best-RSSI H60A6 scan

Scenes only confirm ACK + scene_code read-back (the activation is accepted); whether the
uploaded effect actually RENDERS is visual — watch the light when a step prints OBSERVE.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO / "tests"))  # spec_gen.govee_ble_frame (generated reader)


async def find_device(address: str | None):
    from bleak import BleakScanner

    if address:
        dev = await BleakScanner.find_device_by_address(address, timeout=20.0)
        return dev, None
    from govee_ble_local.identify import sku_from_local_name

    found: dict[str, tuple] = {}

    def _cb(d, adv):  # type: ignore[no-untyped-def]
        name = (adv.local_name or d.name or "")
        if sku_from_local_name(name) == "H60A6":
            found[d.address] = (d, adv)

    sc = BleakScanner(detection_callback=_cb)
    await sc.start()
    await asyncio.sleep(8.0)
    await sc.stop()
    if not found:
        return None, None
    d, adv = max(found.values(), key=lambda t: (t[1].rssi if t[1] else -999))
    return d, adv


def _classify(scene) -> str:  # type: ignore[no-untyped-def]
    """H60A6 dialect for a catalog scene: 'static' | 'diy' (0xA3) | 'graffiti' (0xA4)."""
    if scene.scene_type == 0 or not scene.param:
        return "static"
    val = base64.b64decode(scene.param)[1:]
    if len(val) >= 2 and (val[0] | (val[1] << 8)) + 2 == len(val):
        return "diy"
    return "graffiti"


def _pick(catalog, kind: str, prefer: str | None) -> str | None:  # type: ignore[no-untyped-def]
    if prefer and prefer in catalog and _classify(catalog[prefer]) == kind:
        return prefer
    for name, scene in catalog.items():
        if _classify(scene) == kind:
            return name
    return None


async def refresh(dev, want=None, attempts: int = 5):  # type: ignore[no-untyped-def]
    """Read state back, retrying (the 0xAC burst is drop-prone) until `want(state)`."""
    for _ in range(attempts):
        try:
            await dev.update()
        except Exception as err:  # noqa: BLE001
            print(f"      (update error: {err})")
        if want is None or want(dev.state):
            return dev.state
        await asyncio.sleep(0.6)
    return dev.state


async def read_kelvin(dev) -> int | None:  # type: ignore[no-untyped-def]
    """Kelvin from the mode read-back (aa 05 15 01 <kelvin u2be>), Kaitai-validated."""
    from govee_ble_local.ble import controllers

    try:
        from spec_gen.govee_ble_frame import GoveeBleFrame as GBF
    except Exception:
        GBF = None
    frames = await dev._connection.query(
        controllers.mode_query(), opcode=0xAA, terminal=0x05, timeout=3.0
    )
    for fr in frames:
        if len(fr) == 20 and fr[0] == 0xAA and fr[1] == 0x05:
            if GBF is not None:
                try:
                    m = GBF.from_bytes(fr).body.params
                    if (m.sub_type != GBF.SubMode.color_rgbic_15
                            or m.params.op_type != GBF.Op15.set_color):
                        continue
                except Exception:
                    pass
            return (fr[4] << 8) | fr[5]
    return None


class Runner:
    def __init__(self, dev) -> None:  # type: ignore[no-untyped-def]
        self.dev = dev
        self.results: list[tuple[str, bool, str]] = []

    async def group(self, name: str, factory) -> None:  # type: ignore[no-untyped-def]
        """Run a group (a zero-arg coroutine factory). On a BLE error, reconnect
        and retry once — so a single mid-run link drop doesn't fail the group."""
        from govee_ble_local.exceptions import GoveeBleError

        print(f"\n=== {name} ===")
        ok, note = False, ""
        for attempt in (1, 2):
            try:
                ok, note = await factory()
                break
            except GoveeBleError as err:
                if attempt == 1:
                    print(f"  (recovering from {type(err).__name__}; reconnecting…)")
                    try:
                        await self.dev._connection.connect()
                    except Exception:  # noqa: BLE001
                        pass
                    continue
                ok, note = False, f"EXC {type(err).__name__}: {err}"
            except Exception as err:  # noqa: BLE001
                ok, note = False, f"EXC {type(err).__name__}: {err}"
                break
        self.results.append((name, ok, note))
        print(f"  -> {'PASS' if ok else 'FAIL'}  {note}")

    # -- individual groups (return (ok, note)) --------------------------------
    async def power(self):  # type: ignore[no-untyped-def]
        # NB: refresh() returns the shared, mutable dev.state — snapshot the
        # primitive immediately after each read, never hold the state object.
        dev = self.dev
        await dev.set_power(True)
        await refresh(dev, lambda s: s.is_on is True)
        on1 = dev.state.is_on
        await dev.set_power(False)
        await refresh(dev, lambda s: s.is_on is False)
        off = dev.state.is_on
        await dev.set_power(True)  # leave ON for the rest
        await refresh(dev, lambda s: s.is_on is True)
        on2 = dev.state.is_on
        ok = on1 is True and off is False and on2 is True
        print("  (OBSERVE: whole light off then back on)")
        return ok, f"on={on1} off={off} on={on2}"

    async def brightness(self):  # type: ignore[no-untyped-def]
        dev = self.dev
        await dev.set_brightness(70)
        st = await refresh(dev, lambda s: s.brightness is not None)
        ok = st.brightness is not None and abs(st.brightness - 70) <= 8
        return ok, f"readback brightness={st.brightness}"

    async def rgb(self):  # type: ignore[no-untyped-def]
        dev = self.dev
        await dev.set_rgb((255, 0, 0))  # red, whole device
        st = await refresh(dev, lambda s: bool(s.segments))
        reds = [s.rgb for s in (st.segments or []) if s.rgb]
        ok = bool(reds) and all(r[0] >= 0x80 and r[1] < 0x60 and r[2] < 0x60 for r in reds)
        return ok, f"segments read back red: {reds[:3]}{'…' if len(reds) > 3 else ''}"

    async def kelvin(self):  # type: ignore[no-untyped-def]
        dev = self.dev
        notes, ok = [], True
        for k in (2700, 6500):
            await dev.set_color_temp(k)
            await asyncio.sleep(1.2)
            rk = await read_kelvin(dev)
            good = rk is not None and abs(rk - k) <= 350
            ok = ok and good
            notes.append(f"set {k}K -> readback {rk}K {'ok' if good else 'MISS'}")
            print(f"  {notes[-1]}  (OBSERVE: {'warm' if k == 2700 else 'cold'} white)")
        return ok, "; ".join(notes)

    async def zones(self):  # type: ignore[no-untyped-def]
        # Snapshot the specific zone bit right after each read (shared-state trap).
        # Predicate waits for the commanded value; if the parse cross-maps the two
        # zones it will time out and surface as a MISMATCH (a real finding, not a race).
        dev = self.dev
        notes, ok = [], True
        for zone, idx in (("background", 1), ("main", 0)):
            await dev.set_zone_power(zone, False)
            await refresh(dev, lambda s, i=idx: s.zone_power.get(i) is False)
            off = dev.state.zone_power.get(idx)
            await dev.set_zone_power(zone, True)
            await refresh(dev, lambda s, i=idx: s.zone_power.get(i) is True)
            on = dev.state.zone_power.get(idx)
            good = off is False and on is True
            ok = ok and good
            notes.append(f"{zone}(idx{idx}): off={off} on={on} {'ok' if good else 'MISMATCH'}")
            print(f"  {notes[-1]}  (OBSERVE which physical panel toggles)")
        return ok, "; ".join(notes)

    async def segments(self):  # type: ignore[no-untyped-def]
        """Address ALL segments: set each of the 13 individually to a cycling R/G/B
        colour, then read every segment back and verify its dominant channel."""
        dev = self.dev
        n = dev._segments
        palette = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        await dev.set_brightness(80)
        for i in range(n):
            await dev.set_segment_rgb([i], palette[i % 3])  # mask = just segment i
            await asyncio.sleep(0.15)

        def dom(c):  # type: ignore[no-untyped-def]
            return max(range(3), key=lambda k: c[k]) if c else None

        # All n set_segment_rgb calls above are ACK'd writes (raise on failure), so
        # reaching here means every segment was individually addressed. The read-back
        # (0xA5 colour-group TLV walker) should now surface all n segments.
        await refresh(dev, lambda s: len(s.segments or []) >= n)
        segs = dev.state.segments or []
        mism = [(i, palette[i % 3], segs[i].rgb if i < len(segs) else None)
                for i in range(n)
                if i >= len(segs) or segs[i].rgb is None or dom(segs[i].rgb) != dom(palette[i % 3])]
        ok = len(segs) >= n and not mism
        print(f"  (OBSERVE: all {n} segments show a repeating R,G,B,… pattern)")
        detail = f"set all {n} (acked); read-back {len(segs)}/{n} colours match"
        if mism:
            detail += f"; mismatch={mism}"
        return ok, detail

    async def scene(self, name: str, kind: str, hold: float = 5.0):  # type: ignore[no-untyped-def]
        dev = self.dev
        from govee_ble_local.scenes import load_scenes

        code = load_scenes(dev.sku)[name].code
        await dev.set_scene_by_name(name)  # uploads (dialect-routed) then activates
        print(f"  >>> OBSERVE for {hold:.0f}s: does {name!r} [{kind}] actually RENDER? <<<")
        await asyncio.sleep(hold)
        await refresh(dev, lambda s: s.scene_code == code)
        ok = dev.state.scene_code == code
        return ok, f"activated {name!r} code={code}; scene_code read back={dev.state.scene_code}"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", default=os.environ.get("GOVEE_H60A6_ADDRESS"))
    ap.add_argument("--capture", help="write bidirectional frame log (JSONL) here")
    args = ap.parse_args()

    if args.capture:
        os.environ["GOVEE_FRAME_LOG"] = args.capture  # read at connection construction

    dev_ble, adv = await find_device(args.address)
    if dev_ble is None:
        print("H60A6 not found (set GOVEE_H60A6_ADDRESS or ensure it's advertising).")
        return 2

    from govee_ble_local.registry import create_device
    from govee_ble_local.scenes import load_scenes

    dev = create_device(dev_ble, "H60A6", advertisement_data=adv)
    dev._connection._idle_disconnect = 0  # keep the ONE connection alive for the whole run
    print(f"Connecting to H60A6 {dev.address} …")
    await dev._connection.connect()
    print(f"Connected. encryption={dev._connection._encryption.value}"
          + (f"  capture -> {args.capture}" if args.capture else ""))

    catalog = load_scenes("H60A6")
    graffiti = _pick(catalog, "graffiti", "Aurora")
    diy = _pick(catalog, "diy", "Christmas")
    static = _pick(catalog, "static", None)

    r = Runner(dev)
    try:
        await r.group("full-device on/off", lambda: r.power())
        await r.group("brightness", lambda: r.brightness())
        await r.group("RGB (whole device)", lambda: r.rgb())
        await r.group("kelvin hi/lo", lambda: r.kelvin())
        await r.group("zones (main + background)", lambda: r.zones())
        await r.group("segment control (all 13)", lambda: r.segments())
        # Scene dialects the H60A6 actually uses: dialect B (graffiti 0xA4 + DIY 0xA3)
        # and static. (Dialect A / comType 1/2/7/10 is a different SKU's path — the
        # H60A6 has no dialect-A scenes — so it can't be exercised here.)
        if graffiti:  # Aurora — historically broken; give it a long render hold.
            await r.group(f"scene dialect-B graffiti/0xA4 ({graffiti})",
                          lambda: r.scene(graffiti, "0xA4", hold=8.0))
        if diy:
            await r.group(f"scene dialect-B DIY/0xA3 ({diy})", lambda: r.scene(diy, "0xA3"))
        if static:
            await r.group(f"scene static/activate-only ({static})", lambda: r.scene(static, "static"))
    finally:
        await dev._connection.disconnect()

    print("\n" + "=" * 60 + "\nSUMMARY")
    for name, ok, note in r.results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:40s} {note}")
    failed = [n for n, ok, _ in r.results if not ok]
    print(f"\n{len(r.results) - len(failed)}/{len(r.results)} groups passed"
          + (f"; FAILED: {failed}" if failed else ""))
    print("NOTE: H60A6 covers scene dialects B (0xA4 graffiti, 0xA3 DIY) + static; "
          "dialect A needs another SKU (e.g. H61A8).")
    print("NOTE: scene groups confirm ACK + scene_code read-back — RENDER is the visual "
          "you observe during the hold.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
