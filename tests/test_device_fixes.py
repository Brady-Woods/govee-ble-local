"""T6.1 Device correctness fixes (audit BUG-2/3/5, DES-1/2/7/9/11, secret length)."""
from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device
from test_device_gaps import _adv, _dev, _queried, _sent

from govee_ble_local.devices.device import Device, _mask
from govee_ble_local.exceptions import GoveeBleError, GoveeBleInvalidValue, GoveeBleNotSupported
from govee_ble_local.models import Capability, Encryption


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ── BUG-3 ───────────────────────────────────────────────────────────────────
def test_bar_zone_power_unknown_other_bar_raises_before_sending() -> None:
    d = _dev("H6047")  # no reply to the 0x36 read-back
    with pytest.raises(GoveeBleError, match="unknown"):
        run(d.set_zone_power("left", True))
    assert _sent(d) == [] and d.state.zone_power == {}


# ── BUG-2 ───────────────────────────────────────────────────────────────────
def test_unparseable_pact_falls_back_to_profile_encryption() -> None:
    d = _dev("H60A6")
    adv = _adv("Govee_H60A6_ABCD", {0x1234: b"\x00\x00\x00\x00"})
    assert d._resolve_encryption(adv) is d.profile.encryption is Encryption.AES_RC4_PSK


def test_parsed_plain_advert_still_decides() -> None:
    d = _dev("H60A6")
    adv = _adv("Govee_H60A6_ABCD", {0x8801: bytes([0xEC, 0x00, 0x01, 0x02])})
    assert d._resolve_encryption(adv) is Encryption.NONE


# ── BUG-5 ───────────────────────────────────────────────────────────────────
def test_device_info_flag_stays_false_when_read_raises_and_retries() -> None:
    d = _dev("H6008")
    calls: list[int] = []

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        if frame[1] == 0x07:
            calls.append(1)
            raise TimeoutError("link dropped")
        return []

    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
        run(d.update())
    assert d._device_info_read is False
    with pytest.raises(TimeoutError):
        run(d.update())
    assert len(calls) == 2


def test_device_info_empty_reads_count_as_attempted() -> None:
    d = _dev("H6008")
    run(d.update())
    assert d._device_info_read is True
    n = len(_queried(d))
    run(d.update())
    assert all(q[1] != 0x07 for q in _queried(d)[n:])


# ── DES-1 ───────────────────────────────────────────────────────────────────
def test_mask_validates_indices() -> None:
    assert _mask([0, 15]) == 0x8001
    for bad in (-1, 16, 99):
        with pytest.raises(GoveeBleInvalidValue):
            _mask([bad])


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
        raise ValueError("scheme")

    with pytest.raises(GoveeBleNotSupported):
        d._build_or_unsupported(unsupported)


@pytest.mark.parametrize("kelvin", [100, 2699, 6501, 9000])
def test_kelvin_outside_profile_range_rejected(kelvin: int) -> None:
    d = _dev("H60A6")  # 2700..6500, zones + segments
    for make in (
        lambda: d.set_color_temp(kelvin),
        lambda: d.set_segment_color_temp([0], kelvin),
        lambda: d.set_zone_color_temp("background", kelvin),
        lambda: d.turn_on(kelvin=kelvin),
    ):
        with pytest.raises(GoveeBleInvalidValue):
            run(make())
    assert _sent(d) == [] and d.state.color_temp_kelvin is None


def test_kelvin_range_bounds_inclusive_and_undefined_range_allows() -> None:
    d = _dev("H60A6")
    run(d.set_color_temp(2700))
    run(d.set_color_temp(6500))
    assert len(_sent(d)) == 4          # H60A6 whole-device CCT = main panel + ring frames
    t = _dev("H60A6", min_kelvin=None, max_kelvin=None)
    run(t.set_color_temp(1000))
    assert len(_sent(t)) == 2


def test_zone_rgb_and_color_temp_require_capabilities() -> None:
    caps = frozenset({Capability.POWER, Capability.BRIGHTNESS})
    d = _dev("H60A6", capabilities=caps)
    with pytest.raises(GoveeBleNotSupported):
        run(d.set_zone_rgb("background", (1, 2, 3)))
    only_ct = _dev("H60A6", capabilities=frozenset({Capability.COLOR_TEMP}))
    with pytest.raises(GoveeBleNotSupported):  # SEGMENTS missing
        run(only_ct.set_zone_color_temp("background", 4000))
    assert _sent(d) == [] and _sent(only_ct) == []


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
    d = _dev("H6008")
    d.profile = copy.copy(d.profile)  # profiles are shared; never mutate the registry's
    object.__setattr__(d.profile, "readback", "bogus")  # bypass construction validation
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
