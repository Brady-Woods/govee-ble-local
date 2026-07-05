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
    """How a device secures its command channel.

    Per the app (BleUtil advertisement `encrypt` flag + BgcInfo `encryptVersion`)
    this is binary at heart — encrypted or not — with two encrypted variants:

    - NONE: encrypt flag clear -> no handshake, plaintext frames (H6006, H6052,
      H61A8, ...).
    - AES_RC4_PSK: encrypt flag set, BgcInfo version 1 (or no BgcInfo) -> e7 01
      handshake, session key, AES-ECB+RC4 per frame (H60A6, H5083, ...).
    - AES_GCM: encrypt flag set, BgcInfo version 2 -> e7 1a handshake, AES-GCM
      (Controller4AesGcm). Newer devices.

    (There is no "handshake-only" mode — a device either uses the encrypted
    channel or sends plaintext.)
    """

    NONE = "none"
    AES_RC4_PSK = "aes_rc4_psk"
    AES_GCM = "aes_gcm"


@dataclass(frozen=True)
class Segment:
    """One addressable segment of a multi-segment device."""

    index: int
    rgb: tuple[int, int, int] | None = None
    brightness: int | None = None  # 0..100


@dataclass(frozen=True)
class Zone:
    """A named physical zone of a device (e.g. H60A6's ring / panel).

    `power_index` addresses the zone-on/off command (33 30 <index>);
    `segments` lists the segment bit-indices that make up this zone (for
    zone-level color via the segment mask).
    """

    name: str
    power_index: int
    segments: tuple[int, ...] = ()


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
