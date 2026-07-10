"""Spec-conformant frame builders — the *write* side of ``spec/govee_ble.ksy``.

Kaitai generates readers only, so these builders encode the same layouts by hand
(the ``.ksy`` doc says "the write side is symmetric"). They are intentionally
**independent of** ``govee_ble_local.ble.controllers`` so the spec can be tested
on its own terms: build here → parse with the Kaitai-generated reader
(``tests/spec_gen``) → assert the fields, then (live suite) send to a real device.

Every builder returns a 20-byte frame with the trailing XOR checksum
(``checksum = XOR of bytes 0..18``, per §4.1 / ``govee_ble.ksy`` meta doc).

Field layouts are cited to the ``govee_ble.ksy`` type they mirror.
"""
from __future__ import annotations

import base64

FRAME_LEN = 20

# proType header byte (enum pro_type)
PRO_WRITE = 0x33
PRO_READ = 0xAA
PRO_MULTI_WRITE_V1 = 0xA3
PRO_MULTI_REPLY_READ = 0xAC

# command byte (enum command)
CMD_SWITCH = 0x01
CMD_BRIGHTNESS = 0x04
CMD_MODE = 0x05
CMD_ZONE = 0x30           # light_direction_or_zone
CMD_PLUG_SYNC_TIME = 0xB5

# mode sub-mode byte (enum sub_mode)
SUB_SCENE = 0x04
SUB_COLOR_RGBIC_15 = 0x15

# op15 byte (enum op15)
OP15_SET_COLOR = 0x01
OP15_SET_BRIGHTNESS = 0x02
OP15_SET_COLOR_TEMP = 0x05

# a3_start comm_byte (frame byte 4) — two kinds (govee_ble.ksy a3_start + devices.yaml):
#  - legacy scene-version constant (MULTI_V*_NEW_SCENES): V1=0x01 V2=0x02 V3=0x07 V6=0x0C
#  - device DIY/graffiti protocol code — H60A6 = 0x58 (88), H60A6DiyParse.getProtocolCode
# NB: 0x58 == 0x50 | 0x08 is a COINCIDENCE (H60A6 params are 0x50-prefixed), not a bit-OR rule.
MULTI_V1, MULTI_V2, MULTI_V3, MULTI_V6 = 0x01, 0x02, 0x07, 0x0C
COMM_H60A6 = 0x58


def _checksum(body: bytes) -> int:
    x = 0
    for b in body[:19]:
        x ^= b
    return x


def frame(pro_type: int, *body: int) -> bytes:
    """Assemble a 20-byte frame: [pro_type, *body, zero-pad, checksum@19]."""
    buf = bytearray(FRAME_LEN)
    buf[0] = pro_type
    payload = bytes(body)
    if len(payload) > FRAME_LEN - 2:
        raise ValueError(f"body too long: {len(payload)}")
    buf[1 : 1 + len(payload)] = payload
    buf[19] = _checksum(bytes(buf))
    return bytes(buf)


def _seg_mask_le(seg_mask: int) -> tuple[int, int]:
    """2-byte little-endian segment mask (u2le seg_mask)."""
    return seg_mask & 0xFF, (seg_mask >> 8) & 0xFF


def all_segments_mask(segments: int) -> int:
    """Whole-device mask = every segment bit set (all-ones = whole device)."""
    return (1 << max(0, min(16, segments))) - 1


# ── single_command ─────────────────────────────────────────────────────────
def power(on: bool) -> bytes:
    """switch_payload: 33 01 <state>. Lights use 0x01/0x00."""
    return frame(PRO_WRITE, CMD_SWITCH, 0x01 if on else 0x00)


def brightness(pct: int) -> bytes:
    """brightness_payload: 33 04 <level 0-100>."""
    return frame(PRO_WRITE, CMD_BRIGHTNESS, max(0, min(100, pct)))


def zone_power(zone: int, on: bool) -> bytes:
    """command light_direction_or_zone (0x30): 33 30 <zoneIndex> <state>."""
    return frame(PRO_WRITE, CMD_ZONE, zone & 0xFF, 1 if on else 0)


def plug_sync_time(unix_ts: int, tz_hour: int, tz_min: int) -> bytes:
    """plug_sync_time_payload (0xB5): u4-BE epoch, 0x01, s1 tz_hour, s1 tz_min."""
    ts = unix_ts & 0xFFFFFFFF
    return frame(
        PRO_WRITE, CMD_PLUG_SYNC_TIME,
        (ts >> 24) & 0xFF, (ts >> 16) & 0xFF, (ts >> 8) & 0xFF, ts & 0xFF,
        0x01, tz_hour & 0xFF, tz_min & 0xFF,
    )


