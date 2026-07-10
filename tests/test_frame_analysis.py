"""End-to-end test for tools/analyze_frame_log.py — the CLI over a captured JSONL log.

The frame classification itself lives in govee_ble_local.wire.describe (see
test_wire_describe.py); this only checks the analyzer wires it up + exits non-zero on a
hard issue.
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


def test_main_flags_unknown(tmp_path: pathlib.Path) -> None:
    log = tmp_path / "cap.jsonl"
    good = _frame(0x33, 0x01, 0x01).hex()
    bad = _frame(0x99, 0x01).hex()
    log.write_text(
        '{"dir":"tx","plain":"%s","enc":"none"}\n'
        '{"dir":"rx","plain":null,"enc":"e7"}\n'   # ciphertext-only → skipped
        '{"dir":"tx","plain":"%s","enc":"none"}\n' % (good, bad)
    )
    assert afl.main([str(log)]) == 1  # hard issue present → non-zero
