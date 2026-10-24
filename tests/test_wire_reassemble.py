"""wire.reassemble — de-chunk a 0xAC status burst + walk the TLV reply.

Validated against a real captured H60A6 burst (13 segments, both zones on, brightness 80)."""
from __future__ import annotations

from govee_ble_local.wire import reassemble as r

# Real captured H60A6 0xAC status burst (segments set to a cycling R/G/B pattern).
# Device ids and MACs in it are made-up examples (checksums recomputed).
_BURST = [
    "ac000a000c0300010101040150050415010000e8",
    "ac010707065753005e00020711105653005e00ae",
    "ac0202a60a0100290104030000070d1102005e69",
    "ac030053560100290104031104001e0f0f12079a",
    "ac04ff640000800f00231000000080000000808f",
    "ac0500000080000000804102020130020101a57e",
    "ac06110132ff00006400ff00640000ff64ff00ec",
    "ac0700a511026400ff00640000ff64ff000064e2",
    "ac0800ff00a51103640000ff64ff00006400ff77",
    "acff00640000ffa5050464ff00000000000000f7",
]


def test_parse_real_h60a6_burst() -> None:
    frames = [bytes.fromhex(h) for h in _BURST]
    st = r.parse_status(frames)
    assert st.is_on is True                       # 0x01 switch TLV
    assert st.brightness == 0x50                  # 0x04 brightness TLV (80)
    assert st.zone_power == {0: True, 1: True}    # 0x30 zone TLV (01 01)
    assert len(st.segments) == 13                 # four 0xA5 groups: 4+4+4+1
    assert [s.index for s in st.segments] == list(range(13))
    names = {(255, 0, 0): "R", (0, 255, 0): "G", (0, 0, 255): "B"}
    assert "".join(names.get(s.rgb, "?") if s.rgb else "?" for s in st.segments) == "RGBRGBRGBRGBR"
    assert st.segments[0].brightness == 50 and st.segments[1].brightness == 100


def test_doubled_delivery_reassembles_identically() -> None:
    # devices double-deliver each chunk (ac00 ac00 ac01 ac01 ...); reassemble must
    # dedup by chunk index so duplicates don't drift the offset-based join.
    frames = [bytes.fromhex(h) for h in _BURST]
    doubled = [f for fr in frames for f in (fr, fr)]      # each chunk twice
    assert r.reassemble(doubled) == r.reassemble(frames)
    st = r.parse_status(doubled)
    assert len(st.segments) == 13 and st.brightness == 0x50
    assert st.zone_power == {0: True, 1: True}


def test_out_of_order_chunks_are_not_reassembled() -> None:
    # WAS: put back in index order. The app takes a chunk only when its index is the next one expected
    # (AbsController4BleSingleSendMultiBack.isSameController:220), so an early chunk is ignored and,
    # unless it comes again in order, the read never completes.
    frames = [bytes.fromhex(h) for h in _BURST]
    shuffled = [frames[3], frames[0], frames[-1], frames[1], frames[2], *frames[4:-1]]
    assert r.reassemble(shuffled) == b"" and r.parse_status(shuffled) == r.StatusReply()
    # ...but a chunk that arrives early and again in order is taken the second time.
    assert r.reassemble([frames[3], *frames]) == r.reassemble(frames)


def test_parse_status_stops_on_padding() -> None:
    # The last chunk contributes only its lastLen bytes (AbsController4BleSingleSendMultiBack.h), so
    # the real burst ends exactly on its last TLV. A zero-padded buffer (lastLen counting the pad)
    # still reads whole: the 5 zero bytes are two empty tag-0 records, walked on as u() hands them to the
    # screen's fallback lambda, and a 1-byte tail.
    frames = [bytes.fromhex(h) for h in _BURST]
    buf = r.reassemble(frames)
    assert buf.endswith(bytes.fromhex("a5050464ff0000"))     # the last colour group, no pad
    padded = bytearray(frames[0])
    padded[4] = 17                                           # lastLen 17: 5 zero bytes of pad joined
    assert r.reassemble([bytes(padded), *frames[1:]]) == buf + bytes(5)
    st = r.parse_status([bytes(padded), *frames[1:]])
    assert st.is_on is True and st.brightness == 0x50   # no throw, no phantom TLVs from the pad
    assert len(st.segments) == 13


def test_parse_status_extracts_device_info() -> None:
    # BLE-only devices (H60A6) report device-info ONLY in the 0xAC stream, via the 0x07 TLV
    # (parsed by the generated device_info_read reader — no MAC-anchor heuristic, no address).
    st = r.parse_status([bytes.fromhex(h) for h in _BURST])
    assert st.wifi_mac == "02:00:5E:00:53:56"       # 0x11 wifi TLV
    assert st.hardware_version == "1.04.03"          # matches the old anchor's value
    assert st.firmware_version == "1.00.41"          # bonus vs the anchor (sw was not extracted before)
    assert st.serial_number == "0A:A6:02:00:5E:00:53:56"   # 0x10 basic uid
    assert not hasattr(r, "anchor_device_info")      # heuristic retired


def test_empty_and_short() -> None:
    assert r.parse_status([]).segments == []
    assert r.parse_status([b"\x00" * 4]).is_on is None


# Real H60A6 burst (live capture). The device very often loses the 0xFF terminator
# on the first query of a session, and occasionally drops a chunk mid-burst.
# Device ids and MACs in it are made-up examples (checksums recomputed).
_LIVE = [bytes.fromhex(h) for h in [
    "ac000a000c0300010100040164050415010a8c5b",
    "ac010707065753005e00020711105653005e00ae",
    "ac0202a60a0100290104030000070d1102005e69",
    "ac030053560100290104031104001e0f0f12079a",
    "ac04ff640000800f00231000000080000000808f",
    "ac0500000080000000804102020130020000a57e",
    "ac06110164ffae5464ffae5464ffae5464ffaeee",
    "ac0754a5110264ffae5464ffae5464ffae54644c",
    "ac08ffae54a5110364ffae5464ffae5464ffae23",
    "acff5464ffae54a5050464ffae540000000000a3",
]]


def test_live_burst_full() -> None:
    st = r.parse_status(_LIVE)
    assert (st.is_on, st.brightness, st.zone_power) == (False, 100, {0: False, 1: False})
    assert len(st.segments) == 13


def test_a_lost_terminator_gives_no_state() -> None:
    # WAS: the prefix's state. The app's read never completes (l() never runs), so nothing is applied.
    assert r.parse_status(_LIVE[:-1]) == r.StatusReply()
    assert r.burst_chunks(_LIVE[:-1])[1] is False


def test_a_gap_gives_no_state() -> None:
    gapped = _LIVE[:3] + _LIVE[4:]  # chunk 0x03 lost: the app waits for it, ignoring the rest
    assert r.parse_status(gapped) == r.StatusReply()
    assert r.reassemble(gapped) == b""
    assert r.burst_chunks(gapped)[0] == _LIVE[:3]   # the chunks the app took before the gap


def test_duplicate_chunks_collapse() -> None:
    st = r.parse_status(_LIVE[:5] + _LIVE[3:])
    assert len(st.segments) == 13
