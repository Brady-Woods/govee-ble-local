"""The aa 05 mode decoders read the spec's ``mode_report``, from a reply frame or from the 0xAC 0x05 TLV.

Both sources are read through a root under the device's mode decode (``devices.profile.wire_profile``):
the reply through the frame (``parse.mode_reply``), the TLV in the status stream
(``reassemble.parse_status``), so the burst's mode no longer goes through a synthesised ``aa 05`` frame,
and the app's sign and CCT tests come from the spec's layout instances (``is_cct``, ``shows_tint``) rather
than hand slices of the frame. No hardware."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, get_args

import pytest

from govee_ble_local.devices import _fold
from govee_ble_local.devices.profile import DeviceProfile, ModeColorParse, profile_for, wire_profile
from govee_ble_local.models import DeviceState
from govee_ble_local.wire import build, parse, reassemble
from govee_ble_local.wire.describe import status_burst_report

from fake_ble import status_burst

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


def _tlv_mode(body: bytes, prof: Any) -> Any:
    """The 0x05 TLV value ``body`` as the status read takes it: a burst carrying it alone, read through the
    status stream root with ``prof``'s colour decode (None when the read fails)."""
    return reassemble.parse_status(status_burst(bytes([0x05, len(body)]) + body), decode=prof).mode


def _profile(parse_name: str, *, bar_switch: bool = False) -> Any:
    """The wire profile of a profile on ``parse_name`` (the mode decode alone matters here)."""
    return wire_profile(DeviceProfile(skus=("TEST",), capabilities=frozenset(), bar_switch=bar_switch,
                                      mode_color_parse=parse_name))  # type: ignore[arg-type]


_BODIES = [
    (0x0D, 1, 2, 3, 0x0A, 0x8C, 4, 5, 6),   # [r, g, b][kelvin 2700][tint]
    (0x0D, 1, 2, 3, 0x80, 0x00, 4, 5, 6),   # a negative signed Kelvin
    (0x15, 0x01, 0x0A, 0x8C),               # [gradual][kelvin 2700]
    (0x15, 0x01, 0xFF, 0xFF),               # [gradual][kelvin -1]
    (0x04, 0x2A, 0x01),                     # scene 0x012A
]
_PROFILES = [_profile(name) for name in get_args(ModeColorParse)] + [_profile("kelvin_15", bar_switch=True)]


@pytest.mark.parametrize("body", _BODIES)
def test_a_tlv_mode_and_an_aa05_reply_read_alike(body: tuple[int, ...]) -> None:
    # The TLV carries the reply's 17-byte mode body (a value short of a profile's layout fails the read:
    # test_a_short_tlv_mode_fails_the_read_and_a_long_one_reads).
    value = bytes(body).ljust(17, b"\0")
    for prof in _PROFILES:
        from_reply, from_tlv = parse.mode_reply(_reply(*body), prof), _tlv_mode(value, prof)
        for decode in (parse.sub_mode, parse.scene_of, parse.mode_color):
            assert decode(from_tlv) == decode(from_reply), decode.__name__


def test_a_short_tlv_mode_fails_the_read_and_a_long_one_reads() -> None:
    """A value short of its sub-mode's layout fails the whole status read, as h60a1 Info4Detail.Q throws
    (WAS: zero padded into made-up values, e.g. 2560 K out of ``15 01 0a``); a long one reads whatever follows
    the layout. An empty one is the H60A6's own, read past (the next test)."""
    h60a6 = _profile("kelvin_15_rgb_kelvin_0d")
    switch = bytes([0x01, 0x01, 0x01])   # a TLV after it, so the mode isn't the stream's tail
    for short in (bytes([0x15, 0x01, 0x0A]), bytes([0x0D, 1, 2, 3, 4]), bytes([0x04, 0x2A])):
        burst = status_burst(bytes([0x05, len(short)]) + short + switch)
        assert reassemble.parse_status(burst, decode=h60a6) == reassemble.StatusReply(), short.hex()
    long_value = bytes([0x0D, 1, 2, 3, 0x0A, 0x8C, *range(40)])
    assert parse.mode_color(_tlv_mode(long_value, h60a6)) == (None, 2700)


