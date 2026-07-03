"""Pure Govee BLE protocol helpers: encryption, packet framing, command
construction, and status parsing.

Nothing here touches a Bluetooth stack — it's all deterministic byte
manipulation — so this module imports only `cryptography` and is fully unit
testable without `bleak` installed.
"""
from __future__ import annotations

import logging
import math

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

try:
    from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4
except ImportError:  # cryptography < 43
    from cryptography.hazmat.primitives.ciphers.algorithms import ARC4

from .const import MAX_COLOR_TEMP_KELVIN, MIN_COLOR_TEMP_KELVIN
from .models import GoveeBleSegment, GoveeBleStatus

_LOGGER = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Encryption + packet framing
# --------------------------------------------------------------------------


def aes_ecb(key16: bytes, block16: bytes, encrypt: bool) -> bytes:
    cipher = Cipher(algorithms.AES(key16), modes.ECB())
    op = cipher.encryptor() if encrypt else cipher.decryptor()
    return op.update(block16) + op.finalize()


def rc4(key16: bytes, data: bytes) -> bytes:
    cipher = Cipher(ARC4(key16), mode=None)
    enc = cipher.encryptor()
    return enc.update(data) + enc.finalize()


def checksum(body19: bytes) -> bytes:
    x = 0
    for b in body19:
        x ^= b
    return bytes([x])


def build_plaintext(prefix: bytes) -> bytes:
    """Pad a command prefix to 19 bytes and append the XOR checksum (20 total)."""
    body = prefix + b"\x00" * (19 - len(prefix))
    return body + checksum(body)


def encrypt_packet(key16: bytes, plaintext20: bytes) -> bytes:
    """AES-ECB the first 16 bytes, RC4 the last 4."""
    return aes_ecb(key16, plaintext20[:16], True) + rc4(key16, plaintext20[16:20])


def decrypt_packet(key16: bytes, ciphertext20: bytes) -> bytes:
    return aes_ecb(key16, ciphertext20[:16], False) + rc4(key16, ciphertext20[16:20])


def format_mac(mac_bytes: bytes) -> str:
    return ":".join(f"{b:02X}" for b in mac_bytes)


# --------------------------------------------------------------------------
# Command builders (return the pre-framing "prefix"; frame with build_plaintext)
# --------------------------------------------------------------------------


def cmd_handshake(step: int) -> bytes:
    return bytes([0xE7, step])


def cmd_status_query() -> bytes:
    # Short query -> chunks 0x00-0x04 + 0xFF (the reliable variant).
    return bytes([0xAC, 0x03, 0x02, 0x41, 0x30])


def cmd_metadata_field(field_id: int) -> bytes:
    return bytes([0xAB, 0x01, field_id])


def cmd_set_zone(zone: int, on: bool) -> bytes:
    return bytes([0x33, 0x30, zone, 1 if on else 0])


def cmd_set_brightness(pct: int) -> bytes:
    pct = max(0, min(100, pct))
    return bytes([0x33, 0x04, pct])


def cmd_set_rgb(r: int, g: int, b: int) -> bytes:
    return bytes([0x33, 0x05, 0x15, 0x01, r, g, b, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF, 0x1F])


def cmd_set_color_temp(kelvin: int) -> bytes:
    kelvin = max(MIN_COLOR_TEMP_KELVIN, min(MAX_COLOR_TEMP_KELVIN, kelvin))
    ar, ag, ab = kelvin_to_rgb(kelvin)
    return bytes(
        [
            0x33, 0x05, 0x15, 0x01,
            0xFF, 0xFF, 0xFF,
            (kelvin >> 8) & 0xFF, kelvin & 0xFF,
            ar, ag, ab,
            0xFF, 0x1F,
        ]
    )