# ── mode (0x05) sub-mode payloads ────────────────────────────────────────────
def scene_activate(effect: int) -> bytes:
    """scene_payload (0x04): 33 05 04 <effect u2le>."""
    return frame(PRO_WRITE, CMD_MODE, SUB_SCENE, effect & 0xFF, (effect >> 8) & 0xFF)


def color_rgb_15(r: int, g: int, b: int, seg_mask: int) -> bytes:
    """color_15 / op15_color, H60A1/H60A6 RGB variant:
    33 05 15 01 <r> <g> <b> 00 00 00 00 00 <seg_mask u2le>."""
    lo, hi = _seg_mask_le(seg_mask)
    return frame(
        PRO_WRITE, CMD_MODE, SUB_COLOR_RGBIC_15, OP15_SET_COLOR,
        r, g, b, 0, 0, 0, 0, 0, lo, hi,
    )


def color_temp_15(kelvin: int, tint: tuple[int, int, int], seg_mask: int) -> bytes:
    """color_15 / op15_color, H60A1/H60A6 CCT variant:
    33 05 15 01 FF FF FF <kelvin u2be> <tintR> <tintG> <tintB> <seg_mask u2le>."""
    khi, klo = (kelvin >> 8) & 0xFF, kelvin & 0xFF
    tr, tg, tb = tint
    lo, hi = _seg_mask_le(seg_mask)
    return frame(
        PRO_WRITE, CMD_MODE, SUB_COLOR_RGBIC_15, OP15_SET_COLOR,
        0xFF, 0xFF, 0xFF, khi, klo, tr, tg, tb, lo, hi,
    )


def segment_brightness_15(pct: int, seg_mask: int) -> bytes:
    """color_15 / op15_brightness (op 0x02): 33 05 15 02 <pct> <seg_mask u2le>."""
    lo, hi = _seg_mask_le(seg_mask)
    return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_RGBIC_15, OP15_SET_BRIGHTNESS,
                 max(0, min(100, pct)), lo, hi)


# ── 0xAC status request ──────────────────────────────────────────────────────
def status_query(full: bool = False) -> bytes:
    """multi_ac request: [0xAC, command, N, cmd_1..cmd_N].
    H60A6 single-zone AC 03 02 41 30 / dual-zone AC 03 03 41 30 A5."""
    if full:
        return frame(PRO_MULTI_REPLY_READ, 0x03, 0x03, 0x41, 0x30, 0xA5)
    return frame(PRO_MULTI_REPLY_READ, 0x03, 0x02, 0x41, 0x30)


# ── 0xA3 scene/DIY upload (a3_start dialect) ─────────────────────────────────
def scene_upload(param_b64: str, comm_byte: int = COMM_H60A6) -> list[bytes]:
    """Scene/DIY upload per the corrected ``a3_start`` (proType 0xA3, non-MTU form).

    Byte-stream chunked into 0xA3 frames (seq 0,1,…; last = 0xFF, **data-bearing**):
        [0x01(marker), packet_count, comm_byte, <value…>]
    - ``comm_byte`` (frame byte 4) is the H60A6 DIY/graffiti device protocol code
      (0x58 = 88), NOT a legacy comType and NOT ``value[0] | 0x08``.
    - ``value`` here is the raw cloud blob after its consumed ``0x50`` header.

    KNOWN GAPS (unresolved without an official-app btsnoop):
    - the app uploads a **re-encoded** ``toBytes()`` (H60A6GraffitiParse), not the raw
      blob sent here;
    - the graffiti default may be the **0xA4-MTU** builder (``makeSendBytesMtu``,
      commByte@byte6), not this 0xA3 form.
    """
    value = base64.b64decode(param_b64)[1:]  # 0x50 header is consumed into the comm_byte slot
    stream = bytes([0x01, 0x00, comm_byte]) + value
    pieces = -(-len(stream) // 17)                      # ceil; packet_count fits in 1 byte
    stream = bytes([0x01, pieces & 0xFF, comm_byte]) + value
    frames: list[bytes] = []
    for i in range(pieces):
        piece = stream[i * 17 : (i + 1) * 17]
        seq = 0xFF if i == pieces - 1 else i            # last frame is data-bearing
        frames.append(frame(PRO_MULTI_WRITE_V1, seq, *piece))
    return frames
