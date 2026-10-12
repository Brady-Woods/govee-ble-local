"""Malformed-device-data guards in wire parsing: group index 0 must be skipped, never
produce negative segment indices, and never raise."""
from __future__ import annotations

import logging

import pytest

from govee_ble_local.wire import parse, reassemble


def _frame(*payload: int) -> bytes:
    b = bytearray(20)
    b[: len(payload)] = bytes(payload)
    x = 0
    for c in b[:19]:
        x ^= c
    b[19] = x
    return bytes(b)


def test_bulb_groups_skip_batch_zero() -> None:
    segs = parse.bulb_groups_to_segments(
        [(0, [(None, 1, 1, 1)]), (1, [(5, 2, 2, 2)]), (-3, [(1, 3, 3, 3)])], 3)
    assert [(s.index, s.brightness) for s in segs] == [(0, 5)]


def test_v1_brightness_stays_none() -> None:
    segs = parse.bulb_groups_to_segments([(1, [(None, 1, 2, 3)])], 4)
    assert segs[0].brightness is None


def test_direct_groups_skip_group_zero(caplog: pytest.LogCaptureFixture) -> None:
    good = parse.parse_direct_color_group(
        _frame(0xAA, 0xA5, 1, 50, 255, 0, 0), expected_records=1)
    bad = parse.parse_direct_color_group(
        _frame(0xAA, 0xA5, 0, 50, 9, 9, 9), expected_records=1)
    assert good is not None and bad is not None and bad[0] == 0
    with caplog.at_level(logging.DEBUG, logger="govee_ble_local.wire.parse"):
        segs = parse.direct_groups_to_segments([bad, good], 4)
    assert [s.index for s in segs] == [0] and segs[0].rgb == (255, 0, 0)
    assert any("invalid index 0" in r.message for r in caplog.records)


def test_status_reply_skips_group_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    def tlv(idx: int, rgb: tuple[int, int, int]) -> bytes:
        return bytes([0xA5, 5, idx, 100, *rgb])

    buf = tlv(0, (9, 9, 9)) + tlv(2, (1, 2, 3)) + b"\x00\x00"
    monkeypatch.setattr(reassemble, "reassemble", lambda frames: buf)
    st = reassemble.parse_status([])
    assert [(s.index, s.rgb) for s in st.segments] == [(4, (1, 2, 3))]
