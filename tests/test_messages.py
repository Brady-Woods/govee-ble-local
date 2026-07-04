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


class TestStubsAndGating(unittest.TestCase):
    def test_stubs_recognized_but_not_sendable(self) -> None:
        clock = m.deserialize(_frame(bytes([0x33, 0x09, 0x6A, 0x48, 0xAE, 0xDD, 0x01, 0xF9])), "WRITE")
        ee = m.deserialize(_frame(bytes([0xEE, 0x20, 0x0A])), "NOTIFY")
        a4 = m.deserialize(_frame(bytes([0xA4, 0x58, 0x00])), "NOTIFY")
        for msg, name in ((clock, "clock"), (ee, "stub_ee"), (a4, "stub_a4")):
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
        for name in ("clock", "wifi_provision", "status_field", "ack", "definitely_not_a_command"):
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


if __name__ == "__main__":
    unittest.main()
