#!/usr/bin/env python3
"""Parse a btsnoop_hci.log and decode Govee BLE traffic frame-by-frame.

Three layers, each usable on its own:

- ``iter_hci_records`` / ``iter_att_events`` - pure btsnoop/HCI/L2CAP/ATT
  parsing (no Bluetooth stack, no external tools). Tags every ATT PDU with
  its connection handle and direction (WRITE = host->device, NOTIFY/RESP =
  device->host), and resolves connection handles to BD addresses and
  advertised local names from the same capture, so multi-device sessions can
  be told apart.
- ``decode_frame`` - given a 20-byte Govee frame's plaintext bytes, returns a
  ``DecodedMessage`` with a real-value, no-placeholder summary. Confidence is
  tracked per finding ("confirmed" = matches this library's own protocol.py
  or a documented upstream source; "partial"/"unknown" = observed but not
  yet corroborated - never silently guessed as fact).
- ``format_raw_line`` / ``format_annotated_line`` - fixed-width formatting so
  a monospace viewer lines up the comment column across an entire file.

Encryption: ``decrypt_all`` auto-detects per connection whether traffic is
AES/RC4-encrypted (H60A6-style, via the ``0xE7`` handshake) or sent in the
clear (older plaintext-protocol devices, e.g. H6006) - a plaintext frame's
raw ATT value already satisfies the checksum with no decryption needed.

Usage (quick single-device look, no splitting/heartbeat handling):
    python3 tools/decode_btsnoop.py path/to/btsnoop_hci.log

For multi-device extraction with raw + annotated output files, see
``extract_govee_session.py``, which is built on top of this module.
"""
from __future__ import annotations

import pathlib
import re
import struct
import sys
from dataclasses import dataclass, field

# Run from the repo without an install.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from govee_ble_local import protocol as p  # noqa: E402
from govee_ble_local.const import PSK  # noqa: E402
from govee_ble_local.messages import (  # noqa: E402
    FRAME_LEN,
    REDACT_OPCODES,
    REDACTED_PLACEHOLDER,
    ChunkReassembler,
    DecodedMessage,
)
from govee_ble_local.messages import deserialize as decode_message  # noqa: E402

BTSNOOP_EPOCH_OFFSET = 0x00E03AB44A676000

# All Govee frame decode + reassembly now lives in the library codec
# (govee_ble_local.messages); this tool is only the capture-file layer
# (btsnoop -> HCI -> ATT), plus formatting. FRAME_LEN / REDACT_OPCODES /
# REDACTED_PLACEHOLDER / ChunkReassembler are re-exported from there so
# extract_govee_session.py keeps importing them from this module.

# --------------------------------------------------------------------------
# Layer 1: btsnoop file -> raw HCI records
# --------------------------------------------------------------------------


@dataclass
class HciRecord:
    t_unix: float
    direction: str  # "sent" (host->controller) or "rcvd" (controller->host), per btsnoop flags bit0
    pkt_type: int  # 0x01 Command, 0x02 ACL Data, 0x03 SCO Data, 0x04 Event
    body: bytes  # HCI packet, without the leading pkt_type byte


def iter_hci_records(path: str | pathlib.Path | list[str | pathlib.Path]):
    """Yield HCI records from one file, or chronologically chain several.

    Android keeps only 2 btsnoop generations (``btsnoop_hci.log.last`` then
    ``btsnoop_hci.log``, oldest first) and rotates on every Bluetooth radio
    restart. A single BLE connection can legitimately span that rotation
    boundary - analyzing the two files independently breaks connection-handle
    continuity for it: the newer file alone never sees that connection's
    "Connection Complete" event, so its address is unresolvable for the
    entire time it appears there (confirmed in practice - a real Finger
    Sketch session's later half went "unidentified" this way until both
    files were fed through as one chronological stream). Pass a list in
    oldest-to-newest order - i.e. ``[btsnoop_hci.log.last, btsnoop_hci.log]``
    - to get correct, continuous handle resolution across the boundary.
    """
    paths = [path] if isinstance(path, (str, pathlib.Path)) else path
    for one_path in paths:
        data = pathlib.Path(one_path).read_bytes()
        offset = 16  # skip the 16-byte btsnoop file header ("btsnoop\0" + version + datalink type)
        while offset + 24 <= len(data):
            orig_len, incl_len, flags, drops, ts = struct.unpack(">IIIIq", data[offset : offset + 24])
            offset += 24
            payload = data[offset : offset + incl_len]
            offset += incl_len
            if not payload:
                continue
            pkt_type, body = payload[0], payload[1:]
            unix_s = (ts - BTSNOOP_EPOCH_OFFSET) / 1e6
            direction = "rcvd" if flags & 0x01 else "sent"
            yield HciRecord(unix_s, direction, pkt_type, body)


