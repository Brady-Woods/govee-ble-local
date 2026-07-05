"""SKU -> device-class registry and the device factory."""
from __future__ import annotations

from typing import Any

from bleak.backends.device import BLEDevice

from .devices.base import GoveeDevice
from .devices.plug import GoveePlug
from .exceptions import GoveeBleNotSupported

# Register concrete device classes here as families are ported.
_DEVICE_CLASSES: tuple[type[GoveeDevice], ...] = (GoveePlug,)

# SKU (upper-case) -> class, built from each class's `skus`.
_BY_SKU: dict[str, type[GoveeDevice]] = {
    sku.upper(): cls for cls in _DEVICE_CLASSES for sku in cls.skus
}


def supported_skus() -> list[str]:
    """All SKUs this library can currently control."""
    return sorted(_BY_SKU)


def device_class_for_sku(sku: str) -> type[GoveeDevice] | None:
    return _BY_SKU.get(sku.upper())


def is_supported_sku(sku: str) -> bool:
    return sku.upper() in _BY_SKU


def create_device(
    ble_device: BLEDevice,
    sku: str,
    advertisement_data: Any | None = None,
    *,
    secret: bytes | None = None,
) -> GoveeDevice:
    """Construct the right device class for `sku`."""
    cls = device_class_for_sku(sku)
    if cls is None:
        raise GoveeBleNotSupported(f"unsupported SKU: {sku}")
    return cls(ble_device, advertisement_data, sku=sku.upper(), secret=secret)
