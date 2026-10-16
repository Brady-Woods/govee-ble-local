"""Hardening tests for transport.framelog (SEC-2, SEC-3, OPS-1, exit flush)."""
from __future__ import annotations

import json
import logging
import os
import queue
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from govee_ble_local.transport import framelog
from govee_ble_local.transport.framelog import FrameLog

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")

_SRC = Path(__file__).resolve().parent.parent / "src"


def _drain(timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while framelog._QUEUE.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.005)
    assert framelog._QUEUE.unfinished_tasks == 0


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="ascii").splitlines()


def _run_child(code: str) -> None:
    """Run `code` in a fresh interpreter that imports this checkout (conftest's sys.path
    change only applies to the pytest process)."""
    pythonpath = os.pathsep.join(filter(None, [str(_SRC), os.environ.get("PYTHONPATH")]))
    env = {**os.environ, "PYTHONPATH": pythonpath}
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60, env=env)  # noqa: S603 - fixed argv


def _writer_threads() -> int:
    return sum(t.name == "govee-framelog" for t in threading.enumerate())


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    _drain()  # nothing queued by an earlier test may run under this test's patches
    framelog._failed_paths.clear()
    framelog._mode_warned.clear()
    framelog._drop_warned = False
    before = _writer_threads()
    yield
    assert _writer_threads() <= max(before, 1)  # no extra writer left consuming the shared queue


