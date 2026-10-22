"""Characterization tests for GoveeConnection failure paths (audit TEST-2/TEST-5).

These pin TODAY's behaviour. Where that behaviour is a known bug the test says so
with a `# CURRENT BEHAVIOR (bug BUG-n): ...` comment. No wall-clock assertions."""
from __future__ import annotations

import asyncio
import logging

import pytest
from bleak.exc import BleakError

from govee_ble_local.const import PSK, SESSION_ATTEMPTS
from govee_ble_local.crypto import encrypt
from govee_ble_local.exceptions import (
    GoveeBleAuthError,
    GoveeBleConnectionError,
    GoveeBleHandshakeConnectionError,
    GoveeBleHandshakeError,
    GoveeBleHandshakeTimeout,
    GoveeBleInvalidValue,
    GoveeBleNotSupported,
    GoveeBleTimeout,
)
from govee_ble_local.models import Encryption
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.transport.connection import GoveeConnection

from fake_ble import SESSION_KEY, FakeGovee, FakeClient, ble_device, frame

ADDR = "AA:BB:CC:DD:EE:FF"


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_STEP2_TIMEOUT", 0.05)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_TIMEOUT", 0.1)


def _make(monkeypatch, fake: FakeGovee, **kw) -> GoveeConnection:
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    enc = kw.pop("encryption", Encryption.AES_RC4_PSK if fake.encrypted else Encryption.NONE)
    c = GoveeConnection(ble_device(ADDR, "GVH60A6X"), encryption=enc, **kw)
    c._idle_disconnect = 0
    return c


# -- connect / session setup -------------------------------------------------

# A format-2 BGC value as the app parses it (BaseBgcInfo.Companion.a reads bytes 1..5): format 2,
# encryptVersion 2 (AES-GCM), then the V2 flag, u16 and byte fields. ``[2, 2]`` alone the app can't read.
_BGC_V2 = bytes([2, 2, 0, 0, 0, 0])


async def test_bgc_v2_refused_not_retried(monkeypatch) -> None:
    fake = FakeGovee()
    fake.bgc = _BGC_V2
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleNotSupported):
        await c.connect()
    assert len(fake.clients) == 1  # NotSupported is not retried
    assert c.encryption is Encryption.AES_GCM
    assert fake.clients[0].is_connected is False  # torn down


async def test_bgc_v2_known_refuses_later_calls_without_a_link(monkeypatch) -> None:
    fake = FakeGovee()
    fake.bgc = _BGC_V2
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleNotSupported):
        await c.connect()
    # WAS: every later connect/send/query (each HA poll) opened a full link just to refuse again.
    # FIXED: once the BGC result is cached the refusal is raised before any connect.
    with pytest.raises(GoveeBleNotSupported):
        await c.connect()
    with pytest.raises(GoveeBleNotSupported):
        await c.query(frame(0xAC, 0x03), timeout=0.1)
    assert len(fake.clients) == 1 and fake.bgc_reads == 1


async def test_advertised_aes_gcm_refused(monkeypatch) -> None:
    fake = FakeGovee(encrypted=False)  # no BGC characteristic
    c = _make(monkeypatch, fake, encryption=Encryption.AES_GCM)
    with pytest.raises(GoveeBleNotSupported):
        await c.send(frame(0x33, 0x01, 0x01))
    assert len(fake.clients) == 1
    with pytest.raises(GoveeBleNotSupported):  # settled (BGC absent): no second link
        await c.send(frame(0x33, 0x01, 0x01))
    assert len(fake.clients) == 1


# -- an advertised mode handed over later (set_advertised_encryption) ------------
# The app overwrites its per-MAC encrypt flag from every parsed broadcast (BleBroadVersionCache.d) and reads
# it, OR'd with BgcInfoReader.g(), on every frame read, written or notified
# (EncryptionUtils.isEncryptionSupported); its light screen writes it just before it connects.

