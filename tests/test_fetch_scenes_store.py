"""tools/fetch_scenes.py writes only a store the loader reads: its entry check is the loader's, its layout
refuses two catalogs of one SKU, it checks the store before writing it, and it leaves the store 0644."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import stat
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from conftest import FakeSceneFiles

from govee_ble_local import scenes

_TOOL = Path(__file__).resolve().parent.parent / "tools" / "fetch_scenes.py"


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
    unsorted = {"pool": [{"name": "b", **b}, {"name": "a", **a}], "skus": {"H1234": [0, 1]}}
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
