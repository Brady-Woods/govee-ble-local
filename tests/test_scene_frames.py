"""Pure scene-upload planning (scenes.scene_upload_frames) and its Device delegates.

Expected frames come from the app's own writers ported in ``tests/spec_frames`` (``makeSendBytesV2``
for 0xA3, ``makeSendBytesMtu`` for 0xA4), not from ``wire.build``, so a framing bug can't hide."""
from __future__ import annotations

import base64

import pytest
import spec_frames as sf
from fake_ble import ble_device

from govee_ble_local.devices.device import make_device
from govee_ble_local.scenes import Scene, load_scenes, scene_upload_frames
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


# A graffiti value whose first two bytes (marker 0x20, bg red 0x00) also pass the DIY length gate
# (0x0020 + 2 == 34 bytes), but which no diy_value reads (bg_color_size 0xFF): the app's DIY parser fails
# and its graffiti parser takes it (H60A6DiyConfig order), so it goes out as 0xA4.
_GRAFFITI_WITH_DIY_LENGTH = bytes(
    [0x20, 0x00, 0x01, 0xFF, 0x64, 0x01, 0x01,           # marker, bg r g b, brightness, show_type, 1 layer
     0x19, 0x00,                                         # layer rec_len 25
     0x03, 0x16, 0x00, 0x01, 0x00,                       # graffiti_type, inner_len, 1 colour group
     0x05, 0x00, 0xFF, 0x00, 0x00, 0, 1, 2, 3, 4,        # 5 pixels of red
     0x01, 0x32, 0x64, 0x01, 0x0A, 0x00, 0, 0, 0, 0])    # action, speed, bg bri, priority, duration, pad


def _catalog_value(name: str) -> tuple[Scene, bytes]:
    scene = load_scenes("H60A6")[name]
    return scene, base64.b64decode(scene.param or "")[1:]


def test_h60a6_upload_follows_the_apps_parsers() -> None:
    # BaseSceneViewMode.r3: a 0x50 blob, then H60A6DiyParse (0xA3), else H60A6GraffitiParse (0xA4-MTU).
    christmas, diy = _catalog_value("Christmas")
    assert scene_upload_frames(christmas, "B_h60a6", frozenset()) == sf.make_send_bytes_v2(
        0xA3, bytes([build.COMM_H60A6]), diy)
    afternoon, graffiti = _catalog_value("Afternoon")
    assert scene_upload_frames(afternoon, "B_h60a6", frozenset()) == sf.make_send_bytes_mtu(
        0xA4, build.COMM_H60A6, graffiti, 20)
    tricky = Scene("g", 1, _b64(0x50, *_GRAFFITI_WITH_DIY_LENGTH), scene_type=5)
    assert scene_upload_frames(tricky, "B_h60a6", frozenset()) == sf.make_send_bytes_mtu(
        0xA4, build.COMM_H60A6, _GRAFFITI_WITH_DIY_LENGTH, 20)


def test_h60a6_upload_refuses_what_the_app_does_not_apply() -> None:
    _christmas, diy = _catalog_value("Christmas")
    # Decoded byte 0 is not 0x50: r3 refuses it ("效果参数异常"), the scene is only activated.
    for first in (0x00, 0x51, 0x13, 0xFF):
        assert scene_upload_frames(Scene("d", 1, _b64(first, *diy), scene_type=5), "B_h60a6",
                                   frozenset()) is None
    # Neither parser reads it (WAS: the DIY length gate alone sent it as 0xA3, or else 0xA4).
    for value in (bytes([3, 0, 7, 8, 9]), bytes([3, 0, 7, 8, 9, 10]), diy[:-1], b"\x01"):
        assert scene_upload_frames(Scene("d", 1, _b64(0x50, *value), scene_type=5), "B_h60a6",
                                   frozenset()) is None


def test_h6052_graffiti_only_for_0x13() -> None:
    sc = Scene("g", 1, _b64(0x13, 5, 6), scene_type=5)
    assert scene_upload_frames(sc, "B_h6052", frozenset()) == sf.make_send_bytes_v2(
        0xA3, bytes([build.COMM_H6052_GRAFFITI]), bytes([5, 6]))
    assert scene_upload_frames(Scene("g", 1, _b64(0x12, 5), scene_type=5), "B_h6052", frozenset()) is None


def test_device_methods_delegate() -> None:
    d = make_device(ble_device("AA:BB:CC:DD:EE:40", "H60A6"), "H60A6")
    assert d is not None
    sc, _value = _catalog_value("Christmas")
    expect = scene_upload_frames(sc, d.profile.scene_dialect, d.profile.scene_versions)
    assert expect and d.scene_upload_frames(sc) == expect == d.scene_upload_frames(sc)


@pytest.mark.parametrize("mtu", [20, 64, 244, 509])
def test_h60a6_graffiti_frames_take_the_links_mtu(mtu: int) -> None:
    # H60A6GraffitiParse.makeBleController:240 -> Compose4DefWrite4Multi.d(88, bytes,
    # MtuConfig.getAvailableMtuSize(sku)): the 0xA4 frames are sized to the link (ATT MTU - 3).
    afternoon, graffiti = _catalog_value("Afternoon")
    assert scene_upload_frames(afternoon, "B_h60a6", frozenset(), mtu=mtu) == sf.make_send_bytes_mtu(
        0xA4, build.COMM_H60A6, graffiti, mtu)
    d = make_device(ble_device("AA:BB:CC:DD:EE:60", "H60A6"), "H60A6")
    assert d is not None and d.scene_upload_frames(afternoon, mtu=mtu) == scene_upload_frames(
        afternoon, "B_h60a6", frozenset({0, 1, 2, 3, 5}), mtu=mtu)
    # The 0xA3 DIY path (H60A6DiyParse:487, Compose4DefWrite4Multi.j) has fixed 20-byte frames.
    christmas, _diy = _catalog_value("Christmas")
    assert scene_upload_frames(christmas, "B_h60a6", frozenset(), mtu=mtu) == scene_upload_frames(
        christmas, "B_h60a6", frozenset())
