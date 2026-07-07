"""GoveeDevice base class + capability mixins.

Public API mirrors HA BLE-library conventions (led-ble / switchbot):
constructor takes a BLEDevice (+ optional advertisement); state is exposed as
properties; `update()` refreshes; `register_callback()` subscribes to changes;
`stop()` tears down. Concrete devices compose the capability mixins they support.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, ClassVar

from bleak.backends.device import BLEDevice

from ..ble import controllers, status
from ..ble.controllers import ColorScheme
from ..identify import identify, parse_broadcast_onoff
from ..exceptions import GoveeBleNotSupported
from ..models import Capability, DeviceState, Encryption, Zone
from ..scenes import load_scenes
from ..transport.connection import GoveeConnection, now_ts

_LOGGER = logging.getLogger(__name__)

StateCallback = Callable[[DeviceState], None]


class GoveeDevice:
    """Base for all Govee BLE devices."""

    #: SKUs this class handles (set by subclasses; used by the registry).
    skus: ClassVar[tuple[str, ...]] = ()
    #: Capabilities this class exposes.
    capabilities: ClassVar[frozenset[Capability]] = frozenset()
    #: True if this device requires the secret-key check to accept commands.
    requires_secret: ClassVar[bool] = False
    #: True if power uses the plug relay encoding (0x10/0x11) vs binary (0x00/0x01).
    _relay_power: ClassVar[bool] = False
    #: Command-channel encryption mode.
    _encryption: ClassVar[Encryption] = Encryption.AES_RC4_PSK
    #: RGB/color-temp byte-layout family.
    _color_scheme: ClassVar[ColorScheme] = "h60a6"
    #: Number of addressable segments (drives the whole-device color mask).
    _segments: ClassVar[int] = 13
    #: Named physical zones (e.g. H60A6 ring/panel), empty if none.
    zones: ClassVar[tuple[Zone, ...]] = ()

    def __init__(
        self,
        ble_device: BLEDevice,
        advertisement_data: Any | None = None,
        *,
        sku: str | None = None,
        secret: bytes | None = None,
    ) -> None:
        self._ble_device = ble_device
        self._advertisement = advertisement_data
        self._sku = sku or (self.skus[0] if self.skus else "")
        self._secret = secret
        self._state = DeviceState(optimistic=True)
        self._callbacks: list[StateCallback] = []
        # Encryption is decided by the device's own advertisement (the app's
        # `encrypt` flag), not a per-SKU guess. Fall back to the class default
        # only when no usable advertisement is available (e.g. address-only
        # construction on reconnect).
        self._resolved_encryption = self._resolve_encryption(advertisement_data)
        self._connection = GoveeConnection(
            ble_device,
            encryption=self._resolved_encryption,
            unlock_frames=self._unlock_frames if self.requires_secret else None,
        )

    def _resolve_encryption(self, advertisement_data: Any | None) -> Encryption:
        mfg = getattr(advertisement_data, "manufacturer_data", None)
        name = getattr(advertisement_data, "local_name", None) or self._ble_device.name
        if mfg:
            adv = identify(name, mfg)
            if adv is not None:
                # V2/AES-GCM is distinguished later by the BgcInfo read; the
                # advertisement only says encrypted-vs-not.
                return Encryption.AES_RC4_PSK if adv.encrypted else Encryption.NONE
        return self._encryption

    # -- identity / state (properties: HA convention) -----------------------

    @property
    def address(self) -> str:
        return self._ble_device.address

    @property
    def name(self) -> str:
        return self._ble_device.name or self.address

    @property
    def sku(self) -> str:
        return self._sku

    @property
    def model(self) -> str:
        return self._sku

    @property
    def rssi(self) -> int | None:
        return getattr(self._advertisement, "rssi", None)

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def is_on(self) -> bool | None:
        return self._state.is_on

    # -- device info (populated by read-back where supported) ---------------

    @property
    def wifi_mac(self) -> str | None:
        return self._state.wifi_mac

    @property
    def hardware_version(self) -> str | None:
        return self._state.hardware_version

    @property
    def firmware_version(self) -> str | None:
        return self._state.firmware_version

    @property
    def serial_number(self) -> str | None:
        return self._state.serial_number

    # -- lifecycle ----------------------------------------------------------

    def set_secret(self, secret: bytes) -> None:
        self._secret = secret

    def update_ble_device(self, ble_device: BLEDevice, advertisement_data: Any | None = None) -> None:
        self._ble_device = ble_device
        if advertisement_data is not None:
            self._advertisement = advertisement_data
            self.ingest_advertisement(advertisement_data)
        self._connection.update_ble_device(ble_device)

    def ingest_advertisement(self, advertisement_data: Any | None) -> bool:
        """Update PASSIVE state (on/off) from a BLE advertisement — no
        connection/slot used. Govee lights broadcast on/off in their mfg data
        (parse_broadcast_onoff). Returns True if the state changed."""
        mfg = getattr(advertisement_data, "manufacturer_data", None)
        if not mfg:
            return False
        on = parse_broadcast_onoff(mfg)
        if on is None or on == self._state.is_on:
            return False
        self._state.is_on = on
        self._notify_state()
        return True

    def register_callback(self, callback: StateCallback) -> Callable[[], None]:
        """Subscribe to state changes. Returns an unregister function."""
        self._callbacks.append(callback)

        def _unregister() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return _unregister

    def _notify_state(self) -> None:
        for cb in list(self._callbacks):
            try:
                cb(self._state)
            except Exception:  # pragma: no cover
                _LOGGER.exception("%s: state callback failed", self.address)

    async def update(self) -> DeviceState:
        """Refresh device state.

        Devices with read-back (StatusReadable) connect and read real state.
        Optimistic devices do NO I/O here — connecting just to "poll" a device
        with nothing to read wastes scarce BLE connection slots and blocks
        setup when the device is momentarily unreachable; their state reflects
        the last command sent, and commands connect on demand."""
        await self._read_state()
        self._notify_state()
        return self._state

    async def _read_state(self) -> None:
        """Refresh ``self._state`` from the device over BLE. Default: no
        read-back and no connection — state stays optimistic (last command
        sent). Devices that expose real status override this (StatusReadable,
        PolledLight)."""
        return None

    async def _read_reply(self, frame: bytes, command_type: int) -> bytes | None:
        """Send a single-frame read and return the reply matching
        [0xAA, command_type] (caller validates any sub-selector), or None."""
        frames = await self._connection.query(
            frame, opcode=0xAA, terminal=command_type, timeout=2.0
        )
        for reply in frames:
            if len(reply) >= 2 and reply[0] == 0xAA and reply[1] == command_type:
                return reply
        return None

    async def stop(self) -> None:
        await self._connection.disconnect()

    # -- internal helpers ---------------------------------------------------

    def _unlock_frames(self) -> list[bytes]:
        """Frames sent right after each handshake for secret-gated devices."""
        if self._secret is None:
            _LOGGER.warning("%s: secret required but none set; commands will fail", self.address)
            return []
        return [controllers.secret_check(self._secret)]

    async def read_secret(self) -> bytes | None:
        """Read this device's 8-byte secret directly over BLE (`aa b1`).

        Fully offline (no cloud). Only works while the device is UNBOUND
        (factory-reset / not yet paired to a Govee account); a bound device
        declines and this returns None. The secret is stable, so a value read
        here keeps working after you re-pair in the Govee app.
        """
        # A dedicated connection with NO unlock frames — we don't have the
        # secret yet, so we can't do the 33 b2 check first.
        conn = GoveeConnection(self._ble_device, encryption=self._resolved_encryption)
        try:
            reply = await conn.send(controllers.secret_read())
        finally:
            await conn.disconnect()
        if reply and len(reply) >= 11 and reply[0] == 0xAA and reply[1] == 0xB1 and reply[2] == 0x01:
            secret = reply[3:11]
            self._secret = secret
            return secret
        return None

    # -- optional capability API --------------------------------------------
    #
    # Declared on the base so the full public surface is typed and discoverable
    # from a `GoveeDevice` handle (e.g. the one `create_device` returns). Each
    # capability mixin below overrides the relevant members; a device that does
    # NOT mix a capability in raises GoveeBleNotSupported (or reports None for a
    # state property). Guard calls with `Capability.X in device.capabilities`.

    @property
    def brightness(self) -> int | None:
        return None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return None

    @property
    def color_temp_kelvin(self) -> int | None:
        return None

    @property
    def scene_names(self) -> list[str]:
        return []

    @property
    def active_scene(self) -> str | None:
        return None

    def _unsupported(self, feature: str) -> GoveeBleNotSupported:
        return GoveeBleNotSupported(f"{self.sku}: {feature} not supported")

    async def set_power(self, on: bool) -> None:
        raise self._unsupported("power")

    async def turn_on(self) -> None:
        raise self._unsupported("power")

    async def turn_off(self) -> None:
        raise self._unsupported("power")

    async def set_brightness(self, pct: int) -> None:
        raise self._unsupported("brightness")

    async def set_rgb(self, rgb: tuple[int, int, int]) -> None:
        raise self._unsupported("rgb")

    async def set_color_temp(self, kelvin: int) -> None:
        raise self._unsupported("color-temp")

    async def set_segment_rgb(self, indices: list[int], rgb: tuple[int, int, int]) -> None:
        raise self._unsupported("segment rgb")

    async def set_segment_brightness(self, indices: list[int], pct: int) -> None:
        raise self._unsupported("segment brightness")

    async def set_scene(self, scene_code: int) -> None:
        raise self._unsupported("scenes")

    async def set_scene_full(self, scene_code: int, param_b64: str) -> None:
        raise self._unsupported("scenes")

    async def set_scene_by_name(self, name: str) -> None:
        raise self._unsupported("scenes")

    def zone_is_on(self, zone: str) -> bool | None:
        return None

    async def set_zone_power(self, zone: str, on: bool) -> None:
        raise self._unsupported("zones")

    async def set_zone_rgb(self, zone: str, rgb: tuple[int, int, int]) -> None:
        raise self._unsupported("zones")


class PowerMixin(GoveeDevice):
    """on/off control."""

    async def set_power(self, on: bool) -> None:
        await self._connection.send(controllers.power(on, relay=self._relay_power))
        # Plug relay requires a sync-time follow-up to actuate.
        if self._relay_power:
            await self._connection.send(controllers.sync_time(now_ts()), expect_ack=False)
        self._state.is_on = on
        self._notify_state()

    async def turn_on(self) -> None:
        await self.set_power(True)

    async def turn_off(self) -> None:
        await self.set_power(False)


class BrightnessMixin(GoveeDevice):
    """Brightness control (1..100)."""

    @property
    def brightness(self) -> int | None:
        return self._state.brightness

    async def set_brightness(self, pct: int) -> None:
        await self._connection.send(controllers.brightness(pct))
        self._state.brightness = max(1, min(100, pct))
        self._notify_state()


class RGBMixin(GoveeDevice):
    """Solid RGB color control."""

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return self._state.rgb_color

    async def set_rgb(self, rgb: tuple[int, int, int]) -> None:
        r, g, b = rgb
        await self._connection.send(controllers.rgb(r, g, b, self._color_scheme, self._segments))
        self._state.rgb_color = (r, g, b)
        self._state.color_temp_kelvin = None
        self._state.scene_code = None  # a solid colour exits scene mode
        self._notify_state()


class ColorTempMixin(GoveeDevice):
    """Color-temperature control (Kelvin)."""

    @property
    def color_temp_kelvin(self) -> int | None:
        return self._state.color_temp_kelvin

    async def set_color_temp(self, kelvin: int) -> None:
        await self._connection.send(controllers.color_temp(kelvin, self._color_scheme, self._segments))
        self._state.color_temp_kelvin = kelvin
        self._state.rgb_color = None
        self._state.scene_code = None  # a solid colour exits scene mode
        self._notify_state()


def _mask(indices: list[int] | tuple[int, ...]) -> int:
    mask = 0
    for i in indices:
        mask |= 1 << i
    return mask


class SegmentControl(GoveeDevice):
    """Per-segment RGB/brightness control (segmented strips/ropes/rings)."""

    async def set_segment_rgb(self, indices: list[int], rgb: tuple[int, int, int]) -> None:
        """Set the color of specific segment indices (0-based)."""
        r, g, b = rgb
        await self._connection.send(controllers.segment_rgb(_mask(indices), r, g, b, self._color_scheme))
        self._notify_state()

    async def set_segment_brightness(self, indices: list[int], pct: int) -> None:
        """Set brightness (1..100) on specific segment indices (h60a6 scheme)."""
        await self._connection.send(controllers.segment_brightness(_mask(indices), pct, self._color_scheme))
        self._notify_state()


def _scene_param_is_stub(param_b64: str | None) -> bool:
    """A scene whose library param is a placeholder STUB (0xff at byte[3]).

    Confirmed against a real H60A6 btsnoop capture (78 scenes cycled): the app
    uploads an a3 effect blob ONLY for scenes with a real param (byte[3] !=
    0xff) and *bare-activates* the 0xff-stub scenes (Aurora, Dandelion, …) with
    just 33 05 04 <code>. Uploading a stub corrupts those scenes, so we must
    bare-activate them exactly like the app."""
    if not param_b64:
        return False
    import base64

    try:
        raw = base64.b64decode(param_b64)
    except Exception:  # noqa: BLE001
        return False
    return len(raw) >= 4 and raw[3] == 0xFF


class SceneControl(GoveeDevice):
    """Built-in scene activation.

    The scene code is sent little-endian (33 05 04 <lo> <hi>). Some scenes
    carry a real effect blob that must be uploaded via the a3-chunk burst before
    activating (set_scene_full); the rest (device-built-in, and the 0xff-stub
    "placeholder" scenes) are bare-activated by code (set_scene) — verified
    against the app's own BLE behavior."""

    async def set_scene(self, scene_code: int) -> None:
        """Bare-activate a scene already stored on the device."""
        await self._connection.send(controllers.scene((scene_code & 0xFF, (scene_code >> 8) & 0xFF)))
        # Optimistic (matches the other setters; poll reconciles): reflect the
        # just-activated scene immediately so HA shows the right effect instead
        # of the stale polled one. A scene isn't a solid colour.
        self._state.scene_code = scene_code
        self._state.is_on = True
        self._state.rgb_color = None
        self._state.color_temp_kelvin = None
        self._notify_state()

    async def set_scene_full(self, scene_code: int, param_b64: str) -> None:
        """Upload the scene's effect blob (a3-chunk burst) then activate it —
        correct regardless of whether the device has the scene cached."""
        for chunk in controllers.scene_chunks(param_b64):
            await self._connection.send(chunk, expect_ack=False)
        await self.set_scene(scene_code)

    @property
    def scene_names(self) -> list[str]:
        """Built-in scene names available for this device's SKU."""
        return sorted(load_scenes(self.sku))

    @property
    def active_scene(self) -> str | None:
        """Name of the scene currently active on the device (from the polled
        scene_code), or None if unknown / not in scene mode. Requires a device
        that reads its mode back (StatusReadable)."""
        code = self._state.scene_code
        if code is None:
            return None
        for name, scene in load_scenes(self.sku).items():
            if scene.code == code:
                return name
        return None

    async def set_scene_by_name(self, name: str) -> None:
        """Activate a built-in scene by name (from the bundled catalog).
        Uploads the effect blob when the catalog provides one, else bare-activates."""
        catalog = load_scenes(self.sku)
        scene = catalog.get(name)
        if scene is None:
            raise GoveeBleNotSupported(f"{self.sku}: unknown scene {name!r}")
        # Upload the effect blob only for real params. A 0xff-stub/placeholder
        # param must NOT be uploaded (it corrupts the scene) — the app
        # bare-activates those by code, so we do too.
        if scene.param and not scene.placeholder and not _scene_param_is_stub(scene.param):
            await self.set_scene_full(scene.code, scene.param)
        else:
            await self.set_scene(scene.code)


