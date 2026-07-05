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

from ..ble import controllers
from ..ble.controllers import ColorScheme
from ..identify import identify
from ..exceptions import GoveeBleNotSupported
from ..models import Capability, DeviceState, Encryption, Zone
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
        self._connection = GoveeConnection(
            ble_device,
            encryption=self._resolve_encryption(advertisement_data),
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

    # -- lifecycle ----------------------------------------------------------

    def set_secret(self, secret: bytes) -> None:
        self._secret = secret

    def update_ble_device(self, ble_device: BLEDevice, advertisement_data: Any | None = None) -> None:
        self._ble_device = ble_device
        if advertisement_data is not None:
            self._advertisement = advertisement_data
        self._connection.update_ble_device(ble_device)

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
        """Refresh device state. Devices without read-back just ensure the
        connection is alive and return the optimistic state."""
        await self._connection.connect()
        return self._state

    async def stop(self) -> None:
        await self._connection.disconnect()

    # -- internal helpers ---------------------------------------------------

    def _unlock_frames(self) -> list[bytes]:
        """Frames sent right after each handshake for secret-gated devices."""
        if self._secret is None:
            _LOGGER.warning("%s: secret required but none set; commands will fail", self.address)
            return []
        return [controllers.secret_check(self._secret)]


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


class ZoneControl(GoveeDevice):
    """Named-zone control for devices with physical zones (e.g. H60A6
    ring/panel). Zone power uses the dedicated 33 30 command; zone color uses
    the segment mask of the zone's segments."""

    def _zone(self, name: str) -> Zone:
        for z in self.zones:
            if z.name == name:
                return z
        raise GoveeBleNotSupported(f"{self.sku}: unknown zone {name!r}; have {[z.name for z in self.zones]}")

    async def set_zone_power(self, zone: str, on: bool) -> None:
        await self._connection.send(controllers.zone_power(self._zone(zone).power_index, on))
        self._notify_state()

    async def set_zone_rgb(self, zone: str, rgb: tuple[int, int, int]) -> None:
        z = self._zone(zone)
        if not z.segments:
            raise GoveeBleNotSupported(f"{self.sku}: zone {zone!r} has no segment mapping")
        r, g, b = rgb
        await self._connection.send(controllers.segment_rgb(_mask(z.segments), r, g, b, self._color_scheme))
        self._notify_state()
