"""Offline diagnostics CLI: decode a captured session and report spec coverage/gaps.

Two capture surfaces feed this (see the diagnostics docs):
  * a JSONL frame log (``frame_log=`` / ``$GOVEE_FRAME_LOG``) — analyse directly, and
  * the ``govee_ble_local.frames`` logger (HA-native, filesystem-free) — ``--from-frames-log``
    converts that logger's output back into the JSONL shape first.

Installed as the ``govee-ble-analyze`` console script; also ``python -m govee_ble_local.debug``.

    govee-ble-analyze capture.jsonl [more.jsonl ...]
    govee-ble-analyze --from-frames-log session.log [-o session.jsonl]

Exit codes: 0 = every plaintext frame is represented by the spec, 1 = at least one hard
issue, 2 = an input could not be read or the output could not be written.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from collections.abc import Iterable, Iterator
from typing import cast

from .wire.build import PRO_MULTI_AC
from .wire.describe import Direction, describe_frame, status_burst_report

# Matches a `govee_ble_local.frames` log line regardless of the logging prefix
# (timestamp/level/logger). Format emitted by GoveeConnection._capture:
#   "<addr> <dir> <label> plain=<hex> wire=<hex> enc=<mode>"
# The address is the token right before the direction; it is optional, so a line
# without one still parses (as before). `(?<!\S)` starts the address only at a token
# boundary: without it, re.search retries `\S+` from every offset inside a long token
# (a base64 blob from another integration's log line), which is quadratic in its length.
_FRAME_LINE = re.compile(
    r"(?:(?<!\S)(?P<addr>\S+)\s+)?\b(?P<dir>tx|rx)\b.*?\bplain=(?P<plain>[0-9a-fA-F]*)"
    r"\s+wire=(?P<wire>[0-9a-fA-F]*)\s+enc=(?P<enc>\S+)"
)

# The frames logger's name: Python's default logging format ("%(levelname)s:%(name)s:%(message)s")
# runs it into the address token with no space ("DEBUG:govee_ble_local.frames:AA:BB:..."), so it is
# stripped from the front of a captured address.
_FRAMES_LOGGER_PREFIX = "govee_ble_local.frames:"

# One (where, record, parse problem) item for `_usable`: `where` is the `path:lineno` that
# the skip report names, and a non-None parse problem means the line never became a record.
_NumberedRecord = tuple[str, object, str | None]


def _numbered_frame_records(text: str) -> list[tuple[int, dict[str, object]]]:
    out: list[tuple[int, dict[str, object]]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        # Most of a saved HA log is other loggers' lines: a frame line always has "plain=",
        # so skip the rest without running the regex at all.
        m = _FRAME_LINE.search(line) if "plain=" in line else None
        if not m:
            continue
        rec: dict[str, object] = {}
        addr = (m.group("addr") or "").rpartition(_FRAMES_LOGGER_PREFIX)[2]
        if addr:
            rec["addr"] = addr
        plain = m.group("plain")
        rec.update({
            "dir": m.group("dir"),
            "plain": plain.lower() or None,
            "wire": m.group("wire").lower(),
            "enc": m.group("enc"),
        })
        out.append((lineno, rec))
    return out


def frames_log_to_records(text: str) -> list[dict[str, object]]:
    """Convert captured ``govee_ble_local.frames`` logger output into analyzer records.

    Each record is ``{addr, dir, plain, wire, enc}`` (``addr`` only when the line names
    the device; empty plain -> None, i.e. ciphertext-only). Records are not validated
    here; :func:`analyze` skips unusable ones.
    """
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


def _usable(items: Iterable[_NumberedRecord]) -> tuple[list[dict[str, object]], int]:
    """Keep the usable records; report every other one on stderr as ``path:lineno: reason``.

    Shared by both loaders, so a frames log and a JSONL capture are validated the same way.
    Returns (records, skipped).
    """
    good: list[dict[str, object]] = []
    skipped = 0
    for where, rec, problem in items:
        if problem is None:
            problem = _record_problem(rec)
        if problem is not None:
            print(f"{where}: {problem}", file=sys.stderr)
            skipped += 1
            continue
        good.append(cast("dict[str, object]", rec))  # _record_problem accepted it: a dict
    return good, skipped


def _checked_frames_log(text: str, path: str) -> tuple[list[dict[str, object]], int]:
    """Convert frames-log text to records, skipping and reporting invalid ones (see `_usable`)."""
    return _usable((f"{path}:{lineno}", rec, None) for lineno, rec in _numbered_frame_records(text))


def _jsonl_items(paths: list[str]) -> Iterator[_NumberedRecord]:
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, RecursionError) as err:  # RecursionError: deep nesting
                    yield f"{path}:{lineno}", None, f"invalid JSON: {err}"
                else:
                    yield f"{path}:{lineno}", rec, None


def _load_jsonl_checked(paths: list[str]) -> tuple[list[dict[str, object]], int]:
    """Load JSONL captures, skipping and reporting unusable lines.

    Lines that are not valid JSON, not an object, or carry invalid hex are reported on
    stderr as ``path:lineno: reason``. Returns (records, skipped). A missing/unreadable
    file raises OSError (the CLI maps it to exit code 2).
    """
    return _usable(_jsonl_items(paths))


def _write_records(path: str, records: list[dict[str, object]]) -> None:
    """Write ``records`` as JSONL, created 0600 and never through a symlink.

    The records carry the same device ids and handshake bytes as a frame capture, so the
    file gets the same protection as the library's own sink (transport/framelog.py).
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        for rec in records:
            out.write(json.dumps(rec) + "\n")


