"""SKU -> device factory (data-driven from the DeviceProfile table)."""
from __future__ import annotations

import warnings

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from .devices.device import Device
from .devices.profile import DeviceProfile, profile_for, supported_skus
from .exceptions import GoveeBleNotSupported
from .identify import GoveeAdvertisement, bound_advertisement

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


def is_supported_sku(sku: str, *, pact: tuple[int, int] | None = None, goods_type: int | None = None,
                     allow_modelled: bool = False) -> bool:
    """True if `sku` has a DeviceProfile: :func:`~govee_ble_local.devices.profile.profile_for` with the
    same arguments is not None."""
    return profile_for(sku, pact=pact, goods_type=goods_type, allow_modelled=allow_modelled) is not None


def create_device(
    ble_device: BLEDevice,
    sku: str,
    advertisement_data: AdvertisementData | GoveeAdvertisement | None = None,
    *,
    secret: bytes | None = None,
    frame_log: str | None = None,
    idle_disconnect: float | None = None,
    goods_type: int | None = None,
    allow_modelled: bool = False,
) -> Device:
    """Construct a capability-driven Device for `sku`.

    Pass the device's advertisement (bleak's, or ``DiscoveredDevice.advertisement``): its encrypt flag picks
    the command channel, and its pact the SKU's spec row (``profile_for(sku, pact=...)``), so the profile, and
    with it every capability, is settled here, before any entity is built. Both are read from its Govee
    manufacturer data whatever the advertised name, as the app's light control screen reads a bound device's;
    only discovery (:mod:`.scanner`) needs a Govee name. :func:`~govee_ble_local.bound_advertisement` is that
    read on its own, for a caller that wants the pact or the flag without a Device. Without an advertisement,
    or one without a Govee pact block, the SKU's profile default cipher is used, which is wrong for a device
    whose firmware encrypts although the profile says plaintext (e.g. the H6008), a SKU that several rows
    list needs ``goods_type``, and a SKU whose routes all need a pact (a pact-conditioned reassignment's
    target rows, a SKU a pairing write names at an exact pact, or a ``pact_required`` row's: H6076 / H8076 /
    R16D0, H70B6, H6198 / H6199, and behind ``allow_modelled`` H70B3 / H70B4 / H70B5 / H6810 / H6811 /
    H8811) is unsupported (``profile_for``). A SKU whose brightness range the pact picks
    (``DeviceProfile.brightness_by_pact``) is built, but refuses brightness until
    ``Device.ingest_advertisement`` hears the pact. ``allow_modelled`` also admits a ``modelled`` row
    (inferred from the app, not traced; see ``profile_for``).

    Pass ``secret`` (the 8-byte account-lock key, ``build.SECRET_LEN``) for a device whose profile
    requires one (the plug family); it is sent after every handshake. ``Device.set_secret`` can
    supply it later.

    ``idle_disconnect`` is the seconds of inactivity after which the link is dropped. None uses
    the library default (``const.IDLE_DISCONNECT_DELAY``, 8 s); 0 or less keeps the link up.

    While the link is up the device sends its profile's keep-alive read
    (``DeviceProfile.heartbeat``, every 2 s), which never keeps it up longer than that idle
    policy. To send none, build the Device from the profile with that one field cleared, on any
    SKU (its ``heartbeat_timing`` is then ignored): ``Device(dataclasses.replace(profile_for(sku, pact=...,
    goods_type=goods_type, allow_modelled=allow_modelled), heartbeat=None), ble_device, advertisement_data,
    sku=sku)`` (the arguments given here).

    Pass ``frame_log=<path>`` to persist a JSONL session capture for this device (the
    frame-tier logger ``govee_ble_local.frames`` is the other, filesystem-free, capture
    surface — see the diagnostics docs).

    Raises GoveeBleNotSupported if `sku` has no DeviceProfile (at the advertised pact), and
    GoveeBleInvalidValue if ``secret`` is not exactly 8 bytes."""
    adv = bound_advertisement(advertisement_data, sku, fallback_name=ble_device.name)
    pact = adv.pact if adv is not None else None
    profile = profile_for(sku, pact=pact, goods_type=goods_type, allow_modelled=allow_modelled)
    if profile is None:
        at = "" if pact is None else f" at pact {pact[0]},{pact[1]}"
        raise GoveeBleNotSupported(f"unsupported SKU: {sku}{at}")
    return Device(
        profile, ble_device, adv if adv is not None else advertisement_data, sku=sku, secret=secret,
        frame_log=frame_log, idle_disconnect=idle_disconnect,
    )
