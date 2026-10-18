"""wire.build — every builder round-trips through the generated Kaitai reader
(build -> parse -> assert), proving the write side conforms to the spec's ``govee_ble_frame``: each frame
is read back on its TX route under a profile of the device facts that type it."""
from __future__ import annotations

from typing import Any

import pytest

from conftest import require_optional

require_optional("kaitaistruct", reason="Kaitai runtime not installed")

from govee_ble_local.exceptions import GoveeBleNotSupported  # noqa: E402
from govee_ble_local.wire import _spec  # noqa: E402
from govee_ble_local.wire import build as b  # noqa: E402

C = _spec.Common
F = _spec.Frame


def _p(frame: bytes, **facts: Any) -> Any:   # a Kaitai-generated GoveeBleFrame (untyped)
    assert len(frame) == 20
    x = 0
    for c in frame[:19]:
        x ^= c
    assert frame[19] == x, "BCC"
    return _spec.read_frame(frame, _spec.TX, _spec.profile(**facts))


def test_switch() -> None:
    light, relays = int(C.Form01.light), int(C.Form01.relays)
    assert _p(b.switch(True), f_01=light).body.params.body.state == 0x01
    assert _p(b.switch(False), f_01=light).body.params.body.state == 0x00
    assert _p(b.switch(True, relay=True), f_01=relays).body.params.body.state == 0x11
    assert _p(b.switch(False, relay=True), f_01=relays).body.params.body.state == 0x10


def test_brightness() -> None:
    assert _p(b.brightness(50)).body.params.level == 50
    assert _p(b.brightness(999)).body.params.level == 100  # clamped


def test_color_rgb_schemes() -> None:
    m = _p(b.color_rgb(0x11, 0x22, 0x33, "h60a6", 13), f_15_01=int(C.Form1501.kelvin_tint),
           num_segments=16).body.params
    assert m.sub_mode == C.SubMode.color_rgbic_15
    assert m.params.op == C.Op15.set_color
    data = m.params.data
    assert (data.color.r, data.color.g, data.color.b) == (0x11, 0x22, 0x33) and data.kelvin == 0
    m = _p(b.color_rgb(0x11, 0x22, 0x33, "h6006", 13)).body.params
    assert m.sub_mode == C.SubMode.color_cct_0d
    assert (m.params.color.r, m.params.color.g, m.params.color.b) == (0x11, 0x22, 0x33)
    assert m.params.kelvin == 0
    m = _p(b.color_rgb(0x11, 0x22, 0x33, "h61a8", 15), f_0b=int(C.Form0b.rgb_mask),
           num_segments=16).body.params
    assert m.sub_mode == C.SubMode.color_rgbic_0b
    color = m.params.body.color
    assert (color.r, color.g, color.b) == (0x11, 0x22, 0x33)
    assert m.params.body.mask.bits[:16] == [True] * 15 + [False]


def test_color_temp() -> None:
    m = _p(b.color_temp(4000, "h6006", 13)).body.params
    # both-slot form: tint in the colour slot (Config.bu -> Bytes.p), not white
    assert (m.params.color.r, m.params.color.g, m.params.color.b) == (0xFF, 0xD5, 0xA1)
    assert m.params.kelvin == 4000
    m = _p(b.color_temp(2700, "h60a6", 13), f_15_01=int(C.Form1501.kelvin_tint),
           num_segments=16).body.params
    assert m.sub_mode == C.SubMode.color_rgbic_15 and m.params.op == C.Op15.set_color
    assert m.params.data.kelvin == 2700 and m.params.data.is_cct
    with pytest.raises(GoveeBleNotSupported):
        b.color_temp(4000, "h61a8", 15)


def test_segment_color_temp_carries_mask() -> None:
    # masked CCT (0x15 family): same layout as color_temp but an arbitrary seg mask
    f = b.segment_color_temp(0x0007, 3000, "h60a6")
    assert f[:4].hex() == "33051501"                 # mode / 0x15 / set-color
    assert (f[4], f[5], f[6]) == (0xFF, 0xFF, 0xFF)  # WHITE slot
    assert (f[7], f[8]) == (0x0B, 0xB8)              # kelvin 3000, u2be
    assert (f[9], f[10], f[11]) == (0xFF, 0xB9, 0x69)  # tint for 3000 K (Constant.getTemColorByKelvin)
    assert (f[12], f[13]) == (0x07, 0x00)            # seg mask {0,1,2}, little-endian
    # whole-device color_temp is just the all-segments mask on the same builder
    assert b.color_temp(3000, "h60a6", 13) == b.segment_color_temp((1 << 13) - 1, 3000, "h60a6")
    # non-0x15 schemes can't mask CCT
    with pytest.raises(GoveeBleNotSupported):
        b.segment_color_temp(0x1, 3000, "h6006")
    with pytest.raises(GoveeBleNotSupported):
        b.segment_color_temp(0x1, 3000, "h61a8")


