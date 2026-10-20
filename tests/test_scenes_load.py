"""Scene catalog loader: SKU validation, error contract, cache isolation; the bundled store and the table."""
from __future__ import annotations

import base64
import contextlib
import dataclasses
import errno
import gc
import hashlib
import itertools
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
from govee_ble_local.devices import _fold
from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import DeviceProfile
from govee_ble_local.exceptions import GoveeBleError, GoveeBleInvalidValue
from govee_ble_local.models import Capability
from govee_ble_local.scenes import async_load_scenes, load_scenes, scene_names_by_code

# Every bundled catalog's scene count (1.4.0's ten, then the admitted models'), so a catalog re-pointed,
# added or dropped fails here (test_the_scene_store_is_the_device_tables).
EXPECTED = {
    "H6006": 59, "H6008": 59, "H6047": 65, "H6052": 43,
    "H60A6": 85, "H61A8": 149, "H6641": 149, "H6004": 63, "H6062": 70, "H6188": 9,
    "H1401": 94, "R1401": 94, "H14B1": 63, "H6002": 8,
}
# Every bundled catalog pinned to its content (_content of load_scenes), so a change to any of them fails
# even at the same count. 1.4.0's ten held 1.4.0's content (the 1.4.0 wheel's) until 1.5.0's re-fetch
# (tools/fetch_scenes.py over the public library of 2026-10-19), which leaves H6006 / H6008 out; each one it
# changed gives 1.4.0's and why.
RELEASED = {
    # 1.4.0 64c882fa0abdd5ef: Christmas is no placeholder (only a big-effect scene without a param is)
    "H6004": "57dc60da73d4ee29",
    # 1.4.0's, not re-fetched: on the bench of 2026-10-20 the 2026-10-19 library's codes did not play on them
    "H6006": "7827c664fbe0e692", "H6008": "7827c664fbe0e692",
    "H6047": "493a456314cc7cdb", "H6052": "e23d874ec5ca609b", "H6062": "acc242d1ab4a9e52",
    # 1.4.0 a1b7070e74ef0276: a new Natural "Lightning" (24448); Disney's Moana's three kept (dropped)
    "H60A6": "03c2aa3f543ec8d1",
    "H6188": "b1fce838b659f7bb",
    # 1.4.0 74f16a978bb9d70f (both): their specialEffect values (H61A8: 8 scenes' param and param id; H6641:
    # 92 scenes', 27 of them the param id alone), and Emotion's Flash (2020) new as "Flash (Emotion)"
    "H61A8": "27f9674173f5c2f7", "H6641": "9da94ff28ac7a57f",
    "H1401": "61b53e825a507185", "R1401": "61b53e825a507185",
    "H14B1": "57dc60da73d4ee29",   # H6004's scenes, its Christmas no placeholder (was 1.4.0's H6004's)
    "H6002": "06fe006f08f933d2",
}
# Every catalog the store records at a scene set (its by_set), {(SKU, set): (scene count, _content)}, so a
# per-set catalog re-pointed, added or dropped fails here too. None: every bundled SKU has one scene set
# (H61A8's is {0, 1, 2, 3} at every pact and firmware, devices.yaml row 19); the dreamcolorlightv1 SKUs'
# {0, 1, 2} catalogs follow in 1.5.1.
AT_SET: dict[tuple[str, tuple[int, ...]], tuple[int, str]] = {}


def _content(sku: str, versions: tuple[int, ...] | None = None) -> str:
    """``load_scenes(sku, scene_versions=versions)``'s content: the first 16 hex digits of the SHA-256 of
    every Scene field of every scene, in order, as JSON with sorted keys."""
    rows = [dataclasses.asdict(s) for s in load_scenes(sku, scene_versions=versions).values()]
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _sets(p: DeviceProfile) -> list[frozenset[int]]:
    """Every distinct scene set of profile ``p``: its own, each ``by_pact`` and each ``by_firmware`` one."""
    found = (p.scene_versions, *(v for _p, v in p.scene_versions_by_pact),
             *(v for _s, _h, v in p.scene_versions_by_firmware))
    return list(dict.fromkeys(frozenset(v) for v in found))


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