# --------------------------------------------------------------------------
# Layer 1b: HCI events -> connection handle <-> BD address <-> advertised name
# --------------------------------------------------------------------------


def _format_addr(addr_bytes: bytes) -> str:
    """BD_ADDR is transmitted least-significant-octet first; reverse for display."""
    return ":".join(f"{b:02x}" for b in reversed(addr_bytes))


_EMBEDDED_NAME_RE = re.compile(rb"(Govee_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*|GVH[0-9A-Za-z]+|ihoment_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)")


def extract_embedded_name(value: bytes) -> str | None:
    """Look for a Govee-style device name embedded anywhere in raw ATT bytes.

    Every BLE connection performs standard GATT service discovery, which
    includes reading the Generic Access Service's Device Name characteristic
    (e.g. a "Read By Type Response" carrying literal ASCII like
    "Govee_H6052_3477") - present regardless of whether this capture also
    saw an advertising packet for the connecting address. This matters
    because some devices/reconnects use a different (rotating/private) BLE
    address than whatever address their advertisement was seen under, which
    defeats address-keyed name resolution entirely - this is a second,
    independent identity source keyed by nothing but the bytes on the wire.
    """
    m = _EMBEDDED_NAME_RE.search(value)
    if not m:
        return None
    try:
        return m.group(1).decode("ascii")
    except UnicodeDecodeError:
        return None


def _parse_ad_structures(data: bytes) -> str | None:
    """Pull a Complete/Shortened Local Name (AD types 0x09/0x08) out of an
    advertising payload, if present."""
    name = None
    pos = 0
    while pos < len(data):
        length = data[pos]
        if length == 0 or pos + 1 + length > len(data):
            break
        ad_type = data[pos + 1]
        ad_data = data[pos + 2 : pos + 1 + length]
        if ad_type in (0x08, 0x09):  # Shortened / Complete Local Name
            try:
                name = ad_data.decode("ascii")
            except UnicodeDecodeError:
                pass
        pos += 1 + length
    return name


@dataclass
class BleSessionMap:
    """Resolves connection handles to addresses/names as a capture is walked
    chronologically. Call ``feed_event`` for every Event-type HCI record,
    in order, before trusting ``addr_for_handle``/``name_for_addr``."""

    addr_for_handle: dict[int, str] = field(default_factory=dict)
    name_for_addr: dict[str, str] = field(default_factory=dict)

    def feed_event(self, body: bytes) -> None:
        if len(body) < 2:
            return
        event_code, param_len = body[0], body[1]
        params = body[2 : 2 + param_len]
        # NOTE: addr_for_handle intentionally reflects "whichever address this
        # handle currently means" and gets overwritten (not merged) on every
        # Connection Complete, because handle numbers are small integers the
        # controller freely reuses across unrelated connection instances once
        # a prior one disconnects. That's fine as long as callers resolve each
        # event's address *live*, in chronological order (see iter_att_events),
        # so a query mid-way through the capture only ever sees the handle
        # mapping that was actually true at that moment. Querying this map
        # only after a full pass over the whole file - i.e. "what does handle
        # N mean by EOF" - is a bug: it silently relabels every earlier event
        # that used a since-reused handle number under the LATER device's
        # identity (confirmed in practice: an old H61A8 session's packets got
        # merged into a same-handle H60A6 session's device bucket this way).
        if event_code == 0x05 and len(params) >= 3:  # Disconnection Complete (top-level event, not LE Meta)
            # Drop the mapping so a stale address can't leak into any event
            # that (incorrectly) arrives for this handle after its connection
            # has actually ended, before the next Connection Complete reuses
            # the number for an unrelated device.
            handle = struct.unpack("<H", params[1:3])[0]
            self.addr_for_handle.pop(handle, None)
            return
        if event_code != 0x3E or not params:  # LE Meta Event
            return
        subevent = params[0]
        sub = params[1:]
        if subevent in (0x01, 0x0A) and len(sub) >= 11:  # (Enhanced) Connection Complete
            # sub: status(1) handle(2) role(1) peer_addr_type(1) peer_addr(6) ...
            handle = struct.unpack("<H", sub[1:3])[0]
            addr = _format_addr(sub[5:11])
            self.addr_for_handle[handle] = addr
        elif subevent == 0x02 and sub:  # LE Advertising Report (legacy)
            num = sub[0]
            pos = 1
            for _ in range(num):
                if pos + 8 > len(sub):
                    break
                addr = _format_addr(sub[pos + 2 : pos + 8])
                data_len = sub[pos + 8]
                data = sub[pos + 9 : pos + 9 + data_len]
                name = _parse_ad_structures(data)
                if name:
                    self.name_for_addr[addr] = name
                pos += 9 + data_len + 1  # + trailing RSSI byte
        elif subevent == 0x0D and sub:  # LE Extended Advertising Report
            num = sub[0]
            pos = 1
            for _ in range(num):
                if pos + 24 > len(sub):
                    break
                addr = _format_addr(sub[pos + 3 : pos + 9])
                data_len = sub[pos + 23]
                data = sub[pos + 24 : pos + 24 + data_len]
                name = _parse_ad_structures(data)
                if name:
                    self.name_for_addr[addr] = name
                pos += 24 + data_len

    def name_for_handle(self, handle: int) -> str | None:
        addr = self.addr_for_handle.get(handle)
        return self.name_for_addr.get(addr) if addr else None


