"""Built-in scene catalogs (per-SKU), fetched from Govee's scene library.

`store.json` holds every SKU's catalog, each scene stored once: ``pool`` is the list of the bundled
scenes, unique and sorted by content (the scene name, then the entry's JSON with sorted keys), each
{name, code, param, category, ...}; ``skus`` maps each SKU to its catalog, the increasing indexes of its
scenes in ``pool``, so SKUs that share a scene share its entry. `code` is the BLE activation code; `param`
is the base64 effect blob for the a3-chunk upload (None if the device bare-activates).

A SKU's catalog is the one the Govee app's scene list shows at the union of the SKU's scene sets
(devices.yaml scenes.list_filter). At a smaller set the app's list drops each lightEffect whose op type
is not in the set and plays the first one left, so a scene may be hidden there, or play another of its
effects. ``by_set`` records that: it maps a SKU and a scene set (its versions sorted ascending and joined
by ``,``, ``""`` for the empty set: :func:`_set_key`) to the scenes listed differently there, each name
of the SKU's catalog to ``null`` (not listed at that set) or the pool index of the entry it plays there,
an entry of the same name. Every other scene is as in the catalog. ``by_set`` is ``{}`` where nothing
differs. :func:`load_scenes` and :func:`scene_names_by_code` take ``scene_versions=`` for the catalog at
a set; :class:`~govee_ble_local.devices.device.Device` uses its :attr:`scene_versions`.

Regenerate with tools/fetch_scenes.py, which checks each entry with this module's check
(``_validate_entry``). The store is read and checked once, on the first lookup of any SKU; every lookup
after it, at any scene set, is from memory.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import errno
import functools
import itertools
import json
import re
import sys
import threading
import zipfile
import zlib
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import IntEnum
from importlib import resources
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from ..exceptions import GoveeBleError, GoveeBleInvalidValue
from ..wire import build
from ..wire import _spec
from ..wire._spec import Frame as _F, Upload as _U

try:
    from lzma import LZMAError as _LZMAError
except ImportError:   # a Python built without lzma reads no LZMA member either
    _LZMAError = zlib.error  # type: ignore[assignment,misc]

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
# One catalog as the lookups take it: {name: Scene} in pool order, and {code: name} (first name wins).
_Catalog = tuple[Mapping[str, Scene], Mapping[int, str]]
_NO_CATALOG: Final[_Catalog] = (MappingProxyType({}), MappingProxyType({}))
# The catalogs of a SKU the store holds none for: empty at every set.
_NO_SETS: Final[Mapping[str | None, _Catalog]] = MappingProxyType({None: _NO_CATALOG})
# The scene_select activation's effect field is a u2 (build.scene_activate): the codes a scene can have.
_MAX_CODE: Final = 0xFFFF
# Read errors of the process's state, not of the package file (too many open files, out of memory, try
# again): not kept, so the next lookup reads the store again.
_TRANSIENT: Final = frozenset({errno.EMFILE, errno.ENFILE, errno.ENOMEM, errno.EAGAIN})
# Held across the first lookup's read, so lookups made at once read the store once.
_STORE_LOCK: Final = threading.Lock()


class _MalformedSceneParam(GoveeBleInvalidValue):
    """A scene's bundled/cloud effect blob is not valid base64."""


