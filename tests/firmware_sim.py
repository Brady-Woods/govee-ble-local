"""The spec-driven device simulator: one spec/firmware.yaml image run as a peripheral.

Every frame and advert the device sends is a firmware.yaml node built with the spec's generated
read-write classes and written with ``wire._spec.emit`` / ``serialize``. Its echoes re-emit the request
it read, and its padding is the spec's ``govee_common::echo``, so the simulator constructs no bytes of
its own (tests/test_firmware_provenance.py holds it to that). Requests are read through the
``govee_ble_frame`` root. The behaviour is the image's: its accept rules, answers (shared layer, then
dispatcher), effects, pushes, events and advert, with the expression language firmware.schema.json
defines. Ported from the firmware session's S7a reference engine. What the format leaves open is
marked GAP:.

Semantics followed (firmware.schema.json):

* every frame the device sends is ``image.frame_len`` long. ``fill`` is by offset: every byte after
  the last given field, up to the checksum, is the request's byte at that offset (``request``) or 0
  (``zero``);
* the link cipher is ``image.cipher``: ``e7_v1`` under ``state.cipher`` (the PSK, then
  ``state.session_key``), or ``none``;
* the profile is devices.yaml's ``client_profile`` for the image's SKU and goodsType (``by_pact`` with
  the advert's pact, ``by_firmware`` with ``soft_version`` and ``hard_version``), then
  ``image.profile.forms`` for the selectors it leaves unknown;
* expression names are ``state``, ``image`` (its scalars), ``req``, ``frame``, ``raw``,
  ``checksum_ok``, ``now_ms``, ``event``, the ``let`` names and the enums, with the functions
  ``len``, ``range``, ``bytes``, ``random_bytes``, ``leading`` and ``decode``;
* a node with a ``type`` given for an untyped bytes field is written into that field;
* the effects are set, put, push and disconnect, in order; a disconnect runs the disconnect event at
  once, so later steps see its state;
* the simulator raises connect and disconnect when the link goes up or down, write on every GATT
  write (before the accept rules), and tick every 1000 ms of :meth:`Sim.advance`;
* state is plain data: a spec object stored in state becomes a dict keyed by its seq ids.
"""
from __future__ import annotations

import copy
import functools
import importlib
import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from kaitaistruct import ConsistencyError, ReadWriteKaitaiStruct

from govee_ble_local import crypto
from govee_ble_local.const import PSK
from govee_ble_local.exceptions import GoveeBleInvalidValue
from govee_ble_local.wire import _spec

SPEC = Path(__file__).resolve().parents[1] / "spec"
H5083 = "h5083_sw1_00_30"
_PRIM = re.compile(r"^(?:[us][1248](?:le|be)?|b\d+(?:le|be)?|f[48](?:le|be)?|bool|str|strz)$")
_CIPHERS = ("e7_v1", "none")
TICK_MS = 1000

Path_ = tuple[str, ...]


def _camel(s: str) -> str:
    """A .ksy id as the generated class name (the ids are lower snake case)."""
    return "".join(p.capitalize() for p in s.split("_"))


