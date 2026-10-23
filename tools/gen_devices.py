#!/usr/bin/env python3
"""Generate src/govee_ble_local/_generated/devices.py from spec/devices.yaml and the catalogue snapshot.

    python tools/gen_devices.py               # -> src/govee_ble_local/_generated/devices.py
    python tools/gen_devices.py --out DIR     # -> DIR/devices.py

tools/gen_kaitai.sh runs it after the Kaitai classes, so every build (the in-tree backend), scripts/check.sh
and CI generate both. The module holds what the library reads of devices.yaml, as Python literals typed by
govee_ble_local/devices/_resolve.py: the mode_color_parse table, and each family that has a supported
client_profile row (clientRow `support`, any level: a modelled row is admitted on the caller's opt-in), with
only those rows and their wire layers. Nothing in it is prose, and the library needs no YAML parser at run
time.

The projection is an allow-list over the schema (spec/devices.schema.json): at each definition the library
reads, the properties named in KEEP, each of which must be one the schema declares there; a definition KEEP
doesn't name is copied whole with every property its schema declares. So no key is dropped by its name
wherever it occurs. Before writing, the generator checks the BLE routes over the whole document (a SKU
that a modelled row and a hardware_verified / app_traced row both list is refused), resolves every row with
support, of every level, with the library's own resolver (devices/_resolve.py, standard library only), which
checks each value the library uses, and checks each colour decode, each status_read TLV type and each
heartbeat command against govee_common.ksy, so a missing fact, or one the library can't serve, fails here and
never at import.

The rows also take the routes of the committed catalogue snapshot, spec/govee_catalog.json (the build reads
it and nothing fetched): _resolve.catalogue over the whole document, by the snapshot's precedence. A SKU no
row lists joins the BLE row of its catalogue goodsType (never goodsType 0), in the row's catalogue_models, a
key the generator adds (GENERATED): it takes the row's facts, at the row's support level capped at
app_traced, since the snapshot's goodsType is no bench check. One that a row with support lists at a
goodsType the snapshot contradicts, with no cited rewrite, leaves those rows (a row left with no SKUs is
dropped). The generator prints how many SKUs only the snapshot routes, and the review
list. It also prints the routes of the pact-conditioned assigned_goods_type items that no row carries yet
(_resolve.reassignment_review). The module's SCENE_CATALOGS are the SKUs of the scene store
(src/govee_ble_local/scenes/store.json's skus), the only ones the library's profiles offer scenes to.

Build-time only: PyYAML, which the in-tree backend asks the build frontend for in a repository build
(get_requires_for_build_*). An sdist ships the generated module, so a build from it needs nothing.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import pathlib
import pprint
import re
import sys
from collections.abc import Mapping
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = ROOT / "spec"
CATALOGUE = SPEC / "govee_catalog.json"
SCENE_STORE = ROOT / "src" / "govee_ble_local" / "scenes" / "store.json"
OUT = ROOT / "src" / "govee_ble_local" / "_generated"
RESOLVER = ROOT / "src" / "govee_ble_local" / "devices" / "_resolve.py"

# Schema definition -> the properties the library reads there, to serve them or to check that it can (a
# definition not listed is copied whole). A name added here gets its key in the matching TypedDict of
# devices/_resolve.py in the same change (mypy checks the generated literal against them). GENERATED names the
# keys the generator itself adds at a definition, which the schema must not declare there.
_LAYER = ("forms", "counts", "group", "power", "mode", "mode_status")
_GATES = ("min_soft_version", "min_hard_version", "min_wifi_soft_version")
_ROW_FACTS = ("scene_versions", "kelvin_range", "brightness_range")
KEEP: dict[str, tuple[str, ...]] = {
    "#": ("mode_color_parse", "families"),
    "#/$defs/modeColorStrategy": ("sub_modes",),
    "#/$defs/family": ("id", "connectivity", "specs", "encryption", "assigned_goods_type", "client_profile"),
    "#/$defs/family/properties/specs": _ROW_FACTS,
    "#/$defs/assignedGoodsType": ("models", "goods_type", "from_goods_type", "condition", "pact"),
    "#/$defs/family/properties/client_profile": (
        "has_brightness", "color_scheme", "power_read", "power_live", "mode_color_parse",
        "mode_color_parse_status", "relay_power", "relay_on_mask", "advert_on_mask", "by_model",
        "by_goods_type", "wire"),
    "#/$defs/clientRow": (
        "models", "connectivity", "pacts", "pact_required", "support", "session", "power_read",
        "power_write", "power_live", "mode_color_parse", "mode_color_parse_status", "relay_power",
        "relay_on_mask", "advert_on_mask", "color_scheme", "white_type", "color_temp_control",
        "color_functions", "capabilities", "segment_color_temp", "zones", "segment_brightness_groups",
        "account_lock", "empty_secret_check", "scene_dialect", *_ROW_FACTS, "mode_reports_kelvin",
        "kelvin_readback", "status_read", "device_info_reads", "connect_reads", "connect_writes", "heartbeat",
        "heartbeat_timing",
        "heartbeat_power", "heartbeat_reread", "power_resync", "ic_pieces", "post_status_reads",
        "readback_pieces", "light_num_pieces", "by_pact", "by_firmware"),
    "#/$defs/clientModel": ("mode_color_parse", "mode_color_parse_status", "kelvin_range", "brightness_range",
                            "scene_power_on", "connect_reads", "connect_writes", "ic_pieces", "support",
                            "by_pact", "by_firmware"),
    "#/$defs/clientBranch": (
        "mode_color_parse", "mode_color_parse_status", "status_read", "capabilities", "connect_reads",
        *_ROW_FACTS),
    "#/$defs/clientFirmware": (*_GATES, "compare", "mode_color_parse", *_ROW_FACTS),
    "#/$defs/wire": (*_LAYER, "by_pact", "by_firmware", "by_goods_type", "by_model"),
    "#/$defs/wireLayer": (*_LAYER, "by_pact", "by_firmware"),
    "#/$defs/wireBranch": _LAYER,
    "#/$defs/wireFirmware": (*_LAYER, *_GATES),
}
# catalogue_models: the SKUs only the catalogue snapshot routes to the row (_with_catalogue).
GENERATED: dict[str, tuple[str, ...]] = {"#/$defs/clientRow": ("catalogue_models",)}
# The colour sub-mode keys of a modeColorStrategy -> govee_profile mode_decode params (c02 .. c15).
SUB_MODES = frozenset({"0x02", "0x0b", "0x0d", "0x14", "0x15"})


class GenError(SystemExit):
    """The spec can't be generated: the message names the place and the fact."""

    def __init__(self, message: str) -> None:
        super().__init__(f"gen_devices: {message}")


