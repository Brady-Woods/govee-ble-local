"""Unit tests for tools/analyze_frame_log.py — the Kaitai coverage checker.

Confirms it passes well-formed known frames and RAISES a hard issue for traffic the
spec doesn't represent (unknown proType / command / mode sub-type).
"""
from __future__ import annotations

import pathlib
import sys

import pytest

pytest.importorskip("kaitaistruct", reason="Kaitai runtime not installed")
pytest.importorskip("spec_gen.govee_ble_frame", reason="run tools/gen_kaitai.sh")

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
def test_known_but_unmodelled_payload_is_soft():
    # device_info (0x07) is in the enum but has no modelled payload → soft, not hard
    label, issues = afl.analyze_frame(_frame(0x33, 0x07, 0x02))
    assert not [r for sev, r in issues if sev == "hard"]
    assert any(sev == "soft" for sev, _ in issues)


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
