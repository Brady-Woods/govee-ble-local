"""The device table generated from spec/devices.yaml (tools/gen_devices.py -> _generated/devices.py, resolved
by devices/_resolve.py) against the hand table it replaced: master e55498b's devices/profile.py PROFILES,
pinned below field for field, SKU by SKU. Every difference is one the spec makes on purpose (_CHANGED). The
generator's refusals, one line per rule, cover the rows admitted after 1.4.0. No hardware."""
from __future__ import annotations

import copy
import dataclasses
import importlib.util
import json
import pathlib
import re
import sys
from collections.abc import Callable
from typing import Any, get_args

import pytest
from conftest import require_optional

from govee_ble_local import supported_skus
from govee_ble_local._generated.devices import FAMILIES
from govee_ble_local.devices import _fold, _resolve
from govee_ble_local.devices.profile import PROFILES, DeviceProfile, RelayPower, profile_for, wire_profile
from govee_ble_local.models import Capability as C
from govee_ble_local.models import Encryption as E
from govee_ble_local.models import Zone
from govee_ble_local.transport.connection import HeartbeatTiming
from govee_ble_local.wire.build import ColorScheme

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_LIGHT = frozenset({C.POWER, C.BRIGHTNESS, C.RGB, C.COLOR_TEMP, C.SCENES})
_SEG = _LIGHT | {C.SEGMENTS}
_BULB = dict(capabilities=_LIGHT, color_scheme="h6006", encryption=E.NONE, scene_versions=frozenset({1}),
             min_kelvin=2700, max_kelvin=6500, readback="polled", mode_color_parse="rgb_kelvin_tint")
_PLUG = dict(capabilities=frozenset({C.POWER}), requires_secret=True, relay=True, readback="plug")
# master e55498b, devices/profile.py PROFILES: each SKU's non-default fields. Frozen at 1.4.0's 18 SKUs: a row
# admitted later is checked by the generator's rules (the table in the second test), not pinned here.
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
    # The screen reads aa a3 at connect and writes 33 a3, which an H6641 answers and acks (bench, 2026-10-18).
    ("H6641", "gradual"): (False, True),
    # The spec's name for the bars' 0x15 decode (one Kelvin per bar); the decode itself is unchanged.
    ("H6047", "mode_color_parse"): ("kelvin_15", "kelvin_pair_15"),
    # 1.6.0: each row's keep-alive read (devices.yaml clientRow heartbeat); master sent none. aa 00 on the
    # VM4LightH60A6 screen, aa 36 on the H6047's, aa 01 everywhere else, after 2 s of quiet on goodsType 43.
    ("H60A6", "heartbeat"): (None, 0x00),
    ("H6047", "heartbeat"): (None, 0x36),
    **{(sku, "heartbeat"): (None, 0x01) for sku in (
        "H61A8", "H6004", "H6006", "H6008", "H6052", "H6641", "H6188", "H6062", "H6061", "H5080", "H5083",
        "H5085", "H5082", "H5089", "H5160", "H5161")},
    **{(sku, "heartbeat_timing"): ("fixed_rate", "after_quiet") for sku in ("H5080", "H5083", "H5085")},
}

# (soft, hard) versions: unread, the H61A8 bench unit, and the reference self-test's other two.
_FIRMWARE = ((None, None), ("3.03.18", "3.01.10"), ("1.03.00", "3.08.01"), ("1.05.00", "1.00.01"))