class ZoneControl(GoveeDevice):
    """Named-zone control for devices with physical zones (e.g. H60A6
    ring/panel). Zone power uses the dedicated 33 30 command; zone color uses
    the segment mask of the zone's segments."""

    def _zone(self, name: str) -> Zone:
        for z in self.zones:
            if z.name == name:
                return z
        raise GoveeBleNotSupported(f"{self.sku}: unknown zone {name!r}; have {[z.name for z in self.zones]}")

    def zone_is_on(self, zone: str) -> bool | None:
        """Read-back power state of a named zone (from the polled status), or
        None if unknown / not read back."""
        return self._state.zone_power.get(self._zone(zone).power_index)

    async def set_zone_power(self, zone: str, on: bool) -> None:
        z = self._zone(zone)
        await self._connection.send(controllers.zone_power(z.power_index, on))
        # Optimistic until the next poll confirms it.
        self._state.zone_power[z.power_index] = on
        self._notify_state()

    async def set_zone_rgb(self, zone: str, rgb: tuple[int, int, int]) -> None:
        z = self._zone(zone)
        if not z.segments:
            raise GoveeBleNotSupported(f"{self.sku}: zone {zone!r} has no segment mapping")
        r, g, b = rgb
        await self._connection.send(controllers.segment_rgb(_mask(z.segments), r, g, b, self._color_scheme))
        self._notify_state()