def test_h60a6s_empty_mode_value_is_read_past_and_every_other_failure_still_fails() -> None:
    # FIRMWARE-SOURCED (rule 6; govee_state.app_empty_mode_fails): H60A6 SW1.00.41 sends an empty [05][00] in
    # music mode (0xA0) and every mode but 04 / 0a / 0d / 13 / 15 (FUN_9b025dc8 @0x9b025dd2-0x9b025ddc). The
    # app's read fails on it (Info4Detail.Q reads a first byte), so its power and brightness go stale; the
    # library reads past it with the mode unknown and applies the rest of the burst.
    h60a6 = profile_for("H60A6")
    assert h60a6 is not None
    prof = wire_profile(h60a6)

    def read(tlvs: bytes) -> reassemble.StatusReply:
        return reassemble.parse_status(status_burst(tlvs), pieces=13, decode=prof)

    # The firmware's own mid-burst layout: switch, brightness, the empty mode, the zones.
    tlvs = bytes([0x01, 0x01, 0x01, 0x04, 0x01, 0x50, 0x05, 0x00, 0x30, 0x02, 0x01, 0x01])
    st = read(tlvs)
    assert (st.is_on, st.brightness, st.zone_power, st.mode) == (True, 0x50, {0: True, 1: True}, None)
    state = DeviceState(scene_code=22)
    assert _fold.apply_status(h60a6, state, st) == (True, True)
    assert state.scene_code == 22 and (state.is_on, state.brightness) == (True, 80)   # mode left as it was
    assert status_burst_report(status_burst(tlvs)).gaps == []    # by design, not a spec gap
    # Followed by any byte it is a TLV, read past; ending the stream it is the 2-byte tail, which never fails.
    for after in (b"\x01", b"\x01\x01", bytes([0x04, 0x01, 0x32]), bytes([0x05, 0x00, 0x01, 0x01, 0x01])):
        assert read(bytes([0x05, 0x00]) + after).brightness is not None, after.hex()
    assert read(bytes([0x01, 0x01, 0x01, 0x05, 0x00])).is_on is True
    # Every other value the app's read fails on still fails it: the empty switch, brightness, device-info,
    # timers, zones, power memory and colour group, a mode value short of its layout, a short timer list
    # and a colour group short of its records.
    switch, bri = bytes([0x01, 0x01, 0x01]), bytes([0x04, 0x01, 0x32])
    for bad in ([0x01, 0x00], [0x04, 0x00], [0x07, 0x00], [0x23, 0x00], [0x30, 0x00], [0x41, 0x00],
                [0xA5, 0x00], [0x05, 0x03, 0x15, 0x01, 0x0A], [0x05, 0x01, 0x04], [0x23, 0x01, 0x00],
                [0xA5, 0x05, 0x01, 0x32, 1, 2, 3]):
        assert read(switch + bytes(bad) + bri) == reassemble.StatusReply(), bytes(bad).hex()


def test_the_signed_kelvin_tests_come_from_the_spec() -> None:
    def tint_report(k_hi: int, k_lo: int) -> object:
        mode = _tlv_mode(bytes([0x0D, 1, 2, 3, k_hi, k_lo, 7, 8, 9]), _profile("rgb_kelvin_tint"))
        return parse.mode_color(mode)

    # ModeParseStrategyConfig.a + parseModeValidBytes: CCT only for > 0; a negative Kelvin shows the tint.
    assert tint_report(0x0A, 0x8C) == (None, 2700)
    assert tint_report(0x80, 0x00) == ((7, 8, 9), None)
    assert tint_report(0x00, 0x00) == ((1, 2, 3), None)
    # rgb_kelvin_0d (H6061 / H60A6 0x0d): signed > 0 is CCT, anything else the RGB.
    rgb_kelvin_0d = _tlv_mode(bytes([0x0D, 1, 2, 3, 0x80, 0x00]), _profile("rgb_kelvin_0d"))
    assert parse.mode_color(rgb_kelvin_0d) == ((1, 2, 3), None)
    # The default rgb_kelvin 0x0d parse: ANY non-zero Kelvin is CCT, read unsigned.
    default = _tlv_mode(bytes([0x0D, 1, 2, 3, 0x80, 0x00, 0, 0, 0]), _profile("rgb_kelvin"))
    assert parse.mode_color(default, unsigned_kelvin=True) == (None, 0x8000)
    # SubMode4Color's default [gradual][kelvin s16be].
    gradual = _tlv_mode(bytes([0x15, 0x01, 0xFF, 0xFF]), _profile("kelvin"))
    assert parse.mode_color(gradual) == parse.KelvinReport(-1)


@pytest.mark.parametrize(("mode", "rgb", "kelvin"), [
    (bytes([0x15, 0x01, 0x0A, 0x8C]), None, 2700),          # 0x15 [gradual][kelvin]: CCT
    (bytes([0x0D, 1, 2, 3, 0x80, 0x00]), (1, 2, 3), None),  # 0x0d, a negative Kelvin: the RGB
    (bytes([0x0D, 1, 2, 3, 0x0A, 0x8C]), None, 2700),       # 0x0d, Kelvin > 0: CCT
])
def test_the_h60a6_burst_mode_folds_from_the_tlv(mode: bytes, rgb: object, kelvin: object) -> None:
    profile = profile_for("H60A6")
    assert profile is not None and profile.mode_color_parse == "kelvin_15_rgb_kelvin_0d"
    state = DeviceState(rgb_color=(9, 9, 9))
    burst = status_burst(bytes([0x05, len(mode)]) + mode)
    status = reassemble.parse_status(burst, decode=wire_profile(profile))
    _fold.apply_status(profile, state, status)
    assert (state.rgb_color, state.color_temp_kelvin) == (rgb, kelvin)


def test_no_library_module_builds_a_frame_by_hand() -> None:
    """Ratchet: every frame the library builds goes through the spec's writers; there is no raw frame
    helper (tests build raw frames with ``fake_ble.frame``)."""
    raw_call = re.compile(r"\bbuild\.frame\(|^\s*return frame\(", re.MULTILINE)
    callers = [str(p.relative_to(_SRC)) for p in _SRC.rglob("*.py")
               if "_generated" not in p.parts and raw_call.search(p.read_text())]
    assert callers == []
    assert not hasattr(build, "frame")
    assert not hasattr(_fold, "mode_reply_frame")
