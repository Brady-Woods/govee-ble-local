"""An RGB pick equal to one of the app's Kelvin tint colours is sent as a CCT frame, on every device.

Govee Home 7.7.02 does not always send the RGB frame for a picked colour. When the picked colour's
full ARGB int is exactly one of the 178 colours of the Kelvin tint table (``Constant.k2`` =
``ColorUtils.e``, two per 100 K step) AND that colour's Kelvin is inside the SKU's inclusive
``[lo, hi]`` range (``KelvinConfig.i`` / ``KelvinConfig.c``), the colour screen writes a CCT frame:

* 0x15 family, ``h60a6`` (H60A6, H6641, H6062, H6047): ``ColorModeControllerConfig.c`` ->
  ``Config.Yt`` = ``KelvinConfig.i(c) ? Bytes.r(c, KelvinConfig.c(c), posSet, d1) : Bytes.h(c, posSet)``:
  ``33 05 15 01 ff ff ff <K u2be> <picked RGB> <mask>``, one frame, the mask the RGB frame would use;
* 0x0d family, ``h6006`` / ``h6188`` (H6006, H6008, H6052, H6004, H6188): ``Config.bu`` =
  ``KelvinConfig.i(c) ? Bytes.p(c, KelvinConfig.c(c)) : Bytes.f(c)``:
  ``33 05 0d <picked RGB> <K u2be> <first tint of K>``;
* H6061 is covered in tests/test_h6061.py (``Config.Zt``), including its unchanged [2000, 9000] range.

Every expected frame is either built by the independent ``_cct15`` / ``_cct0d`` helpers below or is
a hex literal checked against them; none of it is hardware-verified."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device

from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import profile_for
from govee_ble_local.exceptions import GoveeBleInvalidValue
from govee_ble_local.wire import build as b
from govee_ble_local.wire.cct import _KELVIN_BY_TINT, kelvin_for_tint, tint_for_kelvin


def _frame(*payload: int) -> bytes:
    buf = bytearray(20)
    buf[: len(payload)] = bytes(payload)
    x = 0
    for c in buf[:19]:
        x ^= c
    buf[19] = x
    return bytes(buf)


def _rgb(colour: int) -> tuple[int, int, int]:
    return (colour >> 16) & 0xFF, (colour >> 8) & 0xFF, colour & 0xFF


def _dev(sku: str, segments: int | None = None) -> Device:
    d = make_device(ble_device("AA:BB:CC:DD:EE:99", sku), sku)
    assert d is not None
    if segments is not None:
        d.set_segment_count(segments)      # H6062 reports its own count; skip the aa 41 read
    sent: list[bytes] = []

    async def send(frame: bytes, **_k: object) -> None:
        sent.append(frame)

    async def send_many(frames: list[bytes]) -> None:
        sent.extend(frames)

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    # Any read or upload these write paths did not script fails loudly instead of reaching the
    # real connection (and the host's Bluetooth stack).
    d._conn.query = AsyncMock(side_effect=AssertionError("unexpected read"))  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("unexpected upload"))
    d._sent = sent  # type: ignore[attr-defined]
    return d


def _sent(d: Device) -> list[bytes]:
    return d._sent  # type: ignore[attr-defined, no-any-return]


def _cct15(kelvin: int, tint: tuple[int, int, int], *mask: int) -> bytes:
    """``33 05 15 01 ff ff ff <K> <tint> <mask>`` (Bytes.r type 1)."""
    return _frame(0x33, 0x05, 0x15, 0x01, 0xFF, 0xFF, 0xFF, kelvin >> 8, kelvin & 0xFF, *tint, *mask)


def _cct0d(kelvin: int, picked: tuple[int, int, int]) -> bytes:
    """``33 05 0d <picked> <K> <first tint of K>`` (Bytes.p)."""
    return _frame(0x33, 0x05, 0x0D, *picked, kelvin >> 8, kelvin & 0xFF, *tint_for_kelvin(kelvin))


# KelvinConfig ranges of the 7.7.02 app per SKU (H6006 is unconfirmed in the app: kept as modelled)
APP_RANGES = {
    "H60A6": (2700, 6500), "H6641": (2000, 9000), "H6062": (2000, 9000), "H6047": (2200, 6500),
    "H6188": (2000, 9000), "H6008": (2700, 6500), "H6004": (2700, 6500), "H6052": (2000, 9000),
    # H6006 and H6008 are goodsType 64 -> KelvinConfig.Config version 2 = [2700, 6500] (KelvinConfig.java:407,
    # :544-546). WAS (2000, 9000) for H6006 (SPEC_CLIENT_AUDIT #3, VERIFICATION G30).
    "H6006": (2700, 6500), "H6061": (2000, 9000),
    # H61A8: KelvinConfig.Config.z2, [2000, 9000] below hardware "3.08.01" (test_device_v3 has the branch)
    "H61A8": (2000, 9000),
}


def test_profile_ranges_equal_the_apps_kelvin_config() -> None:
    for sku, (lo, hi) in APP_RANGES.items():
        p = profile_for(sku)
        assert p is not None and (p.min_kelvin, p.max_kelvin) == (lo, hi), sku


# ── golden frames, whole device (Device.set_rgb and turn_on(rgb=)) ───────────
GOLDEN_WHOLE = [
    # (sku, segment count override, picked colour, frame). 0x15: white slot ff ff ff, picked colour as tint
    ("H60A6", None, 0xFFB969, "33051501ffffff0bb8ffb969ff1f0000000000a1"),    # 13 segments, mask 1fff
    ("H60A6", None, 0xFFB46B, "33051501ffffff0bb8ffb46bff1f0000000000ae"),    # second tint of 3000 K
    ("H6641", None, 0xE5E9FF, "33051501ffffff1f40e5e9ffffff000000000071"),    # 16 segments, 8000 K
    ("H6062", 15, 0xFFE7CC, "33051501ffffff1388ffe7ccff7f000000000012"),      # 15 sections, 5000 K
    ("H6047", None, 0xFFC987, "33051501ffffff0dacffc987ff03000000000031"),    # both bars, 3500 K
    # 0x0d: picked colour, K, then the FIRST tint of K (the same frame for the h6188 scheme)
    ("H6188", None, 0xFFD5A1, "33050dffd5a10fa0ffd5a1000000000000000094"),
    ("H6188", None, 0xFFD1A3, "33050dffd1a30fa0ffd5a1000000000000000092"),    # second tint of 4000 K
    ("H6052", None, 0xFF8D0B, "33050dff8d0b07d0ff8d0b0000000000000000ec"),    # 2000 K, the range minimum
    ("H6008", None, 0xFFB969, "33050dffb9690bb8ffb969000000000000000088"),
    ("H6008", None, 0xFFB46B, "33050dffb46b0bb8ffb969000000000000000087"),
    ("H6004", None, 0xFFB969, "33050dffb9690bb8ffb969000000000000000088"),
    ("H6004", None, 0xFFB46B, "33050dffb46b0bb8ffb969000000000000000087"),
    ("H6006", None, 0xFFD5A1, "33050dffd5a10fa0ffd5a1000000000000000094"),
]


def test_the_golden_literals_equal_the_independent_frame_builders() -> None:
    # recomputed here (checksums included), so a wrong literal cannot hide a wrong builder
    assert bytes.fromhex(GOLDEN_WHOLE[0][3]) == _cct15(3000, (0xFF, 0xB9, 0x69), 0xFF, 0x1F)
    assert bytes.fromhex(GOLDEN_WHOLE[1][3]) == _cct15(3000, (0xFF, 0xB4, 0x6B), 0xFF, 0x1F)
    assert bytes.fromhex(GOLDEN_WHOLE[2][3]) == _cct15(8000, (0xE5, 0xE9, 0xFF), 0xFF, 0xFF)
    assert bytes.fromhex(GOLDEN_WHOLE[3][3]) == _cct15(5000, (0xFF, 0xE7, 0xCC), 0xFF, 0x7F)
    assert bytes.fromhex(GOLDEN_WHOLE[4][3]) == _cct15(3500, (0xFF, 0xC9, 0x87), 0xFF, 0x03)
    assert bytes.fromhex(GOLDEN_WHOLE[5][3]) == _cct0d(4000, (0xFF, 0xD5, 0xA1))
    assert bytes.fromhex(GOLDEN_WHOLE[6][3]) == _cct0d(4000, (0xFF, 0xD1, 0xA3))
    assert bytes.fromhex(GOLDEN_WHOLE[7][3]) == _cct0d(2000, (0xFF, 0x8D, 0x0B))
    assert bytes.fromhex(GOLDEN_WHOLE[8][3]) == _cct0d(3000, (0xFF, 0xB9, 0x69))
    assert bytes.fromhex(GOLDEN_WHOLE[9][3]) == _cct0d(3000, (0xFF, 0xB4, 0x6B))
    assert bytes.fromhex(GOLDEN_WHOLE[9][3])[6:11].hex() == "0bb8ffb969"      # first tint, not the pick


@pytest.mark.parametrize(("sku", "segments", "colour", "expected"), GOLDEN_WHOLE)
def test_set_rgb_tint_pick_sends_one_cct_frame(sku: str, segments: int | None, colour: int,
                                               expected: str) -> None:
    d = _dev(sku, segments)
    asyncio.run(d.set_rgb(_rgb(colour)))
    assert [f.hex() for f in _sent(d)] == [expected]
    # One write of one frame (set_rgb always hands its frames to send_many, which acks each).
    d._conn.send_many.assert_awaited_once()     # type: ignore[attr-defined]
    assert len(d._conn.send_many.await_args.args[0]) == 1  # type: ignore[attr-defined]


@pytest.mark.parametrize(("sku", "segments", "colour", "expected"), GOLDEN_WHOLE)
def test_turn_on_rgb_tint_pick_sends_power_then_the_cct_frame(sku: str, segments: int | None,
                                                              colour: int, expected: str) -> None:
    d = _dev(sku, segments)
    asyncio.run(d.turn_on(rgb=_rgb(colour)))
    sent = _sent(d)
    assert sent[0][:3].hex() == "330101" and [f.hex() for f in sent[1:]] == [expected]


def test_0x0d_second_tint_pick_keeps_the_first_tint_in_the_trailing_slot() -> None:
    f = b.color_rgb_frames(0xFF, 0xD1, 0xA3, "h6188", 1, (2000, 9000))[0]
    assert f[3:6].hex() == "ffd1a3" and f[8:11].hex() == "ffd5a1"
    # h6006 and h6188 take the same frame (the h6188 white-slot form is the slider, not the pick)
    assert b.color_rgb_frames(0xFF, 0xD1, 0xA3, "h6006", 1, (2000, 9000)) == [f]


# ── golden frames, zone / bar / segment picks (0x15 family) ──────────────────
def test_h6047_bar_pick_uses_the_bar_mask() -> None:
    d = _dev("H6047")
    asyncio.run(d.set_zone_rgb("left", (0xFF, 0xC9, 0x87)))
    asyncio.run(d.set_zone_rgb("right", (0xFF, 0xC9, 0x87)))
    assert [f.hex() for f in _sent(d)] == [_cct15(3500, (0xFF, 0xC9, 0x87), 0x1F, 0x00).hex(),
                                           _cct15(3500, (0xFF, 0xC9, 0x87), 0xE0, 0x03).hex()]
    assert _sent(d)[0][12:14].hex() == "1f00"


def test_h60a6_zone_picks_are_one_cct_frame_with_the_zone_mask() -> None:
    d = _dev("H60A6")
    asyncio.run(d.set_zone_rgb("main", (0xFF, 0xB9, 0x69)))
    asyncio.run(d.set_zone_rgb("background", (0xFF, 0xB4, 0x6B)))
    assert [f.hex() for f in _sent(d)] == [_cct15(3000, (0xFF, 0xB9, 0x69), 0x00, 0x10).hex(),
                                           _cct15(3000, (0xFF, 0xB4, 0x6B), 0xFF, 0x0F).hex()]


def test_segment_picks_are_one_cct_frame_with_the_segment_mask() -> None:
    d = _dev("H6641")
    asyncio.run(d.set_segment_rgb([0, 3], (0xE5, 0xE9, 0xFF)))
    assert [f.hex() for f in _sent(d)] == [_cct15(8000, (0xE5, 0xE9, 0xFF), 0x09, 0x00).hex()]
    d = _dev("H6062", 33)                           # more than 16 segments: a 5-byte mask
    asyncio.run(d.set_segment_rgb([32], (0xFF, 0xE7, 0xCC)))
    assert _sent(d) == [_cct15(5000, (0xFF, 0xE7, 0xCC), 0, 0, 0, 0, 1)]


def test_segment_color_temp_default_tint_is_the_first_tint_and_the_tint_is_overridable() -> None:
    assert b.segment_color_temp(0x0F, 3000, "h60a6", 13) == _cct15(3000, (0xFF, 0xB9, 0x69), 0x0F, 0x00)
    assert b.segment_color_temp(0x0F, 3000, "h60a6", 13, tint=(0xFF, 0xB4, 0x6B)) == \
        _cct15(3000, (0xFF, 0xB4, 0x6B), 0x0F, 0x00)


def test_slider_paths_are_unchanged() -> None:
    # set_color_temp keeps the first tint per Kelvin (= the slider), the H60A6 one all-segments frame
    # and the h6188 white-slot form
    d = _dev("H60A6")
    asyncio.run(d.set_color_temp(3000))
    assert [f.hex() for f in _sent(d)] == [_cct15(3000, (0xFF, 0xB9, 0x69), 0xFF, 0x1F).hex()]
    d = _dev("H6188")
    asyncio.run(d.set_color_temp(4000))
    assert _sent(d) == [_frame(0x33, 0x05, 0x0D, 0xFF, 0xFF, 0xFF, 0x0F, 0xA0, 0xFF, 0xD5, 0xA1)]
    d = _dev("H6008")
    asyncio.run(d.set_color_temp(4000))
    assert _sent(d) == [_cct0d(4000, (0xFF, 0xD5, 0xA1))]


# ── every table colour, every scheme: CCT iff inside the SKU's inclusive range ─
WHOLE_CASES = [("H60A6", None), ("H6641", None), ("H6062", 15), ("H6047", None),
               ("H6188", None), ("H6008", None), ("H6004", None), ("H6052", None), ("H6006", None)]


def _expected_whole(sku: str, segments: int | None, rgb: tuple[int, int, int], kelvin: int | None) -> bytes:
    p = profile_for(sku)
    assert p is not None
    n = segments or p.segments
    if kelvin is None:
        return b.color_rgb(*rgb, p.color_scheme, n)
    if p.color_scheme == "h60a6":
        width = 2 if n <= 16 else (n + 7) // 8
        return _cct15(kelvin, rgb, *((1 << n) - 1).to_bytes(width, "little"))
    return _cct0d(kelvin, rgb)


@pytest.mark.parametrize(("sku", "segments"), WHOLE_CASES)
def test_all_178_tint_colours_follow_the_inclusive_range(sku: str, segments: int | None) -> None:
    p = profile_for(sku)
    assert p is not None and p.min_kelvin is not None and p.max_kelvin is not None
    inside = outside = 0
    for colour, kelvin in _KELVIN_BY_TINT.items():
        rgb = _rgb(colour)
        in_range = p.min_kelvin <= kelvin <= p.max_kelvin
        frames = b.color_rgb_frames(*rgb, p.color_scheme, segments or p.segments,
                                    (p.min_kelvin, p.max_kelvin))
        assert frames == [_expected_whole(sku, segments, rgb, kelvin if in_range else None)], (
            sku, hex(colour), kelvin)
        inside += in_range
        outside += not in_range
    assert inside and outside


@pytest.mark.parametrize(("sku", "segments"), WHOLE_CASES)
def test_range_boundaries_exactly_at_and_just_outside(sku: str, segments: int | None) -> None:
    p = profile_for(sku)
    assert p is not None and p.min_kelvin is not None and p.max_kelvin is not None
    lo, hi = p.min_kelvin, p.max_kelvin
    for kelvin, expect_cct in ((lo - 100, False), (lo, True), (hi, True), (hi + 100, False)):
        colours = [c for c, k in _KELVIN_BY_TINT.items() if k == kelvin]   # both colours of the step
        assert colours
        for colour in colours:
            d = _dev(sku, segments)
            asyncio.run(d.set_rgb(_rgb(colour)))
            want = _expected_whole(sku, segments, _rgb(colour), kelvin if expect_cct else None)
            assert _sent(d) == [want], (sku, kelvin, hex(colour))
            assert (d.state.color_temp_kelvin == kelvin) == expect_cct
            assert (d.state.rgb_color is None) == expect_cct


def test_2000k_colour_is_rgb_below_a_2700k_minimum_and_cct_at_a_2000k_minimum() -> None:
    # 0xFF8D0B is the first colour of 2000 K
    for sku in ("H60A6", "H6008", "H6006", "H6004", "H6047"):              # minimum 2700 / 2200
        d = _dev(sku)
        asyncio.run(d.set_rgb((0xFF, 0x8D, 0x0B)))
        p = profile_for(sku)
        assert p is not None
        assert _sent(d) == [b.color_rgb(0xFF, 0x8D, 0x0B, p.color_scheme, p.segments)], sku
    d = _dev("H6008")
    asyncio.run(d.set_rgb((0xFF, 0x8D, 0x0B)))
    assert _sent(d)[0].hex() == "33050dff8d0b0000000000000000000000000042"
    for sku in ("H6641", "H6052", "H6188", "H6062"):                       # minimum 2000
        d = _dev(sku, 15 if sku == "H6062" else None)
        asyncio.run(d.set_rgb((0xFF, 0x8D, 0x0B)))
        assert d.state.color_temp_kelvin == 2000, sku


@pytest.mark.parametrize(("sku", "segments"), WHOLE_CASES)
def test_near_misses_and_ordinary_colours_stay_plain_rgb(sku: str, segments: int | None) -> None:
    p = profile_for(sku)
    assert p is not None
    assert kelvin_for_tint((0xFF, 0xB9, 0x69)) == 3000
    near = [(0xFF, 0xB9, 0x6A), (0xFF, 0xB9, 0x68), (0xFE, 0xB9, 0x69), (0xFF, 0xBA, 0x69),
            (0xFF, 0xFF, 0xFF), (0xFF, 0x00, 0x00), (0x00, 0xFF, 0x00), (0x00, 0x00, 0x00),
            (0x69, 0xB9, 0xFF)]                       # channel-swapped
    for rgb in near:
        assert kelvin_for_tint(rgb) is None
        d = _dev(sku, segments)
        asyncio.run(d.set_rgb(rgb))
        assert _sent(d) == [b.color_rgb(*rgb, p.color_scheme, segments or p.segments)], (sku, rgb)
        assert d.state.rgb_color == rgb and d.state.color_temp_kelvin is None


def test_the_tint_lookup_is_the_24_bit_rgb_of_an_opaque_colour() -> None:
    # The app compares the full ARGB int, alpha 0xFF. A (r, g, b) tuple is always opaque, so a hit
    # needs exactly the 24 RGB bits; a channel outside 0..255 (which would spill into alpha or a
    # neighbouring channel) is refused before any frame is built.
    assert all(0 <= c <= 0xFFFFFF for c in _KELVIN_BY_TINT)
    for bad in ((256, 0xAE, 0x54), (0xFF, 0xAE, -1), (0x1FF, 0xAE, 0x54)):
        with pytest.raises(GoveeBleInvalidValue):
            b.color_rgb_frames(*bad, "h60a6", 13, (2000, 9000))
        with pytest.raises(GoveeBleInvalidValue):
            b.segment_rgb(1, *bad, "h60a6", 13, (2000, 9000))
        with pytest.raises(GoveeBleInvalidValue):
            b.color_rgb_frames(*bad, "h6188", 1, (2000, 9000))


# ── builder API ──────────────────────────────────────────────────────────────
def test_rgb_as_kelvin_is_scheme_driven() -> None:
    tint = (0xFF, 0xB9, 0x69)
    assert b.rgb_as_kelvin(*tint, "h60a6", (2700, 6500)) == 3000
    assert b.rgb_as_kelvin(*tint, "h6006", (2700, 6500)) == 3000
    assert b.rgb_as_kelvin(*tint, "h6188", (2000, 9000)) == 3000
    assert b.rgb_as_kelvin(*tint, "h6061", (2000, 9000)) == 3000
    assert b.rgb_as_kelvin(*tint, "h61a8", (2000, 9000)) is None          # no colour temperature
    assert b.rgb_as_kelvin(*tint, "h60a6", (3100, 6500)) is None          # below the range
    assert b.rgb_as_kelvin(*tint, "h60a6", (2000, 2900)) is None          # above the range
    assert b.rgb_as_kelvin(*tint, "h60a6", (3000, 3000)) == 3000          # inclusive at both ends
    # no range given: only h6061 has a built-in default (its [2000, 9000], unchanged)
    assert b.rgb_as_kelvin(*tint, "h6061") == 3000 and b.rgb_as_kelvin(0xFF, 0x87, 0x00, "h6061") is None
    assert b.rgb_as_kelvin(*tint, "h60a6") is None and b.rgb_as_kelvin(*tint, "h6188") is None


def test_without_a_range_the_builders_send_plain_rgb() -> None:
    assert b.color_rgb_frames(0xFF, 0xB9, 0x69, "h60a6", 13) == [b.color_rgb(0xFF, 0xB9, 0x69, "h60a6", 13)]
    assert b.segment_rgb(3, 0xFF, 0xB9, 0x69, "h60a6", 13) == _frame(
        0x33, 0x05, 0x15, 0x01, 0xFF, 0xB9, 0x69, 0, 0, 0, 0, 0, 3, 0)
    assert b.color_rgb_frames(0xFF, 0xB9, 0x69, "h61a8", 15, (2000, 9000)) == [
        b.color_rgb(0xFF, 0xB9, 0x69, "h61a8", 15)]
    assert b.segment_rgb(3, 0xFF, 0xB9, 0x69, "h61a8", 15, (2000, 9000)) == b.segment_rgb(
        3, 0xFF, 0xB9, 0x69, "h61a8", 15)


def test_color_rgb_stays_the_plain_rgb_frame() -> None:
    # color_rgb is the RGB-frame builder; only the pick paths (color_rgb_frames, segment_rgb with a
    # range) convert
    assert b.color_rgb(0xFF, 0xB9, 0x69, "h60a6", 13)[:7].hex() == "33051501ffb969"
    assert b.color_rgb(0xFF, 0xB9, 0x69, "h6188")[3:6].hex() == "ffb969"
    assert b.color_rgb(0xFF, 0xB9, 0x69, "h6188")[6:8] == b"\x00\x00"


# The H61A8's bench frames (SW 3.03.18 / HW 3.01.10, 2026-10-17): both acked 00 and applied to all 15
# segments, where today's 0x0b frame was rejected (status 01).
H61A8_BENCH_RED = "33051501ff00000000000000ff7f00000000005d"      # Bytes.h, (255, 0, 0)
H61A8_BENCH_2700K = "33051501ffffff0a8cffae54ff7f0000000000de"    # Bytes.r, 2700 K and its tint


def test_h61a8_sends_the_apps_0x15_frames() -> None:
    # WAS: scheme h61a8 sent every pick as 0x0b RGB. goodsType 19 sends the 0x15 forms (Config.Yt,
    # devices.yaml by_goods_type 19): RGB as Bytes.h, a Kelvin or its exact tint as Bytes.r, 15 segments.
    d = _dev("H61A8")
    asyncio.run(d.set_rgb((0xFF, 0x00, 0x00)))
    asyncio.run(d.set_rgb((0xFF, 0xAE, 0x54)))            # 2700 K's tint: sent as that Kelvin
    asyncio.run(d.set_color_temp(2700))
    assert [f.hex() for f in _sent(d)] == [H61A8_BENCH_RED, H61A8_BENCH_2700K, H61A8_BENCH_2700K]
    assert d.state.rgb_color is None and d.state.color_temp_kelvin == 2700
    # A tint pick follows the range in force: 2000 K's tint is a CCT pick only at HW 3.01.10 ([2000, 9000]);
    # unknown hardware ([2700, 6500], valid on every revision) and 3.08.01 send it as RGB.
    tint_2000 = (0xFF, 0x8D, 0x0B)
    as_cct = _cct15(2000, tint_2000, 0xFF, 0x7F)
    assert as_cct.hex().endswith("f3")
    for hardware, frame_, state in ((None, None, (tint_2000, None)), ("3.01.10", as_cct, (None, 2000)),
                                    ("3.08.01", None, (tint_2000, None))):
        h = _dev("H61A8")
        h._state.hardware_version = hardware
        asyncio.run(h.set_rgb(tint_2000))
        rgb_frame = _frame(0x33, 0x05, 0x15, 0x01, *tint_2000, 0, 0, 0, 0, 0, 0xFF, 0x7F)   # Bytes.h
        assert _sent(h) == [frame_ or rgb_frame], hardware
        assert (h.state.rgb_color, h.state.color_temp_kelvin) == state, hardware
    # One range per write: an aa 07 03 that lands mid-write (here, before the state is applied) changes
    # neither the frame nor the state it records.
    race = _dev("H61A8")
    race._state.hardware_version = "3.01.10"
    commit = race._commit

    async def hardware_lands(frames: Any, apply: Any) -> None:
        race._state.hardware_version = "3.08.01"
        await commit(frames, apply)

    race._commit = hardware_lands  # type: ignore[method-assign]
    asyncio.run(race.set_rgb(tint_2000))
    assert _sent(race) == [as_cct] and (race.state.rgb_color, race.state.color_temp_kelvin) == (None, 2000)


# ── state after a conversion: Kelvin, no rgb (the app syncs the Kelvin) ──────
@pytest.mark.parametrize(("sku", "segments", "colour", "kelvin"),
                         [("H60A6", None, 0xFFB969, 3000), ("H6641", None, 0xE5E9FF, 8000),
                          ("H6062", 15, 0xFFE7CC, 5000), ("H6047", None, 0xFFC987, 3500),
                          ("H6188", None, 0xFFD1A3, 4000), ("H6008", None, 0xFFB46B, 3000),
                          ("H6004", None, 0xFFB969, 3000), ("H6052", None, 0xFF8D0B, 2000),
                          ("H6006", None, 0xFFD5A1, 4000)])
def test_state_after_a_conversion_and_back(sku: str, segments: int | None, colour: int,
                                           kelvin: int) -> None:
    d = _dev(sku, segments)
    d._state.scene_code = 7
    asyncio.run(d.set_rgb(_rgb(colour)))
    assert d.state.color_temp_kelvin == kelvin and d.state.rgb_color is None
    assert d.state.scene_code is None
    asyncio.run(d.set_rgb((1, 2, 3)))
    assert d.state.rgb_color == (1, 2, 3) and d.state.color_temp_kelvin is None
    asyncio.run(d.turn_on(rgb=_rgb(colour)))
    assert d.state.color_temp_kelvin == kelvin and d.state.rgb_color is None


def test_h6047_state_tracks_the_kelvin_per_bar() -> None:
    d = _dev("H6047")
    asyncio.run(d.set_rgb((0xFF, 0xC9, 0x87)))
    assert d.state.zone_color_temp_kelvin == {0: 3500, 1: 3500} and d.state.color_temp_kelvin == 3500
    asyncio.run(d.set_zone_rgb("right", (1, 2, 3)))                    # a plain RGB bar leaves CCT
    assert d.state.zone_color_temp_kelvin == {0: 3500} and d.state.color_temp_kelvin is None
    asyncio.run(d.set_zone_rgb("right", (0xFF, 0xB9, 0x69)))           # tint pick on one bar: its Kelvin
    assert d.state.zone_color_temp_kelvin == {0: 3500, 1: 3000} and d.state.color_temp_kelvin is None
    assert d.zone_color_temp_kelvin("right") == 3000
    asyncio.run(d.set_zone_rgb("right", (0xFF, 0xB9, 0x6A)))           # near miss: plain RGB again
    assert d.zone_color_temp_kelvin("right") is None
    assert _sent(d)[-1] == b.segment_rgb(0x3E0, 0xFF, 0xB9, 0x6A, "h60a6", 10)


def test_segment_pick_updates_the_segment_colour_state() -> None:
    d = _dev("H6641")
    asyncio.run(d.set_segment_rgb([2], (0xE5, 0xE9, 0xFF)))
    assert [(s.index, s.rgb) for s in d.state.segments] == [(2, (0xE5, 0xE9, 0xFF))]
