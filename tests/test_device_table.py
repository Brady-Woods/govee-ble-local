"""The device table generated from spec/devices.yaml (tools/gen_devices.py -> _generated/devices.py, resolved
by devices/_resolve.py) against the hand table it replaced: master e55498b's devices/profile.py PROFILES,
pinned below field for field, SKU by SKU. Every difference is one the spec makes on purpose (_CHANGED). No
hardware."""
from __future__ import annotations

import copy
import dataclasses
import importlib.util
import json
import pathlib
import sys
from typing import Any

import pytest
from conftest import require_optional

from govee_ble_local import supported_skus
from govee_ble_local._generated.devices import FAMILIES
from govee_ble_local.devices import _fold, _resolve
from govee_ble_local.devices.profile import PROFILES, DeviceProfile, profile_for, wire_profile
from govee_ble_local.models import Capability as C
from govee_ble_local.models import Encryption as E
from govee_ble_local.models import Zone

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_LIGHT = frozenset({C.POWER, C.BRIGHTNESS, C.RGB, C.COLOR_TEMP, C.SCENES})
_SEG = _LIGHT | {C.SEGMENTS}
_BULB = dict(capabilities=_LIGHT, color_scheme="h6006", encryption=E.NONE, scene_versions=frozenset({1}),
             min_kelvin=2700, max_kelvin=6500, readback="polled", mode_color_parse="rgb_kelvin_tint")
_PLUG = dict(capabilities=frozenset({C.POWER}), requires_secret=True, relay=True, readback="plug")
# master e55498b, devices/profile.py PROFILES: each SKU's non-default fields.
_MASTER: dict[str, dict[str, Any]] = {
    "H60A6": dict(capabilities=_SEG, segments=13,
                  zones=(Zone("main", 0, (12,)), Zone("background", 1, tuple(range(12)))),
                  scene_versions=frozenset({0, 1, 2, 3, 5}), scene_dialect="B_h60a6",
                  min_kelvin=2700, max_kelvin=6500, global_power=True, readback="status",
                  mode_color_parse="kelvin_15_rgb_kelvin_0d", status_color_groups_pact=(1, 2)),
    "H6047": dict(capabilities=_SEG, encryption=E.NONE, segments=10,
                  zones=(Zone("left", 0, (0, 1, 2, 3, 4)), Zone("right", 1, (5, 6, 7, 8, 9))),
                  scene_versions=frozenset({0, 1, 2, 3}), min_kelvin=2200, max_kelvin=6500,
                  bar_switch=True, gradual=True, readback="polled", color_readback="mechanism_a_direct",
                  color_readback_per_batch=4, mode_color_parse="kelvin_15"),
    "H61A8": dict(capabilities=_SEG, encryption=E.NONE, segments=15, scene_versions=frozenset({0, 1, 2, 3}),
                  min_kelvin=2000, max_kelvin=9000, gradual=True, readback="polled",
                  color_readback="mechanism_b", mode_color_parse="kelvin",
                  kelvin_by_hardware=(("3.08.01", 2700, 6500),), mode_reports_kelvin=False),
    "H6006": _BULB,
    "H6008": _BULB,
    "H6004": {**_BULB, "scene_versions": frozenset({0, 1})},
    "H6052": dict(capabilities=_LIGHT, color_scheme="h6006", encryption=E.NONE,
                  scene_versions=frozenset({0, 1, 4, 5}), scene_dialect="B_h6052", min_kelvin=2000,
                  max_kelvin=9000, readback="polled", mode_color_parse="rgb_0d"),
    "H6641": dict(capabilities=_SEG, encryption=E.NONE, segments=16,
                  scene_versions=frozenset({0, 1, 2, 3, 10}), min_kelvin=2000, max_kelvin=9000,
                  readback="polled", color_readback="mechanism_a_direct", color_readback_per_batch=4,
                  color_readback_live_ic=True, color_readback_ic_range=(41, 70),
                  color_readback_ic_per_piece=5, mode_color_parse="kelvin_15"),
    "H6188": dict(capabilities=_LIGHT, color_scheme="h6188", encryption=E.NONE, min_kelvin=2000,
                  max_kelvin=9000, readback="polled", brightness_range=(20, 254),
                  mode_color_parse="rgb_kelvin_tint"),
    "H6062": dict(capabilities=_SEG, encryption=E.NONE, segments=33, scene_versions=frozenset({0, 1, 2, 3}),
                  min_kelvin=2000, max_kelvin=9000, readback="polled", color_readback="mechanism_a_direct",
                  color_readback_per_batch=4, section_count_read=True, mode_color_parse="kelvin"),
    "H6061": dict(capabilities=_LIGHT - {C.SCENES}, color_scheme="h6061", encryption=E.NONE, segments=23,
                  min_kelvin=2000, max_kelvin=9000, readback="polled", color_readback="mechanism_a_direct",
                  color_readback_per_batch=4, mode_color_parse="rgb_kelvin_0d"),
    **{sku: {**_PLUG, "relay_on_mask": 1, "advert_on_mask": 1} for sku in ("H5080", "H5083", "H5085")},
    "H5082": {**_PLUG, "relay_on_mask": 3, "relay_power": "all_outlets", "advert_on_mask": 3,
              "power_resync": True},
    "H5089": {**_PLUG, "relay_on_mask": 3, "relay_power": "total_switch", "advert_on_mask": 3},
    **{sku: {**_PLUG, "relay_on_mask": 7, "relay_power": "all_outlets", "advert_on_mask": 255,
             "power_resync": True} for sku in ("H5160", "H5161")},
}
# (master, generated) where the spec differs on purpose:
_CHANGED: dict[tuple[str, str], tuple[Any, Any]] = {
    # goodsType 64's control screen set (Support.supportScenesOp(64)); version 0 uploads nothing, so no frame
    # changes. The three bulbs now share one profile.
    ("H6006", "scene_versions"): (frozenset({1}), frozenset({0, 1})),
    ("H6008", "scene_versions"): (frozenset({1}), frozenset({0, 1})),
    # The control screen's ConfigDetail set; the library never sent version 10.
    ("H6641", "scene_versions"): (frozenset({0, 1, 2, 3, 10}), frozenset({0, 1, 2, 3})),
    # goodsType 19's set follows the firmware (newVersion4Ble), read at the first poll; until then the newest
    # firmware's {0, 1, 2, 3}, as 1.x sent. The bench unit (SW 3.03.18 / HW 3.01.10) keeps it (checked below).
    ("H61A8", "scene_versions"): (frozenset({0, 1, 2, 3}), frozenset()),
    ("H61A8", "scene_versions_by_firmware"): (
        (), (("1.04.00", "", (0, 1, 2)), ("1.06.00", "1.00.02", (0, 1, 2, 3)))),
    # The spec's name for the bars' 0x15 decode (one Kelvin per bar); the decode itself is unchanged.
    ("H6047", "mode_color_parse"): ("kelvin_15", "kelvin_pair_15"),
}

