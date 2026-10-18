"""Round-trip the spec-conformant builders through the Kaitai-generated reader.

This proves each frame in ``spec_frames`` is genuinely conformant to
the spec's ``govee_ble_frame`` (build → parse → assert fields), independent of any device
and independent of the library's own encoder. It is the offline foundation the
live H60A6 suite builds on.
"""
from __future__ import annotations

import base64

from typing import Any

from conftest import require_optional

require_optional("kaitaistruct", reason="Kaitai runtime not installed")

import spec_frames as sf  # noqa: E402

from govee_ble_local.wire import _spec  # noqa: E402

C = _spec.Common
F = _spec.Frame


def _checksum_ok(frame: bytes) -> bool:
    x = 0
    for b in frame[:19]:
        x ^= b
    return len(frame) == 20 and frame[19] == x


def _parse(frame: bytes, **facts: Any) -> Any:
    """``frame`` read on its TX route under a profile of ``facts`` (the device facts that type it)."""
    assert len(frame) == 20
    assert _checksum_ok(frame)
    return _spec.read_frame(frame, _spec.TX, _spec.profile(**facts))


def test_power():
    f = _parse(sf.power(True), f_01=int(C.Form01.light))
    assert f.pro_type == C.ProType.write
    assert f.body.command == C.Command.power
    assert f.body.params.body.state == 0x01
    assert _parse(sf.power(False), f_01=int(C.Form01.light)).body.params.body.state == 0x00


def test_brightness():
    f = _parse(sf.brightness(50))
    assert f.body.command == C.Command.brightness
    assert f.body.params.level == 50
    assert _parse(sf.brightness(999)).body.params.level == 100  # clamped


def test_zone_power():
    f = _parse(sf.zone_power(1, True), f_30=int(C.Form30.zone_power))
    assert f.body.command == C.Command.cmd_30
    assert f.pro_type == C.ProType.write
    z = f.body.params.body   # zone_power_write: [zone][state]
    assert (z.zone, z.state) == (1, 1)


def test_plug_sync_time_signed_tz():
    f = _parse(sf.plug_sync_time(0x6A49901A, tz_hour=-7, tz_min=0))
    assert f.body.command == C.Command.plug_sync_time
    p = f.body.params
    assert p.unix_seconds == 0x6A49901A
    assert p.marker == 0x01 and p.marker_is_valid
    assert p.tz_hour == -7          # s1: signed
    assert p.tz_min == 0
    # half-hour zone signs both bytes
    p2 = _parse(sf.plug_sync_time(0x6A49901A, -2, -30)).body.params
    assert (p2.tz_hour, p2.tz_min) == (-2, -30)


def test_scene_activate_little_endian():
    f = _parse(sf.scene_activate(0x4AD6))  # 19158
    m = f.body.params
    assert f.body.command == C.Command.mode
    assert m.sub_mode == C.SubMode.scene
    assert m.params.effect == 0x4AD6      # u2le round-trips


_H60A6 = {"f_15_01": int(C.Form1501.kelvin_tint), "num_segments": 16}   # 13 segments: a 16-bit posSet


def test_color_rgb_op15_h60a6_variant():
    mask = sf.all_segments_mask(13)       # 0x1FFF -> ff 1f
    f = _parse(sf.color_rgb_15(0x11, 0x22, 0x33, mask), **_H60A6)
    m = f.body.params
    assert m.sub_mode == C.SubMode.color_rgbic_15
    assert m.params.op == C.Op15.set_color
    d = m.params.data   # op15_color form kelvin_tint: RGB is kelvin 0 and tint 0 (H60A6 RGB: 5 zeros)
    assert (d.color.r, d.color.g, d.color.b) == (0x11, 0x22, 0x33)
    assert d.kelvin == 0 and (d.tint.r, d.tint.g, d.tint.b) == (0, 0, 0) and not d.is_cct
    assert [i for i, bit in enumerate(d.mask.bits) if bit] == list(range(13))   # ...then seg_mask (u2le)


def test_color_temp_op15_cct_variant():
    mask = sf.all_segments_mask(13)
    f = _parse(sf.color_temp_15(4000, (0, 0, 0), mask), **_H60A6)
    d = f.body.params.params.data
    assert (d.color.r, d.color.g, d.color.b) == (0xFF, 0xFF, 0xFF)   # white point
    assert d.kelvin == 4000 and d.is_cct                             # kelvin u2be
    assert (d.tint.r, d.tint.g, d.tint.b) == (0, 0, 0)               # tint (0,0,0 = out-of-table)
    assert [i for i, bit in enumerate(d.mask.bits) if bit] == list(range(13))


def test_segment_brightness_op15():
    f = _parse(sf.segment_brightness_15(75, 0x0004), num_segments=16)
    d = f.body.params.params
    assert d.op == C.Op15.set_brightness
    assert d.data.pct == 75
    assert [i for i, bit in enumerate(d.data.mask.bits) if bit] == [2]


def test_status_query():
    short = _parse(sf.status_query(False))
    assert short.pro_type == C.ProType.status_read
    # status_request = [command][status_read_args: num_types, types[num_types]]
    assert short.body.command == F.StatusCommand.status_read
    assert short.body.args.num_types == 2
    assert list(short.body.args.types) == [0x41, 0x30]
    full = _parse(sf.status_query(True))
    assert full.body.args.num_types == 3
    assert list(full.body.args.types) == [0x41, 0x30, 0xA5]


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
    assert start.pro_type == C.ProType.multi_write_v1
    assert start.body.index == 0x00
    a = start.body.start            # a3_start
    assert a.marker == F.A3Marker.v1
    assert a.comm_byte == sf.COMM_H60A6            # 0x58 device code at frame byte 4
    assert a.num_packets == len(frames)
    # this value is longer than START's 14 bytes, so the last frame (seq 0xFF) carries its final chunk
    assert frames[-1][1] == 0xFF
    value = base64.b64decode(param)[1:]
    stream = b"".join(f[2:19] for f in frames)
    assert stream[3 : 3 + len(value)] == value and len(stream) - 3 - len(value) < 17


def test_scene_upload_a3_short_value_is_start_plus_empty_end():
    # makeSendBytesV2: a value that fits in START (<= 14 bytes with a one-byte commBytes) is still
    # two frames, packet_count 2, followed by an empty 0xFF END.
    frames = sf.make_send_bytes_v2(sf.PRO_MULTI_WRITE_V1, bytes([sf.MULTI_V1]), bytes(range(1, 15)))
    assert len(frames) == 2
    start = _parse(frames[0])
    assert start.body.index == 0x00
    assert start.body.start.marker == F.A3Marker.v1 and start.body.start.num_packets == 2
    assert start.body.start.comm_byte == sf.MULTI_V1
    assert frames[0][5:19] == bytes(range(1, 15))
    assert _checksum_ok(frames[1]) and frames[1][:2] == b"\xa3\xff" and not any(frames[1][2:19])
