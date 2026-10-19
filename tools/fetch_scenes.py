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

A stored name is a Home Assistant effect name that automations keep, so a re-fetch never points one
at another scene (stored_names). Each stored scene records the sceneName the app lists it under
(``scene_name``, written only where the stored name differs) and its category. A listed scene keeps
the stored name of the stored scene of its sceneName it matches: by code, category and blob, else by
code and category, by code (its category renamed), by category and blob, by category (its code
changed); the moves are printed, and a match short of code and category passes over a stored scene
the library still answers as stored. Only a scene that matches none gets a new name, never one the
stored catalog holds: the name another SKU's stored catalog gives the same scene where that is free,
else its sceneName where that is free, else "<name> (<its category>)" (for example
"Flash (Emotion)"), or "<name> (<category>, 2)", 3 and so on. The app lists a name in more than one category
(H61A8 / H6641: Emotion's "Flash", code 2020, and Zootopia 2's, 14769); of the new scenes of one
sceneName that no other SKU names, the highest code keeps it, whatever the app's list order (the
bundled catalog holds 14769 so). A stored scene the fetch no longer lists stays under its name: one
the library dropped, and one left out only for its time (an expired category or effect) or the
firmware's version rules. Only one the library still answers and the SKU's scene list refuses for
good (its op set), or a sceneType-5 scene the library does not upload, is removed, and printed.

    python3 tools/fetch_scenes.py            # all supported SKUs
    python3 tools/fetch_scenes.py H60A6 H61A8
