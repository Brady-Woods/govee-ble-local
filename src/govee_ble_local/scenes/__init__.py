"""Built-in scene catalogs (per-SKU), fetched from Govee's scene library.

`store.json` holds every SKU's catalog, each scene stored once: ``pool`` is the list of the bundled
scenes, unique and sorted by content (the scene name, then the entry's JSON with sorted keys), each
{name, code, param, category, ...}; ``skus`` maps each SKU to its catalog, the increasing indexes of its
scenes in ``pool``, so SKUs that share a scene share its entry. `code` is the BLE activation code; `param`
is the base64 effect blob for the a3-chunk upload (None if the device bare-activates). Regenerate with
tools/fetch_scenes.py.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import functools
import itertools
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from importlib import resources
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from ..exceptions import GoveeBleError, GoveeBleInvalidValue
from ..wire import build
from ..wire import _spec
from ..wire._spec import Frame as _F, Upload as _U

if TYPE_CHECKING:
    from ..devices.profile import SceneDialect


class _SceneType(IntEnum):
    """Scene-library ``sceneType`` values (``Category.Scene.sceneType``).

    An IntEnum, so members compare equal to the plain ints in the catalogs and in
    :attr:`Scene.scene_type`.
    """

    STATIC = 0
    RGB = 1
    RGBIC = 2
    GRAFFITI = 3
    CUBE = 4
    DIY = 5
    COMPOSE = 6


# A decoded scene blob (the spec's govee_upload form ``cloud``) leads with a byte that picks its upload;
# the lead_* types carry it as a ``contents`` marker, and the ``value`` after it is the upload: compose
# 0x0a (ShareDiy.isValidComposeBytes), H60A6 DIY / graffiti 0x50 (BaseSceneViewMode.r3), H6052 graffiti
# 0x13, scenes graffiti 0x01 (ShareDiy.isValidGraffitiBytes) plus the effect's high byte. The value as
# sent (form ``wire``) is read by its kind's value type.
_UK = _U.UploadKind
_CLOUD = int(_U.UploadForm.cloud)
_WIRE = int(_U.UploadForm.wire)

# Path-B scene-library upload table (from the decompiled `ScenesOp.parseSceneV1`,
# confirmed by the Q4 source handoff):
#   sceneType -> (required scene-version, a3 commByte, leading bytes to strip).
# The device uploads only if its `versionArray` contains the required version (strict
# AND-gate). sceneType 0 (static) / 4 (cube) have no upload branch → activate-only.
# sceneType 5 (DIY) is family-specific (H60A6 = dialect B; H6052 = commByte 9) and is
# handled by `scene_upload_frames` (dialect-specific), not this table. sceneType 6
# (compose) also depends on the blob's first byte (ScenesOp.o), checked in `_plan_upload`.
_LIBRARY_SCENE: Final[Mapping[int, tuple[int, int, int]]] = MappingProxyType({
    # comTypes are the spec's comm_byte (the app's MULTI_* constants).
    _SceneType.RGB: (1, int(_F.CommByte.v1_new_scenes), 0),        # MultiNewScenesControllerV1, strip 0
    _SceneType.RGBIC: (2, int(_F.CommByte.v2_new_scenes), 0),      # MultiNewScenesControllerV2, strip 0
    _SceneType.GRAFFITI: (3, int(_F.CommByte.v3_new_scenes), 2),   # MultiNewScenesControllerV3, strip 2
    # compose -> MultiNewScenesControllerV5, comType 0x0a (MULTI_V4_NEW_SCENES), strip 1; gated on
    # scene-version 6 (parseSceneV1: `versionArray.contains(6) && sceneType == 6`).
    _SceneType.COMPOSE: (6, int(_F.CommByte.v4_new_scenes), 1),
})


def scene_upload_params(scene_type: int | None, versions: frozenset[int]) -> tuple[int, int] | None:
    """Return (commByte, strip) for a library-scene a3 upload, or None → activate-only.

    None when the sceneType has no upload branch (0/4), is the dialect-specific DIY path
    (5), is unknown, or the device's `versions` (versionArray) doesn't support the required
    scene-version. See `_LIBRARY_SCENE`. A compose scene (6) is uploaded only if its blob
    also starts with 0x0a; :func:`scene_upload_frames` checks that.
    """
    spec = _LIBRARY_SCENE.get(scene_type) if scene_type is not None else None
    if spec is None:
        return None
    version, comm_byte, strip = spec
    if version not in versions:
        return None
    return (comm_byte, strip)


@dataclass(frozen=True)
class Scene:
    """One built-in scene from a SKU's bundled catalog (see :func:`load_scenes`)."""

    name: str
    """Scene name as the Govee app shows it."""
    code: int
    """BLE activation code."""
    param: str | None
    """Base64 effect blob for the a3/a4 upload; None (or empty) means activate-only."""
    category: str | None = None
    """Scene-library category name, if any."""
    param_id: int | None = None
    """``scenceParamId``: resolves a placeholder blob through the cloud API."""
    placeholder: bool = False
    """True when the real blob is only available from the authenticated API."""
    # Path-B upload drivers (from the scene-library DTO); see spec/docs/SCENE_UPLOAD_ENCODING.md.
    scene_type: int | None = None
    """Scene-library ``sceneType``, which selects the upload path.

    0 static, 1 rgb, 2 rgbic, 3 graffiti, 4 cube, 5 diy, 6 compose.
    """
    cmd_version: int | None = None
    """Scene-library ``cmdVersion``, if any."""
    big_effect: bool = False
    """``bigEffectStr == 1``: the blob must be fetched when ``param`` is empty."""


