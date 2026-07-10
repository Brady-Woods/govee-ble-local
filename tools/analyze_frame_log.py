#!/usr/bin/env python3
"""Thin repo shim over the packaged analyzer (``govee_ble_local.debug``).

The logic lives in the library so an installed wheel exposes it as the ``govee-ble-analyze``
console script. This wrapper just lets you run it from a checkout without installing.

    python3 tools/analyze_frame_log.py <capture.jsonl> [more.jsonl ...]
    python3 tools/analyze_frame_log.py --from-frames-log <session.log> [-o out.jsonl]
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from govee_ble_local.debug import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
