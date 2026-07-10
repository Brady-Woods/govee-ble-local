#!/usr/bin/env python3
"""Post-analysis for a captured frame log (see transport/framelog.py).

Parses every plaintext frame with the Kaitai-generated reader and RAISES any frame
the spec doesn't properly represent:

  HARD issue (spec gap; non-zero exit): kaitai parse fails; proType not in the
    `pro_type` enum; command not in `command`; mode sub-type not in `sub_mode`;
    notify sub-type not in `notify_sub`.
  SOFT note (coverage): the byte IS a known enum value but its payload isn't
    modelled (parses to raw bytes) — reported, not failed.

Emits a coverage histogram + an ISSUES list. Usage:

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


def analyze_frame(plain: bytes) -> tuple[str, list[tuple[str, str]]]:
    """Return (label, issues) where issues = [(severity, reason)].

    severity: 'hard' = a valid frame the spec doesn't represent (real gap);
              'soft' = known opcode, payload not modelled; 'artifact' = invalid
              checksum, i.e. not a real plaintext frame (undecrypted/corrupt/dropped)."""
    if len(plain) != 20:
        return (f"len={len(plain)}", [("artifact", f"not 20 bytes (len {len(plain)})")])
    if not _bcc_ok(plain):
        # A real plaintext Govee frame has a valid XOR checksum; if it doesn't, this is
        # almost certainly ciphertext (wrong/stale session key) or a corrupt/dropped
        # frame — NOT an unmodelled protocol frame. Bucket separately.
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

    if pt in (_PT.write, _PT.read):
        cmd = getattr(body, "command", None)
        if not isinstance(cmd, _CMD):
            return (f"{pt.name}/cmd=0x{plain[1]:02x}",
                    [("hard", f"unknown command 0x{plain[1]:02x} under {pt.name}")])
        label = f"{pt.name}/{cmd.name}"
        params = getattr(body, "params", None)
        if cmd == _CMD.mode:
            st = getattr(params, "sub_type", None)
            if st is not None and not isinstance(st, _SUB):
                issues.append(("hard", f"unknown mode sub_type 0x{plain[2]:02x}"))
                label += f"/sub=0x{plain[2]:02x}"
            elif isinstance(st, _SUB):
                label += f"/{st.name}"
        elif isinstance(params, (bytes, bytearray)):
            issues.append(("soft", f"{cmd.name}: payload not modelled (raw bytes)"))
    elif pt == _PT.notify:
        st = getattr(body, "sub_type", None)
        if not isinstance(st, _NSUB):
            return (f"notify/sub=0x{plain[1]:02x}",
                    [("hard", f"unknown notify sub_type 0x{plain[1]:02x}")])
        label = f"notify/{st.name}"
    # multi_* / handshake: structural only — no deep per-byte coverage check here.
    return (label, issues)


def main(paths: list[str]) -> int:
    hist: collections.Counter = collections.Counter()
    hard: list[str] = []
    soft: collections.Counter = collections.Counter()
    artifacts: collections.Counter = collections.Counter()
    total = skipped = 0

    for path in paths:
        for line in pathlib.Path(path).read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            plain_hex = rec.get("plain")
            direction = rec.get("dir", "?")
            if not plain_hex:
                skipped += 1
                continue
            total += 1
            label, issues = analyze_frame(bytes.fromhex(plain_hex))
            hist[f"{direction:2} {label}"] += 1
            for sev, reason in issues:
                if sev == "hard":
                    hard.append(f"[{direction}] {plain_hex}  {reason}")
                elif sev == "artifact":
                    artifacts[reason] += 1
                else:
                    soft[reason] += 1

    print(f"== frame-log analysis ({total} plaintext frames; {skipped} skipped/ciphertext) ==")
    print("\ncoverage (dir + decoded label):")
    for label, n in hist.most_common():
        print(f"  {n:5d}  {label}")
    if soft:
        print("\ncoverage gaps (known opcode, payload not modelled):")
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