def test_the_generated_table_is_masters_hand_table_but_for_the_spec_fixes() -> None:
    assert set(_MASTER) <= set(supported_skus())
    # D3's 1.x pact fallback is frozen at these 18; every SKU admitted later is served only at its rows'
    # pacts, as the app's checkProtocol (Pact.i) serves it. H7017's row is pacts: any, so (99, 99) is one of
    # them: h7017 Model.t opens its screen with no pact test.
    assert set(_MASTER) == _resolve.PACT_FALLBACK_SKUS
    assert [sku for sku in supported_skus(allow_modelled=True) if sku not in _MASTER
            and profile_for(sku, pact=(99, 99), allow_modelled=True) is not None] == ["H7017"]
    # The resolver's value sets (standard library only) are the profile's Literals.
    assert set(_resolve.COLOR_SCHEMES) == set(get_args(ColorScheme))
    assert set(_resolve.RELAY_POWERS) == set(get_args(RelayPower))
    assert set(_resolve.HEARTBEAT_TIMINGS) == set(get_args(HeartbeatTiming))
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
        assert profile_for(sku, pact=(99, 99)) is new, sku   # D3: a pact no row lists keeps 1.x's route
        fam, gt, _sku = routes[sku]
        assert reference.ble_rows(registry, sku) == [(fam["id"], int(gt))], sku
        # A firmware-gated scene set is master's until the device-info read; the reference's after it.
        if new.scene_versions_by_firmware:
            assert _fold.scene_versions(new) == old.scene_versions, sku
        for soft, hard in _FIRMWARE[1:] if new.scene_versions or new.scene_versions_by_firmware else ():
            ref = reference.client_row(registry, fam["id"], int(gt), soft=soft, hard=hard)
            assert _fold.scene_versions(new, soft, hard) == set(ref["scene_versions"]), (sku, soft, hard)
    # One profile per group of identical facts (the bulbs, the goodsType-43 and -90 plugs), else one per SKU.
    groups = (tuple(sku for sku in p.skus if sku in _MASTER) for p in PROFILES)
    assert sorted(g for g in groups if g) == sorted([
        ("H6004", "H6006", "H6008"), ("H5080", "H5083", "H5085"), ("H5160", "H5161"),
        *((sku,) for sku in ("H60A6", "H6047", "H61A8", "H6052", "H6641", "H6188", "H6062", "H6061", "H5082",
                             "H5089"))])


# The scene and DIY interceptors split_rgbiclight's RgbIcLightRegistry.a() registers for the dreamcolor
# goodsTypes, in its order (RgbIcLightRegistry.java:187-188, DreamColorV2Register then DreamColorV1Register,
# then the :360 chain), and the app's one brightness interceptor (rgblight). DeviceDetailConfig.k0 (scene),
# .Y (DIY) and .N (brightness) take the first registered that matches the device
# (base-classes11/com/govee/base2light/pact/newdetail/DeviceDetailConfig.java:224-232,366-378,567-577); no
# other split registers one that matches these goodsTypes. Each is (lookup, the goodsTypes it matches, its
# cites: the interceptor, and the rule only it reaches). $40 matches where
# LightVersionServiceImpl.getDiySupport is not null, on dreamcolorlightv1 goodsTypes only
# (LightVersionServiceImpl.java:28-31).
_RGBIC = "split_rgbiclight/com/govee/"
_DCV1 = frozenset({19, 71, 74, 75, 83, 262, 123, 175, 176, 193, 187, 178, 136, 204, 207, 234,   # Support.f0
                   17, 18, 70, 73})   # Q0 (dreamcolorlightv1/pact/Support.java:164,188)
_DCV1_SCENES = ((f"{_RGBIC}dreamcolorlightv1/LightVersionServiceImpl.java", 168, 175),   # supportScenesOp
                (f"{_RGBIC}dreamcolorlightv1/pact/Support.java", 953, 958))   # getSupportScenesOpSet
