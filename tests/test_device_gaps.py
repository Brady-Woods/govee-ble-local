"""Characterization tests for Device paths not covered elsewhere (audit TEST-2, device part).

Tests tagged ``# CURRENT BEHAVIOR (BUG-n / DES-n)`` pin what the code does TODAY, including
behavior that is known to be wrong or questionable; they must be updated when the bug is fixed.
Offline and deterministic: the connection is mocked with AsyncMock, as in test_device_v3.py.
"""
from __future__ import annotations

import asyncio
import copy
import dataclasses
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device, unacked_via

from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import profile_for
from govee_ble_local.exceptions import (
    GoveeBleError,
    GoveeBleInvalidValue,
    GoveeBleNotSupported,
    GoveeBleTimeout,
)
from govee_ble_local import bound_advertisement, registry, scanner
from govee_ble_local.identify import from_advertisement, identify
from govee_ble_local.models import Capability, Encryption


def _frame(*payload: int) -> bytes:
    b = bytearray(20)
    b[: len(payload)] = bytes(payload)
    x = 0
    for c in b[:19]:
        x ^= c
    b[19] = x
    return bytes(b)


def _dev(sku: str, replies: dict[int, list[bytes]] | None = None, **profile_overrides: Any) -> Device:
    """Device with a mocked connection. ``replies`` maps frame[1] (command byte) -> reply frames
    returned from query(); unmapped commands get no reply. ``profile_overrides`` build the Device
    on a changed copy of the SKU's profile (a profile is read-only after construction)."""
    if profile_overrides:
        profile = profile_for(sku)
        assert profile is not None
        d: Device | None = Device(dataclasses.replace(profile, **profile_overrides),
                                  ble_device("AA:BB:CC:DD:EE:30", sku), sku=sku)
    else:
        d = make_device(ble_device("AA:BB:CC:DD:EE:30", sku), sku)
    assert d is not None
    return _mocked(d, replies)


def _unchecked_dev(sku: str, **changes: Any) -> Device:
    """Like :func:`_dev`, on a copy of the SKU's profile changed past DeviceProfile's validation:
    for the device-level guards behind it (a profile no Device could be built from)."""
    base = profile_for(sku)
    assert base is not None
    profile = copy.copy(base)   # never mutate the shared registry profile
    for name, value in changes.items():
        object.__setattr__(profile, name, value)
    return _mocked(Device(profile, ble_device("AA:BB:CC:DD:EE:30", sku), sku=sku))


def _mocked(d: Device, replies: dict[int, list[bytes]] | None = None) -> Device:
    """Replace ``d``'s connection I/O with recorders (see :func:`_dev`)."""
    sent: list[bytes] = []
    queried: list[bytes] = []
    table = replies or {}

    async def send(frame: bytes, **_k: object) -> None:
        sent.append(frame)

    async def send_many(frames: list[bytes]) -> None:
        sent.extend(frames)

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        queried.append(frame)
        return list(table.get(frame[1], []))

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock(side_effect=unacked_via(send_many))  # type: ignore[method-assign]
    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    d._sent = sent  # type: ignore[attr-defined]
    d._queried = queried  # type: ignore[attr-defined]
    return d


def _sent(d: Device) -> list[bytes]:
    return list(d._sent)  # type: ignore[attr-defined]


def _queried(d: Device) -> list[bytes]:
    return list(d._queried)  # type: ignore[attr-defined]


def _adv(name: str | None, mfg: dict[int, bytes]) -> Any:
    return type("Adv", (), {"manufacturer_data": mfg, "local_name": name})()


def _encryption(d: Device, raw: Any) -> Encryption:
    """The mode ``d`` resolves for a raw advert, parsed once as the constructor does."""
    return d._resolve_encryption(bound_advertisement(raw, d.sku, fallback_name=d._ble_device.name))


# ── advertisement-driven encryption ─────────────────────────────────────────
def test_encryption_follows_encrypted_advertisement() -> None:
    d = _dev("H6008")  # profile default: NONE
    adv = _adv("Govee_H6008_ABCD", {0x8841: bytes([0xEC, 0x00, 0x01, 0x02])})  # 0x40 = encrypt bit
    assert _encryption(d, adv) is Encryption.AES_RC4_PSK


