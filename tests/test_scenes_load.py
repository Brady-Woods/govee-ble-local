"""Scene catalog loader: SKU validation, error contract, cache isolation; the bundled store and the table."""
from __future__ import annotations

import base64
import dataclasses
import errno
import gc
import hashlib
import json
import lzma
import threading
import time
import traceback
import weakref
from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from types import SimpleNamespace

import pytest
from conftest import FakeSceneFiles, scene_store
from fake_ble import ble_device

from govee_ble_local import profile_for, scenes, supported_skus
from govee_ble_local.devices.device import make_device
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
        scenes._store.cache_clear()
        monkeypatch.setattr(scenes.resources, "files", lambda _pkg: FakeSceneFiles(text))
    yield install
    scenes._store.cache_clear()


def _bad(pool: list, skus: dict | None = None) -> str:
    return json.dumps({"pool": pool, "skus": {"H1234": [0]} if skus is None else skus})


_A, _B = {"name": "a", "code": 1}, {"name": "b", "code": 2}


@pytest.mark.parametrize(
    "text",
    ["{not json", "[1, 2]", '{"pool": {}, "skus": {}}', _bad([5]), _bad([{"name": "a", "param": None}]),
     _bad([{"name": "a", "code": "xx"}]), _bad([{"name": "", "code": 1}]),
     _bad([{**_A, "rules": True}]),   # version-gated in the app (scenes.list_filter): refused
     _bad([{**_A, "scene_name": 5}]),   # fetch_scenes records a sceneName as a string
     _bad([_A], {"H1234": [1]}), _bad([_A, _B], {"H1234": [1, 0]}),
     _bad([_A, {**_A, "code": 2}], {"H1234": [0, 1]}),   # two scenes of one name
     _bad([_A], {"h1234": [0]}),
     _bad([_A, _B]),   # b is no SKU's: an orphan
     # a code JSON reads as a float (inf: int() raised OverflowError) or a nesting too deep for json.loads
     # (RecursionError): a library error like any other
     '{"pool": [{"name": "a", "code": 1e999}], "skus": {"H1234": [0]}}',
     '{"pool": [{"name": "a", "code": Infinity}], "skus": {"H1234": [0]}}',
     "[" * 100_000,
     # nothing is coerced: what tools/fetch_scenes.py refuses to write (scenes._validate_entry)
     *(_bad([{**_A, **bad}]) for bad in (
         {"code": "7"}, {"code": 1.9}, {"code": True}, {"code": -1}, {"code": 0x10000}, {"code": None},
         {"scene_type": "2"}, {"scene_type": 2.0}, {"cmd_version": False}, {"param_id": True},
         {"placeholder": "no"}, {"placeholder": 1}, {"big_effect": "yes"}, {"big_effect": 0},
         {"category": 5}, {"param": 3}, {"param": "é"}, {"name": 5})),
     _bad([_B, _A], {"H1234": [0, 1]}),   # the pool out of its content order
     _bad([_A, _A], {"H1234": [0], "H5678": [1]})],   # one scene twice in the pool
)
def test_malformed_catalog(fake_catalog, text):
    fake_catalog(text)
    with pytest.raises(GoveeBleError, match="the bundled scene store is malformed") as ei:
        load_scenes("h1234")
    assert not isinstance(ei.value, GoveeBleInvalidValue)
    assert ei.value.__cause__ is not None


@pytest.mark.parametrize("name", [{"param": "SECRETBLOB"}, ["SECRETBLOB"]])
def test_a_name_that_is_no_string_is_not_quoted(fake_catalog, name):
    fake_catalog(_bad([{**_A, "name": name}]))
    with pytest.raises(GoveeBleError, match="not a non-empty string") as ei:
        load_scenes("H1234")
    assert "SECRETBLOB" not in "".join(traceback.format_exception(ei.value))   # named by its type: a blob


