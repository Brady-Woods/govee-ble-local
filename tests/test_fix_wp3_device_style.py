"""Regression tests for the device-layer style batch (review findings WP3-004 .. WP3-065) and the
split of ``Device`` into ``devices/_fold.py`` (state folding) and ``devices/_readback.py`` (read walks).

Offline and deterministic: connection I/O is mocked, and the moved walks run on scripted reads
without a ``Device`` or a connection.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import dataclasses
import inspect
import logging
import textwrap
import time
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device
from fake_ble import frame as _frame

from govee_ble_local import const
from govee_ble_local.devices import _fold, _readback
from govee_ble_local.devices import device as device_mod
from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import DeviceProfile, profile_for
from govee_ble_local.exceptions import GoveeBleError, GoveeBleInvalidValue, GoveeBleTimeout
from govee_ble_local.models import DeviceState, Segment
from govee_ble_local.wire import build, reassemble

_DEVICE_LOGGER = "govee_ble_local.devices.device"
_ADDR = "AA:BB:CC:DD:EE:3C"


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _mocked(d: Device) -> Device:
    """Record every write; every read gets no reply; an unscripted scene upload fails the test, so
    nothing reaches the real connection."""
    d._sent = []  # type: ignore[attr-defined]

    async def send(frame: bytes, **_k: object) -> None:
        d._sent.append(frame)  # type: ignore[attr-defined]

    async def send_many(frames: list[bytes]) -> None:
        d._sent.extend(frames)  # type: ignore[attr-defined]

    async def query(_frame: bytes, **_k: object) -> list[bytes]:
        return []

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("unexpected upload"))
    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    return d


def _dev(sku: str) -> Device:
    d = make_device(ble_device(_ADDR, sku), sku)
    assert d is not None
    return _mocked(d)


def _unchecked(sku: str, **changes: Any) -> Device:
    """A device on a copy of the SKU's profile changed past DeviceProfile's validation."""
    base = profile_for(sku)
    assert base is not None
    profile = copy.copy(base)
    for name, value in changes.items():
        object.__setattr__(profile, name, value)
    return _mocked(Device(profile, ble_device(_ADDR, sku), sku=sku))


@dataclasses.dataclass
class _Target:
    """The device a moved walk reads for (``_readback.ReadTarget``), without a Device."""

    sku: str
    profile: DeviceProfile
    segment_count: int
    state: DeviceState = dataclasses.field(default_factory=DeviceState)
    address: str = _ADDR


def _target(sku: str, segment_count: int | None = None) -> _Target:
    p = profile_for(sku)
    assert p is not None
    return _Target(sku, p, p.segments if segment_count is None else segment_count)


# ── WP3-004: the advert suppression window is a named Final constant in const ───────────────
def test_advert_suppression_window_lives_in_const() -> None:
    assert const.ADVERT_SUPPRESS_SECONDS == 15.0
    assert device_mod.ADVERT_SUPPRESS_SECONDS is const.ADVERT_SUPPRESS_SECONDS   # still importable
    d = _dev("H6008")
    before = time.monotonic()
    run(d.set_power(True))
    assert before + 15.0 <= d._advert_ignore_until <= time.monotonic() + 15.0


# ── WP3-010: idle_disconnect is passed as a typed keyword, None meaning the library default ──
@pytest.mark.parametrize(("given", "expected"), [(None, const.IDLE_DISCONNECT_DELAY), (3.5, 3.5), (0.0, 0.0)])
def test_idle_disconnect_reaches_the_connection(given: float | None, expected: float) -> None:
    d = make_device(ble_device(_ADDR, "H6008"), "H6008", idle_disconnect=given)
    assert d is not None and d._conn._idle_disconnect == expected


# ── WP3-018: the unregister function ──────────────────────────────────────────────────────────
def test_unregister_removes_only_its_callback_and_a_second_call_is_a_no_op() -> None:
    d = _dev("H6008")
    first: list[DeviceState] = []
    second: list[DeviceState] = []
    off_first = d.register_callback(first.append)
    off_second = d.register_callback(second.append)
    off_first()
    off_first()                                # a second call is a no-op
    run(d.set_power(True))
    assert (len(first), len(second)) == (0, 1)  # the other callback still fires
    off_second()
    run(d.set_power(False))
    assert (len(first), len(second)) == (0, 1)


# ── WP3-024: the relay follow-up warning names an error that has no message ──────────────────
@pytest.mark.parametrize(("error", "tail"), [
    (GoveeBleTimeout(), "failed: GoveeBleTimeout"),
    (GoveeBleTimeout("no ack"), "failed: no ack"),
])
def test_relay_follow_up_failure_logs_a_readable_reason(
    caplog: pytest.LogCaptureFixture, error: GoveeBleError, tail: str
) -> None:
    d = _dev("H5080")

    async def send(frame: bytes, *, expect_ack: bool = True, **_k: object) -> None:
        if not expect_ack:                     # the sync-time follow-up
            raise error
        d._sent.append(frame)  # type: ignore[attr-defined]

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING, logger=_DEVICE_LOGGER):
        run(d.set_power(True))
    assert d.state.is_on is True and d._sent == [build.switch(True, relay=True)]  # type: ignore[attr-defined]
    lines = [r.getMessage() for r in caplog.records if "sync-time follow-up failed" in r.getMessage()]
    assert len(lines) == 1 and lines[0].endswith(tail)


# ── WP3-031: the segment setters take any index sequence ──────────────────────────────────────
@pytest.mark.parametrize("indices", [(0, 2), range(0, 3, 2)], ids=["tuple", "range"])
def test_segment_setters_take_any_index_sequence(indices: Sequence[int]) -> None:
    d, ref = _dev("H60A6"), _dev("H60A6")
    for dev, idx in ((d, indices), (ref, [0, 2])):
        run(dev.set_segment_rgb(idx, (1, 2, 3)))
        run(dev.set_segment_brightness(idx, 40))
        run(dev.set_segment_color_temp(idx, 3000))
    assert d._sent == ref._sent  # type: ignore[attr-defined]
    assert d.state.segments == ref.state.segments
    assert [s.index for s in d.state.segments] == [0, 2]


# ── WP3-044: an unhandled colour read-back fails loudly, like an unhandled readback ──────────
def test_unknown_color_readback_raises_like_an_unknown_readback() -> None:
    d = _unchecked("H6008", color_readback="bogus")   # bypass construction validation
    with pytest.raises(GoveeBleError, match="unsupported color_readback 'bogus'"):
        run(d.update())
    with pytest.raises(GoveeBleError, match="unsupported readback 'bogus'"):
        run(_unchecked("H6008", readback="bogus").update())


def test_no_color_readback_is_still_a_no_op() -> None:
    d = _dev("H6008")                          # color_readback ""
    run(d.update())
    assert d.state.segments == []


# ── WP3-062: DeviceProfile is documented for pdoc (class and every field) ─────────────────────
def test_device_profile_documents_the_class_and_every_field() -> None:
    assert DeviceProfile.__doc__ is not None and "SKU family" in DeviceProfile.__doc__
    cls = ast.parse(textwrap.dedent(inspect.getsource(DeviceProfile))).body[0]
    assert isinstance(cls, ast.ClassDef)
    documented = {
        stmt.target.id
        for stmt, nxt in zip(cls.body, cls.body[1:], strict=False)
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
        and isinstance(nxt, ast.Expr) and isinstance(nxt.value, ast.Constant)
        and isinstance(nxt.value.value, str)
    }
    assert documented == {f.name for f in dataclasses.fields(DeviceProfile)}


# ── WP3-065: profile validation errors name the SKUs without tuple punctuation ───────────────
@pytest.mark.parametrize(("skus", "prefix"), [
    (("HX",), "DeviceProfile[HX]: "),
    (("HX", "HY"), "DeviceProfile[HX, HY]: "),
])
def test_profile_errors_name_the_skus_plainly(skus: tuple[str, ...], prefix: str) -> None:
    with pytest.raises(GoveeBleInvalidValue) as err:
        DeviceProfile(skus=skus, capabilities=frozenset(), readback="bogus")  # type: ignore[arg-type]
    assert str(err.value).startswith(prefix + "readback='bogus' not in")
    with pytest.raises(GoveeBleInvalidValue) as err:
        DeviceProfile(skus=skus, capabilities=frozenset(), relay_on_mask=0)
    assert str(err.value) == prefix + "relay_on_mask=0"


def test_profile_error_for_bad_skus_shows_what_was_given() -> None:
    with pytest.raises(GoveeBleInvalidValue, match=r"^DeviceProfile\(skus=\(\)\): skus must be"):
        DeviceProfile(skus=(), capabilities=frozenset())


# ── WP3-007 / WP3-008: state folding is testable on a bare DeviceState ─────────────────────────
def test_fold_scene_report_clears_the_colours_on_a_bare_state() -> None:
    p = profile_for("H6047")
    assert p is not None
    st = DeviceState(rgb_color=(1, 2, 3), color_temp_kelvin=3000, zone_color_temp_kelvin={0: 3000, 1: 3000})
    _fold.apply_mode_reply(p, st, _frame(0xAA, 0x05, 0x04, 0x01, 0x00))
    assert st.scene_code == 1 and st.rgb_color is None and st.color_temp_kelvin is None
    assert st.zone_color_temp_kelvin == {}


def test_fold_status_reports_what_it_applied() -> None:
    p = profile_for("H60A6")
    assert p is not None
    st = DeviceState(optimistic=True)
    assert _fold.apply_status(p, st, reassemble.StatusReply()) == (False, False)        # empty burst
    assert _fold.apply_status(p, st, reassemble.StatusReply(serial_number="S")) == (False, False)
    assert st.serial_number == "S"                                                     # merged, not "applied"
    assert _fold.apply_status(p, st, reassemble.StatusReply(brightness=40)) == (True, False)
    assert _fold.apply_status(p, st, reassemble.StatusReply(is_on=True)) == (True, True)
    assert st.is_on is True and st.brightness == 40


def test_fold_advert_power_keeps_the_zones_consistent() -> None:
    p = profile_for("H60A6")
    assert p is not None
    st = DeviceState(zone_power={0: True, 1: False})
    _fold.apply_advert_power(p, st, False)
    assert st.is_on is False and st.zone_power == {0: False, 1: False}
    _fold.apply_advert_power(p, st, True)
    assert st.is_on is True and st.zone_power == {}                   # all read off -> forgotten


def test_device_delegates_fold_into_its_own_state() -> None:
    d = _dev("H6008")
    d._apply_mode_reply(_frame(0xAA, 0x05, 0x0D, 255, 128, 64, 0, 0))
    ref = DeviceState(optimistic=True)
    _fold.apply_mode_reply(d.profile, ref, _frame(0xAA, 0x05, 0x0D, 255, 128, 64, 0, 0))
    assert d.state == ref and d.state.rgb_color == (255, 128, 64)


# ── WP3-007 / WP3-008: the read walks run on a scripted read_reply, without a Device ──────────
def test_walk_mechanism_b_asks_each_batch_in_order() -> None:
    target = _target("H61A8")
    asked: list[bytes] = []

    async def read_reply(frame: bytes, command_type: int) -> bytes | None:
        asked.append(frame)
        assert command_type == build.CMD_BULB_COLOR_V2
        seq = frame[2]
        if seq == 2:
            return _frame(0xAA, 0xA5, 2, 50, 1, 2, 3, 60, 4, 5, 6, 70, 7, 8, 9)
        return None

    segs = run(_readback.walk_mechanism_b(target, read_reply))
    assert asked == [build.bulb_group_query(seq, v2=True) for seq in range(1, 6)]   # 15 / 3 = 5 batches
    assert segs == [Segment(3, (1, 2, 3), 50), Segment(4, (4, 5, 6), 60), Segment(5, (7, 8, 9), 70)]


def test_walk_mechanism_a_direct_skips_on_a_failed_ic_read_and_logs_on_the_device_logger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    target = _target("H6641")
    asked: list[bytes] = []

    async def read_reply(frame: bytes, _command_type: int) -> bytes | None:
        asked.append(frame)
        return None

    with caplog.at_level(logging.DEBUG, logger=_DEVICE_LOGGER):
        assert run(_readback.walk_mechanism_a_direct(target, read_reply)) is None
    assert asked == [build.ic_count_query()]                          # no group walk without a count
    assert any(r.name == _DEVICE_LOGGER and "live IC-count read failed" in r.getMessage()
               for r in caplog.records)


def test_read_device_info_reports_a_silent_device() -> None:
    target = _target("H6008")
    asked: list[bytes] = []

    async def read_reply(frame: bytes, _command_type: int) -> bytes | None:
        asked.append(frame)
        return None

    assert run(_readback.read_device_info(target, read_reply)) is False
    assert asked == [build.soft_version_query(), build.device_info_query(build.INFO_HARD),
                     build.device_info_query(build.INFO_SN), build.device_info_query(build.INFO_WIFI),
                     build.device_info_query(build.INFO_BASIC)]
    assert target.state == DeviceState()                              # nothing invented


def test_status_burst_retries_once_then_warns(caplog: pytest.LogCaptureFixture) -> None:
    target = _target("H60A6")
    calls = 0

    async def query_status() -> tuple[reassemble.StatusReply, list[bytes], list[bytes]]:
        nonlocal calls
        calls += 1
        return reassemble.StatusReply(), [], []

    with caplog.at_level(logging.WARNING, logger=_DEVICE_LOGGER):
        st = run(_readback.status_burst(target, query_status))
    assert st.is_on is None and calls == 2
    assert any("no usable data after retry" in r.getMessage() and "chunks <none>" in r.getMessage()
               for r in caplog.records)
