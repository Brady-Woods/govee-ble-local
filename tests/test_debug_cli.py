"""Characterization tests for the govee_ble_local.debug CLI."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from govee_ble_local import debug

# A valid 20-byte frame: 0x33 0x01 0x01 + zeros + xor checksum.
def _frame(*head: int) -> bytes:
    body = bytes(head) + bytes(19 - len(head))
    c = 0
    for b in body:
        c ^= b
    return body + bytes([c])


PLAIN = _frame(0x33, 0x01, 0x01).hex()
LOG_TEXT = (
    "2026-01-01 12:00:00 DEBUG govee_ble_local.frames: AA:BB tx power plain="
    + PLAIN.upper() + " wire=" + PLAIN + " enc=session\n"
    "unrelated line\n"
    "2026-01-01 12:00:01 DEBUG govee_ble_local.frames: AA:BB rx notify plain= wire=DEADBEEF enc=session\n"
)


def test_frames_log_to_records() -> None:
    recs = debug.frames_log_to_records(LOG_TEXT)
    assert recs == [
        {"dir": "tx", "plain": PLAIN, "wire": PLAIN, "enc": "session"},
        {"dir": "rx", "plain": None, "wire": "deadbeef", "enc": "session"},
    ]


def test_load_jsonl_skips_blank_lines_and_concatenates(tmp_path: Path) -> None:
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text(json.dumps({"dir": "tx", "plain": PLAIN}) + "\n\n   \n")
    b.write_text(json.dumps({"dir": "rx", "plain": None}) + "\n")
    assert debug._load_jsonl([str(a), str(b)]) == [
        {"dir": "tx", "plain": PLAIN},
        {"dir": "rx", "plain": None},
    ]


def test_analyze_reports_coverage(capsys: pytest.CaptureFixture[str]) -> None:
    rc = debug.analyze([
        {"dir": "tx", "plain": PLAIN},
        {"dir": "rx", "plain": None},          # skipped (ciphertext only)
    ])
    out = capsys.readouterr().out
    assert "1 plaintext frames; 1 skipped/ciphertext" in out
    assert "coverage (dir + decoded label):" in out
    assert "ISSUES (valid frames the spec does NOT represent):" in out
    assert rc in (0, 1)


def test_analyze_flags_artifact_for_bad_checksum(capsys: pytest.CaptureFixture[str]) -> None:
    bad = "33" + "00" * 18 + "ff"
    debug.analyze([{"dir": "rx", "plain": bad}])
    assert "unparseable (invalid checksum" in capsys.readouterr().out


def test_main_with_jsonl_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = tmp_path / "cap.jsonl"
    p.write_text(json.dumps({"dir": "tx", "plain": PLAIN, "wire": PLAIN, "enc": ""}) + "\n")
    rc = debug.main([str(p)])
    assert rc in (0, 1)
    assert "frame-log analysis" in capsys.readouterr().out


def test_main_no_args_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        debug.main([])
    assert exc.value.code == 2
    assert "give a JSONL capture path" in capsys.readouterr().err


def test_main_from_frames_log_with_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "session.log"
    out = tmp_path / "session.jsonl"
    src.write_text(LOG_TEXT)
    rc = debug.main(["--from-frames-log", str(src), "-o", str(out)])
    assert rc in (0, 1)
    printed = capsys.readouterr().out
    assert f"wrote 2 records to {out}" in printed
    written = [json.loads(x) for x in out.read_text().splitlines()]
    assert written == debug.frames_log_to_records(LOG_TEXT)


def test_main_from_frames_log_without_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "session.log"
    src.write_text(LOG_TEXT)
    debug.main(["--from-frames-log", str(src)])
    assert "wrote" not in capsys.readouterr().out
