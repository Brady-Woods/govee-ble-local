#!/usr/bin/env python3
"""Decrypt Govee AES-RC4-PSK btsnoop captures to observe plaintext app frames.

Why: the H60A6 wire cipher is AES-ECB(16)+RC4(4), so a plaintext byte scan can't
see the scene-upload opcodes (0xA1/0xA3/0xA4). This tool derives the per-connection
session key from the `e7` handshake REPLY (device->host notify, PSK-decrypts to
`e7 01 <16-byte key>`), then decrypts every ATT write/notify with it. Each candidate
is validated by the frame XOR checksum (BCC), so wrong-key / other-device / plaintext
frames are filtered automatically (a mis-keyed 20-byte decrypt passes BCC ~1/256).

It reassembles L2CAP fragments per ACL handle, so MTU-sized (>20 B) writes are caught
whole -- directly answering whether 0xA4 scene uploads are 20 B or MTU-sized.

    python3 tools/decrypt_btsnoop.py <capture.log> [--dump a1,a3,a4] [--all] [--limit N]

--all dumps every decoded frame; default dumps only the scene/DIY opcodes. Frame bytes
only -- never keys/secrets.
"""
from __future__ import annotations

import argparse
import collections
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from govee_ble_local.const import PSK
from govee_ble_local.crypto import checksum_ok, decrypt

HANDSHAKE = 0xE7
# ATT opcodes carrying a value we care about.
_WRITE_OPS = {0x52, 0x12}          # Write Command / Write Request  (host -> device)
_NOTIFY_OPS = {0x1B, 0x1D}         # Handle Value Notification / Indication (device -> host)


MAX_RECORD = 1 << 20  # 1 MiB: far above any real HCI record; guards against corrupt headers


def _records(path: str):
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        hdr = f.read(16)
        if hdr[:8] != b"btsnoop\x00":
            raise SystemExit(f"{path}: not a btsnoop file")
        while True:
            rh = f.read(24)
            if len(rh) < 24:
                return
            _orig, incl, flags, _drops, _ts = struct.unpack(">IIIIq", rh)
            remaining = size - f.tell()
            if incl > MAX_RECORD or incl > remaining:
                raise SystemExit(
                    f"{path}: corrupt btsnoop record (included length {incl} exceeds "
                    f"{'1 MiB limit' if incl > MAX_RECORD else f'remaining {remaining} bytes'})"
                )
            data = f.read(incl)
            if len(data) < incl:
                return
            yield flags, data


def _addr_str(b: bytes) -> str:
    return ":".join(f"{x:02X}" for x in reversed(b))


def _att_pdus(path: str):
    """Yield (acl_handle, peer_addr, flags, att_bytes) for complete ATT PDUs,
    reassembling L2CAP fragments per ACL handle so MTU-sized PDUs come out whole.
    peer_addr is resolved from HCI LE Connection-Complete events (handle->MAC),
    tracked over time since ACL handles are reused across reconnects."""
    frag: dict[int, list] = {}   # handle -> [l2len, cid, bytearray]
    addr: dict[int, str] = {}    # handle -> peer MAC (latest connection on that handle)
    for flags, data in _records(path):
        if not data:
            continue
        # HCI Event -> watch for LE Connection Complete / Enhanced to map handle->addr.
        if data[0] == 0x04 and len(data) >= 4 and data[1] == 0x3E:
            sub = data[3]
            p = data[4:]
            # params: status(1) handle(2) role(1) peer_addr_type(1) peer_addr(6) ...
            if sub in (0x01, 0x0A) and len(p) >= 11:  # LE Connection Complete / Enhanced Connection Complete
                h = struct.unpack("<H", p[1:3])[0] & 0x0FFF
                addr[h] = _addr_str(p[5:11])
            continue
        if data[0] != 0x02:  # HCI ACL only
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
            st[2].extend(payload)
        else:  # start of a new L2CAP PDU
            if len(payload) < 4:
                continue
            l2len, cid = struct.unpack("<HH", payload[:4])
            frag[handle] = [l2len, cid, bytearray(payload[4:])]
        st = frag.get(handle)
        if st is None:
            continue
        l2len, cid, buf = st
        if len(buf) >= l2len:
            att = bytes(buf[:l2len])
            del frag[handle]
            if cid == 0x0004 and att:  # ATT
                yield handle, addr.get(handle, "?"), flags, att


def _try_plain(value: bytes, key: bytes | None) -> tuple[bytes, str] | None:
    """Return (plaintext frame, mode) if `value` is a valid Govee frame either
    decrypted with `key` ("enc") or already plaintext ("plain", BCC-checked).
    Else None."""
    if key is not None:
        dec = decrypt(value, key)
        if checksum_ok(dec):
            return dec, "enc"
    if checksum_ok(value):  # plaintext device (e.g. H61A8) or unencrypted frame
        return value, "plain"
    return None


def main(path: str, dump_types: set[int], show_all: bool, limit: int, starts_only: bool) -> None:
    keys: dict[int, bytes] = {}                     # acl_handle -> session key
    proto: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
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

        # Key acquisition: the device's e7 REPLY (notify) PSK-decrypts to e7 01 <key>.
        if direction == "RX":
            pt = decrypt(value, PSK)
            if len(pt) >= 18 and pt[0] == HANDSHAKE and pt[1] == 0x01:
                keys[handle] = pt[2:18]
                handshakes += 1
                continue

        res = _try_plain(value, keys.get(handle))
        if res is None:
            continue
        frame, mode = res
        pro = frame[0]
        proto[peer][pro] += 1
        # --starts: only multi-packet START frames (byte1==0x00) for the dumped opcodes.
        is_multi = pro in {0xA1, 0xA3, 0xA4}
        if starts_only and not (is_multi and len(frame) > 1 and frame[1] == 0x00):
            continue
        if (show_all or pro in dump_types) and dumped < limit:
            print(f"  {peer} h{handle:#05x} {direction} {mode:5s} {len(frame):3d}B  {frame.hex()}")
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
    ap.add_argument("--dump", type=_hex_set, default={0xA1, 0xA3, 0xA4}, metavar="a1,a3,a4",
                    help="comma-separated hex opcodes to dump (default a1,a3,a4)")
    ap.add_argument("--limit", type=int, default=200, help="max frames to dump per capture (default 200)")
    ap.add_argument("--all", action="store_true", help="dump every decoded frame")
    ap.add_argument("--starts", action="store_true", help="only multi-packet START frames")
    return ap.parse_intermixed_args(argv)


if __name__ == "__main__":
    _a = parse_args()
    for p in _a.paths:
        main(p, _a.dump, _a.all, _a.limit, _a.starts)
