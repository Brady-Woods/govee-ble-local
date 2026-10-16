"""Discovery: scan for Govee BLE devices and report the supported ones.

Mirrors how a Home Assistant integration discovers devices — feed it a bleak
scan (or use `discover()` directly), and it identifies each advertisement via
`identify.py` and tells you the SKU, whether it's supported, and whether it
uses the encrypted channel.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError

from .exceptions import GoveeBleConnectionError
from .identify import GoveeAdvertisement, from_advertisement, identify
from .registry import is_supported_sku

# Sort key for a device with no RSSI: after every real reading (dBm values are > -128).
_NO_RSSI: Final = -1000


# A failed scan is a GoveeBleConnectionError that is still an instance of the error family 1.3.0
# let through (bleak's own errors, and the OS / D-Bus errors a missing or powered-off adapter, or no
# BlueZ / D-Bus at all, surfaces as), so an ``except BleakError`` (or OSError / EOFError) written
# against 1.3.0 keeps matching, as an ``except GoveeBleError`` does.
class _ScanBleakError(GoveeBleConnectionError, BleakError):
    """The scan failed with a BleakError."""


class _ScanOSError(GoveeBleConnectionError, OSError):
    """The scan failed with an OSError."""


class _ScanEOFError(GoveeBleConnectionError, EOFError):
    """The scan failed with an EOFError (D-Bus connection closed mid-scan)."""


# What a failed scan raises below the library -> what discover() raises instead (first match wins).
_SCAN_ERRORS: Final[tuple[tuple[type[Exception], type[GoveeBleConnectionError]], ...]] = (
    (BleakError, _ScanBleakError),
    (OSError, _ScanOSError),
    (EOFError, _ScanEOFError),
)


@dataclass(frozen=True)
class DiscoveredDevice:
    """A Govee device seen in a scan."""

    ble_device: BLEDevice
    """The bleak device, as passed to :func:`~govee_ble_local.create_device`."""
    advertisement: GoveeAdvertisement
    """The parsed Govee advertisement (SKU, name, encryption flag, pact)."""
    rssi: int | None
    """Signal strength of the advertisement in dBm, or None if the scanner didn't report one."""
    supported: bool
    """True if the DeviceProfile table has an entry for the SKU (this library can control it)."""

    @property
    def address(self) -> str:
        """Bluetooth address of the device (a UUID on macOS)."""
        return self.ble_device.address

    @property
    def sku(self) -> str:
        """Govee model number from the advertisement, e.g. ``"H60A6"``."""
        return self.advertisement.sku

    @property
    def name(self) -> str:
        """Advertised local name, e.g. ``"Govee_H61A8_631F"``."""
        return self.advertisement.name


def supported(name: str | None, manufacturer_data: dict[int, bytes]) -> bool:
    """True if this advertisement is a Govee device this library can control.
    Suitable as a Home Assistant passive-matcher hook."""
    adv = identify(name, manufacturer_data)
    return adv is not None and is_supported_sku(adv.sku)


def match(ble_device: BLEDevice, advertisement_data: AdvertisementData) -> DiscoveredDevice | None:
    """Identify one advertisement into a DiscoveredDevice, or None if it isn't
    a recognizable Govee device.

    Attributes are read defensively (``getattr``) so a partial advertisement
    object is tolerated; the name falls back to the BLEDevice's (:func:`.identify.from_advertisement`,
    the one copy of this normalisation that Device uses too)."""
    adv = from_advertisement(advertisement_data, ble_device.name)
    if adv is None:
        return None
    rssi = getattr(advertisement_data, "rssi", None)
    return DiscoveredDevice(ble_device, adv, rssi, is_supported_sku(adv.sku))


def _rssi_key(device: DiscoveredDevice) -> int:
    return device.rssi if device.rssi is not None else _NO_RSSI


async def discover(timeout: float = 10.0, *, supported_only: bool = True) -> list[DiscoveredDevice]:
    """Active-scan for Govee devices, strongest signal first.

    Returns one entry per address, built from the latest advertisement bleak kept for it.
    `supported_only` filters to SKUs this library can control.

    Raises GoveeBleConnectionError if the scan itself fails (no adapter, adapter powered off,
    BlueZ / D-Bus unavailable). That error is also a BleakError, OSError or EOFError, whichever the
    failure was (1.3.0 raised that error itself), so handlers written for either still match."""
    try:
        results = await BleakScanner.discover(timeout=timeout, return_adv=True)
    except (BleakError, OSError, EOFError) as err:
        wrapper = next(ours for theirs, ours in _SCAN_ERRORS if isinstance(err, theirs))
        raise wrapper(f"BLE scan failed: {err}") from err
    # bleak keys `results` by address, so each address appears once.
    hits = [match(ble_device, adv_data) for ble_device, adv_data in results.values()]
    found = [h for h in hits if h is not None and (h.supported or not supported_only)]
    return sorted(found, key=_rssi_key, reverse=True)
