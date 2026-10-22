"""Segment masks wider than 16 bits (issue #17).

The app's encoder (BleUtils.makeSelectedTwoBytes / BleUtils.X, chosen by the `length > 16`
branch in color/Bytes) writes a 2-byte little-endian mask up to 16 segments and ceil(n/8)
bytes, LSB first, above that. Bytes.C caps the width at 7 bytes; we reject more.
"""
from __future__ import annotations

import asyncio
import dataclasses
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device, unacked_via
from fake_ble import frame as _frame

from govee_ble_local.crypto import checksum_ok
from govee_ble_local.devices.device import Device, _mask
from govee_ble_local.devices.profile import profile_for
from govee_ble_local.exceptions import GoveeBleInvalidValue, GoveeBleNotSupported
from govee_ble_local.wire import build as b
from govee_ble_local.wire.cct import tint_for_kelvin

# segments -> expected mask width in bytes (boundaries 16/17, 23/24, 56/57 called out in #17)
WIDTHS = [(0, 2), (1, 2), (8, 2), (13, 2), (16, 2), (17, 3), (23, 3), (24, 3), (25, 4),
          (32, 4), (33, 5), (40, 5), (41, 6), (48, 6), (49, 7), (56, 7)]
BOUNDARIES = [16, 17, 23, 24, 56]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _dev(sku: str, **profile_overrides: Any) -> Device:
    """Device with every connection method mocked: writes are recorded, reads get no reply (a
    test scripts ``query`` itself), so nothing reaches a real connection. ``profile_overrides``
    build it on a changed copy of the SKU's profile (a profile is read-only after construction)."""
    profile = profile_for(sku)
    assert profile is not None
    if profile_overrides:
        profile = dataclasses.replace(profile, **profile_overrides)
    return _mocked(Device(profile, ble_device("AA:BB:CC:DD:EE:17", sku), sku=sku))


def _mocked(d: Device) -> Device:
    """Replace ``d``'s connection I/O with recorders (see :func:`_dev`)."""
    sent: list[bytes] = []

    async def send(frame: bytes, **_k: object) -> None:
        sent.append(frame)

    async def send_many(frames: list[bytes]) -> None:
        sent.extend(frames)

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock(side_effect=unacked_via(send_many))  # type: ignore[method-assign]
    d._conn.query = AsyncMock(return_value=[])  # type: ignore[method-assign]
    d._sent = sent  # type: ignore[attr-defined]
    return d


def _sent(d: Device) -> list[bytes]:
    return list(d._sent)  # type: ignore[attr-defined]


