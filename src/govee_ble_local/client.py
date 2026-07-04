"""On-demand encrypted BLE session with a Govee light, plus high-level commands.

This module owns the Bluetooth connection lifecycle (via bleak /
bleak-retry-connector) and orchestration (handshake, idle-disconnect, status
retries). All byte-level protocol logic lives in ``govee_ble_local.messages``
(the single encode+decode codec) and ``govee_ble_local.protocol`` (crypto/
framing/parsers).

Every incoming notification is pushed through the codec by ``_dispatch``:
frames we understand are queued for the awaiting request logic; frames we don't
(stubs like the clock/wifi/`0xEE`/`0xA4` opcodes, or genuinely unknown ones)
are logged and dropped by ``messages.dispatch_incoming`` — the device can't
make us act on something we don't understand.
"""
from __future__ import annotations

import asyncio
import logging

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from . import messages, protocol as p
from .const import (
    CONNECT_MAX_ATTEMPTS,
    DISCONNECT_DELAY,
    METADATA_FIELD_TIMEOUT,
    NOTIFY_CHAR_UUID,
    PSK,
    STATUS_CHUNK_TIMEOUT,
    WRITE_CHAR_UUID,
)
from .models import GoveeBleStatus

_LOGGER = logging.getLogger(__name__)

FRAME_LEN = messages.FRAME_LEN


