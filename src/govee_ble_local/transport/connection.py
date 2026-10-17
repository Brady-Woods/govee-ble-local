"""The encrypted BLE session: connect, handshake, send/receive.

Owns the bleak connection, runs the e7 handshake to negotiate the session
key, then encrypts every outgoing frame and decrypts every notification with
it. Handshake-phase notifications are queued raw (the handshake parser needs
the ciphertext); every later notification is decrypted in the notify callback
and queued as PLAINTEXT, so a disconnect (which discards the session key) can
never turn an already-received reply into garbage.

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
* Teardown is detached and bounded: ``client.disconnect()`` runs as its own task
  (not cancelled with the caller) and is awaited for at most ``DISCONNECT_TIMEOUT``,
  so a wedged proxy can't hold the device lock. Its errors are logged, never raised.
* Errors are normalised: bleak/asyncio/timeout failures surface as typed
  :class:`GoveeBleError` subclasses (auth and not-supported errors are permanent and
  never retried; a write the device refuses is :class:`GoveeBleCommandRejected`).
* Junk notifications (late handshake retransmits, frames failing the XOR
  checksum) are dropped at the source, so they can't be mistaken for replies.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Final, Literal, TypeVar, overload

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from ..const import (
    ACTIVE_IDLE_DELAY,
    BGC_INFO_CHAR_UUID,
    BURST_IDLE_GAP,
    CONNECT_MAX_ATTEMPTS,
    DISCONNECT_TIMEOUT,
    FRAME_LEN,
    HANDSHAKE_ECHO_WINDOW,
    HANDSHAKE_STEP2_TIMEOUT,
    HANDSHAKE_TIMEOUT,
    IDLE_DISCONNECT_DELAY,
    NOTIFY_CHAR_UUID,
    OPERATION_TIMEOUT,
    PSK,
    QUERY_DEFAULT_TIMEOUT,
    QUERY_TERMINAL,
    SERVICE_UUID,
    SESSION_ATTEMPTS,
    UNACKED_SEND_GAP,
    WRITE_ACK_TIMEOUT,
    WRITE_ATTEMPTS,
    WRITE_CHAR_UUID,
)
from ..crypto import FrameCipher, checksum, checksum_ok
from ..exceptions import (
    GoveeBleAuthError,
    GoveeBleCommandRejected,
    GoveeBleConnectionError,
    GoveeBleError,
    GoveeBleHandshakeConnectionError,
    GoveeBleHandshakeTimeout,
    GoveeBleInvalidValue,
    GoveeBleNotSupported,
    GoveeBleTimeout,
)
from ..models import Encryption
from ..wire.build import (
    CMD_SECRET_READ,
    CMD_SECRET_WRITE,
    PRO_MULTI_AC,
    PRO_READ,
    PRO_WRITE,
    _F,
)
from ..wire.describe import describe_frame
from ..wire.parse import bgc_encrypt_version, parse_write_ack
from ..wire.reassemble import burst_chunks
from . import handshake
from .framelog import Direction, FrameLog

_LOGGER = logging.getLogger(__name__)
# Dedicated frame-tier logger: the full-session firehose (every TX/RX frame). Off
# unless enabled (e.g. HA `logger: logs: govee_ble_local.frames: debug`); guarded by
# isEnabledFor so it costs nothing when disabled. This is the capture surface over
# a Bluetooth proxy.
_FRAMES = logging.getLogger("govee_ble_local.frames")

# proTypes a device legitimately sends us (the spec's rx_pro_type enum); anything else is surfaced as a
# warning, and queued all the same.
_KNOWN_RX_PRO_TYPES: Final = frozenset(int(t) for t in _F.RxProType)
# Unsolicited pushes accumulate between operations; never let them grow unbounded.
_RX_QUEUE_MAX: Final = 64
# write_payload_size bounds: the app's MtuConfig default (ATT MTU 23 - 3) and its requested MTU 512 - 3.
_MIN_WRITE_PAYLOAD: Final = 20
_MAX_WRITE_PAYLOAD: Final = 509
# DISCONNECT_TIMEOUT and QUERY_TERMINAL live in ..const with the other transport constants; they
# are imported into this module's namespace, which is where tests patch / read them.

# What the BLE stack raises when the link fails under us, mapped to GoveeBleConnectionError
# (and retried where a retry applies) at every site that touches the transport: bleak's own
# errors, EOFError (dbus-fast losing the bus) and ConnectionError (BrokenPipeError /
# ConnectionResetError when the D-Bus socket drops on a disconnect; bleak-retry-connector counts
# BrokenPipeError as transient too). The builtin TimeoutError is NOT one of them: each site maps
# it to GoveeBleTimeout itself.
_TRANSPORT_ERRORS: Final = (BleakError, EOFError, ConnectionError)

_T = TypeVar("_T")


def _err_text(err: BaseException) -> str:
    """An exception's message, or its class name when it has none (e.g. a bare EOFError())."""
    return str(err) or type(err).__name__


