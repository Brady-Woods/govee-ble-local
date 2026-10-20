"""The pact-conditioned assigned_goods_type items as routing data (devices/_resolve.py ``routes`` and
``reassignment_review``): a row at the target goodsType carries the SKU at the pacts at or above the gate, one
at a source goodsType below it, and the pact picks the row. One line per rule, over devices.yaml."""
from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import sys
from typing import Any

import pytest
from conftest import require_optional

from govee_ble_local.devices import _resolve

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_pact_conditioned_reassignments_are_rows_the_pact_picks() -> None:
    yaml = require_optional("yaml", reason="the reassignments are read from spec/devices.yaml")
    fams = yaml.safe_load((_ROOT / "spec" / "devices.yaml").read_text(encoding="utf-8"))["families"]

    def edited(fam_id: str, gt: str, **row: Any) -> list[Any]:
        out = copy.deepcopy(fams)
        rows = next(f for f in out if f["id"] == fam_id)["client_profile"]["by_goods_type"]
        rows[gt] = {**rows.get(gt, {}), **row}
        return out

    # Every target row carries its SKUs; no source row exists yet, so each below-gate route is for review,
    # and so is a second row at the target goodsType that lists the SKU without pacts (h7090 44's H6119).
    review = _resolve.reassignment_review(fams)
    assert all("pacts below" in line for line in review if not line.startswith("H6119: also listed"))
    assert any(line.startswith("H6171: no goodsType-13 row") for line in review)
    assert any(line.startswith("H6119: also listed by h7090 goodsType 44") for line in review)
    # A source row below the gate carries the route; one at or above it, or a target pact below it, disagrees.
    assert not any(line.startswith("H6171:") for line in _resolve.reassignment_review(
        edited("barelightv1", "13", models=["H6171"], pacts=["1,1"])))
    for fam_id, gt, row in (("barelightv1", "13", {"models": ["H6171"], "pacts": ["10,2"]}),
                            ("barelightv1", "17", {"pacts": ["1,1", "10,1"]}),
                            ("barelightv1", "13", {"models": ["H6171"], "pacts": "any"})):
        with pytest.raises(_resolve.SpecError, match=rf"^{fam_id} goodsType {gt} \(H61\w+\): pacts"):
            _resolve.routes(edited(fam_id, gt, **row))
    # A routed SKU a pact-conditioned item names is a route; a firmware-conditioned one stays refused.
    _resolve.routes(edited("barelightv1", "17", support="modelled"))
    with pytest.raises(_resolve.SpecError, match="h6160 assigned_goods_type reassigns it to goodsType 19"):
        _resolve.routes(edited("h6160", "19", support="modelled", pacts=["1,1"]))
    # The generated table keeps each item's condition, so the same holds on it (test_device_table's routes).
    gen = _load(_ROOT / "tools" / "gen_devices.py", "gen_devices_reassignments")
    schema = json.loads((_ROOT / "spec" / "devices.schema.json").read_text(encoding="utf-8"))
    supported = {"families": edited("barelightv1", "17", support="modelled"), "mode_color_parse": {}}
    projected = gen.Projector(schema).project(gen._supported(supported, _resolve.LEVELS), "#")
    _resolve.routes(projected["families"])


def _load(path: pathlib.Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
