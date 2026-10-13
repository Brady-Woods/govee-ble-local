"""Characterization tests for GoveeConnection failure paths (audit TEST-2/TEST-5).

These pin TODAY's behaviour. Where that behaviour is a known bug the test says so
with a `# CURRENT BEHAVIOR (bug BUG-n): ...` comment. No wall-clock assertions."""
from __future__ import annotations

import pytest
from bleak.exc import BleakError

from govee_ble_local.const import SESSION_ATTEMPTS
from govee_ble_local.exceptions import (
    GoveeBleAuthError,
    GoveeBleConnectionError,
    GoveeBleInvalidValue,
    GoveeBleNotSupported,
    GoveeBleTimeout,
)
from govee_ble_local.models import Encryption
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.transport.connection import GoveeConnection

from fake_ble import FakeGovee, ble_device, frame

ADDR = "AA:BB:CC:DD:EE:FF"


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_STEP2_TIMEOUT", 0.05)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_TIMEOUT", 0.1)
    monkeypatch.setattr(conn_mod, "COMMAND_ACK_TIMEOUT", 0.05)


def _make(monkeypatch, fake: FakeGovee, **kw) -> GoveeConnection:
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    enc = kw.pop("encryption", Encryption.AES_RC4_PSK if fake.encrypted else Encryption.NONE)
    c = GoveeConnection(ble_device(ADDR, "GVH60A6X"), encryption=enc, **kw)
    c._idle_disconnect = 0
    return c


# -- connect / session setup -------------------------------------------------

async def test_bgc_v2_refused_not_retried(monkeypatch) -> None:
    fake = FakeGovee()
    fake.bgc = bytes([2, 2])
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleNotSupported):
        await c.connect()
    assert len(fake.clients) == 1  # NotSupported is not retried
    assert c.encryption is Encryption.AES_GCM
    assert fake.clients[0].is_connected is False  # torn down


async def test_advertised_aes_gcm_refused(monkeypatch) -> None:
    fake = FakeGovee(encrypted=False)  # no BGC characteristic
    c = _make(monkeypatch, fake, encryption=Encryption.AES_GCM)
    with pytest.raises(GoveeBleNotSupported):
        await c.send(frame(0x33, 0x01, 0x01))
    assert len(fake.clients) == 1


async def test_connect_timeout_maps_to_ble_timeout(monkeypatch) -> None:
    fake = FakeGovee()
    fake.establish_error = TimeoutError()
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleTimeout):
        await c.connect()
    assert fake.establish_calls == SESSION_ATTEMPTS  # retried across session attempts


async def test_connect_bleak_error_maps_to_connection_error(monkeypatch) -> None:
    fake = FakeGovee()
    fake.establish_error = BleakError("no adapter")
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleConnectionError):
        await c.connect()
    assert fake.establish_calls == SESSION_ATTEMPTS


async def test_bgc_read_error_is_transient_and_not_cached(monkeypatch) -> None:
    fake = FakeGovee()
    fake.bgc_read_error = BleakError("read failed")
    c = _make(monkeypatch, fake)
    await c.connect()  # falls back to the advertised mode
    assert c.is_connected
    assert c._bgc_checked is False


async def test_handshake_timeout(monkeypatch) -> None:
    fake = FakeGovee()
    fake.withhold_e7_reply = True
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleTimeout):
        await c.connect()
    assert len(fake.clients) == SESSION_ATTEMPTS
    assert not c.is_connected


async def test_handshake_link_lost(monkeypatch) -> None:
    fake = FakeGovee()
    fake.drop_on_e7 = True
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleConnectionError, match="link lost during handshake"):
        await c.connect()
    assert len(fake.clients) == SESSION_ATTEMPTS


async def test_missing_step2_confirm_still_connects(monkeypatch) -> None:
    fake = FakeGovee()
    fake.withhold_e7_confirm = True
    c = _make(monkeypatch, fake)
    ack = await c.send(frame(0x33, 0x01, 0x01))
    assert ack is not None and ack[2] == 0
    assert len(fake.clients) == 1


# -- unlock frames -----------------------------------------------------------

async def test_unlock_frame_without_ack_continues(monkeypatch) -> None:
    fake = FakeGovee()
    fake.no_ack_cmds = {0xB2}
    c = _make(monkeypatch, fake, unlock_frames=lambda: [frame(0x33, 0xB2, 0x01)])
    await c.connect()
    assert c.is_connected
    assert frame(0x33, 0xB2, 0x01) in fake.received


