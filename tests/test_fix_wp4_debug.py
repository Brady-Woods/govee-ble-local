"""Regression tests for the govee-ble-analyze review fixes: per-device 0xAC bursts, record
validation in analyze(), deep-JSON lines, read/write error split, 0600 output, --help layout."""
from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest

from conftest import require_optional

from govee_ble_local import debug

require_optional("kaitaistruct", reason="Kaitai runtime not installed")

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX semantics")


def _frame(*head: int) -> bytes:
    body = bytes(head) + bytes(19 - len(head))
    c = 0
    for b in body:
        c ^= b
    return body + bytes([c])


PLAIN = _frame(0x33, 0x01, 0x01).hex()

# A real H60A6 status burst (tests/test_wire_describe.py) ...
# Device ids and MACs in it are made-up examples (checksums recomputed).
_BURST_A = [bytes.fromhex(h) for h in (
    "ac000a000c0300010101040150050415010000e8",
    "ac010707065753005e00020711105653005e00ae",
    "ac0202a60a0100290104030000070d1102005e69",
    "ac030053560100290104031104001e0f0f12079a",
    "ac04ff640000800f00231000000080000000808f",
    "ac0500000080000000804102020130020101a57e",
    "ac06110132ff00006400ff00640000ff64ff00ec",
    "ac0700a511026400ff00640000ff64ff000064e2",
    "ac0800ff00a51103640000ff64ff00006400ff77",
    "acff00640000ffa5050464ff00000000000000f7",
)]
# ... and a clean 3-chunk burst from a second device: header [packets u16le 3, lastLen 17,
# cmd 03, sub 00], then one brightness TLV whose 28-byte value runs one byte into the last
# chunk, so stitching B's first two chunks onto A's chunk 02 walks into phantom TLVs.
_BURST_B = [
    _frame(0xAC, 0x00, 0x03, 0x00, 17, 0x03, 0x00, 0x04, 28, 0x32), _frame(0xAC, 0x01), _frame(0xAC, 0xFF),
]
# A00 A01 | B00 B01 | A02..AFF | BFF: the two devices' bursts overlap in one capture.
_INTERLEAVED = (
    [("AA:AA", f) for f in _BURST_A[:2]] + [("BB:BB", f) for f in _BURST_B[:2]]
    + [("AA:AA", f) for f in _BURST_A[2:]] + [("BB:BB", f) for f in _BURST_B[2:]]
)


def test_each_burst_alone_is_clean(capsys: pytest.CaptureFixture[str]) -> None:
    for burst in (_BURST_A, _BURST_B):
        assert debug.analyze([{"dir": "rx", "plain": f.hex()} for f in burst]) == 0
    capsys.readouterr()


def test_interleaved_bursts_from_two_devices_are_reassembled_per_device(
    capsys: pytest.CaptureFixture[str],
) -> None:
    recs: list[dict[str, object]] = [{"addr": a, "dir": "rx", "plain": f.hex()} for a, f in _INTERLEAVED]
    assert debug.analyze(recs) == 0
    out = capsys.readouterr().out
    assert "ISSUES (valid frames the spec does NOT represent): 0" in out
    assert "0x30:" in out and "malformed 0xAC" not in out       # burst A fully decoded


def test_frames_log_keeps_the_address_for_burst_grouping(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "session.log"
    log.write_text("".join(
        f"2026-01-01 12:00:00 DEBUG govee_ble_local.frames: {a} rx notify plain={f.hex()} "
        f"wire={f.hex()} enc=session\n"
        for a, f in _INTERLEAVED
    ))
    assert debug.main(["--from-frames-log", str(log)]) == 0
    assert "ISSUES (valid frames the spec does NOT represent): 0" in capsys.readouterr().out
    assert {r["addr"] for r in debug.frames_log_to_records(log.read_text())} == {"AA:AA", "BB:BB"}


def test_frames_log_line_without_address_still_parses() -> None:
    assert debug.frames_log_to_records(f"tx w plain={PLAIN} wire={PLAIN} enc=none") == [
        {"dir": "tx", "plain": PLAIN, "wire": PLAIN, "enc": "none"},
    ]


def test_long_token_line_is_matched_in_linear_time() -> None:
    # Another integration's log line with one long base64-like token. The unanchored optional
    # address group took ~2.4 s on a 20k-character token (quadratic); anchored it takes ~1 ms.
    blob = "A" * 25_000
    other = f"2026-01-01 12:00:00 DEBUG other.integration: payload={blob} plain=00 wire=00 enc=none"
    frame = f"2026-01-01 12:00:01 DEBUG govee_ble_local.frames: AA:BB tx w plain={PLAIN} wire={PLAIN} enc=e7"
    start = time.perf_counter()
    assert debug._FRAME_LINE.search(blob) is None
    recs = debug.frames_log_to_records(f"{other}\n{frame}\n")
    assert time.perf_counter() - start < 0.5          # ~1000x headroom over the anchored regex
    assert recs == [{"addr": "AA:BB", "dir": "tx", "plain": PLAIN, "wire": PLAIN, "enc": "e7"}]


def test_analyze_skips_invalid_records_instead_of_crashing(capsys: pytest.CaptureFixture[str]) -> None:
    recs = debug.frames_log_to_records("AA tx q plain=abc wire=00 enc=none")   # odd-length hex
    bad: list[dict[str, object]] = [{"dir": "tx", "plain": 5}, {"dir": "tx", "plain": "zz"}]
    assert debug.analyze(recs + bad + [{"dir": "tx", "plain": PLAIN}]) == 0
    out = capsys.readouterr().out
    assert "3 invalid record(s) skipped" in out and "1 plaintext frames" in out


def test_deeply_nested_json_line_is_skipped_and_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    p = tmp_path / "cap.jsonl"
    p.write_text("[" * 200_000 + "\n" + json.dumps({"dir": "tx", "plain": PLAIN}) + "\n")
    assert debug.main([str(p)]) == 0
    cap = capsys.readouterr()
    assert f"{p}:1: invalid JSON" in cap.err and "1 malformed input line(s) skipped" in cap.out


def test_unwritable_output_is_a_write_error_not_a_read_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "session.log"
    src.write_text(f"AA:BB tx w plain={PLAIN} wire={PLAIN} enc=none\n")
    out = tmp_path / "missing-dir" / "out.jsonl"
    assert debug.main(["--from-frames-log", str(src), "-o", str(out)]) == 2
    err = capsys.readouterr().err
    assert f"cannot write {out}" in err and "cannot read" not in err


@posix_only
def test_output_file_is_private_and_never_follows_a_symlink(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "session.log"
    src.write_text(f"AA:BB tx w plain={PLAIN} wire={PLAIN} enc=none\n")
    out = tmp_path / "out.jsonl"
    old = os.umask(0o022)
    try:
        assert debug.main(["--from-frames-log", str(src), "-o", str(out)]) == 0
    finally:
        os.umask(old)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert json.loads(out.read_text())["addr"] == "AA:BB"

    target = tmp_path / "victim.txt"
    target.write_text("keep me")
    link = tmp_path / "link.jsonl"
    link.symlink_to(target)
    assert debug.main(["--from-frames-log", str(src), "-o", str(link)]) == 2
    assert target.read_text() == "keep me" and "cannot write" in capsys.readouterr().err


def test_help_keeps_the_docstring_layout(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as ei:
        debug.main(["--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "\n  * a JSONL frame log" in out
    assert "\n    govee-ble-analyze --from-frames-log session.log" in out
    assert "``" not in out
