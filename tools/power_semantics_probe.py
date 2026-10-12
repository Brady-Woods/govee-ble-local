#!/usr/bin/env python3
"""Hardware experiment: how does the global power switch (33 01) interact with zone
power (33 30 / 33 36) on a zoned fixture? Answers whether whole-fixture on/off can
be ONE frame instead of one per zone, and whether colour/brightness wake the device.

Run from a host within Bluetooth range of the device (stop Home Assistant's polling
of it first, or accept a few failed HA polls while this runs):

    python tools/power_semantics_probe.py AA:BB:CC:DD:EE:FF H60A6

Every step prints the device's own read-back (0xAC status for H60A6, aa 01/aa 36 for
the polled bar light). The device is restored to its starting power/zone state.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time

from bleak import BleakScanner

from govee_ble_local import create_device
from govee_ble_local.wire import build


async def main(address: str, sku: str) -> None:
    ble = await BleakScanner.find_device_by_address(address, timeout=30)
    if ble is None:
        sys.exit(f"{address} not found (out of range?)")
    dev = create_device(ble, sku, idle_disconnect=0)  # keep ONE session for the whole run
    conn = dev._conn  # raw send() has no public equivalent; this probe sends bare frames

    async def state(label: str) -> None:
        st = await dev.update()
        print(f"   -> {label:38s} on={st.is_on} zones={st.zone_power} bri={st.brightness}")

    async def send(label: str, *frames: bytes) -> None:
        t = time.monotonic()
        for f in frames:
            try:
                ack = await conn.send(f)
                status = "ok" if ack is not None and ack[2] == 0 else f"ack={ack!r}"
            except Exception as err:  # noqa: BLE001 - report, keep probing
                status = f"FAILED {err}"
            print(f"   {f[:4].hex()} {status}")
        print(f"   ({(time.monotonic() - t) * 1000:.0f} ms) {label}")
        await asyncio.sleep(0.8)  # let the device settle before reading back

    zones = list(range(len(dev.profile.zones))) or [0]
    if dev.profile.bar_switch:
        zone_frames = lambda on: [build.bar_switch(on, on)]  # noqa: E731
    else:
        zone_frames = lambda on: [build.zone_power(i, on) for i in zones]  # noqa: E731

    await dev.connect()
    start: tuple[bool | None, dict[int, bool]] | None = None
    try:
        await state("start")
        start = dev.state.is_on, dict(dev.state.zone_power)

        print("1. global OFF (33 01 00)")
        await send("global off", build.switch(False)); await state("after global off")
        print("2. global ON (33 01 01) - do previously-on zones come back?")
        await send("global on", build.switch(True)); await state("after global on")
        print("3. zones OFF, then global ON - does global on light anything?")
        await send("zones off", *zone_frames(False)); await state("after zones off")
        await send("global on", build.switch(True)); await state("after global on w/ zones off")
        print("4. global OFF, then zones ON - do zones wake the master switch?")
        await send("global off", build.switch(False))
        await send("zones on", *zone_frames(True)); await state("after zones on from global off")
        print("5. global OFF, then colour - does colour wake the device?")
        await send("global off", build.switch(False))
        await send("colour", build.color_rgb(0, 80, 255, dev.profile.color_scheme, dev.profile.segments))
        await state("after colour while off")
        print("6. global OFF, then brightness - does brightness wake the device?")
        await send("global off", build.switch(False))
        await send("brightness", build.brightness(40)); await state("after brightness while off")

    finally:
        # Always put the fixture back, even if state()/send() raised mid-experiment.
        if start is not None:
            was_on, was_zones = start
            print("restore")
            try:
                for i, on in was_zones.items():
                    await send("zone", build.zone_power(i, on))
                if was_on is not None:
                    await send("global", build.switch(was_on))
                await state("restored")
            except Exception as err:  # noqa: BLE001
                print(f"   RESTORE FAILED ({err}); check the fixture's power state manually")
        await dev.stop()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("address", help="device BLE address (e.g. AA:BB:CC:DD:EE:FF)")
    ap.add_argument("sku", help="device SKU (e.g. H60A6)")
    return ap.parse_args(argv)


if __name__ == "__main__":
    _a = parse_args()
    asyncio.run(main(_a.address, _a.sku))
