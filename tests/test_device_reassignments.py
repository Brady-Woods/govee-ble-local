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

from govee_ble_local._generated.devices import FAMILIES
from govee_ble_local.devices import _resolve
from govee_ble_local.devices.profile import profile_for, supported_skus

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_pact_conditioned_reassignments_are_rows_the_pact_picks() -> None:
    yaml = require_optional("yaml", reason="the reassignments are read from spec/devices.yaml")
    fams = yaml.safe_load((_ROOT / "spec" / "devices.yaml").read_text(encoding="utf-8"))["families"]

    def edited(fam_id: str, gt: str, **row: Any) -> list[Any]:
        out = copy.deepcopy(fams)
        rows = next(f for f in out if f["id"] == fam_id)["client_profile"]["by_goods_type"]
        rows[gt] = {**rows.get(gt, {}), **row}
        return out

    # Every target row carries its SKUs but barelightv1 17, which leaves H6171 off until its aa 40 read
    # is modelled (the barelightv1 trace, G4). Each other line is a below-gate route no source row carries
    # yet, or a source row that lists the SKU without pacts (the unsupported goodsType-0 rows: h6159 0).
    review = _resolve.reassignment_review(fams)
    assert all("pacts below" in line or "goodsType 0, without pacts" in line
               for line in review if not line.startswith("H6171: no goodsType-17 row"))
    assert any(line.startswith("H6171: no goodsType-17 row") for line in review)
    assert any(line.startswith("H6159: also listed by h6159 goodsType 0") for line in review)
    # A source row below the gate carries the route; one at or above it, or a target pact below it, disagrees.
    assert not any(line.startswith("H6159:") for line in _resolve.reassignment_review(
        edited("h6159", "0", pacts=["1,1"])))
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
    # A pairing write's pact is an exact-pact condition (NewRgb2TLUtil.isNewRgb2TL4BleIot): H615A-E route at
    # (2,1) on stringlightv2 40 / 108, and their (65535, 255) route is for review until a goodsType-40 row
    # lists that pact; a row of another goodsType that accepts it contradicts the app's write. Without a pact
    # the advert may be the write's, where the app runs other frames, so the SKU needs its pact.
    paired = _resolve.bind_review(fams)
    assert [line.split(":")[0] for line in paired] == [f"H613{c}" for c in "ABCDEF"] + [
        f"H615{c}" for c in "ABCDE"]
    assert profile_for("H615A", pact=(2, 1)) is not None and profile_for("H615A", pact=(65535, 255)) is None
    assert profile_for("H615E", goods_type=108) is None and "H615E" in supported_skus()
    assert not any(line.startswith("H615A") for line in _resolve.bind_review(
        edited("stringlightv2", "40", pacts=["2,1", "65535,255"])))
    with pytest.raises(_resolve.SpecError, match=r"^stringlightv2 goodsType 108 \(H615E\): pacts any accept"):
        _resolve.routes(edited("stringlightv2", "108", pacts="any"))
    # A target row is never the route without an advertised pact: the app rewrites only a record whose pact
    # meets the gate, and runs a pact-less one at its source goodsType (profile.profile_for).
    served = [(sku, gt) for sku, targets in _resolve.pact_gates(FAMILIES).items()
              for gt, gate in targets.items()
              if profile_for(sku, pact=gate, goods_type=gt, allow_modelled=True) is not None]
    assert served and all(profile_for(sku, goods_type=gt, allow_modelled=True) is None for sku, gt in served)


def _load(path: pathlib.Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
