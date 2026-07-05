"""The encrypted BLE session: connect, handshake, send/receive.

Owns the bleak connection, runs the e7 handshake to negotiate the session
key, then encrypts every outgoing frame and decrypts every notification with
it. Raw ciphertext is queued from the notify callback and decrypted at the
point of use (PSK during the handshake, session key afterwards).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from ..const import (
    COMMAND_ACK_TIMEOUT,
    CONNECT_MAX_ATTEMPTS,
    FRAME_LEN,
    HANDSHAKE_TIMEOUT,
    IDLE_DISCONNECT_DELAY,
    BGC_INFO_CHAR_UUID,
    NOTIFY_CHAR_UUID,
    PSK,
    WRITE_CHAR_UUID,
)
from ..crypto import decrypt, encrypt
from ..exceptions import (
    GoveeBleConnectionError,
    GoveeBleHandshakeError,
    GoveeBleNotSupported,
    GoveeBleTimeout,
)
from ..models import Encryption
from . import handshake

_LOGGER = logging.getLogger(__name__)

NotifyCallback = Callable[[bytes], None]


class GoveeConnection:
    """One on-demand BLE session with a Govee device.

    The command channel is secured per `encryption`: AES_RC4_PSK negotiates a
    session key and encrypts every frame with it; HANDSHAKE_ONLY performs the
    handshake but sends plaintext; NONE skips the handshake entirely.
    """

    def __init__(
        self,
        ble_device: BLEDevice,
        *,
        encryption: Encryption = Encryption.AES_RC4_PSK,
        on_notify: NotifyCallback | None = None,
        unlock_frames: Callable[[], list[bytes]] | None = None,
        idle_disconnect: float = IDLE_DISCONNECT_DELAY,
    ) -> None:
        self._ble_device = ble_device
        self._encryption = encryption
        self._on_notify = on_notify
        # Frames to send immediately after every handshake, before any user
        # command — e.g. the plug family's `33 b2` secret-key check. A provider
        # (not a static list) so a device can supply/refresh it lazily.
        self._unlock_frames = unlock_frames
        self._idle_disconnect = idle_disconnect
        self._client: BleakClientWithServiceCache | None = None
        self._session_key: bytes | None = None
        self._ready = False  # connected AND handshake done (or not needed)
        self._rx: asyncio.Queue[bytes] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._idle_timer: asyncio.TimerHandle | None = None

    @property
    def address(self) -> str:
        return self._ble_device.address

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected and self._ready

    def _decrypt_rx(self, raw: bytes) -> bytes:
        """Decrypt an application notification per the encryption mode."""
        if self._encryption is Encryption.AES_RC4_PSK and self._session_key is not None:
            return decrypt(raw, self._session_key)
        return raw  # HANDSHAKE_ONLY / NONE: plaintext on the wire

    def update_ble_device(self, ble_device: BLEDevice) -> None:
        self._ble_device = ble_device

    # -- notify pump --------------------------------------------------------

    def _handle_notify(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        raw = bytes(data)
        if len(raw) != FRAME_LEN:
            _LOGGER.debug("%s: ignoring %d-byte notification", self.address, len(raw))
            return
        self._rx.put_nowait(raw)
        # Once ready, application notifications are decodable; surface them.
        if self._ready and self._on_notify is not None:
            try:
                self._on_notify(self._decrypt_rx(raw))
            except Exception:  # pragma: no cover - callback must never break the pump
                _LOGGER.exception("%s: on_notify callback failed", self.address)

    def _drain(self) -> None:
        while not self._rx.empty():
            self._rx.get_nowait()

    # -- lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        """Connect (if needed) and run the handshake."""
        async with self._lock:
            self._cancel_idle_timer()
            if self.is_connected:
                self._schedule_idle_timer()
                return
            await self._connect_locked()
            self._schedule_idle_timer()

    async def _connect_locked(self) -> None:
        if self._client is None or not self._client.is_connected:
            try:
                self._client = await establish_connection(
                    BleakClientWithServiceCache,
                    self._ble_device,
                    self._ble_device.address,
                    disconnected_callback=self._on_disconnect,
                    max_attempts=CONNECT_MAX_ATTEMPTS,
                )
                await self._client.start_notify(NOTIFY_CHAR_UUID, self._handle_notify)
            except BleakError as err:
                raise GoveeBleConnectionError(f"connect to {self.address} failed: {err}") from err
        await self._prepare_session()

    async def _read_bgc_encrypt_version(self) -> int | None:
        """Read the BGC-info characteristic and return the device's
        encryptVersion (0/1/2), or None if the characteristic is absent.
        Port of BgcInfoReader.a()/d(): parse data[0] (format); for format 1 or
        2 the version is data[1]."""
        assert self._client is not None
        char = self._client.services.get_characteristic(BGC_INFO_CHAR_UUID)
        if char is None:
            return None
        try:
            data = bytes(await self._client.read_gatt_char(char))
        except BleakError as err:
            _LOGGER.debug("%s: BGC read failed: %s", self.address, err)
            return None
        if len(data) < 2 or data[0] not in (1, 2):
            return 0
        return data[1]

    async def _prepare_session(self) -> None:
        """Discover the encryption mode (BGC read refines the advertisement's
        encrypt flag), run the handshake if needed, send post-handshake unlock
        frames, and mark the session ready."""
        self._session_key = None
        self._ready = False
        self._drain()
        assert self._client is not None
        # BGC-info read is authoritative for the version (1=RC4, 2=GCM). If the
        # characteristic is absent we keep the advertisement-derived mode.
        bgc_version = await self._read_bgc_encrypt_version()
        if bgc_version == 2:
            self._encryption = Encryption.AES_GCM
        elif bgc_version == 1:
            self._encryption = Encryption.AES_RC4_PSK
        elif bgc_version == 0 and self._encryption is not Encryption.NONE:
            # BGC explicitly reports no encryption but keep any advertisement
            # signal (isEncryptionSupported = checkSupport(adv) OR bgc.g()).
            pass
        try:
            if self._encryption is Encryption.AES_GCM:
                raise GoveeBleNotSupported(
                    f"{self.address}: AES-GCM (V2) handshake not yet implemented"
                )
            if self._encryption is Encryption.AES_RC4_PSK:
                await self._handshake()
            self._ready = True
            self._drain()
            _LOGGER.debug("%s: session ready (%s)", self.address, self._encryption.value)
            # Post-handshake unlock (e.g. secret-key check) — must run before
            # any user command, while we still hold the lock.
            if self._unlock_frames is not None:
                for frame in self._unlock_frames():
                    await self._send_raw_locked(frame, expect_ack=True)
        except asyncio.TimeoutError as err:
            self._ready = False
            raise GoveeBleTimeout(f"{self.address}: handshake timed out") from err
        except BleakError as err:
            self._ready = False
            raise GoveeBleHandshakeError(f"{self.address}: handshake failed: {err}") from err

    async def _handshake(self) -> None:
        await self._raw_write(handshake.build_step1(PSK))
        reply1 = await asyncio.wait_for(self._rx.get(), timeout=HANDSHAKE_TIMEOUT)
        key = handshake.parse_session_key(reply1, PSK)
        if key is None:
            raise GoveeBleHandshakeError(f"{self.address}: unexpected handshake step-1 reply")
        await self._raw_write(handshake.build_step2(PSK))
        try:
            await asyncio.wait_for(self._rx.get(), timeout=COMMAND_ACK_TIMEOUT)
        except asyncio.TimeoutError:
            _LOGGER.debug("%s: no step-2 ack (usually harmless)", self.address)
        # AES_RC4_PSK uses the key to frame commands; HANDSHAKE_ONLY performs the
        # ritual but sends plaintext, so the key is parsed and then unused.
        self._session_key = key

    def _on_disconnect(self, _client: BleakClientWithServiceCache) -> None:
        _LOGGER.debug("%s: disconnected", self.address)
        self._session_key = None
        self._ready = False

    async def disconnect(self) -> None:
        async with self._lock:
            self._cancel_idle_timer()
            await self._disconnect_locked()

    async def _disconnect_locked(self) -> None:
        self._session_key = None
        self._ready = False
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except BleakError as err:
                _LOGGER.debug("%s: disconnect error: %s", self.address, err)

    # -- I/O ----------------------------------------------------------------

    async def _raw_write(self, frame: bytes) -> None:
        assert self._client is not None
        await self._client.write_gatt_char(WRITE_CHAR_UUID, frame, response=False)

    async def _send_raw_locked(self, frame_plaintext: bytes, *, expect_ack: bool) -> bytes | None:
        """Frame (encrypting per the mode) + write, then await a best-effort ack.
        Caller holds the lock and guarantees the session is ready."""
        self._drain()
        if self._encryption is Encryption.AES_RC4_PSK:
            assert self._session_key is not None
            wire = encrypt(frame_plaintext, self._session_key)
        else:
            wire = frame_plaintext
        await self._raw_write(wire)
        if not expect_ack:
            return None
        try:
            raw = await asyncio.wait_for(self._rx.get(), timeout=COMMAND_ACK_TIMEOUT)
            return self._decrypt_rx(raw)
        except asyncio.TimeoutError:
            _LOGGER.debug("%s: no ack for %s", self.address, frame_plaintext[:2].hex())
            return None

    async def send(self, frame_plaintext: bytes, *, expect_ack: bool = True) -> bytes | None:
        """Connect+handshake if needed, then encrypt and send one 20-byte
        plaintext frame; return the decrypted next notification (best-effort ack)."""
        async with self._lock:
            self._cancel_idle_timer()
            if not self.is_connected:
                await self._connect_locked()
            ack = await self._send_raw_locked(frame_plaintext, expect_ack=expect_ack)
            self._schedule_idle_timer()
            return ack

    # -- idle disconnect ----------------------------------------------------

    def _cancel_idle_timer(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _schedule_idle_timer(self) -> None:
        self._cancel_idle_timer()
        if self._idle_disconnect > 0:
            loop = asyncio.get_running_loop()
            self._idle_timer = loop.call_later(
                self._idle_disconnect, lambda: asyncio.ensure_future(self.disconnect())
            )


def now_ts() -> int:
    """Current unix timestamp (for sync_time frames)."""
    return int(time.time())