def test_segment_and_zone_and_bar() -> None:
    m = _p(b.segment_brightness(0x0004, 75, "h60a6"), num_segments=16).body.params
    assert m.params.op == C.Op15.set_brightness
    assert m.params.data.pct == 75
    assert [i for i, bit in enumerate(m.params.data.mask.bits) if bit] == [2]
    with pytest.raises(GoveeBleNotSupported):
        b.segment_brightness(0x1, 50, "h61a8")
    z = _p(b.zone_power(1, True), f_30=int(C.Form30.zone_power))
    assert z.body.command == C.Command.cmd_30
    assert (z.body.params.body.zone, z.body.params.body.state) == (1, 1)
    bar = _p(b.bar_switch(True, False), f_36=int(C.Form36.bar_pair))
    assert bar.body.command == C.Command.cmd_36
    assert (bar.body.params.body.left, bar.body.params.body.right) == (1, 0)


def test_gradual() -> None:
    assert b.gradual(True)[:3].hex() == "33a301"
    assert b.gradual(False)[:3].hex() == "33a300"
    assert b.gradual_query()[:2].hex() == "aaa3"
    assert _p(b.gradual(True)).body.params.state == 1


def test_scene_activate_and_plug_and_secret() -> None:
    s = _p(b.scene_activate(0x4A82)).body.params
    assert s.sub_mode == C.SubMode.scene and s.params.effect == 0x4A82
    p = _p(b.plug_sync_time(0x6A49901A, -7, 0)).body
    assert p.command == C.Command.plug_sync_time
    assert p.params.unix_seconds == 0x6A49901A and p.params.tz_hour == -7  # signed
    sec = _p(b.secret_check(bytes(range(1, 9)))).body
    assert sec.command == C.Command.secret_write and sec.params.secret == bytes(range(1, 9))


def test_queries() -> None:
    assert _p(b.power_query()).pro_type == C.ProType.read
    mq = _p(b.mode_query())
    assert mq.pro_type == C.ProType.read and mq.body.selector.selector == 1
    sq = _p(b.status_query(full=True))
    assert sq.pro_type == C.ProType.status_read
    assert list(sq.body.args.types) == [0x41, 0x30, 0xA5]


def test_ic_count_query() -> None:
    assert b.ic_count_query().hex().startswith("aa40")
    assert _p(b.ic_count_query()).body.command == C.Command.ic_num


# ── scene uploads: reassemble back to the input value ────────────────────────
def _read_any(frame: bytes) -> Any:
    return _spec.read_frame(frame, _spec.TX, _spec.PLAIN)


def _reassemble_a4(frames: list[bytes]) -> bytes:
    parsed = [_read_any(f) for f in frames]
    start = [p for p in parsed if p.body.index == 0]
    end = [p for p in parsed if p.body.index == 0xFFFF]
    mids = sorted((p for p in parsed if p.body.index not in (0, 0xFFFF)), key=lambda p: p.body.index)
    return b"".join(p.body.chunk for p in (start + mids + end))


def test_scene_upload_a3_start_fields() -> None:
    value = bytes(range(40))
    frames = b.scene_upload_a3(value, b.COMM_H60A6)
    start = _p(frames[0])
    assert start.pro_type == C.ProType.multi_write_v1
    assert start.body.index == 0x00
    assert start.body.start.marker == F.A3Marker.v1
    assert start.body.start.comm_byte == b.COMM_H60A6
    assert start.body.start.num_packets == len(frames)
    assert frames[-1][1] == 0xFF  # data-bearing terminator (the value is longer than START's 14 bytes)


def test_scene_upload_a3_short_value_parses_as_start_then_end() -> None:
    # makeSendBytesV2: a value that fits in START still gets the separate (empty) 0xFF END.
    frames = b.scene_upload_a3(bytes(range(1, 11)), 1)
    assert len(frames) == 2
    start = _p(frames[0])
    assert start.pro_type == C.ProType.multi_write_v1 and start.body.index == 0x00
    assert start.body.start.marker == F.A3Marker.v1 and start.body.start.num_packets == 2
    assert start.body.start.comm_byte == 1
    end = _p(frames[1])
    assert end.pro_type == C.ProType.multi_write_v1 and end.body.index == 0xFF
    assert not any(frames[1][2:19])


def test_scene_upload_a4_roundtrips() -> None:
    value = bytes([0x20, 0x00]) + bytes(range(256))[:185]  # 187 B graffiti-shaped
    frames = b.scene_upload_a4_mtu(value, b.COMM_H60A6)
    assert len(frames) == 12
    start = _read_any(frames[0])
    assert start.pro_type == C.ProType.multi_write_v2
    assert start.body.start.comm_byte == b.COMM_H60A6
    assert start.body.start.num_packets == 12
    assert _reassemble_a4(frames) == value
