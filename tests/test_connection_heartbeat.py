"""The transport's heartbeat (GoveeConnection(heartbeat=...), spec devices.yaml timing.heartbeat) against the
generic fake peripheral, which answers every aa read 5 ms later, on a virtual clock: the cadence and its two
timings, the busy rules, the idle policy it never extends, teardown, failures and the reply it leaves
queued."""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging

import pytest
from bleak.exc import BleakError

from govee_ble_local.exceptions import GoveeBleAuthError, GoveeBleInvalidValue, GoveeBleNotSupported
from govee_ble_local.models import Encryption
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.transport.connection import GoveeConnection
from govee_ble_local.wire import build

from fake_ble import FakeGovee, ble_device, frame, virtual_time

ADDR = "AA:BB:CC:DD:EE:01"
BEAT = build.heartbeat(0x01)          # aa 01 00x17 ab
BEAT00 = build.heartbeat(0x00)        # aa 00 00x17 aa
WRITE = frame(0x33, 0x04, 0x10)


@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.1)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_STEP2_TIMEOUT", 0.1)


def _make(monkeypatch: pytest.MonkeyPatch, fake: FakeGovee, *, idle: float = 0,
          heartbeat: bytes | None = BEAT, **kw: object) -> GoveeConnection:
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    enc = Encryption.AES_RC4_PSK if fake.encrypted else Encryption.NONE
    c = GoveeConnection(ble_device(ADDR, "GVH6006X"), encryption=enc, heartbeat=heartbeat,
                        **kw)  # type: ignore[arg-type]
    c._idle_disconnect = idle
    return c


def beats(fake: FakeGovee, beat: bytes = BEAT) -> int:
    return sum(1 for f in fake.received if f == beat)


@pytest.mark.parametrize("encrypted", [True, False])
async def test_cadence_and_no_beat_at_link_up(monkeypatch: pytest.MonkeyPatch, fast: None,
                                              encrypted: bool) -> None:
    fake = FakeGovee(encrypted=encrypted)
    c = _make(monkeypatch, fake)
    with virtual_time():
        await c.connect()
        assert beats(fake) == 0                     # none when the session comes up
        await asyncio.sleep(conn_mod.HEARTBEAT_INTERVAL - 0.01)
        assert beats(fake) == 0
        await asyncio.sleep(0.02)
        assert beats(fake) == 1                     # one interval after the session is set up
        await asyncio.sleep(10 * conn_mod.HEARTBEAT_INTERVAL)
        assert beats(fake) == 11                    # a fixed rate
    # The device received the plaintext frame the app sends, on either cipher.
    assert all(f == BEAT for f in fake.received if f[0] == 0xAA)
    await c.close()


@pytest.mark.parametrize("timing", ["fixed_rate", "after_quiet"])
async def test_no_heartbeat_configured_sends_nothing(monkeypatch: pytest.MonkeyPatch, fast: None,
                                                     timing: str) -> None:
    # Without a heartbeat the timing is ignored: no chain on either, nor after_quiet's restart on a write.
    fake = FakeGovee()
    c = _make(monkeypatch, fake, heartbeat=None, heartbeat_timing=timing)
    with virtual_time():
        await c.connect()
        await c.send(WRITE)
        await asyncio.sleep(10)
    assert not [f for f in fake.received if f[0] == 0xAA] and c._heartbeat_timer is None
    assert c._heartbeat_beat is None
    await c.close()


async def test_heartbeat_never_extends_the_idle_timer(monkeypatch: pytest.MonkeyPatch, fast: None) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake, idle=8.0)
    loop = asyncio.get_running_loop()
    with virtual_time():
        await c.query(frame(0xAA, 0x04), opcode=0xAA, terminal=0x04, idle_gap=None)
        done = loop.time()
        gen, until = c._idle_generation, c._active_until
        await asyncio.sleep(7.9)
        assert c.is_connected and beats(fake) == 3 and c._idle_generation == gen and c._active_until == until
        await asyncio.sleep(0.2)
        assert not c.is_connected and fake.disconnects == 1   # the 8 s after the read, as without beats
        assert loop.time() - done < 8.2
        n = beats(fake)
        await asyncio.sleep(10)
        assert beats(fake) == n and c._heartbeat_timer is None   # stopped with the idle disconnect


