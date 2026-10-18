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
import contextlib
import os
import sys
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))  # govee_ble_local + ._generated (run tools/gen_kaitai.sh first)

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData

    from govee_ble_local.devices.device import Device
    from govee_ble_local.models import DeviceState
    from govee_ble_local.scenes import Scene

GroupResult = tuple[bool, str]  # (passed, note) from one Runner group


async def find_device(address: str | None) -> tuple[BLEDevice | None, AdvertisementData | None]:
    """The H60A6 to drive and its advertisement: by address, else the best-RSSI H60A6 from an
    8 s name scan. Shared by the other H60A6 probes (h60a6_cct_choreography,
    h60a6_segment_map_probe), so scan tuning for the flaky bench lives in one place."""
    from bleak import BleakScanner

    if address:
        dev = await BleakScanner.find_device_by_address(address, timeout=20.0)
        return dev, None
    from govee_ble_local.identify import sku_from_local_name

    found: dict[str, tuple[BLEDevice, AdvertisementData]] = {}

    def _cb(d: BLEDevice, adv: AdvertisementData) -> None:
        name = (adv.local_name or d.name or "")
        if sku_from_local_name(name) == "H60A6":
            found[d.address] = (d, adv)

    sc = BleakScanner(detection_callback=_cb)
    await sc.start()
    await asyncio.sleep(8.0)
    await sc.stop()
    if not found:
        return None, None
    d, adv = max(found.values(), key=lambda t: t[1].rssi)
    return d, adv


def _classify(scene: Scene) -> str:
    """H60A6 dialect for a catalog scene: 'static' | 'diy' (0xA3) | 'graffiti' (0xA4)."""
    if scene.scene_type == 0 or not scene.param:
        return "static"
    val = base64.b64decode(scene.param)[1:]
    if len(val) >= 2 and (val[0] | (val[1] << 8)) + 2 == len(val):
        return "diy"
    return "graffiti"


def _pick(catalog: Mapping[str, Scene], kind: str, prefer: str | None) -> str | None:
    if prefer and prefer in catalog and _classify(catalog[prefer]) == kind:
        return prefer
    for name, scene in catalog.items():
        if _classify(scene) == kind:
            return name
    return None


def _zone_is(idx: int, on: bool) -> Callable[[DeviceState], bool]:
    """refresh() predicate: zone ``idx`` reads back as ``on``."""
    return lambda s: s.zone_power.get(idx) is on


async def refresh(dev: Device, want: Callable[[DeviceState], bool] | None = None,
                  attempts: int = 5) -> DeviceState:
    """Read state back, retrying (the 0xAC burst is drop-prone) until `want(state)`."""
    for _ in range(attempts):
        try:
            await dev.update()
        except Exception as err:
            print(f"      (update error: {err})")
        if want is None or want(dev.state):
            return dev.state
        await asyncio.sleep(0.6)
    return dev.state


async def read_kelvin(dev: Device) -> int | None:
    """Kelvin from the mode read-back (aa 05 15 01 <kelvin u2be>), Kaitai-validated."""
    from govee_ble_local.devices.profile import profile_for, wire_profile
    from govee_ble_local.wire import _spec
    from govee_ble_local.wire import build as controllers

    # The H60A6's mode decode: 0x15 is SubMode4Color's [gradual][kelvin s16be] (gradual_kelvin).
    h60a6 = profile_for("H60A6")
    assert h60a6 is not None
    prof = wire_profile(h60a6)
    frames = await dev._conn.query(
        controllers.mode_query(), opcode=0xAA, terminal=0x05, timeout=3.0
    )
    for fr in frames:
        if len(fr) == 20 and fr[0] == 0xAA and fr[1] == 0x05:
            raw_kelvin = (fr[4] << 8) | fr[5]
            # Reply path: govee_ble_frame.body (read_reply) .state (govee_state) .body (mode_report)
            # with .sub_mode and .params (colour_report) .layout (gradual_kelvin for sub-mode 0x15).
            # No blanket except: an attribute-path mistake must fail loudly.
            try:
                parsed = _spec.read_frame(fr, _spec.RX, prof)
            except Exception as err:
                print(f"  (kaitai could not parse {fr.hex()}: {err}; skipping)")
                continue
            mode = parsed.body.state.body
            if mode.sub_mode != 0x15:
                continue
            kelvin = int(mode.params.layout.kelvin) & 0xFFFF
            if kelvin != raw_kelvin:
                raise AssertionError(f"kelvin mismatch: kaitai {kelvin} != raw {raw_kelvin}")
            return raw_kelvin
    return None


