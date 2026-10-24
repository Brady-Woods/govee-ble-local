"""Whether a step's read-back proves anything, and the step's verdict.

The library's state is optimistic: a setter writes its intent into ``Device.state`` and a poll overwrites
only the fields a reply carried. Comparing the state after ``update()`` with the intent would pass every
field the row never reads back, and every field whose reply the library dropped (a bad checksum) or
rejected (a status stream the app's read fails on, a mode report too short for its layout). So each field
is judged only where the profile reads it, a reply that carries it came back in that poll (which
:class:`FrameWatch` sees on the library's frame logger), AND the poll assigned it in the device's state
(:func:`assignments`, :func:`poll`): what the library applied, not which frames arrived. A write step's
field whose intent is the value it had before the step proves nothing by matching (the read can't tell
the write from no write), and an RGB read after a colour-temperature write proves nothing on a mode report
that can't show a colour temperature (the ``_nocct`` decodes, ``rgb``): both are not read, and the tester
is asked.

:class:`FrameWatch` keeps, per frame, only its time, direction and first two bytes; for an ``33 xx`` ack
its status byte, for an ``aa 05`` reply its sub-mode and for an ``aa 01`` reply its first byte (a plug's
relay bits, one per outlet). A ``0xAC`` status burst is joined in memory to
read its TLV tags and the 0x05 TLV's sub-mode, then dropped: no payload is kept (the library already
blanks the ``33 b2`` / ``aa b1`` secret frames at its capture point).
"""
from __future__ import annotations

import contextlib
import dataclasses
import logging
import time
from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

if TYPE_CHECKING:
    from .plan import Step

FRAMES_LOGGER = "govee_ble_local.frames"
Verdict: TypeAlias = Literal["pass", "fail", "unverifiable", "skipped"]

# Each colour decode of the spec's color_decode enum, as the state fields its report sets in the library
# (wire.parse.mode_color): a Kelvin-only report sets the Kelvin (and clears no RGB), a colour layout both.
# A decode missing here (a newer spec) counts nothing, with a warning in the report.
DECODE_FIELDS: Mapping[str, tuple[str, ...]] = {
    "not_decoded": (),
    "gradual_only": (),
    "gradual_kelvin_s16be": ("color_temp_kelvin",),
    "kelvin_s16be": ("color_temp_kelvin",),
    "gradual_kelvin_s16be_nocct": (),
    "skip_kelvin_s16be_illumination": (),
    "gradual_kelvin_pair_s16be": ("color_temp_kelvin", "zone_color_temp_kelvin"),
    "skip_kelvin_pair_s16be": ("color_temp_kelvin",),
    "gradual_kelvin_triple_s16be": (),
    "gradual_kelvin_triple_s16be_nocct": (),
    "rgb_kelvin_pair_s16be": ("rgb_color", "color_temp_kelvin"),
    "rgb_kelvin_tint": ("rgb_color", "color_temp_kelvin"),
    "rgb_kelvin_tint_positive": ("rgb_color", "color_temp_kelvin"),
    "rgb_kelvin_0d": ("rgb_color", "color_temp_kelvin"),
    "rgb_kelvin_0d_nocct": ("rgb_color",),
    "rgb": ("rgb_color",),
    "rgb_flag1_tint": ("rgb_color", "color_temp_kelvin"),
    "rgb_flagnz_tint": ("rgb_color", "color_temp_kelvin"),
    "rgb_flag_tint_nokelvin": ("rgb_color",),
    "rgb_pair_flag_lookup": ("rgb_color", "color_temp_kelvin"),
    "bri_rgb_pair": ("rgb_color",),
}
# The library's 1.x default parse, which is not in the spec's table (profile._DEFAULT_PARSE).
_DEFAULT_PARSE = {"0x15": "gradual_kelvin_s16be", "0x0d": "rgb_kelvin_tint"}
# The sub-mode a status row's colour writes put the device in, by colour scheme (the 0xAC reply's 0x05
# TLV reports it); a scheme missing here counts no colour from the status reply.
_SCHEME_SUB = {"h60a6": 0x15, "h6061": 0x15, "h6006": 0x0D, "h6188": 0x0D}
SCENE_SUB = 0x04
# Every state field a generic step can be judged on.
FIELDS = ("is_on", "brightness", "rgb_color", "color_temp_kelvin", "scene_code", "segments",
          "zone_power", "zone_color_temp_kelvin", "gradual")


