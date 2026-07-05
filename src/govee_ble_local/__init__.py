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
from .models import Capability, DeviceState, Segment
from .registry import (
    create_device,
    device_class_for_sku,
    is_supported_sku,
    supported_skus,
)

__all__ = [
    "Capability",
    "DeviceState",
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
    "create_device",
    "device_class_for_sku",
    "is_supported_sku",
    "supported_skus",
]