def _bad(pool: list, skus: dict | None = None, by_set: object = None) -> str:
    return json.dumps({"pool": pool, "skus": {"H1234": [0]} if skus is None else skus,
                       "by_set": {} if by_set is None else by_set})


_A, _B = {"name": "a", "code": 1}, {"name": "b", "code": 2}
_A9 = {"name": "a", "code": 9}   # another entry of the name "a", after _A in the pool's content order


def _at(names: object, at: str = "0,1,2", sku: str = "H1234") -> str:
    """A store of _A (H1234's catalog) and _A9, whose by_set gives H1234 ``names`` at ``at``."""
    return _bad([_A, _A9], {"H1234": [0]}, {sku: {at: names}})


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


@pytest.mark.parametrize(("text", "message"), [
    # the store's one shape holds by_set, an object
    (json.dumps({"pool": [_A], "skus": {"H1234": [0]}}), "not an object of 'pool', 'skus' and 'by_set'"),
    (_bad([_A], by_set=[]), "'by_set' not an object"),
    (_bad([{**_A, "by_set": {}}]), "carries no 'by_set'"),   # a pool entry with one: none nests
    (_at({"a": 1}, sku="H5678"), "by_set 'H5678' is not a non-empty object of a SKU"),   # no catalog
    (_bad([_A], by_set={"H1234": {}}), "by_set 'H1234' is not a non-empty object"),
    (_bad([_A], by_set={"H1234": []}), "by_set 'H1234' is not a non-empty object"),
    (_bad([_A], by_set={"H1234": ["0,1,2"]}), "by_set 'H1234' is not a non-empty object"),   # no object
    (json.dumps({"pool": [_A], "skus": {"H1234": [0]}, "by_set": {}, "more": {}}),
     "not an object of 'pool', 'skus' and 'by_set'"),   # exactly those three
    *((_at({"a": 1}, at), f"by_set {at!r} is not a sorted, comma-joined scene set")
      for at in ("2,1,0", "0, 1, 2", "00,1,2", "0,1,1", "-1", "x", "0,1,2,", ",")),
    *((_at(names), "is not a sorted, comma-joined scene set of a non-empty object")
      for names in ({}, None, [1])),
    (_at({"b": None}), "names 'b', which its catalog lacks"),
    *((_at({"a": i}), "gives 'a' no other pool entry of that name") for i in (0, 2, True, "1", -1, 1.0)),
    (_bad([_A, _B], {"H1234": [0], "H5678": [1]}, {"H1234": {"0,1,2": {"a": 1}}}),
     "gives 'a' no other pool entry of that name"),   # another name's entry
    (_bad([_A, _A9], {"H1234": [0]}), "pool entry 1 is no SKU's or override's"),   # an orphan
])
def test_a_malformed_by_set_is_refused(fake_catalog, text, message):
    fake_catalog(text)
    with pytest.raises(GoveeBleError, match="the bundled scene store is malformed") as ei:
        load_scenes("H1234")
    assert message in str(ei.value)


