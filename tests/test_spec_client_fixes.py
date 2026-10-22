"""Regression tests for the library bugs the spec-client audit found (SPEC_CLIENT_AUDIT_ba9e6bd.md,
section 2, items #5, #6, #14, #15, #17, #19). None of them changes a frame a curated SKU sends for
valid input; each test names the item and the app path it follows."""
from __future__ import annotations

import asyncio
import dataclasses
from typing import Any
from unittest.mock import AsyncMock

import pytest

from fake_ble import FakeGovee, ble_device, frame, unacked_via
from govee_ble_local.crypto import checksum
from govee_ble_local.devices.brightness import percent_to_device, percent_to_raw
from govee_ble_local.devices.device import Device
from govee_ble_local.devices.profile import profile_for
from govee_ble_local.exceptions import GoveeBleInvalidValue
from govee_ble_local.models import Encryption
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.transport.connection import GoveeConnection
from govee_ble_local.wire import build, parse, reassemble
from govee_ble_local.wire.build import color_temp
from govee_ble_local.wire.describe import describe_frame, status_burst_report


def _dev(sku: str, **profile_overrides: Any) -> Device:
    profile = profile_for(sku)
    assert profile is not None
    if profile_overrides:
        profile = dataclasses.replace(profile, **profile_overrides)
    d = Device(profile, ble_device("AA:BB:CC:DD:EE:5C", sku), sku=sku)
    sent: list[bytes] = []

    async def send(fr: bytes, **_k: object) -> None:
        sent.append(fr)

    async def send_many(frames: list[bytes]) -> None:
        sent.extend(frames)

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock(side_effect=unacked_via(send_many))  # type: ignore[method-assign]
    d._conn.query = AsyncMock(return_value=[])  # type: ignore[method-assign]
    d._sent = sent  # type: ignore[attr-defined]
    return d


def _sent(d: Device) -> list[bytes]:
    return list(d._sent)  # type: ignore[attr-defined]


def _chunk(body: bytes) -> bytes:
    buf = bytearray(20)
    buf[: len(body)] = body
    buf[19] = checksum(bytes(buf[:19]))
    return bytes(buf)


# ── #5: segment indices stop at the segment count (BleUtils.Y / X) ───────────────────────────
@pytest.mark.parametrize(("sku", "count"), [("H60A6", 13), ("H6047", 10), ("H61A8", 15)])
def test_segment_index_past_the_segment_count_sends_nothing(sku: str, count: int) -> None:
    d = _dev(sku)
    for bad in (count, 15):
        with pytest.raises(GoveeBleInvalidValue, match=rf"0\.\.{count - 1}"):
            asyncio.run(d.set_segment_rgb([bad], (1, 2, 3)))
    assert _sent(d) == []
    asyncio.run(d.set_segment_rgb([count - 1], (1, 2, 3)))   # the last real segment still works
    assert len(_sent(d)) == 1


def test_h60a6_mask_never_sets_bits_13_to_15() -> None:
    d = _dev("H60A6")
    with pytest.raises(GoveeBleInvalidValue):
        asyncio.run(d.set_segment_brightness([13, 14, 15], 50))   # WAS: sent mask e0 00
    assert _sent(d) == []


# ── #6: a [1, 100] brightness range is written as the percent (BrightnessConfig.Companion.c) ──
def test_percent_range_is_the_identity() -> None:
    assert [percent_to_device(p, 1, 100) for p in (1, 50, 51, 99, 100)] == [1, 50, 51, 99, 100]
    assert percent_to_device(0, 1, 100) == 1 and percent_to_device(250, 1, 100) == 100
    # Any other range is NumberUtil.calculateProgress, as before.
    for p in range(1, 101):
        assert percent_to_device(p, 20, 254) == percent_to_raw(p, 20, 254)
    # WAS: calculateProgress over [1, 100] wrote one more for 51..100.
    assert percent_to_raw(100, 1, 100) == 101 and percent_to_raw(51, 1, 100) == 52