@dataclasses.dataclass(frozen=True)
class Frame:
    t: float
    dir: str              # "tx" / "rx"
    op: str               # first two bytes, hex ("3305", "aa05"); "ac" for a whole status burst
    sub: int | None = None             # 33 xx ack: status; aa 05: sub-mode; aa 01: relay bits; burst: 05 sub
    tags: frozenset[int] | None = None  # status burst: its TLV tags (None: could not be joined)


class FrameWatch(logging.Handler):
    """A handler on ``govee_ble_local.frames`` (at DEBUG, not propagated) for one device's frames."""

    def __init__(self, address: str) -> None:
        super().__init__(logging.DEBUG)
        self.address = address.upper()
        self.frames: list[Frame] = []
        self._burst: list[bytes] = []
        self._saved: tuple[int, bool] | None = None

    # -- attach / detach -----------------------------------------------------
    def attach(self) -> FrameWatch:
        log = logging.getLogger(FRAMES_LOGGER)
        self._saved = (log.level, log.propagate)
        log.setLevel(logging.DEBUG)
        log.propagate = False
        log.addHandler(self)
        return self

    def detach(self) -> None:
        log = logging.getLogger(FRAMES_LOGGER)
        log.removeHandler(self)
        if self._saved is not None and not any(isinstance(h, FrameWatch) for h in log.handlers):
            log.setLevel(self._saved[0])
            log.propagate = self._saved[1]
        self._saved = None

    # -- capture -------------------------------------------------------------
    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._take(record)
        except Exception:   # a logging handler never raises into the BLE pump
            self._burst = []

    def _take(self, record: logging.LogRecord) -> None:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 4 or str(args[0]).upper() != self.address:
            return
        if "plain=%s" not in str(record.msg):   # no plaintext (an encrypted frame before the session)
            return
        direction, plain_hex = str(args[1]), str(args[3])
        if len(plain_hex) < 4:
            return
        plain = bytes.fromhex(plain_hex)
        now = time.monotonic()
        op = plain[:2].hex()
        if direction == "rx" and plain[0] == 0xAC:
            self._take_burst(now, plain)
            return
        sub: int | None = None
        if len(plain) > 2 and direction == "rx" and (plain[0] == 0x33 or op in ("aa05", "aa01")):
            sub = plain[2]
        self.frames.append(Frame(now, direction, op, sub))

    def _take_burst(self, now: float, chunk: bytes) -> None:
        """Collect a status burst's chunks; at its last, record its TLV tags and drop the bytes."""
        if len(chunk) < 7:
            return
        if chunk[1] == 0x00:
            self._burst = [chunk]
        elif self._burst:
            self._burst.append(chunk)
        else:
            return
        first = self._burst[0]
        total = first[2] | first[3] << 8
        if not (chunk[1] == 0xFF or total <= 1 or len(self._burst) >= total):
            return
        chunks, self._burst = self._burst, []
        tags, mode_sub = _tlv_tags(_join_burst(chunks))
        self.frames.append(Frame(now, "rx", "ac", mode_sub, tags))

    # -- questions -----------------------------------------------------------
    def mark(self) -> int:
        return len(self.frames)

    def window(self, since: int, until: int | None = None) -> list[Frame]:
        return self.frames[since:until]

    def writes(self, since: int, until: int | None = None) -> list[str]:
        """The frames sent that are not reads (``aa`` / ``ac``) or handshake (``e7``)."""
        return [f.op for f in self.window(since, until)
                if f.dir == "tx" and f.op[:2] not in ("aa", "ac", "e7")]

    def replies(self, since: int, until: int | None = None) -> list[Frame]:
        return [f for f in self.window(since, until) if f.dir == "rx"]

    def acked(self, op: str, since: int = 0, until: int | None = None) -> int | None:
        """The status of the first ``op`` ack (``33 b2`` ...) received in the window, or None."""
        sent = False
        for f in self.window(since, until):
            if f.op == op and f.dir == "tx":
                sent = True
            elif f.op == op and f.dir == "rx" and sent:
                return f.sub
        return None

    def sent(self, op: str, since: int = 0, until: int | None = None) -> bool:
        return any(f.op == op and f.dir == "tx" for f in self.window(since, until))


