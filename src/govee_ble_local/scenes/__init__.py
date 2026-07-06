"""Built-in scene catalogs (per-SKU), fetched from Govee's scene library.

Each `<SKU>.json` maps scene name -> {code, param, category}. `code` is the
BLE activation code; `param` is the base64 effect blob for the a3-chunk upload
(None if the device bare-activates). Regenerate with tools/fetch_scenes.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources


@dataclass(frozen=True)
class Scene:
    name: str
    code: int
    param: str | None       # base64 effect blob (None -> bare activate)
    category: str | None = None
    param_id: int | None = None   # scenceParamId (to resolve placeholders via cloud)
    placeholder: bool = False     # True: real blob only from the authenticated API


@lru_cache(maxsize=None)
def load_scenes(sku: str) -> dict[str, Scene]:
    """Return {scene_name: Scene} for a SKU, or {} if no catalog is bundled."""
    try:
        raw = (resources.files(__package__) / f"{sku.upper()}.json").read_text()
    except (FileNotFoundError, ModuleNotFoundError):
        return {}
    data = json.loads(raw)
    return {
        name: Scene(
            name, int(v["code"]), v.get("param"), v.get("category"),
            v.get("param_id"), bool(v.get("placeholder", False)),
        )
        for name, v in data.items()
    }
