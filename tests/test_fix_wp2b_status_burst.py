"""0xAC status bursts: completeness by the first chunk's packet count, the per-TLV walk, the zone
bit and the Wi-Fi block's versions (runtime ``wire.reassemble``) plus the offline analyzer
(``wire.describe.analyze_status_bursts``), which judges and walks bursts with the same helpers.

Framing per the app's AbsController4BleSingleSendMultiBack: the first chunk is
``[ac, 00, packets u16le, lastLen, cmd, sub, 12 data bytes]``, later chunks carry 17 data bytes,
and the last packet (index 0xFF) carries lastLen of them. A one-packet reply has no 0xFF chunk."""
from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from conftest import require_optional

require_optional("kaitaistruct", reason="Kaitai runtime not installed")

from govee_ble_local import debug  # noqa: E402
from govee_ble_local.wire import reassemble as r  # noqa: E402
from govee_ble_local.wire.describe import (  # noqa: E402
    KNOWN_STATUS_REPLY_TLVS,
    analyze_status_bursts,
    status_burst_report,
)

# Real captured H60A6 0xAC status burst (13 segments, both zones on, brightness 80):
# header ac 00 | 0a 00 = 10 packets | 0c = the 0xFF chunk carries 12 data bytes.
_BURST = [bytes.fromhex(h) for h in (
    "ac000a000c0300010101040150050415010000e8",
    "ac010707065774f453e75c0711105674f453e7f0",
    "ac025cdb2d0100290104030000070d115ce753d9",
    "ac03f474560100290104031104001e0f0f120749",
    "ac04ff640000800f00231000000080000000808f",
    "ac0500000080000000804102020130020101a57e",
    "ac06110132ff00006400ff00640000ff64ff00ec",
    "ac0700a511026400ff00640000ff64ff000064e2",
    "ac0800ff00a51103640000ff64ff00006400ff77",
    "acff00640000ffa5050464ff00000000000000f7",
)]


def _fr(*body: int) -> bytes:
    """20-byte frame with the trailing XOR checksum."""
    b = bytearray(20)
    b[: len(body)] = bytes(body)
    x = 0
    for c in b[:19]:
        x ^= c
    b[19] = x
    return bytes(b)


def _bcc(frame: bytearray) -> bytes:
    """``frame`` with its trailing XOR checksum recomputed."""
    x = 0
    for c in frame[:19]:
        x ^= c
    frame[19] = x
    return bytes(frame)


def _chunk(data: bytes) -> list[bytes]:
    """Split a TLV stream into 0xAC chunks the way the device sends them (the inverse of
    AbsController4BleSingleSendMultiBack.g / .i / .h)."""
    if len(data) <= 12:
        return [_fr(0xAC, 0x00, 1, 0, len(data), 0x03, 0x00, *data)]
    rest = data[12:]
    pieces = [rest[i:i + 17] for i in range(0, len(rest), 17)]
    total = 1 + len(pieces)
    out = [_fr(0xAC, 0x00, total & 0xFF, total >> 8, len(pieces[-1]), 0x03, 0x00, *data[:12])]
    out += [_fr(0xAC, i, *piece) for i, piece in enumerate(pieces[:-1], start=1)]
    out.append(_fr(0xAC, 0xFF, *pieces[-1]))
    return out


def test_chunker_reproduces_the_real_burst() -> None:
    # The test chunker and the reassembler are inverses on a real capture, header bytes included.
    data = r.reassemble(_BURST)
    assert len(data) == 12 + 8 * 17 + 12
    assert _chunk(data) == _BURST