def test_the_catalog_at_a_scene_set(fake_catalog):
    """by_set: at a set the store records, a scene it maps to null is left out, and one it maps to a pool
    entry holds that entry under its own name, at its place; every other scene, and every set it records
    nothing at, is the catalog's. scene_names_by_code follows (first name wins)."""
    a9 = {"name": "a", "code": 9, "param": "kwE=", "category": "x", "scene_type": 1}   # before _A by content
    pool = [a9, _A, _B, {"name": "c", "code": 1}]
    fake_catalog(_bad(pool, {"H1234": [1, 2, 3]}, {"H1234": {"0,1,2": {"a": 0, "b": None}, "": {"c": None}}}))
    whole = {n: scenes.Scene(n, code, None) for n, code in (("a", 1), ("b", 2), ("c", 1))}
    assert load_scenes("H1234") == whole and scene_names_by_code("H1234") == {1: "a", 2: "b"}
    at012 = load_scenes("h1234", scene_versions={2, 1, 0})
    assert list(at012) == ["a", "c"] and at012["a"] == scenes.Scene("a", 9, "kwE=", "x", scene_type=1)
    assert scene_names_by_code("H1234", scene_versions=[0, 1, 2]) == {9: "a", 1: "c"}
    assert load_scenes("H1234", scene_versions=frozenset()) == {"a": whole["a"], "b": whole["b"]}
    for other in ({0, 1}, (0, 1, 2, 3), range(3, 6)):   # a set the store records nothing at
        assert load_scenes("H1234", scene_versions=other) == whole
    assert load_scenes("H9999", scene_versions={0, 1, 2}) == {}
    assert scene_names_by_code("H9999", scene_versions=[]) == {}
    at012.clear()
    assert len(load_scenes("H1234", scene_versions=(0, 1, 2))) == 2   # a fresh dict each call
    for bad in ([True], [-1], "012", 5, [1.5], [None], [10**5000], [-10**5000], iter([0, 1, 2]),
                (v for v in (0, 1, 2))):
        with pytest.raises(GoveeBleInvalidValue, match="not a collection of non-negative ints"):
            load_scenes("H1234", scene_versions=bad)
        with pytest.raises(GoveeBleInvalidValue):
            scene_names_by_code("H1234", scene_versions=bad)
    for ints in (b"\x00\x01\x02", bytearray(b"\x02\x01\x00"), memoryview(b"\x00\x01\x02")):   # its ints
        assert load_scenes("H1234", scene_versions=ints) == load_scenes("H1234", scene_versions={0, 1, 2})


async def test_async_load_scenes_takes_the_scene_set():
    for versions in (None, (0, 1, 2), (), {0, 1, 2, 3}):
        assert await async_load_scenes("H61A8", scene_versions=versions) == load_scenes(
            "H61A8", scene_versions=versions)


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
    assert all(str(e) == str(raised[0]) for e in raised)
    assert "PermissionError: [Errno 13] Permission denied" in str(raised[0])   # the cause's type and text
    assert raised[0].__cause__ is error                    # the lookup that read the store: its real cause
    assert all(e.__cause__ is None and e.__suppress_context__ for e in raised[1:])   # only the message kept
    depth, tb = 0, raised[-1].__traceback__
    while tb is not None:
        depth, tb = depth + 1, tb.tb_next
    assert depth <= 3                                # a fresh error each raise: its traceback never grows


@pytest.mark.parametrize(("store", "message"), [
    (lambda: _bad([{**_A, "param": "!!"}]), "'param' is not valid base64"),   # a ValueError: binascii.Error
    (lambda: PermissionError(13, "Permission denied"), "Permission denied"),
], ids=["base64", "permission"])
def test_the_kept_failure_keeps_no_frame_alive(counted, store, message):
    class Local:
        pass

    def first_lookup():
        local = Local()
        with pytest.raises(GoveeBleError, match=message):
            load_scenes("H1234")
        return weakref.ref(local)

    files = counted(store())
    alive = first_lookup()
    files._store = None   # the fake's own exception, which it raised: a real read raises a fresh one
    gc.collect()
    assert alive() is None   # a kept traceback's frames led back to the first lookup's caller, and its locals
    kept = scenes._store()
    assert isinstance(kept, scenes._Failure) and kept.cause is None and message in kept.message   # only it


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

    files = counted(store())
    alive = first_lookup()
    files._store = None   # the fake's own exception, raised while the caller handled its own: not the store's
    gc.collect()
    assert alive() is None   # nor what it references, kept for the life of the process
    with pytest.raises(GoveeBleError) as ei:
        load_scenes("H6006")   # a later, unrelated lookup
    assert "the caller's own error" not in "".join(traceback.format_exception(ei.value))