@functools.cache
def _store() -> Mapping[str, Mapping[str | None, _Catalog]] | GoveeBleError:
    """Every SKU's catalogs in the bundled store, ``{SKU: {key: catalog}}`` (:func:`_catalogs`: None the
    SKU's whole catalog, each ``by_set`` key the one at that set), read-only: the store is read and checked
    once, on the first lookup of any SKU, and every catalog of every set is built then.

    A store that is missing, unreadable or malformed gives the GoveeBleError that says so, returned, not
    raised, so the cache keeps this first failure: :func:`_catalog` raises a GoveeBleError of it (its message
    and cause) for every SKU, one the store holds no catalog for too, and the store is not read again (a
    package file changes only with a reinstall). A read error of the process's state (:data:`_TRANSIENT`:
    too many open files, out of memory) is raised, not kept, so the next lookup reads the store again. A
    test that swaps the store calls ``_store.cache_clear()`` before and after. The kept failure holds no
    traceback, and neither does any exception of its chain (``__cause__`` / ``__context__``): a traceback's
    frames lead back through the first lookup to its caller, whose frames and locals the cache would keep
    alive for the life of the process; every message stays (:func:`_failure`). Nor does its chain hold the
    exception the first lookup's caller was handling, which is the caller's, not the store's."""
    outer = sys.exc_info()[1]   # what the first lookup's caller is handling: not the store's, never kept
    try:
        # This module is the package's __init__, so __name__ is the package that holds the JSON.
        raw = (resources.files(__name__) / _STORE).read_text(encoding="utf-8")
    # RuntimeError: a zip member flagged encrypted, or one of an unknown method or flag (NotImplementedError)
    except (OSError, UnicodeDecodeError, ModuleNotFoundError, zipfile.BadZipFile, zlib.error, _LZMAError,
            RuntimeError) as err:
        # The store ships with the package: one that is missing or can't be read (permissions, I/O, not UTF-8,
        # a corrupt member of a zipped install) is a library error like a malformed one, so the scene
        # properties and Device.update() raise only GoveeBleError. The cause is quoted by its str: the repr
        # of a UnicodeDecodeError holds the whole store, every blob in it.
        message = f"the bundled scene store could not be read: {type(err).__name__}: {err}"
        if isinstance(err, OSError) and err.errno in _TRANSIENT:
            # raised, so not kept (functools.cache keeps no raise): the next lookup reads again
            raise GoveeBleError(message) from err
        return _failure(message, err, outer)
    try:
        catalogs = _catalogs(json.loads(raw))
    except (ValueError, RecursionError) as err:
        # _catalogs raises only ValueError (binascii.Error, bad base64, is one; its repr never quotes the
        # blob); json.loads raises RecursionError on a store nested too deep.
        return _failure(f"the bundled scene store is malformed: {err!r}", err, outer)
    return MappingProxyType({sku: MappingProxyType(sets) for sku, sets in catalogs.items()})


def _failure(message: str, cause: BaseException, outer: BaseException | None) -> GoveeBleError:
    """The GoveeBleError :func:`_store` keeps: of ``message``, caused by ``cause`` as ``raise ... from cause``
    would chain it, with the traceback of every exception of the chain (``cause``, its ``__cause__`` and
    ``__context__``, theirs, ...) dropped, so it keeps no frame alive; the messages stay. ``outer`` is the
    exception the first lookup's caller was handling (None if none), which a link raised while it was handled
    took in as its ``__context__``: that link is cut, so the kept failure neither holds it (nor what it
    references) nor shows it in a later lookup's log, and its traceback is left to the caller handling it."""
    error = GoveeBleError(message)
    error.__cause__ = cause
    chain: list[BaseException | None] = [cause]
    seen: set[int] = set()
    while chain:
        link = chain.pop()
        if link is not None and id(link) not in seen:
            seen.add(id(link))
            link.__traceback__ = None
            if outer is not None and link.__context__ is outer:
                link.__context__ = None   # the caller's own exception: left as it is, and not kept
            chain += (link.__cause__, link.__context__)
    return error


def _catalog(sku: str) -> Mapping[str | None, _Catalog]:
    """One (already validated, upper-case) SKU's catalogs from :func:`_store`, read-only, by ``by_set`` key
    (:func:`_set_key`; None its whole catalog): :data:`_NO_SETS` where the store holds none for the SKU;
    where the store failed, a GoveeBleError of that first failure (its message and cause), every time. Every
    scene lookup, at every set, goes through this one call; its callers pick the set (the whole catalog
    where the store records nothing at it). A plain lookup once the store is read: no I/O.
    :data:`_STORE_LOCK` makes first lookups made at once (one per device's setup) read the store once:
    functools.cache has no lock of its own, so each lookup that missed it would read and check it."""
    with _STORE_LOCK:
        store = _store()
    if isinstance(store, GoveeBleError):
        # A fresh error each time: one shared instance would carry each raise's traceback and the exception
        # its caller was handling to every other caller, and keep the last caller's frames alive.
        raise GoveeBleError(*store.args) from store.__cause__
    return store.get(sku, _NO_SETS)


