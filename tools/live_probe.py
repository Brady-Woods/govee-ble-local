#!/usr/bin/env python3
"""Interactively drive a real Govee device over BLE using the library.

A thin CLI over GoveeBleClient for manual testing / exploration: read status,
send a single command, or run the zone truth-table. After a mutating command
it re-reads and prints status.

Examples:
    python3 tools/live_probe.py D4:13:68:21:D0:75 status
    python3 tools/live_probe.py D4:13:68:21:D0:75 brightness 60
    python3 tools/live_probe.py D4:13:68:21:D0:75 rgb 255 0 0
    python3 tools/live_probe.py D4:13:68:21:D0:75 zone upper on
    python3 tools/live_probe.py D4:13:68:21:D0:75 scene Sunrise --sku H60A6
    python3 tools/live_probe.py D4:13:68:21:D0:75 truth-table
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from bleak import BleakScanner  # noqa: E402

from govee_ble_local import GoveeBleClient, profile  # noqa: E402
from govee_ble_local.const import ZONE_LOWER, ZONE_UPPER  # noqa: E402


async def _find(address: str) -> GoveeBleClient:
    device = await BleakScanner.find_device_by_address(address, timeout=20.0)
    if device is None:
        raise SystemExit(f"Device {address} not found (powered on / in range?)")
    return GoveeBleClient(device)


async def _print_status(client: GoveeBleClient, with_segments: bool = False) -> None:
    status = await client.get_status()
    print(f"  zones:      upper={status.zone_upper_on} lower={status.zone_lower_on}")
    print(f"  brightness: {status.brightness_pct}")
    print(f"  scene_id:   {status.scene_id}")
    print(f"  hw/mac:     hw={status.hardware_version} ble={status.ble_mac} wifi={status.wifi_mac}")
    if status.segments:
        for seg in status.segments:
            print(f"    seg {seg.index:2d}: bri={seg.brightness_pct:3d} rgb=({seg.r},{seg.g},{seg.b})")


async def run(args: argparse.Namespace) -> None:
    client = await _find(args.address)
    try:
        cmd = args.command
        if cmd == "status":
            await _print_status(client)
        elif cmd == "brightness":
            await client.set_brightness_pct(int(args.args[0]))
            await _print_status(client)
        elif cmd == "rgb":
            r, g, b = (int(x) for x in args.args[:3])
            await client.set_rgb_color(r, g, b)
            await _print_status(client)
        elif cmd == "color-temp":
            await client.set_color_temp_kelvin(int(args.args[0]))
            await _print_status(client)
        elif cmd == "zone":
            zone = ZONE_UPPER if args.args[0].lower() == "upper" else ZONE_LOWER
            await client.set_zone(zone, args.args[1].lower() in ("on", "1", "true"))
            await _print_status(client)
        elif cmd == "segment-color":
            idx, r, g, b = (int(x) for x in args.args[:4])
            await client.set_segment_color(1 << idx, r, g, b)
            await _print_status(client)
        elif cmd == "segment-brightness":
            idx, pct = (int(x) for x in args.args[:2])
            await client.set_segment_brightness(1 << idx, pct)
            await _print_status(client)
        elif cmd == "scene":
            prof = profile.load_by_sku(args.sku) if args.sku else None
            scene = prof.scene_by_name(args.args[0]) if prof else None
            if scene is None:
                raise SystemExit(f"Scene {args.args[0]!r} not found (need --sku with the catalog)")
            if scene.param:
                await client.set_scene_full(scene.code, scene.param)
            else:
                await client.set_scene(scene.scene_id)
            await _print_status(client)
        elif cmd == "serial":
            print(f"  serial: {await client.get_serial_number()}")
        elif cmd == "truth-table":
            for upper in (False, True):
                for lower in (False, True):
                    await client.set_zone(ZONE_UPPER, upper)
                    await client.set_zone(ZONE_LOWER, lower)
                    await asyncio.sleep(1.5)
                    st = await client.get_status()
                    print(f"  set U={int(upper)} L={int(lower)} -> read "
                          f"upper={st.zone_upper_on} lower={st.zone_lower_on}")
    finally:
        await client.disconnect()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("address", help="device BLE MAC")
    ap.add_argument("command", choices=[
        "status", "brightness", "rgb", "color-temp", "zone",
        "segment-color", "segment-brightness", "scene", "serial", "truth-table",
    ])
    ap.add_argument("args", nargs="*", help="command arguments")
    ap.add_argument("--sku", default=None, help="device SKU for scene lookup (e.g. H60A6)")
    asyncio.run(run(ap.parse_args()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
