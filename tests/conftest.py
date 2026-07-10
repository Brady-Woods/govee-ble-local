"""Make ``govee_ble_local`` (src layout) — including the shipped Kaitai readers
under ``govee_ble_local._generated`` — and the spec builders (``tests/spec_frames``)
importable whether or not the package is installed editable."""
from __future__ import annotations

import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"

for _p in (str(_SRC), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