def _set_key(versions: Collection[int]) -> str:
    """The canonical key of the scene set ``versions`` (its scene versions, as
    :attr:`~govee_ble_local.devices.device.Device.scene_versions` gives them): the versions sorted
    ascending, each once, joined by ``,``; ``""`` for the empty set. The store's ``by_set`` keys are in this
    form only, and tools/fetch_scenes.py keys them by this function. GoveeBleInvalidValue unless
    ``versions`` is a collection (``collections.abc.Collection``, read once) of non-negative ints (a bool is
    not one), each short enough to write as a key: so a str is refused (its members are strs) and a bytes,
    bytearray or memoryview of bytes is a collection of ints like any other, while a generator or another
    one-shot iterator is refused (it is not a collection: given twice, it would read as the empty set the
    second time). The message names the type only, never the value (an int too long to write can't be)."""
    members = list(versions) if isinstance(versions, Collection) else None
    try:
        if members is not None and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
                                       for v in members):
            return ",".join(str(int(v)) for v in sorted(set(members)))
    except ValueError:   # a version past the int-to-str limit (sys.get_int_max_str_digits), as 10**5000
        pass
    raise GoveeBleInvalidValue(
        f"scene_versions is not a collection of non-negative ints ({type(versions).__name__})")


def _canonical(key: object) -> bool:
    """Whether ``key`` is a scene set's key in :func:`_set_key`'s form."""
    if not isinstance(key, str):
        return False
    try:
        return _set_key([int(v) for v in key.split(",")] if key else []) == key
    except ValueError:   # not decimal ints, or a negative one (GoveeBleInvalidValue)
        return False


def _catalogs(store: Any) -> dict[str, dict[str | None, _Catalog]]:
    """Every SKU's catalogs in the parsed ``store``: ``{SKU: {None: its catalog, key: its catalog at the
    set of that by_set key}}``, each a :data:`_Catalog` of its scenes in pool order (a scene the set plays
    another entry for keeps its place, under its name; one not listed there is left out) and its codes,
    the first name of each code. Raises ValueError unless ``store`` is an object of exactly:

    - ``pool``, a list of scene entries (:func:`_scene`), unique and sorted by content (the name, then the
      entry's JSON with sorted keys, as tools/fetch_scenes.py's ``layout`` writes it);
    - ``skus``, which maps each upper-case SKU to the strictly increasing indexes of at least one pool
      entry, no two of one name;
    - ``by_set``, which maps SKUs of ``skus`` to non-empty objects that map canonical scene-set keys
      (:func:`_set_key`) to non-empty objects that map names of that SKU's catalog to null or the index of
      a pool entry of that name other than the catalog's own;

    and every pool entry is some SKU's or some override's (no orphan). A pool entry that carries
    ``by_set`` of its own is refused (:func:`_validate_entry`), so an override never nests."""
    if not isinstance(store, dict) or set(store) != {"pool", "skus", "by_set"}:
        raise ValueError("the store is not an object of 'pool', 'skus' and 'by_set'")
    entries, skus, by_set = store["pool"], store["skus"], store["by_set"]
    if not isinstance(entries, list) or not isinstance(skus, dict) or not isinstance(by_set, dict):
        raise ValueError("the store's 'pool' is not a list, or its 'skus' or 'by_set' not an object")
    pool = [_scene(i, entry) for i, entry in enumerate(entries)]
    keys = [(entry["name"], json.dumps(entry, sort_keys=True, separators=(",", ":"))) for entry in entries]
    if any(a >= b for a, b in itertools.pairwise(keys)):
        raise ValueError("the pool is not unique and sorted by content")
    defaults: dict[str, dict[str, int]] = {}   # SKU -> {name: its catalog's pool index}
    for key, refs in skus.items():
        if not (isinstance(key, str) and _SKU_RE.fullmatch(key) and key == key.upper()):
            raise ValueError(f"{key!r} is not an upper-case SKU")
        if (not isinstance(refs, list) or not refs
                or not all(type(i) is int and 0 <= i < len(pool) for i in refs)
                or any(a >= b for a, b in itertools.pairwise(refs))):
            raise ValueError(f"{key}'s catalog is not strictly increasing indexes into the pool")
        defaults[key] = {pool[i].name: i for i in refs}
        if len(defaults[key]) != len(refs):
            raise ValueError(f"{key}'s catalog holds two scenes of one name")
    used = {i for refs in skus.values() for i in refs}
    for sku, sets in by_set.items():
        if sku not in defaults or not isinstance(sets, dict) or not sets:
            raise ValueError(f"by_set {sku!r} is not a non-empty object of a SKU of 'skus'")
        for at, names in sets.items():
            if not _canonical(at) or not isinstance(names, dict) or not names:
                raise ValueError(f"{sku}'s by_set {at!r} is not a sorted, comma-joined scene set of a "
                                 f"non-empty object")
            for name, i in names.items():
                if name not in defaults[sku]:
                    raise ValueError(f"{sku}'s by_set {at!r} names {name!r}, which its catalog lacks")
                if i is not None and not (type(i) is int and 0 <= i < len(pool) and pool[i].name == name
                                          and i != defaults[sku][name]):
                    raise ValueError(f"{sku}'s by_set {at!r} gives {name!r} no other pool entry of that name")
                if i is not None:
                    used.add(i)
    if len(used) != len(pool):
        raise ValueError(f"pool entry {min(set(range(len(pool))) - used)} is no SKU's or override's")
    out: dict[str, dict[str | None, _Catalog]] = {}
    for sku, own in defaults.items():
        out[sku] = {None: _catalog_of([pool[i] for i in own.values()])}
        for at, names in by_set.get(sku, {}).items():
            picked = (names.get(name, i) for name, i in own.items())
            out[sku][at] = _catalog_of([pool[i] for i in picked if i is not None])
    return out