async def test_advertised_mode_applies_from_the_next_session(monkeypatch) -> None:
    fake = FakeGovee()   # an encrypted device
    fake.bgc = None      # without a BGC characteristic: the advertised mode decides
    c = _make(monkeypatch, fake, encryption=Encryption.NONE)
    await c.connect()
    c.set_advertised_encryption(Encryption.AES_RC4_PSK)
    assert c.encryption is Encryption.NONE   # the session that is up keeps its mode
    await c.disconnect()
    assert c.encryption is Encryption.AES_RC4_PSK   # the mode the next session starts from
    await c.send(frame(0x33, 0x01, 0x01))   # handshakes on the new link, so the device reads (and acks) it
    assert c.encryption is Encryption.AES_RC4_PSK and fake.received[-1][:3] == bytes([0x33, 0x01, 0x01])
    assert len(fake.clients) == 2


async def test_advertised_mode_handed_over_during_setup_waits(monkeypatch) -> None:
    fake = FakeGovee(encrypted=False)   # plaintext, no BGC characteristic
    c = _make(monkeypatch, fake)
    read_bgc = c._read_bgc_encrypt_version

    async def heard_meanwhile() -> int | None:
        c.set_advertised_encryption(Encryption.AES_RC4_PSK)   # an advertisement lands during the BGC read
        return await read_bgc()

    monkeypatch.setattr(c, "_read_bgc_encrypt_version", heard_meanwhile)
    await c.send(frame(0x33, 0x01, 0x01))   # the session set up with the mode it started from
    assert c.encryption is Encryption.NONE and fake.received[-1][:3] == bytes([0x33, 0x01, 0x01])
    await c.disconnect()
    assert c.encryption is Encryption.AES_RC4_PSK


async def test_session_mode_is_set_before_the_bgc_read(monkeypatch) -> None:
    # Frames that arrive during the BGC read are captured under the new session's mode, not the last one's.
    fake = FakeGovee()
    fake.bgc = None
    c = _make(monkeypatch, fake, encryption=Encryption.NONE)
    c.set_advertised_encryption(Encryption.AES_RC4_PSK)
    seen: list[Encryption] = []
    read_bgc = c._read_bgc_encrypt_version

    async def spy() -> int | None:
        seen.append(c.encryption)   # a link is up: the session's mode
        return await read_bgc()

    monkeypatch.setattr(c, "_read_bgc_encrypt_version", spy)
    await c.connect()
    assert seen == [Encryption.AES_RC4_PSK]


async def test_bgc_verdict_overrides_an_advertised_mode(monkeypatch) -> None:
    fake = FakeGovee()   # BGC encryptVersion 1
    c = _make(monkeypatch, fake)
    await c.connect()
    await c.disconnect()
    # a plaintext flag never downgrades a BGC 1 device, and the hand-over says so
    assert c.set_advertised_encryption(Encryption.NONE) is Encryption.AES_RC4_PSK
    assert c.encryption is Encryption.AES_RC4_PSK
    await c.send(frame(0x33, 0x01, 0x01))
    assert c.encryption is Encryption.AES_RC4_PSK and fake.bgc_reads == 1   # the cached verdict


async def test_bgc_v2_refusal_outlasts_an_advertised_mode(monkeypatch) -> None:
    fake = FakeGovee()
    fake.bgc = _BGC_V2
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleNotSupported):
        await c.connect()
    c.set_advertised_encryption(Encryption.AES_RC4_PSK)
    with pytest.raises(GoveeBleNotSupported):   # AES-GCM whatever is advertised: still no second link
        await c.connect()
    assert c.encryption is Encryption.AES_GCM and len(fake.clients) == 1


async def test_advertised_mode_lifts_an_advertised_aes_gcm_refusal(monkeypatch) -> None:
    fake = FakeGovee()
    fake.bgc = None
    c = _make(monkeypatch, fake, encryption=Encryption.AES_GCM)
    with pytest.raises(GoveeBleNotSupported):
        await c.connect()
    c.set_advertised_encryption(Encryption.AES_RC4_PSK)   # only the BGC read settles AES-GCM for good
    await c.connect()
    assert c.is_connected and c.encryption is Encryption.AES_RC4_PSK and len(fake.clients) == 2


