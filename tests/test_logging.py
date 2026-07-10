"""Logging scheme + frame-tier capture + the debug converter.

Covers: a device rejection logs at ERROR; an unrecognised RX frame logs at WARNING (closing
the silent-drop gap); the govee_ble_local.frames logger emits every frame when enabled and
is silent when off; and the frames-log -> JSONL converter round-trips into the analyzer.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from bleak.backends.device import BLEDevice

from govee_ble_local import debug
from govee_ble_local.exceptions import GoveeBleError
from govee_ble_local.models import Encryption
from govee_ble_local.transport.connection import GoveeConnection

_FRAMES_LOGGER = "govee_ble_local.frames"
_CONN_LOGGER = "govee_ble_local.transport.connection"


def _bcc(*body: int) -> bytes:
    buf = bytearray(20)
    buf[: len(body)] = bytes(body)
    x = 0
    for b in buf[:19]:
        x ^= b
    buf[19] = x
    return bytes(buf)


def _conn() -> GoveeConnection:
    c = GoveeConnection(BLEDevice("AA:BB:CC:DD:EE:FF", "GVH60A6X", details={}),
                        encryption=Encryption.NONE)
    client = MagicMock()
    client.is_connected = True
    c._client = client
    c._ready = True
    c._idle_disconnect = 0
    return c


def test_rejected_write_logs_error(caplog: pytest.LogCaptureFixture) -> None:
    async def run() -> None:
        c = _conn()

        async def fake_write(_wire: bytes) -> None:
            c._rx.put_nowait(_bcc(0x33, 0x01, 0x02))       # byte2 != 0 -> rejection
        c._raw_write = AsyncMock(side_effect=fake_write)
        with caplog.at_level(logging.ERROR, logger=_CONN_LOGGER), pytest.raises(GoveeBleError):
            await c.send(_bcc(0x33, 0x01, 0x01))
    asyncio.run(run())
    assert any("rejected" in r.message for r in caplog.records if r.levelno == logging.ERROR)


def test_unrecognised_rx_frame_warns(caplog: pytest.LogCaptureFixture) -> None:
    c = _conn()
    with caplog.at_level(logging.WARNING, logger=_CONN_LOGGER):
        c._handle_notify(None, bytearray(_bcc(0x99, 0x01)))  # 0x99 not a known proType
    assert any(r.levelno == logging.WARNING and "RX" in r.message for r in caplog.records)


def test_frames_logger_emits_when_enabled(caplog: pytest.LogCaptureFixture) -> None:
    c = _conn()
    with caplog.at_level(logging.DEBUG, logger=_FRAMES_LOGGER):
        c._capture("tx", wire=_bcc(0x33, 0x01, 0x01), plain=_bcc(0x33, 0x01, 0x01), enc="none")
    recs = [r for r in caplog.records if r.name == _FRAMES_LOGGER]
    assert recs and "plain=" in recs[0].getMessage() and "write/switch" in recs[0].getMessage()


def test_frames_logger_silent_when_off(caplog: pytest.LogCaptureFixture) -> None:
    c = _conn()
    logging.getLogger(_FRAMES_LOGGER).setLevel(logging.INFO)   # below-DEBUG => guarded off
    with caplog.at_level(logging.INFO, logger=_FRAMES_LOGGER):
        c._capture("tx", wire=_bcc(0x33, 0x01, 0x01), plain=_bcc(0x33, 0x01, 0x01), enc="none")
    assert not [r for r in caplog.records if r.name == _FRAMES_LOGGER]


def test_frames_log_converter_roundtrips_into_analyzer(capsys: pytest.CaptureFixture[str]) -> None:
    # Lines as they'd appear from the frames logger (with an arbitrary logging prefix).
    good = _bcc(0x33, 0x01, 0x01).hex()
    text = (
        f"2026-07-10 12:00:00 DEBUG govee_ble_local.frames AA:BB tx write/switch "
        f"plain={good} wire={good} enc=none\n"
        f"2026-07-10 12:00:01 DEBUG govee_ble_local.frames AA:BB tx cipher "
        f"plain= wire=deadbeef enc=e7\n"
    )
    records = debug.frames_log_to_records(text)
    assert [r["dir"] for r in records] == ["tx", "tx"]
    assert records[0]["plain"] == good and records[1]["plain"] is None   # ciphertext -> None
    assert debug.analyze(records) == 0                                    # no hard issues
    assert "frame-log analysis (1 plaintext frames; 1 skipped" in capsys.readouterr().out