@posix_only
def test_new_file_is_owner_only(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "c.jsonl"
    with caplog.at_level(logging.WARNING, logger="govee_ble_local"):
        FrameLog(str(path), "A").record("tx", wire=b"\x01")
        _drain()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert not [r for r in caplog.records if r.name.startswith("govee_ble_local")]  # not flagged


@posix_only
def test_existing_file_mode_kept(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text("")
    os.chmod(path, 0o644)
    FrameLog(str(path), "A").record("tx", wire=b"\x01")
    _drain()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644
    assert len(_lines(path)) == 1


@posix_only
@pytest.mark.parametrize("mode", [0o640, 0o604, 0o660])
def test_group_or_world_accessible_file_warns_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, mode: int
) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text("")
    os.chmod(path, mode)
    with caplog.at_level(logging.WARNING, logger="govee_ble_local"):
        log = FrameLog(str(path), "A")
        log.record("tx", wire=b"\x01")
        _drain()
        log.record("tx", wire=b"\x02")
        _drain()
    warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1 and "group/world-accessible" in warns[0] and f"{mode:o}" in warns[0]
    assert stat.S_IMODE(os.stat(path).st_mode) == mode  # warned, not changed
    assert len(_lines(path)) == 2


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
        _drain()  # the check runs on the writer thread
        log.record("tx", wire=b"\x02")
        _drain()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "disabled" in warnings[0].getMessage()
    assert "does not exist" in warnings[0].getMessage()
    assert log._enabled is False
    assert not bad.parent.exists()


@posix_only
def test_unwritable_directory_disables(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "access", lambda *_a, **_k: False)
    with caplog.at_level(logging.WARNING, logger="govee_ble_local"):
        log = FrameLog(str(tmp_path / "c.jsonl"), "A")
        log.record("tx", wire=b"\x01")
        _drain()
    assert [r for r in caplog.records if "not writable" in r.getMessage()]
    assert not (tmp_path / "c.jsonl").exists()


def test_failing_directory_check_does_not_kill_the_writer(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_path: str) -> bool:
        raise FileNotFoundError("cwd deleted")

    monkeypatch.setattr(FrameLog, "_validate_dir", staticmethod(boom))
    with caplog.at_level(logging.WARNING, logger="govee_ble_local"):
        log = FrameLog(str(tmp_path / "c.jsonl"), "A")
        _drain()
    assert log._enabled is False
    assert [r for r in caplog.records if "cannot check" in r.getMessage()]
    assert framelog._writer is not None and framelog._writer.is_alive()


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

    monkeypatch.setattr(os, "open", fake_open)
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


def test_write_failure_warns_again_after_recovery(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = str(tmp_path / "flaky.jsonl")
    broken = True
    real_open = os.open

    def flaky_open(p: str, *a: object, **k: object) -> int:
        if p == path and broken:
            raise OSError("disk full")
        return real_open(p, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", flaky_open)
    with caplog.at_level(logging.WARNING, logger="govee_ble_local"):
        for state in (True, True, False, True):
            broken = state
            framelog._write_batch(path, ["x\n"])
    fails = [r for r in caplog.records if "write failed" in r.getMessage()]
    assert len(fails) == 2  # the first failure, then the first one after a successful write
    assert _lines(Path(path)) == ["x"]


def test_full_queue_drops_oldest_and_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    q: queue.Queue[framelog._Item] = queue.Queue(maxsize=3)
    monkeypatch.setattr(framelog, "_QUEUE", q)
    with caplog.at_level(logging.WARNING, logger="govee_ble_local"):
        for i in range(6):
            framelog._enqueue(("p", f"{i}\n"))
    assert [q.get_nowait()[1] for _ in range(3)] == ["3\n", "4\n", "5\n"]
    drops = [r for r in caplog.records if "dropping" in r.getMessage()]
    assert len(drops) == 1


def test_drop_warning_rearms_once_the_queue_drains(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    q: queue.Queue[framelog._Item] = queue.Queue(maxsize=2)
    monkeypatch.setattr(framelog, "_QUEUE", q)
    with caplog.at_level(logging.WARNING, logger="govee_ble_local"):
        for i in range(4):
            framelog._enqueue(("p", f"{i}\n"))
        framelog._enqueue(("p", "still full\n"))  # same overflow: no new warning
        while not q.empty():  # the writer catches up
            q.get_nowait()
            q.task_done()
        for i in range(4):
            framelog._enqueue(("p", f"{i}\n"))
    drops = [r for r in caplog.records if "dropping" in r.getMessage()]
    assert len(drops) == 2


def test_per_path_order_preserved(tmp_path: Path) -> None:
    # One mixed batch, as the writer sees it after a burst: lines are grouped by path and keep
    # their arrival order (run directly, so the result can't depend on thread scheduling).
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    batch: list[framelog._Item] = [
        (str(a), "a1\n"), (str(b), "b1\n"), (str(b), "b2\n"), (str(a), "a2\n"), (str(b), "b3\n"),
    ]
    framelog._process_batch(batch)
    assert _lines(a) == ["a1", "a2"]
    assert _lines(b) == ["b1", "b2", "b3"]


def test_lines_queued_before_a_failed_check_are_dropped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A capture's check precedes its lines in the queue, so lines recorded before the writer
    # ran the check (record() still saw the capture enabled) are dropped, not written: no write
    # is attempted for them. The drop is per capture (see test_fix_wp1b_framelog_shared_path),
    # so a bare (path, line) item, which names no capture, is still attempted.
    bad, good = tmp_path / "nope" / "x.jsonl", tmp_path / "ok.jsonl"
    items: list[framelog._Item] = []
    monkeypatch.setattr(framelog, "_enqueue", items.append)  # held back: processed in one batch below
    lb, lg = FrameLog(str(bad), "B"), FrameLog(str(good), "G")
    for log, wire in ((lb, b"\x01"), (lg, b"\x02")):
        log.record("tx", wire=wire)
    with caplog.at_level(logging.WARNING, logger="govee_ble_local"):
        framelog._process_batch(items)
        assert not [r for r in caplog.records if "write failed" in r.getMessage()]
        framelog._process_batch([(str(bad), "late\n")])
    assert (lb._enabled, lg._enabled) == (False, True)
    assert [json.loads(x)["wire"] for x in _lines(good)] == ["02"]
    assert [r for r in caplog.records if "write failed" in r.getMessage()]
    assert not bad.parent.exists()


class _FakeClock:
    """Stands in for framelog's ``time``: sleeping advances the clock instantly."""

    def __init__(self, on_sleep: Callable[[], None] | None = None) -> None:
        self.now = 0.0
        self.sleeps = 0
        self._on_sleep = on_sleep
        self.time = time.time

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        assert self.sleeps < 100_000, "exit drain is not bounded"
        self.now += seconds
        if self._on_sleep is not None:
            self._on_sleep()


class _Thread(SimpleNamespace):
    def is_alive(self) -> bool:
        return bool(self.alive)


def _one_pending(monkeypatch: pytest.MonkeyPatch, *, alive: bool) -> queue.Queue[framelog._Item]:
    q: queue.Queue[framelog._Item] = queue.Queue()
    q.put(("p", "x\n"))
    monkeypatch.setattr(framelog, "_QUEUE", q)
    monkeypatch.setattr(framelog, "_writer", _Thread(alive=alive))
    return q


def test_exit_drain_returns_when_writer_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    q = _one_pending(monkeypatch, alive=False)
    clock = _FakeClock()
    monkeypatch.setattr(framelog, "time", clock)
    framelog._drain_at_exit()
    assert clock.sleeps == 0  # no polling for a writer that can never drain the queue
    assert q.unfinished_tasks == 1


def test_exit_drain_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    q = _one_pending(monkeypatch, alive=True)
    clock = _FakeClock()
    monkeypatch.setattr(framelog, "time", clock)
    framelog._drain_at_exit()  # stuck queue: gives up after the timeout
    assert framelog.EXIT_DRAIN_TIMEOUT <= clock.now < framelog.EXIT_DRAIN_TIMEOUT + 0.1
    assert q.unfinished_tasks == 1


def test_exit_drain_returns_once_flushed(monkeypatch: pytest.MonkeyPatch) -> None:
    q = _one_pending(monkeypatch, alive=True)

    def writer_finishes() -> None:
        q.get_nowait()
        q.task_done()

    clock = _FakeClock(on_sleep=writer_finishes)
    monkeypatch.setattr(framelog, "time", clock)
    framelog._drain_at_exit()
    assert clock.sleeps == 1 and q.unfinished_tasks == 0


def test_all_frames_flushed_at_process_exit(tmp_path: Path) -> None:
    path = tmp_path / "exit.jsonl"
    n = 2000
    _run_child(
        "from govee_ble_local.transport.framelog import FrameLog\n"
        f"log = FrameLog({str(path)!r}, 'AA')\n"
        f"for i in range({n}):\n"
        "    log.record('tx', wire=i.to_bytes(2, 'big'))\n"
    )
    lines = _lines(path)
    assert [json.loads(x)["wire"] for x in lines] == [i.to_bytes(2, "big").hex() for i in range(n)]


def test_exit_hook_registered_on_first_capture_not_at_import(tmp_path: Path) -> None:
    _run_child(
        "import atexit\n"
        "names = []\n"
        "real = atexit.register\n"
        "def spy(fn, *a, **k):\n"
        "    names.append(getattr(fn, '__qualname__', ''))\n"
        "    return real(fn, *a, **k)\n"
        "atexit.register = spy\n"
        "import govee_ble_local\n"
        "from govee_ble_local.transport.framelog import FrameLog\n"
        "assert '_drain_at_exit' not in names, names\n"
        f"FrameLog({str(tmp_path / 'a.jsonl')!r}, 'A')\n"
        f"FrameLog({str(tmp_path / 'b.jsonl')!r}, 'B')\n"
        "assert names.count('_drain_at_exit') == 1, names\n"
    )


def test_record_revives_dead_writer_end_to_end(tmp_path: Path) -> None:
    # A real writer restart, in a child process so the extra thread can't outlive the test.
    path = tmp_path / "r.jsonl"
    _run_child(
        "from govee_ble_local.transport import framelog\n"
        f"log = framelog.FrameLog({str(path)!r}, 'A')\n"
        "class Dead:\n"
        "    def is_alive(self):\n"
        "        return False\n"
        "framelog._writer = Dead()\n"
        "log.record('tx', wire=b'\\x01')\n"
        "assert framelog._writer.is_alive()\n"
    )
    assert [json.loads(x)["wire"] for x in _lines(path)] == ["01"]
