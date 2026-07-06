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


def _frame(*payload: int) -> bytes:
    f = bytes(payload)
    return f + b"\x00" * (20 - len(f))


def test_parse_wifi_info() -> None:
    """aa 07 11 -> (wifi_mac forward-order, software X.YY.ZZ, hardware X.YY.ZZ)."""
    frame = _frame(0xAA, 0x07, 0x11, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 1, 2, 30, 1, 4, 3)
    assert status.parse_wifi_info(frame) == ("11:22:33:44:55:66", "1.02.30", "1.04.03")
    assert status.parse_wifi_info(_frame(0x33, 0x05, 0x11)) is None  # wrong opcode


def test_parse_sn() -> None:
    """aa 07 02 -> 8-byte UID reversed to colon-hex, leading 00:00: stripped."""
    frame = _frame(0xAA, 0x07, 0x02, 0x88, 0x77, 0x66, 0x55, 0x44, 0x33, 0x22, 0x11)
    assert status.parse_sn(frame) == "11:22:33:44:55:66:77:88"
    # leading 00:00 (reversed -> trailing zero bytes) is stripped
    stripped = _frame(0xAA, 0x07, 0x02, 0x88, 0x77, 0x66, 0x55, 0x44, 0x33, 0x00, 0x00)
    assert status.parse_sn(stripped) == "33:44:55:66:77:88"
    assert status.parse_sn(_frame(0xAA, 0x07, 0x02, 0, 0, 0, 0, 0, 0, 0, 0)) is None  # all-zero
    assert status.parse_sn(_frame(0x33, 0x05)) is None  # wrong opcode


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
