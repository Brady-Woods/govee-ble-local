"""The catalogue snapshot's routes (spec/govee_catalog.json through devices/_resolve.py ``catalogue``, which
tools/gen_devices.py bakes into the device table): its precedence rules, one line each, over devices.yaml."""
from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import sys
from typing import Any

import pytest
from conftest import require_optional

from govee_ble_local._generated.devices import FAMILIES, MODE_COLOR_PARSE
from govee_ble_local.devices import _resolve
from govee_ble_local.devices.profile import profile_for

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_the_catalogue_routes_only_skus_no_row_lists() -> None:
    require_optional("yaml", reason="the generator reads spec/devices.yaml")
    gen = _load(_ROOT / "tools" / "gen_devices.py", "gen_devices_catalogue")
    schema = json.loads((_ROOT / "spec" / "devices.schema.json").read_text(encoding="utf-8"))
    doc = gen.load((_ROOT / "spec" / "devices.yaml").read_bytes())
    fams = doc["families"]
    # The committed snapshot settles every conflict, and each SKU it routes is supported on its row.
    committed = _resolve.catalogue(fams, gen.snapshot())
    assert committed.review == () and not committed.unrouted and committed.added
    for (_fam, gt), skus in committed.added.items():
        for sku in skus:
            p = profile_for(sku, allow_modelled=True)   # the SKU takes its row's support level
            assert p is not None and sku in p.skus, sku
            assert p is profile_for(sku, goods_type=int(gt), allow_modelled=True), sku
            assert profile_for(sku, pact=(9, 9), allow_modelled=True) is None, sku   # no 1.x pact fallback

    # A catalogue route's support is its row's capped at app_traced: the bulb row's bench check isn't theirs.
    def served(level: str) -> set[str]:
        return {sku for skus, _fields, _routes in _resolve.profiles(FAMILIES, MODE_COLOR_PARSE, {level})
                for sku in skus}
    assert "H6006" in served("hardware_verified") and "H6009" not in served("hardware_verified")
    assert "H6009" in served("app_traced") and _resolve.catalogue_support("modelled") == "modelled"
    cat = _resolve.catalogue(fams, {
        "H0000": 0,      # goodsType 0 never routes, even with H6188's goodsType-0 row
        "H0064": 64,     # no row lists it: its goodsType's row routes it
        "H0999": 999,    # no row at its goodsType: unsupported
        "H6052": 23,     # a routed SKU, a conflict with no cited rewrite: unrouted, for review
        "H6066": 23,     # the same on a row without support: for review
        "H6171": 13,     # barelightv1's assigned_goods_type cites the app's 13 -> 17: settled
        "H6159": 64,     # no row lists it, but h6159's assigned_goods_type rewrites it: for review
        "H6004": 10,     # one of the SKU's rows has the catalogue's goodsType: agreed
    })
    assert cat.added == {("bulblightv3", "64"): ("H0064",)} and cat.unrouted == {"H6052"}
    assert [line.split(":")[0] for line in cat.review] == ["H6052", "H6066", "H6159"]
    twin = copy.deepcopy(fams)   # a goodsType with rows in two families routes neither
    rows = next(f for f in twin if f["id"] == "h6047")["client_profile"]["by_goods_type"]
    rows["64"] = {"models": ["H6047"]}
    assert [line.split(":")[0] for line in _resolve.catalogue(twin, {"H0064": 64}).review] == ["H0064"]
    # The build: an added SKU joins its row's profile, and a row its only SKU leaves is dropped.
    table, cut = gen.project(doc, schema, {"H0064": 64, "H6052": 23})
    groups = [skus for skus, _fields, _routes in _resolve.profiles(cut, table)]
    assert ("H6004", "H6006", "H6008", "H0064") in groups and not any("H6052" in g for g in groups)
    assert "tablelampv1" not in {f["id"] for f in cut}   # its only row with support was 22


def test_the_snapshot_holds_only_each_skus_goods_type_and_ic(tmp_path: pathlib.Path) -> None:
    # tools/fetch_catalog.py keeps each product's sku, goodsType and ic (no names, images or URLs) and refuses
    # a non-200 answer or a SKU listed twice with different values; the generator refuses any other key.
    fetch = _load(_ROOT / "tools" / "fetch_catalog.py", "fetch_catalog_under_test")
    item = {"sku": "h6009", "goodsType": 64, "ic": 0, "name": "Smart Bulb", "url": "https://example.com/b"}
    payload = {"status": 200, "categories": [{"supportGroups": [{"supportSkuList": [
        {"products": [item, {**item, "sku": "H6009"}, {"sku": "H802A", "goodsType": 74, "ic": 15}]}]}]}]}
    assert fetch.products(payload) == {"H6009": {"goodsType": 64, "ic": 0},
                                       "H802A": {"goodsType": 74, "ic": 15}}
    with pytest.raises(ValueError, match="status 500"):
        fetch.products({**payload, "status": 500})
    payload["categories"][0]["supportGroups"][0]["supportSkuList"][0]["products"].append({**item, "ic": 1})
    with pytest.raises(ValueError, match="H6009 is listed twice"):
        fetch.products(payload)
    require_optional("yaml", reason="the generator reads spec/devices.yaml")
    gen = _load(_ROOT / "tools" / "gen_devices.py", "gen_devices_snapshot")
    good = {"fetched": "2026-10-18", "skus": {"H6009": {"goodsType": 64, "ic": 0}}}
    named, short = {"H6009": {"goodsType": 64, "ic": 0, "name": "x"}}, {"H6009": {"goodsType": 64}}
    for bad, error in (({**good, "names": {}}, r"keys \['names'\]"),
                       ({**good, "skus": named}, "'H6009' is no SKU"),
                       ({**good, "skus": short}, "'H6009' is no SKU")):
        path = tmp_path / "snapshot.json"
        path.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(SystemExit, match=error):
            gen.snapshot(path)
    path.write_text(json.dumps(good), encoding="utf-8")
    assert gen.snapshot(path) == {"H6009": 64}


def _load(path: pathlib.Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
