"""tools/fetch_scenes.py writes only a store the loader reads: its entry check is the loader's, its layout
refuses two catalogs of one SKU, it checks the store before writing it, and it leaves the store 0644. The
tombstone holds the sha256 of the store it was written with, the committed pair agrees, and main() and
--resolve refuse to run over a tombstone that is missing or not the store's. A library value fetch() reads as
an int that is no int fails the SKU's fetch."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import stat
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from conftest import SETS_SKU, FakeSceneFiles, with_scene_sets

from govee_ble_local import scenes

_TOOL = Path(__file__).resolve().parent.parent / "tools" / "fetch_scenes.py"
_STORE = Path(__file__).resolve().parent.parent / "src" / "govee_ble_local" / "scenes" / "store.json"


def _fs() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_tool_fetch_scenes_store", _TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_scene_the_loader_refuses_is_not_written(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'code' must be an int"):   # the loader read "7" as 7
        _fs().store_catalog("H1234", {"s": {"code": "7", "param": None}}, tmp_path)
    assert not (tmp_path / "store.json").exists()


@pytest.mark.parametrize("entry", [
    {"code": 0},
    {"code": 0xFFFF, "param": "kwr/", "param_id": 7, "scene_type": 1, "cmd_version": 0, "placeholder": True,
     "big_effect": False, "scene_name": "s", "rules": None},
    {"code": 1, "param": "", "category": "c", "placeholder": None, "big_effect": None},
])
def test_a_written_scene_loads(entry: dict[str, Any], tmp_path: Path,
                               monkeypatch: pytest.MonkeyPatch) -> None:
    _fs().store_catalog("H1234", {"s": entry}, tmp_path)   # what the tool writes, the loader reads
    text = (tmp_path / "store.json").read_text(encoding="utf-8")
    scenes._store.cache_clear()
    monkeypatch.setattr(scenes, "resources", SimpleNamespace(files=lambda _pkg: FakeSceneFiles(text)))
    try:
        assert scenes.load_scenes("H1234")["s"].code == entry["code"]
    finally:
        scenes._store.cache_clear()


def test_validate_entry_is_the_loaders_check(monkeypatch: pytest.MonkeyPatch) -> None:
    fs = _fs()
    calls: list[tuple[object, object]] = []
    monkeypatch.setattr(scenes, "_validate_entry", lambda name, entry: calls.append((name, entry)))
    fs.validate_entry("s", {"code": "anything"})
    assert calls == [("s", {"code": "anything"})]


def test_layout_refuses_two_catalogs_of_one_sku() -> None:
    fs = _fs()
    x, y = {"code": 1, "param": None}, {"code": 2, "param": None}
    for catalogs in ({"h6006": {"x": x}, "H6006": {"y": y}},     # merged, the union of both
                     {"H6006": {"x": x}, "h6006": {"x": x}}):    # merged, one scene twice
        with pytest.raises(ValueError, match="H6006: two catalogs"):
            fs.layout(catalogs)
    assert fs.layout({"h6006": {"x": x}})["skus"] == {"H6006": [0]}   # one spelling is the SKU's


def test_a_store_the_loader_refuses_is_not_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fs = _fs()
    a, b = {"code": 1, "param": None}, {"code": 2, "param": None}
    fs.store_catalog("H1234", {"a": a}, tmp_path)
    before = (tmp_path / "store.json").read_bytes()
    unsorted = {"pool": [{"name": "b", **b}, {"name": "a", **a}], "skus": {"H1234": [0, 1]}, "by_set": {}}
    monkeypatch.setattr(fs, "layout", lambda _catalogs: unsorted)
    with pytest.raises(ValueError, match="sorted by content"):
        fs.store_catalog("H1234", {"a": a, "b": b}, tmp_path)
    assert (tmp_path / "store.json").read_bytes() == before


def test_read_store_refuses_a_store_nested_too_deep(tmp_path: Path) -> None:
    (tmp_path / "store.json").write_text("[" * 100_000, encoding="utf-8")
    with pytest.raises(ValueError, match=r"store\.json is malformed"):
        _fs().read_store(tmp_path)


def test_a_file_that_is_not_utf8_is_not_quoted(tmp_path: Path) -> None:
    fs = _fs()
    blob = base64.b64encode(b"a planted effect blob, never quoted " * 4).decode()
    fs.store_catalog("H1234", {"a": {"code": 1, "param": blob}}, tmp_path)
    store = tmp_path / "store.json"
    store.write_bytes(store.read_bytes() + b"\xff")   # the repr of its UnicodeDecodeError holds every byte
    tomb = tmp_path / "tombstone.json"
    tomb.write_bytes(json.dumps({"H1234": {"retired": {"a": {"code": 1, "param": blob}}}}).encode() + b"\xff")
    for read in (lambda: fs.read_store(tmp_path), lambda: fs.read_tombstone(tomb)):
        with pytest.raises(ValueError, match="is malformed: UnicodeDecodeError") as ei:
            read()
        assert blob not in str(ei.value)


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_a_written_store_is_0644(tmp_path: Path) -> None:
    fs = _fs()
    path = tmp_path / "store.json"
    for code in (1, 2):   # a new store, then a rewrite: the temp file mkstemp makes is 0600
        fs.store_catalog("H1234", {"a": {"code": code, "param": None}}, tmp_path)
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
        path.chmod(0o600)   # the mode the store had is not the one it gets


# -- per-set results (review G1): the app's list filter at each of the SKU's scene sets --------------------

_RULES = [{"hardVersion": "1.00.01", "softVersion": "1.05.00"}]
_SKU = SETS_SKU


def _op3(code: int, param: str) -> dict[str, Any]:
    return {"sceneCode": code, "sceneType": 2, "cmdVersion": 1, "scenceParam": param}   # RGBIC, op type 3


def _op1(code: int, param: str, **more: Any) -> dict[str, Any]:
    return {"sceneCode": code, "sceneType": 1, "cmdVersion": 0, "scenceParam": param, **more}   # RGB, op 1


def _listing(*scenes: tuple[str, str, list[dict[str, Any]]]) -> bytes:
    """A library response of ``scenes`` ((category, sceneName, lightEffects), ...), in that order."""
    cats: dict[str, list[dict[str, Any]]] = {}
    for category, name, effects in scenes:
        cats.setdefault(category, []).append({"sceneName": name, "lightEffects": effects})
    categories = [{"categoryName": c, "scenes": s} for c, s in cats.items()]
    return json.dumps({"data": {"categories": categories}}).encode()


class _Resp(io.BytesIO):
    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        return None


@pytest.fixture
def sets_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The tool with its store and tombstone in ``tmp_path``, and a row for _SKU whose scene sets are
    {0, 1, 2, 3} (the union, pre-read), {0, 1, 2} and the empty one (conftest.with_scene_sets)."""
    fs = _fs()
    monkeypatch.setattr(fs, "OUT_DIR", tmp_path)
    monkeypatch.setattr(fs, "TOMBSTONE", tmp_path / "tombstone.json")
    with_scene_sets(fs, monkeypatch)
    assert fs.op_set(_SKU) == {0, 1, 2, 3}
    assert fs._smaller_sets(_SKU) == [("", frozenset()), ("0,1,2", frozenset({0, 1, 2}))]
    return fs


