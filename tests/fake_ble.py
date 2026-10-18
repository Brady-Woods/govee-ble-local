"""Scriptable fake Govee BLE peripherals for transport tests.

They stand in for the bleak client returned by ``establish_connection``. :class:`Peripheral` is the
bleak side: the links, the GATT layout, and knobs that make a link half-dead, drop it mid-operation,
fail service discovery, etc. Two peripherals answer on top of it:

* :class:`FirmwareGovee`: a ``spec/firmware.yaml`` image (H5083 today) run by
  ``tests/firmware_sim.py``. Every frame and advert it sends is built through the spec.
* :class:`FakeGovee`: a generic peripheral for the SKUs with no firmware image yet. It behaves like
  the hardware as observed in live frame captures:

  * AES_RC4_PSK handshake: it replies to ``e7 01`` with the session key (delivered TWICE, as
    BlueZ/the device double-deliver it), echoes ``e7 02`` ~immediately and retransmits that echo
    again a little later (late enough to land inside the first query of the session).
  * It acks every ``0x33`` write with ``[0x33, cmd, status]``.
  * It answers the ``0xAC`` status query with a scripted burst.
"""
from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Iterator
from types import SimpleNamespace
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from govee_ble_local.const import BGC_INFO_CHAR_UUID, PSK, SERVICE_UUID, WRITE_CHAR_UUID
from govee_ble_local.crypto import checksum, decrypt, encrypt
from govee_ble_local.wire import _spec

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


def status_burst(tlvs: bytes) -> list[bytes]:
    """A 0xAC status reply carrying the TLV stream ``tlvs``, chunked as the device sends it
    (AbsController4BleSingleSendMultiBack: first chunk [ac 00 <packets u16le> <lastLen> 03 00] + 12 data
    bytes, later chunks 17, the last tagged 0xFF; one packet when it all fits in the first)."""
    if len(tlvs) <= 12:
        return [frame(0xAC, 0x00, 0x01, 0x00, len(tlvs), 0x03, 0x00, *tlvs)]
    rest = tlvs[12:]
    chunks = [rest[i:i + 17] for i in range(0, len(rest), 17)]
    total = 1 + len(chunks)
    out = [frame(0xAC, 0x00, total & 0xFF, total >> 8, len(chunks[-1]), 0x03, 0x00, *tlvs[:12])]
    out += [frame(0xAC, 0xFF if i == len(chunks) else i, *c) for i, c in enumerate(chunks, start=1)]
    return out


