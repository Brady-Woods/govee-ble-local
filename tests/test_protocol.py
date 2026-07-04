#!/usr/bin/env python3
"""Protocol tests for govee-ble-local.

Run directly:  python3 tests/test_protocol.py
Or:            python3 -m pytest tests/

Covers the pure protocol layer (crypto, framing, command builders, parsers)
using real captured fixtures. Requires only `cryptography` — not `bleak` —
because `govee_ble_local.protocol` imports no Bluetooth stack and the package
`__init__` loads the client lazily.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

# Make the src-layout package importable without an editable install.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from govee_ble_local import const, protocol as p  # noqa: E402
from govee_ble_local.models import GoveeBleSegment  # noqa: E402

# --- real captured status fixtures ---------------------------------------

# Device 5C:E7:53:F4:74:57, scene mode (chunk 0x00 present).
STATUS_SCENE_MODE = {
    0xFF: bytes.fromhex("0000800000008041020201300201010000"),
    0x01: bytes.fromhex("07065774f453e75c0711105674f453e75c"),
    0x02: bytes.fromhex("db2d0100290104030000070d115ce753f4"),
    0x03: bytes.fromhex("74560100290104031104001e0f0f1207ff"),
    0x04: bytes.fromhex("640000800f002310000000800000008000"),
}
# Same device, RGB/color mode: chunk 0x00 absent, everything shifts by one.
STATUS_RGB_MODE = {
    0xFF: bytes.fromhex("0000008000000080410202013002010100"),
    0x01: bytes.fromhex("0707065774f453e75c0711105674f453e7"),
    0x02: bytes.fromhex("5cdb2d0100290104030000070d115ce753"),
    0x03: bytes.fromhex("f474560100290104031104001e0f0f1207"),
    0x04: bytes.fromhex("ff640000800f0023100000008000000080"),
}
# Device D4:13:68:21:D0:75, fuller query with per-segment chunks (solid green).
STATUS_WITH_SEGMENTS = {
    0xFF: bytes.fromhex("002900ff00a505043200ff000000000000"),
    0x00: bytes.fromhex("0a000c0300010101040101050415010000"),
    0x01: bytes.fromhex("07070675d0216813d407111074d0216813"),
    0x02: bytes.fromhex("d478040100290104030000070d11d41368"),
    0x03: bytes.fromhex("21d0740100290104031104001e0f0f1207"),
    0x04: bytes.fromhex("00640000800f0023100000008000000080"),
    0x05: bytes.fromhex("00000080000000804102020130020001a5"),
    0x06: bytes.fromhex("11013200ff003c00ff004600ff005100ff"),
    0x07: bytes.fromhex("00a511025a00ff006400ff000100ff0005"),
    0x08: bytes.fromhex("00ff00a511030a00ff001300ff001d00ff"),
}
SEGMENTS_EXPECTED = [
    (50, 0, 255, 0), (60, 0, 255, 0), (70, 0, 255, 0), (81, 0, 255, 0),
    (90, 0, 255, 0), (100, 0, 255, 0), (1, 0, 255, 0), (5, 0, 255, 0),
    (10, 0, 255, 0), (19, 0, 255, 0), (29, 0, 255, 0), (41, 0, 255, 0),
]


class TestFraming(unittest.TestCase):
    def test_checksum_is_xor(self):
        body = bytes([0x33, 0x30, 0x01, 0x01]) + b"\x00" * 15
        expected = 0
        for b in body:
            expected ^= b
        self.assertEqual(p.checksum(body), bytes([expected]))

    def test_build_plaintext_pads_and_checksums(self):
        pt = p.build_plaintext(bytes([0x33, 0x04, 50]))
        self.assertEqual(len(pt), 20)
        self.assertEqual(pt[:3], bytes([0x33, 0x04, 50]))
        self.assertEqual(pt[3:19], b"\x00" * 16)
        x = 0
        for b in pt[:19]:
            x ^= b
        self.assertEqual(pt[19], x)


class TestEncryption(unittest.TestCase):
    KEY = b"0123456789abcdef"

    def test_aes_round_trip(self):
        block = b"the 16 byte data"
        ct = p.aes_ecb(self.KEY, block, True)
        self.assertEqual(p.aes_ecb(self.KEY, ct, False), block)
        self.assertNotEqual(ct, block)

    def test_rc4_symmetric(self):
        data = b"four"
        self.assertEqual(p.rc4(self.KEY, p.rc4(self.KEY, data)), data)

    def test_full_packet_round_trip(self):
        pt = p.build_plaintext(bytes([0x33, 0x30, 0x01, 0x01]))
        ct = p.encrypt_packet(self.KEY, pt)
        self.assertEqual(p.decrypt_packet(self.KEY, ct), pt)
        self.assertEqual(len(ct), 20)


class TestCommandBuilders(unittest.TestCase):
    def test_set_zone(self):
        self.assertEqual(p.cmd_set_zone(const.ZONE_UPPER, True), bytes([0x33, 0x30, 1, 1]))
        self.assertEqual(p.cmd_set_zone(const.ZONE_LOWER, False), bytes([0x33, 0x30, 0, 0]))

    def test_set_brightness_clamps(self):
        self.assertEqual(p.cmd_set_brightness(50), bytes([0x33, 0x04, 50]))
        self.assertEqual(p.cmd_set_brightness(999)[2], 100)
        self.assertEqual(p.cmd_set_brightness(-5)[2], 0)

    def test_set_rgb_layout(self):
        cmd = p.cmd_set_rgb(10, 20, 30)
        self.assertEqual(cmd[:4], bytes([0x33, 0x05, 0x15, 0x01]))
        self.assertEqual(cmd[4:7], bytes([10, 20, 30]))
        self.assertEqual(cmd[-2:], bytes([0xFF, 0x1F]))

    def test_set_color_temp_clamps_and_encodes_kelvin(self):
        cmd = p.cmd_set_color_temp(4000)
        self.assertEqual(cmd[:7], bytes([0x33, 0x05, 0x15, 0x01, 0xFF, 0xFF, 0xFF]))
        self.assertEqual((cmd[7] << 8) | cmd[8], 4000)
        # clamp above max
        hot = p.cmd_set_color_temp(99999)
        self.assertEqual((hot[7] << 8) | hot[8], const.MAX_COLOR_TEMP_KELVIN)

    def test_set_segment_color_mask_placement(self):
        cmd = p.cmd_set_segment_color(1 << 11, 255, 0, 0)
        self.assertEqual(cmd[:4], bytes([0x33, 0x05, 0x15, 0x01]))
        self.assertEqual(cmd[4:7], bytes([255, 0, 0]))
        self.assertEqual(cmd[12], (1 << 11) & 0xFF)
        self.assertEqual(cmd[13], ((1 << 11) >> 8) & 0xFF)

    def test_set_segment_brightness_subopcode_and_mask(self):
        cmd = p.cmd_set_segment_brightness(0x0005, 64)
        self.assertEqual(cmd[:4], bytes([0x33, 0x05, 0x15, 0x02]))
        self.assertEqual(cmd[4], 64)
        self.assertEqual(cmd[5], 0x05)
        self.assertEqual(cmd[6], 0x00)

    def test_set_scene(self):
        self.assertEqual(p.cmd_set_scene((0x82, 0x4A)), bytes([0x33, 0x05, 0x04, 0x82, 0x4A]))

    def test_status_and_metadata_queries(self):
        self.assertEqual(p.cmd_status_query(), bytes([0xAC, 0x03, 0x02, 0x41, 0x30]))
        self.assertEqual(p.cmd_status_query_full(), bytes([0xAC, 0x03, 0x03, 0x41, 0x30, 0xA5]))
        self.assertEqual(p.cmd_metadata_field(0x05), bytes([0xAB, 0x01, 0x05]))

    def test_set_rgb_h6006_color_scheme(self):
        # H6006's alternate layout: no mode byte/mask/checksum-tail dance,
        # just the opcode plus raw RGB - PROTOCOL.md §12.2.
        self.assertEqual(p.cmd_set_rgb(255, 0, 0, "h6006"), bytes([0x33, 0x05, 0x0D, 0xFF, 0x00, 0x00]))

    def test_set_color_temp_h6006_color_scheme(self):
        cmd = p.cmd_set_color_temp(2700, "h6006")
        ar, ag, ab = p.kelvin_to_rgb(2700)
        self.assertEqual(cmd[:3], bytes([0x33, 0x05, 0x0D]))
        self.assertEqual(cmd[3:6], bytes([ar, ag, ab]))
        self.assertEqual((cmd[6] << 8) | cmd[7], 2700)
        self.assertEqual(cmd[8:11], bytes([ar, ag, ab]))  # tint repeated


class TestKelvinToRgb(unittest.TestCase):
    def test_reference_points_within_tolerance(self):
        for kelvin, ref in ((2700, (255, 167, 87)), (6500, (255, 254, 250))):
            r, g, b = p.kelvin_to_rgb(kelvin)
            for got, want in zip((r, g, b), ref):
                self.assertLessEqual(abs(got - want), 3)


class TestBuildSceneChunks(unittest.TestCase):
    def test_flag_bit_set_and_chunking(self):
        import base64

        raw = bytes([0x50]) + bytes(range(40))  # byte0=0x50 -> should become 0x58
        chunks = p.build_scene_chunks(base64.b64encode(raw).decode())
        self.assertTrue(all(c[0] == 0xA3 for c in chunks))
        self.assertEqual(chunks[-1][1], 0xFF)  # last seq is 0xFF
        # content = [0x01, chunk_count] + data; data[0] has the 0x08 flag set.
        self.assertEqual(chunks[0][2], 0x01)
        self.assertEqual(chunks[0][4], 0x58)  # 0x50 | 0x08


class TestParseStatus(unittest.TestCase):
    def test_scene_mode_mac_and_hw(self):
        st = p.parse_status("5C:E7:53:F4:74:57", STATUS_SCENE_MODE)
        self.assertEqual(st.ble_mac, "5C:E7:53:F4:74:57")
        self.assertEqual(st.wifi_mac, "5C:E7:53:F4:74:56")
        self.assertEqual(st.hardware_version, "1.04.03")

    def test_rgb_mode_mac_not_corrupted(self):
        st = p.parse_status("5C:E7:53:F4:74:57", STATUS_RGB_MODE)
        self.assertEqual(st.ble_mac, "5C:E7:53:F4:74:57")
        self.assertEqual(st.wifi_mac, "5C:E7:53:F4:74:56")
        self.assertEqual(st.hardware_version, "1.04.03")

    def test_zone_truth_table(self):
        # byte 14 = lower, byte 15 = upper (byte 13 static 0x02); 4-state
        # truth table captured live on two devices.
        base = bytes.fromhex("00000080000000804102020130020000a5")
        for upper, lower in ((0, 0), (1, 0), (0, 1), (1, 1)):
            term = bytearray(base)
            term[14] = lower
            term[15] = upper
            chunks = {0x00: bytes(17), 0x05: bytes(term)}
            st = p.parse_status("AA:BB:CC:DD:EE:FF", chunks)
            self.assertEqual(st.zone_upper_on, bool(upper), f"upper U={upper} L={lower}")
            self.assertEqual(st.zone_lower_on, bool(lower), f"lower U={upper} L={lower}")

    def test_segments_populated(self):
        st = p.parse_status("D4:13:68:21:D0:75", STATUS_WITH_SEGMENTS)
        self.assertIsNotNone(st.segments)
        self.assertEqual(len(st.segments), 12)
        for i, (expected, seg) in enumerate(zip(SEGMENTS_EXPECTED, st.segments)):
            self.assertEqual((seg.brightness_pct, seg.r, seg.g, seg.b), expected, f"segment {i}")

    def test_segments_none_without_fuller_chunks(self):
        st = p.parse_status("5C:E7:53:F4:74:57", STATUS_SCENE_MODE)
        self.assertIsNone(st.segments)

    def test_rgb_color_read_back_from_uniform_segments(self):
        # Real capture: device set to solid green via set_rgb_color(0,255,0).
        # All segments report (0,255,0), so the solid color IS readable via the
        # fuller query's per-segment data.
        st = p.parse_status("D4:13:68:21:D0:75", STATUS_WITH_SEGMENTS)
        self.assertEqual(st.rgb_color, (0, 255, 0))

    def test_rgb_color_none_without_segments(self):
        # Short query -> no segments -> no color read-back.
        st = p.parse_status("5C:E7:53:F4:74:57", STATUS_SCENE_MODE)
        self.assertIsNone(st.rgb_color)

    def test_empty_chunks_all_none(self):
        st = p.parse_status("5C:E7:53:F4:74:57", {})
        for val in (st.ble_mac, st.wifi_mac, st.hardware_version, st.brightness_pct,
                    st.scene_id, st.zone_upper_on, st.zone_lower_on, st.segments):
            self.assertIsNone(val)

    def test_wrong_address_no_mac(self):
        st = p.parse_status("AA:BB:CC:DD:EE:FF", STATUS_SCENE_MODE)
        self.assertIsNone(st.ble_mac)


class TestParseSegmentRecords(unittest.TestCase):
    def test_real_capture(self):
        segs = p.parse_segment_records(STATUS_WITH_SEGMENTS)
        self.assertIsNotNone(segs)
        self.assertEqual(len(segs), 12)
        self.assertEqual(segs[0], GoveeBleSegment(0, 50, 0, 255, 0))
        self.assertEqual(segs[11], GoveeBleSegment(11, 41, 0, 255, 0))

    def test_missing_chunk_returns_none(self):
        partial = {k: v for k, v in STATUS_WITH_SEGMENTS.items() if k != 0x07}
        self.assertIsNone(p.parse_segment_records(partial))


class TestParseSegmentPages(unittest.TestCase):
    """H61A8-style paginated per-segment status (`aa a5 <page>`) - same
    record shape as parse_segment_records, different outer framing."""

    def test_real_shaped_pages(self):
        pages = {
            1: bytes([100, 255, 0, 0, 90, 0, 255, 0, 80, 0, 0, 255, 0, 0, 0, 0]),
            2: bytes([70, 255, 255, 0, 60, 255, 127, 0, 50, 139, 0, 255, 100, 255, 255, 255]),
        }
        segs = p.parse_segment_pages(pages)
        self.assertIsNotNone(segs)
        self.assertEqual(len(segs), 8)
        self.assertEqual(segs[0], GoveeBleSegment(0, 100, 255, 0, 0))
        self.assertEqual(segs[4], GoveeBleSegment(4, 70, 255, 255, 0))
        self.assertEqual(segs[7], GoveeBleSegment(7, 100, 255, 255, 255))

    def test_missing_or_short_page_returns_none(self):
        self.assertIsNone(p.parse_segment_pages({}))
        self.assertIsNone(p.parse_segment_pages({1: bytes(10)}))  # too short (< 16 bytes)


class TestParseMetadataFieldText(unittest.TestCase):
    def test_real_serial_capture(self):
        # field 0x05 response: 5-byte header + ASCII serial across two chunks.
        seq0 = bytes.fromhex("0200040105" + "463139313330353635464537")
        ff = bytes.fromhex("34314146" + "00" * 13)
        self.assertEqual(p.parse_metadata_field_text(seq0 + ff), "F19130565FE741AF")

    def test_empty_returns_none(self):
        self.assertIsNone(p.parse_metadata_field_text(b"\x00" * 5))


class TestFormatMac(unittest.TestCase):
    def test_format(self):
        self.assertEqual(p.format_mac(bytes([0x5C, 0xE7, 0x53, 0xF4, 0x74, 0x57])), "5C:E7:53:F4:74:57")


if __name__ == "__main__":
    unittest.main(verbosity=2)