def test_set_brightness_on_a_percent_range_profile_sends_the_percent() -> None:
    d = _dev("H6008", brightness_range=(1, 100))
    asyncio.run(d.set_brightness(75))
    assert [f[:3] for f in _sent(d) if f[1] == 0x04] == [bytes([0x33, 0x04, 75])]


# ── #14: the H6062 aa 41 IC count is signed (BleUtil.getSignedShort) ──────────────────────────
def test_section_count_ic_is_signed() -> None:
    assert parse.parse_section_count(frame(0xAA, 0x41, 0x01, 0x2C, 33)) == parse.SectionCount(300, 33)
    assert parse.parse_section_count(frame(0xAA, 0x41, 0xFF, 0xFE, 33)) == parse.SectionCount(-2, 33)


# ── #15: the last 0xAC packet is the first of 0xFF / pkts-1 to arrive after the others ────────
def _burst(n_chunks: int, last_tag: int) -> list[bytes]:
    first = _chunk(bytes([0xAC, 0x00, n_chunks, 0x00, 4, 0x03, 0x00]) + bytes([0x01, 0x01, 0x01]) + bytes(9))
    middle = [_chunk(bytes([0xAC, i]) + bytes(17)) for i in range(1, n_chunks - 1)]
    return [first, *middle, _chunk(bytes([0xAC, last_tag, 0x04, 0x01, 0x50, 0x00]))]


def test_last_packet_tagged_with_its_position_wins_when_it_arrives_first() -> None:
    burst = _burst(3, 0x02)
    stray_ff = _chunk(bytes([0xAC, 0xFF, 0x04, 0x01, 0x10, 0x00]))
    chunks, complete, _ = reassemble.burst_chunks([*burst, stray_ff])
    assert complete and chunks[-1] == burst[-1]   # WAS: the later 0xFF chunk was always taken
    # A 0xFF that came before the prefix was in is refused by the app (END_PKG_ORDER): the pkts-1
    # chunk that arrives after it is the last packet.
    chunks, complete, _ = reassemble.burst_chunks([burst[0], stray_ff, *burst[1:]])
    assert complete and chunks[-1] == burst[-1]
    # Only a 0xFF: taken as before.
    ff_burst = _burst(3, 0xFF)
    assert reassemble.burst_chunks(ff_burst)[0][-1] == ff_burst[-1]


# ── #17: query() ends a 0xAC burst once it is complete, not only on tag 0xFF ──────────────────
@pytest.mark.parametrize("burst", [
    [_chunk(bytes([0xAC, 0x00, 0x01, 0x00, 3, 0x03, 0x00, 0x01, 0x01, 0x01]))],   # one-packet reply
    _burst(3, 0x02),                                                              # last tagged pkts-1
])
async def test_query_returns_when_the_burst_is_complete(monkeypatch: pytest.MonkeyPatch,
                                                        burst: list[bytes]) -> None:
    fake = FakeGovee(encrypted=False)
    trailing = frame(0xEE, 0x20, 0x50)   # a later push stays queued: the read ended at completeness

    def deliver(client: Any, plain: bytes) -> None:
        if plain[0] == 0xAC:
            for f in [*burst, trailing]:
                client._notify(None, bytearray(f))

    fake.on_write = deliver
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    c = GoveeConnection(ble_device("AA:BB:CC:DD:EE:5D", "GVH60A6X"), encryption=Encryption.NONE)
    c._idle_disconnect = 0
    await c.connect()
    # A long idle gap and timeout: only completeness can end this read with the push still queued.
    got = await c.query(frame(0xAC, 0x03), timeout=30, idle_gap=30)
    assert got == burst and c._rx.qsize() == 1
    assert reassemble.parse_status(got).is_on is True


# ── #19: the analyzer checks 0xAC framing as the runtime does ─────────────────────────────────
def test_analyzer_flags_a_bad_bcc_chunk_and_leaves_it_out() -> None:
    burst = _burst(3, 0xFF)
    bad = bytearray(burst[1])
    bad[19] ^= 0x01
    assert describe_frame(bytes(bad), "rx")[0] == "bad_checksum"
    assert describe_frame(burst[1], "rx") == ("status-chunk", [])
    # Dropped before reassembly, as connection._handle_notify drops it: the burst lost a chunk.
    report = status_burst_report([burst[0], bytes(bad), burst[2]])
    assert report.incomplete == 1 and not report.tlv_types


