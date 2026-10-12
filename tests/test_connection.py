"""Transport behaviour against a fake peripheral that reproduces the failure modes
seen in live captures (duplicated/retransmitted handshake replies, half-dead links,
lost terminators, late disconnect callbacks)."""
from __future__ import annotations

import asyncio
import time

import pytest
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from govee_ble_local.exceptions import (
    GoveeBleAuthError,
    GoveeBleConnectionError,
    GoveeBleError,
    GoveeBleTimeout,
)
from govee_ble_local.models import Encryption
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.transport.connection import GoveeConnection

from fake_ble import FakeGovee, frame

ADDR = "AA:BB:CC:DD:EE:FF"

# A real H60A6 status burst (live capture): chunks 00..08 + FF terminator.
H60A6_BURST = [bytes.fromhex(h) for h in (
    "ac000a000c0300010100040164050415010a8c5b ac010707065774f453e75c0711105674f453e7f0 "
    "ac025cdb2d0100290104030000070d115ce753d9 ac03f474560100290104031104001e0f0f120749 "
    "ac04ff640000800f00231000000080000000808f ac0500000080000000804102020130020000a57e "
    "ac06110164ffae5464ffae5464ffae5464ffaeee ac0754a5110264ffae5464ffae5464ffae54644c "
    "ac08ffae54a5110364ffae5464ffae5464ffae23 acff5464ffae54a5050464ffae540000000000a3"
).split()]


@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.1)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_STEP2_TIMEOUT", 0.1)
    monkeypatch.setattr(conn_mod, "BURST_IDLE_GAP", 0.1)


def _make(monkeypatch: pytest.MonkeyPatch, fake: FakeGovee, **kw: object) -> GoveeConnection:
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    enc = Encryption.AES_RC4_PSK if fake.encrypted else Encryption.NONE
    c = GoveeConnection(BLEDevice(ADDR, "GVH60A6X", details={}), encryption=enc, **kw)  # type: ignore[arg-type]
    c._idle_disconnect = 0
    return c


async def test_handshake_ignores_duplicate_and_late_echo(monkeypatch, fast) -> None:
    fake = FakeGovee()
    fake.status_burst = H60A6_BURST
    fake.late_echo_delay = 0.03  # retransmit lands inside the first query
    c = _make(monkeypatch, fake)
    frames = await c.query(frame(0xAC, 0x03, 0x03, 0x41, 0x30, 0xA5), timeout=2.0)
    chunks = [f[1] for f in frames if f[0] == 0xAC]
    assert chunks == [0, 1, 2, 3, 4, 5, 6, 7, 8, 0xFF]
    assert all(f[0] == 0xAC for f in frames)  # no decrypted handshake junk
    assert len(fake.clients) == 1


async def test_query_lost_terminator_returns_on_idle_gap(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    fake.status_burst = H60A6_BURST
    fake.drop_terminal = True
    c = _make(monkeypatch, fake)
    t0 = time.monotonic()
    frames = await c.query(frame(0xAC, 0x03), timeout=5.0)
    assert len(frames) == 9
    assert time.monotonic() - t0 < 1.0  # idle gap, not the 5 s timeout


async def test_write_ack_success_and_warm_window(monkeypatch, fast) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    t0 = asyncio.get_running_loop().time()
    ack = await c.send(frame(0x33, 0x01, 0x01))
    assert ack is not None and ack[:3] == bytes([0x33, 0x01, 0x00])
    assert c._active_until >= t0 + 29
    assert fake.received[-1][:3] == bytes([0x33, 0x01, 0x01])


async def test_read_does_not_open_warm_window(monkeypatch, fast) -> None:
    c = _make(monkeypatch, FakeGovee())
    await c.query(frame(0xAA, 0x01, 0x01), opcode=0xAA, terminal=0x01)
    assert c._active_until == 0.0


async def test_write_rejected_is_not_retried(monkeypatch, fast) -> None:
    fake = FakeGovee()
    fake.ack_status = 2
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleError, match="rejected"):
        await c.send(frame(0x33, 0x01, 0x01))
    assert len(fake.clients) == 1


