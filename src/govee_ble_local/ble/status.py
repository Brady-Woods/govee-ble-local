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

def parse_segments(chunks: dict[int, bytes]) -> list[Segment]:
    """Per-segment colour from the reassembled 0xAC status burst (mechanism A —
    H60A6 / H6047 / H6641).

    The reassembled blob is a TLV stream; segment colour comes in **0xA5 groups**:
    ``[0xA5, len, group_index, records…]`` where each record is 4 bytes
    ``[brightness, R, G, B]``. Groups are 1-indexed and carry 4 records each except
    a final partial group, so segment *i* sits at ``4*(group_index-1)+k``. The TLV
    is self-delimiting (record count = ``(len-1)//4``), so this needs no fixed layout
    or per-SKU count — the H60A6 yields all 13 (groups 4,4,4,1), unlike the old fixed
    ``3×4=12`` walk which dropped the 13th. Returns [] if no colour group is present.

    NOTE: mechanism B (H61A8, 0xAA-notify BulbGroupColor: 0xA2=3-byte / 0xA5=4-byte)
    and C (H6052, 0x0D mode-report) are different transports — not handled here.
    """
    if not chunks:
        return []
    # Reassemble all chunk bodies in tag order (0xFF sorts last) so a group that
    # spans a chunk boundary stays contiguous.
    blob = b"".join(chunks[t] for t in sorted(chunks))
    return _parse_color_groups(blob)


def _parse_color_groups(blob: bytes) -> list[Segment]:
    """Walk the 0xA5 colour-group TLV run in a reassembled status blob."""
    n = len(blob)
    # Anchor on the first valid group (type 0xA5, len = 1+4k, group_index == 1),
    # skipping the device-info TLVs that precede it.
    i = 0
    while i + 2 < n:
        ln = blob[i + 1]
        if blob[i] == 0xA5 and 1 <= ln and i + 2 + ln <= n and (ln - 1) % 4 == 0 and blob[i + 2] == 1:
            break
        i += 1
    else:
        return []
    segs: dict[int, Segment] = {}
    while i + 2 <= n and blob[i] == 0xA5:
        ln = blob[i + 1]
        val = blob[i + 2 : i + 2 + ln]
        if len(val) < ln or ln < 1:
            break
        group_index = val[0]
        records = val[1:]
        for k in range(len(records) // 4):
            brightness, r, g, b = records[k * 4 : k * 4 + 4]
            idx = 4 * (group_index - 1) + k
            segs[idx] = Segment(idx, (r, g, b), brightness)
        i += 2 + ln
    return [segs[k] for k in sorted(segs)]


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


def parse_bar_switch(frame: bytes) -> tuple[bool, bool] | None:
    """aa 36 reply -> (left_on, right_on) for the H6047's two bars.

    byte[2] = left, byte[3] = right (H6048OnOffNotifyParse; verified live:
    33 36 00 01 -> aa 36 00 01, 33 36 01 00 -> aa 36 01 00)."""
    if len(frame) < 4 or frame[0] != 0xAA or frame[1] != 0x36:
        return None
    return frame[2] == 1, frame[3] == 1


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


def _zone_bits(terminator: bytes) -> tuple[int, int] | None:
    """Locate the zone on/off TLV ``30 02 <zone0> <zone1> a5`` in the status
    terminator chunk and return (zone0, zone1). Anchored on the 0xA5 stream marker
    that always follows the 2-byte value, so it's robust to the offset drifting with
    device mode. Returns None if the TLV isn't present."""
    start = 0
    while True:
        i = terminator.find(b"\x30\x02", start)
        if i == -1:
            return None
        if i + 4 < len(terminator) and terminator[i + 4] == 0xA5:
            return terminator[i + 2], terminator[i + 3]
        start = i + 1


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

    # Zone on/off is a TLV in the status stream: [0x30, 0x02, zone0, zone1] followed
    # by the 0xA5 stream marker. Anchor on that TLV rather than a fixed byte offset:
    # the offset drifts with device mode (whether chunk 0x00 is present) and a fixed
    # read can land on the 0xA5 marker, reporting a zone as permanently on. zone_power
    # keys match the 33 30 <index> command order (index 0 -> zone0), so set_zone_power
    # and zone_is_on agree. (Verified byte-exact against H60A6 captures: power-off ->
    # both bytes 0; per-zone toggles flip the matching byte.)
    z0 = z1 = None
    if terminator is not None:
        zbytes = _zone_bits(terminator)
        if zbytes is not None:
            z0, z1 = zbytes
            state.zone_power = {0: bool(z0), 1: bool(z1)}

    if chunk00 is not None and len(chunk00) >= 16:
        state.brightness = chunk00[10]

    state.segments = parse_segments(chunks)
    rgb = _uniform_rgb(state.segments)
    if rgb is not None:
        state.rgb_color = rgb

    if z0 is not None or z1 is not None:
        state.is_on = bool(z0 or z1)

    if address is not None:
        state.wifi_mac, state.hardware_version = _anchor_device_info(chunks, address)
    return state
