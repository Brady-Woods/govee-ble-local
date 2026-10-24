"""Characterization tests for govee_ble_local.identify."""
from __future__ import annotations

import pytest

from govee_ble_local.identify import (
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
        ("ihoment_H5083_5383", "H5083"),
        ("Govee_h61a8_53A8", "H61A8"),
        ("Minger_H6008_0001", "H6008"),
        ("GBK_H1234_ABCD", "H1234"),
        ("Govee_H6052_5352", "H6052"),    # advertised-name form (made-up suffix)
        ("Govee_H6047_5347", "H6047"),    # advertised-name form (made-up suffix)
        ("ihoment_H5083", None),          # wrong segment count
        ("ihoment__5383", None),          # empty SKU segment
        ("Govee_H61A8_AB_CD", None),      # too many segments
        # Java String.split("_") drops trailing empty strings (leading ones stay): the app splits
        # "ihoment_H6008_" into 2 parts and "Govee_H6008_ABCD_" into 3
        ("ihoment_H6008_", None),
        ("Govee_H6008_ABCD_", "H6008"),
        ("Govee_H6008_ABCD__", "H6008"),
        ("_Govee_H6008", "GOVEE"),        # the leading empty part is kept: 3 parts, middle "Govee"
        ("xGovee_H6188_1A2B", "H6188"),   # markers match anywhere (String.contains)
        ("xGBK_H1234_ABCD", None),        # ...but GBK_ is a prefix
        # GVH/GVR without "_": >= 11 chars, SKU = name[2:7] (BaseBleProcessor.f)
        ("GVH60A65357", "H60A6"),         # H60A6 advertised-name form (made-up suffix)
        ("GVH60085308", "H6008"),         # H6008 advertised-name form (made-up suffix)
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


@pytest.mark.parametrize("header", [0x00, 0x01, 0x80, 0x81, 0xC1, 0xFF])
def test_parse_broadcast_onoff_needs_a_signed_header_of_at_least_2(header: int) -> None:
    # BleUtil.parseBleBroadOnOff reads the on/off byte only when the header (scanRecord[i6 + 2], a
    # signed Java byte) is >= 2; AbsBaseBleModel.A otherwise leaves the switch state alone.
    assert parse_broadcast_onoff({_cid(header): bytes([0xEC, 0, 1, 2, 1])}) is None


@pytest.mark.parametrize("header", [0x02, 0x03, 0x41, 0x43, 0x7F])
def test_parse_broadcast_onoff_reads_headers_2_to_7f(header: int) -> None:
    assert parse_broadcast_onoff({_cid(header): bytes([0xEC, 0, 1, 2, 1])}) is True


def test_parse_broadcast_onoff_skips_a_v1_entry_then_reads_the_next() -> None:
    mfg = {_cid(0x01): bytes([0xEC, 0, 1, 2, 0]), _cid(0x02): bytes([0xEC, 0, 1, 2, 1])}
    assert parse_broadcast_onoff(mfg) is True


def test_manufacturer_ids_cover_the_encrypted_v1_v2_headers() -> None:
    # BleUtil.parseBleBroadcastPact accepts every version >= 1 with or without the 0x40 encrypt flag.
    from govee_ble_local import MANUFACTURER_IDS

    assert {0x8841, 0x8842} <= set(MANUFACTURER_IDS)
    for cid in MANUFACTURER_IDS:
        assert parse_manufacturer_data({cid: bytes([0xEC, 0, 1, 2])}) is not None


def test_identify_rejects_non_govee_and_unparseable_sku() -> None:
    assert identify(None, {}) is None
    assert identify("", {}) is None
    assert identify("Random", {}) is None
    assert identify("ihoment_H5083", {}) is None  # Govee prefix but no SKU


def test_identify_unparseable_pact_is_plaintext_unknown() -> None:
    # A Govee-named advert without parseable pact data is reported with pact_type/pact_code = -1
    # and encrypted=False; the typed view says that is unknown, not "plaintext".
    adv = identify("Govee_H61A8_53A8", {})
    assert adv is not None
    assert adv.sku == "H61A8"
    assert adv.encrypted is False
    assert adv.pact_type == -1
    assert adv.pact_code == -1
    assert adv.broadcast_version == 0
    assert adv.pact is None and adv.encryption_known is False
    short = identify("Govee_H61A8_53A8", {_cid(0x41): bytes([0xEC, 1])})
    assert short is not None and short.encrypted is False and short.pact_type == -1
    assert short.pact is None
    # A parsed pact (an encrypted advert, pact 2 / 2): test_h5083_firmware's advert test.