async def test_heartbeat_keeps_serving_the_warm_window(monkeypatch: pytest.MonkeyPatch, fast: None) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake, idle=1.0)     # Home Assistant's POLL_IDLE_DISCONNECT_SECONDS
    with virtual_time():
        await c.send(frame(0x33, 0x01, 0x01))
        await asyncio.sleep(29.0)
        assert c.is_connected and beats(fake) == 14
        await asyncio.sleep(1.5)
        assert not c.is_connected and beats(fake) == 14 and c._heartbeat_timer is None


async def test_with_idle_disconnect_off_beats_run_until_the_link_drops(monkeypatch: pytest.MonkeyPatch,
                                                                        fast: None) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake, idle=0)
    with virtual_time():
        await c.connect()
        await asyncio.sleep(60.5)
        assert c.is_connected and beats(fake) == 30
        fake.clients[-1].drop()
        await asyncio.sleep(10)
        assert not c.is_connected and beats(fake) == 30 and c._heartbeat_timer is None
    await c.close()


async def test_a_busy_tick_is_dropped_not_queued(monkeypatch: pytest.MonkeyPatch, fast: None) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    gate = asyncio.Event()
    with virtual_time():
        await c.connect()

        async def slow() -> None:            # an operation holding the link across three ticks
            await c._ensure_connected_locked()
            await gate.wait()

        op = asyncio.ensure_future(c._run(slow, extend_warm=False))
        await asyncio.sleep(6.5)
        assert beats(fake) == 0
        gate.set()
        await op
        await asyncio.sleep(0.01)
        assert beats(fake) == 0                 # nothing sent on release: the skipped ticks are gone
        await asyncio.sleep(conn_mod.HEARTBEAT_INTERVAL)
        assert beats(fake) == 1
    await c.close()


async def test_a_waiting_operation_drops_the_tick(monkeypatch: pytest.MonkeyPatch, fast: None) -> None:
    # asyncio.Lock.locked() is False between a release and the next waiter's wake-up; the counted lock
    # still marks the link busy, so a tick there sends nothing.
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    c._lock_wanted += 1
    try:
        c._heartbeat_fired(c._client)  # type: ignore[arg-type]
        await asyncio.sleep(0)
        assert c._heartbeat_beat is None and beats(fake) == 0
    finally:
        c._lock_wanted -= 1
    await c.close()


@pytest.mark.parametrize("hold", [False, True])
async def test_a_beat_created_before_an_operation_is_dropped_not_queued(monkeypatch: pytest.MonkeyPatch,
                                                                         fast: None, hold: bool) -> None:
    # A tick finds the link free and creates its beat, but before the beat starts an operation takes the lock
    # (without a hold) or a hold_heartbeat() opens (with one, as update() opens it before its first read). The
    # beat checks again as it starts and sends nothing: it doesn't queue on the lock and go out once the
    # operation ends. The operation never cancels it (the lock was free when it came), so it ends on its own.
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    gate = asyncio.Event()
    with virtual_time():
        await c.connect()

        async def slow() -> None:            # an operation that holds the link until the gate opens
            await c._ensure_connected_locked()
            await gate.wait()

        with contextlib.ExitStack() as stack:
            op = None if hold else asyncio.ensure_future(c._run(slow, extend_warm=False))   # not started yet
            c._heartbeat_fired(c._client)  # type: ignore[arg-type]   # the tick: the link is free
            beat = c._heartbeat_beat
            assert beat is not None and not beat.done()
            if hold:
                stack.enter_context(c.hold_heartbeat())
                op = asyncio.ensure_future(c._run(slow, extend_warm=False))
            await asyncio.sleep(0)            # the operation (if queued first) takes the lock, then the beat
            assert beat.done() and not beat.cancelled() and c._lock.locked()
            gate.set()
            await op
            await asyncio.sleep(0.01)
            assert beats(fake) == 0           # not sent when the operation ended
        await asyncio.sleep(conn_mod.HEARTBEAT_INTERVAL)
        assert beats(fake) == 1               # the chain runs on: the next tick beats
    await c.close()