def _catalog_of(scenes: list[Scene]) -> _Catalog:
    """The :data:`_Catalog` of ``scenes`` (no two of one name), in their order."""
    codes: dict[int, str] = {}
    for scene in scenes:
        codes.setdefault(scene.code, scene.name)
    return MappingProxyType({scene.name: scene for scene in scenes}), MappingProxyType(codes)


def _scene(i: int, entry: Any) -> Scene:
    """Pool entry ``i`` (a scene entry with its ``name``) as a Scene, once :func:`_validate_entry` takes it.
    Its values are taken as they are: nothing is coerced."""
    if not isinstance(entry, dict):
        raise ValueError(f"pool entry {i} is not an object")
    _validate_entry(entry.get("name"), entry)
    return Scene(
        entry["name"], entry["code"], entry.get("param"), entry.get("category"),
        entry.get("param_id"), entry.get("placeholder") is True,
        scene_type=entry.get("scene_type"), cmd_version=entry.get("cmd_version"),
        big_effect=entry.get("big_effect") is True,
    )


# A scene entry's typed keys: each one's type where it is not null (a key left out is null). An int is never
# a bool here.
_ENTRY_TYPES: Final[tuple[tuple[str, type], ...]] = (
    ("param", str), ("category", str), ("scene_name", str), ("param_id", int), ("scene_type", int),
    ("cmd_version", int), ("placeholder", bool), ("big_effect", bool),
)


