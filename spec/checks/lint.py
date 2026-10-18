"""Suite S structural lints for a spec dir (CLEAN_SPEC_DESIGN 1.6, as cut in section 10).

usage: python lint.py SPEC_DIR

Reads SPEC_DIR/*.ksy, devices.schema.json, devices.yaml, firmware.yaml and firmware.schema.json;
writes nothing. Needs PyYAML and jsonschema.

  meta     meta id == file stem; license MIT; ks-version 0.11; endian and bit-endian set;
           every import is the bare stem of another SPEC_DIR/*.ksy.
  banned   _root / _parent in any expression (doc text is skipped; an id or a switch case
           named doc or title is not); io / pos / process keys on an attribute;
           `type: io` params; meta ks-opaque-types.
  roots    the tops are exactly TOPS, each doc starting with its label: `STANDALONE (` for
           the six ROOTS. No nested type is labelled STANDALONE. A CONTEXT top has no seq
           or instances, no other CONTEXT-file type reads bytes (a seq or a non-value
           instance), and a LIBRARY top has no seq, params or instances. Every used type
           that reads bytes is read from a root through seq / instance / switch-case types,
           not only through params. No type name carries a typed / safe / sized /
           frame(s|d) twin word.
  reach    every type is used (seq, instance, param or switch-case type), transitively,
           from a STANDALONE top or a CONTEXT / LIBRARY top; no orphans.
  enums    every enum is applied by an `enum:` key; no enum has fewer than two members.
  schema   a string enum under any devices.schema.json `wire` property is named like a
           govee_profile selector param and lists that enum's ids (the value-0 id may be
           left out); every govee_profile selector enum except color_decode is listed there;
           color_decode's ids minus not_decoded equal the schema's mode_color_decode key
           enum and devices.yaml's mode_color_decode keys.
  doc-ref  each doc-ref is `path.(java|c|kt|smali):N[-M][, N...]` or `IMAGE name @0xADDR`.
  firmware firmware.yaml validates against firmware.schema.json. Every type path, node field,
           enum literal, match path, route, state name, let, push, event arg and expression
           name resolves against the .ksy types and the image; the profile's model and
           goods_type are a devices.yaml client_profile and its forms are govee_profile
           selectors; doc-refs are in the doc-ref form.

Exits 1 on any error.
"""

import ast
import importlib
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

ROOTS = {"govee_ble_frame", "govee_status_stream", "govee_upload", "govee_manufacturer_data",
         "govee_scan_record", "govee_bgc_info"}
TOPS = {**dict.fromkeys(ROOTS, "STANDALONE ("), "govee_state": "SHARED PAYLOAD",
        "govee_profile": "CONTEXT", "govee_common": "LIBRARY"}
TOP_LABELS = ("STANDALONE (", "SHARED PAYLOAD", "CONTEXT", "LIBRARY")
SOURCE_LABELS = ("STANDALONE (", "CONTEXT", "LIBRARY")
NO_BYTES = {"CONTEXT": ("seq", "instances"), "LIBRARY": ("seq", "params", "instances")}
TWIN = re.compile(r"(^|_)(typed|safe|sized|frames?|framed)\d*(_|$)")
DOC_KEYS = {"doc", "doc-ref", "-orig-id", "title"}
BUILTIN = re.compile(r"([us][1248]|f[48])(le|be)?|b\d+(le|be)?|str|strz|bool|bytes|struct|any|io")
REF_FILE = re.compile(r"[\w$./-]+\.(java|c|kt|smali):\d+(-\d+)?(,\s?\d+(-\d+)?)*")
REF_ADDR = re.compile(r"H[0-9A-Z]{4}(/H[0-9A-Z]{4})? [\w.'<>-]+ @0x[0-9a-f]{6,8}(-0x[0-9a-f]{6,8})?")

Path_ = tuple[str, ...]
EnumKey = tuple[Path_, str]
errors: list[str] = []


def err(msg: str) -> None:
    if msg not in errors:
        errors.append(msg)


def name(path: Path_) -> str:
    return "::".join(path)


