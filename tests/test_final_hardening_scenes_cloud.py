"""T11.2 — final re-audit fixes: scene param validation (N-4/N-5).

The cloud hardening tests (N-5/N-7/N-8) live in test_cloud_account_extra.py, behind the
aiohttp optional-dependency gate, so a missing cloud extra no longer skips these.
"""
from __future__ import annotations

import base64

import pytest

from govee_ble_local import scenes
from govee_ble_local.const import FRAME_LEN
from govee_ble_local.devices.profile import SceneDialect
from govee_ble_local.exceptions import GoveeBleError, GoveeBleInvalidValue
from govee_ble_local.scenes import Scene, load_scenes, scene_upload_frames
from govee_ble_local.wire import build

_BAD_B64 = "!!not*base64!!"


def _scene(param: str, st: int = 1) -> Scene:
    return Scene("Sunset", 77, param, scene_type=st)


@pytest.mark.parametrize(
    ("dialect", "st"), [(None, 1), ("B_h60a6", 5), ("B_h6052", 5)]
)
def test_scene_upload_frames_bad_base64_is_ble_error(dialect: SceneDialect | None, st: int) -> None:
    # WAS: bare binascii.Error (a ValueError, not GoveeBleError) escaped.
    with pytest.raises(GoveeBleInvalidValue, match="77") as ei:
        scene_upload_frames(_scene(_BAD_B64, st), dialect, frozenset({1, 2, 3, 6}))
    assert _BAD_B64 not in str(ei.value)
    assert isinstance(ei.value, GoveeBleError)


def test_scene_upload_frames_oversized_payload_is_ble_error() -> None:
    # WAS: bare ValueError('scene payload too large ...') from build.scene_upload_a3. A type-1 blob the
    # app would upload can't get that large (at most 181 packets), so this is a type-2 rgbic blob of 30
    # 255-byte effects that ScenesRgbIC.isValidProtocolBytes accepts: 452 packets.
    big = base64.b64encode(bytes([30]) + (b"\xff" + bytes(255)) * 30).decode()
    with pytest.raises(GoveeBleInvalidValue, match="77") as ei:
        scene_upload_frames(_scene(big, 2), None, frozenset({2}))
    assert big[:20] not in str(ei.value)


def test_build_scene_upload_a3_too_large_is_invalid_value() -> None:
    with pytest.raises(GoveeBleInvalidValue):
        build.scene_upload_a3(bytes(20 * 300), 1)


def test_catalog_with_bad_param_is_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Res:
        def __truediv__(self, _n: str) -> _Res:
            return self

        def read_text(self, encoding: str | None = None) -> str:
            return '{"a": {"code": 3, "param": "!!bad!!"}}'

    scenes._catalog.cache_clear()
    monkeypatch.setattr(scenes.resources, "files", lambda _pkg: _Res())
    try:
        with pytest.raises(GoveeBleError, match="scene catalog for H1234 is malformed") as ei:
            load_scenes("H1234")
        assert "!!bad!!" not in str(ei.value)
    finally:
        scenes._catalog.cache_clear()


def test_frame_len_literals_unchanged() -> None:
    assert len(build.switch(True)) == FRAME_LEN
    from govee_ble_local.transport import handshake

    assert len(handshake._build_plain(1, b"x" * 16)) == FRAME_LEN
