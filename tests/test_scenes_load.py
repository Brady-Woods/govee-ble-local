"""Scene catalog loader: SKU validation, error contract, cache isolation; the bundled store and the table."""
from __future__ import annotations

import base64
import dataclasses
import json
from importlib import resources

import pytest
from conftest import FakeSceneFiles

from govee_ble_local import profile_for, scenes
from govee_ble_local.exceptions import GoveeBleError, GoveeBleInvalidValue
from govee_ble_local.scenes import load_scenes, scene_names_by_code

# Counts captured from the pre-change loader.
EXPECTED = {
    "H6006": 59, "H6008": 59, "H6047": 65, "H6052": 43,
    "H60A6": 84, "H61A8": 148, "H6641": 148,
}


@pytest.mark.parametrize(("sku", "count"), EXPECTED.items())
def test_shipped_catalogs(sku, count):
    assert len(load_scenes(sku)) == count
    assert len(load_scenes(sku.lower())) == count
    assert len(load_scenes(f" {sku} ")) == count


@pytest.mark.parametrize(
    "sku",
    [
        "",
        "  ",
        "../H6006",
        "H6006/../x",
        "H60.6",
        "a/b",
        "H6",
        "H60060606",
        "H60 06",
        "H6006.json",
        "H60\x00A",
        None,
    ],
)
def test_bad_sku(sku):
    with pytest.raises(GoveeBleInvalidValue):
        load_scenes(sku)


def test_unknown_valid_sku():
    assert load_scenes("H9999") == {}
    assert scene_names_by_code("H9999") == {}


@pytest.fixture
def fake_catalog(monkeypatch):
    def install(text):
        scenes._catalog.cache_clear()
        monkeypatch.setattr(scenes.resources, "files", lambda _pkg: FakeSceneFiles("H1234", text))
    yield install
    scenes._catalog.cache_clear()


@pytest.mark.parametrize(
    "text",
    ["{not json", "[1, 2]", '"str"', '{"a": {"param": null}}', '{"a": 5}', '{"a": {"code": "xx"}}'],
)
def test_malformed_catalog(fake_catalog, text):
    fake_catalog(text)
    with pytest.raises(GoveeBleError, match="scene catalog for H1234 is malformed") as ei:
        load_scenes("h1234")
    assert not isinstance(ei.value, GoveeBleInvalidValue)
    assert ei.value.__cause__ is not None


def test_valid_fake_catalog(fake_catalog):
    fake_catalog('{"a": {"code": 3, "param": null}, "b": {"code": 3}}')
    assert scene_names_by_code("H1234") == {3: "a"}


def test_mutation_isolation():
    a = load_scenes("H6006")
    n = len(a)
    name = next(iter(a))
    with pytest.raises(dataclasses.FrozenInstanceError):
        a[name].code = 0  # type: ignore[misc]
    a.clear()
    b = load_scenes("H6006")
    assert len(b) == n and b is not a
    names = scene_names_by_code("H6006")
    names.clear()
    assert scene_names_by_code("H6006")


def test_no_legacy_scene_catalog_holds_a_type5_scene_the_app_uploads():
    """devices.schema.json scene_dialect: the library only activates a sceneType-5 scene under legacy_scene,
    so a legacy_scene SKU's bundled catalog holds none the app uploads: none whose param leads 0x50 (the
    new-DIY parser a goodsType registers, BaseSceneViewMode.r3), and none at all where its versions hold one
    of parseSceneV1's sceneType-5 branches (7 to 13)."""
    index = json.loads((resources.files(scenes) / "index.json").read_text(encoding="utf-8"))
    for sku in index:
        p = profile_for(sku, allow_modelled=True)
        if p is not None and p.scene_dialect != "A":
            continue   # H60A6, H6052: a sceneType-5 dialect of their own
        sets = [] if p is None else [p.scene_versions, *(v for _p, v in p.scene_versions_by_pact),
                                     *(v for _s, _h, v in p.scene_versions_by_firmware)]
        type5_branch = any(7 <= v <= 13 for s in sets for v in s)
        for scene in load_scenes(sku).values():
            raw = base64.b64decode(scene.param or "")
            new_diy = len(raw) >= 2 and raw[0] == 0x50
            assert scene.scene_type != 5 or not (type5_branch or new_diy), (sku, scene.name)
