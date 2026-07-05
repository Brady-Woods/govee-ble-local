"""Generic RGBWW light devices (power + brightness + RGB + color-temp).

Covers the older single-zone bulbs/lamps. Segment/scene-rich strips
(H61A8 etc.) get their own classes; these are the plain lights.
"""
from __future__ import annotations

from typing import ClassVar

from ..ble.controllers import ColorScheme
from ..models import Capability, Encryption
from .base import BrightnessMixin, ColorTempMixin, GoveeDevice, PowerMixin, RGBMixin

_LIGHT_CAPS = frozenset(
    {Capability.POWER, Capability.BRIGHTNESS, Capability.RGB, Capability.COLOR_TEMP}
)


class GoveeRgbLight(PowerMixin, BrightnessMixin, RGBMixin, ColorTempMixin, GoveeDevice):
    """Base for single-zone RGBWW lights: on/off, brightness, RGB, color-temp."""

    capabilities: ClassVar[frozenset[Capability]] = _LIGHT_CAPS
    min_kelvin: ClassVar[int] = 2700
    max_kelvin: ClassVar[int] = 6500


class GoveeLightH60A6(GoveeRgbLight):
    """H60A6 — AES-RC4-PSK, h60a6 color scheme. (Confirmed.)"""

    skus: ClassVar[tuple[str, ...]] = ("H60A6",)
    _encryption: ClassVar[Encryption] = Encryption.AES_RC4_PSK
    _color_scheme: ClassVar[ColorScheme] = "h60a6"


class GoveeLightH6006(GoveeRgbLight):
    """H6006 — plaintext (no handshake), h6006 color scheme. (Confirmed.)"""

    skus: ClassVar[tuple[str, ...]] = ("H6006",)
    _encryption: ClassVar[Encryption] = Encryption.NONE
    _color_scheme: ClassVar[ColorScheme] = "h6006"


class GoveeLightH6052(GoveeRgbLight):
    """H6052 — plaintext (no handshake), h6006 color scheme, wide CT range."""

    skus: ClassVar[tuple[str, ...]] = ("H6052",)
    _encryption: ClassVar[Encryption] = Encryption.NONE
    _color_scheme: ClassVar[ColorScheme] = "h6006"
    min_kelvin: ClassVar[int] = 2000
    max_kelvin: ClassVar[int] = 9000
