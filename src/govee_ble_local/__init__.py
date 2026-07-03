"""govee-ble-local: local Bluetooth LE control of Govee lights.

Public API:
    GoveeBleClient   - on-demand encrypted BLE session + high-level commands
    GoveeBleStatus   - parsed device state
    GoveeBleSegment  - one segment's state

Pure protocol helpers (crypto, framing, command builders, parsers) live in
`govee_ble_local.protocol` and require no Bluetooth stack, so they can be
imported and tested without `bleak` installed. `GoveeBleClient` is imported
lazily (below) so that pulling in the protocol helpers doesn't drag in bleak.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .const import (
    MAX_COLOR_TEMP_KELVIN,
    MIN_COLOR_TEMP_KELVIN,
    SEGMENT_COUNT,
    ZONE_LOWER,
    ZONE_UPPER,
)
from .models import GoveeBleSegment, GoveeBleStatus

if TYPE_CHECKING:
    from .client import GoveeBleClient

__version__ = "0.1.0"

__all__ = [
    "GoveeBleClient",
    "GoveeBleStatus",
    "GoveeBleSegment",
    "ZONE_UPPER",
    "ZONE_LOWER",
    "SEGMENT_COUNT",
    "MIN_COLOR_TEMP_KELVIN",
    "MAX_COLOR_TEMP_KELVIN",
]


def __getattr__(name: str) -> object:
    # Lazy import so `import govee_ble_local` / `from govee_ble_local import
    # protocol` doesn't require bleak; only touching GoveeBleClient does.
    if name == "GoveeBleClient":
        from .client import GoveeBleClient

        return GoveeBleClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
