"""Scene catalog loader: SKU validation, error contract, cache isolation; the bundled store and the table."""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
from importlib import resources

import pytest
from conftest import FakeSceneFiles

from govee_ble_local import profile_for, scenes, supported_skus
from govee_ble_local.exceptions import GoveeBleError, GoveeBleInvalidValue
from govee_ble_local.models import Capability
from govee_ble_local.scenes import load_scenes, scene_names_by_code

# Every bundled catalog's scene count (1.4.0's ten, then the admitted models'), so a catalog re-pointed,
# added or dropped fails here (test_the_scene_store_is_the_device_tables).
EXPECTED = {
    "H6006": 59, "H6008": 59, "H6047": 65, "H6052": 43,
    "H60A6": 84, "H61A8": 148, "H6641": 148, "H6004": 63, "H6062": 70, "H6188": 9,
    "H1401": 94, "R1401": 94, "H14B1": 63, "H6002": 8,
}

# The bundled scenes the app's scene list hides at one of their SKU's scene sets (devices.yaml
# scenes.list_filter) while the library still offers them there: {(SKU, set): how many}.
LIST_FILTER_EXEMPT = {
    # Review G1, the user's call: H61A8 at pact code 2 ((1,2) / (2,2)) or from soft 1.04.00, below part
    # brightness (soft 1.06.00 and hard 1.00.02), e.g. soft 1.05.00 / hard 1.00.01, has the set {0, 1, 2}.
    # Its 36 RGBIC scenes at cmdVersion 1 are op type 3 (CategoryV1.getOpType), which the list drops there
    # (RgbIcLightRegistry.java:723, CategoryV1.java:295-321,591-602); the library still uploads them, as 1.4.0
    # did (_plan_upload doesn't filter by the op set). Until G1 lands.
    ("H61A8", frozenset({0, 1, 2})): 36,
}


def _op_type(scene_type: int | None, cmd_version: int | None) -> int:
    """A scene's op type (CategoryV1.LightEffect.getOpType, CategoryV1.java:295-321; scenes.list_filter)."""
    if scene_type == 2:
        return 2 if not cmd_version else 3
    return {0: 0, 1: 1, 3: 4, 4: 4, 5: 5, 6: 6}.get(scene_type or 0, -1)


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
    ["{not json", "[1, 2]", '"str"', '{"a": {"param": null}}', '{"a": 5}', '{"a": {"code": "xx"}}',
     '{"a": {"code": 1, "rules": true}}'],   # version-gated in the app (scenes.list_filter): refused
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


def test_the_bundled_scenes_are_ones_the_app_lists():
    """devices.yaml scenes.list_filter: each bundled scene's op type (its scene type and cmdVersion for the
    SKU) is in every one of its SKU's scene sets (the row's, each by_pact and each by_firmware one), but for
    the counts LIST_FILTER_EXEMPT names. An empty set is a null op set, which filters nothing. (The app lets a
    base sceneType-0 effect through any set; a catalog keeps the SKU's type only, so a base-static scene typed
    otherwise for its SKU would be named here too.)"""
    index = json.loads((resources.files(scenes) / "index.json").read_text(encoding="utf-8"))
    reached = set()
    for sku in index:
        p = profile_for(sku, allow_modelled=True)
        assert p is not None, sku
        catalog = load_scenes(sku).values()
        for versions in (p.scene_versions, *(v for _p, v in p.scene_versions_by_pact),
                         *(v for _s, _h, v in p.scene_versions_by_firmware)):
            if not versions:
                continue
            key = (sku, frozenset(versions))
            hidden = sum(1 for s in catalog if _op_type(s.scene_type, s.cmd_version) not in versions)
            assert hidden == LIST_FILTER_EXEMPT.get(key, 0), (sku, sorted(versions))
            reached |= {key} & set(LIST_FILTER_EXEMPT)
    assert reached == set(LIST_FILTER_EXEMPT)   # no stale exemption


def test_the_scene_store_is_the_device_tables():
    """The store and the table agree: the SKUs with a catalog are exactly the ones offered scenes (a SKU
    without one is not: _resolve's module doc), each set is named by its SHA-256 prefix, and every set is some
    SKU's (no orphan). EXPECTED pins each catalog's size, so a re-pointed or dropped catalog fails."""
    files = resources.files(scenes)
    index = json.loads((files / "index.json").read_text(encoding="utf-8"))
    offered: set[str] = set()
    for opt_in in (False, True):
        for sku in supported_skus(allow_modelled=opt_in):
            p = profile_for(sku, allow_modelled=opt_in)
            assert p is not None, sku   # every SKU routes without its pact
            if Capability.SCENES in p.capabilities:
                offered.add(sku)
    assert set(index) == offered == set(EXPECTED)
    sets = {f.name.removesuffix(".json"): f.read_text(encoding="utf-8") for f in files.iterdir()
            if f.name.endswith(".json") and f.name != "index.json"}
    assert set(sets) == set(index.values())
    for name, text in sets.items():
        assert hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] == name