def attrs(t: dict[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    yield from (("seq", str(a.get("id")), a) for a in t.get("seq") or [])
    yield from (("instances", str(k), a) for k, a in (t.get("instances") or {}).items())
    yield from (("params", str(a.get("id")), a) for a in t.get("params") or [])


def type_refs(a: dict[str, Any]) -> Iterator[str]:
    t = a.get("type")
    for v in (t.get("cases") or {}).values() if isinstance(t, dict) else [t]:
        if isinstance(v, str):
            ref = v.split("(", 1)[0].strip().removesuffix("[]")
            if not BUILTIN.fullmatch(ref):
                yield ref


def resolve(ref: str, scope: Path_, types: dict[Path_, Any]) -> Path_ | None:
    parts = tuple(ref.split("::"))
    for i in range(len(scope), -1, -1):
        cand = (*scope[:i], *parts)
        if cand in types:
            return cand
    return None


def resolve_enum(ref: str, scope: Path_, types: dict[Path_, Any]) -> EnumKey | None:
    *owner, enum = ref.split("::")
    for i in range(len(scope), -1, -1):
        cand = (*scope[:i], *owner)
        if cand in types and enum in (types[cand].get("enums") or {}):
            return cand, enum
    return None


def code_strings(node: Any, in_cases: bool = False) -> Iterator[str]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield str(k)
            if in_cases or k not in DOC_KEYS or isinstance(v, dict):
                yield from code_strings(v, k == "cases")
    elif isinstance(node, list):
        for v in node:
            yield from code_strings(v)
    elif isinstance(node, str):
        yield node


def doc_refs(node: Any) -> Iterator[Any]:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "doc-ref":
                yield from v if isinstance(v, list) else [v]
            else:
                yield from doc_refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from doc_refs(v)


def parses(t: dict[str, Any]) -> bool:
    """A type reads bytes: it has a seq, or an instance that is not a `value:` instance."""
    return bool(t.get("seq")) or any("value" not in a for a in (t.get("instances") or {}).values())


def index_types(t: dict[str, Any], path: Path_, types: dict[Path_, Any]) -> None:
    types[path] = t
    for sub, st in (t.get("types") or {}).items():
        index_types(st, (*path, sub), types)


def check_file(f: Path, y: dict[str, Any]) -> None:
    meta = y.get("meta") or {}
    if meta.get("id") != f.stem:
        err(f"{f.name}: meta id {meta.get('id')!r} != file stem {f.stem!r}")
    if meta.get("license") != "MIT":
        err(f"{f.name}: meta license {meta.get('license')!r}, want MIT")
    if str(meta.get("ks-version")) != "0.11":
        err(f"{f.name}: meta ks-version {meta.get('ks-version')!r}, want '0.11'")
    for k in ("endian", "bit-endian"):
        if k not in meta:
            err(f"{f.name}: meta lacks {k}")
    for imp in meta.get("imports") or []:
        if "/" in str(imp) or not (f.parent / f"{imp}.ksy").is_file():
            err(f"{f.name}: import {imp!r} is not another spec .ksy (bare stem)")
    if "ks-opaque-types" in meta:
        err(f"{f.name}: banned meta ks-opaque-types")
    for s in code_strings({k: v for k, v in y.items() if k != "meta"}):
        if re.search(r"\b_(root|parent)\b", s):
            err(f"{f.name}: banned _root/_parent: {s.strip()[:70]}")
    for r in doc_refs(y):
        if not (isinstance(r, str) and (REF_FILE.fullmatch(r) or REF_ADDR.fullmatch(r))):
            err(f"{f.name}: doc-ref format: {r!r}")
    if not str(y.get("doc", "")).startswith(TOP_LABELS):
        err(f"{f.name}: top-level doc lacks a role label {TOP_LABELS}")


def check_types(types: dict[Path_, Any]) -> None:
    edges: dict[Path_, set[Path_]] = {p: set() for p in types}
    reads: dict[Path_, set[Path_]] = {p: set() for p in types}
    applied: set[EnumKey] = set()
    for p, t in types.items():
        if len(p) > 1 and str(t.get("doc", "")).startswith("STANDALONE"):
            err(f"{name(p)}: nested type labelled STANDALONE (only file tops are entry points)")
        if TWIN.search(name(p).rsplit("::", 1)[-1]) and p != ("govee_ble_frame",):
            err(f"{name(p)}: typed / safe / sized / frame twin name (one canonical path per message)")
        for kind, aid, a in attrs(t):
            where = f"{name(p)}/{kind}/{aid}"
            for k in ("io", "pos", "process"):
                if k in a:
                    err(f"{where}: banned key {k}")
            if kind == "params" and a.get("type") == "io":
                err(f"{where}: banned `type: io` param")
            for ref in type_refs(a):
                r = resolve(ref, p, types)
                if r is None:
                    err(f"{where}: unresolved type {ref}")
                else:
                    edges[p].add(r)
                    if kind != "params":
                        reads[p].add(r)
            if isinstance(a.get("enum"), str):
                e = resolve_enum(a["enum"], p, types)
                if e is None:
                    err(f"{where}: unresolved enum {a['enum']}")
                else:
                    applied.add(e)
    tops = {p: t for p, t in types.items() if len(p) == 1}
    for top in sorted(set(TOPS) - {p[0] for p in tops}):
        err(f"missing top {top}")
    for p, t in tops.items():
        want = TOPS.get(p[0])
        if want is None:
            err(f"{p[0]}: not one of the nine tops {sorted(TOPS)} (a new top needs the user's approval)")
        elif not str(t.get("doc", "")).startswith(want):
            err(f"{p[0]}: top doc must start with {want!r}")
    for p, t in types.items():
        label = next((lb for lb in NO_BYTES if str(types[p[:1]].get("doc", "")).startswith(lb)), "")
        if len(p) == 1:
            for k in NO_BYTES.get(label, ()):
                if t.get(k):
                    err(f"{name(p)}: a {label} top has {k} (it is not a byte source)")
        elif label == "CONTEXT" and parses(t):
            err(f"{name(p)}: a CONTEXT-file type reads bytes (context is params only)")
    seen: list[Path_] = [p for p, t in tops.items() if str(t.get("doc", "")).startswith(SOURCE_LABELS)]
    reached: set[Path_] = set(seen)
    while seen:
        for q in edges[seen.pop()] - reached:
            reached.add(q)
            seen.append(q)
    for p in sorted(set(types) - reached):
        err(f"{name(p)}: orphan type (not reachable from a STANDALONE, CONTEXT or LIBRARY top)")
    read: set[Path_] = {(r,) for r in ROOTS if (r,) in types}
    todo = list(read)
    while todo:
        for q in reads[todo.pop()] - read:
            read.add(q)
            todo.append(q)
    for p in sorted(reached - read):
        if parses(types[p]):
            err(f"{name(p)}: reads bytes but no root reads it (a param type reads no bytes)")
    for p, t in types.items():
        for e, members in (t.get("enums") or {}).items():
            if len(members or {}) < 2:
                err(f"{name(p)}::{e}: enum with fewer than two members")
            if (p, e) not in applied:
                err(f"{name(p)}::{e}: enum never applied by an `enum:` key")


def enum_ids(types: dict[Path_, Any], key: EnumKey) -> tuple[list[str], str | None]:
    members = types[key[0]]["enums"][key[1]]
    ids = {v: m["id"] if isinstance(m, dict) else m for v, m in members.items()}
    return list(ids.values()), ids.get(0)


def check_schema(spec: Path, types: dict[Path_, Any]) -> None:
    sp, dp = spec / "devices.schema.json", spec / "devices.yaml"
    if ("govee_profile",) not in types:
        err("govee_profile.ksy missing")
        return
    if not sp.is_file() or not dp.is_file():
        err(f"{sp.name} or {dp.name} missing")
        return
    schema = json.loads(sp.read_text())

    def deref(node: Any) -> Any:
        hops = 0
        while isinstance(node, dict) and str(node.get("$ref", "")).startswith("#/") and hops < 32:
            target: Any = schema
            for part in node["$ref"][2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            node, hops = target, hops + 1
        return node

    selectors: dict[str, set[EnumKey]] = {}

    def walk_params(p: Path_, stack: tuple[Path_, ...]) -> None:
        for a in types[p].get("params") or []:
            if isinstance(a.get("enum"), str) and "id" in a:
                e = resolve_enum(a["enum"], p, types)
                if e:
                    selectors.setdefault(a["id"], set()).add(e)
            for ref in type_refs(a):
                r = resolve(ref, p, types)
                if r and r not in stack:
                    walk_params(r, (*stack, r))

    walk_params(("govee_profile",), (("govee_profile",),))

    def find_wire(node: Any) -> Iterator[Any]:
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict) and "wire" in props:
                yield props["wire"]
            for v in node.values():
                yield from find_wire(v)
        elif isinstance(node, list):
            for v in node:
                yield from find_wire(v)

    def enum_leaves(node: Any, leaf: str, stack: tuple[int, ...]) -> Iterator[tuple[str, list[Any]]]:
        node = deref(node)
        if not isinstance(node, dict) or id(node) in stack:
            return
        stack = (*stack, id(node))
        if isinstance(node.get("enum"), list):
            yield leaf, node["enum"]
        for k, v in (node.get("properties") or {}).items():
            yield from enum_leaves(v, k, stack)
        for k in ("items", "additionalProperties"):
            yield from enum_leaves(node.get(k), leaf, stack)
        for k in ("allOf", "anyOf", "oneOf"):
            for v in node.get(k) or []:
                yield from enum_leaves(v, leaf, stack)

    wires = list(find_wire(schema))
    if not wires:
        err(f"{sp.name}: no `wire` property (client_profile.wire)")
    listed: set[EnumKey] = set()
    for w in wires:
        for leaf, values in enum_leaves(w, "wire", ()):
            if leaf not in selectors:
                err(f"{sp.name}: wire enum {leaf!r} is not a govee_profile selector param")
            for key in selectors.get(leaf, ()):
                ids, zero = enum_ids(types, key)
                if set(values) not in (set(ids), set(ids) - {zero}):
                    err(f"{sp.name}: wire {leaf} enum != {name(key[0])}::{key[1]} "
                        f"(missing {sorted(set(ids) - set(values) - {zero})}, "
                        f"extra {sorted(set(map(str, values)) - set(ids))})")
                listed.add(key)
    color = None
    for key in sorted({k for keys in selectors.values() for k in keys}):
        if key[1] == "color_decode":
            color = key
        elif wires and key not in listed:
            err(f"{sp.name}: govee_profile selector enum {name(key[0])}::{key[1]} is not listed under wire")
    if color is None:
        err("govee_profile has no color_decode selector")
        return
    want = set(enum_ids(types, color)[0]) - {"not_decoded"}
    mcd = deref((schema.get("properties") or {}).get("mode_color_decode"))
    names = deref((mcd or {}).get("propertyNames")) if isinstance(mcd, dict) else None
    got = set((names or {}).get("enum") or []) if isinstance(names, dict) else set()
    if got != want:
        err(f"{sp.name}: mode_color_decode key enum != color_decode minus not_decoded "
            f"(missing {sorted(want - got)}, extra {sorted(got - want)})")
    keys = set((yaml.safe_load(dp.read_text()) or {}).get("mode_color_decode") or {})
    if keys != want:
        err(f"{dp.name}: mode_color_decode keys != color_decode minus not_decoded "
            f"(missing {sorted(want - keys)}, extra {sorted(keys - want)})")


FW_FUNCS = {"len", "range", "bytes", "random_bytes", "leading", "decode"}
MERGE_TAG = "tag:yaml.org,2002:merge"
FW_NODES = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.Store, ast.Attribute, ast.Subscript,
            ast.Slice, ast.List, ast.Tuple, ast.Dict, ast.UnaryOp, ast.USub, ast.Invert, ast.Not, ast.BinOp,
            ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod, ast.LShift, ast.RShift, ast.BitAnd, ast.BitOr,
            ast.BitXor, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt,
            ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot, ast.IfExp, ast.ListComp, ast.comprehension,
            ast.Call)
FRAME: Path_ = ("govee_ble_frame",)
ECHO: Path_ = ("govee_common", "echo")
SIM_EVENTS = {"connect", "disconnect", "write", "tick"}
CIPHER_KEYS = {"psk", "session"}
NUMERIC = re.compile(r"([us][1248]|f[48])(le|be)?|b\d+(le|be)?|bool")
Match = dict[str, Any]


class UniqueKeyLoader(yaml.SafeLoader):
    """A SafeLoader that rejects a mapping with a repeated key (safe_load keeps the last one)."""


def _unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode) -> Any:
    if any(k.tag == MERGE_TAG for k, _ in node.value):
        raise yaml.constructor.ConstructorError(None, None, "YAML merge keys (<<) are not allowed",
                                                node.start_mark)
    keys = [loader.construct_object(k) for k, _ in node.value]
    odd = [k for k in keys if not isinstance(k, str)]
    if odd:
        raise yaml.constructor.ConstructorError(None, None, f"keys must be names, not {odd} (YAML 1.1 reads "
                                                "on/off/yes/no as booleans)", node.start_mark)
    dups = sorted({str(k) for k in keys if keys.count(k) > 1})
    if dups:
        raise yaml.constructor.ConstructorError(None, None, f"duplicate keys {dups}", node.start_mark)
    return loader.construct_mapping(node)


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


