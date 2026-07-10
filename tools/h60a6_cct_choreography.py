#!/usr/bin/env python3
"""Live H60A6 colour-temperature choreography — exercises masked / per-zone / per-segment CCT.

ONE persistent connection (idle-disconnect disabled). Each step prints an OBSERVE line and
pauses so you can watch the light; commands rely on the built-in write ACK (a 0x33 send raises
if the device doesn't ack). Kelvin range from the profile: 2700 (warm/low) .. 6500 (cool/high),
midpoint 4600.

Model note (H60A6, live-verified): indices 0..11 are the background RGBIC ring; the HIGHEST
index (12) is the MAIN PANEL, independently addressable. So set_zone_color_temp("main", k) =
mask 0x1000, set_zone_color_temp("background", k) = 0x0FFF, and whole-device set_color_temp =
0x1FFF (both). The main / background / invert steps below therefore genuinely separate.

    GOVEE_H60A6_ADDRESS=AA:BB:CC:DD:EE:FF python3 tools/h60a6_cct_choreography.py
    python3 tools/h60a6_cct_choreography.py --address AA:BB:CC:DD:EE:FF [--capture frames.jsonl]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

MIN_K = 2700
MAX_K = 6500
MID_K = (MIN_K + MAX_K) // 2   # 4600
SETTLE = 2.5                   # seconds to observe each step


async def find_device(address: str | None):  # type: ignore[no-untyped-def]
    from bleak import BleakScanner

    if address:
        return await BleakScanner.find_device_by_address(address, timeout=20.0)
    from govee_ble_local.identify import sku_from_local_name

    found: dict[str, tuple] = {}

    def _cb(d, adv):  # type: ignore[no-untyped-def]
        if sku_from_local_name(adv.local_name or d.name or "") == "H60A6":
            found[d.address] = (d, adv)

    sc = BleakScanner(detection_callback=_cb)
    await sc.start()
    await asyncio.sleep(8.0)
    await sc.stop()
    if not found:
        return None
    d, _adv = max(found.values(), key=lambda t: (t[1].rssi if t[1] else -999))
    return d


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", default=os.environ.get("GOVEE_H60A6_ADDRESS"))
    ap.add_argument("--capture", help="write a JSONL frame log for this session")
    args = ap.parse_args()

    from govee_ble_local import create_device

    ble = await find_device(args.address)
    if ble is None:
        print("no H60A6 found (scan) — set --address / GOVEE_H60A6_ADDRESS")
        return 1
    dev = create_device(ble, "H60A6", frame_log=args.capture)
    dev._conn._idle_disconnect = 0     # keep the one session alive for the whole run
    print(f"device {dev.address}  segments={dev.profile.segments}  kelvin {MIN_K}..{MAX_K}\n")

    step = 0

    async def do(desc: str, coro) -> None:  # type: ignore[no-untyped-def]
        nonlocal step
        step += 1
        print(f"[{step:2}] OBSERVE: {desc}")
        await coro
        await asyncio.sleep(SETTLE)

    async def group(desc: str, *coros) -> None:  # type: ignore[no-untyped-def]
        nonlocal step
        step += 1
        print(f"[{step:2}] OBSERVE: {desc}")
        for c in coros:
            await c
        await asyncio.sleep(SETTLE)

    try:
        # 1. whole device at the midpoint, both zones powered on
        await group(
            f"both zones ON, whole device @ midpoint {MID_K}K",
            dev.set_zone_power("main", True),
            dev.set_zone_power("background", True),
            dev.set_color_temp(MID_K),
        )
        # 2. whole device off
        await do("whole device OFF", dev.turn_off())
        # 3. background on at min (warm), then off
        await group(
            f"background ON @ min {MIN_K}K (warm)",
            dev.set_zone_power("background", True),
            dev.set_zone_color_temp("background", MIN_K),
        )
        await do("background OFF", dev.set_zone_power("background", False))
        # 4. main panel (index 12) at max (cool), then off
        await group(
            f"main panel ON @ max {MAX_K}K (cool)",
            dev.set_zone_power("main", True),
            dev.set_zone_color_temp("main", MAX_K),
        )
        await do("main panel OFF", dev.set_zone_power("main", False))
        # 5. both on at the same kelvin
        await group(
            f"both zones ON @ same {MID_K}K",
            dev.set_zone_power("main", True),
            dev.set_zone_power("background", True),
            dev.set_color_temp(MID_K),
        )
        # 6. invert: background high (cool), main low (warm) — genuinely independent now
        await group(
            f"INVERT: background @ {MAX_K}K (cool), main panel @ {MIN_K}K (warm)",
            dev.set_zone_color_temp("background", MAX_K),
            dev.set_zone_color_temp("main", MIN_K),
        )

        # ── STRETCH: independent per-segment CCT on the 13 background segments ──
        n = dev.profile.segments
        evens = list(range(0, n, 2))
        odds = list(range(1, n, 2))
        await group(
            f"per-segment ALTERNATING: even segs {MAX_K}K / odd segs {MIN_K}K",
            dev.set_segment_color_temp(evens, MAX_K),
            dev.set_segment_color_temp(odds, MIN_K),
        )
        ladder = [MIN_K + round((MAX_K - MIN_K) * i / (n - 1)) for i in range(n)]
        await group(
            f"per-segment GRADIENT low->high across {n} segs: {ladder}",
            *[dev.set_segment_color_temp([i], ladder[i]) for i in range(n)],
        )
        await group(
            "per-segment GRADIENT reversed (high->low)",
            *[dev.set_segment_color_temp([i], ladder[n - 1 - i]) for i in range(n)],
        )

        # 8. reset both on at the midpoint
        await group(
            f"reset: both zones ON @ midpoint {MID_K}K",
            dev.set_zone_power("main", True),
            dev.set_zone_power("background", True),
            dev.set_color_temp(MID_K),
        )
        # read-back to confirm the session is still healthy
        state = await dev.update()
        print(f"     read-back: on={state.is_on} kelvin={state.color_temp_kelvin} "
              f"segs={len(state.segments)} zones={state.zone_power}")
        # 9. whole device off
        await do("whole device OFF (end)", dev.turn_off())
        print("\nchoreography complete.")
        return 0
    finally:
        await dev.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