class Projector:
    """devices.yaml through KEEP, walking the schema alongside the document."""

    def __init__(self, schema: dict[str, Any]) -> None:
        self.schema = schema
        for pointer, keys in KEEP.items():
            props = self.node(pointer).get("properties") or {}
            missing = [k for k in keys if k not in props]
            if missing:
                raise GenError(f"KEEP lists {missing} at {pointer}, which the schema does not declare there")
        for pointer, keys in GENERATED.items():
            declared = [k for k in keys if k in (self.node(pointer).get("properties") or {})]
            if declared:
                raise GenError(f"GENERATED lists {declared} at {pointer}, which the schema declares there")

    def node(self, pointer: str) -> dict[str, Any]:
        """The schema object at a JSON pointer ``#/a/b``."""
        out: Any = self.schema
        for part in pointer.removeprefix("#").strip("/").split("/") if pointer != "#" else ():
            out = out[part]
        return dict(out)

    def project(self, value: Any, pointer: str) -> Any:
        """``value`` (at schema ``pointer``) as Python literals: lists become tuples, and a mapping keeps the
        properties KEEP names at its definition, or all of them (the schema's, as the test suite validates
        devices.yaml against it) when KEEP names none, and the keys GENERATED names there."""
        node = self.node(pointer)
        while "$ref" in node:
            pointer = node["$ref"]
            node = self.node(pointer)
        if isinstance(value, list):
            items = node.get("items")
            return tuple(self.project(v, f"{pointer}/items") if isinstance(items, dict) else _plain(v)
                         for v in value)
        if not isinstance(value, dict):
            return _plain(value)
        props = node.get("properties") or {}
        extra = node.get("additionalProperties")
        keys = [k for k in KEEP[pointer] if k in value] if pointer in KEEP else list(value)
        keys += [k for k in GENERATED.get(pointer, ()) if k in value]
        out = {}
        for key in keys:
            if not isinstance(key, str):
                raise GenError(f"{pointer}: key {key!r} is not a string (quote it in devices.yaml)")
            if key in props:
                out[key] = self.project(value[key], f"{pointer}/properties/{key}")
            elif isinstance(extra, dict):
                out[key] = self.project(value[key], f"{pointer}/additionalProperties")
            else:
                out[key] = _plain(value[key])
        return out