class Runner:
    def __init__(self, dev: Device) -> None:
        self.dev = dev
        self.results: list[tuple[str, bool, str]] = []

    async def group(self, name: str, factory: Callable[[], Awaitable[GroupResult]]) -> None:
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
                    with contextlib.suppress(Exception):
                        await self.dev.connect()
                    continue
                ok, note = False, f"EXC {type(err).__name__}: {err}"
            except Exception as err:
                ok, note = False, f"EXC {type(err).__name__}: {err}"
                break
        self.results.append((name, ok, note))
        print(f"  -> {'PASS' if ok else 'FAIL'}  {note}")

    # -- individual groups (return (ok, note)) --------------------------------
    async def power(self) -> GroupResult:
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

    async def brightness(self) -> GroupResult:
        dev = self.dev
        await dev.set_brightness(70)
        st = await refresh(dev, lambda s: s.brightness is not None)
        ok = st.brightness is not None and abs(st.brightness - 70) <= 8
        return ok, f"readback brightness={st.brightness}"

    async def rgb(self) -> GroupResult:
        dev = self.dev
        await dev.set_rgb((255, 0, 0))  # red, whole device
        st = await refresh(dev, lambda s: bool(s.segments))
        reds = [s.rgb for s in (st.segments or []) if s.rgb]
        ok = bool(reds) and all(r[0] >= 0x80 and r[1] < 0x60 and r[2] < 0x60 for r in reds)
        return ok, f"segments read back red: {reds[:3]}{'…' if len(reds) > 3 else ''}"

    async def kelvin(self) -> GroupResult:
        dev = self.dev
        notes: list[str] = []
        ok = True
        for k in (2700, 6500):
            await dev.set_color_temp(k)
            await asyncio.sleep(1.2)
            rk = await read_kelvin(dev)
            good = rk is not None and abs(rk - k) <= 350
            ok = ok and good
            notes.append(f"set {k}K -> readback {rk}K {'ok' if good else 'MISS'}")
            print(f"  {notes[-1]}  (OBSERVE: {'warm' if k == 2700 else 'cold'} white)")
        return ok, "; ".join(notes)

    async def zones(self) -> GroupResult:
        # Snapshot the specific zone bit right after each read (shared-state trap).
        # Predicate waits for the commanded value; if the parse cross-maps the two
        # zones it will time out and surface as a MISMATCH (a real finding, not a race).
        dev = self.dev
        notes: list[str] = []
        ok = True
        for zone, idx in (("background", 1), ("main", 0)):
            await dev.set_zone_power(zone, False)
            await refresh(dev, _zone_is(idx, False))
            off = dev.state.zone_power.get(idx)
            await dev.set_zone_power(zone, True)
            await refresh(dev, _zone_is(idx, True))
            on = dev.state.zone_power.get(idx)
            good = off is False and on is True
            ok = ok and good
            notes.append(f"{zone}(idx{idx}): off={off} on={on} {'ok' if good else 'MISMATCH'}")
            print(f"  {notes[-1]}  (OBSERVE which physical panel toggles)")
        return ok, "; ".join(notes)

    async def segments(self) -> GroupResult:
        """Address ALL segments: set each of the 13 individually to a cycling R/G/B
        colour, then read every segment back and verify its dominant channel."""
        dev = self.dev
        n = dev.profile.segments
        palette = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        await dev.set_brightness(80)
        for i in range(n):
            await dev.set_segment_rgb([i], palette[i % 3])  # mask = just segment i
            await asyncio.sleep(0.15)

        def dom(c: tuple[int, int, int]) -> int:
            return max(range(3), key=lambda k: c[k])

        # All n set_segment_rgb calls above are ACK'd writes (raise on failure), so
        # reaching here means every segment was individually addressed. The read-back
        # (0xA5 colour-group TLV walker) should now surface all n segments.
        await refresh(dev, lambda s: len(s.segments or []) >= n)
        segs = dev.state.segments or []
        by_index = {s.index: s.rgb for s in segs}  # keyed by Segment.index, not list position
        mism: list[tuple[int, tuple[int, int, int], tuple[int, int, int] | None]] = []
        for i in range(n):
            want, got = palette[i % 3], by_index.get(i)
            if got is None or dom(got) != dom(want):
                mism.append((i, want, got))
        ok = len(by_index) >= n and not mism
        print(f"  (OBSERVE: all {n} segments show a repeating R,G,B,… pattern)")
        detail = f"set all {n} (acked); read-back {len(by_index)}/{n} colours match"
        if mism:
            detail += f"; mismatch={mism}"
        return ok, detail

    async def scene(self, name: str, kind: str, hold: float = 5.0) -> GroupResult:
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

    dev_ble, adv = await find_device(args.address)
    if dev_ble is None:
        print("H60A6 not found (set GOVEE_H60A6_ADDRESS or ensure it's advertising).")
        return 2

    from govee_ble_local.registry import create_device
    from govee_ble_local.scenes import load_scenes

    # idle_disconnect=0 keeps the ONE connection alive for the whole run
    dev = create_device(dev_ble, "H60A6", advertisement_data=adv, idle_disconnect=0,
                        frame_log=args.capture)
    print(f"Connecting to H60A6 {dev.address} …")
    await dev.connect()
    print(f"Connected. encryption={dev.encryption.value}"
          + (f"  capture -> {args.capture}" if args.capture else ""))

    catalog = load_scenes("H60A6")
    graffiti = _pick(catalog, "graffiti", "Aurora")
    diy = _pick(catalog, "diy", "Christmas")
    static = _pick(catalog, "static", None)

    r = Runner(dev)
    try:
        await r.group("full-device on/off", r.power)
        await r.group("brightness", r.brightness)
        await r.group("RGB (whole device)", r.rgb)
        await r.group("kelvin hi/lo", r.kelvin)
        await r.group("zones (main + background)", r.zones)
        await r.group("segment control (all 13)", r.segments)
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
        await dev.stop()

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
