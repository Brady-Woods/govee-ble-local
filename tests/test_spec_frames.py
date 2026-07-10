"""Round-trip the spec-conformant builders through the Kaitai-generated reader.

This proves each frame in ``spec_frames`` is genuinely conformant to
``spec/govee_ble.ksy`` (build → parse → assert fields), independent of any device
and independent of the library's own encoder. It is the offline foundation the
live H60A6 suite builds on.
"""
from __future__ import annotations

import pytest

pytest.importorskip("kaitaistruct", reason="Kaitai runtime not installed")

import spec_frames as sf  # noqa: E402

# Skip cleanly if the generated reader hasn't been produced (needs tools/gen_kaitai.sh).
gb = pytest.importorskip(
    "govee_ble_local._generated.govee_ble_frame", reason="run tools/gen_kaitai.sh to generate the Kaitai reader"
)
GoveeBleFrame = gb.GoveeBleFrame


def _checksum_ok(frame: bytes) -> bool:
    x = 0
    for b in frame[:19]:
        x ^= b
    return len(frame) == 20 and frame[19] == x


def _parse(frame: bytes) -> GoveeBleFrame:
    assert len(frame) == 20
    assert _checksum_ok(frame)
    return GoveeBleFrame.from_bytes(frame)


def test_power():
    f = _parse(sf.power(True))
    assert f.pro_type == GoveeBleFrame.ProType.write
    assert f.body.command == GoveeBleFrame.Command.switch
    assert f.body.params.state == 0x01
    assert _parse(sf.power(False)).body.params.state == 0x00


def test_brightness():
    f = _parse(sf.brightness(50))
    assert f.body.command == GoveeBleFrame.Command.brightness
    assert f.body.params.level == 50
    assert _parse(sf.brightness(999)).body.params.level == 100  # clamped


def test_zone_power():
    f = _parse(sf.zone_power(1, True))
    assert f.body.command == GoveeBleFrame.Command.light_direction_or_zone
    assert f.pro_type == GoveeBleFrame.ProType.write
    # zone/state live in the raw params (no dedicated payload type for 0x30)
    assert bytes(f.body.params)[:2] == b"\x01\x01"


def test_plug_sync_time_signed_tz():
    f = _parse(sf.plug_sync_time(0x6A49901A, tz_hour=-7, tz_min=0))
    assert f.body.command == GoveeBleFrame.Command.plug_sync_time
    p = f.body.params
    assert p.unix_seconds == 0x6A49901A
    assert p.marker == 0x01
    assert p.tz_hour == -7          # s1: signed
    assert p.tz_min == 0
    # half-hour zone signs both bytes
    p2 = _parse(sf.plug_sync_time(0x6A49901A, -2, -30)).body.params
    assert (p2.tz_hour, p2.tz_min) == (-2, -30)


def test_scene_activate_little_endian():
    f = _parse(sf.scene_activate(0x4AD6))  # 19158
    m = f.body.params
    assert f.body.command == GoveeBleFrame.Command.mode
    assert m.sub_type == GoveeBleFrame.SubMode.scene
    assert m.params.effect == 0x4AD6      # u2le round-trips


def test_color_rgb_op15_h60a6_variant():
    mask = sf.all_segments_mask(13)       # 0x1FFF -> ff 1f
    f = _parse(sf.color_rgb_15(0x11, 0x22, 0x33, mask))
    m = f.body.params
    assert m.sub_type == GoveeBleFrame.SubMode.color_rgbic_15
    assert m.params.op_type == GoveeBleFrame.Op15.set_color
    d = m.params.data
    assert (d.r, d.g, d.b) == (0x11, 0x22, 0x33)
    trailer = d.trailer
    assert trailer[:5] == b"\x00\x00\x00\x00\x00"      # H60A6 RGB: 5 zeros...
    assert trailer[5:7] == bytes([mask & 0xFF, mask >> 8])  # ...then seg_mask (u2le)


def test_color_temp_op15_cct_variant():
    mask = sf.all_segments_mask(13)
    f = _parse(sf.color_temp_15(4000, (0, 0, 0), mask))
    d = f.body.params.params.data
    assert (d.r, d.g, d.b) == (0xFF, 0xFF, 0xFF)         # white point
    t = d.trailer
    assert t[0] << 8 | t[1] == 4000                      # kelvin u2be
    assert t[2:5] == b"\x00\x00\x00"                     # tint (0,0,0 = out-of-table)
    assert t[5:7] == bytes([mask & 0xFF, mask >> 8])


def test_segment_brightness_op15():
    f = _parse(sf.segment_brightness_15(75, 0x0004))
    d = f.body.params.params
    assert d.op_type == GoveeBleFrame.Op15.set_brightness
    assert d.data.pct == 75
    assert d.data.seg_mask == 0x0004


def test_status_query():
    short = _parse(sf.status_query(False))
    assert short.pro_type == GoveeBleFrame.ProType.multi_reply_read
    # multi_ac request = {command, count, requested_types[count]} (ksy Change 5)
    assert short.body.count == 2
    assert list(short.body.requested_types) == [0x41, 0x30]
    full = _parse(sf.status_query(True))
    assert full.body.count == 3
    assert list(full.body.requested_types) == [0x41, 0x30, 0xA5]


def test_scene_upload_a3_start_carries_comm_byte():
    # Corrected model (B1 + hardware): a3_start byte 4 = comm_byte = the H60A6 DIY/graffiti
    # device protocol code (0x58 = 88) — NOT a legacy comType, NOT value[0]|0x08. The value
    # is the blob after the consumed 0x50 header; the terminator (seq 0xFF) is data-bearing.
    param = (
        "UCABAQEBAAGyAAOlAAYACgAA/zoACwwNDjc4OTo7HgBW/kEBCg8QERITMjM0NTY8PT4/"
        "QFlaW1xdXl9gYXR1dncPAKz+SGJzeHl6e3x9fn+AgYKDhCYA3/tFAgMICRQVFhcYGSwt"
        "Li8wMUFCQ0RFRlNUVVZXWGNkZWZnbm9wcXIeAO/2NwQHGhscHR4nKCkqK0dISUpLTE1O"
        "T1BRUmhpamtsbQoA//IpBQYfICEiIyQlJgIAZAEQJwAAAAA="
    )
    frames = sf.scene_upload(param, comm_byte=sf.COMM_H60A6)

    start = _parse(frames[0])
    assert start.pro_type == GoveeBleFrame.ProType.multi_write_v1
    assert start.body.seq_no == 0x00
    a = start.body.frame            # a3_start
    assert a.marker == 0x01
    assert a.comm_byte == sf.COMM_H60A6            # 0x58 device code at frame byte 4
    assert a.packet_count == len(frames)
    # terminator is the last, data-bearing frame (seq 0xFF), not a separate empty frame
    assert frames[-1][1] == 0xFF
