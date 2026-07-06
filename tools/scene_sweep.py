#!/usr/bin/env python3
"""Live sweep of the H60A6 built-in (bare-activate, no-upload) scenes.

Usage:
    python3 tools/scene_sweep.py scan          # passive: just find the device
    python3 tools/scene_sweep.py run [dwell]   # activate each bare scene, dwell secs each
"""
from __future__ import annotations

import asyncio
import sys

from bleak import BleakScanner

sys.path.insert(0, "src")
from govee_ble_local.registry import create_device  # noqa: E402
from govee_ble_local.scenes import load_scenes  # noqa: E402

SKU = "H60A6"
NAME_HINTS = ("H60A6", "GVH60A6", "Govee_H60A6", "ihoment_H60A6")


async def _find():
    found = None
    for ble, adv in (await BleakScanner.discover(timeout=12.0, return_adv=True)).values():
        name = (adv.local_name or ble.name or "")
        if any(h.lower() in name.lower() for h in NAME_HINTS):
            found = (ble, adv, name)
            break
    return found


async def scan() -> None:
    f = await _find()
    if not f:
        print("H60A6 NOT FOUND in scan")
        return
    ble, adv, name = f
    print(f"FOUND {name}  addr={ble.address}  rssi={adv.rssi}  mfg={list(adv.manufacturer_data)}")


async def run(dwell: float) -> None:
    f = await _find()
    if not f:
        print("H60A6 NOT FOUND"); return
    ble, adv, name = f
    print(f"Connecting to {name} ({ble.address})...")
    dev = create_device(ble, SKU, adv)
    await dev.update()  # connect + handshake
    print("Connected. Powering on + white baseline.")
    await dev.turn_on()
    await dev.set_brightness(100)
    await asyncio.sleep(1.0)

    bare = sorted(
        ((n, sc.code) for n, sc in load_scenes(SKU).items() if not sc.param),
        key=lambda x: x[1],
    )
    print(f"Sweeping {len(bare)} bare-activate scenes, {dwell}s each:\n")
    results = []
    for n, code in bare:
        print(f"  -> [{code:>3}] {n}")
        try:
            await dev.set_scene(code)
            ok = True
        except Exception as e:  # noqa: BLE001
            print(f"       ERROR: {e}"); ok = False
        results.append((n, code, ok))
        await asyncio.sleep(dwell)

    print("\nRestoring white + on.")
    await dev.turn_on()
    await dev.set_color_temp(4000)
    await dev.stop()
    print("\nSENT OK for:", ", ".join(f"{n}({c})" for n, c, ok in results if ok))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if mode == "scan":
        asyncio.run(scan())
    else:
        asyncio.run(run(float(sys.argv[2]) if len(sys.argv) > 2 else 5.0))