# ── completeness: the first chunk's packet count (allBackPkgs) ──────────────────
def test_lost_chunk_before_terminator_is_incomplete_and_never_spliced() -> None:
    # Chunk 0x08 lost: indices {0..7, FF} are contiguous, but the header declares 10 packets.
    # The terminator's bytes must not be joined straight after chunk 0x07.
    dropped = _BURST[:8] + _BURST[9:]
    chunks, complete, conflicts = r.burst_chunks(dropped)
    assert (complete, conflicts) == (False, 0) and chunks == _BURST[:8]
    two = _BURST[:7] + _BURST[9:]      # 0x07 and 0x08 both lost
    # WAS: the prefix was joined and decoded. The app's read never completes (END_PKG_ORDER, then the
    # timeout), so nothing is joined and no state is used.
    assert r.reassemble(dropped) == r.reassemble(two) == b""
    assert r.parse_status(dropped) == r.parse_status(two) == r.StatusReply()


def test_lone_terminator_is_not_a_burst() -> None:
    # A lone 0xFF chunk used to count as complete and be read as if it were the first chunk.
    assert r.burst_chunks([_BURST[-1]]) == ([], False, 0)
    assert r.reassemble([_BURST[-1]]) == b""
    assert r.parse_status([_BURST[-1]]) == r.StatusReply()


def test_declared_count_caps_the_prefix() -> None:
    # A stray chunk beyond the declared count (e.g. a leftover index) can't extend the burst.
    frames = _chunk(bytes([0x01, 0x01, 0x01, 0x04, 0x01, 0x32]) + bytes(30))   # 3 packets
    assert [fr[1] for fr in frames] == [0x00, 0x01, 0xFF]
    stray = _fr(0xAC, 0x02, *([0x04, 0x01, 0x63] + [0] * 14))
    assert r.burst_chunks([*frames, stray])[:2] == (frames, True)
    assert r.reassemble([*frames, stray]) == r.reassemble(frames)


def test_last_packet_contributes_only_last_len_bytes() -> None:
    # AbsController4BleSingleSendMultiBack.h copies endPkgLen (header byte 4 = 12) bytes of the
    # last packet; anything after them is not part of the reply.
    tail = bytearray(_BURST[-1])
    tail[14:17] = bytes([0x04, 0x01, 0x63])     # a fake brightness TLV after the 12 real bytes
    frames = [*_BURST[:-1], bytes(tail)]
    assert r.reassemble(frames) == r.reassemble(_BURST)
    assert r.parse_status(frames).brightness == 0x50


def test_index_count_minus_one_is_accepted_as_the_last_packet() -> None:
    # isSameController accepts the chunk whose index == backPos == count-1 as the last one too.
    frames = _chunk(bytes([0x01, 0x01, 0x01, 0x04, 0x01, 0x32]) + bytes(30))
    last = bytearray(frames[-1])
    last[1] = 0x02
    chunks, complete, _ = r.burst_chunks([frames[0], frames[1], bytes(last)])
    assert complete and chunks[-1] == bytes(last)


def test_single_packet_reply_has_no_terminator() -> None:
    # count == 1: g() hands lastLen bytes at offset 7 straight to l(); no 0xFF chunk follows.
    tlvs = bytes([0x01, 0x01, 0x01, 0x04, 0x01, 0x32])
    first = bytearray(_fr(0xAC, 0x00, 1, 0, len(tlvs), 0x03, 0x00, *tlvs))
    first[13:16] = bytes([0x04, 0x01, 0x63])    # bytes past lastLen are not part of the reply
    frames = [bytes(first)]
    assert r.burst_chunks(frames) == (frames, True, 0)
    assert r.reassemble(frames) == tlvs
    st = r.parse_status(frames)
    assert (st.is_on, st.brightness) == (True, 0x32)


