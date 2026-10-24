"""Transport behaviour against a fake peripheral that reproduces the failure modes
seen in live captures (duplicated/retransmitted handshake replies, half-dead links,
lost terminators, late disconnect callbacks)."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from pathlib import Path

import pytest
from bleak.exc import BleakError

from govee_ble_local.crypto import checksum_ok
from govee_ble_local.exceptions import (
    GoveeBleAuthError,
    GoveeBleCommandRejected,
    GoveeBleConnectionError,
    GoveeBleInvalidValue,
    GoveeBleTimeout,
)
from govee_ble_local.models import Encryption
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.transport import framelog
from govee_ble_local.transport.connection import GoveeConnection
from govee_ble_local.wire import build, redact

from fake_ble import SESSION_KEY, FakeGovee, ble_device, frame, virtual_time

ADDR = "AA:BB:CC:DD:EE:FF"

# A real H60A6 status burst (live capture): chunks 00..08 + FF terminator.
# Device ids and MACs in it are made-up examples (checksums recomputed).
H60A6_BURST = [bytes.fromhex(h) for h in [
    "ac000a000c0300010100040164050415010a8c5b",
    "ac010707065753005e00020711105653005e00ae",
    "ac0202a60a0100290104030000070d1102005e69",
    "ac030053560100290104031104001e0f0f12079a",
    "ac04ff640000800f00231000000080000000808f",
    "ac0500000080000000804102020130020000a57e",
    "ac06110164ffae5464ffae5464ffae5464ffaeee",
    "ac0754a5110264ffae5464ffae5464ffae54644c",
    "ac08ffae54a5110364ffae5464ffae5464ffae23",
    "acff5464ffae54a5050464ffae540000000000a3",
]]


@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.1)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_STEP2_TIMEOUT", 0.1)


# query()'s idle_gap default (BURST_IDLE_GAP) is bound when the method is defined, so patching
# the module constant would change nothing; tests that need another gap pass idle_gap= instead.
IDLE_GAP = inspect.signature(GoveeConnection.query).parameters["idle_gap"].default


def spy_rx_waits(monkeypatch: pytest.MonkeyPatch, c: GoveeConnection) -> list[float]:
    """Record the wait budget handed to every ``_next_rx`` call (deterministic, no wall clock)."""
    waits: list[float] = []
    real = c._next_rx

    async def spy(timeout: float) -> bytes:
        waits.append(timeout)
        return await real(timeout)

    monkeypatch.setattr(c, "_next_rx", spy)
    return waits


def _make(monkeypatch: pytest.MonkeyPatch, fake: FakeGovee, **kw: object) -> GoveeConnection:
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    enc = Encryption.AES_RC4_PSK if fake.encrypted else Encryption.NONE
    c = GoveeConnection(ble_device(ADDR, "GVH60A6X"), encryption=enc, **kw)  # type: ignore[arg-type]
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
    waits = spy_rx_waits(monkeypatch, c)
    frames = await c.query(frame(0xAC, 0x03), timeout=5.0)
    assert len(frames) == 9
    # The final (timed-out) wait was bounded by the idle gap, not the 5 s query timeout.
    assert waits and waits[-1] <= IDLE_GAP


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
    # WAS: the private transport.connection._WriteRejected reached callers.
    with pytest.raises(GoveeBleCommandRejected, match="rejected"):
        await c.send(frame(0x33, 0x01, 0x01))
    assert len(fake.clients) == 1
    assert conn_mod._WriteRejected is GoveeBleCommandRejected  # old private name kept as an alias


async def test_send_many_rejection_is_typed(monkeypatch, fast) -> None:
    fake = FakeGovee()
    fake.ack_status = 3
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleCommandRejected, match="status 0x03"):
        await c.send_many([frame(0x33, 0x01, 0x01), frame(0x33, 0x04, 50)])
    assert len(fake.received) == 1  # stops at the first refused write


@pytest.mark.parametrize(("closed", "ack", "error", "sends"), [
    (True, frame(0xA3, 0x6E, 0x00, 0x00), None, 1),                    # the unit's ack: byte 3 is 0
    (True, frame(0xA3, 0x6E, 0x00, 0x01), GoveeBleCommandRejected, 1),  # byte 3 refuses the unit
    (True, frame(0x33, 0x05, 0x00), GoveeBleTimeout, 2),   # the last packet's 33 ack isn't the unit's
    (False, None, GoveeBleInvalidValue, 0),                # a run that no write closes is refused
])
async def test_send_many_sends_an_a3_run_and_its_write_as_one_unit(
        monkeypatch, fast, closed: bool, ack: bytes | None, error: type[Exception] | None,
        sends: int) -> None:
    # goodsType 411's colour (ColorModeControllerConfig.j): MultiController.c's 0xA3 packets, then 33 05 15
    # 00, done on an A3 6E ack (AbsControllerNoEvent4Multi.onResult at MultiController.b() = 3); without one
    # the unit goes again, whole, on a fresh link.
    fake = FakeGovee()
    unit = build.color_rgb_frames(0xFF, 0, 0, "h3010", 15)

    def on_write(client, plain: bytes) -> None:
        if ack is not None and plain == unit[-1]:
            client.push(fake._wire(ack, SESSION_KEY), 0.005)

    fake.on_write = on_write
    c = _make(monkeypatch, fake)
    sent = unit if closed else unit[:-1]
    if error is None:
        await c.send_many(sent)
    else:
        with pytest.raises(error):
            await c.send_many(sent)
    assert fake.received == unit * sends


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
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 60.0)  # far beyond the outer bound below
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
    # Only a wake-up by the drop can finish inside the 30 s bound; the ack timeout is 60 s.
    ack = await asyncio.wait_for(c.send(frame(0x33, 0x01, 0x01)), 30.0)
    assert ack is not None
    assert len(fake.clients) == 2


async def test_late_disconnect_callback_from_old_client_is_ignored(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    c = _make(monkeypatch, fake)
    await c.connect()
    old = fake.clients[0]
    await c.disconnect()
    await c.connect()
    assert c.is_connected
    c._on_disconnect(old)  # type: ignore[arg-type]  # stale callback arriving late
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


# -- session frames: once per connection, after the unlock; a refusal changes nothing ----------
_UNLOCK = frame(0x33, 0xB2, *range(8))
_SYNC = frame(0x33, 0xB5, 0x6A, 0x49, 0x90, 0x1A, 0x01, 0xF9, 0x00)


def _session(monkeypatch, fake: FakeGovee) -> tuple[GoveeConnection, list[int]]:
    calls: list[int] = []

    def frames() -> list[bytes]:
        calls.append(1)
        return [_SYNC]

    return _make(monkeypatch, fake, unlock_frames=lambda: [_UNLOCK], session_frames=frames), calls


async def test_session_frames_follow_the_unlock_once_per_connection(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    c, calls = _session(monkeypatch, fake)
    await c.send(frame(0x33, 0x01, 0x11))
    await c.send(frame(0x33, 0x01, 0x10))
    assert [f[:2].hex() for f in fake.received] == ["33b2", "33b5", "3301", "3301"]
    await c.disconnect()
    await c.connect()                       # a new session sends both again, the provider asked anew
    assert [f[:2].hex() for f in fake.received[4:]] == ["33b2", "33b5"]
    assert len(calls) == 2


@pytest.mark.parametrize("refusal", ["rejected", "unacked"])
async def test_a_refused_session_frame_leaves_the_session_ready(monkeypatch, fast, refusal) -> None:
    fake = FakeGovee(encrypted=False)
    if refusal == "unacked":
        fake.no_ack_cmds = {0xB5}
    else:
        def reject_sync(client, plain) -> None:
            client.push(frame(0x33, plain[1], 0x01 if plain[1] == 0xB5 else 0x00), 0.005)

        fake.on_write = reject_sync
    c, _calls = _session(monkeypatch, fake)
    await c.connect()                       # no GoveeBleAuthError: only the 33 b2 is the secret check
    assert c.is_connected
    await c.send(frame(0x33, 0x01, 0x11))
    assert fake.received[-1][:2].hex() == "3301"


async def test_session_frames_without_ack_waits_cost_no_timeout(monkeypatch, fast) -> None:
    # session_ack=False (a light's 33 25 01): each session sends the frame once and does not wait for an ack,
    # so a device that never acks it costs no ack timeout (the verified path would send it twice).
    fake = FakeGovee(encrypted=False)
    fake.no_ack_cmds = {0x25}
    stop = frame(0x33, 0x25, 0x01)
    c = _make(monkeypatch, fake, session_frames=lambda: [stop], session_ack=False)
    await c.send(frame(0x33, 0x01, 0x01))
    assert [f[:2].hex() for f in fake.received] == ["3325", "3301"]


async def test_link_lost_during_a_session_frame_retries_the_session(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)

    def drop_first_sync(client, plain) -> None:
        if plain[1] == 0xB5 and fake.clients.index(client) == 0:
            client.drop()
            return
        client.push(frame(0x33, plain[1], 0x00), 0.005)

    fake.on_write = drop_first_sync
    c, calls = _session(monkeypatch, fake)
    await c.connect()
    assert c.is_connected and len(fake.clients) == 2
    assert [f[:2].hex() for f in fake.received] == ["33b2", "33b5", "33b2", "33b5"]
    assert len(calls) == 2


async def test_bad_checksum_frames_are_dropped(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)

    def junk_then_ack(client, plain) -> None:
        client.push(bytes([0x33, plain[1], 0x00]) + bytes(17))  # bad BCC, would "ack"
        client.push(frame(0x33, plain[1], 0x05), 0.02)           # real ack: rejected

    fake.on_write = junk_then_ack
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleCommandRejected, match="rejected"):
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
    newer = ble_device(ADDR, "via-proxy")
    c.update_ble_device(newer)
    assert fake.connect_kwargs["ble_device_callback"]() is newer
    # What actually carries it: the next session attempt connects with the fresher device.
    await c.disconnect()
    await c.connect()
    assert fake.connect_device is newer


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
    for _ in range(500):  # the rescheduled timer then disconnects normally (bounded ~5 s poll)
        if not c.is_connected:
            break
        await asyncio.sleep(0.01)
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
    secret = bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF])
    c = _make(monkeypatch, fake, unlock_frames=lambda: [frame(0x33, 0xB2, *secret)])
    await c.connect()
    assert fake.received[0][2:10] == secret  # the device still gets the real key
    assert secret.hex() not in caplog.text


@pytest.mark.parametrize("encrypted", [False, True])
async def test_secret_is_masked_in_wire_capture(monkeypatch, fast, caplog, encrypted) -> None:
    """On a plaintext link the wire bytes ARE the secret frame: masked like the plaintext. On an
    encrypted link the ciphertext is blanked whole (its first bytes are AES over the secret's block)."""
    import logging

    caplog.set_level(logging.DEBUG, logger="govee_ble_local.frames")
    fake = FakeGovee(encrypted=encrypted)
    secret = bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF])
    unlock = frame(0x33, 0xB2, *secret)
    c = _make(monkeypatch, fake, unlock_frames=lambda: [unlock])
    await c.connect()
    secret_lines = [r.getMessage() for r in caplog.records if " plain=33b2" in r.getMessage()]
    assert secret_lines, "the unlock frame must still be captured (masked)"
    want = b"\xff" * len(unlock) if encrypted else redact.mask_secret(unlock)
    for line in secret_lines:
        assert line.split(" wire=")[1].split(" ")[0] == want.hex()
    assert secret.hex() not in caplog.text