"""
from __future__ import annotations

import argparse
import base64
import binascii
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
from collections.abc import Collection, Mapping
from typing import Any

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
Entry = dict[str, Any]  # one scene's catalog entry (JSON object)
Answer = tuple[str, object, int]  # (sceneName, category, code) of a scene the library answers with


def validate_sku(sku: str) -> str:
    """Return the upper-cased SKU, or raise ValueError if it is not 3-8 alphanumerics."""
    if not _SKU_RE.fullmatch(sku):
        raise ValueError(f"invalid SKU {sku!r}: expected 3-8 alphanumeric characters")
    return sku.upper()


def validate_entry(name: object, entry: dict[str, object]) -> None:
    """Check one fetched scene against what ``scenes.load_scenes`` requires; raises ValueError. That refuses
    a scene with ``rules`` (version-gated in the app, which the library can't follow)."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"scene name {name!r} is not a non-empty string")
    if not isinstance(entry.get("code"), int) or isinstance(entry.get("code"), bool):
        raise ValueError(f"{name}: missing/non-integer 'code'")
    if entry.get("rules"):
        raise ValueError(f"{name}: gated by version rules, which the library can't follow")
    for key, typ in (("param", str), ("category", str), ("param_id", int),
                     ("scene_type", int), ("cmd_version", int), ("scene_name", str)):
        v = entry.get(key)
        if v is not None and not isinstance(v, typ):
            raise ValueError(f"{name}: '{key}' must be {typ.__name__} or null, got {type(v).__name__}")
    param = entry.get("param")
    if isinstance(param, str) and param:
        try:
            base64.b64decode(param, validate=True)
        except (binascii.Error, ValueError) as err:
            raise ValueError(f"{name}: 'param' is not valid base64: {err}") from err


def write_catalog(path: pathlib.Path, data: object) -> None:
    """Write the scene store (or another JSON file) atomically: temp file in the same directory, then
    ``os.replace``."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_dumps(data))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _dumps(data: object) -> str:
    return json.dumps(data, indent=1, sort_keys=True)


def _canon(entry: Entry) -> str:
    """A pool entry's content as one string: its JSON with sorted keys and no whitespace."""
    return json.dumps(entry, sort_keys=True, separators=(",", ":"))


def layout(catalogs: Mapping[str, Mapping[str, Entry]]) -> dict[str, Any]:
    """The scene store of ``catalogs`` ({SKU: {name: entry}}), as the library reads it (scenes/__init__.py):
    ``pool``, each scene of them once (its entry with its ``name``), sorted by name, then by its JSON with
    sorted keys; ``skus``, each SKU's pool indexes in increasing order, which is its catalog's name order.
    The same catalogs give the same store, whatever order they or their scenes come in, and a scene no SKU
    holds is in no pool. Raises ValueError on an empty catalog, an invalid scene (:func:`validate_entry`) or
    an entry that carries a ``name`` of its own."""
    pool: dict[str, Entry] = {}   # _canon -> pool entry
    refs: dict[str, list[str]] = {}   # SKU -> its scenes' _canon
    for sku, scenes in catalogs.items():
        if not scenes:
            raise ValueError(f"{sku}: an empty catalog is not stored")
        for name, entry in scenes.items():
            validate_entry(name, entry)
            if "name" in entry:
                raise ValueError(f"{sku}: scene {name!r} carries a 'name' of its own")
            full = {"name": name, **entry}
            pool.setdefault(_canon(full), full)
            refs.setdefault(validate_sku(sku), []).append(_canon(full))
    order = sorted(pool, key=lambda c: (pool[c]["name"], c))
    at = {c: i for i, c in enumerate(order)}
    return {"pool": [pool[c] for c in order],
            "skus": {sku: sorted(at[c] for c in own) for sku, own in sorted(refs.items())}}


def read_store(out_dir: pathlib.Path | None = None) -> dict[str, dict[str, Entry]]:
    """Every SKU's stored catalog, ``{SKU: {name: entry}}`` with each entry without its ``name`` ({} when
    there is no store yet). The store is checked as the library reads it (``scenes._catalogs``): ValueError
    when it is malformed."""
    from govee_ble_local import scenes

    path = (out_dir or OUT_DIR) / STORE_NAME
    if not path.exists():
        return {}
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
        scenes._catalogs(store)
    except (ValueError, TypeError, KeyError, AttributeError) as err:
        raise ValueError(f"{STORE_NAME} is malformed: {err!r}") from err
    pool = store["pool"]
    return {sku: {pool[i]["name"]: {k: v for k, v in pool[i].items() if k != "name"} for i in refs}
            for sku, refs in store["skus"].items()}


def read_catalog(sku: str, out_dir: pathlib.Path | None = None) -> dict[str, Entry]:
    """``sku``'s stored catalog ({} if the store has none)."""
    return read_store(out_dir).get(validate_sku(sku), {})


def _peers(sku: str, out_dir: pathlib.Path | None = None) -> tuple[dict[str, Entry], ...]:
    """The other SKUs' stored catalogs, in SKU order, each distinct catalog once (:func:`stored_names`'s
    peers)."""
    catalogs, key = read_store(out_dir), validate_sku(sku)
    out: list[dict[str, Entry]] = []
    for other in sorted(catalogs):
        if other != key and catalogs[other] not in out:
            out.append(catalogs[other])
    return tuple(out)


def store_catalog(sku: str, data: dict[str, Entry], out_dir: pathlib.Path | None = None) -> int:
    """Store ``sku``'s catalog and return the pool's size: the store is laid out again from every SKU's
    catalog (:func:`layout`), so a scene no SKU holds any more leaves the pool, and written in one atomic
    replace, so it is never half written."""
    base = out_dir or OUT_DIR
    catalogs = read_store(base)
    catalogs[validate_sku(sku)] = data
    store = layout(catalogs)
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
    set is the bean's null ``c`` (H61A8 below soft 1.04.00 at pact code 1: Support.getSupportScenesOpSet,
    split_rgbiclight/com/govee/dreamcolorlightv1/pact/Support.java:953-956): no filter, which the union is
    when every set is empty or no row offers scenes. A smaller set of the SKU's may hide a scene this keeps:
    test_scenes_load checks each set and names the exceptions. At an empty set the app may play an effect
    before the union's pick (one the union drops): fetch() names those scenes."""
    return frozenset(v for s in scene_sets(sku) for v in s) or None


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