def _plain(value: Any) -> Any:
    """A YAML value as Python literals (lists as tuples)."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return tuple(_plain(v) for v in value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise GenError(f"{type(value).__name__} {value!r} is not a devices.yaml value type")


def _supported(doc: dict[str, Any], admitted: frozenset[str]) -> dict[str, Any]:
    """``doc`` cut to the families with a row whose support is in ``admitted``: only those rows, their
    by_model entries and their wire layers (the goodsType's and its models')."""
    families = []
    for fam in doc["families"]:
        cp = fam.get("client_profile") or {}
        rows = {gt: row for gt, row in (cp.get("by_goods_type") or {}).items()
                if row.get("support") in admitted}
        if not rows:
            continue
        models = {m for row in rows.values() for key in ("models", "catalogue_models")
                  for m in row.get(key) or ()}
        cp = {**cp, "by_goods_type": rows}
        if "by_model" in cp:
            cp["by_model"] = {m: v for m, v in cp["by_model"].items() if m in models}
        if "wire" in cp:
            wire = dict(cp["wire"])
            for key, keep in (("by_goods_type", rows), ("by_model", models)):
                if key in wire:
                    wire[key] = {k: v for k, v in wire[key].items() if k in keep}
            cp["wire"] = wire
        families.append({**fam, "client_profile": cp})
    return {**doc, "families": families}


def _resolver() -> Any:
    """devices/_resolve.py, loaded from its file (the package itself needs this module to import)."""
    spec = importlib.util.spec_from_file_location("_govee_ble_local_resolve", RESOLVER)
    if spec is None or spec.loader is None:
        raise GenError(f"cannot load {RESOLVER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module   # dataclass / TypedDict machinery looks the module up
    spec.loader.exec_module(module)
    return module


def _members(enum: str) -> set[str]:
    """The member ids of govee_common.ksy's enum ``enum``."""
    common = yaml.safe_load((SPEC / "govee_common.ksy").read_text(encoding="utf-8"))
    return {entry["id"] if isinstance(entry, dict) else entry for entry in common["enums"][enum].values()}


def _check_decodes(table: dict[str, Any]) -> None:
    """Every colour decode the mode_color_parse table names is a govee_common::color_decode member."""
    members = _members("color_decode")
    for name, strategy in table.items():
        for sub_mode, decode in strategy["sub_modes"].items():
            if sub_mode not in SUB_MODES or decode not in members:
                raise GenError(f"mode_color_parse.{name}: {sub_mode} -> {decode} is not a mode_decode param "
                               "and color_decode member")


def _check_status_reads(families: tuple[dict[str, Any], ...]) -> None:
    """Every TLV type a row's status_read (or a pact branch's) requests is a govee_common::command member,
    whose code the library sends (devices/profile.py)."""
    members = _members("command")
    for fam in families:
        for gt, row in fam["client_profile"]["by_goods_type"].items():
            for read in (row.get("status_read") or (), *(b.get("status_read") or () for b in
                                                          (row.get("by_pact") or {}).values())):
                unknown = sorted(set(read) - members)
                if unknown:
                    raise GenError(f"{fam['id']} goodsType {gt}: status_read {unknown} not in govee_common "
                                   "enum command")


def _check_heartbeats(families: tuple[dict[str, Any], ...], beats: tuple[str, ...]) -> None:
    """Every row's heartbeat is a govee_common::command member, whose code the library sends
    (devices/profile.py), and one of ``beats``, the keep-alive reads a screen sends (the resolver's
    ``HEARTBEATS``, which devices/profile.py also checks), or ``none`` (no keep-alive: the resolver serves it
    on a per_command row only)."""
    members = _members("command")
    for fam in families:
        for gt, row in fam["client_profile"]["by_goods_type"].items():
            beat = row.get("heartbeat")
            if beat is not None and beat != "none" and (beat not in members or beat not in beats):
                raise GenError(f"{fam['id']} goodsType {gt}: heartbeat {beat!r} is not a keep-alive read "
                               f"(govee_common enum command, one of {sorted(beats)})")


