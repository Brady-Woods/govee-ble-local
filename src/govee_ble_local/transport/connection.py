"""The encrypted BLE session: connect, handshake, send/receive.

Owns the bleak connection, runs the e7 handshake to negotiate the session
key, then encrypts every outgoing frame and decrypts every notification with
it. Raw ciphertext is queued from the notify callback and decrypted at the
point of use (PSK during the handshake, session key afterwards).

Reliability rules this module enforces (each one learned from live failures):

* Every public operation runs under one lock and one overall deadline
  (``OPERATION_TIMEOUT``), and only ever raises :class:`GoveeBleError`
  subclasses - never a bare bleak/asyncio exception.
* A link that stops acknowledging writes is treated as half-dead: it is torn
  down and the write is retried once on a freshly established link.
* A disconnect wakes anything waiting on a reply immediately instead of letting
  it run out its timeout, and a late disconnect callback from a superseded
  client never clobbers the current one.
* The idle-disconnect timer can never fire after newer activity (generation
  counter), and its task is held so it can't be garbage-collected mid-flight.
* Junk notifications (late handshake retransmits, frames failing the XOR
  checksum) are dropped at the source, so they can't be mistaken for replies.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from ..const import (
    ACTIVE_IDLE_DELAY,
    BGC_INFO_CHAR_UUID,
    BURST_IDLE_GAP,
    COMMAND_ACK_TIMEOUT,
    CONNECT_MAX_ATTEMPTS,
    FRAME_LEN,
    HANDSHAKE_ECHO_WINDOW,
    HANDSHAKE_STEP2_TIMEOUT,
    HANDSHAKE_TIMEOUT,
    IDLE_DISCONNECT_DELAY,
    NOTIFY_CHAR_UUID,
    OPERATION_TIMEOUT,
    PSK,
    SESSION_ATTEMPTS,
    WRITE_ACK_TIMEOUT,
    WRITE_ATTEMPTS,
    WRITE_CHAR_UUID,
)
from ..crypto import checksum_ok, decrypt, encrypt
from ..exceptions import (
    GoveeBleAuthError,
    GoveeBleConnectionError,
    GoveeBleError,
    GoveeBleHandshakeError,
    GoveeBleNotSupported,
    GoveeBleTimeout,
)
from ..models import Encryption
from ..wire.build import PRO_WRITE
from ..wire.describe import describe_frame
from . import handshake
from .framelog import FrameLog

_LOGGER = logging.getLogger(__name__)
# Dedicated frame-tier logger: the full-session firehose (every TX/RX frame). Off
# unless enabled (e.g. HA `logger: logs: govee_ble_local.frames: debug`); guarded by
# isEnabledFor so it costs nothing when disabled. This is the capture surface over
# a Bluetooth proxy.
_FRAMES = logging.getLogger("govee_ble_local.frames")

# proTypes a device legitimately sends us; anything else is surfaced as a warning.
_KNOWN_RX_PRO_TYPES = frozenset({0x33, 0xAA, 0xAC, 0xEE, 0xA1, 0xA3, 0xA4, 0xE7})
# Unsolicited pushes accumulate between operations; never let them grow unbounded.
_RX_QUEUE_MAX = 64

NotifyCallback = Callable[[bytes], None]
_T = TypeVar("_T")


def _carries_secret(plain: bytes) -> bool:
    return len(plain) >= 2 and (
        (plain[0] == PRO_WRITE and plain[1] == 0xB2)  # secret check write
        or (plain[0] == 0xAA and plain[1] == 0xB1)    # secret read reply
    )


def _mask_secret(frame: bytes) -> bytes:
    """Blank the payload of a secret-carrying frame (keeps its 2-byte header)."""
    return frame[:2] + b"\xff" * (len(frame) - 2)


class _LinkLost(Exception):
    """Internal: the link dropped while waiting on a reply (queue sentinel)."""


class _WriteRejected(GoveeBleError):
    """The device explicitly refused a write (ack status byte != 0)."""


class GoveeConnection:
    """One on-demand BLE session with a Govee device.

    The command channel is secured per `encryption`: AES_RC4_PSK negotiates a
    session key and encrypts every frame with it; NONE sends plaintext;
    AES_GCM (BGC version 2) is detected but not yet implemented.
    """

    def __init__(
        self,
        ble_device: BLEDevice,
        *,
        encryption: Encryption = Encryption.AES_RC4_PSK,
        on_notify: NotifyCallback | None = None,
        unlock_frames: Callable[[], list[bytes]] | None = None,
        idle_disconnect: float = IDLE_DISCONNECT_DELAY,
        frame_log: str | None = None,
    ) -> None:
        self._ble_device = ble_device
        self._encryption = encryption
        self._on_notify = on_notify
        # Optional bidirectional frame capture (frame_log arg or $GOVEE_FRAME_LOG).
        self._framelog = FrameLog.resolve(frame_log, ble_device.address)
        # Frames to send immediately after every handshake, before any user
        # command - e.g. the plug family's `33 b2` secret-key check. A provider
        # (not a static list) so a device can supply/refresh it lazily.
        self._unlock_frames = unlock_frames
        self._idle_disconnect = idle_disconnect
        self._client: BleakClientWithServiceCache | None = None
        self._session_key: bytes | None = None
        self._ready = False  # connected AND session set up
        self._closed = False
        self._bgc_version: int | None = None  # cached after the first successful read
        self._bgc_checked = False
        # Wire bytes of this session's handshake frames + when to stop filtering them.
        self._handshake_echoes: set[bytes] = set()
        self._echo_filter_until = 0.0
        # None is the "link lost" sentinel pushed by the disconnect callback.
        self._rx: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._idle_timer: asyncio.TimerHandle | None = None
        self._idle_generation = 0
        self._background: set[asyncio.Task[None]] = set()
        self._active_until = 0.0  # loop time; warm-window end after a user command

    @property
    def address(self) -> str:
        return self._ble_device.address

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected and self._ready

    @property
    def encryption(self) -> Encryption:
        """The command-channel mode actually in use (refined by the BGC read)."""
        return self._encryption

    def update_ble_device(self, ble_device: BLEDevice) -> None:
        """Adopt a fresher BLEDevice (e.g. from the latest advertisement). Used for
        the next connection attempt - and for retries within one attempt, via
        establish_connection's ble_device_callback."""
        self._ble_device = ble_device

    # -- capture ------------------------------------------------------------

    def _capture(self, direction: str, *, wire: bytes, plain: bytes | None, enc: str) -> None:
        """Route one frame to the persistent JSONL sink (if configured) AND the
        frame-tier logger (if enabled) - one choke point, one shape. Account-lock
        secret payloads (33 b2 write / aa b1 read reply) are masked: captures end
        up in logs and diagnostics downloads. The wire bytes are masked too - on a
        plaintext link they ARE the secret, and on an encrypted link the session
        key is derivable from the captured handshake plus the library's public PSK."""
        if plain is not None and _carries_secret(plain):
            plain = plain[:2] + b"\xff" * 17 + plain[19:]
            wire = _mask_secret(wire)
        if self._framelog is not None:
            self._framelog.record(direction, wire=wire, plain=plain, enc=enc)
        if _FRAMES.isEnabledFor(logging.DEBUG):
            if plain is not None:
                label = describe_frame(plain, direction)[0]
                _FRAMES.debug(
                    "%s %s %s plain=%s wire=%s enc=%s",
                    self.address, direction, label, plain.hex(), wire.hex(), enc,
                )
            else:
                _FRAMES.debug(
                    "%s %s cipher plain= wire=%s enc=%s", self.address, direction, wire.hex(), enc
                )

    # -- notify pump --------------------------------------------------------

    def _decrypt_rx(self, raw: bytes) -> bytes:
        if self._encryption is Encryption.AES_RC4_PSK and self._session_key is not None:
            return decrypt(raw, self._session_key)
        return raw

    def _enqueue(self, item: bytes | None) -> None:
        if item is not None and self._rx.qsize() >= _RX_QUEUE_MAX:
            self._rx.get_nowait()  # drop the oldest unsolicited frame
        self._rx.put_nowait(item)

    def _handle_notify(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        raw = bytes(data)
        if len(raw) != FRAME_LEN:
            _LOGGER.debug("%s: ignoring %d-byte notification", self.address, len(raw))
            return
        if not self._ready:
            # Handshake in progress: raw ciphertext, consumed by _handshake().
            self._capture("rx", wire=raw, plain=None, enc=self._encryption.value)
            self._enqueue(raw)
            return
        if raw in self._handshake_echoes and time.monotonic() < self._echo_filter_until:
            # Late retransmit of a handshake frame (the device re-sends its e7
            # replies ~1 s later). Under the session key it decrypts to noise -
            # and occasionally to a frame with a valid checksum.
            _LOGGER.debug("%s: dropped late handshake retransmit", self.address)
            return
        plain = self._decrypt_rx(raw)
        self._capture("rx", wire=raw, plain=plain, enc=self._encryption.value)
        if not checksum_ok(plain):
            shown = _mask_secret(plain) if _carries_secret(plain) else plain
            _LOGGER.debug("%s: dropped frame with bad checksum: %s", self.address, shown.hex())
            return
        if plain[0] not in _KNOWN_RX_PRO_TYPES:
            _LOGGER.warning(
                "%s: RX unrecognised frame %s (%s)",
                self.address, describe_frame(plain, "rx")[0], plain.hex(),
            )
        self._enqueue(raw)
        if self._on_notify is not None:
            try:
                self._on_notify(plain)
            except Exception:  # pragma: no cover - callback must never break the pump
                _LOGGER.exception("%s: on_notify callback failed", self.address)

    def _drain(self) -> None:
        while not self._rx.empty():
            self._rx.get_nowait()

    async def _next_rx(self, timeout: float) -> bytes:
        """Next queued raw frame; raises TimeoutError, or _LinkLost on disconnect."""
        item = await asyncio.wait_for(self._rx.get(), timeout=max(timeout, 0.0))
        if item is None:
            raise _LinkLost
        return item

    # -- lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        """Connect (if needed) and set up the session."""
        await self._run(self._ensure_connected_locked, extend_warm=False)

    async def disconnect(self) -> None:
        async with self._lock:
            self._cancel_idle_timer()
            await self._disconnect_locked()

    async def close(self) -> None:
        """Disconnect and refuse further operations (owner is being torn down)."""
        self._closed = True
        await self.disconnect()

    async def _ensure_connected_locked(self) -> None:
        if self._closed:
            raise GoveeBleConnectionError(f"{self.address}: connection closed")
        if self.is_connected:
            return
        last_err: GoveeBleError | None = None
        for attempt in range(1, SESSION_ATTEMPTS + 1):
            try:
                await self._connect_locked()
                return
            except GoveeBleNotSupported:
                raise
            except GoveeBleError as err:
                last_err = err
                _LOGGER.debug(
                    "%s: session attempt %d/%d failed: %s",
                    self.address, attempt, SESSION_ATTEMPTS, err,
                )
        assert last_err is not None
        raise last_err

    async def _connect_locked(self) -> None:
        try:
            if self._client is None or not self._client.is_connected:
                self._client = await establish_connection(
                    BleakClientWithServiceCache,
                    self._ble_device,
                    self._ble_device.address,
                    disconnected_callback=self._on_disconnect,
                    max_attempts=CONNECT_MAX_ATTEMPTS,
                    # Re-resolve the device on every retry so a retry uses the
                    # best currently-reachable scanner/proxy, not the one that
                    # was best when the attempt started.
                    ble_device_callback=lambda: self._ble_device,
                )
                self._drain()
                await self._client.start_notify(NOTIFY_CHAR_UUID, self._handle_notify)
            await self._prepare_session()
        except (BleakError, TimeoutError, EOFError) as err:
            # A failed / half-open connect must not leave a stale client behind:
            # the next attempt would reuse it and hit "Service Discovery has not
            # been performed yet". Tear it down so every retry starts clean.
            await self._disconnect_locked()
            if isinstance(err, TimeoutError):
                raise GoveeBleTimeout(f"{self.address}: connect/handshake timed out") from err
            raise GoveeBleConnectionError(f"connect to {self.address} failed: {err}") from err
        except BaseException:
            await self._disconnect_locked()
            raise

    async def _read_bgc_encrypt_version(self) -> int | None:
        """Read the BGC-info characteristic and return the device's
        encryptVersion (0/1/2), or None if the characteristic is absent.
        Port of BgcInfoReader.a()/d(): data[0] is the format; for format 1 or
        2 the version is data[1]. Static per device, so read once."""
        if self._bgc_checked:
            return self._bgc_version
        client = self._require_client()
        char = client.services.get_characteristic(BGC_INFO_CHAR_UUID)
        if char is None:
            version = None
        else:
            try:
                data = bytes(await client.read_gatt_char(char))
            except BleakError as err:
                _LOGGER.debug("%s: BGC read failed: %s", self.address, err)
                return None  # transient: try again next connect
            version = data[1] if len(data) >= 2 and data[0] in (1, 2) else 0
        self._bgc_version, self._bgc_checked = version, True
        return version

    async def _prepare_session(self) -> None:
        """Refine the encryption mode (BGC read), run the handshake if needed,
        send post-handshake unlock frames, and mark the session ready."""
        self._session_key = None
        self._ready = False
        self._drain()
        self._require_client()
        # BGC is authoritative for the version (1=RC4, 2=GCM); 0/absent keeps the
        # advertisement-derived mode (isEncryptionSupported = adv OR bgc).
        bgc_version = await self._read_bgc_encrypt_version()
        if bgc_version == 2:
            self._encryption = Encryption.AES_GCM
        elif bgc_version == 1:
            self._encryption = Encryption.AES_RC4_PSK
        if self._encryption is Encryption.AES_GCM:
            raise GoveeBleNotSupported(
                f"{self.address}: AES-GCM (V2) handshake not yet implemented"
            )
        try:
            if self._encryption is Encryption.AES_RC4_PSK:
                await self._handshake()
        except _LinkLost as err:
            raise GoveeBleConnectionError(f"{self.address}: link lost during handshake") from err
        except TimeoutError as err:
            raise GoveeBleTimeout(f"{self.address}: handshake timed out") from err
        self._ready = True
        self._drain()
        _LOGGER.debug("%s: session ready (%s)", self.address, self._encryption.value)
        # Post-handshake unlock (e.g. secret-key check) - must run before any
        # user command, while we still hold the lock.
        if self._unlock_frames is not None:
            for frame in self._unlock_frames():
                try:
                    await self._send_write_verified(frame)
                except _WriteRejected as err:
                    raise GoveeBleAuthError(
                        f"{self.address}: device rejected the secret key - it is wrong "
                        "or the device was re-paired"
                    ) from err
                except GoveeBleTimeout:
                    _LOGGER.debug("%s: no ack for unlock frame (continuing)", self.address)

    async def _handshake(self) -> None:
        """e7 01 -> session key; e7 02 -> confirm. Matches replies by content
        (the device double-delivers its replies and retransmits them ~1 s later),
        and waits for the confirm so nothing else is sent while it's in flight."""
        loop = asyncio.get_running_loop()
        step1 = handshake.build_step1(PSK)
        self._capture("tx", wire=step1, plain=None, enc="e7")
        await self._raw_write(step1)
        deadline = loop.time() + HANDSHAKE_TIMEOUT
        echoes: set[bytes] = set()
        key: bytes | None = None
        while key is None:
            raw = await self._next_rx(deadline - loop.time())
            echoes.add(raw)
            key = handshake.parse_session_key(raw, PSK)
            if key is None:
                _LOGGER.debug("%s: skipping non-handshake frame during step 1", self.address)
        step2 = handshake.build_step2(PSK)
        self._capture("tx", wire=step2, plain=None, enc="e7")
        await self._raw_write(step2)
        echoes.add(step2)
        deadline = loop.time() + HANDSHAKE_STEP2_TIMEOUT
        try:
            while True:
                raw = await self._next_rx(deadline - loop.time())
                echoes.add(raw)
                if handshake.is_step2_ack(raw, PSK):
                    break
        except TimeoutError:
            _LOGGER.debug("%s: no e7 02 confirm (continuing)", self.address)
        self._session_key = key
        self._handshake_echoes = echoes
        self._echo_filter_until = time.monotonic() + HANDSHAKE_ECHO_WINDOW

    def _on_disconnect(self, client: BleakClientWithServiceCache) -> None:
        if client is not self._client:
            return  # a superseded client finishing its teardown late
        _LOGGER.debug("%s: disconnected", self.address)
        self._session_key = None
        self._ready = False
        # Drop the stale client so the next operation re-establishes from scratch.
        self._client = None
        self._enqueue(None)  # wake any waiter now instead of at its timeout

    async def _disconnect_locked(self) -> None:
        self._session_key = None
        self._ready = False
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except (BleakError, TimeoutError, EOFError) as err:
                _LOGGER.debug("%s: disconnect error: %s", self.address, err)

    # -- I/O ----------------------------------------------------------------

    def _require_client(self) -> BleakClientWithServiceCache:
        client = self._client
        if client is None:
            raise GoveeBleConnectionError(f"{self.address}: connection lost")
        return client

    async def _raw_write(self, frame: bytes) -> None:
        await self._require_client().write_gatt_char(WRITE_CHAR_UUID, frame, response=False)

    def _encrypt(self, frame_plaintext: bytes) -> bytes:
        if self._encryption is Encryption.AES_RC4_PSK:
            if self._session_key is None:
                raise GoveeBleConnectionError(
                    f"{self.address}: session key lost (disconnected mid-send)"
                )
            wire = encrypt(frame_plaintext, self._session_key)
        else:
            wire = frame_plaintext
        self._capture("tx", wire=wire, plain=frame_plaintext, enc=self._encryption.value)
        return wire

    async def _run(self, op: Callable[[], Awaitable[_T]], *, extend_warm: bool) -> _T:
        """Run one public operation: lock, deadline, idle-timer bookkeeping, and
        normalization of every transport failure to a GoveeBleError."""
        async with self._lock:
            self._cancel_idle_timer()
            if extend_warm:
                self._active_until = asyncio.get_running_loop().time() + ACTIVE_IDLE_DELAY
            try:
                async with asyncio.timeout(OPERATION_TIMEOUT):
                    return await op()
            except GoveeBleError:
                raise
            except _LinkLost as err:
                raise GoveeBleConnectionError(f"{self.address}: link lost mid-operation") from err
            except TimeoutError as err:
                await self._disconnect_locked()
                raise GoveeBleTimeout(
                    f"{self.address}: operation exceeded {OPERATION_TIMEOUT:.0f}s"
                ) from err
            except (BleakError, EOFError) as err:
                await self._disconnect_locked()
                raise GoveeBleConnectionError(f"{self.address}: {err}") from err
            finally:
                self._schedule_idle_timer()

    async def send(self, frame_plaintext: bytes, *, expect_ack: bool = True) -> bytes | None:
        """Connect+handshake if needed, then send one 20-byte plaintext frame.

        A write (proType 0x33) with expect_ack MIRRORS the app's comm layer: it
        waits for a reply matching [0x33, commandType], treats byte[2]==0 as
        success and raises GoveeBleError on an explicit rejection. If the link
        stops acking (or the write itself fails) it is torn down and the write
        is retried once on a fresh link, so a half-dead cached connection costs
        one reconnect instead of a failed command. Reads/unacked frames are
        sent once."""
        is_write = frame_plaintext[:1] == bytes([PRO_WRITE])

        async def op() -> bytes | None:
            if expect_ack and is_write:
                return await self._write_locked(frame_plaintext)
            await self._ensure_connected_locked()
            self._drain()
            await self._raw_write(self._encrypt(frame_plaintext))
            if not expect_ack:
                return None
            # Non-write with expect_ack: best-effort "next notification" reply.
            try:
                return self._decrypt_rx(await self._next_rx(COMMAND_ACK_TIMEOUT))
            except TimeoutError:
                return None

        return await self._run(op, extend_warm=is_write)

    async def send_many(self, frames: list[bytes]) -> None:
        """Send several write frames back to back under ONE lock acquisition and
        one connection (each still individually ack-verified), so a multi-part
        change (e.g. power + brightness + colour) can't be interleaved with a
        poll and never pays more than one connect."""

        async def op() -> None:
            for frame in frames:
                await self._write_locked(frame)

        await self._run(op, extend_warm=True)

    async def _write_locked(self, frame_plaintext: bytes) -> bytes:
        """Ack-verified write with one reconnect-and-retry. Only failures AFTER the
        link was up trigger the retry (a link that stops acking is usually
        half-dead while bleak still reports it connected); a connect failure is
        not retried here - establish_connection already retried it."""
        await self._ensure_connected_locked()
        try:
            return await self._send_write_verified(frame_plaintext)
        except (_LinkLost, BleakError, GoveeBleTimeout, GoveeBleConnectionError) as err:
            _LOGGER.debug(
                "%s: write 0x%02X failed on this link (%s); reconnecting to retry",
                self.address, frame_plaintext[1], err or type(err).__name__,
            )
            await self._disconnect_locked()
        await self._ensure_connected_locked()
        return await self._send_write_verified(frame_plaintext)

    async def send_unacked(self, frames: list[bytes], *, gap: float = 0.03) -> None:
        """Stream multi-packet frames (scene uploads) that the device does not
        ack individually, paced so a proxy's write queue can't drop them."""

        async def op() -> None:
            await self._ensure_connected_locked()
            self._drain()
            for frame in frames:
                await self._raw_write(self._encrypt(frame))
                await asyncio.sleep(gap)

        await self._run(op, extend_warm=True)

    async def _send_write_verified(self, frame_plaintext: bytes) -> bytes:
        command_type = frame_plaintext[1]
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug("%s: TX %s", self.address, describe_frame(frame_plaintext, "tx")[0])
        for attempt in range(WRITE_ATTEMPTS):
            self._drain()
            await self._raw_write(self._encrypt(frame_plaintext))
            ack = await self._await_reply(PRO_WRITE, command_type, WRITE_ACK_TIMEOUT)
            if ack is not None:
                if len(ack) >= 3 and ack[2] != 0:
                    _LOGGER.error(
                        "%s: device rejected write 0x%02X (status 0x%02X)",
                        self.address, command_type, ack[2],
                    )
                    raise _WriteRejected(
                        f"{self.address}: device rejected write 0x{command_type:02X} "
                        f"(status 0x{ack[2]:02X})"
                    )
                return ack
            _LOGGER.debug(
                "%s: no ack for write 0x%02X (attempt %d/%d)",
                self.address, command_type, attempt + 1, WRITE_ATTEMPTS,
            )
        raise GoveeBleTimeout(
            f"{self.address}: no ack for write 0x{command_type:02X} after {WRITE_ATTEMPTS} attempts"
        )

    async def _await_reply(self, opcode: int, command: int, timeout: float) -> bytes | None:
        """Collect notifications until one matches [opcode, command] (skipping
        unrelated pushes); None on timeout."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            try:
                raw = await self._next_rx(deadline - loop.time())
            except TimeoutError:
                return None
            dec = self._decrypt_rx(raw)
            if len(dec) >= 2 and dec[0] == opcode and dec[1] == command:
                return dec

    async def query(
        self,
        frame_plaintext: bytes,
        *,
        timeout: float = 3.0,
        opcode: int = 0xAC,
        terminal: int = 0xFF,
        idle_gap: float | None = BURST_IDLE_GAP,
    ) -> list[bytes]:
        """Send a query trigger and collect the resulting NOTIFY burst.

        Returns the decrypted frames received until a terminal frame (an
        ``opcode`` frame whose byte 1 == ``terminal``) arrives, the device goes
        quiet for ``idle_gap`` after its last ``opcode`` frame (a lost terminator
        must not cost the whole timeout), or ``timeout`` elapses."""

        async def op() -> list[bytes]:
            await self._ensure_connected_locked()
            self._drain()
            await self._raw_write(self._encrypt(frame_plaintext))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            frames: list[bytes] = []
            seen_opcode = False
            while True:
                remaining = deadline - loop.time()
                if seen_opcode and idle_gap is not None:
                    remaining = min(remaining, idle_gap)
                if remaining <= 0:
                    break
                try:
                    raw = await self._next_rx(remaining)
                except TimeoutError:
                    break
                dec = self._decrypt_rx(raw)
                frames.append(dec)
                if len(dec) >= 2 and dec[0] == opcode:
                    seen_opcode = True
                    if dec[1] == terminal:
                        break
            return frames

        return await self._run(op, extend_warm=False)

    # -- idle disconnect ----------------------------------------------------

    def _cancel_idle_timer(self) -> None:
        # Bumping the generation also invalidates an idle task that already
        # fired and is queued on the lock behind this operation.
        self._idle_generation += 1
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _schedule_idle_timer(self) -> None:
        self._cancel_idle_timer()
        if self._idle_disconnect <= 0 or self._client is None:
            return
        loop = asyncio.get_running_loop()
        # Base idle after any activity, but never cut short a warm window opened
        # by a recent user command (a poll firing mid-window won't shorten it).
        delay = max(self._idle_disconnect, self._active_until - loop.time())
        self._idle_timer = loop.call_later(delay, self._idle_fired, self._idle_generation)

    def _idle_fired(self, generation: int) -> None:
        if generation != self._idle_generation:
            return
        task = asyncio.get_running_loop().create_task(self._idle_disconnect_task(generation))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _idle_disconnect_task(self, generation: int) -> None:
        async with self._lock:
            if generation != self._idle_generation:
                return  # an operation ran since the timer fired
            self._idle_timer = None
            await self._disconnect_locked()


def now_ts() -> int:
    """Current unix timestamp (for sync_time frames)."""
    return int(time.time())