class FakeClient:
    """One BLE link. Created per establish_connection call."""

    def __init__(self, device: Peripheral, disconnected_callback: Callable[[Any], None] | None):
        self.device = device
        self.is_connected = True
        self._disconnected_callback = disconnected_callback
        self._notify: Callable[[Any, bytearray], None] | None = None
        self._services = _Services(
            device.bgc, duplicate=device.duplicate_bgc_char, uuids=device.gatt_services,
            write_size=device.write_size,
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
            self.device.link_down(self)
            cb = self._disconnected_callback
            if cb is not None:
                # bleak calls it from the transport after teardown
                asyncio.get_running_loop().call_soon(cb, self)

    def drop(self) -> None:
        """Peripheral/proxy-side link loss."""
        self.is_connected = False
        self.device.link_down(self)
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


class _WriteChar(_Char):
    """The control service's write characteristic, with bleak's ``max_write_without_response_size``."""

    def __init__(self, service: _Service, size: object) -> None:
        super().__init__(WRITE_CHAR_UUID, service)
        self.max_write_without_response_size = size


class _Service:
    """One resolved GATT service. The Govee control service (1910) and the OTA service (1912)
    carry the BGC-info char 2b12 when the device has one; any other service carries nothing."""

    def __init__(self, bgc: bytes | None, *, uuid: str = SERVICE_UUID, write_size: object = None) -> None:
        self.uuid = uuid
        self._bgc = bgc
        has_bgc = bgc is not None and uuid in (SERVICE_UUID, OTA_SERVICE_UUID)
        self.bgc_char = _Char(BGC_INFO_CHAR_UUID, self) if has_bgc else None
        # The write char only when a test gives it a size (None: the default layout, no write char).
        has_write = write_size is not None and uuid == SERVICE_UUID
        self.write_char = _WriteChar(self, write_size) if has_write else None

    def get_characteristic(self, uuid: str) -> _Char | None:
        # bleak's BleakGATTService.get_characteristic: the first match in THIS service, never raises.
        if uuid == WRITE_CHAR_UUID:
            return self.write_char
        return self.bgc_char if uuid == BGC_INFO_CHAR_UUID else None


class _Services:
    """bleak's BleakGATTServiceCollection, reduced to what the library can touch."""

    def __init__(
        self, bgc: bytes | None, *, duplicate: bool = False, uuids: list[str] | None = None,
        write_size: object = None,
    ) -> None:
        self._bgc = bgc
        # `duplicate`: 2b12 also under the OTA service 1912 (as on some firmwares). `uuids`: the
        # resolved services in discovery order; None is just 1910 (+ 1912 when `duplicate`).
        self._duplicate = duplicate
        if uuids is None:
            uuids = [SERVICE_UUID, OTA_SERVICE_UUID] if duplicate else [SERVICE_UUID]
        self.all = [_Service(bgc, uuid=u, write_size=write_size) for u in uuids]

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


class Peripheral:
    """The bleak side of a fake peripheral, shared across its links: the GATT layout and the
    transport failure knobs. What it answers is a subclass's :meth:`handle_write`."""

    def __init__(self) -> None:
        self.bgc: bytes | None = None
        self.clients: list[FakeClient] = []
        self.disconnects = 0
        self.bgc_reads = 0
        self.dead_links = 0          # the first N links never answer anything
        self.write_error: BaseException | None = None
        self.fail_start_notify = False
        # -- failure-injection knobs (all default OFF) --
        self.establish_error: BaseException | None = None  # raised by establish()
        self.establish_calls = 0
        self.bgc_read_error: BaseException | None = None
        self.disconnect_hangs = False     # client.disconnect() never returns
        self.disconnect_started = 0       # how many hanging disconnects were entered
        self.disconnect_error: BaseException | None = None  # raised by client.disconnect()
        self.start_notify_error: BaseException | None = None  # raised by start_notify()
        self.duplicate_bgc_char = False   # 2b12 under two services (collection lookup raises)
        # Resolved service UUIDs in discovery order (None: the default layout, see _Services).
        self.gatt_services: list[str] | None = None
        self.services_error: BaseException | None = None  # raised by client.services
        self.bgc_read_chars: list[Any] = []  # the characteristic object of each BGC read
        # The write characteristic's max_write_without_response_size (None: no write char resolved).
        self.write_size: object = None

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
        self.link_up(client)
        return client

    def link_up(self, client: FakeClient) -> None:
        """A link came up (``establish``)."""

    def link_down(self, client: FakeClient) -> None:
        """A link went down: the central disconnected, or the peripheral or proxy dropped it."""

    def handle_write(self, client: FakeClient, data: bytes) -> None:
        raise NotImplementedError


class FirmwareGovee(Peripheral):
    """A peripheral whose behaviour is a ``spec/firmware.yaml`` image, run by ``tests/firmware_sim.py``:
    every frame and advert it sends is built through the spec. One link at a time, as the device
    takes one central. ``sim`` is the device: its state, clock (:meth:`Sim.advance`) and events
    (:meth:`Sim.event`, e.g. ``"button"``)."""

    reply_delay = 0.005   # the first frame of a reply; each later one lands 1 ms after the one before

    def __init__(self, image_id: str | None = None, *, seed: int = 1) -> None:
        from firmware_sim import H5083, Sim   # PyYAML is a test dependency: only this fake needs it

        super().__init__()
        self.sim = Sim(image_id or H5083, seed=seed)
        if self.sim.im["gatt"]["bgc_info"]:
            raise NotImplementedError("a 2b12 BGC-info characteristic is not modelled")
        self.sim.on_send = self._deliver
        self.sim.on_drop = self._device_drop
        self._client: FakeClient | None = None
        self._last_at = 0.0

    @property
    def received(self) -> list[bytes]:
        """Each client write as the device decrypted it."""
        return self.sim.received

    def link_up(self, client: FakeClient) -> None:
        self._client = client
        self.sim.connect()

    def link_down(self, client: FakeClient) -> None:
        if client is self._client:
            self._client = None
            self.sim.disconnect()

    def handle_write(self, client: FakeClient, data: bytes) -> None:
        if client is self._client:
            self.sim.write(data)

    def _deliver(self, _what: str, _plain: bytes, wire: bytes) -> None:
        client = self._client
        if client is None:
            return
        loop = asyncio.get_running_loop()
        self._last_at = max(loop.time() + self.reply_delay, self._last_at + 0.001)
        client.push(wire, self._last_at - loop.time())

    def _device_drop(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            asyncio.get_running_loop().call_soon(client.drop)

    def advertisement(self) -> SimpleNamespace | None:
        """What a scanner hears now, in bleak's ``AdvertisementData`` shape (``local_name``,
        ``manufacturer_data`` as {company id: value}, ``rssi``), or None while the device doesn't
        advertise. The device's advertising data and scan response are read through the
        ``govee_scan_record`` root."""
        sent = self.sim.advert()
        if sent is None:
            return None
        name: str | None = None
        mfg: dict[int, bytes] = {}
        ad_type = _spec.ScanRecord.AdType
        for blob in sent:
            for ad in _spec.read(_spec.ScanRecord, blob).ad_structures:
                kind = getattr(ad, "ad_type", None)
                if kind == ad_type.complete_local_name:
                    name = ad.data.text
                elif kind == ad_type.manufacturer_specific:
                    raw = bytes(ad._raw_data)   # bleak splits the AD: company id (u2le), then the value
                    mfg[int.from_bytes(raw[:2], "little")] = raw[2:]
        return SimpleNamespace(local_name=name, manufacturer_data=mfg, rssi=-60)


class FakeGovee(Peripheral):
    """The generic peripheral: canned replies for the SKUs with no firmware image."""

    def __init__(self, *, encrypted: bool = True) -> None:
        super().__init__()
        self.encrypted = encrypted
        self.bgc = bytes([1, 1]) if encrypted else None
        self.ack_status = 0
        self.late_echo_delay = 0.05  # e7 02 retransmit lands inside the next query
        self.status_burst: list[bytes] = []
        self.drop_terminal = False
        self.received: list[bytes] = []  # decrypted application frames
        self.read_replies: dict[int, bytes] = {}  # 0xAA command -> reply frame
        self.on_write: Callable[[FakeClient, bytes], None] | None = None
        self.withhold_e7_reply = False    # never answer e7 01
        self.withhold_e7_confirm = False  # never echo e7 02
        self.drop_on_e7 = False           # drop the link when e7 01 arrives
        self.app_write_error: BaseException | None = None  # raised on post-handshake writes
        self.no_ack_cmds: set[int] = set()  # 0x33 commands that are never acked
        # First N links: ack the first 0x33 write and disconnect in the SAME loop tick.
        self.ack_then_drop_links = 0

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


def frames_of(frames: list[bytes] | Callable[[int], list[bytes]]) -> list[bytes]:
    """What a ``send_unacked`` fake was given: a frame list, or (scene uploads) a function of the link's
    write size, resolved with the default 20 as an unknown MTU is."""
    return list(frames(20)) if callable(frames) else list(frames)


def unacked_via(record: Callable[[list[bytes]], Awaitable[None]]) -> Callable[..., Awaitable[None]]:
    """A ``send_unacked`` fake that hands the resolved frames to ``record`` (e.g. a ``send_many`` fake)."""
    async def send_unacked(frames: Any, **_k: object) -> None:
        await record(frames_of(frames))

    return send_unacked
