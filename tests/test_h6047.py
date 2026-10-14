"""H6047 (goodsType 119, two-bar RGBIC light bar, old ``NewDetailAc`` screen): golden frames and
read-back, derived from the decompiled Govee Home 7.7.02 app (see the ``h6047`` family in
spec/devices.yaml and issue #13). None of this is hardware-verified.

Sources: ``NewDetailVm.switchTrigger`` -> ``ControllerSwitch`` (whole-device power ``33 01``),
``NewDetailVm.D5`` (per-bar ``33 36 <left> <right>``), ``NewDetailVm.n5`` /
``Support.getBulbStringMaxNum(119) = 10`` (read-back piece count), ``SubModeColor.parse`` and
``Info4Detail`` (``aa 05 15`` = ``[gradual][kelvin left u2be][kelvin right u2be]``),
``ColorModeControllerConfig.p`` -> ``Config.G[119]`` -> ``Bytes.w`` (gradual ``a3 <on>``).
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from conftest import require_optional
from fake_ble import ble_device

from govee_ble_local import Capability, Encryption
from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import profile_for
from govee_ble_local.exceptions import GoveeBleError, GoveeBleInvalidValue, GoveeBleNotSupported
from govee_ble_local.wire import build as b
from govee_ble_local.wire import parse


def _frame(*payload: int) -> bytes:
    buf = bytearray(20)
    buf[: len(payload)] = bytes(payload)
    x = 0
    for c in buf[:19]:
        x ^= c
    buf[19] = x
    return bytes(buf)


def _hex(frame: bytes) -> str:
    return frame.hex()


def _dev(replies: dict[int, Any] | None = None) -> Device:
    """Device with a mocked connection. ``replies`` maps frame[1] (command byte) to a list of reply
    frames, or to a function of the request frame returning that list."""
    d = make_device(ble_device("AA:BB:CC:DD:EE:47", "H6047"), "H6047")
    assert d is not None
    sent: list[bytes] = []
    queried: list[bytes] = []
    table = replies or {}

    async def send(frame: bytes, **_k: object) -> None:
        sent.append(frame)

    async def send_many(frames: list[bytes]) -> None:
        sent.extend(frames)

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        queried.append(frame)
        r = table.get(frame[1], [])
        return list(r(frame) if callable(r) else r)

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    d._sent = sent  # type: ignore[attr-defined]
    d._queried = queried  # type: ignore[attr-defined]
    return d


def _sent(d: Device) -> list[bytes]:
    return list(d._sent)  # type: ignore[attr-defined]


def _queried(d: Device) -> list[bytes]:
    return list(d._queried)  # type: ignore[attr-defined]


def _pad(hex_payload: str) -> str:
    """A golden frame from its payload hex: zero-pad to 19 bytes and append the XOR checksum."""
    raw = bytes.fromhex(hex_payload)
    return _hex(_frame(*raw))


def _group_reply(frame: bytes) -> list[bytes]:
    """The device's ``aa a5 <group>`` reply: 4 records per group, brightness 50+seg, R = seg."""
    g = frame[2]
    recs: list[int] = []
    for i in range(4):
        seg = (g - 1) * 4 + i
        recs += [50 + seg, seg, 0, 0]
    return [_frame(0xAA, 0xA5, g, *recs)]


# ── profile ──────────────────────────────────────────────────────────────────
def test_profile() -> None:
    p = profile_for("H6047")
    assert p is not None
    assert p.capabilities == {Capability.POWER, Capability.BRIGHTNESS, Capability.RGB,
                              Capability.COLOR_TEMP, Capability.SEGMENTS, Capability.SCENES}
    assert p.segments == 10 and [z.name for z in p.zones] == ["left", "right"]
    assert p.zones[0].segments == (0, 1, 2, 3, 4) and p.zones[1].segments == (5, 6, 7, 8, 9)
    assert (p.min_kelvin, p.max_kelvin) == (2200, 6500)
    assert p.encryption is Encryption.NONE and p.bar_switch and not p.global_power
    assert p.readback == "polled" and p.scene_versions == frozenset({0, 1, 2, 3})
    # the old detail screen reads getBulbStringMaxNum(119) = 10 pieces, so no separate read-back
    # count (getColorPieceSize = 12 belongs to the newdetail screens 277/288/298 only)
    assert p.color_readback == "mechanism_a_direct" and p.color_readback_per_batch == 4
    assert p.color_readback_segments is None and not p.color_readback_live_ic
    # gradual: read on connect and written by the colour screen
    assert p.gradual is True


