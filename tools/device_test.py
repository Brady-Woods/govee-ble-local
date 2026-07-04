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

When testing finishes (any mode), each device is either restored to how it
was found (``--after restore``, the default - captured before any commands
are sent) or turned off (``--after off``). Restoration is best-effort: color
is only knowable when segments read back a uniform solid, so an active scene
is restored by re-activating its scene id instead of the solid color path.

Examples:
    python3 tools/device_test.py --scan                     # list candidates, exit
    python3 tools/device_test.py --mode auto                # sweep best-signal per model
    python3 tools/device_test.py --pick 0 --mode auto       # just candidate 0
    python3 tools/device_test.py --sku H60A6 --mode interactive  # prompt to pick
    python3 tools/device_test.py --mode auto --after off    # turn devices off when done
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
from govee_ble_local.models import GoveeBleStatus, uniform_rgb  # noqa: E402
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


def _status_guard(p: DeviceProfile, name: str) -> Result | None:
    """Returns a SKIP Result if `name` needs status_scheme='full' read-back
    and this device doesn't have it - the SET side of the underlying
    capability may still work fine (segment/rgb checks have their own
    protocol-aware read-back path instead; this guard is only for checks
    with no alternate verification, e.g. identity/brightness/scene). Called
    explicitly rather than folded into CHECKS' guard= lambdas because those
    gate on *capability* (does this even apply), not *read-back mechanism*
    (can we verify it) - conflating the two would incorrectly skip a
    capability that's real and sendable, just not read-back-verifiable here.
    """
    if p.protocol.status_scheme != "full":
        return Result(name, SKIP, f"status readback unsupported (status_scheme={p.protocol.status_scheme!r}) - use --mode interactive")
    return None


async def auto_power(c: GoveeBleClient, p: DeviceProfile) -> Result:
    """Global on/off (`set_power`) for devices without zones - e.g. H5083,
    a smart plug whose entire function is this opcode. Zoned devices (H60A6)
    exercise power via the zones check instead. No status_scheme currently
    reads global on/off state back (GoveeBleStatus only has zone_upper_on/
    zone_lower_on), so this always sends the real command but can only
    report PASS/FAIL for ack receipt, not confirmed device state."""
    await c.set_power(False)
    await asyncio.sleep(_SETTLE)
    await c.set_power(True)
    await asyncio.sleep(_SETTLE)
    return Result("power", INCONCLUSIVE, "sent OFF then ON, both acked - no status read-back exists to confirm actual device state (use --mode interactive)")


async def auto_identity(c: GoveeBleClient, p: DeviceProfile) -> Result:
    if (guard := _status_guard(p, "identity")) is not None:
        return guard
    st = await c.get_status()
    ok = st.ble_mac is not None and st.hardware_version is not None
    return Result("identity", PASS if ok else FAIL, f"mac={st.ble_mac} hw={st.hardware_version}")


async def auto_serial(c: GoveeBleClient, p: DeviceProfile) -> Result:
    serial = await c.get_serial_number()
    return Result("serial", PASS if serial else INCONCLUSIVE, f"serial={serial}")


async def auto_brightness(c: GoveeBleClient, p: DeviceProfile) -> Result:
    if (guard := _status_guard(p, "brightness")) is not None:
        return guard
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
    # The activate/upload command above is real and sendable regardless of
    # status_scheme - only the read-back verification below needs
    # status_scheme='full' (no other scheme confirms scene_id).
    if p.protocol.status_scheme != "full":
        return Result("scene", INCONCLUSIVE, f"{scene.name}: sent, but status readback unsupported (status_scheme={p.protocol.status_scheme!r}) - use --mode interactive to confirm")
    st = await c.get_status()
    ok = st.scene_id == scene.scene_id
    return Result("scene", PASS if ok else FAIL, f"{scene.name}: set {scene.scene_id}, read {st.scene_id}")


