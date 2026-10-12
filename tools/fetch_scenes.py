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
import json
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request

LIBRARY_URL = "https://app2.govee.com/appsku/v1/light-effect-libraries"
# Self-contained on purpose (the cloud module carries its own copy). Override with
# --app-version or $GOVEE_APP_VERSION if Govee starts rejecting this value.
DEFAULT_APP_VERSION = "7.5.20"
OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "govee_ble_local" / "scenes"
_SKU_RE = re.compile(r"[A-Za-z0-9]{3,8}")


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
    import base64
    try:
        raw = base64.b64decode(param_b64)
    except Exception:  # noqa: BLE001
        return False
    return len(raw) >= 4 and raw[3] == 0xFF


def fetch(sku: str, app_version: str = DEFAULT_APP_VERSION) -> dict[str, dict[str, object]]:
    url = f"{LIBRARY_URL}?sku={urllib.parse.quote(validate_sku(sku), safe='')}"
    req = urllib.request.Request(url, headers={"AppVersion": app_version})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        payload = json.load(resp)
    scenes: dict[str, dict[str, object]] = {}
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
            entry: dict[str, object] = {
                "code": int(code),
                "param": param,
                "category": category.get("categoryName"),
            }
            pid = eff.get("scenceParamId")
            if pid is not None:
                entry["param_id"] = int(pid)
            # Path-B upload drivers (see SCENE_UPLOAD_REVIEW.md): scene_type +
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
                data = json.loads(path.read_text())
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
                path.write_text(json.dumps(data, indent=1, sort_keys=True))
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


def _merge(existing: dict[str, dict], fresh: dict[str, dict]) -> dict[str, dict]:
    """Combine a fresh public fetch with an existing catalog: take fresh data,
    but keep any existing entry that's already valid/resolved (never replace a
    real effect blob with a fresh 0xff stub), and keep entries that dropped out
    of the public library. This makes re-running idempotent and non-destructive
    once placeholders have been resolved via --resolve."""
    # Public metadata that is safe to refresh on a preserved (resolved) entry — it does
    # NOT touch the param/placeholder resolution, only the upload-driver fields.
    _refresh = ("scene_type", "cmd_version", "big_effect", "category")
    merged: dict[str, dict] = {}
    for name, fr in fresh.items():
        ex = existing.get(name)
        if ex is not None and _valid_resolved(ex):
            keep = dict(ex)  # preserve resolved param/placeholder state...
            for k in _refresh:  # ...but pull in fresh public metadata (e.g. scene_type)
                if k in fr:
                    keep[k] = fr[k]
            merged[name] = keep
        else:
            merged[name] = fr
    for name, ex in existing.items():
        merged.setdefault(name, ex)  # scene no longer public but we have it
    return merged


def scene_capable_skus() -> list[str]:
    """All supported SKUs whose device declares Capability.SCENES (so new
    families are covered automatically as they're added to the registry)."""
    from govee_ble_local.models import Capability
    from govee_ble_local.registry import device_profile_for, supported_skus

    out: list[str] = []
    for sku in supported_skus():
        p = device_profile_for(sku)
        if p is not None and Capability.SCENES in p.capabilities:
            out.append(sku)
    return out


def audit() -> None:
    """Report per-SKU scene-upload READINESS (read-only, no cloud). For each
    scene-capable SKU, run every bundled scene through the device's real
    ``Device.scene_upload_frames`` routing and bucket it:
      upload      = a dialect burst is produced (0xA3 / 0xA4-MTU)
      activate    = correctly bare-activated (no param, or static sceneType 0)
      BLOCKED     = has a real effect blob + non-static sceneType but NO upload path
                    (would silently activate-only — the gap to close)
      placeholder = unresolved 0xFF stub (needs `--resolve` with account creds)
    """
    from bleak.backends.device import BLEDevice

    from govee_ble_local.registry import create_device
    from govee_ble_local.scenes import load_scenes

    print(f"{'SKU':7s} {'total':>5s} {'upload':>16s} {'activate':>8s} {'BLOCKED':>7s} {'placeh':>6s}  versions")
    for sku in scene_capable_skus():
        cat = load_scenes(sku)
        if not cat:
            print(f"{sku:7s} (no catalog)"); continue
        dev = create_device(BLEDevice(f"00:00:00:00:00:{0:02x}", sku, details={}), sku)
        upload = activate = blocked = placeholder = 0
        dialects: set[str] = set()
        for scene in cat.values():
            if scene.placeholder:
                placeholder += 1
            try:
                frames = dev.scene_upload_frames(scene)
            except Exception:  # noqa: BLE001
                frames = None
            if frames:
                upload += 1
                dialects.add("0xA4" if any(f[0] == 0xA4 for f in frames) else "0xA3")
            elif scene.param and scene.scene_type not in (0, None):
                blocked += 1
            else:
                activate += 1
        vers = sorted(getattr(dev, "_scene_versions", frozenset()))
        u = f"{upload}{sorted(dialects) if dialects else ''}"
        print(f"{sku:7s} {len(cat):5d} {u:>16s} {activate:8d} {blocked:7d} {placeholder:6d}  {vers}")


def main(skus: list[str], app_version: str = DEFAULT_APP_VERSION) -> int:
    """Fetch + merge each SKU's catalog. Returns the number of SKUs that failed."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0
    for sku in skus:
        try:
            path = catalog_path(sku)
            fresh = fetch(sku, app_version)
            existing = json.loads(path.read_text()) if path.exists() else {}
            if not isinstance(existing, dict):
                raise ValueError(f"existing {path.name} is not a JSON object")
        except Exception as err:  # noqa: BLE001
            print(f"{sku}: FETCH FAILED: {err}", file=sys.stderr)
            failures += 1
            continue
        scenes = _merge(existing, fresh)
        path.write_text(json.dumps(scenes, indent=1, sort_keys=True))
        kept = sum(1 for n in scenes if n in existing and _valid_resolved(existing[n]))
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
    ap.add_argument("skus", nargs="*", metavar="SKU", help="SKUs to fetch (default: all scene-capable)")
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
        audit()
    elif _a.resolve:
        _email = os.environ.get("GOVEE_EMAIL", "")
        _password = os.environ.get("GOVEE_PASSWORD", "")
        if not _email or not _password:
            sys.exit("--resolve needs GOVEE_EMAIL and GOVEE_PASSWORD env vars")
        resolve_placeholders(_email, _password, _skus)
    else:
        sys.exit(1 if main(_skus, _a.app_version) else 0)
