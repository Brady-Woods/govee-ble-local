"""T5.2 — builder-boundary input validation (DES-1 foundation, SEC-4, SEC-6)."""
from __future__ import annotations

import pytest

from govee_ble_local import const
from govee_ble_local.crypto import _rc4, aes_ecb_block, decrypt, encrypt
from govee_ble_local.exceptions import GoveeBleInvalidValue
from govee_ble_local.wire import build as b

BAD_CHANNELS = [-1, 256, 1000]
SCHEMES = ["h60a6", "h6006", "h61a8"]


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("bad", BAD_CHANNELS)
@pytest.mark.parametrize("pos", range(3))
def test_color_rgb_channel_out_of_range(scheme: str, bad: int, pos: int) -> None:
    rgb = [0, 0, 0]
    rgb[pos] = bad
    with pytest.raises(GoveeBleInvalidValue, match=r"0\.\.255"):
        b.color_rgb(*rgb, scheme)  # type: ignore[arg-type]


@pytest.mark.parametrize("scheme", ["h60a6", "h61a8"])
def test_segment_rgb_channel_and_mask(scheme: str) -> None:
    with pytest.raises(GoveeBleInvalidValue, match="green"):
        b.segment_rgb(1, 0, 256, 0, scheme)  # type: ignore[arg-type]
    for bad in (-1, 0x10000):
        with pytest.raises(GoveeBleInvalidValue, match="segment mask"):
            b.segment_rgb(bad, 1, 2, 3, scheme)  # type: ignore[arg-type]


def test_boundary_values_still_work() -> None:
    assert len(b.color_rgb(0, 0, 0, "h60a6")) == 20
    assert len(b.color_rgb(255, 255, 255, "h6006")) == 20
    assert len(b.segment_rgb(0xFFFF, 255, 255, 255, "h60a6")) == 20
    assert len(b.segment_rgb(0, 0, 0, 0, "h61a8")) == 20
    assert b.color_temp(0, "h6006")[6:8] == b"\x00\x00"
    assert b.color_temp(65535, "h6006")[6:8] == b"\xff\xff"
    assert len(b.segment_color_temp(0xFFFF, 65535, "h60a6")) == 20
    assert b.zone_power(0, True)[2] == 0 and b.zone_power(255, True)[2] == 255
    assert b.scene_activate(65535)[3:5] == b"\xff\xff"


@pytest.mark.parametrize("bad", [-1, 65536, 70000])
def test_kelvin_out_of_range(bad: int) -> None:
    for scheme in ("h6006", "h60a6"):
        with pytest.raises(GoveeBleInvalidValue, match="kelvin"):
            b.color_temp(bad, scheme)  # type: ignore[arg-type]
    with pytest.raises(GoveeBleInvalidValue, match="kelvin"):
        b.segment_color_temp(1, bad, "h60a6")


def test_segment_color_temp_and_brightness_mask() -> None:
    for bad in (-1, 0x10000):
        with pytest.raises(GoveeBleInvalidValue, match="segment mask"):
            b.segment_color_temp(bad, 4000, "h60a6")
        with pytest.raises(GoveeBleInvalidValue, match="segment mask"):
            b.segment_brightness(bad, 50, "h60a6")


@pytest.mark.parametrize("bad", [-1, 256])
def test_zone_power_range(bad: int) -> None:
    with pytest.raises(GoveeBleInvalidValue, match="zone"):
        b.zone_power(bad, True)


@pytest.mark.parametrize("bad", [-1, 65536])
def test_scene_code_range(bad: int) -> None:
    with pytest.raises(GoveeBleInvalidValue, match="scene code"):
        b.scene_activate(bad)


def test_non_int_rejected() -> None:
    with pytest.raises(GoveeBleInvalidValue):
        b.color_rgb(1.5, 0, 0, "h60a6")  # type: ignore[arg-type]
    with pytest.raises(GoveeBleInvalidValue):
        b.color_rgb(True, 0, 0, "h60a6")  # type: ignore[arg-type]


def test_brightness_still_clamps() -> None:
    assert b.brightness(-5)[2] == 0 and b.brightness(500)[2] == 100
    assert b.segment_brightness(1, 500, "h60a6")[4] == 100
    assert b.segment_brightness(1, -5, "h60a6")[4] == 0


def test_all_segments_mask_still_clamps() -> None:
    assert b.all_segments_mask(99) == 0xFFFF and b.all_segments_mask(-3) == 0


@pytest.mark.parametrize("n", [0, 7, 9, 16])
def test_secret_check_requires_8_bytes(n: int) -> None:
    with pytest.raises(GoveeBleInvalidValue, match="8 bytes"):
        b.secret_check(bytes(n))
    assert b.secret_check(bytes(range(1, 9)))[2:10] == bytes(range(1, 9))


def test_frame_too_long_message() -> None:
    with pytest.raises(ValueError, match="payload too long: 19"):
        b.frame(0x33, *range(19))
    assert len(b.frame(0x33, *range(18))) == 20


def test_invalid_value_is_value_error() -> None:
    assert issubclass(GoveeBleInvalidValue, ValueError)


# -- crypto (SEC-6) ----------------------------------------------------------
@pytest.mark.parametrize("n", [0, 8, 15, 17, 32])
def test_aes_key_length(n: int) -> None:
    with pytest.raises(GoveeBleInvalidValue, match="16 bytes"):
        aes_ecb_block(bytes(n), bytes(16), encrypt=True)


@pytest.mark.parametrize("n", [0, 15, 17, 20])
def test_aes_block_length(n: int) -> None:
    with pytest.raises(GoveeBleInvalidValue, match="16 bytes"):
        aes_ecb_block(const.PSK, bytes(n), encrypt=True)


def test_rc4_empty_key() -> None:
    with pytest.raises(GoveeBleInvalidValue, match="RC4"):
        _rc4(b"", b"abcd")


def test_crypto_valid_roundtrip_unchanged() -> None:
    pt = bytes(range(20))
    assert decrypt(encrypt(pt, const.PSK), const.PSK) == pt
    assert _rc4(b"Key", b"Plaintext").hex() == "bbf316e8d940af0ad3"
