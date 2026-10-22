"""T6.1 Device correctness fixes (audit BUG-2/3/5, DES-1/2/7/9/11, secret length)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device
from test_device_gaps import _dev, _frame, _queried, _sent, _unchecked_dev

from govee_ble_local.devices.device import Device, _mask
from govee_ble_local.exceptions import (
    GoveeBleError,
    GoveeBleInvalidValue,
    GoveeBleNotSupported,
    GoveeBleTimeout,
)
from govee_ble_local.models import Capability


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# BUG-2 / BUG-3 are covered once, in test_device_gaps.py (advertisement encryption,
# bar-switch read-back refusing to guess the other bar).


# ── BUG-5 ───────────────────────────────────────────────────────────────────
def test_device_info_flag_stays_false_when_read_times_out_and_retries() -> None:
    # The transport turns every timeout into GoveeBleTimeout (never a bare TimeoutError):
    # update() swallows it, the state read stays good, and the next poll retries.
    d = _dev("H6008", {0x01: [_frame(0xAA, 0x01, 0x01)]})
    calls: list[int] = []
    inner = d._conn.query

    async def query(frame: bytes, **k: object) -> list[bytes]:
        if frame[1] == 0x07:
            calls.append(1)
            raise GoveeBleTimeout("device-info read timed out")
        return list(await inner(frame, **k))

    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    st = run(d.update())
    assert st.is_on is True and d._device_info_read is False
    run(d.update())
    assert len(calls) == 2                # retried next poll


def test_device_info_silent_device_is_read_again_next_poll() -> None:
    # A poll on which the device answered none of the device-info reads (wrong cipher, lost
    # notifications, busy) does not use up the one-shot: the next poll reads them again.
    d = _dev("H6008")
    run(d.update())
    assert d._device_info_read is False
    n = len(_queried(d))
    run(d.update())
    assert any(q[1] == 0x06 for q in _queried(d)[n:])


def test_device_info_answered_reads_count_as_attempted() -> None:
    # Once the device answers one of them, unanswered selectors are not retried every poll.
    d = _dev("H6008", {0x06: [_frame(0xAA, 0x06, *b"1.00.01")]})
    run(d.update())
    assert d._device_info_read is True and d.state.firmware_version == "1.00.01"
    n = len(_queried(d))
    run(d.update())
    assert all(q[1] not in (0x06, 0x07) for q in _queried(d)[n:])


# ── DES-1 ───────────────────────────────────────────────────────────────────
def test_mask_validates_indices() -> None:
    assert _mask([0, 15], 16) == 0x8001
    for bad in (-1, 16, 99):
        with pytest.raises(GoveeBleInvalidValue):
            _mask([bad], 16)


def test_invalid_segment_index_sends_nothing() -> None:
    d = _dev("H6047")
    with pytest.raises(GoveeBleInvalidValue):
        run(d.set_segment_rgb([16], (1, 2, 3)))
    assert _sent(d) == []


def test_build_or_unsupported_propagates_invalid_value() -> None:
    d = _dev("H60A6")

    def bad(*_a: object) -> bytes:
        raise GoveeBleInvalidValue("nope")

    with pytest.raises(GoveeBleInvalidValue):
        d._build_or_unsupported(bad)

    def unsupported(*_a: object) -> bytes:
        raise GoveeBleNotSupported("scheme")

    with pytest.raises(GoveeBleNotSupported, match=r"^H60A6: scheme$"):
        d._build_or_unsupported(unsupported)

    # A builder's own programming error (frame()'s payload check) is not "not supported": it
    # propagates as the ValueError it is instead of being relabelled.
    def misuse(*_a: object) -> bytes:
        raise ValueError("payload too long")

    with pytest.raises(ValueError, match="payload too long") as err:
        d._build_or_unsupported(misuse)
    assert not isinstance(err.value, GoveeBleNotSupported)


@pytest.mark.parametrize(("kelvin", "sent"), [(100, 2700), (2699, 2700), (6501, 6500), (9000, 6500)])
def test_kelvin_outside_profile_range_is_clamped(kelvin: int, sent: int) -> None:
    # 2700..6500, zones + segments; the app clamps (KelvinConfig.calibrationKelvin) and never rejects
    for whole_device, make in (
        (True, lambda d: d.set_color_temp(kelvin)),
        (False, lambda d: d.set_segment_color_temp([0], kelvin)),
        (False, lambda d: d.set_zone_color_temp("background", kelvin)),
        (True, lambda d: d.turn_on(kelvin=kelvin)),
    ):
        d = _dev("H60A6")
        run(make(d))
        if whole_device:  # the segment/zone setters do not touch the whole-device state
            assert d.state.color_temp_kelvin == sent
        cct = [f for f in _sent(d) if f[1:3] == b"\x05\x15"]
        assert cct and all(f[7:9] == sent.to_bytes(2, "big") for f in cct)


def test_non_int_kelvin_still_rejected() -> None:
    d = _dev("H60A6")
    with pytest.raises(GoveeBleInvalidValue):
        run(d.set_color_temp(4000.5))  # type: ignore[arg-type]
    assert _sent(d) == [] and d.state.color_temp_kelvin is None


def test_kelvin_range_bounds_inclusive_and_undefined_range_allows() -> None:
    d = _dev("H60A6")
    run(d.set_color_temp(2700))
    run(d.set_color_temp(6500))
    assert len(_sent(d)) == 2          # H60A6 whole-device CCT = one all-segments frame
    t = _dev("H60A6", min_kelvin=None, max_kelvin=None)
    run(t.set_color_temp(1000))
    assert len(_sent(t)) == 1


@pytest.mark.parametrize(("sku", "overrides", "call"), [
    ("H60A6", {"capabilities": frozenset({Capability.POWER, Capability.BRIGHTNESS})},
     lambda d: d.set_zone_rgb("background", (1, 2, 3))),
    ("H60A6", {"capabilities": frozenset({Capability.COLOR_TEMP})},   # SEGMENTS missing
     lambda d: d.set_zone_color_temp("background", 4000)),
    # No Kelvin to a piece selection (DeviceProfile.segment_color_temp); a zone's goes (test_device_gaps).
    ("H60A6", {"segment_color_temp": False}, lambda d: d.set_segment_color_temp([0], 4000)),
    # A fresh H6062 (striplight 41, no Part-tab Kelvin) is refused before its aa 41 segment-count read.
    ("H6062", {}, lambda d: d.set_segment_color_temp([0], 4000)),
])
def test_zone_and_segment_colour_setters_refuse_before_any_io(sku: str, overrides: dict[str, Any],
                                                              call: Any) -> None:
    d = _dev(sku, **overrides)
    with pytest.raises(GoveeBleNotSupported):
        run(call(d))
    assert _sent(d) == [] and _queried(d) == []


def test_set_rgb_out_of_range_invalid_value_nothing_sent() -> None:
    d = _dev("H6008")
    with pytest.raises(GoveeBleInvalidValue):
        run(d.set_rgb((256, 0, 0)))
    assert _sent(d) == [] and d.state.rgb_color is None


# ── DES-2 ───────────────────────────────────────────────────────────────────
def test_raising_callback_isolated_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    d = _dev("H6008")
    later: list[object] = []

    def boom(_s: object) -> None:
        raise RuntimeError("callback failed")

    d.register_callback(boom)
    d.register_callback(later.append)
    with caplog.at_level(logging.WARNING, logger="govee_ble_local.devices.device"):
        run(d.set_power(True))
    assert len(_sent(d)) == 1 and d.state.is_on is True
    assert len(later) == 1
    assert any("boom" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


# ── DES-7 ───────────────────────────────────────────────────────────────────
def test_update_unsupported_readback_raises() -> None:
    d = _unchecked_dev("H6008", readback="bogus")  # bypass construction validation
    with pytest.raises(GoveeBleError, match="unsupported readback 'bogus'"):
        run(d.update())


# ── DES-11 ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "call",
    [
        lambda d: d.set_brightness(50),
        lambda d: d.set_rgb((1, 2, 3)),
        lambda d: d.set_color_temp(4000),
    ],
)
def test_setters_mark_authoritative(call: Any) -> None:
    d = _dev("H6008")
    d._state.is_on = False
    run(call(d))
    from govee_ble_local.devices.device import time

    assert d._advert_ignore_until > time.monotonic()


def test_segment_and_zone_setters_mark_authoritative() -> None:
    from govee_ble_local.devices.device import time

    for call in (
        lambda d: d.set_segment_rgb([0], (1, 2, 3)),
        lambda d: d.set_segment_brightness([0], 50),
        lambda d: d.set_segment_color_temp([0], 4000),
        lambda d: d.set_zone_rgb("background", (1, 2, 3)),
        lambda d: d.set_zone_color_temp("background", 4000),
    ):
        d = _dev("H60A6")
        run(call(d))
        assert d._advert_ignore_until > time.monotonic()


def test_turn_on_rgb_and_kelvin_together_rejected() -> None:
    d = _dev("H6008")
    with pytest.raises(GoveeBleInvalidValue):
        run(d.turn_on(rgb=(1, 2, 3), kelvin=4000))
    assert _sent(d) == []


# ── secret length ───────────────────────────────────────────────────────────
def test_secret_length_validated() -> None:
    d = _dev("H6008")
    with pytest.raises(GoveeBleInvalidValue):
        d.set_secret(b"short")
    d.set_secret(b"12345678")
    with pytest.raises(GoveeBleInvalidValue):
        Device(d.profile, ble_device("AA:BB:CC:DD:EE:32", "H6008"), secret=b"123")
    Device(d.profile, ble_device("AA:BB:CC:DD:EE:32", "H6008"), secret=None)
    Device(d.profile, ble_device("AA:BB:CC:DD:EE:32", "H6008"), secret=b"12345678")