_SKU_RE = re.compile(r"[A-Za-z0-9]{3,8}")
_STORE = "store.json"


class _MalformedSceneParam(GoveeBleInvalidValue):
    """A scene's bundled/cloud effect blob is not valid base64."""


@functools.cache
def _catalog(sku: str) -> MappingProxyType[str, Scene]:
    """Parse + cache one (already validated, upper-case) SKU's catalog, read-only. Each first lookup reads and
    checks the whole store (:func:`_catalogs`), so a store that is missing, unreadable or malformed raises
    GoveeBleError for every SKU, one it holds no catalog for too."""
    try:
        # This module is the package's __init__, so __name__ is the package that holds the JSON.
        raw = (resources.files(__name__) / _STORE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ModuleNotFoundError) as err:
        # The store ships with the package: one that is missing or can't be read (permissions, I/O, not UTF-8)
        # is a library error like a malformed one, so the scene properties and Device.update() raise only
        # GoveeBleError.
        raise GoveeBleError(f"scene catalog for {sku} could not be read: {err!r}") from err
    try:
        catalogs = _catalogs(json.loads(raw))
    except (ValueError, TypeError, KeyError, AttributeError) as err:
        # binascii.Error (bad base64) is a ValueError; its repr never quotes the blob.
        raise GoveeBleError(f"scene catalog for {sku} is malformed: {err!r}") from err
    return MappingProxyType({scene.name: scene for scene in catalogs.get(sku, ())})


def _catalogs(store: Any) -> dict[str, list[Scene]]:
    """Every SKU's catalog in the parsed ``store``, its scenes in pool order. Raises ValueError / TypeError /
    KeyError / AttributeError unless ``store`` is an object of exactly ``pool``, a list of scene entries
    (:func:`_scene`), and ``skus``, which maps each upper-case SKU to the strictly increasing indexes of at
    least one pool entry, no two of one name, and every pool entry is some SKU's (no orphan)."""
    if not isinstance(store, dict) or set(store) != {"pool", "skus"}:
        raise ValueError("the store is not an object of 'pool' and 'skus'")
    entries, skus = store["pool"], store["skus"]
    if not isinstance(entries, list) or not isinstance(skus, dict):
        raise TypeError("the store's 'pool' is not a list, or its 'skus' not an object")
    pool = [_scene(i, entry) for i, entry in enumerate(entries)]
    catalogs: dict[str, list[Scene]] = {}
    for key, refs in skus.items():
        if not (isinstance(key, str) and _SKU_RE.fullmatch(key) and key == key.upper()):
            raise ValueError(f"{key!r} is not an upper-case SKU")
        if (not isinstance(refs, list) or not refs
                or not all(type(i) is int and 0 <= i < len(pool) for i in refs)
                or any(a >= b for a, b in itertools.pairwise(refs))):
            raise ValueError(f"{key}'s catalog is not strictly increasing indexes into the pool")
        catalogs[key] = [pool[i] for i in refs]
        if len({scene.name for scene in catalogs[key]}) != len(refs):
            raise ValueError(f"{key}'s catalog holds two scenes of one name")
    used = {i for refs in skus.values() for i in refs}
    if len(used) != len(pool):
        raise ValueError(f"pool entry {min(set(range(len(pool))) - used)} is no SKU's")
    return catalogs


