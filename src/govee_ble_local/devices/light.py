"""Generic RGBWW light devices (power + brightness + RGB + color-temp).

Covers the older single-zone bulbs/lamps. Segment/scene-rich strips
(H61A8 etc.) get their own classes; these are the plain lights.
"""
from __future__ import annotations

from typing import ClassVar

from ..ble.controllers import ColorScheme
from ..models import Capability, Encryption, Zone
from .base import (
    BrightnessMixin,
    ColorTempMixin,
    GoveeDevice,
    PowerMixin,
    RGBMixin,
    SceneControl,
    SegmentControl,
    ZoneControl,
)

_LIGHT_CAPS = frozenset(
    {Capability.POWER, Capability.BRIGHTNESS, Capability.RGB,
     Capability.COLOR_TEMP, Capability.SCENES}
)


class GoveeRgbLight(PowerMixin, BrightnessMixin, RGBMixin, ColorTempMixin, SceneControl, GoveeDevice):
    """Base for single-zone RGBWW lights: on/off, brightness, RGB, color-temp,
    built-in scene activation."""

    capabilities: ClassVar[frozenset[Capability]] = _LIGHT_CAPS
    min_kelvin: ClassVar[int] = 2700
    max_kelvin: ClassVar[int] = 6500


class GoveeLightH60A6(GoveeRgbLight, SegmentControl, ZoneControl):
    """H60A6 — "Ceiling Light Pro". AES-RC4-PSK, h60a6 (SubModeColorV2 0x15)
    color scheme. Two physical zones (upper ring, lower panel) + 12
    individually-addressable segments.

    - Whole-device: power / brightness / RGB / color-temp (verified).
    - Segments: set_segment_rgb / set_segment_brightness via the 0x15 mask
      (mask confirmed live to address individual segments).
    - Zones: set_zone_power("ring"|"panel", on) via the verified 33 30 command
      (panel=0, ring=1). set_zone_rgb() colors a zone's segments.

    NOTE: the segment->zone split below (ring=0-10, panel=11) is a best-guess
    default - the app protocol does not encode which segment bits belong to
    which zone (confirmed absent from the source). It can be overridden per
    install; zone *power* (33 30) is exact regardless."""

    skus: ClassVar[tuple[str, ...]] = ("H60A6",)
    _encryption: ClassVar[Encryption] = Encryption.AES_RC4_PSK
    _color_scheme: ClassVar[ColorScheme] = "h60a6"
    _segments: ClassVar[int] = 12
    capabilities: ClassVar[frozenset[Capability]] = _LIGHT_CAPS | {Capability.SEGMENTS}
    # Cloud names these mainLightToggle / backgroundLightToggle.
    zones: ClassVar[tuple[Zone, ...]] = (
        Zone("main", power_index=1, segments=tuple(range(0, 11))),   # ring
        Zone("background", power_index=0, segments=(11,)),           # lower panel
    )


class GoveeLightH6006(GoveeRgbLight):
    """H6006 — plaintext (no handshake), h6006 color scheme. (Confirmed.)"""

    skus: ClassVar[tuple[str, ...]] = ("H6006",)
    _encryption: ClassVar[Encryption] = Encryption.NONE
    _color_scheme: ClassVar[ColorScheme] = "h6006"
    min_kelvin: ClassVar[int] = 2000  # per Govee API
    max_kelvin: ClassVar[int] = 9000


class GoveeLightH6052(GoveeRgbLight):
    """H6052 — plaintext (no handshake), h6006 color scheme, wide CT range."""

    skus: ClassVar[tuple[str, ...]] = ("H6052",)
    _encryption: ClassVar[Encryption] = Encryption.NONE
    _color_scheme: ClassVar[ColorScheme] = "h6006"
    min_kelvin: ClassVar[int] = 2000
    max_kelvin: ClassVar[int] = 9000


class GoveeLightH6008(GoveeRgbLight):
    """H6008 — h6006 color scheme. Encryption is discovered from the
    advertisement at runtime; NONE is only the address-only fallback."""

    skus: ClassVar[tuple[str, ...]] = ("H6008",)
    _encryption: ClassVar[Encryption] = Encryption.NONE
    _color_scheme: ClassVar[ColorScheme] = "h6006"
    min_kelvin: ClassVar[int] = 2000  # per Govee API
    max_kelvin: ClassVar[int] = 9000


class GoveeLightH6047(GoveeRgbLight, SegmentControl):
    """H6047 — h60a6 (SubModeColorV2 0x15) color scheme, like H60A6, with
    per-segment control (segmentedColorRgb; 15 segments per the Govee API)."""

    skus: ClassVar[tuple[str, ...]] = ("H6047",)
    _encryption: ClassVar[Encryption] = Encryption.AES_RC4_PSK
    _color_scheme: ClassVar[ColorScheme] = "h60a6"
    _segments: ClassVar[int] = 15
    min_kelvin: ClassVar[int] = 2200
    max_kelvin: ClassVar[int] = 6500
    capabilities: ClassVar[frozenset[Capability]] = _LIGHT_CAPS | {Capability.SEGMENTS}


class GoveeStripH61A8(PowerMixin, BrightnessMixin, RGBMixin, SegmentControl, SceneControl, GoveeDevice):
    """H61A8 — segmented LED rope (dreamcolorlightv1). Plaintext channel
    (advertisement encrypt flag clear), 0x0b color mode with a per-segment
    bitmask, no color-temperature. RGB applies to all segments; use
    set_segment_rgb() for individual segments; set_scene() for built-in scenes."""

    skus: ClassVar[tuple[str, ...]] = ("H61A8",)
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.POWER, Capability.BRIGHTNESS, Capability.RGB,
         Capability.SEGMENTS, Capability.SCENES}
    )
    _encryption: ClassVar[Encryption] = Encryption.NONE
    _color_scheme: ClassVar[ColorScheme] = "h61a8"
    _segments: ClassVar[int] = 15
