"""Generic RGBWW light devices (power + brightness + RGB + color-temp).

Covers the older single-zone bulbs/lamps. Segment/scene-rich strips
(H61A8 etc.) get their own classes; these are the plain lights.
"""
from __future__ import annotations

from typing import ClassVar

from ..ble.controllers import ColorScheme
from ..models import Capability, Encryption, Zone
from .base import (
    BarSwitchControl,
    BrightnessMixin,
    ColorTempMixin,
    GoveeDevice,
    PolledLight,
    PowerMixin,
    RGBMixin,
    SceneControl,
    SegmentControl,
    StatusReadable,
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


class GoveeLightH60A6(GoveeRgbLight, SegmentControl, ZoneControl, StatusReadable):
    """H60A6 — "Ceiling Light Pro". AES-RC4-PSK, h60a6 (SubModeColorV2 0x15)
    color scheme. Two physical zones (upper ring, lower panel) + 13
    individually-addressable segments.

    - Whole-device: power / brightness / RGB / color-temp 2700-6500K (verified).
    - Segments: set_segment_rgb / set_segment_brightness via the 0x15 mask
      (mask confirmed live to address individual segments).
    - Zones: set_zone_power("main"|"background", on) via the verified 33 30
      command (panel=0, ring=1). set_zone_rgb() colors a zone's segments.

    Segment count = 13, verified against SubModeColorV1.isAllChoose()
    (Arrays.copyOf(checkSet, 13)) and ColorModeSegmentView's 13-element list.
    ALL 13 segments are the addressable ring/main light.

    The H60A6 has two INDEPENDENT light elements, each toggled via 33 30
    (VM4LightH60A6.specialDealSnapshot uses toggle indices 0 and 1). Cloud names
    them mainLightToggle / backgroundLightToggle. Verified live: the MAIN light
    is toggle index 0, and the BACKGROUND (the addressable RGBIC ring) is index
    1. RGB scenes and segment colour drive the ring (background); the main light
    is a plain element with power only, so "main" has no segment mapping
    (set_zone_rgb refuses) rather than colouring a ring segment."""

    skus: ClassVar[tuple[str, ...]] = ("H60A6",)
    _encryption: ClassVar[Encryption] = Encryption.AES_RC4_PSK
    _color_scheme: ClassVar[ColorScheme] = "h60a6"
    _segments: ClassVar[int] = 13
    capabilities: ClassVar[frozenset[Capability]] = _LIGHT_CAPS | {Capability.SEGMENTS}
    zones: ClassVar[tuple[Zone, ...]] = (
        Zone("main", power_index=0, segments=()),                        # primary light: power only
        Zone("background", power_index=1, segments=tuple(range(0, 13))), # RGBIC ring (all 13 segments)
    )


class GoveeLightH6006(GoveeRgbLight, PolledLight):
    """H6006 — plaintext (no handshake), h6006 color scheme. (Confirmed.)"""

    skus: ClassVar[tuple[str, ...]] = ("H6006",)
    _encryption: ClassVar[Encryption] = Encryption.NONE
    _color_scheme: ClassVar[ColorScheme] = "h6006"
    min_kelvin: ClassVar[int] = 2000  # per Govee API
    max_kelvin: ClassVar[int] = 9000


class GoveeLightH6052(GoveeRgbLight, PolledLight):
    """H6052 — plaintext (no handshake), h6006 color scheme, wide CT range."""

    skus: ClassVar[tuple[str, ...]] = ("H6052",)
    _encryption: ClassVar[Encryption] = Encryption.NONE
    _color_scheme: ClassVar[ColorScheme] = "h6006"
    min_kelvin: ClassVar[int] = 2000
    max_kelvin: ClassVar[int] = 9000


class GoveeLightH6008(GoveeRgbLight, PolledLight):
    """H6008 — legacy bulb handled by the bulblightv3 module (verified against
    the split). Its SubModeColor writes 0x0d with layout
    [0x0d, r,g,b, kelvin_hi, kelvin_lo, tint_r,tint_g,tint_b] — byte-identical
    to our h6006 scheme (tint 0,0,0 for non-table kelvins). Plaintext (no
    crypto in bulblightv3/ble); encryption still resolved from the advert at
    runtime, NONE is the fallback. Not segmented.

    Color-temp range is bulblightv3's own Support.getColorTemRange() = 2700-6500
    (this module does NOT use the dynamic KelvinConfig the tablelampv1 bulbs do,
    so the value is fixed, not the 2000-9000 cloud-API figure)."""

    skus: ClassVar[tuple[str, ...]] = ("H6008",)
    _encryption: ClassVar[Encryption] = Encryption.NONE
    _color_scheme: ClassVar[ColorScheme] = "h6006"
    min_kelvin: ClassVar[int] = 2700
    max_kelvin: ClassVar[int] = 6500


class GoveeLightH6047(GoveeRgbLight, SegmentControl, BarSwitchControl, PolledLight):
    """H6047 — h60a6 (SubModeColor sub-cmd 0x15) color scheme, like H60A6,
    with per-segment control (segmentedColorRgb).

    Corrected against the h6047 split (com.govee.h6047.ble):
    - 10 segments (Support.getGoodsType4ColorSegment -> 10 for H6047; the
      Govee cloud API's "15" was wrong for the BLE layout).
    - Plaintext channel: the h6047 BLE module has no encryption/handshake.
      Encryption is still resolved from the advertisement at runtime; NONE is
      the accurate fallback.
    - Two light bars (left/right) with independent on/off, exposed as zones.
      Unlike the H60A6's per-zone 33 30, the H6047 uses one combined frame
      33 36 <left> <right> (NewDetailVm.I5 -> value_compose_light_switch 0x36);
      BarSwitchControl re-sends both bar states on each toggle. Left = bar 0,
      right = bar 1 (5 segments each, per Support.getGoodsType4ColorSegment=10).
    Color-temp range 2200-6500K (Support.getColorTemRange). The device also
    exposes Music mode (sub-cmd 0x13) and legacy DIY, deliberately not wired."""

    skus: ClassVar[tuple[str, ...]] = ("H6047",)
    _encryption: ClassVar[Encryption] = Encryption.NONE
    _color_scheme: ClassVar[ColorScheme] = "h60a6"
    _segments: ClassVar[int] = 10
    min_kelvin: ClassVar[int] = 2200
    max_kelvin: ClassVar[int] = 6500
    capabilities: ClassVar[frozenset[Capability]] = _LIGHT_CAPS | {Capability.SEGMENTS}
    zones: ClassVar[tuple[Zone, ...]] = (
        Zone("left", power_index=0, segments=tuple(range(0, 5))),
        Zone("right", power_index=1, segments=tuple(range(5, 10))),
    )


class GoveeStripH61A8(PowerMixin, BrightnessMixin, RGBMixin, SegmentControl, SceneControl, PolledLight, GoveeDevice):
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
