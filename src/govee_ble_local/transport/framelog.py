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
  (0o600); an existing file keeps its mode.
- ``record`` is called from the BLE notify callback on the event loop, so it only
  enqueues; a daemon thread does the (blocking) file I/O. A write error never
  propagates (must not break the BLE pump). The queue is bounded (oldest records are
  dropped when full) and drained, bounded in time, at interpreter exit.

Analyse a capture with ``tools/analyze_frame_log.py``.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import threading
import time
from typing import Final

_LOGGER = logging.getLogger(__name__)

ENV_VAR: Final = "GOVEE_FRAME_LOG"

QUEUE_MAX: Final = 10000
EXIT_DRAIN_TIMEOUT: Final = 2.0

_QUEUE: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=QUEUE_MAX)
_WRITER_LOCK = threading.Lock()
_writer: threading.Thread | None = None
_failed_paths: set[str] = set()
_drop_warned = False


def _write_batch(path: str, lines: list[str]) -> None:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="ascii") as fh:
            fh.write("".join(lines))
    except Exception as err:  # logging must never break I/O or kill the writer
        if path not in _failed_paths:
            _failed_paths.add(path)
            _LOGGER.warning("frame-log write failed (%s): %s", path, err)
        else:
            _LOGGER.debug("frame-log write failed (%s): %s", path, err)


def _write_forever() -> None:
    while True:
        batch = [_QUEUE.get()]
        while True:
            try:
                batch.append(_QUEUE.get_nowait())
            except queue.Empty:
                break
        # Group by path; dict keeps first-seen order and each list keeps arrival order.
        grouped: dict[str, list[str]] = {}
        for path, line in batch:
            grouped.setdefault(path, []).append(line)
        try:
            for path, lines in grouped.items():
                _write_batch(path, lines)
        finally:
            for _ in batch:
                _QUEUE.task_done()


def _enqueue(item: tuple[str, str]) -> None:
    """Non-blocking put; when full, drop the oldest record."""
    global _drop_warned
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


def _drain_at_exit() -> None:
    """Give the writer a bounded time to flush queued records before exit."""
    deadline = time.monotonic() + EXIT_DRAIN_TIMEOUT
    while _QUEUE.unfinished_tasks and time.monotonic() < deadline:
        if _writer is None or not _writer.is_alive():
            return
        time.sleep(0.005)


atexit.register(_drain_at_exit)


def _ensure_writer() -> None:
    global _writer
    with _WRITER_LOCK:
        if _writer is None or not _writer.is_alive():
            _writer = threading.Thread(target=_write_forever, name="govee-framelog", daemon=True)
            _writer.start()


class FrameLog:
    """Append-only JSONL frame sink (asynchronous file writes)."""

    def __init__(self, path: str, address: str) -> None:
        self._path = path
        self._address = address
        self._enabled = self._validate_dir(path)
        if self._enabled:
            _ensure_writer()

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

    @classmethod
    def resolve(cls, explicit: str | None, address: str) -> "FrameLog | None":
        """Return a FrameLog if `explicit` or $GOVEE_FRAME_LOG is set, else None."""
        path = explicit or os.environ.get(ENV_VAR)
        return cls(path, address) if path else None

    def record(
        self,
        direction: str,
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
        _enqueue((self._path, json.dumps(rec) + "\n"))
