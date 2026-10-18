"""registry: the deprecated device_profile_for alias and the create_device contract."""
from __future__ import annotations

import pytest

import govee_ble_local as pkg
from govee_ble_local import registry
from govee_ble_local.devices.device import Device
from govee_ble_local.exceptions import GoveeBleInvalidValue, GoveeBleNotSupported

from fake_ble import ble_device


def test_device_profile_for_is_a_deprecated_alias() -> None:
    with pytest.warns(DeprecationWarning, match="use profile_for"):
        p = pkg.device_profile_for("h60a6")
    assert p is not None and p is pkg.profile_for("H60A6")
    with pytest.warns(DeprecationWarning):
        assert registry.device_profile_for("H9999") is None


def test_profile_for_does_not_warn(recwarn: pytest.WarningsRecorder) -> None:
    assert pkg.profile_for("H60A6") is not None
    assert pkg.is_supported_sku("H60A6") and not pkg.is_supported_sku("H9999")
    assert not [w for w in recwarn if issubclass(w.category, DeprecationWarning)]


def test_create_device_contract() -> None:
    dev = registry.create_device(ble_device("AA:BB:CC:DD:EE:01", "GVH60A6"), "h60a6")
    assert isinstance(dev, Device) and dev.profile is pkg.profile_for("H60A6")
    with pytest.raises(GoveeBleNotSupported, match="H9999"):
        registry.create_device(ble_device("AA:BB:CC:DD:EE:02", "GVH9999"), "H9999")
    with pytest.raises(GoveeBleInvalidValue, match="8 bytes"):
        registry.create_device(ble_device("AA:BB:CC:DD:EE:03", "ihoment_H5083_0001"), "H5083",
                               secret=bytes(7))


def test_create_device_docstring_covers_its_parameters() -> None:
    doc = registry.create_device.__doc__ or ""
    for word in ("secret", "idle_disconnect", "frame_log", "GoveeBleNotSupported", "GoveeBleInvalidValue"):
        assert word in doc, word
