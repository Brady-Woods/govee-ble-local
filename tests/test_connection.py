"""Write-ACK verification in the transport (mirrors the app's AbsSingleController:
reply matched by [0x33, commandType], success == byte[2] == 0)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from bleak.backends.device import BLEDevice

from govee_ble_local.exceptions import GoveeBleError, GoveeBleTimeout
from govee_ble_local.models import Encryption
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.transport.connection import GoveeConnection


def _frame(*b: int) -> bytes:
    return bytes(b) + b"\x00" * (20 - len(b))


def _conn() -> GoveeConnection:
    dev = BLEDevice("AA:BB:CC:DD:EE:FF", "GVH60A6X", details={})
    c = GoveeConnection(dev, encryption=Encryption.NONE)
    client = MagicMock()
    client.is_connected = True
    c._client = client          # bypass connect
    c._ready = True
    c._idle_disconnect = 0      # no lingering idle timer
    return c


def _write_feeding(c: GoveeConnection, *replies: bytes) -> None:
    async def fake_write(_wire: bytes) -> None:
        for r in replies:
            c._rx.put_nowait(r)
    c._raw_write = AsyncMock(side_effect=fake_write)


def test_write_ack_success() -> None:
    async def run() -> None:
        c = _conn()
        _write_feeding(c, _frame(0x33, 0x01, 0x00))          # power ack, byte2==0
        result = await c.send(_frame(0x33, 0x01, 0x01))
        assert result is not None and result[:3] == bytes([0x33, 0x01, 0x00])
    asyncio.run(run())


def test_write_ack_rejected() -> None:
    async def run() -> None:
        c = _conn()
        _write_feeding(c, _frame(0x33, 0x01, 0x02))          # byte2 != 0 -> rejected
        with pytest.raises(GoveeBleError):
            await c.send(_frame(0x33, 0x01, 0x01))
    asyncio.run(run())


def test_write_ack_skips_unrelated_frames() -> None:
    async def run() -> None:
        c = _conn()
        # an unrelated status push arrives first, then the real ack
        _write_feeding(c, _frame(0xAC, 0x00), _frame(0x33, 0x05, 0x00))
        result = await c.send(_frame(0x33, 0x05, 0x04, 0x01, 0x00))  # scene write
        assert result is not None and result[1] == 0x05
    asyncio.run(run())


def test_write_ack_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ATTEMPTS", 1)
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.05)

    async def run() -> None:
        c = _conn()
        c._raw_write = AsyncMock()                            # no reply fed
        with pytest.raises(GoveeBleTimeout):
            await c.send(_frame(0x33, 0x01, 0x01))
    asyncio.run(run())


def test_read_send_is_unverified() -> None:
    async def run() -> None:
        c = _conn()
        _write_feeding(c, _frame(0xAA, 0xB1, 0x01))           # a read reply
        result = await c.send(_frame(0xAA, 0xB1))             # 0xAA read: no ack check
        assert result is not None and result[:2] == bytes([0xAA, 0xB1])
    asyncio.run(run())
