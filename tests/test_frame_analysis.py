"""Unit tests for tools/analyze_frame_log.py — the Kaitai coverage checker.

Confirms it passes well-formed known frames and RAISES a hard issue for traffic the
spec doesn't represent (unknown proType / command / mode sub-type).
"""
from __future__ import annotations

import pathlib
import sys

import pytest

pytest.importorskip("kaitaistruct", reason="Kaitai runtime not installed")
pytest.importorskip("govee_ble_local._generated.govee_ble_frame", reason="run tools/gen_kaitai.sh")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import analyze_frame_log as afl  # noqa: E402


def _frame(*body: int) -> bytes:
    buf = bytearray(20)
    buf[: len(body)] = bytes(body)
    x = 0
    for b in buf[:19]:
        x ^= b
    buf[19] = x
    return bytes(buf)


def _hard(plain: bytes) -> list[str]:
    _label, issues = afl.analyze_frame(plain)
    return [r for sev, r in issues if sev == "hard"]


# ── known-good frames: no hard issues ────────────────────────────────────────
def test_power_frame_clean():
    label, issues = afl.analyze_frame(_frame(0x33, 0x01, 0x01))
    assert not [r for sev, r in issues if sev == "hard"]
    assert label == "write/switch"


def test_mode_color_frame_clean():
    label, _ = afl.analyze_frame(_frame(0x33, 0x05, 0x15, 0x01, 0xFF, 0x00, 0x00))
    assert label.startswith("write/mode/color_rgbic_15")


def test_notify_frame_clean():
    label, issues = afl.analyze_frame(_frame(0xEE, 0x01, 0x00))
    assert not [r for sev, r in issues if sev == "hard"]
    assert label == "notify/light_status"


# ── frames the spec does NOT represent: hard issue raised ────────────────────
def test_unknown_protype_flagged():
    assert _hard(_frame(0x99, 0x01))  # 0x99 not in pro_type enum


def test_unknown_command_flagged():
    # 0x5A is not in the command enum
    assert _hard(_frame(0x33, 0x5A, 0x00))


def test_unknown_mode_submode_flagged():
    # 0x99 is not in the sub_mode enum
    assert _hard(_frame(0x33, 0x05, 0x99))


def test_unknown_notify_sub_flagged():
    assert _hard(_frame(0xEE, 0x99))  # 0x99 not in notify_sub enum


# ── invalid checksum = artifact (undecrypted/ciphertext), NOT a hard spec gap ─
def test_bad_checksum_is_artifact_not_hard():
    # high-entropy 20 bytes with a wrong BCC (typical of ciphertext logged as plain)
    cipher = bytes.fromhex("bd98c03f7b5b30cd0992dc0f2c4bf0ea37bfd220")
    label, issues = afl.analyze_frame(cipher)
    sev = {s for s, _ in issues}
    assert "hard" not in sev and "artifact" in sev
    assert label == "bad_checksum"


# ── soft coverage note: known command, payload not modelled ──────────────────
def _soft(plain: bytes) -> list[str]:
    _label, issues = afl.analyze_frame(plain)
    return [r for sev, r in issues if sev == "soft"]


def test_known_but_unmodelled_payload_is_soft():
    # zone write (0x30) is in the enum but has no modelled payload → soft, not hard
    label, issues = afl.analyze_frame(_frame(0x33, 0x30, 0x01, 0x00))
    assert not [r for sev, r in issues if sev == "hard"]
    assert any(sev == "soft" for sev, _ in issues)


def test_modeled_read_reply_is_clean():
    # AA 01 (switch_read_reply) IS modelled -> no soft/hard
    label, issues = afl.analyze_frame(_frame(0xAA, 0x01, 0x01))
    assert not issues and label == "read/switch"


def test_mechanism_b_read_reply_flagged_soft():
    # AA A2 (BulbGroupColor / mechanism B) — read_command has no 0xa2 case -> opaque -> soft gap
    assert _soft(_frame(0xAA, 0xA2, 0x01, 0x02, 0x03))
    assert not _hard(_frame(0xAA, 0xA2, 0x01, 0x02, 0x03))