async def test_every_lock_site_is_counted(monkeypatch: pytest.MonkeyPatch, fast: None) -> None:
    # An operation, disconnect() and the idle disconnect each count themselves while they wait for the lock.
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    seen: list[int] = []
    async with c._lock:   # held, as a beat holds it while it writes
        waiters = [asyncio.ensure_future(c.send(WRITE)), asyncio.ensure_future(c.disconnect()),
                   asyncio.ensure_future(c._idle_disconnect_task(c._idle_generation))]
        await asyncio.sleep(0)
        seen.append(c._lock_wanted)
    await asyncio.gather(*waiters)
    assert seen == [3] and c._lock_wanted == 0
    await c.close()


async def test_a_hold_stops_beats_between_operations(monkeypatch: pytest.MonkeyPatch, fast: None) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    with virtual_time():
        await c.connect()
        with c.hold_heartbeat():
            for _ in range(4):
                await c.query(frame(0xAA, 0x04), opcode=0xAA, terminal=0x04, idle_gap=None)
                await asyncio.sleep(1.0)       # e.g. a scene catalog load off the loop
            with c.hold_heartbeat():           # holds nest
                await asyncio.sleep(2.5)
            assert beats(fake) == 0 and c._holds == 1
        assert c._holds == 0
        await asyncio.sleep(conn_mod.HEARTBEAT_INTERVAL)
        assert beats(fake) == 1
        with pytest.raises(RuntimeError), c.hold_heartbeat():
            raise RuntimeError
        assert c._holds == 0                   # released when the block raises
    await c.close()


@pytest.mark.parametrize("timing", ["after_quiet", "fixed_rate"])
async def test_after_quiet_beats_only_after_two_quiet_seconds(monkeypatch: pytest.MonkeyPatch, fast: None,
                                                              timing: str) -> None:
    # goodsType 43's HeartHandler.e restarts the wait on every send: an operation every 1.5 s leaves no room
    # for a beat, where the fixed rate beats between them; once idle, one every 2 s either way.
    fake = FakeGovee()
    c = _make(monkeypatch, fake, heartbeat_timing=timing)
    loop = asyncio.get_running_loop()
    sent: list[float] = []
    ends: list[float] = []
    real_write = fake.handle_write

    def stamp(client: object, data: bytes) -> None:
        n = len(fake.received)
        real_write(client, data)  # type: ignore[arg-type]
        if len(fake.received) > n and fake.received[-1] == BEAT:
            sent.append(loop.time())

    fake.handle_write = stamp  # type: ignore[method-assign]
    with virtual_time():
        await c.connect()
        ends.append(loop.time())
        for _ in range(6):
            await asyncio.sleep(1.5)
            await c.send(WRITE)
            ends.append(loop.time())
        busy = beats(fake)
        await asyncio.sleep(6.5)
        idle = beats(fake) - busy
    await c.close()
    if timing == "fixed_rate":
        assert busy == 4 and idle == 3
        return
    assert busy == 0 and idle == 3
    assert all(t - max(e for e in ends if e <= t) >= conn_mod.HEARTBEAT_INTERVAL - 1e-6 for t in sent)
    assert [round(b - a, 6) for a, b in itertools.pairwise(sent)] == [conn_mod.HEARTBEAT_INTERVAL] * 2


@pytest.mark.parametrize("timing", ["after_quiet", "fixed_rate"])
async def test_after_quiet_a_send_in_the_ticks_loop_pass_drops_its_beat(monkeypatch: pytest.MonkeyPatch,
                                                                         fast: None, timing: str) -> None:
    # A tick whose beat is created but not started when an operation sends a frame and ends without yielding
    # (an unacked send over a write that doesn't yield: this fake's, a proxy's write-without-response). On
    # after_quiet that send restarts the wait (HeartHandler.e), so the beat is dropped, not sent right behind
    # the frame; on fixed_rate the beat due at the tick still goes out.
    fake = FakeGovee()
    c = _make(monkeypatch, fake, heartbeat_timing=timing)
    with virtual_time():
        await c.connect()
        c._heartbeat_fired(c._client)  # type: ignore[arg-type]   # the tick: its beat not started yet
        assert c._heartbeat_beat is not None and not c._heartbeat_beat.done()
        await c.send(frame(0x33, 0x05, 0x01), expect_ack=False)   # runs and ends within this loop pass
        await asyncio.sleep(conn_mod.HEARTBEAT_INTERVAL - 0.01)
        assert beats(fake) == (0 if timing == "after_quiet" else 1)
        await asyncio.sleep(0.02)
        assert beats(fake) == (1 if timing == "after_quiet" else 2)
    await c.close()


