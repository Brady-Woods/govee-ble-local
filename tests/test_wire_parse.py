"""wire.parse — runtime parsing of 0xAA read replies + 0xEE notifications
via the shipped Kaitai reader."""
from __future__ import annotations

import pytest

pytest.importorskip("kaitaistruct", reason="Kaitai runtime not installed")

from govee_ble_local.wire import parse  # noqa: E402


def f(*payload: int) -> bytes:
    """20-byte frame with trailing XOR checksum."""
    b = bytearray(20)
    b[: len(payload)] = bytes(payload)
    x = 0
    for c in b[:19]:
        x ^= c
    b[19] = x
    return bytes(b)


def test_power_raw_byte() -> None:
    assert parse.parse_power(f(0xAA, 0x01, 0x01)) == 1
    assert parse.parse_power(f(0xAA, 0x01, 0x00)) == 0
    assert parse.parse_power(f(0xAA, 0x01, 0x0F)) == 0x0F   # plug relay bitmask, raw
    assert parse.parse_power(f(0x33, 0x01, 0x01)) is None    # write, not a read reply
    assert parse.parse_power(f(0xAA, 0x04, 0x01)) is None    # wrong command


def test_brightness_raw_no_rescale() -> None:
    assert parse.parse_brightness(f(0xAA, 0x04, 0x40)) == 0x40
    assert parse.parse_brightness(f(0xAA, 0x04, 200)) == 200  # RAW, no 0-255->0-100 rescale
    assert parse.parse_brightness(f(0xAA, 0x01, 0x40)) is None


def test_active_scene_and_kelvin() -> None:
    assert parse.parse_active_scene(f(0xAA, 0x05, 0x04, 0x82, 0x4A)) == 0x4A82  # u2le
    assert parse.parse_active_scene(f(0xAA, 0x05, 0x15, 0x01, 0x0A, 0x8C)) is None  # cct, not scene
    assert parse.parse_kelvin(f(0xAA, 0x05, 0x15, 0x01, 0x0A, 0x8C)) == 2700  # u2be
    assert parse.parse_kelvin(f(0xAA, 0x05, 0x15, 0x01, 0x19, 0x64)) == 6500
    assert parse.parse_kelvin(f(0xAA, 0x05, 0x04, 0x82, 0x4A)) is None  # scene, not cct


def test_bar_switch() -> None:
    assert parse.parse_bar_switch(f(0xAA, 0x36, 0x01, 0x01)) == (True, True)
    assert parse.parse_bar_switch(f(0xAA, 0x36, 0x01, 0x00)) == (True, False)
    assert parse.parse_bar_switch(f(0xAA, 0x01, 0x01, 0x01)) is None


def test_secret_and_plug_spec() -> None:
    sec = parse.parse_secret(f(0xAA, 0xB1, 0x01, 1, 2, 3, 4, 5, 6, 7, 8))
    assert sec == bytes([1, 2, 3, 4, 5, 6, 7, 8])
    assert parse.parse_secret(f(0xAA, 0xB1, 0x00, 1, 2, 3, 4, 5, 6, 7, 8)) is None  # selector != 1
    assert parse.parse_plug_spec(f(0xAA, 0xB3, 0x07)) == 0x07


def test_device_info() -> None:
    basic = parse.parse_device_info(f(0xAA, 0x07, 0x10, 0x88, 0x77, 0x66, 0x55, 0x44, 0x33, 0x22, 0x11,
                                      1, 4, 3, 1, 0, 0))
    assert basic is not None
    assert basic.serial == "11:22:33:44:55:66:77:88"
    assert basic.sw_version == "1.04.03" and basic.hw_version == "1.00.00"

    wifi = parse.parse_device_info(f(0xAA, 0x07, 0x11, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 1, 2, 30, 1, 4, 3))
    assert wifi is not None and wifi.wifi_mac == "11:22:33:44:55:66"
    assert wifi.sw_version == "1.02.30" and wifi.hw_version == "1.04.03"

    sn = parse.parse_device_info(f(0xAA, 0x07, 0x02, 0x88, 0x77, 0x66, 0x55, 0x44, 0x33, 0x22, 0x11))
    assert sn is not None and sn.serial == "11:22:33:44:55:66:77:88"


def test_notify() -> None:
    lvl = parse.parse_notify(f(0xEE, 0x20, 0x40))
    assert lvl is not None and lvl.sub_type == 0x20 and lvl.level == 0x40
    wifi = parse.parse_notify(f(0xEE, 0x11, 0x00))
    assert wifi is not None and wifi.wifi_connected is True
    assert parse.parse_notify(f(0xEE, 0x11, 0x01)).wifi_connected is False  # type: ignore[union-attr]
    zone = parse.parse_notify(f(0xEE, 0x30, 0x00, 0x01, 0x01, 0x00))
    assert zone is not None and zone.sub_type == 0x30 and zone.zone_flags == (0x01, 0x01, 0x00)
    assert parse.parse_notify(f(0x33, 0x01, 0x01)) is None  # not a notify