def test_mechanism_c_mode_0d_read_flagged_soft():
    # AA 05 0D (mechanism C: 0x0d mode-report reply) — not modelled -> soft gap
    assert any("0x0d" in r for r in _soft(_frame(0xAA, 0x05, 0x0D, 0xFF, 0x00, 0x00)))
    # AA 05 04 (scene code) is read in wire.parse -> NOT flagged
    assert not _soft(_frame(0xAA, 0x05, 0x04, 0x82, 0x4A))


def test_notify_unmodelled_payload_soft():
    # EE 01 (light_status) sub-type is known but its payload has no modelled body -> soft
    assert _soft(_frame(0xEE, 0x01, 0x00))


# ── acks: RX 0x33 = device ack echo (byte2 = result), parsed direction-aware ──
def test_rx_write_is_ack_not_command():
    label, issues = afl.analyze_frame(_frame(0x33, 0x05, 0x00), "rx")
    assert label == "ack/mode" and not issues          # result 0 = success, clean
    # same bytes with no direction -> treated as a (tx) command, not an ack
    assert afl.analyze_frame(_frame(0x33, 0x05, 0x00))[0].startswith("write/mode")


def test_rx_write_rejection_is_hard():
    _label, issues = afl.analyze_frame(_frame(0x33, 0x05, 0x02), "rx")  # result 0x02 != 0
    assert any(sev == "hard" and "REJECTED" in r for sev, r in issues)


def test_multi_chunk_not_artifact():
    # 19-byte 0xA4 END upload chunk -> recognized as a chunk, not corrupt
    end = bytes([0xA4, 0xFF, 0xFF] + [0] * 15 + [0x11])   # 19 bytes
    label, issues = afl.analyze_frame(end, "tx")
    assert not issues and label.startswith("multi-chunk")


# ── 0xAC status reply: reassemble the burst, then walk the TLV stream ─────────
_H60A6_BURST = [bytes.fromhex(h) for h in (
    "ac000a000c0300010101040150050415010000e8",
    "ac010707065774f453e75c0711105674f453e7f0",
    "ac025cdb2d0100290104030000070d115ce753d9",
    "ac03f474560100290104031104001e0f0f120749",
    "ac04ff640000800f00231000000080000000808f",
    "ac0500000080000000804102020130020101a57e",
    "ac06110132ff00006400ff00640000ff64ff00ec",
    "ac0700a511026400ff00640000ff64ff000064e2",
    "ac0800ff00a51103640000ff64ff00006400ff77",
    "acff00640000ffa5050464ff00000000000000f7",
)]


def test_status_burst_reassembles_to_known_tlvs():
    types, gaps, malformed = afl._analyze_status_bursts(_H60A6_BURST)
    assert not gaps and malformed == 0    # every TLV type modelled, burst well-formed
    assert 0x30 in types and 0xA5 in types  # zone + colour-group TLVs present


def test_status_burst_unknown_tlv_flagged():
    a = bytearray(20)
    a[0], a[1] = 0xAC, 0x00              # burst start; first-frame data @ offset 7
    a[7], a[8], a[9] = 0x99, 0x01, 0x00     # unknown TLV type 0x99
    b = bytearray(20)
    b[0], b[1] = 0xAC, 0xFF             # terminator closes the burst
    _types, gaps, malformed = afl._analyze_status_bursts([bytes(a), bytes(b)])
    assert any("0x99" in g for g in gaps) and malformed == 0


def test_status_burst_missing_terminator_is_malformed():
    # two burst-starts with no terminator between -> first is flagged malformed, not walked
    a = bytearray(20); a[0], a[1] = 0xAC, 0x00
    b = bytearray(20); b[0], b[1] = 0xAC, 0x00
    _types, gaps, malformed = afl._analyze_status_bursts([bytes(a), bytes(b)])
    assert malformed >= 1 and not gaps


# ── end-to-end main() over a JSONL file: non-zero exit on a hard issue ───────
def test_main_flags_unknown(tmp_path):
    log = tmp_path / "cap.jsonl"
    good = _frame(0x33, 0x01, 0x01).hex()
    bad = _frame(0x99, 0x01).hex()
    log.write_text(
        '{"dir":"tx","plain":"%s","enc":"none"}\n'
        '{"dir":"rx","plain":null,"enc":"e7"}\n'   # ciphertext-only → skipped
        '{"dir":"tx","plain":"%s","enc":"none"}\n' % (good, bad)
    )
    assert afl.main([str(log)]) == 1  # hard issue present → non-zero