def test_analyzer_reads_the_status_request() -> None:
    req = frame(0xAC, 0x03, 0x03, 0x41, 0x30, 0xA5)
    assert describe_frame(req, "tx") == ("status-request/3", [])
    assert describe_frame(req[:-1] + bytes([req[-1] ^ 1]), "tx")[0] == "bad_checksum"
    # A request declaring more types than the frame holds: a spec gap, not a clean request.
    label, issues = describe_frame(frame(0xAC, 0x03, 0x20, 0x41), "tx")
    assert label == "parse_error" and issues[0][0] == "hard"
    # A short 0xAC record is an artifact, as for any frame.
    short = describe_frame(bytes([0xAC, 0x00, 0x01]), "rx")
    assert short == ("len=3", [("artifact", "not 20 bytes (len 3)")])


def test_analyzer_keeps_redacted_chunks() -> None:
    # The field-test masking zeroes every data byte and the BCC; such a chunk is not corrupt.
    first = bytes([0xAC, 0x00, 0x03, 0x00, 0x04, 0x03, 0x00]) + bytes(13)
    later = [bytes([0xAC, 0x01]) + bytes(18), bytes([0xAC, 0xFF]) + bytes(18)]
    assert describe_frame(later[0], "rx") == ("status-chunk", [])
    # Kept for reassembly (not dropped as bad-BCC chunks): the burst's framing is whole.
    report = status_burst_report([first, *later])
    assert (report.incomplete, report.conflicting, report.malformed) == (0, 0, 0)
    # A capture masked before the header was kept: counted as redacted, as before.
    old_first = bytes([0xAC, 0x00]) + bytes(18)
    report = status_burst_report([old_first, *later])
    assert report.redacted == 1 and report.malformed == 0


# ── #3 / G30: H6006 is goodsType 64, KelvinConfig version 2 = [2700, 6500] ────────────────────
def test_h6006_kelvin_is_the_apps_range() -> None:
    d = _dev("H6006")
    assert (d.min_kelvin, d.max_kelvin) == (2700, 6500)
    for asked, sent in ((2000, 2700), (9000, 6500), (4000, 4000)):   # clamped to the range, as the app does
        asyncio.run(d.set_color_temp(asked))
        assert _sent(d)[-1] == color_temp(sent, "h6006"), (asked, sent)
        assert d.state.color_temp_kelvin == sent
    # A tint colour outside the range is a plain RGB pick, as on H6008 (KelvinConfig.i).
    asyncio.run(d.set_rgb((0xFF, 0x8D, 0x0B)))   # 2000 K's first tint
    assert _sent(d)[-1].hex() == "33050dff8d0b0000000000000000000000000042"


# ── #1 / G3: each plug's main switch is its goodsType's frame ─────────────────────────────────
@pytest.mark.parametrize(("sku", "on_frame", "off_frame"), [
    # gt 43; the H5083 is test_h5083_firmware.test_set_power_switches_the_relay_and_reads_it_back
    ("H5080", "330111", "330110"), ("H5085", "330111", "330110"),
    ("H5082", "3301ff", "3301f0"),                                                                # gt 50
    ("H5160", "3301ff", "3301f0"), ("H5161", "3301ff", "3301f0"),                                 # gt 90
    ("H5089", "330201", "330200"),                                                                # gt 307
])
def test_plug_power_frame_per_goods_type(sku: str, on_frame: str, off_frame: str) -> None:
    for on, want in ((True, on_frame), (False, off_frame)):
        d = _dev(sku)
        asyncio.run(d.set_power(on))
        sent = _sent(d)
        assert sent[0][:3].hex() == want, (sku, on)
        assert d.state.is_on is on
        # The 33 b5 time sync is sent once per connection; only gt 50 / 90 re-send it after a power write
        # (power_resync, after re-reading aa 06 and aa 07 03).
        resync = sku in ("H5082", "H5160", "H5161")
        assert [f[:2].hex() for f in sent[1:]] == (["33b5"] if resync else [])


