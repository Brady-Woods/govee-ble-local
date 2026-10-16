"""Optional bidirectional BLE frame capture for :class:`GoveeConnection`.

Enabled per-connection (``frame_log=<path>``) or globally via the ``GOVEE_FRAME_LOG``
environment variable. Every TX/RX frame is appended as one JSON line:

    {"ts", "addr", "dir": "tx"|"rx", "plain": <hex|null>, "wire": <hex>, "enc": <mode>}

- ``plain`` is the 20-byte *application* frame (what to analyse against the Kaitai
  spec); ``wire`` is the on-air bytes (ciphertext when the link is encrypted).
- Only frame bytes are logged: no key, session key or PSK is written as such. However,
  captures include the handshake wire frames, from which the session key is derivable
  with the (public) PSK, so a capture must be treated as sensitive. Handshake bytes are
  deliberately not masked (the capture format is unchanged). Files are created owner-only
  (0o600); an existing file keeps its mode, with one warning (on POSIX) if it is group- or
  world-accessible.
- A :class:`FrameLog` is built on the event loop and ``record`` is called from the BLE
  notify callback, so neither touches the filesystem: they only enqueue, and a daemon
  thread does the (blocking) file I/O, including the check that the capture's directory
  exists and is writable (if not, that capture is disabled with one warning). A write
  error never propagates (must not break the BLE pump). The queue is bounded (oldest
  records are dropped when full) and drained, bounded in time, at interpreter exit.

Analyse a capture with ``tools/analyze_frame_log.py``.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import stat
import threading
import time
from typing import Final, Literal, TypeAlias

_LOGGER = logging.getLogger(__name__)

ENV_VAR: Final = "GOVEE_FRAME_LOG"

# A frame's direction: the JSONL ``"dir"`` field (and the frames-logger line).
Direction: TypeAlias = Literal["tx", "rx"]

QUEUE_MAX: Final = 10000
EXIT_DRAIN_TIMEOUT: Final = 2.0

# A queue item: (path, FrameLog) asks the writer to check a new capture's directory before
# writing anything for it; (path, JSONL line, FrameLog) is a line that capture recorded, dropped
# if its check failed; (path, JSONL line) is a line that names no capture, always written.
_Item: TypeAlias = "tuple[str, str | FrameLog] | tuple[str, str, FrameLog]"

_QUEUE: queue.Queue[_Item] = queue.Queue(maxsize=QUEUE_MAX)
_WRITER_LOCK = threading.Lock()
_writer: threading.Thread | None = None
_exit_hook_registered = False    # _drain_at_exit is registered on the first writer start, not at import
_failed_paths: set[str] = set()  # last write failed: warned once, then DEBUG until a write succeeds
_mode_warned: set[str] = set()   # existing capture files already warned about as group/world-accessible
_drop_warned = False             # warned about the current overflow; re-armed once the queue drains


class _NotRegularFileError(OSError):
    """The capture path is not a regular file (FIFO, device, ...): refused."""


def _write_batch(path: str, lines: list[str]) -> None:
    try:
        # O_NOFOLLOW: never write through a symlink; O_NONBLOCK: opening a FIFO must not hang.
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "a", encoding="ascii") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise _NotRegularFileError("not a regular file")
            if os.name == "posix" and st.st_mode & 0o077 and path not in _mode_warned:
                # The mode is kept (documented); only flag it, since captures are sensitive. Off
                # POSIX the bits are made up (Windows: 0o666, or 0o444 if read-only), so skip it.
                _mode_warned.add(path)
                _LOGGER.warning(
                    "frame capture %s is group/world-accessible (mode %o); captures are sensitive",
                    path, stat.S_IMODE(st.st_mode),
                )
            fh.write("".join(lines))
    except Exception as err:  # noqa: BLE001 - logging must never break I/O or kill the writer
        if path not in _failed_paths:
            _failed_paths.add(path)
            _LOGGER.warning("frame-log write failed (%s): %s", path, err)
        else:
            _LOGGER.debug("frame-log write failed (%s): %s", path, err)
    else:
        _failed_paths.discard(path)  # recovered: a later failure warns again


def _process_batch(batch: list[_Item]) -> None:
    """Run the directory checks of one batch, then write its lines grouped by path."""
    # Group by path; dict keeps first-seen order and each list keeps arrival order.
    grouped: dict[str, list[str]] = {}
    for item in batch:
        path, payload = item[0], item[1]
        if isinstance(payload, FrameLog):
            payload._check_dir()
        elif len(item) == 2 or item[2]._enabled:
            # A capture's check precedes its lines in the queue, so this drops exactly the lines
            # it recorded before a failed check; other captures sharing the path keep writing.
            grouped.setdefault(path, []).append(payload)
    for path, lines in grouped.items():
        _write_batch(path, lines)


def _write_forever() -> None:
    while True:
        batch = [_QUEUE.get()]
        while True:
            try:
                batch.append(_QUEUE.get_nowait())
            except queue.Empty:
                break
        try:
            _process_batch(batch)
        finally:
            for _ in batch:
                _QUEUE.task_done()


def _enqueue(item: _Item) -> None:
    """Non-blocking put; when full, drop the oldest record (one warning per overflow)."""
    global _drop_warned
    if _drop_warned and _QUEUE.empty():
        _drop_warned = False  # the writer caught up since the last overflow: warn on the next one
    while True:
        try:
            _QUEUE.put_nowait(item)
            return
        except queue.Full:
            try:
                _QUEUE.get_nowait()
                _QUEUE.task_done()
            except queue.Empty:  # pragma: no cover - racing with the writer
                pass
            if not _drop_warned:
                _drop_warned = True
                _LOGGER.warning("frame-log queue full (%d); dropping oldest records", QUEUE_MAX)


def flush(timeout: float = EXIT_DRAIN_TIMEOUT) -> bool:
    """Block (at most ``timeout`` seconds) until the writer thread has written every queued record.

    Returns True once the queue is drained, False on timeout or when no writer is running (nothing
    would drain it). Blocking: call it off the event loop, e.g. from a tool after the device
    stopped, before reading the capture back."""
    deadline = time.monotonic() + timeout
    while _QUEUE.unfinished_tasks:
        if _writer is None or not _writer.is_alive() or time.monotonic() >= deadline:
            return False
        time.sleep(0.005)
    return True


def _drain_at_exit() -> None:
    """Give the writer a bounded time to flush queued records before exit."""
    flush(EXIT_DRAIN_TIMEOUT)


def _ensure_writer() -> None:
    global _writer, _exit_hook_registered
    with _WRITER_LOCK:
        if _writer is None or not _writer.is_alive():
            _writer = threading.Thread(target=_write_forever, name="govee-framelog", daemon=True)
            _writer.start()
        if not _exit_hook_registered:
            # Only once capture is in use: importing the library must not add an exit hook.
            atexit.register(_drain_at_exit)
            _exit_hook_registered = True


class FrameLog:
    """Append-only JSONL frame sink (asynchronous file writes)."""

    def __init__(self, path: str, address: str) -> None:
        self._path = path
        self._address = address
        # Cleared by the writer thread if the directory check fails. The check is queued, not
        # run here: this is built on the event loop, where filesystem syscalls must not run.
        self._enabled = True
        _ensure_writer()
        _enqueue((path, self))

    @staticmethod
    def _validate_dir(path: str) -> bool:
        parent = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(parent):
            _LOGGER.warning("frame capture disabled: directory %s does not exist", parent)
            return False
        if not os.access(parent, os.W_OK | os.X_OK):
            _LOGGER.warning("frame capture disabled: directory %s is not writable", parent)
            return False
        return True

    def _check_dir(self) -> None:
        """Writer thread: disable this capture (not others on its path) if its directory is unusable."""
        try:
            ok = self._validate_dir(self._path)
        except Exception as err:  # noqa: BLE001 - e.g. a deleted cwd; must not kill the writer
            _LOGGER.warning("frame capture disabled: cannot check %s: %s", self._path, err)
            ok = False
        self._enabled = ok  # _process_batch drops the lines this capture queued before the check

    @classmethod
    def resolve(cls, explicit: str | None, address: str) -> FrameLog | None:
        """Return a FrameLog if `explicit` or $GOVEE_FRAME_LOG is set, else None."""
        path = explicit or os.environ.get(ENV_VAR)
        return cls(path, address) if path else None

    def record(
        self,
        direction: Direction,
        *,
        wire: bytes,
        plain: bytes | None = None,
        enc: str = "",
    ) -> None:
        if not self._enabled:
            return
        rec = {
            "ts": round(time.time(), 6),
            "addr": self._address,
            "dir": direction,
            "plain": plain.hex() if plain is not None else None,
            "wire": wire.hex(),
            "enc": enc,
        }
        if _writer is None or not _writer.is_alive():
            _ensure_writer()  # a dead writer must not silently swallow every later record
        _enqueue((self._path, json.dumps(rec) + "\n", self))