def test_encryption_follows_plaintext_advertisement() -> None:
    d = _dev("H60A6")  # profile default: AES
    adv = _adv("Govee_H60A6_ABCD", {0x8801: bytes([0xEC, 0x00, 0x01, 0x02])})
    assert _encryption(d, adv) is Encryption.NONE


def test_encryption_falls_back_to_profile_without_usable_advert() -> None:
    d = _dev("H60A6")
    assert d._resolve_encryption(None) is Encryption.AES_RC4_PSK
    assert _encryption(d, None) is Encryption.AES_RC4_PSK
    # a non-Govee name without a Govee pact block (another company's entry only, or none)
    foreign = {0x2E28: bytes.fromhex("89304dad0000")}
    assert _encryption(d, _adv("SomethingElse", foreign)) is Encryption.AES_RC4_PSK
    assert _encryption(d, _adv("SomethingElse", {})) is Encryption.AES_RC4_PSK
    # a Govee name without manufacturer data: pact unknown, so the profile default stands
    assert _encryption(d, _adv("Govee_H60A6_ABCD", {})) is Encryption.AES_RC4_PSK


# A bound device's advert under a name that isn't a Govee name: the H6008 whose Home Assistant record carried
# a neighbour's name and company-0x2E28 entry (2026-10-21) beside its own Govee block (header 0x43: encrypted,
# broadcast version 3, pact 1,1; switch byte 1, on, as the bulb's clean advert has it). The app's light
# control screen matches a bound device by MAC and reads that block whatever the name (identify module
# docstring); its add-device scan gates on the name.
_RENAMED = "5134P0098101970F"
_RENAMED_MFG = {0x8843: bytes.fromhex("ec0001010100"), 0x2E28: bytes.fromhex("89304dad0000")}


def test_bound_device_reads_the_encrypt_flag_whatever_its_name() -> None:
    # (advertised name, BLEDevice name): renamed in both, the advert nameless with a renamed BLEDevice (the
    # fallback name), and no name anywhere.
    for adv_name, ble_name in ((_RENAMED, _RENAMED), (None, _RENAMED), (None, None)):
        d = registry.create_device(ble_device("AA:BB:CC:DD:EE:32", ble_name), "H6008",
                                   _adv(adv_name, _RENAMED_MFG))
        assert (d.encryption, d.pact) == (Encryption.AES_RC4_PSK, (1, 1))   # was NONE (the profile's), None
    direct = Device(profile_for("H6008"), ble_device("AA:BB:CC:DD:EE:32", _RENAMED),
                    _adv(_RENAMED, _RENAMED_MFG), sku="H6008")
    assert (direct.encryption, direct.pact) == (Encryption.AES_RC4_PSK, (1, 1))
    made = make_device(ble_device("AA:BB:CC:DD:EE:32", _RENAMED), "H6008", _adv(_RENAMED, _RENAMED_MFG))
    assert made is not None and made.encryption is Encryption.AES_RC4_PSK
    # Its flag decides either way, as under a Govee name: clear on an AES-default profile, it says plaintext.
    plain = _dev("H60A6")
    assert _encryption(plain, _adv(_RENAMED, {0x8801: bytes([0xEC, 0x00, 0x01, 0x02])})) is Encryption.NONE


def test_a_govee_name_reads_the_same_on_both_paths() -> None:
    # Pact, flag and name as discovery reads them; the SKU is the caller's even when the name carries another.
    adv = _adv("GVH60062691", _RENAMED_MFG)
    found = from_advertisement(adv)
    assert found is not None and found == identify("GVH60062691", _RENAMED_MFG) and found.sku == "H6006"
    read = bound_advertisement(adv, "h6008")
    assert read is not None and read.sku == "H6008"
    assert (read.pact, read.encrypted, read.encryption_known, read.broadcast_version, read.name) == (
        found.pact, found.encrypted, found.encryption_known, found.broadcast_version, found.name)


