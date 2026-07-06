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


def _version(b: bytes) -> str:
    """Govee 3-byte version -> "X.YY.ZZ" (BasicWifiInfoController.u)."""
    return f"{b[0]}.{b[1]:02d}.{b[2]:02d}"


def _anchor_device_info(chunks: dict[int, bytes], address: str) -> tuple[str | None, str | None]:
    """(wifi_mac, hardware_version) from the 0xAC status stream, anchored on the
    device's own BLE MAC (little-endian) in the joined 0x01-0x04 stream: +9..15 =
    Wi-Fi MAC (reversed), +20..23 = hardware version. This is how BLE-only
    devices (e.g. H60A6) report their hardware version; wifi bytes are zero
    (dropped) on a device with no WiFi. Verified byte-exact in v1."""
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
    if len(wifi_bytes) == 6 and any(wifi_bytes):
        wifi_mac = format_mac(wifi_bytes[::-1])
    hardware_version = None
    hw = stream[anchor + 20 : anchor + 23]
    if len(hw) == 3 and any(hw):
        hardware_version = _version(hw)
    return wifi_mac, hardware_version


def parse_wifi_info(frame: bytes) -> tuple[str, str, str] | None:
    """Parse a BasicWifiInfoController response (aa 07 11): returns
    (wifi_mac, software_version, hardware_version). Layout after the
    proType+commandType: [0x11, wifiMac(6, forward), soft(3), hard(3)]."""
    if len(frame) < 15 or frame[0] != 0xAA or frame[1] != 0x07 or frame[2] != 0x11:
        return None
    wifi_mac = ":".join(f"{b:02X}" for b in frame[3:9])   # z5=true -> forward order
    software = _version(frame[9:12])
    hardware = _version(frame[12:15])
    return wifi_mac, software, hardware


def _uid_to_serial(uid: bytes) -> str | None:
    """8-byte UID -> reversed colon-hex, leading 00:00: stripped (toAddressBytes
    z5=false). None if all-zero."""
    if not any(uid):
        return None
    sn = ":".join(f"{b:02X}" for b in reversed(uid))
    return sn[6:] if sn.startswith("00:00:") else sn


def parse_basic_info(frame: bytes) -> tuple[str | None, str, str] | None:
    """Parse a BasicInfoController response (aa 07 10) for a BLE (non-WiFi)
    device: returns (serial, software_version, hardware_version). Layout after
    proType+commandType: [0x10, uid(8), soft(3), hard(3)]."""
    if len(frame) < 17 or frame[0] != 0xAA or frame[1] != 0x07 or frame[2] != 0x10:
        return None
    return _uid_to_serial(frame[3:11]), _version(frame[11:14]), _version(frame[14:17])


def parse_sn(frame: bytes) -> str | None:
    """Parse an SnController response (aa 07 02): an 8-byte UID formatted as
    colon-hex, reversed (toAddressBytes z5=false), with a leading "00:00:"
    stripped. Returns None for an all-zero/invalid UID."""
    if len(frame) < 11 or frame[0] != 0xAA or frame[1] != 0x07 or frame[2] != 0x02:
        return None
    return _uid_to_serial(frame[3:11])


def parse_power(frame: bytes) -> bool | None:
    """aa 01 reply -> on/off (SwitchController: data[0] != 0)."""
    if len(frame) < 3 or frame[0] != 0xAA or frame[1] != 0x01:
        return None
    return frame[2] != 0


def parse_brightness(frame: bytes) -> int | None:
    """aa 04 reply -> brightness (BrightnessController: data[0]). The light
    families we poll use a 0-100 scale (same as the write path); if a device
    ever reports 0-255 it's rescaled."""
    if len(frame) < 3 or frame[0] != 0xAA or frame[1] != 0x04:
        return None
    value = frame[2]
    return round(value / 255 * 100) if value > 100 else value


def parse_active_scene(frame: bytes) -> int | None:
    """From a mode-read reply (aa 05 <subMode> <data>): the active scene code if
    the device is in scene sub-mode (0x04), else None. The code is
    little-endian (matches the 33 05 04 <lo> <hi> write)."""
    if len(frame) < 5 or frame[0] != 0xAA or frame[1] != 0x05 or frame[2] != 0x04:
        return None
    return frame[3] | (frame[4] << 8)


def parse_status(chunks: dict[int, bytes], address: str | None = None) -> DeviceState:
    """Build a DeviceState from reassembled 0xAC status chunks.

    Zone truth table (chunk 0x00 present -> shift 0), captured live:
      byte 14 = LOWER zone on, byte 15 = UPPER zone on (in the terminator chunk,
      0x05 in the full query else 0xFF). brightness is chunk 0x00 byte 10.
    When chunk 0x00 is absent (RGB/color-temp mode omits it) every offset in the
    terminator shifts by 1. When `address` is given, wifi_mac + hardware_version
    are also extracted from the joined stream (how BLE-only devices report them).
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
            # byte 15 = power_index 0 (H60A6 main), byte 14 = power_index 1
            # (background). (Verified live: the earlier 14->main mapping was
            # reversed.)
            state.zone_power = {0: upper, 1: lower}

    if chunk00 is not None and len(chunk00) >= 16:
        state.brightness = chunk00[10]

    state.segments = parse_segments(chunks)
    rgb = _uniform_rgb(state.segments)
    if rgb is not None:
        state.rgb_color = rgb

    if lower is not None or upper is not None:
        state.is_on = bool(lower or upper)

    if address is not None:
        state.wifi_mac, state.hardware_version = _anchor_device_info(chunks, address)
    return state
