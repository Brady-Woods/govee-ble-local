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

# Path-B scene-library upload table (from the decompiled `ScenesOp.parseSceneV1`,
# confirmed by the Q4 source handoff):
#   sceneType -> (required scene-version, a3 commByte, leading bytes to strip).
# The device uploads only if its `versionArray` contains the required version (strict
# AND-gate). sceneType 0 (static) / 4 (cube) have no upload branch → activate-only.
# sceneType 5 (DIY) is family-specific (H60A6 = dialect B; H6052 = commByte 9) and is
# handled by per-device `_scene_upload_frames` overrides, not this table.
_LIBRARY_SCENE: dict[int, tuple[int, int, int]] = {
    1: (1, 1, 0),    # rgb      -> V1, comType 1, strip 0
    2: (2, 2, 0),    # rgbic    -> V2, comType 2, strip 0
    3: (3, 7, 2),    # graffiti -> V3, comType 7, strip 2
    6: (6, 10, 1),   # compose  -> V6, comType 10 (0x0a), strip 1  (parseSceneV1: version 6, not 5)
}


def scene_upload_params(scene_type: int | None, versions: frozenset[int]) -> tuple[int, int] | None:
    """Return (commByte, strip) for a library-scene a3 upload, or None → activate-only.

    None when the sceneType has no upload branch (0/4), is the unresolved DIY path (5),
    is unknown, or the device's `versions` (versionArray) doesn't support the required
    scene-version. See `_LIBRARY_SCENE`."""
    spec = _LIBRARY_SCENE.get(scene_type) if scene_type is not None else None
    if spec is None:
        return None
    version, comm_byte, strip = spec
    if version not in versions:
        return None
    return (comm_byte, strip)


@dataclass(frozen=True)
class Scene:
    name: str
    code: int
    param: str | None       # base64 effect blob (None -> bare activate)
    category: str | None = None
    param_id: int | None = None   # scenceParamId (to resolve placeholders via cloud)
    placeholder: bool = False     # True: real blob only from the authenticated API
    # Path-B upload drivers (from the scene-library DTO); see SCENE_UPLOAD_REVIEW.md.
    scene_type: int | None = None  # 0 static,1 rgb,2 rgbic,3 graffiti,4 cube,5 diy,6 compose
    cmd_version: int | None = None
    big_effect: bool = False       # bigEffectStr==1: fetch-required when param is empty


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
            scene_type=v.get("scene_type"), cmd_version=v.get("cmd_version"),
            big_effect=bool(v.get("big_effect", False)),
        )
        for name, v in data.items()
    }
