"""Characterization tests for govee_ble_local.scanner (BleakScanner faked)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from govee_ble_local import scanner
from govee_ble_local.scanner import DiscoveredDevice, discover, match, supported

_MFG = {(0x88 << 8) | 0x41: bytes([0xEC, 0, 2, 2, 1])}


def _dev(addr: str, name: str | None) -> SimpleNamespace:
    return SimpleNamespace(address=addr, name=name)


def _adv(local_name: str | None, rssi: int | None, mfg: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(local_name=local_name, rssi=rssi, manufacturer_data=_MFG if mfg is None else mfg)


def test_supported() -> None:
    assert supported("ihoment_H5083_A2D1", _MFG) is True
    assert supported("ihoment_H9999_A2D1", _MFG) is False  # Govee but unknown SKU
    assert supported("NotGovee", _MFG) is False


def test_match_properties_and_fallback_to_device_name() -> None:
    hit = match(_dev("AA:01", "Govee_H61A8_631F"), _adv(None, -50))
    assert isinstance(hit, DiscoveredDevice)
    assert (hit.address, hit.sku, hit.name, hit.rssi, hit.supported) == (
        "AA:01", "H61A8", "Govee_H61A8_631F", -50, True,
    )
    # advertisement_data without attributes at all
    hit2 = match(_dev("AA:02", "ihoment_H5083_0001"), object())
    assert hit2 is not None and hit2.rssi is None and hit2.advertisement.pact_type == -1
    assert match(_dev("AA:03", "Nope"), _adv(None, -1)) is None


@pytest.fixture
def fake_scan(monkeypatch: pytest.MonkeyPatch):
    def install(results: dict) -> dict:
        seen: dict = {}

        async def fake_discover(**kwargs):  # type: ignore[no-untyped-def]
            seen.update(kwargs)
            return results

        monkeypatch.setattr(scanner.BleakScanner, "discover", fake_discover)
        return seen

    return install


async def test_discover_filters_dedups_and_sorts(fake_scan) -> None:  # type: ignore[no-untyped-def]
    results = {
        # same address under two keys: strongest RSSI must win
        "k1": (_dev("AA:01", "Govee_H61A8_0001"), _adv(None, -80)),
        "k2": (_dev("AA:01", "Govee_H61A8_0001"), _adv(None, -40)),
        "k3": (_dev("AA:01", "Govee_H61A8_0001"), _adv(None, -90)),  # weaker, ignored
        "k4": (_dev("AA:02", "ihoment_H5083_0002"), _adv(None, -60)),
        "k5": (_dev("AA:03", "ihoment_H9999_0003"), _adv(None, -10)),  # unsupported
        "k6": (_dev("AA:04", "Other"), _adv(None, -5)),                # not Govee
        "k7": (_dev("AA:05", "ihoment_H5083_0005"), _adv(None, None)),  # no rssi
    }
    seen = fake_scan(results)
    found = await discover(timeout=1.5)
    assert seen == {"timeout": 1.5, "return_adv": True}
    assert [(d.address, d.rssi) for d in found] == [("AA:01", -40), ("AA:02", -60), ("AA:05", None)]

    everything = await discover(supported_only=False)
    assert [d.address for d in everything] == ["AA:03", "AA:01", "AA:02", "AA:05"]
    assert everything[0].supported is False


async def test_discover_empty(fake_scan) -> None:  # type: ignore[no-untyped-def]
    fake_scan({})
    assert await discover() == []