@pytest.mark.parametrize("mode", [Encryption.AES_GCM, "aes_rc4_psk"])
def test_set_advertised_encryption_takes_an_advertised_flag_only(mode: object) -> None:
    c = GoveeConnection(ble_device(ADDR, "GVH60A6X"), encryption=Encryption.NONE)
    with pytest.raises(GoveeBleInvalidValue, match="NONE or AES_RC4_PSK"):
        c.set_advertised_encryption(mode)  # type: ignore[arg-type]
    assert c.encryption is Encryption.NONE


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


@pytest.mark.parametrize("exc", [BrokenPipeError(), ConnectionResetError(), EOFError()])
async def test_connect_os_level_link_errors_map_to_connection_error(monkeypatch, exc) -> None:
    # WAS: BrokenPipeError / ConnectionResetError (the D-Bus socket dropping) escaped connect()
    # as bare builtins. FIXED: the shared transport-error set maps them like BleakError.
    fake = FakeGovee()
    fake.establish_error = exc
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleConnectionError, match=f"connect failed: {type(exc).__name__}"):
        await c.connect()
    assert fake.establish_calls == SESSION_ATTEMPTS


# -- a failed BGC read fails that attempt -----------------------------------------
# The app sends no frame on a link whose 2b12 read failed (BgcInfoReader.a waits for a read callback;
# BgcInfoReaderPool.checkNeedParseInfo takes only a status-0 read, so EncryptionBluetoothGattCallback.r
# never runs). The library tears the link down and the session loop reads again on a new one.

def _first_link_only(fake: FakeGovee, knob: str, err: BaseException) -> None:
    """Inject ``err`` through the fake's ``knob`` on the first link only."""
    setattr(fake, knob, err)

    def link_up(_client: FakeClient) -> None:
        if len(fake.clients) > 1:
            setattr(fake, knob, None)

    fake.link_up = link_up  # type: ignore[method-assign]


async def test_bgc_read_error_fails_the_attempt_and_the_next_link_reads_again(monkeypatch) -> None:
    fake = FakeGovee()   # BGC encryptVersion 1
    _first_link_only(fake, "bgc_read_error", BleakError("read failed"))
    c = _make(monkeypatch, fake, encryption=Encryption.NONE)   # it advertises plaintext
    # WAS: the session ran on the advertised mode, plaintext frames a BGC-1 device ignores.
    await c.connect()
    assert len(fake.clients) == 2 and fake.bgc_reads == 2
    assert fake.clients[0].is_connected is False and fake.clients[0].writes == []   # nothing sent on it
    assert c.is_connected and c.encryption is Encryption.AES_RC4_PSK and c._bgc_checked


@pytest.mark.parametrize(("exc", "raised", "message"), [
    (BleakError("read failed"), GoveeBleConnectionError, "BGC read failed: read failed"),
    (EOFError(), GoveeBleConnectionError, "BGC read failed: EOFError"),
    (BrokenPipeError(), GoveeBleConnectionError, "BGC read failed: BrokenPipeError"),
    (TimeoutError(), GoveeBleTimeout, "BGC read timed out"),
])
async def test_bgc_read_error_on_every_link_fails_the_connect(monkeypatch, exc, raised, message) -> None:
    fake = FakeGovee()
    fake.bgc_read_error = exc
    c = _make(monkeypatch, fake)
    with pytest.raises(raised, match=message):
        await c.connect()
    assert len(fake.clients) == SESSION_ATTEMPTS and fake.bgc_reads == SESSION_ATTEMPTS
    assert all(not cl.is_connected and cl.writes == [] for cl in fake.clients)
    assert c._bgc_checked is False   # nothing cached: the next connect reads again
    fake.bgc_read_error = None
    await c.connect()
    assert c.is_connected and c._bgc_checked and fake.bgc_reads == SESSION_ATTEMPTS + 1


async def test_bgc_lookup_error_fails_the_attempt_and_the_next_link_reads(monkeypatch) -> None:
    fake = FakeGovee()
    _first_link_only(fake, "services_error", BleakError("Service Discovery has not been performed yet"))
    c = _make(monkeypatch, fake, encryption=Encryption.NONE)
    await c.connect()
    assert len(fake.clients) == 2 and fake.bgc_reads == 1   # the first link never got to the read
    assert fake.clients[0].is_connected is False and fake.clients[0].writes == []
    assert c.is_connected and c.encryption is Encryption.AES_RC4_PSK and c._bgc_checked


