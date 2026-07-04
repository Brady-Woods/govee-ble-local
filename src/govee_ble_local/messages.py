"""Single source of truth for the Govee BLE wire format: one place that both
**builds** commands (Python -> bytes) and **decodes** frames (bytes -> Python).

Before this module the encode side lived in ``protocol.py`` (``cmd_*``
builders) and a separate decode side lived in ``tools/decode_btsnoop.py``
(``_decode_*``) - two encodings of the same facts that drifted apart. Now:

- ``build_*`` functions are the encode source of truth; ``protocol.py``'s
  ``cmd_*`` are thin wrappers over them (byte-identical output, preserved by
  ``tests/test_protocol.py``).
- ``deserialize()`` is the decode source of truth; the btsnoop tool calls it
  instead of carrying its own decoder.
- ``ChunkReassembler`` reassembles the multi-frame exchanges (status/metadata/
  scene) using the real parsers in ``protocol.py``.

Sendability is gated: only frame types we actually understand well enough to
originate have a ``build_*`` and are marked ``sendable``. Opcodes we've *seen
but don't understand* (clock ``0x33 0x09``, wifi-provisioning ``0xA1``, and the
still-opaque ``0xEE``/``0xA4``) are **stubs**: recognized and decoded on
receive, but never constructible/sendable. On receive, anything not understood
(stub or genuinely novel) is meant to be logged and dropped - see
``dispatch_incoming``.

Pure/offline: imports only ``protocol`` primitives + ``const`` (no bleak).
``protocol`` imports this module lazily (inside its ``cmd_*`` wrappers) so the
import graph stays acyclic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from .const import (
    MAX_COLOR_TEMP_KELVIN,
    MIN_COLOR_TEMP_KELVIN,
    SEGMENT_COUNT,
    STATUS_CHUNK_ACCEPTED_FULL,
    STATUS_CHUNK_REQUIRED,
)
from .protocol import kelvin_to_rgb, parse_metadata_field_text, parse_status

_LOGGER = logging.getLogger(__name__)

FRAME_LEN = 20  # 1 opcode + <=18 payload + 1 XOR checksum byte

# Opcodes confirmed to carry sensitive data whose *content* must never be
# rendered, logged, or written anywhere - even in "raw" output. 0xA1 carries
# the device's WiFi SSID + password in plaintext (see PROTOCOL.md §12.5). The
# btsnoop tool also redacts these opcodes' raw hex bytes (its own defence in
# depth); this module simply never puts their content into a summary.
REDACT_OPCODES: frozenset[int] = frozenset({0xA1})
REDACTED_PLACEHOLDER = "<REDACTED - see REDACT_OPCODES in messages.py>"


class UnsupportedCommand(RuntimeError):
    """Raised when asked to build/send a frame type that is not ``sendable``
    (a stub, or an opcode we recognize on receive but don't originate)."""


@dataclass(frozen=True)
class DecodedMessage:
    """The result of decoding one 20-byte Govee frame.

    ``understood`` is the receive-side gate: a message that is *not* understood
    (a stub, or a genuinely unknown opcode) should be logged and dropped rather
    than acted on. ``sendable`` is the transmit-side gate: only understood frame
    types we can correctly originate are sendable. ``fields`` carries real
    decoded values (never placeholders); for reassembled aggregates it may hold
    a structured object such as ``{"status": GoveeBleStatus(...)}``.
    """

    name: str
    understood: bool
    sendable: bool
    summary: str
    confidence: str = "confirmed"  # "confirmed" | "partial" | "unknown"
    fields: dict[str, Any] = field(default_factory=dict)
    raw: bytes = b""


# --------------------------------------------------------------------------
# Encode: build_* return the pre-framing prefix (opcode + payload). Framing
# (pad to 19 + XOR checksum) is done by protocol.build_plaintext downstream.
# These are the single definition of each command's byte layout.
# --------------------------------------------------------------------------


def build_handshake(step: int) -> bytes:
    return bytes([0xE7, step])


def build_status_query(full: bool = False) -> bytes:
    # Short query -> chunks 0x00-0x04 + 0xFF. Full -> additionally 0x05-0x08
    # (per-segment). Byte-identical to the historic cmd_status_query[_full]().
    if full:
        return bytes([0xAC, 0x03, 0x03, 0x41, 0x30, 0xA5])
    return bytes([0xAC, 0x03, 0x02, 0x41, 0x30])


def build_metadata_query(field_id: int) -> bytes:
    return bytes([0xAB, 0x01, field_id])


def build_power(on: bool) -> bytes:
    return bytes([0x33, 0x01, 1 if on else 0])


def build_zone(zone: int, on: bool) -> bytes:
    return bytes([0x33, 0x30, zone, 1 if on else 0])


def build_brightness(pct: int) -> bytes:
    pct = max(0, min(100, pct))
    return bytes([0x33, 0x04, pct])


def build_rgb(r: int, g: int, b: int) -> bytes:
    return bytes([0x33, 0x05, 0x15, 0x01, r, g, b, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF, 0x1F])


def build_color_temp(kelvin: int) -> bytes:
    kelvin = max(MIN_COLOR_TEMP_KELVIN, min(MAX_COLOR_TEMP_KELVIN, kelvin))
    ar, ag, ab = kelvin_to_rgb(kelvin)
    return bytes(
        [
            0x33, 0x05, 0x15, 0x01,
            0xFF, 0xFF, 0xFF,
            (kelvin >> 8) & 0xFF, kelvin & 0xFF,
            ar, ag, ab,
            0xFF, 0x1F,
        ]
    )


def build_segment_color(segment_mask: int, r: int, g: int, b: int) -> bytes:
    mask_lo = segment_mask & 0xFF
    mask_hi = (segment_mask >> 8) & 0xFF
    return bytes(
        [
            0x33, 0x05, 0x15, 0x01,
            r, g, b,
            0x00, 0x00, 0x00, 0x00, 0x00,
            mask_lo, mask_hi,
            0x00, 0x00, 0x00, 0x00, 0x00,
        ]
    )


def build_segment_brightness(segment_mask: int, pct: int) -> bytes:
    pct = max(0, min(100, pct))
    mask_lo = segment_mask & 0xFF
    mask_hi = (segment_mask >> 8) & 0xFF
    return bytes(
        [
            0x33, 0x05, 0x15, 0x02,
            pct,
            mask_lo, mask_hi,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        ]
    )


def build_scene(scene_id: tuple[int, int]) -> bytes:
    return bytes([0x33, 0x05, 0x04, scene_id[0], scene_id[1]])


# Calibration (rotation adjustment). Sendable per design decision, though the
# device gives no readback to confirm the result. Direction: 0x01 = clockwise,
# 0x02 = counter-clockwise (confirmed against a real cw-then-ccw user action;
# see PROTOCOL.md §12.5).
CALIBRATION_CW = 0x01
CALIBRATION_CCW = 0x02


def build_calibration_enter() -> bytes:
    return bytes([0x33, 0x42, 0x01])


def build_calibration_rotate(direction: int) -> bytes:
    return bytes([0x33, 0x42, 0x02, direction])


def build_calibration_confirm() -> bytes:
    return bytes([0x33, 0x42, 0xFF])


def build_calibration_exit() -> bytes:
    return bytes([0x33, 0x42, 0x00])


# name -> builder, for the generic sendability gate. Names match the ``name``
# field that ``deserialize`` assigns, so a decoded message can be checked for
# round-trip sendability. Stubs and receive-only types are deliberately absent.
_BUILDERS: dict[str, Callable[..., bytes]] = {
    "handshake": build_handshake,
    "status_query": build_status_query,
    "metadata_query": build_metadata_query,
    "power": build_power,
    "zone": build_zone,
    "brightness": build_brightness,
    "color_rgb": build_rgb,
    "color_temp": build_color_temp,
    "segment_color": build_segment_color,
    "segment_brightness": build_segment_brightness,
    "scene_activate": build_scene,
    "calibration": build_calibration_rotate,  # representative; see build_calibration_*
}


def is_sendable(name: str) -> bool:
    """Whether a message ``name`` may be constructed and transmitted."""
    return name in _BUILDERS


def serialize(name: str, *args: Any, **kwargs: Any) -> bytes:
    """Build the pre-framing prefix for a sendable command by name.

    Raises ``UnsupportedCommand`` for stubs / receive-only / unknown names -
    this is the enforcement point for "we don't send what we don't understand".
    """
    builder = _BUILDERS.get(name)
    if builder is None:
        raise UnsupportedCommand(
            f"{name!r} is not a sendable command "
            "(it's a stub, a receive-only message, or unknown)"
        )
    return builder(*args, **kwargs)


# --------------------------------------------------------------------------
# Decode helpers (ported verbatim from the btsnoop tool so summaries match)
# --------------------------------------------------------------------------


def _pad_note(tail: bytes) -> str | None:
    if not tail:
        return None
    if all(b == 0 for b in tail):
        return f"pad({len(tail)}x00)"
    return f"UNKNOWN[{len(tail)}]=0x{tail.hex()}"


def _join(*parts: str | None) -> str:
    return ", ".join(x for x in parts if x)


def _ascii_or_hex(b: bytes) -> str:
    if not b:
        return ""
    stripped = b.rstrip(b"\x00")
    if not stripped:
        return f"pad({len(b)}x00)"
    try:
        s = stripped.decode("ascii")
        if s and all(32 <= ord(c) < 127 for c in s):
            return f'ascii="{s}"' + (f", pad({len(b) - len(stripped)}x00)" if len(b) > len(stripped) else "")
    except UnicodeDecodeError:
        pass
    return f"0x{b.hex()}"


def _format_addr(addr_bytes: bytes) -> str:
    """MAC transmitted least-significant-octet first; reverse for display."""
    return ":".join(f"{x:02x}" for x in reversed(addr_bytes))


def _checksum_ok(pt20: bytes) -> bool:
    x = 0
    for b in pt20[:19]:
        x ^= b
    return len(pt20) == FRAME_LEN and x == pt20[19]


# --------------------------------------------------------------------------
# Decode: deserialize() one 20-byte plaintext frame -> DecodedMessage
# --------------------------------------------------------------------------


def _decode_0x33(payload: bytes, direction: str) -> DecodedMessage:
    """0x33 control family. ``payload`` starts at the sub-command byte.

    A NOTIFY 0x33 with an all-zero payload is a bare ack (same bytes whatever
    was set), so it's classified as an ack rather than mis-read as a value.
    """
    cmd, rest = payload[0], payload[1:]

    if direction == "NOTIFY" and all(b == 0 for b in rest):
        return DecodedMessage(
            "ack", understood=True, sendable=False,
            summary=f"ACK: command 0x{cmd:02X} accepted (bare acknowledgment, not a value)",
            fields={"cmd": cmd},
        )

    if cmd == 0x30 and len(rest) >= 2:  # ZONE
        zone, state = rest[0], rest[1]
        zone_label = {0: "lower", 1: "upper"}.get(zone, f"UNKNOWN(0x{zone:02X})")
        state_label = {0: "OFF", 1: "ON"}.get(state, f"UNKNOWN(0x{state:02X})")
        return DecodedMessage(
            "zone", understood=True, sendable=True,
            summary=_join(f"ZONE {zone_label}: {state_label}", _pad_note(rest[2:])),
            fields={"zone": zone, "on": bool(state)},
        )

    if cmd == 0x42 and rest:  # CALIBRATION (see PROTOCOL.md §12.5)
        subcmd = rest[0]
        if subcmd == 0x01:
            return DecodedMessage("calibration", True, True, _join("CALIBRATION: enter mode", _pad_note(rest[1:])), "partial", {"action": "enter"})
        if subcmd == 0x02 and len(rest) >= 2:
            direction_label = {0x01: "clockwise", 0x02: "counter-clockwise"}.get(rest[1], f"UNKNOWN(0x{rest[1]:02X})")
            return DecodedMessage("calibration", True, True, _join(f"CALIBRATION: rotate {direction_label}", _pad_note(rest[2:])), "partial", {"action": "rotate", "direction": rest[1]})
        if subcmd == 0xFF:
            return DecodedMessage("calibration", True, True, _join("CALIBRATION: confirm/apply", _pad_note(rest[1:])), "partial", {"action": "confirm"})
        if subcmd == 0x00:
            return DecodedMessage("calibration", True, True, _join("CALIBRATION: exit mode", _pad_note(rest[1:])), "partial", {"action": "exit"})
        return DecodedMessage("calibration", False, False, f"CALIBRATION: UNKNOWN sub-command 0x{subcmd:02X}, payload=0x{rest[1:].hex()}", "unknown")

    if cmd == 0x01:  # POWER
        state, tail = rest[0], rest[1:]
        label = {0: "OFF", 1: "ON"}.get(state, f"UNKNOWN(0x{state:02X})")
        return DecodedMessage("power", True, True, _join(f"POWER: {label}", _pad_note(tail)), fields={"on": bool(state)})

    if cmd == 0x04:  # BRIGHTNESS
        pct, tail = rest[0], rest[1:]
        return DecodedMessage("brightness", True, True, _join(f"BRIGHTNESS: {pct}%", _pad_note(tail)), fields={"pct": pct})

    if cmd == 0x05:  # COLOR family - byte after 0x05 selects the sub-mode
        mode = rest[0]
        sub: bytes = rest[1:]

        if mode == 0x0D and len(sub) >= 8:  # H6006 combined plain-RGB / color-temp
            r, g, b = sub[0], sub[1], sub[2]
            kelvin = (sub[3] << 8) | sub[4]
            r2, g2, b2 = sub[5], sub[6], sub[7]
            tail = sub[8:]
            if kelvin:
                return DecodedMessage(
                    "color_temp", True, False,
                    _join(f"COLOR_TEMP: {kelvin}K (tint rgb=({r},{g},{b}), repeated ({r2},{g2},{b2}))", _pad_note(tail)),
                    fields={"kelvin": kelvin},
                )
            extra = None if (r2, g2, b2) == (0, 0, 0) else f"trailing rgb2=({r2},{g2},{b2}) [unexplained, nonzero]"
            return DecodedMessage("color_rgb", True, False, _join(f"COLOR: rgb=({r},{g},{b})", extra, _pad_note(tail)), fields={"rgb": (r, g, b)})

        if mode == 0x04 and len(sub) >= 2:  # scene activation
            code = sub[0] | (sub[1] << 8)
            return DecodedMessage("scene_activate", True, True, _join(f"SCENE ACTIVATE: code={code}", _pad_note(sub[2:])), fields={"code": code})

        if mode == 0x02 and len(sub) >= 3:  # upstream MANUAL plain-color
            r, g, b = sub[0], sub[1], sub[2]
            return DecodedMessage("color_rgb", True, False, _join(f"COLOR (upstream mode=MANUAL/0x02): rgb=({r},{g},{b})", _pad_note(sub[3:])), "partial", {"rgb": (r, g, b)})

        if mode == 0x15:  # H60A6 RGB / color-temp / segment (ambiguous standalone)
            return DecodedMessage("color_rgb", True, False, f"COLOR (H60A6-style mode=SEGMENTS-or-RGB/0x15): payload=0x{sub.hex()}", "partial")

        return DecodedMessage("color_unknown", False, False, f"COLOR: UNKNOWN sub-mode 0x{mode:02X}, payload=0x{sub.hex()}", "unknown")

    if cmd == 0x09 and len(rest) >= 6:  # STUB: clock/time-sync (partly understood, not actionable)
        ts_val = int.from_bytes(rest[0:4], "big")
        extra_bytes = rest[4:6]
        return DecodedMessage(
            "clock", understood=False, sendable=False,
            summary=_join(
                f"UNKNOWN cmd 0x09: bytes[0:4] as big-endian uint32 = {ts_val} (plausible unix timestamp, unconfirmed)",
                f"bytes[4:6]=0x{extra_bytes.hex()} (meaning unknown)",
                _pad_note(rest[6:]),
            ),
            confidence="unknown",
        )

    return DecodedMessage("cmd_unknown", False, False, _join(f"UNKNOWN cmd 0x{cmd:02X}: payload=0x{rest.hex()}"), "unknown")


def _decode_0xAA(payload: bytes) -> DecodedMessage:
    """0xAA status/keepalive family (unencrypted analog of 0xAC). byte[0] is a
    field id; layouts only partly understood."""
    field_id, rest = payload[0], payload[1:]

    if field_id == 0x00:
        return DecodedMessage("heartbeat", True, False, _join("STATUS field 0x00 (H60A6-style heartbeat/keepalive)", _pad_note(rest)), fields={"field": 0x00})

    if field_id == 0x01 and rest:
        val, tail = rest[0], rest[1:]
        return DecodedMessage("heartbeat", True, False, _join(f"STATUS field 0x01 (heartbeat/online poll): value=0x{val:02X} (exact meaning unconfirmed)", _pad_note(tail)), "partial", {"field": 0x01, "value": val})

    if field_id in (0x06, 0x20, 0x21) and rest:
        return DecodedMessage("status_field", True, False, _join(f"STATUS field 0x{field_id:02X} (version string, exact role unconfirmed): {_ascii_or_hex(rest)}"), "partial", {"field": field_id})

    if field_id == 0x07 and rest:
        sub_field, sub_rest = rest[0], rest[1:]
        return DecodedMessage("status_field", True, False, _join(f"STATUS field 0x07 sub=0x{sub_field:02X} (role unconfirmed): {_ascii_or_hex(sub_rest)}"), "partial", {"field": 0x07})

    if field_id == 0x14 and len(rest) >= 6:
        addr = _format_addr(rest[0:6])
        return DecodedMessage("status_field", True, False, _join(f"STATUS field 0x14: device MAC={addr} (byte order/exact field role partly cross-checked)", _pad_note(rest[6:])), "partial", {"field": 0x14, "mac": addr})

    return DecodedMessage("status_field", False, False, f"STATUS field 0x{field_id:02X}: raw=0x{rest.hex()}", "unknown", {"field": field_id})


def _decode_0xAB(payload: bytes, direction: str) -> DecodedMessage:
    """0xAB metadata query (WRITE: byte[1]=field id) / response chunk (NOTIFY:
    byte[1]=chunk sequence, 0xFF=last)."""
    sub_or_seq, rest = payload[0], payload[1:]

    if direction == "WRITE":
        if sub_or_seq == 0x01 and rest:
            field_id, tail = rest[0], rest[1:]
            return DecodedMessage("metadata_query", True, True, _join(f"DEVICE_META query: field=0x{field_id:02X}", _pad_note(tail)), fields={"field": field_id})
        return DecodedMessage("metadata_query", False, False, f"DEVICE_META WRITE: UNKNOWN sub-command 0x{sub_or_seq:02X}, payload=0x{rest.hex()}", "unknown")

    seq_label = "last" if sub_or_seq == 0xFF else str(sub_or_seq)
    return DecodedMessage("metadata_chunk", True, False, f"DEVICE_META response chunk seq={seq_label}: {_ascii_or_hex(rest)}", fields={"seq": sub_or_seq})


def _decode_0xA3(payload: bytes) -> DecodedMessage:
    """0xA3 scene/effect upload chunk (opaque binary payload)."""
    seq, rest = payload[0], payload[1:]
    seq_label = "last" if seq == 0xFF else str(seq)
    return DecodedMessage("scene_data", True, False, f"SCENE_DATA chunk seq={seq_label}: 0x{rest.hex()}", fields={"seq": seq})


def _decode_0xA1(payload: bytes) -> DecodedMessage:
    """0xA1 WiFi-provisioning exchange. SENSITIVE: carries SSID + password in
    plaintext. This never decodes or displays chunk content - only structural
    shape - and is not understood/actionable (dropped on receive)."""
    const, rest = payload[0], payload[1:]
    if not rest:
        return DecodedMessage("wifi_provision", False, False, "STRING_EXCHANGE: unexpected shape (REDACTED - see PROTOCOL.md §12.5)", "unknown")
    seq, data = rest[0], rest[1:]
    if seq == 0x00 and data:
        count = data[0]
        return DecodedMessage("wifi_provision", False, False, f"STRING_EXCHANGE header: const=0x{const:02X}, item_count={count} (REDACTED - see PROTOCOL.md §12.5)", "partial")
    if seq == 0xFF:
        return DecodedMessage("wifi_provision", False, False, f"STRING_EXCHANGE terminator (const=0x{const:02X})", "partial")
    return DecodedMessage("wifi_provision", False, False, f"STRING_EXCHANGE data chunk seq={seq}, {len(data)} bytes (REDACTED - carries WiFi credentials, see PROTOCOL.md §12.5)", "partial")


def _decode_0xAC(payload: bytes, direction: str) -> DecodedMessage:
    """0xAC status query (WRITE trigger) / response chunk (NOTIFY, tag in
    byte[0], 0xFF=last). A single chunk isn't meaningful on its own - the
    ChunkReassembler joins the set and calls protocol.parse_status - but the
    frame *type* is recognized and routed."""
    if direction == "WRITE":
        return DecodedMessage("status_query", True, True, f"STATUS query trigger: 0x{payload.hex()}", fields={"full": len(payload) >= 2 and payload[1:2] == b"\x03"})
    tag = payload[0]
    tag_label = "last" if tag == 0xFF else f"0x{tag:02X}"
    return DecodedMessage("status_chunk", True, False, f"STATUS chunk tag={tag_label} (reassembled across chunks - see aggregate)", "partial", {"tag": tag})


def _stub(name: str, opcode: int, payload: bytes) -> DecodedMessage:
    """A registered stub: an opcode we've *seen but don't understand*. Named
    (so logs are friendly) but never understood/sendable."""
    return DecodedMessage(name, False, False, f"STUB opcode 0x{opcode:02X} (seen but not understood): payload=0x{payload.hex()}", "unknown")


def deserialize(frame: bytes, direction: str = "WRITE") -> DecodedMessage:
    """Decode one 20-byte plaintext Govee frame into a ``DecodedMessage``.

    ``direction`` ("WRITE" host->device, "NOTIFY" device->host) disambiguates
    frame shapes that mean different things per side (acks, 0xAB, 0xAC).
    """
    if len(frame) != FRAME_LEN:
        return DecodedMessage("malformed", False, False, f"non-standard length ({len(frame)} bytes): 0x{frame.hex()}", "unknown", raw=frame)

    opcode, payload = frame[0], frame[1:19]
    checksum_note = None if _checksum_ok(frame) else f"CHECKSUM MISMATCH (got 0x{frame[19]:02X})"

    if opcode == 0x33:
        d = _decode_0x33(payload, direction)
    elif opcode == 0xAC:
        d = _decode_0xAC(payload, direction)
    elif opcode == 0xAA:
        d = _decode_0xAA(payload)
    elif opcode == 0xAB:
        d = _decode_0xAB(payload, direction)
    elif opcode == 0xA3:
        d = _decode_0xA3(payload)
    elif opcode == 0xA1:
        d = _decode_0xA1(payload)
    elif opcode == 0xE7:
        d = DecodedMessage("handshake", True, True, f"HANDSHAKE step={payload[0]}: {payload[1:].hex()}", fields={"step": payload[0]})
    elif opcode == 0xEE:
        d = _stub("stub_ee", 0xEE, payload)
    elif opcode == 0xA4:
        d = _stub("stub_a4", 0xA4, payload)
    else:
        d = DecodedMessage("unknown", False, False, f"UNKNOWN opcode 0x{opcode:02X}: payload=0x{payload.hex()}", "unknown")

    if checksum_note:
        d = DecodedMessage(d.name, False, d.sendable, _join(d.summary, checksum_note), "unknown", d.fields, frame)
        return d
    return DecodedMessage(d.name, d.understood, d.sendable, d.summary, d.confidence, d.fields, frame)


def dispatch_incoming(frame: bytes, direction: str = "NOTIFY") -> DecodedMessage:
    """Receive-side entry point. Decodes ``frame`` and, if it is not understood
    (a stub or a genuinely unknown opcode), **logs it and returns** so the
    caller can drop it ("move on without doing anything"). WiFi-provisioning
    content is never logged. Understood messages are returned for routing.
    """
    msg = deserialize(frame, direction)
    if not msg.understood:
        if msg.name in ("wifi_provision",) or msg.raw[:1] in (b"\xa1",):
            _LOGGER.debug("Dropping un-actionable incoming %s (content redacted)", msg.name)
        elif msg.name in ("stub_ee", "stub_a4", "clock"):
            _LOGGER.debug("Dropping recognized-but-un-actionable incoming %s: %s", msg.name, frame.hex())
        else:
            _LOGGER.info("Dropping unrecognized incoming frame: %s", frame.hex())
    return msg


# --------------------------------------------------------------------------
# Multi-packet chunk reassembly (0xAC / 0xAB / 0xA3 / 0xA1)
# --------------------------------------------------------------------------


class ChunkReassembler:
    """Buffers a device's multi-frame chunked exchanges across a connection and,
    once a group completes, returns one aggregate ``DecodedMessage`` - reusing
    ``protocol.parse_status`` / ``protocol.parse_metadata_field_text`` rather
    than re-deriving meaning. The structured result is placed in ``.fields``
    (``{"status": GoveeBleStatus}`` / ``{"field": id, "text": str|None}``) so
    the client can consume it, while ``.summary`` serves human/tool output.

    Feed every frame in chronological order (both the outbound WRITE trigger -
    which tells 0xAC whether a full/segment read is expected - and inbound
    NOTIFY chunks) via ``feed(direction, frame)``. A non-None return completed a
    sequence. Call ``flush()`` at end of stream to report incomplete groups.
    """

    def __init__(self, address: str):
        self._address = address
        self._ac_buf: dict[int, bytes] = {}
        self._ac_full = False
        self._ab_buf: dict[int, bytes] = {}
        self._ab_field_id: int | None = None
        self._a3_buf: dict[int, bytes] = {}

    def feed(self, direction: str, data: bytes) -> DecodedMessage | None:
        if len(data) != FRAME_LEN:
            return None
        opcode, payload = data[0], data[1:19]
        if opcode == 0xAC:
            return self._feed_ac(direction, payload)
        if opcode == 0xAB:
            return self._feed_ab(direction, payload)
        if opcode == 0xA3:
            return self._feed_a3(direction, payload)
        if opcode == 0xA1:
            return self._feed_a1(direction, payload)
        return None

    def flush(self) -> list[DecodedMessage]:
        notes: list[DecodedMessage] = []
        if self._ac_buf:
            notes.append(DecodedMessage("status_incomplete", False, False, f"STATUS query never completed - only got tags {sorted(self._ac_buf.keys())} before the capture ended", "unknown"))
        if self._ab_buf:
            notes.append(DecodedMessage("metadata_incomplete", False, False, f"DEVICE_META response never completed - only got chunks {sorted(self._ab_buf.keys())} before the capture ended", "unknown"))
        if self._a3_buf:
            notes.append(DecodedMessage("scene_incomplete", False, False, f"SCENE_DATA upload never completed - only got chunks {sorted(self._a3_buf.keys())} before the capture ended", "unknown"))
        return notes

    # -- 0xAC status via protocol.parse_status ------------------------------

    def _feed_ac(self, direction: str, payload: bytes) -> DecodedMessage | None:
        tag, body = payload[0], payload[1:]
        if direction == "WRITE":
            if tag == 0x03 and body:  # trigger; body[0]==0x03 -> full/segment read
                self._ac_full = body[0] == 0x03
                self._ac_buf = {}
            return None
        if tag in self._ac_buf:
            abandoned = DecodedMessage("status_incomplete", False, False, f"STATUS query interrupted before completing - only got tags {sorted(self._ac_buf.keys())}", "unknown")
            self._ac_buf = {tag: body}
            return abandoned
        self._ac_buf[tag] = body
        required = STATUS_CHUNK_ACCEPTED_FULL if self._ac_full else STATUS_CHUNK_REQUIRED
        if set(required).issubset(self._ac_buf):
            return self._finalize_ac()
        return None

    def _finalize_ac(self) -> DecodedMessage:
        buf, self._ac_buf = self._ac_buf, {}
        status = parse_status(self._address, buf)
        parts = []
        if status.zone_lower_on is not None or status.zone_upper_on is not None:
            parts.append(f"zones(lower={status.zone_lower_on}, upper={status.zone_upper_on})")
        if status.brightness_pct is not None:
            parts.append(f"brightness={status.brightness_pct}%")
        if status.scene_id is not None:
            parts.append(f"scene_id={status.scene_id}")
        if status.hardware_version:
            parts.append(f"hw={status.hardware_version}")
        if status.ble_mac:
            parts.append(f"ble_mac={status.ble_mac}")
        if status.wifi_mac:
            parts.append(f"wifi_mac={status.wifi_mac}")
        if status.segments:
            parts.append(f"{len(status.segments)} segments")
        fields = ", ".join(parts) if parts else "no fields parsed"
        return DecodedMessage("status", True, False, f"STATUS reassembled ({len(buf)} chunks, via protocol.parse_status): {fields}", fields={"status": status})

    # -- 0xAB metadata via protocol.parse_metadata_field_text ---------------

    def _feed_ab(self, direction: str, payload: bytes) -> DecodedMessage | None:
        sub_or_seq, body = payload[0], payload[1:]
        if direction == "WRITE":
            if sub_or_seq == 0x01 and body:
                self._ab_field_id = body[0]
                self._ab_buf = {}
            return None
        self._ab_buf[sub_or_seq] = body
        if sub_or_seq == 0xFF:
            return self._finalize_ab()
        return None

    def _finalize_ab(self) -> DecodedMessage:
        buf, self._ab_buf = self._ab_buf, {}
        field_id, self._ab_field_id = self._ab_field_id, None
        ordered = sorted(k for k in buf if k != 0xFF)
        if 0xFF in buf:
            ordered.append(0xFF)
        raw = b"".join(buf[k] for k in ordered)
        text = parse_metadata_field_text(raw)
        field_label = f"field=0x{field_id:02X}" if field_id is not None else "field=? (query not observed)"
        if text is not None:
            return DecodedMessage("metadata", True, False, f"DEVICE_META reassembled ({field_label}, {len(buf)} chunks, via protocol.parse_metadata_field_text): {_ascii_or_hex(text.encode())}", fields={"field": field_id, "text": text})
        return DecodedMessage("metadata", True, False, f"DEVICE_META reassembled ({field_label}, {len(buf)} chunks): did not parse as clean ASCII - raw=0x{raw.hex()}", "partial", {"field": field_id, "text": None})

    # -- 0xA3 scene upload (structure only) ---------------------------------

    def _feed_a3(self, direction: str, payload: bytes) -> DecodedMessage | None:
        seq, body = payload[0], payload[1:]
        self._a3_buf[seq] = body
        if seq == 0xFF:
            return self._finalize_a3()
        return None

    def _finalize_a3(self) -> DecodedMessage:
        buf, self._a3_buf = self._a3_buf, {}
        ordered = sorted(k for k in buf if k != 0xFF)
        if 0xFF in buf:
            ordered.append(0xFF)
        raw = b"".join(buf[k] for k in ordered)
        if len(raw) < 2:
            return DecodedMessage("scene", False, False, f"SCENE_DATA reassembled: too short ({len(raw)} bytes) to have a header", "unknown")
        header_const, declared_count, effect_data = raw[0], raw[1], raw[2:]
        note = "" if header_const == 0x01 else f", unexpected header const 0x{header_const:02X} (expected 0x01)"
        return DecodedMessage("scene", True, False, f"SCENE_DATA reassembled ({len(ordered)} chunks, framing matches protocol.build_scene_chunks): declared_chunk_count={declared_count}{note}, {len(effect_data)}-byte effect payload (opaque binary - see PROTOCOL.md §6/§11.2 for what's known about scene data semantics)")

    # -- 0xA1 wifi provisioning: never reassemble/reveal content ------------

    def _feed_a1(self, direction: str, payload: bytes) -> DecodedMessage | None:
        if len(payload) < 2:
            return None
        if payload[1] == 0xFF:
            return DecodedMessage("wifi_provision", False, False, "STRING_EXCHANGE reassembly complete (content redacted - see PROTOCOL.md §12.5 and REDACT_OPCODES)", "partial")
        return None


# --------------------------------------------------------------------------
# Capability inference: which message name implies which device capability.
# Single source reused by the config-generator so it stays in sync with decode.
# --------------------------------------------------------------------------

CAPABILITY_BY_MESSAGE: dict[str, str] = {
    "brightness": "brightness",
    "color_rgb": "rgb",
    "color_temp": "color_temp",
    "zone": "zones",
    "segment_color": "segments",
    "segment_brightness": "segments",
    "scene_activate": "scenes",
    "scene_data": "scenes",
    "scene": "scenes",
}

DEFAULT_SEGMENT_COUNT = SEGMENT_COUNT