async def test_after_quiet_restarts_only_on_an_operation_that_sends(monkeypatch: pytest.MonkeyPatch,
                                                                     fast: None) -> None:
    # connect() on a live link (Device.connect) writes nothing, so it doesn't restart the wait: HeartHandler.e
    # restarts it on a send. Called every 1.5 s, it leaves the beat at every 2 s from the session's set-up.
    fake = FakeGovee()
    c = _make(monkeypatch, fake, heartbeat_timing="after_quiet")
    loop = asyncio.get_running_loop()
    sent: list[float] = []
    real_write = fake.handle_write

    def stamp(client: object, data: bytes) -> None:
        n = len(fake.received)
        real_write(client, data)  # type: ignore[arg-type]
        if len(fake.received) > n and fake.received[-1] == BEAT:
            sent.append(loop.time())

    fake.handle_write = stamp  # type: ignore[method-assign]
    with virtual_time():
        await c.connect()
        ready = loop.time()
        writes = len(fake.received)
        for _ in range(8):
            await asyncio.sleep(1.5)
            await c.connect()
        assert len(fake.received) - writes == len(sent) == 6    # only beats: at +2, +4 .. +12 s
        assert [round(t - ready, 6) for t in sent] == [2.0 * k for k in range(1, 7)]
        await c.send(WRITE)                                     # a send does restart it
        wrote = loop.time()
        await asyncio.sleep(1.9)
        assert len(sent) == 6
        await asyncio.sleep(0.2)
        assert len(sent) == 7 and round(sent[-1] - wrote, 6) == conn_mod.HEARTBEAT_INTERVAL
    await c.close()


async def test_a_beat_writing_at_the_idle_expiry_does_not_delay_the_close(monkeypatch: pytest.MonkeyPatch,
                                                                          fast: None) -> None:
    # The idle disconnect cancels a beat still writing instead of waiting HEARTBEAT_WRITE_TIMEOUT for the
    # lock: the link closes 8 s after the read, as it does without a heartbeat.
    fake = FakeGovee()
    c = _make(monkeypatch, fake, idle=8.0)
    loop = asyncio.get_running_loop()
    with virtual_time():
        await c.connect()
        await asyncio.sleep(0.5)                # the read ends off the session's 2 s grid
        await c.query(frame(0xAA, 0x04), opcode=0xAA, terminal=0x04, idle_gap=None)
        end = loop.time()
        client = fake.clients[-1]
        real = client.write_gatt_char

        async def wedged(uuid: str, data: bytes, response: bool = False) -> None:
            if loop.time() - end > 7.0:         # the beat just before the idle expiry hangs
                await asyncio.sleep(3600)
            await real(uuid, data, response)

        client.write_gatt_char = wedged  # type: ignore[method-assign]
        await asyncio.sleep(7.99)
        beat = c._heartbeat_beat
        assert c.is_connected and beat is not None and not beat.done() and c._lock.locked()
        assert beats(fake) == 3                 # +1.5, +3.5, +5.5; the one at +7.5 hangs
        await asyncio.sleep(0.02)               # 8.01 s after the read: closed, the beat cancelled
        assert not c.is_connected and beat.cancelled() and not c._lock.locked()
        assert fake.disconnects == 1 and loop.time() - end < 8.0 + conn_mod.HEARTBEAT_WRITE_TIMEOUT / 2


async def test_teardown_and_close_stop_the_heartbeat(monkeypatch: pytest.MonkeyPatch, fast: None) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    with virtual_time():
        await c.connect()
        await asyncio.sleep(2.1)
        assert beats(fake) == 1
        await c.disconnect()
        assert c._heartbeat_timer is None
        await asyncio.sleep(10)
        assert beats(fake) == 1
        await c.connect()                       # a new session re-arms
        await asyncio.sleep(2.1)
        assert beats(fake) == 2
        await c.close()
        await asyncio.sleep(10)
        assert beats(fake) == 2 and c._heartbeat_timer is None and not c._background