def test_a_caller_without_a_device_reads_what_create_device_reads() -> None:
    # The public bound_advertisement is create_device's own read: renamed, nameless and Govee-named adverts
    # give the pact and encrypt flag the Device is built with. The SKU is the caller's, upper-cased; the name
    # is the advertised one, else the BLEDevice's (a Govee BLEDevice name doesn't win over a renamed advert).
    for adv_name, ble_name in ((_RENAMED, _RENAMED), (None, _RENAMED), (None, None), ("GVH60082691", None),
                               (_RENAMED, "GVH60082691")):
        raw = _adv(adv_name, _RENAMED_MFG)
        read = bound_advertisement(raw, "h6008", fallback_name=ble_name)
        d = registry.create_device(ble_device("AA:BB:CC:DD:EE:35", ble_name), "H6008", raw)
        assert read is not None and read.encryption_known
        assert (read.sku, read.pact, read.encrypted) == ("H6008", d.pact, True)
        assert d.encryption is Encryption.AES_RC4_PSK
        assert read.name == (adv_name or ble_name or "")
    # At a pact the SKU's rows refuse, it reads the pact create_device's GoveeBleNotSupported names.
    refused = bound_advertisement(_adv(_RENAMED, {0x8843: bytes([0xEC, 0x00, 0x01, 0x02])}), "H6042")
    assert refused is not None and refused.pact == (1, 2)
    # No Govee pact block: None under a non-Govee name (or no advert), an unknown pact under a Govee name.
    assert bound_advertisement(_adv(_RENAMED, {0x2E28: bytes.fromhex("89304dad0000")}), "H6008") is None
    assert bound_advertisement(None, "H6008") is None
    named = bound_advertisement(_adv("GVH60082691", {}), "H6008")
    assert named is not None and named.pact is None and not named.encryption_known


def test_discovery_keeps_the_name_gate() -> None:
    # The add-device scan drops a non-Govee name (BaseBleProcessor.parse), and no app path takes a SKU from
    # manufacturer data alone: discovery adopts nothing new.
    assert from_advertisement(_adv(_RENAMED, _RENAMED_MFG)) is None
    assert not scanner.supported(_RENAMED, _RENAMED_MFG)
    assert scanner.match(ble_device("AA:BB:CC:DD:EE:32", _RENAMED), _adv(_RENAMED, _RENAMED_MFG)) is None


def test_bound_device_at_a_pact_its_rows_refuse_is_not_supported() -> None:
    # A renamed advert's pact now routes the profile too, as the app's control screen refuses a pact the
    # goodsType doesn't accept (AbsViewMode4Op.onScanEvent, O1 -> p0(999)). H6042's rows take (1,1) and (2,1).
    adv = _adv(_RENAMED, {0x8843: bytes([0xEC, 0x00, 0x01, 0x02])})
    with pytest.raises(GoveeBleNotSupported, match="at pact 1,2"):
        registry.create_device(ble_device("AA:BB:CC:DD:EE:33", _RENAMED), "H6042", adv)
    assert make_device(ble_device("AA:BB:CC:DD:EE:33", _RENAMED), "H6042", adv) is None


def test_ingest_reads_a_renamed_advert() -> None:
    d = Device(profile_for("H6008"), ble_device("AA:BB:CC:DD:EE:34", _RENAMED), sku="H6008")
    calls: list[object] = []
    d.register_callback(calls.append)
    assert d.pact is None
    assert d.ingest_advertisement(_adv(_RENAMED, _RENAMED_MFG)) is True   # its on/off: switch byte 1
    assert (d.pact, d.state.is_on, len(calls)) == ((1, 1), True, 1)


# ── re-learning the encrypt flag (Device.ingest_advertisement, _learn_cipher) ─────
# The app overwrites its per-MAC flag from each parsed broadcast (BleBroadVersionCache.d); a Device hands what
# it hears to its connection for the next session. Header 0x43: encrypted, version 3; 0x03: plaintext.
_ENCRYPTED = {0x8843: bytes.fromhex("ec0001010100")}
_PLAINTEXT = {0x8803: bytes.fromhex("ec0001010100")}
_MIXED = {**_PLAINTEXT, **_ENCRYPTED}   # a merged record: both headers, the older first


