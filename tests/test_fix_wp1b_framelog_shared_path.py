"""A failed directory check disables only its own capture, not others sharing the path (WP1b-011),
and the group/world-accessible warning only runs where POSIX modes mean something (WP1b-010)."""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from govee_ble_local.transport import framelog
from govee_ble_local.transport.framelog import FrameLog


def _drain(timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while framelog._QUEUE.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.005)
    assert framelog._QUEUE.unfinished_tasks == 0


def _wires(path: Path) -> list[str]:
    return [json.loads(x)["wire"] for x in path.read_text(encoding="ascii").splitlines()]


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    _drain()
    framelog._failed_paths.clear()
    framelog._mode_warned.clear()
    yield
    _drain()


def test_failed_check_of_a_new_capture_leaves_an_enabled_capture_on_the_path_writing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # $GOVEE_FRAME_LOG: every connection captures to one path. A capture built while the
    # directory is missing is disabled; one that passed its check keeps writing once it's back.
    d = tmp_path / "caps"
    d.mkdir()
    path = d / "c.jsonl"
    with caplog.at_level(logging.WARNING, logger="govee_ble_local"):
        a = FrameLog(str(path), "A")
        a.record("tx", wire=b"\x01")
        _drain()
        path.unlink()
        d.rmdir()
        b = FrameLog(str(path), "B")
        _drain()
        d.mkdir()
        a.record("tx", wire=b"\x02")
        _drain()
    assert (a._enabled, b._enabled) == (True, False)
    assert _wires(path) == ["02"]
    warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1 and "disabled" in warns[0] and "does not exist" in warns[0]


def test_failed_check_drops_only_that_captures_queued_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One batch as the writer sees it: A's check passes, then B's fails (the directory went away
    # in between). B's line, recorded before its check ran, is dropped; A's lines on the same
    # path, and a bare (path, line) item that names no capture, are still written.
    path = tmp_path / "c.jsonl"
    items: list[framelog._Item] = []
    monkeypatch.setattr(framelog, "_enqueue", items.append)  # held back: processed in one batch below
    checks = iter([True, False])
    monkeypatch.setattr(FrameLog, "_validate_dir", staticmethod(lambda _path: next(checks)))
    a, b = FrameLog(str(path), "A"), FrameLog(str(path), "B")
    a.record("tx", wire=b"\x01")
    b.record("tx", wire=b"\x02")
    a.record("tx", wire=b"\x03")
    items.append((str(path), json.dumps({"wire": "04"}) + "\n"))
    framelog._process_batch(items)
    assert (a._enabled, b._enabled) == (True, False)
    assert _wires(path) == ["01", "03", "04"]


def test_mode_warning_skipped_off_posix(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows makes st_mode up from the read-only attribute (0o666 / 0o444), so the group/world
    # bits are always set there and say nothing about who can read the file.
    path = tmp_path / "c.jsonl"
    path.write_text("")
    os.chmod(path, 0o644)
    with caplog.at_level(logging.WARNING, logger="govee_ble_local"):
        monkeypatch.setattr(os, "name", "nt")
        framelog._write_batch(str(path), ["x\n"])  # called directly: no other thread runs while patched
        monkeypatch.undo()
    assert not [r for r in caplog.records if "group/world-accessible" in r.getMessage()]
    assert path.read_text(encoding="ascii") == "x\n"