def relay_bits(frames: Iterable[Frame]) -> int | None:
    """The relay byte of the last ``aa 01`` reply among ``frames`` (one bit per outlet), or None."""
    bits = [f.sub for f in frames if f.dir == "rx" and f.op == "aa01" and f.sub is not None]
    return bits[-1] if bits else None


def outlet_mask(profile: Any) -> int | None:
    """The relay bits a plug's power write sets: bit 0 for the outlet-0 frame (``33 01 11 / 10``; also the
    library's frame for every plug before ``relay_power``), the outlets' bits for the every-outlet frames
    (``33 01 FF / F0``, the ``33 02`` total switch), or None where the row doesn't say which bits are
    outlets (``relay_on_mask`` 0xFF: any bit)."""
    if getattr(profile, "relay_power", "outlet0") == "outlet0":
        return 0x01
    mask = int(getattr(profile, "relay_on_mask", 0xFF))
    return None if mask == 0xFF else mask


def _join_burst(chunks: list[bytes]) -> bytes | None:
    """A status burst's data (AbsController4BleSingleSendMultiBack): the first chunk's 12 bytes after its
    7-byte header, then 17 a chunk, the last (index 0xFF) cut to its lastLen. None when inconsistent."""
    first = chunks[0]
    total, last_len = first[2] | first[3] << 8, first[4]
    if total <= 1:
        return first[7:7 + last_len] if last_len <= 12 else None
    if len(chunks) != total or chunks[-1][1] != 0xFF:
        return None
    data = bytearray(first[7:19])
    for chunk in chunks[1:-1]:
        data += chunk[2:19]
    data += chunks[-1][2:2 + last_len]
    return bytes(data)


def _tlv_tags(data: bytes | None) -> tuple[frozenset[int] | None, int | None]:
    """The TLV tags of a status stream ([tag][len][value]...), and the 0x05 TLV's sub-mode byte."""
    if data is None:
        return None, None
    tags: set[int] = set()
    mode_sub: int | None = None
    pos = 0
    while len(data) - pos >= 3:
        tag, length = data[pos], data[pos + 1]
        if length > 0x7F:
            break
        tags.add(tag)
        if tag == 0x05 and length >= 1 and pos + 2 < len(data):
            mode_sub = data[pos + 2]
        pos += 2 + length
    return frozenset(tags), mode_sub


# -- what a poll set ------------------------------------------------------------------------------

NOT_SEEN = "the tool could not see which fields the poll set"
NOT_SET = "the poll's reply set nothing here (the library dropped or rejected it)"
COLOURS = ("rgb_color", "color_temp_kelvin")


@contextlib.contextmanager
def assignments(dev: Any) -> Iterator[set[str] | None]:
    """The state fields the library assigns on ``dev``'s state object while the block runs (a poll: what its
    replies set, a value equal to the one held included), or None where the tool can't see them (no
    dataclass state). The state class's ``__setattr__`` records the assignments to that one object for the
    block, and is put back after: every reader in the library assigns the field it sets (a new dict or list
    for ``zone_power`` and ``segments``), so this is what the poll applied, whatever its frames were."""
    state = getattr(dev, "state", None)
    cls = type(state)
    if state is None or not dataclasses.is_dataclass(state) or getattr(cls, "__slots__", None) is not None:
        yield None
        return
    seen: set[str] = set()
    target = id(state)
    own = cls.__dict__.get("__setattr__")
    inherited: Any = cls.__setattr__

    def record(obj: Any, name: str, value: Any) -> None:
        if id(obj) == target:
            seen.add(name)
        inherited(obj, name, value)

    setattr(cls, "__setattr__", record)   # noqa: B010 - a method is assigned, mypy refuses the plain form
    try:
        yield seen
    finally:
        if own is None:
            delattr(cls, "__setattr__")
        else:
            setattr(cls, "__setattr__", own)   # noqa: B010


