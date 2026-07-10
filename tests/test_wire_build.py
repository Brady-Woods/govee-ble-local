"""wire.build — every builder round-trips through the generated Kaitai reader
(build -> parse -> assert), proving the write side conforms to spec/govee_ble.ksy."""
from __future__ import annotations

import pytest

pytest.importorskip("kaitaistruct", reason="Kaitai runtime not installed")
_gb = pytest.importorskip("govee_ble_local._generated.govee_ble_frame")
F = _gb.GoveeBleFrame

from govee_ble_local.wire import build as b  # noqa: E402


def _p(frame: bytes) -> object:
    assert len(frame) == 20
    x = 0
    for c in frame[:19]:
        x ^= c
    assert frame[19] == x, "BCC"
    return F.from_bytes(frame)


def test_switch() -> None:
    assert _p(b.switch(True)).body.params.state == 0x01
    assert _p(b.switch(False)).body.params.state == 0x00
    assert _p(b.switch(True, relay=True)).body.params.state == 0x11
    assert _p(b.switch(False, relay=True)).body.params.state == 0x10


def test_brightness() -> None:
    assert _p(b.brightness(50)).body.params.level == 50
    assert _p(b.brightness(999)).body.params.level == 100  # clamped


def test_color_rgb_schemes() -> None:
    m = _p(b.color_rgb(0x11, 0x22, 0x33, "h60a6", 13)).body.params
    assert m.sub_type == F.SubMode.color_rgbic_15
    assert m.params.op_type == F.Op15.set_color
    assert (m.params.data.r, m.params.data.g, m.params.data.b) == (0x11, 0x22, 0x33)
    m = _p(b.color_rgb(0x11, 0x22, 0x33, "h6006", 13)).body.params
    assert m.sub_type == F.SubMode.color_cct_0d
    assert (m.params.r, m.params.g, m.params.b) == (0x11, 0x22, 0x33) and m.params.kelvin == 0
    m = _p(b.color_rgb(0x11, 0x22, 0x33, "h61a8", 15)).body.params
    assert m.sub_type == F.SubMode.color_rgbic_0b
    assert (m.params.r, m.params.g, m.params.b) == (0x11, 0x22, 0x33)


def test_color_temp() -> None:
    m = _p(b.color_temp(4000, "h6006", 13)).body.params
    assert (m.params.r, m.params.g, m.params.b) == (0xFF, 0xFF, 0xFF) and m.params.kelvin == 4000
    m = _p(b.color_temp(2700, "h60a6", 13)).body.params
    assert m.sub_type == F.SubMode.color_rgbic_15 and m.params.op_type == F.Op15.set_color
    with pytest.raises(ValueError):
        b.color_temp(4000, "h61a8", 15)


def test_segment_color_temp_carries_mask() -> None:
    # masked CCT (0x15 family): same layout as color_temp but an arbitrary seg mask
    f = b.segment_color_temp(0x0007, 3000, "h60a6")
    assert f[:4].hex() == "33051501"                 # mode / 0x15 / set-color
    assert (f[4], f[5], f[6]) == (0xFF, 0xFF, 0xFF)  # WHITE slot
    assert (f[7], f[8]) == (0x0B, 0xB8)              # kelvin 3000, u2be
    assert (f[12], f[13]) == (0x07, 0x00)            # seg mask {0,1,2}, little-endian
    # whole-device color_temp is just the all-segments mask on the same builder
    assert b.color_temp(3000, "h60a6", 13) == b.segment_color_temp((1 << 13) - 1, 3000, "h60a6")
    # non-0x15 schemes can't mask CCT
    with pytest.raises(ValueError):
        b.segment_color_temp(0x1, 3000, "h6006")
    with pytest.raises(ValueError):
        b.segment_color_temp(0x1, 3000, "h61a8")


def test_segment_and_zone_and_bar() -> None:
    m = _p(b.segment_brightness(0x0004, 75, "h60a6")).body.params
    assert m.params.op_type == F.Op15.set_brightness
    assert m.params.data.pct == 75 and m.params.data.seg_mask == 0x0004
    with pytest.raises(ValueError):
        b.segment_brightness(0x1, 50, "h61a8")
    z = _p(b.zone_power(1, True))
    assert z.body.command == F.Command.light_direction_or_zone
    assert bytes(z.body.params)[:2] == b"\x01\x01"
    bar = _p(b.bar_switch(True, False))
    assert bar.body.command == F.Command.compose_light_switch
    assert bytes(bar.body.params)[:2] == b"\x01\x00"


def test_scene_activate_and_plug_and_secret() -> None:
    s = _p(b.scene_activate(0x4A82)).body.params
    assert s.sub_type == F.SubMode.scene and s.params.effect == 0x4A82
    p = _p(b.plug_sync_time(0x6A49901A, -7, 0)).body
    assert p.command == F.Command.plug_sync_time
    assert p.params.unix_seconds == 0x6A49901A and p.params.tz_hour == -7  # signed
    sec = _p(b.secret_check(bytes(range(1, 9)))).body
    assert sec.command == F.Command.secret_write and bytes(sec.params)[:8] == bytes(range(1, 9))


def test_queries() -> None:
    assert _p(b.power_query()).pro_type == F.ProType.read
    assert _p(b.mode_query()).pro_type == F.ProType.read
    sq = _p(b.status_query(True))
    assert sq.pro_type == F.ProType.multi_reply_read
    assert list(sq.body.requested_types) == [0x41, 0x30, 0xA5]


# ── scene uploads: reassemble back to the input value ────────────────────────
def _reassemble_a4(frames: list[bytes]) -> bytes:
    parsed = [F.from_bytes(f) for f in frames]
    start = [p for p in parsed if p.body.seq_marker == 0]
    end = [p for p in parsed if p.body.seq_marker == 0xFFFF]
    mids = sorted((p for p in parsed if p.body.seq_marker not in (0, 0xFFFF)),
                  key=lambda p: p.body.seq_marker)
    return b"".join(p.body.value for p in (start + mids + end))


def test_scene_upload_a3_start_fields() -> None:
    value = bytes(range(40))
    frames = b.scene_upload_a3(value, b.COMM_H60A6)
    start = _p(frames[0])
    assert start.pro_type == F.ProType.multi_write_v1
    assert start.body.seq_no == 0x00
    assert start.body.frame.marker == 0x01
    assert start.body.frame.comm_byte == b.COMM_H60A6
    assert start.body.frame.packet_count == len(frames)
    assert frames[-1][1] == 0xFF  # data-bearing terminator


def test_scene_upload_a4_roundtrips() -> None:
    value = bytes([0x20, 0x00]) + bytes(range(256))[:185]  # 187 B graffiti-shaped
    frames = b.scene_upload_a4_mtu(value, b.COMM_H60A6)
    assert len(frames) == 12
    start = F.from_bytes(frames[0])
    assert start.pro_type == F.ProType.multi_write_v2
    assert start.body.start.comm_byte == b.COMM_H60A6
    assert start.body.start.packet_count == 12
    assert _reassemble_a4(frames) == value
