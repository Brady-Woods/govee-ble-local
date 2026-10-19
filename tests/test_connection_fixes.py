"""Reliability fixes for transport/connection.py (T6.2): BUG-1/6/7/8/10, DES-4/9,
the shared capture mask, and the link-lifecycle audit. No wall-clock
assertions; waits are event/ordering based."""
from __future__ import annotations

import asyncio
import logging

import pytest
from bleak.exc import BleakError

from govee_ble_local import const
from govee_ble_local.const import FRAME_LEN, PSK
from govee_ble_local.crypto import decrypt, encrypt
from govee_ble_local.exceptions import (
    GoveeBleAuthError,
    GoveeBleCommandRejected,
    GoveeBleConnectionError,
    GoveeBleInvalidValue,
    GoveeBleTimeout,
)
from govee_ble_local.models import Encryption
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.transport.connection import GoveeConnection, _LinkLost
from govee_ble_local.wire import redact

from fake_ble import OTA_SERVICE_UUID, SESSION_KEY, FakeGovee, ble_device, frame

ADDR = "AA:BB:CC:DD:EE:FF"


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_STEP2_TIMEOUT", 0.05)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_TIMEOUT", 0.1)
    monkeypatch.setattr(conn_mod, "DISCONNECT_TIMEOUT", 0.05)


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


async def test_send_many_non_write_raises_before_io(monkeypatch) -> None:
    # WAS: send_many only checked the length, so a non-write frame waited for a [0x33, cmd] ack
    # that never comes, tore the link down, reconnected, retried and only then timed out.
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleInvalidValue, match="proType 0xAA"):
        await c.send_many([frame(0x33, 0x01, 0x01), frame(0xAA, 0x01)])
    assert fake.establish_calls == 0 and fake.received == []  # nothing sent, not even frame 1


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


@pytest.mark.parametrize("exc", [RuntimeError("weird"), BleakError("nope"), BrokenPipeError()])
async def test_start_notify_failure_closes_link(monkeypatch, exc) -> None:
    fake = FakeGovee()
    fake.start_notify_error = exc
    c = _make(monkeypatch, fake)
    # WAS: the fake ignored `exc` (always BleakError) and the tuple let a bare RuntimeError through.
    # FIXED: whatever start_notify raises, connect() raises a GoveeBleConnectionError chained to it.
    with pytest.raises(GoveeBleConnectionError, match="connect failed") as info:
        await c.connect()
    assert info.value.__cause__ is exc
    assert len(fake.clients) == const.SESSION_ATTEMPTS  # a failed connect is retried
    assert all(not cl.is_connected for cl in fake.clients)
    assert c._client is None and c._ready is False


async def test_operation_timeout_during_establish_leaves_no_tracked_garbage(monkeypatch) -> None:
    entered = asyncio.Event()

    async def hang(*_a, **_k):
        entered.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(conn_mod, "establish_connection", hang)
    monkeypatch.setattr(conn_mod, "OPERATION_TIMEOUT", 0.05)
    c = GoveeConnection(ble_device(ADDR, "x"), encryption=Encryption.NONE)
    c._idle_disconnect = 0
    with pytest.raises(GoveeBleTimeout, match="operation exceeded"):  # never a bare TimeoutError
        await c.connect()
    assert entered.is_set()  # the deadline fired inside establish
    assert c._client is None and c._ready is False and not c._lock.locked()


