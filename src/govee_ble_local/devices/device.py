"""Capability-driven device: one class, configured by a :class:`DeviceProfile`.

Behaviour is gated by ``profile.capabilities`` and dispatched to the spec-first
:mod:`..wire` layer (build / parse / reassemble) over a
:class:`~..transport.connection.GoveeConnection`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any, Never

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from ..const import INFO_READ_TIMEOUT, STATUS_READ_TIMEOUT
from ..exceptions import GoveeBleError, GoveeBleInvalidValue, GoveeBleNotSupported
from ..identify import identify, parse_broadcast_onoff
from ..models import Capability, DeviceState, Encryption, Segment, Zone
from ..scenes import Scene, load_scenes, scene_names_by_code, scene_upload_frames
from ..transport.connection import GoveeConnection
from ..wire import build, parse, reassemble
from .brightness import percent_to_raw, raw_to_percent
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




def _unsupported_readback(value: Never) -> Never:
    """Exhaustiveness guard: mypy proves every ``ReadBack`` value is handled; at runtime
    (a profile mutated past its construction-time validation) raise a library error."""
    raise GoveeBleError(f"unsupported readback {value!r}")


def _check_segment_count(count: int) -> None:
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= build.MAX_MASK_SEGMENTS:
        raise GoveeBleInvalidValue(
            f"segment count must be an integer in 1..{build.MAX_MASK_SEGMENTS}, got {count!r}")


def _mask(indices: list[int] | tuple[int, ...], segments: int = 0) -> int:
    """Segment-index list -> mask bits. ``segments`` is the device's segment count: up to 16
    the whole 2-byte mask is addressable (so a 10-segment device still accepts 0..15, as it
    always has); above 16 the app's mask is exactly ``segments`` bits, so indices must be
    below it."""
    limit = max(build.LEGACY_MASK_SEGMENTS, segments)
    m = 0
    for i in indices:
        if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < limit:
            raise GoveeBleInvalidValue(
                f"segment index must be an integer in 0..{limit - 1}, got {i!r}")
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
        advertisement_data: AdvertisementData | None = None,
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
        self._segment_count: int | None = None   # runtime-reported; overrides profile.segments
        self._device_info_read = False   # device-info (mac/hw/fw/sn) read once, lazily
        self._polled_once = False        # for the one-time INFO "first poll" line
        # Mutation sequence (BUG-4): bumped by every state-mutating setter when it applies its
        # optimistic state. update() snapshots it before each read and discards the reply if it
        # moved, so a stale read never overwrites a newer setter. Not a lock: setters never wait.
        self._mutation_seq = 0
        # Serializes the bar-switch read-modify-write in set_zone_power (one frame carries both
        # bars). Lock order: this is OUTERMOST, the connection lock inner; never take it from
        # inside a connection operation.
        self._bar_lock = asyncio.Lock()
        self._discarded_reads = 0        # cumulative count of discarded stale reads
        self._callbacks: list[StateCallback] = []
        self._advert_ignore_until = 0.0   # monotonic; see ADVERT_SUPPRESS_SECONDS
        # (pact_type, pact_code) from the Govee advertisement, or None until one is heard.
        self._pact: tuple[int, int] | None = None
        self._learn_pact(advertisement_data)
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

    def _learn_pact(self, adv: AdvertisementData | None) -> None:
        """Remember the advertised ``(pact_type, pact_code)``; the app reads the same pair
        (``BleUtil.parseBleBroadcastPact``) into the device model and gates some frames on it."""
        mfg = getattr(adv, "manufacturer_data", None)
        if not mfg:
            return
        a = identify(getattr(adv, "local_name", None) or self._ble_device.name, mfg)
        if a is not None and a.pact_type != -1:
            self._pact = (a.pact_type, a.pact_code)

    @property
    def pact(self) -> tuple[int, int] | None:
        """The advertised ``(pact_type, pact_code)``, or None if no advertisement carrying it
        has been seen (e.g. a device built without advertisement data)."""
        return self._pact

    def _resolve_encryption(self, adv: AdvertisementData | None) -> Encryption:
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
        """BLE address of the underlying device."""
        return self._ble_device.address

    @property
    def name(self) -> str:
        """Advertised name, falling back to the address."""
        return self._ble_device.name or self.address

    @property
    def sku(self) -> str:
        """Upper-case SKU this device was built for."""
        return self._sku

    @property
    def capabilities(self) -> frozenset[Capability]:
        """Capabilities enabled by the profile."""
        return self.profile.capabilities

    @property
    def zones(self) -> tuple[Zone, ...]:
        """The profile's independently-switchable zones (empty for unzoned devices)."""
        return self.profile.zones

    @property
    def segment_count(self) -> int:
        """Segments this device addresses: the count it reported at runtime
        (:meth:`set_segment_count`) if any, else the profile's static ``segments``. Sets the
        whole-device mask and its byte width (2 bytes up to 16 segments, ceil(n/8) above)."""
        return self._segment_count if self._segment_count is not None else self.profile.segments

    def set_segment_count(self, count: int | None) -> None:
        """Override the profile's segment count with one the device reported (e.g. a
        runtime read of its segment/section count); ``None`` reverts to the profile value.

        Raises GoveeBleInvalidValue unless ``count`` is 1..56 (the widest mask, 7 bytes, the
        app encodes). Does not touch the connection or any state."""
        if count is not None:
            _check_segment_count(count)
        self._segment_count = count

    @property
    def min_kelvin(self) -> int | None:
        """Lowest supported colour temperature, or None if unconstrained."""
        return self.profile.min_kelvin

    @property
    def max_kelvin(self) -> int | None:
        """Highest supported colour temperature, or None if unconstrained."""
        return self.profile.max_kelvin

    @property
    def encryption(self) -> Encryption:
        """The command-channel mode in use (advertisement + BGC refined)."""
        return self._conn.encryption

    @property
    def state(self) -> DeviceState:
        """The live (optimistic until first read-back) device state."""
        return self._state

    @property
    def is_on(self) -> bool | None:
        """Power state, or None if unknown."""
        return self._state.is_on

    @property
    def scene_names(self) -> list[str]:
        """Sorted names of the bundled scenes; empty without the SCENES capability."""
        return sorted(load_scenes(self._sku)) if Capability.SCENES in self.capabilities else []

    @property
    def active_scene(self) -> str | None:
        """Name of the active scene, or None if no scene (or an uncatalogued code) is active."""
        code = self._state.scene_code
        if code is None:
            return None
        return scene_names_by_code(self._sku).get(code)

    def register_callback(self, cb: StateCallback) -> Callable[[], None]:
        """Register a state-change callback; returns a function that unregisters it.

        A callback that raises is logged and does not affect other callbacks."""
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
        """Set the account-lock secret used by the next unlock handshake.

        Raises GoveeBleInvalidValue if it is not exactly ``build.SECRET_LEN`` bytes."""
        self._secret = self._checked_secret(secret)

    def _check_kelvin(self, kelvin: int) -> int:
        """The Kelvin that goes on the wire (and into ``state``) for ``kelvin``: clamped to the
        profile range, then rounded to the 100 K grid, as the app does
        (:func:`build.normalize_kelvin`). Out-of-range input is clamped, not rejected; only a
        non-int raises GoveeBleInvalidValue."""
        return build.normalize_kelvin(kelvin, self.profile.min_kelvin, self.profile.max_kelvin)

    def _color_temp_frames(self, kelvin: int) -> list[bytes]:
        """Frames for whole-device colour temperature (``kelvin`` already normalised).

        One frame, except on a profile with ``cct_per_zone_min_pact`` (H60A6): when the
        advertised pact is at or above it - or not known yet - one frame per zone in profile
        order (main panel, then ring), as ``ControllerBuild.j`` does. Below the pact gate the
        app uses a different multi-packet colour path that is not modelled, so the single
        all-segments frame is sent."""
        profile = self.profile
        n = self.segment_count
        gate = profile.cct_per_zone_min_pact
        if gate is not None and (
                self._pact is None or (self._pact[0] == gate[0] and self._pact[1] >= gate[1])):
            return [
                build.segment_color_temp(_mask(z.segments, n), kelvin, profile.color_scheme, n)
                for z in profile.zones if z.segments]
        return [build.color_temp(kelvin, profile.color_scheme, n)]

    def _kelvin_range(self) -> tuple[int, int] | None:
        lo, hi = self.profile.min_kelvin, self.profile.max_kelvin
        return (lo, hi) if lo is not None and hi is not None else None

    def _color_rgb_frames(self, rgb: tuple[int, int, int]) -> list[bytes]:
        """Frames for a whole-device RGB pick: one, except on a profile whose colour scheme writes the
        panels in groups (``h6061``: ``ceil(n/4)`` frames over :attr:`segment_count` panels). On every
        scheme with a colour-temperature mode, a colour that is exactly one of the app's Kelvin tint
        colours within the SKU's range is the single CCT frame instead, as the app sends it
        (:func:`build.rgb_as_kelvin`)."""
        return build.color_rgb_frames(
            *rgb, self.profile.color_scheme, self.segment_count, self._kelvin_range())

    def _apply_rgb_state(self, rgb: tuple[int, int, int]) -> None:
        """Optimistic state for a whole-device RGB write: the colour, or - when the app sends it as
        a colour temperature (``build.rgb_as_kelvin``) - that Kelvin, which is what the device reports
        (and what the app syncs to its Kelvin slider; both bars of a bar-switch device)."""
        kelvin = build.rgb_as_kelvin(*rgb, self.profile.color_scheme, self._kelvin_range())
        if kelvin is None:
            self._state.rgb_color, self._state.color_temp_kelvin = rgb, None
        else:
            self._state.rgb_color, self._state.color_temp_kelvin = None, kelvin
        self._state.scene_code = None
        self._apply_zone_kelvin({0: kelvin, 1: kelvin})

    def update_ble_device(self, ble_device: BLEDevice) -> None:
        """Swap in a fresh BLEDevice (e.g. a new scanner path) for later connects."""
        self._ble_device = ble_device
        self._conn.update_ble_device(ble_device)

    def ingest_advertisement(self, advertisement_data: AdvertisementData) -> bool:
        """Update passive state from a BLE advertisement — no connection needed.

        Reads on/off from the Govee manufacturer data (identify.parse_broadcast_onoff);
        returns True if `state.is_on` changed (so a caller can notify selectively). Ignored
        for ``ADVERT_SUPPRESS_SECONDS`` after a command or authoritative read."""
        mfg = getattr(advertisement_data, "manufacturer_data", None)
        if not mfg:
            return False
        self._learn_pact(advertisement_data)
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

    def _mutated(self) -> None:
        """Record that a setter changed (or is applying) device state; see ``_mutation_seq``."""
        self._mutation_seq += 1

    def _stale(self, seq: int, what: str) -> bool:
        """True (and logged) if a setter applied state since ``seq`` was captured, i.e. the
        read that was in flight is older than the state and must not be applied."""
        if seq == self._mutation_seq:
            return False
        self._discarded_reads += 1
        _LOGGER.debug(
            "%s (%s): discarding stale %s read (state changed by a setter while it was in flight)",
            self.address, self._sku, what,
        )
        return True

    def _brightness_pct(self, raw: int) -> int:
        if self.profile.brightness_range is not None:
            return raw_to_percent(raw, *self.profile.brightness_range)
        full = self.profile.brightness_max
        return raw if full == 100 else round(raw * 100 / full)

    # -- power / brightness -------------------------------------------------
    def _brightness_frame(self, pct: int) -> bytes:
        """33 04 for a 1..100 percent: verbatim on percent devices, mapped onto the raw
        range (NumberUtil.calculateProgress) on devices with ``brightness_range``."""
        if self.profile.brightness_range is not None:
            return build.brightness_raw(percent_to_raw(pct, *self.profile.brightness_range))
        return build.brightness(pct)

    def _power_frames(self, on: bool) -> list[bytes]:
        """Frames for whole-device power: always a single frame where the device
        allows it - the global 33 01 switch (which on H60A6 also drives both
        zones; H6047's main switch sends it too - ``33 36`` is only its per-bar
        buttons, see :meth:`set_zone_power`). Only a zoned fixture without a
        zone-driving master switch falls back to one 33 30 frame per zone."""
        if self.profile.zones and not self.profile.global_power and not self.profile.bar_switch:
            return [build.zone_power(z.power_index, on) for z in self.profile.zones]
        return [build.switch(on, relay=self.profile.relay)]

    def _apply_power(self, on: bool) -> None:
        self._state.is_on = on
        if self.profile.bar_switch and on:
            # 33 01 on: the app does not say which bars light (it learns that from the next aa 36
            # heartbeat), so don't guess; the next zone write re-reads them rather than assuming.
            self._state.zone_power = {}
        elif self.profile.zones:
            self._state.zone_power = {z.power_index: on for z in self.profile.zones}

    async def set_power(self, on: bool) -> None:
        """Switch the device on or off.

        Raises GoveeBleNotSupported without POWER; connection errors propagate. Relay (plug)
        devices get a best-effort sync-time follow-up whose failure is only logged."""
        self._require(Capability.POWER)
        await self._conn.send_many(self._power_frames(on))
        self._mutated()
        self._apply_power(on)
        self._mark_authoritative()
        self._notify()
        if self.profile.relay:
            # Plug relay needs a sync-time follow-up to actuate. The power write already
            # succeeded and state is applied, so a failure here must neither raise nor undo it.
            ts = int(time.time())
            try:
                await self._conn.send(build.plug_sync_time(ts, *_local_tz(ts)), expect_ack=False)
            except GoveeBleError as err:
                _LOGGER.warning(
                    "%s (%s): relay power written but sync-time follow-up failed: %s",
                    self.address, self._sku, err or type(err).__name__,
                )

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
        the power frames (already on). ``kelvin`` is clamped and rounded as in
        :meth:`set_color_temp`."""
        if rgb is not None and kelvin is not None:
            raise GoveeBleInvalidValue("turn_on: pass either rgb or kelvin, not both")
        if rgb is not None or kelvin is not None:
            await self._ensure_segment_count()
        frames: list[bytes] = []
        if power:
            self._require(Capability.POWER)
            frames += self._power_frames(True)
        if brightness is not None:
            self._require(Capability.BRIGHTNESS)
            brightness = max(1, min(100, brightness))
            frames.append(self._brightness_frame(brightness))
        if rgb is not None:
            self._require(Capability.RGB)
            frames += self._color_rgb_frames(rgb)
        elif kelvin is not None:
            self._require(Capability.COLOR_TEMP)
            kelvin = self._check_kelvin(kelvin)
            frames += self._color_temp_frames(kelvin)
        if not frames:
            return
        await self._conn.send_many(frames)
        self._mutated()
        if power:
            self._apply_power(True)
        if brightness is not None:
            self._state.brightness = brightness
        if rgb is not None:
            self._apply_rgb_state(rgb)
        elif kelvin is not None:
            self._state.color_temp_kelvin, self._state.rgb_color = kelvin, None
            self._state.scene_code = None
            self._apply_zone_kelvin({0: kelvin, 1: kelvin})
        self._mark_authoritative()
        self._notify()

    async def turn_off(self) -> None:
        """Power off; equivalent to ``set_power(False)``."""
        await self.set_power(False)

    async def set_brightness(self, pct: int) -> None:
        """Set brightness in percent (clamped to 1..100; 0 is never sent).

        Raises GoveeBleNotSupported without BRIGHTNESS."""
        self._require(Capability.BRIGHTNESS)
        pct = max(1, min(100, pct))  # 0 is not "off" on these devices; never send it
        await self._conn.send(self._brightness_frame(pct))
        self._mutated()
        self._state.brightness = pct
        self._mark_authoritative()
        self._notify()

    # -- colour -------------------------------------------------------------
    async def set_rgb(self, rgb: tuple[int, int, int]) -> None:
        """Set the whole-device colour (clears colour temperature and active scene).

        Raises GoveeBleNotSupported without RGB; GoveeBleInvalidValue for out-of-range channels."""
        self._require(Capability.RGB)
        await self._ensure_segment_count()
        r, g, b = rgb
        frames = self._color_rgb_frames((r, g, b))
        if len(frames) == 1:
            await self._conn.send(frames[0])
        else:
            await self._conn.send_many(frames)
        self._mutated()
        self._apply_rgb_state((r, g, b))
        self._mark_authoritative()
        self._notify()

    async def set_color_temp(self, kelvin: int) -> None:
        """Set the whole-device colour temperature (clears rgb and active scene).

        Raises GoveeBleNotSupported without COLOR_TEMP and GoveeBleInvalidValue for a non-int.
        Like the app, an out-of-range value is clamped to the profile's kelvin range (not
        rejected) and then rounded to the nearest 100 K, the only values the app sends; the
        Kelvin actually sent is what ``state`` keeps, and the frame carries the app's tint."""
        self._require(Capability.COLOR_TEMP)
        kelvin = self._check_kelvin(kelvin)
        await self._ensure_segment_count()
        frames = self._color_temp_frames(kelvin)
        if len(frames) == 1:
            await self._conn.send(frames[0])
        else:
            await self._conn.send_many(frames)
        self._mutated()
        self._state.color_temp_kelvin = kelvin
        self._state.rgb_color = None
        self._state.scene_code = None
        self._apply_zone_kelvin({0: kelvin, 1: kelvin})
        self._mark_authoritative()
        self._notify()

    async def set_segment_rgb(self, indices: list[int], rgb: tuple[int, int, int]) -> None:
        """Set the colour of selected segments (indices 0..15, or up to the device's
        :attr:`segment_count` when that is above 16).

        Raises GoveeBleNotSupported without SEGMENTS; GoveeBleInvalidValue for a bad index."""
        self._require(Capability.SEGMENTS)
        await self._ensure_segment_count()
        r, g, b = rgb
        n = self.segment_count
        await self._conn.send(build.segment_rgb(
            _mask(indices, n), r, g, b, self.profile.color_scheme, n, self._kelvin_range()))
        self._mutated()
        self._update_segments(indices, rgb=(r, g, b))
        self._mark_authoritative()
        self._notify()

    async def set_segment_brightness(self, indices: list[int], pct: int) -> None:
        """Brightness on selected segments. Raises GoveeBleNotSupported for colour
        schemes without per-segment brightness (e.g. H61A8)."""
        self._require(Capability.SEGMENTS)
        pct = max(0, min(100, pct))
        await self._ensure_segment_count()
        n = self.segment_count
        frame = self._build_or_unsupported(
            build.segment_brightness, _mask(indices, n), pct, self.profile.color_scheme, n)
        await self._conn.send(frame)
        self._mutated()
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
        carries the segment mask). Raises GoveeBleNotSupported for non-maskable schemes.
        ``kelvin`` is clamped and rounded as in :meth:`set_color_temp`."""
        self._require(Capability.SEGMENTS)
        self._require(Capability.COLOR_TEMP)
        kelvin = self._check_kelvin(kelvin)
        await self._ensure_segment_count()
        n = self.segment_count
        frame = self._build_or_unsupported(
            build.segment_color_temp, _mask(indices, n), kelvin, self.profile.color_scheme, n)
        await self._conn.send(frame)
        self._mutated()   # device colour changed (no optimistic field): invalidate in-flight reads
        self._mark_authoritative()
        self._notify()

    # -- zones --------------------------------------------------------------
    def _zone(self, name: str) -> Zone:
        for z in self.profile.zones:
            if z.name == name:
                return z
        raise GoveeBleNotSupported(f"{self._sku}: unknown zone {name!r}")

    def zone_color_temp_kelvin(self, name: str) -> int | None:
        """Last known colour temperature (Kelvin) of zone ``name``, or None when the zone is not
        known to be in colour-temperature mode or the device doesn't report it per zone (only
        H6047 does: one kelvin per bar from ``aa 05 15``).

        Raises GoveeBleNotSupported for an unknown zone."""
        return self._state.zone_color_temp_kelvin.get(self._zone(name).power_index)

    def zone_is_on(self, name: str) -> bool | None:
        """Last known power state of zone ``name``, or None if unknown.

        Raises GoveeBleNotSupported for an unknown zone."""
        return self._state.zone_power.get(self._zone(name).power_index)

    async def set_zone_power(self, name: str, on: bool) -> None:
        """Switch one zone on or off.

        On bar-switch devices the other bar's state is read first if unknown and preserved;
        GoveeBleError if that read-back fails. Raises GoveeBleNotSupported for an unknown zone."""
        z = self._zone(name)
        if self.profile.bar_switch:
            async with self._bar_lock:
                await self._set_bar_power(z.power_index, on)
        else:
            await self._conn.send(build.zone_power(z.power_index, on))
            self._mutated()
            self._state.zone_power[z.power_index] = on
        if self.profile.zones and len(self._state.zone_power) == len(self.profile.zones):
            self._state.is_on = any(self._state.zone_power.values())
        self._mark_authoritative()
        self._notify()

    async def _set_bar_power(self, index: int, on: bool) -> None:
        """Bar-switch read-modify-write; the caller holds ``_bar_lock``."""
        # One frame carries BOTH bars, so the other bar's current state must be
        # known - never assume it (assuming "on" turned the other bar ON).
        zp = self._state.zone_power
        discarded = False
        if len(zp) < 2:
            discarded = await self._read_bar_switch()
            zp = self._state.zone_power
        other = 1 - index
        if other not in zp:
            if discarded:
                raise GoveeBleError(f"{self._sku}: state changed during read; retry")
            raise GoveeBleError(
                f"{self._sku}: state of the other bar is unknown (read-back failed); "
                "not changing it blindly")
        target = {0: zp.get(0, False), 1: zp.get(1, False)}
        target[index] = on
        await self._conn.send(build.bar_switch(target[0], target[1]))
        self._mutated()
        self._state.zone_power = dict(target)

    async def _read_bar_switch(self) -> bool:
        """Read the bar switch into state; True if the reply was discarded as stale."""
        seq = self._mutation_seq
        reply = await self._read_reply(build.bar_switch_query(), build.CMD_BAR_SWITCH)
        bars = parse.parse_bar_switch(reply or b"")
        if bars is None:
            return False
        if self._stale(seq, "bar-switch"):
            return True
        self._state.zone_power = {0: bars[0], 1: bars[1]}
        # The app shows the device as on when either bar is on (NewDetailVm heart parse).
        self._state.is_on = bars[0] or bars[1]
        return False

    async def set_zone_rgb(self, name: str, rgb: tuple[int, int, int]) -> None:
        """Set the colour of one zone (its segment mask).

        Raises GoveeBleNotSupported without SEGMENTS/RGB, for an unknown zone, or a zone
        without segments."""
        self._require(Capability.SEGMENTS)
        self._require(Capability.RGB)
        z = self._zone(name)
        if not z.segments:
            raise GoveeBleNotSupported(f"{self._sku}: zone {name!r} has no segments")
        r, g, b = rgb
        await self._ensure_segment_count()
        n = self.segment_count
        await self._conn.send(build.segment_rgb(
            _mask(z.segments, n), r, g, b, self.profile.color_scheme, n, self._kelvin_range()))
        self._mutated()
        self._update_segments(z.segments, rgb=(r, g, b))
        # A pick equal to a tint colour went out as the zone's CCT frame (build.rgb_as_kelvin): the
        # bar then reports that Kelvin, like set_zone_color_temp.
        self._apply_zone_kelvin({z.power_index: build.rgb_as_kelvin(
            r, g, b, self.profile.color_scheme, self._kelvin_range())})
        self._mark_authoritative()
        self._notify()

    async def set_zone_color_temp(self, name: str, kelvin: int) -> None:
        """Colour temperature for one zone (its segment mask). Physically supported on the
        0x15 family (H60A6/H6047), whose CCT frame carries a segment mask. ``kelvin`` is clamped
        and rounded as in :meth:`set_color_temp`."""
        self._require(Capability.SEGMENTS)
        self._require(Capability.COLOR_TEMP)
        kelvin = self._check_kelvin(kelvin)
        z = self._zone(name)
        if not z.segments:
            raise GoveeBleNotSupported(f"{self._sku}: zone {name!r} has no segments")
        await self._ensure_segment_count()
        n = self.segment_count
        frame = self._build_or_unsupported(
            build.segment_color_temp, _mask(z.segments, n), kelvin, self.profile.color_scheme, n)
        await self._conn.send(frame)
        self._mutated()   # device colour changed: invalidate in-flight reads
        self._apply_zone_kelvin({z.power_index: kelvin})
        self._mark_authoritative()
        self._notify()

    # -- scenes -------------------------------------------------------------
    async def set_scene(self, code: int) -> None:
        """Activate a scene by its BLE code (no upload).

        Raises GoveeBleNotSupported without SCENES."""
        self._require(Capability.SCENES)
        await self._conn.send(build.scene_activate(code))
        self._mutated()
        self._state.scene_code = code
        self._apply_power(True)
        self._mark_authoritative()
        self._state.rgb_color = None
        self._state.color_temp_kelvin = None
        self._apply_zone_kelvin({0: None, 1: None})
        self._notify()

    def scene_upload_frames(self, scene: Scene) -> list[bytes] | None:
        """The a3/a4 frames that upload ``scene`` to this device (per its profile's scene
        dialect and supported scene versions), or None when it is activate-only. Pure: no I/O."""
        return scene_upload_frames(scene, self.profile.scene_dialect, self.profile.scene_versions)

    async def set_scene_by_name(self, name: str) -> None:
        """Upload (if the scene needs it) and activate a bundled scene by name.

        Raises GoveeBleNotSupported without SCENES or for an unknown name."""
        self._require(Capability.SCENES)
        scene = load_scenes(self._sku).get(name)
        if scene is None:
            raise GoveeBleNotSupported(f"{self._sku}: unknown scene {name!r}")
        frames = self.scene_upload_frames(scene)
        if frames:
            await self._conn.send_unacked(frames)
        await self.set_scene(scene.code)

    # -- misc settings ------------------------------------------------------
    async def set_gradual(self, on: bool) -> None:
        """Toggle the gradual/fade-on-BLE↔wifi-handoff flag (0xA3). Only SKUs whose profile
        sets ``gradual`` (H61A8, H6047) support it; others raise GoveeBleNotSupported."""
        if not self.profile.gradual:
            raise GoveeBleNotSupported(f"{self._sku}: gradual (BLE/wifi fade) not supported")
        await self._conn.send(build.gradual(on))
        self._mutated()
        self._state.gradual = on
        self._notify()

    # -- read-back ----------------------------------------------------------
    async def update(self) -> DeviceState:
        """Poll the device and fold the replies into state; returns the current state.

        Reads are not serialized against setters (the connection lock is per-operation), so a
        setter can complete while a read is in flight. Each read therefore snapshots
        ``_mutation_seq`` before it is issued and is DISCARDED (DEBUG log; no state merge, no
        ``_mark_authoritative``) if a setter applied state since. A discarded read means "state
        is newer than this poll": ``update()`` still returns the current (setter-applied) state
        and notifies, but does not consume the one-time "first poll" step (``_polled_once``);
        callers simply poll again later. Static device-info is exempt (never raced by setters)."""
        discarded_before = self._discarded_reads
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
            if self.profile.section_count_read and self._segment_count is None:
                _LOGGER.debug("%s (%s): segment count unknown; skipping segment colour read-back",
                              self.address, self._sku)
            else:
                await self._read_mechanism_a_direct()
        if self.profile.gradual:
            seq = self._mutation_seq
            reply = await self._read_reply(build.gradual_query(), build.CMD_GRADUAL_WIFI_BLE)
            g = parse.parse_gradual(reply or b"")
            if g is not None and not self._stale(seq, "gradual"):
                self._state.gradual = g
        # Device-info (mac/hw/fw/serial) — static-ish, read once on the first good poll.
        if not self._device_info_read and self.profile.readback != "none":
            try:
                await self._read_device_info()
            except GoveeBleError as err:
                # Flag stays False so the next poll retries; the state read above is still good.
                _LOGGER.debug("%s (%s): device-info read failed (%s); retrying next poll",
                              self.address, self._sku, err)
        if self._discarded_reads != discarded_before:
            _LOGGER.debug(
                "%s (%s): poll partially discarded (state newer than poll)", self.address, self._sku)
        elif not self._polled_once and self.profile.readback != "none":
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

    def _apply_zone_kelvin(self, kelvin_by_index: dict[int, int | None]) -> None:
        """Fold a per-bar kelvin change into state (bar-switch devices only; others are a no-op).

        ``None`` or 0 means that bar is not in colour-temperature mode. Like the app
        (``Info4Detail`` subMode4Color parse), the whole-device ``color_temp_kelvin`` is set only
        while both bars carry the same kelvin, and unknown otherwise."""
        if not self.profile.bar_switch:
            return
        zk = dict(self._state.zone_color_temp_kelvin)
        for index, k in kelvin_by_index.items():
            if k:
                zk[index] = k
            else:
                zk.pop(index, None)
        self._state.zone_color_temp_kelvin = zk
        left = zk.get(0)
        self._state.color_temp_kelvin = left if left is not None and left == zk.get(1) else None

    def _apply_mode_reply(self, reply: bytes) -> None:
        """Fold an aa 05 mode report into state: active scene, or the colour /
        colour temperature of the solid-colour sub-mode."""
        self._state.scene_code = parse.parse_active_scene(reply)
        bars = parse.parse_bar_kelvin(reply) if self.profile.bar_switch else None
        if bars is not None:
            # H6047 aa 05 15: [gradual][kelvin left u2be][kelvin right u2be]
            self._apply_zone_kelvin({0: bars[0], 1: bars[1]})
            return
        kelvin = parse.parse_kelvin(reply)
        if kelvin:
            self._state.color_temp_kelvin, self._state.rgb_color = kelvin, None
            return
        mode_0d = (parse.parse_mode_0d_rgblight(reply) if self.profile.color_scheme == "h6188"
                   else parse.parse_mode_0d(reply))
        if mode_0d is not None:
            rgb, kelvin = mode_0d
            self._state.rgb_color, self._state.color_temp_kelvin = rgb, kelvin

    async def _read_plug(self) -> None:
        """Poll a plug's relay state (aa 01 -> raw relay bitmask; any bit set = on),
        so state.is_on reflects the device rather than the last command sent."""
        seq = self._mutation_seq
        power = parse.parse_power(await self._read_reply(build.power_query(), build.CMD_SWITCH) or b"")
        if power is not None and not self._stale(seq, "plug power"):
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
        seg_count = self.profile.color_readback_segments or self.segment_count
        batch_count = build.ceil_div(seg_count, per_batch)
        batches: list[tuple[int, list[tuple[int | None, int, int, int]]]] = []
        mseq = self._mutation_seq
        for seq in range(1, batch_count + 1):
            reply = await self._read_reply(build.bulb_group_query(seq, v2=True), build.CMD_BULB_COLOR_V2)
            parsed = parse.parse_bulb_group_batch(reply) if reply else None
            if parsed is not None:
                batches.append(parsed)
        segs = parse.bulb_groups_to_segments(batches, per_batch)
        if segs and not self._stale(mseq, "segment colour"):
            self._state.segments = segs

    async def _read_mechanism_a_direct(self) -> None:
        """H6047/H6641 per-segment colour via Controller4ColorInfoByGroup's direct per-group
        requests (AA A5 <group>) — a different controller from H61A8's mechanism_b
        (BulbGroupColorV2); they only share the wire opcode. Group size is a fixed client
        constant (`color_readback_per_batch`, source-confirmed 4 for both curated SKUs). The
        total piece count — and hence the group count — comes from either a static per-SKU
        value (`color_readback_segments`/`segments`; H6047 uses its 10 `segments`) or,
        when `color_readback_live_ic` is set (H6641), a live `0x40` IC-count read: the device
        reports its OWN group count directly (`ic_segment_read.segment`), sidestepping any
        client-side group-count-divisor assumption."""
        group_size = self.profile.color_readback_per_batch or 4
        mseq = self._mutation_seq
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
            total = self.profile.color_readback_segments or self.segment_count
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
        if segs and not self._stale(mseq, "segment colour"):
            self._state.segments = segs

    async def _query_status(self) -> tuple[reassemble.StatusReply, list[bytes], list[bytes]]:
        frames = await self._conn.query(build.status_query(full=True), timeout=STATUS_READ_TIMEOUT)
        ac = [f for f in frames if f and f[0] == build.PRO_MULTI_AC]
        return reassemble.parse_status(ac), ac, frames

    async def _read_status(self) -> None:
        seq = self._mutation_seq
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
        applied = False
        if not self._stale(seq, "status"):
            applied = True
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
            seq = self._mutation_seq
            reply = await self._read_reply(build.mode_query(), build.CMD_MODE)
            if reply is not None and not self._stale(seq, "mode"):
                applied = True
                self._apply_mode_reply(reply)
        if applied:
            self._state.optimistic = False
            self._mark_authoritative()

    async def _read_polled(self) -> None:
        # Always read power: a device turned on externally (app, remote, wall
        # switch) must be noticed even when we last saw it off.
        applied = False
        seq = self._mutation_seq
        power = parse.parse_power(await self._read_reply(build.power_query(), build.CMD_SWITCH) or b"")
        if power is not None and not self._stale(seq, "power"):
            applied = True
            self._state.is_on = bool(power)
        seq = self._mutation_seq
        reply = await self._read_reply(build.brightness_query(), build.CMD_BRIGHTNESS)
        raw = parse.parse_brightness(reply or b"")
        if raw is not None and not self._stale(seq, "brightness"):
            applied = True
            self._state.brightness = self._brightness_pct(raw)
        if self.profile.bar_switch:
            await self._read_bar_switch()
        if self.profile.section_count_read:
            # The app's connect order: aa 01, aa 04, aa 41, aa 05 (ControllerStrategyConfig.O).
            await self._read_section_count()
        if (Capability.SCENES in self.capabilities or Capability.COLOR_TEMP in self.capabilities
                or self.profile.color_scheme in ("h6006", "h6188", "h6061")):
            seq = self._mutation_seq
            reply = await self._read_reply(build.mode_query(), build.CMD_MODE)
            if reply is not None and not self._stale(seq, "mode"):
                applied = True
                self._apply_mode_reply(reply)
        if applied:
            self._state.optimistic = False
            self._mark_authoritative()

    async def _read_section_count(self) -> bool:
        """Read the segment count the device reports (``aa 41`` -> ``sectionNum``) and adopt it
        as :attr:`segment_count`, as the app does on every connect. Returns False (state left as
        it was) when the device does not answer or reports a count the masks cannot carry."""
        reply = await self._read_reply(build.section_count_query(), build.CMD_SECTION_COUNT)
        sc = parse.parse_section_count(reply) if reply else None
        if sc is None or not 1 <= sc.section_num <= build.MAX_MASK_SEGMENTS:
            _LOGGER.debug("%s (%s): aa 41 section-count read gave no usable count (%s)",
                          self.address, self._sku, None if sc is None else sc.section_num)
            return False
        self._segment_count = sc.section_num
        return True

    async def _ensure_segment_count(self) -> None:
        """Make sure the device's own segment count is known before a write that carries a segment
        mask. The app always reads it on connect and cannot control the device without it
        (``AbsComposeControllerWithOpResult.onOvertime`` fails the connect read), so a profile with
        ``section_count_read`` does the same lazily here and sends nothing if the device does not
        answer, rather than guessing a mask. A no-op on every other profile and once the count is
        known."""
        if (self.profile.section_count_read and self._segment_count is None
                and not await self._read_section_count()):
            raise GoveeBleError(
                f"{self._sku}: the device did not report its segment count (aa 41); "
                "not sending a colour frame with a guessed segment mask")

    async def _read_reply(self, frame: bytes, command_type: int) -> bytes | None:
        frames = await self._conn.query(
            frame, opcode=build.PRO_READ, terminal=command_type, timeout=INFO_READ_TIMEOUT)
        return parse.find_reply(frames, command_type)

    async def read_secret(self) -> bytes | None:
        """Read the 8-byte account-lock secret off the device (aa b1), or None.

        For the SETUP/bootstrap case: an unbound plug returns its secret without one
        being supplied, so call this BEFORE set_secret() (while `_secret` is None the
        post-handshake unlock step sends nothing, avoiding the secret chicken-and-egg).
        The frame builder is build.secret_read()."""
        reply = await self._read_reply(build.secret_read(), build.CMD_SECRET_READ)
        return parse.parse_secret(reply) if reply else None


def make_device(
    ble_device: BLEDevice,
    sku: str,
    advertisement_data: AdvertisementData | None = None,
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