def cmd_set_segment_color(segment_mask: int, r: int, g: int, b: int) -> bytes:
    """Per-segment RGB. `segment_mask` is a 16-bit little-endian bitmask
    (bits 0-11 confirmed on the H60A6)."""
    mask_lo = segment_mask & 0xFF
    mask_hi = (segment_mask >> 8) & 0xFF
    return bytes(
        [
            0x33, 0x05, 0x15, 0x01,
            r, g, b,
            0x00, 0x00, 0x00, 0x00, 0x00,
            mask_lo, mask_hi,
            0x00, 0x00, 0x00, 0x00, 0x00,
        ]
    )


def cmd_set_segment_brightness(segment_mask: int, pct: int) -> bytes:
    """Per-segment brightness (0-100). Same bitmask scheme as
    cmd_set_segment_color but sub-opcode 0x02."""
    pct = max(0, min(100, pct))
    mask_lo = segment_mask & 0xFF
    mask_hi = (segment_mask >> 8) & 0xFF
    return bytes(
        [
            0x33, 0x05, 0x15, 0x02,
            pct,
            mask_lo, mask_hi,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        ]
    )


def cmd_set_scene(scene_id: tuple[int, int]) -> bytes:
    """Bare scene activation (works only if the device already has the scene
    cached; prefer a full upload via build_scene_chunks otherwise)."""
    return bytes([0x33, 0x05, 0x04, scene_id[0], scene_id[1]])


def kelvin_to_rgb(kelvin: int) -> tuple[int, int, int]:
    """Approximate the black-body RGB tint for a color temperature.

    Verified against real captured reference points from the H60A6:
    2700K -> (255, 174, 84) real vs (255, 167, 87) computed; 6500K ->
    (255, 249, 251) real vs (255, 254, 250) computed. This is only a cosmetic
    tint sent alongside the raw Kelvin value, not the primary color driver.
    """
    temp = kelvin / 100.0
    if temp <= 66:
        red = 255.0
    else:
        red = 329.698727446 * ((temp - 60) ** -0.1332047592)
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


# --------------------------------------------------------------------------
# Scene upload chunking
# --------------------------------------------------------------------------


