"""Built-in scene catalogs (per-SKU), fetched from Govee's scene library.

Each `<SKU>.json` maps scene name -> {code, param, category}. `code` is the
BLE activation code; `param` is the base64 effect blob for the a3-chunk upload
(None if the device bare-activates). Regenerate with tools/fetch_scenes.py.
"""
from __future__ import annotations

import base64
import functools
import json
import re
from dataclasses import dataclass
from importlib import resources
from types import MappingProxyType

from ..exceptions import GoveeBleError, GoveeBleInvalidValue
from ..wire import build

# Path-B scene-library upload table (from the decompiled `ScenesOp.parseSceneV1`,
# confirmed by the Q4 source handoff):
#   sceneType -> (required scene-version, a3 commByte, leading bytes to strip).
# The device uploads only if its `versionArray` contains the required version (strict
# AND-gate). sceneType 0 (static) / 4 (cube) have no upload branch → activate-only.
# sceneType 5 (DIY) is family-specific (H60A6 = dialect B; H6052 = commByte 9) and is
# handled by `scene_upload_frames` (dialect-specific), not this table.
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
    # Path-B upload drivers (from the scene-library DTO); see spec/docs/SCENE_UPLOAD_ENCODING.md.
    scene_type: int | None = None  # 0 static,1 rgb,2 rgbic,3 graffiti,4 cube,5 diy,6 compose
    cmd_version: int | None = None
    big_effect: bool = False       # bigEffectStr==1: fetch-required when param is empty


_SKU_RE = re.compile(r"[A-Za-z0-9]{3,8}")


@functools.cache
def _catalog(sku: str) -> MappingProxyType[str, Scene]:
    """Parse + cache one (already validated, upper-case) SKU's catalog, read-only."""
    try:
        raw = (resources.files(__package__) / f"{sku}.json").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        return MappingProxyType({})
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError(f"top-level value is {type(data).__name__}, expected object")
        scenes = {
            name: Scene(
                name, int(v["code"]), v.get("param"), v.get("category"),
                v.get("param_id"), bool(v.get("placeholder", False)),
                scene_type=v.get("scene_type"), cmd_version=v.get("cmd_version"),
                big_effect=bool(v.get("big_effect", False)),
            )
            for name, v in data.items()
        }
    except (ValueError, TypeError, KeyError, AttributeError) as err:
        raise GoveeBleError(f"scene catalog for {sku} is malformed: {err!r}") from err
    return MappingProxyType(scenes)


def load_scenes(sku: str) -> dict[str, Scene]:
    """Return {scene_name: Scene} for a SKU, or {} if no catalog is bundled.

    Returns a fresh dict each call (Scene is frozen), so callers can't corrupt the cache.
    Raises GoveeBleInvalidValue for a malformed SKU, GoveeBleError for a corrupt catalog."""
    key = sku.strip() if isinstance(sku, str) else ""
    if not _SKU_RE.fullmatch(key):
        raise GoveeBleInvalidValue(f"invalid SKU {sku!r}: expected 3-8 alphanumeric characters")
    return dict(_catalog(key.upper()))


def scene_names_by_code(sku: str) -> dict[int, str]:
    """Reverse index {code: name} for a SKU's catalog (first name wins)."""
    out: dict[int, str] = {}
    for name, scene in load_scenes(sku).items():
        out.setdefault(scene.code, name)
    return out


def scene_upload_frames(
    scene: Scene, scene_dialect: str | None, scene_versions: frozenset[int]
) -> list[bytes] | None:
    """Plan the a3/a4 upload frames that stage ``scene`` on a device, or None -> activate-only.

    Pure: takes the scene plus the profile's ``scene_dialect`` / ``scene_versions``. DIY
    (sceneType 5) is dialect-specific; every other type goes through the library table
    (:func:`scene_upload_params`)."""
    param = scene.param
    if not param:
        return None
    st = scene.scene_type
    if scene_dialect == "B_h60a6" and st == 5:
        value = base64.b64decode(param)[1:]
        if len(value) < 2:
            return None
        is_diy = (value[0] | (value[1] << 8)) + 2 == len(value)
        return (build.scene_upload_a3(value, build.COMM_H60A6) if is_diy
                else build.scene_upload_a4_mtu(value, build.COMM_H60A6))
    if scene_dialect == "B_h6052" and st == 5:
        raw = base64.b64decode(param)
        if raw and raw[0] == 0x13:
            return build.scene_upload_a3(raw[1:], build.COMM_H6052_GRAFFITI)
        return None
    sel = scene_upload_params(st, scene_versions)
    if sel is None:
        return None
    comm_byte, strip = sel
    return build.scene_upload_a3(base64.b64decode(param)[strip:], comm_byte)
