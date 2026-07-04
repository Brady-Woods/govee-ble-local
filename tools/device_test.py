#!/usr/bin/env python3
"""Real-device functional test suite for a Govee BLE device.

Exercises everything the device's profile says it supports, in one of two modes:

  --mode auto         Send commands and verify via status read-back. Zones,
                      brightness, scene, segments, and identity read back
                      directly; RGB and color temp are verified through the
                      per-segment color data (with_segments read-back), falling
                      back to INCONCLUSIVE only when those chunks drop on a poll.
                      No human needed.

  --mode interactive  Drive each capability and ask a human to confirm what
                      they see on the physical device.

Devices come from a live scan (works cross-platform, incl. macOS where BLE
MACs aren't exposed): the suite scrapes each Govee advertisement (local name,
RSSI, manufacturer data) and matches it to a device profile. Selection:

  - interactive mode  -> prompts you to pick one device (needs a TTY).
  - auto mode         -> tests the strongest-signal device from EACH supported
                         model in range (a full sweep of every distinct model),
                         unless narrowed with --pick/--sku.

Examples:
    python3 tools/device_test.py --scan                     # list candidates, exit
    python3 tools/device_test.py --mode auto                # sweep best-signal per model
    python3 tools/device_test.py --pick 0 --mode auto       # just candidate 0
    python3 tools/device_test.py --sku H60A6 --mode interactive  # prompt to pick
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from bleak import BleakScanner  # noqa: E402

from govee_ble_local import GoveeBleClient, profile as profile_mod  # noqa: E402
from govee_ble_local.const import ZONE_LOWER, ZONE_UPPER  # noqa: E402
from govee_ble_local.profile import DeviceProfile  # noqa: E402

PASS, FAIL, INCONCLUSIVE, SKIP = "PASS", "FAIL", "INCONCLUSIVE", "SKIP"
_SETTLE = 1.2  # seconds to let a command take effect before reading back


@dataclass
class Result:
    name: str
    status: str
    detail: str = ""


def _confirm(prompt: str) -> bool:
    return input(f"    ?? {prompt} [y/N] ").strip().lower() in ("y", "yes")


# --------------------------------------------------------------------------
# Automated checks (verify via read-back where the protocol allows)
# --------------------------------------------------------------------------


async def auto_identity(c: GoveeBleClient, p: DeviceProfile) -> Result:
    st = await c.get_status()
    ok = st.ble_mac is not None and st.hardware_version is not None
    return Result("identity", PASS if ok else FAIL, f"mac={st.ble_mac} hw={st.hardware_version}")


async def auto_serial(c: GoveeBleClient, p: DeviceProfile) -> Result:
    serial = await c.get_serial_number()
    return Result("serial", PASS if serial else INCONCLUSIVE, f"serial={serial}")


async def auto_brightness(c: GoveeBleClient, p: DeviceProfile) -> Result:
    for target in (30, 80):
        await c.set_brightness_pct(target)
        await asyncio.sleep(_SETTLE)
        st = await c.get_status()
        if st.brightness_pct != target:
            return Result("brightness", FAIL, f"set {target}%, read {st.brightness_pct}%")
    return Result("brightness", PASS, "read-back matched set values (30, 80)")


async def auto_zones(c: GoveeBleClient, p: DeviceProfile) -> Result:
    for upper in (False, True):
        for lower in (False, True):
            await c.set_zone(ZONE_UPPER, upper)
            await c.set_zone(ZONE_LOWER, lower)
            await asyncio.sleep(_SETTLE)
            st = await c.get_status()
            if st.zone_upper_on != upper or st.zone_lower_on != lower:
                return Result("zones", FAIL,
                              f"set U={upper} L={lower}, read U={st.zone_upper_on} L={st.zone_lower_on}")
    return Result("zones", PASS, "all 4 upper/lower states read back correctly")


async def auto_scene(c: GoveeBleClient, p: DeviceProfile) -> Result:
    scenes = p.selectable_scenes()
    if not scenes:
        return Result("scene", SKIP, "no selectable scenes")
    scene = scenes[0]
    if scene.param:
        await c.set_scene_full(scene.code, scene.param)
    else:
        await c.set_scene(scene.scene_id)
    await asyncio.sleep(_SETTLE)
    st = await c.get_status()
    ok = st.scene_id == scene.scene_id
    return Result("scene", PASS if ok else FAIL, f"{scene.name}: set {scene.scene_id}, read {st.scene_id}")


async def _read_rgb(c: GoveeBleClient, retries: int = 5) -> tuple[int, int, int] | None:
    """Read the solid RGB back via the per-segment data (retry: chunks drop)."""
    for _ in range(retries):
        st = await c.get_status(with_segments=True)
        if st.rgb_color is not None:
            return st.rgb_color
        await asyncio.sleep(1.0)
    return None


async def auto_rgb(c: GoveeBleClient, p: DeviceProfile) -> Result:
    await c.set_rgb_color(255, 0, 0)
    await asyncio.sleep(_SETTLE)
    rgb = await _read_rgb(c)
    if rgb is None:
        return Result("rgb", INCONCLUSIVE, "segment chunks dropped; couldn't read color back")
    return Result("rgb", PASS if rgb == (255, 0, 0) else FAIL, f"set (255,0,0), read {rgb}")


async def auto_color_temp(c: GoveeBleClient, p: DeviceProfile) -> Result:
    # No Kelvin read-back exists, but the rendered tint shows up as segment RGB.
    # Verify the tint moves the right way: warm has less blue than cool.
    lo, hi = p.capabilities.color_temp
    await c.set_color_temp_kelvin(lo)
    await asyncio.sleep(_SETTLE)
    warm = await _read_rgb(c)
    await c.set_color_temp_kelvin(hi)
    await asyncio.sleep(_SETTLE)
    cool = await _read_rgb(c)
    if warm is None or cool is None:
        return Result("color_temp", INCONCLUSIVE, "segment chunks dropped; couldn't read tint back")
    ok = cool[2] > warm[2]  # cooler temperature -> more blue
    return Result("color_temp", PASS if ok else FAIL, f"warm={warm} cool={cool} (expect cool bluer)")


async def auto_segments(c: GoveeBleClient, p: DeviceProfile) -> Result:
    await c.set_segment_color(1 << 0, 255, 0, 0)
    await c.set_segment_brightness(1 << 0, 50)
    await asyncio.sleep(_SETTLE)
    for _ in range(5):  # segment chunks drop often; retry a few times
        st = await c.get_status(with_segments=True)
        if st.segments:
            seg = st.segments[0]
            ok = (seg.r, seg.g, seg.b) == (255, 0, 0) and abs(seg.brightness_pct - 50) <= 1
            return Result("segments", PASS if ok else FAIL,
                          f"seg0 read bri={seg.brightness_pct} rgb=({seg.r},{seg.g},{seg.b})")
        await asyncio.sleep(1.0)
    return Result("segments", INCONCLUSIVE, "segment chunks dropped on all polls (BLE contention)")


# --------------------------------------------------------------------------
# Interactive checks (human confirms the physical result)
# --------------------------------------------------------------------------


async def ix_brightness(c: GoveeBleClient, p: DeviceProfile) -> Result:
    await c.set_zone(ZONE_UPPER, True)
    await c.set_zone(ZONE_LOWER, True)
    await c.set_brightness_pct(15)
    await asyncio.sleep(_SETTLE)
    dim = _confirm("Light is now DIM?")
    await c.set_brightness_pct(100)
    await asyncio.sleep(_SETTLE)
    bright = _confirm("Light is now BRIGHT?")
    return Result("brightness", PASS if dim and bright else FAIL, "")


async def ix_rgb(c: GoveeBleClient, p: DeviceProfile) -> Result:
    oks = []
    for name, (r, g, b) in (("RED", (255, 0, 0)), ("GREEN", (0, 255, 0)), ("BLUE", (0, 0, 255))):
        await c.set_rgb_color(r, g, b)
        await asyncio.sleep(_SETTLE)
        oks.append(_confirm(f"Light is {name}?"))
    return Result("rgb", PASS if all(oks) else FAIL, "")


async def ix_color_temp(c: GoveeBleClient, p: DeviceProfile) -> Result:
    lo, hi = p.capabilities.color_temp
    await c.set_color_temp_kelvin(lo)
    await asyncio.sleep(_SETTLE)
    warm = _confirm(f"Light is WARM/orange (~{lo}K)?")
    await c.set_color_temp_kelvin(hi)
    await asyncio.sleep(_SETTLE)
    cool = _confirm(f"Light is COOL/blue-white (~{hi}K)?")
    return Result("color_temp", PASS if warm and cool else FAIL, "")


async def ix_zones(c: GoveeBleClient, p: DeviceProfile) -> Result:
    await c.set_zone(ZONE_UPPER, True)
    await c.set_zone(ZONE_LOWER, False)
    await asyncio.sleep(_SETTLE)
    upper_only = _confirm("ONLY the upper ring is lit?")
    await c.set_zone(ZONE_UPPER, False)
    await c.set_zone(ZONE_LOWER, True)
    await asyncio.sleep(_SETTLE)
    lower_only = _confirm("ONLY the lower panel is lit?")
    return Result("zones", PASS if upper_only and lower_only else FAIL, "")


async def ix_segments(c: GoveeBleClient, p: DeviceProfile) -> Result:
    await c.set_segment_brightness((1 << p.capabilities.segments) - 1, 100)
    await c.set_segment_color((1 << 0), 255, 0, 0)
    await asyncio.sleep(_SETTLE)
    ok = _confirm("A single segment turned RED (others unchanged)?")
    return Result("segments", PASS if ok else FAIL, "")


async def ix_scene(c: GoveeBleClient, p: DeviceProfile) -> Result:
    scenes = p.selectable_scenes()
    if not scenes:
        return Result("scene", SKIP, "no selectable scenes")
    scene = scenes[0]
    if scene.param:
        await c.set_scene_full(scene.code, scene.param)
    else:
        await c.set_scene(scene.scene_id)
    await asyncio.sleep(_SETTLE)
    ok = _confirm(f"The '{scene.name}' scene is playing?")
    return Result("scene", PASS if ok else FAIL, "")


# capability -> (guard, auto_fn, interactive_fn)
CHECKS = [
    ("identity", lambda p: True, auto_identity, None),
    ("serial", lambda p: True, auto_serial, None),
    ("brightness", lambda p: p.capabilities.brightness, auto_brightness, ix_brightness),
    ("rgb", lambda p: p.capabilities.rgb, auto_rgb, ix_rgb),
    ("color_temp", lambda p: p.capabilities.color_temp is not None, auto_color_temp, ix_color_temp),
    ("zones", lambda p: bool(p.capabilities.zones), auto_zones, ix_zones),
    ("segments", lambda p: p.capabilities.segments > 0, auto_segments, ix_segments),
    ("scenes", lambda p: p.capabilities.scenes, auto_scene, ix_scene),
]


async def restore(c: GoveeBleClient, p: DeviceProfile) -> None:
    """Leave the device in a sane, on, neutral state."""
    try:
        if p.capabilities.zones:
            await c.set_zone(ZONE_UPPER, True)
            await c.set_zone(ZONE_LOWER, True)
        await c.set_brightness_pct(100)
        if p.capabilities.color_temp:
            await c.set_color_temp_kelvin(3500)
    except Exception:  # noqa: BLE001 - best-effort cleanup
        pass


def _describe(idx: int, dev, adv, name: str, prof) -> str:
    sku = prof.sku if prof else "unknown"
    mfg = ",".join(f"{cid:#06x}={data.hex()}" for cid, data in (adv.manufacturer_data or {}).items())
    return f"  [{idx}] {name:<14} sku={sku:<8} rssi={adv.rssi} id={dev.address} mfg=[{mfg}]"


async def scan_candidates(timeout: float):
    """Scan and return Govee candidates as (device, adv, name, profile) tuples."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    candidates = []
    for _addr, (dev, adv) in found.items():
        name = adv.local_name or dev.name
        if not name:
            continue
        prof = profile_mod.match_local_name(name)
        if prof is None and not name.upper().startswith("GV"):
            continue
        candidates.append((dev, adv, name, prof))
    candidates.sort(key=lambda c: (c[1].rssi if c[1].rssi is not None else -999), reverse=True)
    return candidates