# ── power: 33 01 whole device, 33 36 per bar ─────────────────────────────────
def test_whole_device_power_is_33_01() -> None:
    d = _dev()
    asyncio.run(d.set_power(False))
    asyncio.run(d.set_power(True))
    assert [_hex(f) for f in _sent(d)] == [_pad("330100"), _pad("330101")]
    assert _sent(d)[0].hex() == "330100" + "00" * 16 + "32"     # BCC = 33 ^ 01 ^ 00
    assert _sent(d)[1].hex() == "330101" + "00" * 16 + "33"      # BCC = 33 ^ 01 ^ 01


def test_whole_device_off_marks_both_bars_off_on_leaves_them_unknown() -> None:
    d = _dev()
    asyncio.run(d.set_power(False))
    assert d.state.is_on is False and d.state.zone_power == {0: False, 1: False}
    asyncio.run(d.set_power(True))
    # the app learns which bars lit from the next aa 36 heartbeat; so does the library
    assert d.state.is_on is True and d.state.zone_power == {}


def test_zone_power_after_whole_device_on_rereads_the_bars() -> None:
    d = _dev({0x36: [_frame(0xAA, 0x36, 0x01, 0x01)]})
    asyncio.run(d.set_power(True))
    asyncio.run(d.set_zone_power("left", False))
    assert [f[:2].hex() for f in _queried(d)] == ["aa36"]
    assert _hex(_sent(d)[-1]) == _pad("33360001")               # right bar kept on


def test_turn_on_uses_global_power_frame() -> None:
    d = _dev()
    asyncio.run(d.turn_on(brightness=50))
    assert [f[:3].hex() for f in _sent(d)] == ["330101", "330432"]


def test_zone_power_is_the_combined_33_36_frame() -> None:
    d = _dev()
    d.state.zone_power = {0: True, 1: True}
    asyncio.run(d.set_zone_power("left", False))
    asyncio.run(d.set_zone_power("right", False))
    asyncio.run(d.set_zone_power("left", True))
    assert [_hex(f) for f in _sent(d)] == [
        _pad("33360001"), _pad("33360000"), _pad("33360100")]
    assert _sent(d)[0].hex() == "33360001" + "00" * 15 + "04"     # BCC = 33 ^ 36 ^ 00 ^ 01
    assert d.state.zone_power == {0: True, 1: False} and d.state.is_on is True


def test_zone_power_unknown_other_bar_is_read_first() -> None:
    d = _dev({0x36: [_frame(0xAA, 0x36, 0x00, 0x01, 0x00)]})
    asyncio.run(d.set_zone_power("right", False))
    assert [f[:2].hex() for f in _queried(d)] == ["aa36"]
    assert _hex(_sent(d)[0]) == _pad("33360000")
    assert d.state.is_on is False


def test_zone_power_unreadable_other_bar_refuses_to_guess() -> None:
    d = _dev()
    with pytest.raises(GoveeBleError, match="unknown"):
        asyncio.run(d.set_zone_power("left", True))
    assert _sent(d) == []


# ── colour frames the app sends ──────────────────────────────────────────────
def test_brightness_is_a_percent() -> None:
    d = _dev()
    asyncio.run(d.set_brightness(100))
    asyncio.run(d.set_brightness(1))
    assert [f[:3].hex() for f in _sent(d)] == ["330464", "330401"]


def test_rgb_whole_device_and_zone_goldens() -> None:
    d = _dev()
    asyncio.run(d.set_rgb((255, 0, 0)))
    asyncio.run(d.set_zone_rgb("right", (0, 0, 255)))
    assert [_hex(f)[:28] for f in _sent(d)] == [
        "33051501" "ff0000" "0000000000" "ff03",   # 15 01 r g b 00x5 mask 0x03ff
        "33051501" "0000ff" "0000000000" "e003",   # right bar = segments 5..9 = mask 0x03e0
    ]


def test_segment_brightness_golden() -> None:
    d = _dev()
    asyncio.run(d.set_segment_brightness([0, 1, 2, 3, 4], 50))
    assert _hex(_sent(d)[0])[:14] == "33051502" "32" "1f00"          # 15 02 <pct> mask lo hi


@pytest.mark.parametrize(("kelvin", "k_hex", "tint_hex"), [
    (2200, "0898", "ff9829"), (4000, "0fa0", "ffd5a1"), (6500, "1964", "fff9fb"),
])
def test_cct_frames_carry_the_tint_per_bar(kelvin: int, k_hex: str, tint_hex: str) -> None:
    d = _dev()
    asyncio.run(d.set_color_temp(kelvin))
    asyncio.run(d.set_zone_color_temp("left", kelvin))
    asyncio.run(d.set_zone_color_temp("right", kelvin))
    head = "33051501ffffff" + k_hex + tint_hex
    assert [_hex(f)[:28] for f in _sent(d)] == [
        head + "ff03",       # whole device, mask 0x03ff
        head + "1f00",       # left bar, segments 0..4
        head + "e003",       # right bar, segments 5..9
    ]