_DCV2 = frozenset({17, 18, 19, 21})
_FIRST_MATCH: tuple[tuple[str, frozenset[int], tuple[tuple[str, int, int], ...]], ...] = (
    ("scene", _DCV2, ((f"{_RGBIC}dreamcolorlightv2/adjust/DetailConfig.java", 24, 33),
                      (f"{_RGBIC}dreamcolorlightv2/pact/Support.java", 337, 342))),
    ("diy", _DCV2, ((f"{_RGBIC}dreamcolorlightv2/adjust/DetailConfig.java", 34, 43),
                    (f"{_RGBIC}dreamcolorlightv2/pact/Support.java", 291, 296))),
    ("diy", frozenset({178}), ((f"{_RGBIC}dreamcolorlightv1/adjust/DetailConfig.java", 19, 28),)),
    ("scene", frozenset({70}),   # $32
     ((f"{_RGBIC}rgbiclight/RgbIcLightRegistry.java", 708, 718), *_DCV1_SCENES)),
    ("scene", _DCV1, ((f"{_RGBIC}rgbiclight/RgbIcLightRegistry.java", 719, 730), *_DCV1_SCENES)),   # $33
    ("diy", _DCV1, ((f"{_RGBIC}rgbiclight/RgbIcLightRegistry.java", 801, 811),)),   # $40
    # Support4StringLightV2.E (split_rgblight/com/govee/stringlightv2/Support4StringLightV2.java:37,46,171)
    ("brightness", frozenset({1, 2, 39, 40, 68, 78, 108, 143}),
     (("split_rgblight/com/govee/rgblight/RgbLightDetailRegistrar.java", 36, 48),)),
)
_CITE = re.compile(r"(?P<path>[^:\s]+\.java):(?P<lines>[0-9,-]+)")


def test_each_row_cites_the_interceptor_the_app_reaches() -> None:
    """Review G1: a row's scene, DIY or brightness rule is the one of the first interceptor the app's lookup
    finds for its goodsType. H61A8 (19) cited the dreamcolorlightv1 $33 (RgbIcLightRegistry.java:723), which
    the dreamcolorlightv2 interceptor registered ahead of it shadows for 19. Each line range a row's doc-refs
    cite that covers an interceptor of _FIRST_MATCH (or the rule only it reaches) names the first of those it
    covers that matches the row's goodsType, and that is the goodsType's first match for that lookup (a range
    over several, as :708-730 over $32 and $33, names the first that applies); a row that offers scenes
    cites its goodsType's first scene match."""
    yaml = require_optional("yaml", reason="the guard reads spec/devices.yaml")
    registry = yaml.safe_load((_ROOT / "spec" / "devices.yaml").read_text(encoding="utf-8"))
    offered: set[int] = set()
    for fam in registry["families"]:
        for gt_key, row in ((fam.get("client_profile") or {}).get("by_goods_type") or {}).items():
            gt, where = int(gt_key), (fam["id"], gt_key)
            spans = [(m["path"], int(lo), int(hi or lo)) for ref in row.get("doc-ref") or ()
                     for m in [_CITE.fullmatch(str(ref))] if m
                     for lo, _dash, hi in (r.partition("-") for r in m["lines"].split(","))]
            for lookup in ("scene", "diy", "brightness"):
                first = next((i for i, (kind, gts, _own) in enumerate(_FIRST_MATCH)
                              if kind == lookup and gt in gts), None)
                named = []
                for path, lo, hi in spans:
                    covered = [i for i, (kind, gts, own) in enumerate(_FIRST_MATCH) if kind == lookup
                               and any(p == path and lo <= b and a <= hi for p, a, b in own)]
                    if covered:
                        named.append(next((i for i in covered if gt in _FIRST_MATCH[i][1]), None))
                assert all(i == first for i in named), (*where, lookup, _path_of(named), first)
                if lookup == "scene" and first is not None and "scenes" in (row.get("capabilities") or ()):
                    assert first in named, (*where, "cites no scene interceptor")
                    offered.add(gt)
    # the rows that offer scenes on these goodsTypes, each checked
    assert offered == {19, 70, 73, 74, 75, 83, 136, 175, 176, 178, 187, 207, 262}


def _path_of(named: list[int | None]) -> list[str | None]:
    """The interceptors ``named`` (indexes of _FIRST_MATCH), as their first cite, for an assertion message."""
    return [None if i is None else f"{_FIRST_MATCH[i][2][0][0]}:{_FIRST_MATCH[i][2][0][1]}" for i in named]


def _row(fam: dict[str, Any], gt: str) -> Any:
    return fam["client_profile"]["by_goods_type"][gt]


def _wire(fam: dict[str, Any], gt: str | None = None) -> Any:
    wire = fam["client_profile"]["wire"]
    return wire if gt is None else wire["by_goods_type"][gt]


