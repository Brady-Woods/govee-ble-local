#!/usr/bin/env python3
"""Fetch Govee's built-in scene library per SKU and store it in-repo.

Uses the public (no-auth) app scene-library endpoint:
    GET https://app2.govee.com/appsku/v1/light-effect-libraries?sku=<SKU>
(only an AppVersion header). Each scene yields a BLE `sceneCode` and a base64
`scenceParam` (the effect blob uploaded via the a3-chunk burst). Writes one
JSON per SKU to src/govee_ble_local/scenes/<SKU>.json so the catalog ships with
the library and no runtime cloud call is needed.

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
import urllib.parse
import urllib.request
from typing import Any

LIBRARY_URL = "https://app2.govee.com/appsku/v1/light-effect-libraries"
# Self-contained on purpose (the cloud module carries its own copy). Override with
# --app-version or $GOVEE_APP_VERSION if Govee starts rejecting this value.
DEFAULT_APP_VERSION = "7.5.20"
OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "govee_ble_local" / "scenes"
# Upper bound on the library response body; real catalogs are well under 1 MiB.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_SKU_RE = re.compile(r"[A-Za-z0-9]{3,8}")
Entry = dict[str, Any]  # one scene's catalog entry (JSON object)


def validate_sku(sku: str) -> str:
    """Return the upper-cased SKU, or raise ValueError if it is not 3-8 alphanumerics."""
    if not _SKU_RE.fullmatch(sku):
        raise ValueError(f"invalid SKU {sku!r}: expected 3-8 alphanumeric characters")
    return sku.upper()


def catalog_path(sku: str, out_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Output path for a SKU's catalog; guaranteed to resolve inside ``out_dir``."""
    base = (out_dir or OUT_DIR).resolve()
    path = (base / f"{validate_sku(sku)}.json").resolve()
    if path.parent != base:
        raise ValueError(f"refusing to write outside {base}: {path}")
    return path


def validate_entry(name: object, entry: dict[str, object]) -> None:
    """Check one fetched scene against what ``scenes.load_scenes`` requires; raises ValueError."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"scene name {name!r} is not a non-empty string")
    if not isinstance(entry.get("code"), int) or isinstance(entry.get("code"), bool):
        raise ValueError(f"{name}: missing/non-integer 'code'")
    for key, typ in (("param", str), ("category", str), ("param_id", int),
                     ("scene_type", int), ("cmd_version", int)):
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
    """Write a catalog atomically: temp file in the same directory, then ``os.replace``."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=1, sort_keys=True))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _is_placeholder(param_b64: str | None) -> bool:
    """A "placeholder" is a big/DIY-backed scene the public library ships as a
    STUB blob (0xff at byte[3]); its real effect data must be fetched from the
    authenticated effect-strs endpoint (see get_scene_effect_strs) and uploaded
    via the a3-chunk burst.

    A scene with NO public param is NOT a placeholder — it is a device-built-in
    scene activated by bare code (33 05 04 <lo> <hi>), with no upload and no
    server blob (the effect-strs endpoint returns empty for these). Treating
    those as placeholders is wrong: they resolve to nothing and should just
    bare-activate."""
    if not param_b64:
        return False
    try:
        raw = base64.b64decode(param_b64)
    except (binascii.Error, ValueError):
        return False
    return len(raw) >= 4 and raw[3] == 0xFF


def fetch(sku: str, app_version: str = DEFAULT_APP_VERSION) -> dict[str, Entry]:
    url = f"{LIBRARY_URL}?sku={urllib.parse.quote(validate_sku(sku), safe='')}"
    req = urllib.request.Request(url, headers={"AppVersion": app_version})
    # Fixed https LIBRARY_URL; the only variable part (sku) is validated and URL-quoted above.
    with urllib.request.urlopen(req, timeout=20) as resp:  # nosec B310
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError(f"library response for {sku} exceeds {MAX_RESPONSE_BYTES} bytes; refusing")
    payload = json.loads(raw)
    scenes: dict[str, Entry] = {}
    for category in payload.get("data", {}).get("categories", []):
        for scene in category.get("scenes", []):
            effects = scene.get("lightEffects") or []
            if not effects:
                continue
            eff = effects[0]
            code = eff.get("sceneCode")
            if code is None:
                continue
            param = eff.get("scenceParam") or None
            entry: Entry = {
                "code": int(code),
                "param": param,
                "category": category.get("categoryName"),
            }
            pid = eff.get("scenceParamId")
            if pid is not None:
                entry["param_id"] = int(pid)
            # Path-B upload drivers (see spec/docs/SCENE_UPLOAD_ENCODING.md): scene_type +
            # cmd_version pick the a3 commByte; bigEffectStr flags fetch-required.
            if eff.get("sceneType") is not None:
                entry["scene_type"] = int(eff["sceneType"])
            if eff.get("cmdVersion") is not None:
                entry["cmd_version"] = int(eff["cmdVersion"])
            if eff.get("bigEffectStr"):
                entry["big_effect"] = True
            if _is_placeholder(param):
                # real blob must come from the authenticated effect-strs endpoint
                entry["placeholder"] = True
            validate_entry(scene.get("sceneName"), entry)
            scenes[scene["sceneName"]] = entry
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
                path = catalog_path(sku)
                if not path.exists():
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
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
                write_catalog(path, data)
                print(f"{sku}: resolved {fixed}/{len(ids)} placeholder scenes")
        finally:
            await acct.close()

    asyncio.run(run())