# (soft, hard) versions: unread, the H61A8 bench unit, and the reference self-test's other two.
_FIRMWARE = ((None, None), ("3.03.18", "3.01.10"), ("1.03.00", "3.08.01"), ("1.05.00", "1.00.01"))


def test_the_generated_table_is_masters_hand_table_but_for_the_spec_fixes() -> None:
    assert supported_skus() == sorted(_MASTER)
    routes = _resolve.routes(FAMILIES)
    reference = _reference()
    yaml = require_optional("yaml", reason="the reference resolver reads spec/devices.yaml")
    registry = yaml.safe_load((_ROOT / "spec" / "devices.yaml").read_text(encoding="utf-8"))
    for sku, fields in _MASTER.items():
        old, new = DeviceProfile(skus=(sku,), **fields), profile_for(sku)
        assert new is not None, sku
        for f in dataclasses.fields(DeviceProfile):
            if f.name != "skus":
                pair = (getattr(old, f.name), getattr(new, f.name))
                assert pair == _CHANGED.get((sku, f.name), (pair[0], pair[0])), (sku, f.name)
        # The same decode context for every reply (one cached govee_profile for the same facts), and the
        # route the spec's reference resolver gives.
        assert wire_profile(new) is wire_profile(old), sku
        fam, gt, _sku = routes[sku]
        assert reference.ble_rows(registry, sku) == [(fam["id"], int(gt))], sku
        # A firmware-gated scene set is master's until the device-info read; the reference's after it.
        if new.scene_versions_by_firmware:
            assert _fold.scene_versions(new) == old.scene_versions, sku
        for soft, hard in _FIRMWARE[1:] if new.scene_versions or new.scene_versions_by_firmware else ():
            ref = reference.client_row(registry, fam["id"], int(gt), soft=soft, hard=hard)
            assert _fold.scene_versions(new, soft, hard) == set(ref["scene_versions"]), (sku, soft, hard)
    # One profile per group of identical facts (the bulbs, the goodsType-43 and -90 plugs), else one per SKU.
    assert sorted(p.skus for p in PROFILES) == sorted([
        ("H6004", "H6006", "H6008"), ("H5080", "H5083", "H5085"), ("H5160", "H5161"),
        *((sku,) for sku in ("H60A6", "H6047", "H61A8", "H6052", "H6641", "H6188", "H6062", "H6061", "H5082",
                             "H5089"))])


def test_the_generator_keeps_data_keys_and_raises_on_a_missing_fact() -> None:
    require_optional("yaml", reason="the generator reads spec/devices.yaml")
    gen = _load(_ROOT / "tools" / "gen_devices.py", "gen_devices_under_test")
    schema = json.loads((_ROOT / "spec" / "devices.schema.json").read_text(encoding="utf-8"))
    doc = gen.load((_ROOT / "spec" / "devices.yaml").read_bytes())
    _table, families = gen.project(doc, schema)
    h60ax = next(f for f in families if f["id"] == "h60ax")
    # A data key named like prose survives (wire.group.rule); prose and unread keys don't.
    assert h60ax["client_profile"]["wire"]["group"]["rule"] == "fixed"
    assert "doc" not in h60ax["client_profile"]["by_goods_type"]["303"] and "encryption" not in h60ax
    # A fact missing (the family specs have no kelvin_range either), the segment count missing, a colour
    # decode the library can't serve.
    for fam_id, gt, edit, error in (
            ("h60ax", "303", lambda f: f["by_goods_type"]["303"].pop("kelvin_range"), "no kelvin_range"),
            ("h6061", "51", lambda f: f["wire"]["by_goods_type"]["51"].pop("counts"), "segments or a colour"),
            ("h6047", "119", lambda f: f["by_goods_type"]["119"].update(mode_color_parse="untraced"),
             "mode_color_parse untraced")):
        broken = copy.deepcopy(doc)
        edit(next(f for f in broken["families"] if f["id"] == fam_id)["client_profile"])
        with pytest.raises(SystemExit, match=rf"{fam_id} goodsType {gt} \(H\w+\): {error}"):
            gen.project(broken, schema)


def _reference() -> Any:
    return _load(_ROOT / "spec" / "govee_reference.py", "govee_reference_device_table")


def _load(path: pathlib.Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
