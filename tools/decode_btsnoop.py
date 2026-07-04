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


def iter_hci_records(path: str | pathlib.Path):
    data = pathlib.Path(path).read_bytes()
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
        # Deliberately not clearing addr_for_handle on Disconnection Complete: this
        # map is queried after a full pass over the capture, and a handle that
        # disconnects before EOF would otherwise be unresolvable at query time even
        # though it was valid for the whole time its packets were being sent. A
        # reused handle simply gets overwritten by its next Connection Complete.
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


def iter_att_events(path: str | pathlib.Path):
    """Yield every ATT PDU in the capture, tagged with connection handle and
    direction. Does not filter by device - callers select connection handles
    of interest via ``BleSessionMap``."""
    for rec in iter_hci_records(path):
        if rec.pkt_type == 0x04:
            continue  # events are consumed separately via BleSessionMap.feed_event
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
        yield AttEvent(rec.t_unix, chandle, opcode, name, direction, att_handle, value)


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


def decrypt_all(events: list[AttEvent]):
    """Per-connection-handle: auto-detect plaintext vs. encrypted, decrypt
    accordingly, yield a PlainEvent per input event."""
    session_key_by_handle: dict[int, bytes] = {}
    for ev in events:
        if len(ev.value) != FRAME_LEN:
            yield PlainEvent(ev.t, ev.chandle, ev.direction, "OTHER", ev.value, ev.opcode_name)
            continue
        if _checksum_ok(ev.value):  # already-plaintext frame (older, unencrypted devices)
            yield PlainEvent(ev.t, ev.chandle, ev.direction, "OK", ev.value, ev.opcode_name)
            continue
        psk_pt = p.decrypt_packet(PSK, ev.value)
        if _checksum_ok(psk_pt) and psk_pt[0] == 0xE7 and psk_pt[1] in (0x01, 0x02):
            if psk_pt[1] == 0x01 and ev.direction == "NOTIFY":
                session_key_by_handle[ev.chandle] = psk_pt[2:18]
            yield PlainEvent(ev.t, ev.chandle, ev.direction, "HANDSHAKE", psk_pt, ev.opcode_name)
            continue
        session_key = session_key_by_handle.get(ev.chandle)
        if session_key is not None:
            pt = p.decrypt_packet(session_key, ev.value)
            if _checksum_ok(pt):
                yield PlainEvent(ev.t, ev.chandle, ev.direction, "OK", pt, ev.opcode_name)
                continue
        yield PlainEvent(ev.t, ev.chandle, ev.direction, "FAIL", ev.value, ev.opcode_name)


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
    events = []
    for rec in iter_hci_records(path):
        if rec.pkt_type == 0x04:
            session.feed_event(rec.body)
    events = list(iter_att_events(path))
    if not events:
        print("No ATT events found")
        return 1
    t0 = events[0].t
    decoded = list(decrypt_all(events))
    fails = sum(1 for e in decoded if e.status == "FAIL")
    others = sum(1 for e in decoded if e.status == "OTHER")
    print(f"{len(events)} ATT events, {fails} failed to decrypt, {others} non-20-byte/non-Govee\n")
    for e in decoded:
        name = session.name_for_handle(e.chandle) or "?"
        if e.status in ("FAIL", "OTHER"):
            print(f"t+{e.t - t0:9.3f}s h={e.chandle:3d} ({name}) {e.direction:<7s} {e.status:9s} 0x{e.data.hex()}")
            continue
        d = decode_frame(e.data, e.direction) if e.status == "OK" else note(f"HANDSHAKE: {e.data.hex()}")
        print(f"t+{e.t - t0:9.3f}s h={e.chandle:3d} ({name}) " + format_annotated_line(e.t - t0, e.direction, e.data, d)[PREFIX_WIDTH:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