async def _read_segments(c: GoveeBleClient, p: DeviceProfile, retries: int = 5):
    """Read current per-segment state back, via whichever mechanism this
    device's status_scheme actually supports. Returns None if status
    readback isn't available at all (status_scheme='none') or every poll's
    chunks dropped (retry: both mechanisms are drop-prone over BLE)."""
    if p.protocol.status_scheme == "full":
        for _ in range(retries):
            st = await c.get_status(with_segments=True)
            if st.segments:
                return st.segments
            await asyncio.sleep(1.0)
        return None
    if p.protocol.status_scheme == "segment_fields":
        for _ in range(retries):
            try:
                return await c.get_segment_status()
            except Exception:  # noqa: BLE001 - a dropped poll, retry like the "full" path above
                await asyncio.sleep(1.0)
        return None
    return None  # status_scheme == "none": no readback mechanism exists


async def _read_rgb(c: GoveeBleClient, p: DeviceProfile, retries: int = 5) -> tuple[int, int, int] | None:
    """Read the solid RGB back via whichever per-segment mechanism this
    device supports (see _read_segments); None if unavailable/dropped."""
    segments = await _read_segments(c, p, retries)
    return uniform_rgb(segments)


async def auto_rgb(c: GoveeBleClient, p: DeviceProfile) -> Result:
    await c.set_rgb_color(255, 0, 0)
    await asyncio.sleep(_SETTLE)
    rgb = await _read_rgb(c, p)
    if rgb is None:
        return Result("rgb", INCONCLUSIVE, "no segment read-back available or chunks dropped; couldn't confirm color")
    return Result("rgb", PASS if rgb == (255, 0, 0) else FAIL, f"set (255,0,0), read {rgb}")


async def auto_color_temp(c: GoveeBleClient, p: DeviceProfile) -> Result:
    # No Kelvin read-back exists, but the rendered tint shows up as segment RGB.
    # Verify the tint moves the right way: warm has less blue than cool.
    lo, hi = p.capabilities.color_temp
    await c.set_color_temp_kelvin(lo)
    await asyncio.sleep(_SETTLE)
    warm = await _read_rgb(c, p)
    await c.set_color_temp_kelvin(hi)
    await asyncio.sleep(_SETTLE)
    cool = await _read_rgb(c, p)
    if warm is None or cool is None:
        return Result("color_temp", INCONCLUSIVE, "no segment read-back available or chunks dropped; couldn't confirm tint")
    ok = cool[2] > warm[2]  # cooler temperature -> more blue
    return Result("color_temp", PASS if ok else FAIL, f"warm={warm} cool={cool} (expect cool bluer)")


async def auto_segments(c: GoveeBleClient, p: DeviceProfile) -> Result:
    await c.set_segment_color(1 << 0, 255, 0, 0)
    await c.set_segment_brightness(1 << 0, 50)
    await asyncio.sleep(_SETTLE)
    segments = await _read_segments(c, p)
    if not segments:
        return Result("segments", INCONCLUSIVE, "no segment data on any poll (BLE contention, or status_scheme='none')")
    seg = segments[0]
    ok = (seg.r, seg.g, seg.b) == (255, 0, 0) and abs(seg.brightness_pct - 50) <= 1
    return Result("segments", PASS if ok else FAIL,
                  f"seg0 read bri={seg.brightness_pct} rgb=({seg.r},{seg.g},{seg.b})")


# --------------------------------------------------------------------------
# Interactive checks (human confirms the physical result)
# --------------------------------------------------------------------------


async def ix_power(c: GoveeBleClient, p: DeviceProfile) -> Result:
    await c.set_power(False)
    await asyncio.sleep(_SETTLE)
    off = _confirm("Device is now OFF?")
    await c.set_power(True)
    await asyncio.sleep(_SETTLE)
    on = _confirm("Device is now ON?")
    return Result("power", PASS if off and on else FAIL, "")


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
    ("power", lambda p: not p.capabilities.zones, auto_power, ix_power),
    ("brightness", lambda p: p.capabilities.brightness, auto_brightness, ix_brightness),
    ("rgb", lambda p: p.capabilities.rgb, auto_rgb, ix_rgb),
    ("color_temp", lambda p: p.capabilities.color_temp is not None, auto_color_temp, ix_color_temp),
    ("zones", lambda p: bool(p.capabilities.zones), auto_zones, ix_zones),
    ("segments", lambda p: p.capabilities.segments > 0, auto_segments, ix_segments),
    ("scenes", lambda p: p.capabilities.scenes, auto_scene, ix_scene),
]


