#!/usr/bin/env python3
"""Hardware experiment: how does the global power switch (33 01) interact with zone
power (33 30 / 33 36) on a zoned fixture? Answers whether whole-fixture on/off can
be ONE frame instead of one per zone, and whether colour/brightness wake the device.

Run from a host within Bluetooth range of the device (stop Home Assistant's polling
of it first, or accept a few failed HA polls while this runs):

    python tools/power_semantics_probe.py AA:BB:CC:DD:EE:FF H60A6

Every step prints the device's own read-back (0xAC status for H60A6, aa 01/aa 36 for
the polled bar light). The device is restored to its starting power/zone state with the
same per-profile zone frames the experiment uses; if any restore frame is not acked, or the
starting power or zone state was never read, the probe says so and asks you to check the
fixture by hand.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Mapping

from bleak import BleakScanner

from govee_ble_local import DeviceProfile, create_device
from govee_ble_local.wire import build


def zone_frames(profile: DeviceProfile, states: Mapping[int, bool]) -> list[bytes]:
    """The frames that put each zone (power_index -> on) into ``states``, chosen per profile as
    the library does (Device.set_zone_power): a bar-switch fixture (H6047) carries both bars in
    one ``33 36 <left> <right>`` frame (Device._set_bar_power); any other zoned fixture takes one
    ``33 30 <zone> <on>`` per zone. A bar frame needs BOTH bar states: with either unknown this
    returns [] rather than switch a bar blindly."""
    if profile.bar_switch:
        if not {0, 1} <= states.keys():
            return []
        return [build.bar_switch(states[0], states[1])]
    return [build.zone_power(i, on) for i, on in states.items()]


def unknown_zones(profile: DeviceProfile, was_zones: Mapping[int, bool]) -> list[int]:
    """The profile zones (power_index) the experiment switches whose starting state is unknown,
    i.e. missing from the start read-back (a failed aa 36 read leaves a bar light's zone_power {})."""
    return sorted({z.power_index for z in profile.zones} - was_zones.keys())


def restore_plan(profile: DeviceProfile, was_on: bool | None,
                 was_zones: Mapping[int, bool]) -> tuple[list[tuple[str, list[bytes]]], bool]:
    """``(steps, complete)``: the ``(label, frames)`` steps, in send order, that put the fixture
    back to its starting power/zone state, and whether they cover all of it. Not complete when
    the starting power state or the starting state of any zone the experiment switches was
    unknown (unknown_zones); a bar light with either bar unknown gets no bar frame (zone_frames).

    Bar-switch fixture: the global switch first, because ``33 01 01`` doesn't say which bars
    light (Device._apply_power), so the ``33 36`` frame after it sets each bar exactly. Any other
    fixture: the zone frames first and the global switch last, so a zone-on frame (step 4 asks
    whether those wake the master switch) can't leave a fixture that started off switched on."""
    zones = zone_frames(profile, was_zones)
    complete = was_on is not None and not unknown_zones(profile, was_zones)
    zone_step = [("zone", zones)] if zones else []
    global_step = [("global", [build.switch(was_on)])] if was_on is not None else []
    steps = global_step + zone_step if profile.bar_switch else zone_step + global_step
    return steps, complete


async def main(address: str, sku: str) -> None:
    ble = await BleakScanner.find_device_by_address(address, timeout=30)
    if ble is None:
        sys.exit(f"{address} not found (out of range?)")
    dev = create_device(ble, sku, idle_disconnect=0)  # keep ONE session for the whole run
    conn = dev._conn  # raw send() has no public equivalent; this probe sends bare frames

    async def state(label: str) -> None:
        st = await dev.update()
        print(f"   -> {label:38s} on={st.is_on} zones={st.zone_power} bri={st.brightness}")

    async def send(label: str, *frames: bytes) -> bool:
        """Send each frame and print its ACK status; True only if every frame was acked."""
        t = time.monotonic()
        all_ok = True
        for f in frames:
            try:
                ack = await conn.send(f)
                status = "ok" if ack is not None and ack[2] == 0 else f"ack={ack!r}"
            except Exception as err:
                status = f"FAILED {err}"
            all_ok = all_ok and status == "ok"
            print(f"   {f[:4].hex()} {status}")
        print(f"   ({(time.monotonic() - t) * 1000:.0f} ms) {label}")
        await asyncio.sleep(0.8)  # let the device settle before reading back
        return all_ok

    every_zone = [z.power_index for z in dev.profile.zones] or [0]
    all_off = dict.fromkeys(every_zone, False)
    all_on = dict.fromkeys(every_zone, True)

    await dev.connect()
    start: tuple[bool | None, dict[int, bool]] | None = None
    try:
        await state("start")
        start = dev.state.is_on, dict(dev.state.zone_power)

        print("1. global OFF (33 01 00)")
        await send("global off", build.switch(False))
        await state("after global off")
        print("2. global ON (33 01 01) - do previously-on zones come back?")
        await send("global on", build.switch(True))
        await state("after global on")
        print("3. zones OFF, then global ON - does global on light anything?")
        await send("zones off", *zone_frames(dev.profile, all_off))
        await state("after zones off")
        await send("global on", build.switch(True))
        await state("after global on w/ zones off")
        print("4. global OFF, then zones ON - do zones wake the master switch?")
        await send("global off", build.switch(False))
        await send("zones on", *zone_frames(dev.profile, all_on))
        await state("after zones on from global off")
        print("5. global OFF, then colour - does colour wake the device?")
        await send("global off", build.switch(False))
        await send("colour", build.color_rgb(0, 80, 255, dev.profile.color_scheme, dev.profile.segments))
        await state("after colour while off")
        print("6. global OFF, then brightness - does brightness wake the device?")
        await send("global off", build.switch(False))
        await send("brightness", build.brightness(40))
        await state("after brightness while off")

    finally:
        # Always put the fixture back, even if state()/send() raised mid-experiment.
        if start is not None:
            was_on, was_zones = start
            print("restore")
            steps, restored = restore_plan(dev.profile, was_on, was_zones)
            if was_on is None:
                print("   starting power state unknown: not switching it blindly")
            unknown = unknown_zones(dev.profile, was_zones)
            if unknown:
                what = "bar" if dev.profile.bar_switch else "zone"
                print(f"   starting {what} state unknown (power index {unknown}): not switching blindly")
            try:
                for label, frames in steps:
                    restored = await send(label, *frames) and restored
                await state("restored" if restored else "after restore")
            except Exception as err:
                restored = False
                print(f"   ({err})")
            if not restored:
                print("   RESTORE FAILED (see above); check the fixture's power state manually")
        await dev.stop()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("address", help="device BLE address (e.g. AA:BB:CC:DD:EE:FF)")
    ap.add_argument("sku", help="device SKU (e.g. H60A6)")
    return ap.parse_args(argv)


if __name__ == "__main__":
    _a = parse_args()
    asyncio.run(main(_a.address, _a.sku))