def test_bad_last_len_rejects_the_burst() -> None:
    # A lastLen longer than its packet can hold makes the app reject the burst before it copies a
    # byte (g() returns -1, the read fails): nothing of it is decoded.
    one = _fr(0xAC, 0x00, 1, 0, 13, 0x03, 0x00, 0x01, 0x01, 0x01)
    assert r.burst_chunks([one]) == ([], False, 0)
    assert r.parse_status([one]) == r.StatusReply()
    assert r.burst_chunks([_fr(0xAC, 0x00, 1, 0, 12, 0x03, 0x00, 0x01, 0x01, 0x01)])[1] is True
    frames = _chunk(bytes([0x01, 0x01, 0x01]) + bytes(20))
    first = bytearray(frames[0])
    first[4] = 18
    assert r.burst_chunks([bytes(first), *frames[1:]]) == ([], False, 0)
    assert r.parse_status([bytes(first), *frames[1:]]) == r.StatusReply()
    first[4] = 17
    assert r.burst_chunks([bytes(first), *frames[1:]])[1] is True


def test_conflicting_duplicate_is_counted_and_first_copy_kept(caplog: pytest.LogCaptureFixture) -> None:
    other = bytearray(_BURST[3])
    other[5] ^= 0xFF
    with caplog.at_level(logging.DEBUG, logger="govee_ble_local.wire.reassemble"):
        chunks, complete, conflicts = r.burst_chunks([*_BURST[:4], bytes(other), *_BURST[4:]])
    assert (complete, conflicts) == (True, 1) and chunks[3] == _BURST[3]
    assert any("different bytes" in rec.message for rec in caplog.records)


# ── TLV walk: a TLV the app's walker throws on fails the app's read ─────────────
def test_unparseable_tlv_fails_the_read_like_the_app() -> None:
    # A basic (0x10) block with no body: the app's w() copies past its end and throws, the read
    # ends in result2Fail, and VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.l (switch,
    # brightness, mode sync) never runs; the app reports the read failed (.m: P(1)). So no state at
    # all, not even the zone / colour TLVs before the bad one (the VM lambda had only stored those
    # in its model).
    st = r.parse_status(_chunk(bytes([0x01, 0x01, 0x01, 0x04, 0x01, 0x40, 0x07, 0x01, 0x10])))
    assert st == r.StatusReply()
    st = r.parse_status(_chunk(bytes([0x01, 0x01, 0x01, 0x30, 0x02, 0x01, 0x01,
                                      0xA5, 0x05, 0x01, 100, 1, 2, 3, 0x07, 0x03, 0x10, 0xAA, 0xBB])))
    assert st == r.StatusReply()
    # 0x30 with len 1: the VM lambda (.o) stores zone A (bit0 of byte 0) in its model before
    # bArr2[1] throws, but the read fails all the same, so no zone is reported.
    st = r.parse_status(_chunk(bytes([0x30, 0x01, 0x01, 0x30, 0x02, 0x01, 0x01])))
    assert st == r.StatusReply()


def test_complete_burst_whose_tlv_overruns_it_fails_the_read() -> None:
    # Every declared packet arrived, but the last TLV's len runs past the reply: the app's
    # System.arraycopy in u() throws there, and the read fails.
    st = r.parse_status(_chunk(bytes([0x01, 0x01, 0x01, 0x30, 0x02, 0x01, 0x00, 0x04, 0x05, 0x32])))
    assert st == r.StatusReply()


def test_info_block_of_another_selector_is_stepped_past() -> None:
    # Compose4BaseInfoSingleRead.w reads only selectors 0x10 / 0x11; for any other it logs and
    # returns without reading the body, so u() walks on and result2Suc applies switch and
    # brightness. A short SN (0x02) or a non-UTF-8 hard-version (0x03) block the spec can't read is
    # stepped past instead of failing the read.
    st = r.parse_status(_chunk(bytes([0x07, 0x04, 0x03, 0xFF, 0xFE, 0xFD,
                                      0x01, 0x01, 0x01, 0x04, 0x01, 0x32])))
    assert (st.is_on, st.brightness) == (True, 0x32)
    st = r.parse_status(_chunk(bytes([0x01, 0x01, 0x01, 0x04, 0x01, 0x32, 0x07, 0x03, 0x02, 0xAA, 0xBB,
                                      0x30, 0x02, 0x01, 0x01])))
    assert (st.is_on, st.brightness, st.zone_power) == (True, 0x32, {0: True, 1: True})
    for block in (bytes([0x07, 0x03, 0x02, 0xAA, 0xBB]), bytes([0x07, 0x02, 0x03, 0xFF])):
        st = r.parse_status(_chunk(bytes([0x01, 0x01, 0x01]) + block + bytes([0x04, 0x01, 0x40])))
        assert (st.is_on, st.brightness, st.serial_number, st.hardware_version) == (True, 0x40, None, None)


