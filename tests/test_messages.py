#!/usr/bin/env python3
"""Tests for the unified message codec (govee_ble_local.messages).

Run:  python3 -m pytest tests/    (or: python3 tests/test_messages.py)

Covers the single encode+decode source of truth: that builders are
byte-identical to the historic protocol.cmd_* API, that decode round-trips,
that sendability is correctly gated (stubs/receive-only can't be built), and
that the receive-side dispatch drops what it doesn't understand without ever
surfacing WiFi-provisioning content.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from govee_ble_local import messages as m  # noqa: E402
from govee_ble_local import protocol as p  # noqa: E402
from govee_ble_local.models import GoveeBleStatus  # noqa: E402


def _frame(prefix: bytes) -> bytes:
    """Pad + checksum a command prefix into a full 20-byte frame."""
    return p.build_plaintext(prefix)


class TestBuildersMatchLegacyApi(unittest.TestCase):
    """build_* is the encode source of truth; protocol.cmd_* delegates to it.
    Both must stay byte-identical (and match hand-checked literals)."""

    def test_literal_layouts(self) -> None:
        self.assertEqual(m.build_brightness(50), bytes([0x33, 0x04, 50]))
        self.assertEqual(m.build_power(True), bytes([0x33, 0x01, 1]))
        self.assertEqual(m.build_zone(1, True), bytes([0x33, 0x30, 1, 1]))
        self.assertEqual(m.build_scene((0x82, 0x4A)), bytes([0x33, 0x05, 0x04, 0x82, 0x4A]))
        self.assertEqual(m.build_status_query(full=False), bytes([0xAC, 0x03, 0x02, 0x41, 0x30]))
        self.assertEqual(m.build_status_query(full=True), bytes([0xAC, 0x03, 0x03, 0x41, 0x30, 0xA5]))

    def test_calibration_layouts(self) -> None:
        self.assertEqual(m.build_calibration_enter(), bytes([0x33, 0x42, 0x01]))
        self.assertEqual(m.build_calibration_rotate(m.CALIBRATION_CW), bytes([0x33, 0x42, 0x02, 0x01]))
        self.assertEqual(m.build_calibration_rotate(m.CALIBRATION_CCW), bytes([0x33, 0x42, 0x02, 0x02]))
        self.assertEqual(m.build_calibration_confirm(), bytes([0x33, 0x42, 0xFF]))
        self.assertEqual(m.build_calibration_exit(), bytes([0x33, 0x42, 0x00]))

    def test_delegation_is_byte_identical(self) -> None:
        self.assertEqual(m.build_brightness(60), p.cmd_set_brightness(60))
        self.assertEqual(m.build_zone(0, False), p.cmd_set_zone(0, False))
        self.assertEqual(m.build_rgb(10, 20, 30), p.cmd_set_rgb(10, 20, 30))
        self.assertEqual(m.build_color_temp(4000), p.cmd_set_color_temp(4000))
        self.assertEqual(m.build_segment_color(0x0020, 1, 2, 3), p.cmd_set_segment_color(0x0020, 1, 2, 3))
        self.assertEqual(m.build_segment_brightness(0x0020, 50), p.cmd_set_segment_brightness(0x0020, 50))
        self.assertEqual(m.build_scene((0x01, 0x0A)), p.cmd_set_scene((0x01, 0x0A)))
        self.assertEqual(m.build_metadata_query(0x05), p.cmd_metadata_field(0x05))
        self.assertEqual(m.build_handshake(0x01), p.cmd_handshake(0x01))
        # color_scheme passthrough (H6006's alternate layout) delegates identically too.
        self.assertEqual(m.build_rgb(10, 20, 30, "h6006"), p.cmd_set_rgb(10, 20, 30, "h6006"))
        self.assertEqual(m.build_color_temp(4000, "h6006"), p.cmd_set_color_temp(4000, "h6006"))


class TestH6006ColorScheme(unittest.TestCase):
    """H6006's alternate `33 05 0D` color/color-temp layout - structure
    confirmed byte-exact against real capture data (PROTOCOL.md §12.2,
    devices/h6006/captures/2026-07-03_manual-test_annotated.log). Default
    (color_scheme="h60a6") stays untouched - covered by every other test in
    this file that calls build_rgb/build_color_temp with no scheme arg."""

    def test_rgb_layout(self) -> None:
        self.assertEqual(m.build_rgb(255, 0, 0, "h6006"), bytes([0x33, 0x05, 0x0D, 0xFF, 0x00, 0x00]))
        self.assertEqual(m.build_rgb(0, 255, 0, "h6006"), bytes([0x33, 0x05, 0x0D, 0x00, 0xFF, 0x00]))

    def test_color_temp_layout(self) -> None:
        # Structure: tint RGB, 2-byte kelvin, tint RGB repeated. Tint values
        # come from the same kelvin_to_rgb approximation h60a6 uses (a
        # pre-existing, accepted gap vs. the real app's exact tint table -
        # see PROTOCOL.md §4.1), not a literal capture-byte match.
        ar, ag, ab = p.kelvin_to_rgb(2700)
        expected = bytes([0x33, 0x05, 0x0D, ar, ag, ab, (2700 >> 8) & 0xFF, 2700 & 0xFF, ar, ag, ab])
        self.assertEqual(m.build_color_temp(2700, "h6006"), expected)

    def test_default_scheme_unchanged(self) -> None:
        self.assertEqual(m.build_rgb(1, 2, 3), bytes([0x33, 0x05, 0x15, 0x01, 1, 2, 3, 0, 0, 0, 0, 0, 0xFF, 0x1F]))


class TestProtocol(unittest.TestCase):
    def test_default_matches_legacy_h60a6_behavior(self) -> None:
        self.assertEqual(m.Protocol(), m.Protocol("aes_rc4_psk", "h60a6", "full"))

    def test_all_known_combos_construct(self) -> None:
        for combo in m.KNOWN_PROTOCOL_COMBOS:
            m.Protocol(*combo)  # must not raise

    def test_unimplemented_combo_rejected(self) -> None:
        with self.assertRaises(ValueError):
            m.Protocol("none", "h60a6", "full")

    def test_power_scheme_defaults_binary(self) -> None:
        self.assertEqual(m.Protocol().power_scheme, "binary")

    def test_plug_relay_requires_a_registered_combo(self) -> None:
        # H5083's real combo (handshake_only/h6006/none/plug_relay) must be
        # registered; an arbitrary combo pairing plug_relay with something
        # else not yet confirmed live should still be rejected.
        m.Protocol("handshake_only", "h6006", "none", "plug_relay")  # must not raise
        with self.assertRaises(ValueError):
            m.Protocol("aes_rc4_psk", "h60a6", "full", "plug_relay")

    def test_segment_status_page_count(self) -> None:
        self.assertEqual(m.segment_status_page_count(20), 5)
        self.assertEqual(m.segment_status_page_count(12), 3)
        self.assertEqual(m.segment_status_page_count(1), 1)
        self.assertEqual(m.segment_status_page_count(0), 0)


class TestDeserializeRoundTrip(unittest.TestCase):
    def test_brightness(self) -> None:
        msg = m.deserialize(_frame(m.build_brightness(60)), "WRITE")
        self.assertEqual(msg.name, "brightness")
        self.assertTrue(msg.understood and msg.sendable)
        self.assertEqual(msg.fields["pct"], 60)

    def test_power_and_zone(self) -> None:
        pwr = m.deserialize(_frame(m.build_power(True)), "WRITE")
        self.assertEqual((pwr.name, pwr.fields["on"]), ("power", True))
        zone = m.deserialize(_frame(m.build_zone(1, False)), "WRITE")
        self.assertEqual((zone.name, zone.fields["zone"], zone.fields["on"]), ("zone", 1, False))

    def test_power_plug_relay_scheme(self) -> None:
        # H5083's smart-plug power encoding (0x10/0x11, not 0x00/0x01) -
        # PROTOCOL.md §15.3. build_power(..., "plug_relay") round-trips
        # through deserialize correctly, and doesn't get misread as truthy
        # by a naive bool(state) (0x10 is truthy but must decode as OFF).
        off = m.build_power(False, "plug_relay")
        on = m.build_power(True, "plug_relay")
        self.assertEqual(off, bytes([0x33, 0x01, 0x10]))
        self.assertEqual(on, bytes([0x33, 0x01, 0x11]))
        off_msg = m.deserialize(_frame(off), "WRITE")
        on_msg = m.deserialize(_frame(on), "WRITE")
        self.assertEqual((off_msg.name, off_msg.fields["on"]), ("power", False))
        self.assertEqual((on_msg.name, on_msg.fields["on"]), ("power", True))

    def test_scene_and_color_temp(self) -> None:
        sc = m.deserialize(_frame(m.build_scene((0x7B, 0x00))), "WRITE")
        self.assertEqual((sc.name, sc.fields["code"]), ("scene_activate", 0x7B))
        # Round-trip the H6006-style 0x0D color-temp frame (decode side).
        ct = m.deserialize(_frame(bytes([0x33, 0x05, 0x0D, 0xFF, 0xAE, 0x54, 0x0A, 0x8C, 0xFF, 0xAE, 0x54])), "WRITE")
        self.assertEqual(ct.name, "color_temp")
        self.assertEqual(ct.fields["kelvin"], 0x0A8C)

    def test_calibration_direction(self) -> None:
        cw = m.deserialize(_frame(m.build_calibration_rotate(m.CALIBRATION_CW)), "WRITE")
        self.assertEqual(cw.fields["action"], "rotate")
        self.assertEqual(cw.fields["direction"], m.CALIBRATION_CW)
        self.assertIn("clockwise", cw.summary)

    def test_notify_ack_not_misread_as_value(self) -> None:
        # A NOTIFY 0x33 with all-zero payload is a bare ack, not "brightness 0".
        ack = m.deserialize(_frame(bytes([0x33, 0x04])), "NOTIFY")
        self.assertEqual(ack.name, "ack")
        self.assertEqual(ack.fields["cmd"], 0x04)
        self.assertFalse(ack.sendable)

    def test_segment_status_query_direction_gating(self) -> None:
        # WRITE side is the sendable trigger (mirrors status_query/metadata_query).
        query = m.deserialize(_frame(m.build_segment_status_query(3)), "WRITE")
        self.assertEqual(query.name, "segment_status_query")
        self.assertTrue(query.understood and query.sendable)
        self.assertEqual(query.fields["page"], 3)
        self.assertTrue(m.is_sendable("segment_status_query"))
        self.assertEqual(m.serialize("segment_status_query", 3), m.build_segment_status_query(3))
        # NOTIFY side (real per-page data) is understood but not independently sendable.
        body = bytes([0xA5, 3]) + bytes([100, 255, 0, 0, 90, 0, 255, 0, 80, 0, 0, 255, 0, 0, 0, 0]) + bytes(0)
        data = m.deserialize(_frame(bytes([0xAA]) + body), "NOTIFY")
        self.assertEqual(data.name, "segment_status_chunk")
        self.assertTrue(data.understood)
        self.assertFalse(data.sendable)


class TestClockSync(unittest.TestCase):
    """cmd 0x09 (most devices) / 0xB5 (H5083's smart-plug family) - see
    PROTOCOL.md §15.3. Confirmed real and, for H5083, actually required
    after every power command - not just a receive-only stub."""

    def test_decode_confirmed_timestamp(self) -> None:
        # This fixture's bytes (0x6A48AEDD, big-endian) decode to a real,
        # plausible unix timestamp (2026-07-04 06:57:33 UTC) - confirmed
        # against real H61A8 capture data as the phone pushing its current
        # wall-clock time to the device on connect.
        clock = m.deserialize(_frame(bytes([0x33, 0x09, 0x6A, 0x48, 0xAE, 0xDD, 0x01, 0xF9])), "WRITE")
        self.assertEqual(clock.name, "clock_sync")
        self.assertTrue(clock.understood)
        self.assertTrue(clock.sendable)
        self.assertTrue(m.is_sendable("clock_sync"))

    def test_decode_b5_opcode_same_as_09(self) -> None:
        # Same fixture bytes, H5083's cmd byte (0xB5 instead of 0x09) under
        # the same top-level 0x33 opcode.
        clock = m.deserialize(_frame(bytes([0x33, 0xB5, 0x6A, 0x48, 0xAE, 0xDD, 0x01, 0xF9])), "WRITE")
        self.assertEqual(clock.name, "clock_sync")
        self.assertTrue(clock.understood)
        self.assertEqual(clock.fields["cmd"], 0xB5)

    def test_build_default_opcode_09(self) -> None:
        frame = m.build_clock_sync()
        self.assertEqual(frame[0], 0x09)
        self.assertEqual(len(frame), 7)
        self.assertEqual(frame[5:7], bytes([0x01, 0xF9]))

    def test_build_b5_opcode_for_plug(self) -> None:
        frame = m.build_clock_sync(0xB5)
        self.assertEqual(frame[0], 0xB5)
        self.assertEqual(frame[5:7], bytes([0x01, 0xF9]))


class TestStubsAndGating(unittest.TestCase):
    def test_stubs_recognized_but_not_sendable(self) -> None:
        ee = m.deserialize(_frame(bytes([0xEE, 0x20, 0x0A])), "NOTIFY")
        a4 = m.deserialize(_frame(bytes([0xA4, 0x58, 0x00])), "NOTIFY")
        for msg, name in ((ee, "stub_ee"), (a4, "stub_a4")):
            self.assertEqual(msg.name, name)
            self.assertFalse(msg.understood, name)
            self.assertFalse(msg.sendable, name)
            self.assertFalse(m.is_sendable(name), name)

    def test_unknown_opcode(self) -> None:
        msg = m.deserialize(_frame(bytes([0x77, 0x01, 0x02])), "NOTIFY")
        self.assertEqual(msg.name, "unknown")
        self.assertFalse(msg.understood)

    def test_serialize_gates_non_sendable(self) -> None:
        self.assertEqual(m.serialize("brightness", 50), m.build_brightness(50))
        for name in ("wifi_provision", "status_field", "ack", "definitely_not_a_command"):
            with self.assertRaises(m.UnsupportedCommand):
                m.serialize(name)


class TestWifiProvisioningRedaction(unittest.TestCase):
    def test_content_never_surfaced(self) -> None:
        secret = b"MySSID__and__password"
        frame = _frame(bytes([0xA1, 0x11, 0x01]) + secret[:16])
        msg = m.deserialize(frame, "WRITE")
        self.assertEqual(msg.name, "wifi_provision")
        self.assertFalse(msg.understood or msg.sendable)
        self.assertIn("REDACTED", msg.summary)
        self.assertNotIn("MySSID", msg.summary)
        self.assertNotIn(secret[:6].decode(), msg.summary)

    def test_dispatch_incoming_drops_and_redacts(self) -> None:
        frame = _frame(bytes([0xA1, 0x11, 0x01]) + b"MySSID__secret__")
        msg = m.dispatch_incoming(frame, "NOTIFY")
        self.assertFalse(msg.understood)  # -> caller drops it
        self.assertNotIn("MySSID", msg.summary)

    def test_dispatch_incoming_drops_unknown(self) -> None:
        self.assertFalse(m.dispatch_incoming(_frame(bytes([0x77])), "NOTIFY").understood)
        self.assertFalse(m.dispatch_incoming(_frame(bytes([0xEE, 0x20])), "NOTIFY").understood)


class TestChunkReassembler(unittest.TestCase):
    def test_metadata_reassembled_via_real_parser(self) -> None:
        # Real serial (field 0x05) response from device D4:...:75 / F19130565FE741AF.
        reasm = m.ChunkReassembler("D4:13:68:21:D0:75")
        self.assertIsNone(reasm.feed("WRITE", _frame(m.build_metadata_query(0x05))))
        # Chunk bodies are 17 bytes each (frame = opcode + seq + 17 + checksum).
        # seq0 body: 5-byte header (02 00 04 01 05) + "F19130565FE7".
        body0 = bytes.fromhex("0200040105") + b"F19130565FE7"
        body_last = b"41AF" + b"\x00" * 13
        chunk0 = bytes([0xAB, 0x00]) + body0 + b"\x00"
        chunk_last = bytes([0xAB, 0xFF]) + body_last + b"\x00"
        self.assertEqual((len(chunk0), len(chunk_last)), (20, 20))
        self.assertIsNone(reasm.feed("NOTIFY", chunk0))
        result = reasm.feed("NOTIFY", chunk_last)
        assert result is not None
        self.assertEqual(result.name, "metadata")
        self.assertEqual(result.fields["field"], 0x05)
        self.assertEqual(result.fields["text"], "F19130565FE741AF")

    def test_status_reassembles_and_yields_structured_object(self) -> None:
        reasm = m.ChunkReassembler("5C:E7:53:F4:74:57")
        reasm.feed("WRITE", _frame(m.build_status_query(full=False)))
        result = None
        for tag in (0x00, 0x01, 0x02, 0x03, 0x04, 0xFF):
            body = bytes([tag]) + bytes(16)  # 17-byte chunk body
            result = reasm.feed("NOTIFY", bytes([0xAC, tag]) + body + b"\x00")
        assert result is not None
        self.assertEqual(result.name, "status")
        self.assertIsInstance(result.fields["status"], GoveeBleStatus)

    def test_flush_reports_incomplete(self) -> None:
        reasm = m.ChunkReassembler("5C:E7:53:F4:74:57")
        reasm.feed("NOTIFY", bytes([0xAC, 0x00]) + bytes(18))  # one lonely chunk
        notes = reasm.flush()
        self.assertTrue(any(not n.understood and "never completed" in n.summary for n in notes))

    def test_segment_pages_reassemble_via_real_parser(self) -> None:
        # H61A8-style paginated per-segment status: 5 pages of 4 records
        # each = 20 segments (PROTOCOL.md §13.1).
        reasm = m.ChunkReassembler("D1:C7:C2:06:63:1F", segment_pages=5)
        pages = {
            1: bytes([100, 255, 0, 0, 90, 0, 255, 0, 80, 0, 0, 255, 0, 0, 0, 0]),
            2: bytes([70, 255, 255, 0, 60, 255, 127, 0, 50, 139, 0, 255, 0, 0, 0, 0]),
            3: bytes([41, 0, 255, 0, 30, 0, 255, 255, 20, 255, 255, 255, 0, 0, 0, 0]),
            4: bytes([10, 139, 0, 255, 1, 0, 255, 255, 100, 0, 255, 0, 0, 0, 0, 0]),
            5: bytes([100, 255, 255, 0, 100, 255, 127, 0, 100, 255, 0, 0, 100, 0, 0, 255]),
        }
        result = None
        for page in sorted(pages):
            result = reasm.feed("NOTIFY", bytes([0xAA, 0xA5, page]) + pages[page] + bytes(1))
        assert result is not None
        self.assertEqual(result.name, "segment_status")
        segments = result.fields["segments"]
        self.assertEqual(len(segments), 20)
        self.assertEqual((segments[0].index, segments[0].brightness_pct, segments[0].r, segments[0].g, segments[0].b), (0, 100, 255, 0, 0))
        self.assertEqual((segments[19].index, segments[19].brightness_pct), (19, 100))

    def test_segment_pages_no_data_yet_returns_none(self) -> None:
        # A page whose body is all-zero is the query echo, not real data - the
        # reassembler must not treat it as page 1 of a completed poll.
        reasm = m.ChunkReassembler("D1:C7:C2:06:63:1F", segment_pages=1)
        self.assertIsNone(reasm.feed("NOTIFY", bytes([0xAA, 0xA5, 1]) + bytes(17)))


if __name__ == "__main__":
    unittest.main()