def _label(category: object) -> str:
    """A category's name as a stored name's suffix."""
    return category if isinstance(category, str) and category else "no category"


def _apart(name: str, category: object) -> str:
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
    own = entry.get("scene_name")
    return own if isinstance(own, str) and own else name


def _scene(entry: Entry) -> tuple[object, object]:
    """A stored scene's identity, which its name keeps: code and category."""
    return (entry.get("code"), entry.get("category"))


def _code(entry: Entry) -> int:
    code = entry.get("code")
    return code if isinstance(code, int) else 0


def _order(entry: Entry) -> tuple[int, str, str]:
    """A total order on scenes that the app's list order does not enter: code, category, then the entry."""
    return (_code(entry), _label(entry.get("category")), _dumps(entry))


# How a listed scene matches a stored one of its sceneName, best first: by code, category and blob, by code
# and category, by code (its category renamed), by category and blob (its code changed: H6006's 26 recodes
# keep their blob), by category. The blob passes pair same-named scenes of one code or one category by
# their own blob, not by code or name order.
_MATCHES = (("code", "category", "param"), ("code", "category"), ("code",), ("category", "param"),
            ("category",))
Listed = tuple[str, Entry, frozenset[int]]   # (sceneName, entry, the codes of its lightEffects for the SKU)


def _exact(keys: tuple[str, ...]) -> bool:
    """Whether a match on ``keys`` holds the scene's code and category, its identity (:func:`_scene`)."""
    return "code" in keys and "category" in keys


def _pair(listed: list[Listed], order: list[int], names: list[str | None], pool: dict[str, Entry],
          matches: tuple[tuple[str, ...], ...], answers: Collection[Answer]) -> None:
    """Give each unnamed listed scene the name of an entry of ``pool`` of its sceneName, by the first of
    ``matches`` that finds one, each match tried for every scene (in ``order``) before the next; a name taken
    leaves ``pool``. In a match short of code and category, an entry the library still answers at its own
    sceneName, category and code (``answers``) is that scene's, not this one's, unless its code is one of
    this scene's own lightEffects'."""
    for keys in matches:
        for i in order:
            own, entry, codes = listed[i]
            if names[i] is not None:
                continue
            hits = sorted((n for n, e in pool.items()
                           if scene_name(n, e) == own and all(e.get(k) == entry.get(k) for k in keys)
                           and (_exact(keys) or e.get("code") in codes
                                or (own, e.get("category"), e.get("code")) not in answers)),
                          key=lambda n: (_code(pool[n]), n))
            if hits:
                names[i] = hits[0]
                del pool[hits[0]]


def stored_names(listed: list[Listed], existing: Mapping[str, Entry] | None = None,
                 answers: Collection[Answer] = (), peers: tuple[Mapping[str, Entry], ...] = ()) -> list[str]:
    """The names the ``listed`` scenes (:data:`Listed`) are stored under, one per scene, whatever the order
    they are listed in.

    - Each listed scene takes the name of a stored scene in ``existing`` (the stored catalog) of its sceneName
      (:func:`scene_name`) that no other has taken, by the first match of :data:`_MATCHES` that finds one:
      by code, category and blob, else by code and category, by code, by category and blob, by category.
      Each match is tried for every listed scene before the next, scenes in sceneName and code order, so
      the best match wins and a stored name is never moved to another scene of its name. A match short of
      code and category passes over a stored scene the library still answers at its own
      sceneName, category and code (``answers``, fetch's :func:`_answers`), unless that code is one of the
      listed scene's own lightEffects': that scene is still there, and its name stays its own.
    - A scene that matches none takes the name another SKU's stored catalog (``peers``, in SKU order) gives
      the same scene (its sceneName, code and category; one of its blob first), where neither
      ``existing`` nor another listed scene holds it, so identical responses name alike whatever each SKU's
      history.
    - Any other gets a name that neither ``existing`` nor another listed scene holds: its sceneName, which
      of the new scenes of that sceneName the highest code takes (:func:`_order`; the bundled H61A8 / H6641
      catalog holds that one, the last-one-wins fetch's pick); else "<name> (<its category>)", or
      "<name> (<category>, 2)", 3 and so on, in code order. The sceneNames are given out before any is stored
      apart, so a sceneName that reads "<name> (<category>)" keeps it where it is free."""
    taken = set(existing or {})
    names: list[str | None] = [None] * len(listed)
    order = sorted(range(len(listed)), key=lambda i: (listed[i][0], _order(listed[i][1])))
    _pair(listed, order, names, dict(existing or {}), _MATCHES, answers)
    pool: dict[str, Entry] = {}
    for peer in peers:
        for n, e in peer.items():
            if n not in taken:
                pool.setdefault(n, e)
    _pair(listed, order, names, pool, tuple(k for k in _MATCHES if _exact(k)), ())
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


