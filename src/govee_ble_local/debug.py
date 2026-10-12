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


def _numbered_frame_records(text: str) -> list[tuple[int, dict[str, object]]]:
    out: list[tuple[int, dict[str, object]]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        m = _FRAME_LINE.search(line)
        if not m:
            continue
        plain = m.group("plain")
        out.append((lineno, {
            "dir": m.group("dir"),
            "plain": plain.lower() or None,
            "wire": m.group("wire").lower(),
            "enc": m.group("enc"),
        }))
    return out


def frames_log_to_records(text: str) -> list[dict[str, object]]:
    """Convert captured ``govee_ble_local.frames`` logger output into analyzer records
    ``{dir, plain, wire, enc}`` (empty plain -> None, i.e. ciphertext-only)."""
    return [rec for _, rec in _numbered_frame_records(text)]


def _record_problem(rec: object) -> str | None:
    """Why ``rec`` is not a usable capture record, or None if it is fine."""
    if not isinstance(rec, dict):
        return f"record is {type(rec).__name__}, expected an object"
    plain = rec.get("plain")
    if plain:
        if not isinstance(plain, str):
            return f"'plain' is {type(plain).__name__}, expected a hex string"
        try:
            bytes.fromhex(plain)
        except ValueError as err:
            return f"invalid hex in 'plain': {err}"
    return None


def _checked_frames_log(text: str, path: str) -> tuple[list[dict[str, object]], int]:
    """Convert frames-log text to records, applying the JSONL per-record validation: invalid
    records are skipped and reported on stderr as ``path:lineno: reason``."""
    good: list[dict[str, object]] = []
    skipped = 0
    for lineno, rec in _numbered_frame_records(text):
        problem = _record_problem(rec)
        if problem is not None:
            print(f"{path}:{lineno}: {problem}", file=sys.stderr)
            skipped += 1
            continue
        good.append(rec)
    return good, skipped


def _load_jsonl_checked(paths: list[str]) -> tuple[list[dict[str, object]], int]:
    """Load JSONL captures; skip (and report on stderr as ``path:lineno: reason``) lines that
    are not valid JSON, not an object, or carry invalid hex. Returns (records, skipped).
    A missing/unreadable file raises OSError (the CLI maps it to exit code 2)."""
    records: list[dict[str, object]] = []
    skipped = 0
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as err:
                    problem: str | None = f"invalid JSON: {err}"
                else:
                    problem = _record_problem(rec)
                if problem is not None:
                    print(f"{path}:{lineno}: {problem}", file=sys.stderr)
                    skipped += 1
                    continue
                records.append(rec)
    return records, skipped


def _load_jsonl(paths: list[str]) -> list[dict[str, object]]:
    return _load_jsonl_checked(paths)[0]


def analyze(records: list[dict[str, object]], malformed_lines: int = 0) -> int:
    """Print a coverage report for a list of capture records; return 1 if any hard issue.

    ``malformed_lines`` is the count of input lines the loader skipped; it is only reported."""
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
    if malformed_lines:
        print(f"{malformed_lines} malformed input line(s) skipped (see stderr)")
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

    try:
        if args.from_frames_log:
            with open(args.from_frames_log, encoding="utf-8") as fh:
                records, skipped = _checked_frames_log(fh.read(), args.from_frames_log)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as out:
                    for rec in records:
                        out.write(json.dumps(rec) + "\n")
                print(f"wrote {len(records)} records to {args.out}\n")
            return analyze(records, skipped)

        if not args.paths:
            ap.error("give a JSONL capture path, or --from-frames-log FILE")
        records, skipped = _load_jsonl_checked(args.paths)
    except (OSError, UnicodeDecodeError) as err:
        name = getattr(err, "filename", None) or "input"
        reason = err.strerror if isinstance(err, OSError) and err.strerror else str(err)
        print(f"govee-ble-analyze: cannot read {name}: {reason}", file=sys.stderr)
        return 2
    return analyze(records, skipped)


if __name__ == "__main__":
    raise SystemExit(main())