def _serve(monkeypatch: pytest.MonkeyPatch, fs: ModuleType, body: bytes) -> None:
    """The library answers ``body``."""
    monkeypatch.setattr(fs.urllib.request, "urlopen", lambda *a, **k: _Resp(body))


def test_fetch_stores_what_the_app_lists_at_each_scene_set(sets_tool: ModuleType, tmp_path: Path,
                                                           capsys: pytest.CaptureFixture[str],
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """At {0, 1, 2} the app's list drops the op-type-3 effects: a scene with an op-1 effect after it plays
    that one there, one with none left is not listed there, and one whose effect left first has version rules
    is not listed there either (the library can't follow them), printed, its entry at the union as without
    G1, as is one whose effect left first there is one the shared entry check refuses (a code past 0xFFFF):
    never a failed fetch. A scene only the empty set lists
    (op type 5, which the union drops) is left out, printed. What the tool writes, its store read back and
    the library's loader give at each set, each scene's result at a set printed by name; a re-fetch changes
    nothing."""
    fs = sets_tool
    _serve(monkeypatch, fs, _listing(("c", "Twice", [_op3(10, "AAAA"), _op1(11, "BBBB")]),
                      ("c", "Gone", [_op3(20, "CCCC")]),
                      ("c", "Ruled", [_op3(30, "DDDD"), _op1(31, "EEEE", rules=_RULES)]),
                      ("c", "OnlyEmpty", [{"sceneCode": 40, "sceneType": 5, "scenceParam": "AAAA"}]),
                      ("c", "Plain", [_op1(50, "FFFF")]),
                      ("c", "Bad", [_op3(80, "IIII"), {"sceneCode": 0x10000, "sceneType": 1,
                                                        "scenceParam": "JJJJ"}])))
    union = {n: {"code": code, "param": param, "category": "c", "scene_type": 2, "cmd_version": 1}
             for n, code, param in (("Twice", 10, "AAAA"), ("Gone", 20, "CCCC"), ("Ruled", 30, "DDDD"),
                                    ("Bad", 80, "IIII"))}
    plain = {"code": 50, "param": "FFFF", "category": "c", "scene_type": 1, "cmd_version": 0}
    hidden = {"by_set": {"0,1,2": None}}
    expected = {"Twice": {**union["Twice"], "by_set": {"0,1,2": {**plain, "code": 11, "param": "BBBB"}}},
                "Gone": {**union["Gone"], **hidden}, "Ruled": {**union["Ruled"], **hidden}, "Plain": plain,
                "Bad": {**union["Bad"], **hidden}}
    assert fs.fetch(_SKU) == expected
    out = capsys.readouterr().out
    assert (f"{_SKU}: left out 1 scenes the app lists only at its empty scene set (no op filter): "
            "OnlyEmpty") in out
    assert (f"{_SKU}: hidden at scene set [0, 1, 2], where the app plays an effect the library can't store "
            "(version rules / no code / sceneType 5 / placeholder / invalid): 2 scenes: Ruled, Bad") in out
    assert fs.main([_SKU]) == 0
    out = capsys.readouterr().out
    assert (f'{_SKU}: 4 scenes change at scene set [0, 1, 2]: "Bad" hidden, "Gone" hidden, '
            '"Ruled" hidden, "Twice" plays code 11, c') in out and "change at scene set []" not in out
    assert "4 scenes differ at a smaller scene set" in out
    assert fs.read_catalog(_SKU) == expected   # layout, then read_store: the tool's form back
    text = (tmp_path / "store.json").read_text(encoding="utf-8")
    store = json.loads(text)
    twice = store["pool"].index({"name": "Twice", **expected["Twice"]["by_set"]["0,1,2"]})
    assert store["by_set"] == {_SKU: {"0,1,2": {"Bad": None, "Gone": None, "Ruled": None, "Twice": twice}}}
    assert fs.main([_SKU]) == 0   # an identical re-fetch changes nothing, and prints no per-set change
    assert (tmp_path / "store.json").read_text(encoding="utf-8") == text
    assert "change at scene set" not in capsys.readouterr().out
    assert fs.read_tombstone() == {"store_sha256": hashlib.sha256(text.encode()).hexdigest()}   # no record
    scenes._store.cache_clear()
    monkeypatch.setattr(scenes, "resources", SimpleNamespace(files=lambda _pkg: FakeSceneFiles(text)))
    try:
        assert {n: s.code for n, s in scenes.load_scenes(_SKU).items()} == {
            "Bad": 80, "Gone": 20, "Plain": 50, "Ruled": 30, "Twice": 10}
        assert {n: s.code for n, s in scenes.load_scenes(_SKU, scene_versions={0, 1, 2}).items()} == {
            "Plain": 50, "Twice": 11}
        assert scenes.load_scenes(_SKU, scene_versions=()) == scenes.load_scenes(_SKU)
    finally:
        scenes._store.cache_clear()


def test_a_pick_at_a_smaller_set_that_is_no_int_fails_the_fetch(sets_tool: ModuleType,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """A code no int holds (JSON reads Infinity, or 1e999, as a float) on the effect a smaller set plays
    fails the SKU's fetch, as a value that is no int does on any lightEffect (_check_ints, the naming
    follow-up's F2 guard; the app's Gson fails the whole response), with nothing written."""
    fs = sets_tool
    inf = {"sceneCode": float("inf"), "sceneType": 1, "scenceParam": "LLLL"}   # json.dumps writes Infinity
    _serve(monkeypatch, fs, _listing(("c", "Inf", [_op3(90, "KKKK"), inf])))
    with pytest.raises(ValueError, match="'sceneCode' must be an int or null, got float"):
        fs.fetch(_SKU)
    assert fs.main([_SKU]) == 1 and not (fs.OUT_DIR / "store.json").exists()


def test_naming_ignores_the_per_set_results(sets_tool: ModuleType, capsys: pytest.CaptureFixture[str],
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """Two alike scenes (one sceneName, category and entry at the union) keep their names when only the
    effect they play at {0, 1, 2} changes on a re-fetch: naming matches a stored scene by its entry at the
    union, never by its per-set results, and each such change is printed by name, per set (a new entry
    there, then hidden there). A name the store gives up is retired with that entry only."""
    fs = sets_tool

    def twins(alternate: str) -> list[tuple[str, str, list[dict[str, Any]]]]:
        return [("Festival", "Halloween", [_op3(2497, "kwE="), _op1(110, alternate)])] * 2

    _serve(monkeypatch, fs, _listing(*twins("kwI="), ("c", "Twice", [_op3(10, "AAAA"), _op1(11, "BBBB")])))
    assert fs.main([_SKU]) == 0
    names = fs.read_catalog(_SKU)
    assert set(names) == {"Halloween", "Halloween (Festival)", "Twice"}
    capsys.readouterr()
    _serve(monkeypatch, fs, _listing(*twins("kwM="), ("c", "Twice", [_op3(10, "AAAA"), _op1(11, "BBBB")])))
    assert fs.main([_SKU]) == 0
    again = fs.read_catalog(_SKU)
    out = capsys.readouterr().out
    assert set(again) == set(names) and "ambiguous" not in out
    assert (f'{_SKU}: 2 scenes change at scene set [0, 1, 2]: "Halloween" changes from code 110, Festival to '
            'code 110, Festival, a new entry, "Halloween (Festival)" changes from code 110, Festival to code '
            '110, Festival, a new entry') in out
    assert {n: e["by_set"]["0,1,2"]["param"] for n, e in again.items() if n != "Twice"} == {
        "Halloween": "kwM=", "Halloween (Festival)": "kwM="}
    assert {n: fs._default(e) for n, e in again.items()} == {n: fs._default(e) for n, e in names.items()}
    # Twice loses its op-1 effect: not listed at {0, 1, 2} now; its name and entry at the union stay
    _serve(monkeypatch, fs, _listing(*twins("kwM="), ("c", "Twice", [_op3(10, "AAAA")])))
    assert fs.main([_SKU]) == 0
    out = capsys.readouterr().out
    assert f'{_SKU}: 1 scenes change at scene set [0, 1, 2]: "Twice" hidden' in out
    assert fs.read_catalog(_SKU)["Twice"] == {**fs._default(names["Twice"]), "by_set": {"0,1,2": None}}
    # ... and gets it back: listed again there, with that effect
    _serve(monkeypatch, fs, _listing(*twins("kwM="), ("c", "Twice", [_op3(10, "AAAA"), _op1(11, "BBBB")])))
    assert fs.main([_SKU]) == 0
    assert (f'{_SKU}: 1 scenes change at scene set [0, 1, 2]: "Twice" listed again, plays code 11, c'
            ) in capsys.readouterr().out
    # Twice's scene is refused for good now (its only effect is op type 5, at its code and blob): removed,
    # and retired with its entry at the union, not its per-set results
    refused = {"sceneCode": 10, "sceneType": 5, "scenceParam": "AAAA"}
    _serve(monkeypatch, fs, _listing(*twins("kwM="), ("c", "Twice", [refused])))
    assert fs.main([_SKU]) == 0
    assert set(fs.read_catalog(_SKU)) == {"Halloween", "Halloween (Festival)"}
    assert fs.read_tombstone()[_SKU] == {"retired": {"Twice": fs._default(names["Twice"])}}
    assert "by_set" not in fs.read_tombstone()[_SKU]["retired"]["Twice"]


def test_twins_take_the_order_of_their_content(sets_tool: ModuleType, capsys: pytest.CaptureFixture[str],
                                               monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Review G1 item 4: twins (one sceneName and one entry at the union) that play different effects at
    {0, 1, 2} keep which name plays which effect whatever order the library lists them in: a reordered
    response changes nothing, and a first fetch of either order names them alike (the order of their whole
    lightEffect content, not the response's)."""
    fs = sets_tool
    a = ("Festival", "Halloween", [_op3(2497, "kwE="), _op1(110, "kwI=")])
    b = ("Festival", "Halloween", [_op3(2497, "kwE="), _op1(111, "kwM=")])

    def played() -> dict[str, int]:
        return {n: e["by_set"]["0,1,2"]["code"] for n, e in fs.read_catalog(_SKU).items()}

    _serve(monkeypatch, fs, _listing(a, b))
    assert fs.main([_SKU]) == 0
    first = played()
    assert sorted(first.values()) == [110, 111]
    text = (tmp_path / "store.json").read_text(encoding="utf-8")
    capsys.readouterr()
    _serve(monkeypatch, fs, _listing(b, a))   # the response reordered
    assert fs.main([_SKU]) == 0
    assert played() == first and (tmp_path / "store.json").read_text(encoding="utf-8") == text
    assert "change at scene set" not in capsys.readouterr().out
    (tmp_path / "store.json").unlink()   # a first fetch of the other order names them alike
    assert fs.main([_SKU]) == 0
    assert played() == first


def test_a_kept_scenes_per_set_effect_the_type5_cut_drops_is_not_listed_there(
        sets_tool: ModuleType, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """main(): a stored scene the fetch no longer lists is kept, and its stored effect at a set that the
    sceneType-5 cut now drops (a 0x50 new-DIY blob, which the library does not upload under legacy_scene) is
    not listed at that set, printed, rather than the scene dropped."""
    fs = sets_tool
    diy = {"code": 7, "param": base64.b64encode(b"\x50\x01").decode(), "category": "c", "scene_type": 5}
    kept = {"code": 6, "param": "AAAA", "category": "c", "scene_type": 2, "cmd_version": 1}
    fs.store_catalog(_SKU, {"Kept": {**kept, "by_set": {"0,1,2": diy}}})   # stored before the cut
    monkeypatch.setattr(fs, "fetch", lambda sku, app_version="", **_kw: {"Other": {"code": 8, "param": None}})
    assert fs.main([_SKU]) == 0
    out = capsys.readouterr().out
    assert (f"{_SKU}: hidden at scene set [0, 1, 2], where the app plays an effect the library can't store "
            "(sceneType 5): 1 scenes: Kept") in out
    assert f'{_SKU}: 1 scenes change at scene set [0, 1, 2]: "Kept" hidden' in out
    assert fs.read_catalog(_SKU) == {"Kept": {**kept, "by_set": {"0,1,2": None}},
                                     "Other": {"code": 8, "param": None}}


def test_a_kept_scene_follows_the_skus_scene_sets(sets_tool: ModuleType, capsys: pytest.CaptureFixture[str],
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Review G1 item 5: a stored scene the fetch no longer lists keeps its per-set results at the SKU's
    current scene sets only: one at a set the SKU no longer has ("0,1") is dropped, printed, and at a set it
    has none for (one added since) it is hidden where its entry's op type is outside the set, so the store
    holds what F3 asks (an op type 3 is not offered at {0, 1, 2}); an op-1 one, a base static one and the
    empty set (no op filter) keep the entry at the union."""
    fs = sets_tool
    op3 = {"code": 6, "param": "AAAA", "category": "c", "scene_type": 2, "cmd_version": 1}
    op1 = {"code": 7, "param": "BBBB", "category": "c", "scene_type": 1, "cmd_version": 0}
    static = {"code": 9, "param": None, "category": "c", "scene_type": 0}
    fs.store_catalog(_SKU, {"Kept": {**op3, "by_set": {"0,1": None}}, "Rgb": {**op1, "by_set": {"0,1": None}},
                            "Static": static})
    monkeypatch.setattr(fs, "fetch", lambda sku, app_version="", **_kw: {"Other": {"code": 8, "param": None}})
    assert fs.main([_SKU]) == 0
    out = capsys.readouterr().out
    assert (f"{_SKU}: scene set [0, 1] is none of the SKU's now: the results there of 2 stored scenes are "
            "dropped: Kept, Rgb") in out
    assert f'{_SKU}: 1 scenes change at scene set [0, 1, 2]: "Kept" hidden' in out
    assert fs.read_catalog(_SKU) == {"Kept": {**op3, "by_set": {"0,1,2": None}}, "Rgb": op1, "Static": static,
                                     "Other": {"code": 8, "param": None}}


def test_a_per_set_entry_is_checked_as_any_entry(tmp_path: Path) -> None:
    """layout checks each per-set entry with the shared entry check, and the store with the loader's rules
    for by_set: a store the loader refuses is never written."""
    fs = _fs()
    good = {"code": 1, "param": None}
    for bad, message in (({"0,1,2": {"code": "7"}}, "'code' must be an int"),
                         ({"0,1,2": {**good, "by_set": {}}}, "carries no 'by_set'"),
                         ({"0,1,2": {**good, "name": "a"}}, "carries a 'name' of its own"),
                         ([], "'by_set' is not an object"),
                         ({"2,1": {"code": 2, "param": None}}, "is not a sorted, comma-joined scene set"),
                         ({0: {"code": 2, "param": None}}, "is not a sorted, comma-joined scene set"),
                         ({"0,1,2": dict(good)}, "no other pool entry of that name")):   # the default's own
        with pytest.raises(ValueError, match=message):
            fs.store_catalog("H1234", {"a": {**good, "by_set": bad}}, tmp_path)
        assert not (tmp_path / "store.json").exists()
    with pytest.raises(ValueError, match="carries no 'by_set'"):
        fs.validate_entry("a", {**good, "by_set": {}})   # the flat check: a tombstone entry has none


def test_unstorable_alternates_are_hidden_there(sets_tool: ModuleType, capsys: pytest.CaptureFixture[str],
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch(): at {0, 1, 2} a scene whose effect left first is a sceneType-5 upload the library does not
    make (a base sceneType 0, which passes any op set, whose special for the SKU is a 0x50 new-DIY blob) or a
    placeholder (a big effect with no blob) is not listed there, printed, rather than stored as the effect it
    plays there."""
    fs = sets_tool
    special = {"supportSku": [_SKU], "sceneCode": 61, "sceneType": 5, "scenceParam": "UAEC"}   # 50 01 02
    diy = {"sceneCode": 61, "sceneType": 0, "scenceParam": "", "specialEffect": [special]}
    big = {"sceneCode": 71, "sceneType": 1, "cmdVersion": 0, "scenceParam": "", "bigEffectStr": 1}
    _serve(monkeypatch, fs, _listing(("c", "Diy", [_op3(60, "GGGG"), diy]),
                                     ("c", "Big", [_op3(70, "HHHH"), big]),
                                     ("c", "Plain", [_op1(50, "FFFF")])))
    got = fs.fetch(_SKU)
    assert got["Diy"]["by_set"] == {"0,1,2": None} and got["Big"]["by_set"] == {"0,1,2": None}
    assert (f"{_SKU}: hidden at scene set [0, 1, 2], where the app plays an effect the library can't store "
            "(version rules / no code / sceneType 5 / placeholder / invalid): 2 scenes: Diy, Big"
            ) in capsys.readouterr().out


# A valid RGB scene blob (ScenesRgb.isValidProtocolBytes): H61A8's Halloween at code 110, three V1 frames.
_RGB = "gwb/9QAFAP///wUA/+n/BQD///8FAP/p2QUA//j/BgAE/x4A/1oA/zIA/3gA"


def test_audit_plans_each_scene_set(capsys: pytest.CaptureFixture[str],
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Review G1 item 6: --audit plans a SKU at each of its scene sets, one row each, every scene of the
    catalog offered there (the effect it plays there included) planned at that set. H6168's row has
    {0, 1, 2, 3} at part brightness (the union) and {0, 1, 2} below it; at {0, 1, 2} "Gone" is hidden and
    "Swap" plays its RGB effect, which uploads there (V1, commByte 0x01)."""
    fs = _fs()
    union = {"param": None, "category": "c", "scene_type": 2, "cmd_version": 1}   # op type 3, activate only
    catalog = {"Gone": {**union, "code": 20, "by_set": {"0,1,2": None}},
               "Swap": {**union, "code": 10,
                        "by_set": {"0,1,2": {"code": 11, "param": _RGB, "category": "c", "scene_type": 1}}},
               "Plain": {"code": 50, "param": None, "category": "c", "scene_type": 1}}
    text = json.dumps(fs.layout({"H6168": catalog}))
    scenes._store.cache_clear()
    monkeypatch.setattr(scenes, "resources", SimpleNamespace(files=lambda _pkg: FakeSceneFiles(text)))
    try:
        fs.audit(["H6168"])
    finally:
        scenes._store.cache_clear()
    rows = capsys.readouterr().out.splitlines()[1:]
    assert [" ".join(row.split()) for row in rows] == [
        "H6168 3 0 3 0 0 [0, 1, 2, 3]", "H6168 2 1['0xA3'] 1 0 0 [0, 1, 2]"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_committed_tombstone_holds_the_committed_stores_sha256() -> None:
    """Review F3: a store.json committed without the tombstone its fetch wrote fails here (a fresh checkout
    would let a new scene take a retired name silently). Run tools/fetch_scenes.py, which writes both, and
    commit both."""
    tomb = _fs().read_tombstone()
    assert tomb.get("store_sha256") == _sha256(_STORE), (
        "tools/scene_tombstone.json is not store.json's: refresh its store_sha256 with a store write "
        "(tools/fetch_scenes.py, store_catalog), never by hand, and commit the tombstone from that same run")


def test_a_store_written_with_its_tombstone_records_its_sha256(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    fs = _fs()
    monkeypatch.setattr(fs, "OUT_DIR", tmp_path)
    monkeypatch.setattr(fs, "TOMBSTONE", tmp_path / "tombstone.json")
    fs.write_catalog(fs.TOMBSTONE, {"H1234": {"dropped": ["b"]}})
    fs.store_catalog("H1234", {"a": {"code": 1, "param": None}})
    digest = _sha256(tmp_path / "store.json")
    assert fs.read_tombstone() == {"H1234": {"dropped": ["b"]}, "store_sha256": digest}
    writes: list[Path] = []
    write = fs.write_catalog
    monkeypatch.setattr(fs, "write_catalog", lambda path, data: (writes.append(path), write(path, data)))
    fs.store_catalog("H1234", {"a": {"code": 1, "param": None}})
    assert writes == [tmp_path / "store.json"]   # the same store: the tombstone is not rewritten
    fs.store_catalog("H1234", {"a": {"code": 2, "param": None}})
    assert writes[1:] == [tmp_path / "tombstone.json", tmp_path / "store.json"]   # the tombstone first
    assert fs.read_tombstone()["store_sha256"] == _sha256(tmp_path / "store.json")
    fs.store_catalog("H1234", None)   # None takes the SKU's catalog out of the store
    assert fs.read_store() == {} and fs.read_tombstone()["store_sha256"] == _sha256(tmp_path / "store.json")
    (tmp_path / "x").mkdir()
    fs.store_catalog("H1234", {"a": {"code": 3, "param": None}}, tmp_path / "x")   # a store elsewhere: none
    assert writes[-1] == tmp_path / "x" / "store.json" and fs.read_tombstone()["H1234"] == {"dropped": ["b"]}


def test_resolve_writes_the_stores_sha256(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fs = _fs()
    monkeypatch.setattr(fs, "OUT_DIR", tmp_path)
    monkeypatch.setattr(fs, "TOMBSTONE", tmp_path / "tombstone.json")
    fs.store_catalog("H1234", {"a": {"code": 1, "param": None, "param_id": 7, "placeholder": True,
                                     "big_effect": True}})

    class Account:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        async def login(self) -> None:
            return None

        async def get_scene_effect_strs(self, ids: list[int]) -> dict[int, str]:
            return {7: "kwEB"}

        async def close(self) -> None:
            return None

    cloud = ModuleType("govee_ble_local.cloud")
    cloud.GoveeCloudAccount = Account  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "govee_ble_local.cloud", cloud)
    fs.resolve_placeholders("a@b.c", "pw", ["H1234"])
    assert fs.read_catalog("H1234")["a"]["param"] == "kwEB"
    assert fs.read_tombstone()["store_sha256"] == _sha256(tmp_path / "store.json")


@pytest.mark.parametrize("digest", ["", "62608FD3" * 8, "62608fd3", 62608, None])
def test_read_tombstone_refuses_a_malformed_store_sha256(digest: object, tmp_path: Path) -> None:
    path = tmp_path / "tombstone.json"
    path.write_text(json.dumps({"store_sha256": digest}), encoding="utf-8")
    with pytest.raises(ValueError, match="is not a lower-case hex sha256"):
        _fs().read_tombstone(path)


def _library(effect: dict[str, Any], special: dict[str, Any] | None = None, *, sku: str = "H6641",
             b: dict[str, Any] | None = None, more: list[dict[str, Any]] | None = None) -> bytes:
    """A scene-library response of one Party scene "a" whose lightEffect is ``effect`` over a well-formed base
    (and ``special``, a specialEffect entry for ``sku``, where given; ``more``, its further lightEffects), and
    a scene "b" whose lightEffect is ``b`` over a base of its own (code 8, param id 6), where given."""
    eff = {"sceneCode": 7, "sceneType": 1, "scenceParam": "kwEB", "scenceParamId": 5, "cmdVersion": 0,
           **effect}
    if special is not None:
        eff["specialEffect"] = [{"supportSku": [sku], **special}]
    scenes = [{"sceneId": 1, "sceneName": "a", "lightEffects": [eff, *(more or ())]}]
    if b is not None:
        other = {"sceneCode": 8, "sceneType": 1, "scenceParam": "kwEC", "scenceParamId": 6, "cmdVersion": 0}
        scenes.append({"sceneId": 2, "sceneName": "b", "lightEffects": [{**other, **b}]})
    return json.dumps({"data": {"categories": [{"categoryName": "Party", "scenes": scenes}]}}).encode()


class _Response(io.BytesIO):
    """A library response, as urlopen's context manager gives it."""

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_a: object) -> None:
        return None


def _fetch(monkeypatch: pytest.MonkeyPatch, body: bytes) -> dict[str, Any]:
    fs = _fs()
    monkeypatch.setattr(fs.urllib.request, "urlopen", lambda _req, timeout=0: _Response(body))
    with contextlib.redirect_stdout(io.StringIO()):
        return fs.fetch("H6641")


_INTS = ("sceneCode", "scenceParamId", "sceneType", "cmdVersion", "bigEffectStr")


@pytest.mark.parametrize(("key", "value"), [(k, v) for k in _INTS for v in (True, False, 1.9, 7.0, "7")])
def test_fetch_refuses_a_value_that_is_no_int(key: str, value: object,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """The F2 guard (review ingestion): every int field fetch() reads of a lightEffect is checked before it is
    read, listed or not. A scene type 1.9, 7.0 or "7" was taken for an op type H6641 refuses, a scene refused
    for good (so, since F2, every name of a SKU whose every scene type was so was removed, and its catalog
    left the store), a code true for code 1 and a bigEffectStr true for a big effect. Every saved response
    (93 SKUs, 10,611 lightEffects and their 225,510 specialEffect entries) carries JSON ints only."""
    with pytest.raises(ValueError, match=f"'{key}' must be an int or null, got {type(value).__name__}"):
        _fetch(monkeypatch, _library({key: value}))


def test_fetch_refuses_a_special_effects_cmd_version_that_is_no_int(monkeypatch: pytest.MonkeyPatch) -> None:
    """A specialEffect's cmdVersion is 0 only where the entry has none (the DTO's int field), and a bool or a
    fraction is refused (``or 0`` took false for 0)."""
    assert _fetch(monkeypatch, _library({}, {"scenceParamId": 9}))["a"]["cmd_version"] == 0   # none: 0
    for bad in (False, True, 1.5):
        with pytest.raises(ValueError, match="'cmdVersion' must be an int"):
            _fetch(monkeypatch, _library({}, {"scenceParamId": 9, "cmdVersion": bad}))


@pytest.mark.parametrize("sku", ["H6641", "H6008"])
@pytest.mark.parametrize(("key", "value"), [(k, v) for k in _INTS for v in (True, False, 1.5, "7")])
def test_fetch_refuses_a_special_effect_value_that_is_no_int(sku: str, key: str, value: object,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """A specialEffect's code, param id or scene type false (the entry passed over), a scene type 1.5 that is
    not the effect's own, and any value of another SKU's entry are refused: Gson fails the whole response on
    any of them."""
    with pytest.raises(ValueError, match=f"'{key}' must be an int or null"):
        _fetch(monkeypatch, _library({}, {"scenceParamId": 9, key: value}, sku=sku))


@pytest.mark.parametrize(("effect", "special", "more"), [
    ({}, None, [{"sceneCode": 9, "sceneType": "1"}]),
    ({}, None, [{"sceneCode": 9, "sceneType": 1}, {"sceneCode": 10, "sceneType": 1.5}]),
    ({}, {"scenceParamId": 9},
     [{"sceneCode": 9, "specialEffect": [{"supportSku": ["H6641"], "sceneType": "1"}]}]),
], ids=["second-light-effect", "third-light-effect", "special-of-the-second"])
def test_every_light_effect_is_checked(effect: dict[str, Any], special: dict[str, Any] | None,
                                       more: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    """Review H3 (T11): a scene type that is no int on a lightEffect after the first, or on a specialEffect
    entry of one, fails the fetch as on the first."""
    with pytest.raises(ValueError, match="'sceneType' must be an int or null"):
        _fetch(monkeypatch, _library(effect, special, more=more))


def test_every_special_effect_entry_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review H3 (T11b): a scene type that is no int on a specialEffect entry after the first fails the fetch
    as on the first."""
    eff = {"sceneCode": 7, "sceneType": 1, "scenceParam": "kwEB", "scenceParamId": 5, "cmdVersion": 0,
           "specialEffect": [{"supportSku": ["H6641"], "scenceParamId": 9},
                             {"supportSku": ["H6008"], "sceneType": "1"}]}
    body = json.dumps({"data": {"categories": [{"categoryName": "Party", "scenes": [
        {"sceneId": 1, "sceneName": "a", "lightEffects": [eff]}]}]}}).encode()
    with pytest.raises(ValueError, match="'sceneType' must be an int or null"):
        _fetch(monkeypatch, body)


def _main(fs: ModuleType, monkeypatch: pytest.MonkeyPatch, body: bytes, *,
          reset: bool = False) -> tuple[int, str, str]:
    """main() for H6641 over the library response ``body``: what it returns, its stdout and its stderr."""
    monkeypatch.setattr(fs.urllib.request, "urlopen", lambda _req, timeout=0: _Response(body))
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        return fs.main(["H6641"], reset=reset), out.getvalue(), err.getvalue()


def _stored(fs: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
            first: bytes) -> tuple[bytes, bytes]:
    """The bytes of the store and the tombstone main() writes into ``tmp_path`` from ``first``."""
    monkeypatch.setattr(fs, "OUT_DIR", tmp_path)
    monkeypatch.setattr(fs, "TOMBSTONE", tmp_path / "tombstone.json")
    rc, _out, err = _main(fs, monkeypatch, first)
    assert (rc, err) == (0, "")
    return (tmp_path / "store.json").read_bytes(), fs.TOMBSTONE.read_bytes()


@pytest.mark.parametrize("then", [
    _library({"sceneType": 1.9}, b={}),
    _library({"sceneType": 1.5}, b={}),
    _library({"sceneType": "1"}, b={}),
    _library({"sceneType": "1"}, b={"sceneType": "1"}),
    _library({}, {"scenceParamId": 9, "sceneCode": False}, b={}),
    _library({}, {"scenceParamId": False}, b={}),
    _library({}, {"scenceParamId": 9, "sceneType": False}, b={}),
    _library({}, {"scenceParamId": 9, "sceneType": 1.5}, b={}),
], ids=["type-1.9", "type-1.5", "type-str", "every-type-str", "special-code-false", "special-id-false",
        "special-type-false", "special-type-1.5"])
def test_a_value_that_is_no_int_changes_no_stored_scene(then: bytes, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """The F2 guard (review ingestion follow-up): a stored scene's lightEffect at a scene type of 1.9 or "1"
    was refused for good (an op type H6641 refuses), so main() returned 0, removed its stored name and
    retired it; where every scene type was so, the SKU's whole catalog left the store (F2), every name
    retired. A specialEffect's value that is no int rewrote the store. The SKU's fetch fails now, and the
    store and the tombstone are unchanged."""
    fs = _fs()
    store, tomb = _stored(fs, monkeypatch, tmp_path, _library({}, b={}))
    assert sorted(fs.read_catalog("H6641")) == ["a", "b"]
    rc, _out, err = _main(fs, monkeypatch, then)
    assert rc == 1 and "H6641: FETCH FAILED: 'a': " in err and "must be an int or null" in err
    assert (tmp_path / "store.json").read_bytes() == store and fs.TOMBSTONE.read_bytes() == tomb


def test_a_code_true_is_no_code_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Answered.refuse took a refused effect's code true for code 1 (True == 1), so a stored scene at code 1
    was removed and retired. The SKU's fetch fails now; code 1 itself at a scene type H6641 refuses (9) is
    still refused for good."""
    fs = _fs()
    store, tomb = _stored(fs, monkeypatch, tmp_path, _library({"sceneCode": 1}, b={}))
    assert _main(fs, monkeypatch, _library({"sceneCode": True, "sceneType": 9}, b={}))[0] == 1
    assert (tmp_path / "store.json").read_bytes() == store and fs.TOMBSTONE.read_bytes() == tomb
    assert _main(fs, monkeypatch, _library({"sceneCode": 1, "sceneType": 9}, b={}))[0] == 0
    assert sorted(fs.read_catalog("H6641")) == ["b"]
    assert sorted(fs.read_tombstone()["H6641"]["retired"]) == ["a"]


def test_fetch_takes_the_int_forms_the_saved_responses_use(monkeypatch: pytest.MonkeyPatch) -> None:
    # the specialEffect's form in the saved responses: a param id and a cmdVersion, no param (scene type 0)
    entry = _fetch(monkeypatch, _library({}, {"scenceParamId": 9, "cmdVersion": 1}))["a"]
    assert entry == {"code": 7, "param": "kwEB", "category": "Party", "param_id": 9, "scene_type": 0,
                     "cmd_version": 1}
    assert _fetch(monkeypatch, _library({}))["a"] == {"code": 7, "param": "kwEB", "category": "Party",
                                                      "param_id": 5, "scene_type": 1, "cmd_version": 0}


# H6641's scene "b" refused for good (scene type 9, op type -1), then a new scene "b" at another code and blob
_REFUSE_B = _library({}, b={"sceneType": 9})
_NEW_B = _library({}, b={"sceneCode": 9, "scenceParam": "kwED", "scenceParamId": 10})


@pytest.mark.parametrize("lost", ["missing", "stale", "no-sha"])
def test_main_refuses_a_tombstone_that_is_missing_or_not_the_stores(lost: str, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Review N1 (F3-R1): main() read a missing tombstone as {} and a stale one as current, so a retired name
    went silently to a new scene (here "b", retired, to the new "b" at code 9), and the run wrote a fresh pair
    that the pairing test took. Where the store exists and the tombstone is missing or its store_sha256 is not
    the store's, every SKU fails before anything is fetched or written, and both files are left as they are;
    --reset-tombstone (``reset``) goes on over it."""
    fs = _fs()
    _stored(fs, monkeypatch, tmp_path, _library({}, b={}))
    first = fs.TOMBSTONE.read_bytes()   # the first run's tombstone: no record
    assert _main(fs, monkeypatch, _REFUSE_B)[0] == 0
    assert set(fs.read_tombstone()["H6641"]["retired"]) == {"b"}
    if lost == "missing":
        fs.TOMBSTONE.unlink()
    elif lost == "stale":
        fs.TOMBSTONE.write_bytes(first)   # its store_sha256 is the first store's
    else:
        fs.write_catalog(fs.TOMBSTONE, {k: v for k, v in fs.read_tombstone().items() if k != "store_sha256"})
    store = (tmp_path / "store.json").read_bytes()
    tomb = fs.TOMBSTONE.read_bytes() if fs.TOMBSTONE.exists() else None
    monkeypatch.setattr(fs, "fetch", lambda *_a, **_k: pytest.fail("fetched over an unpaired tombstone"))
    rc, out, err = _main(fs, monkeypatch, _NEW_B)
    assert rc != 0 and out == ""
    assert f"restore {fs.TOMBSTONE} from git" in err and "--reset-tombstone" in err
    assert (tmp_path / "store.json").read_bytes() == store
    assert (fs.TOMBSTONE.read_bytes() if fs.TOMBSTONE.exists() else None) == tomb
    monkeypatch.undo()
    _stored(fs, monkeypatch, tmp_path / "x", _library({}))   # a store of its own, paired: no refusal
    assert _main(fs, monkeypatch, _library({}))[0] == 0


def test_reset_tombstone_goes_on_over_it_and_pairs_them_again(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Review N1: --reset-tombstone, a deliberate reset, goes on over a missing tombstone (a lost one's
    retired name is then free, so the new "b" takes it), and the run's store write pairs them again."""
    fs = _fs()
    _stored(fs, monkeypatch, tmp_path, _library({}, b={}))
    assert _main(fs, monkeypatch, _REFUSE_B)[0] == 0
    fs.TOMBSTONE.unlink()
    assert _main(fs, monkeypatch, _NEW_B)[0] != 0
    rc, out, _err = _main(fs, monkeypatch, _NEW_B, reset=True)
    assert rc == 0 and fs.read_catalog("H6641")["b"]["code"] == 9
    assert fs.read_tombstone() == {"store_sha256": _sha256(tmp_path / "store.json")}
    assert f"{fs.TOMBSTONE} changed (0 records): commit it with this run" in out
    assert _main(fs, monkeypatch, _NEW_B)[0] == 0   # paired again
    assert fs.parse_args(["--reset-tombstone", "H6641"]).reset_tombstone
    assert not fs.parse_args(["--resolve"]).reset_tombstone
    assert fs._shown(fs._BUNDLED_TOMBSTONE) == "tools/scene_tombstone.json"


def test_resolve_refuses_a_tombstone_that_is_not_the_stores(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """Review N1: --resolve writes the store too, so it refuses as main() does, before it logs in."""
    fs = _fs()
    monkeypatch.setattr(fs, "OUT_DIR", tmp_path)
    monkeypatch.setattr(fs, "TOMBSTONE", tmp_path / "tombstone.json")
    fs.store_catalog("H1234", {"a": {"code": 1, "param": None, "param_id": 7, "placeholder": True,
                                     "big_effect": True}})
    fs.TOMBSTONE.unlink()
    store = (tmp_path / "store.json").read_bytes()
    cloud = ModuleType("govee_ble_local.cloud")
    cloud.GoveeCloudAccount = lambda *_a, **_k: pytest.fail("logged in")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "govee_ble_local.cloud", cloud)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        assert fs.resolve_placeholders("a@b.c", "pw", ["H1234"]) == 1
    assert "--resolve refused" in err.getvalue() and f"restore {fs.TOMBSTONE} from git" in err.getvalue()
    assert (tmp_path / "store.json").read_bytes() == store and not fs.TOMBSTONE.exists()


def test_main_says_when_it_changes_the_tombstone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review N2: store_sha256 can't see a change of the tombstone alone: a name dropped (here "b", gone from
    the library) leaves the store's bytes as they were, so a lost tombstone diff still paired. A run that
    changes the tombstone ends by saying so, to be committed with the run; one that does not says nothing."""
    fs = _fs()
    store, _tomb = _stored(fs, monkeypatch, tmp_path, _library({}, b={}))
    rc, out, _err = _main(fs, monkeypatch, _library({}))
    assert rc == 0 and fs.read_tombstone()["H6641"] == {"dropped": ["b"]}
    assert (tmp_path / "store.json").read_bytes() == store   # the tombstone alone changed
    assert out.splitlines()[-1] == f"{fs.TOMBSTONE} changed (1 records): commit it with this run"
    assert "changed (" not in _main(fs, monkeypatch, _library({}))[1]   # an identical re-fetch


def test_a_scratch_store_leaves_the_committed_tombstone(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Review N3 (F3-R2): with only OUT_DIR pointed elsewhere, store_catalog wrote the scratch store's sha256
    into the committed tombstone. The committed tombstone pairs with the bundled store, so a scratch store
    leaves its store_sha256 as it is (where TOMBSTONE is pointed elsewhere too, it gets that store's)."""
    fs = _fs()
    bundled, scratch, committed = tmp_path / "bundled", tmp_path / "scratch", tmp_path / "tombstone.json"
    bundled.mkdir()
    scratch.mkdir()
    for name, value in (("_BUNDLED_DIR", bundled), ("OUT_DIR", bundled), ("_BUNDLED_TOMBSTONE", committed),
                        ("TOMBSTONE", committed)):
        monkeypatch.setattr(fs, name, value)
    fs.store_catalog("H6641", {"a": {"code": 1, "param": None}})   # the bundled pair
    digest = _sha256(bundled / "store.json")
    assert fs.read_tombstone() == {"store_sha256": digest}
    tomb = committed.read_bytes()
    (scratch / "store.json").write_bytes((bundled / "store.json").read_bytes())
    monkeypatch.setattr(fs, "OUT_DIR", scratch)
    fs.store_catalog("H6641", {"a": {"code": 2, "param": None}})
    assert committed.read_bytes() == tomb
    rc, _out, _err = _main(fs, monkeypatch, _library({}, b={}))   # main() over the scratch store
    assert rc == 0 and "b" in fs.read_catalog("H6641") and _sha256(scratch / "store.json") != digest
    assert fs.read_tombstone()["store_sha256"] == digest   # a record may change, not the sha256
    monkeypatch.setattr(fs, "TOMBSTONE", scratch / "tombstone.json")   # pointed elsewhere with it
    fs.store_catalog("H6641", {"a": {"code": 3, "param": None}})
    assert fs.read_tombstone() == {"store_sha256": _sha256(scratch / "store.json")}
