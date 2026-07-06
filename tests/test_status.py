"""Tests for the H60A6 status read-back parser (ble/status.py)."""
from __future__ import annotations

from govee_ble_local.ble import controllers, status


def test_status_query_frames() -> None:
    short = controllers.status_query(full=False)
    full = controllers.status_query(full=True)
    assert short[:5].hex() == "ac03024130"
    assert full[:6].hex() == "ac03034130a5"
    assert len(short) == 20 and len(full) == 20


def test_parse_status_brightness_and_zones() -> None:
    # chunk 0x00 present -> shift 0; byte10 = brightness; terminator 0xFF
    # byte14 = lower zone, byte15 = upper zone.
    chunk00 = bytes([0] * 10 + [40] + [0] * 5)          # brightness = 40%
    terminator = bytes([0] * 14 + [0, 1])                # lower off, upper on
    st = status.parse_status({0x00: chunk00, 0xFF: terminator})
    assert st.brightness == 40
    assert st.is_on is True                               # any zone on
    assert st.segments == []
    assert st.rgb_color is None


def test_parse_status_all_zones_off() -> None:
    chunk00 = bytes([0] * 16)
    terminator = bytes([0] * 16)                          # both zones off
    st = status.parse_status({0x00: chunk00, 0xFF: terminator})
    assert st.is_on is False


def test_parse_metadata_text_serial() -> None:
    """A metadata (ab) response decodes to its ASCII value after the 5-byte
    header."""
    body = b"\x00\x00\x00\x00\x00" + b"1AB2C3D4E5"  # 5-byte header + ascii serial
    frame = bytes([0xAB, 0xFF]) + body[:17]
    frame = frame + b"\x00" * (20 - len(frame))
    assert status.parse_metadata_text([frame]) == "1AB2C3D4E5"
    assert status.parse_metadata_text([]) is None
    assert status.parse_metadata_text([bytes([0x33] + [0] * 19)]) is None  # wrong opcode


def test_parse_device_info_wifi_mac_and_hw() -> None:
    """wifi_mac + hardware_version are anchored on the device's own BLE MAC
    (little-endian) in the joined 0x01-0x04 stream."""
    address = "AA:BB:CC:DD:EE:05"
    own_le = bytes.fromhex("05EEDDCCBBAA")            # reversed own MAC
    wifi_le = bytes.fromhex("112233445566")            # wifi MAC stored little-endian
    # anchor at offset 3; +9..15 wifi, +20..23 hw = (1, 2, 30)
    blob = b"\x00\x00\x00" + own_le + b"\x00\x00\x00" + wifi_le + b"\x00\x00\x00\x00\x00" + bytes([1, 2, 30])
    # split into chunks 0x01..0x04 (17 bytes each) — one chunk is enough here
    chunks = {0x00: bytes(16), 0x01: blob[:17], 0x02: blob[17:34], 0x05: bytes(16)}
    st = status.parse_status(chunks, address)
    assert st.wifi_mac == "66:55:44:33:22:11"
    assert st.hardware_version == "1.02.30"


def test_parse_segments_uniform_rgb() -> None:
    # 12 records of [brightness, r, g, b], all red -> uniform rgb.
    record = bytes([50, 255, 0, 0])
    stream = bytearray(b"\x00" * 19)                      # 19-byte header
    for group in range(3):
        stream += record * 4                              # 4 records
        if group < 2:
            stream += b"\x00\x00\x00"                     # inter-group marker
    # split into 17-byte chunk bodies for tags 0x05..0x08, 0xFF
    stream = bytes(stream)
    tags = (0x05, 0x06, 0x07, 0x08, 0xFF)
    chunks = {tags[i]: stream[i * 17 : (i + 1) * 17] for i in range(len(tags))}
    chunks[0x00] = bytes([0] * 16)                        # present -> shift 0
    st = status.parse_status(chunks)
    assert len(st.segments) == 12
    assert all(s.rgb == (255, 0, 0) and s.brightness == 50 for s in st.segments)
    assert st.rgb_color == (255, 0, 0)
