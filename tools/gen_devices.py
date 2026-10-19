#!/usr/bin/env python3
"""Generate src/govee_ble_local/_generated/devices.py from spec/devices.yaml (a build product).

    python tools/gen_devices.py               # -> src/govee_ble_local/_generated/devices.py
    python tools/gen_devices.py --out DIR     # -> DIR/devices.py

tools/gen_kaitai.sh runs it after the Kaitai classes, so every build (the in-tree backend), scripts/check.sh
and CI generate both. The module holds what the library reads of devices.yaml, as Python literals typed by
govee_ble_local/devices/_resolve.py: the mode_color_parse table, and each family that has a supported
client_profile row (clientRow `support`), with only those rows and their wire layers. Nothing in it is
prose, and the library needs no YAML parser at run time.

The projection is an allow-list over the schema (spec/devices.schema.json): at each definition the library
reads, the properties named in KEEP, each of which must be one the schema declares there; a definition KEEP
doesn't name is copied whole with every property its schema declares. So no key is dropped by its name
wherever it occurs. Before writing, the generator checks the BLE routes over the whole document (a SKU two
rows with support list is refused whatever their support), resolves every admitted row with the library's
own resolver (devices/_resolve.py, standard library only), which checks each value the library uses, and
checks each colour decode against govee_common.ksy, so a missing fact, or one the library can't serve,
fails here and never at import.

Build-time only: PyYAML, which the in-tree backend asks the build frontend for in a repository build
(get_requires_for_build_*). An sdist ships the generated module, so a build from it needs nothing.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import pprint
import sys
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = ROOT / "spec"
OUT = ROOT / "src" / "govee_ble_local" / "_generated"
RESOLVER = ROOT / "src" / "govee_ble_local" / "devices" / "_resolve.py"

# Schema definition -> the properties the library reads there, to serve them or to check that it can (a
# definition not listed is copied whole). A name added here gets its key in the matching TypedDict of
# devices/_resolve.py in the same change (mypy checks the generated literal against them).
_LAYER = ("forms", "counts", "group", "power", "mode", "mode_status")
_GATES = ("min_soft_version", "min_hard_version", "min_wifi_soft_version")
_ROW_FACTS = ("scene_versions", "kelvin_range", "brightness_range")
KEEP: dict[str, tuple[str, ...]] = {
    "#": ("mode_color_parse", "families"),
    "#/$defs/modeColorStrategy": ("sub_modes",),
    "#/$defs/family": ("id", "connectivity", "specs", "encryption", "assigned_goods_type", "client_profile"),
    "#/$defs/family/properties/specs": _ROW_FACTS,
    "#/$defs/assignedGoodsType": ("models", "goods_type"),
    "#/$defs/family/properties/client_profile": (
        "has_brightness", "color_scheme", "power_read", "power_live", "mode_color_parse",
        "mode_color_parse_status", "relay_power", "relay_on_mask", "advert_on_mask", "by_model",
        "by_goods_type", "wire"),
    "#/$defs/clientRow": (
        "models", "connectivity", "pacts", "support", "power_read", "power_live", "mode_color_parse",
        "mode_color_parse_status", "relay_power", "relay_on_mask", "advert_on_mask", "color_scheme",
        "color_functions", "capabilities", "zones", "account_lock", "scene_dialect", *_ROW_FACTS,
        "mode_reports_kelvin", "status_read", "connect_reads", "connect_writes", "power_resync", "ic_pieces",
        "by_pact", "by_firmware"),
    "#/$defs/clientModel": ("mode_color_parse", "mode_color_parse_status", "by_pact", "by_firmware"),
    "#/$defs/clientBranch": (
        "mode_color_parse", "mode_color_parse_status", "status_read", "capabilities", "connect_reads",
        *_ROW_FACTS),
    "#/$defs/clientFirmware": (*_GATES, "mode_color_parse", *_ROW_FACTS),
    "#/$defs/wire": (*_LAYER, "by_pact", "by_firmware", "by_goods_type", "by_model"),
    "#/$defs/wireLayer": (*_LAYER, "by_pact", "by_firmware"),
    "#/$defs/wireBranch": _LAYER,
    "#/$defs/wireFirmware": (*_LAYER, *_GATES),
}
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

    def node(self, pointer: str) -> dict[str, Any]:
        """The schema object at a JSON pointer ``#/a/b``."""
        out: Any = self.schema
        for part in pointer.removeprefix("#").strip("/").split("/") if pointer != "#" else ():
            out = out[part]
        return dict(out)

    def project(self, value: Any, pointer: str) -> Any:
        """``value`` (at schema ``pointer``) as Python literals: lists become tuples, and a mapping keeps the
        properties KEEP names at its definition, or all of them (the schema's, as the test suite validates
        devices.yaml against it) when KEEP names none."""
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
    """``doc`` cut to the families with an admitted row: only those rows, their by_model entries and their
    wire layers (the goodsType's and its models')."""
    families = []
    for fam in doc["families"]:
        cp = fam.get("client_profile") or {}
        rows = {gt: row for gt, row in (cp.get("by_goods_type") or {}).items()
                if row.get("support") in admitted}
        if not rows:
            continue
        models = {m for row in rows.values() for m in row.get("models") or ()}
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


