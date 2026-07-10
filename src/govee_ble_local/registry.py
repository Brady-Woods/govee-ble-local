"""SKU -> device factory (data-driven from the DeviceProfile table)."""
from __future__ import annotations

from typing import Any

from bleak.backends.device import BLEDevice

from .devices.device import Device, make_device
from .devices.profile import DeviceProfile, profile_for, supported_skus
from .exceptions import GoveeBleNotSupported

__all__ = [
    "Device",
    "create_device",
    "device_profile_for",
    "is_supported_sku",
    "supported_skus",
]


def device_profile_for(sku: str) -> DeviceProfile | None:
    """The DeviceProfile for `sku`, or None if unsupported."""
    return profile_for(sku)


def is_supported_sku(sku: str) -> bool:
    return profile_for(sku) is not None


def create_device(
    ble_device: BLEDevice,
    sku: str,
    advertisement_data: Any | None = None,
    *,
    secret: bytes | None = None,
) -> Device:
    """Construct a capability-driven Device for `sku`."""
    dev = make_device(ble_device, sku, advertisement_data, secret=secret)
    if dev is None:
        raise GoveeBleNotSupported(f"unsupported SKU: {sku}")
    return dev