# --------------------------------------------------------------------------
# Layer 2: ACL data -> ATT events
# --------------------------------------------------------------------------

# opcode -> (label, direction, has_attribute_handle)
# direction is fixed by the ATT client/server roles: the phone app is always
# the GATT client (issues Read/Write requests), the bulb is always the server
# (issues responses/notifications) - so opcode alone determines direction.
ATT_OPCODES: dict[int, tuple[str, str, bool]] = {
    0x01: ("ErrorResponse", "NOTIFY", False),
    0x02: ("ExchangeMtuReq", "WRITE", False),
    0x03: ("ExchangeMtuResp", "NOTIFY", False),
    0x04: ("FindInfoReq", "WRITE", False),
    0x05: ("FindInfoResp", "NOTIFY", False),
    0x06: ("FindByTypeValueReq", "WRITE", False),
    0x07: ("FindByTypeValueResp", "NOTIFY", False),
    0x08: ("ReadByTypeReq", "WRITE", False),
    0x09: ("ReadByTypeResp", "NOTIFY", False),
    0x0A: ("ReadReq", "WRITE", True),
    0x0B: ("ReadResp", "NOTIFY", False),
    0x0C: ("ReadBlobReq", "WRITE", True),
    0x0D: ("ReadBlobResp", "NOTIFY", False),
    0x10: ("ReadByGroupTypeReq", "WRITE", False),
    0x11: ("ReadByGroupTypeResp", "NOTIFY", False),
    0x12: ("WriteReq", "WRITE", True),
    0x13: ("WriteResp", "NOTIFY", False),
    0x16: ("PrepareWriteReq", "WRITE", True),
    0x17: ("PrepareWriteResp", "NOTIFY", False),
    0x18: ("ExecuteWriteReq", "WRITE", False),
    0x19: ("ExecuteWriteResp", "NOTIFY", False),
    0x1B: ("Notify", "NOTIFY", True),
    0x1D: ("Indicate", "NOTIFY", True),
    0x1E: ("Confirm", "WRITE", False),
    0x52: ("WriteCmd", "WRITE", True),
}


@dataclass
class AttEvent:
    t: float
    chandle: int
    opcode: int
    opcode_name: str
    direction: str  # "WRITE" or "NOTIFY"
    att_handle: int | None
    value: bytes
    addr: str | None = None  # resolved LIVE, as of this event's moment - see iter_att_events


