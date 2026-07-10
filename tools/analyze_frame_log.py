#!/usr/bin/env python3
"""Post-analysis for a captured frame log (see transport/framelog.py).

Parses every plaintext frame with the Kaitai-generated reader and RAISES any frame
the spec doesn't properly represent:

  HARD issue (spec gap; non-zero exit): kaitai parse fails; proType not in the
    `pro_type` enum; command not in `command`; mode sub-type not in `sub_mode`;
    notify sub-type not in `notify_sub`; a device write-ACK with result != 0
    (rejection); a reassembled 0xAC status TLV type not in the modelled set.
  SOFT note (coverage): the byte IS a known enum value but its payload isn't
    modelled (parses to raw bytes) — reported, not failed.

Frames that can't be judged one-at-a-time are handled specially:
  * 0xAC status replies are BURSTS — a chunk is meaningless alone. They are grouped
    by chunk index (ac 00 … ac FF), de-duplicated (the device double-delivers each
    notification), reassembled (wire.reassemble), then the TLV stream is walked as a
    whole. A dropped terminator / conflicting duplicate => the burst is reported as
    malformed (a transport artifact) rather than mis-parsed into phantom TLVs.
  * Direction matters: an RX 0x33 is a write-ACK echo ([0x33, cmd, result, …]), not
    a command — parsed as ack/<cmd> with byte2 = result. (The ksy models only the
    write command; ack vs command is direction-keyed, a documented model limitation.)
  * 0xA1/0xA3/0xA4 sub-20-byte frames are multi-chunk upload fragments, not corrupt.

Emits a coverage histogram, the reassembled status TLV inventory, and an ISSUES list.
Usage:

    python3 tools/analyze_frame_log.py <capture.jsonl> [more.jsonl ...]
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))  # for govee_ble_local._generated (shipped reader)

try:
    from govee_ble_local._generated.govee_ble_frame import GoveeBleFrame
except Exception as exc:  # noqa: BLE001
    raise SystemExit(f"generated reader missing — run tools/gen_kaitai.sh first ({exc})")

_PT = GoveeBleFrame.ProType
_CMD = GoveeBleFrame.Command
_SUB = GoveeBleFrame.SubMode
_NSUB = GoveeBleFrame.NotifySub


def _bcc_ok(f: bytes) -> bool:
    x = 0
    for b in f[:19]:
        x ^= b
    return len(f) == 20 and f[19] == x


def _opaque(x: object) -> bool:
    """True if the Kaitai reader left this body as raw bytes (no modelled layout)."""
    return isinstance(x, (bytes, bytearray))


def analyze_frame(plain: bytes, direction: str = "?") -> tuple[str, list[tuple[str, str]]]:
    """Return (label, issues) where issues = [(severity, reason)].

    severity: 'hard' = a valid frame whose STRUCTURE the spec doesn't represent (real
                gap: unknown proType/command/sub-mode/notify-sub, or a parse failure);
              'soft' = known opcode but its BODY is opaque (raw bytes) — the spec models
                the frame but not this payload. This is where read-back gaps surface:
                unmodelled read replies (e.g. 0xa2 BulbGroupColor), mode reply sub-modes
                (e.g. 0x0d), and notify payloads all show up here.
              'artifact' = invalid checksum → not a real plaintext frame (ciphertext/corrupt)."""
    if plain and plain[0] == 0xAC:
        # 0xAC status request/reply — a reply chunk is NOT parseable alone (it's a burst
        # fragment). Handled in the reassembly pass; don't try to parse it here.
        return ("status-chunk", [])
    if plain and plain[0] in (0xA1, 0xA3, 0xA4) and len(plain) != 20:
        return (f"multi-chunk/0x{plain[0]:02x}", [])   # variable-length upload chunk (e.g. 0xA4 END, 19 B)
    if len(plain) != 20:
        return (f"len={len(plain)}", [("artifact", f"not 20 bytes (len {len(plain)})")])
    if not _bcc_ok(plain):
        return ("bad_checksum", [("artifact", "invalid BCC — likely undecrypted ciphertext / corrupt")])
    try:
        f = GoveeBleFrame.from_bytes(plain)
    except Exception as exc:  # noqa: BLE001
        return ("parse_error", [("hard", f"kaitai parse failed: {exc!r}")])

    pt = f.pro_type
    if not isinstance(pt, _PT):
        return (f"proType=0x{plain[0]:02x}", [("hard", f"unknown proType 0x{plain[0]:02x}")])

    issues: list[tuple[str, str]] = []
    body = f.body
    label = pt.name

    if direction == "rx" and pt == _PT.write:
        # Device write-ACK echo: [0x33, command, result, zeros]; byte2 = result (0 = success).
        # The ksy models only the write COMMAND — command vs ack is direction-keyed (both are
        # 0x33), so a lone frame can't tell them apart; parse it as an ack here (byte2 = result).
        cmd = getattr(body, "command", None)
        name = cmd.name if isinstance(cmd, _CMD) else f"0x{plain[1]:02x}"
        if plain[2] != 0:
            return (f"ack/{name}", [("hard", f"device REJECTED {name} write (result 0x{plain[2]:02x})")])
        return (f"ack/{name}", [])

    if pt == _PT.write:
        cmd = getattr(body, "command", None)
        if not isinstance(cmd, _CMD):
            return (f"write/cmd=0x{plain[1]:02x}", [("hard", f"unknown command 0x{plain[1]:02x} (write)")])
        label = f"write/{cmd.name}"
        params = getattr(body, "params", None)
        if cmd == _CMD.mode:
            st = getattr(params, "sub_type", None)
            if st is not None and not isinstance(st, _SUB):
                return (f"write/mode/sub=0x{plain[2]:02x}", [("hard", f"unknown mode sub_type 0x{plain[2]:02x}")])
            if isinstance(st, _SUB):
                label += f"/{st.name}"
                if _opaque(getattr(params, "params", None)):
                    issues.append(("soft", f"write mode/{st.name} payload not modelled"))
        elif _opaque(params):
            issues.append(("soft", f"write {cmd.name} payload not modelled"))

    elif pt == _PT.read:
        cmd = getattr(body, "command", None)
        if not isinstance(cmd, _CMD):
            return (f"read/cmd=0x{plain[1]:02x}", [("hard", f"unknown command 0x{plain[1]:02x} (read)")])
        label = f"read/{cmd.name}"
        rb = getattr(body, "body", None)
        if cmd == _CMD.mode:                       # mode_read: 0x15/0x13 typed; others opaque
            sel = getattr(rb, "selector_or_sub_mode", None)
            if sel is not None:
                label += f"/sub=0x{sel:02x}"
            # 0x01 = request selector (no reply body); 0x04 = scene code (read in wire.parse)
            if _opaque(getattr(rb, "rest", None)) and sel not in (0x01, 0x04):
                issues.append(("soft", f"mode read sub 0x{sel:02x} reply not modelled"))
        elif _opaque(rb):                          # e.g. 0xa2 BulbGroupColor (mechanism B)
            issues.append(("soft", f"read {cmd.name} reply not modelled (opaque)"))

    elif pt == _PT.notify:
        st = getattr(body, "sub_type", None)
        if not isinstance(st, _NSUB):
            return (f"notify/sub=0x{plain[1]:02x}", [("hard", f"unknown notify sub_type 0x{plain[1]:02x}")])
        label = f"notify/{st.name}"
        if _opaque(getattr(body, "data", None)):
            issues.append(("soft", f"notify {st.name} payload not modelled"))
    # multi_* / handshake: structural only.
    return (label, issues)


# TLV types the 0xAC status reply is known to carry (Compose4BaseInfoSingleRead.u):
_KNOWN_TLV = {0x00, 0x01, 0x04, 0x05, 0x07, 0x11, 0x12, 0x23, 0x30, 0x41, 0xA5}


def _analyze_status_bursts(
    rx_ac: list[bytes],
) -> tuple[collections.Counter, list[str], int]:
    """Group RX 0xAC reply chunks into bursts and walk each reassembled TLV stream.

    A status burst is the index sequence ``ac 00, ac 01, … ac FF`` (byte1 = chunk
    index; 0xFF = terminator). Grouping keys on that index — a new ``index==0x00``
    starts a fresh burst, ``0xFF`` closes it — so a dropped terminator (which would
    otherwise concatenate two reads and drift the walk into phantom TLVs) is caught
    as *malformed* instead of mis-reported. Only complete bursts are walked; a TLV
    type outside the known set is then a real spec gap.

    Returns ``(tlv_type_counts, gaps, malformed_burst_count)``."""
    from govee_ble_local.wire import reassemble as R

    def _dedup(chunks: list[bytes]) -> list[bytes] | None:
        """Collapse a burst to one chunk per index (the device double-delivers each
        notification). Exact duplicates are dropped; a conflicting duplicate index =>
        None (malformed)."""
        seen: dict[int, bytes] = {}
        order: list[bytes] = []
        for fr in chunks:
            i = fr[1]
            if i in seen:
                if seen[i] != fr:
                    return None
                continue
            seen[i] = fr
            order.append(fr)
        return order

    complete: list[list[bytes]] = []
    malformed = 0
    cur: list[bytes] = []
    for fr in rx_ac:
        idx = fr[1] if len(fr) >= 2 else -1
        if idx == 0x00:                 # start of a burst
            if cur:                     # previous burst never terminated -> merged/truncated
                malformed += 1
            cur = [fr]
        elif idx == 0xFF:               # terminator closes the burst
            if cur:
                cur.append(fr)
                deduped = _dedup(cur)
                if deduped is None:
                    malformed += 1
                else:
                    complete.append(deduped)
                cur = []
            # stray terminator with no open burst -> ignore
        elif cur:                       # interior chunk of an open burst
            cur.append(fr)
    if cur:                             # trailing chunks with no terminator
        malformed += 1

    types: collections.Counter = collections.Counter()
    gaps: list[str] = []
    for b in complete:
        for t, _val in R.walk_tlvs(R.reassemble(b)):
            types[t] += 1
            if t not in _KNOWN_TLV:
                gaps.append(f"0xAC reply TLV type 0x{t:02x} not modelled")
    return types, gaps, malformed


def main(paths: list[str]) -> int:
    frames: list[tuple[str, bytes]] = []
    skipped = 0
    for path in paths:
        for line in pathlib.Path(path).read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            plain_hex = rec.get("plain")
            if not plain_hex:
                skipped += 1
                continue
            frames.append((rec.get("dir", "?"), bytes.fromhex(plain_hex)))

    hist: collections.Counter = collections.Counter()
    hard: list[str] = []
    soft: collections.Counter = collections.Counter()
    artifacts: collections.Counter = collections.Counter()
    for direction, plain in frames:
        label, issues = analyze_frame(plain, direction)
        hist[f"{direction:2} {label}"] += 1
        for sev, reason in issues:
            if sev == "hard":
                hard.append(f"[{direction}] {plain.hex()}  {reason}")
            elif sev == "artifact":
                artifacts[reason] += 1
            else:
                soft[reason] += 1

    # 0xAC status replies must be reassembled before parsing — do that here.
    rx_ac = [p for d, p in frames if d == "rx" and p and p[0] == 0xAC]
    tlv_types, tlv_gaps, malformed = _analyze_status_bursts(rx_ac)
    hard.extend(f"[rx] {g}" for g in tlv_gaps)

    print(f"== frame-log analysis ({len(frames)} plaintext frames; {skipped} skipped/ciphertext) ==")
    print("\ncoverage (dir + decoded label):")
    for label, n in hist.most_common():
        print(f"  {n:5d}  {label}")
    if tlv_types:
        print("\n0xAC status reply — reassembled TLV types seen (type:count):")
        print("  " + ", ".join(f"0x{t:02x}:{n}" for t, n in sorted(tlv_types.items())))
    if malformed:
        print(f"\nmalformed 0xAC bursts skipped (dropped/merged frames, NOT spec gaps): {malformed}")
    if soft:
        print("\ncoverage gaps (known opcode, payload not modelled by the spec):")
        for reason, n in soft.most_common():
            print(f"  {n:5d}  {reason}")
    if artifacts:
        print("\nunparseable (invalid checksum — likely undecrypted/corrupt, NOT spec gaps):")
        for reason, n in artifacts.most_common():
            print(f"  {n:5d}  {reason}")
    print(f"\nISSUES (valid frames the spec does NOT represent): {len(hard)}")
    for h in hard:
        print(f"  {h}")
    return 1 if hard else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1:]))
