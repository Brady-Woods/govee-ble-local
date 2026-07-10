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


def test_tlv_walk_stops_on_padding() -> None:
    # a minimal buffer: switch on, brightness 60, then zero pad
    buf = bytes([0x01, 1, 1, 0x04, 1, 60, 0, 0, 0, 0])
    got = dict(r.walk_tlvs(buf))
    assert got[0x01] == b"\x01" and got[0x04] == bytes([60])
    assert 0x00 not in got  # padding terminates the walk


def test_empty_and_short() -> None:
    assert r.parse_status([]).segments == []
    assert r.parse_status([b"\x00" * 4]).is_on is None