def _wait_framelog_flushed(timeout: float = 5.0) -> None:
    """Block until the frame-log writer thread has written every queued record."""
    deadline = time.monotonic() + timeout
    while framelog._QUEUE.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.005)
    assert framelog._QUEUE.unfinished_tasks == 0


@pytest.mark.parametrize("encrypted", [False, True])
async def test_secret_is_masked_in_jsonl_frame_log(
    monkeypatch, fast, tmp_path: Path, encrypted: bool
) -> None:
    """The persistent capture (frame_log= / $GOVEE_FRAME_LOG) gets attached to bug reports:
    the 33 b2 secret must be masked in BOTH its plain and wire fields, as in the frames logger."""
    path = tmp_path / "cap.jsonl"
    fake = FakeGovee(encrypted=encrypted)
    secret = bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF])
    c = _make(monkeypatch, fake, unlock_frames=lambda: [frame(0x33, 0xB2, *secret)],
              frame_log=str(path))
    await c.connect()
    assert fake.received[0][2:10] == secret  # the device still gets the real key
    _wait_framelog_flushed()
    text = path.read_text()
    records = [json.loads(line) for line in text.splitlines()]
    secret_records = [r for r in records if (r["plain"] or "").startswith("33b2")]
    assert [r["dir"] for r in secret_records] == ["tx", "rx"]  # the unlock write and its ack
    # header kept, payload blanked, checksum recomputed
    masked = redact.mask_secret(frame(0x33, 0xB2, *secret))
    for rec in secret_records:
        # wire masked too: on a plaintext link it IS the secret (masked like the plaintext); on an
        # encrypted one it is blanked whole.
        assert rec["plain"] == masked.hex() and checksum_ok(masked)
        assert rec["wire"] == (b"\xff" * len(masked) if encrypted else masked).hex()
    assert secret.hex() not in text


