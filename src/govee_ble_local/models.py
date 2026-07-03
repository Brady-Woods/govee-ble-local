"""Data models returned by the Govee BLE client."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GoveeBleSegment:
    """One individually-addressable segment's current state.

    `index` is both the record position in the status response and the bit
    position in the segment-color/brightness bitmask (confirmed identical via
    live testing across the full 0-11 range).
    """

    index: int
    brightness_pct: int
    r: int
    g: int
    b: int


@dataclass
class GoveeBleStatus:
    """Snapshot of device state parsed from a status query.

    Fields are `None` when the corresponding bytes weren't present in the
    response (e.g. `segments` is only populated by the fuller status query).
    """

    zone_upper_on: bool | None = None
    zone_lower_on: bool | None = None
    brightness_pct: int | None = None
    scene_id: tuple[int, int] | None = None
    hardware_version: str | None = None
    ble_mac: str | None = None
    wifi_mac: str | None = None
    segments: list[GoveeBleSegment] | None = None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Current solid RGB, derived from the per-segment data.

        There's no dedicated "current color" field in the status response, but
        a solid color sets every segment to it — so when all segments share one
        color we can read it back. Requires the fuller query
        (get_status(with_segments=True)); returns None if segments weren't read
        or the segments differ (e.g. a multi-color scene). Color temperature
        shows up here as its rendered RGB tint (there is no Kelvin read-back).
        """
        if not self.segments:
            return None
        colors = {(s.r, s.g, s.b) for s in self.segments}
        return next(iter(colors)) if len(colors) == 1 else None
