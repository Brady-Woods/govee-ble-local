"""Spec-conformant frame builders — the *write* side of ``spec/govee_ble.ksy``.

Kaitai generates readers only, so these builders encode the same layouts by hand
(the ``.ksy`` doc says "the write side is symmetric"). They are intentionally
**independent of** the library's own encoder, ``govee_ble_local.wire.build``, so the spec can be
tested on its own terms: build here → parse with the Kaitai-generated reader
(``govee_ble_local._generated``) → assert the fields, then (live suite) send to a real device.
``tests/test_fix_wp2a_builders.py`` checks the library's builders against these byte for byte, and
the multi-packet uploads are line-for-line ports of the app's ``MultipleControllerCommV1`` writers
(:func:`make_send_bytes_v2`, :func:`make_send_bytes_mtu`), not copies of the library's slicing.

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


# ── multi-packet writers: ports of MultipleControllerCommV1 ──────────────────
def _bcc(buf: bytes | bytearray, n: int) -> int:
    """``BleUtils.getBCC(packet, n)``: XOR of ``packet[0..n-1]``."""
    x = 0
    for b in buf[:n]:
        x ^= b
    return x


def make_send_bytes_v2(pro_type: int, comm_bytes: bytes, value: bytes) -> list[bytes]:
    """Line-for-line port of ``MultipleControllerCommV1.makeSendBytesV2`` (base2light
    ``ble/comm/MultipleControllerCommV1.java:785-849``), the 0xA3 writer behind ``makeSendBytesV1``
    (``commBytes`` = one byte). START ``[pro, 00, 01, count, commBytes…, value…]`` holds
    ``15 - len(commBytes)`` value bytes; a value that fits there leaves ``count`` at 2 and the END
    ``[pro, ff, 00…]`` empty; otherwise 17-byte MIDDLE frames ``[pro, seq, …]`` follow and the final
    chunk rides in END, ``count`` = 1 + chunks. The END frame is always appended."""
    length = len(value)
    n_comm = len(comm_bytes)
    start = bytearray(FRAME_LEN)
    start[0] = pro_type
    start[1] = 0
    start[2] = 1
    start[4 : 4 + n_comm] = comm_bytes
    i9 = n_comm + 4
    end = bytearray(FRAME_LEN)
    end[0] = pro_type
    end[1] = 0xFF
    i10 = 15 - n_comm
    middles: list[bytes] = []
    i7 = 2
    if length <= i10:
        start[i9 : i9 + length] = value
    else:
        i11 = length - i10
        i12 = i11 % 17
        i13 = (i11 // 17) + (1 if i12 > 0 else 0)
        if i12 == 0:
            i12 = 17
        start[i9 : i9 + i10] = value[:i10]
        for i15 in range(1, i13 + 1):
            last = i15 == i13
            i16 = i12 if last else 17
            chunk = bytearray(17)
            chunk[:i16] = value[i10 : i10 + i16]
            i10 += i16
            if last:
                end[2:19] = chunk
            else:
                mid = bytearray(FRAME_LEN)
                mid[0] = pro_type
                mid[1] = i15 & 0xFF
                mid[2:19] = chunk
                mid[19] = _bcc(mid, 19)
                middles.append(bytes(mid))
        i7 = i13 + 1
    start[3] = i7 & 0xFF
    start[19] = _bcc(start, 19)
    end[19] = _bcc(end, 19)
    return [bytes(start), *middles, bytes(end)]


def make_send_bytes_mtu(pro_type: int, command_type: int, value: bytes, mtu: int) -> list[bytes]:
    """Line-for-line port of ``MultipleControllerCommV1.makeSendBytesMtu`` (``:409-545``), the 0xA4
    writer: exact-length frames, BCC last. A value of at most ``mtu - 8`` bytes is START
    ``[pro, 00 00, 01, 02 00, cmd, value…]`` plus the empty END ``[pro, ff ff]``; a longer one is
    ``mtu``-byte START / MIDDLE frames (u16le count / seq, ``ByteUtils.getLowHighBytes``) and an END
    ``[pro, ff ff, rest]``."""
    length = len(value)
    i6, i7 = mtu - 8, mtu - 4
    if length <= i6:
        start = bytearray(length + 8)
        start[0:7] = bytes([pro_type, 0, 0, 1, 2, 0, command_type])
        start[7 : 7 + length] = value
        start[length + 7] = _bcc(start, length + 7)
        end = bytearray([pro_type, 0xFF, 0xFF, 0])
        end[3] = _bcc(end, 3)
        return [bytes(start), bytes(end)]
    i11 = length - i6
    i12 = i11 % i7
    i13 = (i11 // i7) + 1 + (0 if i12 == 0 else 1)
    out: list[bytes] = []
    for i14 in range(i13):
        if i14 == 0:
            buf = bytearray(mtu)
            buf[0:7] = bytes([pro_type, 0, 0, 1, i13 & 0xFF, (i13 >> 8) & 0xFF, command_type])
            buf[7 : 7 + i6] = value[:i6]
        elif i14 == i13 - 1:
            i16 = mtu if i12 == 0 else i12 + 4
            buf = bytearray(i16)
            buf[0:3] = bytes([pro_type, 0xFF, 0xFF])
            off = i6 + (i14 - 1) * i7
            buf[3 : i16 - 1] = value[off : off + i16 - 4]
        else:
            buf = bytearray(mtu)
            buf[0:3] = bytes([pro_type, i14 & 0xFF, (i14 >> 8) & 0xFF])
            off = (i14 - 1) * i7 + i6
            buf[3 : 3 + i7] = value[off : off + i7]
        buf[-1] = _bcc(buf, len(buf) - 1)
        out.append(bytes(buf))
    return out


# ── 0xA3 scene/DIY upload (a3_start dialect) ─────────────────────────────────
def scene_upload(param_b64: str, comm_byte: int = COMM_H60A6) -> list[bytes]:
    """Scene/DIY upload per the corrected ``a3_start`` (proType 0xA3, non-MTU form).

    The app's ``makeSendBytesV1`` framing (:func:`make_send_bytes_v2`, seq 0,1,…; last = 0xFF):
        [0x01(marker), packet_count, comm_byte, <value…>]
    - ``comm_byte`` (frame byte 4) is the H60A6 DIY/graffiti device protocol code
      (0x58 = 88), NOT a legacy comType and NOT ``value[0] | 0x08``.
    - ``value`` here is the raw cloud blob after its consumed ``0x50`` header.
    - the 0xFF END frame carries the final chunk of a value longer than 14 bytes; a shorter value
      rides whole in START and the END is empty (packet_count 2).

    KNOWN GAPS (unresolved without an official-app btsnoop):
    - the app uploads a **re-encoded** ``toBytes()`` (H60A6GraffitiParse), not the raw
      blob sent here;
    - the graffiti default may be the **0xA4-MTU** builder (``makeSendBytesMtu``,
      commByte@byte6), not this 0xA3 form.
    """
    value = base64.b64decode(param_b64)[1:]  # 0x50 header is consumed into the comm_byte slot
    return make_send_bytes_v2(PRO_MULTI_WRITE_V1, bytes([comm_byte]), value)