class GoveeBleClient:
    """Maintains an on-demand encrypted BLE session with the light."""

    def __init__(self, ble_device: BLEDevice) -> None:
        self._ble_device = ble_device
        self._client: BleakClientWithServiceCache | None = None
        self._session_key: bytes | None = None
        # Key used to decrypt incoming notifications: the PSK during the
        # handshake (when there's no session key yet, and the TX2 ack is still
        # PSK-framed), the session key afterwards. Swapped only once the
        # handshake fully completes, so both handshake replies decrypt with PSK.
        self._rx_key: bytes = PSK
        self._lock = asyncio.Lock()
        self._notify_queue: asyncio.Queue[messages.DecodedMessage] = asyncio.Queue()
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._expire_task: asyncio.Task[None] | None = None

    @property
    def address(self) -> str:
        return self._ble_device.address

    def update_ble_device(self, ble_device: BLEDevice) -> None:
        """Swap in a fresh BLEDevice (e.g. from a new advertisement)."""
        self._ble_device = ble_device

    # -- connection lifecycle ------------------------------------------------

    def _dispatch(self, raw: bytes) -> None:
        """Handle one incoming notification: decrypt, decode, and either queue
        it (understood) or log-and-drop it (stub / unknown / redacted)."""
        if len(raw) != FRAME_LEN:
            _LOGGER.debug("Ignoring %d-byte notification from %s", len(raw), self._ble_device.address)
            return
        plaintext = p.decrypt_packet(self._rx_key, raw)
        msg = messages.dispatch_incoming(plaintext, "NOTIFY")
        if msg.understood:
            self._notify_queue.put_nowait(msg)
        # Not understood -> dispatch_incoming already logged it; drop.

    def _on_notify(self, _characteristic: BleakGATTCharacteristic, data: bytearray) -> None:
        self._dispatch(bytes(data))

    async def _connect(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        _LOGGER.debug("Connecting to Govee %s", self._ble_device.address)
        try:
            self._client = await establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                self._ble_device.address,
                disconnected_callback=self._on_disconnect,
                max_attempts=CONNECT_MAX_ATTEMPTS,
            )
            await self._client.start_notify(NOTIFY_CHAR_UUID, self._on_notify)
            await self._handshake()
        except BleakError as err:
            # Connection failures are expected/transient (contention, range);
            # log briefly without a full traceback and let the caller decide.
            _LOGGER.debug("Connection to %s failed: %s", self._ble_device.address, err)
            raise
        _LOGGER.debug("Connected and authenticated with %s", self._ble_device.address)

    def _on_disconnect(self, _client: BleakClientWithServiceCache) -> None:
        _LOGGER.debug("Govee %s disconnected", self._ble_device.address)
        self._session_key = None
        self._rx_key = PSK

    async def _drain_notify_queue(self) -> None:
        while not self._notify_queue.empty():
            self._notify_queue.get_nowait()

    async def _handshake(self) -> None:
        await self._drain_notify_queue()
        assert self._client is not None

        _LOGGER.debug("Starting handshake with %s", self._ble_device.address)
        self._rx_key = PSK  # both handshake replies are PSK-framed
        tx1 = p.encrypt_packet(PSK, p.build_plaintext(messages.build_handshake(0x01)))
        await self._client.write_gatt_char(WRITE_CHAR_UUID, tx1, response=False)
        rx1 = await asyncio.wait_for(self._notify_queue.get(), timeout=10)
        if rx1.name != "handshake" or rx1.fields.get("step") != 0x01:
            _LOGGER.warning(
                "Unexpected handshake response from %s: %s",
                self._ble_device.address,
                rx1.raw.hex(),
            )
            raise BleakError(f"Unexpected handshake response: {rx1.raw.hex()}")
        self._session_key = rx1.raw[2:18]
        _LOGGER.debug("Session key established for %s", self._ble_device.address)

        tx2 = p.encrypt_packet(PSK, p.build_plaintext(messages.build_handshake(0x02)))
        await self._client.write_gatt_char(WRITE_CHAR_UUID, tx2, response=False)
        try:
            await asyncio.wait_for(self._notify_queue.get(), timeout=3)  # TX2 ack (PSK), discarded
        except asyncio.TimeoutError:
            _LOGGER.debug("No TX2 ack from %s (usually harmless)", self._ble_device.address)
        self._rx_key = self._session_key  # subsequent frames use the session key
        await self._drain_notify_queue()

    def _cancel_disconnect_timer(self) -> None:
        if self._disconnect_timer is not None:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None

    def _schedule_disconnect(self) -> None:
        self._cancel_disconnect_timer()
        loop = asyncio.get_running_loop()
        self._disconnect_timer = loop.call_later(DISCONNECT_DELAY, self._on_disconnect_timer)

    def _on_disconnect_timer(self) -> None:
        # Fire the actual disconnect as a lock-guarded task so it can't tear
        # the connection down mid-command.
        self._disconnect_timer = None
        self._expire_task = asyncio.create_task(self._async_timed_disconnect())

    async def _async_timed_disconnect(self) -> None:
        async with self._lock:
            # If an operation started after the timer fired it scheduled a fresh
            # timer while we waited for the lock - so we're not idle anymore.
            if self._disconnect_timer is not None:
                return
            await self._disconnect_locked()

    # -- commands ------------------------------------------------------------

    async def _write(self, prefix: bytes) -> None:
        """Frame, encrypt (session key), and write one command prefix."""
        assert self._client is not None and self._session_key is not None
        ciphertext = p.encrypt_packet(self._session_key, p.build_plaintext(prefix))
        await self._client.write_gatt_char(WRITE_CHAR_UUID, ciphertext, response=False)

    async def send_command(self, prefix: bytes) -> bytes | None:
        """Connect if needed, send one command, return the decrypted ack (or None).

        The returned ack is best-effort only: with write-without-response the
        next notification isn't reliably the echo of *this* write, so callers
        should not treat it as confirmation (re-query status instead).
        """
        async with self._lock:
            await self._connect()
            self._cancel_disconnect_timer()
            # Clear stale/late notifications so a leftover packet isn't consumed
            # as this command's ack.
            await self._drain_notify_queue()

            _LOGGER.debug("Sending command %s to %s", prefix.hex(), self._ble_device.address)
            await self._write(prefix)

            ack: bytes | None = None
            try:
                msg = await asyncio.wait_for(self._notify_queue.get(), timeout=3)
                ack = msg.raw
                _LOGGER.debug("Ack for %s: %s", prefix.hex(), ack.hex())
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "No ack notification for command %s to %s",
                    prefix.hex(),
                    self._ble_device.address,
                )

            self._schedule_disconnect()
            return ack

    async def set_zone(self, zone: int, on: bool) -> None:
        await self.send_command(messages.build_zone(zone, on))

    async def set_brightness_pct(self, pct: int) -> None:
        await self.send_command(messages.build_brightness(pct))

    async def set_rgb_color(self, r: int, g: int, b: int) -> None:
        await self.send_command(messages.build_rgb(r, g, b))

    async def set_color_temp_kelvin(self, kelvin: int) -> None:
        await self.send_command(messages.build_color_temp(kelvin))

    async def set_segment_color(self, segment_mask: int, r: int, g: int, b: int) -> None:
        await self.send_command(messages.build_segment_color(segment_mask, r, g, b))

    async def set_segment_brightness(self, segment_mask: int, pct: int) -> None:
        await self.send_command(messages.build_segment_brightness(segment_mask, pct))

    async def set_scene(self, scene_id: tuple[int, int]) -> None:
        await self.send_command(messages.build_scene(scene_id))

    async def set_scene_full(self, scene_code: int, scenceParam_b64: str) -> bool:
        """Upload full effect data (a3-chunk burst) then activate it. Correct
        regardless of whether the device has the scene cached. Returns whether a
        completion ack was seen (diagnostic only)."""
        chunks = p.build_scene_chunks(scenceParam_b64)
        async with self._lock:
            await self._connect()
            self._cancel_disconnect_timer()
            await self._drain_notify_queue()

            _LOGGER.debug(
                "Uploading %d scene chunks (burst) for code %d to %s",
                len(chunks),
                scene_code,
                self._ble_device.address,
            )
            for chunk_prefix in chunks:
                await self._write(chunk_prefix)

            ack_received = False
            try:
                await asyncio.wait_for(self._notify_queue.get(), timeout=3)
                ack_received = True
            except asyncio.TimeoutError:
                # Acks aren't reliably 1:1 with the write; the burst already
                # went out, so proceed to activation regardless.
                _LOGGER.debug("No ack after scene upload to %s - activating anyway", self._ble_device.address)

            self._schedule_disconnect()

        await self.set_scene((scene_code & 0xFF, (scene_code >> 8) & 0xFF))
        # Large uploads need a moment before the device can answer anything else.
        await asyncio.sleep(min(0.2 + 0.1 * len(chunks), 2.0))
        return ack_received

    # -- status / metadata (reassembled via the shared ChunkReassembler) -----

    async def _read_status(self, full: bool) -> GoveeBleStatus | None:
        """Trigger a status query and reassemble the chunked response into a
        GoveeBleStatus (via messages.ChunkReassembler -> protocol.parse_status).
        Returns None if the response never completed within the timeout."""
        assert self._client is not None and self._session_key is not None
        await self._drain_notify_queue()
        reasm = messages.ChunkReassembler(self._ble_device.address)
        trigger = messages.build_status_query(full=full)
        reasm.feed("WRITE", trigger)  # tells the reassembler whether to expect the fuller (segment) set
        _LOGGER.debug("Requesting status from %s (full=%s)", self._ble_device.address, full)
        await self._write(trigger)
        try:
            while True:
                msg = await asyncio.wait_for(self._notify_queue.get(), timeout=STATUS_CHUNK_TIMEOUT)
                result = reasm.feed("NOTIFY", msg.raw)
                if result is not None and "status" in result.fields:
                    status = result.fields["status"]
                    assert isinstance(status, GoveeBleStatus)
                    return status
        except asyncio.TimeoutError:
            _LOGGER.debug("Status query from %s did not complete in time", self._ble_device.address)
            return None

    async def get_status(self, with_segments: bool = False) -> GoveeBleStatus:
        """Query current device status (zones, brightness, scene, versions, MACs).

        with_segments=True uses the fuller query that also returns per-segment
        state — a longer, drop-prone burst, so ``status.segments`` may still be
        None on a given poll even when requested.
        """
        async with self._lock:
            await self._connect()
            self._cancel_disconnect_timer()

            status = await self._read_status(with_segments)
            if status is None:
                # The device can be briefly unresponsive after a large op and may
                # even drop the connection; one quick retry (re-connecting first)
                # avoids flagging unavailable over a transient blip.
                _LOGGER.debug("Empty status from %s, retrying once", self._ble_device.address)
                await asyncio.sleep(0.5)
                await self._connect()
                status = await self._read_status(with_segments)

            self._schedule_disconnect()

            if status is None:
                raise BleakError(f"No status response from {self._ble_device.address}")

            _LOGGER.debug("Status from %s: %s", self._ble_device.address, status)
            return status

    async def _read_metadata_field(self, field_id: int) -> str | None:
        """Query a device metadata field (`ab` opcode) and return its
        reassembled ASCII value (via messages.ChunkReassembler ->
        protocol.parse_metadata_field_text), or None if unavailable."""
        assert self._client is not None and self._session_key is not None
        await self._drain_notify_queue()
        reasm = messages.ChunkReassembler(self._ble_device.address)
        trigger = messages.build_metadata_query(field_id)
        reasm.feed("WRITE", trigger)  # records which field is being read
        await self._write(trigger)
        try:
            while True:
                msg = await asyncio.wait_for(self._notify_queue.get(), timeout=METADATA_FIELD_TIMEOUT)
                result = reasm.feed("NOTIFY", msg.raw)
                if result is not None and result.name == "metadata":
                    text = result.fields.get("text")
                    return text if isinstance(text, str) else None
        except asyncio.TimeoutError:
            _LOGGER.debug("Metadata field 0x%02x from %s did not complete", field_id, self._ble_device.address)
            return None

    async def get_serial_number(self) -> str | None:
        """Query the device serial/UID string (`ab` field 0x05). Returns None
        if unavailable or the payload doesn't decode cleanly."""
        async with self._lock:
            await self._connect()
            self._cancel_disconnect_timer()
            value = await self._read_metadata_field(0x05)
            self._schedule_disconnect()
        return value

    # -- teardown ------------------------------------------------------------

    async def disconnect(self) -> None:
        """Cancel any pending idle timer and tear down the connection, waiting
        for any in-flight command to finish first."""
        self._cancel_disconnect_timer()
        async with self._lock:
            await self._disconnect_locked()

    async def _disconnect_locked(self) -> None:
        """Actual teardown. Caller must hold self._lock."""
        if self._client is not None and self._client.is_connected:
            _LOGGER.debug("Disconnecting from %s (idle)", self._ble_device.address)
            await self._client.disconnect()
        self._client = None
        self._session_key = None
        self._rx_key = PSK
