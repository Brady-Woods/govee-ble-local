"""wire.describe — the shared frame label + gap classifier.

Confirms describe_frame() labels known frames cleanly and flags (hard) traffic the spec
can't represent, (soft) known opcodes with opaque payloads, and (artifact) corrupt frames;
plus analyze_status_bursts() over reassembled 0xAC bursts. This is the single implementation
used by both runtime logging and the offline analyzer.
"""
from __future__ import annotations

import pytest

pytest.importorskip("kaitaistruct", reason="Kaitai runtime not installed")

from govee_ble_local.wire.describe import (  # noqa: E402
    analyze_status_bursts,
    describe_frame,
)


def _frame(*body: int) -> bytes:
    buf = bytearray(20)
    buf[: len(body)] = bytes(body)
    x = 0
    for b in buf[:19]:
        x ^= b
    buf[19] = x
    return bytes(buf)


def _hard(plain: bytes) -> list[str]:
    return [r for sev, r in describe_frame(plain)[1] if sev == "hard"]


def _soft(plain: bytes) -> list[str]:
    return [r for sev, r in describe_frame(plain)[1] if sev == "soft"]


# ── known-good frames: no hard issues ────────────────────────────────────────
def test_power_frame_clean() -> None:
    label, issues = describe_frame(_frame(0x33, 0x01, 0x01))
    assert not [r for sev, r in issues if sev == "hard"]
    assert label == "write/switch"


def test_mode_color_frame_clean() -> None:
    label, _ = describe_frame(_frame(0x33, 0x05, 0x15, 0x01, 0xFF, 0x00, 0x00))
    assert label.startswith("write/mode/color_rgbic_15")


def test_notify_frame_clean() -> None:
    label, issues = describe_frame(_frame(0xEE, 0x01, 0x00))
    assert not [r for sev, r in issues if sev == "hard"]
    assert label == "notify/light_status"


# ── frames the spec does NOT represent: hard issue raised ────────────────────
def test_unknown_protype_flagged() -> None:
    assert _hard(_frame(0x99, 0x01))  # 0x99 not in pro_type enum


def test_unknown_command_flagged() -> None:
    assert _hard(_frame(0x33, 0x5A, 0x00))  # 0x5A not in the command enum


def test_unknown_mode_submode_flagged() -> None:
    assert _hard(_frame(0x33, 0x05, 0x99))  # 0x99 not in the sub_mode enum


def test_unknown_notify_sub_flagged() -> None:
    assert _hard(_frame(0xEE, 0x99))  # 0x99 not in notify_sub enum


# ── invalid checksum = artifact (undecrypted/ciphertext), NOT a hard spec gap ─
def test_bad_checksum_is_artifact_not_hard() -> None:
    cipher = bytes.fromhex("bd98c03f7b5b30cd0992dc0f2c4bf0ea37bfd220")
    label, issues = describe_frame(cipher)
    sev = {s for s, _ in issues}
    assert "hard" not in sev and "artifact" in sev
    assert label == "bad_checksum"


# ── soft coverage note: known command, payload not modelled ──────────────────
def test_known_but_unmodelled_payload_is_soft() -> None:
    label, issues = describe_frame(_frame(0x33, 0x30, 0x01, 0x00))  # zone write, opaque payload
    assert not [r for sev, r in issues if sev == "hard"]
    assert any(sev == "soft" for sev, _ in issues)


def test_modeled_read_reply_is_clean() -> None:
    label, issues = describe_frame(_frame(0xAA, 0x01, 0x01))  # switch_read_reply IS modelled
    assert not issues and label == "read/switch"


def test_mechanism_b_read_reply_now_modelled() -> None:
    # AA A2 / AA A5 (BulbGroupColor V1/V2) are typed in the ksy -> not opaque.
    for f in (_frame(0xAA, 0xA2, 0x01, 0x02, 0x03), _frame(0xAA, 0xA5, 0x01, 50, 2, 3)):
        assert not _soft(f) and not _hard(f)


def test_mechanism_c_mode_0d_read_now_modelled() -> None:
    assert not _soft(_frame(0xAA, 0x05, 0x0D, 0xFF, 0x00, 0x00))
    assert not _soft(_frame(0xAA, 0x05, 0x04, 0x82, 0x4A))   # scene code read in wire.parse


def test_notify_unmodelled_payload_soft() -> None:
    assert _soft(_frame(0xEE, 0x01, 0x00))  # light_status payload not modelled


# ── acks: RX 0x33 = device ack echo (byte2 = result), parsed direction-aware ──
def test_rx_write_is_ack_not_command() -> None:
    label, issues = describe_frame(_frame(0x33, 0x05, 0x00), "rx")
    assert label == "ack/mode" and not issues
    assert describe_frame(_frame(0x33, 0x05, 0x00))[0].startswith("write/mode")  # no dir -> command


def test_rx_write_rejection_is_hard() -> None:
    _label, issues = describe_frame(_frame(0x33, 0x05, 0x02), "rx")  # result 0x02 != 0
    assert any(sev == "hard" and "REJECTED" in r for sev, r in issues)


def test_multi_chunk_not_artifact() -> None:
    end = bytes([0xA4, 0xFF, 0xFF] + [0] * 15 + [0x11])   # 19-byte 0xA4 END upload chunk
    label, issues = describe_frame(end, "tx")
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


def test_status_burst_reassembles_to_known_tlvs() -> None:
    types, gaps, malformed = analyze_status_bursts(_H60A6_BURST)
    assert not gaps and malformed == 0
    assert 0x30 in types and 0xA5 in types


def test_status_burst_unknown_tlv_flagged() -> None:
    a = bytearray(20)
    a[0], a[1] = 0xAC, 0x00
    a[7], a[8], a[9] = 0x99, 0x01, 0x00     # unknown TLV type 0x99
    b = bytearray(20)
    b[0], b[1] = 0xAC, 0xFF
    _types, gaps, malformed = analyze_status_bursts([bytes(a), bytes(b)])
    assert any("0x99" in g for g in gaps) and malformed == 0


def test_status_burst_missing_terminator_is_malformed() -> None:
    a = bytearray(20); a[0], a[1] = 0xAC, 0x00
    b = bytearray(20); b[0], b[1] = 0xAC, 0x00
    _types, gaps, malformed = analyze_status_bursts([bytes(a), bytes(b)])
    assert malformed >= 1 and not gaps