class BarSwitchControl(ZoneControl):
    """Zone control for the H6047's two light bars (left = power_index 0, right =
    1). The H6047 carries BOTH bar states in one `33 36 <left> <right>` frame
    (com.govee.h6047 NewDetailVm.I5), so toggling one bar re-sends the other
    bar's last-known state. State is tracked optimistically (the write fully
    determines both bars); a failed send doesn't commit, so HA reverts."""

    async def set_zone_power(self, zone: str, on: bool) -> None:
        z = self._zone(zone)
        zp = self._state.zone_power
        # Intended state of both bars: unknown bars default to on (a fresh device
        # powers both on), then apply the requested change.
        target = {0: zp.get(0, True), 1: zp.get(1, True)}
        target[z.power_index] = on
        await self._connection.send(controllers.bar_switch(target[0], target[1]))
        zp.update(target)
        self._notify_state()

    async def _read_state(self) -> None:
        # Poll power/brightness/scene via the sibling PolledLight, then read the
        # two bar states back from aa 36 (verified live: reply aa 36 <l> <r>).
        await super()._read_state()
        if self._state.is_on is False:
            self._state.zone_power = {0: False, 1: False}
            return
        reply = await self._read_reply(controllers.bar_switch_query(), controllers.CMD_BAR_SWITCH)
        bars = status.parse_bar_switch(reply or b"")
        if bars is not None:
            self._state.zone_power = {0: bars[0], 1: bars[1]}


