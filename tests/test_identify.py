"""Characterization tests for govee_ble_local.identify."""
from __future__ import annotations

import pytest

from govee_ble_local.identify import (
    GoveeAdvertisement,
    identify,
    parse_broadcast_onoff,
    parse_manufacturer_data,
    sku_from_local_name,
)


def _cid(header: int, hi: int = 0x88) -> int:
    return (hi << 8) | header


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # brand markers: split on "_", exactly 3 parts, SKU = middle (BaseBleProcessor.c/d)
        ("ihoment_H5083_A2D1", "H5083"),
        ("Govee_h61a8_631F", "H61A8"),
        ("Minger_H6008_0001", "H6008"),
        ("GBK_H1234_ABCD", "H1234"),
        ("Govee_H6052_3477", "H6052"),    # live name (bazzite scan)
        ("Govee_H6047_177E", "H6047"),    # live name (bazzite scan)
        ("ihoment_H5083", None),          # wrong segment count
        ("ihoment__A2D1", None),          # empty SKU segment
        ("Govee_H61A8_AB_CD", None),      # too many segments
        ("xGovee_H6188_1A2B", "H6188"),   # markers match anywhere (String.contains)
        ("xGBK_H1234_ABCD", None),        # ...but GBK_ is a prefix
        # GVH/GVR without "_": >= 11 chars, SKU = name[2:7] (BaseBleProcessor.f)
        ("GVH60A67457", "H60A6"),         # live H60A6 name (bazzite scan)
        ("GVH60087A93", "H6008"),         # live H6008 name (bazzite scan)
        ("GVH60A6XXXX", "H60A6"),
        ("GVH6008abc", None),             # 10 chars: too short for f()
        ("GVH", None),
        ("GVR1234567", None),             # 10 chars
        ("GVR61841234", "R6184"),         # the app keeps the 'R' (substring(2, 7))
        # GVH/GVR with "_": SKU = name[2:index('_')] (BaseBleProcessor.e)
        ("GVH6188_1A2B", "H6188"),
        ("GVR6184_XXXX", "R6184"),
        # GV: >= 10 chars, SKU = "H" + name[2:6] (BaseBleProcessor.g)
        ("GV61881A2B", "H6188"),          # was '61881' before the fix
        ("GV6188_1A2B", "H6188"),         # was '6188_' before the fix
        ("GV5122ABCD", "H5122"),          # devices.yaml name_parsing example
        ("GVh60a6xxxx", "HH60A"),         # 'GVH' is case-sensitive -> GV branch
        ("GV6188ABC", None),              # 9 chars: too short for g()
        ("GV", None),
        ("", None),
        ("SomethingElse", None),
    ],
)
def test_sku_from_local_name(name: str, expected: str | None) -> None:
    assert sku_from_local_name(name) == expected


def test_identify_follows_name_parsing() -> None:
    adv = identify("GV61881A2B", {})
    assert adv is not None and adv.sku == "H6188"
    adv = identify("xx Govee_H6188_1A2B", {})
    assert adv is not None and adv.sku == "H6188"
    assert identify("GV6188ABC", {}) is None
    assert identify(None, {}) is None


def test_parse_manufacturer_data_encrypted_and_plain() -> None:
    enc = parse_manufacturer_data({_cid(0x41): bytes([0xEC, 0x12, 0x34, 0x56, 0x01])})
    assert enc == (True, 1, 0x1234, 0x56)
    plain = parse_manufacturer_data({_cid(0x02): bytes([0xEC, 0x00, 0x02, 0x03])})
    assert plain == (False, 2, 2, 3)


def test_parse_manufacturer_data_rejects_bad_input() -> None:
    assert parse_manufacturer_data({}) is None
    assert parse_manufacturer_data({_cid(0x41): bytes([0xEC, 1, 2])}) is None  # short
    assert parse_manufacturer_data({_cid(0x41, hi=0x11): bytes([0xEC, 1, 2, 3])}) is None  # marker hi
    assert parse_manufacturer_data({_cid(0x41): bytes([0xAA, 1, 2, 3])}) is None  # marker lo
    assert parse_manufacturer_data({_cid(0x40): bytes([0xEC, 1, 2, 3])}) is None  # version 0


def test_parse_manufacturer_data_skips_bad_entry_then_finds_good() -> None:
    mfg = {0x0001: b"\x00" * 6, _cid(0x01): bytes([0xEC, 0, 9, 7])}
    assert parse_manufacturer_data(mfg) == (False, 1, 9, 7)


def test_parse_broadcast_onoff() -> None:
    on = {_cid(0x41): bytes([0xEC, 0, 1, 2, 1])}
    off = {_cid(0x41): bytes([0xEC, 0, 1, 2, 0])}
    assert parse_broadcast_onoff(on) is True
    assert parse_broadcast_onoff(off) is False
    assert parse_broadcast_onoff({}) is None
    assert parse_broadcast_onoff({_cid(0x41): bytes([0xEC, 0, 1, 2])}) is None  # too short
    assert parse_broadcast_onoff({_cid(0x41, hi=0x01): bytes([0xEC, 0, 1, 2, 1])}) is None
    assert parse_broadcast_onoff({_cid(0x41): bytes([0x00, 0, 1, 2, 1])}) is None


def test_identify_encrypted_device() -> None:
    adv = identify("ihoment_H5083_A2D1", {_cid(0x41): bytes([0xEC, 0, 2, 2, 1])})
    assert adv == GoveeAdvertisement("H5083", "ihoment_H5083_A2D1", True, 1, 2, 2)


def test_identify_rejects_non_govee_and_unparseable_sku() -> None:
    assert identify(None, {}) is None
    assert identify("", {}) is None
    assert identify("Random", {}) is None
    assert identify("ihoment_H5083", {}) is None  # Govee prefix but no SKU


def test_identify_unparseable_pact_is_plaintext_unknown() -> None:
    # CURRENT BEHAVIOR (BUG-2): a Govee-named advert without parseable pact data
    # is reported as unencrypted with pact_type/pact_code = -1.
    adv = identify("Govee_H61A8_631F", {})
    assert adv is not None
    assert adv.sku == "H61A8"
    assert adv.encrypted is False
    assert adv.pact_type == -1
    assert adv.pact_code == -1
    assert adv.broadcast_version == 0
    short = identify("Govee_H61A8_631F", {_cid(0x41): bytes([0xEC, 1])})
    assert short is not None and short.encrypted is False and short.pact_type == -1
