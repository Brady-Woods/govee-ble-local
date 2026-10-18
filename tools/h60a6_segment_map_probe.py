#!/usr/bin/env python3
"""Test the hypothesis: the HIGHEST reported H60A6 segment (index 12) is the MAIN PANEL,
and indices 0..11 are the background ring.

Vivid RGB (not CCT) so it's unmistakable which physical element lights. Baseline every
reported segment dim blue, then isolate one index bright red at a time and OBSERVE which
element turns red. The decisive steps drive the ring and the top index to OPPOSITE colours
to confirm they're independent.

If confirmed, the fix is a profile change: H60A6 background zone -> segments range(12),
main zone -> segments (12,) — after which set_zone_color_temp("main", k) addresses the panel.

    python3 tools/h60a6_segment_map_probe.py [--address AA:BB..] [--full] [--settle 3] [--capture f.jsonl]
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

BLUE = (0, 0, 90)
RED = (255, 0, 0)
GREEN = (0, 200, 0)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", default=os.environ.get("GOVEE_H60A6_ADDRESS"))
    ap.add_argument("--full", action="store_true", help="sweep every index 0..N-1 individually")
    ap.add_argument("--settle", type=float, default=3.0)
    ap.add_argument("--capture")
    args = ap.parse_args()

    from govee_ble_local import create_device

    ble, _adv = await find_device(args.address)
    if ble is None:
        print("no H60A6 found — set --address / GOVEE_H60A6_ADDRESS")
        return 1
    dev = create_device(ble, "H60A6", frame_log=args.capture, idle_disconnect=0)
    n = dev.profile.segments
    ring = list(range(n - 1))     # hypothesis: 0..11 = ring
    top = n - 1                   # hypothesis: 12 = main panel
    print(f"device {dev.address}  reported segments={n}  ring?=0..{n - 2}  top index={top}\n")

    step = 0

    async def obs(desc: str, *calls: Callable[[], Awaitable[object]]) -> None:
        """One OBSERVE step: run each zero-arg call in order (a coroutine is only created when its
        call runs, so a failed send leaves nothing un-awaited)."""
        nonlocal step
        step += 1
        print(f"[{step:2}] OBSERVE: {desc}")
        for call in calls:
            await call()
        await asyncio.sleep(args.settle)

    try:
        await dev.set_zone_power("main", True)
        await dev.set_zone_power("background", True)
        st = await dev.update()
        print(f"     read-back reports {len(st.segments)} segments\n")

        await obs("baseline — ALL reported segments dim BLUE",
                  lambda: dev.set_segment_rgb(list(range(n)), BLUE))
        await obs(f"isolate TOP index {top} = RED  →  is the MAIN PANEL red (ring stays blue)?",
                  lambda: dev.set_segment_rgb([top], RED))
        await obs(f"restore {top} blue; isolate index {n - 2} = RED  →  last RING position?",
                  lambda: dev.set_segment_rgb([top], BLUE), lambda: dev.set_segment_rgb([n - 2], RED))
        await obs(f"restore {n - 2} blue; isolate index 0 = RED  →  first RING position?",
                  lambda: dev.set_segment_rgb([n - 2], BLUE), lambda: dev.set_segment_rgb([0], RED))

        # decisive independence: ring vs top in opposite colours, then read the device's
        # OWN per-segment report back — index `top` holding a distinct colour from 0..n-2
        # proves it's independently addressable (the library requirement for a "main" zone).
        await obs(f"ring 0..{n - 2} = GREEN, top {top} = RED  →  panel red, ring green?",
                  lambda: dev.set_segment_rgb([0], BLUE),
                  lambda: dev.set_segment_rgb(ring, GREEN), lambda: dev.set_segment_rgb([top], RED))
        st = await dev.update()
        by_idx = {s.index: s.rgb for s in st.segments}
        print(f"     read-back: ring sample idx0={by_idx.get(0)} idx{n - 2}={by_idx.get(n - 2)} "
              f"| TOP idx{top}={by_idx.get(top)}")
        indep = by_idx.get(top) not in (None, by_idx.get(0)) and by_idx.get(0) == by_idx.get(n - 2)
        print(f"     -> index {top} independently addressable from the ring: "
              f"{'YES' if indep else 'inconclusive (check the read-back above / watch the light)'}")
        await obs(f"SWAP: ring 0..{n - 2} = RED, top {top} = GREEN  →  panel green, ring red?",
                  lambda: dev.set_segment_rgb(ring, RED), lambda: dev.set_segment_rgb([top], GREEN))

        if args.full:
            await obs("full sweep: re-baseline BLUE", lambda: dev.set_segment_rgb(list(range(n)), BLUE))
            for k in range(n):
                await obs(f"index {k} = RED  (which element?)",
                          functools.partial(dev.set_segment_rgb, [k], RED))
                await dev.set_segment_rgb([k], BLUE)

        print("\nprobe complete — leaving both zones on.")
        return 0
    finally:
        await dev.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
