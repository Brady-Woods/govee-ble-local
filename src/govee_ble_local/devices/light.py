"""Generic RGBWW light devices (power + brightness + RGB + color-temp).

Covers the older single-zone bulbs/lamps. Segment/scene-rich strips
(H61A8 etc.) get their own classes; these are the plain lights.
"""
from __future__ import annotations

import base64
from typing import Any, ClassVar

from ..ble import controllers
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
    # H60A6 bundled scenes are all sceneType 5 (73) / 0 (static, 11). Type-5/0x50 scenes
    # route via the DIY path (dialect B, commByte 0x58), NOT parseSceneV1 — implemented in
    # _scene_upload_frames below. Kept for API symmetry; the dialect-A table isn't used here.
    _scene_versions: ClassVar[frozenset[int]] = frozenset({0, 1, 2, 3, 5})
    capabilities: ClassVar[frozenset[Capability]] = _LIGHT_CAPS | {Capability.SEGMENTS}
    zones: ClassVar[tuple[Zone, ...]] = (
        Zone("main", power_index=0, segments=()),                        # primary light: power only
        Zone("background", power_index=1, segments=tuple(range(0, 13))), # RGBIC ring (all 13 segments)
    )

    def _scene_upload_frames(self, scene: Any) -> list[bytes] | None:
        """H60A6 dialect-B upload (commByte 0x58). sceneType-5 / byte0==0x50 scenes
        route to the DIY path; the value is ``decode(scenceParam)[1:]`` (drop the
        0x50 header — verified byte-exact vs ``toBytes(parse(param))``). The
        DIY-first length gate then picks the frame form::

            u16le(value[0:2]) + 2 == len(value)  -> DIY      -> 0xA3 (scene_upload_a3, strip 1)
            else                                 -> graffiti -> 0xA4-MTU (scene_upload_a4_mtu)

        (e.g. Christmas = DIY/0xA3; Aurora, Halloween B = ... per the gate.) Non-type-5
        (static) scenes fall through to activate-only via the base."""
        param = scene.param
        if scene.scene_type == 5 and param:
            value = base64.b64decode(param)[1:]
            if len(value) >= 2:
                is_diy = (value[0] | (value[1] << 8)) + 2 == len(value)
                if is_diy:
                    return controllers.scene_upload_a3(param, controllers.COMM_H60A6, strip=1)
                return controllers.scene_upload_a4_mtu(value, controllers.COMM_H60A6)
        return super()._scene_upload_frames(scene)


class GoveeLightH6006(GoveeRgbLight, PolledLight):
    """H6006 — plaintext (no handshake), h6006 color scheme. (Confirmed.)"""

    skus: ClassVar[tuple[str, ...]] = ("H6006",)
    _encryption: ClassVar[Encryption] = Encryption.NONE
    _color_scheme: ClassVar[ColorScheme] = "h6006"
    min_kelvin: ClassVar[int] = 2000  # per Govee API
    max_kelvin: ClassVar[int] = 9000
    # Bulbs UPLOAD type-1 RGB scenes: the apply path (Support.is2NewScenesMode ->
    # ScenesOp.parseScene(sceneM, {1})) hardcodes version 1 and ignores supportScenesOp
    # ({0}). So version 1 → comType 1, strip 0, 0xA3. (source Q1)
    _scene_versions: ClassVar[frozenset[int]] = frozenset({1})


class GoveeLightH6641(GoveeRgbLight, PolledLight):
    """H6641 — RGBIC light strip (goodsType 247), handled by the shared h61d3
    module (also H6640/H41E5/H41E6). Plaintext channel; h60a6 (SubModeColorV2
    sub-cmd 0x15) colour scheme — the RGB/colour-temp write layout is
    byte-identical to the H60A6/H6047 path (verified against h61d3
    SubModeColor.getWriteBytes: [0x15, 0x01, r,g,b, kelvin?, tint?, mask0..3]).
    Color-temp 2000-9000K (H61D3Support goodsType-247 range).

    Whole-device colour selects every segment via the 0x15 mask; the strip's
    segment count is dynamic (LED count / 3), so we set all 16 mask bits to cover
    the whole strip. Per-segment control isn't exposed (count isn't read back).
    Scenes activate once an H6641 catalog is bundled (empty effect list until
    then; the light otherwise works fully)."""

    skus: ClassVar[tuple[str, ...]] = ("H6641",)
    _encryption: ClassVar[Encryption] = Encryption.NONE
    _color_scheme: ClassVar[ColorScheme] = "h60a6"
    _segments: ClassVar[int] = 16
    min_kelvin: ClassVar[int] = 2000
    max_kelvin: ClassVar[int] = 9000
    # versionArray (h61d3 goodsType 247). Bundled scenes are sceneType 2 (rgbic) →
    # V2 comType 2, strip 0 → dialect-A upload. (source Q4 / handoff)
    _scene_versions: ClassVar[frozenset[int]] = frozenset({0, 1, 2, 3, 10})


class GoveeLightH6052(GoveeRgbLight, PolledLight):
    """H6052 — plaintext (no handshake), h6006 color scheme, wide CT range.

    Scenes (goodsType 22, versionArray {0,1,4,5}): type-3 graffiti is NOT uploadable
    (needs version 3, which H6052 lacks) → activate-only. type-5 (byte0=0x13) uploads
    via the DIY professional-graffiti path (MultipleDiyInScenesController, commByte 9 =
    DiyGraffitiV3.a(), which == decode(param)[1:] for catalog scenes). (source Q2/Q3)"""

    skus: ClassVar[tuple[str, ...]] = ("H6052",)
    _encryption: ClassVar[Encryption] = Encryption.NONE
    _color_scheme: ClassVar[ColorScheme] = "h6006"
    min_kelvin: ClassVar[int] = 2000
    max_kelvin: ClassVar[int] = 9000
    _scene_versions: ClassVar[frozenset[int]] = frozenset({0, 1, 4, 5})

    def _scene_upload_frames(self, scene: Any) -> list[bytes] | None:
        param = scene.param
        if scene.scene_type == 5 and param:
            raw = base64.b64decode(param)
            if raw and raw[0] == 0x13:  # DiyGraffitiV3 professional-graffiti, commByte 9
                return controllers.scene_upload_a3(param, controllers.COMM_H6052_GRAFFITI, strip=1)
        return super()._scene_upload_frames(scene)  # type-3 → activate-only (no version 3)


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
    _scene_versions: ClassVar[frozenset[int]] = frozenset({1})  # type-1 upload (see H6006, Q1)


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
    # versionArray (h6047). Bundled scenes are sceneType 2 (rgbic) → V2 comType 2,
    # strip 0 → dialect-A upload. (source Q4 / handoff)
    _scene_versions: ClassVar[frozenset[int]] = frozenset({0, 1, 2, 3})
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
    # versionArray (dreamcolorlightv1). Bundled scenes are sceneType 2 (rgbic) → V2
    # comType 2, strip 0 → dialect-A upload. Aligned to the confirmed spec set. (Q4)
    _scene_versions: ClassVar[frozenset[int]] = frozenset({0, 1, 2, 3})