async def power_off(c: GoveeBleClient, p: DeviceProfile) -> None:
    """Turn the device fully off: both zones for zoned devices (verified on
    H60A6 - one of the four states auto_zones already cycles through), the
    global power opcode otherwise (verified on H6006/H61A8)."""
    try:
        await profile_mod.set_power(c, p, False)
    except Exception:  # noqa: BLE001 - best-effort
        pass


async def _restore_neutral(c: GoveeBleClient, p: DeviceProfile) -> None:
    """Fallback when the pre-test snapshot is unavailable: leave the device
    on and usable rather than in an unknown state."""
    await profile_mod.set_power(c, p, True)
    await c.set_brightness_pct(100)
    if p.capabilities.color_temp:
        await c.set_color_temp_kelvin(3500)


async def restore_initial(c: GoveeBleClient, p: DeviceProfile, initial: GoveeBleStatus | None) -> None:
    """Best-effort: put the device back how it was before testing started.

    Restores brightness and color/scene from the pre-test status snapshot,
    then zones *last*. Order matters: activating a scene or solid color can
    re-light the fixture as a side effect (confirmed live - restoring a scene
    while zones were both off turned them back on), so the zone state must be
    the final command sent or an originally-off device would end up on.

    Color is approximate: a solid color is only knowable when segments read
    back uniform (``rgb_color``), so a scene that was playing is restored by
    re-activating its last-known scene id instead (works only if the device
    still has it cached, which it should since it was already playing it).
    Falls back to a neutral on/bright/mid-temp state if no snapshot was taken
    (e.g. the initial status query itself failed).
    """
    try:
        if initial is None:
            await _restore_neutral(c, p)
            return
        if initial.brightness_pct is not None:
            await c.set_brightness_pct(initial.brightness_pct)
        if p.capabilities.rgb and initial.rgb_color is not None:
            await c.set_rgb_color(*initial.rgb_color)
        elif p.capabilities.scenes and initial.scene_id is not None:
            await c.set_scene(initial.scene_id)
        if p.capabilities.zones and initial.zone_upper_on is not None and initial.zone_lower_on is not None:
            await c.set_zone(ZONE_UPPER, initial.zone_upper_on)
            await c.set_zone(ZONE_LOWER, initial.zone_lower_on)
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
        idx = int(input("Select device index (0-based, per the [N] above): ").strip())
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
    client = GoveeBleClient(device, prof.protocol, prof.capabilities.segments)
    results: list[Result] = []
    initial_status: GoveeBleStatus | None = None
    try:
        if args.after == "restore":
            try:
                initial_status = await client.get_status(with_segments=True)
            except Exception as err:  # noqa: BLE001 - best-effort; falls back to a neutral restore
                print(f"   (couldn't read initial state before testing: {err}; will restore to a neutral state instead)")

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

        if args.after == "off":
            await power_off(client, prof)
        else:
            await restore_initial(client, prof, initial_status)
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
    ap.add_argument("--pick", type=int, default=None, help="select candidate index non-interactively (0-based, per the [N] shown)")
    ap.add_argument("--scan-timeout", type=float, default=25.0, dest="scan_timeout",
                     help="seconds to scan for devices - longer gives weak-signal devices more chances to be seen (default: %(default)s)")
    ap.add_argument("--after", choices=["restore", "off"], default="restore",
                     help="what to do when testing finishes: 'restore' puts the device back how it was "
                     "before testing started (best-effort; falls back to a neutral on state if the initial "
                     "read fails), 'off' turns it off (default: %(default)s)")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