def test_info_block_without_selector_or_too_short_still_fails_the_read() -> None:
    # An empty 0x07 has no selector (w()'s bArr[0] throws); a short Wi-Fi (0x11) block makes w()
    # copy past its end. Both fail the app's read.
    assert r.parse_status(_chunk(bytes([0x01, 0x01, 0x01, 0x07, 0x00, 0x04, 0x01, 0x40]))) == r.StatusReply()
    assert r.parse_status(_chunk(bytes([0x01, 0x01, 0x01, 0x07, 0x03, 0x11, 0xAA, 0xBB,
                                        0x04, 0x01, 0x40]))) == r.StatusReply()


def test_walk_hands_on_the_info_blocks_the_app_does_not_decode() -> None:
    # status_tlv 0x07 reads device_info_record on the status path, which leaves an SN (0x02) block unread
    # (app_decodes False), as the app's w() skips it, and the stream reads on.
    buf = bytes([0x01, 0x01, 0x01, 0x07, 0x03, 0x02, 0xAA, 0xBB, 0x04, 0x01, 0x40])
    read, err = r.read_status_stream(buf)
    assert [int(t.tag) for t in read.tlvs] == [0x01, 0x07, 0x04] and err is None
    assert read.tlvs[1].value.body.app_decodes is False
    assert r.parse_status(_chunk(buf)).brightness == 0x40


def test_a_burst_cut_by_lost_chunks_gives_no_state() -> None:
    # WAS: the library's own leniency kept every whole TLV before the cut. The app never decodes such a
    # burst (l() runs only once the last packet is in).
    assert r.parse_status(_BURST[:3]) == r.StatusReply()


def test_unparseable_first_tlv_yields_empty_state_without_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    c0 = bytearray(20)
    c0[:7] = bytes([0xAC, 0x00, 2, 0, 0, 0x03, 0x00])
    c0[7:13] = bytes([0x01, 0x01, 0x01, 0x07, 0x01, 0x10])
    end = bytearray(20)
    end[:2] = b"\xac\xff"
    with caplog.at_level(logging.DEBUG, logger="govee_ble_local.wire.reassemble"):
        assert r.parse_status([bytes(c0), bytes(end)]) == r.StatusReply()
        assert r.parse_status(_chunk(bytes([0x07, 0x01, 0x10, 0x01, 0x01, 0x01]))) == r.StatusReply()
    assert any("stopped at TLV 0x07" in rec.message for rec in caplog.records)


def test_the_stream_read_reports_where_and_why_it_stopped() -> None:
    # A value its type can't read (a 0x10 block with no record): the read stops in that record.
    read, err = r.read_status_stream(bytes([0x01, 0x01, 0x01, 0x07, 0x01, 0x10]))
    assert err is not None and [int(t.tag) for t in read.tlvs] == [0x01, 0x07]
    # A value past the end: the record holds no value bytes.
    read, err = r.read_status_stream(bytes([0x01, 0x01, 0x01, 0x04, 0x05, 0x10]))
    assert err is not None and getattr(read.tlvs[-1], "_raw_value", None) is None
    # Fewer than three bytes left: u() stops cleanly (:257-267, :271-282), keeping the switch; the
    # bytes are the stream's tail (a zero pad among them).
    for tail in (b"\x04", b"\x30\x00", b"\x00\x00"):
        read, err = r.read_status_stream(bytes([0x01, 0x01, 0x01]) + tail)
        assert err is None and [int(t.tag) for t in read.tlvs] == [0x01] and bytes(read.tail) == tail
    read, err = r.read_status_stream(b"")
    assert err is None and getattr(read, "tlvs", None) is None and bytes(read.tail) == b""


