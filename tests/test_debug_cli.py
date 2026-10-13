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


def test_missing_file_exits_2_with_message(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "nope.jsonl"
    assert debug.main([str(missing)]) == 2
    err = capsys.readouterr().err
    assert "cannot read" in err and "nope.jsonl" in err


def test_missing_frames_log_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert debug.main(["--from-frames-log", str(tmp_path / "nope.log")]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_malformed_lines_skipped_reported_and_analysis_continues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    p = tmp_path / "cap.jsonl"
    p.write_text(
        json.dumps({"dir": "tx", "plain": PLAIN}) + "\n"
        "{not json\n"                                   # line 2: bad JSON
        "[1, 2]\n"                                      # line 3: non-dict record
        + json.dumps({"dir": "rx", "plain": "zz11"}) + "\n"   # line 4: invalid hex
        + json.dumps({"dir": "rx", "plain": 5}) + "\n"        # line 5: wrong type
        + json.dumps({"dir": "tx", "plain": PLAIN}) + "\n"
    )
    rc = debug.main([str(p)])
    cap = capsys.readouterr()
    assert rc in (0, 1)
    assert f"{p}:2: invalid JSON" in cap.err
    assert f"{p}:3: record is list" in cap.err
    assert f"{p}:4: invalid hex" in cap.err
    assert f"{p}:5: 'plain' is int" in cap.err
    assert "2 plaintext frames" in cap.out
    assert "4 malformed input line(s) skipped" in cap.out


def test_load_jsonl_checked_counts(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    p.write_text("oops\n" + json.dumps({"dir": "tx", "plain": PLAIN}) + "\n")
    recs, skipped = debug._load_jsonl_checked([str(p)])
    assert len(recs) == 1 and skipped == 1


def test_analyze_default_reports_no_malformed_line(capsys: pytest.CaptureFixture[str]) -> None:
    debug.analyze([{"dir": "tx", "plain": PLAIN}])
    assert "malformed input line" not in capsys.readouterr().out