async def resolve_targets(args: argparse.Namespace):
    """Scan, list candidates, and select which device(s) to test.

    - ``--pick N`` / ``--sku X``: one specific device.
    - interactive mode (``--mode interactive``): prompt for one (needs a TTY).
    - otherwise (non-interactive/auto): the best-signal device from *each*
      supported model, so a full run covers every distinct Govee model in range.

    Returns a list of (device, profile). Empty means nothing to do.
    """
    print(f"Scanning {args.scan_timeout:.0f}s for Govee devices...")
    candidates = await scan_candidates(args.scan_timeout)  # already RSSI-desc sorted
    if not candidates:
        print("No Govee devices advertising nearby.")
        return []
    print(f"Found {len(candidates)} candidate(s):")
    for i, (dev, adv, name, prof) in enumerate(candidates):
        print(_describe(i, dev, adv, name, prof))
    if args.scan:
        return []

    # Explicit index wins, in any mode.
    if args.pick is not None:
        if not 0 <= args.pick < len(candidates):
            print(f"Invalid index {args.pick}")
            return []
        dev, _adv, name, prof = candidates[args.pick]
        prof = profile_mod.load_by_sku(args.sku) if args.sku else prof
        if prof is None:
            print(f"No profile matched {name!r}; pass --sku.")
            return []
        return [(dev, prof)]

    supported = [(dev, adv, name, prof) for (dev, adv, name, prof) in candidates if prof is not None]

    # A specific SKU: best-signal device advertising that model.
    if args.sku:
        prof = profile_mod.load_by_sku(args.sku)
        if prof is None:
            print(f"Unknown --sku {args.sku!r}")
            return []
        matching = [c for c in supported if c[3].sku.casefold() == args.sku.casefold()]
        if not matching:
            print(f"No advertising device matched --sku {args.sku}.")
            return []
        best = matching[0]  # candidates are RSSI-desc, so the first is strongest
        return [(best[0], prof)]

    # Interactive: prompt for a single device.
    if args.mode == "interactive":
        if not sys.stdin.isatty():
            print("Interactive mode needs a TTY — pass --pick <index> or --sku <model>.")
            return []
        idx = int(input("Select device index: ").strip())
        if not 0 <= idx < len(candidates):
            print(f"Invalid index {idx}")
            return []
        dev, _adv, name, prof = candidates[idx]
        if prof is None:
            print(f"No profile matched {name!r}; pass --sku.")
            return []
        return [(dev, prof)]

    # Non-interactive (auto): the best-signal device per supported model.
    if not supported:
        print("No devices matching a known profile in range (auto mode tests supported models only).")
        return []
    best_by_sku: dict[str, tuple] = {}
    for dev, _adv, _name, prof in supported:
        best_by_sku.setdefault(prof.sku, (dev, prof))  # first per SKU = strongest RSSI
    targets = list(best_by_sku.values())
    print(f"\nAuto-selected the strongest device for {len(targets)} supported model(s): "
          f"{', '.join(sorted(best_by_sku))}")
    return targets


