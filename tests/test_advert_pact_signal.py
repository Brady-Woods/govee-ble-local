"""An advertisement that changes the pact notifies the callbacks (``Device.ingest_advertisement``).

The pact picks the Kelvin range (``kelvin_by_pact``: H61F2 / H61F5 / H61F6), the brightness range and
whether brightness can be written yet (``brightness_by_pact``), the scene set (``scene_versions_by_pact``)
and whether the 0xAC status read asks for colour groups (``status_color_groups_pact``: H60A6). A changed
pact notifies the callbacks once, apart from the on/off path and its ``ADVERT_SUPPRESS_SECONDS`` window,
which is about a lagging on/off and never holds the pact back. The return value still reports only the
on/off, as in 1.4.0.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device

from govee_ble_local.devices.device import Device
from govee_ble_local.devices.profile import DeviceProfile, profile_for
from govee_ble_local.exceptions import GoveeBleNotSupported
from govee_ble_local.models import Capability, DeviceState
from govee_ble_local.wire import build, reassemble


def _adv(sku: str, pact: tuple[int, int] | None, on: int | None = None) -> Any:
    """A Govee advertisement ``[flags][pact_type u16be][pact_code][switch]``: header 2 with ``on`` (the
    app reads the switch byte from header 2 up, ``BleUtil.parseBleBroadOnOff``), header 1 (pact only)
    without. A ``pact`` of None is Govee manufacturer data too short to carry pact info."""
    if pact is None:
        mfg = {0x8802: bytes([0xEC, 0x00, 0x04])}
    else:
        body = bytes([0xEC, pact[0] >> 8, pact[0] & 0xFF, pact[1]])
        mfg = {0x8801: body + b"\x01"} if on is None else {0x8802: body + bytes([on])}
    return type("Adv", (), {"manufacturer_data": mfg, "local_name": f"Govee_{sku}_ABCD"})()


def _device(sku: str, profile: DeviceProfile | None = None) -> tuple[Device, list[DeviceState]]:
    """A device built without an advertisement (pact unknown), and the states its callback got."""
    prof = profile or profile_for(sku, allow_modelled=True)
    assert prof is not None
    d = Device(prof, ble_device("AA:BB:CC:DD:EE:5A", sku))
    calls: list[DeviceState] = []
    d.register_callback(calls.append)
    return d, calls


def test_first_pact_notifies_with_the_kelvin_range_it_picks() -> None:
    d, calls = _device("H61F2")   # kelvin_by_pact (4, 1): 1000..10000 K; 2700..6500 K on every pact
    seen: list[tuple[Any, ...]] = []
    d.register_callback(lambda _st: seen.append((d.pact, d.min_kelvin, d.max_kelvin)))
    assert (d.pact, d.min_kelvin, d.max_kelvin) == (None, 2700, 6500)
    assert d.ingest_advertisement(_adv("H61F2", (4, 1))) is False   # True only for an on/off change
    assert seen == [((4, 1), 1000, 10000)]   # the callback already sees what the pact picks
    assert len(calls) == 1 and calls[0] is d.state
    assert (d.pact, d.min_kelvin, d.max_kelvin) == ((4, 1), 1000, 10000)
    assert d.state.is_on is None   # a header-1 advertisement carries no on/off


def test_first_pact_notifies_once_brightness_can_be_written() -> None:
    prof = DeviceProfile(skus=("H6008",), capabilities=frozenset({Capability.BRIGHTNESS}),
                         brightness_range=(6, 254), brightness_by_pact=(((2, 1), None),))
    d, calls = _device("H6008", prof)
    with pytest.raises(GoveeBleNotSupported, match="pact"):
        d._brightness_frame(50)
    assert d.ingest_advertisement(_adv("H6008", (2, 1))) is False and len(calls) == 1
    assert d._brightness_frame(50) == build.brightness(50)   # percent on pact (2, 1)


def test_unchanged_pact_is_no_event() -> None:
    d, calls = _device("H61F2")
    assert d.ingest_advertisement(_adv("H61F2", (4, 1), on=1)) is True
    assert d.ingest_advertisement(_adv("H61F2", (4, 1), on=1)) is False
    assert d.ingest_advertisement(_adv("H61F2", (4, 1))) is False
    assert len(calls) == 1 and d.pact == (4, 1)


def test_pact_change_with_on_off_unchanged_notifies() -> None:
    d, calls = _device("H60A6")   # status_color_groups_pact (1, 2)
    # On/off and pact in one advertisement: one notification.
    assert d.ingest_advertisement(_adv("H60A6", (1, 1), on=1)) is True and len(calls) == 1
    assert reassemble.TYPE_COLOR_GROUP not in d._status_types()
    assert d.ingest_advertisement(_adv("H60A6", (1, 2), on=1)) is False and len(calls) == 2
    assert d.pact == (1, 2) and d.state.is_on is True
    assert reassemble.TYPE_COLOR_GROUP in d._status_types()


def test_pact_change_inside_the_suppress_window_notifies_and_keeps_the_on_off() -> None:
    d, calls = _device("H60A6")
    d._conn.send_many = AsyncMock()  # type: ignore[method-assign]
    asyncio.run(d.set_power(True))   # authoritative: the advertised on/off is ignored for a while
    calls.clear()
    assert d.ingest_advertisement(_adv("H60A6", (1, 2), on=0)) is False and len(calls) == 1
    assert d.pact == (1, 2) and d.state.is_on is True   # the pact is learned, the lagging off is not
    assert d.ingest_advertisement(_adv("H60A6", (1, 2), on=0)) is False and len(calls) == 1
    d._advert_ignore_until = 0
    assert d.ingest_advertisement(_adv("H60A6", (1, 2), on=0)) is True and len(calls) == 2
    assert d.state.is_on is False


def test_advertisement_without_pact_info_is_no_event() -> None:
    d, calls = _device("H61F2")
    assert d.ingest_advertisement(_adv("H61F2", None)) is False and d.pact is None
    assert d.ingest_advertisement(_adv("H61F2", (4, 1))) is False and len(calls) == 1
    assert d.ingest_advertisement(_adv("H61F2", None)) is False
    assert d.ingest_advertisement(type("Adv", (), {"manufacturer_data": {}})()) is False
    assert len(calls) == 1 and d.pact == (4, 1)   # the pact heard is kept
