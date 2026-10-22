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
  subclasses - never a bare bleak/asyncio exception. A caller can hold one
  operation's connect to its own ``max_attempts`` and a time limit
  (:class:`ConnectBudget`), so it gives the lock up sooner on a device that
  doesn't answer.
* A link that stops acknowledging writes is treated as half-dead: it is torn
  down and the write is retried once on a freshly established link.
* A disconnect wakes anything waiting on a reply immediately instead of letting
  it run out its timeout, and a superseded client never reaches the current
  session: neither its late disconnect callback nor its notifications.
* The idle-disconnect timer can never fire after newer activity (generation
  counter), and its task is held so it can't be garbage-collected mid-flight.
  The warm window a write opens (``ACTIVE_IDLE_DELAY``) lives with the link it
  ends on: every teardown (``disconnect``, the idle one, a failed operation's)
  and a device-side drop end it, so the next session starts at the base idle
  delay (a write on it opens its own window).
* Teardown is detached and bounded: ``client.disconnect()`` runs as its own task
  (not cancelled with the caller) and is awaited for at most ``DISCONNECT_TIMEOUT``,
  so a wedged proxy can't hold the device lock; at that bound it is abandoned
  (cancelled), even when the caller was cancelled mid-wait. When a connect limit
  (:class:`ConnectBudget`) runs out, every teardown still running whose wait was cut
  short is waited out to its bound before the lock is given up, so the next connect
  doesn't start while an old link's disconnect still runs; a cancellation of that
  wait (the operation deadline, the caller's) gives the lock up at once and leaves
  each teardown to its abandon at its bound, as without a limit. The disconnect's
  errors are logged, never raised.
* A ``send_once`` (the device-list card's session, a ``session: per_command`` row) holds no link: it
  connects, writes its one frame, reads the reply for at most the write-ack timeout, and tears the link
  down, each session ``PER_COMMAND_SESSION_GAP`` after the last one's release.
* Errors are normalised: bleak/asyncio/timeout failures surface as typed
  :class:`GoveeBleError` subclasses (auth and not-supported errors are permanent and
  never retried; a write the device refuses is :class:`GoveeBleCommandRejected`).
* Junk notifications (late handshake retransmits, frames failing the XOR
  checksum) are dropped at the source, so they can't be mistaken for replies.
* The heartbeat (``heartbeat``: the keep-alive read the app's control screen sends
  while the link is up; spec devices.yaml timing.heartbeat) is armed once a session
  is set up and stopped with every teardown and link loss, so exactly one chain of
  ticks runs per session. ``fixed_rate`` ticks every ``HEARTBEAT_INTERVAL``;
  ``after_quiet`` also restarts the wait when each operation that sent a frame
  ends. A tick that finds the link busy (an operation holding or waiting for the
  lock, a :meth:`GoveeConnection.hold_heartbeat`, or the last beat still writing)
  is dropped, not queued. A beat is one write, bounded by
  ``HEARTBEAT_WRITE_TIMEOUT``, whose failure is logged at DEBUG and changes nothing;
  it never touches the idle timer or the warm window, and a disconnect (idle,
  ``disconnect`` or ``close``) or an operation that wants the link cancels a beat
  still writing rather than wait for it (the app writes its beat off its command
  queue), so no command waits for a beat and the link lasts exactly as long as
  without it. Its reply stays in the receive queue for the next operation to drain; with
  ``heartbeat_reply`` every frame notified while no other frame has gone out since the beat (one sent
  after it may have changed what the reply reports) is also handed to that callback, which reads the
  reply out.
  With ``heartbeat=None`` nothing is armed, whatever ``heartbeat_timing`` says.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Final, Literal, TypeVar, get_args, overload

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
    HEARTBEAT_INTERVAL,
    HEARTBEAT_WRITE_TIMEOUT,
    IDLE_DISCONNECT_DELAY,
    NOTIFY_CHAR_UUID,
    OPERATION_TIMEOUT,
    PER_COMMAND_SESSION_GAP,
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
from ..crypto import FrameCipher, checksum_ok
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
from ..wire._spec import BgcInfo as _BgcInfo, Frame as _Frame
from ..wire.build import (
    PRO_MULTI_AC,
    PRO_READ,
    PRO_WRITE,
)
from ..wire.describe import describe_frame
from ..wire.parse import bgc_encrypt_version, parse_write_ack, write_command
from ..wire.reassemble import BurstAssembler
from ..wire.redact import carries_secret, mask_secret, shown_hex
from . import handshake
from .framelog import Direction, FrameLog

_LOGGER = logging.getLogger(__name__)
# Dedicated frame-tier logger: the full-session firehose (every TX/RX frame). Off
# unless enabled (e.g. HA `logger: logs: govee_ble_local.frames: debug`); guarded by
# isEnabledFor so it costs nothing when disabled. This is the capture surface over
# a Bluetooth proxy.
_FRAMES = logging.getLogger("govee_ble_local.frames")

# proTypes a device legitimately sends us (the spec's RX routes: enum route, direction bit 0x100);
# anything else is surfaced as a warning, and queued all the same.
_KNOWN_RX_PRO_TYPES: Final = frozenset(int(r) & 0xFF for r in _Frame.Route if int(r) & 0x100)
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

# When the heartbeat goes out (spec devices.yaml clientRow heartbeat_timing): "fixed_rate" = every
# HEARTBEAT_INTERVAL from the session's start (AbsBle.HeartRunnable); "after_quiet" = HEARTBEAT_INTERVAL after
# the last operation that sent a frame, each one restarting the wait (the ctlchannel HeartHandler.e, which
# restarts it on every send; goodsType 43).
HeartbeatTiming = Literal["fixed_rate", "after_quiet"]


def _err_text(err: BaseException) -> str:
    """An exception's message, or its class name when it has none (e.g. a bare EOFError())."""
    return str(err) or type(err).__name__


class _LinkLost(Exception):
    """Internal: the link dropped while waiting on a reply (queue sentinel)."""


# The device explicitly refused a write (ack status byte != 0). The historical private name
# stays as an alias of the public class, so existing `except _WriteRejected` code keeps working.
_WriteRejected = GoveeBleCommandRejected


@dataclass(frozen=True, slots=True)
class ConnectBudget:
    """One operation's own connect limits, in place of the transport's (``Device.update``'s
    ``connect_attempts`` / ``connect_timeout``). They hold only the connect phase of the operation they
    are passed to (:meth:`GoveeConnection.query`, :meth:`GoveeConnection.send`): every other operation,
    and the reads and writes after the connect, keep the defaults.

    * ``attempts``: bleak-retry-connector's ``max_attempts`` for each establishment the connect makes, i.e.
      how many attempts that time out or fail outright it makes before giving up. bleak-retry-connector
      also tries again after a transient failure (device not found, no free connection slot,
      ``ESP_GATT_CONN_FAIL_ESTABLISH``, ``le-connection-abort-by-local``) whatever ``attempts`` is, and
      gives up only at the 9th (``MAX_TRANSIENT_ERRORS``), so ``timeout`` is what bounds the time. A
      link that never came up is not tried again by the session retry (``SESSION_ATTEMPTS``), which still
      re-establishes a link whose session setup failed. None:
      ``CONNECT_MAX_ATTEMPTS`` per establishment, which the session retry may repeat. So
      ``attempts=CONNECT_MAX_ATTEMPTS`` is not the default: a link that never comes up is established once,
      where None establishes it up to ``SESSION_ATTEMPTS`` times.
    * ``timeout``: seconds each connect of the operation (the link, the BGC read, the handshake, the unlock
      and the session frames, with their retries) may take. Then the link the library holds is torn down,
      every teardown still running whose wait was cut short (the limit's own, or one an earlier
      operation's cancellation cut short) is waited out to its own ``DISCONNECT_TIMEOUT`` bound (and
      abandoned there), and only then is GoveeBleTimeout raised: a connect gives the lock up within
      ``timeout`` + ``DISCONNECT_TIMEOUT``, with every teardown finished or abandoned. Three cases are left
      out. A connect cancelled inside bleak (``establish_connection``) is cleaned up by the BLE backend,
      bounded only by ``OPERATION_TIMEOUT``: BlueZ disconnects it; bleak's CoreBluetooth backend (macOS)
      leaves the pending connection request, so the device may stay connected to the host. When the wait
      for the teardowns would run past the operation's ``OPERATION_TIMEOUT`` (a ``timeout`` above
      ``OPERATION_TIMEOUT`` - ``DISCONNECT_TIMEOUT``, 40 s, or a later connect of the same operation), the
      deadline ends it instead: GoveeBleTimeout ("operation exceeded 45s") is raised at the deadline, and
      the teardown is left to its abandon at its own ``DISCONNECT_TIMEOUT`` bound, as on any deadline. And
      an operation its caller cancels during that wait gives the lock up at once, leaving the teardown to
      the same abandon. The limit is per connect, not per operation: an operation that connects again
      (:meth:`GoveeConnection.send`'s write retry) has the whole limit for each connect. None: no limit of
      its own. The operation's ``OPERATION_TIMEOUT`` bounds the whole operation either way.

    Raises GoveeBleInvalidValue unless each one given is valid: ``attempts`` an integer of at least 1,
    ``timeout`` a finite number above 0."""

    attempts: int | None = None
    timeout: float | None = None

    def __post_init__(self) -> None:
        attempts, timeout = self.attempts, self.timeout
        if attempts is not None and (
            isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1
        ):
            raise GoveeBleInvalidValue(f"connect_attempts must be an integer >= 1, got {_shown(attempts)}")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not _finite(timeout) or timeout <= 0
        ):
            raise GoveeBleInvalidValue(f"connect_timeout must be a finite number > 0, got {_shown(timeout)}")


def _shown(value: object) -> str:
    """``repr``, or a stand-in for an int past ``sys.get_int_max_str_digits()``, whose repr raises
    ValueError."""
    try:
        return repr(value)
    except ValueError:
        return f"<{type(value).__name__} too large to show>"


def _finite(value: float) -> bool:
    """``math.isfinite``, false for an integer too large for a float (which it raises OverflowError on)."""
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


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
        session_ack: bool = True,
        heartbeat: bytes | None = None,
        heartbeat_timing: HeartbeatTiming = "fixed_rate",
        heartbeat_reply: Callable[[bytes], None] | None = None,
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
        # frame can carry the time it is sent. With session_ack each waits for its ack (the plug screens
        # wait for the time sync's); without, each is sent once and not waited for, so a device that never
        # acks one (a light's `33 25 01`) costs no ack timeout on each session.
        self._session_frames = session_frames
        self._session_ack = session_ack
        self._idle_disconnect = idle_disconnect
        self._client: BleakClientWithServiceCache | None = None
        self._session_key: bytes | None = None
        # The session key's cipher, its key setup done once per session (crypto.FrameCipher); set
        # and cleared with _session_key.
        self._session_cipher: FrameCipher | None = None
        self._ready = False  # connected AND session set up
        # Links establish_connection has brought up: tells the session retry whether a failed
        # attempt got a link (_open_session_locked), and numbers the link that is up (link_number).
        self._links_opened = 0
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
        # Every teardown whose wait was cut short (its caller cancelled mid-wait) and still running: its
        # task and the loop time it is abandoned at (_disconnect_locked), oldest first. Each is removed
        # when its task ends.
        self._teardowns: dict[asyncio.Task[None], float] = {}
        self._active_until = 0.0  # loop time; warm-window end after a user command
        # Loop time the last send_once session was released (its link torn down): the next starts
        # PER_COMMAND_SESSION_GAP after it at the earliest. None before the first.
        self._released_at: float | None = None
        # The keep-alive read to send while the link is up (None: none) and when (HeartbeatTiming). Checked
        # here, before any I/O: a 20-byte aa read with its checksum, and a known timing. Without a heartbeat
        # the timing is ignored: no chain is ever armed (_arm_heartbeat), so neither timing sends a beat or
        # restarts a wait (_run), and the link gets what 1.5.0's did.
        if heartbeat is not None and not (
                isinstance(heartbeat, (bytes, bytearray)) and len(heartbeat) == FRAME_LEN
                and heartbeat[0] == PRO_READ and checksum_ok(bytes(heartbeat))):
            raise GoveeBleInvalidValue(
                f"{self.address}: a heartbeat is a {FRAME_LEN}-byte 0x{PRO_READ:02X} read with its checksum, "
                f"got {heartbeat!r}")
        if heartbeat_timing not in get_args(HeartbeatTiming):
            raise GoveeBleInvalidValue(
                f"{self.address}: heartbeat_timing must be one of {get_args(HeartbeatTiming)}, "
                f"got {heartbeat_timing!r}")
        self._heartbeat = None if heartbeat is None else bytes(heartbeat)
        self._heartbeat_timing = heartbeat_timing
        self._heartbeat_timer: asyncio.TimerHandle | None = None   # the next tick, while a chain runs
        self._heartbeat_beat: asyncio.Task[None] | None = None    # the last tick's write
        # Called with each frame notified while no other frame has gone out since the beat's write:
        # ``_tx_count`` is still ``_beat_tx``, the count right after that write (None before the session's
        # first beat). The callback reads the beat's reply out of them through the spec (Device: the aa 01
        # power reply, parse.parse_light_power; None for any other frame). A frame sent after the beat (a
        # power write, a read) may have changed what the reply reports, so that reply goes only to the queue.
        # The callback runs in the notify callback and must not raise. None: the reply only stays queued.
        self._heartbeat_reply = heartbeat_reply
        self._beat_tx: int | None = None
        # Callers holding or waiting for the lock (_locked), and open holds (hold_heartbeat): a tick that
        # finds either is dropped.
        self._lock_wanted = 0
        self._holds = 0
        # Frames written to the link (_raw_write), so an after_quiet operation restarts the wait only when it
        # sent one (HeartHandler.e restarts it on a send, not on a connect() that finds the link up).
        self._tx_count = 0

    @property
    def address(self) -> str:
        return self._ble_device.address

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected and self._ready

    @property
    def link_number(self) -> int:
        """Which link is up: the count of links established so far while one is up (each new link takes the
        next number), 0 with none. No I/O."""
        return self._links_opened if self._client is not None else 0

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
        up in logs and diagnostics downloads. It fails closed:

        - a secret frame's plaintext is masked by its header (``wire.redact``); its
          wire is masked the same way on a plaintext link, where it IS the plaintext,
          and blanked whole on an encrypted link, where its first bytes are AES over
          the block that holds the secret (a check on a guess);
        - a frame with no plaintext (``plain`` None: it arrived before the session
          was ready, or after the teardown cleared it) is masked by its header on a
          plaintext link, and on an encrypted link only its length is kept (its wire
          blanked, logged as ``withheld``), since it can't be checked. That includes
          the e7 handshake frames: with the public PSK, their bytes would give the
          session key."""
        withheld = False
        if plain is not None:
            if carries_secret(plain):
                wire = mask_secret(wire) if wire == plain else b"\xff" * len(wire)
                plain = mask_secret(plain)
        elif self._encryption is Encryption.NONE:
            if carries_secret(wire):
                wire = mask_secret(wire)
        else:
            wire, withheld = b"\xff" * len(wire), True
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
                    "%s %s %s plain= wire=%s enc=%s", self.address, direction,
                    "withheld" if withheld else "cipher", wire.hex(), enc,
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

    def _notify_from(
        self, client: BleakClientWithServiceCache,
    ) -> Callable[[BleakGATTCharacteristic, bytearray], None]:
        """``client``'s notify callback: its frames reach :meth:`_handle_notify` only while it is the
        current link. A link whose teardown was cut short or abandoned can still deliver; its frames
        must not reach the next session's queue (the next handshake would read them)."""
        def on_notify(char: BleakGATTCharacteristic, data: bytearray) -> None:
            if client is not self._client:
                _LOGGER.debug("%s: dropped a notification from a superseded link", self.address)
                return
            self._handle_notify(char, data)

        return on_notify

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
        # A deliberate deviation from the app, which never checks the RX XOR byte: ComposeBleComm.K:190-211
        # drops only a frame that isn't 20 bytes, and the controllers read the body as it is. The library
        # drops a frame that fails the checksum instead of decoding a corrupted frame as state.
        if not checksum_ok(plain):
            _LOGGER.debug("%s: dropped frame with bad checksum: %s", self.address, shown_hex(plain))
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
        if self._heartbeat_reply is not None and self._beat_tx == self._tx_count:
            self._heartbeat_reply(plain)   # nothing sent since the beat: the callback reads its reply out
        self._enqueue(plain)  # plaintext: survives a disconnect nulling the session key

    def _drain(self) -> None:
        while not self._rx.empty():
            self._rx.get_nowait()

    async def _next_rx(self, timeout: float) -> bytes:
        """Next queued frame (raw during the handshake, plaintext afterwards);
        raises TimeoutError, or _LinkLost on disconnect. An already-queued frame
        is returned even with no budget left (wait_for(timeout=0) would not).

        The wait is an ``asyncio.timeout`` in this task, not ``asyncio.wait_for``: on Python 3.11
        wait_for returns a frame that arrives in the same tick as a cancellation of its caller (the
        operation deadline, a connect limit) and swallows the cancellation (gh-86296). On 3.11.0 to
        3.11.2, whose ``asyncio.timeout`` has gh-102780 (fixed in 3.11.3), a wait that runs out in a task
        carrying a cancellation it suppressed without ``uncancel()`` raises CancelledError, not
        TimeoutError."""
        try:
            item = self._rx.get_nowait()
        except asyncio.QueueEmpty:
            if timeout <= 0:
                raise TimeoutError from None
            async with asyncio.timeout(timeout):
                item = await self._rx.get()
        if item is None:
            raise _LinkLost
        return item

    # -- lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        """Connect (if needed) and run the handshake/unlock, under the operation deadline.

        Raises only :class:`GoveeBleError` subclasses."""
        await self._run(self._ensure_connected_locked, extend_warm=False)

    async def disconnect(self, *, keep: Callable[[], bool] | None = None) -> bool:
        """Drop the session and link now (bounded by ``DISCONNECT_TIMEOUT``) and end any warm window a write
        opened (``ACTIVE_IDLE_DELAY``, :meth:`_disconnect_locked`), so the next session starts at the base
        idle delay (a write on it opens its own window). It waits for the running operation and every one
        queued before it (the lock is FIFO); one queued after it reconnects. ``keep`` is asked once the lock
        is held, after all of those: True keeps the link and drops nothing (``Device.release``: the link is a
        later write's). Returns whether a link was up and was dropped: False when there was none (a no-op, as
        after :meth:`close`) or ``keep`` kept it. Never raises a GoveeBleError: the teardown's errors are
        logged at DEBUG (:meth:`_disconnect_done`)."""
        self._cancel_beat()   # a beat still writing doesn't hold the teardown up
        async with self._locked():
            if keep is not None and keep():
                return False
            self._cancel_idle_timer()
            dropped = self._client is not None
            await self._disconnect_locked()
            return dropped

    async def close(self) -> None:
        """Disconnect and refuse further operations (owner is being torn down). The heartbeat chain stops at
        once, before the wait for the lock, so a close cancelled while it waits leaves no tick armed."""
        self._closed = True
        self._stop_heartbeat()
        await self.disconnect()

    async def _ensure_connected_locked(self, budget: ConnectBudget | None = None) -> None:
        """The connect phase of an operation: connect and set the session up, unless it is up.
        ``budget`` is the operation's own connect limits (:class:`ConnectBudget`); None keeps the
        transport's."""
        if self._closed:
            raise GoveeBleConnectionError(f"{self.address}: connection closed")
        if self.is_connected:
            return
        if self._bgc_checked and self._encryption is Encryption.AES_GCM:
            # Settled (the BGC result is cached): don't open a link just to refuse again.
            raise GoveeBleNotSupported(
                f"{self.address}: AES-GCM (V2) handshake not yet implemented"
            )
        if budget is None or budget.timeout is None:
            await self._open_session_locked(budget)
            return
        try:
            async with asyncio.timeout(budget.timeout):
                await self._open_session_locked(budget)
        except TimeoutError as err:
            # Only this limit raises a TimeoutError here: the BLE stack's own arrive as GoveeBleTimeout
            # (_connect_locked), and the operation deadline as a cancellation. _connect_locked has torn
            # down the link it held on the way out, as on any cancellation; a connect cancelled inside
            # establish_connection was cleaned up by bleak's backend (see ConnectBudget). A failed
            # attempt's teardown that the limit cut short, and any an earlier operation's cancellation
            # cut short, is waited out to its bound first, so the lock is not handed on while an old
            # link's disconnect still runs (unless this wait is cancelled in turn: see ConnectBudget).
            await self._finish_teardown()
            raise GoveeBleTimeout(
                f"{self.address}: connect exceeded {budget.timeout:g}s"
            ) from err

    async def _open_session_locked(self, budget: ConnectBudget | None) -> None:
        """Up to ``SESSION_ATTEMPTS`` connects (:meth:`_connect_locked`), each an establishment with
        bleak-retry-connector's ``max_attempts`` of ``CONNECT_MAX_ATTEMPTS``, or the budget's ``attempts``
        (attempts that time out or fail outright; it tries again after a transient failure regardless,
        giving up at the 9th). With the budget's, a link that never came up is not tried again:
        establish_connection has made the operation's attempts."""
        attempts = None if budget is None else budget.attempts
        last_err: GoveeBleError | None = None
        for attempt in range(1, SESSION_ATTEMPTS + 1):
            links = self._links_opened
            try:
                await self._connect_locked(CONNECT_MAX_ATTEMPTS if attempts is None else attempts)
                return
            except (GoveeBleNotSupported, GoveeBleAuthError):
                raise  # permanent: a retry can only repeat the same refusal
            except GoveeBleError as err:
                if attempts is not None and self._links_opened == links:
                    raise  # no link came up: the budget's attempts are spent
                last_err = err
                _LOGGER.debug(
                    "%s: session attempt %d/%d failed: %s",
                    self.address, attempt, SESSION_ATTEMPTS, err,
                )
        if last_err is None:  # pragma: no cover - SESSION_ATTEMPTS >= 1
            raise AssertionError("unreachable: no session attempt ran")
        raise last_err

    async def _connect_locked(self, max_attempts: int) -> None:
        try:
            if self._client is None or not self._client.is_connected:
                self._client = await establish_connection(
                    BleakClientWithServiceCache,
                    self._ble_device,
                    self._ble_device.address,
                    disconnected_callback=self._on_disconnect,
                    max_attempts=max_attempts,
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
                self._links_opened += 1
                self._drain()
                await self._client.start_notify(NOTIFY_CHAR_UUID, self._notify_from(self._client))
            await self._prepare_session()
            self._arm_heartbeat()   # the session is set up: the first beat one interval from now
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
        if bgc_version == int(_BgcInfo.EncryptVersion.aes_gcm):
            self._encryption = Encryption.AES_GCM
        elif bgc_version == int(_BgcInfo.EncryptVersion.aes_rc4_psk):
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
                    if self._session_ack:
                        await self._send_write_verified(frame)
                    else:
                        await self._send_frame(frame)
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
        self._beat_tx = None   # the next session's replies answer its own beats

    def _on_disconnect(self, client: BleakClientWithServiceCache) -> None:
        if client is not self._client:
            return  # a superseded client finishing its teardown late
        _LOGGER.debug("%s: disconnected", self.address)
        self._stop_heartbeat()
        self._reset_session()
        # The warm window ends with the link, as at every teardown (_disconnect_locked): a read that
        # reconnects after a device-side drop gets the base idle delay, not what was left of a write's
        # window (a write running now opens it again on the link it ends on, _run).
        self._active_until = 0.0
        # Drop the stale client so the next operation re-establishes from scratch.
        self._client = None
        self._enqueue(None)  # wake any waiter now instead of at its timeout

    async def _disconnect_locked(self) -> None:
        self._stop_heartbeat()
        self._reset_session()
        # The warm window ends with the link, whoever tears it down (disconnect(), the idle timer, a failed
        # operation, a connect attempt that failed): the next session starts at the base idle delay, and a
        # write that ends on a fresh link opens the window again there (_run).
        self._active_until = 0.0
        client, self._client = self._client, None
        if client is None:
            return
        # Run the teardown as its own task: bounded by DISCONNECT_TIMEOUT so a wedged
        # proxy can't hold the device lock forever, yet NOT cancelled if our caller is
        # cancelled (the client is already forgotten, so nothing else would close it).
        loop = asyncio.get_running_loop()
        abandon_at = loop.time() + DISCONNECT_TIMEOUT
        task = loop.create_task(client.disconnect())
        self._background.add(task)
        task.add_done_callback(self._disconnect_done)
        try:
            await asyncio.wait({task}, timeout=DISCONNECT_TIMEOUT)
        except asyncio.CancelledError:
            # Our caller was cancelled mid-wait (the operation deadline, a connect limit, the
            # caller's own): the teardown runs on, and is abandoned at the same bound. A connect
            # limit waits it out (_finish_teardown) before it gives the lock up. A teardown that
            # ended as the cancellation landed has run _disconnect_done already: not recorded.
            if not task.done():
                self._teardowns[task] = abandon_at
                loop.call_at(abandon_at, self._abandon_disconnect, task)
            raise
        self._abandon_disconnect(task)

    async def _finish_teardown(self) -> None:
        """Wait out every teardown whose wait was cut short (``_teardowns``: this connect's, and one an
        earlier operation's cancellation cut short) until it ends or reaches its ``DISCONNECT_TIMEOUT``
        bound, and abandon it there (:meth:`_abandon_disconnect`). Oldest first, so the earliest bound
        comes first and the whole wait ends by the latest one. Cancelled mid-wait (the operation
        deadline, the caller), it gives up at once and leaves each teardown to the abandon already
        scheduled at its bound."""
        loop = asyncio.get_running_loop()
        for task, abandon_at in list(self._teardowns.items()):
            remaining = abandon_at - loop.time()
            if remaining > 0:
                await asyncio.wait({task}, timeout=remaining)
            self._abandon_disconnect(task)

    def _abandon_disconnect(self, task: asyncio.Task[None]) -> None:
        """Cancel a teardown still running at its ``DISCONNECT_TIMEOUT`` (a wedged proxy). Once only: a
        teardown both waited out and scheduled for abandon (:meth:`_finish_teardown`) is cancelled once."""
        if task.done() or task.cancelling():
            return
        _LOGGER.debug(
            "%s: disconnect did not finish within %.0fs; abandoning it",
            self.address, DISCONNECT_TIMEOUT,
        )
        task.cancel()

    def _disconnect_done(self, task: asyncio.Task[None]) -> None:
        self._background.discard(task)
        self._teardowns.pop(task, None)
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
        client = self._require_client()
        self._tx_count += 1
        await client.write_gatt_char(WRITE_CHAR_UUID, frame, response=False)

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
        if self._lock.locked():
            # A beat still writing holds it: cancel that write rather than queue behind it (the app writes its
            # beat off its command queue, so a command never waits for one). Held by an operation instead, a
            # beat not yet started drops itself anyway (_link_busy). Only when held: a beat created in this
            # loop pass, beside a connect() that finds the link up and returns at once, still goes out.
            self._cancel_beat()
        async with self._locked():
            sent = self._tx_count
            self._cancel_idle_timer()
            warm_until = asyncio.get_running_loop().time() + ACTIVE_IDLE_DELAY
            if extend_warm:
                self._active_until = warm_until
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
                if extend_warm and self._client is not None:
                    # A teardown or a device-side drop mid-operation ended the window (_disconnect_locked,
                    # _on_disconnect); a write that ends with a link up (its retry on a fresh link,
                    # _write_locked) went out on this one, which keeps it.
                    self._active_until = warm_until
                self._schedule_idle_timer()
                # Only a running chain restarts, and none runs without a heartbeat (whatever the timing).
                if (self._heartbeat_timing == "after_quiet" and self._heartbeat_timer is not None
                        and self._tx_count != sent):
                    self._arm_heartbeat()   # it wrote a frame: restart the wait (HeartHandler.e)

    @overload
    async def send(
        self, frame_plaintext: bytes, *, expect_ack: Literal[True] = ...,
        connect_budget: ConnectBudget | None = ...,
    ) -> bytes: ...

    @overload
    async def send(
        self, frame_plaintext: bytes, *, expect_ack: Literal[False],
        connect_budget: ConnectBudget | None = ...,
    ) -> None: ...

    @overload
    async def send(
        self, frame_plaintext: bytes, *, expect_ack: bool, connect_budget: ConnectBudget | None = ...,
    ) -> bytes | None: ...

    async def send(
        self, frame_plaintext: bytes, *, expect_ack: bool = True, connect_budget: ConnectBudget | None = None,
    ) -> bytes | None:
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
        raises GoveeBleInvalidValue before any I/O. ``connect_budget`` holds this operation's
        connects (the retry's reconnect too) to its limits (:class:`ConnectBudget`)."""
        self._check_frame(frame_plaintext)
        is_write = frame_plaintext[0] == PRO_WRITE
        if expect_ack:
            self._check_write_frame(frame_plaintext)

        async def op() -> bytes | None:
            if expect_ack:
                return await self._write_locked(frame_plaintext, connect_budget)
            await self._ensure_connected_locked(connect_budget)
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

    def _check_write_frame(self, frame: bytes) -> int:
        """Validate a frame for an ack-verified send, 2+ bytes AND a write (proType 0x33) with a command, and
        return the command, both read by the frame reader (:func:`..wire.parse.write_command`)."""
        self._check_frame(frame)
        try:
            return write_command(frame)
        except ValueError as err:
            raise GoveeBleInvalidValue(
                f"{self.address}: only a write frame (proType 0x{PRO_WRITE:02X}) has an ack to "
                f"wait for; {err} (use query() for reads)"
            ) from None

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

    async def _write_locked(self, frame_plaintext: bytes, budget: ConnectBudget | None = None) -> bytes:
        """Ack-verified write with one reconnect-and-retry. Only failures AFTER the
        link was up trigger the retry (a link that stops acking is usually
        half-dead while bleak still reports it connected); a connect failure is
        not retried here - establish_connection already retried it. Both connects
        are held to ``budget`` (:class:`ConnectBudget`)."""
        await self._ensure_connected_locked(budget)
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
        await self._ensure_connected_locked(budget)
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

    async def send_once(self, frame_plaintext: bytes) -> None:
        """Send one write on a session of its own, as the app's device-list card does (a devices.yaml
        ``session: per_command`` row; spec devices.yaml timing.per_command): the card builds a client for each
        command, writes its one frame and closes it (``CardCommManagerProxy.u`` -> ``triggerBleCmd$2``).

        One operation under the lock and the deadline: it waits until ``PER_COMMAND_SESSION_GAP`` has passed
        since the last such session's release (``CardCommManagerProxy.s``, which ``triggerBleCmd$2`` waits
        out), drops any link still up so the frame goes out on a fresh session, connects and sets the session
        up as every connect does (the BGC read, the handshake where the cipher needs it, any unlock and
        session frames), writes the frame once, then tears the link down, failed or not, and stamps the
        release. No retry, no second copy, and nothing armed afterwards: no idle timer (no link is left) and
        no heartbeat (the teardown stops any).

        The card counts the write's completion as success and reads no reply (its ``GMessageMulti`` registers
        no reply timeout, ``ClientUnionMgr.excuBusinessSend``). Before closing, this reads the device's reply
        for up to ``WRITE_ACK_TIMEOUT`` and raises :class:`GoveeBleCommandRejected` on a refusal (a non-zero
        status), so a refused write is not reported as done; a good reply ends the wait at once, and no reply,
        or a link lost while it waits, is a success, as on the card. Every frame must be a write (proType
        0x33), else GoveeBleInvalidValue before any I/O."""
        command_type = self._check_write_frame(frame_plaintext)

        async def op() -> None:
            loop = asyncio.get_running_loop()
            if self._released_at is not None:
                wait = self._released_at + PER_COMMAND_SESSION_GAP - loop.time()
                if wait > 0:
                    await asyncio.sleep(wait)
            try:
                if self._client is not None:
                    await self._disconnect_locked()   # a fresh session for each command (none is held)
                await self._ensure_connected_locked()
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug("%s: TX %s (its own session)", self.address,
                                  describe_frame(frame_plaintext, "tx")[0])
                self._drain()
                await self._send_frame(frame_plaintext)
                try:
                    reply = await self._await_reply(PRO_WRITE, command_type, WRITE_ACK_TIMEOUT)
                except _LinkLost:
                    reply = None   # the write went out: the card's success
                if reply is not None:
                    self._check_ack(reply, command_type)
            finally:
                try:
                    await self._disconnect_locked()
                finally:
                    self._released_at = loop.time()

        await self._run(op, extend_warm=False)

    def _check_ack(self, ack: bytes, command_type: int) -> None:
        """Raise :class:`GoveeBleCommandRejected` when the write ack ``ack`` (``write_ack.result``) refuses
        the write (a status other than 0)."""
        status = parse_write_ack(ack)
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

    async def _send_write_verified(self, frame_plaintext: bytes) -> bytes:
        command_type = write_command(frame_plaintext)   # checked before the operation (_check_write_frame)
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug("%s: TX %s", self.address, describe_frame(frame_plaintext, "tx")[0])
        for attempt in range(WRITE_ATTEMPTS):
            self._drain()
            await self._send_frame(frame_plaintext)
            ack = await self._await_reply(PRO_WRITE, command_type, WRITE_ACK_TIMEOUT)
            if ack is not None:
                self._check_ack(ack, command_type)
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
        connect_budget: ConnectBudget | None = None,
        burst_command: int = BurstAssembler.command,
        burst_sub: int = BurstAssembler.sub,
    ) -> list[bytes]:
        """Send a query trigger and collect the resulting NOTIFY burst.

        Returns the decrypted frames received until a terminal frame (an
        ``opcode`` frame whose byte 1 == ``terminal``) arrives, the device goes
        quiet for ``idle_gap`` after its last ``opcode`` frame (a lost terminator
        must not cost the whole timeout), or ``timeout`` elapses. Frames already
        queued when the budget runs out are still collected. ``idle_gap`` is armed
        by ANY ``opcode`` frame, so a single-reply read (terminal = the expected
        command) should pass ``idle_gap=None``: there a stray ``opcode`` frame (a late
        reply to an earlier read) must not cut the wait short.

        For ``opcode`` 0xAC the burst is assembled as the app assembles it
        (:class:`..wire.reassemble.BurstAssembler`) and ``terminal`` is not used: the
        read ends when the burst is complete (a one-packet reply, or the last packet
        once every packet before it is in) or failed (a first chunk whose lastLen
        doesn't fit), and only a chunk the app takes arms ``idle_gap``, so a stray
        early 0xFF or a late chunk of an earlier burst doesn't end it. ``burst_command``
        and ``burst_sub`` are the reader's, whose first chunk the burst starts with: the
        status read's by default, the special-info read's for ``ac a1``.

        ``connect_budget`` holds this operation's connect, not its reads, to its limits
        (:class:`ConnectBudget`)."""

        async def op() -> list[bytes]:
            await self._ensure_connected_locked(connect_budget)
            self._drain()
            await self._send_frame(frame_plaintext)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            frames: list[bytes] = []
            burst = (BurstAssembler(command=burst_command, sub=burst_sub) if opcode == PRO_MULTI_AC
                     else None)
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
                    if burst is None:
                        seen_opcode = True
                        if dec[1] == terminal:
                            break
                    else:
                        seen_opcode |= burst.feed(dec)
                        if burst.status != "waiting":
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
        if generation == self._idle_generation:
            self._cancel_beat()   # the idle delay is up: a beat still writing doesn't extend the link
        async with self._locked():
            if generation != self._idle_generation:
                return  # an operation ran since the timer fired
            self._idle_timer = None
            await self._disconnect_locked()

    # -- heartbeat ----------------------------------------------------------

    @contextlib.asynccontextmanager
    async def _locked(self) -> AsyncIterator[None]:
        """The connection lock, counted while it is held or waited for (``_lock_wanted``), so a heartbeat tick
        between a release and the next waiter's wake-up (when ``asyncio.Lock.locked()`` is False) still finds
        the link busy and is dropped, not queued behind the waiter. Every operation, ``disconnect`` and the
        idle disconnect take it; only a beat takes the lock bare (:meth:`_beat`)."""
        self._lock_wanted += 1
        try:
            async with self._lock:
                yield
        finally:
            self._lock_wanted -= 1

    @contextlib.contextmanager
    def hold_heartbeat(self) -> Iterator[None]:
        """Send no heartbeat while the block runs: for several operations that belong together (a poll's
        reads, a scene's upload and its activation), between which the app sends none (it skips its heartbeat
        while a compose or multi-packet send is in flight, ``AbsBle.I``, and while its connect read runs). It
        takes no lock and nests: other operations still run, and a tick due meanwhile is dropped. No I/O."""
        self._holds += 1
        try:
            yield
        finally:
            self._holds -= 1

    def _link_busy(self) -> bool:
        """An operation holds or waits for the lock, a :meth:`hold_heartbeat` is open, or the last beat (other
        than the caller's own) is still writing: a tick then sends nothing."""
        beat = self._heartbeat_beat
        writing = beat is not None and not beat.done() and beat is not asyncio.current_task()
        return self._lock_wanted > 0 or self._lock.locked() or self._holds > 0 or writing

    def _arm_heartbeat(self) -> None:
        """(Re)start the chain on the current session: the next tick ``HEARTBEAT_INTERVAL`` from now,
        replacing any armed one (so one chain runs). Nothing without a heartbeat, once closed, or with no
        link. Called once the session is set up (the app sends no beat before its connect-time info read is
        over: ShareVM.R -> updateSkipHeart, Ble4NewDetail.Q), and on an after_quiet connection when an
        operation that sent a frame ends."""
        self._stop_heartbeat()
        if self._heartbeat is None or self._closed or self._client is None:
            return
        self._heartbeat_timer = asyncio.get_running_loop().call_later(
            HEARTBEAT_INTERVAL, self._heartbeat_fired, self._client)

    def _stop_heartbeat(self) -> None:
        """End the chain (every teardown and link loss); a beat still writing ends at its bound, or sooner
        when a disconnect or the next operation cancels it (:meth:`_cancel_beat`)."""
        if self._heartbeat_timer is not None:
            self._heartbeat_timer.cancel()
            self._heartbeat_timer = None

    def _cancel_beat(self) -> None:
        """Cancel a beat still writing, ahead of a teardown (``disconnect``, ``close`` and the idle
        disconnect) or an operation (:meth:`_run`), which would otherwise wait for the lock until the write
        ends or reaches ``HEARTBEAT_WRITE_TIMEOUT``: the link then closes when the idle policy says, and a
        command goes out when it is sent, as without a heartbeat. The beat gives the lock up as it unwinds."""
        beat = self._heartbeat_beat
        if beat is not None and not beat.done():
            beat.cancel()

    def _heartbeat_fired(self, client: BleakClientWithServiceCache) -> None:
        """One tick: re-arm at the interval (a fixed rate), then start the beat unless the link is busy, where
        the tick is dropped, not queued (AbsHeartRunnable.a; HeartRunnable.c reposts either way). The chain
        ends on a closed connection or a link that is gone or superseded."""
        self._stop_heartbeat()
        if self._closed or client is not self._client or not self.is_connected:
            return
        loop = asyncio.get_running_loop()
        self._heartbeat_timer = loop.call_later(HEARTBEAT_INTERVAL, self._heartbeat_fired, client)
        if self._link_busy():
            return
        task = loop.create_task(self._beat(client, self._tx_count))
        self._heartbeat_beat = task
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _beat(self, client: BleakClientWithServiceCache, sent: int) -> None:
        """Write one heartbeat to ``client`` under the lock, with no ack or reply wait (the app's heart is a
        write outside its command queue: AbsBleComm.z -> ControllerComm.m), bounded by
        ``HEARTBEAT_WRITE_TIMEOUT`` and cancelled by a disconnect or an operation that wants the link
        (:meth:`_cancel_beat`). ``sent`` is the frame count at the tick: on after_quiet, a tick whose beat
        has not started when an operation sends a frame (one that ran and ended in the tick's loop pass)
        drops itself, since that send restarted the wait (HeartHandler.e). Its reply stays in the receive
        queue, which the next operation drains, and goes to ``heartbeat_reply`` too while nothing else has
        been sent since (``_beat_tx``). A failure is logged at DEBUG and changes nothing: no
        teardown and no count (the app counts none; EventHeart is not retried), so a dead link shows at the
        next operation's write retry. Never touches the idle timer or the warm window."""
        frame = self._heartbeat
        if frame is None or self._link_busy():
            return   # an operation started since the tick
        if self._heartbeat_timing == "after_quiet" and self._tx_count != sent:
            return   # an operation sent a frame since the tick (in the same loop pass): the wait restarted
        async with self._lock:   # free, and no one waits for it: taken without a yield
            if client is not self._client or not self.is_connected:
                return
            try:
                async with asyncio.timeout(HEARTBEAT_WRITE_TIMEOUT):
                    await self._send_frame(frame)
                self._beat_tx = self._tx_count   # its replies, until another frame goes out (heartbeat_reply)
            except (GoveeBleError, *_TRANSPORT_ERRORS, TimeoutError) as err:
                _LOGGER.debug("%s: heartbeat not sent: %s", self.address, _err_text(err))
            except Exception as err:   # a background beat never raises
                _LOGGER.debug("%s: heartbeat failed: %r", self.address, err, exc_info=err)
