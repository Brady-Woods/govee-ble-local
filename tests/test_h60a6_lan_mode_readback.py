"""H60A6 mode replies captured after external LAN color changes (firmware 1.00.41)."""
import pytest

from govee_ble_local.wire import build
from test_device_gaps import _dev


# Captured after Wi-Fi green and red writes, including a newly created BLE client.
RGB_MODE = bytes.fromhex("aa051501000000000000000000000000000000bb")
WHITE_2700 = bytes.fromhex("aa0515010a8c000000000000000000000000003d")


@pytest.mark.parametrize("kelvin", [2700, 6500])
def test_external_rgb_clears_previous_h60a6_kelvin(kelvin: int) -> None:
    d = _dev("H60A6")
    d._apply_mode_reply(build.frame(0xAA, 0x05, 0x15, 0x01, kelvin >> 8, kelvin & 0xFF))
    assert d.state.color_temp_kelvin == kelvin
    d._apply_mode_reply(RGB_MODE)
    assert d.state.color_temp_kelvin is None


def test_zero_kelvin_does_not_claim_a_cached_rgb_value() -> None:
    d = _dev("H60A6")
    d._state.rgb_color = (1, 2, 3)
    d._apply_mode_reply(RGB_MODE)
    # The mode reply has no RGB triple. Fresh per-segment state is a separate read.
    assert d.state.rgb_color is None


def test_color_temperature_can_be_restored_after_rgb() -> None:
    d = _dev("H60A6")
    d._apply_mode_reply(RGB_MODE)
    d._apply_mode_reply(WHITE_2700)
    assert d.state.color_temp_kelvin == 2700


def test_malformed_reply_does_not_clear_known_kelvin() -> None:
    d = _dev("H60A6")
    d._apply_mode_reply(WHITE_2700)
    d._apply_mode_reply(b"\xaa\x05\x15")
    assert d.state.color_temp_kelvin == 2700


def test_other_model_keeps_its_existing_zero_kelvin_behavior() -> None:
    d = _dev("H6641")
    d._state.color_temp_kelvin = 4500
    d._apply_mode_reply(RGB_MODE)
    assert d.state.color_temp_kelvin == 4500
