"""Parse the H60A6-family status read-back into a DeviceState.

A status query (33-family ``ac 03 02 41 30`` short / ``ac 03 03 41 30 a5`` full)
triggers a burst of ``0xAC`` NOTIFY chunks tagged 0x00..0x08 with 0xFF as the
terminator. Reassembled into ``{tag: 17-byte body}`` they carry brightness, zone
on/off, and per-segment colour. Byte offsets are verified byte-exact against
real H60A6 captures (see the v1 library's parse_status, ported here).
"""
from __future__ import annotations

from ..models import DeviceState, Segment

# Chunk tags that must be present before a status response is considered
# complete (short query), and the fuller set that also carries segment colour.
STATUS_CHUNK_REQUIRED = (0x00, 0x01, 0x02, 0x03, 0x04, 0xFF)
STATUS_CHUNK_FULL = STATUS_CHUNK_REQUIRED + (0x05, 0x06, 0x07, 0x08)

_HEADER_LEN = 19
_GROUP = 4 * 4       # 4 records x [brightness, r, g, b]
_MARKER = 3          # inter-group marker


def parse_segments(chunks: dict[int, bytes]) -> list[Segment]:
    """Per-segment [brightness, r, g, b] records from chunks 0x05-0x08 (+0xFF
    tail). Returns [] if the segment chunks are absent or truncated."""
    if any(k not in chunks for k in (0x05, 0x06, 0x07, 0x08)):
        return []
    stream = b"".join(chunks.get(k, b"") for k in (0x05, 0x06, 0x07, 0x08, 0xFF))
    need = _HEADER_LEN + 2 * (_GROUP + _MARKER) + _GROUP
    if len(stream) < need:
        return []
    segments: list[Segment] = []
    pos = _HEADER_LEN
    for _group in range(3):
        for _ in range(4):
            brightness, r, g, b = stream[pos : pos + 4]
            segments.append(Segment(len(segments), (r, g, b), brightness))
            pos += 4
        pos += _MARKER
    return segments


def _uniform_rgb(segments: list[Segment]) -> tuple[int, int, int] | None:
    """The shared colour if every segment matches (a solid colour sets them
    all), else None (a multi-colour scene or no segment data)."""
    colours = {s.rgb for s in segments if s.rgb is not None}
    return colours.pop() if len(colours) == 1 else None


def format_mac(mac_bytes: bytes) -> str:
    return ":".join(f"{b:02X}" for b in mac_bytes)


def _parse_device_info(chunks: dict[int, bytes], address: str) -> tuple[str | None, str | None]:
    """Extract (wifi_mac, hardware_version) from the status chunks by anchoring
    on the device's own BLE MAC (little-endian) in the joined 0x01-0x04 stream.
    Layout (relative to the anchor): +9..15 = Wi-Fi MAC (reversed), +20..23 =
    hardware version bytes. Verified byte-exact in v1."""
    stream = b"".join(chunks.get(k, b"") for k in (0x01, 0x02, 0x03, 0x04, 0xFF))
    try:
        own = bytes(int(b, 16) for b in address.split(":"))
    except ValueError:
        return None, None
    anchor = stream.find(own[::-1])
    if anchor == -1:
        return None, None
    wifi_mac = None
    wifi_bytes = stream[anchor + 9 : anchor + 15]
    if len(wifi_bytes) == 6:
        wifi_mac = format_mac(wifi_bytes[::-1])
    hardware_version = None
    hw = stream[anchor + 20 : anchor + 23]
    if len(hw) == 3:
        hardware_version = f"{hw[0]}.{hw[1]:02d}.{hw[2]:02d}"
    return wifi_mac, hardware_version


def parse_metadata_text(frames: list[bytes]) -> str | None:
    """Reassemble an `ab` metadata-field response (0xAB NOTIFY chunks) into its
    ASCII value: 5-byte header then an ASCII string, zero-padded. Used for the
    serial-number read (`ab 01 05`)."""
    chunks: dict[int, bytes] = {}
    for frame in frames:
        if len(frame) == 20 and frame[0] == 0xAB:
            chunks[frame[1]] = frame[2:19]
    if not chunks:
        return None
    raw = b"".join(chunks[t] for t in sorted(chunks))
    if len(raw) <= 5:
        return None
    value = raw[5:].rstrip(b"\x00")
    try:
        return value.decode("ascii") or None
    except UnicodeDecodeError:
        return None


def parse_status(chunks: dict[int, bytes], address: str | None = None) -> DeviceState:
    """Build a DeviceState from reassembled 0xAC status chunks.

    Zone truth table (chunk 0x00 present -> shift 0), captured live:
      byte 14 = LOWER zone on, byte 15 = UPPER zone on (in the terminator chunk,
      0x05 in the full query else 0xFF). brightness is chunk 0x00 byte 10.
    When chunk 0x00 is absent (RGB/color-temp mode omits it) every offset in the
    terminator shifts by 1. When `address` is given, wifi_mac + hardware_version
    are also extracted from the joined stream.
    """
    state = DeviceState()
    chunk00 = chunks.get(0x00)
    terminator = chunks.get(0x05) or chunks.get(0xFF)

    lower = upper = None
    if terminator is not None:
        shift = 0 if chunk00 is not None else 1
        if len(terminator) >= 16 + shift:
            lower = bool(terminator[14 + shift])
            upper = bool(terminator[15 + shift])

    if chunk00 is not None and len(chunk00) >= 16:
        state.brightness = chunk00[10]

    state.segments = parse_segments(chunks)
    rgb = _uniform_rgb(state.segments)
    if rgb is not None:
        state.rgb_color = rgb

    if lower is not None or upper is not None:
        state.is_on = bool(lower or upper)

    if address is not None:
        state.wifi_mac, state.hardware_version = _parse_device_info(chunks, address)
    return state