@pytest.mark.parametrize("error", [
    RuntimeError("File 'x' is encrypted, password required for extraction"),   # a member flagged encrypted
    NotImplementedError("That compression method is not supported"),   # an unknown method, flag bit 5 or 6
    lzma.LZMAError("Corrupt input data"),   # a damaged LZMA member
    EOFError(),   # review ST-2: a member that runs past the archive's end (overlapping entries at one offset)
])
def test_a_corrupt_zip_member_is_unreadable(counted, error):
    # a store.json member of a zipped install: zipfile raises these, so the read is a library error
    files = counted(error)
    for sku in ("H1234", "H6006"):
        with pytest.raises(GoveeBleError, match="could not be read"):
            load_scenes(sku)
    assert files.reads == 1   # kept, not raised (functools.cache keeps no raise: each lookup read again)


def test_an_error_with_no_text_is_named_by_its_type(counted):
    """Review H5: an EOFError has no text, so the message ended "EOFError: " with nothing after it."""
    counted(EOFError())
    with pytest.raises(GoveeBleError) as ei:
        load_scenes("H1234")
    assert str(ei.value) == "the bundled scene store could not be read: EOFError"


def test_one_of_first_lookups_made_at_once_takes_the_cause(counted, monkeypatch):
    """Review H3 (L15): of first lookups made at once over a store that fails, only the one that read it is
    raised from its cause (it takes the cause out of the cache under _STORE_LOCK); every other one from None.
    The kept failure's cause is slow to take here, so a take outside the lock would hand it to more."""
    class SlowFailure:
        def __init__(self, message, cause):
            self.message, self._cause = message, cause

        @property
        def cause(self):
            taken = self._cause
            time.sleep(0.01)   # another taker meanwhile finds it still there
            return taken

        @cause.setter
        def cause(self, value):
            self._cause = value

    monkeypatch.setattr(scenes, "_Failure", SlowFailure)
    files = counted(PermissionError(13, "Permission denied"))
    files.delay = 0.05
    barrier = threading.Barrier(8)

    def lookup(_i):
        barrier.wait()
        try:
            load_scenes("H1234")
        except GoveeBleError as err:
            return err
        return None

    with ThreadPoolExecutor(8) as pool:
        raised = list(pool.map(lookup, range(8)))
    assert files.reads == 1 and all(isinstance(e, GoveeBleError) for e in raised)
    assert sum(e.__cause__ is not None for e in raised) == 1


def test_a_callers_use_of_the_error_reaches_no_later_lookup(counted):
    """Review ST-3: the kept failure shared one cause object, so a caller's ``raise err.__cause__`` (or a note
    added to it) put its frames into that cause for the life of the process, and into every later lookup's
    error. Only the message is kept now: a later lookup's error is a fresh one, raised from None."""
    class Local:
        pass

    alive = []

    def unwrapping_caller():
        local = Local()
        alive.append(weakref.ref(local))
        try:
            load_scenes("H1234")
        except GoveeBleError as err:
            assert err.__cause__ is not None   # the lookup that read the store: from the real cause
            err.__cause__.add_note("the first caller's note")
            raise err.__cause__  # noqa: B904 - an unwrap: the cause takes this frame into its traceback

    counted(_bad([{**_A, "param": "!!"}]))
    with pytest.raises(ValueError):
        unwrapping_caller()
    gc.collect()
    assert alive[0]() is None   # the cache kept the cause, and with it this caller's frames
    with pytest.raises(GoveeBleError, match="'param' is not valid base64") as ei:
        load_scenes("H6006")
    shown = "".join(traceback.format_exception(ei.value))
    assert ei.value.__cause__ is None and "unwrapping_caller" not in shown
    assert "the first caller's note" not in shown


