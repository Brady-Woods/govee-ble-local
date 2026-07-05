"""Command builders — one function per device command.

Each returns a 20-byte plaintext frame (the transport encrypts it with the
session key before writing). Sub-command opcodes are from
``com.govee.h5080.ble.BleConstants`` and the shared light controllers; the
color/color-temp/scene byte layouts are verified byte-exact against real
device captures (see PROTOCOL.md).
"""
from __future__ import annotations

import math
from typing import Literal

from .frame import PRO_READ, PRO_WRITE, build_frame

# --- opcodes (BleConstants + observed) -------------------------------------
CMD_POWER = 0x01           # 33 01 <val>           (SwitchController, cmd 1)
CMD_BRIGHTNESS = 0x04      # 33 04 <pct>           (BrightnessController, cmd 4)
CMD_MODE = 0x05            # 33 05 <sub> ...       (AbsModeController: color/scene)
CMD_SECRET_CHECK = 0xB2    # 33 b2 <secret>        (SINGLE_CHECK_SECRET_KEY)
CMD_SECRET_READ = 0xB1     # aa b1                 (SINGLE_READ_SECRET_KEY)
CMD_SYNC_TIME = 0xB5       # 33 b5 <ts> 01 f9      (plug family; 0x09 on lights)
CMD_STATUS_FIELD = 0x01    # aa 01                 (status/heartbeat read)

# mode sub-command bytes
MODE_SCENE = 0x04          # 33 05 04 <id-hi> <id-lo>
COLOR_H60A6 = 0x15         # 33 05 15 01 ...  (H60A6/H6047/H61A8 scheme)
COLOR_H6006 = 0x0D         # 33 05 0d ...     (H6006/H6052 scheme)

# power payload values
POWER_ON, POWER_OFF = 0x01, 0x00
RELAY_ON, RELAY_OFF = 0x11, 0x10  # plug_relay family (H5080/H5083...)

ColorScheme = Literal["h60a6", "h6006"]
MIN_KELVIN, MAX_KELVIN = 2700, 6500


# --- power / brightness ----------------------------------------------------
def power(on: bool, *, relay: bool = False) -> bytes:
    """Turn on/off. `relay=True` for the plug family (0x10/0x11)."""
    if relay:
        val = RELAY_ON if on else RELAY_OFF
    else:
        val = POWER_ON if on else POWER_OFF
    return build_frame(PRO_WRITE, CMD_POWER, bytes([val]))


def brightness(pct: int) -> bytes:
    """Set brightness 1..100 (BrightnessController: 33 04 <pct>)."""
    return build_frame(PRO_WRITE, CMD_BRIGHTNESS, bytes([max(0, min(100, pct))]))


# --- color -----------------------------------------------------------------
def rgb(r: int, g: int, b: int, scheme: ColorScheme = "h60a6") -> bytes:
    """Set a solid RGB color. Layout differs by device generation."""
    if scheme == "h6006":
        return build_frame(PRO_WRITE, CMD_MODE, bytes([COLOR_H6006, r, g, b]))
    return build_frame(
        PRO_WRITE, CMD_MODE,
        bytes([COLOR_H60A6, 0x01, r, g, b, 0, 0, 0, 0, 0, 0xFF, 0x1F]),
    )


def color_temp(kelvin: int, scheme: ColorScheme = "h60a6") -> bytes:
    """Set color temperature (Kelvin). Sends the raw Kelvin plus a cosmetic
    black-body tint (kelvin_to_rgb)."""
    kelvin = max(MIN_KELVIN, min(MAX_KELVIN, kelvin))
    tr, tg, tb = kelvin_to_rgb(kelvin)
    khi, klo = (kelvin >> 8) & 0xFF, kelvin & 0xFF
    if scheme == "h6006":
        return build_frame(PRO_WRITE, CMD_MODE, bytes([COLOR_H6006, tr, tg, tb, khi, klo, tr, tg, tb]))
    return build_frame(
        PRO_WRITE, CMD_MODE,
        bytes([COLOR_H60A6, 0x01, 0xFF, 0xFF, 0xFF, khi, klo, tr, tg, tb, 0xFF, 0x1F]),
    )


def segment_color(segment_mask: int, r: int, g: int, b: int) -> bytes:
    """Set the color of one or more segments (h60a6-scheme devices with
    segments, e.g. H61A8). `segment_mask` is a 16-bit bitmask of segments."""
    lo, hi = segment_mask & 0xFF, (segment_mask >> 8) & 0xFF
    return build_frame(
        PRO_WRITE, CMD_MODE,
        bytes([COLOR_H60A6, 0x01, r, g, b, 0, 0, 0, 0, 0, lo, hi]),
    )


def scene(scene_id: tuple[int, int]) -> bytes:
    """Activate a built-in scene by its 2-byte id (33 05 04 <hi> <lo>)."""
    return build_frame(PRO_WRITE, CMD_MODE, bytes([MODE_SCENE, scene_id[0], scene_id[1]]))


# --- plug / transport-adjacent --------------------------------------------
def sync_time(unix_ts: int) -> bytes:
    """Push wall-clock time: 33 b5 <4-byte big-endian ts> 01 f9. Required
    after every power command on the plug family for the relay to actuate."""
    ts = unix_ts & 0xFFFFFFFF
    return build_frame(
        PRO_WRITE, CMD_SYNC_TIME,
        bytes([(ts >> 24) & 0xFF, (ts >> 16) & 0xFF, (ts >> 8) & 0xFF, ts & 0xFF, 0x01, 0xF9]),
    )


def secret_read() -> bytes:
    """Read the device's 8-byte secret (only succeeds on an unbound device)."""
    return build_frame(PRO_READ, CMD_SECRET_READ)


def secret_check(secret: bytes) -> bytes:
    """Present the 8-byte secret to unlock command processing (33 b2 <secret>)."""
    if len(secret) != 8:
        raise ValueError(f"secret must be 8 bytes, got {len(secret)}")
    return build_frame(PRO_WRITE, CMD_SECRET_CHECK, secret)


def status_field(field: int = CMD_STATUS_FIELD) -> bytes:
    """Read a status field (aa <field>), e.g. 0x01 = online/heartbeat poll."""
    return build_frame(PRO_READ, field)


# --- helpers ---------------------------------------------------------------
def kelvin_to_rgb(kelvin: int) -> tuple[int, int, int]:
    """Approximate black-body RGB tint for a color temperature (cosmetic;
    the raw Kelvin value drives the actual color). Verified against H60A6
    reference points (2700K/6500K)."""
    temp = kelvin / 100.0
    red = 255.0 if temp <= 66 else 329.698727446 * ((temp - 60) ** -0.1332047592)
    if temp <= 66:
        green = 99.4708025861 * math.log(temp) - 161.1195681661
    else:
        green = 288.1221695283 * ((temp - 60) ** -0.0755148492)
    if temp >= 66:
        blue = 255.0
    elif temp <= 19:
        blue = 0.0
    else:
        blue = 138.5177312231 * math.log(temp - 10) - 305.0447927307
    def clamp(v: float) -> int:
        return max(0, min(255, round(v)))

    return (clamp(red), clamp(green), clamp(blue))