async def test_close_during_a_beat_leaves_no_timer_or_task(monkeypatch: pytest.MonkeyPatch,
                                                            fast: None) -> None:
    monkeypatch.setattr(conn_mod, "HEARTBEAT_WRITE_TIMEOUT", 0.5)
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    with virtual_time():
        await c.connect()
        client = fake.clients[-1]
        real = client.write_gatt_char

        async def wedged(uuid: str, data: bytes, response: bool = False) -> None:
            await asyncio.sleep(3600)
            await real(uuid, data, response)

        client.write_gatt_char = wedged  # type: ignore[method-assign]
        await asyncio.sleep(2.1)
        beat = c._heartbeat_beat
        assert beat is not None and not beat.done() and c._lock.locked()
        loop = asyncio.get_running_loop()
        closing = loop.time()
        await c.close()                         # cancels the beat, then tears down: no wait for its bound
        assert loop.time() - closing < 0.01
        await asyncio.sleep(0)
        assert beat.cancelled() and c._heartbeat_timer is None and not c._background
        await asyncio.sleep(10)
        assert c._heartbeat_timer is None and not c._background


async def test_a_close_cancelled_while_it_waits_stops_the_chain(monkeypatch: pytest.MonkeyPatch,
                                                                 fast: None) -> None:
    # close() stops the chain before it waits for the lock: cancelled there (behind an operation holding the
    # link), it leaves no tick armed though its teardown never ran, and no beat goes out after it. WAS: the
    # tick stayed armed until it next fired and found the connection closed.
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    gate = asyncio.Event()
    with virtual_time():
        await c.connect()

        async def slow() -> None:
            await c._ensure_connected_locked()
            await gate.wait()

        op = asyncio.ensure_future(c._run(slow, extend_warm=False))
        await asyncio.sleep(0)
        closing = asyncio.ensure_future(c.close())
        await asyncio.sleep(0)
        assert not closing.done() and c._lock_wanted == 2      # waits behind the operation
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert c._heartbeat_timer is None and c.is_connected    # the chain stopped; the link not torn down
        gate.set()
        await op
        await asyncio.sleep(10 * conn_mod.HEARTBEAT_INTERVAL)
        assert beats(fake) == 0 and c._heartbeat_timer is None and c._heartbeat_beat is None
        await c.close()                         # closed again: the teardown runs
    assert not c.is_connected and fake.disconnects == 1


async def test_link_loss_stops_it_and_a_reconnect_runs_one_chain(monkeypatch: pytest.MonkeyPatch,
                                                                  fast: None) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    with virtual_time():
        await c.connect()
        old = fake.clients[-1]
        old.drop()
        assert c._heartbeat_timer is None
        before = len(old.writes)
        await asyncio.sleep(5)
        assert len(old.writes) == before and beats(fake) == 0
        await c.connect()
        new = fake.clients[-1]
        await asyncio.sleep(4.1)
        assert len(old.writes) == before and beats(fake) == 2   # only the new link beats, once per tick
        assert new is not old
    await c.close()