async def test_unlock_rejected_raises_auth_error_not_retried(monkeypatch) -> None:
    fake = FakeGovee()
    fake.ack_status = 1
    c = _make(monkeypatch, fake, unlock_frames=lambda: [frame(0x33, 0xB2, 0x01)])
    with pytest.raises(GoveeBleAuthError):
        await c.connect()
    # WAS (DES-4): a wrong secret key (auth error) was retried like a transient
    # failure - a whole extra connect+handshake per SESSION_ATTEMPTS (2 clients, 2 unlocks).
    # FIXED: an auth error is not transient; one connect, one unlock, link torn down.
    assert len(fake.clients) == 1
    assert fake.received.count(frame(0x33, 0xB2, 0x01)) == 1
    assert fake.clients[0].is_connected is False


# -- _run mapping ------------------------------------------------------------

async def test_run_maps_link_lost_mid_query(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.on_write = lambda client, _plain: client.drop()
    with pytest.raises(GoveeBleConnectionError, match="link lost mid-operation"):
        await c.query(frame(0xAC, 0x03), timeout=1.0)


async def test_run_maps_bleak_error_and_disconnects(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.app_write_error = BleakError("gatt write failed")
    with pytest.raises(GoveeBleConnectionError, match="gatt write failed"):
        await c.query(frame(0xAC, 0x03), timeout=1.0)
    assert not c.is_connected
    assert fake.clients[0].is_connected is False


async def test_run_maps_eof_error(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.app_write_error = EOFError()
    with pytest.raises(GoveeBleConnectionError):
        await c.send_unacked([frame(0xA3, 0x01)], gap=0)
    assert not c.is_connected


async def test_write_bleak_error_retried_once_then_surfaces(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.app_write_error = BleakError("boom")
    with pytest.raises(GoveeBleConnectionError):
        await c.send(frame(0x33, 0x01, 0x01))
    assert len(fake.clients) == 2  # one reconnect-and-retry, then gives up


async def test_write_without_ack_times_out_after_retry(monkeypatch) -> None:
    fake = FakeGovee()
    fake.no_ack_cmds = {0x01}
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleTimeout):
        await c.send(frame(0x33, 0x01, 0x01))
    assert len(fake.clients) == 2


# -- send() variants ---------------------------------------------------------

async def test_send_expect_ack_false_returns_none(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    assert await c.send(frame(0x33, 0x01, 0x01), expect_ack=False) is None
    assert frame(0x33, 0x01, 0x01) in fake.received


async def test_send_non_write_with_expect_ack_rejected(monkeypatch) -> None:
    # WAS (BUG-10): send(non-write) with expect_ack=True returned whatever frame
    # arrived next (an unrelated push could be mistaken for the reply).
    # FIXED: only writes (proType 0x33) have an ack to wait for -> InvalidValue,
    # raised before any connect or I/O.
    fake = FakeGovee()
    fake.read_replies[0x07] = frame(0xAA, 0x07, 0x01, 0x02)
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleInvalidValue):
        await c.send(frame(0xAA, 0x07))
    assert fake.clients == [] and fake.received == []


async def test_send_non_write_unacked_sends_once(monkeypatch) -> None:
    # WAS: test_send_non_write_without_reply_returns_none (expect_ack default True).
    # FIXED: a non-write is sent with expect_ack=False and returns None.
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    assert await c.send(frame(0xAA, 0x07), expect_ack=False) is None
    assert fake.received == [frame(0xAA, 0x07)]


# -- known-bug characterization ----------------------------------------------

async def test_ack_then_disconnect_same_tick_does_not_resend_write(monkeypatch) -> None:
    fake = FakeGovee()
    fake.ack_then_drop_links = 1
    c = _make(monkeypatch, fake)
    ack = await c.send(frame(0x33, 0x01, 0x01))
    assert ack is not None and ack[:3] == bytes([0x33, 0x01, 0x00])
    # WAS (BUG-1): the device acked, but the disconnect callback nulled the
    # session key before the waiter ran, so the ack was "decrypted" without a key
    # (garbage, no match); the following link-lost sentinel then made
    # _write_locked reconnect and RESEND an already-applied write
    # (2 clients, write received twice).
    # FIXED: notifications are decrypted on arrival, so the ack survives the
    # disconnect: the write is accepted and NOT resent.
    assert len(fake.clients) == 1
    assert fake.received.count(frame(0x33, 0x01, 0x01)) == 1


async def test_next_rx_zero_timeout_returns_queued_frame(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    c._rx.put_nowait(b"\x00" * 20)
    # WAS (BUG-7): wait_for(timeout=0) raised TimeoutError even though a frame was
    # already queued (3.11 and 3.14), leaving it unconsumed.
    # FIXED: a queued frame is returned regardless of the budget; only an EMPTY
    # queue with no budget times out.
    assert await c._next_rx(0) == b"\x00" * 20
    assert c._rx.qsize() == 0
    with pytest.raises(TimeoutError):
        await c._next_rx(0)


async def test_next_rx_negative_timeout_clamped(monkeypatch) -> None:
    c = _make(monkeypatch, FakeGovee())
    with pytest.raises(TimeoutError):
        await c._next_rx(-5)
