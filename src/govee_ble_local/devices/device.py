"""Capability-driven device — one class, configured by a :class:`DeviceProfile`.

Replaces the v2 mixin hierarchy: behaviour is gated by ``profile.capabilities`` and
dispatched to the spec-first :mod:`..wire` layer (build / parse / reassemble) over the
kept :class:`~..transport.connection.GoveeConnection`.
"""
from __future__ import annotations

import base64
import time
from collections.abc import Callable
from typing import Any

from bleak.backends.device import BLEDevice

from ..exceptions import GoveeBleNotSupported
from ..identify import identify
from ..models import Capability, DeviceState, Encryption
from ..scenes import Scene, load_scenes, scene_upload_params
from ..transport.connection import GoveeConnection, now_ts
from ..wire import build, parse, reassemble
from .profile import DeviceProfile, profile_for

StateCallback = Callable[[DeviceState], None]


def _local_tz(unix_ts: int) -> tuple[int, int]:
    off = time.localtime(unix_ts).tm_gmtoff or 0
    sign = -1 if off < 0 else 1
    off = abs(off)
    return sign * (off // 3600), sign * ((off % 3600) // 60)


def _mask(indices: list[int] | tuple[int, ...]) -> int:
    m = 0
    for i in indices:
        m |= 1 << i
    return m


class Device:
    """A Govee BLE device driven entirely by its ``DeviceProfile``."""

    def __init__(
        self,
        profile: DeviceProfile,
        ble_device: BLEDevice,
        advertisement_data: Any | None = None,
        *,
        sku: str | None = None,
        secret: bytes | None = None,
    ) -> None:
        self.profile = profile
        self._ble_device = ble_device
        self._advertisement = advertisement_data
        self._sku = (sku or profile.skus[0]).upper()
        self._secret = secret
        self._state = DeviceState(optimistic=True)
        self._callbacks: list[StateCallback] = []
        enc = self._resolve_encryption(advertisement_data)
        self._conn = GoveeConnection(
            ble_device,
            encryption=enc,
            unlock_frames=self._unlock_frames if profile.requires_secret else None,
        )

    def _resolve_encryption(self, adv: Any | None) -> Encryption:
        mfg = getattr(adv, "manufacturer_data", None)
        name = getattr(adv, "local_name", None) or self._ble_device.name
        if mfg:
            a = identify(name, mfg)
            if a is not None:
                return Encryption.AES_RC4_PSK if a.encrypted else Encryption.NONE
        return self.profile.encryption

    # -- identity / state ---------------------------------------------------
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
    def capabilities(self) -> frozenset[Capability]:
        return self.profile.capabilities

    @property
    def zones(self) -> tuple:  # type: ignore[type-arg]
        return self.profile.zones

    @property
    def min_kelvin(self) -> int | None:
        return self.profile.min_kelvin

    @property
    def max_kelvin(self) -> int | None:
        return self.profile.max_kelvin

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def is_on(self) -> bool | None:
        return self._state.is_on

    @property
    def scene_names(self) -> list[str]:
        return sorted(load_scenes(self._sku)) if Capability.SCENES in self.capabilities else []

    @property
    def active_scene(self) -> str | None:
        code = self._state.scene_code
        if code is None:
            return None
        for name, sc in load_scenes(self._sku).items():
            if sc.code == code:
                return name
        return None

    def register_callback(self, cb: StateCallback) -> Callable[[], None]:
        self._callbacks.append(cb)
        return lambda: self._callbacks.remove(cb) if cb in self._callbacks else None

    def _notify(self) -> None:
        for cb in list(self._callbacks):
            cb(self._state)

    def _require(self, cap: Capability) -> None:
        if cap not in self.profile.capabilities:
            raise GoveeBleNotSupported(f"{self._sku}: {cap.name} not supported")

    def _unlock_frames(self) -> list[bytes]:
        return [build.secret_check(self._secret)] if self._secret else []

    def set_secret(self, secret: bytes) -> None:
        self._secret = secret

    def update_ble_device(self, ble_device: BLEDevice) -> None:
        self._ble_device = ble_device
        self._conn.update_ble_device(ble_device)

    async def stop(self) -> None:
        await self._conn.disconnect()

    # -- power / brightness -------------------------------------------------
    async def set_power(self, on: bool) -> None:
        self._require(Capability.POWER)
        await self._conn.send(build.switch(on, relay=self.profile.relay))
        if self.profile.relay:  # plug relay needs a sync-time follow-up to actuate
            ts = now_ts()
            await self._conn.send(build.plug_sync_time(ts, *_local_tz(ts)), expect_ack=False)
        self._state.is_on = on
        self._notify()

    async def turn_on(self) -> None:
        await self.set_power(True)

    async def turn_off(self) -> None:
        await self.set_power(False)

    async def set_brightness(self, pct: int) -> None:
        self._require(Capability.BRIGHTNESS)
        await self._conn.send(build.brightness(pct))
        self._state.brightness = max(1, min(100, pct))
        self._notify()

    # -- colour -------------------------------------------------------------
    async def set_rgb(self, rgb: tuple[int, int, int]) -> None:
        self._require(Capability.RGB)
        r, g, b = rgb
        await self._conn.send(build.color_rgb(r, g, b, self.profile.color_scheme, self.profile.segments))
        self._state.rgb_color = (r, g, b)
        self._state.color_temp_kelvin = None
        self._state.scene_code = None
        self._notify()

    async def set_color_temp(self, kelvin: int) -> None:
        self._require(Capability.COLOR_TEMP)
        await self._conn.send(build.color_temp(kelvin, self.profile.color_scheme, self.profile.segments))
        self._state.color_temp_kelvin = kelvin
        self._state.rgb_color = None
        self._state.scene_code = None
        self._notify()

    async def set_segment_rgb(self, indices: list[int], rgb: tuple[int, int, int]) -> None:
        self._require(Capability.SEGMENTS)
        r, g, b = rgb
        await self._conn.send(build.segment_rgb(_mask(indices), r, g, b, self.profile.color_scheme))
        self._notify()

    async def set_segment_brightness(self, indices: list[int], pct: int) -> None:
        self._require(Capability.SEGMENTS)
        await self._conn.send(build.segment_brightness(_mask(indices), pct, self.profile.color_scheme))
        self._notify()

    # -- zones --------------------------------------------------------------
    def _zone(self, name: str) -> Any:
        for z in self.profile.zones:
            if z.name == name:
                return z
        raise GoveeBleNotSupported(f"{self._sku}: unknown zone {name!r}")

    def zone_is_on(self, name: str) -> bool | None:
        return self._state.zone_power.get(self._zone(name).power_index)

    async def set_zone_power(self, name: str, on: bool) -> None:
        z = self._zone(name)
        if self.profile.bar_switch:
            zp = self._state.zone_power
            target = {0: zp.get(0, True), 1: zp.get(1, True)}
            target[z.power_index] = on
            await self._conn.send(build.bar_switch(bool(target[0]), bool(target[1])))
            zp.update(target)
        else:
            await self._conn.send(build.zone_power(z.power_index, on))
            self._state.zone_power[z.power_index] = on
        self._notify()

    async def set_zone_rgb(self, name: str, rgb: tuple[int, int, int]) -> None:
        z = self._zone(name)
        if not z.segments:
            raise GoveeBleNotSupported(f"{self._sku}: zone {name!r} has no segments")
        r, g, b = rgb
        await self._conn.send(build.segment_rgb(_mask(z.segments), r, g, b, self.profile.color_scheme))
        self._notify()

    # -- scenes -------------------------------------------------------------
    async def set_scene(self, code: int) -> None:
        self._require(Capability.SCENES)
        await self._conn.send(build.scene_activate(code))
        self._state.scene_code = code
        self._state.is_on = True
        self._state.rgb_color = None
        self._state.color_temp_kelvin = None
        self._notify()

    def _scene_upload_frames(self, scene: Scene) -> list[bytes] | None:
        param = scene.param
        if not param:
            return None
        st = scene.scene_type
        dialect = self.profile.scene_dialect
        if dialect == "B_h60a6" and st == 5:
            value = base64.b64decode(param)[1:]
            if len(value) < 2:
                return None
            is_diy = (value[0] | (value[1] << 8)) + 2 == len(value)
            return (build.scene_upload_a3(value, build.COMM_H60A6) if is_diy
                    else build.scene_upload_a4_mtu(value, build.COMM_H60A6))
        if dialect == "B_h6052" and st == 5:
            raw = base64.b64decode(param)
            if raw and raw[0] == 0x13:
                return build.scene_upload_a3(raw[1:], build.COMM_H6052_GRAFFITI)
            return None
        sel = scene_upload_params(st, self.profile.scene_versions)
        if sel is None:
            return None
        comm_byte, strip = sel
        return build.scene_upload_a3(base64.b64decode(param)[strip:], comm_byte)

    async def set_scene_by_name(self, name: str) -> None:
        self._require(Capability.SCENES)
        scene = load_scenes(self._sku).get(name)
        if scene is None:
            raise GoveeBleNotSupported(f"{self._sku}: unknown scene {name!r}")
        frames = self._scene_upload_frames(scene)
        if frames is not None:
            for chunk in frames:
                await self._conn.send(chunk, expect_ack=False)
        await self.set_scene(scene.code)

    # -- read-back ----------------------------------------------------------
    async def update(self) -> DeviceState:
        if self.profile.readback == "status":
            await self._read_status()
        elif self.profile.readback == "polled":
            await self._read_polled()
        self._notify()
        return self._state

    async def _read_status(self) -> None:
        frames = await self._conn.query(build.status_query(full=True), timeout=5.0)
        st = reassemble.parse_status([f for f in frames if f and f[0] == 0xAC])
        if st.is_on is not None:
            self._state.is_on = st.is_on
        if st.brightness is not None:
            self._state.brightness = st.brightness if st.brightness <= 100 else round(st.brightness / 255 * 100)
        if st.zone_power:
            self._state.zone_power = st.zone_power
        if st.segments:
            self._state.segments = st.segments
        if Capability.SCENES in self.capabilities:
            reply = await self._read_reply(build.mode_query(), 0x05)
            if reply is not None:
                self._state.scene_code = parse.parse_active_scene(reply)
        self._state.optimistic = False

    async def _read_polled(self) -> None:
        if self._state.is_on is False:
            return
        power = parse.parse_power(await self._read_reply(build.power_query(), 0x01) or b"")
        if power is not None:
            self._state.is_on = bool(power)
        raw = parse.parse_brightness(await self._read_reply(build.brightness_query(), 0x04) or b"")
        if raw is not None:
            self._state.brightness = raw if raw <= 100 else round(raw / 255 * 100)
        if Capability.SCENES in self.capabilities:
            reply = await self._read_reply(build.mode_query(), 0x05)
            if reply is not None:
                self._state.scene_code = parse.parse_active_scene(reply)
        self._state.optimistic = False

    async def _read_reply(self, frame: bytes, command_type: int) -> bytes | None:
        frames = await self._conn.query(frame, opcode=0xAA, terminal=command_type, timeout=2.0)
        for reply in frames:
            if len(reply) >= 2 and reply[0] == 0xAA and reply[1] == command_type:
                return reply
        return None


def make_device(
    ble_device: BLEDevice,
    sku: str,
    advertisement_data: Any | None = None,
    *,
    secret: bytes | None = None,
) -> Device | None:
    """Build a capability-driven Device for `sku`, or None if unsupported."""
    p = profile_for(sku)
    return None if p is None else Device(p, ble_device, advertisement_data, sku=sku, secret=secret)
