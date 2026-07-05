"""Discovery: scan for Govee BLE devices and report the supported ones.

Mirrors how a Home Assistant integration discovers devices — feed it a bleak
scan (or use `discover()` directly), and it identifies each advertisement via
`identify.py` and tells you the SKU, whether it's supported, and whether it
uses the encrypted channel.
"""
from __future__ import annotations

from dataclasses import dataclass

from bleak import BleakScanner
from bleak.backends.device import BLEDevice

from .identify import GoveeAdvertisement, identify
from .registry import is_supported_sku


@dataclass(frozen=True)
class DiscoveredDevice:
    """A Govee device seen in a scan."""

    ble_device: BLEDevice
    advertisement: GoveeAdvertisement
    rssi: int | None
    supported: bool  # True if this library has a device class for the SKU

    @property
    def address(self) -> str:
        return self.ble_device.address

    @property
    def sku(self) -> str:
        return self.advertisement.sku

    @property
    def name(self) -> str:
        return self.advertisement.name


def supported(name: str | None, manufacturer_data: dict[int, bytes]) -> bool:
    """True if this advertisement is a Govee device this library can control.
    Suitable as a Home Assistant passive-matcher hook."""
    adv = identify(name, manufacturer_data)
    return adv is not None and is_supported_sku(adv.sku)


def match(ble_device: BLEDevice, advertisement_data: object) -> DiscoveredDevice | None:
    """Identify one advertisement into a DiscoveredDevice, or None if it isn't
    a recognizable Govee device."""
    name = getattr(advertisement_data, "local_name", None) or ble_device.name
    mfg = getattr(advertisement_data, "manufacturer_data", None) or {}
    adv = identify(name, mfg)
    if adv is None:
        return None
    rssi = getattr(advertisement_data, "rssi", None)
    return DiscoveredDevice(ble_device, adv, rssi, is_supported_sku(adv.sku))


async def discover(timeout: float = 10.0, *, supported_only: bool = True) -> list[DiscoveredDevice]:
    """Active-scan for Govee devices. Returns the strongest advertisement per
    address. `supported_only` filters to SKUs this library can control."""
    found: dict[str, DiscoveredDevice] = {}
    for ble_device, adv_data in (await BleakScanner.discover(timeout=timeout, return_adv=True)).values():
        hit = match(ble_device, adv_data)
        if hit is None:
            continue
        if supported_only and not hit.supported:
            continue
        prev = found.get(hit.address)
        if prev is None or (hit.rssi or -999) > (prev.rssi or -999):
            found[hit.address] = hit
    return sorted(found.values(), key=lambda d: -(d.rssi or -999))
