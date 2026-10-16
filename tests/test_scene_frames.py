"""Pure scene-upload planning (scenes.scene_upload_frames) and its Device delegates.

Expected frames come from the app's own writers ported in ``tests/spec_frames`` (``makeSendBytesV2``
for 0xA3, ``makeSendBytesMtu`` for 0xA4), not from ``wire.build``, so a framing bug can't hide."""
from __future__ import annotations

import base64

import spec_frames as sf
from fake_ble import ble_device

from govee_ble_local.devices.device import make_device
from govee_ble_local.scenes import Scene, scene_upload_frames
from govee_ble_local.wire import build


def _b64(*b: int) -> str:
    return base64.b64encode(bytes(b)).decode()


def test_no_param_is_activate_only() -> None:
    assert scene_upload_frames(Scene("x", 1, None, scene_type=1), "A", frozenset({1})) is None
    assert scene_upload_frames(Scene("x", 1, "", scene_type=1), "A", frozenset({1})) is None


def test_library_type_gated_by_versions() -> None:
    sc = Scene("x", 1, _b64(*range(10)), scene_type=1)
    assert scene_upload_frames(sc, "A", frozenset({2})) is None
    assert scene_upload_frames(sc, "A", frozenset({1})) == sf.make_send_bytes_v2(
        0xA3, b"\x01", bytes(range(10)))
    assert scene_upload_frames(Scene("x", 1, _b64(1), scene_type=0), "A", frozenset({0, 1})) is None


def test_h60a6_diy_length_gate() -> None:
    value = bytes([3, 0, 7, 8, 9])                     # u16le len 3, +2 == 5 -> DIY (a3)
    sc = Scene("d", 1, _b64(0xFF, *value), scene_type=5)
    assert scene_upload_frames(sc, "B_h60a6", frozenset()) == sf.make_send_bytes_v2(
        0xA3, bytes([build.COMM_H60A6]), value)
    long = bytes([3, 0, 7, 8, 9, 10])                  # length mismatch -> a4 MTU
    sc2 = Scene("d", 1, _b64(0xFF, *long), scene_type=5)
    expected = sf.make_send_bytes_mtu(0xA4, build.COMM_H60A6, long, 20)
    assert scene_upload_frames(sc2, "B_h60a6", frozenset()) == expected
    assert scene_upload_frames(Scene("d", 1, _b64(0xFF, 1), scene_type=5), "B_h60a6", frozenset()) is None


def test_h6052_graffiti_only_for_0x13() -> None:
    sc = Scene("g", 1, _b64(0x13, 5, 6), scene_type=5)
    assert scene_upload_frames(sc, "B_h6052", frozenset()) == sf.make_send_bytes_v2(
        0xA3, bytes([build.COMM_H6052_GRAFFITI]), bytes([5, 6]))
    assert scene_upload_frames(Scene("g", 1, _b64(0x12, 5), scene_type=5), "B_h6052", frozenset()) is None


def test_device_methods_delegate() -> None:
    d = make_device(ble_device("AA:BB:CC:DD:EE:40", "H60A6"), "H60A6")
    assert d is not None
    sc = Scene("d", 1, _b64(0xFF, 3, 0, 7, 8, 9), scene_type=5)
    expect = scene_upload_frames(sc, d.profile.scene_dialect, d.profile.scene_versions)
    assert expect and d.scene_upload_frames(sc) == expect == d.scene_upload_frames(sc)