def _split_args(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        depth += ch in "(["
        depth -= ch in ")]"
        cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _type_ref(s: str) -> tuple[str, list[str]]:
    """A .ksy type invocation ``name(arg, ...)`` as the name and its argument expressions."""
    s = s.strip()
    if "(" in s:
        name, rest = s.split("(", 1)
        return name.strip(), _split_args(rest.rsplit(")", 1)[0])
    return s, []


@functools.cache
def _yaml(name: str) -> Any:
    """A spec YAML file, parsed once per test run (callers copy what they change)."""
    return yaml.safe_load((SPEC / name).read_text())


def image_skus() -> frozenset[str]:
    """The SKUs that have a firmware.yaml image (their devices are answered by the simulator only)."""
    return frozenset(str(i["sku"]).upper() for i in _yaml("firmware.yaml")["images"])


def _image_bytes(v: Any) -> bytes:
    """The bytes of a value the image (firmware.yaml) or a .ksy gives for an untyped bytes field: a str
    (ASCII), a list of ints, or bytes. The simulator's one conversion to bytes; the static rule in
    tests/test_firmware_provenance.py allows no other."""
    return v.encode("ascii") if isinstance(v, str) else bytes(v)


def _version_int(v: str) -> int:
    try:
        return int(str(v).replace(".", ""))
    except ValueError:
        return 0


class Ksy:
    """The .ksy types, their generated classes and their enums."""

    def __init__(self) -> None:
        self.types: dict[Path_, dict[str, Any]] = {}
        for f in sorted(SPEC.glob("*.ksy")):
            self._index(_yaml(f.name), (f.stem,))
        self.enum_names: dict[str, list[tuple[Path_, str]]] = {}
        for p, t in self.types.items():
            for e in t.get("enums") or {}:
                self.enum_names.setdefault(e, []).append((p, e))

    def _index(self, t: dict[str, Any], path: Path_) -> None:
        self.types[path] = t
        for k, st in (t.get("types") or {}).items():
            self._index(st, (*path, k))

    def resolve(self, ref: str, scope: Path_) -> Path_ | None:
        parts = tuple(ref.split("::"))
        for i in range(len(scope), -1, -1):
            cand = (*scope[:i], *parts)
            if cand in self.types:
                return cand
        return None

    def enum(self, ref: str, scope: Path_) -> dict[str, int]:
        *owner, name = ref.split("::")
        for i in range(len(scope), -1, -1):
            cand = (*scope[:i], *owner)
            if cand in self.types and name in (self.types[cand].get("enums") or {}):
                return self.members(cand, name)
        raise KeyError(f"enum {ref} from {scope}")

    def members(self, path: Path_, name: str) -> dict[str, int]:
        return {str(v["id"] if isinstance(v, dict) else v): int(k)
                for k, v in self.types[path]["enums"][name].items()}

    def enum_literal(self, s: str, scope: Path_) -> int:
        *ref, member = s.split("::")
        return self.enum("::".join(ref), scope)[member]

    def cls(self, path: Path_) -> Any:
        mod = importlib.import_module(f"govee_ble_local._generated.{path[0]}")
        obj = getattr(mod, _camel(path[0]))
        for p in path[1:]:
            obj = getattr(obj, _camel(p))
        return obj

    def attr(self, tp: Path_, aid: str) -> dict[str, Any] | None:
        return next((a for a in self.types[tp].get("seq") or [] if a.get("id") == aid), None)


@functools.cache
def _ksy() -> Ksy:
    return Ksy()


class Bag(dict[str, Any]):
    """A dict read as attributes: the state, image and event namespaces and the enums."""

    def __getattr__(self, k: str) -> Any:
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k) from None

    def __setattr__(self, k: str, v: Any) -> None:   # a scenario's `state.x = v` writes the key
        self[k] = v


class _FillEcho(_spec.Common.Echo):
    """A ``govee_common::echo`` the node left out: records the space it fills when written (the echo
    writes its bytes, then zeros to the end of its stream)."""

    def _write__seq(self, io: Any = None) -> None:
        if io is not None:
            self._io = io
        self._space = self._io.size() - self._io.pos()
        super()._write__seq(io)


@dataclass
class _Tail:
    """A trailing size-eos field of untyped bytes that the fill covers: its given bytes, or a typed node
    written into it. A write sizes it: the field's space is what the given part leaves short."""

    obj: Any
    fid: str
    given: bytes
    typed: Any = None
    space: int = 0

    def size(self, short: int) -> None:
        """Give the field its whole space: the typed node, or the given bytes as an echo, zero-padded."""
        self.space = len(self.given) + short
        value = self.typed if self.typed is not None else _spec.leaf(_spec.Common.Echo, bytes=self.given)
        setattr(self.obj, self.fid, _spec.serialize(value, self.space))