def _scene(i: int, entry: Any) -> Scene:
    """Pool entry ``i`` as a Scene. Its param is checked now, so a corrupt bundled blob fails at load, not at
    upload, and a scene with version ``rules`` is refused: the app lists it only on firmware its rules allow
    (devices.yaml scenes.list_filter), which the library doesn't model, so offered it would show on every
    one."""
    name = entry["name"]
    if not isinstance(name, str) or not name:
        raise ValueError(f"pool entry {i} has no scene name")
    param = entry.get("param")
    if param:
        base64.b64decode(param, validate=True)
    if entry.get("rules"):
        raise ValueError(f"{name!r} is gated by version rules")
    return Scene(
        name, int(entry["code"]), param, entry.get("category"),
        entry.get("param_id"), bool(entry.get("placeholder", False)),
        scene_type=entry.get("scene_type"), cmd_version=entry.get("cmd_version"),
        big_effect=bool(entry.get("big_effect", False)),
    )


def _catalog_key(sku: str) -> str:
    """The upper-case catalog key for ``sku``; GoveeBleInvalidValue if it is malformed."""
    key = sku.strip() if isinstance(sku, str) else ""
    if not _SKU_RE.fullmatch(key):
        raise GoveeBleInvalidValue(f"invalid SKU {sku!r}: expected 3-8 alphanumeric characters")
    return key.upper()


def load_scenes(sku: str) -> dict[str, Scene]:
    """Return {scene_name: Scene} for a SKU, or {} if no catalog is bundled.

    Returns a fresh dict each call (Scene is frozen), so callers can't corrupt the cache.
    The first call per SKU reads and parses the bundled JSON from disk; on an event loop
    use :func:`async_load_scenes` (or call this from an executor) for that first call.
    Raises GoveeBleInvalidValue for a malformed SKU, GoveeBleError for a missing, corrupt or
    unreadable store.
    """
    return dict(_catalog(_catalog_key(sku)))


async def async_load_scenes(sku: str) -> dict[str, Scene]:
    """:func:`load_scenes` without blocking the event loop.

    The first lookup per SKU reads and parses the bundled JSON from disk; this runs that
    in a worker thread (later lookups are cached and return immediately)."""
    return await asyncio.to_thread(load_scenes, sku)


# Reverse indexes {code: name}, keyed by catalog key. Each entry remembers the catalog it
# was built from and is rebuilt when `_catalog` returns a different mapping (for example
# after `_catalog.cache_clear()`), so the two caches can't disagree.
_NAMES_BY_CODE: dict[str, tuple[Mapping[str, Scene], Mapping[int, str]]] = {}


def scene_names_by_code(sku: str) -> dict[int, str]:
    """Reverse index {code: name} for a SKU's catalog (first name wins).

    Built once per catalog and cached; returns a fresh dict each call.
    """
    key = _catalog_key(sku)
    catalog = _catalog(key)
    hit = _NAMES_BY_CODE.get(key)
    if hit is None or hit[0] is not catalog:
        index: dict[int, str] = {}
        for name, scene in catalog.items():
            index.setdefault(scene.code, name)
        hit = (catalog, MappingProxyType(index))
        _NAMES_BY_CODE[key] = hit
    return dict(hit[1])


def _decode_param(scene: Scene) -> bytes:
    """Strictly base64-decode ``scene.param``.

    Raises _MalformedSceneParam (a GoveeBleInvalidValue naming the scene, never the
    payload) on bad data.
    """
    try:
        return base64.b64decode(scene.param or "", validate=True)
    except (binascii.Error, ValueError, TypeError) as err:
        raise _MalformedSceneParam(
            f"scene {scene.name!r} (code {scene.code}) has a malformed effect parameter"
        ) from err


def scene_upload_frames(
    scene: Scene, scene_dialect: SceneDialect | None, scene_versions: frozenset[int], *, mtu: int = 20
) -> list[bytes] | None:
    """Plan the a3/a4 upload frames that stage ``scene`` on a device, or None -> activate-only.

    Pure: takes the scene plus the profile's ``scene_dialect`` / ``scene_versions``. DIY
    (sceneType 5) is dialect-specific; every other type goes through the library table
    (:func:`scene_upload_params`). ``mtu`` is the link's write size (ATT MTU - 3,
    ``GoveeConnection.write_payload_size``); only the 0xA4-MTU graffiti upload (H60A6) uses it, as
    the app does (``H60A6GraffitiParse.makeBleController:240``: ``MtuConfig.getAvailableMtuSize``)."""
    if not scene.param:
        return None
    try:
        return _plan_upload(scene, scene_dialect, scene_versions, mtu)
    except _MalformedSceneParam:
        raise
    except GoveeBleInvalidValue as err:
        raise GoveeBleInvalidValue(
            f"scene {scene.name!r} (code {scene.code}) cannot be uploaded: {err}"
        ) from err


def _reads_as(kind: Any, form: int, value: bytes) -> Any:
    """``value`` read as the spec's ``govee_upload(kind, form)`` (its lead, then its value), or None when
    the spec can't read it as that kind."""
    try:
        return _spec.read(_U, value, kind, form)
    except Exception:  # noqa: BLE001 - a value the spec type can't read is not that kind of effect
        return None


