"""The aa 05 mode decoders read the spec's ``mode_read``, from a reply frame or from the 0xAC 0x05 TLV.

Both sources go through one reader (``parse.mode_reply`` / ``parse.mode_value``), so the burst's mode
no longer goes through a synthesised ``aa 05`` frame, and the app's sign and CCT tests come from the
spec's ``mode_color_0d_typed`` instances (``kelvin_signed``, ``report_kelvin_signed``, ``is_cct``,
``is_cct_positive``) rather than hand slices of the frame. No hardware."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from govee_ble_local.devices import _fold
from govee_ble_local.devices.profile import profile_for
from govee_ble_local.models import DeviceState
from govee_ble_local.wire import parse, reassemble

_SRC = Path(__file__).resolve().parent.parent / "src" / "govee_ble_local"


def _reply(*mode: int) -> bytes:
    """A 20-byte ``aa 05`` reply carrying the mode body ``mode`` = [sub_mode, params]."""
    buf = bytearray(20)
    buf[: 2 + len(mode)] = bytes([0xAA, 0x05, *mode])
    x = 0
    for c in buf[:19]:
        x ^= c
    buf[19] = x
    return bytes(buf)


_BODIES = [
    (0x0D, 1, 2, 3, 0x0A, 0x8C, 4, 5, 6),   # [r, g, b][kelvin 2700][tint]
    (0x0D, 1, 2, 3, 0x80, 0x00, 4, 5, 6),   # a negative signed Kelvin
    (0x15, 0x01, 0x0A, 0x8C),               # [gradual][kelvin 2700]
    (0x15, 0x01, 0xFF, 0xFF),               # [gradual][kelvin -1]
    (0x04, 0x2A, 0x01),                     # scene 0x012A
]


@pytest.mark.parametrize("body", _BODIES)
def test_a_tlv_mode_and_an_aa05_reply_read_alike(body: tuple[int, ...]) -> None:
    from_reply, from_tlv = parse.mode_reply(_reply(*body)), parse.mode_value(bytes(body))
    for decode in (parse.sub_mode, parse.scene_of, parse.gradual_kelvin_of, parse.cct_0d_of,
                   parse.rgb_kelvin_tint_of, parse.rgb_0d_of, parse.rgb_kelvin_0d_of):
        assert decode(from_tlv) == decode(from_reply), decode.__name__


def test_a_tlv_mode_reads_as_a_replys_17_byte_body() -> None:
    """A short value is zero padded (the reply's own padding), a long one cut, so both still decode."""
    assert parse.gradual_kelvin_of(parse.mode_value(bytes([0x15, 0x01]))) == 0
    assert parse.sub_mode(parse.mode_value(b"")) == 0
    long_value = bytes([0x0D, 1, 2, 3, 0x0A, 0x8C, *range(40)])
    assert parse.rgb_kelvin_0d_of(parse.mode_value(long_value)) == (None, 2700)


def test_the_signed_kelvin_tests_come_from_the_spec() -> None:
    def tint_report(k_hi: int, k_lo: int) -> object:
        return parse.rgb_kelvin_tint_of(parse.mode_value(bytes([0x0D, 1, 2, 3, k_hi, k_lo, 7, 8, 9])))

    # ModeParseStrategyConfig.a + parseModeValidBytes: CCT only for > 0; a negative Kelvin shows the tint.
    assert tint_report(0x0A, 0x8C) == (None, 2700)
    assert tint_report(0x80, 0x00) == ((7, 8, 9), None)
    assert tint_report(0x00, 0x00) == ((1, 2, 3), None)
    # rgb_kelvin_0d (H6061 / H60A6 0x0d): signed > 0 is CCT, anything else the RGB.
    assert parse.rgb_kelvin_0d_of(parse.mode_value(bytes([0x0D, 1, 2, 3, 0x80, 0x00]))) == ((1, 2, 3), None)
    # The default rgb_kelvin 0x0d parse: ANY non-zero Kelvin is CCT.
    assert parse.cct_0d_of(parse.mode_value(bytes([0x0D, 1, 2, 3, 0x80, 0x00, 0, 0, 0]))) == (None, 0x8000)
    # SubMode4Color's default [gradual][kelvin s16be].
    assert parse.gradual_kelvin_of(parse.mode_value(bytes([0x15, 0x01, 0xFF, 0xFF]))) == -1


@pytest.mark.parametrize(("mode", "rgb", "kelvin"), [
    (bytes([0x15, 0x01, 0x0A, 0x8C]), None, 2700),          # 0x15 [gradual][kelvin]: CCT
    (bytes([0x0D, 1, 2, 3, 0x80, 0x00]), (1, 2, 3), None),  # 0x0d, a negative Kelvin: the RGB
    (bytes([0x0D, 1, 2, 3, 0x0A, 0x8C]), None, 2700),       # 0x0d, Kelvin > 0: CCT
])
def test_the_h60a6_burst_mode_folds_from_the_tlv(mode: bytes, rgb: object, kelvin: object) -> None:
    profile = profile_for("H60A6")
    assert profile is not None and profile.mode_color_parse == "kelvin_15_rgb_kelvin_0d"
    state = DeviceState(rgb_color=(9, 9, 9))
    _fold.apply_status(profile, state, reassemble.StatusReply(mode=mode))
    assert (state.rgb_color, state.color_temp_kelvin) == (rgb, kelvin)


def test_no_library_module_builds_a_frame_by_hand() -> None:
    """Ratchet: every frame the library builds goes through the spec's writers; the raw
    ``wire.build.frame`` helper stays for tests and tools only."""
    raw_call = re.compile(r"\bbuild\.frame\(|^\s*return frame\(", re.MULTILINE)
    callers = [str(p.relative_to(_SRC)) for p in _SRC.rglob("*.py")
               if "_generated" not in p.parts and raw_call.search(p.read_text())]
    assert callers == []
    assert not hasattr(_fold, "mode_reply_frame")