def load(raw: bytes) -> Any:
    """devices.yaml ``raw`` parsed (libyaml's safe loader where PyYAML has it)."""
    loader = getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader
    return yaml.load(raw, Loader=loader)  # noqa: S506  # nosec B506  # a safe loader


# What the snapshot holds (tools/fetch_catalog.py writes it): its prose, the AppVersion header it was
# fetched with (the endpoint's answer depends on it) and its date, and per SKU the goodsType and ic only, so
# no product name, image or URL gets in.
_SNAPSHOT_KEYS = frozenset({"app_version", "doc", "doc-ref", "fetched", "skus", "source_app"})
_ENTRY_KEYS = frozenset({"goodsType", "ic"})


def _iso_date(text: object) -> bool:
    """Whether ``text`` is a calendar date written YYYY-MM-DD."""
    if not isinstance(text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def snapshot(path: pathlib.Path = CATALOGUE) -> dict[str, int]:
    """The catalogue snapshot's ``{SKU: goodsType}`` (its ``ic`` is read nowhere: _resolve's module doc).
    GenError on a missing or malformed file, a top-level key outside :data:`_SNAPSHOT_KEYS`, an
    ``app_version`` missing or not in the app's version form (7.5.20, 7.7.02), a ``fetched`` missing or not
    an ISO date (2026-10-18, as tools/fetch_catalog.py checks and writes it), a SKU that isn't 3-8
    upper-case alphanumerics (as tools/fetch_catalog.py writes them), or an entry whose keys are not exactly
    goodsType and ic, each a non-negative int."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        skus = data["skus"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GenError(f"{path.name}: {exc!r}") from None
    extra = sorted(set(data) - _SNAPSHOT_KEYS)
    if extra:
        raise GenError(f"{path.name}: keys {extra} (it holds only {sorted(_SNAPSHOT_KEYS)})")
    version = data.get("app_version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d{2}", version):
        raise GenError(f"{path.name}: app_version {version!r} is not the AppVersion it was fetched with "
                       "(such as 7.5.20)")
    fetched = data.get("fetched")
    if not _iso_date(fetched):
        raise GenError(f"{path.name}: fetched {fetched!r} is not the date it was fetched on "
                       "(such as 2026-10-18)")
    if not isinstance(skus, dict) or not skus:
        raise GenError(f"{path.name}: no skus")
    out: dict[str, int] = {}
    for sku, entry in skus.items():
        keys, values = (set(entry), entry.values()) if isinstance(entry, dict) else (set(), ())
        if (not re.fullmatch(r"[A-Z0-9]{3,8}", sku) or keys != _ENTRY_KEYS
                or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values)):
            raise GenError(f"{path.name}: {sku!r} is no SKU with exactly a non-negative int goodsType and ic")
        out[sku] = entry["goodsType"]
    return out


def scene_catalogs(path: pathlib.Path = SCENE_STORE) -> frozenset[str]:
    """The SKUs the scene store has a catalog for (``scenes/store.json``'s ``skus`` keys, which
    tools/fetch_scenes.py writes): the only ones a profile offers scenes to (_resolve's module doc). GenError
    on a store without an object of them (the library's loader checks the rest)."""
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GenError(f"{path.name}: {exc!r}") from None
    index = store.get("skus") if isinstance(store, dict) else None
    if not isinstance(index, dict) or not all(re.fullmatch(r"[A-Z0-9]{3,8}", k) for k in index):
        raise GenError(f"{path.name}: no skus object keyed by upper-case SKUs")
    return frozenset(index)


def _with_catalogue(doc: dict[str, Any], routed: Any) -> dict[str, Any]:
    """``doc`` with the catalogue's routes (``_resolve.Catalogue``): each unrouted SKU out of the rows with
    support, and each added one in its row's ``catalogue_models`` (GENERATED: _resolve serves those SKUs with
    the row's facts, at its support level capped at app_traced). A row left with neither routes nothing and is
    dropped."""
    families = []
    for fam in doc["families"]:
        rows = (fam.get("client_profile") or {}).get("by_goods_type") or {}
        kept: dict[str, Any] = {}
        for gt, row in rows.items():
            own = list(row.get("models") or ())
            models = [m for m in own if "support" not in row or m.upper() not in routed.unrouted]
            added = list(routed.added.get((fam["id"], gt), ()))
            if models == own and not added:
                kept[gt] = row
            elif models or added:
                kept[gt] = {**row, "models": models, **({"catalogue_models": added} if added else {})}
        families.append({**fam, "client_profile": {**fam["client_profile"], "by_goods_type": kept}}
                        if kept != rows else fam)
    return {**doc, "families": families}


def project(doc: dict[str, Any], schema: dict[str, Any], catalogue: Mapping[str, int] | None = None
            ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """``(mode_color_parse table, families)``: devices.yaml ``doc`` with the routes of ``catalogue`` (SKU ->
    goodsType; default the committed snapshot), through KEEP, checked by the library's resolver (the routes
    over the whole document, its per-SKU entries with the catalogue's routes, then each row with support, of
    every level) and against govee_common.ksy. GenError on a missing fact or one the library can't serve."""
    resolve = _resolver()
    try:
        resolve.routes(doc["families"])
        routed = resolve.catalogue(doc["families"], snapshot() if catalogue is None else catalogue)
        resolve.sku_entries(doc["families"], routed)   # before the cut, which drops a stray entry silently
        doc = _with_catalogue(doc, routed)
        projected = Projector(schema).project(_supported(doc, resolve.LEVELS), "#")
        table, families = projected["mode_color_parse"], projected["families"]
        _check_decodes(table)
        _check_status_reads(families)
        _check_heartbeats(families, resolve.HEARTBEATS)
        resolve.profiles(families, table, resolve.LEVELS)   # modelled rows too: the opt-in builds them
    except resolve.SpecError as exc:
        raise GenError(str(exc)) from None
    return table, families


def render(raw: bytes, schema: dict[str, Any]) -> tuple[str, list[str]]:
    """The generated module's source for devices.yaml ``raw``, and the lines :func:`main` reports: the
    committed catalogue snapshot's routes and review list, and the routes of the pact-conditioned
    assigned_goods_type items that no row carries yet."""
    doc, catalogue = load(raw), snapshot()
    table, families = project(doc, schema, catalogue)
    resolve = _resolver()
    routed = resolve.catalogue(doc["families"], catalogue)
    reassigned = resolve.reassignment_review(doc["families"])
    paired = resolve.bind_review(doc["families"])
    only = frozenset(sku for skus in routed.added.values() for sku in skus)
    fmt = pprint.PrettyPrinter(width=110, sort_dicts=False).pformat
    return "\n".join([
        '"""Generated from spec/devices.yaml, spec/govee_catalog.json and scenes/store.json by '
        'tools/gen_devices.py: do not edit (a build product, never committed)."""',
        "from __future__ import annotations",
        "",
        "from typing import TYPE_CHECKING, Final",
        "",
        "if TYPE_CHECKING:",
        "    from ..devices._resolve import Family, ModeColorStrategy",
        "",
        f"MODE_COLOR_PARSE: Final[dict[str, ModeColorStrategy]] = {fmt(table)}",
        f"FAMILIES: Final[tuple[Family, ...]] = {fmt(families)}",
        "# The SKUs with a bundled scene catalog (src/govee_ble_local/scenes/store.json): the only ones a"
        " profile offers scenes to.",
        f"SCENE_CATALOGS: Final[frozenset[str]] = frozenset({fmt(tuple(sorted(scene_catalogs())))})",
        "",
    ]), [f">> catalogue snapshot: {len(only)} SKUs routed that no devices.yaml row lists, "
         f"{len(routed.review)} for review", *(f"   review: {line}" for line in routed.review),
         f">> pact-conditioned reassignments: {len(reassigned)} for review",
         *(f"   review: {line}" for line in reassigned),
         f">> exact-pact pairing writes: {len(paired)} for review",
         *(f"   review: {line}" for line in paired)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    args = ap.parse_args(argv)
    schema = json.loads((SPEC / "devices.schema.json").read_text(encoding="utf-8"))
    raw = (SPEC / "devices.yaml").read_bytes()
    text, report = render(raw, schema)
    compile(text, "devices.py", "exec")   # valid Python before it is written
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "devices.py").write_text(text, encoding="utf-8")
    print(f">> devices table: {args.out / 'devices.py'} ({len(text)} bytes)")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