class Firmware:
    """Checks one firmware.yaml image against the .ksy types and devices.yaml."""

    def __init__(self, types: dict[Path_, Any], where: str, devices: dict[str, Any]) -> None:
        self.types = types
        self.where = where
        self.devices = devices
        self.enums: dict[str, list[EnumKey]] = {}
        for p, t in types.items():
            for e in t.get("enums") or {}:
                self.enums.setdefault(e, []).append((p, e))
        body: dict[str, Any] = next((a for a in self.seq(FRAME) if a.get("id") == "body"), {})
        self.routes = set(self.ids((FRAME, "route")))
        self.route_body: dict[str, Path_] = {}
        for case, ref in ((body.get("type") or {}).get("cases") or {}).items():
            r = resolve(str(ref).split("(", 1)[0].strip(), FRAME, types)
            if r is not None:
                self.route_body[str(case).split("::")[-1]] = r
        self.im: dict[str, Any] = {}
        self.forms: dict[str, Any] = {}
        self.state: dict[str, Any] = {}
        self.pushes: set[str] = set()

    def ids(self, key: EnumKey) -> list[str]:
        members = self.types[key[0]].get("enums", {}).get(key[1]) or {}
        return [str(m["id"] if isinstance(m, dict) else m) for m in members.values()]

    def seq(self, tp: Path_) -> list[dict[str, Any]]:
        return list(self.types[tp].get("seq") or [])

    def attr(self, tp: Path_, aid: str) -> dict[str, Any] | None:
        found = next((a for a in self.seq(tp) if a.get("id") == aid), None)
        instances = self.types[tp].get("instances") or {}
        return found if found is not None or aid not in instances else dict(instances[aid])

    def targets(self, tp: Path_, a: dict[str, Any]) -> list[Path_]:
        return [r for ref in type_refs(a) if (r := resolve(ref, tp, self.types)) is not None]

    def cases(self, tp: Path_, a: dict[str, Any], match: Match | None, prefix: list[str]) -> list[Path_]:
        """The types a field can hold; for a switch on a field the answer matches, only that case."""
        sw = a.get("type")
        if isinstance(sw, dict) and match:
            want = match.get(".".join([*prefix, str(sw.get("switch-on", ""))]))
            refs = [str(ref).split("(", 1)[0].strip() for case, ref in (sw.get("cases") or {}).items()
                    if isinstance(want, str) and str(case).split("::")[-1] == want]
            if refs:
                return [r for ref in refs if (r := resolve(ref, tp, self.types)) is not None]
        return self.targets(tp, a)

    def path(self, ref: str) -> Path_ | None:
        p = tuple(ref.split("::"))
        return p if p in self.types else None

    def walk(self, where: str, roots: list[Path_], chain: list[str],
             match: Match | None = None) -> list[tuple[Path_, dict[str, Any]]]:
        """The (type, attribute) pairs a dotted field path can end on."""
        cands: list[Path_] = roots
        hits: list[tuple[Path_, dict[str, Any]]] = []
        for i, aid in enumerate(chain):
            hits = [(tp, a) for tp in cands if (a := self.attr(tp, aid)) is not None]
            if not hits:
                err(f"{where}: {'.'.join(chain[:i + 1])} is no field of {sorted(name(c) for c in cands)}")
                return []
            cands = [r for tp, a in hits for r in self.cases(tp, a, match, chain[:i])]
            if not cands and i + 1 < len(chain):
                err(f"{where}: {'.'.join(chain[:i + 1])} has no fields to read {chain[i + 1]!r} from")
                return []
        return hits

    def expr(self, where: str, src: str, names: set[str], req: list[Path_] | None,
             args: set[str] | None = None, match: Match | None = None) -> None:
        try:
            tree = ast.parse(src[1:], mode="eval")
        except SyntaxError as e:
            err(f"{where}: expression {src!r} does not parse ({e.msg})")
            return
        nodes = list(ast.walk(tree))
        bound = {n.id for c in nodes if isinstance(c, ast.comprehension)
                 for n in ast.walk(c.target) if isinstance(n, ast.Name)}
        known = names | bound | FW_FUNCS | set(self.enums) | {"image"}
        inner = {id(n.value) for n in nodes if isinstance(n, ast.Attribute)}
        for n in nodes:
            if not isinstance(n, FW_NODES):
                err(f"{where}: {type(n).__name__} not allowed in {src!r}")
            elif isinstance(n, ast.Constant) and isinstance(n.value, (float, complex, bytes)):
                err(f"{where}: a {type(n.value).__name__} literal is not allowed in {src!r}")
            elif (isinstance(n, ast.Name) and n.id.startswith("_")) or (
                    isinstance(n, ast.Attribute) and n.attr.startswith("_")):
                err(f"{where}: names starting with _ are not allowed in {src!r}")
            elif isinstance(n, ast.Call) and (n.keywords or not isinstance(n.func, ast.Name)
                                              or n.func.id not in FW_FUNCS):
                err(f"{where}: only {sorted(FW_FUNCS)} may be called, in {src!r}")
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "decode":
                first = n.args[0] if n.args else None
                if not (isinstance(first, ast.Constant) and isinstance(first.value, str)
                        and self.path(first.value) is not None):
                    err(f"{where}: decode's first argument must be a .ksy type path, in {src!r}")
            elif isinstance(n, ast.Name) and n.id not in known:
                err(f"{where}: unknown name {n.id!r} in {src!r}")
            elif isinstance(n, ast.Attribute) and id(n) not in inner:
                chain: list[str] = []
                node: ast.expr = n
                while isinstance(node, ast.Attribute):
                    chain.insert(0, node.attr)
                    node = node.value
                if isinstance(node, ast.Name) and node.id not in bound:
                    self.chain(where, src, node.id, chain, req, args, match)

    def chain(self, where: str, src: str, base: str, chain: list[str], req: list[Path_] | None,
              args: set[str] | None, match: Match | None) -> None:
        scalars = {k for k, v in self.im.items() if isinstance(v, (str, int, bool))}
        if base == "state" and chain[0] not in self.state:
            err(f"{where}: unknown state field {chain[0]!r} in {src!r}")
        elif base == "image" and (chain[0] not in scalars or len(chain) > 1):
            err(f"{where}: image has no scalar field {'.'.join(chain)!r} in {src!r}")
        elif base == "event" and args is not None and chain[0] not in args:
            err(f"{where}: event has no arg {chain[0]!r} in {src!r}")
        elif base in self.enums:
            keys = self.enums[base]
            if len(keys) > 1:
                err(f"{where}: enum name {base!r} is ambiguous ({[name(k[0]) for k in keys]})")
            elif chain[0] not in self.ids(keys[0]):
                err(f"{where}: {base}.{chain[0]} is no member of {name(keys[0][0])}::{base}")
        elif base == "req" and req:
            self.walk(where, req, chain, match)
        elif base == "frame":
            self.walk(where, [FRAME], chain, match)

    @staticmethod
    def call(ref: str) -> tuple[str, list[str]]:
        """A type call `name(a, b)` as its name and argument texts."""
        head, _, rest = ref.partition("(")
        args: list[str] = []
        depth, cur = 0, ""
        for ch in rest.rstrip()[:-1] if rest else "":
            if ch == "," and depth == 0:
                args.append(cur.strip())
                cur = ""
                continue
            depth += (ch in "([") - (ch in ")]")
            cur += ch
        if cur.strip():
            args.append(cur.strip())
        return head.strip(), args

    def bind(self, child: Path_, args: list[str], parent: dict[str, Any],
             binds: dict[str, Any]) -> dict[str, Any]:
        """What each of child's params is known to be: a sibling literal, a profile form or a passed-on
        param."""
        out: dict[str, Any] = {}
        for prm, arg in zip(self.types[child].get("params") or [], args, strict=False):
            pid = str(prm.get("id"))
            form = re.fullmatch(r"profile\.forms\.(f_[0-9a-z_]+)", arg)
            sib = parent.get(arg)
            if form:
                out[pid] = self.forms.get(form.group(1), "unknown")
            elif isinstance(sib, (str, int)) and not str(sib).startswith("="):
                out[pid] = sib
            elif arg in binds:
                out[pid] = binds[arg]
        return out

    def chosen(self, a: dict[str, Any], node: dict[str, Any],
               binds: dict[str, Any]) -> tuple[bool, str | None]:
        """For a switch field whose selector is known: (True, the case's type call, or None for no case)."""
        sw = a.get("type")
        if not isinstance(sw, dict):
            return False, None
        on = str(sw.get("switch-on", ""))
        sel = node[on] if on in node else binds.get(on)
        if sel is None or (isinstance(sel, str) and sel.startswith("=")):
            return False, None
        hit = next((str(ref) for case, ref in (sw.get("cases") or {}).items()
                    if case == sel or str(case).split("::")[-1] == str(sel)), None)
        return True, hit

    def value(self, where: str, tp: Path_, a: dict[str, Any], v: Any, names: set[str],
              req: list[Path_] | None, match: Match | None, parent: dict[str, Any],
              binds: dict[str, Any]) -> None:
        cands = self.targets(tp, a)
        if cands == [ECHO]:
            err(f"{where}: a rest is the frame's fill, not a field to give")
            return
        raw = "size" in a or bool(a.get("size-eos")) or "terminator" in a
        switch = isinstance(a.get("type"), dict)
        known, case = self.chosen(a, parent, binds)
        if known:
            cands = [] if case is None else [r for r in [resolve(self.call(case)[0], tp, self.types)] if r]
        for x in v if isinstance(v, list) and a.get("repeat") else [v]:
            if isinstance(x, str) and x.startswith("="):
                self.expr(where, x, names, req, None, match)
            elif isinstance(x, dict):
                ref = x.get("type")
                only = cands[0] if len(cands) == 1 and not switch else None
                want = self.path(ref) if isinstance(ref, str) else only
                if switch and ref is None:
                    err(f"{where}: a switch field's node needs `type`")
                elif known and not cands:
                    tail = " or give raw bytes" if raw else ""
                    err(f"{where}: its selector picks no case, so leave it out{tail}")
                elif cands and want not in cands:
                    err(f"{where}: type {ref!r} is not {'the selected case' if known else 'one of'} "
                        f"{sorted(name(c) for c in cands)}")
                elif not cands and not (raw and want is not None):
                    err(f"{where}: a node needs a user-typed field, or a `type` on a raw bytes field")
                elif want is not None:
                    every = [str(r) for r in (a["type"].get("cases") or {}).values()] if switch else []
                    calls = [case] if known and case else every or [str(a.get("type", ""))]
                    call = next((c for c in calls if resolve(self.call(c)[0], tp, self.types) == want), "")
                    child = self.bind(want, self.call(call)[1], parent, binds)
                    self.node(where, want, x, names, req, match, child)
            elif isinstance(x, list):
                if cands or not raw:
                    err(f"{where}: a list of ints is only for an untyped bytes field")
                for i, b in enumerate(x):
                    if isinstance(b, str) and b.startswith("="):
                        self.expr(f"{where}[{i}]", b, names, req, None, match)
                    elif not (isinstance(b, int) and not isinstance(b, bool) and 0 <= b <= 255):
                        err(f"{where}[{i}]: {b!r} is not a byte")
            elif cands:
                err(f"{where}: a literal for a field of type {sorted(name(c) for c in cands)}")
            elif isinstance(x, str) and isinstance(a.get("enum"), str):
                key = resolve_enum(a["enum"], tp, self.types)
                if key is not None and x not in self.ids(key):
                    err(f"{where}: {x!r} is no member of {name(key[0])}::{key[1]}")
            elif isinstance(x, str) and NUMERIC.fullmatch(str(a.get("type", ""))):
                err(f"{where}: text {x!r} for a {a.get('type')} field")

    def node(self, where: str, tp: Path_, node: dict[str, Any], names: set[str],
             req: list[Path_] | None, match: Match | None = None,
             binds: dict[str, Any] | None = None) -> None:
        seq = self.seq(tp)
        binds = binds or {}
        for k, v in node.items():
            if k == "type":
                continue
            a = next((x for x in seq if x.get("id") == k), None)
            if a is None:
                err(f"{where}: {name(tp)} has no seq field {k!r}")
            else:
                self.value(f"{where}.{k}", tp, a, v, names, req, match, node, binds)
        for i, a in enumerate(seq):
            aid = str(a.get("id"))
            tail = bool(a.get("size-eos")) and i == len(seq) - 1
            if aid in node or "if" in a or "contents" in a or tail or self.targets(tp, a) == [ECHO]:
                continue
            known, case = self.chosen(a, node, binds)
            if known and case is None and "size" not in a:
                continue
            err(f"{where}: {name(tp)} field {aid!r} is not given (it has no `if` and no fill covers it)")

    def frame(self, where: str, f: dict[str, Any], names: set[str], req: list[Path_] | None,
              match: Match | None = None) -> None:
        route = str(f.get("route"))
        if not route.endswith("_rx") or route not in self.route_body:
            err(f"{where}: {route!r} is no rx route with a body type")
            return
        self.node(f"{where}.body", self.route_body[route], f.get("body") or {}, names, req, match)

    def effects(self, where: str, effects: list[dict[str, Any]], names: set[str], req: list[Path_] | None,
                args: set[str] | None = None, match: Match | None = None) -> None:
        for i, e in enumerate(effects):
            w = f"{where}.effects[{i}]"
            for t in [*(e.get("set") or {}), *([e["put"]] if "put" in e else [])]:
                field = t.removeprefix("state.")
                if field not in self.state:
                    err(f"{w}: unknown state field {field!r}")
            if "put" in e:
                init = (self.state.get(e["put"].removeprefix("state.")) or {}).get("init")
                if not (isinstance(init, list) or (isinstance(init, str) and init.startswith("=["))):
                    err(f"{w}: put needs a list state field, not {e['put']!r}")
            cipher = (e.get("set") or {}).get("state.cipher")
            if isinstance(cipher, str) and not cipher.startswith("=") and cipher not in CIPHER_KEYS:
                err(f"{w}: state.cipher is one of {sorted(CIPHER_KEYS)}, not {cipher!r}")
            values = [*(e.get("set") or {}).values(), *(e[k] for k in ("when", "at", "value") if k in e)]
            for v in values:
                for x in v if isinstance(v, list) else [v]:
                    if isinstance(x, str) and x.startswith("="):
                        self.expr(w, x, names, req, args, match)
            if "push" in e and e["push"] not in self.pushes:
                err(f"{w}: unknown push {e['push']!r}")

    def lets(self, where: str, lets: dict[str, str], names: set[str], req: list[Path_] | None,
             args: set[str] | None = None, match: Match | None = None) -> set[str]:
        scope = set(names)
        for k, v in lets.items():
            self.expr(f"{where}.let.{k}", v, scope, req, args, match)
            scope.add(k)
        return scope

    def image(self, im: dict[str, Any]) -> None:
        w = f"{self.where}:{im.get('id')}"
        self.im = im
        self.state = dict(im.get("state") or {})
        self.pushes = set(im.get("pushes") or {})
        self.profile(w, im)
        if im.get("cipher") == "e7_v1":
            for k in ("cipher", "session_key"):
                if k not in self.state:
                    err(f"{w}: cipher e7_v1 needs state.{k}")
            init = (self.state.get("cipher") or {}).get("init")
            if "cipher" in self.state and init not in CIPHER_KEYS:
                err(f"{w}: state.cipher init {init!r} is not one of {sorted(CIPHER_KEYS)}")
        base = {"state", "now_ms"}
        for k, f in self.state.items():
            for x in f["init"] if isinstance(f.get("init"), list) else [f.get("init")]:
                if isinstance(x, str) and x.startswith("="):
                    self.expr(f"{w}.state.{k}", x, set(), None)
        adv = im.get("advert") or {}
        scope = self.lets(f"{w}.advert", adv.get("let") or {}, base, None)
        if "when" in adv:
            self.expr(f"{w}.advert.when", adv["when"], scope, None)
        for part in ("adv", "scan_response"):
            for i, n in enumerate(adv.get(part) or []):
                self.node(f"{w}.advert.{part}[{i}]", ("govee_scan_record", "ad_structure"), n, scope, None)
        frame_names = base | {"frame", "raw", "checksum_ok"}
        rule_ids = [str(r.get("id")) for r in im.get("accept") or []]
        for r in im.get("accept") or []:
            self.expr(f"{w}.accept.{r.get('id')}", r["drop"], frame_names, None)
        answer_ids = [str(a.get("id")) for a in im.get("answers") or []]
        for kind, ids in (("accept", rule_ids), ("answer", answer_ids)):
            for dup in sorted({x for x in ids if ids.count(x) > 1}):
                err(f"{w}: duplicate {kind} id {dup!r}")
        for a in im.get("answers") or []:
            self.answer(f"{w}.answers.{a.get('id')}", a, frame_names | {"req"})
        for k, p in (im.get("pushes") or {}).items():
            if p["frame"].get("fill") != "zero":
                err(f"{w}.pushes.{k}: a push has no request, so its fill is zero")
            self.frame(f"{w}.pushes.{k}", p["frame"], base, None)
        for k, e in (im.get("events") or {}).items():
            args = set(e.get("args") or {})
            if k in SIM_EVENTS and args:
                err(f"{w}.events.{k}: the simulator raises {k}, which takes no args")
            scope = self.lets(f"{w}.events.{k}", e.get("let") or {}, base | {"event"}, None, args)
            if "when" in e:
                self.expr(f"{w}.events.{k}.when", e["when"], scope, None, args)
            self.effects(f"{w}.events.{k}", e.get("effects") or [], scope, None, args)

    def answer(self, aw: str, a: dict[str, Any], names: set[str]) -> None:
        match = dict(a.get("match") or {})
        route = str(match.get("route", ""))
        if not route.endswith("_tx") or route not in self.routes:
            err(f"{aw}: {route!r} is no tx route")
        req = [self.route_body[route]] if route in self.route_body else None
        for k, v in match.items():
            if k == "route":
                continue
            if req is None:
                err(f"{aw}: route {route!r} has no body type to match {k!r} in")
                continue
            ends = self.walk(f"{aw}.match", req, k.split("."), match)
            if any(self.targets(tp, x) for tp, x in ends):
                err(f"{aw}.match: {k} is not a scalar or enum field")
            keys = [e for tp, x in ends if isinstance(x.get("enum"), str)
                    and (e := resolve_enum(x["enum"], tp, self.types)) is not None]
            if ends and isinstance(v, str) and not any(v in self.ids(e) for e in keys):
                err(f"{aw}.match: {v!r} is no member of {sorted(name(e[0]) + '::' + e[1] for e in keys)}")
        if "unmodelled" in a and a.get("effects"):
            err(f"{aw}: an unmodelled answer applies no effects")
        scope = self.lets(aw, a.get("let") or {}, names, req, None, match)
        if "when" in a:
            self.expr(f"{aw}.when", a["when"], scope, req, None, match)
        self.effects(aw, a.get("effects") or [], scope, req, None, match)
        if isinstance(a.get("reply"), dict):
            self.frame(f"{aw}.reply", a["reply"], scope, req, match)

    def profile(self, where: str, im: dict[str, Any]) -> None:
        sku, gt = im.get("sku"), str(im.get("goods_type"))

        def gts(fam: dict[str, Any]) -> set[str]:
            cp = fam.get("client_profile") or {}
            wire_gts = (cp.get("wire") or {}).get("by_goods_type") or {}
            listed = {str(g) for g in fam.get("goods_types") or []}
            return listed | {str(k) for k in cp.get("by_goods_type") or {}} | {str(k) for k in wire_gts}

        fams = [fam for fam in self.devices.get("families") or [] if isinstance(fam, dict)
                and sku in (fam.get("models") or []) and gt in gts(fam)]
        if not fams:
            err(f"{where}.profile: no devices.yaml family lists {sku} with goodsType {gt}")
            return
        wire = (fams[0].get("client_profile") or {}).get("wire") or {}
        layers = [wire, (wire.get("by_model") or {}).get(sku) or {},
                  (wire.get("by_goods_type") or {}).get(gt) or {}]
        branches = [b for la in layers for k in ("by_pact", "by_firmware")
                    for b in ((la.get(k) or {}).values() if isinstance(la.get(k), dict) else la.get(k) or [])
                    if isinstance(b, dict)]
        self.forms = {k: v for la in layers for k, v in (la.get("forms") or {}).items()}
        client = set(self.forms) | {k for b in branches for k in b.get("forms") or {}}
        forms = ("govee_profile", "command_forms")
        for k, v in ((im.get("profile") or {}).get("forms") or {}).items():
            a = next((x for x in self.types[forms].get("params") or [] if x.get("id") == k), None)
            key = resolve_enum(str(a.get("enum")), forms, self.types) if a and a.get("enum") else None
            if key is None:
                err(f"{where}.profile: {k!r} is no govee_profile form selector")
            elif v not in self.ids(key):
                err(f"{where}.profile: {v!r} is no member of {name(key[0])}::{key[1]}")
            elif k in client:
                err(f"{where}.profile: {k} is set by the client profile; an image only fills unknown forms")
            else:
                self.forms[k] = v


