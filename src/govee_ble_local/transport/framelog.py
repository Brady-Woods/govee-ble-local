"""Optional bidirectional BLE frame capture for :class:`GoveeConnection`.

Enabled per-connection (``frame_log=<path>``) or globally via the ``GOVEE_FRAME_LOG``
environment variable. Every TX/RX frame is appended as one JSON line:

    {"ts", "addr", "dir": "tx"|"rx", "plain": <hex|null>, "wire": <hex>, "enc": <mode>}

- ``plain`` is the 20-byte *application* frame (what to analyse against the Kaitai
  spec); ``wire`` is the on-air bytes (ciphertext when the link is encrypted).
- Only frame bytes are logged — never keys, the session key, or the PSK.
- ``record`` is called from the BLE notify callback on the event loop, so it only
  enqueues; a daemon thread does the (blocking) file I/O. A write error never
  propagates (must not break the BLE pump).

Analyse a capture with ``tools/analyze_frame_log.py``.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from typing import Final

_LOGGER = logging.getLogger(__name__)

ENV_VAR: Final = "GOVEE_FRAME_LOG"

_QUEUE: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
_WRITER_LOCK = threading.Lock()
_writer: threading.Thread | None = None


def _write_forever() -> None:
    while True:
        path, line = _QUEUE.get()
        try:
            with open(path, "a", encoding="ascii") as fh:
                fh.write(line)
                # Opportunistically batch whatever else is already queued for this path.
                while True:
                    try:
                        nxt_path, nxt_line = _QUEUE.get_nowait()
                    except queue.Empty:
                        break
                    if nxt_path != path:
                        _QUEUE.put((nxt_path, nxt_line))
                        break
                    fh.write(nxt_line)
        except OSError as err:  # pragma: no cover - logging must never break I/O
            _LOGGER.debug("frame-log write failed (%s): %s", path, err)


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
        _ensure_writer()

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
        rec = {
            "ts": round(time.time(), 6),
            "addr": self._address,
            "dir": direction,
            "plain": plain.hex() if plain is not None else None,
            "wire": wire.hex(),
            "enc": enc,
        }
        _QUEUE.put((self._path, json.dumps(rec) + "\n"))