def _headroom() -> int:
    """How many more nested Python calls fit below the caller before a RecursionError.

    Measured, not counted from the frames: on 3.11 the recursion limit also counts C-level calls (pytest's
    among them), so a frame count says there is room where there is none."""
    calls = 0

    def probe() -> None:
        nonlocal calls
        calls += 1
        probe()

    with contextlib.suppress(RecursionError):
        probe()
    return calls


def test_a_recursion_error_is_not_kept(counted, monkeypatch):
    """Review: a RecursionError is a RuntimeError, so the read kept it as an unreadable store (and the check
    as a malformed one), and a first lookup made near the recursion limit left the store unreadable for good.
    It is raised as a GoveeBleError and not kept, in both, so the next lookup reads the store again."""
    files = counted(RecursionError("maximum recursion depth exceeded"))
    with pytest.raises(GoveeBleError, match="could not be read: RecursionError") as ei:
        load_scenes("H1234")
    assert isinstance(ei.value.__cause__, RecursionError)   # review H3 (L12): raised from it
    files._store = scene_store("H1234", {"a": {"code": 3}})
    assert load_scenes("H1234") == {"a": scenes.Scene("a", 3, None)} and files.reads == 2
    scenes._store.cache_clear()
    check = scenes._catalogs
    calls = []

    def once(store):
        calls.append(1)
        if len(calls) == 1:
            raise RecursionError("maximum recursion depth exceeded")
        return check(store)

    monkeypatch.setattr(scenes, "_catalogs", once)
    with pytest.raises(GoveeBleError, match="is malformed: RecursionError") as ei:
        load_scenes("H1234")
    assert isinstance(ei.value.__cause__, RecursionError)
    assert load_scenes("H1234") == {"a": scenes.Scene("a", 3, None)} and files.reads == 4


def test_a_recursion_error_of_the_call_to_the_store_is_not_kept(counted, monkeypatch):
    """Review H4: a lookup a frame from the recursion limit raised its RecursionError from the call to _store
    itself, before _store's try blocks, so it escaped as itself, past a caller's ``except GoveeBleError``
    (Home Assistant's setup). It is raised as a GoveeBleError from it, and not kept."""
    files = counted(scene_store("H1234", {"a": {"code": 3}}))
    real, calls = scenes._store, []

    def store():
        calls.append(1)
        if len(calls) == 1:
            raise RecursionError("maximum recursion depth exceeded")
        return real()

    def at_the_limit(_err):   # the handler runs at the limit too: a Python call there would raise again
        raise RecursionError("maximum recursion depth exceeded")

    store.cache_clear = real.cache_clear   # type: ignore[attr-defined]
    monkeypatch.setattr(scenes, "_store", store)
    monkeypatch.setattr(scenes, "_named", at_the_limit)   # as the read's handler calls it
    with pytest.raises(GoveeBleError, match="could not be read: RecursionError: maximum recursion") as ei:
        load_scenes("H1234")
    assert isinstance(ei.value.__cause__, RecursionError)
    assert load_scenes("H1234") == {"a": scenes.Scene("a", 3, None)} and files.reads == 1


def test_a_deep_first_lookup_leaves_the_store_readable(counted):
    """Review: a first lookup made a few frames short of the recursion limit, then a shallow one, which reads
    the store. Each depth from 1 to 79 frames short of the limit is tried on a fresh cache."""
    counted(scene_store("H1234", {"a": {"code": 3}}))
    failed: list[BaseException] = []

    def deep(remaining):
        if remaining > 0:
            return deep(remaining - 1)
        try:
            load_scenes("H1234")
        except (GoveeBleError, RecursionError) as err:
            failed.append(err)
        return None

    for short in range(1, 80):
        scenes._store.cache_clear()
        deep(_headroom() - short)
        assert load_scenes("H1234") == {"a": scenes.Scene("a", 3, None)}, short
    assert any(isinstance(e, GoveeBleError) and isinstance(e.__cause__, RecursionError) for e in failed)


