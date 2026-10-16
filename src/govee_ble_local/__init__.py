"""govee-ble-local: local Bluetooth LE control of Govee devices.

Spec-first: device behaviour is driven by a data ``DeviceProfile`` and the
Kaitai-generated wire layer, not per-SKU subclasses. Public API mirrors Home
Assistant BLE-library conventions; ``__all__`` is the semver-stable contract.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version

from .const import LOCAL_NAME_PREFIXES, MANUFACTURER_IDS
from .devices.device import Device, StateCallback
from .devices.profile import DeviceProfile, profile_for
from .exceptions import (
    GoveeBleAuthError,
    GoveeBleCommandRejected,
    GoveeBleConnectionError,
    GoveeBleError,
    GoveeBleHandshakeConnectionError,
    GoveeBleHandshakeError,
    GoveeBleHandshakeTimeout,
    GoveeBleInvalidValue,
    GoveeBleNotSupported,
    GoveeBleTimeout,
    GoveeBleUnknownName,
    GoveeCloudAuthError,
    GoveeCloudError,
)
from .identify import GoveeAdvertisement
from .models import Capability, DeviceState, Encryption, Segment, Zone
from .registry import (
    create_device,
    device_profile_for,
    is_supported_sku,
    supported_skus,
)
from .scanner import DiscoveredDevice, discover, match, supported
from .scenes import Scene

try:
    __version__ = _pkg_version("govee-ble-local")
except PackageNotFoundError:  # pragma: no cover - uninstalled source tree
    __version__ = "0+unknown"

__all__ = [
    "LOCAL_NAME_PREFIXES",
    "MANUFACTURER_IDS",
    "Capability",
    "Device",
    "DeviceProfile",
    "DeviceState",
    "DiscoveredDevice",
    "Encryption",
    "GoveeAdvertisement",
    "GoveeBleAuthError",
    "GoveeBleCommandRejected",
    "GoveeBleConnectionError",
    "GoveeBleError",
    "GoveeBleHandshakeConnectionError",
    "GoveeBleHandshakeError",
    "GoveeBleHandshakeTimeout",
    "GoveeBleInvalidValue",
    "GoveeBleNotSupported",
    "GoveeBleTimeout",
    "GoveeBleUnknownName",
    "GoveeCloudAuthError",
    "GoveeCloudError",
    "Scene",
    "Segment",
    "StateCallback",
    "Zone",
    "__version__",
    "create_device",
    "device_profile_for",
    "discover",
    "is_supported_sku",
    "match",
    "profile_for",
    "supported",
    "supported_skus",
]