def test_h5082_and_h5089_have_their_own_profiles() -> None:
    h5082, h5089 = profile_for("H5082"), profile_for("H5089")
    assert h5082 is not None and h5089 is not None
    assert (h5082.skus, h5082.relay_power) == (("H5082",), "all_outlets")
    assert (h5089.skus, h5089.relay_power) == (("H5089",), "total_switch")
    assert h5082.relay_on_mask == h5089.relay_on_mask == 0x03 and h5089.requires_secret   # the heartbeat rule


def test_relay_power_is_validated_and_defaults_to_1_3_0s_frame() -> None:
    p = profile_for("H5083")
    assert p is not None and p.relay_power == "outlet0"
    with pytest.raises(GoveeBleInvalidValue, match="relay_power"):
        dataclasses.replace(p, relay_power="bogus")  # type: ignore[arg-type]


# ── #2 / G16 / G27: aa 01 / aa 04 as the app sends and reads them ─────────────────────────────
def test_power_and_brightness_reads_carry_no_selector() -> None:
    assert [f[:3].hex() for f in (build.power_query(), build.brightness_query())] == ["aa0100", "aa0400"]


def test_light_power_is_on_only_for_one() -> None:
    assert parse.parse_light_power(frame(0xAA, 0x01, 0x01)) is True
    assert parse.parse_light_power(frame(0xAA, 0x01, 0x00)) is False
    assert parse.parse_light_power(frame(0xAA, 0x01, 0x02)) is False   # WAS on (any non-zero byte)
    assert parse.parse_light_power(frame(0xAA, 0x04, 0x01)) is None    # not an aa 01 reply
    assert parse.parse_power(frame(0xAA, 0x01, 0x11)) == 0x11           # plugs keep the raw relay byte


def test_polled_light_reads_power_with_the_light_rule() -> None:
    async def query(fr: bytes, **_k: object) -> list[bytes]:
        return [frame(0xAA, 0x01, 0x02)] if fr[1] == 0x01 else []

    # On for a byte of 1 only, or for any non-zero byte where the row's screen reads it so (light_nonzero):
    # H1168's connect read takes 1, its aa 01 heartbeat any non-zero byte, and the poll follows the heartbeat.
    for d, on in ((_dev("H6008"), False), (_dev("H1168"), True)):
        d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
        asyncio.run(d._read_polled())
        assert d.state.is_on is on


# ── F1 (faa2cb7 review): light vs plug power decode ──────────────────────────────────────────
def test_parse_light_power_values_and_non_replies() -> None:
    assert [parse.parse_light_power(frame(0xAA, 0x01, b)) for b in (0x00, 0x01, 0x02, 0xFF)] == \
        [False, True, False, False]
    assert parse.parse_light_power(frame(0x33, 0x01, 0x01)) is None    # a write, not a reply
    assert parse.parse_light_power(frame(0xAA, 0x04, 0x01)) is None    # brightness reply
    assert parse.parse_light_power(b"\xaa\x01") is None                 # truncated


def _scripted(sku: str, replies: dict[int, bytes]) -> Device:
    """``_dev`` whose aa reads get ``replies[command]`` (one frame) and anything else nothing."""
    d = _dev(sku)

    async def query(fr: bytes, **_k: object) -> list[bytes]:
        r = replies.get(fr[1]) if fr[0] == 0xAA else None
        return [r] if r is not None else []

    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    return d


@pytest.mark.parametrize("sku", ["H6047", "H61A8", "H6006", "H6008", "H6052", "H6641"])
@pytest.mark.parametrize(("byte", "on"), [(0x00, False), (0x01, True), (0x02, False), (0xFF, False)])
def test_polled_lights_are_on_only_for_one(sku: str, byte: int, on: bool) -> None:
    # Every curated polled light screen decodes aa 01 with ControllerSwitch.Companion.c (== 1).
    # Source-modelled for 0x02 / 0xFF: the firmware only ever stores 0 or 1.
    d = _scripted(sku, {0x01: frame(0xAA, 0x01, byte)})
    asyncio.run(d._read_polled())
    assert d.state.is_on is on


