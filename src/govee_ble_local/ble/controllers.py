"""Command builders — one function per device command.

Each returns a 20-byte plaintext frame (the transport encrypts it with the
session key before writing). Sub-command opcodes are from
``com.govee.h5080.ble.BleConstants`` and the shared light controllers; the
color/color-temp/scene byte layouts are verified byte-exact against real
device captures (see PROTOCOL.md).
"""
from __future__ import annotations

import time
from typing import Literal

from ..crypto import checksum
from .frame import PRO_READ, PRO_WRITE, build_frame

# H60A6 dialect-B scene/DIY protocol code (getProtocolCode() = 88). Used as the
# commByte for both the 0xA3 DIY and 0xA4-MTU graffiti upload forms.
COMM_H60A6 = 0x58
# H6052 type-5 professional-graffiti commByte (MultipleDiyInScenesController -> DiyGraffitiV3.a()).
COMM_H6052_GRAFFITI = 0x09

# --- opcodes (BleConstants + observed) -------------------------------------
CMD_POWER = 0x01           # 33 01 <val>           (SwitchController, cmd 1)
CMD_BRIGHTNESS = 0x04      # 33 04 <pct>           (BrightnessController, cmd 4)
CMD_MODE = 0x05            # 33 05 <sub> ...       (AbsModeController: color/scene)
CMD_ZONE = 0x30            # 33 30 <zone> <state>  (H60A6 zone on/off)
CMD_BAR_SWITCH = 0x36      # 33 36 <left> <right>  (H6047 compose-light-switch)
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


def segment_brightness(segment_mask: int, pct: int, scheme: ColorScheme = "h60a6") -> bytes:
    """Set brightness (0-100) on specific segments (h60a6 SubModeColor opType 2):
    33 05 15 02 <pct> <mask_lo> <mask_hi> ...

    Only the 0x15 family (H60A6/H6047) supports per-segment brightness. The
    h61a8 dreamcolorlightv1 SubModeColor (0x0b) has no per-segment brightness
    path (verified in the h61 split), so we refuse rather than emit an invalid
    0x15 frame to a 0x0b device."""
    if scheme == "h61a8":
        raise ValueError("h61a8 has no per-segment brightness (use set_brightness for the whole device)")
    lo, hi = segment_mask & 0xFF, (segment_mask >> 8) & 0xFF
    pct = max(0, min(100, pct))
    return build_frame(PRO_WRITE, CMD_MODE, bytes([COLOR_H60A6, 0x02, pct, lo, hi]))


def zone_power(zone: int, on: bool) -> bytes:
    """Turn a physical zone on/off (H60A6: 33 30 <zone> <state>; zone 0 = lower
    panel, 1 = upper ring). Verified in v1."""
    return build_frame(PRO_WRITE, CMD_ZONE, bytes([zone, 1 if on else 0]))


def bar_switch(left: bool, right: bool) -> bytes:
    """Turn the H6047's two bars on/off in one frame: 33 36 <left> <right>.

    Ported from com.govee.h6047 (NewDetailVm.I5 -> Controller4ExtBytes.e(
    {0x36, left, right}); 0x36 = value_compose_light_switch). Unlike the H60A6's
    per-zone 33 30 command, the H6047 carries BOTH bar states in a single write,
    so callers must pass the intended state of both bars."""
    return build_frame(PRO_WRITE, CMD_BAR_SWITCH, bytes([1 if left else 0, 1 if right else 0]))


def bar_switch_query() -> bytes:
    """Read the H6047's two bar states: aa 36 -> reply aa 36 <left> <right>
    (verified live). 0x30 is push-only; 0x36 is the readable one."""
    return build_frame(PRO_READ, CMD_BAR_SWITCH)


def scene(scene_id: tuple[int, int]) -> bytes:
    """Activate a built-in scene: 33 05 04 <b0> <b1> (code is little-endian,
    so callers pass (code & 0xFF, code >> 8))."""
    return build_frame(PRO_WRITE, CMD_MODE, bytes([MODE_SCENE, scene_id[0], scene_id[1]]))


