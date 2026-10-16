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
import inspect
from collections.abc import Callable, Iterator
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from govee_ble_local.const import BGC_INFO_CHAR_UUID, PSK, SERVICE_UUID
from govee_ble_local.crypto import checksum, decrypt, encrypt

SESSION_KEY = bytes(range(16))
# The OTA V1 service: it also carries a 2b12 characteristic (spec/docs/GOVEE_BLE_GATT_PROTOCOL.md).
OTA_SERVICE_UUID = "00010203-0405-0607-0809-0a0b0c0d1912"


def ble_device(address: str, name: str | None) -> BLEDevice:
    """Build a BLEDevice across bleak versions (<1.0 requires ``rssi``; >=1.0 dropped it)."""
    kwargs: dict[str, Any] = {"details": {}}
    if "rssi" in inspect.signature(BLEDevice.__init__).parameters:
        kwargs["rssi"] = 0
    return BLEDevice(address, name, **kwargs)


def frame(*b: int) -> bytes:
    body = bytes(b) + b"\x00" * (19 - len(b))
    return body + bytes([checksum(body)])


class FakeClient:
    """One BLE link. Created per establish_connection call."""

    def __init__(self, device: FakeGovee, disconnected_callback: Callable[[Any], None] | None):
        self.device = device
        self.is_connected = True
        self._disconnected_callback = disconnected_callback
        self._notify: Callable[[Any, bytearray], None] | None = None
        self._services = _Services(
            device.bgc, duplicate=device.duplicate_bgc_char, uuids=device.gatt_services,
        )
        self.writes: list[bytes] = []
        self.dead = False  # half-open: writes "succeed" but nothing comes back

    @property
    def services(self) -> _Services:
        if self.device.services_error is not None:
            raise self.device.services_error  # bleak: "Service Discovery has not been performed yet"
        return self._services

    @services.setter
    def services(self, value: _Services) -> None:
        self._services = value

    async def start_notify(self, _uuid: str, cb: Callable[[Any, bytearray], None]) -> None:
        if self.device.start_notify_error is not None:
            raise self.device.start_notify_error
        if self.device.fail_start_notify:
            raise BleakError("notify failed")
        self._notify = cb

    async def read_gatt_char(self, char: Any) -> bytearray:
        self.device.bgc_reads += 1
        self.device.bgc_read_chars.append(char)
        if self.device.bgc_read_error is not None:
            raise self.device.bgc_read_error
        return bytearray(self.device.bgc or b"")

    async def disconnect(self) -> None:
        if self.device.disconnect_hangs:
            self.device.disconnect_started += 1
            await asyncio.sleep(3600)  # wedged proxy: never completes
        if self.device.disconnect_error is not None:
            raise self.device.disconnect_error
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


class _Char:
    """A resolved characteristic: its UUID and the service it sits under."""

    def __init__(self, uuid: str, service: _Service) -> None:
        self.uuid = uuid
        self.service = service


class _Service:
    """One resolved GATT service. The Govee control service (1910) and the OTA service (1912)
    carry the BGC-info char 2b12 when the device has one; any other service carries nothing."""

    def __init__(self, bgc: bytes | None, *, uuid: str = SERVICE_UUID) -> None:
        self.uuid = uuid
        self._bgc = bgc
        has_bgc = bgc is not None and uuid in (SERVICE_UUID, OTA_SERVICE_UUID)
        self.bgc_char = _Char(BGC_INFO_CHAR_UUID, self) if has_bgc else None

    def get_characteristic(self, uuid: str) -> _Char | None:
        # bleak's BleakGATTService.get_characteristic: the first match in THIS service, never raises.
        return self.bgc_char if uuid == BGC_INFO_CHAR_UUID else None