def check_firmware(spec: Path, types: dict[Path_, Any]) -> None:
    fp, sp = spec / "firmware.yaml", spec / "firmware.schema.json"
    if not fp.is_file():
        return
    try:
        jsonschema: Any = importlib.import_module("jsonschema")
    except ImportError:
        err("firmware.yaml: jsonschema is needed to check it")
        return
    try:
        fw = yaml.load(fp.read_text(), Loader=UniqueKeyLoader)  # noqa: S506 (a SafeLoader subclass)
    except yaml.YAMLError as e:
        err(f"{fp.name}: {str(e).splitlines()[0]}")
        return
    for ve in jsonschema.Draft202012Validator(json.loads(sp.read_text())).iter_errors(fw):
        err(f"{fp.name}: {'/'.join(map(str, ve.absolute_path))}: {ve.message[:160]}")
    for r in doc_refs(fw):
        if not (isinstance(r, str) and (REF_FILE.fullmatch(r) or REF_ADDR.fullmatch(r))):
            err(f"{fp.name}: doc-ref format: {r!r}")
    devices = yaml.safe_load((spec / "devices.yaml").read_text()) or {}
    ids = [im.get("id") for im in fw.get("images") or []]
    for i in sorted({i for i in ids if ids.count(i) > 1}):
        err(f"{fp.name}: duplicate image id {i!r}")
    for im in fw.get("images") or []:
        Firmware(types, fp.name, devices).image(im)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    spec = Path(sys.argv[1])
    files = sorted(spec.glob("*.ksy"))
    if not files:
        err(f"no .ksy files in {spec}")
    types: dict[Path_, Any] = {}
    for f in files:
        y = yaml.safe_load(f.read_text())
        check_file(f, y)
        index_types(y, (f.stem,), types)
    check_types(types)
    check_schema(spec, types)
    check_firmware(spec, types)
    for e in errors:
        print("ERR", e)
    print(f"suite S lint: {len(files)} ksy, {len(types)} types, {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