def _direction(value: object) -> Direction:
    """A record's ``dir`` as a :data:`~.wire.describe.Direction`: "rx" / "tx", anything else "?"."""
    if value == "rx":
        return "rx"
    if value == "tx":
        return "tx"
    return "?"


def analyze(records: list[dict[str, object]], malformed_lines: int = 0) -> int:
    """Print a coverage report for a list of capture records; return 1 if any hard issue.

    ``malformed_lines`` is the count of input lines the loader skipped; it is only reported.
    Records the loaders would reject (not an object, bad ``plain`` hex) are skipped and
    counted here too, so records built by hand or by :func:`frames_log_to_records` can't
    crash the report. 0xAC status bursts are reassembled per device (``addr``), so
    interleaved bursts from several devices in one capture are not stitched together.
    """
    frames: list[tuple[str, Direction, bytes]] = []
    skipped = 0
    invalid = 0
    for rec in records:
        if _record_problem(rec) is not None:
            invalid += 1
            continue
        plain_hex = rec.get("plain")
        if not plain_hex:
            skipped += 1
            continue
        frames.append((str(rec.get("addr", "?")), _direction(rec.get("dir")), bytes.fromhex(str(plain_hex))))

    hist: collections.Counter[str] = collections.Counter()
    hard: list[str] = []
    soft: collections.Counter[str] = collections.Counter()
    artifacts: collections.Counter[str] = collections.Counter()
    for _addr, direction, plain in frames:
        label, issues = describe_frame(plain, direction)
        hist[f"{direction:2} {label}"] += 1
        for sev, reason in issues:
            if sev == "hard":
                hard.append(f"[{direction}] {plain.hex()}  {reason}")
            elif sev == "artifact":
                artifacts[reason] += 1
            else:
                soft[reason] += 1

    rx_ac: dict[str, list[bytes]] = {}
    for addr, d, p in frames:
        if d == "rx" and p and p[0] == PRO_MULTI_AC:
            rx_ac.setdefault(addr, []).append(p)
    tlv_types: collections.Counter[int] = collections.Counter()
    malformed = truncated = redacted = special = 0
    for chunks in rx_ac.values():
        report = status_burst_report(chunks)
        tlv_types.update(report.tlv_types)
        hard.extend(f"[rx] {g}" for g in report.gaps)
        malformed += report.malformed
        truncated += report.truncated
        redacted += report.redacted
        special += report.special_info

    print(f"== frame-log analysis ({len(frames)} plaintext frames; {skipped} skipped/ciphertext) ==")
    if malformed_lines:
        print(f"{malformed_lines} malformed input line(s) skipped (see stderr)")
    if invalid:
        print(f"{invalid} invalid record(s) skipped (not an object, or bad 'plain' hex)")
    print("\ncoverage (dir + decoded label):")
    for label, n in hist.most_common():
        print(f"  {n:5d}  {label}")
    if tlv_types:
        print("\n0xAC status reply — reassembled TLV types seen (type:count):")
        print("  " + ", ".join(f"0x{t:02x}:{n}" for t, n in sorted(tlv_types.items())))
    if special:
        print(f"\n0xAC special-info replies (ac a1 segment IC record, not a TLV stream): {special}")
    if malformed:
        print(f"\nmalformed 0xAC bursts skipped (dropped/merged frames, NOT spec gaps): {malformed}")
    if truncated:
        print("\n0xAC bursts whose TLV stream overruns the complete reply (walked up to the cut; the "
              f"runtime uses NO state from them): {truncated}")
    if redacted:
        print("\n0xAC bursts in a redacted capture (payload zeroed; framing checked, TLVs not walked): "
              f"{redacted}")
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
    """Run the ``govee-ble-analyze`` CLI on ``argv``; return its exit code (see the module doc)."""
    ap = argparse.ArgumentParser(
        prog="govee-ble-analyze",
        # Keep the docstring's bullets and examples; drop its reST literal markup for the terminal.
        description=(__doc__ or "").replace("``", ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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
        else:
            if not args.paths:
                ap.error("give a JSONL capture path, or --from-frames-log FILE")
            records, skipped = _load_jsonl_checked(args.paths)
    except (OSError, UnicodeDecodeError) as err:
        name = getattr(err, "filename", None) or "input"
        reason = err.strerror if isinstance(err, OSError) and err.strerror else str(err)
        print(f"govee-ble-analyze: cannot read {name}: {reason}", file=sys.stderr)
        return 2

    if args.from_frames_log and args.out:
        try:
            _write_records(args.out, records)
        except OSError as err:
            print(f"govee-ble-analyze: cannot write {args.out}: {err.strerror or err}", file=sys.stderr)
            return 2
        print(f"wrote {len(records)} records to {args.out}\n")
    return analyze(records, skipped)


if __name__ == "__main__":
    raise SystemExit(main())
