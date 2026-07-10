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
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

BLUE = (0, 0, 90)
RED = (255, 0, 0)
GREEN = (0, 200, 0)


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
    d, _ = max(found.values(), key=lambda t: (t[1].rssi if t[1] else -999))
    return d


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", default=os.environ.get("GOVEE_H60A6_ADDRESS"))
    ap.add_argument("--full", action="store_true", help="sweep every index 0..N-1 individually")
    ap.add_argument("--settle", type=float, default=3.0)
    ap.add_argument("--capture")
    args = ap.parse_args()

    from govee_ble_local import create_device

    ble = await find_device(args.address)
    if ble is None:
        print("no H60A6 found — set --address / GOVEE_H60A6_ADDRESS")
        return 1
    dev = create_device(ble, "H60A6", frame_log=args.capture)
    dev._conn._idle_disconnect = 0
    n = dev.profile.segments
    ring = list(range(n - 1))     # hypothesis: 0..11 = ring
    top = n - 1                   # hypothesis: 12 = main panel
    print(f"device {dev.address}  reported segments={n}  ring?=0..{n - 2}  top index={top}\n")

    step = 0

    async def obs(desc: str, *coros) -> None:  # type: ignore[no-untyped-def]
        nonlocal step
        step += 1
        print(f"[{step:2}] OBSERVE: {desc}")
        for c in coros:
            await c
        await asyncio.sleep(args.settle)

    try:
        await dev.set_zone_power("main", True)
        await dev.set_zone_power("background", True)
        st = await dev.update()
        print(f"     read-back reports {len(st.segments)} segments\n")

        await obs("baseline — ALL reported segments dim BLUE", dev.set_segment_rgb(list(range(n)), BLUE))
        await obs(f"isolate TOP index {top} = RED  →  is the MAIN PANEL red (ring stays blue)?",
                  dev.set_segment_rgb([top], RED))
        await obs(f"restore {top} blue; isolate index {n - 2} = RED  →  last RING position?",
                  dev.set_segment_rgb([top], BLUE), dev.set_segment_rgb([n - 2], RED))
        await obs(f"restore {n - 2} blue; isolate index 0 = RED  →  first RING position?",
                  dev.set_segment_rgb([n - 2], BLUE), dev.set_segment_rgb([0], RED))

        # decisive independence: ring vs top in opposite colours, then read the device's
        # OWN per-segment report back — index `top` holding a distinct colour from 0..n-2
        # proves it's independently addressable (the library requirement for a "main" zone).
        await obs(f"ring 0..{n - 2} = GREEN, top {top} = RED  →  panel red, ring green?",
                  dev.set_segment_rgb([0], BLUE),
                  dev.set_segment_rgb(ring, GREEN), dev.set_segment_rgb([top], RED))
        st = await dev.update()
        by_idx = {s.index: s.rgb for s in st.segments}
        print(f"     read-back: ring sample idx0={by_idx.get(0)} idx{n - 2}={by_idx.get(n - 2)} "
              f"| TOP idx{top}={by_idx.get(top)}")
        indep = by_idx.get(top) not in (None, by_idx.get(0)) and by_idx.get(0) == by_idx.get(n - 2)
        print(f"     -> index {top} independently addressable from the ring: "
              f"{'YES' if indep else 'inconclusive (check the read-back above / watch the light)'}")
        await obs(f"SWAP: ring 0..{n - 2} = RED, top {top} = GREEN  →  panel green, ring red?",
                  dev.set_segment_rgb(ring, RED), dev.set_segment_rgb([top], GREEN))

        if args.full:
            await obs("full sweep: re-baseline BLUE", dev.set_segment_rgb(list(range(n)), BLUE))
            for k in range(n):
                await obs(f"index {k} = RED  (which element?)",
                          dev.set_segment_rgb([k], RED))
                await dev.set_segment_rgb([k], BLUE)

        print("\nprobe complete — leaving both zones on.")
        return 0
    finally:
        await dev.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
