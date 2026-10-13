"""Pure helpers factored out of Device (segment merge, ceil-div, FRAME_LEN) plus the
additive Device API (connect, idle_disconnect, device-info merge)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device

from govee_ble_local import const, crypto
from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.exceptions import GoveeBleNotSupported
from govee_ble_local.models import Segment
from govee_ble_local.registry import create_device
from govee_ble_local.wire import build, parse


def _seg(i: int, rgb: tuple[int, int, int] | None = None, bri: int | None = None) -> Segment:
    return Segment(index=i, rgb=rgb, brightness=bri)


def test_merge_segments_by_index_later_wins_and_sorted() -> None:
    old = [_seg(0, (1, 1, 1), 10), _seg(2, (2, 2, 2), 20)]
    new = [_seg(1, (9, 9, 9), 90), _seg(2, (3, 3, 3), 30)]
    out = parse.merge_segments(old, new)
    assert [s.index for s in out] == [0, 1, 2]
    assert out[2].rgb == (3, 3, 3)
    assert parse.merge_segments() == []
    assert parse.merge_segments([_seg(4, bri=1), _seg(4, bri=2)])[0].brightness == 2


def test_direct_groups_to_segments_positions() -> None:
    groups = [(2, [(50, 1, 2, 3), (60, 4, 5, 6)]), (1, [(10, 7, 8, 9)])]
    out = parse.direct_groups_to_segments(groups, 4)
    assert [s.index for s in out] == [0, 4, 5]
    assert out[1] == Segment(index=4, rgb=(1, 2, 3), brightness=50)
    assert parse.direct_groups_to_segments([], 4) == []


def test_bulb_groups_to_segments_uses_merge() -> None:
    out = parse.bulb_groups_to_segments([(2, [(None, 1, 2, 3)]), (1, [(5, 4, 5, 6)])], 3)
    assert [(s.index, s.brightness) for s in out] == [(0, 5), (3, None)]


def test_find_reply() -> None:
    a = bytes([0xAA, 0x04]) + bytes(18)
    b = bytes([0xAA, 0x05]) + bytes(18)
    assert parse.find_reply([bytes([0x33, 0x05]) + bytes(18), a, b], 0x05) == b
    assert parse.find_reply([a], 0x05) is None
    assert parse.find_reply([b"\xaa"], 0xAA) is None


def test_ceil_div() -> None:
    assert [build.ceil_div(a, 3) for a in (0, 1, 3, 4, 6, 7)] == [0, 1, 1, 2, 2, 3]
    assert build.ceil_div(12, 4) == 3


def test_frame_len_single_source_and_checksum_ok() -> None:
    assert build.FRAME_LEN is const.FRAME_LEN == 20
    body = bytes(range(19))
    ok = body + bytes([crypto.checksum(body)])
    assert crypto.checksum_ok(ok)
    assert not crypto.checksum_ok(ok[:-1] + bytes([ok[-1] ^ 1]))
    assert not crypto.checksum_ok(ok + b"\x00") and not crypto.checksum_ok(ok[:19])


def _dev(sku: str = "H6006") -> Device:
    d = make_device(ble_device("AA:BB:CC:DD:EE:30", sku), sku)
    assert d is not None
    return d


def test_merge_device_info_only_non_none() -> None:
    d = _dev()
    d._merge_device_info(serial="S1", wifi_mac="m", firmware="f1", hardware="h1")
    d._merge_device_info(serial=None, firmware="f2")
    s = d.state
    assert (s.serial_number, s.wifi_mac, s.firmware_version, s.hardware_version) == ("S1", "m", "f2", "h1")


def test_build_or_unsupported_translates_value_error() -> None:
    d = _dev()

    def boom(*_a: object) -> bytes:
        raise ValueError("nope")

    with pytest.raises(GoveeBleNotSupported, match="H6006: nope"):
        d._build_or_unsupported(boom, 1)
    assert d._build_or_unsupported(lambda x: bytes([x]), 7) == b"\x07"


def test_device_connect_delegates() -> None:
    d = _dev()
    d._conn.connect = AsyncMock()  # type: ignore[method-assign]
    asyncio.run(d.connect())
    d._conn.connect.assert_awaited_once_with()


def test_idle_disconnect_kwarg_flows_to_connection() -> None:
    dflt = _dev()
    d = make_device(ble_device("AA:BB:CC:DD:EE:31", "H6006"), "H6006", idle_disconnect=7.5)
    assert d is not None and d._conn._idle_disconnect == 7.5
    c = create_device(ble_device("AA:BB:CC:DD:EE:32", "H6006"), "H6006", idle_disconnect=3.0)
    assert c._conn._idle_disconnect == 3.0
    assert dflt._conn._idle_disconnect != 7.5   # None -> connection default kept