def test_a_store_of_every_typed_key_loads(fake_catalog):
    full = {"code": 0xFFFF, "param": "kwr/", "category": "c", "scene_name": "A", "param_id": 7,
            "scene_type": 1, "cmd_version": 0, "placeholder": True, "big_effect": False, "other": [1.5]}
    nulls = {"code": 0, **{key: None for key, _type in scenes._ENTRY_TYPES}, "rules": None}
    fake_catalog(scene_store("H1234", {"a": full, "b": nulls}))
    a, b = load_scenes("H1234").values()
    assert a == scenes.Scene("a", 0xFFFF, "kwr/", "c", 7, True, scene_type=1, cmd_version=0, big_effect=False)
    assert b == scenes.Scene("b", 0, None)


class _CountingFiles(FakeSceneFiles):
    """FakeSceneFiles that counts the store's reads, each one taking ``delay`` seconds."""

    def __init__(self, store):
        super().__init__(store)
        self.reads = 0
        self.delay = 0.0

    def __truediv__(self, file):
        path = super().__truediv__(file)

        def read_text(encoding=None):
            self.reads += 1
            time.sleep(self.delay)
            return path.read_text(encoding)
        return type(path)(read_text=read_text)


@pytest.fixture
def counted(monkeypatch):
    """Installs a store of ``store`` (FakeSceneFiles) whose reads are counted, and returns its files;
    ``clear=False`` keeps the store already read cached."""
    def install(store, *, clear=True):
        if clear:
            scenes._store.cache_clear()
        files = _CountingFiles(store)
        monkeypatch.setattr(scenes, "resources", SimpleNamespace(files=lambda _pkg: files))
        return files
    scenes._store.cache_clear()
    yield install
    scenes._store.cache_clear()


def test_the_store_is_read_and_checked_once(counted):
    files = counted(scene_store("H1234", {"a": {"code": 3}}))
    for sku in ("H1234", "h1234", "H9999", "H6006"):
        load_scenes(sku)
        scene_names_by_code(sku)
    assert files.reads == 1 and load_scenes("H1234") == {"a": scenes.Scene("a", 3, None)}
    scenes._store.cache_clear()   # the tests' path: the next lookup reads it again
    assert load_scenes("H6006") == {} and files.reads == 2


def test_first_lookups_made_at_once_read_the_store_once(counted):
    # one per device's setup (hass-govee-ble-local sets its config entries up at once): functools.cache has
    # no lock, so without _STORE_LOCK each thread that missed it read and checked the whole store
    files = counted(scene_store("H1234", {"a": {"code": 3}}))
    files.delay = 0.05
    barrier = threading.Barrier(8)

    def lookup(_i):
        barrier.wait()
        return load_scenes("H1234")

    with ThreadPoolExecutor(8) as pool:
        found = list(pool.map(lookup, range(8)))
    assert files.reads == 1 and found == [{"a": scenes.Scene("a", 3, None)}] * 8


def test_the_first_failure_is_kept(counted):
    error = PermissionError(13, "Permission denied")
    files = counted(error)
    raised = []
    for sku in ("H1234", "H9999", "H6006", "H1234"):
        with pytest.raises(GoveeBleError, match="could not be read") as ei:
            load_scenes(sku)
        raised.append(ei.value)
    with pytest.raises(GoveeBleError):
        scene_names_by_code("H6006")
    assert files.reads == 1                          # a package file does not heal: it is not read again
    assert len({id(e) for e in raised}) == len(raised)   # each lookup's own error: no raise changes another's
    assert all(str(e) == str(raised[0]) and e.__cause__ is error for e in raised)
    depth, tb = 0, raised[-1].__traceback__
    while tb is not None:
        depth, tb = depth + 1, tb.tb_next
    assert depth <= 3                                # a fresh error each raise: its traceback never grows