def test_h6047_bars_still_override_the_aa01_decode() -> None:
    d = _scripted("H6047", {0x01: frame(0xAA, 0x01, 0x02), 0x36: frame(0xAA, 0x36, 0x01, 0x00)})
    asyncio.run(d._read_polled())
    assert d.state.is_on is True and d.state.zone_power == {0: True, 1: False}


@pytest.mark.parametrize(("sku", "byte", "on"), [
    ("H5082", 0x02, True), ("H5080", 0x02, False), ("H5160", 0x08, False), ("H5160", 0x04, True),
])
def test_plugs_keep_the_relay_mask(sku: str, byte: int, on: bool) -> None:
    d = _scripted(sku, {0x01: frame(0xAA, 0x01, byte)})
    asyncio.run(d._read_plug())
    assert d.state.is_on is on


def test_h60a6_status_power_is_any_non_zero_byte() -> None:
    # Compose4BaseInfoSingleRead.u decodes TLV 0x01 as != 0 (status_switch.is_on), unlike aa 01.
    st = reassemble.parse_status([_chunk(bytes([0xAC, 0x00, 0x01, 0x00, 3, 0x03, 0x00, 0x01, 0x01, 0x02]))])
    assert st.is_on is True


# ── F2 (faa2cb7 review): the H60A6 reads power from the status burst only ──────────────────────
def test_h60a6_state_poll_is_the_status_burst_only() -> None:
    # power_read ac_status: no aa 01 / 04 / 05 in the state read, just ac 03 03 41 30 a5 (one retry at most).
    d = _dev("H60A6")
    asyncio.run(d._read_status())
    sent = [c.args[0] for c in d._conn.query.call_args_list]   # type: ignore[attr-defined]
    assert 1 <= len(sent) <= 2 and all(f[:6].hex() == "ac03034130a5" for f in sent)


# ── F5 (faa2cb7 review): the widest mask ─────────────────────────────────────────────────────
def test_masks_follow_the_apps_caps_up_to_112_segments() -> None:
    # The app's writers cut the mask at the body's cap (coerceAtMost): 7 bytes for the 0x15 RGB / CCT frames
    # (Bytes.h :324, Bytes.r :499), 14 for per-segment brightness (Bytes.G :179). WAS: refused above 56.
    assert build.color_rgb(1, 2, 3, "h60a6", 57)[12:19] == bytes([0xFF] * 7)   # positions 56+ dropped
    assert build.color_rgb(1, 2, 3, "h60a6", 57) == build.color_rgb(1, 2, 3, "h60a6", 56)
    frame = build.segment_brightness((1 << 112) - 1, 50, "h60a6", 112)
    assert frame[5:19] == bytes([0xFF] * 14)                                   # 14 mask bytes, at the cap
    with pytest.raises(GoveeBleInvalidValue):
        build.color_rgb(1, 2, 3, "h60a6", 113)
    with pytest.raises(GoveeBleInvalidValue):
        build.segment_brightness(1, 50, "h60a6", 113)


def test_analyzer_knows_the_bar_switch_and_ic_count_tlvs() -> None:
    # 0x36 (H6038 / H6048: VM4H6038 requests [41, 30, 36, A5]) and 0x40 (H6671 / H6672) are TLVs the app's
    # status reads carry; govee-ble-analyze reported them as spec gaps and exited 1.
    tlvs = bytes([0x01, 0x01, 0x01, 0x36, 0x02, 0x01, 0x00, 0x40, 0x03, 0x00, 0x30, 0x06])
    report = status_burst_report([_chunk(bytes([0xAC, 0x00, 0x01, 0x00, len(tlvs), 0x03, 0x00]) + tlvs)])
    assert report.gaps == [] and {0x36, 0x40} <= set(report.tlv_types)
