"""Device-info reads match the Govee app: aa 06 firmware, aa 07 03 hardware, aa 07 02 device id.

The reply frames below were captured live (2026-10-15); their device ids are made-up examples
(checksums recomputed). The app-side sources are SoftVersionController / HardVersionController /
SnController.parseValidBytes.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest
from conftest import require_optional
from fake_ble import FakeGovee, ble_device, frame
from conftest import gated_scene_profile
from test_device_v3 import _STATUS_BURST

require_optional("kaitaistruct", reason="Kaitai runtime not installed")

from govee_ble_local import GoveeAdvertisement, create_device  # noqa: E402
from govee_ble_local.const import SERVICE_UUID  # noqa: E402
from govee_ble_local.devices.device import Device, make_device  # noqa: E402
from govee_ble_local.models import Encryption  # noqa: E402
from govee_ble_local.transport import connection as conn_mod  # noqa: E402
from govee_ble_local.wire import build, parse  # noqa: E402

# sku -> (aa 06 reply, aa 07 03 reply, aa 07 02 reply, firmware, hardware, device id). The H5083 reads
# run against its firmware image instead (test_h5083_firmware.test_device_info_is_read_once_per_device).
CAPTURES = {
    "H6047": ("aa06312e30302e3132000000000000000000009e", "aa0703332e30322e30310000000000000000009e",
              "aa07024753005e0002470a0000000000000000aa", "1.00.12", "3.02.01", "0A:47:02:00:5E:00:53:47"),
    "H61A8": ("aa06332e30332e31380000000000000000000095", "aa0703332e30312e31300000000000000000009d",
              "aa0702a853005e0002a80a0000000000000000aa", "3.03.18", "3.01.10", "0A:A8:02:00:5E:00:53:A8"),
    "H6052": ("aa06322e30302e31390000000000000000000096", "aa0703332e30322e30320000000000000000009d",
              "aa07025253005e0002520a0000000000000000aa", "2.00.19", "3.02.02", "0A:52:02:00:5E:00:53:52"),
    "H6006": ("aa06312e30302e35390000000000000000000091", "aa0703312e30322e30300000000000000000009d",
              "aa07020653005e0002060a0000000000000000aa", "1.00.59", "1.02.00", "0A:06:02:00:5E:00:53:06"),
    "H60A6": ("aa06312e30302e34310000000000000000000098", "aa0703312e30342e303300000000000000000098",
              "aa07025653005e0002a60a00000000000000005a", "1.00.41", "1.04.03", "0A:A6:02:00:5E:00:53:56"),
}
# H60A6 answers aa 07 10 / 11 with all zeros (no UID, versions or Wi-Fi MAC).
H60A6_BASIC_ZEROS = bytes.fromhex("aa071000000000000000000000000000000000bd")
H60A6_WIFI_ZEROS = bytes.fromhex("aa071100000000000000000000000000000000bc")


def _dev(sku: str, replies: Callable[[bytes], list[bytes]]) -> tuple[Device, list[bytes]]:
    d = make_device(ble_device("AA:BB:CC:DD:EE:30", sku), sku)
    assert d is not None
    sent: list[bytes] = []

    async def query(fr: bytes, **_k: object) -> list[bytes]:
        sent.append(fr)
        return replies(fr)

    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    return d, sent


def test_builders_match_the_app_frames() -> None:
    assert build.soft_version_query()[:3] == bytes([0xAA, 0x06, 0x00])
    assert build.device_info_query(build.INFO_HARD)[:3] == bytes([0xAA, 0x07, 0x03])
    assert build.device_info_query(build.INFO_SN)[:3] == bytes([0xAA, 0x07, 0x02])


@pytest.mark.parametrize("sku", sorted(CAPTURES))
def test_parse_captured_replies(sku: str) -> None:
    soft, hard, sn, firmware, hardware, device_id = (bytes.fromhex(x) if x.startswith("aa") else x
                                                     for x in CAPTURES[sku])
    assert parse.parse_soft_version(soft) == firmware  # type: ignore[arg-type]
    info = parse.parse_device_info(hard)  # type: ignore[arg-type]
    assert info is not None and info.hw_version == hardware
    sn_info = parse.parse_device_info(sn)  # type: ignore[arg-type]
    assert sn_info is not None and sn_info.serial == device_id


def test_parse_version_edge_cases() -> None:
    assert parse.parse_soft_version(frame(0xAA, 0x06)) is None                     # empty
    assert parse.parse_soft_version(frame(0xAA, 0x06, 0x01)) is None               # control byte only
    assert parse.parse_soft_version(frame(0xAA, 0x06, *b" 1.2.3 ")) == "1.2.3"     # trimmed
    assert parse.parse_soft_version(frame(0xAA, 0x07, 0x03)) is None               # wrong command
    hard = parse.parse_device_info(frame(0xAA, 0x07, 0x03))
    assert hard is not None and hard.hw_version is None


def test_device_id_zero_uid_is_invalid_and_one_zero_header_is_dropped() -> None:
    # NumberUtil.isInvalidUuid: an all-zero UID is no id (the old parser rendered it as "00").
    zero = parse.parse_device_info(frame(0xAA, 0x07, 0x02))
    assert zero is not None and zero.serial is None
    zero_basic = parse.parse_device_info(H60A6_BASIC_ZEROS)
    assert zero_basic is not None and zero_basic.serial is None
    # SnController strips ONE leading "00:00:" (replaceFirst), not every zero pair.
    one = parse.parse_device_info(frame(0xAA, 0x07, 0x02, 1, 2, 3, 4, 5, 6, 0, 0))
    assert one is not None and one.serial == "06:05:04:03:02:01"
    three = parse.parse_device_info(frame(0xAA, 0x07, 0x02, 1, 2, 3, 4, 5, 0, 0, 0))
    assert three is not None and three.serial == "00:05:04:03:02:01"


@pytest.mark.parametrize("sku", sorted(set(CAPTURES) - {"H60A6"}))
def test_device_info_read_sends_only_the_firmware_backed_reads(sku: str) -> None:
    soft, hard, sn, firmware, hardware, device_id = CAPTURES[sku]
    by_request = {(0x06, 0): soft, (0x07, 0x03): hard, (0x07, 0x02): sn}

    def replies(fr: bytes) -> list[bytes]:
        raw = by_request.get((fr[1], fr[2] if fr[1] == 0x07 else 0))
        return [bytes.fromhex(raw)] if raw else []

    d, sent = _dev(sku, replies)
    asyncio.run(d._read_device_info())
    st = d.state
    assert (st.firmware_version, st.hardware_version) == (firmware, hardware)
    assert st.device_id == device_id == st.serial_number
    # No aa 07 10 / 11: no firmware image handles them and the app never sends them.
    assert [fr[1:3].hex() for fr in sent] == ["0600", "0703", "0702"]
    assert d._device_info_read is True


def _info_reads(sent: list[bytes]) -> list[str]:
    """The device-info reads among ``sent`` (aa 06, aa 07 <selector>), as their request bytes."""
    return [fr[1:3].hex() for fr in sent if fr[:2] in (b"\xaa\x06", b"\xaa\x07")]


@pytest.mark.parametrize("lost", ["0600", "0703"])
def test_a_version_a_firmware_gate_tests_is_read_again_until_it_is_in(lost: str) -> None:
    """Review G1 item 2: one lost aa 06 or aa 07 03 reply no longer fixes a firmware-gated fact for the life
    of the Device. The device-info read is done once the device answered, as before, but each later poll reads
    the version a gate tests that it did not bring back, alone, until it is in. H6168 with its scene gate
    (soft 1.06.00 and hard 1.00.02: conftest.gated_scene_profile) on soft 1.05.00 / hard 3.01.10: while the
    soft version is missing its gate counts as met ({0, 1, 2, 3}, the newest firmware's), while the hard one
    is the read soft version already fails it ({0, 1, 2}, which no hard version changes); then its own
    {0, 1, 2} either way."""
    soft, hard, sn, *_ = CAPTURES["H61A8"]
    replies = {"0600": bytes.fromhex(soft).replace(b"3.03.18", b"1.05.00"), "0703": bytes.fromhex(hard),
               "0702": bytes.fromhex(sn)}
    answers = {**replies, lost: b""}   # this poll loses it
    d, sent = _dev("H6168", lambda fr: [answers[fr[1:3].hex()]] if answers.get(fr[1:3].hex()) else [])
    d.profile = gated_scene_profile()
    asyncio.run(d.update())
    assert _info_reads(sent) == ["0600", "0703", "0702"] and d._device_info_read
    assert d.scene_versions == ({0, 1, 2, 3} if lost == "0600" else {0, 1, 2})
    sent.clear()
    asyncio.run(d.update())   # lost again: only it is sent
    assert _info_reads(sent) == [lost]
    answers[lost] = replies[lost]
    sent.clear()
    asyncio.run(d.update())
    assert _info_reads(sent) == [lost] and d.scene_versions == {0, 1, 2}
    sent.clear()
    asyncio.run(d.update())   # both in: no device-info read any more
    assert _info_reads(sent) == []


def test_only_the_version_a_gate_tests_is_read_again() -> None:
    """The H61A8's one firmware-gated fact is its Kelvin range (the hard version, KelvinConfig.Config.z2): a
    lost aa 07 03 is read again on the next poll, a lost aa 06 is not (no gate tests it), and a SKU with no
    firmware-gated fact (H6006) reads neither again, as before."""
    soft, hard, sn, *_ = CAPTURES["H61A8"]
    for sku, lost, again in (("H61A8", "0703", ["0703"]), ("H61A8", "0600", []), ("H6006", "0703", [])):
        answers = {"0600": bytes.fromhex(soft), "0703": bytes.fromhex(hard), "0702": bytes.fromhex(sn)}
        del answers[lost]
        d, sent = _dev(sku, lambda fr, a=answers: [a[fr[1:3].hex()]] if fr[1:3].hex() in a else [])
        asyncio.run(d.update())
        assert _info_reads(sent) == ["0600", "0703", "0702"], sku
        sent.clear()
        asyncio.run(d.update())
        assert _info_reads(sent) == again, (sku, lost)


@pytest.mark.parametrize(("sku", "sent"), [("H60A6", ["0600", "0703", "0702"]), ("H3751", [])])
def test_a_status_profile_without_device_info_reads_takes_them_from_tlv_07(sku: str, sent: list[str]) -> None:
    """A status row whose screen sends no device-info read (devices.yaml ``device_info_reads: none``, H3751's
    h3751 354) gets none from the library on any poll: its versions and device id come from the status
    reply's TLV 0x07 alone (Compose4BaseInfoSingleRead.w), as on the screen. A status row that makes them
    (H60A6's h60ax 303, ``firmware``) sends them once. Both read the captured H60A6 burst, which carries TLV
    0x07."""
    d, frames = _dev(sku, lambda fr: list(_STATUS_BURST) if fr[0] == 0xAC else [])
    assert d.profile.device_info_reads == bool(sent)
    asyncio.run(d.update())
    asyncio.run(d.update())
    assert _info_reads(frames) == sent
    st = d.state
    assert (st.firmware_version, st.hardware_version, st.serial_number) == (
        "1.00.41", "1.04.03", "0A:A6:02:00:5E:00:53:56")


def test_h60a6_device_id_comes_from_the_sn_read_despite_zeroed_basic() -> None:
    soft, hard, sn, firmware, hardware, device_id = CAPTURES["H60A6"]
    by_request = {(0x06, 0): bytes.fromhex(soft), (0x07, 0x03): bytes.fromhex(hard),
                  (0x07, 0x02): bytes.fromhex(sn), (0x07, 0x10): H60A6_BASIC_ZEROS,
                  (0x07, 0x11): H60A6_WIFI_ZEROS}

    def replies(fr: bytes) -> list[bytes]:
        raw = by_request.get((fr[1], fr[2] if fr[1] == 0x07 else 0))
        return [raw] if raw else []

    d, _ = _dev("H60A6", replies)
    d._state.serial_number = None
    asyncio.run(d._read_device_info())
    assert d.state.device_id == device_id
    assert (d.state.firmware_version, d.state.hardware_version) == (firmware, hardware)
    assert d.state.wifi_mac is None


def test_no_basic_or_wifi_read_even_when_the_app_reads_are_unanswered() -> None:
    # WAS: aa 07 10 was a fallback for unknown versions. It has no firmware handler (H60A6 echoes the
    # request as zeros), so it is never sent; a device that answers only it gives nothing.
    basic = frame(0xAA, 0x07, 0x10, 0x02, 0x00, 0x5E, 0x00, 0x53, 0x56, 0, 0, 1, 2, 3, 1, 0, 5)

    d, sent = _dev("H60A6", lambda fr: [basic] if fr[1:3] == b"\x07\x10" else [])
    asyncio.run(d._read_device_info())
    assert [fr[1:3].hex() for fr in sent] == ["0600", "0703", "0702"]
    assert (d.state.firmware_version, d.state.hardware_version, d.state.device_id) == (None, None, None)


def test_ascii_reads_override_the_status_stream_versions() -> None:
    soft, hard, *_ = CAPTURES["H60A6"]
    d, _ = _dev("H60A6", lambda fr: [bytes.fromhex(soft)] if fr[1] == 0x06 else
                [bytes.fromhex(hard)] if fr[1:3] == b"\x07\x03" else [])
    d._state.firmware_version, d._state.hardware_version = "0.0.1", "0.0.2"
    asyncio.run(d._read_device_info())
    assert (d.state.firmware_version, d.state.hardware_version) == ("1.00.41", "1.04.03")


async def test_connect_resolves_only_the_govee_service(monkeypatch: pytest.MonkeyPatch) -> None:
    # macOS CoreBluetooth rejects descriptor discovery on a characteristic outside the Govee
    # service (H60A6, H6008), failing the whole connect unless the service filter is passed.
    fake = FakeGovee()
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    dev = create_device(ble_device("AA:BB:CC:DD:EE:31", "GVH60A6"), "H60A6")  # type: ignore[arg-type]
    dev._conn._idle_disconnect = 0
    await dev.connect()
    assert fake.connect_kwargs["services"] == [SERVICE_UUID]
    await dev.stop()


@pytest.mark.parametrize(("sku", "encrypted", "pact_type", "expected"), [
    ("H6008", True, 1, Encryption.AES_RC4_PSK),   # real firmware encrypts although the profile says NONE
    ("H60A6", False, 1, Encryption.NONE),         # the advertisement overrides the profile either way
    ("H6008", True, -1, Encryption.NONE),         # unparseable pact info: unknown -> the profile default
])
def test_parsed_advertisement_selects_the_encryption_mode(
    sku: str, encrypted: bool, pact_type: int, expected: Encryption,
) -> None:
    # discover() hands back a GoveeAdvertisement; create_device must honour its encrypt flag.
    adv = GoveeAdvertisement(sku=sku, name=f"GV{sku}2691", encrypted=encrypted,
                             broadcast_version=3, pact_type=pact_type, pact_code=1)
    dev = create_device(ble_device("AA:BB:CC:DD:EE:32", adv.name), sku, adv)
    assert dev.encryption is expected


def test_no_advertisement_uses_the_profile_default() -> None:
    assert create_device(ble_device("AA:BB:CC:DD:EE:33", "GVH6008"), "H6008").encryption is Encryption.NONE


def test_parsed_advertisement_also_records_the_pact() -> None:
    adv = GoveeAdvertisement(sku="H6008", name="GVH60082691", encrypted=True,
                             broadcast_version=3, pact_type=1, pact_code=1)
    assert create_device(ble_device("AA:BB:CC:DD:EE:34", adv.name), "H6008", adv).pact == (1, 1)