class _Services:
    """bleak's BleakGATTServiceCollection, reduced to what the library can touch."""

    def __init__(
        self, bgc: bytes | None, *, duplicate: bool = False, uuids: list[str] | None = None,
    ) -> None:
        self._bgc = bgc
        # `duplicate`: 2b12 also under the OTA service 1912 (as on some firmwares). `uuids`: the
        # resolved services in discovery order; None is just 1910 (+ 1912 when `duplicate`).
        self._duplicate = duplicate
        if uuids is None:
            uuids = [SERVICE_UUID, OTA_SERVICE_UUID] if duplicate else [SERVICE_UUID]
        self.all = [_Service(bgc, uuid=u) for u in uuids]

    def __iter__(self) -> Iterator[_Service]:
        return iter(self.all)

    def get_service(self, uuid: str) -> _Service | None:
        # Like bleak: matched by UUID across the collection, raising when two services share it.
        found = [s for s in self.all if s.uuid == uuid]
        if len(found) > 1:
            raise BleakError("Multiple Services with this UUID, refer to your desired service by "
                             "the `handle` attribute instead.")
        return found[0] if found else None

    def get_characteristic(self, uuid: str) -> _Char | None:
        # Like bleak: across every service, raising when the UUID exists under two of them.
        found = [c for s in self.all if (c := s.get_characteristic(uuid)) is not None]
        if len(found) > 1:
            raise BleakError("Multiple Characteristics with this UUID, refer to your desired "
                             "characteristic by the `handle` attribute instead.")
        return found[0] if found else None


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
        # -- failure-injection knobs (all default OFF) --
        self.establish_error: BaseException | None = None  # raised by establish()
        self.establish_calls = 0
        self.bgc_read_error: BaseException | None = None
        self.withhold_e7_reply = False    # never answer e7 01
        self.withhold_e7_confirm = False  # never echo e7 02
        self.drop_on_e7 = False           # drop the link when e7 01 arrives
        self.app_write_error: BaseException | None = None  # raised on post-handshake writes
        self.no_ack_cmds: set[int] = set()  # 0x33 commands that are never acked
        self.disconnect_hangs = False     # client.disconnect() never returns
        self.disconnect_started = 0       # how many hanging disconnects were entered
        self.disconnect_error: BaseException | None = None  # raised by client.disconnect()
        # First N links: ack the first 0x33 write and disconnect in the SAME loop tick.
        self.ack_then_drop_links = 0
        self.start_notify_error: BaseException | None = None  # raised by start_notify()
        self.duplicate_bgc_char = False   # 2b12 under two services (collection lookup raises)
        # Resolved service UUIDs in discovery order (None: the default layout, see _Services).
        self.gatt_services: list[str] | None = None
        self.services_error: BaseException | None = None  # raised by client.services
        self.bgc_read_chars: list[Any] = []  # the characteristic object of each BGC read

    # -- establish_connection stand-in ---------------------------------------
    async def establish(self, _cls: Any, _device: Any, _name: str, **kwargs: Any) -> FakeClient:
        self.connect_kwargs = kwargs
        self.connect_device = _device  # the BLEDevice this attempt connected with
        self.establish_calls += 1
        if self.establish_error is not None:
            raise self.establish_error
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
                if self.drop_on_e7:
                    asyncio.get_running_loop().call_soon(client.drop)
                    return
                if self.withhold_e7_reply:
                    return
                reply = encrypt(frame(0xE7, 0x01, *SESSION_KEY), PSK)
                client.push(reply)
                client.push(reply)  # double delivery
                return
            if hs[:2] == b"\xe7\x02":
                if self.withhold_e7_confirm:
                    return
                client.push(data, 0.01)                 # immediate echo
                client.push(data, self.late_echo_delay)  # late retransmit
                return
            plain = decrypt(data, SESSION_KEY)
        else:
            plain = data
        if self.app_write_error is not None:
            raise self.app_write_error
        self.received.append(plain)
        if self.on_write is not None:
            self.on_write(client, plain)
            return
        key = SESSION_KEY if self.encrypted else None
        if plain[0] == 0x33 and plain[1] in self.no_ack_cmds:
            return
        if plain[0] == 0x33 and self.clients.index(client) < self.ack_then_drop_links:
            ack = self._wire(frame(0x33, plain[1], self.ack_status), key)

            def ack_and_drop() -> None:
                client._notify(None, bytearray(ack))  # type: ignore[misc]
                client.drop()  # same tick: ack queued, then link-lost sentinel

            asyncio.get_running_loop().call_later(0.005, ack_and_drop)
            return
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
