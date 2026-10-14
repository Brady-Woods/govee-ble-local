"""Colour-temperature frames carry the Kelvin tint the Govee app sends (issue #19).

Golden frames are derived from the decompiled Govee Home 7.7.02 app, never from the library:

* 0x15 white-slot form ``15 01 ff ff ff <K u2be> <tint> <mask u2le>``: ``Bytes.r(colour, K, mask,
  type=1)`` (H6047 ``SubModeColor.makeSubModeColor4Kelvin`` / ``Config.Yt``, H6641 ``Config.x[247]``
  -> ``Config.Yt``, H60A6 ``ControllerBuild.d``).
* H60A6 whole-device CCT is two frames, main panel (mask bit 12) then ring (bits 0..11):
  ``VM4LightH60A6.configColorStripController`` -> ``ControllerBuild.j``, gated on
  ``Support.supportPartTem`` (pact type 1, code >= 3).
* 0x0d both-slot form ``0d <tint> <K u2be> <tint>``: ``Config.bu`` -> ``Bytes.p`` (H6008, H6006,
  H6052). Distinct from H6188's ``0d ff ff ff <K> <tint>`` (``Bytes.A``), covered in test_h6188.py.
* Kelvin: clamped to the device range (``KelvinConfig.calibrationKelvin``) and whole 100 K steps.

Tints used (``Constant.getTemColorByKelvin``): 2000 K ff8d0b, 2200 K ff9829, 2700 K ffae54,
4000 K ffd5a1, 6500 K fff9fb, 9000 K d9e1ff.
"""
from __future__ import annotations

import asyncio
import dataclasses
from typing import Any
from unittest.mock import AsyncMock

import pytest
from conftest import require_optional
from fake_ble import ble_device

from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import profile_for
from govee_ble_local.exceptions import GoveeBleInvalidValue
from govee_ble_local.wire import build as b
from govee_ble_local.wire import parse
from govee_ble_local.wire.cct import tint_for_kelvin

# bleak splits the Govee AD into company id (hdr low byte, marker 0x88 high byte) + value
# [0xEC, pactType u16be, pactCode, on/off]
_ON = 0x01


def _adv(sku: str, pact_type: int, pact_code: int) -> Any:
    value = bytes([0xEC, pact_type >> 8, pact_type & 0xFF, pact_code, 0x01])
    return type("Adv", (), {"manufacturer_data": {0x8801: value}, "local_name": f"Govee_{sku}_ABCD"})()


def _frame(*payload: int) -> bytes:
    buf = bytearray(20)
    buf[: len(payload)] = bytes(payload)
    x = 0
    for c in buf[:19]:
        x ^= c
    buf[19] = x
    return bytes(buf)


def _dev(sku: str, adv: Any = None) -> Device:
    d = make_device(ble_device("AA:BB:CC:DD:EE:19", sku), sku, adv)
    assert d is not None
    sent: list[bytes] = []

    async def send(frame: bytes, **_k: object) -> None:
        sent.append(frame)

    async def send_many(frames: list[bytes]) -> None:
        sent.extend(frames)

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._sent = sent  # type: ignore[attr-defined]
    return d


def _sent(d: Device) -> list[bytes]:
    return d._sent  # type: ignore[attr-defined, no-any-return]


def _hex14(f: bytes) -> str:
    return f[:14].hex()


# ── 0x15 white-slot form ─────────────────────────────────────────────────────
def test_segment_color_temp_carries_the_tint_in_bytes_9_to_11() -> None:
    f = b.segment_color_temp(0x1000, 4000, "h60a6")
    # 33 05 | 15 01 | ff ff ff | 0f a0 | ff d5 a1 | 00 10
    assert _hex14(f) == "3305" "1501" "ffffff" "0fa0" "ffd5a1" "0010"
    assert f[14:19] == bytes(5) and len(f) == 20