async def poll(dev: Any) -> tuple[Any, frozenset[str] | None]:
    """``await dev.update()``, and the fields it assigned (:func:`assignments`)."""
    with assignments(dev) as seen:
        state = await dev.update()
    return state, None if seen is None else frozenset(seen)


# -- which fields a poll carried ------------------------------------------------------------------


def _decode_table(profile: Any, name: str) -> dict[str, str]:
    """``{"0x15": color_decode, ...}`` of a mode-colour parse name."""
    if name == "rgb_kelvin":
        return dict(_DEFAULT_PARSE)
    try:
        from govee_ble_local._generated import devices as table
    except ImportError:
        return {}
    strategy = getattr(table, "MODE_COLOR_PARSE", {}).get(name) or {}
    subs = dict(strategy.get("sub_modes") or {})
    if getattr(profile, "bar_switch", False):
        subs["0x15"] = "gradual_kelvin_pair_s16be"
    return subs


def _colour_fields(profile: Any, parse_name: str, sub: int | None, warnings: list[str]) -> tuple[str, ...]:
    """The colour fields a mode report of sub-mode ``sub`` sets under the profile's parse."""
    if sub is None:
        return ()
    if sub == SCENE_SUB:   # a scene report clears both colours
        return ("rgb_color", "color_temp_kelvin")
    decode = _decode_table(profile, parse_name).get(f"0x{sub:02x}")
    if decode is None:
        return ()
    fields = DECODE_FIELDS.get(decode)
    if fields is None:
        warnings.append(f"colour decode {decode!r} unknown to the tool: counted as no colour read-back")
        return ()
    if "color_temp_kelvin" in fields and not getattr(profile, "mode_reports_kelvin", True):
        fields = tuple(f for f in fields if f != "color_temp_kelvin")
    return fields


def _status_reads(dev: Any) -> tuple[int, ...]:
    """The TLVs a status row's 0xAC read asks for at the device's pact."""
    p = dev.profile
    reads = tuple(getattr(p, "status_read", ()))
    for pact, types in getattr(p, "status_read_by_pact", ()) or ():
        if dev.pact is not None and tuple(pact) == tuple(dev.pact):
            reads = tuple(types)
    gate = getattr(p, "status_color_groups_pact", None)
    if gate is not None and dev.pact is not None and not (dev.pact[0] == gate[0] and dev.pact[1] >= gate[1]):
        reads = tuple(t for t in reads if t != 0xA5)
    return reads


def _brightness_pending(dev: Any) -> bool:
    p = dev.profile
    return bool(getattr(p, "brightness_by_pact", ())) and dev.pact is None


def carried(dev: Any, frames: Iterable[Frame], warnings: list[str] | None = None, *,
            assigned: Iterable[str] | None = None) -> dict[str, str | None]:
    """``{field: None}`` for each state field the poll's replies (``frames``, one poll's window) carried and
    the poll set (``assigned``, :func:`poll`; None: unknown, so nothing counts), ``{field: reason}`` for the
    others: what a step on this row can be judged on. A colour the poll's mode report set to a value counts
    too where the decode table here says that report can't carry it: the library read it (a newer library's
    fold, such as a Kelvin tint read as its colour temperature)."""
    warnings = [] if warnings is None else warnings
    frames = list(frames)
    out = _carried(dev, frames, warnings)
    if assigned is None:
        return {f: reason if reason is not None else NOT_SEEN for f, reason in out.items()}
    seen = set(assigned)
    state = getattr(dev, "state", None)
    for field, reason in out.items():
        if reason is None and field not in seen:
            out[field] = NOT_SET
        elif reason is not None and field in COLOURS and field in seen and _mode_reported(dev, frames) \
                and getattr(state, field, None) is not None:
            out[field] = None
    return out


def _mode_reported(dev: Any, frames: Iterable[Frame]) -> bool:
    """The poll brought a mode report: an ``aa 05`` reply, or a status burst with its 0x05 TLV."""
    if getattr(getattr(dev, "profile", None), "readback", "none") not in ("polled", "status"):
        return False
    return any(f.dir == "rx" and (f.op == "aa05" or (f.op == "ac" and f.tags is not None and 0x05 in f.tags))
               for f in frames)