def test_a_lookup_the_first_read_makes_on_its_thread_returns(counted, monkeypatch):
    """Review ST-1: _STORE_LOCK was a plain Lock held across the first read, so a lookup that read made on
    its own thread (a signal handler, a finalizer or a trace hook run inside it) deadlocked that thread and
    every later lookup. A re-entrant lookup from inside the first read (here from _catalogs) returns."""
    files = counted(scene_store("H1234", {"a": {"code": 3}}))
    # a fresh lock of its kind, held by no other test (3.11 and 3.12 can't create a _thread.lock by its type)
    kind = threading.RLock if isinstance(scenes._STORE_LOCK, type(threading.RLock())) else threading.Lock
    monkeypatch.setattr(scenes, "_STORE_LOCK", kind())
    check, entered, inner, outer = scenes._catalogs, [], [], []

    def reentrant(store):
        if not entered:
            entered.append(True)
            inner.append(load_scenes("H1234"))   # the same thread, the first read under way
        return check(store)

    monkeypatch.setattr(scenes, "_catalogs", reentrant)
    thread = threading.Thread(target=lambda: outer.append(load_scenes("H1234")), daemon=True)
    thread.start()
    thread.join(10)
    assert not thread.is_alive(), "the lookup inside the first read deadlocked _STORE_LOCK"
    assert inner == outer == [{"a": scenes.Scene("a", 3, None)}] and files.reads == 2
    assert load_scenes("H6006") == {} and files.reads == 2   # read and kept: later lookups read nothing


def test_a_read_error_of_the_process_is_not_kept(counted):
    files = counted(OSError(errno.EMFILE, "Too many open files"))
    with pytest.raises(GoveeBleError, match=r"could not be read: OSError: .*Too many open files") as ei:
        load_scenes("H1234")
    assert isinstance(ei.value.__cause__, OSError) and ei.value.__cause__.errno == errno.EMFILE   # H3 (L12)
    files._store = scene_store("H1234", {"a": {"code": 3}})   # the process has file descriptors again
    assert load_scenes("H1234") == {"a": scenes.Scene("a", 3, None)} and files.reads == 2


async def test_every_lookup_at_every_set_goes_through_the_one_catalog_call(monkeypatch):
    """Every scene lookup, at every scene set, is one call of ``scenes._catalog(sku)``, which its callers pick
    the set from: hass-govee-ble-local's tests make the store unreadable by patching that call with a
    one-argument stand-in that raises (its broken_scene_store fixture), so a lookup must raise the stand-in's
    GoveeBleError, never a TypeError of a second argument. A device's sync scene properties make that call
    only until a lookup fails: then they raise its failure with no call (the hardening follow-up's H1), so
    active_scene is read on a device of its own."""
    called: list[str] = []

    def unreadable(sku):
        called.append(sku)
        raise GoveeBleError("the bundled scene store could not be read: FileNotFoundError")

    monkeypatch.setattr(scenes, "_catalog", unreadable)
    dev = make_device(ble_device("AA:BB:CC:DD:EE:5E", "H61A8"), "H61A8")
    other = make_device(ble_device("AA:BB:CC:DD:EE:5F", "H61A8"), "H61A8")
    assert dev is not None and other is not None
    dev._state.scene_code = other._state.scene_code = 110
    lookups = [lambda: load_scenes("H61A8"), lambda: load_scenes("H61A8", scene_versions={0, 1, 2}),
               lambda: scene_names_by_code("H61A8", scene_versions=()), lambda: dev.scene_names,
               lambda: other.active_scene]
    for lookup in lookups:
        with pytest.raises(GoveeBleError, match="could not be read"):
            lookup()
    for coro in (lambda: async_load_scenes("H61A8", scene_versions=[0, 1, 2]),
                 lambda: dev.set_scene_by_name("Halloween")):
        with pytest.raises(GoveeBleError, match="could not be read"):
            await coro()
    assert called == ["H61A8"] * 7
    for prop in (lambda: dev.active_scene, lambda: other.scene_names):   # after a failed lookup: no call
        with pytest.raises(GoveeBleError, match="could not be read"):
            prop()
    assert called == ["H61A8"] * 7