class StatusReadable(GoveeDevice):
    """Devices that report state via the 0xAC status burst (H60A6 family).

    A full status query returns brightness, zone on/off, and per-segment colour,
    letting `update()` reflect changes made from the Govee app / physical
    control rather than only the last command this library sent."""

    async def _read_state(self) -> None:
        frames = await self._connection.query(
            controllers.status_query(full=True), timeout=5.0
        )
        chunks: dict[int, bytes] = {}
        for frame in frames:
            if len(frame) == 20 and frame[0] == 0xAC:
                chunks[frame[1]] = frame[2:19]
        # Parse as soon as we have the useful data: chunk 0x00 (brightness) and a
        # terminator chunk (0x05 in the full burst, else 0xFF) carrying zone
        # on/off. The 0xFF tail is only needed for the last segment's colour, so
        # don't gate the whole read on it — brightness + on/off come first.
        if 0x00 not in chunks or not (0x05 in chunks or 0xFF in chunks):
            _LOGGER.debug("%s: incomplete status read (chunks %s)", self.address, sorted(chunks))
            return
        parsed = status.parse_status(chunks, self.address)
        if parsed.is_on is not None:
            self._state.is_on = parsed.is_on
        if parsed.zone_power:
            self._state.zone_power = parsed.zone_power
        if parsed.brightness is not None:
            self._state.brightness = parsed.brightness
        if parsed.segments:
            self._state.segments = parsed.segments
        if parsed.rgb_color is not None:
            self._state.rgb_color = parsed.rgb_color
            self._state.color_temp_kelvin = None
        # Hardware version + wifi MAC come from the status stream (works for
        # BLE-only devices like the H60A6).
        if parsed.hardware_version:
            self._state.hardware_version = parsed.hardware_version
        if parsed.wifi_mac:
            self._state.wifi_mac = parsed.wifi_mac
        self._state.optimistic = False

        # Active scene: read the current mode (aa 05 01); scene_code is set when
        # the device is in scene sub-mode, else cleared (it's in color/music).
        if Capability.SCENES in self.capabilities:
            reply = await self._read_reply(controllers.mode_query(), 0x05)
            if reply is not None:
                self._state.scene_code = status.parse_active_scene(reply)

        # Overall power gates everything: when the device is off, no zone,
        # segment, colour or scene is lit — force them off rather than reporting
        # stale last-on values.
        if self._state.is_on is False:
            self._state.zone_power = {z.power_index: False for z in self.zones}
            self._state.segments = []
            self._state.rgb_color = None
            self._state.scene_code = None

        await self._read_device_info()

    async def _read_device_info(self) -> None:
        """Read static device info that the status stream doesn't carry, via
        commandType 0x07 (single-frame aa 07 <sel>): 0x02 = per-device serial/UID
        (SnController), 0x11 = wifi MAC + software/hardware version
        (BasicWifiInfoController; non-zero only on WiFi devices, and the source
        of firmware version). Capped at a few attempts (it's static)."""
        attempts = getattr(self, "_info_attempts", 0)
        need_serial = self._state.serial_number is None
        need_wifi = self._state.firmware_version is None
        if attempts >= 3 or not (need_serial or need_wifi):
            return
        self._info_attempts = attempts + 1

        if need_serial:
            reply = await self._read_reply(controllers.device_info_query(0x02), 0x07)
            serial = status.parse_sn(reply or b"")
            if serial:
                self._state.serial_number = serial
        if need_wifi:
            reply = await self._read_reply(controllers.device_info_query(0x11), 0x07)
            wifi = status.parse_wifi_info(reply or b"")
            if wifi is not None:
                wifi_mac, software, hardware = wifi
                if wifi_mac != "00:00:00:00:00:00":
                    self._state.wifi_mac = wifi_mac
                if software != "0.00.00":
                    self._state.firmware_version = software
                if hardware != "0.00.00" and not self._state.hardware_version:
                    self._state.hardware_version = hardware


