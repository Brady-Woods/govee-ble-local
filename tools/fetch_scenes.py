#!/usr/bin/env python3
"""Fetch Govee's built-in scene library per SKU and store it in-repo.

Uses the public (no-auth) app scene-library endpoint:
    GET https://app2.govee.com/appsku/v1/light-effect-libraries?sku=<SKU>
(only an AppVersion header). Each scene yields a BLE `sceneCode` and a base64
`scenceParam` (the effect blob uploaded via the a3-chunk burst), taken from the lightEffect
the app's scene list keeps for the SKU (devices.yaml scenes.list_filter). Writes each SKU's
catalog into src/govee_ble_local/scenes/ (so it ships with the library and no runtime
cloud call is needed), stored once by content: `<set>.json` is named by the first 16
hex digits of its SHA-256 and `index.json` maps each SKU to its set, so SKUs with the
same scenes share one file.

    python3 tools/fetch_scenes.py            # all supported SKUs
    python3 tools/fetch_scenes.py H60A6 H61A8
"""
from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import hashlib
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
from collections.abc import Collection
from typing import Any

LIBRARY_URL = "https://app2.govee.com/appsku/v1/light-effect-libraries"
# The traced app's version (decomp-7.7.02). Self-contained on purpose (the cloud module carries its own
# copy). The scene library answered 7.5.20 byte for byte as 7.7.02 for every bundled SKU (2026-10-19);
# the catalogue endpoint did not (15 more SKUs at 7.7.02), so tools/fetch_catalog.py asks as its snapshot's
# app_version records instead. Override with --app-version or $GOVEE_APP_VERSION if Govee starts rejecting
# this value.
DEFAULT_APP_VERSION = "7.7.02"
OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "govee_ble_local" / "scenes"
# Upper bound on the library response body; real catalogs are well under 1 MiB.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_SKU_RE = re.compile(r"[A-Za-z0-9]{3,8}")
_SET_RE = re.compile(r"[0-9a-f]{16}")
# The app decodes a scene blob with android.util.Base64.decode(str, NO_WRAP) (Encode.decryByBase64,
# base-classes10/com/govee/base2home/util/Encode.java:29-31), which skips every character outside the
# alphabet and '='. Some library blobs carry such characters (H6087's JSON-escaped "\/"), so fetch()
# drops them too and stores the blob the app decodes.
_NOT_BASE64 = re.compile(r"[^A-Za-z0-9+/=]")
INDEX_NAME = "index.json"
Entry = dict[str, Any]  # one scene's catalog entry (JSON object)


def validate_sku(sku: str) -> str:
    """Return the upper-cased SKU, or raise ValueError if it is not 3-8 alphanumerics."""
    if not _SKU_RE.fullmatch(sku):
        raise ValueError(f"invalid SKU {sku!r}: expected 3-8 alphanumeric characters")
    return sku.upper()


def validate_entry(name: object, entry: dict[str, object]) -> None:
    """Check one fetched scene against what ``scenes.load_scenes`` requires; raises ValueError."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"scene name {name!r} is not a non-empty string")
    if not isinstance(entry.get("code"), int) or isinstance(entry.get("code"), bool):
        raise ValueError(f"{name}: missing/non-integer 'code'")
    for key, typ in (("param", str), ("category", str), ("param_id", int),
                     ("scene_type", int), ("cmd_version", int), ("rules", bool)):
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
    """Write a scene set or the index atomically: temp file in the same directory, then ``os.replace``."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:  # the bytes set_name() hashes
            fh.write(_dumps(data))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _dumps(data: object) -> str:
    return json.dumps(data, indent=1, sort_keys=True)


def set_name(text: str) -> str:
    """A scene set's name: the first 16 hex digits of the SHA-256 of its file's text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def set_path(name: str, out_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Path of the scene set ``name``; anything but a set name is refused, so it stays in ``out_dir``."""
    if not _SET_RE.fullmatch(name):
        raise ValueError(f"invalid scene set name {name!r}: expected 16 lower-case hex digits")
    return (out_dir or OUT_DIR).resolve() / f"{name}.json"


def read_index(out_dir: pathlib.Path | None = None) -> dict[str, str]:
    """The store's SKU -> scene set index ({} if there is none yet)."""
    path = (out_dir or OUT_DIR) / INDEX_NAME
    if not path.exists():
        return {}
    index = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(index, dict) or not all(isinstance(v, str) and _SET_RE.fullmatch(v)
                                              for v in index.values()):
        raise ValueError(f"{INDEX_NAME} is not a JSON object of scene set names")
    return index


