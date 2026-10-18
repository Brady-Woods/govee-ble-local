"""SKU -> device factory (data-driven from the DeviceProfile table)."""
from __future__ import annotations

import warnings

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from .devices.device import Device
from .devices.profile import DeviceProfile, profile_for, supported_skus
from .exceptions import GoveeBleNotSupported
from .identify import GoveeAdvertisement

__all__ = [
    "Device",
    "create_device",
    "device_profile_for",
    "is_supported_sku",
    "supported_skus",
]


def device_profile_for(sku: str) -> DeviceProfile | None:
    """Deprecated alias of :func:`~govee_ble_local.devices.profile.profile_for` (None if unsupported).

    Use ``profile_for``, the canonical name. This alias emits a DeprecationWarning and is
    removed in 2.0."""
    warnings.warn(
        "device_profile_for() is deprecated; use profile_for() (removed in 2.0)",
        DeprecationWarning,
        stacklevel=2,
    )
    return profile_for(sku)


def is_supported_sku(sku: str) -> bool:
    """True if the DeviceProfile table has an entry for `sku`."""
    return profile_for(sku) is not None


def create_device(
    ble_device: BLEDevice,
    sku: str,
    advertisement_data: AdvertisementData | GoveeAdvertisement | None = None,
    *,
    secret: bytes | None = None,
    frame_log: str | None = None,
    idle_disconnect: float | None = None,
) -> Device:
    """Construct a capability-driven Device for `sku`.

    Pass the device's advertisement (bleak's, or ``DiscoveredDevice.advertisement``): its encrypt
    flag picks the command channel. Without one the SKU's profile default is used, which is wrong
    for a device whose firmware encrypts although the profile says plaintext (e.g. the H6008).

    Pass ``secret`` (the 8-byte account-lock key, ``build.SECRET_LEN``) for a device whose profile
    requires one (the plug family); it is sent after every handshake. ``Device.set_secret`` can
    supply it later.

    ``idle_disconnect`` is the seconds of inactivity after which the link is dropped. None uses
    the library default (``const.IDLE_DISCONNECT_DELAY``, 8 s); 0 or less keeps the link up.

    Pass ``frame_log=<path>`` to persist a JSONL session capture for this device (the
    frame-tier logger ``govee_ble_local.frames`` is the other, filesystem-free, capture
    surface — see the diagnostics docs).

    Raises GoveeBleNotSupported if `sku` has no DeviceProfile, and GoveeBleInvalidValue if
    ``secret`` is not exactly 8 bytes."""
    profile = profile_for(sku)
    if profile is None:
        raise GoveeBleNotSupported(f"unsupported SKU: {sku}")
    return Device(
        profile, ble_device, advertisement_data, sku=sku, secret=secret, frame_log=frame_log,
        idle_disconnect=idle_disconnect,
    )
