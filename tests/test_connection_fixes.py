"""Reliability fixes for transport/connection.py (T6.2): BUG-1/6/7/8/10, DES-4/9,
the shared capture mask, send_mixed, and the link-lifecycle audit. No wall-clock
assertions; waits are event/ordering based."""
from __future__ import annotations

import asyncio
import logging

import pytest
from bleak.exc import BleakError

from govee_ble_local import const
from govee_ble_local.const import FRAME_LEN
from govee_ble_local.crypto import checksum_ok, encrypt
from govee_ble_local.exceptions import (
    GoveeBleAuthError,
    GoveeBleConnectionError,
    GoveeBleInvalidValue,
    GoveeBleTimeout,
)
from govee_ble_local.models import Encryption
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.transport.connection import GoveeConnection, _LinkLost

from fake_ble import SESSION_KEY, FakeGovee, ble_device, frame

ADDR = "AA:BB:CC:DD:EE:FF"


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_STEP2_TIMEOUT", 0.05)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_TIMEOUT", 0.1)
    monkeypatch.setattr(conn_mod, "COMMAND_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(conn_mod, "DISCONNECT_TIMEOUT", 0.05)
    monkeypatch.setattr(conn_mod, "UNACKED_SEND_GAP", 0.0)


def _make(monkeypatch, fake: FakeGovee, **kw) -> GoveeConnection:
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    enc = kw.pop("encryption", Encryption.AES_RC4_PSK if fake.encrypted else Encryption.NONE)
    c = GoveeConnection(ble_device(ADDR, "GVH60A6X"), encryption=enc, **kw)
    c._idle_disconnect = 0
    return c


# -- BUG-1: plaintext queue --------------------------------------------------

async def test_rx_queue_holds_plaintext_and_survives_disconnect(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    ack = frame(0x33, 0x01, 0x00)
    c._handle_notify(None, bytearray(encrypt(ack, SESSION_KEY)))
    c._on_disconnect(c._client)  # type: ignore[arg-type]
    assert c._session_key is None
    assert await c._next_rx(1.0) == ack  # plaintext, not ciphertext
    with pytest.raises(_LinkLost):
        await c._next_rx(1.0)


async def test_query_frames_survive_same_tick_disconnect(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    f1, f2 = frame(0xAC, 0x00, 1), frame(0xAC, 0x01, 2)
    for f in (f1, f2):
        c._handle_notify(None, bytearray(encrypt(f, SESSION_KEY)))
    c._on_disconnect(c._client)  # type: ignore[arg-type]
    assert await c._next_rx(0) == f1
    assert await c._next_rx(0) == f2


# -- BUG-8: sentinel never dropped -------------------------------------------

async def test_enqueue_overflow_never_drops_link_lost_sentinel(monkeypatch) -> None:
    c = _make(monkeypatch, FakeGovee())
    # WAS: the overflow drop removed the OLDEST item even when that was the None
    # sentinel, so a waiter would sleep to its timeout instead of seeing link loss.
    c._enqueue(None)
    for i in range(conn_mod._RX_QUEUE_MAX + 5):
        c._enqueue(bytes([i]) * FRAME_LEN)
    assert None in list(c._rx._queue)  # type: ignore[attr-defined]
    assert c._rx.qsize() <= conn_mod._RX_QUEUE_MAX + 1
    # frames queued ahead of the sentinel are still delivered first, then link loss
    with pytest.raises(_LinkLost):
        for _ in range(conn_mod._RX_QUEUE_MAX + 10):
            await c._next_rx(0)


async def test_waiter_wakes_link_lost_when_queue_overflows_around_disconnect(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    for i in range(conn_mod._RX_QUEUE_MAX):
        c._enqueue(bytes([i]) * FRAME_LEN)
    c._on_disconnect(c._client)  # type: ignore[arg-type]
    for i in range(conn_mod._RX_QUEUE_MAX):  # late junk after the sentinel
        c._enqueue(bytes([i]) * FRAME_LEN)
    seen = 0
    with pytest.raises(_LinkLost):
        while True:
            await c._next_rx(0.5)
            seen += 1
    assert seen <= conn_mod._RX_QUEUE_MAX * 2


# -- BUG-6: disconnect bounded -----------------------------------------------

async def test_public_disconnect_bounded_when_proxy_wedged(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.disconnect_hangs = True
    await asyncio.wait_for(c.disconnect(), 5)
    assert c._client is None and c._ready is False and not c.is_connected
    assert fake.disconnect_started == 1


async def test_connect_failure_cleanup_bounded_when_proxy_wedged(monkeypatch) -> None:
    fake = FakeGovee()
    fake.withhold_e7_reply = True
    fake.disconnect_hangs = True
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleTimeout):
        await asyncio.wait_for(c.connect(), 5)
    assert c._client is None and c._ready is False


async def test_idle_disconnect_task_swallows_any_exception(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.disconnect_error = RuntimeError("proxy exploded")
    await c._idle_disconnect_task(c._idle_generation)  # must not raise
    assert c._client is None and c._ready is False


async def test_idle_disconnect_task_bounded_when_wedged(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.disconnect_hangs = True
    await asyncio.wait_for(c._idle_disconnect_task(c._idle_generation), 5)
    assert c._client is None and not c._lock.locked()


async def test_disconnect_survives_caller_cancellation(monkeypatch) -> None:
    """Lifecycle: cancelling the caller mid-disconnect must not abandon the link
    (the client reference is already dropped, so nothing else would close it)."""
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    client = fake.clients[0]
    gate = asyncio.Event()
    real = client.disconnect

    async def slow_disconnect() -> None:
        await gate.wait()
        await real()

    client.disconnect = slow_disconnect  # type: ignore[method-assign]
    monkeypatch.setattr(conn_mod, "DISCONNECT_TIMEOUT", 30.0)
    task = asyncio.ensure_future(c.disconnect())
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    gate.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert client.is_connected is False  # the teardown still ran to completion


# -- BUG-10 ------------------------------------------------------------------

async def test_send_many_style_non_write_expect_ack_raises_before_io(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleInvalidValue):
        await c.send(frame(0xAC, 0x03), expect_ack=True)
    assert fake.establish_calls == 0


# -- DES-4 / lifecycle: auth + unlock ----------------------------------------

async def test_auth_error_not_retried_even_via_write(monkeypatch) -> None:
    fake = FakeGovee()
    fake.ack_status = 1
    c = _make(monkeypatch, fake, unlock_frames=lambda: [frame(0x33, 0xB2, 0x01)])
    with pytest.raises(GoveeBleAuthError):
        await c.send(frame(0x33, 0x01, 0x01))
    assert len(fake.clients) == 1
    assert fake.clients[0].is_connected is False and c._ready is False


async def test_link_lost_during_unlock_is_connection_error_and_retried(monkeypatch) -> None:
    fake = FakeGovee()
    unlock = frame(0x33, 0xB2, 0x01)
    state = {"n": 0}

    def on_write(client, plain: bytes) -> None:
        if plain == unlock and state["n"] == 0:
            state["n"] += 1
            client.drop()  # first link dies while the unlock is in flight
            return
        client.push(fake._wire(frame(0x33, plain[1], 0), SESSION_KEY), 0.005)

    fake.on_write = on_write
    c = _make(monkeypatch, fake, unlock_frames=lambda: [unlock])
    await c.connect()  # second session attempt succeeds
    assert len(fake.clients) == 2 and c.is_connected


# -- lifecycle: cancellation / failures never leave an untracked open link ----

async def test_cancel_during_handshake_closes_link(monkeypatch) -> None:
    fake = FakeGovee()
    fake.withhold_e7_reply = True
    monkeypatch.setattr(conn_mod, "HANDSHAKE_TIMEOUT", 30.0)
    c = _make(monkeypatch, fake)
    task = asyncio.ensure_future(c.connect())
    for _ in range(20):
        await asyncio.sleep(0)
    assert fake.clients and fake.clients[0].is_connected
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(5):
        await asyncio.sleep(0)
    assert all(not cl.is_connected for cl in fake.clients)
    assert c._client is None and c._ready is False and not c._lock.locked()


@pytest.mark.parametrize("exc", [RuntimeError("weird"), BleakError("nope")])
async def test_start_notify_failure_closes_link(monkeypatch, exc) -> None:
    fake = FakeGovee()
    fake.fail_start_notify = True
    c = _make(monkeypatch, fake)
    with pytest.raises(Exception):
        await c.connect()
    assert all(not cl.is_connected for cl in fake.clients)
    assert c._client is None and c._ready is False


async def test_operation_timeout_during_establish_leaves_no_tracked_garbage(monkeypatch) -> None:
    fake = FakeGovee()

    async def hang(*_a, **_k):
        await asyncio.sleep(3600)

    monkeypatch.setattr(conn_mod, "establish_connection", hang)
    monkeypatch.setattr(conn_mod, "OPERATION_TIMEOUT", 0.05)
    c = GoveeConnection(ble_device(ADDR, "x"), encryption=Encryption.NONE)
    c._idle_disconnect = 0
    with pytest.raises(Exception):
        await c.connect()
    assert c._client is None and c._ready is False and not c._lock.locked()
    assert fake.clients == []


async def test_drop_after_ready_clears_ready(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.clients[0].drop()
    assert c._ready is False and c._client is None and not c.is_connected


# -- DES-9 / capture mask ----------------------------------------------------

def test_named_constants_in_use() -> None:
    assert conn_mod.QUERY_DEFAULT_TIMEOUT == const.QUERY_DEFAULT_TIMEOUT
    assert conn_mod.UNACKED_SEND_GAP == const.UNACKED_SEND_GAP or conn_mod.UNACKED_SEND_GAP == 0.0
    kw = GoveeConnection.query.__kwdefaults__
    assert kw["timeout"] == const.QUERY_DEFAULT_TIMEOUT
    assert GoveeConnection.send_unacked.__kwdefaults__["gap"] == const.UNACKED_SEND_GAP


def test_known_rx_types_unchanged() -> None:
    assert conn_mod._KNOWN_RX_PRO_TYPES == frozenset(
        {0x33, 0xAA, 0xAC, 0xEE, 0xA1, 0xA3, 0xA4, 0xE7}
    )


def test_mask_helper_recomputes_checksum() -> None:
    secret = bytes([0x61, 0x55, 0x21, 0x09, 0x0B, 0x73, 0x5C, 0x54])
    plain = frame(0x33, 0xB2, *secret)
    masked = conn_mod._mask_secret(plain)
    assert len(masked) == FRAME_LEN
    assert masked[:2] == plain[:2] and masked[2:-1] == b"\xff" * (FRAME_LEN - 3)
    assert checksum_ok(masked)
    assert masked[-1] != plain[-1]  # no longer the XOR of the secret
    # short/odd lengths: header kept, rest blanked, no checksum fabricated
    assert conn_mod._mask_secret(b"\x33\xb2\x01\x02") == b"\x33\xb2\xff\xff"


async def test_capture_and_mask_secret_share_one_helper(monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="govee_ble_local.frames")
    c = _make(monkeypatch, FakeGovee(encrypted=False))
    plain = frame(0x33, 0xB2, 1, 2, 3, 4, 5, 6, 7, 8)
    c._capture("tx", wire=plain, plain=plain, enc="none")
    masked = conn_mod._mask_secret(plain).hex()
    assert f"plain={masked} wire={masked}" in caplog.text


# -- send_mixed --------------------------------------------------------------

async def test_send_mixed_acked_and_unacked(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    items = [
        (frame(0x33, 0x01, 1), True),
        (frame(0xA3, 0x00, 9), False),
        (frame(0x33, 0x04, 50), True),
    ]
    res = await c.send_mixed(items)
    assert len(fake.clients) == 1
    assert fake.received == [f for f, _ in items]
    assert res[0] is not None and res[0][:3] == bytes([0x33, 0x01, 0])
    assert res[1] is None
    assert res[2] is not None and res[2][:3] == bytes([0x33, 0x04, 0])


async def test_send_mixed_empty_and_validation(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    assert await c.send_mixed([]) == []
    with pytest.raises(GoveeBleInvalidValue):
        await c.send_mixed([(frame(0x33, 0x01, 1), True), (frame(0xAA, 0x07), True)])
    assert fake.received == []  # validated up front: nothing sent


async def test_send_mixed_mid_sequence_link_loss_retries_only_failed_write(monkeypatch) -> None:
    fake = FakeGovee()
    a, u, b = frame(0x33, 0x01, 1), frame(0xA3, 0x00, 9), frame(0x33, 0x04, 50)
    seen = {"dropped": False}

    def on_write(client, plain: bytes) -> None:
        if plain == b and not seen["dropped"]:
            seen["dropped"] = True
            client.drop()
            return
        if plain[0] == 0x33:
            client.push(fake._wire(frame(0x33, plain[1], 0), SESSION_KEY), 0.005)

    fake.on_write = on_write
    c = _make(monkeypatch, fake)
    res = await c.send_mixed([(a, True), (u, False), (b, True)])
    assert len(fake.clients) == 2
    assert fake.received.count(a) == 1 and fake.received.count(u) == 1
    assert fake.received.count(b) == 2
    assert res[1] is None and res[2] is not None


async def test_send_mixed_uses_one_lock_acquisition(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    calls = {"n": 0}
    real = c._run

    async def spy(op, *, extend_warm):
        calls["n"] += 1
        return await real(op, extend_warm=extend_warm)

    monkeypatch.setattr(c, "_run", spy)
    await c.send_mixed([(frame(0x33, 0x01, 1), True), (frame(0xA3, 1), False)])
    assert calls["n"] == 1


def test_now_ts_removed() -> None:
    assert not hasattr(conn_mod, "now_ts")