async def test_bgc_lookup_error_on_every_link_fails_the_connect(monkeypatch) -> None:
    fake = FakeGovee()
    fake.services_error = BleakError("Service Discovery has not been performed yet")
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleConnectionError, match="BGC lookup failed"):
        await c.connect()
    assert len(fake.clients) == SESSION_ATTEMPTS and fake.bgc_reads == 0 and c._bgc_checked is False


async def test_handshake_timeout(monkeypatch) -> None:
    fake = FakeGovee()
    fake.withhold_e7_reply = True
    c = _make(monkeypatch, fake)
    # WAS: a plain GoveeBleTimeout, so the public GoveeBleHandshakeError could never fire.
    with pytest.raises(GoveeBleHandshakeTimeout) as info:
        await c.connect()
    assert isinstance(info.value, GoveeBleTimeout)          # old handlers keep matching
    assert isinstance(info.value, GoveeBleHandshakeError)
    assert len(fake.clients) == SESSION_ATTEMPTS
    assert not c.is_connected


async def test_handshake_link_lost(monkeypatch) -> None:
    fake = FakeGovee()
    fake.drop_on_e7 = True
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleHandshakeConnectionError, match="link lost during handshake") as info:
        await c.connect()
    assert isinstance(info.value, GoveeBleConnectionError)  # old handlers keep matching
    assert isinstance(info.value, GoveeBleHandshakeError)
    assert len(fake.clients) == SESSION_ATTEMPTS


async def test_handshake_link_lost_between_steps(monkeypatch) -> None:
    fake = FakeGovee()
    real = fake.handle_write

    def drop_after_step1_reply(client: FakeClient, data: bytes) -> None:
        if len(client.writes) != 1:
            real(client, data)
            return
        reply = encrypt(frame(0xE7, 0x01, *SESSION_KEY), PSK)

        def answer_then_drop() -> None:  # e7 01 answered, then the link dies before e7 02
            client._notify(None, bytearray(reply))  # type: ignore[misc]
            client.drop()

        asyncio.get_running_loop().call_soon(answer_then_drop)

    fake.handle_write = drop_after_step1_reply  # type: ignore[method-assign]
    c = _make(monkeypatch, fake)
    with pytest.raises(GoveeBleHandshakeConnectionError, match="link lost during handshake"):
        await c.connect()
    assert len(fake.clients) == SESSION_ATTEMPTS


@pytest.mark.parametrize(
    "exc", [BleakError("Not connected"), BrokenPipeError(), EOFError()],
    ids=["bleak", "broken-pipe", "eof"],
)
async def test_handshake_write_transport_error_is_handshake_link_loss(monkeypatch, exc) -> None:
    fake = FakeGovee()
    fake.write_error = exc  # the e7 01 write itself fails: how BlueZ / proxies report a dropped link
    c = _make(monkeypatch, fake)
    # WAS: escaped _prepare_session as a plain GoveeBleConnectionError("connect failed: ..."), so
    # `except GoveeBleHandshakeError` missed it while a TimeoutError on the same write matched.
    with pytest.raises(GoveeBleHandshakeConnectionError, match="handshake write failed") as info:
        await c.connect()
    assert isinstance(info.value, GoveeBleConnectionError)  # old handlers keep matching
    assert info.value.__cause__ is exc
    assert len(fake.clients) == SESSION_ATTEMPTS            # still retried like any session failure
    assert fake.disconnects == SESSION_ATTEMPTS and not c.is_connected  # every link torn down


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
    with pytest.raises(GoveeBleConnectionError, match="EOFError"):  # never an empty message
        await c.send_unacked([frame(0xA3, 0x01)], gap=0)
    assert not c.is_connected


