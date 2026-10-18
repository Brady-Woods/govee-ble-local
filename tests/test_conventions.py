"""Shared conventions: input-error type, typed profile fields, StrEnum, exports."""
from __future__ import annotations

import dataclasses
from typing import get_args

import pytest

import govee_ble_local as pkg
from govee_ble_local import Capability, Encryption, GoveeBleError, GoveeBleInvalidValue
from govee_ble_local.devices import profile as prof
from govee_ble_local.devices.profile import PROFILES
from govee_ble_local.exceptions import GoveeBleNotSupported


def test_invalid_value_hierarchy():
    assert issubclass(GoveeBleInvalidValue, GoveeBleError)
    assert issubclass(GoveeBleInvalidValue, ValueError)
    assert not issubclass(GoveeBleInvalidValue, GoveeBleNotSupported)


FIELDS = {
    "color_scheme": prof.ColorScheme,
    "scene_dialect": prof.SceneDialect,
    "readback": prof.ReadBack,
    "color_readback": prof.ColorReadBack,
}


def test_literal_sets_exact():
    assert set(get_args(prof.SceneDialect)) == {"A", "B_h60a6", "B_h6052"}
    assert set(get_args(prof.ReadBack)) == {"status", "polled", "plug", "none"}
    assert set(get_args(prof.ColorReadBack)) == {"", "mechanism_b", "mechanism_a_direct"}


@pytest.mark.parametrize("p", PROFILES, ids=lambda p: p.skus[0])
def test_profile_values_in_literals(p):
    for name, lit in FIELDS.items():
        assert getattr(p, name) in get_args(lit), name


@pytest.mark.parametrize("field", list(FIELDS))
def test_bogus_profile_value_raises(field):
    base = PROFILES[0]
    with pytest.raises(ValueError, match=field):
        dataclasses.replace(base, **{field: "bogus"})


def test_enum_values_unchanged():
    assert {c.name: c.value for c in Capability} == {
        "POWER": "power", "BRIGHTNESS": "brightness", "RGB": "rgb",
        "COLOR_TEMP": "color_temp", "SEGMENTS": "segments", "SCENES": "scenes",
    }
    assert {e.name: e.value for e in Encryption} == {
        "NONE": "none", "AES_RC4_PSK": "aes_rc4_psk", "AES_GCM": "aes_gcm",
    }
    # StrEnum: str() is the value (old (str, Enum) gave 'Encryption.AES_RC4_PSK').
    assert str(Encryption.AES_RC4_PSK) == "aes_rc4_psk"
    assert f"{Capability.POWER}" == "power"


def test_all_resolves_and_sorted():
    assert len(set(pkg.__all__)) == len(pkg.__all__)
    for n in pkg.__all__:
        assert hasattr(pkg, n), n

    def key(n):
        return (0 if n.isupper() else 1 if n[0].isupper() else 2, n)

    assert pkg.__all__ == sorted(pkg.__all__, key=key)
    for n in ("Encryption", "Scene", "StateCallback", "GoveeBleInvalidValue",
              "profile_for", "device_profile_for", "GoveeBleHandshakeError",
              "GoveeCloudError", "GoveeCloudAuthError", "supported", "match"):
        assert n in pkg.__all__


def test_version():
    assert isinstance(pkg.__version__, str) and pkg.__version__
