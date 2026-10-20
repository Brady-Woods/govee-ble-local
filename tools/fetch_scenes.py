#!/usr/bin/env python3
"""Fetch Govee's built-in scene library per SKU and store it in-repo.

Uses the public (no-auth) app scene-library endpoint:
    GET https://app2.govee.com/appsku/v1/light-effect-libraries?sku=<SKU>
(only an AppVersion header). Each scene yields a BLE `sceneCode` and a base64
`scenceParam` (the effect blob uploaded via the a3-chunk burst), taken from the lightEffect
the app's scene list keeps for the SKU (devices.yaml scenes.list_filter). Writes each SKU's
catalog into src/govee_ble_local/scenes/store.json (so it ships with the library and no
runtime cloud call is needed), each scene stored once: the store's `pool` lists every
bundled scene once, sorted by content, and `skus` maps each SKU to the indexes of its
scenes in it (layout), so SKUs that share a scene share its entry.

The app's scene list runs that filter at the scene set in force, so a fetch runs it at each of the SKU's
scene sets (scene_sets). A scene's entry is its pick at the union of the sets (op_set), the newest
firmware's; where the result at a smaller set differs, the entry's ``by_set`` (the store's ``by_set``,
layout) records it per set: None where the app lists no such scene there, else the entry of the effect it
plays there. A scene whose pick at a set is an effect the library can't store (version rules, no code, a
sceneType-5 upload, a placeholder) is not listed at that set only, and printed. Naming ignores the per-set
results: a name follows the scene's entry at the union, so a change to its other effects never renames it,
and the tombstone keeps that entry only. Alike twins (one sceneName, one entry at the union) take the order
of their whole lightEffect content, not the response's, so a reorder never swaps which plays which effect.
Each scene whose result at a set is not the one the store held is printed by name, per set; a kept scene
the fetch no longer lists keeps its results at the SKU's current sets only (_reconciled).

A stored name is a Home Assistant effect name that automations keep, so a re-fetch never points one at
another scene (stored_names), and a name the store gave up never comes back on another scene (the
tombstone, TOMBSTONE). Each stored scene records the sceneName the app lists it under (``scene_name``,
written only where the stored name differs) and its category. A listed scene keeps the stored name of
the stored scene of its sceneName that holds its code and category (its blob or param id may change;
ties go by the whole entry, then blob and param id). Else, where exactly one stored scene and one listed
scene fit each other, it keeps that one's name by one of its own lightEffects' codes (its category
renamed, or the app's list plays another of its lightEffects), by category and blob (its code changed:
H6006's 26 recodes keep their blob), or, printed as a guess, by category and param id or category alone.
A guess passes over a stored scene whose code the library still answers under any sceneName, or whose param
id another listed scene carries (its scene, listed under another name), and a blob match over one it still
answers at its own sceneName and category (that scene is still there); both pass over one a fetch found gone
from the library, or found its scene (its param id) listed under another name or left out only for its time
or rules at another code, since it was last listed. Where more than one fits,
it takes a new name, printed as ambiguous. Only a scene that matches none gets a new name, never one the
store or the tombstone holds: its sceneName where that is free, else "<name> (<its category>)" (for example
"Flash (Emotion)"), or "<name> (<category>, 2)", 3 and so on. The app lists a name in more than one
category (H61A8 / H6641: Emotion's "Flash", code 2020, and Zootopia 2's, 14769); of the new scenes
of one sceneName the highest code keeps it, whatever the app's list order (the bundled catalog holds
14769 so), so a first fetch names a response the same whatever the store holds. A stored scene the fetch
no longer lists stays under its name: one the library dropped, one left out only for its time (an
expired category or effect) or the firmware's version rules, and one whose scene (its param id) is listed
under another name or left out so at another code. A sceneName the app renames keeps its old stored name
too, so two names hold one code (``scenes.scene_names_by_code`` gives the first). Only a stored scene
whose scene the SKU's scene list refuses for good (its op set), or a sceneType-5 scene the library does not
upload, is removed (where two stored scenes the fetch does not list could be that scene, neither is):
printed, and retired in the tombstone, so only that scene's exact return (its sceneName, code, category
and blob) takes the name back, printed too; a placeholder, which has no blob, returns so with its param id,
and a scene whose blob --resolve baked in as its placeholder of the same param id, taking the blob back with
the name. Every change to a stored name is printed.

    python3 tools/fetch_scenes.py            # all supported SKUs
    python3 tools/fetch_scenes.py H60A6 H61A8
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import inspect
import json
import os
import pathlib
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any, Final

LIBRARY_URL = "https://app2.govee.com/appsku/v1/light-effect-libraries"
# The traced app's version (decomp-7.7.02). Self-contained on purpose (the cloud module carries its own
# copy). The scene library answered 7.5.20 byte for byte as 7.7.02 for every bundled SKU (2026-10-19);
# the catalogue endpoint did not (15 more SKUs at 7.7.02), so tools/fetch_catalog.py asks as its snapshot's
# app_version records instead. Override with --app-version or $GOVEE_APP_VERSION if Govee starts rejecting
# this value. --resolve logs in with it too (the cloud client's own default, cloud/account.py APP_VERSION, is
# the library's runtime one).
DEFAULT_APP_VERSION = "7.7.02"
OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "govee_ble_local" / "scenes"
# Upper bound on the library response body; real catalogs are well under 1 MiB.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_SKU_RE = re.compile(r"[A-Za-z0-9]{3,8}")
# The app decodes a scene blob with android.util.Base64.decode(str, NO_WRAP) (Encode.decryByBase64,
# base-classes10/com/govee/base2home/util/Encode.java:29-31), which skips every character outside the
# alphabet and '='. Some library blobs carry such characters (H6087's JSON-escaped "\/"), so fetch()
# drops them too and stores the blob the app decodes.
_NOT_BASE64 = re.compile(r"[^A-Za-z0-9+/=]")
STORE_NAME = "store.json"
# The tombstone: per SKU, the names only a scene's exact return may take. ``retired`` maps each name main()
# removed from the store to the entry it held (one the fetch matched, then cut: the entry it was to hold), and
# only that scene's return by its sceneName, code, category and blob (a blob --resolve baked in: as its
# placeholder) takes it back; ``dropped`` lists the stored names whose scene a fetch found gone from the
# library (its sceneName listed or held at its code under no category, but where another stored or retired
# name sits), or found (its param id) listed under another name or left out only for its time or rules at
# another code, since they were last listed: only a match by code and category keeps one, and a scene of
# another blob refused at its place later is not its own. Next to the tool, never in the package: the store
# and the loader are unchanged. A SKU with neither has no record; there is no file until one is written.
TOMBSTONE = pathlib.Path(__file__).resolve().parent / "scene_tombstone.json"
Entry = dict[str, Any]  # one scene's catalog entry (JSON object)
Answer = tuple[str, object, int]  # (sceneName, category, code) of a scene the library answers with


def validate_sku(sku: str) -> str:
    """Return the upper-cased SKU, or raise ValueError if it is not 3-8 alphanumerics."""
    if not _SKU_RE.fullmatch(sku):
        raise ValueError(f"invalid SKU {sku!r}: expected 3-8 alphanumeric characters")
    return sku.upper()


def validate_entry(name: object, entry: dict[str, object]) -> None:
    """Check one scene as ``scenes.load_scenes`` takes it, by the loader's own check
    (``scenes._validate_entry``), so the tool writes no scene the loader refuses and refuses none it takes;
    raises ValueError. That refuses a mistyped value (nothing is coerced), a code outside 0 to 0xFFFF, a blob
    that is not base64, a scene with ``rules`` (version-gated in the app, which the library can't follow) and
    an entry that carries ``by_set`` (a flat entry: :func:`_check` takes the tool's own form)."""
    from govee_ble_local import scenes

    scenes._validate_entry(name, entry)


def _default(entry: Entry) -> Entry:
    """A fetched or stored entry without its per-set results (``by_set``): the scene's entry at the union
    of its SKU's scene sets, a flat entry."""
    return {k: v for k, v in entry.items() if k != "by_set"}


def _check(name: object, entry: Entry) -> None:
    """Check one fetched or stored entry of the tool's own form: :func:`validate_entry` of its default
    (:func:`_default`) and of each per-set entry of its ``by_set`` (an object of scene-set keys to None or an
    entry), ValueError where one fails. The store's rules for ``by_set`` (canonical keys, no empty map, no
    entry of the default's) are the loader's, which layout's caller runs (``scenes._catalogs``)."""
    by_set = entry.get("by_set", {}) if isinstance(entry, dict) else {}
    if not isinstance(by_set, dict):
        raise ValueError(f"{name!r}: 'by_set' is not an object")
    validate_entry(name, _default(entry) if isinstance(entry, dict) else entry)
    for alt in by_set.values():
        if alt is not None:
            validate_entry(name, alt)


def write_catalog(path: pathlib.Path, data: object) -> None:
    """Write the scene store (or another JSON file) atomically: temp file in the same directory, then
    ``os.replace``. The file is left 0644, readable by all whatever the umask (it is package data): mkstemp
    creates the temp file 0600, which the replace would give the file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_dumps(data))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _dumps(data: object) -> str:
    return json.dumps(data, indent=1, sort_keys=True)


def _canon(entry: object) -> str:
    """A pool entry's content (or a library scene's lightEffects) as one string: its JSON with sorted keys
    and no whitespace."""
    return json.dumps(entry, sort_keys=True, separators=(",", ":"))


def layout(catalogs: Mapping[str, Mapping[str, Entry]]) -> dict[str, Any]:
    """The scene store of ``catalogs`` ({SKU: {name: entry}}), as the library reads it (scenes/__init__.py):
    ``pool``, each scene of them once (its entry with its ``name``, without its ``by_set``) and each per-set
    entry of their ``by_set`` once (with its scene's ``name``), sorted by name, then by its JSON with sorted
    keys; ``skus``, each SKU's pool indexes in increasing order, which is its catalog's name order;
    ``by_set``, ``{SKU: {set key: {name: None or the pool index of its entry there}}}`` for each SKU and set
    where an entry records a per-set result. The same catalogs give the same store, whatever order they or
    their scenes come in, and a scene no SKU holds is in no pool. Raises ValueError on an empty catalog, an
    invalid scene (:func:`_check`), an entry or a per-set entry that carries a ``name`` of its own, or two
    catalogs of one SKU (its spellings, "h6006" and "H6006", which would be one catalog of both)."""
    pool: dict[str, Entry] = {}   # _canon -> pool entry
    refs: dict[str, list[str]] = {}   # SKU -> its scenes' _canon
    sets: dict[str, dict[str, dict[str, str | None]]] = {}   # SKU -> set key -> name -> _canon (None: hidden)
    spelled: dict[str, str] = {}   # SKU -> the key its catalog is given under
    for sku, scenes in catalogs.items():
        key = validate_sku(sku)
        if spelled.setdefault(key, sku) != sku:
            raise ValueError(f"{key}: two catalogs, {spelled[key]!r} and {sku!r}")
        if not scenes:
            raise ValueError(f"{sku}: an empty catalog is not stored")
        for name, entry in scenes.items():
            _check(name, entry)
            for one in (entry, *(alt for alt in entry.get("by_set", {}).values() if alt is not None)):
                if "name" in one:
                    raise ValueError(f"{sku}: scene {name!r} carries a 'name' of its own")
            full = {"name": name, **_default(entry)}
            pool.setdefault(_canon(full), full)
            refs.setdefault(key, []).append(_canon(full))
            for at_set, alt in entry.get("by_set", {}).items():
                pick = None if alt is None else {"name": name, **alt}
                if pick is not None:
                    pool.setdefault(_canon(pick), pick)
                sets.setdefault(key, {}).setdefault(at_set, {})[name] = None if pick is None else _canon(pick)
    order = sorted(pool, key=lambda c: (pool[c]["name"], c))
    at = {c: i for i, c in enumerate(order)}
    return {"pool": [pool[c] for c in order],
            "skus": {sku: sorted(at[c] for c in own) for sku, own in sorted(refs.items())},
            "by_set": {sku: {k: {n: None if c is None else at[c] for n, c in names.items()}
                             for k, names in own_sets.items()}
                       for sku, own_sets in sorted(sets.items())}}


def read_store(out_dir: pathlib.Path | None = None) -> dict[str, dict[str, Entry]]:
    """Every SKU's stored catalog, ``{SKU: {name: entry}}`` with each entry without its ``name`` ({} when
    there is no store yet), and with its ``by_set`` where the store's ``by_set`` records a per-set result
    for it (``{set key: None or its entry there}``, each without its ``name``). The store is checked as the
    library reads it (``scenes._catalogs``): ValueError when it is malformed."""
    from govee_ble_local import scenes

    path = (out_dir or OUT_DIR) / STORE_NAME
    if not path.exists():
        return {}
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
        scenes._catalogs(store)
    except (ValueError, RecursionError) as err:   # _catalogs raises ValueError; json.loads RecursionError too
        # quoted by its str: the repr of a UnicodeDecodeError holds the whole file, every blob in it
        raise ValueError(f"{STORE_NAME} is malformed: {type(err).__name__}: {err}") from err
    pool = store["pool"]

    def unnamed(i: int) -> Entry:
        return {k: v for k, v in pool[i].items() if k != "name"}

    out = {sku: {pool[i]["name"]: unnamed(i) for i in refs} for sku, refs in store["skus"].items()}
    for sku, own_sets in store["by_set"].items():
        for at_set, names in own_sets.items():
            for name, i in names.items():
                out[sku][name].setdefault("by_set", {})[at_set] = None if i is None else unnamed(i)
    return out


def read_catalog(sku: str, out_dir: pathlib.Path | None = None) -> dict[str, Entry]:
    """``sku``'s stored catalog ({} if the store has none)."""
    return read_store(out_dir).get(validate_sku(sku), {})


def read_tombstone(path: pathlib.Path | None = None) -> dict[str, dict[str, Any]]:
    """The tombstone (:data:`TOMBSTONE`), ``{SKU: {"retired": {name: entry}, "dropped": [name, ...]}}`` with
    either key left out where empty ({} when there is no file yet). ValueError when it is malformed: a SKU not
    in upper case, an empty record or another key, a retired entry :func:`validate_entry` refuses, or dropped
    names that are not sorted, unique strings."""
    path = path or TOMBSTONE
    if not path.exists():
        return {}
    try:
        tomb = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(tomb, dict):
            raise TypeError("not an object")
        for sku, record in tomb.items():
            if validate_sku(sku) != sku or not isinstance(record, dict) or not record \
                    or not set(record) <= {"retired", "dropped"}:
                raise ValueError(f"{sku!r}: not an upper-case SKU's record of 'retired' and 'dropped'")
            for name, entry in record.get("retired", {}).items():
                validate_entry(name, entry)
            dropped = record.get("dropped", [])
            if not all(isinstance(n, str) and n for n in dropped) or dropped != sorted(set(dropped)):
                raise ValueError(f"{sku}: 'dropped' is not sorted, unique names")
    except (ValueError, TypeError, AttributeError) as err:
        # quoted by its str, as read_store quotes it
        raise ValueError(f"{path.name} is malformed: {type(err).__name__}: {err}") from err
    return tomb


def _record(retired: Mapping[str, Entry], dropped: Collection[str]) -> dict[str, Any]:
    """A SKU's tombstone record of ``retired`` and ``dropped``, each left out where empty."""
    record: dict[str, Any] = {}
    if retired:
        record["retired"] = dict(sorted(retired.items()))
    if dropped:
        record["dropped"] = sorted(dropped)
    return record


def store_catalog(sku: str, data: dict[str, Entry], out_dir: pathlib.Path | None = None) -> int:
    """Store ``sku``'s catalog and return the pool's size: the store is laid out again from every SKU's
    catalog (:func:`layout`), so a scene no SKU holds any more leaves the pool, checked as the library reads
    it (``scenes._catalogs``, ValueError: a store the loader refuses is never written), and written in one
    atomic replace, so it is never half written."""
    from govee_ble_local import scenes

    base = out_dir or OUT_DIR
    catalogs = read_store(base)
    catalogs[validate_sku(sku)] = data
    store = layout(catalogs)
    scenes._catalogs(store)
    write_catalog(base / STORE_NAME, store)
    return len(store["pool"])


def _is_placeholder(entry: Entry) -> bool:
    """A "placeholder" is a big-effect scene the public library ships without its blob: the app's own signal,
    ``isBigEffectScene`` with an empty ``scenceParam``, on which it fetches the effect before the apply
    (BaseSceneViewMode.java:630, SceneDataVM.java:1324, base-classes11/com/govee/base2light/pact/newdetail/
    content/scene/). Its real blob comes from the authenticated effect-strs endpoint (--resolve).

    Nothing else is: a scene with no param and no big-effect flag is a device-built-in scene activated by
    bare code (33 05 04 <lo> <hi>), and a scene with a param uploads that param, whatever its bytes. (Before,
    a param whose byte 3 was 0xFF was taken for a stub: H1401's Christmas, a well-formed 0x93 RGB scene, was
    flagged so.)"""
    return bool(entry.get("big_effect")) and not entry.get("param")


def for_sku(sku: str, eff: dict[str, Any]) -> dict[str, Any]:
    """One lightEffect's values for ``sku``, as the app reads them (CategoryV1.LightEffect,
    base-classes10/com/govee/base2light/ble/scenes/CategoryV1.java:323-422): a ``specialEffect`` entry whose
    ``supportSku`` holds the SKU overrides the base values.

    - code: the first such entry's ``sceneCode`` when above 0 (getSceneCode);
    - scene type: that entry's ``sceneType`` when its code is above 0, or its type is and it has a param;
      else 0 when it has no param and the base ``scenceParamId`` is set; else the base (getSceneType);
    - param and param id: the first such entry with a param / a non-zero id (getScenesParam,
      getScenesParamId); cmdVersion and bigEffectStr: the first such entry's (getCmdVersion,
      isBigEffectScene);
    - rules: the version rules that gate the lightEffect on the firmware (getSupportRules: the entry's where
      its type is used), which the app's scene list applies (:func:`listed_effect`)."""
    specials = [x for x in eff.get("specialEffect") or () if sku in (x.get("supportSku") or ())]
    first = specials[0] if specials else None
    out = {"code": eff.get("sceneCode"), "type": eff.get("sceneType"), "param": eff.get("scenceParam"),
           "param_id": eff.get("scenceParamId"), "cmd_version": eff.get("cmdVersion"),
           "big_effect": eff.get("bigEffectStr") == 1, "rules": eff.get("rules")}
    if first is not None:
        own_type = (first.get("sceneCode") or 0) > 0 or ((first.get("sceneType") or 0) > 0
                                                           and bool(first.get("scenceParam")))
        if (first.get("sceneCode") or 0) > 0:
            out["code"] = first["sceneCode"]
        if own_type:
            out["type"], out["rules"] = first.get("sceneType"), first.get("rules")
        elif not first.get("scenceParam") and (eff.get("scenceParamId") or 0) > 0:
            out["type"] = 0
        out["cmd_version"] = first.get("cmdVersion") or 0   # the DTO's int field: 0 when absent
        out["big_effect"] = first.get("bigEffectStr") == 1
    out["param"] = next((x["scenceParam"] for x in specials if x.get("scenceParam")), out["param"])
    out["param_id"] = next((x["scenceParamId"] for x in specials if x.get("scenceParamId")), out["param_id"])
    return out


def op_type(scene_type: int | None, cmd_version: int | None) -> int:
    """A lightEffect's op type from its scene type and cmdVersion for the SKU (CategoryV1.LightEffect.
    getOpType, CategoryV1.java:295-321; devices.yaml scenes.list_filter.op_type): 0, 1, 4 for a graffiti (3)
    or a cube (4), 2 or 3 for an RGBIC scene (2) at cmdVersion 0 or not, 5, 6, else -1."""
    t = scene_type or 0
    if t == 2:
        return 2 if not cmd_version else 3
    return {0: 0, 1: 1, 3: 4, 4: 4, 5: 5, 6: 6}.get(t, -1)


def scene_sets(sku: str) -> list[Collection[int]]:
    """Every scene set of ``sku``'s routes that offer scenes: each row's ``scene_versions`` and each of its
    ``by_pact`` and ``by_firmware`` ones, as :func:`op_set` and fetch() read them."""
    routes = [f for f in scene_rows().get(validate_sku(sku), []) if "scenes" in f["capabilities"]]
    return [s for f in routes for s in (f["scene_versions"], *(v for _p, v in f["scene_versions_by_pact"]),
                                        *(v for _s, _h, v in f["scene_versions_by_firmware"]))]


def op_set(sku: str) -> frozenset[int] | None:
    """The scene op set fetch() filters ``sku``'s scenes by, or None for no filter: the ScenesConfigBean's
    ``c`` (opScenesSet), which SceneDataVM takes as its op set (SceneDataVM.java:1632, ScenesConfigBean.m) and
    hands to CategoryV1.checkSupportNew, run only with one (:1502-1514). The library takes it from the SKU's
    rows: every scene row's ``scene_versions`` is its bean's ``c`` (devices.schema.json), so this is the
    union of the non-empty sets (row, ``by_pact``, ``by_firmware``) of the SKU's routes that offer scenes
    (:func:`scene_sets`), the newest firmware's as the library assumes before the device-info read. An empty
    set is the bean's null ``c``: no filter, which the union is when every set is empty or no row offers
    scenes. At each other set of the SKU's (:func:`_smaller_sets`,
    the empty one included) the app may hide a scene this keeps or play another of its effects: fetch()
    stores each such result in the scene's ``by_set``."""
    return frozenset(v for s in scene_sets(sku) for v in s) or None


def _smaller_sets(sku: str) -> list[tuple[str, frozenset[int]]]:
    """Each distinct scene set of ``sku``'s (:func:`scene_sets`) but the union (:func:`op_set`; an empty set
    where that is None), the empty one included, with its key (``scenes._set_key``, the loader's form), in
    key order."""
    from govee_ble_local import scenes

    union = op_set(sku) or frozenset()
    return sorted(((scenes._set_key(s), s) for s in {frozenset(s) for s in scene_sets(sku)} - {union}),
                  key=lambda pair: pair[0])


def effective(item: dict[str, Any], now_ms: float) -> bool:
    """Whether a category or lightEffect is in force at ``now_ms`` (SceneDataVM.Companion.g, SceneDataVM.java:
    256-268): it is, unless its ``checkEffectiveTime`` is 1 and ``now_ms`` falls in none of its
    ``effectiveTimes`` windows."""
    windows = item.get("effectiveTimes") or ()
    if item.get("checkEffectiveTime") != 1 or not windows:
        return True
    return any(w.get("startTime", 0) <= now_ms <= w.get("endTime", 0) for w in windows)


def _supported(sku: str, eff: dict[str, Any], ops: frozenset[int]) -> bool:
    """SceneV1.checkSupport's firmware-independent test of one lightEffect (CategoryV1.java:684-724): a base
    sceneType 0 passes whatever the op set (op4StaticTypeByVersion; its rules are the firmware gate, below);
    any other passes when its op type for the SKU is in ``ops``, and an op type 5 only with DIY effect codes
    (isSupport :591-602, getDiyEffectCode :277-289; EffectCodes.supportDiyEffect refuses none). Whether the
    device's own DIY codes hold them (its DiyConfigBean's EffectCodes) is not in the spec: kept."""
    if (eff.get("sceneType") or 0) == 0:
        return True
    own = for_sku(sku, eff)
    kind = op_type(own["type"], own["cmd_version"])
    if kind not in ops:
        return False
    if kind != 5:
        return True
    codes = next((x["diyEffectCode"] for x in eff.get("specialEffect") or ()
                  if sku in (x.get("supportSku") or ()) and x.get("diyEffectCode")), eff.get("diyEffectCode"))
    return bool(codes)


def listed_effect(sku: str, effects: list[dict[str, Any]], ops: frozenset[int] | None,
                  now_ms: float) -> tuple[dict[str, Any] | None, bool]:
    """The lightEffect the app's scene list plays for ``sku``, as ``(effect, gated)`` (devices.yaml
    scenes.list_filter): with an op set, SceneDataVM.b2 runs CategoryV1.checkSupportNew (SceneDataVM.java:
    1502-1514), which drops each lightEffect SceneV1.checkSupport refuses (:func:`_supported`) or whose
    version rules the firmware fails (checkVersion, filterVersion 1: isBleOpSupport4Set over getSupportRules,
    CategoryV1.java:166-191, 726-753); then Companion.k drops the expired ones (:func:`effective`, :1515). The
    scene plays the first left (getSceneType(0, ...) and the like). The firmware is unknown here, so when the
    first effect the firmware-independent tests leave has rules, which one plays depends on the firmware:
    ``(None, True)``, left out, because the library can't follow the gate and the next effect is not what the
    app plays where the rules hold. ``(None, False)`` when none is left: the app lists no such scene."""
    left = [e for e in effects if (ops is None or _supported(sku, e, ops)) and effective(e, now_ms)]
    if not left:
        return None, False
    if ops is not None and for_sku(sku, left[0])["rules"]:
        return None, True
    return left[0], False


def _label(category: str | None) -> str:
    """A category's name as a stored name's suffix (an entry's category is a str or null: validate_entry)."""
    return category or "no category"


def _apart(name: str, category: str | None) -> str:
    """``name`` stored apart for a scene of ``category``: "<name> (<category>)"."""
    return f"{name} ({_label(category)})"


def _described(name: str, entry: Entry, *more: str) -> str:
    """A stored name and its scene, as printed: '"<name>" (code <code>, <category>[; <more>])'."""
    return f'"{name}" ({"; ".join((_of(entry), *more))})'


def _of(entry: Entry) -> str:
    return f"code {entry.get('code')}, {_label(entry.get('category'))}"


def scene_name(name: str, entry: Entry) -> str:
    """The sceneName the app lists the scene stored as ``name`` under: its ``scene_name``, which fetch()
    writes where the two differ (a scene stored apart), else ``name``."""
    own: str | None = entry.get("scene_name")   # a str or null: validate_entry
    return own or name


def _scene(entry: Entry) -> tuple[object, object]:
    """A stored scene's identity, which its name keeps: code and category."""
    return (entry.get("code"), entry.get("category"))


def _code(entry: Entry) -> int:
    code: int = entry["code"]   # an int: every fetched, stored and retired entry passes validate_entry
    return code


def _order(entry: Entry) -> tuple[int, str, str]:
    """A total order on scenes that the app's list order does not enter: code, category, then the entry."""
    return (_code(entry), _label(entry.get("category")), _dumps(entry))


def _content(entry: Entry) -> Entry:
    """A stored entry's scene, without the sceneName it records (``scene_name``) and without its per-set
    results (``by_set``), which naming ignores: a scene is matched by its entry at the union of its SKU's
    sets only, so a change to its other effects never renames it."""
    return {k: v for k, v in entry.items() if k not in ("scene_name", "by_set")}


def _unresolved(entry: Entry) -> Entry | None:
    """A stored entry whose blob --resolve baked in (a big-effect scene with a param id), as the public
    library ships it: the placeholder of its code, category and param id (:func:`_content`, no blob). None
    for any other entry."""
    if not entry.get("big_effect") or entry.get("placeholder") or not entry.get("param") \
            or entry.get("param_id") is None:
        return None
    return {**_content(entry), "param": None, "placeholder": True}


Listed = tuple[str, Entry, frozenset[int]]   # (sceneName, entry, the codes of its lightEffects for the SKU)
_Fit = Callable[[Entry, frozenset[int], Entry], bool]   # (listed entry, its effects' codes, stored entry)


def _keys(*keys: str) -> _Fit:
    return lambda entry, _codes, stored: all(stored.get(k) == entry.get(k) for k in keys)


# How a listed scene matches a stored scene of its sceneName, best first, as (how, test). The first _EXACT
# hold its code and category, the scene's identity (:func:`_scene`): by its whole entry (one whose blob
# --resolve baked in: by its placeholder, stored_names), by blob and param id, by blob (neither one a
# placeholder, which has none: the library leaves it out, so only blob and param id, the key effect-strs
# resolves by, match one), by param id, by nothing more. The first _RETURN of those hold its blob too, the
# only ones a retired name comes back by. The rest are close: by one of its own lightEffects' codes (its
# category renamed, or the app's list plays another of its lightEffects now), by category and blob (its code
# changed: H6006's 26 recodes keep their blob), by category and param id, by category alone (code and blob
# both new: a guess; the param id, which changes at a kept code too, only breaks a tie).
_MATCHES: tuple[tuple[str, _Fit], ...] = (
    ("entry", lambda entry, _codes, stored: _content(stored) == entry),
    ("blob and param id", _keys("code", "category", "param", "param_id")),
    ("blob", lambda entry, _codes, stored: not _is_placeholder(entry) and not _is_placeholder(stored)
     and _keys("code", "category", "param")(entry, _codes, stored)),
    ("param id", _keys("code", "category", "param_id")),
    ("code and category", _keys("code", "category")),
    ("own code", lambda _entry, codes, stored: stored.get("code") in codes),
    ("category and blob", lambda entry, _codes, stored: bool(entry.get("param"))
     and _keys("category", "param")(entry, _codes, stored)),
    ("category and param id", lambda entry, _codes, stored: entry.get("param_id") is not None
     and _keys("category", "param_id")(entry, _codes, stored)),
    ("category", _keys("category")),
)
_EXACT, _RETURN = 5, 3
_GUESSES = frozenset({"category and param id", "category"})   # printed as guesses, not as kept scenes


def _how(entry: Entry, codes: frozenset[int], stored: Entry) -> str:
    """The first match of :data:`_MATCHES` by which ``stored`` fits the listed scene ``entry``: the one
    :func:`stored_names` paired them by."""
    return next(how for how, fits in _MATCHES if fits(entry, codes, stored))


def _groups(fits: Mapping[int, set[str]]) -> list[tuple[list[int], list[str]]]:
    """The connected groups of ``fits`` (listed scene -> the stored names that fit it), each as its listed
    scenes and its stored names, sorted."""
    holders: dict[str, set[int]] = {}
    for i, names in fits.items():
        for n in names:
            holders.setdefault(n, set()).add(i)
    out: list[tuple[list[int], list[str]]] = []
    done: set[int] = set()
    for first in sorted(fits):
        if first in done:
            continue
        scenes, names, todo = set(), set(), [first]
        while todo:
            i = todo.pop()
            if i not in scenes:
                scenes.add(i)
                for n in fits[i] - names:
                    names.add(n)
                    todo.extend(holders[n])
        done |= scenes
        out.append((sorted(scenes), sorted(names)))
    return out


def stored_names(listed: list[Listed], existing: Mapping[str, Entry] | None = None,
                 answers: Collection[Answer] = (), retired: Mapping[str, Entry] | None = None,
                 dropped: Collection[str] = (), ambiguous: dict[int, list[str]] | None = None) -> list[str]:
    """The names the ``listed`` scenes (:data:`Listed`) are stored under, one per scene, whatever the order
    they are listed in.

    - Each listed scene takes the name of a stored scene of its sceneName (:func:`scene_name`) in
      ``existing`` (the stored catalog) by the first match of :data:`_MATCHES` that pairs it, each match tried
      for every listed scene before the next, so the best match wins. A match pairs a listed scene and a
      stored scene that fit each other and no other (by the whole entry, alike ones in any number). Where
      more fit, those listed scenes take new names (``ambiguous``, when given, gets each one's index and the
      stored names that fit it) and those stored scenes stay as they are: no guess.
    - The whole-entry match takes a stored entry whose blob --resolve baked in, retired or not, by its
      placeholder (:func:`_unresolved`): the public library ships that scene so, and :func:`_merge` keeps the
      blob (main() gives a retired one's back). A placeholder, listed, stored or retired, has no blob to
      match by: by blob it matches only with its param id too.
    - A retired name (``retired``, the tombstone's, where ``existing`` does not hold it) is matched only by
      the whole entry or by code, category and blob: its scene's exact return.
    - A close match (short of code and category) passes over a stored scene in ``dropped`` (the
      tombstone's: one a fetch found gone from the library, or found its scene listed under another name or
      left out only for its time or rules at another code, since it was last listed), and,
      unless its code is one of the listed scene's own lightEffects', over one the library still answers
      (``answers``, fetch's :func:`_answers`): a guess (:data:`_GUESSES`) over one whose code it answers
      under any sceneName or category, or whose param id another listed scene carries (its scene, listed
      under another name), a blob match over one it answers at its own sceneName, category and code. That
      scene is still there, and its name stays its own.
    - Any other gets a name that neither ``existing``, ``retired`` nor another listed scene holds: its
      sceneName, which of the new scenes of that sceneName the highest code takes (:func:`_order`; the
      bundled H61A8 / H6641 catalog holds that one, the last-one-wins fetch's pick); else
      "<name> (<its category>)", or "<name> (<category>, 2)", 3 and so on, in code order. The sceneNames
      are given out before any is stored apart, so a sceneName that reads "<name> (<category>)" keeps it
      where it is free. So a first fetch names a response alike, whatever the store holds."""
    live = dict(existing or {})
    back = {n: e for n, e in (retired or {}).items() if n not in live}
    still = {code for _name, _category, code in answers}   # the codes some library scene is still at
    carriers: dict[object, set[int]] = {}   # param id -> the listed scenes that carry it
    for i, (_own, entry, _codes) in enumerate(listed):
        if entry.get("param_id") is not None:
            carriers.setdefault(entry["param_id"], set()).add(i)
    taken = set(live) | set(back)
    names: list[str | None] = [None] * len(listed)
    order = sorted(range(len(listed)), key=lambda i: (listed[i][0], _order(listed[i][1])))
    unsure: dict[int, list[str]] = {}
    for step, (how, fits) in enumerate(_MATCHES):
        pool = {**live, **back} if step < _RETURN else live
        found: dict[int, set[str]] = {}
        for i in order:
            own, entry, codes = listed[i]
            if names[i] is not None or i in unsure:
                continue
            elsewhere = {pid for pid, at in carriers.items() if at != {i}}   # listed as another scene
            hits = {n for n, e in pool.items() if scene_name(n, e) == own
                    and (fits(entry, codes, e) or (step == 0 and _unresolved(e) == entry))
                    and (step < _EXACT or (n not in dropped and (
                        e.get("code") in codes
                        or (e.get("code") not in still and e.get("param_id") not in elsewhere
                            if how in _GUESSES
                            else (own, e.get("category"), e.get("code")) not in answers))))}
            if hits:
                found[i] = hits
        for scenes, fitting in _groups(found):
            if step == 0:   # alike, so any pairing stores the same: as many as both hold
                pairs = list(zip(scenes, fitting, strict=False))
            elif len(scenes) == len(fitting) == 1:
                pairs = [(scenes[0], fitting[0])]
            else:   # more than one fits: no guess, and no later match takes these stored scenes either
                pairs = []
                unsure.update((i, fitting) for i in scenes)
            for i, n in pairs:
                names[i] = n
            for n in [n for _i, n in pairs] if step == 0 else fitting:
                live.pop(n, None)
                back.pop(n, None)
    if ambiguous is not None:
        ambiguous.update(unsure)
    taken.update(n for n in names if n is not None)
    new: dict[str, list[int]] = {}
    for i in order:
        if names[i] is None:
            new.setdefault(listed[i][0], []).append(i)
    for own, idx in new.items():
        if own not in taken:
            top = max(idx, key=lambda i: _order(listed[i][1]))
            names[top] = own
            taken.add(own)
    for own, idx in new.items():
        for i in idx:
            if names[i] is not None:
                continue
            label, n = _label(listed[i][1].get("category")), 2
            stored = _apart(own, label)
            while stored in taken:
                stored, n = f"{own} ({label}, {n})", n + 1
            names[i] = stored
            taken.add(stored)
    return [n for n in names if n is not None]


def _answers(sku: str, payload: Mapping[str, Any]) -> set[Answer]:
    """``(sceneName, category, code)`` of every scene the library answers with, in force or not, listed for
    ``sku`` or not (:func:`_scene_answers`)."""
    return {a for category in payload.get("data", {}).get("categories", [])
            for scene in category.get("scenes", []) for a in _scene_answers(sku, category, scene)}


def _scene_answers(sku: str, category: Mapping[str, Any], scene: Mapping[str, Any]) -> set[Answer]:
    """``(sceneName, category, code)`` of one library scene at each of its lightEffects' codes for ``sku``
    (:func:`for_sku`)."""
    codes = (for_sku(sku, eff)["code"] for eff in scene.get("lightEffects") or ())
    name, label = str(scene.get("sceneName")), category.get("categoryName")
    return {(name, label, c) for c in codes if isinstance(c, int)}


def _in_time(sku: str, effects: list[dict[str, Any]], ops: frozenset[int] | None, now_ms: float) -> bool:
    """Whether the app's scene list lists the scene of ``effects`` once its category and every effect are in
    force, or leaves it to the firmware's version rules (:func:`listed_effect`, with no effect's
    ``checkEffectiveTime`` set): a scene left out only for its time or its rules, which the library may list
    again with nothing changed. One it leaves out otherwise (:func:`_supported` refuses each effect) is out
    for good."""
    return listed_effect(sku, [{**e, "checkEffectiveTime": 0} for e in effects], ops, now_ms) != (None, False)


def _blob(param: object) -> str | None:
    """A lightEffect's ``scenceParam`` as stored: without the characters the app's decoder skips
    (_NOT_BASE64), None where nothing is left."""
    return (_NOT_BASE64.sub("", param) or None) if isinstance(param, str) else None


class Answered:
    """What a fetch learns of the scenes the library answers with, beyond the ones it lists (fetch's
    ``seen``, for :func:`_merge` and main()): ``live``, the (sceneName, category, code) of every lightEffect
    of each scene it lists or leaves out only for its time or the firmware's version rules (``held``, those
    of each one it leaves out so, and ``held_ids``, each of their lightEffects' param id with its place);
    ``gone``, the same of each scene the SKU's scene list refuses for good or the library does not upload
    (sceneType 5), with the (blob, param id) of each lightEffect the library answers there."""

    def __init__(self) -> None:
        self.live: set[Answer] = set()
        self.held: set[Answer] = set()
        self.held_ids: dict[object, set[Answer]] = {}   # param id -> its places, of each scene held so
        self.gone: dict[Answer, set[tuple[str | None, object]]] = {}

    def refuse(self, sku: str, category: Mapping[str, Any], scene: Mapping[str, Any]) -> None:
        """Count one library scene among ``gone``, at each of its lightEffects for ``sku``
        (:func:`for_sku`)."""
        name, label = str(scene.get("sceneName")), category.get("categoryName")
        for eff in scene.get("lightEffects") or ():
            own = for_sku(sku, eff)
            if isinstance(own["code"], int):
                self.gone.setdefault((name, label, own["code"]), set()).add((_blob(own["param"]),
                                                                             own["param_id"]))


def fetch(sku: str, app_version: str = DEFAULT_APP_VERSION, *, seen: Answered | None = None,
          existing: Mapping[str, Entry] | None = None, retired: Mapping[str, Entry] | None = None,
          dropped: Collection[str] = ()) -> dict[str, Entry]:
    """``sku``'s scenes from the public scene library: each listed scene's lightEffect for the SKU
    (:func:`listed_effect`, over :func:`op_set` and the categories in force), with the values the app reads
    for it (:func:`for_sku`, :func:`_entry`). A scene whose effect depends on the firmware's version rules is
    left out, and named on stdout. For a ``legacy_scene`` SKU the sceneType-5 scenes the library does not
    upload are left out (:func:`uploads_type5`) and named, before any scene is named.

    The app's list runs the same filter at the scene set in force, so each listed scene is evaluated again at
    each other scene set of the SKU's (:func:`_smaller_sets`; at an empty one, a null op set, SceneDataVM.b2
    runs no checkSupportNew, only Companion.k, SceneDataVM.java:1502-1515). Where the result there differs,
    the scene's entry gets a ``by_set`` of that set's key: None where nothing is left (the app lists no such
    scene there), or where the effect left first is one the library can't store (its version rules decide,
    it has no code, it is a sceneType-5 upload the library does not make, a placeholder, or an entry the
    shared check refuses, a number no int holds among them: never a failed fetch), which is named on stdout
    per set; else the entry of that effect. A per-set result changes nothing else: not the scene's entry, its
    name (stored_names never sees one) or what ``seen`` gets (main() names each scene whose result at a set
    is not the store's). A scene the union lists no effect of, but an empty set of the SKU's would, is left
    out (names never come back after the device-info read, which only shrinks the list) and named on stdout.

    The rest are named over ``existing``, the
    stored catalog, and the SKU's tombstone record, ``retired`` and ``dropped`` (:func:`stored_names`);
    stdout names each stored name whose scene has a new code or category (a match by category and param id
    or category alone as a guess), each new scene stored apart, and each new one more than one stored scene
    fits (main() names each retired name that comes back, once its sceneType-5 cut is done). ``seen``, when
    given, gets what the library answers with besides (:class:`Answered`, for :func:`_merge`): a scene left
    out only for its time (its category's or its effects') or the firmware's version rules (:func:`_in_time`)
    counts as live and held, its param ids with it, so _merge keeps it under its stored name, as one the
    library dropped; one left out for good, or for sceneType 5, as gone."""
    key = validate_sku(sku)
    seen = Answered() if seen is None else seen
    ops = op_set(key)
    smaller = _smaller_sets(key)
    versions = legacy_versions(key)
    now_ms = time.time() * 1000
    url = f"{LIBRARY_URL}?sku={urllib.parse.quote(key, safe='')}"
    req = urllib.request.Request(url, headers={"AppVersion": app_version})
    # Fixed https LIBRARY_URL; the only variable part (sku) is validated and URL-quoted above.
    with urllib.request.urlopen(req, timeout=20) as resp:  # nosec B310
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError(f"library response for {sku} exceeds {MAX_RESPONSE_BYTES} bytes; refusing")
    payload = json.loads(raw)
    named: list[Listed] = []
    per_set: list[dict[str, Entry | None]] = []   # each named scene's by_set
    contents: list[str] = []   # each named scene's lightEffects, as one string (_canon)
    gated: list[str] = []
    type5: list[str] = []
    only_empty: list[str] = []   # listed by the app at an empty set only
    unfollowed: dict[str, list[str]] = {}   # set key -> the scenes hidden there for an effect not stored
    for category in payload.get("data", {}).get("categories", []):
        # SceneDataVM.b2 skips a category out of its time (:1454-1461)
        in_force = effective(category, now_ms)
        for scene in category.get("scenes", []):
            effects = scene.get("lightEffects") or []
            answered = _scene_answers(key, category, scene)
            listed, held = listed_effect(key, effects, ops, now_ms) if in_force else (None, False)
            if held:
                gated.append(str(scene.get("sceneName")))
            if listed is None:
                if in_force and not held and any(not s for _k, s in smaller) \
                        and listed_effect(key, effects, None, now_ms)[0] is not None:
                    only_empty.append(str(scene.get("sceneName")))
                if _in_time(key, effects, ops, now_ms):
                    seen.live |= answered
                    seen.held |= answered
                    for eff in effects:   # each lightEffect's param id, at its own code
                        own = for_sku(key, eff)
                        if own["param_id"] is not None and isinstance(own["code"], int):
                            seen.held_ids.setdefault(own["param_id"], set()).add(
                                (str(scene.get("sceneName")), category.get("categoryName"), own["code"]))
                else:
                    seen.refuse(key, category, scene)
                continue
            entry = _entry(key, category, scene, listed)
            if entry is None:   # listed, but there is no code to activate it by
                seen.refuse(key, category, scene)
                continue
            if versions is not None and uploads_type5(entry, versions):
                type5.append(str(scene["sceneName"]))
                seen.refuse(key, category, scene)
                continue
            seen.live |= answered
            named.append((scene["sceneName"], entry, frozenset(code for _n, _c, code in answered)))
            overrides: dict[str, Entry | None] = {}
            for at_set, versions_at in smaller:
                pick, rules = listed_effect(key, effects, versions_at or None, now_ms)
                if pick is None and not rules:
                    overrides[at_set] = None   # none left there: the app lists no such scene
                    continue
                try:
                    alt = None if pick is None else _entry(key, category, scene, pick)
                except (ValueError, TypeError, OverflowError):   # not stored there: an effect the shared
                    alt = None   # check refuses, or one whose number no int holds (JSON reads 1e999 as inf)
                if alt == entry:
                    continue   # the union's pick plays there too (or an effect of the same values)
                if alt is None or _is_placeholder(alt) \
                        or (versions is not None and uploads_type5(alt, versions)):
                    overrides[at_set] = None   # listed there, with an effect the library can't store
                    unfollowed.setdefault(at_set, []).append(str(scene["sceneName"]))
                else:
                    overrides[at_set] = alt
            per_set.append(overrides)
            contents.append(_canon(effects))
    # Twins (one sceneName and one entry at the union) differ only in their other lightEffects, so naming,
    # which pairs alike scenes in the order they come, would give each its name by the response's order: they
    # take the order of their whole lightEffect content instead, so a reordered response never swaps which
    # name plays which effect at a smaller set, and a first fetch names them alike whatever the order.
    twins: dict[tuple[str, str], list[int]] = {}
    for i, (own, entry, _codes) in enumerate(named):
        twins.setdefault((own, _canon(entry)), []).append(i)
    for places in (idx for idx in twins.values() if len(idx) > 1):
        ranked = [(named[i], per_set[i]) for i in sorted(places, key=lambda i: contents[i])]
        for i, (item, overrides) in zip(places, ranked, strict=True):
            named[i], per_set[i] = item, overrides
    stored, back = existing or {}, retired or {}
    unsure: dict[int, list[str]] = {}
    names = stored_names(named, stored, _answers(key, payload), back, dropped, unsure)
    scenes: dict[str, Entry] = {}
    for (own, entry, _codes), name, overrides in zip(named, names, per_set, strict=True):
        if name != own:
            entry["scene_name"] = own
        if overrides:   # after naming, which ignores it
            entry["by_set"] = overrides
        scenes[name] = entry
    apart, moved, guessed, ambiguous = [], [], [], []
    for i, ((own, entry, codes), name) in enumerate(zip(named, names, strict=True)):
        was = stored.get(name)
        if was is None and name in back:
            continue   # a reclaim: main() prints it once its sceneType-5 cut is done
        if was is None and name != own:
            plain = scenes.get(own) or stored.get(own) or back.get(own) or {}
            apart.append(_described(name, entry, f'"{own}" is {_of(plain)}'))
        elif was is not None and _scene(was) != _scene(entry):
            how = _how(_content(entry), codes, was)
            (guessed if how in _GUESSES else moved).append(_described(name, entry, f"was {_of(was)}"))
        if i in unsure:
            fits = ", ".join(f'"{n}"' for n in unsure[i])
            ambiguous.append(_described(name, entry, f"could be {fits}"))
    if type5:
        print(f"{key}: left out {len(type5)} sceneType-5 scenes the app uploads and the library does not "
              f"(legacy_scene): {', '.join(type5)}")
    if apart:
        print(f"{key}: {len(apart)} new scenes share a name with another, stored apart: {', '.join(apart)}")
    if ambiguous:
        print(f"{key}: {len(ambiguous)} new scenes are ambiguous (more than one stored scene of their name "
              f"fits, or one fits more than one of them), so each took a new name: {', '.join(ambiguous)}")
    if moved:
        print(f"{key}: {len(moved)} stored names keep their scene at a new code or category: "
              f"{', '.join(moved)}")
    if guessed:
        print(f"{key}: {len(guessed)} stored names are guessed onto a scene of their sceneName and category "
              f"at a new code and blob, the only one either way: {', '.join(guessed)}")
    if gated:
        print(f"{key}: left out {len(gated)} scenes whose effect the firmware's version rules pick: "
              f"{', '.join(gated)}")
    if only_empty:
        print(f"{key}: left out {len(only_empty)} scenes the app lists only at its empty scene set (no op "
              f"filter): {', '.join(only_empty)}")
    for at_set, _versions_at in smaller:
        if at_set in unfollowed:
            print(f"{key}: hidden at scene set {_shown(at_set)}, where the app plays an effect the library "
                  f"can't store (version rules / no code / sceneType 5 / placeholder / invalid): "
                  f"{len(unfollowed[at_set])} scenes: {', '.join(unfollowed[at_set])}")
    return scenes


def _shown(at_set: str) -> str:
    """A scene set's key as printed: "[0, 1, 2]", "[]" for the empty set."""
    return f"[{', '.join(at_set.split(','))}]" if at_set else "[]"


def _entry(sku: str, category: Mapping[str, Any], scene: Mapping[str, Any],
           effect: dict[str, Any]) -> Entry | None:
    """The catalog entry of ``effect``, one lightEffect of the library ``scene`` in ``category``, with the
    values the app reads for ``sku`` (:func:`for_sku`), checked (:func:`validate_entry`); None when it has no
    code to activate it by. A param that is no string stays, so the check refuses it (and the SKU fails)."""
    eff = for_sku(sku, effect)
    if eff["code"] is None:
        return None
    entry: Entry = {
        "code": int(eff["code"]),
        "param": _blob(eff["param"]) if isinstance(eff["param"], str) else (eff["param"] or None),
        "category": category.get("categoryName"),
    }
    if eff["param_id"] is not None:
        entry["param_id"] = int(eff["param_id"])
    # Path-B upload drivers (see spec/docs/SCENE_UPLOAD_ENCODING.md): scene_type +
    # cmd_version pick the a3 commByte; bigEffectStr flags fetch-required.
    if eff["type"] is not None:
        entry["scene_type"] = int(eff["type"])
    if eff["cmd_version"] is not None:
        entry["cmd_version"] = int(eff["cmd_version"])
    if eff["big_effect"]:
        entry["big_effect"] = True
    if _is_placeholder(entry):
        # real blob must come from the authenticated effect-strs endpoint
        entry["placeholder"] = True
    validate_entry(scene.get("sceneName"), entry)
    return entry


def resolve_placeholders(email: str, password: str, skus: list[str],
                         app_version: str = DEFAULT_APP_VERSION) -> None:
    """Log into the Govee account and bake real effect blobs into the catalogs
    for placeholder scenes (via the authenticated effect-strs endpoint), so the
    shipped JSON is complete and no runtime cloud call is needed. The login and
    the effect-strs request carry ``app_version``, as the library fetch does (the
    cloud client's own default is the library's runtime one).

    Credentials: passed here only to obtain a token; nothing is persisted by
    this tool beyond the resulting scene blobs. Prefer env vars GOVEE_EMAIL /
    GOVEE_PASSWORD over CLI args.
    """
    import asyncio

    from govee_ble_local.cloud import GoveeCloudAccount

    async def run() -> None:
        acct = GoveeCloudAccount(email, password, app_version=app_version)
        try:
            await acct.login()
            for sku in skus:
                data = read_catalog(sku)
                ids = [v["param_id"] for v in data.values() if v.get("placeholder") and v.get("param_id")]
                if not ids:
                    continue
                real = await acct.get_scene_effect_strs(ids)
                fixed = 0
                for v in data.values():
                    pid = v.get("param_id")
                    if v.get("placeholder") and pid in real:
                        v["param"] = real[pid]
                        v.pop("placeholder", None)
                        fixed += 1
                store_catalog(sku, data)
                print(f"{sku}: resolved {fixed}/{len(ids)} placeholder scenes")
        finally:
            await acct.close()

    asyncio.run(run())


def _valid_resolved(entry: dict[str, object]) -> bool:
    """True if an existing catalog entry is already a complete, usable
    definition we should PRESERVE rather than clobber with a fresh public
    fetch: it is NOT flagged as an unresolved placeholder (every stored and
    retired entry has an int code: read_store and read_tombstone check it with
    the shared check). The ``placeholder`` flag is the source of truth — --resolve
    pops it once the real blob is baked in. A ``param``-less (bare-activate) scene
    is valid too."""
    return not entry.get("placeholder")


def _resolved(existing: Mapping[str, Entry], name: str, fr: Entry) -> Entry | None:
    """The existing entry whose blob, resolved by --resolve, the fresh entry ``fr`` (stored as ``name``)
    keeps: ``fr`` is the public placeholder (a big-effect scene without its blob) of a resolved entry with its
    param_id (the key effect-strs resolves by), the one case where the existing blob beats the fresh fetch.
    That is the entry stored under ``name`` (stored_names matched it to ``fr``, through a renumber or a
    category rename too), else one of the same code and category under another name, else the resolved
    entries of that (non-null) param id where they hold one blob (one scene under more names), under whatever
    names and codes (a scene that took a new name). Only an entry with a blob counts: a bare one of that param
    id (never a placeholder) has none to keep, and ``fr`` stays a placeholder."""
    if not fr.get("placeholder"):
        return None

    def blob(ex: Entry) -> bool:
        return _valid_resolved(ex) and bool(ex.get("param"))

    own = existing.get(name)
    pid = fr.get("param_id")
    if own is not None and blob(own) and pid is not None and own.get("param_id") == pid:
        return own
    for n in sorted(existing):
        ex = existing[n]
        if (blob(ex) and (ex.get("code"), ex.get("category"), ex.get("param_id"))
                == (fr.get("code"), fr.get("category"), pid)):
            return ex
    same = [ex for ex in existing.values() if blob(ex) and pid is not None and ex.get("param_id") == pid]
    return same[0] if len({ex.get("param") for ex in same}) == 1 else None   # one blob, under any names


def _merge(existing: dict[str, Entry], fresh: dict[str, Entry],
           seen: Answered | None = None, retired: Mapping[str, Entry] | None = None,
           dropped: Collection[str] = ()) -> dict[str, Entry]:
    """Combine a fresh public fetch, named over ``existing`` (fetch's ``existing``, so a name both hold is one
    scene), with the existing catalog: take the fresh data, except that a fresh placeholder never replaces a
    real effect blob already resolved via --resolve (:func:`_resolved`: the fresh entry, its code, category
    and upload metadata replaced, not merged, keeps that blob); and keep each stored scene the fetch no longer
    lists, under its name. This makes re-running idempotent and non-destructive once placeholders have been
    resolved. ``seen`` is fetch's: a stored scene not listed whose scene the SKU's scene list refuses for good
    (:func:`listed_effect`), or that the library does not upload (sceneType 5), is not kept (:func:`_gone`,
    over the stored and the retired scenes, the tombstone's, that the fetch does not list, the param ids it
    lists or leaves out only for their time or version rules, and the tombstone's ``dropped``). One the
    library dropped, one left out only for its time or version rules, and one whose scene is listed under
    another name or left out so at another place (its param id) stay under their names."""
    merged: dict[str, Entry] = {}
    for name, fr in fresh.items():
        ex = _resolved(existing, name, fr)
        merged[name] = fr if ex is None else {**{k: v for k, v in fr.items() if k != "placeholder"},
                                              "param": ex.get("param")}
    # the scenes a refused one may be: a listed one is not
    unlisted = {n: e for n, e in existing.items() if n not in fresh}
    out = {n: e for n, e in (retired or {}).items() if n not in fresh}
    shown = ({e.get("param_id") for e in fresh.values()} | set(seen.held_ids if seen else ())) - {None}
    for name, ex in unlisted.items():
        if seen is None or not _gone(name, ex, seen, unlisted, out, shown, name in dropped):
            merged[name] = ex  # scene no longer public but we have it
    return merged


def _answer_of(name: str, entry: Entry) -> Answer:
    """The (sceneName, category, code) the library answers the scene stored as ``name`` at."""
    return (scene_name(name, entry), entry.get("category"), _code(entry))


def _gone(name: str, entry: Entry, seen: Answered, stored: Mapping[str, Entry],
          retired: Mapping[str, Entry], shown: Collection[object], dropped: bool = False) -> bool:
    """Whether the scene stored as ``name`` is one the library answers with and leaves out for good
    (``seen.gone``), among the other ``stored`` and ``retired`` scenes the fetch does not list. It is where
    the library answers its sceneName (:func:`scene_name`), category and code so:

    - with its blob and param id, unless another of them there has both too (two scenes of one blob and param
      id can't be told apart, so each stays);
    - else, unless the fetch lists its param id or leaves it out only for its time or rules (``shown``: its
      scene is listed under another name, or held at another place), with its blob, where no other of them
      there has that blob or is a resolved one the library answers there as its placeholder (of its param
      id: that one may be the scene refused) and no same-named scene there is left out only for its time or
      rules (``seen.held``): two scenes of one blob differ by their param id alone;
    - or, unless the fetch lists or holds its param id so, with another blob, where no scene listed or left
      out only for its time or rules answers it there (``seen.live``: a same-named scene of one code is not
      taken for it), no other stored scene sits there (which of them the library changed is not known, so each
      stays), no retired one there holds a blob it answers or is a resolved one it answers as its placeholder
      (that one is the scene left out; this one was dropped) and the name is not on the tombstone's dropped
      list (``dropped``: its scene left the library, or was listed or held elsewhere, since it was last
      listed; the one refused there now is another)."""
    at = _answer_of(name, entry)
    ids = seen.gone.get(at)
    if ids is None:
        return False
    blob, pid, blobs = entry.get("param"), entry.get("param_id"), {b for b, _pid in ids}

    def there(scenes: Mapping[str, Entry]) -> list[Entry]:
        return [e for n, e in scenes.items() if n != name and _answer_of(n, e) == at]

    def placeholder_of(e: Entry) -> bool:   # a resolved one, answered as its placeholder of its param id
        return _unresolved(e) is not None and (None, e.get("param_id")) in ids

    others = [*there(stored), *there(retired)]
    if (blob, pid) in ids:
        return all((e.get("param"), e.get("param_id")) != (blob, pid) for e in others)
    if pid in shown:
        return False
    if blob in blobs:
        return at not in seen.held and all(e.get("param") != blob and not placeholder_of(e) for e in others)
    return not dropped and at not in seen.live and not there(stored) and not any(
        e.get("param") in blobs or placeholder_of(e) for e in there(retired))


def _still_there(name: str, entry: Entry, answers: Collection[Answer], scenes: Mapping[str, Entry],
                 retired: Mapping[str, Entry]) -> bool:
    """Whether the library still answers the scene stored as ``name``: its sceneName at its code
    (``answers``, fetch's ``seen.live``: listed, or left out only for its time or rules; a scene refused for
    good or cut is not still there) under a category where no other stored name (``scenes``) and no retired
    one (``retired``: the tombstone's with this fetch's removals, but the ones ``scenes`` holds again) sits,
    its own or another (one out only for its time or rules as its category is renamed). An answer where
    another name sits is that name's scene, a retired one's too (its scene refused for good or cut, and out
    only for its time or rules since)."""
    own, _category, code = _answer_of(name, entry)
    held = {_answer_of(n, e) for m in (scenes, retired) for n, e in m.items() if n != name}
    return any(a not in held for a in answers if a[0] == own and a[2] == code)


# SKUs whose scene list is compiled into the app, not served by the scene library: their
# bundled catalog is hand-derived from the APK and must not be overwritten by a fetch.
# H6188: rgblight Scenes4H6188 (fixed codes, activate-only; DelegateSceneV1 makes no cloud call).
APP_DEFINED_SCENE_SKUS = frozenset({"H6188"})


# parseSceneV1's sceneType-5 branches, versions 7 to 13 (ScenesOp.parseSceneV1,
# base-classes10/com/govee/base2light/ble/ScenesOp.java:555-575; devices.yaml scenes.dispatch.table).
_TYPE5_VERSIONS = frozenset(range(7, 14))


def uploads_type5(entry: Entry, versions: frozenset[int]) -> bool:
    """True if the app uploads this sceneType-5 scene on a ``legacy_scene`` screen whose scene versions are
    ``versions``: one whose decoded param leads 0x50 (at least 2 bytes) goes through the goodsType's new-DIY
    parser (BaseSceneViewMode.r3, base-classes11/com/govee/base2light/pact/newdetail/content/scene/
    BaseSceneViewMode.java:1152-1200), and any other through a sceneType-5 branch of parseSceneV1 where the
    versions hold one. The library uploads no sceneType-5 scene under legacy_scene (it only activates one), so
    such a scene is left out of the catalog until its path is modelled."""
    if entry.get("scene_type") != 5:
        return False
    if versions & _TYPE5_VERSIONS:
        return True
    raw = base64.b64decode(entry.get("param") or "")
    return len(raw) >= 2 and raw[0] == 0x50


def scene_rows(levels: frozenset[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    """``{SKU: [profile fields of each of its routes]}`` for the device table's rows at ``levels`` (default:
    every level), resolved without the library's catalog cut: a profile offers scenes only to a SKU with a
    bundled catalog (devices/_resolve.py), and these are the facts its row gives before that, so a SKU whose
    row offers scenes is listed before its catalog exists."""
    from govee_ble_local._generated import devices
    from govee_ble_local.devices import _resolve

    out: dict[str, list[dict[str, Any]]] = {}
    for skus, fields, _routes in _resolve.profiles(devices.FAMILIES, devices.MODE_COLOR_PARSE,
                                                   _resolve.LEVELS if levels is None else levels):
        for sku in skus:
            out.setdefault(sku.upper(), []).append(fields)
    return out


def legacy_versions(sku: str) -> frozenset[int] | None:
    """The scene versions of ``sku``'s ``legacy_scene`` routes (every version of their pact and firmware
    sets), or None when each of its routes uploads sceneType 5 in a dialect of its own (H60A6, H6052). A SKU
    the table has no row for counts as legacy_scene with no versions, so a fetch for it leaves out what a
    legacy screen would upload."""
    routes = [f for f in scene_rows().get(validate_sku(sku), []) if "scenes" in f["capabilities"]]
    legacy = [f for f in routes if f["scene_dialect"] == "A"]
    if routes and not legacy:
        return None
    sets = [s for f in legacy for s in (f["scene_versions"], *(v for _p, v in f["scene_versions_by_pact"]),
                                        *(v for _s, _h, v in f["scene_versions_by_firmware"]))]
    return frozenset(v for s in sets for v in s)


def scene_capable_skus() -> list[str]:
    """The SKUs whose admitted row offers scenes (so new families are covered as they're added to
    devices.yaml), whether or not a catalog is bundled for them yet, except the APP_DEFINED_SCENE_SKUS
    whose catalogs are not from the scene library."""
    from govee_ble_local.devices import _resolve

    return sorted(sku for sku, routes in scene_rows(_resolve.ADMITTED).items()
                  if any("scenes" in f["capabilities"] for f in routes) and sku not in APP_DEFINED_SCENE_SKUS)


def audit(skus: list[str]) -> None:
    """Report per-SKU scene-upload READINESS (read-only, no cloud). For each
    SKU, at each of its scene sets (the union, :func:`op_set`, which is the
    device's before the device-info read, then each smaller one,
    :func:`_smaller_sets`: one row each, its versions last), run every scene
    of the catalog the library offers at that set (``load_scenes(sku,
    scene_versions=...)``, the effects it plays there included) through the
    real upload planner at that set
    (``scenes.scene_upload_frames`` with the device's scene dialect, as
    ``Device.set_scene_by_name`` plans it) and put it in exactly one bucket
    (the four add up to ``total``):
      upload      = a dialect burst is produced (0xA3 / 0xA4-MTU)
      activate    = correctly bare-activated (no param, or static sceneType 0)
      BLOCKED     = has a real effect blob + non-static sceneType but NO upload path
                    (would silently activate-only — the gap to close), or the
                    planner rejects it (GoveeBleInvalidValue / GoveeBleNotSupported)
      placeholder = a big-effect scene without its blob (needs `--resolve` with account creds)
    Any other exception from the planner is a bug and propagates.
    """
    from bleak.backends.device import BLEDevice

    from govee_ble_local import scenes
    from govee_ble_local.exceptions import GoveeBleInvalidValue, GoveeBleNotSupported
    from govee_ble_local.registry import create_device

    print(
        f"{'SKU':7s} {'total':>5s} {'upload':>16s} {'activate':>8s} {'BLOCKED':>7s} {'placeh':>6s}  versions"
    )
    for sku in skus:
        cat = scenes.load_scenes(sku)
        if not cat:
            print(f"{sku:7s} (no catalog)")
            continue
        # bleak < 1.0 requires rssi (the dependency floor is 0.21); >= 1.0 dropped it.
        ble_kwargs: dict[str, Any] = {"details": {}}
        if "rssi" in inspect.signature(BLEDevice.__init__).parameters:
            ble_kwargs["rssi"] = 0
        try:
            dev = create_device(BLEDevice("00:00:00:00:00:00", sku, **ble_kwargs), sku)
        except GoveeBleNotSupported:
            print(f"{sku:7s} (not a supported SKU)")
            continue
        union = op_set(sku) or frozenset()   # the catalog's own set, the device's before its device-info read
        for versions in (union, *(s for _k, s in _smaller_sets(sku))):
            at = cat if versions == union else scenes.load_scenes(sku, scene_versions=versions)
            upload = activate = blocked = placeholder = 0
            dialects: set[str] = set()
            for scene in at.values():
                if scene.placeholder:
                    placeholder += 1
                    continue
                try:
                    frames = scenes.scene_upload_frames(scene, dev.profile.scene_dialect, versions)
                except (GoveeBleInvalidValue, GoveeBleNotSupported):
                    blocked += 1  # the planner refuses this blob: a real upload gap
                    continue
                if frames:
                    upload += 1
                    dialects.add("0xA4" if any(f[0] == 0xA4 for f in frames) else "0xA3")
                elif scene.param and scene.scene_type not in (0, None):
                    blocked += 1
                else:
                    activate += 1
            u = f"{upload}{sorted(dialects) if dialects else ''}"
            print(f"{sku:7s} {len(at):5d} {u:>16s} {activate:8d} {blocked:7d} {placeholder:6d}  "
                  f"{sorted(versions)}")


_UNION: Final = "as at the union"   # a scene with no per-set result at a set: its entry at the union plays


def _set_changes(sku: str, existing: Mapping[str, Entry], scenes: Mapping[str, Entry],
                 sets: Collection[str]) -> list[str]:
    """A line per scene set of ``sets`` (keys) naming each scene of ``scenes`` whose result there is not the
    one the store held (``existing``; a scene new to the store held none, so its entry at the union played
    there): hidden there, plays an effect there (its code and category), changes from one effect there to
    another (or to a new entry of the same code and category), or listed again there, as at the union or
    with an effect. Naming never sees a per-set result, so this is the only line such a change gets."""
    def there(entry: Entry | None, at: str) -> Entry | str | None:   # the one str is _UNION
        own = {} if entry is None else entry.get("by_set", {})
        return own.get(at, _UNION)

    lines: list[str] = []
    for at in sorted(sets):
        changed: list[str] = []
        for n in sorted(scenes):
            was, now = there(existing.get(n), at), there(scenes[n], at)
            if was == now:
                continue
            if now is None:
                changed.append(f'"{n}" hidden')
            elif was is None:
                how = "as at the union" if isinstance(now, str) else f"plays {_of(now)}"
                changed.append(f'"{n}" listed again, {how}')
            elif isinstance(now, str):
                changed.append(f'"{n}" plays its entry at the union again ({_of(scenes[n])})')
            elif isinstance(was, str):
                changed.append(f'"{n}" plays {_of(now)}')
            else:
                new = ", a new entry" if _of(was) == _of(now) else ""
                changed.append(f'"{n}" changes from {_of(was)} to {_of(now)}{new}')
        if changed:
            lines.append(f"{sku}: {len(changed)} scenes change at scene set {_shown(at)}: "
                         f"{', '.join(changed)}")
    return lines


def _reconciled(entry: Entry, sets: Sequence[tuple[str, frozenset[int]]]) -> Entry:
    """A stored scene the fetch no longer lists (kept under its name), its per-set results reconciled with
    the SKU's smaller scene sets ``sets`` (:func:`_smaller_sets`): one at a set the SKU no longer has is
    dropped, and at a set it has none for (one added since it was last listed) the scene is hidden where its
    entry's op type is outside the set (a base sceneType 0 passes any): the app then plays a later
    lightEffect or hides it (DESIGN section 1), which the store can't know, so the library can't follow it.
    At the empty set (no op filter) the entry stands."""
    own = entry.get("by_set", {})
    out: dict[str, Entry | None] = {}
    for at, versions in sets:
        if at in own:
            out[at] = own[at]
        elif versions and (entry.get("scene_type") or 0) != 0 \
                and op_type(entry.get("scene_type"), entry.get("cmd_version")) not in versions:
            out[at] = None
    return {**_default(entry), **({"by_set": out} if out else {})}


def main(skus: list[str], app_version: str = DEFAULT_APP_VERSION) -> int:
    """Fetch + merge each SKU's catalog. Returns the number of SKUs that failed. Every stored name the store
    loses is retired in the tombstone (:data:`TOMBSTONE`), written before the store, whatever removed it."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0
    for raw in skus:
        try:   # the tombstone's records are keyed by the upper-case SKU, as the store's are
            sku = validate_sku(raw)
        except ValueError as err:
            print(f"{raw}: FETCH FAILED: {err}", file=sys.stderr)
            failures += 1
            continue
        if sku in APP_DEFINED_SCENE_SKUS:
            print(f"{sku}: skipped (scene list is compiled into the app, not the scene library)")
            continue
        seen = Answered()
        try:
            existing = read_catalog(sku)
            tomb = read_tombstone()
            record = tomb.get(sku, {})
            retired: dict[str, Entry] = record.get("retired", {})
            fresh = fetch(sku, app_version, seen=seen, existing=existing, retired=retired,
                          dropped=record.get("dropped", ()))
        except Exception as err:
            print(f"{sku}: FETCH FAILED: {err}", file=sys.stderr)
            failures += 1
            continue
        # a reclaimed name's retired entry, so its blob --resolve baked in comes back with it; fresh holds
        # every one of them, so _merge keeps no other retired name
        base = {**existing, **{n: retired[n] for n in fresh if n in retired and n not in existing}}
        scenes = _merge(base, fresh, seen, retired, record.get("dropped", ()))
        versions = legacy_versions(sku)
        if versions is not None:   # a new name keeps its listed placeholder, not another's blob the cut drops
            for n in fresh:
                if n not in base and uploads_type5(scenes[n], versions) \
                        and not uploads_type5(fresh[n], versions):
                    scenes[n] = fresh[n]
        held = sorted(n for n, v in scenes.items() if versions is not None and uploads_type5(v, versions))
        cut = {n: scenes[n] for n in held}
        if held:   # fetch() leaves out the listed ones: these are stored scenes kept, or stored blobs merged
            scenes = {n: v for n, v in scenes.items() if n not in held}
            print(f"{sku}: left out {len(held)} sceneType-5 scenes the app uploads and the library does not "
                  f"(legacy_scene): {', '.join(held)}")
        # a kept scene's per-set effect the cut drops: the scene is not listed at that set (fetch() stores a
        # listed one's so)
        recut: dict[str, list[str]] = {}
        for n, v in scenes.items():
            for at_set, alt in v.get("by_set", {}).items():
                if versions is not None and alt is not None and uploads_type5(alt, versions):
                    recut.setdefault(at_set, []).append(n)
        for at_set, names in sorted(recut.items()):
            for n in names:
                scenes[n] = {**scenes[n], "by_set": {**scenes[n]["by_set"], at_set: None}}
            print(f"{sku}: hidden at scene set {_shown(at_set)}, where the app plays an effect the library "
                  f"can't store (sceneType 5): {len(names)} scenes: {', '.join(names)}")
        # a kept scene's per-set results, from the fetch that last listed it, follow the SKU's sets now (a
        # listed one's are this fetch's, at exactly those sets)
        smaller = _smaller_sets(sku)
        stale: dict[str, list[str]] = {}
        for n in [n for n in scenes if n not in fresh]:
            for at_set in sorted(set(scenes[n].get("by_set", {})) - {k for k, _v in smaller}):
                stale.setdefault(at_set, []).append(n)
            scenes[n] = _reconciled(scenes[n], smaller)
        for at_set, names in sorted(stale.items()):
            print(f"{sku}: scene set {_shown(at_set)} is none of the SKU's now: the results there of "
                  f"{len(names)} stored scenes are dropped: {', '.join(names)}")
        if not scenes:
            print(f"{sku}: no scenes in the library; nothing stored")
            continue
        back = sorted(n for n in scenes if n in retired and n not in existing)
        if back:   # after the cut: a reclaimed name it drops stays retired
            print(f"{sku}: {len(back)} retired names come back with their scene (its sceneName, code, "
                  f"category and blob), reclaimed: {', '.join(_described(n, scenes[n]) for n in back)}")
        gone = sorted(n for n in existing if n not in scenes)
        if gone:
            print(f"{sku}: removed {len(gone)} stored names whose scene the SKU's scene list refuses for "
                  f"good, or the library does not upload; retired, so only that scene's return takes one "
                  f"back: {', '.join(_described(n, existing[n]) for n in gone)}")
        for line in _set_changes(sku, existing, scenes, [k for k, _v in smaller]):
            print(line)
        # dropped: a kept name the library no longer lists or holds (:func:`_still_there`), or whose scene
        # (its param id) the fetch lists under another name or leaves out only for its time or rules at
        # another code (an answer at its own place is another scene's), and stays so until it is listed again
        # (a match by code and category) or removed
        answers = seen.live   # a scene refused for good or cut is not still there, at any category
        was_dropped = set(record.get("dropped", ()))
        # a name this fetch matched and the cut drops retires with the entry it was to hold, so its scene's
        # identical return reclaims it (and the cut drops it again): a re-fetch changes nothing
        # (its entry at the union only: a name comes back by that alone)
        retire = {**retired, **{n: _default(cut[n] if n in fresh else existing[n]) for n in gone}}
        out = {n: e for n, e in retire.items() if n not in scenes}   # a reclaimed name sits in scenes
        listed_ids = {e.get("param_id") for e in fresh.values()} - {None}
        # held at another code only: a scene whose category is renamed while it is out still follows
        elsewhere = {n for n, v in scenes.items() if v.get("param_id") in listed_ids
                     or any(c != _code(v) for _n, _c, c in seen.held_ids.get(v.get("param_id"), ()))}
        now = _record(retire, [n for n, v in scenes.items() if n not in fresh
                               and (n in was_dropped or n in elsewhere
                                    or not _still_there(n, v, answers, scenes, out))])
        try:
            if now != record:   # the tombstone first: a name is retired before the store loses it
                write_catalog(TOMBSTONE, {s: r for s, r in sorted({**tomb, sku: now}.items()) if r})
            pooled = store_catalog(sku, scenes)
        except (OSError, ValueError) as err:
            print(f"{sku}: WRITE FAILED: {err}", file=sys.stderr)
            failures += 1
            continue
        # preserved = taken from the existing catalog: resolved blobs kept over a public stub,
        # plus scenes that are no longer in the public library
        kept = sum(1 for n in scenes if n not in fresh or scenes[n] != fresh[n])
        placeholders = sorted(n for n, v in scenes.items() if v.get("placeholder"))
        note = f"; {len(placeholders)} placeholders (run --resolve)" if placeholders else ""
        differ = sum(1 for v in scenes.values() if v.get("by_set"))
        note += f"; {differ} scenes differ at a smaller scene set" if differ else ""
        print(
            f"{sku}: {len(scenes)} scenes -> scenes/{STORE_NAME} ({pooled} in the pool) "
            f"[{kept} preserved]{note}"
        )
        if placeholders:
            print(f"    unresolved placeholders: {', '.join(placeholders)}")
    if failures:
        print(f"{failures}/{len(skus)} SKU fetch(es) failed", file=sys.stderr)
    return failures


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skus", nargs="*", metavar="SKU",
                    help="SKUs to fetch / resolve / audit (default: all scene-capable)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--audit", action="store_true", help="per-SKU upload readiness; no cloud call")
    mode.add_argument("--resolve", action="store_true",
                      help="bake real blobs for placeholder scenes (needs GOVEE_EMAIL/GOVEE_PASSWORD)")
    ap.add_argument("--app-version", default=os.environ.get("GOVEE_APP_VERSION", DEFAULT_APP_VERSION),
                    help="AppVersion header of the fetch and of the --resolve login "
                         f"(env GOVEE_APP_VERSION; default {DEFAULT_APP_VERSION})")
    args = ap.parse_args(argv)
    try:
        args.skus = [validate_sku(x) for x in args.skus]
    except ValueError as err:
        ap.error(str(err))
    return args


if __name__ == "__main__":
    _a = parse_args()
    _skus = _a.skus or scene_capable_skus()
    if _a.audit:
        audit(_skus)
    elif _a.resolve:
        _email = os.environ.get("GOVEE_EMAIL", "")
        _password = os.environ.get("GOVEE_PASSWORD", "")
        if not _email or not _password:
            sys.exit("--resolve needs GOVEE_EMAIL and GOVEE_PASSWORD env vars")
        resolve_placeholders(_email, _password, _skus, _a.app_version)
    else:
        sys.exit(1 if main(_skus, _a.app_version) else 0)