def _check_decodes(table: dict[str, Any]) -> None:
    """Every colour decode the mode_color_parse table names is a govee_common::color_decode member."""
    common = yaml.safe_load((SPEC / "govee_common.ksy").read_text(encoding="utf-8"))
    members = {entry["id"] if isinstance(entry, dict) else entry
               for entry in common["enums"]["color_decode"].values()}
    for name, strategy in table.items():
        for sub_mode, decode in strategy["sub_modes"].items():
            if sub_mode not in SUB_MODES or decode not in members:
                raise GenError(f"mode_color_parse.{name}: {sub_mode} -> {decode} is not a mode_decode param "
                               "and color_decode member")


def load(raw: bytes) -> Any:
    """devices.yaml ``raw`` parsed (libyaml's safe loader where PyYAML has it)."""
    loader = getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader
    return yaml.load(raw, Loader=loader)  # noqa: S506  # nosec B506  # a safe loader


def project(doc: dict[str, Any], schema: dict[str, Any]) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """``(mode_color_parse table, families)``: devices.yaml ``doc`` through KEEP, checked by the library's
    resolver (the routes over the whole document, before the admission cut, then each admitted row) and
    against govee_common.ksy. GenError on a missing fact or one the library can't serve."""
    resolve = _resolver()
    try:
        resolve.routes(doc["families"])
        projected = Projector(schema).project(_supported(doc, resolve.ADMITTED), "#")
        table, families = projected["mode_color_parse"], projected["families"]
        _check_decodes(table)
        resolve.profiles(families, table)
    except resolve.SpecError as exc:
        raise GenError(str(exc)) from None
    return table, families


def render(raw: bytes, schema: dict[str, Any]) -> str:
    """The generated module's source for devices.yaml ``raw``."""
    table, families = project(load(raw), schema)
    fmt = pprint.PrettyPrinter(width=110, sort_dicts=False).pformat
    return "\n".join([
        '"""Generated from spec/devices.yaml by tools/gen_devices.py: do not edit (a build product, never '
        'committed)."""',
        "from __future__ import annotations",
        "",
        "from typing import TYPE_CHECKING, Final",
        "",
        "if TYPE_CHECKING:",
        "    from ..devices._resolve import Family, ModeColorStrategy",
        "",
        f"MODE_COLOR_PARSE: Final[dict[str, ModeColorStrategy]] = {fmt(table)}",
        f"FAMILIES: Final[tuple[Family, ...]] = {fmt(families)}",
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    args = ap.parse_args(argv)
    schema = json.loads((SPEC / "devices.schema.json").read_text(encoding="utf-8"))
    text = render((SPEC / "devices.yaml").read_bytes(), schema)
    compile(text, "devices.py", "exec")   # valid Python before it is written
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "devices.py").write_text(text, encoding="utf-8")
    print(f">> devices table: {args.out / 'devices.py'} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