def _bound(sku: str, adv: Any = None) -> Device:
    """A Device on a real (idle) connection, built for ``sku`` from ``adv``."""
    return Device(profile_for(sku), ble_device("AA:BB:CC:DD:EE:35", _RENAMED), adv, sku=sku)


def test_ingest_relearns_the_flag_for_the_next_session(caplog: pytest.LogCaptureFixture) -> None:
    # An H6008 built before any advert was heard has the profile's plaintext default; its own encrypted
    # advert now picks the next session's.
    d = _bound("H6008")
    calls: list[object] = []
    d.register_callback(calls.append)
    assert d.encryption is Encryption.NONE
    with caplog.at_level(logging.INFO, logger="govee_ble_local"):
        # True for its on/off; the flag adds no callback
        assert d.ingest_advertisement(_adv(_RENAMED, _ENCRYPTED)) is True
    assert d.encryption is Encryption.AES_RC4_PSK and len(calls) == 1
    assert "advertises the aes_rc4_psk command channel; the next session uses aes_rc4_psk\n" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="govee_ble_local"):
        d.ingest_advertisement(_adv(_RENAMED, _ENCRYPTED))   # heard again: nothing new
        d.ingest_advertisement(_adv(_RENAMED, _PLAINTEXT))   # and back (BleBroadVersionCache.d:135)
    assert d.encryption is Encryption.NONE and caplog.text.count("command channel") == 1


def test_ingest_names_a_bgc_verdict_over_the_flag(caplog: pytest.LogCaptureFixture) -> None:
    d = _bound("H6008", _adv(_RENAMED, _ENCRYPTED))
    d._conn._bgc_version, d._conn._bgc_checked = 1, True   # as an earlier session's BGC read left it
    with caplog.at_level(logging.INFO, logger="govee_ble_local"):
        d.ingest_advertisement(_adv(_RENAMED, _PLAINTEXT))
    # flag OR BGC (EncryptionUtils.isEncryptionSupported): a plaintext flag never downgrades a BGC-1 device
    assert d.encryption is Encryption.AES_RC4_PSK
    assert ("advertises the none command channel; the next session uses aes_rc4_psk (its BGC version "
            "overrides the advertised flag)") in caplog.text


def test_ingest_keeps_the_cipher_on_a_mixed_record(caplog: pytest.LogCaptureFixture) -> None:
    d = _bound("H6008", _adv(_RENAMED, _ENCRYPTED))
    with caplog.at_level(logging.INFO, logger="govee_ble_local"):
        d.ingest_advertisement(_adv(_RENAMED, _MIXED))
        d.ingest_advertisement(_adv(_RENAMED, _MIXED))   # still mixed: warned once
    assert d.encryption is Encryption.AES_RC4_PSK
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "headers 0x03, 0x43" in warnings[0].getMessage()
    assert ("the advertised mode stays aes_rc4_psk (a BGC version of 1 or 2 overrides it)"
            in warnings[0].getMessage())
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="govee_ble_local"):
        d.ingest_advertisement(_adv(_RENAMED, _ENCRYPTED))   # agrees again (a cleared or rebuilt record)
        d.ingest_advertisement(_adv(_RENAMED, _MIXED))       # a new spell: warned again
    assert len(caplog.records) == 1 and d.encryption is Encryption.AES_RC4_PSK


def test_ingest_without_a_flag_keeps_the_cipher() -> None:
    d = _bound("H60A6")   # profile default: AES
    version_0 = {0x8800: bytes.fromhex("ec0001010100")}   # header 0x00: no pact broadcast (version 0)
    for mfg in ({0x2E28: bytes.fromhex("89304dad0000")}, version_0, {0x8801: bytes([0xEC, 0x00])}):
        d.ingest_advertisement(_adv(_RENAMED, mfg))
        assert d.encryption is Encryption.AES_RC4_PSK
    d.ingest_advertisement(_adv("Govee_H60A6_ABCD", {}))   # a Govee name alone says nothing either
    assert d.encryption is Encryption.AES_RC4_PSK


