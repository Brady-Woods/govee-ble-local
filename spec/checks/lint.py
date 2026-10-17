"""Suite S structural lints for a spec dir (CLEAN_SPEC_DESIGN 1.6, as cut in section 10).

usage: python lint.py SPEC_DIR

Reads SPEC_DIR/*.ksy, devices.schema.json and devices.yaml; writes nothing.

  meta     meta id == file stem; license MIT; ks-version 0.11; endian and bit-endian set.
  banned   _root / _parent in any expression; io / pos / process keys on an attribute;
           `type: io` params; meta ks-opaque-types.
  roots    every top-level doc starts with STANDALONE (, SHARED PAYLOAD, CONTEXT or LIBRARY;
           the STANDALONE tops are exactly ROOTS; no nested type is labelled STANDALONE.
  reach    every type is used (seq, instance, param or switch-case type), transitively,
           from a STANDALONE top or a CONTEXT / LIBRARY top; no orphans.
  enums    every enum is applied by an `enum:` key; no enum has fewer than two members.
  schema   a string enum under any devices.schema.json `wire` property is named like a
           govee_profile selector param and lists that enum's ids (the value-0 id may be
           left out); every govee_profile selector enum except color_decode is listed there;
           color_decode's ids minus not_decoded equal the schema's mode_color_decode key
           enum and devices.yaml's mode_color_decode keys.
  doc-ref  each doc-ref is `path.(java|c|kt|smali):N[-M][, N...]` or `IMAGE name @0xADDR`.

Exits 1 on any error.
"""

import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

ROOTS = {"govee_ble_frame", "govee_status_stream", "govee_upload", "govee_manufacturer_data",
         "govee_scan_record", "govee_bgc_info"}
TOP_LABELS = ("STANDALONE (", "SHARED PAYLOAD", "CONTEXT", "LIBRARY")
SOURCE_LABELS = ("STANDALONE (", "CONTEXT", "LIBRARY")
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


def code_strings(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield str(k)
            if k not in DOC_KEYS:
                yield from code_strings(v)
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
    applied: set[EnumKey] = set()
    for p, t in types.items():
        if len(p) > 1 and str(t.get("doc", "")).startswith("STANDALONE"):
            err(f"{name(p)}: nested type labelled STANDALONE (only file tops are entry points)")
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
            if isinstance(a.get("enum"), str):
                e = resolve_enum(a["enum"], p, types)
                if e is None:
                    err(f"{where}: unresolved enum {a['enum']}")
                else:
                    applied.add(e)
    tops = {p: t for p, t in types.items() if len(p) == 1}
    standalone = {p[0] for p, t in tops.items() if str(t.get("doc", "")).startswith("STANDALONE (")}
    for r in sorted(ROOTS - standalone):
        err(f"missing STANDALONE root {r}")
    for r in sorted(standalone - ROOTS):
        err(f"{r}: STANDALONE top is not one of the six roots {sorted(ROOTS)}")
    seen = [p for p, t in tops.items() if str(t.get("doc", "")).startswith(SOURCE_LABELS)]
    reached = set(seen)
    while seen:
        for q in edges[seen.pop()] - reached:
            reached.add(q)
            seen.append(q)
    for p in sorted(set(types) - reached):
        err(f"{name(p)}: orphan type (not reachable from a STANDALONE, CONTEXT or LIBRARY top)")
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
    for e in errors:
        print("ERR", e)
    print(f"suite S lint: {len(files)} ksy, {len(types)} types, {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