async def test_device_scene_properties_need_no_io_once_loaded(counted):
    """Once the store is loaded (update() loads it off the loop), Device.scene_names and active_scene read no
    file and raise nothing, at every scene set the device can be at: each set's catalog is built with the
    store, and the lookup is from memory."""
    d = make_device(ble_device("AA:BB:CC:DD:EE:5B", "H6008"), "H6008")
    h61a8 = make_device(ble_device("AA:BB:CC:DD:EE:5C", "H61A8"), "H61A8")
    assert d is not None and h61a8 is not None
    names = sorted(await scenes.async_load_scenes("H6008"))   # the real store, loaded as update() does
    d._state.scene_code = load_scenes("H6008")["Aurora"].code
    unreadable = counted(OSError(5, "I/O error"), clear=False)   # a read now would raise
    assert d.scene_names == names and d.active_scene == "Aurora"
    h61a8._state.scene_code = 2497
    assert len(h61a8.scene_names) == EXPECTED["H61A8"] and h61a8.active_scene == "Halloween"
    # every bundled SKU, at every set its versions and pact reach (unread, below each gate, at each gate)
    for sku in _store()["skus"]:
        p = profile_for(sku, allow_modelled=True)
        assert p is not None
        dev = Device(p, ble_device("AA:BB:CC:DD:EE:5D", sku), sku=sku)
        gates = [g for s, h, _v in p.scene_versions_by_firmware for g in (s, h) if g]
        reached = set()
        for pact in (None, *(key for key, _v in p.scene_versions_by_pact)):
            for soft, hard in itertools.product((None, "0.00.00", *gates), repeat=2):
                dev._pact, dev._state.firmware_version, dev._state.hardware_version = pact, soft, hard
                reached.add(dev.scene_versions)
                codes = scene_names_by_code(sku, scene_versions=dev.scene_versions)
                assert dev.scene_names == sorted(load_scenes(sku, scene_versions=dev.scene_versions))
                for code in (*codes, 0x10000):
                    dev._state.scene_code = code
                    assert dev.active_scene == codes.get(code)
        assert reached <= set(_sets(p)), sku
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
    at = load_scenes("H6006", scene_versions={0, 1})
    at.clear()
    assert len(load_scenes("H6006", scene_versions={0, 1})) == n


def test_no_legacy_scene_catalog_holds_a_type5_scene_the_app_uploads():
    """devices.schema.json scene_dialect: the library only activates a sceneType-5 scene under legacy_scene,
    so a legacy_scene SKU's bundled catalog holds none the app uploads, at any of its scene sets: none whose
    param leads 0x50 (the new-DIY parser a goodsType registers, BaseSceneViewMode.r3), and none at all where
    its versions hold one of parseSceneV1's sceneType-5 branches (7 to 13)."""
    for sku in _store()["skus"]:
        p = profile_for(sku, allow_modelled=True)
        if p is not None and p.scene_dialect != "A":
            continue   # H60A6, H6052: a sceneType-5 dialect of their own
        sets = [] if p is None else _sets(p)
        type5_branch = any(7 <= v <= 13 for s in sets for v in s)
        for at in (None, *sets):
            for scene in load_scenes(sku, scene_versions=at).values():
                raw = base64.b64decode(scene.param or "")
                new_diy = len(raw) >= 2 and raw[0] == 0x50
                assert scene.scene_type != 5 or not (type5_branch or new_diy), (sku, at, scene.name)