def test_encryption_constructor_uses_advertisement() -> None:
    # A raw bleak-style advert (what Home Assistant passes to create_device) decides the mode the
    # connection is built with; the same H6008 profile without an advert keeps its default.
    adv = _adv("Govee_H6008_ABCD", {0x8841: bytes([0xEC, 0x00, 0x01, 0x02])})
    profile = _dev("H6008").profile
    d = Device(profile, ble_device("AA:BB:CC:DD:EE:31", "x"), adv)
    assert d.encryption is Encryption.AES_RC4_PSK            # the connection's mode
    plain = Device(profile, ble_device("AA:BB:CC:DD:EE:31", "x"), None)
    assert plain.encryption is Encryption.NONE


def test_encryption_unparseable_pact_keeps_profile_encryption_for_h60a6() -> None:
    # WAS (BUG-2): pact -1 (unparseable) made identify() report encrypted=False, resolving an
    # AES-profile H60A6 to NONE. FIXED (T6.1): pact -1 is "unknown", so the profile default wins.
    d = _dev("H60A6")
    adv = _adv("Govee_H60A6_ABCD", {0x1234: b"\x00\x00\x00\x00"})
    assert _encryption(d, adv) is Encryption.AES_RC4_PSK
    assert d.profile.encryption is Encryption.AES_RC4_PSK


# ── power frames for a zoned fixture without a master switch ────────────────
def test_zoned_fixture_without_global_power_sends_one_frame_per_zone() -> None:
    d = _dev("H60A6", global_power=False)
    asyncio.run(d.set_power(True))
    assert [f[:4].hex() for f in _sent(d)] == ["33300001", "33300101"]
    assert d.state.zone_power == {0: True, 1: True}


# ── colour temperature / zones / gradual ────────────────────────────────────
def test_set_color_temp_updates_state_and_clears_rgb() -> None:
    d = _dev("H6008")
    asyncio.run(d.set_rgb((1, 2, 3)))
    asyncio.run(d.set_color_temp(4000))
    assert _sent(d)[-1][:3].hex() == "33050d"
    assert d.state.color_temp_kelvin == 4000 and d.state.rgb_color is None
    assert d.state.scene_code is None


def test_segment_color_temp_unsupported_scheme_is_not_supported() -> None:
    # A custom H6008 profile given SEGMENTS is accepted, as in 1.3.0, and fails at the setter: no
    # 0x0d (h6006) colour frame carries a segment mask. Only the table's build refuses the pairing
    # (devices/_resolve.py SCHEME_LACKS).
    profile = profile_for("H6008")
    assert profile is not None
    segmented = dataclasses.replace(profile, capabilities=profile.capabilities | {Capability.SEGMENTS})
    d = _dev("H6008", capabilities=segmented.capabilities)
    with pytest.raises(GoveeBleNotSupported, match=r"^H6008: "):
        asyncio.run(d.set_segment_color_temp([0], 4000))  # h6006 CCT has no segment mask
    assert _sent(d) == []
    # H61D2-H61D6 (h61d3 202): segment colour, but the colour page hides the segment-brightness slider
    # (Config.I[202][3] false), so it doesn't go out.
    h61d2 = _dev("H61D2")
    assert Capability.SEGMENTS in h61d2.capabilities and not h61d2.supports_segment_brightness
    with pytest.raises(GoveeBleNotSupported, match=r"^H61D2: no segment brightness"):
        asyncio.run(h61d2.set_segment_brightness([0], 50))
    assert _sent(h61d2) == [] and _dev("H60A6").supports_segment_brightness


def test_set_zone_rgb_sends_zone_mask_and_updates_segments() -> None:
    d = _dev("H60A6")
    asyncio.run(d.set_zone_rgb("main", (10, 20, 30)))
    f = _sent(d)[0]
    assert f[:3].hex() == "330515" and f[12:14] == bytes([0x00, 0x10])  # mask 0x1000 (LE lo, hi)
    assert {s.index: s.rgb for s in d.state.segments} == {12: (10, 20, 30)}


def test_set_zone_rgb_zone_without_segments_raises() -> None:
    zones = _dev("H60A6").profile.zones
    d = _dev("H60A6", zones=(dataclasses.replace(zones[0], segments=()), zones[1]))
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(d.set_zone_rgb("main", (1, 2, 3)))
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(d.set_zone_color_temp("main", 4000))
    assert _sent(d) == []