async def test_a_failed_beat_is_ignored(monkeypatch: pytest.MonkeyPatch, fast: None,
                                        caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    with virtual_time():
        await c.connect()
        fake.write_error = BleakError("write failed")
        with caplog.at_level(logging.DEBUG, logger="govee_ble_local.transport.connection"):
            await asyncio.sleep(2.1)            # the beat's write raises: logged, nothing else
        assert c.is_connected and fake.disconnects == 0
        assert [r.levelno for r in caplog.records if "heartbeat" in r.getMessage()] == [logging.DEBUG]
        fake.write_error = None
        ack = await c.send(frame(0x33, 0x01, 0x01))
        assert ack is not None and len(fake.clients) == 1


async def test_an_unexpected_beat_error_is_logged_at_debug(monkeypatch: pytest.MonkeyPatch, fast: None,
                                                           caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    with virtual_time():
        await c.connect()
        fake.write_error = RuntimeError("backend")
        with caplog.at_level(logging.DEBUG, logger="govee_ble_local.transport.connection"):
            await asyncio.sleep(2.1)
        failed = [r for r in caplog.records if "heartbeat failed" in r.getMessage()]
        assert [r.levelno for r in failed] == [logging.DEBUG] and failed[0].exc_info is not None
        assert c.is_connected and fake.disconnects == 0
        fake.write_error = None
    await c.close()


async def test_a_hanging_beat_write_gives_the_lock_up(monkeypatch: pytest.MonkeyPatch, fast: None) -> None:
    monkeypatch.setattr(conn_mod, "HEARTBEAT_WRITE_TIMEOUT", 0.5)
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    with virtual_time():
        await c.connect()
        client = fake.clients[-1]
        real = client.write_gatt_char
        hang = [True]

        async def wedged(uuid: str, data: bytes, response: bool = False) -> None:
            if hang[0]:
                await asyncio.sleep(3600)
            await real(uuid, data, response)

        client.write_gatt_char = wedged  # type: ignore[method-assign]
        await asyncio.sleep(2.1)
        assert c._lock.locked()                 # the beat holds it while its write runs
        await asyncio.sleep(0.5)
        assert not c._lock.locked()             # bounded: given up
        hang[0] = False
        assert await c.send(frame(0x33, 0x01, 0x01)) is not None
    await c.close()


async def test_an_operation_does_not_queue_behind_a_beat_still_writing(monkeypatch: pytest.MonkeyPatch,
                                                                        fast: None) -> None:
    # The app writes its beat off its command queue, so a command never waits for one: an operation that wants
    # the link while a beat writes cancels that write. Queued behind it, the send would wait up to 0.85 s
    # here, and its idle close and warm window would move as much later.
    fake = FakeGovee(encrypted=False)
    c = _make(monkeypatch, fake, idle=8.0)
    loop = asyncio.get_running_loop()
    with virtual_time():
        await c.connect()
        client = fake.clients[-1]
        real = client.write_gatt_char

        async def slow_beat(uuid: str, data: bytes, response: bool = False) -> None:
            if bytes(data) == BEAT:
                await asyncio.sleep(0.9)
            await real(uuid, data, response)

        client.write_gatt_char = slow_beat  # type: ignore[method-assign]
        await asyncio.sleep(conn_mod.HEARTBEAT_INTERVAL + 0.05)
        beat = c._heartbeat_beat
        assert beat is not None and not beat.done() and c._lock.locked()
        t = loop.time()
        assert await c.send(WRITE) is not None
        assert loop.time() - t < 0.1 and beat.cancelled() and beats(fake) == 0
        assert abs(c._active_until - (t + conn_mod.ACTIVE_IDLE_DELAY)) < 0.1
        assert not c._lock.locked() and c._lock_wanted == 0
    await c.close()


async def test_a_reconnect_does_not_wait_for_a_beat_hung_on_the_lost_link(monkeypatch: pytest.MonkeyPatch,
                                                                          fast: None) -> None:
    # The link drops while a beat's write hangs: the link loss stops the chain, and the next operation cancels
    # the beat still holding the lock instead of waiting out its HEARTBEAT_WRITE_TIMEOUT before it reconnects.
    fake = FakeGovee(encrypted=False)
    c = _make(monkeypatch, fake, idle=8.0)
    loop = asyncio.get_running_loop()
    with virtual_time():
        await c.connect()
        old = fake.clients[-1]
        real = old.write_gatt_char

        async def wedged(uuid: str, data: bytes, response: bool = False) -> None:
            if bytes(data) == BEAT:
                await asyncio.sleep(3600)
            await real(uuid, data, response)

        old.write_gatt_char = wedged  # type: ignore[method-assign]
        await asyncio.sleep(conn_mod.HEARTBEAT_INTERVAL + 0.05)
        beat = c._heartbeat_beat
        assert beat is not None and not beat.done() and c._lock.locked()
        old.drop()
        assert not c.is_connected and c._heartbeat_timer is None and c._lock.locked()
        t = loop.time()
        assert await c.send(WRITE) is not None
        assert loop.time() - t < 0.1 and beat.cancelled() and fake.clients[-1] is not old
    await c.close()


async def test_a_beat_reply_arriving_inside_the_next_read(monkeypatch: pytest.MonkeyPatch,
                                                          fast: None) -> None:
    # The beat's reply comes after the next operation's drain: a same-command read takes it (the same
    # read, answered a few ms earlier), and the late real reply is drained by the operation after.
    fake = FakeGovee()
    fake.read_replies[0x01] = frame(0xAA, 0x01, 0x01)
    c = _make(monkeypatch, fake)
    with virtual_time():
        await c.connect()
        await asyncio.sleep(2.0 + 1e-4)          # the beat is written; its reply lands 5 ms later
        assert beats(fake) == 1
        got = await c.query(build.power_query(), opcode=0xAA, terminal=0x01, idle_gap=None, timeout=2.0)
        assert got[-1][:3] == bytes([0xAA, 0x01, 0x01])
        ack = await c.send(frame(0x33, 0x04, 0x10))
        assert ack is not None and ack[:2] == bytes([0x33, 0x04])
    await c.close()


async def test_a_beat_reply_is_left_for_the_next_operation(monkeypatch: pytest.MonkeyPatch,
                                                           fast: None) -> None:
    # The reply is not folded into anything: it waits in the receive queue, and the next operation drains it.
    fake = FakeGovee()
    c = _make(monkeypatch, fake, heartbeat=BEAT00)
    with virtual_time():
        await c.connect()
        await asyncio.sleep(2.1)
        assert beats(fake, BEAT00) == 1 and c._rx.qsize() == 1 and c._rx.get_nowait()[:2] == b"\xaa\x00"
        await asyncio.sleep(2.0)
        assert c._rx.qsize() == 1
        assert await c.send(WRITE) is not None and c._rx.empty()
    await c.close()


async def test_no_heartbeat_on_a_session_that_never_came_up(monkeypatch: pytest.MonkeyPatch,
                                                            fast: None) -> None:
    fake = FakeGovee()
    fake.bgc = bytes([1, 2])                    # AES-GCM: not supported
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleNotSupported):
        await c.connect()
    assert c._heartbeat_timer is None
    fake2 = FakeGovee()
    fake2.ack_status = 1                         # the unlock is refused
    c2 = _make(monkeypatch, fake2, unlock_frames=lambda: [frame(0x33, 0xB2, *range(8))])
    with pytest.raises(GoveeBleAuthError):
        await c2.connect()
    assert c2._heartbeat_timer is None


@pytest.mark.parametrize("bad", [frame(0x33, 0x01), frame(0xAA, 0x01)[:19],
                                 bytes(frame(0xAA, 0x01)[:19]) + b"\x00", "aa01"])
def test_a_heartbeat_must_be_a_checked_read(bad: bytes) -> None:
    with pytest.raises(GoveeBleInvalidValue):
        GoveeConnection(ble_device(ADDR, "x"), heartbeat=bad)


@pytest.mark.parametrize("command", [0x04, 0x41, 0x99, 0x100, -1, True, 1.0, "aa01", None])
def test_build_heartbeat_takes_only_a_keep_alive_read(command: object) -> None:
    # The four keep-alive reads only (the resolver's HEARTBEATS, as DeviceProfile.heartbeat): any other
    # command, a known one included, is a GoveeBleInvalidValue, never another read or a raw enum error.
    with pytest.raises(GoveeBleInvalidValue, match="keep-alive read"):
        build.heartbeat(command)  # type: ignore[arg-type]


@pytest.mark.parametrize("heartbeat", [BEAT, None])
def test_the_heartbeat_timing_is_checked(heartbeat: bytes | None) -> None:
    # With or without a heartbeat (without one a valid timing is ignored, an unknown one still refused).
    with pytest.raises(GoveeBleInvalidValue, match="heartbeat_timing"):
        GoveeConnection(ble_device(ADDR, "x"), heartbeat=heartbeat,
                        heartbeat_timing="later")  # type: ignore[arg-type]


def test_heartbeat_frames_are_the_apps() -> None:
    assert BEAT.hex() == "aa01" + "00" * 17 + "ab"
    assert BEAT00.hex() == "aa00" + "00" * 17 + "aa"
    assert build.heartbeat(0x33).hex() == "aa33" + "00" * 17 + "99"
    assert build.heartbeat(0x36).hex() == "aa36" + "00" * 17 + "9c"
    assert build.heartbeat(0x01) == build.power_query() and build.heartbeat(0x36) == build.bar_switch_query()