class Sim:
    """One firmware.yaml image. ``on_send(what, plain, wire)`` takes each frame the device sends;
    ``on_drop()`` is called when the device itself drops the link (a ``disconnect`` effect)."""

    def __init__(self, image_id: str = H5083, seed: int = 1) -> None:
        self.ksy = _ksy()
        self.im: dict[str, Any] = copy.deepcopy(
            next(i for i in _yaml("firmware.yaml")["images"] if i["id"] == image_id))
        self.frame_len = int(self.im["frame_len"])
        self.image = Bag({k: v for k, v in self.im.items() if isinstance(v, (str, int, bool))})
        if self.image.cipher not in _CIPHERS:
            raise NotImplementedError(f"cipher {self.image.cipher}")
        self.rng = random.Random(seed)  # noqa: S311 - a reproducible device, not a key
        self.now_ms = 0
        self.log: list[str] = []
        self.gaps: set[str] = set()
        self.sent: list[tuple[str, bytes]] = []   # (what, plaintext) the device sent, in order
        self.built: set[bytes] = set()            # every frame and advert the image built (its provenance)
        self.received: list[bytes] = []           # each client write, decrypted
        self.unreadable: list[bytes] = []         # the writes that don't read as a frame
        self.answered: list[str] = []             # the id of each answer that took a write
        self.events_raised: list[str] = []
        self.link = False
        self.on_send: Callable[[str, bytes, bytes], None] | None = None
        self.on_drop: Callable[[], None] | None = None
        self.enums = self._enum_ns()
        self.route_ids = self.ksy.members(("govee_ble_frame",), "route")
        fb = self.ksy.attr(("govee_ble_frame",), "body")
        assert fb is not None
        self.route_body: dict[str, tuple[Path_, list[str]]] = {}
        for case, ref in fb["type"]["cases"].items():
            name, args = _type_ref(ref)
            tp = self.ksy.resolve(name, ("govee_ble_frame",))
            assert tp is not None, ref
            self.route_body[case.split("::")[-1]] = (tp, args)
        self.state = Bag()
        for k, f in self.im["state"].items():
            self.state[k] = self.plain(self.value(f["init"], {}))
        self.prof = self._profile()

    # ── the profile ───────────────────────────────────────────────────────────
    def _advertised_pact(self) -> tuple[int, int] | None:
        # GAP: "the pact the advert carries" is read off the advert's govee_manufacturer_data node on the
        # power-up state (the profile is fixed per image; every image's pact fields are constants).
        adv = self.im.get("advert") or {}
        scope = self.lets(adv.get("let"), {})
        for n in adv.get("adv") or []:
            d = n.get("data")
            if isinstance(d, dict) and d.get("type") == "govee_manufacturer_data":
                return int(self.value(d["pact_type"], scope)), int(self.value(d["pact_code"], scope))
        return None

    def _profile(self) -> Any:
        dev = _yaml("devices.yaml")
        sku, gt = self.im["sku"], str(self.im["goods_type"])
        fam = next(f for f in dev["families"] if isinstance(f, dict) and any(
            sku in (r.get("models") or []) for k, r in
            ((f.get("client_profile") or {}).get("by_goods_type") or {}).items() if str(k) == gt))
        cp = fam["client_profile"]
        wire = cp.get("wire") or {}
        pact = self._advertised_pact()
        versions = {"min_soft_version": _version_int(self.im["soft_version"]),
                    "min_hard_version": _version_int(self.im["hard_version"])}
        forms: dict[str, str] = {}
        power: dict[str, int] = {}
        counts: dict[str, int] = {}

        def merge(layer: dict[str, Any]) -> None:
            forms.update(layer.get("forms") or {})
            power.update(layer.get("power") or {})
            counts.update(layer.get("counts") or {})
            if layer.get("group") or layer.get("mode") or layer.get("mode_status"):
                raise NotImplementedError("group / mode layers (no plug family uses them)")

        def apply(layer: dict[str, Any]) -> None:
            merge(layer)
            if pact is not None:
                br = (layer.get("by_pact") or {}).get(f"{pact[0]},{pact[1]}")
                if br:
                    merge(br)
            for br in layer.get("by_firmware") or []:
                keys = [k for k in br if k.startswith("min_")]
                if set(keys) - set(versions):
                    raise NotImplementedError(f"by_firmware keys {keys}: the schema names soft and hard only")
                if all(_version_int(br[k]) <= versions[k] for k in keys):
                    merge(br)
                    break

        apply(wire)
        apply((wire.get("by_model") or {}).get(sku) or {})
        apply((wire.get("by_goods_type") or {}).get(gt) or {})
        for k, v in ((self.im.get("profile") or {}).get("forms") or {}).items():
            forms.setdefault(k, v)   # an image only fills selectors the client profile leaves unknown
        kw: dict[str, Any] = {k: self.ksy.enum(f"govee_common::form_{k[2:]}", ("govee_common",))[v]
                              for k, v in forms.items()}
        kw.update({k: int(v) for k, v in counts.items()})
        kw["relay_on_mask"] = int((cp.get("by_goods_type") or {})[gt].get("relay_on_mask", 0))
        kw["relay_heartbeat_on_mask"] = int(power.get("relay_heartbeat_on_mask", 0))
        return _spec.profile(**kw)

    def _enum_ns(self) -> dict[str, Any]:
        return {name: Bag(self.ksy.members(*keys[0]))
                for name, keys in self.ksy.enum_names.items() if len(keys) == 1}

    # ── expressions and values ────────────────────────────────────────────────
    def _decode(self, tname: str, data: Any, *params: Any) -> Any:
        tp = self.ksy.resolve(tname, (tname.split("::")[0],))
        assert tp is not None, tname
        try:
            return _spec.read(self.ksy.cls(tp), _image_bytes(data), *params)
        except Exception:   # noqa: BLE001 - decode() is None when the data does not read
            return None

    def _funcs(self) -> dict[str, Any]:
        return {
            "len": len, "range": range, "bytes": bytes,
            "random_bytes": self.rng.randbytes,
            "leading": lambda xs: next((i for i, x in enumerate(xs) if not x), len(xs)),
            "decode": self._decode,
        }

    def eval(self, src: str, scope: dict[str, Any]) -> Any:
        names = {**self.enums, **self._funcs(), "state": self.state, "image": self.image,
                 "now_ms": self.now_ms, **scope}
        # The expressions are this repository's own spec/firmware.yaml, checked by spec/checks/lint.py
        # to the schema's Python subset, and run with no builtins.
        return eval(src[1:], {"__builtins__": {}}, names)  # noqa: S307

    def value(self, v: Any, scope: dict[str, Any]) -> Any:
        if isinstance(v, str) and v.startswith("="):
            return self.eval(v, scope)
        if isinstance(v, list):
            return [self.value(x, scope) for x in v]
        return v

    def lets(self, lets: dict[str, str] | None, scope: dict[str, Any]) -> dict[str, Any]:
        scope = dict(scope)
        for k, v in (lets or {}).items():
            scope[k] = self.eval(v, scope)
        return scope

    def plain(self, v: Any) -> Any:
        """State is plain data: a spec object becomes a dict keyed by its seq ids, an enum an int."""
        if isinstance(v, ReadWriteKaitaiStruct):
            ids = [a["id"] for a in self.ksy.types[self._type_path(v)].get("seq") or []]
            return {k: self.plain(getattr(v, k)) for k in ids if hasattr(v, k)}
        if isinstance(v, list):
            return [self.plain(x) for x in v]
        if isinstance(v, (bytes, bytearray)):
            return _image_bytes(v)
        if isinstance(v, int) and not isinstance(v, bool):
            return int(v)
        return v

    def _type_path(self, obj: Any) -> Path_:
        mod = type(obj).__module__.rsplit(".", 1)[-1]
        names = type(obj).__qualname__.split(".")
        for tp in self.ksy.types:
            if tp[0] == mod and len(tp) == len(names) and all(
                    _camel(a) == b for a, b in zip(tp, names, strict=True)):
                return tp
        raise KeyError(type(obj).__qualname__)

    # ── building spec objects from nodes ─────────────────────────────────────
    def _ctx_get(self, obj: Any, tp: Path_, expr: str) -> Any:
        """A .ksy param argument evaluated in ``obj``'s context (the parent field's type invocation)."""
        e = expr.strip()
        if e in ("true", "false"):
            return e == "true"
        if re.fullmatch(r"-?\d+|0x[0-9a-fA-F]+", e):
            return int(e, 0)
        if "::" in e:
            return self.ksy.enum_literal(e, tp)
        cur = obj
        for part in e.split("."):
            cur = getattr(cur, part)
        return cur

    def _make(self, tp: Path_, parent: Any, params: list[Any]) -> Any:
        same = parent is not None and type(parent).__module__.rsplit(".", 1)[-1] == tp[0]
        return _spec.node(self.ksy.cls(tp), parent if same else None, *params)

    def _field_type(self, tp: Path_, a: dict[str, Any], obj: Any, want: str | None
                    ) -> tuple[Path_, list[str]] | None:
        """The user type (and its param args) of field ``a``, by the node's ``type`` or the switch."""
        t = a.get("type")
        if isinstance(t, str):
            name, args = _type_ref(t)
            if _PRIM.match(name):
                return None
            rt = self.ksy.resolve(name, tp)
            return None if rt is None else (rt, args)
        if isinstance(t, dict):
            cases = t["cases"]
            if want is not None:
                wp = tuple(want.split("::"))
                for ref in cases.values():
                    name, args = _type_ref(ref)
                    if self.ksy.resolve(name, tp) == wp:
                        return wp, args
                raise ValueError(f"{want} is no case of {tp}.{a['id']}")
            on = self._ctx_get(obj, tp, t["switch-on"])
            for key, ref in cases.items():
                k = self.ksy.enum_literal(key, tp) if "::" in str(key) else int(key)
                if int(on) == k:
                    name, args = _type_ref(ref)
                    rt = self.ksy.resolve(name, tp)
                    return None if rt is None else (rt, args)
        return None   # no case: raw bytes

    def _to_node(self, v: Any) -> Any:
        """A record value (a spec object, or a dict from state) as a node dict."""
        if isinstance(v, ReadWriteKaitaiStruct):
            return self.plain(v)
        if isinstance(v, list):
            return [self._to_node(x) for x in v]
        return v

    def build(self, tp: Path_, parent: Any, params: list[Any], node: Any, scope: dict[str, Any],
              fills: list[Any]) -> Any:
        """The spec object of type ``tp`` for a firmware.yaml node (seq ids -> values). ``fills`` collects
        the fields the frame's fill covers (left-out echoes, trailing untyped fields)."""
        node = self._to_node(node)
        obj = self._make(tp, parent, params)
        seq = self.ksy.types[tp].get("seq") or []
        for i, a in enumerate(seq):
            fid = a["id"]
            trailing = a.get("size-eos") and "type" not in a and i == len(seq) - 1
            if fid in node:
                setattr(obj, fid, self._convert(tp, a, obj, node[fid], scope, fills, trailing=trailing))
            elif "contents" in a:
                setattr(obj, fid, _image_bytes(a["contents"]))
            elif a.get("type") == "govee_common::echo":
                e = _FillEcho()
                e.bytes = b""
                setattr(obj, fid, e)
                fills.append(e)
            elif trailing:
                setattr(obj, fid, b"")
                fills.append(_Tail(obj, fid, b""))
            # a field with an `if` the node leaves out is absent (its condition must not hold)
        for k in node:
            if k != "type" and not any(a["id"] == k for a in seq):
                raise ValueError(f"{tp} has no seq field {k}")
        return obj

    def _convert(self, tp: Path_, a: dict[str, Any], obj: Any, v: Any, scope: dict[str, Any],
                 fills: list[Any], *, trailing: bool) -> Any:
        v = self.value(v, scope)
        if a.get("repeat"):
            return [self._convert1(tp, a, obj, x, scope, fills) for x in v]
        out = self._convert1(tp, a, obj, v, scope, fills)
        if isinstance(out, _Tail):          # a typed node in an untyped field: sized on the first write
            fills.append(out)
            return out.given
        if trailing and isinstance(out, bytes):
            fills.append(_Tail(obj, a["id"], out))   # the fill extends a short trailing field
        return out

    def _convert1(self, tp: Path_, a: dict[str, Any], obj: Any, v: Any, scope: dict[str, Any],
                  fills: list[Any]) -> Any:
        v = self._to_node(v)
        if isinstance(v, dict):
            ft = self._field_type(tp, a, obj, v.get("type"))
            if ft is None:
                # a typed node written into an untyped bytes field (a switch with no case): built as its
                # own root, written into the field's space
                want = v.get("type")
                if want is None:
                    raise ValueError(f"node for a non-user field {tp}.{a['id']} needs a type")
                sub: list[Any] = []
                o = self.build(tuple(want.split("::")), None, [], v, scope, sub)
                if sub:
                    # GAP: a node type whose length depends on its stream (a trailing size-eos or
                    # echo) has no stated length inside an untyped field; it takes the field's space.
                    self.gaps.add(f"typed node {want} in raw {tp}.{a['id']} has a stream-sized tail")
                return _Tail(obj, a["id"], b"", typed=o)
            ctp, args = ft
            params = [self._ctx_get(obj, tp, x) for x in args]
            return self.build(ctp, obj, params, v, scope, fills)
        t = a.get("type")
        if isinstance(v, str) and a.get("enum"):
            return self.ksy.enum(a["enum"], tp)[v]
        if isinstance(t, str) and _PRIM.match(_type_ref(t)[0]):
            return bool(v) if t == "b1" else int(v)
        # bytes fields (size, size-eos, terminator, or a switch with no case): the image's value
        if isinstance(v, (str, list, tuple, bytes, bytearray)):
            return _image_bytes(v)
        raise ValueError(f"{tp}.{a['id']}: cannot convert {v!r}")

    def _write(self, root: Any, length: int, fills: list[Any], fill: str, request: bytes | None,
               body_end: int) -> bytes:
        """Write ``root`` (a frame with its checksum, or a scan record) at ``length`` bytes. The fill
        covers the left-out echoes and the short trailing fields: zeros, or the request's bytes at the
        same offsets."""
        write = _spec.emit if isinstance(root, _spec.Frame) else _spec.serialize
        tails = [f for f in fills if isinstance(f, _Tail)]
        for _ in range(len(tails) + 1):
            try:
                out: bytes = write(root, length)
                break
            except GoveeBleInvalidValue as err:
                # a trailing size-eos field written short: the size check names it and what it lacks
                c = err.__cause__
                tail = next((t for t in tails if not t.space and isinstance(c, ConsistencyError)
                             and c.id == t.fid and c.expected == 0 and isinstance(c.actual, int)), None)
                if tail is None:
                    raise
                tail.size(c.actual)
        else:
            raise RuntimeError("the fill did not converge")
        if fill != "request" or request is None:
            return self._record(out)
        for f in fills:
            if isinstance(f, _FillEcho) and f._space:
                # GAP: past the request's end (a shorter request) the echo writes zeros
                f.bytes = request[body_end - f._space:body_end]
            elif isinstance(f, _Tail) and f.space:
                if f.given or f.typed is not None:
                    raise NotImplementedError("GAP: a given trailing field under fill: request")
                setattr(f.obj, f.fid, request[body_end - f.space:body_end])
        return self._record(write(root, length))

    def _record(self, built: bytes) -> bytes:
        """``built`` as the image built it: what the provenance check (fake_ble.Provenance) accepts."""
        self.built.add(built)
        return built

    def frame(self, fr: dict[str, Any], scope: dict[str, Any], request: bytes | None,
              direction: int | None = None) -> bytes:
        """A firmware.yaml frame node (``route``, ``fill``, ``body``) as a ``govee_ble_frame``: RX, as the
        device sends it, unless ``direction`` says TX."""
        direction = _spec.RX if direction is None else direction
        route = fr["route"]
        f = _spec.node(_spec.Frame, None, direction, self.prof, pro_type=self.route_ids[route] & 0xFF)
        btp, bargs = self.route_body[route]
        fills: list[Any] = []
        f.body = self.build(btp, f, [self._ctx_get(f, ("govee_ble_frame",), x) for x in bargs],
                            fr["body"], scope, fills)
        body_end = self.frame_len - (0 if f.checksum_kind == _spec.Frame.ChecksumKind.none else 1)
        return self._write(f, self.frame_len, fills, fr["fill"], request, body_end)

    # ── the link ─────────────────────────────────────────────────────────────
    def _key(self) -> bytes:
        return PSK if self.state.cipher == "psk" else _image_bytes(self.state.session_key)

    def _seal(self, plain: bytes) -> bytes:
        return plain if self.image.cipher == "none" else crypto.encrypt(plain, self._key())

    def _open(self, wire: bytes) -> bytes:
        return wire if self.image.cipher == "none" else crypto.decrypt(wire, self._key())

    def send(self, what: str, plain: bytes) -> None:
        if not self.link:
            self.log.append(f"no link: {what} dropped")
            return
        wire = self._seal(plain)
        self.sent.append((what, plain))
        if self.on_send is not None:
            self.on_send(what, plain, wire)

    def effects(self, effects: list[dict[str, Any]] | None, scope: dict[str, Any]) -> None:
        for e in effects or []:
            if "when" in e and not self.eval(e["when"], scope):
                continue
            if "set" in e:
                vals = {p: self.plain(self.value(v, scope)) for p, v in e["set"].items()}   # all read first
                for p, v in vals.items():
                    self.state[p.split(".", 1)[1]] = v
            elif "put" in e:
                lst = self.state[e["put"].split(".", 1)[1]]
                lst[int(self.eval(e["at"], scope))] = self.plain(self.value(e["value"], scope))
            elif "push" in e:
                self.push(e["push"])
            elif "disconnect" in e:
                self.log.append("device drops the link")
                if self._link_down() and self.on_drop is not None:
                    self.on_drop()

    def push(self, name: str) -> None:
        p = self.im["pushes"][name]
        if not self.link:
            self.log.append(f"no link: push {name} dropped")
            return
        self.send(f"push {name}", self.frame(p["frame"], self.lets(p.get("let"), {}), None))

    def event(self, name: str, **args: Any) -> None:
        e = self.im["events"].get(name)
        self.events_raised.append(name)
        if e is None:
            return
        scope = self.lets(e.get("let"), {"event": Bag(args)})
        if "when" in e and not self.eval(e["when"], scope):
            return
        self.effects(e.get("effects"), scope)

    def connect(self) -> None:
        self.link = True
        self.event("connect")

    def _link_down(self) -> bool:
        if not self.link:
            return False
        self.link = False
        self.event("disconnect")
        return True

    def disconnect(self) -> None:
        """The central (or the radio) drops the link."""
        self._link_down()

    def advance(self, ms: int) -> None:
        """Move the clock, raising tick at every multiple of TICK_MS crossed (the power-up's phase)."""
        end = self.now_ms + ms
        nxt = (self.now_ms // TICK_MS + 1) * TICK_MS
        while nxt <= end:
            self.now_ms = nxt
            self.event("tick")
            nxt += TICK_MS
        self.now_ms = end

    # ── a client write ───────────────────────────────────────────────────────
    @staticmethod
    def _checksum_ok(kind: Any, raw: bytes) -> bool:
        if kind == _spec.Frame.ChecksumKind.none:
            return True
        want = _spec._sum8(raw[:-1]) if kind == _spec.Frame.ChecksumKind.sum8 else crypto.checksum(raw[:-1])
        return raw[-1] == want

    def write(self, wire: bytes) -> list[tuple[str, bytes]]:
        """One GATT write: the frames the device sends for it, as (what, plaintext)."""
        start = len(self.sent)
        if not self.link:
            self.log.append("write with no link")
            return []
        self.event("write")
        raw = self._open(wire)
        self.received.append(raw)
        frame = _spec.try_read_frame(raw, _spec.TX, self.prof)
        if frame is None:
            self.unreadable.append(raw)
            self.log.append(f"request does not read as its route, dropped: {raw.hex(' ')}")
            return []
        kind = frame.checksum_kind
        req = getattr(frame, "body", None)
        base = {"frame": frame, "raw": raw, "checksum_ok": self._checksum_ok(kind, raw), "req": req}
        for layer in ("shared", "dispatcher"):
            if any(self.eval(r["drop"], base) for r in self.im["accept"] if r.get("layer") in (None, layer)):
                self.log.append(f"dropped before {layer}: {raw.hex(' ')}")
                break
            hit, scope = None, base
            for ans in self.im["answers"]:
                if ans["layer"] != layer or not self._matches(ans["match"], frame, req):
                    continue
                scope = self.lets(ans.get("let"), base)
                if "when" in ans and not self.eval(ans["when"], scope):
                    continue
                hit = ans
                break
            if hit is None:
                if layer == "dispatcher":
                    self.log.append(f"no answer: {raw.hex(' ')}")
                continue
            self.answered.append(hit["id"])
            if "unmodelled" in hit:
                self.log.append(f"unmodelled: {hit['unmodelled']}")
                break
            after = hit.get("apply") == "after_reply"
            if not after:
                self.effects(hit.get("effects"), scope)
            reply = hit.get("reply")
            if reply == "echo":   # the request frame as read, written again (its checksum by its kind)
                self.send(f"echo {hit['id']}", self._record(_spec.emit(frame, len(raw))))
            elif isinstance(reply, dict):
                self.send(f"reply {hit['id']}", self.frame(reply, scope, raw))
            if after:
                self.effects(hit.get("effects"), scope)
            if not hit.get("forward"):
                break
        return self.sent[start:]

    def requests(self) -> list[str]:
        """Each client write read through ``govee_ble_frame(tx)``, named by its route and the enum names of
        its command (or op) and selector, e.g. ``"read_tx device_info sn"``."""
        names = []
        for raw in self.received:
            f = _spec.try_read_frame(raw, _spec.TX, self.prof)
            body = None if f is None else getattr(f, "body", None)
            parts = ["unreadable" if f is None else f.route.name]
            for attr in ("command", "op", "com_type"):
                v = getattr(body, attr, None)
                if v is not None:
                    parts.append(str(getattr(v, "name", v)))
                    break
            sel = getattr(getattr(body, "selector", None), "selector", None)
            if sel is not None:
                parts.append(str(getattr(sel, "name", sel)))
            names.append(" ".join(parts))
        return names

    def _matches(self, match: dict[str, Any], frame: Any, req: Any) -> bool:
        if int(frame.route) != self.route_ids[match["route"]]:
            return False
        for path, lit in match.items():
            if path == "route":
                continue
            cur = req
            for p in path.split("."):
                cur = getattr(cur, p, None)
                if cur is None:
                    return False
            if isinstance(lit, str):
                if getattr(cur, "name", None) != lit:
                    return False
            elif int(cur) != lit:
                return False
        return True

    # ── the advert ───────────────────────────────────────────────────────────
    def advert(self) -> tuple[bytes, bytes] | None:
        """The advertising data and the scan response the device sends now, or None when it does not
        advertise."""
        adv = self.im["advert"]
        scope = self.lets(adv.get("let"), {})
        if "when" in adv and not self.eval(adv["when"], scope):
            return None
        return self._ad_list(adv["adv"], scope), self._ad_list(adv["scan_response"], scope)

    def _ad_list(self, nodes: list[dict[str, Any]], scope: dict[str, Any]) -> bytes:
        tp = ("govee_scan_record",)
        root = self._make(tp, None, [])
        fills: list[Any] = []
        root.ad_structures = [self.build((*tp, "ad_structure"), root, [], n, scope, fills) for n in nodes]
        length = sum(1 + int(a.len_ad) for a in root.ad_structures)   # each AD takes 1 + len_ad bytes
        return self._write(root, length, fills, "zero", None, length)
