"""Write-ACK verification in the transport (mirrors the app's AbsSingleController:
reply matched by [0x33, commandType], success == byte[2] == 0)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from govee_ble_local.exceptions import (
    GoveeBleConnectionError,
    GoveeBleError,
    GoveeBleTimeout,
)
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


def test_on_disconnect_clears_client() -> None:
    # A device-initiated disconnect must drop the client so it can't be reused
    # with wiped services ("Service Discovery has not been performed yet").
    c = _conn()
    assert c.is_connected
    c._on_disconnect(c._client)
    assert c._client is None
    assert not c.is_connected


def test_connect_failure_tears_down_client(monkeypatch: pytest.MonkeyPatch) -> None:
    # If the connect half-succeeds (client established) but start_notify drops on
    # a flaky link, _connect_locked must disconnect + null the client so the next
    # attempt re-establishes cleanly instead of reusing a stale one.
    async def run() -> None:
        dev = BLEDevice("AA:BB:CC:DD:EE:FF", "GVH60A6X", details={})
        c = GoveeConnection(dev, encryption=Encryption.NONE)
        c._idle_disconnect = 0
        bad = MagicMock()
        bad.is_connected = True
        bad.start_notify = AsyncMock(side_effect=BleakError("boom"))
        bad.disconnect = AsyncMock()

        async def fake_establish(*_a: object, **_k: object) -> MagicMock:
            return bad

        monkeypatch.setattr(conn_mod, "establish_connection", fake_establish)
        with pytest.raises(GoveeBleConnectionError):
            await c._connect_locked()
        assert c._client is None          # torn down, not left stale
        bad.disconnect.assert_awaited()   # half-open client cleaned up

    asyncio.run(run())


def test_raw_write_after_drop_raises_cleanly() -> None:
    # If the disconnect callback nulls the client mid-flow, a subsequent write
    # raises a catchable GoveeBleConnectionError, not an AssertionError.
    async def run() -> None:
        c = _conn()
        c._client = None
        with pytest.raises(GoveeBleConnectionError):
            await c._raw_write(_frame(0x33, 0x01, 0x01))

    asyncio.run(run())


def test_command_opens_warm_window_read_does_not() -> None:
    """A user command (0x33 write) opens the ~30s warm window; a read (0xAA)
    does not, so idle devices/polls still release the slot on the base delay."""
    async def run() -> None:
        c = _conn()
        assert c._active_until == 0.0
        _write_feeding(c, _frame(0xAA, 0xB1, 0x01))
        await c.send(_frame(0xAA, 0xB1))            # read: no warm window
        assert c._active_until == 0.0
        _write_feeding(c, _frame(0x33, 0x01, 0x00))
        t0 = asyncio.get_running_loop().time()
        await c.send(_frame(0x33, 0x01, 0x01))      # command: warm window opens
        assert c._active_until >= t0 + 29
    asyncio.run(run())
