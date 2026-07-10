"""govee-ble-local: local Bluetooth LE control of Govee devices.

Spec-first (v3): device behaviour is driven by a data ``DeviceProfile`` and the
Kaitai-generated wire layer, not per-SKU subclasses. Public API mirrors Home
Assistant BLE-library conventions.
"""
from __future__ import annotations

from .const import LOCAL_NAME_PREFIXES, MANUFACTURER_IDS
from .devices.device import Device
from .devices.profile import DeviceProfile
from .exceptions import (
    GoveeBleAuthError,
    GoveeBleConnectionError,
    GoveeBleError,
    GoveeBleHandshakeError,
    GoveeBleNotSupported,
    GoveeBleTimeout,
)
from .identify import GoveeAdvertisement
from .models import Capability, DeviceState, Segment, Zone
from .registry import (
    create_device,
    device_profile_for,
    is_supported_sku,
    supported_skus,
)
from .scanner import DiscoveredDevice, discover, match, supported

__all__ = [
    "Capability",
    "Device",
    "DeviceProfile",
    "DeviceState",
    "DiscoveredDevice",
    "GoveeAdvertisement",
    "GoveeBleAuthError",
    "GoveeBleConnectionError",
    "GoveeBleError",
    "GoveeBleHandshakeError",
    "GoveeBleNotSupported",
    "GoveeBleTimeout",
    "LOCAL_NAME_PREFIXES",
    "MANUFACTURER_IDS",
    "Segment",
    "Zone",
    "create_device",
    "device_profile_for",
    "discover",
    "is_supported_sku",
    "match",
    "supported",
    "supported_skus",
]
