"""Capability-driven device — one class, configured by a :class:`DeviceProfile`.

Replaces the v2 mixin hierarchy: behaviour is gated by ``profile.capabilities`` and
dispatched to the spec-first :mod:`..wire` layer (build / parse / reassemble) over the
kept :class:`~..transport.connection.GoveeConnection`.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Never

from bleak.backends.device import BLEDevice

from ..const import INFO_READ_TIMEOUT, STATUS_READ_TIMEOUT
from ..exceptions import GoveeBleError, GoveeBleInvalidValue, GoveeBleNotSupported
from ..identify import identify, parse_broadcast_onoff
from ..models import Capability, DeviceState, Encryption, Segment
from ..scenes import Scene, load_scenes, scene_names_by_code, scene_upload_frames
from ..transport.connection import GoveeConnection
from ..wire import build, parse, reassemble
from .profile import DeviceProfile, profile_for

_LOGGER = logging.getLogger(__name__)

# After a command or an authoritative read, ignore the advertised on/off for this
# long: Govee refreshes the broadcast on/off byte lazily, so a stale advertisement
# arriving right after a toggle would otherwise flip the state straight back.
ADVERT_SUPPRESS_SECONDS = 15.0

StateCallback = Callable[[DeviceState], None]


def _local_tz(unix_ts: int) -> tuple[int, int]:
    off = time.localtime(unix_ts).tm_gmtoff or 0
    sign = -1 if off < 0 else 1
    off = abs(off)
    return sign * (off // 3600), sign * ((off % 3600) // 60)


_MAX_SEGMENT_INDEX = 16   # the segment mask is a u16


def _unsupported_readback(value: Never) -> Never:
    """Exhaustiveness guard: mypy proves every ``ReadBack`` value is handled; at runtime
    (a profile mutated past its construction-time validation) raise a library error."""
    raise GoveeBleError(f"unsupported readback {value!r}")


def _mask(indices: list[int] | tuple[int, ...]) -> int:
    m = 0
    for i in indices:
        if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < _MAX_SEGMENT_INDEX:
            raise GoveeBleInvalidValue(
                f"segment index must be an integer in 0..{_MAX_SEGMENT_INDEX - 1}, got {i!r}")
        m |= 1 << i
    return m


def _chunk_indices(ac_frames: list[bytes]) -> str:
    """Format the chunk-index byte (frame[1]) of each received 0xAC frame, for diagnosing a
    dropped/truncated status burst (e.g. ``0x00,0x01,0x02,0x04,0xff`` — index 3 missing)."""
    if not ac_frames:
        return "<none>"
    idx = sorted({f[1] for f in ac_frames if len(f) >= 2})
    return ",".join(f"0x{i:02x}" for i in idx)


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
        idle_disconnect: float | None = None,
    ) -> None:
        self.profile = profile
        self._ble_device = ble_device
        self._sku = (sku or profile.skus[0]).upper()
        self._secret = self._checked_secret(secret)
        self._state = DeviceState(optimistic=True)
        self._device_info_read = False   # device-info (mac/hw/fw/sn) read once, lazily
        self._polled_once = False        # for the one-time INFO "first poll" line
        self._callbacks: list[StateCallback] = []
        self._advert_ignore_until = 0.0   # monotonic; see ADVERT_SUPPRESS_SECONDS
        enc = self._resolve_encryption(advertisement_data)
        conn_kw: dict[str, Any] = {}
        if idle_disconnect is not None:
            conn_kw["idle_disconnect"] = idle_disconnect
        self._conn = GoveeConnection(
            ble_device,
            encryption=enc,
            unlock_frames=self._unlock_frames if profile.requires_secret else None,
            frame_log=frame_log,
            **conn_kw,
        )

    def _resolve_encryption(self, adv: Any | None) -> Encryption:
        mfg = getattr(adv, "manufacturer_data", None)
        name = getattr(adv, "local_name", None) or self._ble_device.name
        if mfg:
            a = identify(name, mfg)
            # pact_type -1 = Govee-named but unparseable pact info: unknown, not "plaintext".
            if a is not None and a.pact_type != -1:
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
    def encryption(self) -> Encryption:
        """The command-channel mode in use (advertisement + BGC refined)."""
        return self._conn.encryption

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
        return scene_names_by_code(self._sku).get(code)

    def register_callback(self, cb: StateCallback) -> Callable[[], None]:
        self._callbacks.append(cb)
        return lambda: self._callbacks.remove(cb) if cb in self._callbacks else None

    def _notify(self) -> None:
        for cb in list(self._callbacks):
            try:
                cb(self._state)
            except Exception:
                _LOGGER.warning(
                    "%s (%s): state callback %s raised; continuing",
                    self.address, self._sku, getattr(cb, "__qualname__", repr(cb)), exc_info=True)

    def _require(self, cap: Capability) -> None:
        if cap not in self.profile.capabilities:
            raise GoveeBleNotSupported(f"{self._sku}: {cap.name} not supported")

    def _unlock_frames(self) -> list[bytes]:
        return [build.secret_check(self._secret)] if self._secret else []

    @staticmethod
    def _checked_secret(secret: bytes | None) -> bytes | None:
        if secret is not None and len(secret) != build.SECRET_LEN:
            raise GoveeBleInvalidValue(
                f"secret must be exactly {build.SECRET_LEN} bytes, got {len(secret)}")
        return secret

    def set_secret(self, secret: bytes) -> None:
        self._secret = self._checked_secret(secret)

    def _check_kelvin(self, kelvin: int) -> None:
        lo, hi = self.profile.min_kelvin, self.profile.max_kelvin
        if (lo is not None and kelvin < lo) or (hi is not None and kelvin > hi):
            raise GoveeBleInvalidValue(
                f"{self._sku}: kelvin must be within {lo}..{hi}, got {kelvin!r}")

    def update_ble_device(self, ble_device: BLEDevice) -> None:
        self._ble_device = ble_device
        self._conn.update_ble_device(ble_device)

    def ingest_advertisement(self, advertisement_data: Any) -> bool:
        """Update passive state from a BLE advertisement — no connection needed.

        Reads on/off from the Govee manufacturer data (identify.parse_broadcast_onoff);
        returns True if `state.is_on` changed (so a caller can notify selectively).
        Restores the v2 device-level ingest so consumers don't parse adverts inline."""
        mfg = getattr(advertisement_data, "manufacturer_data", None)
        if not mfg:
            return False
        on = parse_broadcast_onoff(mfg)
        if on is None or on == self._state.is_on:
            return False
        if time.monotonic() < self._advert_ignore_until:
            return False  # fresher state from a command/read; advert lags
        self._state.is_on = on
        self._notify()
        return True

    async def connect(self) -> None:
        """Establish the BLE session now (connect, handshake, unlock) instead of lazily on
        the first command. Idempotent; the connection's idle timer still applies afterwards."""
        await self._conn.connect()

    async def stop(self) -> None:
        """Disconnect and refuse further I/O (the owner is being torn down)."""
        await self._conn.close()

    def _mark_authoritative(self) -> None:
        self._advert_ignore_until = time.monotonic() + ADVERT_SUPPRESS_SECONDS

    def _brightness_pct(self, raw: int) -> int:
        full = self.profile.brightness_max
        return raw if full == 100 else round(raw * 100 / full)

    # -- power / brightness -------------------------------------------------
    def _power_frames(self, on: bool) -> list[bytes]:
        """Frames for whole-device power: always a single frame where the device
        allows it - the global 33 01 switch (which on H60A6 also drives both
        zones), or H6047's combined 33 36 bar frame. Only a zoned fixture without
        a zone-driving master switch falls back to one 33 30 frame per zone."""
        if self.profile.bar_switch:
            return [build.bar_switch(on, on)]
        if self.profile.zones and not self.profile.global_power:
            return [build.zone_power(z.power_index, on) for z in self.profile.zones]
        return [build.switch(on, relay=self.profile.relay)]

    def _apply_power(self, on: bool) -> None:
        self._state.is_on = on
        if self.profile.zones:
            self._state.zone_power = {z.power_index: on for z in self.profile.zones}

    async def set_power(self, on: bool) -> None:
        self._require(Capability.POWER)
        await self._conn.send_many(self._power_frames(on))
        if self.profile.relay:  # plug relay needs a sync-time follow-up to actuate
            ts = int(time.time())
            await self._conn.send(build.plug_sync_time(ts, *_local_tz(ts)), expect_ack=False)
        self._apply_power(on)
        self._mark_authoritative()
        self._notify()

    async def turn_on(
        self,
        *,
        brightness: int | None = None,
        rgb: tuple[int, int, int] | None = None,
        kelvin: int | None = None,
        power: bool = True,
    ) -> None:
        """Power on and apply brightness/colour as ONE batched operation: one
        lock acquisition, one connection, each frame ack-verified in order
        (power first so the rest lands on a lit fixture). ``power=False`` skips
        the power frames (already on)."""
        if rgb is not None and kelvin is not None:
            raise GoveeBleInvalidValue("turn_on: pass either rgb or kelvin, not both")
        frames: list[bytes] = []
        if power:
            self._require(Capability.POWER)
            frames += self._power_frames(True)
        if brightness is not None:
            self._require(Capability.BRIGHTNESS)
            brightness = max(1, min(100, brightness))
            frames.append(build.brightness(brightness))
        if rgb is not None:
            self._require(Capability.RGB)
            frames.append(build.color_rgb(*rgb, self.profile.color_scheme, self.profile.segments))
        elif kelvin is not None:
            self._require(Capability.COLOR_TEMP)
            self._check_kelvin(kelvin)
            frames.append(build.color_temp(kelvin, self.profile.color_scheme, self.profile.segments))
        if not frames:
            return
        await self._conn.send_many(frames)
        if power:
            self._apply_power(True)
        if brightness is not None:
            self._state.brightness = brightness
        if rgb is not None:
            self._state.rgb_color, self._state.color_temp_kelvin = tuple(rgb), None  # type: ignore[assignment]
            self._state.scene_code = None
        elif kelvin is not None:
            self._state.color_temp_kelvin, self._state.rgb_color = kelvin, None
            self._state.scene_code = None
        self._mark_authoritative()
        self._notify()

    async def turn_off(self) -> None:
        await self.set_power(False)

    async def set_brightness(self, pct: int) -> None:
        self._require(Capability.BRIGHTNESS)
        pct = max(1, min(100, pct))  # 0 is not "off" on these devices; never send it
        await self._conn.send(build.brightness(pct))
        self._state.brightness = pct
        self._mark_authoritative()
        self._notify()

    # -- colour -------------------------------------------------------------
    async def set_rgb(self, rgb: tuple[int, int, int]) -> None:
        self._require(Capability.RGB)
        r, g, b = rgb
        await self._conn.send(build.color_rgb(r, g, b, self.profile.color_scheme, self.profile.segments))
        self._state.rgb_color = (r, g, b)
        self._state.color_temp_kelvin = None
        self._state.scene_code = None
        self._mark_authoritative()
        self._notify()

    async def set_color_temp(self, kelvin: int) -> None:
        self._require(Capability.COLOR_TEMP)
        self._check_kelvin(kelvin)
        await self._conn.send(build.color_temp(kelvin, self.profile.color_scheme, self.profile.segments))
        self._state.color_temp_kelvin = kelvin
        self._state.rgb_color = None
        self._state.scene_code = None
        self._mark_authoritative()
        self._notify()

    async def set_segment_rgb(self, indices: list[int], rgb: tuple[int, int, int]) -> None:
        self._require(Capability.SEGMENTS)
        r, g, b = rgb
        await self._conn.send(build.segment_rgb(_mask(indices), r, g, b, self.profile.color_scheme))
        self._update_segments(indices, rgb=(r, g, b))
        self._mark_authoritative()
        self._notify()

    async def set_segment_brightness(self, indices: list[int], pct: int) -> None:
        """Brightness on selected segments. Raises GoveeBleNotSupported for colour
        schemes without per-segment brightness (e.g. H61A8)."""
        self._require(Capability.SEGMENTS)
        pct = max(0, min(100, pct))
        frame = self._build_or_unsupported(
            build.segment_brightness, _mask(indices), pct, self.profile.color_scheme)
        await self._conn.send(frame)
        self._update_segments(indices, brightness=pct)
        self._mark_authoritative()
        self._notify()

    def _build_or_unsupported(self, builder: Callable[..., bytes], *args: Any) -> bytes:
        """Run a frame builder, translating its ValueError (scheme can't express the
        request) into GoveeBleNotSupported."""
        try:
            return builder(*args)
        except GoveeBleInvalidValue:
            raise   # bad caller input, not an unsupported scheme
        except ValueError as exc:
            raise GoveeBleNotSupported(f"{self._sku}: {exc}") from exc

    def _update_segments(
        self,
        indices: list[int] | tuple[int, ...],
        *,
        rgb: tuple[int, int, int] | None = None,
        brightness: int | None = None,
    ) -> None:
        """Optimistically fold a segment write into state.segments."""
        by_index = {seg.index: seg for seg in self._state.segments}
        updated: list[Segment] = []
        for i in indices:
            cur = by_index.get(i, Segment(index=i))
            seg = Segment(
                index=i,
                rgb=rgb if rgb is not None else cur.rgb,
                brightness=brightness if brightness is not None else cur.brightness,
            )
            by_index[i] = seg   # a repeated index folds onto the previous write
            updated.append(seg)
        self._state.segments = parse.merge_segments(self._state.segments, updated)

    async def set_segment_color_temp(self, indices: list[int], kelvin: int) -> None:
        """Colour temperature on selected segments (0x15 family only — the CCT frame
        carries the segment mask). Raises GoveeBleNotSupported for non-maskable schemes."""
        self._require(Capability.SEGMENTS)
        self._require(Capability.COLOR_TEMP)
        self._check_kelvin(kelvin)
        frame = self._build_or_unsupported(
            build.segment_color_temp, _mask(indices), kelvin, self.profile.color_scheme)
        await self._conn.send(frame)
        self._mark_authoritative()
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
            # One frame carries BOTH bars, so the other bar's current state must be
            # known - never assume it (assuming "on" turned the other bar ON).
            zp = self._state.zone_power
            if len(zp) < 2:
                await self._read_bar_switch()
                zp = self._state.zone_power
            other = 1 - z.power_index
            if other not in zp:
                raise GoveeBleError(
                    f"{self._sku}: state of the other bar is unknown (read-back failed); "
                    "not changing it blindly")
            target = {0: zp.get(0, False), 1: zp.get(1, False)}
            target[z.power_index] = on
            await self._conn.send(build.bar_switch(target[0], target[1]))
            self._state.zone_power = dict(target)
        else:
            await self._conn.send(build.zone_power(z.power_index, on))
            self._state.zone_power[z.power_index] = on
        if self.profile.zones and len(self._state.zone_power) == len(self.profile.zones):
            self._state.is_on = any(self._state.zone_power.values())
        self._mark_authoritative()
        self._notify()

    async def _read_bar_switch(self) -> None:
        bars = parse.parse_bar_switch(await self._read_reply(build.bar_switch_query(), build.CMD_BAR_SWITCH) or b"")
        if bars is not None:
            self._state.zone_power = {0: bars[0], 1: bars[1]}

    async def set_zone_rgb(self, name: str, rgb: tuple[int, int, int]) -> None:
        self._require(Capability.SEGMENTS)
        self._require(Capability.RGB)
        z = self._zone(name)
        if not z.segments:
            raise GoveeBleNotSupported(f"{self._sku}: zone {name!r} has no segments")
        r, g, b = rgb
        await self._conn.send(build.segment_rgb(_mask(z.segments), r, g, b, self.profile.color_scheme))
        self._update_segments(z.segments, rgb=(r, g, b))
        self._mark_authoritative()
        self._notify()

    async def set_zone_color_temp(self, name: str, kelvin: int) -> None:
        """Colour temperature for one zone (its segment mask). Physically supported on the
        0x15 family (H60A6/H6047), whose CCT frame carries a segment mask."""
        self._require(Capability.SEGMENTS)
        self._require(Capability.COLOR_TEMP)
        self._check_kelvin(kelvin)
        z = self._zone(name)
        if not z.segments:
            raise GoveeBleNotSupported(f"{self._sku}: zone {name!r} has no segments")
        frame = self._build_or_unsupported(
            build.segment_color_temp, _mask(z.segments), kelvin, self.profile.color_scheme)
        await self._conn.send(frame)
        self._mark_authoritative()
        self._notify()

    # -- scenes -------------------------------------------------------------
    async def set_scene(self, code: int) -> None:
        self._require(Capability.SCENES)
        await self._conn.send(build.scene_activate(code))
        self._state.scene_code = code
        self._apply_power(True)
        self._mark_authoritative()
        self._state.rgb_color = None
        self._state.color_temp_kelvin = None
        self._notify()

    def scene_upload_frames(self, scene: Scene) -> list[bytes] | None:
        """The a3/a4 frames that upload ``scene`` to this device (per its profile's scene
        dialect and supported scene versions), or None when it is activate-only. Pure: no I/O."""
        return scene_upload_frames(scene, self.profile.scene_dialect, self.profile.scene_versions)

    # Kept: tools/fetch_scenes.py and tests call the old private name.
    _scene_upload_frames = scene_upload_frames

    async def set_scene_by_name(self, name: str) -> None:
        self._require(Capability.SCENES)
        scene = load_scenes(self._sku).get(name)
        if scene is None:
            raise GoveeBleNotSupported(f"{self._sku}: unknown scene {name!r}")
        frames = self._scene_upload_frames(scene)
        if frames:
            await self._conn.send_unacked(frames)
        await self.set_scene(scene.code)

    # -- misc settings ------------------------------------------------------
    async def set_gradual(self, on: bool) -> None:
        """Toggle the gradual/fade-on-BLE↔wifi-handoff flag (0xA3). Only SKUs whose profile
        sets ``gradual`` (e.g. H61A8) support it; others raise GoveeBleNotSupported."""
        if not self.profile.gradual:
            raise GoveeBleNotSupported(f"{self._sku}: gradual (BLE/wifi fade) not supported")
        await self._conn.send(build.gradual(on))
        self._state.gradual = on
        self._notify()

    # -- read-back ----------------------------------------------------------
    async def update(self) -> DeviceState:
        if self.profile.readback == "status":
            await self._read_status()
        elif self.profile.readback == "polled":
            await self._read_polled()
        elif self.profile.readback == "plug":
            await self._read_plug()
        elif self.profile.readback == "none":
            pass
        else:
            _unsupported_readback(self.profile.readback)
        # Segment / single-colour read-back layered on top (spec Change 7).
        if self.profile.color_readback == "mechanism_b":
            await self._read_mechanism_b()
        elif self.profile.color_readback == "mechanism_a_direct":
            await self._read_mechanism_a_direct()
        if self.profile.gradual:
            g = parse.parse_gradual(await self._read_reply(build.gradual_query(), build.CMD_GRADUAL_WIFI_BLE) or b"")
            if g is not None:
                self._state.gradual = g
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

    def _apply_mode_reply(self, reply: bytes) -> None:
        """Fold an aa 05 mode report into state: active scene, or the colour /
        colour temperature of the solid-colour sub-mode."""
        self._state.scene_code = parse.parse_active_scene(reply)
        kelvin = parse.parse_kelvin(reply)
        if kelvin:
            self._state.color_temp_kelvin, self._state.rgb_color = kelvin, None
            return
        mode_0d = parse.parse_mode_0d(reply)
        if mode_0d is not None:
            rgb, kelvin = mode_0d
            self._state.rgb_color, self._state.color_temp_kelvin = rgb, kelvin

    async def _read_plug(self) -> None:
        """Poll a plug's relay state (aa 01 -> raw relay bitmask; any bit set = on),
        so state.is_on reflects the device rather than the last command sent."""
        power = parse.parse_power(await self._read_reply(build.power_query(), build.CMD_SWITCH) or b"")
        if power is not None:
            self._state.is_on = bool(power)
            self._state.optimistic = False
            self._mark_authoritative()

    async def _read_device_info(self) -> None:
        """Populate the static device-info fields from the aa 07 replies. Selector-aware:
        device fw/hw + serial come from BASIC (0x10); wifi_mac from WIFI (0x11) — whose
        sw/hw are the *wifi-module* versions and must NOT clobber the device's; SN (0x02)
        is only a serial fallback. Best-effort + once; unanswered selectors leave fields
        None. ble_mac stays None unless a reply carries a MAC distinct from the connectable
        address (none confirmed yet — the connectable address is otherwise the BLE MAC)."""
        basic = parse.parse_device_info(
            await self._read_reply(build.device_info_query(build.INFO_BASIC), build.CMD_DEVICE_INFO) or b"")
        if basic is not None:
            self._merge_device_info(
                serial=basic.serial, firmware=basic.sw_version, hardware=basic.hw_version)
        wifi = parse.parse_device_info(
            await self._read_reply(build.device_info_query(build.INFO_WIFI), build.CMD_DEVICE_INFO) or b"")
        if wifi is not None:
            self._merge_device_info(wifi_mac=wifi.wifi_mac)   # never wifi sw/hw (see docstring)
        if self._state.serial_number is None:   # SN read only if basic didn't carry it
            sn = parse.parse_device_info(
                await self._read_reply(build.device_info_query(build.INFO_SN), build.CMD_DEVICE_INFO) or b"")
            if sn is not None:
                self._merge_device_info(serial=sn.serial)
        # Only after the reads completed without raising: an unanswered selector still counts as
        # attempted (no retry every poll), but a link drop mid-read retries on the next poll.
        self._device_info_read = True

    def _merge_device_info(
        self,
        *,
        serial: str | None = None,
        wifi_mac: str | None = None,
        firmware: str | None = None,
        hardware: str | None = None,
    ) -> None:
        """Copy each non-None device-info field onto state; None leaves the field as is."""
        if serial is not None:
            self._state.serial_number = serial
        if wifi_mac is not None:
            self._state.wifi_mac = wifi_mac
        if firmware is not None:
            self._state.firmware_version = firmware
        if hardware is not None:
            self._state.hardware_version = hardware

    async def _read_mechanism_b(self) -> None:
        """H61A8's per-segment colour via BulbGroupColorV2 (AA A5 <seq>, V2): request each
        batch and assemble positional segments. per_batch/batch-count are client constants
        (not frame-encoded, the controller's own fixed group size), source-modeled and not
        yet live-verified. NOT shared with H6047/H6641 — they use a different controller
        (Controller4ColorInfoByGroup, a different group size) via `_read_mechanism_a_direct`;
        the two only happen to share the wire opcode (0xA5), not the reply layout."""
        per_batch = self.profile.color_readback_per_batch or 3
        seg_count = self.profile.color_readback_segments or self.profile.segments
        batch_count = build.ceil_div(seg_count, per_batch)
        batches: list[tuple[int, list[tuple[int | None, int, int, int]]]] = []
        for seq in range(1, batch_count + 1):
            reply = await self._read_reply(build.bulb_group_query(seq, v2=True), build.CMD_BULB_COLOR_V2)
            parsed = parse.parse_bulb_group_batch(reply) if reply else None
            if parsed is not None:
                batches.append(parsed)
        segs = parse.bulb_groups_to_segments(batches, per_batch)
        if segs:
            self._state.segments = segs

    async def _read_mechanism_a_direct(self) -> None:
        """H6047/H6641 per-segment colour via Controller4ColorInfoByGroup's direct per-group
        requests (AA A5 <group>) — a different controller from H61A8's mechanism_b
        (BulbGroupColorV2); they only share the wire opcode. Group size is a fixed client
        constant (`color_readback_per_batch`, source-confirmed 4 for both curated SKUs). The
        total piece count — and hence the group count — comes from either a static per-SKU
        value (`color_readback_segments`/`segments`, e.g. H6047's getColorPieceSize=12) or,
        when `color_readback_live_ic` is set (H6641), a live `0x40` IC-count read: the device
        reports its OWN group count directly (`ic_segment_read.segment`), sidestepping any
        client-side group-count-divisor assumption."""
        group_size = self.profile.color_readback_per_batch or 4
        if self.profile.color_readback_live_ic:
            ic_reply = await self._read_reply(build.ic_count_query(), build.CMD_IC_NUM)
            ic = parse.parse_ic_count(ic_reply) if ic_reply else None
            if ic is None:
                _LOGGER.debug(
                    "%s (%s): live IC-count read failed; skipping segment colour read-back",
                    self.address, self._sku,
                )
                return
            total, group_count = ic.ic_count, ic.segment_count
        else:
            total = self.profile.color_readback_segments or self.profile.segments
            group_count = build.ceil_div(total, group_size)

        groups: list[tuple[int, list[tuple[int, int, int, int]]]] = []
        for g in range(1, group_count + 1):
            reply = await self._read_reply(build.bulb_group_query(g, v2=True), build.CMD_BULB_COLOR_V2)
            expected = max(0, min(group_size, total - (g - 1) * group_size))
            parsed = parse.parse_direct_color_group(reply, expected) if reply else None
            if parsed is None:
                continue
            groups.append(parsed)
        segs = parse.direct_groups_to_segments(groups, group_size)
        if segs:
            self._state.segments = segs

    async def _query_status(self) -> tuple[reassemble.StatusReply, list[bytes], list[bytes]]:
        frames = await self._conn.query(build.status_query(full=True), timeout=STATUS_READ_TIMEOUT)
        ac = [f for f in frames if f and f[0] == build.PRO_MULTI_AC]
        return reassemble.parse_status(ac), ac, frames

    async def _read_status(self) -> None:
        st, ac, frames = await self._query_status()
        if st.is_on is None:
            # The device answered (or didn't) but no usable TLVs came back — most often a
            # dropped/truncated 0xAC burst (a BLE notification lost mid-sequence, so the
            # reassembled buffer ends mid-TLV or is empty). Retry once with a fresh query —
            # a new burst is independent, so this self-heals most single-poll losses — before
            # leaving state stale. This is also the signal for "mechanism-A doesn't work here"
            # for a device that reliably returns nothing (e.g. H6047).
            _LOGGER.debug(
                "%s (%s): status read empty on first attempt (%d 0xAC of %d frames, "
                "chunks %s); retrying once",
                self.address, self._sku, len(ac), len(frames), _chunk_indices(ac),
            )
            st, ac, frames = await self._query_status()
        if st.is_on is None:
            _LOGGER.warning(
                "%s (%s): status read returned no usable data after retry "
                "(%d 0xAC of %d frames, chunks %s); state left stale",
                self.address, self._sku, len(ac), len(frames), _chunk_indices(ac),
            )
        if st.is_on is not None:
            self._state.is_on = st.is_on
        if st.brightness is not None:
            self._state.brightness = self._brightness_pct(st.brightness)
        if st.zone_power:
            self._state.zone_power = st.zone_power
        if st.segments:
            # A burst whose tail was lost carries only a prefix of the segments;
            # merge so the rest keep their last known values.
            self._state.segments = parse.merge_segments(self._state.segments, st.segments)
        # Device-info (serial / wifi_mac / fw / hw) comes from the 0x07 TLV in the 0xAC
        # stream — the ONLY source for BLE-only devices (H60A6); aa 07 returns zeros there.
        self._merge_device_info(
            serial=st.serial_number, wifi_mac=st.wifi_mac,
            firmware=st.firmware_version, hardware=st.hardware_version)
        if Capability.SCENES in self.capabilities:
            reply = await self._read_reply(build.mode_query(), build.CMD_MODE)
            if reply is not None:
                self._apply_mode_reply(reply)
        self._state.optimistic = False
        self._mark_authoritative()

    async def _read_polled(self) -> None:
        # Always read power: a device turned on externally (app, remote, wall
        # switch) must be noticed even when we last saw it off.
        power = parse.parse_power(await self._read_reply(build.power_query(), build.CMD_SWITCH) or b"")
        if power is not None:
            self._state.is_on = bool(power)
        raw = parse.parse_brightness(await self._read_reply(build.brightness_query(), build.CMD_BRIGHTNESS) or b"")
        if raw is not None:
            self._state.brightness = self._brightness_pct(raw)
        if self.profile.bar_switch:
            await self._read_bar_switch()
        if Capability.SCENES in self.capabilities or self.profile.color_scheme == "h6006":
            reply = await self._read_reply(build.mode_query(), build.CMD_MODE)
            if reply is not None:
                self._apply_mode_reply(reply)
        self._state.optimistic = False
        self._mark_authoritative()

    async def _read_reply(self, frame: bytes, command_type: int) -> bytes | None:
        frames = await self._conn.query(frame, opcode=build.PRO_READ, terminal=command_type, timeout=INFO_READ_TIMEOUT)
        return parse.find_reply(frames, command_type)

    async def read_secret(self) -> bytes | None:
        """Read the 8-byte account-lock secret off the device (aa b1), or None.

        For the SETUP/bootstrap case: an unbound plug returns its secret without one
        being supplied, so call this BEFORE set_secret() (while `_secret` is None the
        post-handshake unlock step sends nothing, avoiding the secret chicken-and-egg).
        Restores the v2 auto-read; the frame builder is build.secret_read()."""
        reply = await self._read_reply(build.secret_read(), build.CMD_SECRET_READ)
        return parse.parse_secret(reply) if reply else None


def make_device(
    ble_device: BLEDevice,
    sku: str,
    advertisement_data: Any | None = None,
    *,
    secret: bytes | None = None,
    frame_log: str | None = None,
    idle_disconnect: float | None = None,
) -> Device | None:
    """Build a capability-driven Device for `sku`, or None if unsupported."""
    p = profile_for(sku)
    if p is None:
        return None
    return Device(
        p, ble_device, advertisement_data, sku=sku, secret=secret, frame_log=frame_log,
        idle_disconnect=idle_disconnect,
    )