@pytest.mark.parametrize(("store", "message"), [
    (_bad([{**_A, "param": "!!"}]), "'param' is not valid base64"),   # a ValueError from a binascii.Error
    (PermissionError(13, "Permission denied"), "Permission denied"),
])
def test_the_kept_failure_keeps_no_frame_alive(counted, store, message):
    class Local:
        pass

    def first_lookup():
        local = Local()
        with pytest.raises(GoveeBleError, match=message):
            load_scenes("H1234")
        return weakref.ref(local)

    counted(store)
    alive = first_lookup()
    gc.collect()
    assert alive() is None   # a kept traceback's frames led back to the first lookup's caller, and its locals
    chain, link = [], scenes._store()
    while link is not None:
        chain.append(link)
        link = link.__cause__ or link.__context__
    assert len(chain) >= 2 and all(e.__traceback__ is None and str(e) for e in chain)   # every message stays


@pytest.mark.parametrize("store", [
    lambda: _bad([{**_A, "param": "!!"}]),   # a ValueError from a binascii.Error
    lambda: "{not json",                      # a JSONDecodeError, json's StopIteration its context
    lambda: UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),   # not UTF-8
    lambda: PermissionError(13, "Permission denied"),
], ids=["base64", "json", "utf8", "permission"])
def test_the_kept_failure_keeps_not_the_callers_exception(counted, store):
    # every exception raised while the first lookup's caller handles one takes that one in as its
    # __context__: the kept failure's chain neither holds it nor strips its traceback
    class Handled(Exception):
        pass

    def first_lookup():
        try:
            raise Handled("the caller's own error")
        except Handled as handled:
            with pytest.raises(GoveeBleError):
                load_scenes("H1234")
            assert handled.__traceback__ is not None   # the caller's, as the caller has it
            return weakref.ref(handled)

    counted(store())
    alive = first_lookup()
    gc.collect()
    assert alive() is None   # nor what it references, kept for the life of the process
    with pytest.raises(GoveeBleError) as ei:
        load_scenes("H6006")   # a later, unrelated lookup
    assert "the caller's own error" not in "".join(traceback.format_exception(ei.value))


@pytest.mark.parametrize("error", [
    RuntimeError("File 'x' is encrypted, password required for extraction"),   # a member flagged encrypted
    NotImplementedError("That compression method is not supported"),   # an unknown method, flag bit 5 or 6
    lzma.LZMAError("Corrupt input data"),   # a damaged LZMA member
])
def test_a_corrupt_zip_member_is_unreadable(counted, error):
    # a store.json member of a zipped install: zipfile raises these, so the read is a library error
    files = counted(error)
    for sku in ("H1234", "H6006"):
        with pytest.raises(GoveeBleError, match="could not be read"):
            load_scenes(sku)
    assert files.reads == 1   # kept, not raised (functools.cache keeps no raise: each lookup read again)


def test_a_read_error_of_the_process_is_not_kept(counted):
    files = counted(OSError(errno.EMFILE, "Too many open files"))
    with pytest.raises(GoveeBleError, match=r"could not be read: OSError: .*Too many open files"):
        load_scenes("H1234")
    files._store = scene_store("H1234", {"a": {"code": 3}})   # the process has file descriptors again
    assert load_scenes("H1234") == {"a": scenes.Scene("a", 3, None)} and files.reads == 2


async def test_device_scene_properties_need_no_io_once_loaded(counted):
    d = make_device(ble_device("AA:BB:CC:DD:EE:5B", "H6008"), "H6008")
    assert d is not None
    names = sorted(await scenes.async_load_scenes("H6008"))   # the real store, loaded as update() does
    d._state.scene_code = load_scenes("H6008")["Aurora"].code
    unreadable = counted(OSError(5, "I/O error"), clear=False)   # a read now would raise
    assert d.scene_names == names and d.active_scene == "Aurora"
    assert unreadable.reads == 0


@pytest.mark.parametrize("entry", [None, [], "x", 5])
def test_validate_entry_refuses_any_value_with_value_error(entry):
    with pytest.raises(ValueError, match="not an object"):
        scenes._validate_entry("a", entry)
    for name in (None, "", 5):
        with pytest.raises(ValueError, match="not a non-empty string"):
            scenes._validate_entry(name, {"code": 1})


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