def iter_att_events(path: str | pathlib.Path | list[str | pathlib.Path], session: BleSessionMap | None = None):
    """Yield every ATT PDU in the capture, tagged with connection handle and
    direction, plus the address that handle actually meant *at that moment*.

    Walks Event and ACL records together in one single chronological pass
    (both are already file-ordered by ``iter_hci_records``) so each ACL
    packet's connection handle is resolved against the ``BleSessionMap``
    state as it stood right then - not after a full pass over the entire
    capture. Handle numbers are small integers the controller reuses across
    unrelated connections once a prior one disconnects; resolving "after the
    fact" would retroactively relabel an earlier device's traffic under
    whichever later device happened to reuse its handle number (this
    happened in practice - see the note on ``BleSessionMap.feed_event``).

    Pass a fresh ``BleSessionMap`` (or omit to use an internal one) - by the
    time iteration completes, its ``name_for_addr`` is also fully populated
    from every advertising report seen, for callers that want a bulk lookup.
    """
    if session is None:
        session = BleSessionMap()
    for rec in iter_hci_records(path):
        if rec.pkt_type == 0x04:
            session.feed_event(rec.body)
            continue
        if rec.pkt_type != 0x02 or len(rec.body) < 4:
            continue
        handle_flags = struct.unpack("<H", rec.body[0:2])[0]
        chandle = handle_flags & 0x0FFF
        acl_len = struct.unpack("<H", rec.body[2:4])[0]
        l2cap_data = rec.body[4 : 4 + acl_len]
        if len(l2cap_data) < 4:
            continue
        l2cap_len, cid = struct.unpack("<HH", l2cap_data[0:4])
        att_pdu = l2cap_data[4 : 4 + l2cap_len]
        if cid != 0x0004 or not att_pdu:
            continue
        opcode = att_pdu[0]
        name, direction, has_handle = ATT_OPCODES.get(opcode, (f"Opcode0x{opcode:02X}", "UNKNOWN", False))
        if has_handle and len(att_pdu) >= 3:
            att_handle = struct.unpack("<H", att_pdu[1:3])[0]
            value = att_pdu[3:]
        else:
            att_handle = None
            value = att_pdu[1:]
        addr = session.addr_for_handle.get(chandle)
        yield AttEvent(rec.t_unix, chandle, opcode, name, direction, att_handle, value, addr)


# --------------------------------------------------------------------------
# Layer 2b: decryption (auto-detects plaintext vs. H60A6-style AES/RC4)
# --------------------------------------------------------------------------


def _checksum_ok(pt20: bytes) -> bool:
    return len(pt20) == FRAME_LEN and p.checksum(pt20[:19]) == pt20[19:20]


@dataclass
class PlainEvent:
    t: float
    chandle: int
    direction: str
    status: str  # "OK" (a Govee 20-byte frame), "HANDSHAKE", "OTHER" (non-Govee ATT traffic), "FAIL" (decrypt failed)
    data: bytes  # for OK/HANDSHAKE: the 20-byte plaintext frame; for OTHER/FAIL: the raw value
    opcode_name: str = ""  # ATT opcode label (e.g. "ReadByGroupTypeReq") - populated for OTHER/FAIL
    addr: str | None = None  # propagated from AttEvent.addr - the live-resolved address, if any


def decrypt_all(events: list[AttEvent]):
    """Per-connection-handle: auto-detect plaintext vs. encrypted, decrypt
    accordingly, yield a PlainEvent per input event."""
    # Keyed by (addr, chandle) rather than chandle alone: addresses are far
    # less likely to collide across genuinely-different connections than
    # small reused handle numbers are, so this also reduces (does not fully
    # eliminate, since addresses can themselves rotate) the odds of a stale
    # session key from a disconnected device's handshake leaking into a
    # later, unrelated connection that happens to reuse the same handle.
    session_key_by_conn: dict[tuple[str | None, int], bytes] = {}
    for ev in events:
        conn = (ev.addr, ev.chandle)
        if len(ev.value) != FRAME_LEN:
            yield PlainEvent(ev.t, ev.chandle, ev.direction, "OTHER", ev.value, ev.opcode_name, ev.addr)
            continue
        if _checksum_ok(ev.value):  # already-plaintext frame (older, unencrypted devices)
            yield PlainEvent(ev.t, ev.chandle, ev.direction, "OK", ev.value, ev.opcode_name, ev.addr)
            continue
        psk_pt = p.decrypt_packet(PSK, ev.value)
        if _checksum_ok(psk_pt) and psk_pt[0] == 0xE7 and psk_pt[1] in (0x01, 0x02):
            if psk_pt[1] == 0x01 and ev.direction == "NOTIFY":
                session_key_by_conn[conn] = psk_pt[2:18]
            yield PlainEvent(ev.t, ev.chandle, ev.direction, "HANDSHAKE", psk_pt, ev.opcode_name, ev.addr)
            continue
        session_key = session_key_by_conn.get(conn)
        if session_key is not None:
            pt = p.decrypt_packet(session_key, ev.value)
            if _checksum_ok(pt):
                yield PlainEvent(ev.t, ev.chandle, ev.direction, "OK", pt, ev.opcode_name, ev.addr)
                continue
        yield PlainEvent(ev.t, ev.chandle, ev.direction, "FAIL", ev.value, ev.opcode_name, ev.addr)


