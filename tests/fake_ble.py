"""A scriptable fake Govee BLE peripheral for transport tests.

Stands in for the bleak client returned by ``establish_connection`` and behaves
like the real hardware as observed in live frame captures:

* AES_RC4_PSK handshake: replies to ``e7 01`` with the session key (delivered
  TWICE, as BlueZ/the device double-deliver it), echoes ``e7 02`` ~immediately and
  retransmits that echo again a little later (late enough to land inside the
  first query of the session).
* Acks every ``0x33`` write with ``[0x33, cmd, status]``.
* Answers the ``0xAC`` status query with a scripted burst.

Knobs let a test make a link half-dead (stop acking), reject writes, drop the
link mid-operation, etc.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from govee_ble_local.const import PSK
from govee_ble_local.crypto import checksum, decrypt, encrypt

SESSION_KEY = bytes(range(16))


def frame(*b: int) -> bytes:
    body = bytes(b) + b"\x00" * (19 - len(b))
    return body + bytes([checksum(body)])


class FakeClient:
    """One BLE link. Created per establish_connection call."""

    def __init__(self, device: "FakeGovee", disconnected_callback: Callable[[Any], None] | None):
        self.device = device
        self.is_connected = True
        self._disconnected_callback = disconnected_callback
        self._notify: Callable[[Any, bytearray], None] | None = None
        self.services = _Services(device.bgc)
        self.writes: list[bytes] = []
        self.dead = False  # half-open: writes "succeed" but nothing comes back

    async def start_notify(self, _uuid: str, cb: Callable[[Any, bytearray], None]) -> None:
        if self.device.fail_start_notify:
            from bleak.exc import BleakError

            raise BleakError("notify failed")
        self._notify = cb

    async def read_gatt_char(self, _char: Any) -> bytearray:
        self.device.bgc_reads += 1
        return bytearray(self.device.bgc or b"")

    async def disconnect(self) -> None:
        if self.is_connected:
            self.is_connected = False
            self.device.disconnects += 1
            cb = self._disconnected_callback
            if cb is not None:
                # bleak calls it from the transport after teardown
                asyncio.get_running_loop().call_soon(cb, self)

    def drop(self) -> None:
        """Peripheral/proxy-side link loss."""
        self.is_connected = False
        if self._disconnected_callback is not None:
            self._disconnected_callback(self)

    def push(self, raw: bytes, delay: float = 0.0) -> None:
        def deliver() -> None:
            if self.is_connected and self._notify is not None:
                self._notify(None, bytearray(raw))

        asyncio.get_running_loop().call_later(delay, deliver)

    async def write_gatt_char(self, _uuid: str, data: bytes, response: bool = False) -> None:
        if self.device.write_error is not None:
            raise self.device.write_error
        self.writes.append(bytes(data))
        if self.dead:
            return
        self.device.handle_write(self, bytes(data))


class _Services:
    def __init__(self, bgc: bytes | None) -> None:
        self._bgc = bgc

    def get_characteristic(self, _uuid: str) -> object | None:
        return object() if self._bgc is not None else None


class FakeGovee:
    """The peripheral: behaviour shared across links."""

    def __init__(self, *, encrypted: bool = True) -> None:
        self.encrypted = encrypted
        self.bgc: bytes | None = bytes([1, 1]) if encrypted else None
        self.clients: list[FakeClient] = []
        self.disconnects = 0
        self.bgc_reads = 0
        self.ack_status = 0
        self.dead_links = 0          # the first N links never answer anything
        self.write_error: BaseException | None = None
        self.fail_start_notify = False
        self.late_echo_delay = 0.05  # e7 02 retransmit lands inside the next query
        self.status_burst: list[bytes] = []
        self.drop_terminal = False
        self.received: list[bytes] = []  # decrypted application frames
        self.read_replies: dict[int, bytes] = {}  # 0xAA command -> reply frame
        self.on_write: Callable[[FakeClient, bytes], None] | None = None

    # -- establish_connection stand-in ---------------------------------------
    async def establish(self, _cls: Any, _device: Any, _name: str, **kwargs: Any) -> FakeClient:
        self.connect_kwargs = kwargs
        client = FakeClient(self, kwargs.get("disconnected_callback"))
        if len(self.clients) < self.dead_links:
            client.dead = True
        self.clients.append(client)
        return client

    # -- protocol ------------------------------------------------------------
    def _wire(self, plain: bytes, key: bytes | None) -> bytes:
        return encrypt(plain, key) if (self.encrypted and key) else plain

    def handle_write(self, client: FakeClient, data: bytes) -> None:
        if self.encrypted:
            hs = decrypt(data, PSK)
            if hs[:2] == b"\xe7\x01":
                reply = encrypt(frame(0xE7, 0x01, *SESSION_KEY), PSK)
                client.push(reply)
                client.push(reply)  # double delivery
                return
            if hs[:2] == b"\xe7\x02":
                client.push(data, 0.01)                 # immediate echo
                client.push(data, self.late_echo_delay)  # late retransmit
                return
            plain = decrypt(data, SESSION_KEY)
        else:
            plain = data
        self.received.append(plain)
        if self.on_write is not None:
            self.on_write(client, plain)
            return
        key = SESSION_KEY if self.encrypted else None
        if plain[0] == 0x33:
            client.push(self._wire(frame(0x33, plain[1], self.ack_status), key), 0.005)
        elif plain[0] == 0xAC:
            burst = list(self.status_burst)
            if self.drop_terminal:
                burst = [f for f in burst if f[1] != 0xFF]
            for i, f in enumerate(burst):
                client.push(self._wire(f, key), 0.06 + 0.005 * i)
        elif plain[0] == 0xAA:
            reply = self.read_replies.get(plain[1], frame(0xAA, plain[1], 0x01))
            client.push(self._wire(reply, key), 0.005)