def _h60a6_upload(raw: bytes, mtu: int) -> list[bytes] | None:
    """The upload of an H60A6 type-5 (DIY / graffiti) blob, as the app's new-DIY path chooses it, or
    None -> activate-only.

    ``BaseSceneViewMode.r3``: the decoded blob must start with 0x50, then the H60A6's parsers are tried
    in order (``H60A6DiyConfig``: ``H60A6DiyParse``, then ``H60A6GraffitiParse``) and the first that
    reads it sends it; when none does, r3 fails and the scene is only activated. Each parser is the
    spec's value type for it: a DIY is a ``diy_value`` whose length prefix covers the rest (kind
    ``h60a6_diy``; ``Pro4H60A6Diy``; 0xA3, commByte 0x58), a graffiti a ``graffiti_value`` (kind
    ``h60a6_graffiti``, marker 0x20; 0xA4-MTU, commByte 0x58, frames of up to ``mtu`` bytes:
    ``Compose4DefWrite4Multi.d:233-246``). The try-in-order is the library's (Kaitai has no backtracking).
    The value is sent as the catalogue holds it; the app re-serialises what it parsed
    (``IDiyParse.toBytes``), which is not modelled."""
    param = _reads_as(_UK.h60a6_diy, _CLOUD, raw)   # lead 0x50, then the value
    value = b"" if param is None else bytes(param.value)
    if not value:
        return None
    if _reads_as(_UK.h60a6_diy, _WIRE, value) is not None:
        return build.scene_upload_a3(value, build.COMM_H60A6)
    if _reads_as(_UK.h60a6_graffiti, _WIRE, value) is not None:
        return build.scene_upload_a4_mtu(value, build.COMM_H60A6, mtu=mtu)
    return None


def _plan_upload(
    scene: Scene, scene_dialect: SceneDialect | None, scene_versions: frozenset[int], mtu: int
) -> list[bytes] | None:
    st = scene.scene_type
    if scene_dialect == "B_h60a6" and st == _SceneType.DIY:
        return _h60a6_upload(_decode_param(scene), mtu)
    if scene_dialect == "B_h6052" and st == _SceneType.DIY:
        graffiti = _reads_as(_UK.h6052_graffiti, _CLOUD, _decode_param(scene))   # lead 0x13, then the value
        if graffiti is not None:
            return build.scene_upload_a3(bytes(graffiti.value), build.COMM_H6052_GRAFFITI)
        return None
    sel = scene_upload_params(st, scene_versions)
    if sel is None:
        return None
    comm_byte, strip = sel
    raw = _decode_param(scene)
    if st == _SceneType.COMPOSE:
        # ScenesOp.o (parserScenes4Compose): only a blob that ShareDiy.isValidComposeBytes accepts (lead
        # 0x0a, lead_compose) gets MultiNewScenesControllerV5. A 0x51 blob goes to
        # MultiNewScenesControllerH60B0 (commByte 90), which is not modelled; any other lead gets no
        # controller. Both stay activate-only.
        compose = _reads_as(_UK.compose, _CLOUD, raw)
        return None if compose is None else build.scene_upload_a3(bytes(compose.value), comm_byte)
    if st == _SceneType.GRAFFITI:
        # ScenesOp.n (:411-429) sends bytes [2:] only when ShareDiy.isValidGraffitiBytes (:96-115) accepts
        # the blob: the spec's scenes_graffiti_gate.is_valid (size >= 9, lead 0x01, the group-header walk).
        # The value sent is the one after the scenes_graffiti lead.
        check = _reads_as(_UK.scenes_graffiti_gate, _CLOUD, raw)
        graffiti = _reads_as(_UK.scenes_graffiti, _CLOUD, raw)
        if check is None or not check.value.is_valid or graffiti is None:
            return None
        return build.scene_upload_a3(bytes(graffiti.value), comm_byte)
    if st == _SceneType.RGB:
        # ScenesOp.l (:377-392): only a blob ScenesRgb.isValidProtocolBytes (:1676-1701) accepts, the spec's
        # rgb_scene_value.is_valid; any other is only activated.
        rgb = _reads_as(_UK.rgb_scene, _CLOUD, raw)
        if rgb is None or not rgb.value.is_valid:
            return None
    elif st == _SceneType.RGBIC and _reads_as(_UK.rgbic_scene, _CLOUD, raw) is None:
        # ScenesOp.m (:394-409): ScenesRgbIC.isValidProtocolBytes (:2783-2807), which rgbic_scene_value reads
        # exactly (a blob it can't read is only activated).
        return None
    return build.scene_upload_a3(raw[strip:], comm_byte)