def _carries_secret(plain: bytes) -> bool:
    """True for a frame that carries the account-lock secret: a ``33 b2`` check write or an ``aa b1`` read
    reply. Matched on the two header bytes on purpose, not through the spec's frame reader: masking must
    fail closed, so a secret frame whose body doesn't parse (short, malformed, a capture fragment) is
    still masked before it reaches a log or a frame capture."""
    return len(plain) >= 2 and (
        (plain[0] == PRO_WRITE and plain[1] == CMD_SECRET_WRITE)  # secret check write
        or (plain[0] == PRO_READ and plain[1] == CMD_SECRET_READ)  # secret read reply
    )


def _mask_secret(frame: bytes) -> bytes:
    """Blank the payload of a secret-carrying frame, keeping its 2-byte header.

    The single masking helper (used for plain, wire and log output). A full-length
    frame keeps a VALID trailing checksum - recomputed over the masked bytes, so it
    never leaks the XOR of the secret; any other length is just header + blanks."""
    if len(frame) != FRAME_LEN:
        return frame[:2] + b"\xff" * (len(frame) - 2)
    body = frame[:2] + b"\xff" * (FRAME_LEN - 3)
    return body + bytes([checksum(body)])


class _LinkLost(Exception):
    """Internal: the link dropped while waiting on a reply (queue sentinel)."""