def fetch(sku: str, app_version: str = DEFAULT_APP_VERSION, *, seen: set[Answer] | None = None,
          existing: Mapping[str, Entry] | None = None,
          peers: tuple[Mapping[str, Entry], ...] = ()) -> dict[str, Entry]:
    """``sku``'s scenes from the public scene library: each listed scene's lightEffect for the SKU
    (:func:`listed_effect`, over :func:`op_set` and the categories in force), with the values the app reads
    for it (:func:`for_sku`). A scene whose effect depends on the firmware's version rules is left out, and
    named on stdout. Where one of the SKU's scene sets is empty (a null op set: SceneDataVM.b2 runs no
    checkSupportNew, only Companion.k, SceneDataVM.java:1502-1515), a scene the app plays another effect for
    there (the first in force, which the union may drop) keeps the union's, and is named on stdout too. For a
    ``legacy_scene`` SKU the sceneType-5 scenes the library does not upload are left out
    (:func:`uploads_type5`) and named, before any scene is named. The rest are named over ``existing``, the
    stored catalog, and ``peers``, the other SKUs' (:func:`stored_names`); stdout names each new one stored
    apart and each stored name whose scene has a new code or category. ``seen``, when given, gets
    ``(sceneName, category, code)`` of every scene the library answered with that is listed or left out for
    good (:func:`_answers`, for :func:`_merge`): not one left out only for its time (its category's or its
    effects') or the firmware's version rules (:func:`_in_time`), which _merge keeps under its stored name,
    as one the library dropped."""
    key = validate_sku(sku)
    ops = op_set(key)
    null_set = any(not s for s in scene_sets(key))
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
    held_back: set[Answer] = set()   # left out for their time or version rules only: _merge keeps them
    named: list[Listed] = []
    gated: list[str] = []
    unfiltered: list[str] = []
    type5: list[str] = []
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
                if _in_time(key, effects, ops, now_ms):
                    held_back |= answered
                continue
            eff = for_sku(key, listed)
            if eff["code"] is None:
                continue
            if null_set and listed_effect(key, effects, None, now_ms)[0] is not listed:
                unfiltered.append(str(scene.get("sceneName")))
            param = eff["param"] or None
            if isinstance(param, str):
                param = _NOT_BASE64.sub("", param) or None
            entry: Entry = {
                "code": int(eff["code"]),
                "param": param,
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
            if versions is not None and uploads_type5(entry, versions):
                type5.append(str(scene["sceneName"]))
                continue
            named.append((scene["sceneName"], entry, frozenset(code for _n, _c, code in answered)))
    answers = _answers(key, payload)
    if seen is not None:
        seen.update(answers - held_back)
    stored = existing or {}
    names = stored_names(named, stored, answers, peers)
    scenes: dict[str, Entry] = {}
    apart: list[str] = []
    moved: list[str] = []
    for (own, entry, _codes), name in zip(named, names, strict=True):
        if name != own:
            entry["scene_name"] = own
        scenes[name] = entry
    for (own, entry, _codes), name in zip(named, names, strict=True):
        was = stored.get(name)
        if was is None and name != own:
            plain = scenes.get(own) or stored.get(own) or {}
            apart.append(_described(name, entry, f'"{own}" is {_of(plain)}'))
        elif was is not None and _scene(was) != _scene(entry):
            moved.append(_described(name, entry, f"was {_of(was)}"))
    if type5:
        print(f"{key}: left out {len(type5)} sceneType-5 scenes the app uploads and the library does not "
              f"(legacy_scene): {', '.join(type5)}")
    if apart:
        print(f"{key}: {len(apart)} new scenes share a name with another, stored apart: {', '.join(apart)}")
    if moved:
        print(f"{key}: {len(moved)} stored names keep their scene at a new code or category: "
              f"{', '.join(moved)}")
    if gated:
        print(f"{key}: left out {len(gated)} scenes whose effect the firmware's version rules pick: "
              f"{', '.join(gated)}")
    if unfiltered:
        print(f"{key}: at its empty scene set (no op set) the app plays another lightEffect for "
              f"{len(unfiltered)} scenes: {', '.join(unfiltered)}")
    return scenes


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
                        validate_entry(f"param_id {pid}", {"code": 0, "param": real[pid]})
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
    fetch: it has an integer code and is NOT flagged as an unresolved
    placeholder. The ``placeholder`` flag is the source of truth — --resolve
    pops it once the real blob is baked in. A ``param``-less (bare-activate) scene
    is valid too."""
    return isinstance(entry.get("code"), int) and not entry.get("placeholder")


def _resolved(existing: Mapping[str, Entry], name: str, fr: Entry) -> Entry | None:
    """The existing entry whose blob, resolved by --resolve, the fresh entry ``fr`` (stored as ``name``)
    keeps: ``fr`` is the public placeholder (a big-effect scene without its blob) of a resolved entry with its
    param_id (the key effect-strs resolves by), the one case where the existing blob beats the fresh fetch.
    That is the entry stored under ``name`` (stored_names matched it to ``fr``, through a renumber or a
    category rename too), else one of the same code and category under another name."""
    if not fr.get("placeholder"):
        return None
    own = existing.get(name)
    if own is not None and _valid_resolved(own) and fr.get("param_id") is not None \
            and own.get("param_id") == fr.get("param_id"):
        return own
    for n in sorted(existing):
        ex = existing[n]
        if (_valid_resolved(ex) and (ex.get("code"), ex.get("category"), ex.get("param_id"))
                == (fr.get("code"), fr.get("category"), fr.get("param_id"))):
            return ex
    return None


def _merge(existing: dict[str, Entry], fresh: dict[str, Entry],
           public: Collection[Answer] | None = None) -> dict[str, Entry]:
    """Combine a fresh public fetch, named over ``existing`` (fetch's ``existing``, so a name both hold is one
    scene), with the existing catalog: take the fresh data, except that a fresh placeholder never replaces a
    real effect blob already resolved via --resolve (:func:`_resolved`: the fresh entry, its code, category
    and upload metadata replaced, not merged, keeps that blob); and keep each stored scene the fetch no longer
    lists, under its name. This makes re-running idempotent and non-destructive once placeholders have been
    resolved. ``public`` (fetch's ``seen``) is every scene the library still answers with and the SKU's scene
    list lists or leaves out for good: a stored scene not listed that it holds, which the app's scene list
    refuses for the SKU (:func:`listed_effect`, or a sceneType 5 the library does not upload), is not kept
    (:func:`_answered`). One left out only for its time or version rules is not in it, so it stays under its
    name."""
    merged: dict[str, Entry] = {}
    for name, fr in fresh.items():
        ex = _resolved(existing, name, fr)
        merged[name] = fr if ex is None else {**{k: v for k, v in fr.items() if k != "placeholder"},
                                              "param": ex.get("param")}
    for name, ex in existing.items():
        if name not in fresh and (public is None or not _answered(name, ex, public)):
            merged[name] = ex  # scene no longer public but we have it
    return merged


def _answered(name: str, entry: Entry, public: Collection[Answer]) -> bool:
    """Whether the scene stored as ``name`` is one the library answers with: ``public`` (fetch's ``seen``)
    holds its sceneName (:func:`scene_name`), category and code."""
    return (scene_name(name, entry), entry.get("category"), entry.get("code")) in public


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
    SKU, run every bundled scene through the device's real
    ``Device.scene_upload_frames`` routing and put it in exactly one bucket
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

    from govee_ble_local.exceptions import GoveeBleInvalidValue, GoveeBleNotSupported
    from govee_ble_local.registry import create_device
    from govee_ble_local.scenes import load_scenes

    print(
        f"{'SKU':7s} {'total':>5s} {'upload':>16s} {'activate':>8s} {'BLOCKED':>7s} {'placeh':>6s}  versions"
    )
    for sku in skus:
        cat = load_scenes(sku)
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
        upload = activate = blocked = placeholder = 0
        dialects: set[str] = set()
        for scene in cat.values():
            if scene.placeholder:
                placeholder += 1
                continue
            try:
                frames = dev.scene_upload_frames(scene)
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
        vers = sorted(dev.profile.scene_versions)
        u = f"{upload}{sorted(dialects) if dialects else ''}"
        print(f"{sku:7s} {len(cat):5d} {u:>16s} {activate:8d} {blocked:7d} {placeholder:6d}  {vers}")


def main(skus: list[str], app_version: str = DEFAULT_APP_VERSION) -> int:
    """Fetch + merge each SKU's catalog. Returns the number of SKUs that failed."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0
    for sku in skus:
        if sku in APP_DEFINED_SCENE_SKUS:
            print(f"{sku}: skipped (scene list is compiled into the app, not the scene library)")
            continue
        public: set[Answer] = set()
        try:
            existing = read_catalog(sku)
            fresh = fetch(sku, app_version, seen=public, existing=existing, peers=_peers(sku))
        except Exception as err:
            print(f"{sku}: FETCH FAILED: {err}", file=sys.stderr)
            failures += 1
            continue
        scenes = _merge(existing, fresh, public)
        versions = legacy_versions(sku)
        held = sorted(n for n, v in scenes.items() if versions is not None and uploads_type5(v, versions))
        if held:   # fetch() leaves out the listed ones: these are stored scenes kept
            scenes = {n: v for n, v in scenes.items() if n not in held}
            print(f"{sku}: left out {len(held)} sceneType-5 scenes the app uploads and the library does not "
                  f"(legacy_scene): {', '.join(held)}")
        if not scenes:
            print(f"{sku}: no scenes in the library; nothing stored")
            continue
        gone = [_described(n, existing[n]) for n in sorted(existing) if n not in scenes]
        if gone:
            print(f"{sku}: removed {len(gone)} stored names whose scene the library still answers and the "
                  f"SKU's scene list refuses, or the library does not upload: {', '.join(gone)}")
        try:
            pooled = store_catalog(sku, scenes)
        except (OSError, ValueError) as err:
            print(f"{sku}: WRITE FAILED: {err}", file=sys.stderr)
            failures += 1
            continue
        # preserved = taken from the existing catalog: resolved blobs kept over a public stub,
        # plus scenes that are no longer in the public library
        kept = sum(1 for n in scenes if n not in fresh or _resolved(existing, n, fresh[n]) is not None)
        placeholders = sorted(n for n, v in scenes.items() if v.get("placeholder"))
        note = f"; {len(placeholders)} placeholders (run --resolve)" if placeholders else ""
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