def scene_chunks(param_b64: str) -> list[bytes]:
    """LEGACY / diagnostic only — do NOT use for production scene upload.

    This is the historical builder: it ORs ``0x08`` onto blob byte 0 and omits the
    a3_start ``commByte``. Hardware + Java review showed this is wrong (the byte 4
    slot is a device/scene ``commByte``; the `0x50|0x08 == 0x58` match on H60A6 was
    a coincidence). Kept only so the `tools/*_ab_*` A/B experiments can reproduce the
    old framing. Production uses :func:`scene_upload_a3`."""
    import base64

    raw = bytearray(base64.b64decode(param_b64))
    raw[0] |= 0x08
    data = bytes(raw)
    chunk_count = -(-(2 + len(data)) // 17)  # ceil
    content = bytes([0x01, chunk_count]) + data
    pieces = -(-len(content) // 17)
    out: list[bytes] = []
    for i in range(pieces):
        piece = content[i * 17 : (i + 1) * 17]  # <=17 data bytes
        seq = 0xFF if i == pieces - 1 else i
        # a3 <seq> <17 data bytes> <checksum> (build_frame zero-pads + checksums)
        out.append(build_frame(0xA3, seq, piece))
    return out


def scene_upload_a3(param_b64: str, comm_byte: int, *, strip: int = 0) -> list[bytes]:
    """Path-B (scene-library) a3 upload burst — the corrected framing.

    Byte-stream chunked into ``0xA3`` frames (seq 0,1,…; last = ``0xFF``,
    **data-bearing**): ``[0x01(marker), packet_count, comm_byte, <value…>]``.
    - ``comm_byte`` (frame byte 4) is the scene-version comType chosen by the caller
      from ``(sceneType, versionArray)`` (see ``scenes.scene_upload_params``); NOT
      ``value[0] | 0x08``.
    - ``value`` = base64-decoded ``scenceParam`` verbatim, minus ``strip`` leading
      bytes (0 for rgb/rgbic, 2 for graffiti/V3, 1 for compose — per the Java review).
      No re-encode is applied (library scenes upload the param ~verbatim).

    ``packet_count`` = total frames incl. START and the data-bearing terminator.
    """
    import base64

    value = base64.b64decode(param_b64)[strip:]
    stream = bytes([0x01, 0x00, comm_byte & 0xFF]) + value
    pieces = -(-len(stream) // 17)  # ceil; packet_count fits in one byte
    stream = bytes([0x01, pieces & 0xFF, comm_byte & 0xFF]) + value
    out: list[bytes] = []
    for i in range(pieces):
        piece = stream[i * 17 : (i + 1) * 17]
        seq = 0xFF if i == pieces - 1 else i  # last frame is data-bearing
        out.append(build_frame(0xA3, seq, piece))
    return out


def scene_upload_a4_mtu(value: bytes, comm_byte: int, *, mtu: int = 20) -> list[bytes]:
    """0xA4-MTU multi-packet scene upload — MultipleControllerCommV1.makeSendBytesMtu.

    Used by the H60A6 graffiti dialect-B path (commByte 0x58). Frame forms
    (seq_marker @ bytes 1-2, u16 LE)::

        START  [A4 00 00 01 cntLo cntHi comm  value×(mtu-8)]   seq_marker 0x0000
        MIDDLE [A4 seqLo seqHi           value×(mtu-4)]        seq = 1-based index
        END    [A4 FF FF                 value]                seq_marker 0xFFFF

    ``cnt`` = total frame count. The END frame is **data-bearing** (carries the last
    value chunk); there is no separate empty terminator in the multi-packet case.
    Frames are exact-length with a trailing BCC (NOT zero-padded to 20), so the END
    is naturally short and ``START.value ++ MIDDLEs ++ END.value == value`` byte-exact.
    ``mtu`` is the app-internal MtuConfig size (default 20), not the GATT ATT MTU.

    Verified vs makeSendBytesMtu: Aurora (187 B, mtu 20) -> 12 frames =
    START(12) + 10×MIDDLE(16) + END(15).
    """
    def _f(body: bytes) -> bytes:
        return body + bytes([checksum(body)])

    start_cap, mid_cap = mtu - 8, mtu - 4
    head, rest = value[:start_cap], value[start_cap:]
    chunks = [rest[i : i + mid_cap] for i in range(0, len(rest), mid_cap)]
    total = 1 + (len(chunks) if chunks else 1)  # START + (data frames | empty END)
    out = [_f(bytes([0xA4, 0x00, 0x00, 0x01, total & 0xFF, (total >> 8) & 0xFF,
                     comm_byte & 0xFF]) + head)]
    if not chunks:  # small case: START holds all; empty terminator END
        out.append(_f(bytes([0xA4, 0xFF, 0xFF])))
        return out
    last = len(chunks) - 1
    for idx, chunk in enumerate(chunks):
        if idx == last:  # data-bearing END
            out.append(_f(bytes([0xA4, 0xFF, 0xFF]) + chunk))
        else:            # MIDDLE, 1-based packet index
            seq = idx + 1
            out.append(_f(bytes([0xA4, seq & 0xFF, (seq >> 8) & 0xFF]) + chunk))
    return out


# --- plug / transport-adjacent --------------------------------------------
def sync_time(unix_ts: int) -> bytes:
    """Push wall-clock time: ``33 b5 <4-byte BE ts> 01 <tzHour> <tzMin>``.

    Layout confirmed against ``h5080/ble/controller/SyncTimeController.java``: the
    plug family uses cmd ``0xB5`` with a 4-byte big-endian Unix timestamp, a
    constant ``0x01``, then the **local UTC offset** as signed hour + signed
    minute bytes (DST-aware; the app derives it from ``TimeZoneUtil``). Both the
    hour and minute bytes carry the sign, e.g. UTC-7 -> ``F9 00``,
    UTC-3:30 -> ``FD E2`` (-3, -30). (Previously the offset was hardcoded to
    ``01 F9`` = UTC-7 / tzMin 0, wrong for every other zone.) Required after
    every power command on the plug family for the relay to actuate."""
    ts = unix_ts & 0xFFFFFFFF
    off = time.localtime(unix_ts).tm_gmtoff or 0   # seconds east of UTC, DST-aware
    tz_hour = int(off / 3600)                       # truncate toward zero, signed
    tz_min = int((abs(off) % 3600) / 60)            # magnitude minutes...
    if off < 0:
        tz_min = -tz_min                            # ...with the same sign as the hour
    return build_frame(
        PRO_WRITE, CMD_SYNC_TIME,
        bytes([
            (ts >> 24) & 0xFF, (ts >> 16) & 0xFF, (ts >> 8) & 0xFF, ts & 0xFF,
            0x01, tz_hour & 0xFF, tz_min & 0xFF,
        ]),
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


def device_info_query(selector: int) -> bytes:
    """Read device info via commandType 0x07 (aa 07 <selector>). Selectors
    (from BasicWifiInfoController / SnController): 0x11 = wifi MAC + software +
    hardware version; 0x02 = serial/UID. Response is a single aa 07 <sel> frame."""
    return build_frame(PRO_READ, 0x07, bytes([selector]))


def power_query() -> bytes:
    """Read on/off (aa 01 01); reply aa 01 <on>. (SwitchController)"""
    return build_frame(PRO_READ, CMD_POWER, bytes([0x01]))


def brightness_query() -> bytes:
    """Read brightness (aa 04 01); reply aa 04 <pct>. (BrightnessController)"""
    return build_frame(PRO_READ, CMD_BRIGHTNESS, bytes([0x01]))


def mode_query() -> bytes:
    """Read the current mode (aa 05 01). The reply is aa 05 <subMode> <data>:
    subMode 0x04 = scene (data = little-endian scene code), 0x15/0x0d/0x0b =
    color, 0x13 = music. This is how the app knows the active scene."""
    return build_frame(PRO_READ, CMD_MODE, bytes([0x01]))


def status_query(full: bool = False) -> bytes:
    """Trigger the H60A6-family status read-back (a burst of 0xAC NOTIFY
    chunks). full=True also requests per-segment colour (adds the 0xa5 sub):
    ac 03 02 41 30 (short) / ac 03 03 41 30 a5 (full)."""
    if full:
        return build_frame(0xAC, 0x03, bytes([0x03, 0x41, 0x30, 0xA5]))
    return build_frame(0xAC, 0x03, bytes([0x02, 0x41, 0x30]))
