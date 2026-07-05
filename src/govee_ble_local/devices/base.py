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
from ..models import Capability, DeviceState
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
        self._connection = GoveeConnection(
            ble_device,
            unlock_frames=self._unlock_frames if self.requires_secret else None,
        )

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
