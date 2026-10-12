"""Characterization tests for govee_ble_local.transport.framelog."""
from __future__ import annotations

import json
import os
import queue
import time
from pathlib import Path

import pytest

from govee_ble_local.transport import framelog
from govee_ble_local.transport.framelog import ENV_VAR, FrameLog


def _wait_for_lines(path: Path, n: int, timeout: float = 5.0) -> list[str]:
    deadline = time.monotonic() + timeout
    lines: list[str] = []
    while time.monotonic() < deadline:
        if path.exists():
            lines = path.read_text(encoding="ascii").splitlines()
            if len(lines) >= n:
                return lines
        time.sleep(0.01)
    return lines


def _wait_queue_empty(timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not framelog._QUEUE.empty() and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.1)  # let the writer finish the in-flight item


def test_resolve_explicit_env_and_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert FrameLog.resolve(None, "AA") is None
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "env.jsonl"))
    env_log = FrameLog.resolve(None, "AA")
    assert env_log is not None and env_log._path == str(tmp_path / "env.jsonl")
    explicit = FrameLog.resolve(str(tmp_path / "x.jsonl"), "AA")
    assert explicit is not None and explicit._path == str(tmp_path / "x.jsonl")
    monkeypatch.setenv(ENV_VAR, "")
    assert FrameLog.resolve(None, "AA") is None


def test_record_writes_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "cap.jsonl"
    log = FrameLog(str(path), "AA:BB")
    log.record("tx", wire=b"\x01\x02", plain=b"\xaa\xbb", enc="session")
    log.record("rx", wire=b"\xff")
    lines = _wait_for_lines(path, 2)
    assert len(lines) == 2
    first, second = (json.loads(x) for x in lines)
    assert first["addr"] == "AA:BB" and first["dir"] == "tx"
    assert first["plain"] == "aabb" and first["wire"] == "0102" and first["enc"] == "session"
    assert isinstance(first["ts"], float)
    assert second["plain"] is None and second["wire"] == "ff" and second["enc"] == ""


def test_lines_are_ascii_json(tmp_path: Path) -> None:
    path = tmp_path / "ascii.jsonl"
    log = FrameLog(str(path), "ADDR-é")  # non-ascii address is JSON-escaped, so file stays ascii
    log.record("tx", wire=b"\x00")
    lines = _wait_for_lines(path, 1)
    assert lines and lines[0].isascii()
    assert json.loads(lines[0])["addr"] == "ADDR-é"


def test_write_error_is_swallowed(tmp_path: Path) -> None:
    bad = tmp_path / "no" / "such" / "dir" / "cap.jsonl"
    good = tmp_path / "good.jsonl"
    FrameLog(str(bad), "A").record("tx", wire=b"\x01")
    # the writer thread must survive the OSError and keep serving later records
    FrameLog(str(good), "B").record("tx", wire=b"\x02")
    lines = _wait_for_lines(good, 1)
    assert len(lines) == 1
    assert not bad.exists()
    assert framelog._writer is not None and framelog._writer.is_alive()


def test_ensure_writer_restarts_dead_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    class Dead:
        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(framelog, "_writer", Dead())
    framelog._ensure_writer()
    assert framelog._writer is not None and not isinstance(framelog._writer, Dead)
    assert framelog._writer.is_alive()


def test_batching_across_paths_preserves_all_records(tmp_path: Path) -> None:
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    la, lb = FrameLog(str(a), "A"), FrameLog(str(b), "B")
    for i in range(5):
        la.record("tx", wire=bytes([i]))
        lb.record("rx", wire=bytes([i]))
    assert len(_wait_for_lines(a, 5)) == 5
    assert len(_wait_for_lines(b, 5)) == 5
    # Ordering is NOT asserted: when the writer meets a record for another path it
    # re-queues it at the tail, so interleaved paths can reorder (observed behavior).
    assert sorted(json.loads(x)["wire"] for x in a.read_text().splitlines()) == [f"{i:02x}" for i in range(5)]
    assert isinstance(framelog._QUEUE, queue.SimpleQueue)
    _wait_queue_empty()
    assert os.path.exists(a)