def _validate_entry(name: object, entry: object) -> None:
    """Check one scene entry named ``name`` (the store's pool entry holds it as its ``name`` key, the tool's
    catalog as its key) as the loader takes it; raises ValueError where it fails. It is the one check of the
    loader (:func:`_scene`) and of tools/fetch_scenes.py's ``validate_entry``, so the loader refuses exactly
    what the tool refuses to write:

    - ``name`` is a non-empty string and ``entry`` an object;
    - ``code`` is an int from 0 to 0xFFFF, the scene_select effect the activation sends
      (``build.scene_activate``);
    - ``param``, ``category`` and ``scene_name`` (the sceneName the tool records for a scene stored apart)
      are strings, ``param_id``, ``scene_type`` and ``cmd_version`` ints, ``placeholder`` and ``big_effect``
      bools, or null (a key left out is null). Nothing is coerced: a code "7", 1.9 or true, a scene type "2"
      and a placeholder "no" are refused, as is a bool where an int goes;
    - a ``param`` that is not empty is strict base64, so a corrupt bundled blob fails at load, not at upload;
    - an entry with version ``rules`` is refused: the app lists that scene only on firmware its rules allow
      (devices.yaml scenes.list_filter), which the library doesn't model, so offered it would show on every
      one;
    - an entry with a ``by_set`` key is refused: the per-set entries are the store's ``by_set``, each a flat
      entry of its own, so none nests.

    Other keys are not read. A message names the key and the type, never the value (a blob is never quoted).
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"the scene name ({type(name).__name__}) is not a non-empty string")
    if not isinstance(entry, dict):
        raise ValueError(f"{name!r}: the entry is not an object")
    code = entry.get("code")
    if type(code) is not int or not 0 <= code <= _MAX_CODE:
        raise ValueError(f"{name!r}: 'code' must be an int from 0 to {_MAX_CODE}, got {type(code).__name__}")
    if entry.get("rules"):
        raise ValueError(f"{name!r} is gated by version rules, which the library can't follow")
    if "by_set" in entry:
        raise ValueError(f"{name!r}: an entry carries no 'by_set' (the store's by_set holds per-set entries)")
    for key, typ in _ENTRY_TYPES:
        value = entry.get(key)
        if value is not None and type(value) is not typ:
            raise ValueError(f"{name!r}: '{key}' must be {typ.__name__} or null, got {type(value).__name__}")
    param = entry.get("param")
    if param:
        try:
            base64.b64decode(param, validate=True)
        except ValueError as err:   # binascii.Error, or a character outside ASCII
            raise ValueError(f"{name!r}: 'param' is not valid base64: {err}") from err


def _catalog_key(sku: str) -> str:
    """The upper-case catalog key for ``sku``; GoveeBleInvalidValue if it is malformed."""
    key = sku.strip() if isinstance(sku, str) else ""
    if not _SKU_RE.fullmatch(key):
        raise GoveeBleInvalidValue(f"invalid SKU {sku!r}: expected 3-8 alphanumeric characters")
    return key.upper()


def load_scenes(sku: str, *, scene_versions: Collection[int] | None = None) -> dict[str, Scene]:
    """Return {scene_name: Scene} for a SKU, or {} if no catalog is bundled.

    ``scene_versions`` None (the default) gives the SKU's whole bundled catalog, the scenes the Govee
    app's scene list shows at the union of the SKU's scene sets. A set (normally
    :attr:`~govee_ble_local.devices.device.Device.scene_versions`, one of the SKU's scene sets) gives
    the catalog the app's list shows at that set (devices.yaml scenes.list_filter): a scene the app hides
    there is left out, and one it plays another lightEffect for keeps its name and holds that effect. The
    loader does not know the device table, so at a set the SKU never has it gives the whole catalog.

    Returns a fresh dict each call (Scene is frozen), so callers can't corrupt the cache.
    The first call (of any SKU) reads and checks the whole bundled store from disk, once, and builds the
    catalog of every set; every later call, at any set, is from memory. On an event loop use
    :func:`async_load_scenes` (or call this from an executor) for that first call. Raises
    GoveeBleInvalidValue for a malformed SKU or a ``scene_versions`` that is not a collection of
    non-negative ints, GoveeBleError for a missing, corrupt or unreadable store: a GoveeBleError of the
    first such failure (its message and cause), raised by every later call with no second read, except a
    read error of the process's state (too many open files, out of memory), after which the next call
    reads the store again.
    """
    key = _catalog_key(sku)
    at = None if scene_versions is None else _set_key(scene_versions)
    sets = _catalog(key)
    return dict(sets.get(at, sets[None])[0])


async def async_load_scenes(sku: str, *, scene_versions: Collection[int] | None = None) -> dict[str, Scene]:
    """:func:`load_scenes` without blocking the event loop.

    The first lookup (of any SKU) reads and checks the bundled store from disk; this runs
    that in a worker thread (later lookups are cached and return immediately)."""
    return await asyncio.to_thread(load_scenes, sku, scene_versions=scene_versions)


def scene_names_by_code(sku: str, *, scene_versions: Collection[int] | None = None) -> dict[int, str]:
    """Reverse index {code: name} for a SKU's catalog at ``scene_versions`` (as :func:`load_scenes`; first
    name wins).

    Built with the catalog, once; returns a fresh dict each call.
    """
    key = _catalog_key(sku)
    at = None if scene_versions is None else _set_key(scene_versions)
    sets = _catalog(key)
    return dict(sets.get(at, sets[None])[1])


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
