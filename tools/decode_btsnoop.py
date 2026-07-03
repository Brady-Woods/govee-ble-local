#!/usr/bin/env python3
"""Decode a btsnoop_hci.log of Govee BLE traffic into a readable timeline.

Parses the capture, finds each auth handshake (there may be several if the app
reconnected), derives the session key, and decrypts every ATT write/notify on
the data characteristic — reusing the crypto from ``govee_ble_local.protocol``
rather than reimplementing it.

Usage:
    python3 tools/decode_btsnoop.py path/to/btsnoop_hci.log
"""
from __future__ import annotations

import pathlib
import struct
import sys

# Run from the repo without an install.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from govee_ble_local import protocol as p  # noqa: E402
from govee_ble_local.const import PSK  # noqa: E402

BTSNOOP_EPOCH_OFFSET = 0x00E03AB44A676000

KNOWN_PREFIXES = [
    (b"\x33\x30", "ZONE"),
    (b"\x33\x04", "BRIGHTNESS"),
    (b"\x33\x05\x04", "SCENE_ACTIVATE"),
    (b"\x33\x05\x15", "COLOR"),
    (b"\xa3", "SCENE_DATA_CHUNK"),
    (b"\xaa", "STATUS_QUERY"),
    (b"\xab", "DEVICE_META"),
    (b"\xac", "DEVICE_INFO"),
    (b"\xee", "STATE_REPORT"),
]


def _checksum_ok(pt20: bytes) -> bool:
    return len(pt20) == 20 and p.checksum(pt20[:19]) == pt20[19:20]


def parse_events(path: str) -> list[tuple[float, int, str, bytes]]:
    data = pathlib.Path(path).read_bytes()
    offset = 16  # skip btsnoop file header
    events: list[tuple[float, int, str, bytes]] = []
    while offset + 24 <= len(data):
        orig_len, incl_len, flags, drops, ts = struct.unpack(">IIIIq", data[offset : offset + 24])
        offset += 24
        payload = data[offset : offset + incl_len]
        offset += incl_len
        if not payload:
            continue
        pkt_type, body = payload[0], payload[1:]
        unix_us = ts - BTSNOOP_EPOCH_OFFSET
        if pkt_type != 0x02 or len(body) < 4:
            continue
        acl_len = struct.unpack("<H", body[2:4])[0]
        l2cap_data = body[4 : 4 + acl_len]
        if len(l2cap_data) < 4:
            continue
        l2cap_len, cid = struct.unpack("<HH", l2cap_data[0:4])
        att_pdu = l2cap_data[4 : 4 + l2cap_len]
        if cid != 0x0004 or not att_pdu:
            continue
        opcode = att_pdu[0]
        if opcode in (0x12, 0x52, 0x1B, 0x1D) and len(att_pdu) >= 3:
            att_handle = struct.unpack("<H", att_pdu[1:3])[0]
            kind = {0x12: "WriteReq", 0x52: "WriteCmd", 0x1B: "Notify", 0x1D: "Indicate"}[opcode]
            events.append((unix_us / 1e6, att_handle, kind, att_pdu[3:]))
    return events


def decrypt_all(events):
    session_key = None
    for t, handle, kind, value in events:
        if len(value) != 20:
            continue
        psk_pt = p.decrypt_packet(PSK, value)
        if _checksum_ok(psk_pt) and psk_pt[0] == 0xE7 and psk_pt[1] in (0x01, 0x02):
            if psk_pt[1] == 0x01 and kind in ("Notify", "Indicate"):
                session_key = psk_pt[2:18]
            yield (t, handle, kind, "HANDSHAKE", psk_pt)
            continue
        if session_key is not None:
            pt = p.decrypt_packet(session_key, value)
            if _checksum_ok(pt):
                yield (t, handle, kind, "OK", pt)
                continue
        yield (t, handle, kind, "FAIL", value)


def classify(pt: bytes) -> str:
    for prefix, name in KNOWN_PREFIXES:
        if pt[: len(prefix)] == prefix:
            return name
    return "UNKNOWN"


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "btsnoop_hci.log"
    events = parse_events(path)
    if not events:
        print("No ATT events found")
        return 1
    t0 = events[0][0]
    decoded = list(decrypt_all(events))
    fails = sum(1 for *_, status, _ in decoded if status == "FAIL")
    print(f"{len(events)} raw ATT events, {len(decoded)} len-20 packets, {fails} failed to decrypt\n")
    for t, handle, kind, status, pt in decoded:
        if status == "FAIL":
            print(f"t+{t - t0:9.3f}s h={handle:3d} {kind:8s} FAIL ct={pt.hex()}")
            continue
        label = "HANDSHAKE" if status == "HANDSHAKE" else classify(pt)
        print(f"t+{t - t0:9.3f}s h={handle:3d} {kind:8s} {label:16s} {pt.hex()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
