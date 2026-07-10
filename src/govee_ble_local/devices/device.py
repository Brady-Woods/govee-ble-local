"""Capability-driven device — one class, configured by a :class:`DeviceProfile`.

Replaces the v2 mixin hierarchy: behaviour is gated by ``profile.capabilities`` and
dispatched to the spec-first :mod:`..wire` layer (build / parse / reassemble) over the
kept :class:`~..transport.connection.GoveeConnection`.
"""
from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable
from typing import Any

from bleak.backends.device import BLEDevice

from ..exceptions import GoveeBleNotSupported
from ..identify import identify, parse_broadcast_onoff
from ..models import Capability, DeviceState, Encryption
from ..scenes import Scene, load_scenes, scene_upload_params
from ..transport.connection import GoveeConnection, now_ts
from ..wire import build, parse, reassemble
from .profile import DeviceProfile, profile_for

_LOGGER = logging.getLogger(__name__)

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
        frame_log: str | None = None,
    ) -> None:
        self.profile = profile
        self._ble_device = ble_device
        self._advertisement = advertisement_data
        self._sku = (sku or profile.skus[0]).upper()
        self._secret = secret
        self._state = DeviceState(optimistic=True)
        self._device_info_read = False   # device-info (mac/hw/fw/sn) read once, lazily
        self._polled_once = False        # for the one-time INFO "first poll" line
        self._callbacks: list[StateCallback] = []
        enc = self._resolve_encryption(advertisement_data)
        self._conn = GoveeConnection(
            ble_device,
            encryption=enc,
            unlock_frames=self._unlock_frames if profile.requires_secret else None,
            frame_log=frame_log,
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

    def ingest_advertisement(self, advertisement_data: Any) -> bool:
        """Update passive state from a BLE advertisement — no connection needed.

        Reads on/off from the Govee manufacturer data (identify.parse_broadcast_onoff);
        returns True if `state.is_on` changed (so a caller can notify selectively).
        Restores the v2 device-level ingest so consumers don't parse adverts inline."""
        self._advertisement = advertisement_data
        mfg = getattr(advertisement_data, "manufacturer_data", None)
        if not mfg:
            return False
        on = parse_broadcast_onoff(mfg)
        if on is None or on == self._state.is_on:
            return False
        self._state.is_on = on
        self._notify()
        return True

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

    async def set_segment_color_temp(self, indices: list[int], kelvin: int) -> None:
        """Colour temperature on selected segments (0x15 family only — the CCT frame
        carries the segment mask). Raises GoveeBleNotSupported for non-maskable schemes."""
        self._require(Capability.SEGMENTS)
        self._require(Capability.COLOR_TEMP)
        try:
            frame = build.segment_color_temp(_mask(indices), kelvin, self.profile.color_scheme)
        except ValueError as exc:
            raise GoveeBleNotSupported(f"{self._sku}: {exc}") from exc
        await self._conn.send(frame)
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

    async def set_zone_color_temp(self, name: str, kelvin: int) -> None:
        """Colour temperature for one zone (its segment mask). Physically supported on the
        0x15 family (H60A6/H6047), whose CCT frame carries a segment mask."""
        self._require(Capability.COLOR_TEMP)
        z = self._zone(name)
        if not z.segments:
            raise GoveeBleNotSupported(f"{self._sku}: zone {name!r} has no segments")
        try:
            frame = build.segment_color_temp(_mask(z.segments), kelvin, self.profile.color_scheme)
        except ValueError as exc:
            raise GoveeBleNotSupported(f"{self._sku}: {exc}") from exc
        await self._conn.send(frame)
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
        elif self.profile.readback == "plug":
            await self._read_plug()
        # Segment / single-colour read-back layered on top (spec Change 7).
        if self.profile.color_readback == "mechanism_b":
            await self._read_mechanism_b()
        elif self.profile.color_readback == "mechanism_c":
            await self._read_mechanism_c()
        # Device-info (mac/hw/fw/serial) — static-ish, read once on the first good poll.
        if not self._device_info_read and self.profile.readback != "none":
            await self._read_device_info()
        if not self._polled_once and self.profile.readback != "none":
            _LOGGER.info("%s (%s) first poll: %s", self.address, self._sku, self._state_summary())
            self._polled_once = True
        elif _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug("%s (%s) state: %s", self.address, self._sku, self._state_summary())
        self._notify()
        return self._state

    def _state_summary(self) -> str:
        s = self._state
        return (
            f"on={s.is_on} bri={s.brightness} rgb={s.rgb_color} kelvin={s.color_temp_kelvin} "
            f"segs={len(s.segments)} zones={s.zone_power} scene={s.scene_code}"
        )

    async def _read_plug(self) -> None:
        """Poll a plug's relay state (aa 01 -> raw relay bitmask; any bit set = on),
        so state.is_on reflects the device rather than the last command sent."""
        power = parse.parse_power(await self._read_reply(build.power_query(), 0x01) or b"")
        if power is not None:
            self._state.is_on = bool(power)
            self._state.optimistic = False

    async def _read_device_info(self) -> None:
        """Populate the static device-info fields from the aa 07 replies. Selector-aware:
        device fw/hw + serial come from BASIC (0x10); wifi_mac from WIFI (0x11) — whose
        sw/hw are the *wifi-module* versions and must NOT clobber the device's; SN (0x02)
        is only a serial fallback. Best-effort + once; unanswered selectors leave fields
        None. ble_mac stays None unless a reply carries a MAC distinct from the connectable
        address (none confirmed yet — the connectable address is otherwise the BLE MAC)."""
        self._device_info_read = True   # don't retry every poll even if it yields nothing
        basic = parse.parse_device_info(
            await self._read_reply(build.device_info_query(0x10), 0x07) or b"")
        if basic is not None:
            if basic.serial is not None:
                self._state.serial_number = basic.serial
            if basic.sw_version is not None:
                self._state.firmware_version = basic.sw_version
            if basic.hw_version is not None:
                self._state.hardware_version = basic.hw_version
        wifi = parse.parse_device_info(
            await self._read_reply(build.device_info_query(0x11), 0x07) or b"")
        if wifi is not None and wifi.wifi_mac is not None:
            self._state.wifi_mac = wifi.wifi_mac
        if self._state.serial_number is None:   # SN read only if basic didn't carry it
            sn = parse.parse_device_info(
                await self._read_reply(build.device_info_query(0x02), 0x07) or b"")
            if sn is not None and sn.serial is not None:
                self._state.serial_number = sn.serial

    async def _read_mechanism_b(self) -> None:
        """H61A8 per-segment colour: request each 0xA5 (V2) batch (AA A5 <seq>) and
        assemble positional segments. per_batch/batch-count are client constants
        (not frame-encoded); modeled from source, not yet live-verified."""
        per_batch = self.profile.color_readback_per_batch or 3
        batch_count = -(-self.profile.segments // per_batch)  # ceil
        batches: list[tuple[int, list[tuple[int | None, int, int, int]]]] = []
        for seq in range(1, batch_count + 1):
            reply = await self._read_reply(build.bulb_group_query(seq, v2=True), 0xA5)
            parsed = parse.parse_bulb_group_batch(reply) if reply else None
            if parsed is not None:
                batches.append(parsed)
        segs = parse.bulb_groups_to_segments(batches, per_batch)
        if segs:
            self._state.segments = segs

    async def _read_mechanism_c(self) -> None:
        """H6052 single colour from the 0x05 sub-mode 0x0D mode report."""
        reply = await self._read_reply(build.mode_query(), 0x05)
        rgb = parse.parse_mode_color_0d(reply) if reply else None
        if rgb is not None:
            self._state.rgb_color = rgb

    async def _read_status(self) -> None:
        frames = await self._conn.query(build.status_query(full=True), timeout=5.0)
        ac = [f for f in frames if f and f[0] == 0xAC]
        st = reassemble.parse_status(ac)
        if st.is_on is None and not st.segments and not st.zone_power:
            # The device answered (or didn't) but no usable TLVs came back — state left
            # stale. This is the signal for "mechanism-A doesn't work here" (e.g. H6047).
            _LOGGER.warning(
                "%s (%s): status read returned no usable data (%d 0xAC of %d frames); state left stale",
                self.address, self._sku, len(ac), len(frames),
            )
        if st.is_on is not None:
            self._state.is_on = st.is_on
        if st.brightness is not None:
            self._state.brightness = st.brightness if st.brightness <= 100 else round(st.brightness / 255 * 100)
        if st.zone_power:
            self._state.zone_power = st.zone_power
        if st.segments:
            self._state.segments = st.segments
        # Device-info (serial / wifi_mac / fw / hw) comes from the 0x07 TLV in the 0xAC
        # stream — the ONLY source for BLE-only devices (H60A6); aa 07 returns zeros there.
        if st.serial_number is not None:
            self._state.serial_number = st.serial_number
        if st.wifi_mac is not None:
            self._state.wifi_mac = st.wifi_mac
        if st.firmware_version is not None:
            self._state.firmware_version = st.firmware_version
        if st.hardware_version is not None:
            self._state.hardware_version = st.hardware_version
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

    async def read_secret(self) -> bytes | None:
        """Read the 8-byte account-lock secret off the device (aa b1), or None.

        For the SETUP/bootstrap case: an unbound plug returns its secret without one
        being supplied, so call this BEFORE set_secret() (while `_secret` is None the
        post-handshake unlock step sends nothing, avoiding the secret chicken-and-egg).
        Restores the v2 auto-read; the frame builder is build.secret_read()."""
        reply = await self._read_reply(build.secret_read(), 0xB1)
        return parse.parse_secret(reply) if reply else None


def make_device(
    ble_device: BLEDevice,
    sku: str,
    advertisement_data: Any | None = None,
    *,
    secret: bytes | None = None,
    frame_log: str | None = None,
) -> Device | None:
    """Build a capability-driven Device for `sku`, or None if unsupported."""
    p = profile_for(sku)
    if p is None:
        return None
    return Device(p, ble_device, advertisement_data, sku=sku, secret=secret, frame_log=frame_log)