# ── values: zone bit and the Wi-Fi block's versions ─────────────────────────────
def test_zone_power_reads_bit0_only() -> None:
    # VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.o: ParseUtils.getBit2Boolean(b, 1) = bit0.
    st = r.parse_status(_chunk(bytes([0x30, 0x02, 0x02, 0x03])))
    assert st.zone_power == {0: False, 1: True}


_WIFI = bytes([0x07, 0x0D, 0x11, 0x5C, 0xE7, 0x53, 0xF4, 0x74, 0x56, 2, 0, 9, 3, 0, 1])
_BASIC = bytes([0x07, 0x11, 0x10, 0x56, 0x74, 0xF4, 0x53, 0xE7, 0x5C, 0xDB, 0x2D,
                1, 0, 41, 1, 4, 3, 0, 0])


def test_wifi_module_versions_never_fill_the_device_versions() -> None:
    # Compose4BaseInfoSingleRead.w keeps 0x11's sw/hw as wifiSoftVersion / wifiHardVersion.
    st = r.parse_status(_chunk(_WIFI + _BASIC))        # Wi-Fi block first
    assert st.wifi_mac == "5C:E7:53:F4:74:56"
    assert (st.firmware_version, st.hardware_version) == ("1.00.41", "1.04.03")
    alone = r.parse_status(_chunk(_WIFI))
    assert alone.wifi_mac == "5C:E7:53:F4:74:56"
    assert (alone.firmware_version, alone.hardware_version) == (None, None)


def test_status_device_info_reads_only_the_basic_and_wifi_blocks() -> None:
    # Compose4BaseInfoSingleRead.w handles selectors 0x10 and 0x11 only. An SN (0x02) or
    # hard-version (0x03) block in the status reply is skipped, though aa 07 02 / 03 replies use them.
    sn = bytes([0x07, 0x09, 0x02, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18])
    hard = bytes([0x07, 0x08, 0x03, *b"9.99.99"])
    st = r.parse_status(_chunk(sn + hard + bytes([0x01, 0x01, 0x01])))
    assert st.is_on is True                                 # the walk went on past both blocks
    assert (st.serial_number, st.hardware_version) == (None, None)
    st = r.parse_status(_chunk(sn + hard + _BASIC))
    assert (st.serial_number, st.hardware_version) == ("2D:DB:5C:E7:53:F4:74:56", "1.04.03")


# ── analyzer: same completeness rule and walk as the runtime ────────────────────
def test_analyzer_known_tlv_set_is_the_specs_status_tlv_type() -> None:
    # A status_tlv's tag is a command (enum command): every tag the spec names is known, the old
    # status_tlv_type set (0x36 for H6038 / H6048, 0x40 for H6671 / H6672, ...) among them.
    from govee_ble_local.wire import _spec

    assert frozenset(int(c) for c in _spec.Common.Command) == KNOWN_STATUS_REPLY_TLVS
    assert frozenset({0x00, 0x01, 0x04, 0x05, 0x07, 0x11, 0x12, 0x16, 0x23, 0x30, 0x36, 0x39, 0x40, 0x41,
                      0xA3, 0xA5}) <= KNOWN_STATUS_REPLY_TLVS


def test_analyzer_double_delivery_is_not_malformed() -> None:
    # The device double-delivers every chunk (ac00 ac00 ac01 ac01 ...): one clean burst.
    doubled = [f for fr in _BURST for f in (fr, fr)]
    assert analyze_status_bursts(doubled) == analyze_status_bursts(_BURST)
    assert analyze_status_bursts(_BURST)[2] == 0