class PolledLight(GoveeDevice):
    """Light families that report state via the simple single-frame reads shared
    across the app's light modules (Compose4InfoBleIot): aa 01 (power),
    aa 04 (brightness), aa 05 (mode -> active scene). on/off also arrives
    passively from advertisements, so a device the advert says is off is not
    connected to at all.

    RGB / color-temperature are NOT polled: the connected colour-read parse is
    scheme-specific and inconsistent across families (verified in the app), so
    those stay optimistic (set from the last command)."""

    async def _read_state(self) -> None:
        # If the passive advertisement already says the device is off, there is
        # nothing lit to read — skip the connection entirely (slot-friendly).
        if self._state.is_on is False:
            self._state.scene_code = None
            return

        power = status.parse_power(await self._read_reply(controllers.power_query(), 0x01) or b"")
        if power is not None:
            self._state.is_on = power
        bright = status.parse_brightness(
            await self._read_reply(controllers.brightness_query(), 0x04) or b""
        )
        if bright is not None:
            self._state.brightness = bright
        if Capability.SCENES in self.capabilities:
            reply = await self._read_reply(controllers.mode_query(), 0x05)
            if reply is not None:
                self._state.scene_code = status.parse_active_scene(reply)
        self._state.optimistic = False

        if self._state.is_on is False:
            self._state.scene_code = None