# The device explicitly refused a write (ack status byte != 0). The historical private name
# stays as an alias of the public class, so existing `except _WriteRejected` code keeps working.
_WriteRejected = GoveeBleCommandRejected


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
        unlock_frames: Callable[[], list[bytes]] | None = None,
        idle_disconnect: float = IDLE_DISCONNECT_DELAY,
        frame_log: str | None = None,
        session_frames: Callable[[], list[bytes]] | None = None,
    ) -> None:
        self._ble_device = ble_device
        self._encryption = encryption
        # Optional bidirectional frame capture (frame_log arg or $GOVEE_FRAME_LOG).
        self._framelog = FrameLog.resolve(frame_log, ble_device.address)
        # Frames to send immediately after every handshake, before any user
        # command - e.g. the plug family's `33 b2` secret-key check. A provider
        # (not a static list) so a device can supply/refresh it lazily.
        self._unlock_frames = unlock_frames
        # Writes the app sends once per connection after the unlock, whose rejection or missing ack
        # changes nothing - e.g. the plugs' `33 b5` time sync. A provider, called per session, so a
        # frame can carry the time it is sent.
        self._session_frames = session_frames
        self._idle_disconnect = idle_disconnect
        self._client: BleakClientWithServiceCache | None = None
        self._session_key: bytes | None = None
        # The session key's cipher, its key setup done once per session (crypto.FrameCipher); set
        # and cleared with _session_key.
        self._session_cipher: FrameCipher | None = None
        self._ready = False  # connected AND session set up
        self._closed = False
        self._bgc_version: int | None = None  # cached after the first successful read
        self._bgc_checked = False
        # Wire bytes of this session's handshake frames + when to stop filtering them.
        self._handshake_echoes: set[bytes] = set()
        self._echo_filter_until = 0.0
        # Unknown RX proTypes already warned about: repeats go to DEBUG (no log flood). Kept for
        # this object's life (the device's), not reset on reconnect.
        self._warned_pro_types: set[int] = set()
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

    @property
    def write_payload_size(self) -> int:
        """The most bytes this link takes in one write without response: the app's ``mtuSize``
        (``MtuConfig.getAvailableMtuSize`` = the ATT MTU - 3, at least 20; ``MtuConfig.java:48-71``).
        It is the write characteristic's ``max_write_without_response_size`` (bleak: the ATT MTU - 3 on
        BlueZ >= 5.62, ``maximumWriteValueLength`` on CoreBluetooth), clamped to 20..509 (the app asks
        for an MTU of 512, ``VM4LightH60A6.T3:736-738``). 20, the app's default, when not connected or
        when the backend can't say. No I/O."""
        client = self._client
        if client is None or not client.is_connected:
            return _MIN_WRITE_PAYLOAD
        try:
            service = next((s for s in client.services if s.uuid == SERVICE_UUID), None)
            char = service.get_characteristic(WRITE_CHAR_UUID) if service is not None else None
            size = None if char is None else char.max_write_without_response_size
        except (BleakError, AttributeError):
            return _MIN_WRITE_PAYLOAD
        if isinstance(size, bool) or not isinstance(size, int):
            return _MIN_WRITE_PAYLOAD
        return max(_MIN_WRITE_PAYLOAD, min(size, _MAX_WRITE_PAYLOAD))

    def update_ble_device(self, ble_device: BLEDevice) -> None:
        """Adopt a fresher BLEDevice (e.g. from the latest advertisement). Used from
        the next session attempt on: every attempt re-reads it (an attempt already
        inside establish_connection keeps the device it started with)."""
        self._ble_device = ble_device

    # -- capture ------------------------------------------------------------

    def _capture(self, direction: Direction, *, wire: bytes, plain: bytes | None, enc: str) -> None:
        """Route one frame to the persistent JSONL sink (if configured) AND the
        frame-tier logger (if enabled) - one choke point, one shape. Account-lock
        secret payloads (33 b2 write / aa b1 read reply) are masked: captures end
        up in logs and diagnostics downloads. The wire bytes are masked too - on a
        plaintext link they ARE the secret, and on an encrypted link the session
        key is derivable from the captured handshake plus the library's public PSK."""
        if plain is not None and _carries_secret(plain):
            plain = _mask_secret(plain)
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
        """Notify-callback-time decrypt (session key, post-handshake only)."""
        if self._encryption is Encryption.AES_RC4_PSK and self._session_cipher is not None:
            return self._session_cipher.decrypt(raw)
        return raw

    def _enqueue(self, item: bytes | None) -> None:
        """Queue a frame (or the None link-lost sentinel). When full, the oldest
        FRAME is dropped - never the sentinel, which is what wakes a waiter."""
        if item is not None and self._rx.qsize() >= _RX_QUEUE_MAX:
            kept: list[bytes | None] = []
            dropped = False
            while not self._rx.empty():
                queued = self._rx.get_nowait()
                if not dropped and queued is not None:
                    dropped = True  # drop the oldest unsolicited frame
                    continue
                kept.append(queued)
            for queued in kept:
                self._rx.put_nowait(queued)
        self._rx.put_nowait(item)

    def _handle_notify(self, _char: BleakGATTCharacteristic | None, data: bytearray) -> None:
        # `_char` is unused (there is one notify characteristic), so None is accepted too.
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
            # Device-controlled input: warn once per proType, then DEBUG, so firmware that
            # keeps pushing an unmodelled frame can't flood the log.
            if plain[0] not in self._warned_pro_types:
                self._warned_pro_types.add(plain[0])
                _LOGGER.warning(
                    "%s: RX unrecognised frame %s (%s); further proType 0x%02X frames at DEBUG",
                    self.address, describe_frame(plain, "rx")[0], plain.hex(), plain[0],
                )
            elif _LOGGER.isEnabledFor(logging.DEBUG):  # don't build the label per repeat when off
                _LOGGER.debug(
                    "%s: RX unrecognised frame %s (%s)",
                    self.address, describe_frame(plain, "rx")[0], plain.hex(),
                )
        self._enqueue(plain)  # plaintext: survives a disconnect nulling the session key

    def _drain(self) -> None:
        while not self._rx.empty():
            self._rx.get_nowait()

    async def _next_rx(self, timeout: float) -> bytes:
        """Next queued frame (raw during the handshake, plaintext afterwards);
        raises TimeoutError, or _LinkLost on disconnect. An already-queued frame
        is returned even with no budget left (wait_for(timeout=0) would not)."""
        try:
            item = self._rx.get_nowait()
        except asyncio.QueueEmpty:
            if timeout <= 0:
                raise TimeoutError from None
            item = await asyncio.wait_for(self._rx.get(), timeout=timeout)
        if item is None:
            raise _LinkLost
        return item

    # -- lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        """Connect (if needed) and run the handshake/unlock, under the operation deadline.

        Raises only :class:`GoveeBleError` subclasses."""
        await self._run(self._ensure_connected_locked, extend_warm=False)

    async def disconnect(self) -> None:
        """Drop the session and link now (bounded by ``DISCONNECT_TIMEOUT``)."""
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
        if self._bgc_checked and self._encryption is Encryption.AES_GCM:
            # Settled (the BGC result is cached): don't open a link just to refuse again.
            raise GoveeBleNotSupported(
                f"{self.address}: AES-GCM (V2) handshake not yet implemented"
            )
        last_err: GoveeBleError | None = None
        for attempt in range(1, SESSION_ATTEMPTS + 1):
            try:
                await self._connect_locked()
                return
            except (GoveeBleNotSupported, GoveeBleAuthError):
                raise  # permanent: a retry can only repeat the same refusal
            except GoveeBleError as err:
                last_err = err
                _LOGGER.debug(
                    "%s: session attempt %d/%d failed: %s",
                    self.address, attempt, SESSION_ATTEMPTS, err,
                )
        if last_err is None:  # pragma: no cover - SESSION_ATTEMPTS >= 1
            raise AssertionError("unreachable: no session attempt ran")
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
                    # A fresher BLEDevice reaches the next session attempt through
                    # self._ble_device (each attempt re-reads it). This callback is only
                    # offered to bleak-retry-connector: 4.x accepts it but never calls
                    # it (it builds the client once, before its retry loop).
                    ble_device_callback=lambda: self._ble_device,
                    # Resolve only the Govee service. On macOS, CoreBluetooth refuses descriptor
                    # discovery on a characteristic of another service that some firmwares expose
                    # (H60A6: "UUID is not allowed for this operation"), which fails the whole
                    # connect. bleak's BlueZ and WinRT backends apply the same filter to
                    # client.services; the BGC lookup below is scoped to this service anyway.
                    services=[SERVICE_UUID],
                )
                self._drain()
                await self._client.start_notify(NOTIFY_CHAR_UUID, self._handle_notify)
            await self._prepare_session()
        # A failed / half-open connect must not leave a stale client behind: the next
        # attempt would reuse it and hit "Service Discovery has not been performed yet".
        # Every branch tears it down so each retry starts clean.
        except TimeoutError as err:
            await self._disconnect_locked()
            raise GoveeBleTimeout(f"{self.address}: connect timed out") from err
        except _TRANSPORT_ERRORS as err:
            await self._disconnect_locked()
            raise GoveeBleConnectionError(f"{self.address}: connect failed: {_err_text(err)}") from err
        except GoveeBleError:
            await self._disconnect_locked()
            raise
        except Exception as err:
            # Any other backend error (e.g. a RuntimeError from start_notify) is still a
            # failed connect: callers only ever see GoveeBleError subclasses.
            await self._disconnect_locked()
            raise GoveeBleConnectionError(f"{self.address}: connect failed: {err!r}") from err
        except BaseException:  # cancellation (e.g. the operation deadline): close, re-raise
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
        # Port of BgcInfoReader.a (BgcInfoReader.java:58-72): walk gatt.getServices() and take
        # the FIRST service whose UUID is Constants.d (...1910), then its Constants.b (...2b12)
        # characteristic. bleak's UUID lookups don't match that: get_service() raises when two
        # services share 1910, and the collection-wide get_characteristic() raises because 2b12
        # also sits under the OTA service 1912. Iterating the collection never raises, and
        # BleakGATTService.get_characteristic returns the first match.
        try:
            service = next((s for s in client.services if s.uuid == SERVICE_UUID), None)
            char = service.get_characteristic(BGC_INFO_CHAR_UUID) if service is not None else None
        except BleakError as err:  # client.services: "Service Discovery has not been performed yet"
            _LOGGER.debug("%s: BGC lookup failed: %s", self.address, err)
            return None  # not cached: the advertised mode stands, retried next connect
        if char is None:
            version = None
        else:
            try:
                data = bytes(await client.read_gatt_char(char))
            except BleakError as err:
                _LOGGER.debug("%s: BGC read failed: %s", self.address, err)
                return None  # transient: try again next connect
            version = bgc_encrypt_version(data)   # the spec's bgc_info (BgcInfoReader.a / .d)
        self._bgc_version, self._bgc_checked = version, True
        return version

    async def _prepare_session(self) -> None:
        """Refine the encryption mode (BGC read), run the handshake if needed,
        send post-handshake unlock frames, then the session frames, and mark the session ready."""
        self._reset_session()
        self._drain()
        self._require_client()
        # BGC is authoritative for the version (1=RC4, 2=GCM); 0/absent keeps the
        # advertisement-derived mode (isEncryptionSupported = adv OR bgc).
        bgc_version = await self._read_bgc_encrypt_version()
        if bgc_version == int(_F.EncryptVersion.aes_gcm):
            self._encryption = Encryption.AES_GCM
        elif bgc_version == int(_F.EncryptVersion.aes_rc4_psk):
            self._encryption = Encryption.AES_RC4_PSK
        if self._encryption is Encryption.AES_GCM:
            raise GoveeBleNotSupported(
                f"{self.address}: AES-GCM (V2) handshake not yet implemented"
            )
        try:
            if self._encryption is Encryption.AES_RC4_PSK:
                await self._handshake()
        except (_LinkLost, GoveeBleConnectionError) as err:  # dropped mid-wait / between writes
            raise GoveeBleHandshakeConnectionError(
                f"{self.address}: link lost during handshake"
            ) from err
        except _TRANSPORT_ERRORS as err:
            # An e7 write itself failed: how BlueZ and proxies usually report a dropped link
            # (BleakError "Not connected", BrokenPipeError, EOFError). Mapped like a drop
            # mid-wait, and the session loop retries it the same way. The message stays neutral:
            # the same clause also takes errors that are not a link loss (bleak's
            # BleakCharacteristicNotFoundError for a stale service cache).
            raise GoveeBleHandshakeConnectionError(
                f"{self.address}: handshake write failed: {_err_text(err)}"
            ) from err
        except TimeoutError as err:
            raise GoveeBleHandshakeTimeout(f"{self.address}: handshake timed out") from err
        self._ready = True
        self._drain()
        _LOGGER.debug("%s: session ready (%s)", self.address, self._encryption.value)
        # Post-handshake unlock (e.g. secret-key check) - must run before any
        # user command, while we still hold the lock.
        if self._unlock_frames is not None:
            for frame in self._unlock_frames():
                try:
                    await self._send_write_verified(frame)
                except _LinkLost as err:
                    raise GoveeBleConnectionError(
                        f"{self.address}: link lost during unlock"
                    ) from err
                except GoveeBleCommandRejected as err:
                    raise GoveeBleAuthError(
                        f"{self.address}: device rejected the secret key - it is wrong "
                        "or the device was re-paired"
                    ) from err
                except GoveeBleTimeout:
                    _LOGGER.debug("%s: no ack for unlock frame (continuing)", self.address)
        if self._session_frames is not None:
            for frame in self._session_frames():
                try:
                    await self._send_write_verified(frame)
                except _LinkLost as err:
                    raise GoveeBleConnectionError(
                        f"{self.address}: link lost during session setup"
                    ) from err
                except (GoveeBleCommandRejected, GoveeBleTimeout) as err:
                    _LOGGER.debug("%s: session frame not acked (continuing): %s", self.address, err)

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
        self._session_cipher = FrameCipher(key)
        self._handshake_echoes = echoes
        self._echo_filter_until = time.monotonic() + HANDSHAKE_ECHO_WINDOW

    def _reset_session(self) -> None:
        """Forget the negotiated session: no key (nor its cipher), not ready (the one place they
        are cleared)."""
        self._session_key = None
        self._session_cipher = None
        self._ready = False

    def _on_disconnect(self, client: BleakClientWithServiceCache) -> None:
        if client is not self._client:
            return  # a superseded client finishing its teardown late
        _LOGGER.debug("%s: disconnected", self.address)
        self._reset_session()
        # Drop the stale client so the next operation re-establishes from scratch.
        self._client = None
        self._enqueue(None)  # wake any waiter now instead of at its timeout

    async def _disconnect_locked(self) -> None:
        self._reset_session()
        client, self._client = self._client, None
        if client is None:
            return
        # Run the teardown as its own task: bounded by DISCONNECT_TIMEOUT so a wedged
        # proxy can't hold the device lock forever, yet NOT cancelled if our caller is
        # cancelled (the client is already forgotten, so nothing else would close it).
        task = asyncio.get_running_loop().create_task(client.disconnect())
        self._background.add(task)
        task.add_done_callback(self._disconnect_done)
        _done, pending = await asyncio.wait({task}, timeout=DISCONNECT_TIMEOUT)
        if pending:
            _LOGGER.debug(
                "%s: disconnect did not finish within %.0fs; abandoning it",
                self.address, DISCONNECT_TIMEOUT,
            )
            task.cancel()

    def _disconnect_done(self, task: asyncio.Task[None]) -> None:
        self._background.discard(task)
        if task.cancelled():
            return
        err = task.exception()  # always retrieved: never an "unretrieved exception"
        if err is not None:
            _LOGGER.debug("%s: disconnect error: %r", self.address, err, exc_info=err)

    # -- I/O ----------------------------------------------------------------

    def _require_client(self) -> BleakClientWithServiceCache:
        client = self._client
        if client is None:
            raise GoveeBleConnectionError(f"{self.address}: connection lost")
        return client

    async def _raw_write(self, frame: bytes) -> None:
        await self._require_client().write_gatt_char(WRITE_CHAR_UUID, frame, response=False)

    def _encrypt(self, frame_plaintext: bytes) -> bytes:
        """Pure transform: plaintext frame -> wire bytes (no logging/capture)."""
        if self._encryption is Encryption.AES_RC4_PSK:
            if self._session_cipher is None:
                raise GoveeBleConnectionError(
                    f"{self.address}: session key lost (disconnected mid-send)"
                )
            wire = self._session_cipher.encrypt(frame_plaintext)
        else:
            wire = frame_plaintext
        return wire

    async def _send_frame(self, frame_plaintext: bytes) -> None:
        """Encrypt one plaintext frame, capture/log it as TX, then write it."""
        wire = self._encrypt(frame_plaintext)
        self._capture("tx", wire=wire, plain=frame_plaintext, enc=self._encryption.value)
        await self._raw_write(wire)

    async def _run(self, op: Callable[[], Awaitable[_T]], *, extend_warm: bool) -> _T:
        """Run one public operation: lock, deadline, idle-timer bookkeeping, and
        normalization of every transport failure to a GoveeBleError."""
        async with self._lock:
            self._cancel_idle_timer()
            if extend_warm:
                self._active_until = asyncio.get_running_loop().time() + ACTIVE_IDLE_DELAY
            deadline = asyncio.timeout(OPERATION_TIMEOUT)
            try:
                async with deadline:
                    return await op()
            except GoveeBleError:
                raise
            except _LinkLost as err:
                raise GoveeBleConnectionError(f"{self.address}: link lost mid-operation") from err
            except TimeoutError as err:
                await self._disconnect_locked()
                if deadline.expired():
                    raise GoveeBleTimeout(
                        f"{self.address}: operation exceeded {OPERATION_TIMEOUT:.0f}s"
                    ) from err
                # Raised by the BLE stack itself (e.g. a write), not by the deadline.
                raise GoveeBleTimeout(f"{self.address}: BLE I/O timed out ({_err_text(err)})") from err
            except _TRANSPORT_ERRORS as err:
                await self._disconnect_locked()
                raise GoveeBleConnectionError(f"{self.address}: {_err_text(err)}") from err
            except Exception as err:
                # Contract: callers only ever see GoveeBleError subclasses. The link state is
                # unknown after an unexpected backend error, so it is torn down as well.
                await self._disconnect_locked()
                raise GoveeBleConnectionError(
                    f"{self.address}: unexpected transport error: {err!r}"
                ) from err
            finally:
                self._schedule_idle_timer()

    @overload
    async def send(self, frame_plaintext: bytes, *, expect_ack: Literal[True] = ...) -> bytes: ...

    @overload
    async def send(self, frame_plaintext: bytes, *, expect_ack: Literal[False]) -> None: ...

    @overload
    async def send(self, frame_plaintext: bytes, *, expect_ack: bool) -> bytes | None: ...

    async def send(self, frame_plaintext: bytes, *, expect_ack: bool = True) -> bytes | None:
        """Connect+handshake if needed, then send one 20-byte plaintext frame.

        A write (proType 0x33) with expect_ack MIRRORS the app's comm layer: it
        waits for a reply matching [0x33, commandType], treats byte[2]==0 as
        success and raises :class:`GoveeBleCommandRejected` on an explicit
        rejection. If the link stops acking (or the write itself fails) it is torn
        down and the write is retried once on a fresh link, so a half-dead cached
        connection costs one reconnect instead of a failed command. ``expect_ack=False``
        sends the frame once and returns None. Only writes have an ack to wait for: a
        non-write frame with ``expect_ack=True`` raises GoveeBleInvalidValue
        (use :meth:`query` to read replies). A frame shorter than 2 bytes (no commandType)
        raises GoveeBleInvalidValue before any I/O."""
        self._check_frame(frame_plaintext)
        is_write = frame_plaintext[0] == PRO_WRITE
        if expect_ack:
            self._check_write_frame(frame_plaintext)

        async def op() -> bytes | None:
            if expect_ack:
                return await self._write_locked(frame_plaintext)
            await self._ensure_connected_locked()
            self._drain()
            await self._send_frame(frame_plaintext)
            return None

        return await self._run(op, extend_warm=is_write)

    def _check_frame(self, frame: bytes) -> None:
        if len(frame) < 2:
            raise GoveeBleInvalidValue(
                f"{self.address}: frame must be at least 2 bytes (proType, commandType); "
                f"got {len(frame)}"
            )

    def _check_write_frame(self, frame: bytes) -> None:
        """Validate a frame for an ack-verified send: 2+ bytes AND a write (proType 0x33)."""
        self._check_frame(frame)
        if frame[0] != PRO_WRITE:
            raise GoveeBleInvalidValue(
                f"{self.address}: only a write frame (proType 0x{PRO_WRITE:02X}) has an ack to "
                f"wait for; got proType 0x{frame[0]:02X} (use query() for reads)"
            )

    async def send_many(self, frames: list[bytes]) -> None:
        """Send several write frames back to back under ONE lock acquisition and
        one connection (each still individually ack-verified), so a multi-part
        change (e.g. power + brightness + colour) can't be interleaved with a
        poll and never pays more than one connect. Every frame must be a write
        (proType 0x33); anything else raises GoveeBleInvalidValue before any I/O."""
        for frame in frames:
            self._check_write_frame(frame)

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
        except (
            _LinkLost, *_TRANSPORT_ERRORS, TimeoutError, GoveeBleTimeout, GoveeBleConnectionError,
        ) as err:
            # (The operation deadline arrives here as CancelledError, never TimeoutError.)
            _LOGGER.debug(
                "%s: write 0x%02X failed on this link (%s); reconnecting to retry",
                self.address, frame_plaintext[1], _err_text(err),
            )
            await self._disconnect_locked()
        await self._ensure_connected_locked()
        return await self._send_write_verified(frame_plaintext)

    async def send_unacked(
        self, frames: list[bytes] | Callable[[int], list[bytes]], *, gap: float = UNACKED_SEND_GAP
    ) -> None:
        """Stream multi-packet frames (scene uploads) that the device does not
        ack individually, paced so a proxy's write queue can't drop them.

        ``frames`` may be a function of the link's :attr:`write_payload_size`, called once the link
        is up (after a reconnect too), so frames sized to the MTU fit the link they go out on."""

        async def op() -> None:
            await self._ensure_connected_locked()
            self._drain()
            for frame in frames(self.write_payload_size) if callable(frames) else frames:
                await self._send_frame(frame)
                await asyncio.sleep(gap)

        await self._run(op, extend_warm=True)

    async def _send_write_verified(self, frame_plaintext: bytes) -> bytes:
        command_type = frame_plaintext[1]
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug("%s: TX %s", self.address, describe_frame(frame_plaintext, "tx")[0])
        for attempt in range(WRITE_ATTEMPTS):
            self._drain()
            await self._send_frame(frame_plaintext)
            ack = await self._await_reply(PRO_WRITE, command_type, WRITE_ACK_TIMEOUT)
            status = None if ack is None else parse_write_ack(ack)   # write_ack.result
            if ack is not None:
                if status not in (None, 0):
                    # DEBUG only: the typed exception carries the same text and the caller
                    # decides whether to log it (the unlock path re-raises it as an auth error).
                    _LOGGER.debug(
                        "%s: device rejected write 0x%02X (status 0x%02X)",
                        self.address, command_type, status,
                    )
                    raise GoveeBleCommandRejected(
                        f"{self.address}: device rejected write 0x{command_type:02X} "
                        f"(status 0x{status:02X})"
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
            # Matched on the two header bytes, like every RX filter here: the transport routes frames
            # before anything decodes them, and a reply whose body doesn't parse must still end its wait
            # (the caller's decoder then reports it), as wire.parse.find_reply does with the reader.
            if len(raw) >= 2 and raw[0] == opcode and raw[1] == command:
                return raw

    async def query(
        self,
        frame_plaintext: bytes,
        *,
        timeout: float = QUERY_DEFAULT_TIMEOUT,
        opcode: int = PRO_MULTI_AC,
        terminal: int = QUERY_TERMINAL,
        idle_gap: float | None = BURST_IDLE_GAP,
    ) -> list[bytes]:
        """Send a query trigger and collect the resulting NOTIFY burst.

        Returns the decrypted frames received until a terminal frame (an
        ``opcode`` frame whose byte 1 == ``terminal``) arrives, the 0xAC burst is
        complete (``opcode`` 0xAC: :func:`..wire.reassemble.burst_chunks`, the app's
        rule, so a one-packet reply or a burst whose last packet carries its position
        instead of 0xFF ends there too), the device goes
        quiet for ``idle_gap`` after its last ``opcode`` frame (a lost terminator
        must not cost the whole timeout), or ``timeout`` elapses. Frames already
        queued when the budget runs out are still collected. ``idle_gap`` is armed
        by ANY ``opcode`` frame, so a single-reply read (terminal = the expected
        command) should pass ``idle_gap=None``: there a stray ``opcode`` frame (a late
        reply to an earlier read) must not cut the wait short."""

        async def op() -> list[bytes]:
            await self._ensure_connected_locked()
            self._drain()
            await self._send_frame(frame_plaintext)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            frames: list[bytes] = []
            burst: list[bytes] = []   # the 0xAC chunks so far, when collecting a status burst
            seen_opcode = False
            while True:
                remaining = deadline - loop.time()
                if seen_opcode and idle_gap is not None:
                    remaining = min(remaining, idle_gap)
                try:
                    # With no budget left this only drains what is already queued (no
                    # yield), so the loop stays bounded; a queued terminator is kept.
                    dec = await self._next_rx(remaining)
                except TimeoutError:
                    break
                frames.append(dec)
                if len(dec) >= 2 and dec[0] == opcode:
                    seen_opcode = True
                    if dec[1] == terminal:
                        break
                    if opcode == PRO_MULTI_AC:
                        burst.append(dec)
                        if burst_chunks(burst)[1]:
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