def test_analyzer_short_record_does_not_crash() -> None:
    # A 1-byte "ac" record inside a burst used to raise IndexError; it is skipped like the runtime.
    assert analyze_status_bursts([_BURST[0], b"\xac", *_BURST[1:]]) == analyze_status_bursts(_BURST)
    assert analyze_status_bursts([b"\xac", b"", b"\xac\x00"]) == analyze_status_bursts([])


def test_analyzer_interior_gap_is_malformed() -> None:
    types, gaps, malformed = analyze_status_bursts([_BURST[0], *_BURST[2:]])
    assert (dict(types), gaps, malformed) == ({}, [], 1)


def test_analyzer_lost_chunk_before_terminator_is_malformed() -> None:
    types, gaps, malformed = analyze_status_bursts(_BURST[:8] + _BURST[9:])
    assert (dict(types), gaps, malformed) == ({}, [], 1)
    report = status_burst_report(_BURST[:8] + _BURST[9:])
    assert (report.incomplete, report.conflicting, report.truncated) == (1, 0, 0)


def test_analyzer_conflicting_duplicate_is_malformed() -> None:
    other = bytearray(_BURST[3])
    other[5] ^= 0xFF
    other[19] ^= 0xFF   # keep the BCC valid: a chunk failing it is dropped before reassembly
    report = status_burst_report([*_BURST[:4], bytes(other), *_BURST[4:]])
    assert (report.conflicting, report.malformed, dict(report.tlv_types)) == (1, 1, {})


def test_analyzer_counts_the_tlvs_its_walk_reached() -> None:
    # A complete burst with a TLV its spec type can't read: the analyzer counts the TLV types the walk
    # reached (spec coverage) and flags the bad TLV as a spec gap (the framing is fine: not malformed).
    # The runtime uses NO state from this burst: the app's read fails on the bad TLV (parse_status).
    frames = _chunk(bytes([0x01, 0x01, 0x01, 0x04, 0x01, 0x40, 0x07, 0x01, 0x10]))
    types, gaps, malformed = analyze_status_bursts(frames)
    assert malformed == 0 and dict(types) == {0x01: 1, 0x04: 1, 0x07: 1}
    assert gaps == ["0xAC reply TLV type 0x07 (len 1) did not parse against the spec"]


def test_analyzer_single_packet_burst_is_walked() -> None:
    frames = _chunk(bytes([0x01, 0x01, 0x01, 0x04, 0x01, 0x32]))
    assert len(frames) == 1
    types, gaps, malformed = analyze_status_bursts(frames + frames)   # double-delivered
    assert (dict(types), gaps, malformed) == ({0x01: 1, 0x04: 1}, [], 0)


def test_analyzer_overrunning_stream_counts_as_truncated() -> None:
    # Complete framing, but the last TLV's len runs past the reply: walked up to the cut, so it is
    # truncated, not one of the malformed bursts that were skipped.
    frames = _chunk(bytes([0x01, 0x01, 0x01, 0x04, 0x05, 0x32]))
    report = status_burst_report(frames)
    assert (dict(report.tlv_types), report.truncated, report.malformed) == ({0x01: 1}, 1, 0)
    assert analyze_status_bursts(frames) == (report.tlv_types, [], 0)


def test_analyzer_reads_an_info_block_the_app_skips_without_a_gap() -> None:
    # status_device_info reads the SN block raw, as the app skips it: no spec gap any more (WAS: "did not
    # parse against the spec"), and the TLVs after it are counted.
    frames = _chunk(bytes([0x01, 0x01, 0x01, 0x07, 0x03, 0x02, 0xAA, 0xBB, 0x04, 0x01, 0x40]))
    types, gaps, malformed = analyze_status_bursts(frames)
    assert malformed == 0 and dict(types) == {0x01: 1, 0x07: 1, 0x04: 1} and gaps == []


