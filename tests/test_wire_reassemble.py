"""wire.reassemble — de-chunk a 0xAC status burst + walk the TLV reply.

Validated against a real captured H60A6 burst (13 segments, both zones on, brightness 80)."""
from __future__ import annotations

from govee_ble_local.wire import reassemble as r

# Real captured H60A6 0xAC status burst (segments set to a cycling R/G/B pattern).
_BURST = [
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
    assert "".join(names.get(s.rgb, "?") for s in st.segments) == "RGBRGBRGBRGBR"
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


def test_out_of_order_chunks_reassemble_by_index() -> None:
    frames = [bytes.fromhex(h) for h in _BURST]
    shuffled = [frames[3], frames[0], frames[-1], frames[1], frames[2], *frames[4:-1]]
    assert r.reassemble(shuffled) == r.reassemble(frames)


def test_parse_status_stops_on_padding() -> None:
    # the generated status_reply reader terminates on the trailing zero pad (repeat-until
    # type==0) — a burst padded to the frame boundary parses cleanly to just the real TLVs.
    frames = [bytes.fromhex(h) for h in _BURST]
    st = r.parse_status(frames)
    assert st.is_on is True and st.brightness == 0x50   # no throw, no phantom TLVs from the pad


def test_parse_status_extracts_device_info() -> None:
    # BLE-only devices (H60A6) report device-info ONLY in the 0xAC stream, via the 0x07 TLV
    # (parsed by the generated device_info_read reader — no MAC-anchor heuristic, no address).
    st = r.parse_status([bytes.fromhex(h) for h in _BURST])
    assert st.wifi_mac == "5C:E7:53:F4:74:56"       # 0x11 wifi TLV
    assert st.hardware_version == "1.04.03"          # matches the old anchor's value
    assert st.firmware_version == "1.00.41"          # bonus vs the anchor (sw was not extracted before)
    assert st.serial_number == "2D:DB:5C:E7:53:F4:74:56"   # 0x10 basic uid
    assert not hasattr(r, "anchor_device_info")      # heuristic retired


def test_empty_and_short() -> None:
    assert r.parse_status([]).segments == []
    assert r.parse_status([b"\x00" * 4]).is_on is None