def _carried(dev: Any, frames: list[Frame], warnings: list[str]) -> dict[str, str | None]:
    """:func:`carried` by the frames alone: which fields this poll's replies can carry on this row."""
    out: dict[str, str | None] = {f: "not read back on this row" for f in FIELDS}
    p = getattr(dev, "profile", None)
    if p is None:
        return {f: "no device profile" for f in FIELDS}
    rx = [f for f in frames if f.dir == "rx"]
    ops = {f.op for f in rx}

    def got(field: str, ok: bool, reason: str) -> None:
        if out.get(field) is None:
            return
        out[field] = None if ok else reason

    readback = getattr(p, "readback", "none")
    if readback == "none":
        return {f: "no read-back on this row" for f in FIELDS}
    if readback == "plug":
        got("is_on", "aa01" in ops, "no aa 01 relay reply in this poll")
        return out
    if readback == "polled":
        power_ops = {"aa01"} | ({"aa34"} if getattr(p, "detail_power_read", False) else set())
        power_ops |= {"aa36"} if p.bar_switch else set()
        got("is_on", bool(ops & power_ops), "no power reply in this poll")
        if _brightness_pending(dev):
            out["brightness"] = "brightness range waits for the advertised pact"
        else:
            got("brightness", "aa04" in ops, "no aa 04 brightness reply in this poll")
        modes = [f for f in rx if f.op == "aa05"]
        got("scene_code", bool(modes), "no aa 05 mode reply in this poll")
        if modes:
            for field in _colour_fields(p, p.mode_color_parse, modes[-1].sub, warnings):
                got(field, True, "")
        if out["rgb_color"] is not None:
            out["rgb_color"] = ("the mode reply carries no RGB" if modes
                                else "no aa 05 mode reply in this poll")
        if out["color_temp_kelvin"] is not None:
            out["color_temp_kelvin"] = ("the mode reply carries no Kelvin" if modes
                                        else "no aa 05 mode reply in this poll")
        if getattr(p, "color_readback", ""):
            got("segments", bool(ops & {"aaa5", "aaa2"}), "no segment colour replies in this poll")
        if p.bar_switch:
            got("zone_power", "aa36" in ops, "no aa 36 bar reply in this poll")
            bars = [f for f in modes if f.sub == 0x15]
            got("zone_color_temp_kelvin", bool(bars), "no aa 05 15 bar Kelvin reply in this poll")
        else:
            out["zone_color_temp_kelvin"] = "not read back on this row"
        if p.gradual:
            got("gradual", "aaa3" in ops, "no aa a3 gradual reply in this poll")
        return out
    # readback == "status": one 0xAC burst carries what its TLVs hold
    bursts = [f for f in rx if f.op == "ac"]
    if not bursts:
        return {f: "no 0xAC status reply in this poll" for f in FIELDS}
    burst = bursts[-1]
    decode = dict(getattr(p, "status_decode", ()))
    reads = _status_reads(dev)
    tags = burst.tags
    parse_name = getattr(p, "mode_color_parse_status", None) or p.mode_color_parse
    out["is_on"] = None   # a reply without TLV 0x01 reads off, as the app's does
    if tags is None:      # the burst could not be joined: the request's types, the scheme's sub-mode, and
        # only where the poll set them (carried)
        warnings.append("a status burst could not be joined: fields counted from the request")
        got("brightness", not _brightness_pending(dev), "brightness range waits for the advertised pact")
        out["scene_code"] = None
        for field in _colour_fields(p, parse_name, _SCHEME_SUB.get(p.color_scheme), warnings):
            got(field, True, "")
        got("segments", 0xA5 in reads and decode.get("f_a5") == "color_group", "no colour groups read")
        got("zone_power", 0x30 in reads and "f_30" in decode, "no zone TLV read")
        return out
    if _brightness_pending(dev):
        out["brightness"] = "brightness range waits for the advertised pact"
    else:
        got("brightness", 0x04 in tags, "no brightness TLV in the status reply")
    got("scene_code", 0x05 in tags and burst.sub is not None, "no mode TLV in the status reply")
    if 0x05 in tags:
        for field in _colour_fields(p, parse_name, burst.sub, warnings):
            got(field, True, "")
    got("segments", 0xA5 in tags and decode.get("f_a5") == "color_group",
        "no colour-group TLV in the status reply")
    got("zone_power", 0x30 in tags and "f_30" in decode, "no zone TLV in the status reply")
    got("gradual", bool(p.gradual) and 0xA3 in tags and "f_a3" in decode,
        "no gradual TLV in the status reply")
    return out