# -- write_payload_size: the app's mtuSize (ATT MTU - 3), from the write characteristic ---------------
@pytest.mark.parametrize(("size", "want"), [
    (None, 20), (20, 20), (64, 64), (244, 244), (509, 509), (514, 509), (5, 20), (True, 20), ("x", 20),
])
async def test_write_payload_size_is_the_write_chars_size_clamped(monkeypatch, fast, size, want) -> None:
    fake = FakeGovee(encrypted=False)
    fake.write_size = size
    c = _make(monkeypatch, fake)
    assert c.write_payload_size == 20                      # not connected: the app's default
    await c.connect()
    assert c.write_payload_size == want


async def test_write_payload_size_is_20_when_the_backend_cannot_say(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    fake.write_size = 244
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.services_error = BleakError("Service Discovery has not been performed yet")
    assert c.write_payload_size == 20


async def test_send_unacked_sizes_frames_for_the_link_they_go_out_on(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    fake.write_size = 244
    c = _make(monkeypatch, fake)
    sizes: list[int] = []

    def plan(size: int) -> list[bytes]:
        sizes.append(size)
        return [frame(0xA3, 0x00, size & 0xFF)]

    await c.send_unacked(plan, gap=0)                      # connects first, then plans
    await c.disconnect()
    fake.write_size = 64                                   # the next link negotiated less
    await c.send_unacked(plan, gap=0)
    assert sizes == [244, 64]
    assert [f[2] for f in fake.received] == [244, 64]


# -- disconnect(): the early release behind Device.release() -----------------------------------------
_W1, _W2, _W3 = frame(0x33, 0x01, 0x01), frame(0x33, 0x04, 0x10), frame(0x33, 0x04, 0x20)


def _ack_after(fake: FakeGovee, delay: float) -> None:
    """Ack every write ``delay`` seconds after it (a slow proxy), on a plaintext link."""
    fake.on_write = lambda client, plain: client.push(frame(0x33, plain[1], 0x00), delay)


async def test_disconnect_after_a_write_drops_the_link_and_ends_the_warm_window(monkeypatch, fast) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    c._idle_disconnect = 8.0
    assert await c.send(_W1) is not None
    assert c._idle_timer is not None and c._active_until > 0
    assert await c.disconnect() is True
    assert fake.disconnects == 1 and not c.is_connected and c._client is None
    assert c._idle_timer is None and c._active_until == 0.0


@pytest.mark.parametrize("ended_by", ["disconnect", "device drop"])
async def test_a_read_after_the_link_ended_gets_the_base_idle_delay(monkeypatch, fast, ended_by) -> None:
    # WAS (1.5.0): the window survived the link, so a read that reconnected 5 s after a write kept its link
    # until the write's 30 s were up (_schedule_idle_timer's max()), 25 s rather than the idle delay.
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    c._idle_disconnect = 8.0
    loop = asyncio.get_running_loop()
    with virtual_time():
        await c.send(_W1)
        await asyncio.sleep(5.0)
        if ended_by == "disconnect":
            await c.disconnect()
        else:
            fake.clients[-1].drop()   # the device ends the link itself (most do at about 9-10 s)
        assert c._active_until == 0.0
        await c.query(frame(0xAA, 0x01), opcode=0xAA, terminal=0x01, idle_gap=None)
        timer = c._idle_timer
        assert len(fake.clients) == 2 and timer is not None
        assert timer.when() - loop.time() == pytest.approx(8.0)
    await c.close()


@pytest.mark.parametrize("failed", ["write that got no link", "read whose write found the link dead"])
async def test_a_failed_operations_teardown_ends_the_warm_window(monkeypatch, fast, failed) -> None:
    # WAS: only disconnect() and a device-side drop ended the window, so after a failed write (which opened
    # it), or a failed operation's teardown inside a write's 30 s, it outlived the link and the next session
    # kept its link until those 30 s were up. A drop that shows up as a failed write (BlueZ "Not connected")
    # never reaches _on_disconnect: the teardown forgets the client first, so its late callback is ignored.
    fake = FakeGovee(encrypted=False)
    c = _make(monkeypatch, fake)
    c._idle_disconnect = 8.0
    loop = asyncio.get_running_loop()
    with virtual_time():
        if failed == "write that got no link":
            fake.establish_error = BleakError("no connection slot")
            with pytest.raises(GoveeBleConnectionError):
                await c.send(_W1)
            fake.establish_error = None
        else:
            await c.send(_W1)
            await asyncio.sleep(3.0)
            fake.write_error = BleakError("Not connected")
            with pytest.raises(GoveeBleConnectionError):
                await c.query(frame(0xAA, 0x01), opcode=0xAA, terminal=0x01, idle_gap=None)
            fake.write_error = None
        assert c._client is None and c._active_until == 0.0
        await asyncio.sleep(2.0)
        await c.query(frame(0xAA, 0x01), opcode=0xAA, terminal=0x01, idle_gap=None)
        timer = c._idle_timer
        assert timer is not None and timer.when() - loop.time() == pytest.approx(8.0)
    await c.close()


async def test_a_write_retried_on_a_fresh_link_keeps_its_warm_window(monkeypatch, fast) -> None:
    # The device-side drop that ends a window can land inside a write, whose retry then goes out on a new
    # link: the window is the write's, on the link it ended on.
    fake = FakeGovee(encrypted=False)
    c = _make(monkeypatch, fake)
    c._idle_disconnect = 8.0

    def drop_first_link(client, plain) -> None:
        if fake.clients.index(client) == 0:
            asyncio.get_running_loop().call_later(0.02, client.drop)
        else:
            client.push(frame(0x33, plain[1], 0x00), 0.005)

    fake.on_write = drop_first_link
    loop = asyncio.get_running_loop()
    with virtual_time():
        t0 = loop.time()
        assert await c.send(_W1) is not None
        assert len(fake.clients) == 2 and c.is_connected
        assert c._active_until == pytest.approx(t0 + conn_mod.ACTIVE_IDLE_DELAY)
        timer = c._idle_timer
        assert timer is not None and timer.when() == pytest.approx(t0 + conn_mod.ACTIVE_IDLE_DELAY)
    await c.close()


async def test_a_write_whose_link_drops_with_its_ack_leaves_no_window(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    fake.ack_then_drop_links = 1   # ack, then the link-lost sentinel, in one loop tick
    c = _make(monkeypatch, fake)
    c._idle_disconnect = 8.0
    assert await c.send(_W1) is not None
    assert not c.is_connected and c._active_until == 0.0 and c._idle_timer is None


async def test_disconnect_waits_for_the_running_write(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    _ack_after(fake, 0.05)
    c = _make(monkeypatch, fake)
    with virtual_time():
        await c.connect()
        client = fake.clients[0]
        real = client.disconnect
        seen: list[bool] = []

        async def recording() -> None:
            seen.append(write.done() and write.result() is not None)   # the write had its ack by then
            await real()

        client.disconnect = recording  # type: ignore[method-assign]
        write = asyncio.ensure_future(c.send(_W1))
        await asyncio.sleep(0)
        assert c._lock.locked()          # the write holds the link, waiting for its ack
        assert await c.disconnect() is True
        assert seen == [True] and fake.disconnects == 1


async def test_disconnect_is_fifo_with_the_operations_around_it(monkeypatch, fast) -> None:
    # A runs, B is queued before the disconnect, C after it: A and B go out on the first link, then it drops,
    # and C connects again.
    fake = FakeGovee(encrypted=False)
    _ack_after(fake, 0.05)
    c = _make(monkeypatch, fake)
    with virtual_time():
        await c.connect()
        a = asyncio.ensure_future(c.send(_W1))
        await asyncio.sleep(0)
        assert c._lock.locked()
        b = asyncio.ensure_future(c.send(_W2))
        await asyncio.sleep(0)
        d = asyncio.ensure_future(c.disconnect())
        await asyncio.sleep(0)
        later = asyncio.ensure_future(c.send(_W3))
        acks = await asyncio.gather(a, b, d, later)
    assert acks[2] is True and None not in acks
    assert fake.establish_calls == 2 and fake.disconnects == 1
    assert fake.clients[0].writes == [_W1, _W2] and fake.clients[1].writes == [_W3]
    await c.close()


async def test_disconnect_asks_keep_under_the_lock_after_the_operations_before_it(monkeypatch, fast) -> None:
    # Device.release(): keep() is asked once the lock is held, after the running write and the one queued
    # before the disconnect, on the link they went out on (link_number); True keeps the link: False.
    fake = FakeGovee(encrypted=False)
    _ack_after(fake, 0.05)
    c = _make(monkeypatch, fake)
    asked: list[tuple[int, int]] = []

    def keep() -> bool:
        asked.append((len(fake.clients[0].writes), c.link_number))
        return True

    with virtual_time():
        assert c.link_number == 0
        await c.connect()
        a = asyncio.ensure_future(c.send(_W1))
        await asyncio.sleep(0)
        b = asyncio.ensure_future(c.send(_W2))
        await asyncio.sleep(0)
        results = await asyncio.gather(a, b, c.disconnect(keep=keep))
        assert results[2] is False and asked == [(2, 1)]
        assert c.is_connected and fake.disconnects == 0
        assert await c.disconnect(keep=lambda: False) is True and c.link_number == 0
        await c.connect()
        assert c.link_number == 2                    # each new link takes the next number
    await c.close()


async def test_an_idle_task_queued_behind_disconnect_does_nothing(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    c = _make(monkeypatch, fake)
    c._idle_disconnect = 8.0
    await c.connect()
    client = fake.clients[0]
    drops: list[int] = []
    teardowns: list[int] = []
    real_drop, real_teardown = client.disconnect, c._disconnect_locked

    async def counted_drop() -> None:
        drops.append(1)
        await real_drop()

    async def counted_teardown() -> None:
        teardowns.append(1)
        await real_teardown()

    client.disconnect = counted_drop  # type: ignore[method-assign]
    monkeypatch.setattr(c, "_disconnect_locked", counted_teardown)
    generation = c._idle_generation
    async with c._lock:                  # the link is busy: disconnect() queues, then the fired idle task
        d = asyncio.ensure_future(c.disconnect())
        await asyncio.sleep(0)
        c._idle_fired(generation)        # the timer fires while disconnect() waits
        await asyncio.sleep(0)
        assert c._lock_wanted == 2
    assert await d is True
    for _ in range(5):
        await asyncio.sleep(0)
    assert not c._background             # the idle task ran and returned on its stale generation
    assert drops == [1] and teardowns == [1]


async def test_disconnect_returns_at_its_bound_when_the_proxy_wedges(monkeypatch, fast, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="govee_ble_local")
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.disconnect_hangs = True
    loop = asyncio.get_running_loop()
    with virtual_time():
        t = loop.time()
        assert await c.disconnect() is True          # nothing raised
        assert loop.time() - t == pytest.approx(conn_mod.DISCONNECT_TIMEOUT)
        for _ in range(3):
            await asyncio.sleep(0)
    assert fake.disconnect_started == 1 and not c._background and not c._teardowns   # the teardown cancelled
    assert any("abandoning it" in r.getMessage() and r.levelno == logging.DEBUG for r in caplog.records)
    assert c._client is None and not c._lock.locked()


async def test_a_disconnect_error_is_only_logged(monkeypatch, fast, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="govee_ble_local")
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.disconnect_error = BleakError("proxy went away")
    assert await c.disconnect() is True
    await asyncio.sleep(0)
    errors = [r for r in caplog.records if "disconnect error" in r.getMessage()]
    assert errors and all(r.levelno == logging.DEBUG for r in errors)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert c._client is None


async def test_disconnect_cancelled_while_it_waits_for_the_lock_drops_nothing(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    _ack_after(fake, 0.05)
    c = _make(monkeypatch, fake)
    with virtual_time():
        await c.connect()
        write = asyncio.ensure_future(c.send(_W1))
        await asyncio.sleep(0)
        d = asyncio.ensure_future(c.disconnect())
        await asyncio.sleep(0)
        d.cancel()
        with pytest.raises(asyncio.CancelledError):
            await d
        assert await write is not None               # the running write is unaffected
    assert c.is_connected and fake.disconnects == 0 and c._lock_wanted == 0
    await c.send(_W2)                                # and the link still serves the next one
    assert fake.establish_calls == 1
    await c.close()


async def test_disconnect_cancelled_during_the_teardown_leaves_it_to_its_bound(monkeypatch, fast) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.disconnect_hangs = True
    loop = asyncio.get_running_loop()
    with virtual_time():
        t = loop.time()
        d = asyncio.ensure_future(c.disconnect())
        for _ in range(3):
            await asyncio.sleep(0)
        assert fake.disconnect_started == 1 and not d.done()   # in the teardown's wait
        d.cancel()
        with pytest.raises(asyncio.CancelledError):
            await d
        [(teardown, abandon_at)] = c._teardowns.items()
        assert abandon_at == pytest.approx(t + conn_mod.DISCONNECT_TIMEOUT)
        assert not c._lock.locked() and c._client is None and not teardown.done()
        await asyncio.sleep(conn_mod.DISCONNECT_TIMEOUT + 0.01)
        assert teardown.cancelled() and not c._teardowns   # abandoned at the bound, detached from the caller


async def test_disconnect_after_close_is_a_no_op(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    c = _make(monkeypatch, fake)
    await c.connect()
    await c.close()
    assert await c.disconnect() is False
    with pytest.raises(GoveeBleConnectionError, match="connection closed"):
        await c.send(_W1)
    assert fake.establish_calls == 1 and fake.disconnects == 1
