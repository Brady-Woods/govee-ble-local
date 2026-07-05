"""Public data types: capabilities and device state."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Capability(str, Enum):
    """A feature a device supports. Drives Home Assistant entity creation."""

    POWER = "power"
    BRIGHTNESS = "brightness"
    RGB = "rgb"
    COLOR_TEMP = "color_temp"
    SEGMENTS = "segments"
    SCENES = "scenes"


class Encryption(str, Enum):
    """How a device secures its command channel (independent of everything else).

    - AES_RC4_PSK: e7 handshake -> session key; every frame encrypted with it
      (H60A6, H5083, ...).
    - HANDSHAKE_ONLY: performs the e7 handshake but sends application frames in
      plaintext (some older strips — verify per device).
    - NONE: no handshake at all; plaintext frames (H6006, ...).
    """

    AES_RC4_PSK = "aes_rc4_psk"
    HANDSHAKE_ONLY = "handshake_only"
    NONE = "none"


@dataclass(frozen=True)
class Segment:
    """One addressable segment of a multi-segment device."""

    index: int
    rgb: tuple[int, int, int] | None = None
    brightness: int | None = None  # 0..100


@dataclass
class DeviceState:
    """Best-known device state. Fields are None when unknown/unsupported.

    Devices with no status read-back (e.g. the plug family) track state
    optimistically: it reflects the last command we sent, not a device read.
    """

    is_on: bool | None = None
    brightness: int | None = None            # 0..100
    rgb_color: tuple[int, int, int] | None = None
    color_temp_kelvin: int | None = None
    segments: list[Segment] = field(default_factory=list)
    optimistic: bool = False                 # True if not read back from the device
