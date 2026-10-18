"""``python tools/field_test`` (or ``python -m field_test`` from ``tools/``)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:   # what a type checker sees: this module is the field_test package's __main__
    from .runner import main
else:
    if not __package__:   # run as `python tools/field_test`: make `field_test` importable
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from field_test.runner import main

if __name__ == "__main__":
    sys.exit(main())
