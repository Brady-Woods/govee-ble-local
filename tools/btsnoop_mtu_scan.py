#!/usr/bin/env python3
"""Decryption-free btsnoop scanner for the MTU question.

Reports, per capture: the ATT **Exchange-MTU** request/response values (what MTU the
peers negotiated) and a histogram of ATT **write** PDU value lengths (whether any write
exceeded the 20-byte single-frame size → evidence of MTU-sized / 0xA4 / 0xA6 writes).

Works on encrypted Govee links because the ATT header + PDU lengths are plaintext.

    python3 tools/btsnoop_mtu_scan.py <capture.log> [more.log ...]
"""
from __future__ import annotations

import collections
import struct
import sys


def _records(path: str):
    with open(path, "rb") as f:
        hdr = f.read(16)
        if hdr[:8] != b"btsnoop\x00":
            raise SystemExit(f"{path}: not a btsnoop file")
        _ver, datalink = struct.unpack(">II", hdr[8:16])
        while True:
            rh = f.read(24)
            if len(rh) < 24:
                return
            _orig, incl, _flags, _drops, _ts = struct.unpack(">IIIIq", rh)
            data = f.read(incl)
            if len(data) < incl:
                return
            yield datalink, data


def _att_pdus(path: str):
    """Yield (att_opcode, att_pdu_len) for ATT PDUs (CID 0x0004), skipping L2CAP
    continuation fragments. att_pdu_len comes from the L2CAP length (robust to a
    truncated 'included length' capture)."""
    for datalink, data in _records(path):
        pkt = data
        # datalink 1002 = HCI UART/H4: first byte = HCI packet type. Try that; if the
        # first byte isn't ACL(0x02) we skip. (1001 = raw HCI ACL; handled the same way.)
        if not pkt:
            continue
        h4 = pkt[0]
        body = pkt[1:]
        if h4 != 0x02:  # ACL data only
            continue
        if len(body) < 4:
            continue
        handle_flags, _acl_len = struct.unpack("<HH", body[:4])
        pb = (handle_flags >> 12) & 0x3
        if pb == 0x1:  # continuation fragment
            continue
        l2 = body[4:]
        if len(l2) < 4:
            continue
        l2len, cid = struct.unpack("<HH", l2[:4])
        if cid != 0x0004:  # ATT
            continue
        att = l2[4:]
        if not att:
            continue
        yield att[0], l2len, att


def main(path: str, dump_protype: bool = False) -> None:
    mtu_req: set[int] = set()
    mtu_rsp: set[int] = set()
    write_len = collections.Counter()
    first_byte = collections.Counter()   # first byte of 20-byte writes (proType if plaintext)
    multi_frames = []                     # 0xA1/0xA3/0xA4 writes (plaintext scene/DIY)
    big = []
    for op, l2len, att in _att_pdus(path):
        if op == 0x02 and len(att) >= 3:            # Exchange MTU Request
            mtu_req.add(struct.unpack("<H", att[1:3])[0])
        elif op == 0x03 and len(att) >= 3:          # Exchange MTU Response
            mtu_rsp.add(struct.unpack("<H", att[1:3])[0])
        elif op in (0x52, 0x12):                    # Write Command / Write Request
            vlen = l2len - 3                        # opcode(1) + handle(2) + value
            if vlen >= 0:
                write_len[vlen] += 1
                if vlen > 20:
                    big.append(vlen)
                value = att[3:]                     # full for single-frame writes
                if vlen == 20 and len(value) >= 1:
                    first_byte[value[0]] += 1
                    if value[0] in (0xA1, 0xA3, 0xA4) and len(value) >= 6:
                        multi_frames.append(value[:20].hex())
    print(f"== {path} ==")
    print(f"  Exchange-MTU requests : {sorted(mtu_req) or 'none'}")
    print(f"  Exchange-MTU responses: {sorted(mtu_rsp) or 'none'}")
    print("  ATT write value-length histogram:")
    for vlen in sorted(write_len):
        mark = "  <-- >20B (MTU-sized)" if vlen > 20 else ""
        print(f"    {vlen:4d} B : {write_len[vlen]}{mark}")
    print(f"  writes >20B: {len(big)} (max {max(big) if big else 0})")
    if dump_protype:
        top = ", ".join(f"0x{b:02x}:{n}" for b, n in first_byte.most_common(10))
        print(f"  20B-write first-byte histogram (proType if plaintext): {top}")
        if multi_frames:
            print(f"  0xA1/0xA3/0xA4 (scene/DIY) frames seen: {len(multi_frames)}; first few:")
            for h in multi_frames[:8]:
                print(f"    {h}")
    print()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--protype"]
    dump = "--protype" in sys.argv
    if not args:
        raise SystemExit(__doc__)
    for p in args:
        main(p, dump_protype=dump)