@pytest.mark.parametrize(("kelvin", "k_hex", "tint_hex"), [
    (2200, "0898", "ff9829"), (2700, "0a8c", "ffae54"), (6500, "1964", "fff9fb"), (9000, "2328", "d9e1ff"),
])
def test_whole_device_0x15_cct_goldens(kelvin: int, k_hex: str, tint_hex: str) -> None:
    f = b.color_temp(kelvin, "h60a6", 10)    # H6047 / H6641-style single frame, 10 segments
    assert _hex14(f) == "3305" "1501" "ffffff" + k_hex + tint_hex + "ff03"
    assert f[9:12] == bytes(tint_for_kelvin(kelvin))


def test_h6047_whole_device_cct_is_one_frame_with_tint() -> None:
    d = _dev("H6047")
    asyncio.run(d.set_color_temp(4000))
    assert [_hex14(f) for f in _sent(d)] == ["33051501ffffff0fa0ffd5a1ff03"]   # mask 0x03ff


def test_h6641_whole_device_cct_is_one_frame_with_tint() -> None:
    d = _dev("H6641")
    asyncio.run(d.set_color_temp(2000))
    assert [_hex14(f) for f in _sent(d)] == ["33051501ffffff07d0ff8d0bffff"]   # 16 segments


def test_h6047_zone_and_segment_cct_carry_tint() -> None:
    d = _dev("H6047")
    asyncio.run(d.set_zone_color_temp("left", 2700))
    asyncio.run(d.set_segment_color_temp([5, 6], 2700))
    assert [_hex14(f) for f in _sent(d)] == [
        "33051501ffffff0a8cffae541f00",     # left bar = segments 0..4
        "33051501ffffff0a8cffae546000",     # segments 5, 6
    ]


# ── H60A6: two frames (main panel, ring), gated on the advertised pact ───────
_MAIN_4000 = "33051501ffffff0fa0ffd5a10010"
_RING_4000 = "33051501ffffff0fa0ffd5a1ff0f"
_ALL_4000 = "33051501ffffff0fa0ffd5a1ff1f"


@pytest.mark.parametrize("adv", [None, _adv("H60A6", 1, 3), _adv("H60A6", 1, 9)])
def test_h60a6_whole_device_cct_is_main_panel_then_ring(adv: Any) -> None:
    d = _dev("H60A6", adv)
    asyncio.run(d.set_color_temp(4000))
    assert [_hex14(f) for f in _sent(d)] == [_MAIN_4000, _RING_4000]
    d._conn.send_many.assert_awaited_once()  # type: ignore[attr-defined]  # one ack-ordered batch
    assert d.state.color_temp_kelvin == 4000 and d.state.rgb_color is None


@pytest.mark.parametrize("adv", [_adv("H60A6", 1, 2), _adv("H60A6", 1, 0), _adv("H60A6", 2, 9)])
def test_h60a6_below_the_pact_gate_sends_one_all_segments_frame(adv: Any) -> None:
    # Below supportPartTem the app takes ColorModeControllerConfig.w (not modelled): the single
    # all-segments frame, now with its tint.
    d = _dev("H60A6", adv)
    asyncio.run(d.set_color_temp(4000))
    assert [_hex14(f) for f in _sent(d)] == [_ALL_4000]
    d._conn.send.assert_awaited_once()  # type: ignore[attr-defined]


def test_h60a6_pact_is_learned_from_the_advertisement() -> None:
    d = _dev("H60A6")
    assert d.pact is None
    d.ingest_advertisement(_adv("H60A6", 1, 2))
    assert d.pact == (1, 2)
    asyncio.run(d.set_color_temp(4000))
    assert [_hex14(f) for f in _sent(d)] == [_ALL_4000]
    d.ingest_advertisement(_adv("H60A6", 1, 5))
    assert d.pact == (1, 5)


def test_unparseable_advertisement_does_not_set_pact() -> None:
    bad = type("Adv", (), {"manufacturer_data": {0x1234: b"\x00" * 4}, "local_name": "Govee_H60A6_ABCD"})()
    d = _dev("H60A6", bad)
    assert d.pact is None


