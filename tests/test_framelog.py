"""Characterization tests for govee_ble_local.transport.framelog."""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from govee_ble_local.transport import framelog
from govee_ble_local.transport.framelog import ENV_VAR, FrameLog


def _writer_threads() -> int:
    return sum(t.name == "govee-framelog" for t in threading.enumerate())


@pytest.fixture(autouse=True)
def _no_stray_writers() -> Iterator[None]:
    # Only the first capture in the process may start the (single) writer thread; a test that
    # starts more leaves extra consumers on the shared queue for every later test.
    before = _writer_threads()
    yield
    assert _writer_threads() <= max(before, 1)


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


def _drain(timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while framelog._QUEUE.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.005)
    assert framelog._QUEUE.unfinished_tasks == 0


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
    unchecked = tmp_path / "other" / "cap.jsonl"
    good = tmp_path / "good.jsonl"
    # A missing directory disables the log (OPS-1); the writer must also survive a failure
    # that only appears at write time (a path no FrameLog checked).
    FrameLog(str(bad), "A").record("tx", wire=b"\x01")
    framelog._enqueue((str(unchecked), "x\n"))
    # the writer thread must survive the OSError and keep serving later records
    FrameLog(str(good), "B").record("tx", wire=b"\x02")
    lines = _wait_for_lines(good, 1)
    assert len(lines) == 1
    assert not bad.exists() and not unchecked.exists()
    assert framelog._writer is not None and framelog._writer.is_alive()


class _Dead:
    def is_alive(self) -> bool:
        return False


def _fake_threading(monkeypatch: pytest.MonkeyPatch) -> list[Callable[[], None]]:
    """Stub framelog's Thread so a test can check a (re)start without leaving a real writer behind."""
    started: list[Callable[[], None]] = []

    class FakeThread:
        def __init__(self, *, target: Callable[[], None], name: str, daemon: bool) -> None:
            assert name == "govee-framelog" and daemon
            self._target = target

        def start(self) -> None:
            started.append(self._target)

        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(framelog, "threading", SimpleNamespace(Thread=FakeThread))
    return started


def test_ensure_writer_restarts_dead_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    started = _fake_threading(monkeypatch)
    monkeypatch.setattr(framelog, "_writer", _Dead())
    framelog._ensure_writer()
    assert started == [framelog._write_forever]
    assert framelog._writer is not None and framelog._writer.is_alive()
    framelog._ensure_writer()  # alive now: no second start
    assert started == [framelog._write_forever]


def test_record_restarts_dead_writer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    q: queue.Queue[framelog._Item] = queue.Queue()
    monkeypatch.setattr(framelog, "_QUEUE", q)
    started = _fake_threading(monkeypatch)
    log = FrameLog(str(tmp_path / "r.jsonl"), "A")
    monkeypatch.setattr(framelog, "_writer", _Dead())
    log.record("tx", wire=b"\x01")
    assert started[-1:] == [framelog._write_forever]
    assert [type(item[1]).__name__ for item in q.queue] == ["FrameLog", "str"]
    assert q.queue[1][2:] == (log,)  # the line names its capture


def test_batching_across_paths_preserves_all_records(tmp_path: Path) -> None:
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    la, lb = FrameLog(str(a), "A"), FrameLog(str(b), "B")
    for i in range(5):
        la.record("tx", wire=bytes([i]))
        lb.record("rx", wire=bytes([i]))
    assert len(_wait_for_lines(a, 5)) == 5
    assert len(_wait_for_lines(b, 5)) == 5
    # Per-path arrival order is preserved (see test_framelog_hardening).
    assert [json.loads(x)["wire"] for x in a.read_text().splitlines()] == [f"{i:02x}" for i in range(5)]
    assert isinstance(framelog._QUEUE, queue.Queue) and framelog._QUEUE.maxsize > 0


def test_construction_does_no_filesystem_io_on_the_caller_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # FrameLog is built from GoveeConnection.__init__, i.e. on the event loop: the directory
    # check (stat / access syscalls) must run on the writer thread instead.
    caller = threading.get_ident()
    seen: list[tuple[str, int]] = []
    real_isdir, real_access = os.path.isdir, os.access

    def isdir(p: str) -> bool:
        if p == str(tmp_path):
            seen.append(("isdir", threading.get_ident()))
        return real_isdir(p)

    def access(p: str, mode: int) -> bool:
        if p == str(tmp_path):
            seen.append(("access", threading.get_ident()))
        return real_access(p, mode)

    _drain()
    monkeypatch.setattr(os.path, "isdir", isdir)
    monkeypatch.setattr(os, "access", access)
    path = tmp_path / "c.jsonl"
    log = FrameLog(str(path), "A")
    assert not [s for s in seen if s[1] == caller]
    log.record("tx", wire=b"\x01")
    assert len(_wait_for_lines(path, 1)) == 1
    _drain()
    assert {name for name, _ in seen} == {"isdir", "access"}
    assert all(ident != caller for _, ident in seen)


def test_new_capture_rechecks_a_directory_created_since(tmp_path: Path) -> None:
    path = tmp_path / "later" / "c.jsonl"
    first = FrameLog(str(path), "A")
    first.record("tx", wire=b"\x01")
    _drain()
    assert first._enabled is False and not path.parent.exists()
    path.parent.mkdir()
    second = FrameLog(str(path), "A")
    second.record("tx", wire=b"\x02")
    _drain()
    assert second._enabled is True
    assert [json.loads(x)["wire"] for x in path.read_text().splitlines()] == ["02"]