@pytest.mark.parametrize(("sku", "zone", "mask"), [
    ("H60A6", "background", b"\xff\x0f"), ("H60A6", "main", b"\x00\x10"),
    ("H6047", "left", b"\x1f\x00"), ("H6047", "right", b"\xe0\x03"),
])
def test_set_zone_color_temp_sends_zone_mask(sku: str, zone: str, mask: bytes) -> None:
    # The zone's own mask, the one the app's Part-tab Kelvin widens a pick to, though segment_color_temp is
    # false (decision 24: that is the piece selection's Kelvin only).
    d = _dev(sku)
    assert not d.profile.segment_color_temp
    asyncio.run(d.set_zone_color_temp(zone, 4000))
    f = _sent(d)[0]
    assert f[:3].hex() == "330515"
    assert f[7:9] == (4000).to_bytes(2, "big") and f[12:14] == mask


def test_set_zone_color_temp_unsupported_scheme_and_unknown_zone() -> None:
    d = _dev("H60A6", color_scheme="h6006")   # a segmented profile on a mask-less scheme (as 1.3.0)
    with pytest.raises(GoveeBleNotSupported, match=r"^H60A6: "):
        asyncio.run(d.set_zone_color_temp("main", 4000))
    # An unknown zone is bad caller input; it is still a GoveeBleNotSupported, as it was.
    with pytest.raises(GoveeBleInvalidValue) as err:
        asyncio.run(d.set_zone_color_temp("nope", 4000))
    assert isinstance(err.value, GoveeBleNotSupported)
    with pytest.raises(GoveeBleInvalidValue) as err:
        asyncio.run(d.set_zone_power("nope", True))
    assert isinstance(err.value, GoveeBleNotSupported)
    assert _sent(d) == []


def test_set_gradual_on_supported_and_unsupported() -> None:
    d = _dev("H61A8")
    asyncio.run(d.set_gradual(True))
    assert d.state.gradual is True and len(_sent(d)) == 1
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(_dev("H6008").set_gradual(True))


# ── update() read-back paths ────────────────────────────────────────────────
def test_update_plug_reads_relay_then_device_info() -> None:
    d = _dev("H5080", {0x01: [_frame(0xAA, 0x01, 0x01)], 0x06: [_frame(0xAA, 0x06, *b"1.00.30")]})
    st = asyncio.run(d.update())
    assert st is d.state and st.is_on is True and st.optimistic is False
    assert d._polled_once is True and d._device_info_read is True


def test_update_reads_gradual_flag() -> None:
    d = _dev("H61A8", {0xA3: [_frame(0xAA, 0xA3, 0x01)]})
    asyncio.run(d.update())
    assert d.state.gradual is True
    d2 = _dev("H61A8", {0xA3: [_frame(0xAA, 0xA3, 0x00)]})
    asyncio.run(d2.update())
    assert d2.state.gradual is False
    d3 = _dev("H6008", {0xA3: [_frame(0xAA, 0xA3, 0x01)]}, gradual_read_only=True)   # read, not exposed
    asyncio.run(d3.update())
    assert d3.state.gradual is None and any(f[1] == 0xA3 for f in _queried(d3))


def test_update_gradual_no_reply_leaves_state() -> None:
    d = _dev("H61A8")
    asyncio.run(d.update())
    assert d.state.gradual is None


def test_live_ic_read_failure_skips_segment_readback() -> None:
    d = _dev("H6641")  # color_readback_live_ic; no reply to the 0x40 IC-count query
    asyncio.run(d._read_mechanism_a_direct())
    assert d.state.segments == []
    assert all(f[1] == 0x40 for f in _queried(d))  # no per-group reads attempted
    d2 = _dev("H6047", ic_num_read_only=True)   # an aa 40 read that sizes nothing: the static walk still runs
    asyncio.run(d2.update())
    asked = [f[1] for f in _queried(d2)]
    assert 0x40 in asked and 0xA5 in asked and asked.index(0x40) < asked.index(0xA5)