async def test_drop_after_ready_clears_ready(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.clients[0].drop()
    assert c._ready is False and c._client is None and not c.is_connected


# -- DES-9 / capture mask ----------------------------------------------------

def test_named_constants_in_use() -> None:
    assert conn_mod.QUERY_DEFAULT_TIMEOUT == const.QUERY_DEFAULT_TIMEOUT
    assert conn_mod.UNACKED_SEND_GAP == const.UNACKED_SEND_GAP
    kw = GoveeConnection.query.__kwdefaults__ or {}
    assert kw["timeout"] == const.QUERY_DEFAULT_TIMEOUT
    assert (GoveeConnection.send_unacked.__kwdefaults__ or {})["gap"] == const.UNACKED_SEND_GAP


def test_known_rx_types_are_the_specs_rx_pro_type() -> None:
    # The spec's RX routes (what a device sends): the old rx_pro_type set, and the 0x05 / 0x3F / 0x66 /
    # 0xA5 / 0xB0 / 0xB3 / 0xB4 heads the frame names; none of them is logged as unknown.
    from govee_ble_local.wire import _spec

    rx_routes = frozenset(int(r) & 0xFF for r in _spec.Frame.Route if int(r) & 0x100)
    assert rx_routes == conn_mod._KNOWN_RX_PRO_TYPES
    assert frozenset(
        {0x33, 0x3A, 0xAA, 0xAC, 0xEE, 0xA1, 0xA2, 0xA3, 0xA4, 0xA6, 0xAB, 0xE7, 0x05, 0x3F, 0x66, 0xA5, 0xB0,
         0xB3, 0xB4}
    ) == conn_mod._KNOWN_RX_PRO_TYPES


async def test_capture_and_mask_secret_share_one_helper(monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="govee_ble_local.frames")
    c = _make(monkeypatch, FakeGovee(encrypted=False))
    plain = frame(0x33, 0xB2, 1, 2, 3, 4, 5, 6, 7, 8)
    c._capture("tx", wire=plain, plain=plain, enc="none")
    masked = redact.mask_secret(plain).hex()
    assert f"plain={masked} wire={masked}" in caplog.text


class _CaptureLog:
    """A frame log that keeps the records it is handed."""

    def __init__(self) -> None:
        self.records: list[tuple[bytes, bytes | None]] = []

    def record(self, _direction: str, *, wire: bytes, plain: bytes | None = None, enc: str = "") -> None:
        self.records.append((wire, plain))


_SECRET = bytes([0xDE, 0xAD, 0xBE, 0xEF, 0xC0, 0xFE, 0xE1, 0x9D])


@pytest.mark.parametrize("encrypted", [False, True])
@pytest.mark.parametrize("case", ["ready", "bad_checksum", "torn_down"])
async def test_a_secret_reply_never_reaches_the_capture_or_log(monkeypatch, caplog, encrypted, case) -> None:
    """An rx aa b1 secret reply: masked by its header while the session is ready (a bad checksum too, in
    the drop log), and after the teardown cleared it masked by its header on a plaintext link, kept only as
    its length on an encrypted one. The teardown case is a late or repeated aa b1 reply landing while
    stop() resets the session, shown with this fake, not seen on hardware. On an encrypted link a secret
    frame's wire is blanked whole, and the e7 handshake frames are kept only as their length too."""
    caplog.set_level(logging.DEBUG, logger="govee_ble_local")
    c = _make(monkeypatch, FakeGovee(encrypted=encrypted))
    c._framelog = log = _CaptureLog()   # type: ignore[assignment]
    await c.connect()
    if encrypted:   # the handshake: every e7 frame withheld (its bytes plus the PSK give the session key)
        assert log.records and all(w == b"\xff" * len(w) and p is None for w, p in log.records)
        assert " cipher plain=" not in caplog.text and " withheld plain=" in caplog.text
    log.records.clear()
    plain = frame(0xAA, 0xB1, 0x01, *_SECRET)
    if case == "bad_checksum":
        plain = plain[:-1] + bytes([plain[-1] ^ 0xFF])
    raw = encrypt(plain, SESSION_KEY) if encrypted else plain
    if case == "torn_down":
        c._reset_session()   # as _disconnect_locked does before client.disconnect()
    caplog.clear()
    c._handle_notify(None, bytearray(raw))
    [(wire, captured)] = log.records
    masked_wire = b"\xff" * FRAME_LEN if encrypted else redact.mask_secret(plain)
    assert (wire, captured) == (masked_wire, None if case == "torn_down" else redact.mask_secret(plain))
    if case == "bad_checksum":
        assert f"bad checksum: {redact.shown_hex(plain)}" in caplog.text
    if case == "torn_down" and encrypted:
        assert f" withheld plain= wire={'ff' * FRAME_LEN} " in caplog.text
    shown = caplog.text
    for leaked in (plain[2:], raw if encrypted else b""):
        assert not any(leaked[i:i + 2].hex() in shown for i in range(len(leaked) - 1)), shown
    if case == "torn_down" and not encrypted:   # a frame that carries no secret is captured as it came
        other = frame(0xAA, 0x01, 0x01)
        c._handle_notify(None, bytearray(other))
        assert log.records[-1] == (other, None)
    await c.disconnect()


def test_now_ts_removed() -> None:
    assert not hasattr(conn_mod, "now_ts")


# -- late handshake retransmit filter (deterministic) -------------------------

async def test_late_handshake_retransmit_is_filtered(monkeypatch, caplog) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    # Synchronously, before any await: the fake's own late retransmits can't race the asserts.
    caplog.set_level(logging.DEBUG, logger="govee_ble_local.transport.connection")
    assert c._handshake_echoes
    for echo in c._handshake_echoes:
        c._handle_notify(None, bytearray(echo))
    # WAS covered only by the checksum guard, which a random echo passes ~1 time in 256.
    assert c._rx.qsize() == 0
    assert caplog.text.count("dropped late handshake retransmit") == len(c._handshake_echoes)


async def test_handshake_echo_filter_is_time_boxed(monkeypatch, caplog) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    c._echo_filter_until = 0  # the window has closed
    caplog.set_level(logging.DEBUG, logger="govee_ble_local.transport.connection")
    c._handle_notify(None, bytearray(next(iter(c._handshake_echoes))))
    # Past the window the frame takes the normal (decrypt + checksum) path; whether its random
    # fill then passes the checksum is luck, so only the filter's own verdict is asserted.
    assert "dropped late handshake retransmit" not in caplog.text


# -- stale frames are drained before every operation ---------------------------

async def test_stale_ack_is_drained_before_write(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    c._enqueue(frame(0x33, 0x01, 0x00))  # stale success ack from an earlier write
    fake.ack_status = 2                   # this write is rejected
    with pytest.raises(GoveeBleCommandRejected, match="rejected"):
        await c.send(frame(0x33, 0x01, 0x01))


async def test_stale_ack_arriving_via_notify_is_drained_before_write(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    c._handle_notify(None, bytearray(encrypt(frame(0x33, 0x01, 0x00), SESSION_KEY)))  # duplicate ack
    assert c._rx.qsize() == 1
    fake.ack_status = 2
    with pytest.raises(GoveeBleCommandRejected):
        await c.send(frame(0x33, 0x01, 0x01))


async def test_stale_burst_is_drained_before_query(monkeypatch) -> None:
    fake = FakeGovee()
    fake.status_burst = [frame(0xAC, 0x00, 7), frame(0xAC, 0xFF, 8)]
    c = _make(monkeypatch, fake)
    await c.connect()
    c._enqueue(frame(0xAC, 0xFF, 1))  # stale terminator from a previous burst
    assert await c.query(frame(0xAC, 0x03), timeout=1.0) == fake.status_burst


async def test_stale_frames_are_drained_before_unacked_sends(monkeypatch) -> None:
    fake = FakeGovee()
    fake.no_ack_cmds = {0x01}  # nothing new arrives: only a leftover frame could be queued
    c = _make(monkeypatch, fake)
    await c.connect()
    c._enqueue(frame(0xEE, 0x01))  # unsolicited push between operations
    await c.send(frame(0x33, 0x01, 0x01), expect_ack=False)
    assert c._rx.qsize() == 0
    c._enqueue(frame(0xEE, 0x01))
    await c.send_unacked([frame(0xA3, 0x00, 0x01)], gap=0)
    assert c._rx.qsize() == 0


async def test_a_superseded_links_notifications_never_reach_the_next_session(monkeypatch, caplog) -> None:
    # A link whose teardown wedged (abandoned at DISCONNECT_TIMEOUT) is still up and still notifies: its
    # e7 01 reply, carrying its own session's key, lands in the next link's handshake ahead of that link's.
    stale_key = bytes(range(16, 32))

    class Superseded(FakeGovee):
        def handle_write(self, client, data) -> None:
            if client is self.clients[-1] and len(self.clients) > 1 and decrypt(data, PSK)[:2] == b"\xe7\x01":
                self.clients[0]._notify(None, bytearray(encrypt(frame(0xE7, 0x01, *stale_key), PSK)))
            super().handle_write(client, data)

    caplog.set_level(logging.DEBUG, logger="govee_ble_local.transport.connection")
    fake = Superseded()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.disconnect_hangs = True
    await c.disconnect()   # abandoned at its bound: the old link stays up
    fake.disconnect_hangs = False
    assert fake.clients[0].is_connected
    caplog.clear()
    await c.connect()
    assert c._session_key == SESSION_KEY   # the new link's own key, not the stale one
    assert "dropped a notification from a superseded link" in caplog.text
    assert await c.send(frame(0x33, 0x01, 0x01)) == frame(0x33, 0x01, 0x00)   # and the session works
    fake.clients[1]._notify(None, bytearray(encrypt(frame(0x33, 0x01, 0x00), SESSION_KEY)))
    assert c._rx.qsize() == 1   # the current link's frames are still taken
    await c.disconnect()


# -- the reply wait never swallows a cancellation ------------------------------

@pytest.mark.parametrize("ticks", [0, 1, 2])
async def test_a_cancellation_of_a_reply_wait_is_never_swallowed(ticks: int) -> None:
    # gh-86296: on Python 3.11 asyncio.wait_for returned a frame that its inner task had taken when the
    # caller's cancellation (the operation deadline, a connect limit) arrived, and swallowed the
    # cancellation. The reply wait is an asyncio.timeout in the caller's own task: a cancellation the
    # waiting task accepts is raised, and the frame stays queued.
    c = GoveeConnection(ble_device(ADDR, "GVH60A6X"))
    reply = frame(0x33, 0x01, 0x00)
    waiter = asyncio.ensure_future(c._next_rx(5.0))
    await asyncio.sleep(0)   # waiting on the empty queue
    c._enqueue(reply)
    for _ in range(ticks):   # the cancellation lands this many loop iterations after the frame
        await asyncio.sleep(0)
    if waiter.cancel():   # accepted: the wait had not returned
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert c._rx.get_nowait() == reply
    else:   # the wait had returned the frame first
        assert ticks and waiter.result() == reply


# -- query(): frames already queued at the deadline are collected --------------

async def test_query_collects_frames_queued_when_budget_runs_out(monkeypatch) -> None:
    fake = FakeGovee(encrypted=False)
    # A three-packet burst: the first chunk declares 3 packets (u16le) and a 1-byte last packet.
    burst = [frame(0xAC, 0x00, 0x03, 0x00, 0x01), frame(0xAC, 0x01, 2), frame(0xAC, 0xFF, 3)]

    def deliver_whole_burst_now(client, plain: bytes) -> None:
        if plain[0] == 0xAC:
            for f in burst:  # already queued by the time query() starts waiting
                client._notify(None, bytearray(f))

    fake.on_write = deliver_whole_burst_now
    c = _make(monkeypatch, fake)
    await c.connect()
    # WAS: query() broke out on `remaining <= 0` before reading the queue, so a fully received
    # burst (terminator included) was dropped and later thrown away by the next _drain() - the
    # BUG-7 fix in _next_rx never reached query(). A zero budget is the deterministic stand-in
    # for event-loop lag past the deadline.
    assert await c.query(frame(0xAC, 0x03), timeout=0) == burst
    assert c._rx.qsize() == 0


# -- BGC lookup is scoped to the control service --------------------------------

async def test_bgc_lookup_scoped_to_control_service(monkeypatch) -> None:
    fake = FakeGovee()
    fake.duplicate_bgc_char = True  # 2b12 also under the OTA service: a global lookup raises
    c = _make(monkeypatch, fake)
    # WAS: client.services.get_characteristic raised "Multiple Characteristics", outside the
    # try, so every connect failed. FIXED: looked up under service 1910, like BgcInfoReader.a.
    await c.connect()
    assert c.is_connected and c._bgc_checked and fake.bgc_reads == 1
    assert len(fake.clients) == 1
    assert [ch.service.uuid for ch in fake.bgc_read_chars] == [const.SERVICE_UUID]  # not OTA 1912


async def test_bgc_lookup_takes_the_first_control_service(monkeypatch) -> None:
    fake = FakeGovee()
    # OTA first (it carries 2b12 too), then 1910 twice: bleak's get_service(1910) raises
    # "Multiple Services with this UUID".
    fake.gatt_services = [OTA_SERVICE_UUID, const.SERVICE_UUID, const.SERVICE_UUID]
    c = _make(monkeypatch, fake)
    await c.connect()
    # WAS: the get_service() BleakError skipped the BGC read and kept the advertised mode.
    # BgcInfoReader.a (BgcInfoReader.java:58-72) reads 2b12 from the FIRST 1910 service.
    assert c.is_connected and c._bgc_checked and fake.bgc_reads == 1
    first_control = list(fake.clients[0].services)[1]
    assert [ch.service for ch in fake.bgc_read_chars] == [first_control]


async def test_bgc_lookup_error_is_transient(monkeypatch) -> None:
    fake = FakeGovee()
    fake.services_error = BleakError("Service Discovery has not been performed yet")
    c = _make(monkeypatch, fake)
    await c.connect()  # falls back to the advertised mode, like a failed BGC read
    assert c.is_connected and c._bgc_checked is False and fake.bgc_reads == 0
