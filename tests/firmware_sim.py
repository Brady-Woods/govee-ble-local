"""The spec-driven device simulator: one spec/firmware.yaml image run as a peripheral.

Every frame and advert the device sends is a firmware.yaml node built with the spec's generated
read-write classes, so the simulator holds no frame-shaped byte literal (tests/test_hand_frames_lint.py
enforces that). Requests are read through the ``govee_ble_frame`` root. The behaviour is the image's:
its accept rules, answers (shared layer, then dispatcher), effects, pushes, events and advert, with the
expression language firmware.schema.json defines. Ported from the firmware session's S7a reference
engine. The choices the format leaves open are marked GAP:.

Semantics followed (firmware.schema.json):

* every frame the device sends is ``image.frame_len`` long. ``fill`` is by offset: every byte after
  the last given field, up to the checksum, is the request's byte at that offset (``request``) or 0
  (``zero``);
* the profile is devices.yaml's ``client_profile`` for the image's SKU and goodsType (``by_pact``,
  ``by_firmware``), then ``image.profile.forms`` for the selectors it leaves unknown;
* expression names are ``state``, ``image`` (its scalars), ``req``, ``frame``, ``raw``,
  ``checksum_ok``, ``now_ms``, ``event``, the ``let`` names and the enums, with the functions
  ``len``, ``min``, ``max``, ``range``, ``bytes``, ``random_bytes``, ``leading`` and ``decode``;
* a node with a ``type`` given for an untyped bytes field is written into that field;
* the effects are set, put, push and disconnect;
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
from io import BytesIO
from pathlib import Path
from typing import Any

import yaml
from kaitaistruct import ConsistencyError, KaitaiStream, ReadWriteKaitaiStruct

from govee_ble_local import crypto
from govee_ble_local.const import PSK
from govee_ble_local.wire import _spec

SPEC = Path(__file__).resolve().parents[1] / "spec"
H5083 = "h5083_sw1_00_30"
_PRIM = re.compile(r"^(?:[us][1248](?:le|be)?|b\d+(?:le|be)?|f[48](?:le|be)?|bool|str|strz)$")
TICK_MS = 1000

Path_ = tuple[str, ...]


def _camel(s: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in s.split("_"))


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
    """A ``govee_common::echo`` the node left out: records the space it fills when written."""

    def _write__seq(self, io: Any = None) -> None:
        if io is not None:
            self._io = io
        self._space = self._io.size() - self._io.pos()
        super()._write__seq(io)


class Sim:
    """One firmware.yaml image. ``on_send(what, plain, wire)`` takes each frame the device sends;
    ``on_drop()`` is called when the device itself drops the link (a ``disconnect`` effect)."""

    def __init__(self, image_id: str = H5083, seed: int = 1) -> None:
        self.ksy = _ksy()
        self.im: dict[str, Any] = copy.deepcopy(
            next(i for i in _yaml("firmware.yaml")["images"] if i["id"] == image_id))
        self.frame_len = int(self.im["frame_len"])
        self.image = Bag({k: v for k, v in self.im.items() if isinstance(v, (str, int, bool))})
        self.rng = random.Random(seed)  # noqa: S311 - a reproducible device, not a key
        self.now_ms = 0
        self.log: list[str] = []
        self.gaps: set[str] = set()
        self.sent: list[tuple[str, bytes]] = []   # (what, plaintext) the device sent, in order
        self.received: list[bytes] = []           # each client write, decrypted
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
        # GAP: "the advertised pact" is read off the advert's govee_manufacturer_data node, evaluated
        # against the power-up state (the format does not say which state, or what if `when` fails).
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
        soft = _version_int(self.im["soft_version"])
        hard = _version_int(self.im["hard_version"])
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
                # GAP: min_hard_version / min_wifi_soft_version have no stated input (the schema names
                # only soft_version); taken here as image.hard_version / image.soft_version.
                if all(_version_int(br[k]) <= v for k, v in (("min_soft_version", soft),
                       ("min_hard_version", hard), ("min_wifi_soft_version", soft)) if k in br):
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
            return _spec.read(self.ksy.cls(tp), bytes(data), *params)
        except Exception:   # noqa: BLE001 - decode() is None when the data does not read
            return None

    def _funcs(self) -> dict[str, Any]:
        return {
            "len": len, "min": min, "max": max, "range": range, "bytes": bytes,
            "random_bytes": lambda n: bytes(self.rng.randrange(256) for _ in range(n)),
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
        if isinstance(v, bytearray):
            return bytes(v)
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
        """The spec object of type ``tp`` for a firmware.yaml node (seq ids -> values)."""
        node = self._to_node(node)
        obj = self._make(tp, parent, params)
        seq = self.ksy.types[tp].get("seq") or []
        for i, a in enumerate(seq):
            fid = a["id"]
            if fid in node:
                setattr(obj, fid, self._convert(tp, a, obj, node[fid], scope, fills))
            elif "contents" in a:
                setattr(obj, fid, bytes(a["contents"]))
            elif a.get("type") == "govee_common::echo":
                e = _FillEcho()
                e.bytes = b""
                setattr(obj, fid, e)
                fills.append(("echo", e))
            elif a.get("size-eos") and "type" not in a and i == len(seq) - 1:
                setattr(obj, fid, b"")
                fills.append(("raw", obj, fid, 0))
            # a field with an `if` the node leaves out is absent (its condition must not hold)
        for k in node:
            if k != "type" and not any(a["id"] == k for a in seq):
                raise ValueError(f"{tp} has no seq field {k}")
        return obj

    def _convert(self, tp: Path_, a: dict[str, Any], obj: Any, v: Any, scope: dict[str, Any],
                 fills: list[Any]) -> Any:
        v = self.value(v, scope)
        if a.get("repeat"):
            return [self._convert1(tp, a, obj, x, scope, fills) for x in v]
        out = self._convert1(tp, a, obj, v, scope, fills)
        seq = self.ksy.types[tp].get("seq") or []
        if a.get("size-eos") and isinstance(out, bytes) and seq and seq[-1] is a:
            fills.append(("raw", obj, a["id"], len(out)))   # a trailing size-eos field the fill extends
        return out

    def _convert1(self, tp: Path_, a: dict[str, Any], obj: Any, v: Any, scope: dict[str, Any],
                  fills: list[Any]) -> Any:
        v = self._to_node(v)
        if isinstance(v, dict):
            ft = self._field_type(tp, a, obj, v.get("type"))
            if ft is None:
                # a typed node written into an untyped bytes field (status_chunk.data, a switch with no
                # case): built as its own root and written at its natural length
                want = v.get("type")
                if want is None:
                    raise ValueError(f"node for a non-user field {tp}.{a['id']} needs a type")
                sub: list[Any] = []
                o = self.build(tuple(want.split("::")), None, [], v, scope, sub)
                if sub:
                    # GAP: a node type whose length depends on its stream (a trailing size-eos or
                    # echo) has no stated length inside a raw bytes field.
                    self.gaps.add(f"typed node {want} in raw {tp}.{a['id']} has a stream-sized tail")
                return self._natural_bytes(o)
            ctp, args = ft
            params = [self._ctx_get(obj, tp, x) for x in args]
            return self.build(ctp, obj, params, v, scope, fills)
        t = a.get("type")
        if isinstance(v, str) and a.get("enum"):
            return self.ksy.enum(a["enum"], tp)[v]
        if isinstance(t, str) and _PRIM.match(_type_ref(t)[0]):
            return bool(v) if t == "b1" else int(v)
        # bytes fields (size, size-eos, terminator, or a switch with no case)
        if isinstance(v, str):
            return v.encode("ascii")
        if isinstance(v, (list, tuple, bytes, bytearray)):
            return bytes(v)
        raise ValueError(f"{tp}.{a['id']}: cannot convert {v!r}")

    def _natural_bytes(self, obj: Any) -> bytes:
        _spec._check_tree(obj)
        io = KaitaiStream(BytesIO(bytearray(256)))
        obj._write(io)
        return bytes(io.to_byte_array())[:io.pos()]

    def _emit_filled(self, root: Any, length: int, fills: list[Any], fill: str, request: bytes | None,
                     body_end: int) -> bytes:
        """Write ``root``; the left-out echo or short trailing size-eos field takes the space up to
        ``body_end``, as zeros or the request's bytes at the same offsets."""
        for _ in range(8):
            try:
                out = self._emit(root, length)
                break
            except ConsistencyError as err:
                hit = next((f for f in fills if f[0] == "raw" and f[2] == err.id
                            and not hasattr(f[1], "_padded_" + f[2])), None)
                if hit is None or not isinstance(err.actual, int):
                    raise
                _, obj, fid, _n = hit
                cur = getattr(obj, fid)
                setattr(obj, fid, cur + bytes(err.actual))
                setattr(obj, "_padded_" + fid, (len(cur), err.actual))
        else:
            raise RuntimeError("fill did not converge")
        start = body_end
        for f in fills:
            if f[0] == "echo" and getattr(f[1], "_space", 0):
                start = min(start, body_end - f[1]._space)
            elif f[0] == "raw" and hasattr(f[1], "_padded_" + f[2]):
                start = min(start, body_end - getattr(f[1], "_padded_" + f[2])[1])
        if fill == "request" and request is not None and start < body_end:
            src = request[start:body_end]
            if len(src) < body_end - start:
                self.gaps.add("fill request: request shorter than the reply's fill range")
                src = src + bytes(body_end - start - len(src))
            for f in fills:
                if f[0] == "echo" and getattr(f[1], "_space", 0):
                    f[1].bytes = src[body_end - f[1]._space - start:]
                elif f[0] == "raw" and hasattr(f[1], "_padded_" + f[2]):
                    n0, pad = getattr(f[1], "_padded_" + f[2])
                    cur = getattr(f[1], f[2])
                    setattr(f[1], f[2], cur[:n0] + src[body_end - pad - start:])
            out = self._emit(root, length)
        return out

    def _emit(self, root: Any, length: int) -> bytes:
        if not isinstance(root, _spec.Frame):
            return self._ser(root, length)
        kind = root.checksum_kind
        if kind == _spec.Frame.ChecksumKind.none:
            return self._ser(root, length)
        root.checksum = 0
        first = self._ser(root, length)
        root.checksum = self._checksum(kind, first[:-1])
        return self._ser(root, length)

    @staticmethod
    def _checksum(kind: Any, body: bytes) -> int:
        return (sum(body) & 0xFF) if kind == _spec.Frame.ChecksumKind.sum8 else crypto.checksum(body)

    def _ser(self, obj: Any, length: int) -> bytes:
        _spec._check_tree(obj)
        io = KaitaiStream(BytesIO(bytearray(length)))
        obj._write(io)
        return bytes(io.to_byte_array())

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
        return self._emit_filled(f, self.frame_len, fills, fr["fill"], request, body_end)

    # ── the link ─────────────────────────────────────────────────────────────
    def key(self) -> bytes:
        return PSK if self.state.cipher == "psk" else bytes(self.state.session_key)

    def send(self, what: str, plain: bytes) -> None:
        if not self.link:
            self.log.append(f"no link: {what} dropped")
            return
        wire = crypto.encrypt(plain, self.key())
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
                # GAP: the format does not say whether the disconnect event runs at once (later effects
                # of this rule then see it) or after the rule; it runs at once here.
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
    def write(self, ciphertext: bytes) -> list[tuple[str, bytes]]:
        """One GATT write: the frames the device sends for it, as (what, plaintext)."""
        start = len(self.sent)
        if not self.link:
            self.log.append("write with no link")
            return []
        self.event("write")
        raw = crypto.decrypt(ciphertext, self.key())
        self.received.append(raw)
        frame = _spec.try_read_frame(raw, _spec.TX, self.prof)
        if frame is None:
            self.log.append(f"request does not read as its route, dropped: {raw.hex(' ')}")
            return []
        kind = frame.checksum_kind
        ok = kind == _spec.Frame.ChecksumKind.none or raw[-1] == self._checksum(kind, raw[:-1])
        req = getattr(frame, "body", None)
        base = {"frame": frame, "raw": raw, "checksum_ok": ok, "req": req}
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
            if reply == "echo":
                body = raw if kind == _spec.Frame.ChecksumKind.none else raw[:-1]
                self.send(f"echo {hit['id']}", raw if kind == _spec.Frame.ChecksumKind.none
                          else body + bytes([self._checksum(kind, body)]))
            elif isinstance(reply, dict):
                self.send(f"reply {hit['id']}", self.frame(reply, scope, raw))
            if after:
                self.effects(hit.get("effects"), scope)
            if not hit.get("forward"):
                break
        return self.sent[start:]

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
        return self._emit_filled(root, length, fills, "zero", None, length)
