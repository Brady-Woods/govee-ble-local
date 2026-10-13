"""Hardening tests for transport.framelog (SEC-2, SEC-3, OPS-1, exit flush)."""
from __future__ import annotations

import json
import logging
import os
import queue
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from govee_ble_local.transport import framelog
from govee_ble_local.transport.framelog import FrameLog

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")


def _drain(timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while framelog._QUEUE.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.005)
    assert framelog._QUEUE.unfinished_tasks == 0


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="ascii").splitlines()


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    framelog._failed_paths.clear()
    framelog._drop_warned = False


@posix_only
def test_new_file_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    FrameLog(str(path), "A").record("tx", wire=b"\x01")
    _drain()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


@posix_only
def test_existing_file_mode_kept(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text("")
    os.chmod(path, 0o644)
    FrameLog(str(path), "A").record("tx", wire=b"\x01")
    _drain()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644
    assert len(_lines(path)) == 1


def test_docstring_states_sensitivity() -> None:
    doc = framelog.__doc__ or ""
    assert "never keys" not in doc
    assert "sensitive" in doc


def test_missing_directory_disables_with_one_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = tmp_path / "nope" / "cap.jsonl"
    with caplog.at_level(logging.WARNING, logger="govee_ble_local"):
        log = FrameLog(str(bad), "A")
        log.record("tx", wire=b"\x01")
        log.record("tx", wire=b"\x02")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "disabled" in warnings[0].getMessage()
    assert "does not exist" in warnings[0].getMessage()
    assert not bad.parent.exists()


@posix_only
def test_unwritable_directory_disables(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(framelog.os, "access", lambda *_a, **_k: False)
    with caplog.at_level(logging.WARNING, logger="govee_ble_local"):
        log = FrameLog(str(tmp_path / "c.jsonl"), "A")
        log.record("tx", wire=b"\x01")
    assert [r for r in caplog.records if "not writable" in r.getMessage()]
    assert not (tmp_path / "c.jsonl").exists()


def test_writer_survives_non_oserror_and_warns_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = tmp_path / "good.jsonl"
    boom = str(tmp_path / "boom.jsonl")
    real_open = os.open

    def fake_open(path: str, *a: object, **k: object) -> int:
        if path == boom:
            raise ValueError("kaboom")
        return real_open(path, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(framelog.os, "open", fake_open)
    with caplog.at_level(logging.DEBUG, logger="govee_ble_local"):
        framelog._enqueue((boom, "x\n"))
        _drain()
        framelog._enqueue((boom, "y\n"))
        _drain()
        FrameLog(str(good), "G").record("tx", wire=b"\x01")
        _drain()
    fails = [r for r in caplog.records if "write failed" in r.getMessage()]
    assert [r.levelno for r in fails] == [logging.WARNING, logging.DEBUG]
    assert len(_lines(good)) == 1
    assert framelog._writer is not None and framelog._writer.is_alive()


def test_full_queue_drops_oldest_and_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    q: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=3)
    monkeypatch.setattr(framelog, "_QUEUE", q)
    with caplog.at_level(logging.WARNING, logger="govee_ble_local"):
        for i in range(6):
            framelog._enqueue(("p", f"{i}\n"))
    assert [q.get_nowait()[1] for _ in range(3)] == ["3\n", "4\n", "5\n"]
    drops = [r for r in caplog.records if "dropping" in r.getMessage()]
    assert len(drops) == 1


def test_per_path_order_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    la, lb = FrameLog(str(a), "A"), FrameLog(str(b), "B")
    # Fill the queue while the writer is blocked so it sees one mixed batch.
    gate_path = tmp_path / "gate.jsonl"
    import threading

    gate = threading.Event()
    real = framelog._write_batch

    def slow(path: str, lines: list[str]) -> None:
        if path == str(gate_path):
            gate.wait(5)
        real(path, lines)

    monkeypatch.setattr(framelog, "_write_batch", slow)
    framelog._enqueue((str(gate_path), "g\n"))
    time.sleep(0.05)  # bounded: writer picks up the gate record and blocks
    la.record("tx", wire=b"\x01")
    lb.record("tx", wire=b"\x01")
    lb.record("tx", wire=b"\x02")
    la.record("tx", wire=b"\x02")
    lb.record("tx", wire=b"\x03")
    gate.set()
    _drain()
    assert [json.loads(x)["wire"] for x in _lines(a)] == ["01", "02"]
    assert [json.loads(x)["wire"] for x in _lines(b)] == ["01", "02", "03"]


def test_exit_drain_returns_when_writer_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    class Dead:
        def is_alive(self) -> bool:
            return False

    q: queue.Queue[tuple[str, str]] = queue.Queue()
    q.put(("p", "x\n"))
    monkeypatch.setattr(framelog, "_QUEUE", q)
    monkeypatch.setattr(framelog, "_writer", Dead())
    framelog._drain_at_exit()  # must return promptly instead of waiting the full timeout


def test_exit_drain_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    class Alive:
        def is_alive(self) -> bool:
            return True

    q: queue.Queue[tuple[str, str]] = queue.Queue()
    q.put(("p", "x\n"))
    monkeypatch.setattr(framelog, "_QUEUE", q)
    monkeypatch.setattr(framelog, "_writer", Alive())
    monkeypatch.setattr(framelog, "EXIT_DRAIN_TIMEOUT", 0.05)
    framelog._drain_at_exit()  # stuck queue: gives up after the timeout


def test_all_frames_flushed_at_process_exit(tmp_path: Path) -> None:
    path = tmp_path / "exit.jsonl"
    n = 2000
    code = (
        "from govee_ble_local.transport.framelog import FrameLog\n"
        f"log = FrameLog({str(path)!r}, 'AA')\n"
        f"for i in range({n}):\n"
        "    log.record('tx', wire=i.to_bytes(2, 'big'))\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)  # noqa: S603 - fixed argv, sys.executable
    lines = _lines(path)
    assert [json.loads(x)["wire"] for x in lines] == [i.to_bytes(2, "big").hex() for i in range(n)]