@pytest.mark.parametrize("encrypted", [True, False])
async def test_send_unacked_link_lost_mid_stream(monkeypatch, encrypted) -> None:
    """A link drop between scene-upload frames is a GoveeBleConnectionError, never a bare
    TypeError/AttributeError from encrypting with no key / writing with no client."""
    fake = FakeGovee(encrypted=encrypted)
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.on_write = lambda client, plain: client.drop() if plain[0] == 0xA3 else None
    guard = "session key lost" if encrypted else "connection lost"
    with pytest.raises(GoveeBleConnectionError, match=guard):
        await c.send_unacked([frame(0xA3, 0, 1), frame(0xA3, 1, 1), frame(0xA3, 0xFF, 1)], gap=0)
    assert sum(f[0] == 0xA3 for f in fake.received) == 1


async def test_write_bleak_error_retried_once_then_surfaces(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.app_write_error = BleakError("boom")
    with pytest.raises(GoveeBleConnectionError):
        await c.send(frame(0x33, 0x01, 0x01))
    assert len(fake.clients) == 2  # one reconnect-and-retry, then gives up


def _fail_first_app_write(fake: FakeGovee, exc: BaseException) -> None:
    """The first post-handshake write raises `exc` (one-shot); later writes are acked."""
    state = {"failed": False}

    def on_write(client: FakeClient, plain: bytes) -> None:
        if not state["failed"]:
            state["failed"] = True
            raise exc
        client.push(fake._wire(frame(0x33, plain[1], 0), SESSION_KEY), 0.005)

    fake.on_write = on_write


@pytest.mark.parametrize(
    "exc", [BleakError("gone"), BrokenPipeError(), ConnectionResetError(), EOFError(), TimeoutError()]
)
async def test_one_shot_write_failure_reconnects_and_retries(monkeypatch, exc) -> None:
    # WAS: only BleakError got the documented reconnect-and-retry. EOFError / TimeoutError failed
    # the command outright, and BrokenPipeError escaped send() as a bare builtin with the
    # client left up. FIXED: one transport-error set at every site.
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    _fail_first_app_write(fake, exc)
    ack = await c.send(frame(0x33, 0x01, 0x01))
    assert ack is not None and ack[:3] == bytes([0x33, 0x01, 0x00])
    assert len(fake.clients) == 2 and fake.clients[0].is_connected is False


async def test_write_retry_log_names_a_messageless_error(monkeypatch, caplog) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    _fail_first_app_write(fake, EOFError())
    caplog.set_level(logging.DEBUG, logger="govee_ble_local.transport.connection")
    await c.send(frame(0x33, 0x01, 0x01))
    # WAS: `err or type(err).__name__` is always `err` (exceptions are truthy): "on this link ()".
    assert "failed on this link (EOFError); reconnecting" in caplog.text


@pytest.mark.parametrize("exc", [BrokenPipeError(), ConnectionResetError(), EOFError()])
async def test_persistent_os_level_write_error_is_connection_error(monkeypatch, exc) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.app_write_error = exc  # post-handshake writes only: the reconnect itself succeeds
    with pytest.raises(GoveeBleConnectionError):
        await c.send(frame(0x33, 0x01, 0x01))
    assert len(fake.clients) == 2  # one reconnect-and-retry, then gives up
    assert not c.is_connected and all(not cl.is_connected for cl in fake.clients)


async def test_write_timeout_error_is_not_reported_as_operation_deadline(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.app_write_error = TimeoutError()
    with pytest.raises(GoveeBleTimeout, match="BLE I/O timed out") as info:
        await c.send(frame(0x33, 0x01, 0x01))
    # WAS: "operation exceeded 45s", although the 45 s deadline was never reached.
    assert "exceeded" not in str(info.value)
    assert len(fake.clients) == 2 and not c.is_connected


async def test_unexpected_write_error_is_wrapped_and_link_closed(monkeypatch) -> None:
    fake = FakeGovee()
    c = _make(monkeypatch, fake)
    await c.connect()
    fake.app_write_error = RuntimeError("backend exploded")
    # WAS: the bare RuntimeError escaped (the module promises GoveeBleError subclasses only).
    with pytest.raises(GoveeBleConnectionError, match="backend exploded") as info:
        await c.send_unacked([frame(0xA3, 0x01)], gap=0)
    assert isinstance(info.value.__cause__, RuntimeError)
    assert not c.is_connected and fake.clients[0].is_connected is False


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
