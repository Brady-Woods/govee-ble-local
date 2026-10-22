#!/usr/bin/env python3
"""Live H60A6 colour-temperature choreography — exercises masked / per-zone CCT, and per-segment CCT
on a profile that allows it (``DeviceProfile.segment_color_temp``). The H60A6's doesn't since decision 24
(its Part-tab Kelvin widens every pick to a whole zone, ``ColorModeH60A6.canClickColorTem``): skipped.

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
import functools
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS.parent / "src"))
sys.path.insert(0, str(_TOOLS))  # sibling h60a6_live_check: the shared H60A6 scan helper

from h60a6_live_check import find_device  # noqa: E402 - needs the sys.path entry above

MIN_K = 2700
MAX_K = 6500
MID_K = (MIN_K + MAX_K) // 2   # 4600
SETTLE = 2.5                   # seconds to observe each step


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", default=os.environ.get("GOVEE_H60A6_ADDRESS"))
    ap.add_argument("--capture", help="write a JSONL frame log for this session")
    args = ap.parse_args()

    from govee_ble_local import create_device

    ble, _adv = await find_device(args.address)
    if ble is None:
        print("no H60A6 found (scan) — set --address / GOVEE_H60A6_ADDRESS")
        return 1
    # idle_disconnect=0: keep the one session alive for the whole run
    dev = create_device(ble, "H60A6", frame_log=args.capture, idle_disconnect=0)
    print(f"device {dev.address}  segments={dev.profile.segments}  kelvin {MIN_K}..{MAX_K}\n")

    step = 0

    async def group(desc: str, *calls: Callable[[], Awaitable[object]]) -> None:
        """One OBSERVE step: run each zero-arg call in order (each send raises on a missing ACK,
        and a coroutine is only created when its call runs, so nothing is left un-awaited)."""
        nonlocal step
        step += 1
        print(f"[{step:2}] OBSERVE: {desc}")
        for call in calls:
            await call()
        await asyncio.sleep(SETTLE)

    try:
        # whole device at the midpoint, both zones powered on
        await group(
            f"both zones ON, whole device @ midpoint {MID_K}K",
            lambda: dev.set_zone_power("main", True),
            lambda: dev.set_zone_power("background", True),
            lambda: dev.set_color_temp(MID_K),
        )
        await group("whole device OFF", dev.turn_off)
        # background on at min (warm), then off
        await group(
            f"background ON @ min {MIN_K}K (warm)",
            lambda: dev.set_zone_power("background", True),
            lambda: dev.set_zone_color_temp("background", MIN_K),
        )
        await group("background OFF", lambda: dev.set_zone_power("background", False))
        # main panel (index 12) at max (cool), then off
        await group(
            f"main panel ON @ max {MAX_K}K (cool)",
            lambda: dev.set_zone_power("main", True),
            lambda: dev.set_zone_color_temp("main", MAX_K),
        )
        await group("main panel OFF", lambda: dev.set_zone_power("main", False))
        # both on at the same kelvin
        await group(
            f"both zones ON @ same {MID_K}K",
            lambda: dev.set_zone_power("main", True),
            lambda: dev.set_zone_power("background", True),
            lambda: dev.set_color_temp(MID_K),
        )
        # invert: background high (cool), main low (warm) — genuinely independent now
        await group(
            f"INVERT: background @ {MAX_K}K (cool), main panel @ {MIN_K}K (warm)",
            lambda: dev.set_zone_color_temp("background", MAX_K),
            lambda: dev.set_zone_color_temp("main", MIN_K),
        )

        # ── STRETCH: per-segment CCT across all n segments (0..11 background ring + 12 main panel) ──
        n = dev.profile.segments
        if not dev.profile.segment_color_temp:
            print("     per-segment CCT skipped: the profile sends no Kelvin to a piece selection")
        else:
            evens = list(range(0, n, 2))
            odds = list(range(1, n, 2))
            await group(
                f"per-segment ALTERNATING: even segs {MAX_K}K / odd segs {MIN_K}K",
                lambda: dev.set_segment_color_temp(evens, MAX_K),
                lambda: dev.set_segment_color_temp(odds, MIN_K),
            )
            ladder = [MIN_K + round((MAX_K - MIN_K) * i / (n - 1)) for i in range(n)]
            await group(
                f"per-segment GRADIENT low->high across {n} segs: {ladder}",
                *[functools.partial(dev.set_segment_color_temp, [i], k) for i, k in enumerate(ladder)],
            )
            await group(
                "per-segment GRADIENT reversed (high->low)",
                *[functools.partial(dev.set_segment_color_temp, [i], k)
                  for i, k in enumerate(reversed(ladder))],
            )

        # reset both on at the midpoint
        await group(
            f"reset: both zones ON @ midpoint {MID_K}K",
            lambda: dev.set_zone_power("main", True),
            lambda: dev.set_zone_power("background", True),
            lambda: dev.set_color_temp(MID_K),
        )
        # read-back to confirm the session is still healthy
        state = await dev.update()
        print(f"     read-back: on={state.is_on} kelvin={state.color_temp_kelvin} "
              f"segs={len(state.segments)} zones={state.zone_power}")
        await group("whole device OFF (end)", dev.turn_off)
        print("\nchoreography complete.")
        return 0
    finally:
        await dev.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
