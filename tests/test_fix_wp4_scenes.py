"""Regression tests for the scenes review fixes: compose (sceneType 6) gated on the blob's
first byte like ScenesOp.o, typed malformed-param re-raise, cached reverse index."""
from __future__ import annotations

import base64

import pytest

from govee_ble_local import scenes
from govee_ble_local.exceptions import GoveeBleInvalidValue
from govee_ble_local.scenes import Scene, scene_names_by_code, scene_upload_frames, scene_upload_params
from govee_ble_local.wire import build


def _b64(*data: int) -> str:
    return base64.b64encode(bytes(data)).decode()


def _compose(*blob: int) -> Scene:
    return Scene("Compose", 42, _b64(*blob), scene_type=6)


# -- compose: ScenesOp.o -> ShareDiy.isValidComposeBytes (bytes[0] == 10) -> V5, comType 10 --

def test_compose_0x0a_blob_uploads_v5_comtype_10_strip_1() -> None:
    sc = _compose(0x0A, 1, 2, 3, 4)
    assert scene_upload_frames(sc, "A", frozenset({6})) == build.scene_upload_a3(bytes([1, 2, 3, 4]), 10)


@pytest.mark.parametrize("first", [0x51, 0x00, 0x13, 0xFF])
def test_compose_other_first_byte_is_activate_only(first: int) -> None:
    # 0x51 = MultiNewScenesControllerH60B0 (commByte 90, not modelled); others: no controller.
    assert scene_upload_frames(_compose(first, 1, 2, 3), "A", frozenset({6})) is None


def test_compose_needs_scene_version_6() -> None:
    assert scene_upload_frames(_compose(0x0A, 1, 2), "A", frozenset({1, 2, 3, 5})) is None
    assert scene_upload_params(6, frozenset({6})) == (10, 1)
    assert scene_upload_params(6, frozenset({5})) is None


def test_scene_type_enum_and_read_only_table() -> None:
    assert [int(t) for t in scenes._SceneType] == [0, 1, 2, 3, 4, 5, 6]
    assert scenes._SceneType.DIY == 5
    with pytest.raises(TypeError):
        scenes._LIBRARY_SCENE[9] = (9, 9, 9)  # type: ignore[index]


# -- malformed-param re-raise keys on the type, not the message ----------------------------

def test_build_error_mentioning_the_phrase_is_still_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_value: bytes, _comm: int) -> list[bytes]:
        raise GoveeBleInvalidValue("payload has a malformed effect parameter-ish length")

    monkeypatch.setattr(scenes.build, "scene_upload_a3", boom)
    with pytest.raises(GoveeBleInvalidValue, match=r"\(code 7\) cannot be uploaded"):
        scene_upload_frames(Scene("x", 7, _b64(0x83, 0, 0), scene_type=1), "A", frozenset({1}))


def test_bad_base64_is_not_double_wrapped() -> None:
    with pytest.raises(GoveeBleInvalidValue) as ei:
        scene_upload_frames(Scene("x", 7, "!!bad!!", scene_type=1), "A", frozenset({1}))
    assert "malformed effect parameter" in str(ei.value) and "cannot be uploaded" not in str(ei.value)
    assert isinstance(ei.value, scenes._MalformedSceneParam)


# -- cached reverse index --------------------------------------------------------------------

class _Res:
    def __init__(self, text: str) -> None:
        self._text = text

    def __truediv__(self, _name: str) -> _Res:
        return self

    def read_text(self, encoding: str | None = None) -> str:
        return self._text


def test_names_by_code_cached_fresh_and_follows_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    scenes._catalog.cache_clear()
    try:
        monkeypatch.setattr(scenes.resources, "files", lambda _pkg: _Res('{"a": {"code": 3}}'))
        first = scene_names_by_code("H4321")
        index = scenes._NAMES_BY_CODE["H4321"][1]
        first.clear()                                           # caller copy: cache unaffected
        assert scene_names_by_code("h4321") == {3: "a"}
        assert scenes._NAMES_BY_CODE["H4321"][1] is index       # not rebuilt
        scenes._catalog.cache_clear()                           # a new catalog -> new index
        monkeypatch.setattr(scenes.resources, "files", lambda _pkg: _Res('{"b": {"code": 4}}'))
        assert scene_names_by_code("H4321") == {4: "b"}
    finally:
        scenes._catalog.cache_clear()
        scenes._NAMES_BY_CODE.pop("H4321", None)


def test_names_by_code_bad_sku_still_invalid_value() -> None:
    with pytest.raises(GoveeBleInvalidValue):
        scene_names_by_code("../x")


async def test_async_load_scenes_matches_sync() -> None:
    assert await scenes.async_load_scenes("H6006") == scenes.load_scenes("H6006")