def _valid_resolved(entry: dict[str, object]) -> bool:
    """True if an existing catalog entry is already a complete, usable
    definition we should PRESERVE rather than clobber with a fresh public
    fetch: it has an integer code and is NOT flagged as an unresolved
    placeholder. The ``placeholder`` flag is the source of truth — --resolve
    pops it once the real blob is baked in. We deliberately do NOT re-run the
    0xff-stub heuristic on the param here: a resolved effect blob can legitimately
    carry 0xff at byte[3], and re-checking would wrongly discard it. A
    ``param``-less (bare-activate) scene is valid too."""
    return isinstance(entry.get("code"), int) and not entry.get("placeholder")


# Public metadata refreshed onto a preserved (resolved) entry. fetch() only sets scene_type /
# cmd_version / big_effect when the library has them, so they are REPLACED (absent -> dropped),
# never merged: a stale upload-routing field would pick the wrong upload path.
_REFRESH = ("scene_type", "cmd_version", "big_effect", "category")


def _keeps_resolved_blob(ex: Entry | None, fr: Entry) -> bool:
    """True if the fresh entry is the public 0xff STUB of a blob the existing entry already
    resolved (same param_id): the one case where the existing blob beats the fresh fetch."""
    return (ex is not None and _valid_resolved(ex) and bool(fr.get("placeholder"))
            and ex.get("param_id") == fr.get("param_id"))


def _merge(existing: dict[str, Entry], fresh: dict[str, Entry]) -> dict[str, Entry]:
    """Combine a fresh public fetch with an existing catalog: take the fresh data, except
    that a fresh 0xff stub never replaces a real effect blob already resolved via --resolve
    (that entry keeps its blob, takes the fresh code, and has its public metadata replaced
    from the fresh entry); and keep entries that dropped out of the public library. This
    makes re-running idempotent and non-destructive once placeholders have been resolved."""
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
        merged.setdefault(name, ex)  # scene no longer public but we have it
    return merged


# SKUs whose scene list is compiled into the app, not served by the scene library: their
# bundled catalog is hand-derived from the APK and must not be overwritten by a fetch.
# H6188: rgblight Scenes4H6188 (fixed codes, activate-only; DelegateSceneV1 makes no cloud call).
APP_DEFINED_SCENE_SKUS = frozenset({"H6188"})


def scene_capable_skus() -> list[str]:
    """All supported SKUs whose device declares Capability.SCENES (so new
    families are covered automatically as they're added to the registry), except the
    APP_DEFINED_SCENE_SKUS whose catalogs are not from the scene library."""
    from govee_ble_local import profile_for, supported_skus
    from govee_ble_local.models import Capability

    out: list[str] = []
    for sku in supported_skus():
        p = profile_for(sku)
        if p is not None and Capability.SCENES in p.capabilities and sku not in APP_DEFINED_SCENE_SKUS:
            out.append(sku)
    return out


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
      placeholder = unresolved 0xFF stub (needs `--resolve` with account creds)
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
        dev = create_device(BLEDevice("00:00:00:00:00:00", sku, **ble_kwargs), sku)
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
        try:
            path = catalog_path(sku)
            fresh = fetch(sku, app_version)
            existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if not isinstance(existing, dict) or not all(isinstance(v, dict) for v in existing.values()):
                raise ValueError(f"existing {path.name} is not a JSON object of scene objects")
        except Exception as err:
            print(f"{sku}: FETCH FAILED: {err}", file=sys.stderr)
            failures += 1
            continue
        scenes = _merge(existing, fresh)
        try:
            write_catalog(path, scenes)
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
            f"{sku}: {len(scenes)} scenes -> scenes/{path.name} "
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