# --------------------------------------------------------------------------
# Layer 3: Govee frame decode - delegated entirely to the library codec
# --------------------------------------------------------------------------


def decode_frame(frame: bytes, direction: str = "WRITE") -> DecodedMessage:
    """Decode one 20-byte Govee plaintext frame via the library codec.

    Thin pass-through to ``govee_ble_local.messages.deserialize`` - the single
    source of truth for the wire format. ``direction`` ("WRITE" host->device,
    "NOTIFY" device->host) disambiguates side-dependent frame shapes.
    """
    return decode_message(frame, direction)


def note(summary: str, confidence: str = "unknown") -> DecodedMessage:
    """A capture-layer annotation that is NOT a real Govee frame: standard
    BLE/GATT housekeeping, a decrypt failure, a handshake placeholder, or an
    elision marker. Carries only .summary/.confidence for formatting."""
    return DecodedMessage("note", understood=False, sendable=False, summary=summary, confidence=confidence)


# --------------------------------------------------------------------------
# Formatting - fixed-width so a monospace viewer aligns the comment column
# --------------------------------------------------------------------------

PREFIX_WIDTH = len("t+9999.999s NOTIFY  ")


def format_prefix(t_rel: float, direction: str) -> str:
    return f"t+{t_rel:8.3f}s {direction:<7s}".ljust(PREFIX_WIDTH)


def format_left(t_rel: float, direction: str, data: bytes) -> str:
    """The hex-data column: prefix + hex bytes. Not padded - callers that
    need the comment column aligned across a whole file must measure the
    widest ``format_left`` result themselves (packet length varies: Govee
    frames are a fixed 20 bytes, but the same capture also carries ordinary
    variable-length BLE/GATT traffic) and pass that as ``left_width``.

    Frames whose opcode is in ``REDACT_OPCODES`` never have their actual
    bytes rendered here, in raw output or annotated - this is the one choke
    point both go through, so redaction can't be bypassed by only fixing the
    decode summary."""
    if data and data[0] in REDACT_OPCODES:
        hex_str = REDACTED_PLACEHOLDER
    else:
        hex_str = " ".join(f"{b:02x}" for b in data)
    return f"{format_prefix(t_rel, direction)}{hex_str}"


def format_raw_line(t_rel: float, direction: str, data: bytes) -> str:
    return format_left(t_rel, direction, data)


def format_annotated_line(t_rel: float, direction: str, data: bytes, decoded: DecodedMessage, left_width: int = 0) -> str:
    left = format_left(t_rel, direction, data).ljust(left_width)
    marker = "" if decoded.confidence == "confirmed" else f"[{decoded.confidence.upper()}] "
    return f"{left}  |  {marker}{decoded.summary}"


# --------------------------------------------------------------------------
# Standalone CLI - quick single-device look (no splitting/heartbeat handling)
# --------------------------------------------------------------------------


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "btsnoop_hci.log"
    session = BleSessionMap()
    events = list(iter_att_events(path, session))
    if not events:
        print("No ATT events found")
        return 1
    t0 = events[0].t
    decoded = list(decrypt_all(events))
    fails = sum(1 for e in decoded if e.status == "FAIL")
    others = sum(1 for e in decoded if e.status == "OTHER")
    print(f"{len(events)} ATT events, {fails} failed to decrypt, {others} non-20-byte/non-Govee\n")
    for e in decoded:
        # e.addr is the address as it was resolved *at that event's moment*
        # (handles get reused across unrelated connections) - name_for_addr
        # is a simple whole-capture lookup, which is fine for names (they
        # don't change mid-capture the way handle->address bindings do).
        name = (session.name_for_addr.get(e.addr) if e.addr else None) or "?"
        if e.status in ("FAIL", "OTHER"):
            print(f"t+{e.t - t0:9.3f}s h={e.chandle:3d} ({name}) {e.direction:<7s} {e.status:9s} 0x{e.data.hex()}")
            continue
        d = decode_frame(e.data, e.direction) if e.status == "OK" else note(f"HANDSHAKE: {e.data.hex()}")
        print(f"t+{e.t - t0:9.3f}s h={e.chandle:3d} ({name}) " + format_annotated_line(e.t - t0, e.direction, e.data, d)[PREFIX_WIDTH:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