def _all_ones(n: int) -> bytes:
    """Reference encoder: LSB-first bytes with the first n bits set, max(2, ceil(n/8)) of them."""
    width = max(2, (n + 7) // 8)
    return bytes(sum(1 << i for i in range(8) if 8 * j + i < n) for j in range(width))


# ── builders ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(("segments", "width"), WIDTHS)
def test_mask_width(segments: int, width: int) -> None:
    assert b.mask_width(segments) == width


@pytest.mark.parametrize("bad", [-1, 113, 1000])
def test_mask_width_rejects_out_of_range(bad: int) -> None:
    with pytest.raises(GoveeBleInvalidValue, match="segment count"):
        b.mask_width(bad)


@pytest.mark.parametrize("bad", [True, 2.0, "16", None])
def test_mask_width_rejects_non_int(bad: Any) -> None:
    with pytest.raises(GoveeBleInvalidValue):
        b.mask_width(bad)


@pytest.mark.parametrize(("segments", "width"), WIDTHS)
def test_all_segments_mask_is_exactly_n_bits(segments: int, width: int) -> None:
    mask = b.all_segments_mask(segments)
    assert mask == (1 << segments) - 1
    assert mask.to_bytes(width, "little") == _all_ones(segments)


def test_color_rgb_h60a6_default_unchanged() -> None:
    # 13 segments (H60A6): ff 1f, exactly as before the refactor.
    assert b.color_rgb(1, 2, 3, "h60a6", 13)[12:14] == bytes([0xFF, 0x1F])
    assert b.color_rgb(1, 2, 3, "h60a6")[12:14] == bytes([0xFF, 0x1F])
    assert b.color_rgb(1, 2, 3, "h60a6", 16)[12:15] == bytes([0xFF, 0xFF, 0x00])


@pytest.mark.parametrize("segments", BOUNDARIES)
def test_color_rgb_whole_device_mask(segments: int) -> None:
    f = b.color_rgb(1, 2, 3, "h60a6", segments)
    width = b.mask_width(segments)
    assert f[:4].hex() == "33051501" and f[4:7] == bytes([1, 2, 3])
    assert f[7:12] == bytes(5)
    assert f[12 : 12 + width] == _all_ones(segments)
    assert f[12 + width : 19] == bytes(7 - width)          # nothing after the mask
    assert checksum_ok(f)


def test_color_rgb_literal_masks_around_a_byte_boundary() -> None:
    assert b.color_rgb(1, 2, 3, "h60a6", 17)[12:15] == bytes([0xFF, 0xFF, 0x01])
    assert b.color_rgb(1, 2, 3, "h60a6", 23)[12:15] == bytes([0xFF, 0xFF, 0x7F])
    assert b.color_rgb(1, 2, 3, "h60a6", 24)[12:15] == bytes([0xFF, 0xFF, 0xFF])


@pytest.mark.parametrize("segments", BOUNDARIES)
def test_color_temp_whole_device_mask(segments: int) -> None:
    f = b.color_temp(4000, "h60a6", segments)
    width = b.mask_width(segments)
    assert f[:4].hex() == "33051501"
    assert f[4:7] == b"\xff\xff\xff" and f[7:9] == (4000).to_bytes(2, "big")
    assert f[12 : 12 + width] == _all_ones(segments)
    assert f[12 + width : 19] == bytes(7 - width)
    assert checksum_ok(f)


def test_widest_frame_fills_the_payload_exactly() -> None:
    # 7 mask bytes end at index 18, the last payload byte before the checksum.
    f = b.color_rgb(0, 0, 0, "h60a6", 56)
    assert f[12:19] == bytes([0xFF] * 7) and checksum_ok(f)


@pytest.mark.parametrize("segments", [17, 23, 24, 56])
def test_segment_rgb_width_follows_segment_count(segments: int) -> None:
    width = b.mask_width(segments)
    f = b.segment_rgb(1, 9, 8, 7, "h60a6", segments)
    assert f[4:7] == bytes([9, 8, 7])
    assert f[12 : 12 + width] == bytes([1]) + bytes(width - 1)
    assert f[12 + width : 19] == bytes(7 - width)


def test_segment_rgb_bit_positions() -> None:
    # byte j bit i = segment 8j+i
    cases = {0: (0, 0), 7: (0, 7), 8: (1, 0), 16: (2, 0), 17: (2, 1), 23: (2, 7),
             24: (3, 0), 55: (6, 7)}
    for seg, (byte_idx, bit) in cases.items():
        mask = b.segment_rgb(1 << seg, 1, 2, 3, "h60a6", 56)[12:19]
        assert mask[byte_idx] == 1 << bit
        assert sum(mask) == 1 << bit, seg


def test_segment_rgb_width_is_set_by_device_not_selection() -> None:
    # Selecting only segment 0 of a 23-segment device still writes 3 bytes (the app's boolean
    # array is sized by the device, not by the highest selected segment).
    assert b.segment_rgb(1, 1, 2, 3, "h60a6", 23)[12:15] == bytes([1, 0, 0])
    assert b.segment_rgb(1, 1, 2, 3, "h60a6", 10)[12:15] == bytes([1, 0, 0])


def test_segment_rgb_without_segments_is_the_legacy_two_bytes() -> None:
    f = b.segment_rgb(0x8001, 1, 2, 3, "h60a6")
    assert f[12:14] == bytes([0x01, 0x80]) and f[14] == 0
    with pytest.raises(GoveeBleInvalidValue, match="segment mask"):
        b.segment_rgb(0x10000, 1, 2, 3, "h60a6")


@pytest.mark.parametrize("segments", BOUNDARIES)
def test_segment_color_temp_and_brightness_masks(segments: int) -> None:
    width = b.mask_width(segments)
    top = 1 << (segments - 1)
    ct = b.segment_color_temp(top, 5000, "h60a6", segments)
    assert ct[7:9] == (5000).to_bytes(2, "big")
    assert ct[12 : 12 + width] == top.to_bytes(width, "little")
    assert ct[12 + width : 19] == bytes(7 - width)
    br = b.segment_brightness(top, 55, "h60a6", segments)
    assert br[:4].hex() == "33051502" and br[4] == 55
    assert br[5 : 5 + width] == top.to_bytes(width, "little")
    assert br[5 + width : 19] == bytes(14 - width)
    assert checksum_ok(ct) and checksum_ok(br)


def test_mask_too_wide_for_the_segment_count_is_rejected() -> None:
    with pytest.raises(GoveeBleInvalidValue, match="segment mask"):
        b.segment_rgb(1 << 16, 1, 2, 3, "h60a6", 16)      # 2 bytes can't hold bit 16
    with pytest.raises(GoveeBleInvalidValue, match="segment mask"):
        b.segment_rgb(1 << 24, 1, 2, 3, "h60a6", 24)      # 3 bytes can't hold bit 24
    with pytest.raises(GoveeBleInvalidValue, match="segment mask"):
        b.segment_color_temp(1 << 56, 4000, "h60a6", 56)
    with pytest.raises(GoveeBleInvalidValue, match="segment mask"):
        b.segment_brightness(1 << 24, 50, "h60a6", 24)
    with pytest.raises(GoveeBleInvalidValue, match="segment mask"):
        b.segment_rgb(-1, 1, 2, 3, "h60a6", 56)


@pytest.mark.parametrize("segments", [57, 64, 100, 112])
def test_past_56_segments_each_body_keeps_the_apps_cap(segments: int) -> None:
    # The 0x15 RGB / CCT frames carry 7 mask bytes (Bytes.h / Bytes.r coerceAtMost 7), per-segment brightness
    # ceil(n/8) up to 14 (Bytes.G coerceAtMost 14): the app drops the positions past the cap.
    assert b.mask_width(segments, b.COLOR_MASK_CAP) == 7
    assert b.mask_width(segments, b.BRIGHTNESS_MASK_CAP) == -(-segments // 8)
    assert b.mask_positions(segments, b.COLOR_MASK_CAP) == 56
    assert b.mask_positions(segments, b.BRIGHTNESS_MASK_CAP) == segments
    assert b.color_rgb(1, 2, 3, "h60a6", segments)[12:19] == bytes([0xFF] * 7)
    assert b.segment_rgb(1 | 1 << 60, 1, 2, 3, "h60a6", segments)[12:19] == bytes([1, 0, 0, 0, 0, 0, 0])
    assert b.color_temp(4000, "h60a6", segments)[-8:-1] == bytes([0xFF] * 7)
    width = -(-segments // 8)
    frame = b.segment_brightness(1 << (segments - 1), 50, "h60a6", segments)
    assert frame[5:5 + width] == (1 << (segments - 1)).to_bytes(width, "little")
    with pytest.raises(GoveeBleInvalidValue, match="segment count"):
        b.color_rgb(1, 2, 3, "h60a6", 113)


def test_schemes_without_a_0x15_mask_ignore_the_segment_count() -> None:
    # 0x0d frames carry no mask, so a large count is not an error there.
    for scheme in ("h6006", "h6188"):
        assert b.color_rgb(1, 2, 3, scheme, 99)[:3].hex() == "33050d"


def test_h61a8_mask_stays_two_bytes() -> None:
    assert b.color_rgb(1, 2, 3, "h61a8", 15)[6:8] == bytes([0xFF, 0x7F])
    with pytest.raises(GoveeBleInvalidValue, match="segment mask"):
        b.segment_rgb(1 << 16, 1, 2, 3, "h61a8", 17)
    with pytest.raises(GoveeBleInvalidValue, match="segment mask"):
        b.color_rgb(1, 2, 3, "h61a8", 17)


# ── runtime segment count on Device ──────────────────────────────────────────
def test_segment_count_defaults_to_profile() -> None:
    d = _dev("H60A6")
    assert d.segment_count == d.profile.segments == 13


def test_runtime_segment_count_overrides_profile_and_reverts() -> None:
    d = _dev("H60A6")
    d.set_segment_count(23)
    assert d.segment_count == 23 and d.profile.segments == 13   # profile left untouched
    d.set_segment_count(None)
    assert d.segment_count == 13


@pytest.mark.parametrize("bad", [0, -1, 113, True, 3.0, "23"])
def test_set_segment_count_validates(bad: Any) -> None:
    d = _dev("H60A6")
    with pytest.raises(GoveeBleInvalidValue, match="segment count"):
        d.set_segment_count(bad)
    assert d.segment_count == 13


def test_set_rgb_uses_the_runtime_count() -> None:
    d = _dev("H60A6")
    d.set_segment_count(23)
    run(d.set_rgb((1, 2, 3)))
    assert _sent(d)[0][12:15] == bytes([0xFF, 0xFF, 0x7F])


def test_set_color_temp_and_turn_on_use_the_runtime_count() -> None:
    # Whole-device CCT is the single all-segments frame, whose mask follows the runtime count.
    d = _dev("H60A6")
    d.set_segment_count(24)
    run(d.set_color_temp(4000))
    assert _sent(d)[0][12:15] == bytes([0xFF, 0xFF, 0xFF])
    d2 = _dev("H60A6")
    d2.set_segment_count(17)
    run(d2.turn_on(rgb=(1, 2, 3), power=False))
    run(d2.turn_on(kelvin=4000, power=False))
    sent = _sent(d2)
    assert sent[0][12:15] == bytes([0xFF, 0xFF, 0x01])
    assert sent[1][12:15] == bytes([0xFF, 0xFF, 0x01])


@pytest.mark.parametrize("segments", [17, 24, 56])
def test_wide_0x15_cct_frame_carries_the_tint_and_the_wide_mask(segments: int) -> None:
    # The CCT layout is `15 01 ff ff ff <K u2be> <tint rgb> <mask>`: a mask wider than 16 bits must
    # not displace or zero the tint (the two changes touch the same bytes).
    width = b.mask_width(segments)
    tint = tint_for_kelvin(4000)
    assert tint == (0xFF, 0xD5, 0xA1)
    whole = b.color_temp(4000, "h60a6", segments)
    top = 1 << (segments - 1)
    one = b.segment_color_temp(top, 4000, "h60a6", segments)
    for f, mask in ((whole, b.all_segments_mask(segments)), (one, top)):
        assert f[:4].hex() == "33051501" and f[4:7] == b"\xff\xff\xff"
        assert f[7:9] == (4000).to_bytes(2, "big")
        assert f[9:12] == bytes(tint)
        assert f[12 : 12 + width] == mask.to_bytes(width, "little")
        assert f[12 + width : 19] == bytes(7 - width)
        assert checksum_ok(f)


def test_device_wide_cct_frames_carry_tint_and_runtime_width() -> None:
    # Whole device: one all-segments frame, 3-byte mask for a 24-segment device.
    d = _dev("H60A6")
    d.set_segment_count(24)
    run(d.set_color_temp(4000))
    assert _sent(d)[0][:15].hex() == "33051501ffffff0fa0ffd5a1ffffff"
    # Per-segment CCT on a >16-segment device: tint plus the wide mask (an H6098: no Kelvin to an H60A6
    # piece selection, decision 24).
    s = _dev("H6098")
    s.set_segment_count(24)
    run(s.set_segment_color_temp([17], 4000))
    assert _sent(s)[0][:15].hex() == "33051501ffffff0fa0ffd5a1000002"
    # The per-zone frames keep the tint and use the wide width too.
    d2 = _dev("H60A6")
    d2.set_segment_count(24)
    run(d2.set_zone_color_temp("main", 4000))
    run(d2.set_zone_color_temp("background", 4000))
    zone_frames = _sent(d2)
    assert [f[:15].hex() for f in zone_frames] == [
        "33051501ffffff0fa0ffd5a1001000", "33051501ffffff0fa0ffd5a1ff0f00"]


def test_segment_writes_use_runtime_width_and_bounds() -> None:
    d = _dev("H6098")   # an H60A6 sends no Kelvin to a piece selection (decision 24)
    d.set_segment_count(23)
    run(d.set_segment_rgb([0, 22], (1, 2, 3)))
    run(d.set_segment_brightness([16], 40))
    run(d.set_segment_color_temp([17], 4000))
    f_rgb, f_br, f_ct = _sent(d)
    assert f_rgb[12:15] == bytes([0x01, 0x00, 0x40])
    assert f_br[5:8] == bytes([0x00, 0x00, 0x01])
    assert f_ct[12:15] == bytes([0x00, 0x00, 0x02])
    # index 23 is out of range for a 23-segment device (it would have been "fine" as a bit).
    with pytest.raises(GoveeBleInvalidValue, match=r"0\.\.22"):
        run(d.set_segment_rgb([23], (1, 2, 3)))
    assert len(_sent(d)) == 3


def test_segment_index_limits() -> None:
    # Indices stop at the segment count, as the app's position set does (BleUtils.Y / X): a 10-segment
    # H6047 takes 0..9 and a 13-segment H60A6 0..12, not the 2-byte mask's 0..15.
    assert _mask([0, 9], 10) == 0x0201 and _mask([12], 13) == 0x1000 and _mask([15], 16) == 0x8000
    with pytest.raises(GoveeBleInvalidValue, match=r"0\.\.9"):
        _mask([10], 10)
    with pytest.raises(GoveeBleInvalidValue, match=r"0\.\.12"):
        _mask([13], 13)
    for n in (0, 16):   # 0: a custom profile with no count keeps the 2-byte mask's 16 positions
        with pytest.raises(GoveeBleInvalidValue, match=r"0\.\.15"):
            _mask([16], n)
    assert _mask([16], 17) == 1 << 16 and _mask([55], 56) == 1 << 55
    with pytest.raises(GoveeBleInvalidValue, match=r"0\.\.55"):
        _mask([56], 56)


def test_zone_writes_use_runtime_width() -> None:
    d = _dev("H60A6")
    d.set_segment_count(17)   # not real for H60A6; proves the width follows the count
    run(d.set_zone_rgb("main", (1, 2, 3)))
    run(d.set_zone_color_temp("background", 4000))
    rgb, ct = _sent(d)
    assert rgb[12:15] == bytes([0x00, 0x10, 0x00])       # zone "main" = segment 12
    assert ct[12:15] == bytes([0xFF, 0x0F, 0x00])        # zone "background" = segments 0..11


def test_unsupported_scheme_still_raises_not_supported_with_a_count() -> None:
    # A custom profile pairing SEGMENTS with the mask-less 0x0d scheme is accepted (as 1.3.0); the
    # builder refuses the frame, with the SKU named (the table's build refuses the pairing itself).
    d = _dev("H60A6", color_scheme="h6006")
    d.set_segment_count(23)
    with pytest.raises(GoveeBleNotSupported, match=r"^H60A6: "):
        run(d.set_segment_color_temp([1], 4000))
    assert d._sent == []  # type: ignore[attr-defined]


def test_direct_readback_walks_the_runtime_count() -> None:
    # No static read-back count on the profile -> the aa a5 group walk follows the live
    # segment count: 23 segments / 4 per group = 6 groups, the last carrying 3.
    base = _dev("H6047")
    d = _dev("H6047", color_readback_segments=None)
    d.set_segment_count(23)
    assert base.profile.color_readback_segments is None and d.segment_count == 23
    asked: list[int] = []

    def _group(frame: bytes, **_kw: object) -> list[bytes]:
        g = frame[2]
        asked.append(g)
        n = 3 if g == 6 else 4
        recs: list[int] = []
        for i in range(4):
            seg = (g - 1) * 4 + i
            recs += [50, seg, 0, 0] if i < n else [0, 0, 0, 0]
        return [_frame(0xAA, 0xA5, g, *recs)]

    d._conn.query = AsyncMock(side_effect=_group)  # type: ignore[attr-defined]
    run(d._read_mechanism_a_direct())
    assert asked == [1, 2, 3, 4, 5, 6]
    assert [s.index for s in d.state.segments] == list(range(23))


def test_segment_state_follows_the_positions_the_frame_carries() -> None:
    # 80 segments: the RGB frame's mask is cut to 7 bytes (positions 0..55), so segment 60 is neither sent
    # nor folded into state; the brightness frame carries all 10 mask bytes.
    d = _dev("H60A6")
    d.set_segment_count(80)
    run(d.set_segment_rgb([0, 60], (9, 8, 7)))
    assert _sent(d)[-1][12:19] == bytes([1, 0, 0, 0, 0, 0, 0])
    assert [s.index for s in d.state.segments if s.rgb == (9, 8, 7)] == [0]
    run(d.set_segment_brightness([0, 60], 40))
    assert _sent(d)[-1][5:15] == (1 | 1 << 60).to_bytes(10, "little")
    assert sorted(s.index for s in d.state.segments if s.brightness == 40) == [0, 60]