# -- verdicts -------------------------------------------------------------------------------------


@dataclasses.dataclass
class Judgement:
    verdict: Verdict
    basis: str | None = None        # "readback", "observed", "refusal", "expect"
    reason: str | None = None
    fields: dict[str, str] = dataclasses.field(default_factory=dict)


def _raw_brightness(dev: Any) -> bool:
    p = getattr(dev, "profile", None)
    return bool(getattr(p, "brightness_range", None) or getattr(p, "brightness_by_pact", ()))


def _segments(state: Any) -> dict[int, Any]:
    return {s.index: s for s in (getattr(state, "segments", None) or [])}


def _same(field: str, want: Any, got: Any, *, raw: bool) -> bool:
    if field == "brightness" and raw and isinstance(want, int) and isinstance(got, int):
        return abs(want - got) <= 1
    if isinstance(want, tuple) or isinstance(got, tuple):
        return want is not None and got is not None and tuple(want) == tuple(got)
    return bool(want == got)


UNCHANGED = "the step left it as it was, so the read can't tell the write from none"
CCT_BLIND = "the mode report shows no colour temperature, so its RGB can't tell a Kelvin write"


def _value(attr: str, seg: Any) -> Any:
    value = getattr(seg, attr, None)
    return tuple(value) if attr == "rgb" and value is not None else value


def _judge_segments(step: Step, pre: Any, intent: Any, post: Any, attr: str) -> tuple[str, str | None]:
    """A segment step: the touched indices as intended, the others as they read before the step. Touched
    segments that already read as intended before it prove nothing by reading so again."""
    after, wanted, before = _segments(post), _segments(intent), _segments(pre)
    if not after:
        return "not read: no segments in the read-back", None
    bad: list[str] = []
    for i, seg in sorted(after.items()):
        ref = wanted.get(i) if i in step.touched else before.get(i)
        if ref is None:
            continue
        want, got = getattr(ref, attr), getattr(seg, attr)
        if want is not None and not _same(attr, tuple(want) if attr == "rgb" else want,
                                          tuple(got) if attr == "rgb" and got else got, raw=False):
            bad.append(f"segment {i}: {got} (wanted {want})")
    missing = [i for i in step.touched if i not in after]
    if missing:
        bad.append(f"segments {missing} missing from the read-back")
    if bad:
        return "fail", "; ".join(bad[:4])
    if step.kind == "write" and all(i in before and i in wanted and _value(attr, before[i]) is not None
                                    and _value(attr, before[i]) == _value(attr, wanted[i])
                                    for i in step.touched):
        return f"not read: {UNCHANGED}", None
    return "pass", None