def test_analyzer_lost_terminator_is_not_folded_into_the_next_poll() -> None:
    # Two polls of an unchanged light share their first chunk byte for byte. Poll 1 lost its 0xFF
    # terminator: poll 2's ac00 opens a new burst instead of being taken as a double delivery, so
    # poll 1 is reported incomplete and poll 2 is walked on its own.
    report = status_burst_report(_BURST[:-1] + _BURST)
    assert (report.incomplete, report.conflicting, report.malformed) == (1, 0, 1)
    assert analyze_status_bursts(_BURST[:-1] + _BURST) == (analyze_status_bursts(_BURST)[0], [], 1)
    # Poll 2 has a different colour in chunk 6 (record 1's G): still walked, not "conflicting".
    changed = bytearray(_BURST[6])
    changed[6] ^= 0x01
    poll2 = [*_BURST[:6], _bcc(changed), *_BURST[7:]]
    report = status_burst_report(_BURST[:-1] + poll2)
    assert (report.incomplete, report.conflicting) == (1, 0)
    assert analyze_status_bursts(_BURST[:-1] + poll2) == (analyze_status_bursts(poll2)[0], [], 1)
    assert 0xA5 in analyze_status_bursts(poll2)[0]


def test_analyze_cli_reports_a_double_delivered_burst_as_clean(capsys: pytest.CaptureFixture[str]) -> None:
    # govee-ble-analyze over a capture where the device double-delivered every chunk: the burst is
    # reassembled and its TLV types listed, and nothing is reported as a malformed burst.
    records: list[dict[str, object]] = [
        {"addr": "AA:BB", "dir": "rx", "plain": fr.hex(), "enc": "none"} for c in _BURST for fr in (c, c)]
    assert debug.analyze(records) == 0
    out = capsys.readouterr().out
    assert "0xAC status reply — reassembled TLV types seen" in out
    assert "0x01:1" in out and "0xa5:4" in out
    assert "malformed 0xAC bursts" not in out
    assert "status-chunk" in out


def test_analyze_cli_does_not_call_a_walked_burst_skipped(capsys: pytest.CaptureFixture[str]) -> None:
    # A complete burst whose last TLV overruns it is walked up to the cut: its TLV types are listed,
    # and it is not also reported among the "malformed 0xAC bursts skipped".
    records: list[dict[str, object]] = [
        {"addr": "AA:BB", "dir": "rx", "plain": fr.hex(), "enc": "none"}
        for fr in _chunk(bytes([0x01, 0x01, 0x01, 0x04, 0x05, 0x32]))]
    debug.analyze(records)
    out = capsys.readouterr().out
    assert "0x01:1" in out
    assert "malformed 0xAC bursts" not in out


# ── analyzer over a redacted field-test capture (tools/field_test/masking.redact_frames) ──────
_TOOLS = Path(__file__).resolve().parent.parent / "tools"


