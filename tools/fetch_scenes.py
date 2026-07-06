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


def main(skus: list[str]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for sku in skus:
        try:
            scenes = fetch(sku)
        except Exception as err:  # noqa: BLE001
            print(f"{sku}: FETCH FAILED: {err}")
            continue
        (OUT_DIR / f"{sku.upper()}.json").write_text(json.dumps(scenes, indent=1, sort_keys=True))
        ph = sum(1 for v in scenes.values() if v.get("placeholder"))
        print(f"{sku}: {len(scenes)} scenes -> scenes/{sku.upper()}.json ({ph} placeholders)")


if __name__ == "__main__":
    import os

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    skus = args or ["H60A6", "H6006", "H6008", "H6047", "H6052", "H61A8"]
    if "--resolve" in sys.argv:
        email = os.environ.get("GOVEE_EMAIL", "")
        password = os.environ.get("GOVEE_PASSWORD", "")
        if not email or not password:
            sys.exit("--resolve needs GOVEE_EMAIL and GOVEE_PASSWORD env vars")
        resolve_placeholders(email, password, skus)
    else:
        main(skus)