def test_cct_range_is_2200_to_6500() -> None:
    d = _dev()
    for bad in (2100, 6600):
        with pytest.raises(GoveeBleInvalidValue):
            asyncio.run(d.set_color_temp(bad))
    assert _sent(d) == []


# ── gradual ──────────────────────────────────────────────────────────────────
def test_gradual_write_is_33_a3() -> None:
    d = _dev()
    asyncio.run(d.set_gradual(True))
    asyncio.run(d.set_gradual(False))
    assert [_hex(f) for f in _sent(d)] == [_pad("33a301"), _pad("33a300")]
    assert _sent(d)[0].hex() == "33a301" + "00" * 16 + "91"        # BCC = 33 ^ a3 ^ 01
    assert d.state.gradual is False


def test_gradual_not_supported_where_the_profile_lacks_it() -> None:
    d = make_device(ble_device("AA:BB:CC:DD:EE:48", "H6008"), "H6008")
    assert d is not None
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(d.set_gradual(True))


# ── read-back parse ──────────────────────────────────────────────────────────
def test_parse_bar_kelvin() -> None:
    f = _frame(0xAA, 0x05, 0x15, 0x01, 0x0A, 0x8C, 0x0F, 0xA0)
    assert parse.parse_bar_kelvin(f) == (2700, 4000)
    assert parse.parse_kelvin(f) == 2700                      # the left kelvin, as before
    assert parse.parse_bar_kelvin(_frame(0xAA, 0x05, 0x15, 0x00, 0, 0, 0, 0)) == (0, 0)
    assert parse.parse_bar_kelvin(_frame(0xAA, 0x05, 0x04, 0x09, 0x00)) is None   # scene mode
    assert parse.parse_bar_kelvin(_frame(0xAA, 0x04, 0x15)) is None               # not a mode reply


def test_ksy_reads_gradual_two_kelvins_and_the_feast_byte() -> None:
    require_optional("kaitaistruct", reason="Kaitai runtime not installed")
    gb = require_optional("govee_ble_local._generated.govee_ble_frame")
    mode = gb.GoveeBleFrame.from_bytes(_frame(0xAA, 0x05, 0x15, 0x01, 0x0A, 0x8C, 0x0F, 0xA0)).body.body
    rest = mode.rest
    assert (rest.op, rest.kelvin, rest.kelvin_right) == (1, 2700, 4000)    # [gradual][kL][kR]
    bars = gb.GoveeBleFrame.from_bytes(_frame(0xAA, 0x36, 0x01, 0x00, 0x01)).body.body
    assert (bars.left, bars.right, bars.movie_feast) == (1, 0, 1)


# ── read-back through update() ───────────────────────────────────────────────
def _polled(mode_reply: bytes, bars: bytes | None = None) -> dict[int, Any]:
    return {
        0x01: [_frame(0xAA, 0x01, 0x01)],
        0x04: [_frame(0xAA, 0x04, 0x32)],
        0x36: [bars or _frame(0xAA, 0x36, 0x01, 0x01, 0x00)],
        0x05: [mode_reply],
        0xA5: _group_reply,
        0xA3: [_frame(0xAA, 0xA3, 0x01)],
    }


def test_update_reads_ten_segments_not_twelve() -> None:
    d = _dev(_polled(_frame(0xAA, 0x05, 0x15, 0x01, 0, 0, 0, 0)))
    asyncio.run(d.update())
    assert [f[:3].hex() for f in _queried(d) if f[1] == 0xA5] == ["aaa501", "aaa502", "aaa503"]
    assert [s.index for s in d.state.segments] == list(range(10))     # no phantom segments 10, 11
    assert d.state.segments[9].brightness == 59                        # the last record of group 3


def test_update_reads_gradual() -> None:
    d = _dev(_polled(_frame(0xAA, 0x05, 0x15, 0x01, 0, 0, 0, 0)))
    asyncio.run(d.update())
    assert d.state.gradual is True
    assert any(f[:2].hex() == "aaa3" for f in _queried(d))     # the aa a3 read is issued