def test_the_bundled_scenes_are_ones_the_app_lists():
    """devices.yaml scenes.list_filter, at every scene set of each catalog's SKU (the row's, each by_pact and
    each by_firmware one), with no exemption: the catalog the library offers there (``load_scenes`` at that
    set) is what the app's list filter leaves, as far as the store can show it.

    (i) At a non-empty set, every scene offered has an op type (its scene type and cmdVersion for the SKU) in
    the set: the filter drops each effect of another.
    (ii) At a non-empty set, every scene the store hides or replaces there has an entry (its pick at the union
    of the SKU's sets) whose op type is not in the set, and a replaced one plays an entry of its category:
    each stored per-set result is one the filter needs, since the effects before the union's pick fail at any
    smaller set too (tools/fetch_scenes.fetch).
    (iii) Every set the store records a result at is one of the SKU's: none is stale.
    (iv) Before the device-info read (every version unread; at each pact the profile lists, and at none) the
    catalog offered is the whole one, so the read can only shrink the list.

    An empty set is a null op set, which filters no op type: only (iii) holds there. Which effect left plays,
    the first in the library's order, the store alone can't show: tools/fetch_scenes.fetch runs the whole
    filter over the library's response at every set. (The app lets a base sceneType-0 effect through any
    set; a catalog keeps the SKU's type only, so a base-static scene typed otherwise for its SKU would be
    named here too.)"""
    store = _store()
    for sku in store["skus"]:
        p = profile_for(sku, allow_modelled=True)
        assert p is not None, sku
        whole = load_scenes(sku)
        sets = _sets(p)
        keys = {",".join(map(str, sorted(s))) for s in sets}
        assert set(store["by_set"].get(sku, {})) <= keys, sku   # (iii)
        for versions in sets:
            if not versions:
                continue
            at = load_scenes(sku, scene_versions=versions)
            where = (sku, sorted(versions))
            outside = [n for n, s in at.items() if _op_type(s.scene_type, s.cmd_version) not in versions]
            assert outside == [], where   # (i)
            changed = {n for n, s in whole.items() if at.get(n) != s}
            assert [n for n in changed if _op_type(whole[n].scene_type, whole[n].cmd_version) in versions] \
                == [], where   # (ii)
            assert all(at[n].category == whole[n].category for n in changed & set(at)), where
        for pact in (None, *(key for key, _v in p.scene_versions_by_pact)):   # (iv)
            pre_read = _fold.scene_versions(p, None, None, pact)
            assert load_scenes(sku, scene_versions=pre_read) == whole, (sku, pact)


def test_the_scene_store_is_the_device_tables():
    """The store and the table agree: the SKUs with a catalog are exactly the ones offered scenes (a SKU
    without one is not: _resolve's module doc). The store is tools/fetch_scenes.py's layout, an object of
    ``pool``, ``skus`` and ``by_set``: the pool holds each scene once, sorted by content (its name, then its
    JSON with sorted keys), every entry some SKU's catalog's or some override's (no orphan), in the tool's
    JSON form, and it is the package's only JSON. EXPECTED pins each catalog's size, so a re-pointed or
    dropped catalog fails, RELEASED each catalog to its content (every Scene field), so a change to one of
    them fails even at the same count, and AT_SET each catalog the store records at a scene set to
    its size and content, so every catalog is content-pinned at every set it differs at."""
    files = resources.files(scenes)
    text = (files / "store.json").read_text(encoding="utf-8")
    store = json.loads(text)
    assert set(store) == {"pool", "skus", "by_set"}
    assert set(RELEASED) == set(EXPECTED)   # every catalog content-pinned
    assert {sku: _content(sku) for sku in RELEASED} == RELEASED
    recorded = {(sku, tuple(int(v) for v in key.split(",")) if key else ())
                for sku, sets in store["by_set"].items() for key in sets}
    assert recorded == set(AT_SET)
    assert {at: (len(load_scenes(at[0], scene_versions=at[1])), _content(*at)) for at in AT_SET} == AT_SET
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
    refs = ({i for r in store["skus"].values() for i in r}
            | {i for sets in store["by_set"].values() for names in sets.values()
               for i in names.values() if i is not None})
    assert refs == set(range(len(keys)))
    assert text == json.dumps(store, indent=1, sort_keys=True)
    assert [f.name for f in files.iterdir() if f.name.endswith(".json")] == ["store.json"]