def read_catalog(sku: str, out_dir: pathlib.Path | None = None) -> dict[str, Entry]:
    """``sku``'s stored catalog ({} if the index has none)."""
    name = read_index(out_dir).get(validate_sku(sku))
    if name is None:
        return {}
    data = json.loads(set_path(name, out_dir).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        raise ValueError(f"scene set {name} ({sku}) is not a JSON object of scene objects")
    return data


def store_catalog(sku: str, data: dict[str, Entry], out_dir: pathlib.Path | None = None) -> str:
    """Store ``sku``'s catalog once by content and return its set name: write the set if it is new,
    point the index at it, then delete the sets no SKU points to. The set is written before the
    index, so the index never names a missing set."""
    base = out_dir or OUT_DIR
    name = set_name(_dumps(data))
    path = set_path(name, base)
    if not path.exists():
        write_catalog(path, data)
    index = read_index(base)
    index[validate_sku(sku)] = name
    write_catalog(base / INDEX_NAME, index)
    for stale in base.glob("*.json"):
        if _SET_RE.fullmatch(stale.stem) and stale.stem not in index.values():
            stale.unlink()
    return name


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


def op_set(sku: str) -> frozenset[int] | None:
    """The scene op set fetch() filters ``sku``'s scenes by, or None for no filter: the ScenesConfigBean's
    ``c`` (opScenesSet), which SceneDataVM takes as its op set (SceneDataVM.java:1632, ScenesConfigBean.m) and
    hands to CategoryV1.checkSupportNew, run only with one (:1502-1514). The library takes it from the SKU's
    rows: every scene row's ``scene_versions`` is its bean's ``c`` (devices.schema.json), so this is the
    union of the non-empty sets (row, ``by_pact``, ``by_firmware``) of the SKU's routes that offer scenes,
    the newest firmware's as the library assumes before the device-info read. An empty set is the bean's
    null ``c`` (H61A8 below soft 1.04.00 at pact code 1): no filter, which the union is when every set is
    empty or no row offers scenes. A smaller set of the SKU's may hide a scene this keeps: test_scenes_load
    checks each set and names the exceptions."""
    routes = [f for f in scene_rows().get(validate_sku(sku), []) if "scenes" in f["capabilities"]]
    sets = [s for f in routes for s in (f["scene_versions"], *(v for _p, v in f["scene_versions_by_pact"]),
                                        *(v for _s, _h, v in f["scene_versions_by_firmware"]))]
    return frozenset(v for s in sets for v in s) or None


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


def fetch(sku: str, app_version: str = DEFAULT_APP_VERSION, *, seen: set[str] | None = None
          ) -> dict[str, Entry]:
    """``sku``'s scenes from the public scene library: each listed scene's lightEffect for the SKU
    (:func:`listed_effect`, over :func:`op_set` and the categories in force), with the values the app reads
    for it (:func:`for_sku`). A scene whose effect depends on the firmware's version rules is left out, and
    named on stdout. ``seen``, when given, gets the name of every scene the library answered with, listed or
    not (:func:`_merge`)."""
    key = validate_sku(sku)
    ops = op_set(key)
    now_ms = time.time() * 1000
    url = f"{LIBRARY_URL}?sku={urllib.parse.quote(key, safe='')}"
    req = urllib.request.Request(url, headers={"AppVersion": app_version})
    # Fixed https LIBRARY_URL; the only variable part (sku) is validated and URL-quoted above.
    with urllib.request.urlopen(req, timeout=20) as resp:  # nosec B310
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError(f"library response for {sku} exceeds {MAX_RESPONSE_BYTES} bytes; refusing")
    payload = json.loads(raw)
    scenes: dict[str, Entry] = {}
    gated: list[str] = []
    for category in payload.get("data", {}).get("categories", []):
        if seen is not None:
            seen.update(str(s.get("sceneName")) for s in category.get("scenes", []))
        if not effective(category, now_ms):   # SceneDataVM.b2 skips a category out of its time (:1454-1461)
            continue
        for scene in category.get("scenes", []):
            listed, held = listed_effect(key, scene.get("lightEffects") or [], ops, now_ms)
            if held:
                gated.append(str(scene.get("sceneName")))
            if listed is None:
                continue
            eff = for_sku(key, listed)
            if eff["code"] is None:
                continue
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
            scenes[scene["sceneName"]] = entry
    if gated:
        print(f"{key}: left out {len(gated)} scenes whose effect the firmware's version rules pick: "
              f"{', '.join(gated)}")
    return scenes


def resolve_placeholders(email: str, password: str, skus: list[str]) -> None:
    """Log into the Govee account and bake real effect blobs into the catalogs
    for placeholder scenes (via the authenticated effect-strs endpoint), so the
    shipped JSON is complete and no runtime cloud call is needed.

    Credentials: passed here only to obtain a token; nothing is persisted by
    this tool beyond the resulting scene blobs. Prefer env vars GOVEE_EMAIL /
    GOVEE_PASSWORD over CLI args.
    """
    import asyncio

    from govee_ble_local.cloud import GoveeCloudAccount

    async def run() -> None:
        acct = GoveeCloudAccount(email, password)
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


# Public metadata refreshed onto a preserved (resolved) entry. fetch() only sets scene_type /
# cmd_version / big_effect when the library has them, so they are REPLACED (absent -> dropped),
# never merged: a stale upload-routing field would pick the wrong upload path.
_REFRESH = ("scene_type", "cmd_version", "big_effect", "rules", "category")


def _keeps_resolved_blob(ex: Entry | None, fr: Entry) -> bool:
    """True if the fresh entry is the public placeholder (a big-effect scene without its blob) of a
    blob the existing entry already resolved (same param_id): the one case where the existing blob
    beats the fresh fetch."""
    return (ex is not None and _valid_resolved(ex) and bool(fr.get("placeholder"))
            and ex.get("param_id") == fr.get("param_id"))


def _merge(existing: dict[str, Entry], fresh: dict[str, Entry],
           public: Collection[str] | None = None) -> dict[str, Entry]:
    """Combine a fresh public fetch with an existing catalog: take the fresh data, except
    that a fresh placeholder never replaces a real effect blob already resolved via --resolve
    (that entry keeps its blob, takes the fresh code, and has its public metadata replaced
    from the fresh entry); and keep entries that dropped out of the public library. This
    makes re-running idempotent and non-destructive once placeholders have been resolved.
    ``public`` (fetch's ``seen``) is every scene the library still answers with: one of them that
    the app's scene list leaves out for the SKU (:func:`listed_effect`) is not kept."""
    merged: dict[str, Entry] = {}
    for name, fr in fresh.items():
        ex = existing.get(name)
        if ex is not None and _keeps_resolved_blob(ex, fr):
            keep = {k: v for k, v in ex.items() if k not in _REFRESH}
            keep.update({k: fr[k] for k in _REFRESH if k in fr})
            keep["code"] = fr["code"]
            merged[name] = keep
        else:
            merged[name] = fr
    for name, ex in existing.items():
        if public is None or name not in public:
            merged.setdefault(name, ex)  # scene no longer public but we have it
    return merged


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
        public: set[str] = set()
        try:
            fresh = fetch(sku, app_version, seen=public)
            existing = read_catalog(sku)
        except Exception as err:
            print(f"{sku}: FETCH FAILED: {err}", file=sys.stderr)
            failures += 1
            continue
        scenes = _merge(existing, fresh, public)
        versions = legacy_versions(sku)
        held = sorted(n for n, v in scenes.items() if versions is not None and uploads_type5(v, versions))
        if held:
            scenes = {n: v for n, v in scenes.items() if n not in held}
            print(f"{sku}: left out {len(held)} sceneType-5 scenes the app uploads and the library does not "
                  f"(legacy_scene): {', '.join(held)}")
        if not scenes:
            print(f"{sku}: no scenes in the library; nothing stored")
            continue
        try:
            name = store_catalog(sku, scenes)
        except OSError as err:
            print(f"{sku}: WRITE FAILED: {err}", file=sys.stderr)
            failures += 1
            continue
        # preserved = taken from the existing catalog: resolved blobs kept over a public stub,
        # plus scenes that are no longer in the public library
        kept = sum(1 for n in scenes if n not in fresh or _keeps_resolved_blob(existing.get(n), fresh[n]))
        placeholders = sorted(n for n, v in scenes.items() if v.get("placeholder"))
        note = f"; {len(placeholders)} placeholders (run --resolve)" if placeholders else ""
        print(
            f"{sku}: {len(scenes)} scenes -> scenes/{name}.json "
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
                    help=f"AppVersion header (env GOVEE_APP_VERSION; default {DEFAULT_APP_VERSION})")
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
        resolve_placeholders(_email, _password, _skus)
    else:
        sys.exit(1 if main(_skus, _a.app_version) else 0)
