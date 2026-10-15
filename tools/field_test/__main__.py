"""``python tools/field_test`` (or ``python -m field_test`` from ``tools/``)."""
from __future__ import annotations

import sys
from pathlib import Path

if not __package__:   # run as `python tools/field_test`: make `field_test` importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from field_test.runner import main

if __name__ == "__main__":
    sys.exit(main())