async def test_half_dead_link_reconnects_and_retries(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.clients[0].dead = True  # link silently stops answering
    ack = await c.send(frame(0x33, 0x04, 0x32))
    assert ack is not None and ack[1] == 0x04
    assert len(fake.clients) == 2  # one reconnect


async def test_write_error_reconnects_and_retries(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    c = _make(monkeypatch, fake)
    await c.connect()
    first = fake.clients[0]

    async def boom(*_a: object, **_k: object) -> None:
        raise BleakError("Not connected")

    first.write_gatt_char = boom  # type: ignore[method-assign]
    assert await c.send(frame(0x33, 0x01, 0x01)) is not None
    assert len(fake.clients) == 2


async def test_dead_device_fails_cleanly(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    fake.dead_links = 99
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleTimeout):
        await c.send(frame(0x33, 0x01, 0x01))
    assert len(fake.clients) == 2  # original + exactly one retry link


async def test_disconnect_wakes_waiter_immediately(monkeypatch, fast) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 5.0)
    fake = FakeGovee(encrypted=False)
    c = _make(monkeypatch, fake)
    await c.connect()
    dropped: list[bool] = []

    def drop_first_link(client, plain) -> None:
        if not dropped:  # the first link dies mid-wait; the retry link is healthy
            dropped.append(True)
            asyncio.get_running_loop().call_later(0.05, client.drop)
        else:
            client.push(frame(0x33, plain[1], 0x00), 0.005)

    fake.on_write = drop_first_link
    t0 = time.monotonic()
    ack = await c.send(frame(0x33, 0x01, 0x01))
    assert ack is not None
    assert time.monotonic() - t0 < 1.0  # woken by the drop, not a 5 s ack timeout
    assert len(fake.clients) == 2


async def test_late_disconnect_callback_from_old_client_is_ignored(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    c = _make(monkeypatch, fake)
    await c.connect()
    old = fake.clients[0]
    await c.disconnect()
    await c.connect()
    assert c.is_connected
    c._on_disconnect(old)  # stale callback arriving late
    assert c.is_connected and c._client is fake.clients[1]


async def test_connect_failure_tears_down_client(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    fake.fail_start_notify = True
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleConnectionError):
        await c.connect()
    assert c._client is None
    assert all(not cl.is_connected for cl in fake.clients)


async def test_secret_rejection_raises_auth_error(monkeypatch, fast) -> None:
    fake = FakeGovee()
    fake.ack_status = 1
    c = _make(monkeypatch, fake, unlock_frames=lambda: [frame(0x33, 0xB2, *range(8))])
    with pytest.raises(GoveeBleAuthError):
        await c.connect()


async def test_bad_checksum_frames_are_dropped(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)

    def junk_then_ack(client, plain) -> None:
        client.push(bytes([0x33, plain[1], 0x00]) + bytes(17))  # bad BCC, would "ack"
        client.push(frame(0x33, plain[1], 0x05), 0.02)           # real ack: rejected

    fake.on_write = junk_then_ack
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleError, match="rejected"):
        await c.send(frame(0x33, 0x01, 0x01))


async def test_bgc_read_is_cached(monkeypatch, fast) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    await c.disconnect()
    await c.connect()
    assert fake.bgc_reads == 1


async def test_establish_uses_fresh_ble_device(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    c = _make(monkeypatch, fake)
    await c.connect()
    newer = BLEDevice(ADDR, "via-proxy", details={})
    c.update_ble_device(newer)
    assert fake.connect_kwargs["ble_device_callback"]() is newer


async def test_operation_timeout(monkeypatch, fast) -> None:
    monkeypatch.setattr(conn_mod, "OPERATION_TIMEOUT", 0.2)
    fake = FakeGovee(encrypted=False)

    async def hang(*_a: object, **_k: object) -> None:
        await asyncio.sleep(10)

    c = _make(monkeypatch, fake)
    await c.connect()
    fake.clients[0].write_gatt_char = hang  # type: ignore[method-assign]
    with pytest.raises(GoveeBleTimeout):
        await c.send(frame(0x33, 0x01, 0x01))
    assert c._client is None  # wedged link torn down


async def test_idle_timer_never_fires_after_newer_activity(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    c = _make(monkeypatch, fake)
    c._idle_disconnect = 0.05
    await c.connect()
    gen = c._idle_generation
    c._idle_fired(gen)  # timer fires...
    await c.query(frame(0xAA, 0x01, 0x01), opcode=0xAA, terminal=0x01)  # ...but activity wins the lock
    await asyncio.sleep(0)
    assert c.is_connected
    await asyncio.sleep(0.1)  # the rescheduled timer then disconnects normally
    assert not c.is_connected


async def test_send_many_uses_one_connection(monkeypatch, fast) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.send_many([frame(0x33, 0x30, 0, 1), frame(0x33, 0x30, 1, 1), frame(0x33, 0x04, 50)])
    assert len(fake.clients) == 1
    assert [f[:2] for f in fake.received] == [b"\x33\x30", b"\x33\x30", b"\x33\x04"]


async def test_closed_connection_refuses_io(monkeypatch, fast) -> None:
    c = _make(monkeypatch, FakeGovee(encrypted=False))
    await c.close()
    with pytest.raises(GoveeBleConnectionError):
        await c.send(frame(0x33, 0x01, 0x01))


async def test_secret_is_masked_in_frame_capture(monkeypatch, fast, caplog) -> None:
    import logging

    caplog.set_level(logging.DEBUG, logger="govee_ble_local.frames")
    fake = FakeGovee()
    secret = bytes([0x61, 0x55, 0x21, 0x09, 0x0B, 0x73, 0x5C, 0x54])
    c = _make(monkeypatch, fake, unlock_frames=lambda: [frame(0x33, 0xB2, *secret)])
    await c.connect()
    assert fake.received[0][2:10] == secret  # the device still gets the real key
    assert secret.hex() not in caplog.text