def test_device_info_serial_falls_back_to_sn_selector() -> None:
    sn = _frame(0xAA, 0x07, 0x02, 0x5C, 0xE7, 0x53, 0xF4, 0x74, 0x56, 0, 0)
    d = _dev("H60A6")
    # Only the SN (0x02) selector answers; basic (0x10) / wifi (0x11) carry nothing.

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        return [sn] if frame[2] == 0x02 else []

    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    asyncio.run(d._read_device_info())
    assert d.state.serial_number == "56:74:F4:53:E7:5C"


def test_update_status_logs_debug_state_after_first_poll(caplog: pytest.LogCaptureFixture) -> None:
    d = _dev("H6008")
    d._polled_once = True
    with caplog.at_level(logging.DEBUG, logger="govee_ble_local.devices.device"):
        asyncio.run(d.update())
    assert any("state:" in r.message for r in caplog.records)


# ── characterization of known-buggy / questionable behavior ─────────────────
def test_bar_switch_zone_power_with_failed_read_refuses_to_guess_other_bar() -> None:
    # WAS (BUG-3): with no reply to the 0x36 read the unknown other bar was sent OFF.
    # FIXED (T6.1): raises GoveeBleError before sending anything.
    d = _dev("H6047")  # no reply to the 0x36 query
    with pytest.raises(GoveeBleError, match="unknown"):
        asyncio.run(d.set_zone_power("left", True))
    assert _queried(d)[0][1] == 0x36
    assert _sent(d) == [] and d.state.zone_power == {}


# DES-2 (a raising callback is isolated and logged) is covered in test_device_fixes.py.


def test_invalid_inputs_raise_typed_errors_and_send_nothing() -> None:
    # WAS (DES-1): out-of-range RGB escaped as a raw ValueError. FIXED (T5.2/T6.1): it is a
    # GoveeBleInvalidValue (still a ValueError); nothing is sent and state is untouched.
    d = _dev("H6008")
    with pytest.raises(GoveeBleInvalidValue) as ei:
        asyncio.run(d.set_rgb((300, 0, 0)))
    assert not isinstance(ei.value, GoveeBleNotSupported)
    assert _sent(d) == [] and d.state.rgb_color is None

    # A request the colour scheme can't express is GoveeBleNotSupported, prefixed with the SKU:
    # test_fix_wp3_device_design.test_a_scheme_without_segment_brightness_is_not_supported_with_the_sku.

    # kelvin beyond u16 once wrapped silently (70000 -> 0x1170 on the wire, unwrapped value in
    # state). Like the app (KelvinConfig.calibrationKelvin) it is now clamped to the profile
    # range, so what is sent and stored is the in-range value; a non-int is still rejected.
    t = _dev("H6008")
    asyncio.run(t.set_color_temp(70000))
    assert _sent(t)[0][6:8].hex() == "1964" and t.state.color_temp_kelvin == 6500
    with pytest.raises(GoveeBleInvalidValue):
        asyncio.run(t.set_color_temp(70000.0))  # type: ignore[arg-type]


# BUG-5 (a failed device-info read is retried next poll) is covered in test_device_fixes.py and
# test_device_final.py.


def test_update_propagates_read_failure_and_skips_notify() -> None:
    # CURRENT BEHAVIOR: a raising read aborts update(); state keeps its optimistic flag, no
    # callback fires and the first-poll log flag is not set. (The transport raises GoveeBleTimeout
    # for a timed-out read, never a bare TimeoutError.)
    d = _dev("H6008")
    d._conn.query = AsyncMock(side_effect=GoveeBleTimeout("boom"))  # type: ignore[method-assign]
    seen: list[object] = []
    d.register_callback(seen.append)
    with pytest.raises(GoveeBleTimeout):
        asyncio.run(d.update())
    assert d.state.optimistic is True and seen == [] and d._polled_once is False


def test_update_unknown_readback_value_is_rejected_at_profile_construction() -> None:
    # WAS (DES-7): an unrecognised ``readback`` value was neither an error nor a warning;
    # update() skipped the state read, then still read device info and notified.
    # FIXED (T5.1): the profile now validates ``readback`` on construction, so a bogus value
    # raises ValueError before any Device exists.
    with pytest.raises(ValueError):
        _dev("H6008", readback="bogus")
