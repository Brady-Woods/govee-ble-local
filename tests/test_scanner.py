"""Characterization tests for govee_ble_local.scanner (BleakScanner faked)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from bleak.exc import BleakError

from govee_ble_local import scanner
from govee_ble_local.exceptions import GoveeBleConnectionError, GoveeBleError
from govee_ble_local.scanner import DiscoveredDevice, discover, match, supported

from fake_ble import FirmwareGovee

# A Govee plug's manufacturer data: the H5083's own advert, from its firmware image (kept alive: the
# provenance check accepts what a live image built).
_H5083 = FirmwareGovee()
_H5083_ADVERT = _H5083.advertisement()
assert _H5083_ADVERT is not None
_MFG = _H5083_ADVERT.manufacturer_data


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


def _by_address(*pairs: tuple[SimpleNamespace, SimpleNamespace]) -> dict:
    # bleak's discover(return_adv=True) maps each address to its latest (device, advertisement).
    return {dev.address: (dev, adv) for dev, adv in pairs}


async def test_discover_filters_and_sorts(fake_scan) -> None:  # type: ignore[no-untyped-def]
    results = _by_address(
        (_dev("AA:01", "Govee_H61A8_0001"), _adv(None, -40)),
        (_dev("AA:02", "ihoment_H5083_0002"), _adv(None, -60)),
        (_dev("AA:03", "ihoment_H9999_0003"), _adv(None, -10)),  # unsupported
        (_dev("AA:04", "Other"), _adv(None, -5)),                # not Govee
        (_dev("AA:05", "ihoment_H5083_0005"), _adv(None, None)),  # no rssi
    )
    seen = fake_scan(results)
    found = await discover(timeout=1.5)
    assert seen == {"timeout": 1.5, "return_adv": True}
    assert [(d.address, d.rssi) for d in found] == [("AA:01", -40), ("AA:02", -60), ("AA:05", None)]

    everything = await discover(supported_only=False)
    assert [d.address for d in everything] == ["AA:03", "AA:01", "AA:02", "AA:05"]
    assert everything[0].supported is False


async def test_discover_rssi_zero_is_a_reading_not_missing(fake_scan) -> None:  # type: ignore[no-untyped-def]
    # 0 dBm is a (very strong) reading: it sorts first, not alongside the devices with no RSSI.
    fake_scan(_by_address(
        (_dev("AA:01", "ihoment_H5083_0001"), _adv(None, None)),
        (_dev("AA:02", "ihoment_H5083_0002"), _adv(None, -70)),
        (_dev("AA:03", "ihoment_H5083_0003"), _adv(None, 0)),
    ))
    assert [d.address for d in await discover()] == ["AA:03", "AA:02", "AA:01"]


async def test_discover_empty(fake_scan) -> None:  # type: ignore[no-untyped-def]
    fake_scan({})
    assert await discover() == []


@pytest.mark.parametrize("err", [
    BleakError("Bluetooth adapter not found"),   # no / powered-off adapter
    FileNotFoundError(2, "No such file or directory"),   # no D-Bus socket (BlueZ)
    ConnectionRefusedError(111, "Connection refused"),
    EOFError(),                                   # D-Bus connection closed mid-scan
])
async def test_discover_failure_is_a_library_error(
    monkeypatch: pytest.MonkeyPatch, err: BaseException
) -> None:
    async def failing_discover(**_kw: object) -> dict:
        raise err

    monkeypatch.setattr(scanner.BleakScanner, "discover", failing_discover)
    with pytest.raises(GoveeBleConnectionError, match="BLE scan failed") as ei:
        await discover(timeout=0.1)
    assert isinstance(ei.value, GoveeBleError)
    assert ei.value.__cause__ is err


def test_discovered_device_is_documented() -> None:
    for name in ("address", "sku", "name"):
        assert getattr(DiscoveredDevice, name).__doc__, name