def build_scene_chunks(scenceParam_b64: str) -> list[bytes]:
    """Split a scene's effect data into the `a3`-chunk payload sequence.

    Each returned entry is a 19-byte prefix: [0xA3, seq_byte, <=17 data bytes],
    ready to be checksummed+encrypted+sent (seq bytes: 0x00, 0x01, ... for all
    but the last chunk, which always uses 0xFF).
    """
    import base64

    data = bytearray(base64.b64decode(scenceParam_b64))
    # Byte 0 of the API's raw scenceParam is an "unconfirmed template" flag;
    # the device silently no-ops unless this bit is set, even though it still
    # acks. The real app always sends it set.
    data[0] |= 0x08
    data = bytes(data)
    content_len = 2 + len(data)
    chunk_count = -(-content_len // 17)  # ceiling division
    content = bytes([0x01, chunk_count]) + data

    chunks: list[bytes] = []
    num_pieces = -(-len(content) // 17)
    for i in range(num_pieces):
        piece = content[i * 17 : (i + 1) * 17]
        piece = piece + b"\x00" * (17 - len(piece))
        seq = 0xFF if i == num_pieces - 1 else i
        chunks.append(bytes([0xA3, seq]) + piece)
    return chunks


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------


def parse_metadata_field_text(raw: bytes) -> str | None:
    """Extract the ASCII text value from a reassembled `ab` metadata field
    response: a 5-byte header followed by an ASCII string, zero-padded to the
    end of the last chunk. Returns None if empty or non-ASCII."""
    if len(raw) <= 5:
        return None
    value = raw[5:].rstrip(b"\x00")
    try:
        return value.decode("ascii") or None
    except UnicodeDecodeError:
        return None


def parse_segment_records(chunks: dict[int, bytes]) -> list[GoveeBleSegment] | None:
    """Extract per-segment (brightness, r, g, b) state from status chunks
    0x05-0x08 (+ the tail of 0xFF).

    Structure: a fixed 19-byte header, then 3 groups of [4 records of 4 bytes
    (brightness_pct, r, g, b)][3-byte marker]. Returns None if the required
    chunks are absent or the reassembled stream is too short (treated as
    "segment data unavailable this poll", not an error).
    """
    if any(k not in chunks for k in (0x05, 0x06, 0x07, 0x08)):
        _LOGGER.debug(
            "Segment records unavailable: missing chunk(s) %s (have %s)",
            {0x05, 0x06, 0x07, 0x08} - set(chunks),
            sorted(chunks.keys()),
        )
        return None
    stream = b"".join(chunks.get(k, b"") for k in (0x05, 0x06, 0x07, 0x08, 0xFF))

    header_len = 19
    group_size = 4 * 4  # 4 records x 4 bytes
    marker_len = 3
    records_needed = header_len + 2 * (group_size + marker_len) + group_size
    if len(stream) < records_needed:
        _LOGGER.debug(
            "Segment records unavailable: reassembled stream too short "
            "(%d bytes, need %d) - chunk 0xFF likely truncated this poll",
            len(stream),
            records_needed,
        )
        return None

    segments: list[GoveeBleSegment] = []
    pos = header_len
    for _group in range(3):
        for _ in range(4):
            brightness, r, g, b = stream[pos : pos + 4]
            segments.append(GoveeBleSegment(len(segments), brightness, r, g, b))
            pos += 4
        pos += marker_len
    return segments


def parse_status(address: str, chunks: dict[int, bytes]) -> GoveeBleStatus:
    """Parse a full status response into a GoveeBleStatus.

    `address` is the device's own BLE MAC (colon-hex), used as an anchor to
    locate the MAC/version block regardless of the mode-dependent one-byte
    shift (chunk 0x00 is omitted in RGB/color-temp mode).
    """
    status = GoveeBleStatus()

    chunk00 = chunks.get(0x00)
    has_chunk00 = chunk00 is not None

    # Zone power state. In the short query the terminator is chunk 0xFF; in the
    # fuller per-segment query the same bytes are relabeled 0x05 (0xFF becomes
    # the segment tail), so prefer 0x05 then fall back to 0xFF.
    #
    # Truth table (chunk00 present, shift 0), captured live on two devices:
    #   U=0 L=0 -> byte14=0x00 byte15=0x00
    #   U=1 L=0 -> byte14=0x00 byte15=0x01
    #   U=0 L=1 -> byte14=0x01 byte15=0x00
    #   U=1 L=1 -> byte14=0x01 byte15=0x01
    # => byte 14 = LOWER zone, byte 15 = UPPER zone. (byte 13 is a static 0x02
    # and does NOT reflect power.)
    terminator = chunks.get(0x05) or chunks.get(0xFF)
    if terminator is not None:
        shift = 0 if has_chunk00 else 1
        if len(terminator) >= 16 + shift:
            status.zone_lower_on = bool(terminator[14 + shift])
            status.zone_upper_on = bool(terminator[15 + shift])

    if has_chunk00 and len(chunk00) >= 16:
        status.brightness_pct = chunk00[10]
        status.scene_id = (chunk00[14], chunk00[15])

    stream = b"".join(chunks.get(k, b"") for k in (0x01, 0x02, 0x03, 0x04, 0xFF))
    own_mac_bytes = bytes(int(b, 16) for b in address.split(":"))
    anchor = stream.find(own_mac_bytes[::-1])
    if anchor != -1:
        status.ble_mac = format_mac(own_mac_bytes)
        wifi_mac_bytes = stream[anchor + 9 : anchor + 15]
        if len(wifi_mac_bytes) == 6:
            status.wifi_mac = format_mac(wifi_mac_bytes[::-1])
        hw_bytes = stream[anchor + 20 : anchor + 23]
        if len(hw_bytes) == 3:
            status.hardware_version = f"{hw_bytes[0]}.{hw_bytes[1]:02d}.{hw_bytes[2]:02d}"
    else:
        _LOGGER.debug(
            "Could not locate own BLE MAC in status stream from %s: %s",
            address,
            stream.hex(),
        )

    status.segments = parse_segment_records(chunks)
    return status