def test_h60a6_zone_cct_is_one_frame_with_tint() -> None:
    d = _dev("H60A6")
    asyncio.run(d.set_zone_color_temp("main", 4000))
    asyncio.run(d.set_zone_color_temp("background", 4000))
    assert [_hex14(f) for f in _sent(d)] == [_MAIN_4000, _RING_4000]


def test_h60a6_turn_on_with_kelvin_batches_the_two_cct_frames() -> None:
    d = _dev("H60A6")
    asyncio.run(d.turn_on(brightness=40, kelvin=4000))
    assert [f[:3].hex() for f in _sent(d)] == ["330101", "330428", "330515", "330515"]
    assert [_hex14(f) for f in _sent(d)[2:]] == [_MAIN_4000, _RING_4000]
    d._conn.send_many.assert_awaited_once()  # type: ignore[attr-defined]


def test_profile_cct_gate_fields() -> None:
    assert profile_for("H60A6").cct_per_zone_min_pact == (1, 3)  # type: ignore[union-attr]
    for sku in ("H6047", "H6641", "H6008", "H6006", "H6052", "H6188", "H61A8"):
        assert profile_for(sku).cct_per_zone_min_pact is None, sku  # type: ignore[union-attr]
    p = profile_for("H6047")
    assert p is not None
    with pytest.raises(ValueError, match="cct_per_zone_min_pact"):
        dataclasses.replace(p, zones=(), cct_per_zone_min_pact=(1, 3))


# ── 0x0d both-slot form ──────────────────────────────────────────────────────
@pytest.mark.parametrize(("kelvin", "k_hex", "tint_hex"), [
    (2700, "0a8c", "ffae54"), (4000, "0fa0", "ffd5a1"), (6500, "1964", "fff9fb"),
])
def test_both_slot_0d_goldens(kelvin: int, k_hex: str, tint_hex: str) -> None:
    f = b.color_temp(kelvin, "h6006")
    assert f[:11].hex() == "33050d" + tint_hex + k_hex + tint_hex
    assert f[11:19] == bytes(8) and len(f) == 20


@pytest.mark.parametrize("sku", ["H6008", "H6006", "H6052"])
def test_bulbs_and_lamp_send_the_both_slot_form(sku: str) -> None:
    d = _dev(sku)
    asyncio.run(d.set_color_temp(4000))
    assert _sent(d)[0][:11].hex() == "33050d" "ffd5a1" "0fa0" "ffd5a1"
    asyncio.run(d.turn_on(kelvin=2700))
    assert _sent(d)[-1][:11].hex() == "33050d" "ffae54" "0a8c" "ffae54"


def test_h6006_and_h6052_wide_range_ends() -> None:
    d = _dev("H6052")                   # KelvinConfig [2000, 9000]
    asyncio.run(d.set_color_temp(2000))
    asyncio.run(d.set_color_temp(9000))
    assert [f[:11].hex() for f in _sent(d)] == ["33050d" "ff8d0b" "07d0" "ff8d0b",
                                                 "33050d" "d9e1ff" "2328" "d9e1ff"]


def test_h6188_form_is_unchanged_by_the_both_slot_form() -> None:
    f = b.color_temp(4000, "h6188")
    assert f[:11].hex() == "33050d" "ffffff" "0fa0" "ffd5a1"
    assert f[:11] != b.color_temp(4000, "h6006")[:12]


def test_both_slot_form_round_trips_kaitai() -> None:
    require_optional("kaitaistruct", reason="Kaitai runtime not installed")
    gb = require_optional("govee_ble_local._generated.govee_ble_frame")
    m = gb.GoveeBleFrame.from_bytes(b.color_temp(4000, "h6006")).body.params
    assert m.sub_type == gb.GoveeBleFrame.SubMode.color_cct_0d
    assert (m.params.r, m.params.g, m.params.b, m.params.kelvin) == (0xFF, 0xD5, 0xA1, 4000)
    assert (m.params.tint_r, m.params.tint_g, m.params.tint_b) == (0xFF, 0xD5, 0xA1)


