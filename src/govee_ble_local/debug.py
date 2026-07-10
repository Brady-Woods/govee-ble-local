"""Offline diagnostics CLI: decode a captured session and report spec coverage/gaps.

Two capture surfaces feed this (see the diagnostics docs):
  * a JSONL frame log (``frame_log=`` / ``$GOVEE_FRAME_LOG``) — analyse directly, and
  * the ``govee_ble_local.frames`` logger (HA-native, filesystem-free) — ``--from-frames-log``
    converts that logger's output back into the JSONL shape first.

Installed as the ``govee-ble-analyze`` console script; also ``python -m govee_ble_local.debug``.

    govee-ble-analyze capture.jsonl [more.jsonl ...]
    govee-ble-analyze --from-frames-log session.log [-o session.jsonl]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys

from .wire.describe import analyze_status_bursts, describe_frame

# Matches a `govee_ble_local.frames` log line regardless of the logging prefix
# (timestamp/level/logger). Format emitted by GoveeConnection._capture:
#   "<addr> <dir> <label> plain=<hex> wire=<hex> enc=<mode>"
_FRAME_LINE = re.compile(
    r"\b(?P<dir>tx|rx)\b.*?\bplain=(?P<plain>[0-9a-fA-F]*)\s+wire=(?P<wire>[0-9a-fA-F]*)\s+enc=(?P<enc>\S+)"
)


def frames_log_to_records(text: str) -> list[dict[str, object]]:
    """Convert captured ``govee_ble_local.frames`` logger output into analyzer records
    ``{dir, plain, wire, enc}`` (empty plain -> None, i.e. ciphertext-only)."""
    records: list[dict[str, object]] = []
    for line in text.splitlines():
        m = _FRAME_LINE.search(line)
        if not m:
            continue
        plain = m.group("plain")
        records.append({
            "dir": m.group("dir"),
            "plain": plain.lower() or None,
            "wire": m.group("wire").lower(),
            "enc": m.group("enc"),
        })
    return records


def _load_jsonl(paths: list[str]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def analyze(records: list[dict[str, object]]) -> int:
    """Print a coverage report for a list of capture records; return 1 if any hard issue."""
    frames: list[tuple[str, bytes]] = []
    skipped = 0
    for rec in records:
        plain_hex = rec.get("plain")
        if not plain_hex:
            skipped += 1
            continue
        frames.append((str(rec.get("dir", "?")), bytes.fromhex(str(plain_hex))))

    hist: collections.Counter[str] = collections.Counter()
    hard: list[str] = []
    soft: collections.Counter[str] = collections.Counter()
    artifacts: collections.Counter[str] = collections.Counter()
    for direction, plain in frames:
        label, issues = describe_frame(plain, direction)
        hist[f"{direction:2} {label}"] += 1
        for sev, reason in issues:
            if sev == "hard":
                hard.append(f"[{direction}] {plain.hex()}  {reason}")
            elif sev == "artifact":
                artifacts[reason] += 1
            else:
                soft[reason] += 1

    rx_ac = [p for d, p in frames if d == "rx" and p and p[0] == 0xAC]
    tlv_types, tlv_gaps, malformed = analyze_status_bursts(rx_ac)
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="govee-ble-analyze", description=__doc__)
    ap.add_argument("paths", nargs="*", help="JSONL capture file(s)")
    ap.add_argument(
        "--from-frames-log", metavar="FILE",
        help="convert a captured govee_ble_local.frames logger output to JSONL, then analyse",
    )
    ap.add_argument("-o", "--out", metavar="FILE", help="write the converted JSONL to FILE")
    args = ap.parse_args(argv)

    if args.from_frames_log:
        with open(args.from_frames_log, encoding="utf-8") as fh:
            records = frames_log_to_records(fh.read())
        if args.out:
            with open(args.out, "w", encoding="utf-8") as out:
                for rec in records:
                    out.write(json.dumps(rec) + "\n")
            print(f"wrote {len(records)} records to {args.out}\n")
        return analyze(records)

    if not args.paths:
        ap.error("give a JSONL capture path, or --from-frames-log FILE")
    return analyze(_load_jsonl(args.paths))


if __name__ == "__main__":
    raise SystemExit(main())