_Edit = Callable[[dict[str, Any]], object]
_STATUS_PAIR = ["cmd_41", "cmd_30"]
# (family, goodsType, an edit of that family, the refusal): one line per rule of devices/_resolve.py.
_REFUSED: tuple[tuple[str, str, _Edit, str], ...] = (
    # routes (over the whole document, before the admission cut)
    ("bulblightv3", "64", lambda f: _row(f, "64").pop("models"), "a row with support needs models"),
    ("h6047", "196", lambda f: f["client_profile"]["by_goods_type"].update(
        {"196": {**_row(f, "119"), "support": "modelled"}}), "also listed by h6047 119"),
    ("tablelampv1", "22", lambda f: _row(f, "22").update(models=[""]),
     "models must be non-empty SKU strings"),
    ("bulblightv3", "64", lambda f: f["assigned_goods_type"][0]["models"].append("h6004"),
     "bulblightv3 assigned_goods_type reassigns it"),
    ("dreamcolorlightv1", "19", lambda f: _row(f, "19")["models"].append("H6163"),
     "h6160 assigned_goods_type reassigns it to goodsType 19"),
    # wire branches, mode decodes and forms
    ("h60ax", "303", lambda f: _wire(f, "303")["by_pact"]["1,1"]["group"].update(num_pieces=2),
     "wire by_goods_type 303 by_pact 1,1 is not served"),
    ("striplight", "41", lambda f: _wire(f, "41").update(
        by_firmware=[{"min_soft_version": "1.00.00", "forms": {"f_a5": "bulb_batch_v2"}}]),
     "wire by_goods_type 41 by_firmware 0 is not served"),
    ("striplight", "41", lambda f: _wire(f).update(mode={"music": "v1_flagged"}), "wire mode"),
    ("h60ax", "303", lambda f: _wire(f, "303")["forms"].update(f_30="light_direction"),
     "wire forms f_30 'light_direction', where the library uses zone_power"),
    ("dreamcolorlightv1", "19", lambda f: (_row(f, "19").update(color_scheme="h61a8"),
                                           _wire(f)["forms"].update(f_0b="unknown")), "wire forms f_0b"),
    ("h61d3", "247", lambda f: _wire(f, "247")["forms"].update(f_a5="unknown"), "colour read-back with f_a5"),
    ("striplight", "41", lambda f: _wire(f)["forms"].update(f_41="power_memory"),
     "an aa 41 read without f_41"),
    ("h6047", "119", lambda f: _wire(f, "119")["forms"].update(f_36="bar_index"), "the aa 36 read and f_36"),
    ("striplight", "41", lambda f: _wire(f)["group"].update(num_per_group=0), "wire group num_per_group 0"),
    ("h60ax", "303", lambda f: _wire(f, "303")["group"].update(rule="unknown"), "wire group rule 'unknown'"),
    # family facts
    ("h5080", "43", lambda f: f["client_profile"].update(by_model={"H5080": {"by_pact": {"1,1": {
        "mode_color_parse": "none"}}}}), "by_model pact / firmware branches"),
    ("h6188", "0", lambda f: f.update(encryption="aes_gcm_v2"), "encryption 'aes_gcm_v2'"),
    ("h6047", "119", lambda f: f["client_profile"].update(has_brightness=False), "without has_brightness"),
    ("striplight", "41", lambda f: f["client_profile"].update(power_live=["heartbeat_aa33"]),
     "power_live heartbeat_aa33 is not served"),
    ("striplight", "41", lambda f: f["client_profile"].update(mode_color_parse="kelvin_16"),
     "mode_color_parse ['kelvin_16'] not in"),
    ("h6047", "119", lambda f: _row(f, "119").update(mode_color_parse="untraced"),
     "mode_color_parse untraced"),
    # firmware and pact branches
    ("dreamcolorlightv1", "136", lambda f: _row(f, "136")["by_firmware"][0].update(min_soft_version="1.x"),
     "firmware gates"),
    ("dreamcolorlightv1", "19", lambda f: _row(f, "19")["by_firmware"][0].update(min_soft_version="1.00.00"),
     "a Kelvin firmware branch must gate"),
    ("dreamcolorlightv1", "19", lambda f: f["client_profile"].setdefault("by_model", {}).setdefault(
        "H61A8", {}).update(by_firmware=[{"min_soft_version": "1.00.00", "kelvin_range": [2700, 6500]}]),
     "Kelvin firmware branches must be the row's"),
    ("dreamcolorlightv1", "19", lambda f: f["client_profile"].setdefault("by_model", {}).setdefault(
        "H61A8", {}).update(kelvin_range=[2700, 6500]), "by_model ['kelvin_range'] under the row's own"),
    ("dreamcolorlightv1", "136", lambda f: _row(f, "136")["by_firmware"][0].update(
        min_wifi_soft_version="1.00.00"), "a scene firmware branch must gate"),
    ("dreamcolorlightv1", "19", lambda f: _row(f, "19")["by_firmware"].append(
        {"min_soft_version": "1.00.00", "brightness_range": [1, 100]}),
     "firmware branch on ['brightness_range']"),
    ("dreamcolorlightv1", "19", lambda f: _row(f, "19")["by_firmware"][0].update(kelvin_range=[9500, 9800]),
     "need a base kelvin_range (2000, 9000), one per version"),
    ("dreamcolorlightv1", "19", lambda f: _row(f, "19")["by_firmware"][0].update(min_hard_version="3.8.01"),
     "Kelvin firmware gate '3.8.01' is not the app's a.bb.cc form"),
    ("h60ax", "235", lambda f: f["client_profile"]["by_goods_type"].update({"235": {
        **_row(f, "303"), "models": ["HX235"],
        "by_firmware": [{"min_hard_version": "1.04.03", "kelvin_range": [2700, 6500]}]}}),
     "whose gate the app compares numerically (KelvinConfig.Config.Y2"),
    ("h60ax", "303", lambda f: _row(f, "303")["by_pact"]["1,1"].update(mode_color_parse="kelvin_15"),
     "pact branch 1,1 on ['mode_color_parse']"),
    ("h60ax", "303", lambda f: _row(f, "303")["by_pact"].update({"9,9": {"status_read": _STATUS_PAIR}}),
     "pact branches ['9,9'] are not among the row's pacts"),
    ("dreamcolorlightv1", "19",
     lambda f: _row(f, "19").update(by_pact={"1,2": {"kelvin_range": [2700, 6500]}}),
     "a pact kelvin_range needs a base kelvin_range"),
    ("h61d3", "261", lambda f: _row(f, "261")["by_pact"]["4,1"].update(kelvin_range=[9500, 9800]),
     "must each overlap the base [2700, 6500]"),
    ("h5080", "43", lambda f: _row(f, "43").update(by_pact={"1,1": {"brightness_range": [1, 100]}}),
     "a pact brightness_range without the brightness capability"),
    ("bulblightv3", "64", lambda f: _row(f, "64").update(by_pact={"1,1": {"capabilities": ["power"]}}),
     "pact branch 1,1 on ['capabilities']"),
    ("h60ax", "303", lambda f: _row(f, "303")["by_pact"]["1,1"]["capabilities"].remove("scenes"),
     "pact branch 1,1 on ['capabilities']"),
    ("h60ax", "303", lambda f: _row(f, "303").update(status_read=["cmd_41", "cmd_41"]),
     "status_read ['cmd_41', 'cmd_41'] (distinct"),
    ("h60ax", "303", lambda f: _row(f, "303")["status_read"].append("cmd_zz"),
     "status_read ['cmd_zz'] not in govee_common"),
    ("h60ax", "303", lambda f: _row(f, "303")["by_pact"]["1,1"].update(status_read=["cmd_41"]),
     "a pact's status_read"),
    ("h60ax", "303", lambda f: _row(f, "303").update(pacts="any"), "needs listed pacts"),
    ("h60ax", "303", lambda f: _row(f, "303").update(pacts=["1,1", "1,2", "2,3"]), "not one pact type"),
    ("h60ax", "303", lambda f: _row(f, "303")["by_pact"].update({"1,3": {"status_read": _STATUS_PAIR}}),
     "not a code range"),
    ("h60ax", "303", lambda f: _row(f, "303").update(pacts=["1,1", "1,300"]), "is not within a byte"),
    # capabilities, colour scheme, scenes and ranges
    ("bulblightv3", "64", lambda f: _row(f, "64")["capabilities"].append("music"), "capabilities ['music']"),
    ("h6188", "0", lambda f: f["client_profile"].pop("color_scheme"), "no color_scheme"),
    ("h5080", "307", lambda f: _row(f, "307")["capabilities"].append("rgb"), "['rgb'] on a relay row"),
    ("dreamcolorlightv1", "19", lambda f: _row(f, "19").update(color_scheme="h60a7"), "color_scheme 'h60a7'"),
    ("dreamcolorlightv1", "19", lambda f: (f["client_profile"].update(color_scheme="h61a8"),
                                           _row(f, "19").pop("color_scheme")),
     "the family color_scheme h61a8 would drop ['color_temp']"),
    ("dreamcolorlightv1", "19", lambda f: _row(f, "19")["color_functions"].remove("color_temperature"),
     "color_functions ['segment_brightness'] disagree"),
    ("bulblightv3", "64", lambda f: _row(f, "64").update(scene_dialect="graffiti"),
     "scene_dialect 'graffiti'"),
    ("h6047", "119", lambda f: _row(f, "119").update(scene_versions=[0, -1]), "scene_versions [0, -1]"),
    ("h60ax", "303", lambda f: _row(f, "303").pop("kelvin_range"), "no kelvin_range"),   # nor in the specs
    ("h60ax", "303", lambda f: _row(f, "303").update(kelvin_range=[6500, 2700]), "kelvin_range [6500, 2700]"),
    ("h60ax", "303", lambda f: _row(f, "303").update(brightness_range=[0, 300]), "brightness_range [0, 300]"),
    ("h6047", "119", lambda f: _row(f, "119").pop("brightness_range"), "no brightness_range"),
    ("h60ax", "303", lambda f: _row(f, "303").pop("account_lock"), "no account_lock"),
    ("h6047", "119", lambda f: _row(f, "119")["zones"][1]["segments"].append(42), "zone right segments"),
    ("h6061", "51", lambda f: _wire(f, "51").pop("counts"), "segments or a colour read-back without"),
    # reads and writes
    ("bulblightv3", "64", lambda f: _row(f, "64").update(power_read="aa33"), "power_read 'aa33'"),
    ("h60ax", "303", lambda f: _row(f, "303").update(power_read="aa01"),
     "power_read 'aa01' with status_read"),
    ("bulblightv3", "64", lambda f: _row(f, "64")["connect_reads"].append("cmd_ac"),
     "connect_reads ['cmd_ac']"),
    ("tablelampv1", "22", lambda f: _row(f, "22").update(connect_reads=[]), "connect_reads lists brightness"),
    ("h6047", "119", lambda f: _row(f, "119")["connect_reads"].remove("gradual"),
     "connect_reads lists gradual"),
    ("h5080", "43", lambda f: _row(f, "43")["connect_reads"].append("gradual"),
     "a gradual read without the capability is served on a polled light row only"),
    ("h60ax", "303", lambda f: _row(f, "303")["connect_reads"].append("gradual"),
     "a gradual read without the capability is served on a polled light row only"),
    ("h61d3", "247", lambda f: _row(f, "247").pop("ic_pieces"), "an ic_num read and ic_pieces go together"),
    ("h61d3", "247", lambda f: _row(f, "247")["connect_reads"].remove("ic_num"),
     "an ic_num read and ic_pieces go together"),
    ("h61d3", "247", lambda f: _row(f, "247")["ic_pieces"].update(min_ic=80), "ic_pieces"),
    ("h5080", "43", lambda f: _row(f, "43").update(connect_writes=[]), "connect_writes []"),
    ("bulblightv3", "64", lambda f: _row(f, "64").update(connect_writes=["plug_sync_time"]),
     "connect_writes ['plug_sync_time']"),
    # plugs
    ("h5080", "50", lambda f: _row(f, "50").pop("power_resync"), "no power_resync"),
    ("bulblightv3", "64", lambda f: _row(f, "64").update(power_resync=True), "power_resync on a light"),
    ("bulblightv3", "328", lambda f: f["client_profile"]["by_model"].update(H8015={"scene_power_on": True}),
     "by_model scene_power_on on a row without the scenes capability"),
    ("h5080", "50", lambda f: _row(f, "50").update(relay_power="all_outlet"), "relay_power 'all_outlet'"),
    ("h5080", "43", lambda f: _row(f, "43").update(advert_on_mask=300), "advert_on_mask 300"),
    # the heartbeat
    ("bulblightv3", "64", lambda f: _row(f, "64").pop("heartbeat"), "no heartbeat"),
    ("bulblightv3", "64", lambda f: _row(f, "64").update(heartbeat="brightness"),
     "heartbeat 'brightness' is not a keep-alive read"),
    ("bulblightv3", "64", lambda f: _row(f, "64").update(heartbeat="cmd_zz"), "heartbeat 'cmd_zz'"),
    ("h5080", "43", lambda f: _row(f, "43").update(heartbeat_timing="later"), "heartbeat_timing 'later'"),
    ("bulblightv3", "64", lambda f: _row(f, "64").update(heartbeat="heartbeat"),
     "heartbeat heartbeat where power_live ['heartbeat_aa01'] take power from the reply to power"),
    ("h6047", "119", lambda f: _row(f, "119").update(heartbeat="power"),
     "heartbeat power where power_live ['heartbeat_aa36'] take power from the reply to cmd_36"),
    ("h5080", "50", lambda f: _row(f, "50").update(heartbeat="heartbeat"),
     "where power_live [] and the relay take power from the reply to power"),
)