# ── Kelvin: clamp to the device range, whole 100 K steps ─────────────────────
@pytest.mark.parametrize(("raw", "expected"), [
    (2700, 2700), (2749, 2700), (2750, 2800), (2799, 2800), (2701, 2700), (6500, 6500),
])
def test_normalize_kelvin_rounds_to_nearest_100(raw: int, expected: int) -> None:
    assert b.normalize_kelvin(raw) == expected


def test_normalize_kelvin_clamps_to_the_range() -> None:
    assert b.normalize_kelvin(1960, 2000, 9000) == 2000
    assert b.normalize_kelvin(9040, 2000, 9000) == 9000
    # rounding can never leave a range whose edges are not 100 K multiples
    assert b.normalize_kelvin(2240, 2250, 6500) == 2250
    assert b.normalize_kelvin(6560, 2250, 6540) == 6540
    with pytest.raises(GoveeBleInvalidValue):
        b.normalize_kelvin(-1)
    with pytest.raises(GoveeBleInvalidValue):
        b.normalize_kelvin(0x10000)


def test_device_rounds_the_kelvin_it_sends_and_keeps_in_state() -> None:
    d = _dev("H6008")                   # [2700, 6500]
    asyncio.run(d.set_color_temp(2750))
    assert _sent(d)[0][3:11].hex() == "ffb25b" "0af0" "ffb25b"   # 2800 K -> tint ffb25b
    assert d.state.color_temp_kelvin == 2800
    asyncio.run(d.turn_on(kelvin=6499))
    assert _sent(d)[-1][6:8].hex() == "1964" and d.state.color_temp_kelvin == 6500
    # still rejected, not silently clamped, when outside the range (the existing contract)
    with pytest.raises(GoveeBleInvalidValue):
        asyncio.run(d.set_color_temp(2600))


def test_device_rounds_zone_and_segment_cct() -> None:
    d = _dev("H60A6")
    asyncio.run(d.set_zone_color_temp("main", 4049))
    asyncio.run(d.set_segment_color_temp([0], 4051))
    assert [f[7:9].hex() for f in _sent(d)] == ["0fa0", "1004"]   # 4000, 4100


# ── read-back of the new write ───────────────────────────────────────────────
def test_both_slot_report_reads_back_as_cct() -> None:
    # The device holds what the app wrote: aa 05 0d <tint> <K> <tint>. The app treats any non-zero
    # kelvin as CCT (bulblightstringv1 ModeParseStrategyConfig, tablelampv1 SubModeColor.parse).
    d = _dev("H6008")
    d._apply_mode_reply(_frame(0xAA, 0x05, 0x0D, 0xFF, 0xAE, 0x54, 0x0A, 0x8C, 0xFF, 0xAE, 0x54))
    assert d.state.color_temp_kelvin == 2700 and d.state.rgb_color is None
    d._apply_mode_reply(_frame(0xAA, 0x05, 0x0D, 1, 2, 3, 0, 0, 0, 0, 0))
    assert d.state.rgb_color == (1, 2, 3) and d.state.color_temp_kelvin is None


def test_parse_mode_0d_rule() -> None:
    assert parse.parse_mode_0d(_frame(0xAA, 0x05, 0x0D, 9, 8, 7, 0x0F, 0xA0)) == (None, 4000)
    assert parse.parse_mode_0d(_frame(0xAA, 0x05, 0x0D, 9, 8, 7, 0, 0)) == ((9, 8, 7), None)
    assert parse.parse_mode_0d(_frame(0xAA, 0x05, 0x04, 1)) is None
    assert parse.parse_mode_0d(b"\xaa\x05\x0d\x01") is None
