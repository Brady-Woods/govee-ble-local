#!/usr/bin/env python3
"""Decrypt Govee AES-RC4-PSK btsnoop captures to observe plaintext app frames.

Why: the H60A6 wire cipher is AES-ECB(16)+RC4(4), so a plaintext byte scan can't
see the scene/DIY-upload opcodes (0xA1/0xA3/0xA4/0xA6). This tool derives the per-connection
session key from the `e7` handshake (the host's `e7 01` write, then the device's REPLY
notify, which PSK-decrypts to `e7 01 <16-byte key>`), then decrypts every ATT write/notify
with it. Each candidate is validated by the frame XOR checksum (BCC), so wrong-key /
other-device / plaintext frames are filtered automatically: a mis-keyed 20-byte decrypt
passes BCC ~1/256; an exact-length (not 20-byte) frame must also start with a multi-packet
opcode 0xA3/0xA4/0xA6, so it passes 3/256 * 1/256, ~1/21845.

It reassembles L2CAP fragments per ACL handle, so MTU-sized (>20 B) writes are caught
whole -- directly answering whether 0xA4 scene uploads are 20 B or MTU-sized. Those
frames are exact-length with the BCC appended, not zero-padded
(MultipleControllerCommV1.makeSendBytesMtu for 0xA4, .makeSendBytesMtu0xA6 for 0xA6),
and are decoded at their real length.

    python3 tools/decrypt_btsnoop.py <capture.log> [--dump a1,a3,a4,a6] [--all] [--limit N]

--all dumps every decoded frame; default dumps only the scene/DIY opcodes. Frame bytes
only -- never keys/secrets: account-secret frames (33 b2 writes, aa b1 replies) are masked
(payload blanked, header kept) before printing. Session keys are never printed.
"""
from __future__ import annotations

import argparse
import collections
import struct
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

_TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS.parent / "src"))
sys.path.insert(0, str(_TOOLS))  # sibling btsnoop_mtu_scan: the shared, stdlib-only record reader

# The imports below need the sys.path entries above (the tool runs from a checkout, not installed).
from btsnoop_mtu_scan import H4_ACL, H4_EVENT, read_records  # noqa: E402

from govee_ble_local.const import FRAME_LEN, PSK  # noqa: E402
from govee_ble_local.crypto import checksum, checksum_ok, decrypt  # noqa: E402
from govee_ble_local.wire.redact import shown_hex  # noqa: E402

HANDSHAKE = 0xE7
# ATT opcodes carrying a value we care about.
_WRITE_OPS = {0x52, 0x12}          # Write Command / Write Request  (host -> device)
_NOTIFY_OPS = {0x1B, 0x1D}         # Handle Value Notification / Indication (device -> host)
# Multi-packet opcodes whose frames may be exact-length (BCC appended, not zero-padded):
# 0xA4 = MultipleControllerCommV1.makeSendBytesMtu (MTU-sized START/MIDDLE, short END, 4-byte
#   [A4 FF FF BCC] terminator), sent via Compose4DefWrite4Multi and PlanCmdOpManager;
# 0xA6 = MultipleControllerCommV1.makeSendBytesMtu0xA6 (MTU-sized START/MIDDLE, or a short START
#   when the value fits, short END, 4-byte [A6 FF FF BCC] terminator), sent via
#   Compose4DefWrite4Multi.c (DIY, ControllerMTUMulti(.., (byte) -90)) and DaySyncUtils.v (:1188;
#   DaySyncUtils.K builds those frames with .j/.k, the same layout);
# 0xA3 = only a harmless allowance: MultipleControllerCommV1.N frames any proType at its
#   packageSize (short last chunk, 3-byte [A3 FF BCC] terminator), but its only caller,
#   makeSendBytesMtuV1, has no caller outside that class, so the decompiled app never sends it.
#   The 0xA3 builders the app does call (MultipleControllerCommV1.makeSendBytesV0/V1/V2, and V3
#   via .K) all emit 20-byte frames.
_EXACT_LEN_MULTI = frozenset({0xA3, 0xA4, 0xA6})
# Multi-packet opcodes whose START frame has index 0x00 at byte 1 (--starts).
_MULTI_OPS = frozenset({0xA1, 0xA3, 0xA4, 0xA6})


class _L2Frag(NamedTuple):
    """An L2CAP PDU being reassembled on one ACL handle (``buf`` grows in place)."""
    l2len: int
    cid: int
    buf: bytearray


def _addr_str(b: bytes) -> str:
    return ":".join(f"{x:02X}" for x in reversed(b))


def _att_pdus(path: str) -> Iterator[tuple[int, str, int, bytes]]:
    """Yield (acl_handle, peer_addr, flags, att_bytes) for complete ATT PDUs,
    reassembling L2CAP fragments per ACL handle so MTU-sized PDUs come out whole.
    peer_addr is resolved from HCI LE Connection-Complete events (handle->MAC),
    tracked over time since ACL handles are reused across reconnects."""
    frag: dict[int, _L2Frag] = {}  # handle -> PDU being reassembled
    addr: dict[int, str] = {}      # handle -> peer MAC (latest connection on that handle)
    for flags, data in read_records(path):
        if not data:
            continue
        # HCI Event -> watch for LE Connection Complete / Enhanced to map handle->addr.
        if data[0] == H4_EVENT and len(data) >= 4 and data[1] == 0x3E:
            sub = data[3]
            p = data[4:]
            # params: status(1) handle(2) role(1) peer_addr_type(1) peer_addr(6) ...
            if sub in (0x01, 0x0A) and len(p) >= 11:  # LE Connection Complete / Enhanced Connection Complete
                h = struct.unpack("<H", p[1:3])[0] & 0x0FFF
                addr[h] = _addr_str(p[5:11])
            continue
        if data[0] != H4_ACL:  # HCI ACL only
            continue
        body = data[1:]
        if len(body) < 4:
            continue
        handle_flags, acl_len = struct.unpack("<HH", body[:4])
        handle = handle_flags & 0x0FFF
        pb = (handle_flags >> 12) & 0x3
        payload = body[4 : 4 + acl_len]
        if pb == 0x01:  # continuation of the current L2CAP PDU
            st = frag.get(handle)
            if st is None:
                continue
            st.buf.extend(payload)
        else:  # start of a new L2CAP PDU
            if len(payload) < 4:
                continue
            l2len, cid = struct.unpack("<HH", payload[:4])
            st = frag[handle] = _L2Frag(l2len, cid, bytearray(payload[4:]))
        if len(st.buf) >= st.l2len:
            att = bytes(st.buf[: st.l2len])
            del frag[handle]
            if st.cid == 0x0004 and att:  # ATT
                yield handle, addr.get(handle, "?"), flags, att