def test_the_generator_keeps_data_keys_and_raises_on_a_missing_fact() -> None:
    require_optional("yaml", reason="the generator reads spec/devices.yaml")
    gen = _load(_ROOT / "tools" / "gen_devices.py", "gen_devices_under_test")
    schema = json.loads((_ROOT / "spec" / "devices.schema.json").read_text(encoding="utf-8"))
    doc = gen.load((_ROOT / "spec" / "devices.yaml").read_bytes())
    _table, families = gen.project(doc, schema)
    h60ax = next(f for f in families if f["id"] == "h60ax")
    # A data key named like prose survives (wire.group.rule); prose and unread keys don't.
    assert h60ax["client_profile"]["wire"]["group"]["rule"] == "fixed"
    assert "doc" not in h60ax["client_profile"]["by_goods_type"]["303"] and "category" not in h60ax

    def edited(fam_id: str, edit: _Edit) -> dict[str, Any]:
        fams = [copy.deepcopy(f) if f["id"] == fam_id else f for f in doc["families"]]
        edit(next(f for f in fams if f["id"] == fam_id))
        return {**doc, "families": fams}

    for fam_id, gt, edit, error in _REFUSED:
        with pytest.raises(SystemExit, match=rf"{fam_id} goodsType {gt}\b[^:]*: .*{re.escape(error)}"):
            gen.project(edited(fam_id, edit), schema)
    # A per-SKU entry of a SKU no row of the family lists (in a family the library serves).
    stray = edited("h6047", lambda f: f["client_profile"]["wire"].setdefault("by_model", {}).update(HX1={}))
    with pytest.raises(SystemExit, match=re.escape("h6047 wire by_model ['HX1']: no row of the family")):
        gen.project(stray, schema)
    # A modelled row is built and checked, but admitted only on the opt-in.
    modelled = edited("tablelampv1", lambda f: _row(f, "22").update(support="modelled"))
    table, cut = gen.project(modelled, schema)

    def skus(levels: frozenset[str]) -> set[str]:
        return {sku for group, _fields, _routes in _resolve.profiles(cut, table, levels) for sku in group}

    assert "H6052" not in skus(_resolve.ADMITTED) and "H6052" in skus(_resolve.MODELLED)


def _reference() -> Any:
    return _load(_ROOT / "spec" / "govee_reference.py", "govee_reference_device_table")


def _load(path: pathlib.Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
