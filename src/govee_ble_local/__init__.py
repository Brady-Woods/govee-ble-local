"""govee-ble-local: local Bluetooth LE control of Govee devices.

Ported from the decompiled Govee Home app. Public API mirrors Home Assistant
BLE-library conventions (led-ble / switchbot).
"""
from __future__ import annotations

from .const import LOCAL_NAME_PREFIXES, MANUFACTURER_IDS
from .devices.base import GoveeDevice
from .devices.plug import GoveePlug
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
    device_class_for_sku,
    is_supported_sku,
    supported_skus,
)
from .scanner import DiscoveredDevice, discover, match, supported

__all__ = [
    "Capability",
    "DeviceState",
    "DiscoveredDevice",
    "GoveeAdvertisement",
    "GoveeBleAuthError",
    "GoveeBleConnectionError",
    "GoveeBleError",
    "GoveeBleHandshakeError",
    "GoveeBleNotSupported",
    "GoveeBleTimeout",
    "GoveeDevice",
    "GoveePlug",
    "LOCAL_NAME_PREFIXES",
    "MANUFACTURER_IDS",
    "Segment",
    "Zone",
    "create_device",
    "device_class_for_sku",
    "discover",
    "is_supported_sku",
    "match",
    "supported",
    "supported_skus",
]
