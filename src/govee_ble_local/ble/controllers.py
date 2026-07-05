"""Command builders — one function per device command.

Each returns a 20-byte plaintext frame (the transport encrypts it with the
session key before writing). Sub-command opcodes are from
``com.govee.h5080.ble.BleConstants`` and the shared light controllers; the
color/color-temp/scene byte layouts are verified byte-exact against real
device captures (see PROTOCOL.md).
"""
from __future__ import annotations

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

ColorScheme = Literal["h60a6", "h6006", "h61a8"]
COLOR_H61A8 = 0x0B  # dreamcolorlightv1.SubModeColor (subModeCommandType 11)


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
def _all_segments_mask(segments: int) -> tuple[int, int]:
    """The 2-byte 'select every segment' bitmask (BleUtil.makeBytes4SelectPos-
    ByOneBit with all positions set). e.g. 13 segments -> (0xff, 0x1f)."""
    bits = (1 << max(0, min(16, segments))) - 1
    return bits & 0xFF, (bits >> 8) & 0xFF


def rgb(r: int, g: int, b: int, scheme: ColorScheme = "h6006", segments: int = 13) -> bytes:
    """Set a solid RGB color. Each family's SubModeColor.getWriteBytes:
    - h6006 (tablelampv1, sub-cmd 0x0d): [0x0d, r,g,b, 0,0, 0,0,0]
    - h60a6 (SubModeColorV2, sub-cmd 0x15): [0x15, 1, r,g,b, 0,0, 0,0,0, mask_lo,mask_hi]
    - h61a8 (dreamcolorlightv1.SubModeColor, sub-cmd 0x0b): [0x0b, r,g,b, mask_lo,mask_hi]
    Whole-device color selects every segment (all-bits mask)."""
    lo, hi = _all_segments_mask(segments)
    if scheme == "h6006":
        return build_frame(PRO_WRITE, CMD_MODE, bytes([COLOR_H6006, r, g, b, 0, 0, 0, 0, 0]))
    if scheme == "h61a8":
        return build_frame(PRO_WRITE, CMD_MODE, bytes([COLOR_H61A8, r, g, b, lo, hi]))
    return build_frame(
        PRO_WRITE, CMD_MODE,
        bytes([COLOR_H60A6, 0x01, r, g, b, 0, 0, 0, 0, 0, lo, hi]),
    )


def color_temp(kelvin: int, scheme: ColorScheme = "h6006", segments: int = 13) -> bytes:
    """Set color temperature (Kelvin) — exact ports of each SubModeColor's
    color-temp path: WHITE in the RGB slot, the raw 16-bit Kelvin, then a
    cosmetic tint. The app looks the tint up in a table
    (Constant.getTemColorByKelvin) and sends (0,0,0) when the Kelvin isn't a
    table entry (the common case), so we send (0,0,0): the raw Kelvin drives it.
    """
    khi, klo = (kelvin >> 8) & 0xFF, kelvin & 0xFF
    lo, hi = _all_segments_mask(segments)
    if scheme == "h6006":
        return build_frame(PRO_WRITE, CMD_MODE, bytes([COLOR_H6006, 0xFF, 0xFF, 0xFF, khi, klo, 0x00, 0x00, 0x00]))
    if scheme == "h61a8":
        # dreamcolorlightv1.SubModeColor has no color-temp path (RGB rope, no CT).
        raise ValueError("h61a8 has no color-temperature capability")
    return build_frame(
        PRO_WRITE, CMD_MODE,
        bytes([COLOR_H60A6, 0x01, 0xFF, 0xFF, 0xFF, khi, klo, 0x00, 0x00, 0x00, lo, hi]),
    )


def segment_rgb(segment_mask: int, r: int, g: int, b: int, scheme: ColorScheme = "h61a8") -> bytes:
    """Set the color of specific segments. `segment_mask` is a 16-bit bitmask.
    Same per-family layouts as rgb(), with the mask selecting the target
    segments instead of all of them."""
    lo, hi = segment_mask & 0xFF, (segment_mask >> 8) & 0xFF
    if scheme == "h61a8":
        return build_frame(PRO_WRITE, CMD_MODE, bytes([COLOR_H61A8, r, g, b, lo, hi]))
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