def test_update_on_when_either_bar_is_on() -> None:
    mode = _frame(0xAA, 0x05, 0x15, 0x01, 0, 0, 0, 0)
    for bars, on in ((b"\x01\x00", True), (b"\x00\x01", True), (b"\x00\x00", False)):
        replies = _polled(mode, _frame(0xAA, 0x36, *bars))
        replies[0x01] = [_frame(0xAA, 0x01, 0x00)]        # aa 01 disagrees: the bar heartbeat wins
        d = _dev(replies)
        asyncio.run(d.update())
        assert d.state.is_on is on
        assert d.state.zone_power == {0: bool(bars[0]), 1: bool(bars[1])}


def test_update_exposes_a_kelvin_per_bar() -> None:
    d = _dev(_polled(_frame(0xAA, 0x05, 0x15, 0x01, 0x0A, 0x8C, 0x0F, 0xA0)))
    asyncio.run(d.update())
    s = d.state
    assert s.zone_color_temp_kelvin == {0: 2700, 1: 4000}
    assert d.zone_color_temp_kelvin("left") == 2700 and d.zone_color_temp_kelvin("right") == 4000
    assert s.color_temp_kelvin is None           # whole-device kelvin only when both bars match
    assert s.scene_code is None


def test_update_whole_device_kelvin_when_both_bars_match() -> None:
    d = _dev(_polled(_frame(0xAA, 0x05, 0x15, 0x01, 0x0F, 0xA0, 0x0F, 0xA0)))
    asyncio.run(d.update())
    assert d.state.color_temp_kelvin == 4000
    assert d.state.zone_color_temp_kelvin == {0: 4000, 1: 4000}


def test_update_one_bar_in_cct_the_other_in_colour() -> None:
    d = _dev(_polled(_frame(0xAA, 0x05, 0x15, 0x01, 0x0F, 0xA0, 0x00, 0x00)))
    asyncio.run(d.update())
    assert d.state.zone_color_temp_kelvin == {0: 4000}
    assert d.zone_color_temp_kelvin("right") is None and d.state.color_temp_kelvin is None


def test_update_colour_mode_clears_kelvin() -> None:
    d = _dev(_polled(_frame(0xAA, 0x05, 0x15, 0x01, 0, 0, 0, 0)))
    d.state.color_temp_kelvin, d.state.zone_color_temp_kelvin = 4000, {0: 4000, 1: 4000}
    asyncio.run(d.update())
    assert d.state.color_temp_kelvin is None and d.state.zone_color_temp_kelvin == {}


def test_zone_kelvin_unknown_zone_rejected() -> None:
    with pytest.raises(GoveeBleNotSupported):
        _dev().zone_color_temp_kelvin("middle")


# ── optimistic state after writes ────────────────────────────────────────────
def test_writes_keep_per_bar_kelvin_state_consistent() -> None:
    d = _dev()
    asyncio.run(d.set_color_temp(4000))
    assert d.state.zone_color_temp_kelvin == {0: 4000, 1: 4000} and d.state.color_temp_kelvin == 4000
    asyncio.run(d.set_zone_color_temp("left", 2700))
    assert d.state.zone_color_temp_kelvin == {0: 2700, 1: 4000} and d.state.color_temp_kelvin is None
    asyncio.run(d.set_zone_color_temp("right", 2700))
    assert d.state.color_temp_kelvin == 2700
    asyncio.run(d.set_zone_rgb("left", (1, 2, 3)))
    assert d.state.zone_color_temp_kelvin == {1: 2700} and d.state.color_temp_kelvin is None
    asyncio.run(d.set_rgb((1, 2, 3)))
    assert d.state.zone_color_temp_kelvin == {}
    asyncio.run(d.turn_on(kelvin=3000))
    assert d.state.zone_color_temp_kelvin == {0: 3000, 1: 3000}
    asyncio.run(d.set_scene(1))
    assert d.state.zone_color_temp_kelvin == {} and d.state.color_temp_kelvin is None


def test_other_zoned_devices_do_not_gain_zone_kelvin() -> None:
    d = make_device(ble_device("AA:BB:CC:DD:EE:60", "H60A6"), "H60A6")
    assert d is not None
    d._conn.send = AsyncMock()  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock()  # type: ignore[method-assign]
    asyncio.run(d.set_zone_color_temp("background", 4000))
    asyncio.run(d.set_color_temp(4000))
    assert d.state.zone_color_temp_kelvin == {}


# ── builders ─────────────────────────────────────────────────────────────────
def test_builders_golden() -> None:
    assert _hex(b.switch(True)) == _pad("330101")
    assert _hex(b.bar_switch(True, False)) == _pad("33360100")
    assert _hex(b.bar_switch_query()) == _hex(_frame(0xAA, 0x36))