async def test_one(device, prof: DeviceProfile, args: argparse.Namespace) -> tuple[int, int]:
    """Run the capability suite against one device. Returns (fail, inconclusive)."""
    print(f"\n=== Testing {prof.name} ({prof.sku}) via {device.address} — mode={args.mode} ===\n")
    client = GoveeBleClient(device)
    results: list[Result] = []
    try:
        for name, guard, auto_fn, ix_fn in CHECKS:
            if not guard(prof):
                results.append(Result(name, SKIP, "not in device capabilities"))
                continue
            fn = auto_fn if args.mode == "auto" else (ix_fn or auto_fn)
            print(f"-> {name}")
            try:
                res = await fn(client, prof)
            except Exception as err:  # noqa: BLE001
                res = Result(name, FAIL, f"exception: {err}")
            results.append(res)
            print(f"   {res.status}: {res.detail}" if res.detail else f"   {res.status}")
        await restore(client, prof)
    finally:
        await client.disconnect()

    print(f"\n--- {prof.sku} summary ---")
    width = max(len(r.name) for r in results)
    for r in results:
        print(f"  {r.name:<{width}}  {r.status}")
    tally = {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, INCONCLUSIVE, SKIP)}
    print(f"  {tally[PASS]} pass, {tally[FAIL]} fail, {tally[INCONCLUSIVE]} inconclusive, {tally[SKIP]} skip")
    return tally[FAIL], tally[INCONCLUSIVE]


async def run(args: argparse.Namespace) -> int:
    targets = await resolve_targets(args)
    if args.scan:
        return 0
    if not targets:
        return 2

    total_fail = 0
    per_device: list[tuple[str, int, int]] = []
    for device, prof in targets:
        fail, inconclusive = await test_one(device, prof, args)
        total_fail += fail
        per_device.append((f"{prof.sku} ({device.address})", fail, inconclusive))

    if len(per_device) > 1:
        print("\n=== overall ===")
        for label, fail, inconclusive in per_device:
            verdict = "FAIL" if fail else ("INCONCLUSIVE" if inconclusive else "PASS")
            print(f"  {label:<28} {verdict}  ({fail} fail, {inconclusive} inconclusive)")
    return 1 if total_fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sku", default=None, help="device SKU (else matched from advertised name)")
    ap.add_argument("--mode", choices=["auto", "interactive"], default="auto")
    ap.add_argument("--scan", action="store_true", help="scan, list candidates, and exit")
    ap.add_argument("--pick", type=int, default=None, help="select candidate index non-interactively")
    ap.add_argument("--scan-timeout", type=float, default=10.0, dest="scan_timeout")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
