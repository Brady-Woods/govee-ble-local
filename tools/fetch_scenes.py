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

import json
import pathlib
import sys
import urllib.request

LIBRARY_URL = "https://app2.govee.com/appsku/v1/light-effect-libraries"
APP_VERSION = "7.5.20"
OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "govee_ble_local" / "scenes"


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


def fetch(sku: str) -> dict[str, dict[str, object]]:
    req = urllib.request.Request(f"{LIBRARY_URL}?sku={sku}", headers={"AppVersion": APP_VERSION})
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
                path = OUT_DIR / f"{sku.upper()}.json"
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
    ``_scene_upload_frames`` routing and bucket it:
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
                frames = dev._scene_upload_frames(scene)  # type: ignore[attr-defined]
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


def main(skus: list[str]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for sku in skus:
        path = OUT_DIR / f"{sku.upper()}.json"
        try:
            fresh = fetch(sku)
        except Exception as err:  # noqa: BLE001
            print(f"{sku}: FETCH FAILED: {err}")
            continue
        existing = json.loads(path.read_text()) if path.exists() else {}
        scenes = _merge(existing, fresh)
        path.write_text(json.dumps(scenes, indent=1, sort_keys=True))
        kept = sum(1 for n in scenes if n in existing and _valid_resolved(existing[n]))
        placeholders = sorted(n for n, v in scenes.items() if v.get("placeholder"))
        note = f"; {len(placeholders)} placeholders (run --resolve)" if placeholders else ""
        print(
            f"{sku}: {len(scenes)} scenes -> scenes/{sku.upper()}.json "
            f"[{kept} preserved]{note}"
        )
        if placeholders:
            print(f"    unresolved placeholders: {', '.join(placeholders)}")


if __name__ == "__main__":
    import os

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    skus = args or scene_capable_skus()
    if "--audit" in sys.argv:
        audit()
    elif "--resolve" in sys.argv:
        email = os.environ.get("GOVEE_EMAIL", "")
        password = os.environ.get("GOVEE_PASSWORD", "")
        if not email or not password:
            sys.exit("--resolve needs GOVEE_EMAIL and GOVEE_PASSWORD env vars")
        resolve_placeholders(email, password, skus)
    else:
        main(skus)