def judge_fields(step: Step, dev: Any, pre: Any, intent: Any, post: Any,
                 carried_map: Mapping[str, str | None]) -> tuple[dict[str, str], list[str]]:
    """``{field: "pass" | "fail" | "not read: <reason>"}`` for the step's fields, and the failures."""
    out: dict[str, str] = {}
    fails: list[str] = []
    raw = _raw_brightness(dev)
    for field in step.fields:
        if field in ("segments", "segment_brightness"):
            if carried_map.get("segments") is not None:
                out[field] = f"not read: {carried_map['segments']}"
                continue
            verdict, why = _judge_segments(step, pre, intent, post,
                                           "brightness" if field == "segment_brightness" else "rgb")
            out[field] = verdict
            if why:
                fails.append(f"{field}: {why}")
            continue
        reason = carried_map.get(field, "not a state field")
        if field == "rgb_color" and reason is not None and carried_map.get("segments") is None:
            # read back through the segment colours: every segment the colour the library intended
            want = getattr(intent, "rgb_color", None)
            segs = _segments(post)
            if want is None or not segs:
                out[field] = "not read: " + ("no RGB intended" if want is None else "no segments read")
                continue
            off = [i for i, s in segs.items() if s.rgb is None or tuple(s.rgb) != tuple(want)]
            was = _segments(pre)
            if not off and step.kind == "write" and was and all(
                    s.rgb is not None and tuple(s.rgb) == tuple(want) for s in was.values()):
                out[field] = f"not read: {UNCHANGED}"
                continue
            out[field] = "fail" if off else "pass"
            if off:
                fails.append(f"rgb_color (segments): {len(off)} of {len(segs)} segments not {tuple(want)}")
            continue
        kelvin_intended = getattr(intent, "color_temp_kelvin", None) is not None
        if field == "rgb_color" and reason is None and kelvin_intended \
                and carried_map.get("color_temp_kelvin") is not None:
            # A colour-temperature write, read back by a report that has no Kelvin (rgb_kelvin_0d_nocct,
            # rgb): it shows an RGB whatever the device does, so that RGB says nothing of the write.
            out[field] = f"not read: {CCT_BLIND}"
            continue
        if reason is not None:
            out[field] = f"not read: {reason}"
            continue
        want, got = getattr(intent, field, None), getattr(post, field, None)
        before = getattr(pre, field, None)
        if field == "zone_power":
            # The library forgets a zone it can no longer vouch for (an H6047 33 01 on forgets the bars):
            # only the zones it holds are judged.
            if not want:
                out[field] = "not read: the library holds no zone power after this write"
                continue
            got = {k: v for k, v in (got or {}).items() if k in want}
            before = {k: v for k, v in (before or {}).items() if k in want}
        if not _same(field, want, got, raw=raw):
            out[field] = "fail"
            fails.append(f"{field}: read {got!r}, intended {want!r}")
        elif step.kind == "write" and pre is not None and _same(field, want, before, raw=raw):
            out[field] = f"not read: {UNCHANGED}"   # e.g. a None intended and already None
        else:
            out[field] = "pass"
    return out, fails


def judge(step: Step, *, sent_error: str | None, field_results: Mapping[str, str], fails: list[str],
          observed: str | None, writes: int, raised: BaseException | None = None,
          expect_match: bool | None = None, check: tuple[Verdict, str | None] | None = None) -> Judgement:
    """The step's overall verdict.

    * fail: a send error, a field mismatch, a failed check (``expect`` / ``check``), the tester answered
      "no", or a refusal broke its contract (it did not raise, or something was sent);
    * pass: a field matched, a check held, a refusal held, or the tester answered "yes" where nothing was
      read back (basis ``observed``);
    * unverifiable: neither."""
    fields = dict(field_results)
    if step.refusal is not None:
        name = step.refusal.__name__
        if raised is not None and isinstance(raised, step.refusal) and writes == 0:
            return Judgement("pass", "refusal", f"{name} raised, nothing sent", fields)
        if raised is not None and isinstance(raised, step.refusal):
            return Judgement("fail", "refusal", f"{name} raised but {writes} frame(s) were sent", fields)
        got = "nothing" if raised is None else type(raised).__name__
        return Judgement("fail", "refusal", f"expected {name} before any I/O, got {got}", fields)
    if sent_error:
        return Judgement("fail", "send", sent_error, fields)
    if fails:
        return Judgement("fail", "readback", "; ".join(fails), fields)
    if check is not None and check[0] == "fail":
        return Judgement("fail", "check", check[1], fields)
    if expect_match is False:
        return Judgement("fail", "expect", "the plan's read-back check failed", fields)
    if observed == "no":
        return Judgement("fail", "observed", "the tester answered no", fields)
    if check is not None and check[0] == "pass":
        return Judgement("pass", "check", check[1], fields)
    if expect_match is True or any(v == "pass" for v in fields.values()):
        return Judgement("pass", "expect" if expect_match else "readback", None, fields)
    if observed == "yes":
        return Judgement("pass", "observed", None, fields)
    if check is not None and check[1]:
        reason = check[1]
    else:
        reason = "nothing read back" if not fields else "no field read back"
    if observed == "skipped":
        reason += "; the tester skipped the question"
    return Judgement("unverifiable", None, reason, fields)
