"""Optional bidirectional BLE frame capture for :class:`GoveeConnection`.

Enabled per-connection (``frame_log=<path>``) or globally via the ``GOVEE_FRAME_LOG``
environment variable. Every TX/RX frame is appended as one JSON line:

    {"ts", "addr", "dir": "tx"|"rx", "plain": <hex|null>, "wire": <hex>, "enc": <mode>}

- ``plain`` is the 20-byte *application* frame (what to analyse against the Kaitai
  spec); ``wire`` is the on-air bytes (ciphertext when the link is encrypted).
- Only frame bytes are logged — never keys, the session key, or the PSK.
- A write error never propagates (must not break the BLE pump).

Analyse a capture with ``tools/analyze_frame_log.py``.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Final

_LOGGER = logging.getLogger(__name__)

ENV_VAR: Final = "GOVEE_FRAME_LOG"


class FrameLog:
    """Append-only JSONL frame sink."""

    def __init__(self, path: str, address: str) -> None:
        self._path = path
        self._address = address

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
        try:
            with open(self._path, "a", encoding="ascii") as fh:
                fh.write(json.dumps(rec) + "\n")
        except OSError as err:  # pragma: no cover - logging must never break I/O
            _LOGGER.debug("frame-log write failed (%s): %s", self._path, err)
