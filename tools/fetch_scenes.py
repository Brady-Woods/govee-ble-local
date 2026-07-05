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
            scenes[scene["sceneName"]] = {
                "code": int(code),
                "param": eff.get("scenceParam") or None,
                "category": category.get("categoryName"),
            }
    return scenes


def main(skus: list[str]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for sku in skus:
        try:
            scenes = fetch(sku)
        except Exception as err:  # noqa: BLE001
            print(f"{sku}: FETCH FAILED: {err}")
            continue
        (OUT_DIR / f"{sku.upper()}.json").write_text(json.dumps(scenes, indent=1, sort_keys=True))
        print(f"{sku}: {len(scenes)} scenes -> scenes/{sku.upper()}.json")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        # supported SKUs with scenes (lights); plugs have none.
        args = ["H60A6", "H6006", "H6008", "H6047", "H6052", "H61A8"]
    main(args)