@pytest.fixture
def masking(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.syspath_prepend(str(_TOOLS))
    yield importlib.import_module("field_test.masking")


def _redact(fr: bytes) -> bytes:
    """A chunk as the capture masking writes it today: ``ac <index>`` kept, everything else zeroed."""
    return fr[:2] + bytes(len(fr) - 2)


def test_analyzer_redacted_burst_is_not_malformed() -> None:
    # The masking zeroes the first chunk's packet count; the clean burst must not read as dropped.
    redacted = [_redact(fr) for fr in _BURST]
    report = status_burst_report(redacted)
    assert (report.redacted, report.incomplete, report.conflicting, report.malformed) == (1, 0, 0, 0)
    assert (dict(report.tlv_types), report.gaps, report.truncated) == ({}, [], 0)   # nothing to walk
    assert analyze_status_bursts(redacted) == (report.tlv_types, [], 0)
    doubled = [f for fr in redacted for f in (fr, fr)]
    assert status_burst_report(doubled) == report
    two_polls = status_burst_report(redacted + redacted)
    assert (two_polls.redacted, two_polls.malformed) == (2, 0)


def test_analyzer_redacted_burst_still_shows_dropped_chunks() -> None:
    # Judged by chunk index: an interior gap or a lost terminator is still a dropped burst.
    redacted = [_redact(fr) for fr in _BURST]
    gap = status_burst_report([*redacted[:3], *redacted[4:]])
    assert (gap.redacted, gap.incomplete, gap.malformed) == (1, 1, 1)
    lost_ff = status_burst_report(redacted[:-1] + redacted)
    assert (lost_ff.redacted, lost_ff.incomplete, lost_ff.malformed) == (2, 1, 1)


def test_analyzer_zero_count_header_of_a_real_frame_keeps_the_app_rule() -> None:
    # A device chunk with a corrupt packet count (0) but its cmd byte and BCC set is not a redacted
    # one: the app never completes it, so it stays incomplete.
    first = _fr(0xAC, 0x00, 0, 0, 0, 0x03, 0x00, 0x01, 0x01, 0x01)
    report = status_burst_report([first, _fr(0xAC, 0xFF)])
    assert (report.redacted, report.incomplete, report.malformed) == (0, 1, 1)


def test_analyzer_header_kept_redaction_uses_the_packet_count() -> None:
    # If the masking keeps the first chunk's 7-byte header, the app's rule applies again: the clean
    # burst is complete, and a lost chunk just before 0xFF is caught.
    kept = [fr[:7] + bytes(13) if fr[1] == 0 else _redact(fr) for fr in _BURST]
    report = status_burst_report(kept)
    # The zeroed payload reads as tag-0 records, as the app's walk takes them (WAS: a type-0 byte ended
    # the walk, so none were counted).
    assert (report.redacted, report.malformed, dict(report.tlv_types), report.gaps) == (0, 0, {0: 79}, [])
    assert status_burst_report(kept[:8] + kept[9:]).incomplete == 1


def _capture(tmp_path: Path, frames: list[bytes]) -> Path:
    path = tmp_path / "h60a6-frames-T.jsonl"
    rows = [{"ts": 1, "addr": "5C:E7:53:F4:74:57", "dir": "tx", "plain": "ac" + "00" * 19, "wire": None}]
    rows += [{"ts": 2 + i, "addr": "5C:E7:53:F4:74:57", "dir": "rx", "plain": fr.hex(), "wire": None}
             for i, fr in enumerate(frames)]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def test_redacted_capture_round_trip_reports_a_clean_burst_as_clean(
    masking: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    # The field test redacts its capture before it is attached to an issue; govee-ble-analyze over
    # it must not call the clean status burst dropped or merged (whichever header masking applies).
    path = _capture(tmp_path, [f for fr in _BURST for f in (fr, fr)])
    assert masking.redact_frames(path, "5C:E7:53:F4:74:57") == 21
    records: list[dict[str, object]] = [json.loads(line) for line in path.read_text().splitlines()]
    rx_ac = [bytes.fromhex(str(rec["plain"])) for rec in records if rec["dir"] == "rx"]
    assert status_burst_report(rx_ac).malformed == 0
    assert debug.analyze(records) == 0
    assert "malformed 0xAC bursts" not in capsys.readouterr().out


def test_redacted_capture_round_trip_still_reports_a_dropped_chunk(
    masking: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    path = _capture(tmp_path, [*_BURST[:3], *_BURST[4:]])          # chunk 0x03 lost
    masking.redact_frames(path, "5C:E7:53:F4:74:57")
    records: list[dict[str, object]] = [json.loads(line) for line in path.read_text().splitlines()]
    debug.analyze(records)
    out = capsys.readouterr().out
    assert "malformed 0xAC bursts skipped (dropped/merged frames, NOT spec gaps): 1" in out