def _frame_ok(frame: bytes) -> bool:
    """True if ``frame`` is a BCC-valid Govee frame: a 20-byte frame (checksum_ok), or an
    exact-length 0xA3/0xA4/0xA6 multi-packet frame (BCC appended at its real length; see
    _EXACT_LEN_MULTI). The opcode gate keeps other lengths from passing on a bare ~1/256 XOR."""
    if len(frame) == FRAME_LEN:
        return checksum_ok(frame)
    return len(frame) >= 3 and frame[0] in _EXACT_LEN_MULTI and checksum(frame[:-1]) == frame[-1]


def _try_plain(value: bytes, key: bytes | None) -> tuple[bytes, str] | None:
    """Return (plaintext frame, mode) if `value` is a valid Govee frame either
    decrypted with `key` ("enc") or already plaintext ("plain", BCC-checked).
    Else None."""
    if key is not None:
        dec = decrypt(value, key)
        if _frame_ok(dec):
            return dec, "enc"
    if _frame_ok(value):  # plaintext device (e.g. H61A8) or unencrypted frame
        return value, "plain"
    return None


def _handshake_step1(value: bytes) -> bytes | None:
    """The PSK-decrypted plaintext if ``value`` is a 20-byte ``e7 01`` handshake frame (the host's
    request or the device's key reply, Controller4Aes.e/.g), else None."""
    if len(value) != FRAME_LEN:
        return None
    pt = decrypt(value, PSK)
    return pt if pt[0] == HANDSHAKE and pt[1] == 0x01 else None


def main(path: str, dump_types: set[int], show_all: bool, limit: int, starts_only: bool) -> None:
    # Keyed by connection, not handle alone: a reused handle must not inherit another device's key.
    keys: dict[tuple[int, str], bytes] = {}         # (acl_handle, peer) -> session key
    pending: set[tuple[int, str]] = set()           # sent an e7 01 request; key reply not seen yet
    proto: dict[str, collections.Counter[int]] = collections.defaultdict(collections.Counter)
    handshakes = 0
    dumped = 0
    print(f"== {path} ==")
    for handle, peer, _flags, att in _att_pdus(path):
        op = att[0]
        if op in _WRITE_OPS:
            direction, value = "TX", att[3:]
        elif op in _NOTIFY_OPS:
            direction, value = "RX", att[3:]
        else:
            continue
        if len(value) < 2:
            continue

        # Key acquisition: the host writes e7 01, the device's REPLY (notify) PSK-decrypts to
        # e7 01 <key>. Only a reply to a request seen on this connection is taken as the key, so a
        # session frame that happens to PSK-decrypt to e7 01 (~1/65536) can't replace it.
        conn = (handle, peer)
        step1 = _handshake_step1(value)
        if step1 is not None:
            if direction == "TX":
                pending.add(conn)
                continue
            if conn in pending:
                pending.discard(conn)
                keys[conn] = step1[2:18]
                handshakes += 1
                continue

        res = _try_plain(value, keys.get(conn))
        if res is None:
            continue
        frame, mode = res
        pro = frame[0]
        proto[peer][pro] += 1
        # --starts: only multi-packet START frames (byte1==0x00) for the dumped opcodes.
        is_multi = pro in _MULTI_OPS
        if starts_only and not (is_multi and len(frame) > 1 and frame[1] == 0x00):
            continue
        if (show_all or pro in dump_types) and dumped < limit:
            print(f"  {peer} h{handle:#05x} {direction} {mode:5s} {len(frame):3d}B  {shown_hex(frame)}")
            dumped += 1

    print(f"  handshakes (session keys derived): {handshakes}")
    for peer, counter in sorted(proto.items()):
        hist = ", ".join(f"0x{p:02x}:{n}" for p, n in counter.most_common())
        print(f"  device {peer}: {hist}")
    if not proto:
        print("  (no valid Govee frames decoded)")
    print()


def _hex_set(text: str) -> set[int]:
    try:
        return {int(x, 16) for x in text.split(",")}
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"--dump wants comma-separated hex opcodes: {err}") from err


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", metavar="capture.log", help="btsnoop capture file(s)")
    ap.add_argument("--dump", type=_hex_set, default=set(_MULTI_OPS), metavar="a1,a3,a4,a6",
                    help="comma-separated hex opcodes to dump (default a1,a3,a4,a6)")
    ap.add_argument("--limit", type=int, default=200, help="max frames to dump per capture (default 200)")
    ap.add_argument("--all", action="store_true", help="dump every decoded frame")
    ap.add_argument("--starts", action="store_true", help="only multi-packet START frames")
    return ap.parse_intermixed_args(argv)


if __name__ == "__main__":
    _a = parse_args()
    for p in _a.paths:
        main(p, _a.dump, _a.all, _a.limit, _a.starts)
