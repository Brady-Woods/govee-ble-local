"""Scene catalog loader: SKU validation, error contract, cache isolation; the bundled store and the table."""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
from importlib import resources

import pytest
from conftest import FakeSceneFiles, scene_store

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
# 1.4.0's ten, each pinned to its content (_content of v1.4.0's load_scenes, from the 1.4.0 wheel), so a
# change to one of their catalogs fails even at the same count.
RELEASED = {
    "H6004": "64c882fa0abdd5ef", "H6006": "7827c664fbe0e692", "H6008": "7827c664fbe0e692",
    "H6047": "493a456314cc7cdb", "H6052": "e23d874ec5ca609b", "H6062": "acc242d1ab4a9e52",
    "H60A6": "a1b7070e74ef0276", "H6188": "b1fce838b659f7bb", "H61A8": "74f16a978bb9d70f",
    "H6641": "74f16a978bb9d70f",
}

# The bundled scenes whose bundled lightEffect the app's scene list drops at one of their SKU's scene sets
# (devices.yaml scenes.list_filter: it hides the scene, or plays another of its effects) while the library
# still offers it there: {(SKU, set): how many}.
LIST_FILTER_EXEMPT = {
    # Review G1, the user's call: H61A8 at pact code 2 ((1,2) / (2,2)) or from soft 1.04.00, below part
    # brightness (soft 1.06.00 and hard 1.00.02), e.g. soft 1.05.00 / hard 1.00.01, has the set {0, 1, 2}.
    # Its 36 RGBIC scenes at cmdVersion 1 are op type 3 (CategoryV1.getOpType), which the list drops there
    # (RgbIcLightRegistry.java:723, CategoryV1.java:295-321,591-602); the library still uploads them, as 1.4.0
    # did (_plan_upload doesn't filter by the op set). Until G1 lands.
    ("H61A8", frozenset({0, 1, 2})): 36,
}


def _content(sku: str) -> str:
    """``load_scenes(sku)``'s content: the first 16 hex digits of the SHA-256 of every Scene field of every
    scene, in order, as JSON with sorted keys."""
    rows = [dataclasses.asdict(s) for s in load_scenes(sku).values()]
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _store() -> dict:
    """The bundled store, as JSON."""
    return json.loads((resources.files(scenes) / "store.json").read_text(encoding="utf-8"))


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
        monkeypatch.setattr(scenes.resources, "files", lambda _pkg: FakeSceneFiles(text))
    yield install
    scenes._catalog.cache_clear()


def _bad(pool: list, skus: dict | None = None) -> str:
    return json.dumps({"pool": pool, "skus": {"H1234": [0]} if skus is None else skus})


_A, _B = {"name": "a", "code": 1}, {"name": "b", "code": 2}


@pytest.mark.parametrize(
    "text",
    ["{not json", "[1, 2]", '{"pool": {}, "skus": {}}', _bad([5]), _bad([{"name": "a", "param": None}]),
     _bad([{"name": "a", "code": "xx"}]), _bad([{"name": "", "code": 1}]),
     _bad([{**_A, "rules": True}]),   # version-gated in the app (scenes.list_filter): refused
     _bad([_A], {"H1234": [1]}), _bad([_A, _B], {"H1234": [1, 0]}),
     _bad([_A, {**_A, "code": 2}], {"H1234": [0, 1]}),   # two scenes of one name
     _bad([_A], {"h1234": [0]}),
     _bad([_A, _B])],   # b is no SKU's: an orphan
)
def test_malformed_catalog(fake_catalog, text):
    fake_catalog(text)
    with pytest.raises(GoveeBleError, match="scene catalog for H1234 is malformed") as ei:
        load_scenes("h1234")
    assert not isinstance(ei.value, GoveeBleInvalidValue)
    assert ei.value.__cause__ is not None


def test_valid_fake_catalog(fake_catalog):
    fake_catalog(scene_store("H1234", {"a": {"code": 3, "param": None}, "b": {"code": 3}}))
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
    for sku in _store()["skus"]:
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
    the counts LIST_FILTER_EXEMPT names. An empty set is a null op set: the app's list filters no op type
    there, but plays a scene's first effect in force, which may not be the bundled one; a catalog keeps one
    effect only, so that is checked at fetch time (fetch_scenes.fetch names such scenes), not here. (The app
    lets a base sceneType-0 effect through any set; a catalog keeps the SKU's type only, so a base-static
    scene typed otherwise for its SKU would be named here too.)"""
    reached = set()
    for sku in _store()["skus"]:
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
    without one is not: _resolve's module doc). The store is tools/fetch_scenes.py's layout: the pool holds
    each scene once, sorted by content (its name, then its JSON with sorted keys), every entry some SKU's (no
    orphan), in the tool's JSON form, and it is the package's only JSON. EXPECTED pins each catalog's size,
    so a re-pointed or dropped catalog fails, and RELEASED each of 1.4.0's ten to 1.4.0's content (every
    Scene field), so a change to one of them fails even at the same count."""
    files = resources.files(scenes)
    text = (files / "store.json").read_text(encoding="utf-8")
    store = json.loads(text)
    assert {sku: _content(sku) for sku in RELEASED} == RELEASED
    offered: set[str] = set()
    for opt_in in (False, True):
        for sku in supported_skus(allow_modelled=opt_in):
            p = profile_for(sku, allow_modelled=opt_in)
            assert p is not None, sku   # every SKU routes without its pact
            if Capability.SCENES in p.capabilities:
                offered.add(sku)
    assert set(store["skus"]) == offered == set(EXPECTED)
    keys = [(e["name"], json.dumps(e, sort_keys=True, separators=(",", ":"))) for e in store["pool"]]
    assert keys == sorted(set(keys))
    assert {i for refs in store["skus"].values() for i in refs} == set(range(len(keys)))
    assert text == json.dumps(store, indent=1, sort_keys=True)
    assert [f.name for f in files.iterdir() if f.name.endswith(".json")] == ["store.json"]
