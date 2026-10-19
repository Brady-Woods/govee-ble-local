"""Capability-driven device: one class, configured by a :class:`DeviceProfile`.

Behaviour is gated by ``profile.capabilities`` and dispatched to the spec-first
:mod:`..wire` layer (build / parse / reassemble) over a
:class:`~..transport.connection.GoveeConnection`. How a write or a reply changes the state lives in
:mod:`._fold`, and the multi-read walks behind :meth:`Device.update` in :mod:`._readback`; this
module keeps the public API, the frame choice per setter, the locks and the stale-read protocol.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Final, Literal, Never, ParamSpec, TypeVar

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from ..const import ADVERT_SUPPRESS_SECONDS, IDLE_DISCONNECT_DELAY, INFO_READ_TIMEOUT, STATUS_READ_TIMEOUT
from ..exceptions import (
    GoveeBleAuthError,
    GoveeBleCommandRejected,
    GoveeBleError,
    GoveeBleInvalidValue,
    GoveeBleNotSupported,
    GoveeBleTimeout,
    GoveeBleUnknownName,
)
from ..identify import GoveeAdvertisement, from_advertisement, parse_broadcast_onoff
from ..models import Capability, DeviceState, Encryption, Segment, Zone
from ..scenes import Scene, async_load_scenes, load_scenes, scene_names_by_code
from ..scenes import scene_upload_frames as _scene_upload_frames
from ..transport.connection import GoveeConnection
from ..wire import build, parse, reassemble
from . import _fold, _readback
from .brightness import percent_to_device
from .profile import DeviceProfile, profile_for, wire_profile

_LOGGER = logging.getLogger(__name__)

StateCallback = Callable[[DeviceState], None]

_T = TypeVar("_T")
_P = ParamSpec("_P")

# What a read-back did (Device._read_fresh): applied the device's reply to state, discarded it as
# stale (a setter applied state while it was in flight), or got no usable reply.
_ReadResult = Literal["applied", "stale", "none"]

# A polled profile reads the aa 05 mode report when it has any capability the report carries: the
# active scene, or the solid colour / colour temperature of the colour sub-mode (the 0x0d bulbs
# report their colour only there). The app's connect read includes aa 05 for every polled SKU in
# the profile table (see the profile notes); a test pins that each of them still issues it.
_MODE_REPORT_CAPS: Final = frozenset({Capability.SCENES, Capability.COLOR_TEMP, Capability.RGB})


def _local_tz(unix_ts: int) -> tuple[int, int]:
    off = time.localtime(unix_ts).tm_gmtoff or 0
    sign = -1 if off < 0 else 1
    off = abs(off)
    return sign * (off // 3600), sign * ((off % 3600) // 60)


def _check_int(value: object, what: str) -> int:
    """``value`` if it is an int (``bool`` excluded), else GoveeBleInvalidValue: the setters' type check,
    as the frame builders do for colour channels and Kelvin (a float would otherwise fail inside
    ``bytes()`` with a bare TypeError, or be stored in state as a float)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise GoveeBleInvalidValue(f"{what} must be an integer, got {value!r}")
    return value


def _check_pct(pct: object) -> int:
    """A whole-device brightness percent as it is sent: an int (else GoveeBleInvalidValue) clamped
    to 1..100. 0 is never sent: it is not "off" here."""
    return max(1, min(100, _check_int(pct, "brightness")))


def _check_rgb(rgb: object) -> tuple[int, int, int]:
    """``rgb`` as a fresh ``(r, g, b)`` tuple of ints in 0..255, else GoveeBleInvalidValue. Run before
    any I/O, so a malformed colour costs no read and never reaches a builder as shifted arguments,
    and state never keeps a caller-owned list."""
    if not isinstance(rgb, (tuple, list)) or len(rgb) != 3:
        raise GoveeBleInvalidValue(f"rgb must be an (r, g, b) triple, got {rgb!r}")
    for name, value in zip(("red", "green", "blue"), rgb, strict=True):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
            raise GoveeBleInvalidValue(f"{name} must be an integer in 0..255, got {value!r}")
    r, g, b = rgb
    return (r, g, b)


def _unhandled(value: Never, field: str) -> Never:
    """Exhaustiveness guard for a profile Literal (``ReadBack``, ``ColorReadBack``): mypy proves
    every value is handled; at runtime (a profile mutated past its construction-time validation)
    raise a library error."""
    raise GoveeBleError(f"unsupported {field} {value!r}")


def _check_segment_count(count: int) -> None:
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= build.MAX_MASK_SEGMENTS:
        raise GoveeBleInvalidValue(
            f"segment count must be an integer in 1..{build.MAX_MASK_SEGMENTS}, got {count!r}")


def _mask(indices: Sequence[int], segments: int) -> int:
    """Segment indices -> mask bits. ``segments`` is the device's segment count, and indices must be
    below it: the app's position set has exactly that many positions (the piece count), and its mask
    encoders can't set a bit past it (``BleUtils.Y`` copies the set into the 2-byte mask's low bits;
    above 16 ``BleUtils.X`` writes exactly ``segments`` bits). A count of 0 (a custom profile given
    SEGMENTS without segments; the table's build refuses that) keeps the 2-byte mask's 16 positions,
    so the colour scheme's own refusal (GoveeBleNotSupported) still comes first."""
    limit = segments or build.LEGACY_MASK_SEGMENTS
    m = 0
    for i in indices:
        if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < limit:
            raise GoveeBleInvalidValue(
                f"segment index must be an integer in 0..{limit - 1}, got {i!r}")
        m |= 1 << i
    return m


class Device:
    """A Govee BLE device driven entirely by its ``DeviceProfile``.

    ``profile`` is read-only after construction: the connection's unlock step and the fallback
    cipher are derived from it once, in the constructor, so assigning another profile would leave
    the device running a mix of the two. Build a new ``Device`` for a different profile.

    State changes follow one protocol (BUG-4, see ``_mutation_seq``): every setter writes through
    :meth:`_commit` and every read-back folds its reply in through :meth:`_read_fresh`; what each
    write or reply does to the state is in :mod:`._fold`."""

    def __init__(
        self,
        profile: DeviceProfile,
        ble_device: BLEDevice,
        advertisement_data: AdvertisementData | GoveeAdvertisement | None = None,
        *,
        sku: str | None = None,
        secret: bytes | None = None,
        frame_log: str | None = None,
        idle_disconnect: float | None = None,
    ) -> None:
        """Build a Device for ``profile`` (:func:`~govee_ble_local.create_device` looks it up by SKU).

        ``advertisement_data`` (bleak's, or a parsed :class:`GoveeAdvertisement`) picks the command
        channel from its encrypt flag; without one the profile default is used. ``sku`` names the
        device (default: the profile's first SKU). ``secret`` is the 8-byte account-lock key, sent
        after every handshake on a profile that requires one (:meth:`set_secret` can supply it
        later). ``frame_log=<path>`` persists a JSONL session capture. ``idle_disconnect`` is the
        seconds of inactivity after which the link is dropped: None uses the library default
        (``const.IDLE_DISCONNECT_DELAY``), 0 or less keeps the link up.

        Raises GoveeBleInvalidValue if ``secret`` is not exactly 8 bytes."""
        self.profile = profile
        self._ble_device = ble_device
        self._sku = (sku or profile.skus[0]).upper()
        self._secret = self._checked_secret(secret)
        # set_secret() changed the secret: send its 33 b2 unlock on the live session (see set_secret).
        self._unlock_pending = False
        self._scenes_warm = False        # bundled scene catalog loaded off the loop (update())
        self._state = DeviceState(optimistic=True)
        self._segment_count: int | None = None   # runtime-reported; overrides profile.segments
        self._device_info_read = False   # device-info (mac/hw/fw/sn) read once, lazily
        self._polled_once = False        # for the one-time INFO "first poll" line
        # Mutation sequence (BUG-4): bumped by every state-mutating setter when it applies its
        # optimistic state (_commit). Each read snapshots it and discards the reply if it moved
        # (_read_fresh), so a stale read never overwrites a newer setter. Not a lock: setters never
        # wait.
        self._mutation_seq = 0
        # Serializes the bar-switch read-modify-write in set_zone_power (one frame carries both
        # bars). Lock order: _scene_lock, then this, then _read_gate, then the connection lock;
        # never take it from inside a connection operation.
        self._bar_lock = asyncio.Lock()
        # Serializes scene writes, so a scene's upload and its activation are never split by another
        # scene's (the connection lock is per-operation). Outermost lock (see _bar_lock).
        self._scene_lock = asyncio.Lock()
        # Held by a scene write across its upload and activation, and briefly by every read-back
        # (_read_reply, _query_status), so no poll lands between the two (see set_scene_by_name).
        # Innermost device lock: taken after _scene_lock and _bar_lock, before the connection lock.
        self._read_gate = asyncio.Lock()
        self._discarded_reads = 0        # cumulative count of discarded stale reads
        self._callbacks: list[StateCallback] = []
        self._advert_ignore_until = 0.0   # monotonic; see ADVERT_SUPPRESS_SECONDS
        # (pact_type, pact_code) from the Govee advertisement, or None until one is heard.
        self._pact: tuple[int, int] | None = None
        adv = from_advertisement(advertisement_data, ble_device.name)   # parsed once, used twice
        self._learn_pact(adv)
        enc = self._resolve_encryption(adv)
        self._conn = GoveeConnection(
            ble_device,
            encryption=enc,
            unlock_frames=self._unlock_frames if profile.requires_secret else None,
            session_frames=self._session_frames if profile.relay or profile.light_effect_stop else None,
            frame_log=frame_log,
            idle_disconnect=IDLE_DISCONNECT_DELAY if idle_disconnect is None else idle_disconnect,
        )

    def _learn_pact(self, adv: GoveeAdvertisement | None) -> None:
        """Remember the advertised ``(pact_type, pact_code)``; the app reads the same pair
        (``BleUtil.parseBleBroadcastPact``) into the device model. Exposed as :attr:`pact`; it decides
        whether the 0xAC status read asks for colour groups (:meth:`_wants_color_groups`), and picks the
        profile's pact entries for the scene set and the Kelvin and brightness ranges. An advertisement
        without pact info changes nothing."""
        if adv is not None and adv.pact is not None:
            self._pact = adv.pact

    @property
    def pact(self) -> tuple[int, int] | None:
        """The advertised ``(pact_type, pact_code)``, or None if no advertisement carrying it
        has been seen (e.g. a device built without advertisement data)."""
        return self._pact

    def _resolve_encryption(self, adv: GoveeAdvertisement | None) -> Encryption:
        """The command-channel mode the parsed advertisement announces (its encrypt flag), else the
        profile default: without an advertisement, or when it carried no pact info, whose plaintext
        ``encrypted`` is a guess (unknown, not "plaintext")."""
        if adv is None or not adv.encryption_known:
            return self.profile.encryption
        return Encryption.AES_RC4_PSK if adv.encrypted else Encryption.NONE

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
        """Lowest supported colour temperature, or None if unconstrained. It may change once the
        hardware version is read (:attr:`DeviceProfile.kelvin_by_hardware`) or a pact is advertised
        (:attr:`DeviceProfile.kelvin_by_pact`)."""
        span = self._kelvin_range()
        return self.profile.min_kelvin if span is None else span[0]

    @property
    def max_kelvin(self) -> int | None:
        """Highest supported colour temperature, or None if unconstrained (see :attr:`min_kelvin`)."""
        span = self._kelvin_range()
        return self.profile.max_kelvin if span is None else span[1]

    @property
    def scene_versions(self) -> frozenset[int]:
        """The scene versionArray in force: :attr:`DeviceProfile.scene_versions`, or the
        :attr:`DeviceProfile.scene_versions_by_pact` set of the advertised pact and the
        :attr:`DeviceProfile.scene_versions_by_firmware` set the soft / hard versions select
        (:func:`._fold.scene_versions`; a version not yet read meets its gates)."""
        return _fold.scene_versions(self.profile, self._state.firmware_version,
                                    self._state.hardware_version, self._pact)

    def _kelvin_range(self) -> tuple[int, int] | None:
        """The Kelvin range for this device's hardware version and advertised pact, as far as they are
        known."""
        return _fold.kelvin_range(self.profile, self._state.hardware_version, self._pact)

    @property
    def encryption(self) -> Encryption:
        """The command-channel mode in use (advertisement + BGC refined)."""
        return self._conn.encryption

    @property
    def state(self) -> DeviceState:
        """The live (optimistic until first read-back) device state.

        This is the device's own object, changed in place by every setter and read-back (and
        returned by :meth:`update` and passed to the callbacks): read it, don't modify it. Use
        :meth:`snapshot` for a copy to keep or compare."""
        return self._state

    def snapshot(self) -> DeviceState:
        """A deep copy of :attr:`state` that later changes never touch, e.g. to tell what an
        :meth:`update` changed (``before = dev.snapshot(); await dev.update()``)."""
        return copy.deepcopy(self._state)

    @property
    def is_on(self) -> bool | None:
        """Power state, or None if unknown."""
        return self._state.is_on

    @property
    def scene_names(self) -> list[str]:
        """Sorted names of the bundled scenes; empty without the SCENES capability.

        The first lookup per SKU reads the bundled catalog from disk; :meth:`update` (and
        :meth:`set_scene_by_name`) load it in a worker thread first, so on the event loop this is
        a cache hit once the device has been polled."""
        return sorted(load_scenes(self._sku)) if Capability.SCENES in self.capabilities else []

    @property
    def active_scene(self) -> str | None:
        """Name of the active scene, or None if no scene (or an uncatalogued code) is active, or
        the device has no SCENES capability (as :attr:`scene_names`)."""
        code = self._state.scene_code
        if code is None or Capability.SCENES not in self.capabilities:
            return None
        return scene_names_by_code(self._sku).get(code)

    def register_callback(self, cb: StateCallback) -> Callable[[], None]:
        """Register a state-change callback; returns a function that unregisters it.

        The callback gets the live :attr:`state` (call :meth:`snapshot` to keep a copy). A callback
        that raises is logged and does not affect other callbacks."""
        self._callbacks.append(cb)

        def _unregister() -> None:
            with contextlib.suppress(ValueError):
                self._callbacks.remove(cb)

        return _unregister

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
    def _sync_time_frame() -> bytes:
        """The plugs' ``33 b5`` time sync for now (``SyncTimeController``: [u4be epoch, 1, tzH, tzM])."""
        ts = int(time.time())
        return build.plug_sync_time(ts, *_local_tz(ts))

    def _session_frames(self) -> list[bytes]:
        """The once-per-connection writes after the unlock. A plug's: the ``33 b5`` time sync, which every
        plug screen sends in its connect read and waits to be acked (``SyncTimeController`` is an
        ``AbsOnlyWriteSingleController``, ``super(true)``). gt 43: ``H5083NewDetailRepository.
        necessaryMessages:323-339``, first; gt 50 / 90: ``BleOpV2.o0:141-171`` / ``BleOpV4.l0:124-147``,
        after ``aa 06`` and ``aa 07 03`` (which the library reads once per Device instead,
        ``_readback.read_device_info``); gt 307: ``BleOp4H5089.V:145-163``, first. A light's: ``33 25 01``
        where :attr:`.DeviceProfile.light_effect_stop` is set (bulblight ``VM4Light$connectBleSuc$1.j`` ->
        ``VM4Light.i5``, once the connect reads succeed). A rejection or a missing ack is only logged
        (``GoveeConnection._prepare_session``)."""
        return [*([self._sync_time_frame()] if self.profile.relay else ()),
                *([build.light_effect_stop()] if self.profile.light_effect_stop else ())]

    @staticmethod
    def _checked_secret(secret: bytes | None) -> bytes | None:
        """``secret`` as ``bytes``, or GoveeBleInvalidValue unless it is exactly ``build.SECRET_LEN``
        bytes (``bytes``/``bytearray``/``memoryview``; a ``str`` such as an un-decoded hex string is
        rejected here rather than failing every connect). The message never echoes the value."""
        if secret is None:
            return None
        if not isinstance(secret, (bytes, bytearray, memoryview)):
            raise GoveeBleInvalidValue(
                f"secret must be exactly {build.SECRET_LEN} bytes, got {type(secret).__name__}")
        raw = bytes(secret)
        if len(raw) != build.SECRET_LEN:
            raise GoveeBleInvalidValue(f"secret must be exactly {build.SECRET_LEN} bytes, got {len(raw)}")
        return raw

    def set_secret(self, secret: bytes) -> None:
        """Set the account-lock secret.

        Every new session sends it (the ``33 b2`` unlock right after the handshake). When it
        changes while a session is up - the bootstrap flow: :meth:`read_secret`, then this - the
        next operation on that session sends the ``33 b2`` first, as the app's pairing screen
        writes the secret it has just read on the same connection
        (``AbsPairAc4SecretV1.onEventSecretKey`` -> ``Q1`` -> ``SecretKeyController`` write). Every
        setter, scene upload and read-back (:meth:`update`, :meth:`read_secret`, the segment-count
        read before a colour write) sends it before its own first frame, and so does
        :meth:`connect`.

        Raises GoveeBleInvalidValue if it is not exactly ``build.SECRET_LEN`` bytes."""
        checked = self._checked_secret(secret)
        if checked != self._secret and self.profile.requires_secret:
            self._unlock_pending = True
        self._secret = checked

    async def _send_pending_unlock(self) -> None:
        """Send the ``33 b2`` unlock for a secret set while the session was already up (see
        :meth:`set_secret`). Without a live session nothing is sent: the next session setup sends
        it. A rejection is a GoveeBleAuthError, as at session setup; an unacked unlock is only
        logged, as at session setup (``GoveeConnection._prepare_session``)."""
        if not self._unlock_pending:
            return
        self._unlock_pending = False   # taken: a concurrent operation doesn't send it twice
        secret = self._secret
        if not secret or not self._conn.is_connected:
            return   # the next session setup sends it (or there is none)
        done = False   # the write finished: acked, rejected, or unacked (treated as sent)
        try:
            await self._conn.send(build.secret_check(secret))
            done = True
        except GoveeBleCommandRejected as err:
            done = True
            raise GoveeBleAuthError(
                f"{self._sku}: device rejected the secret key - it is wrong or the device was re-paired"
            ) from err
        except GoveeBleTimeout:
            done = True
            _LOGGER.debug("%s (%s): no ack for unlock frame (continuing)", self.address, self._sku)
        finally:
            # A cancelled or failed write on a session that is still up never reached the device:
            # pending again, so the next operation sends it first. Once the session is gone the next
            # setup sends it anyway, and a set_secret() during the write has re-armed it itself.
            if not done and self._conn.is_connected and self._secret == secret:
                self._unlock_pending = True

    def _check_kelvin(self, kelvin: int) -> int:
        """The Kelvin that goes on the wire (and into ``state``) for ``kelvin``: clamped to the
        device's range (:attr:`min_kelvin` / :attr:`max_kelvin`, which may follow its hardware version),
        then rounded to the 100 K grid, as the app does
        (:func:`build.normalize_kelvin`). Out-of-range input is clamped, not rejected; only a
        non-int raises GoveeBleInvalidValue."""
        return build.normalize_kelvin(kelvin, self.min_kelvin, self.max_kelvin)

    def _color_rgb_frames(self, rgb: tuple[int, int, int], span: tuple[int, int] | None) -> list[bytes]:
        """Frames for a whole-device RGB pick: one, except on a profile whose colour scheme writes the
        panels in groups (``h6061``: ``ceil(n/4)`` frames over :attr:`segment_count` panels). On every
        scheme with a colour-temperature mode, a colour that is exactly one of the app's Kelvin tint
        colours within the Kelvin range ``span`` (:meth:`_kelvin_range`, taken once per write so the
        frame and the state agree) is the single CCT frame instead, as the app sends it
        (:func:`build.rgb_as_kelvin`)."""
        r, g, b = rgb
        return self._build_or_unsupported(
            build.color_rgb_frames, r, g, b, self.profile.color_scheme, self.segment_count, span,
            pieces=self.profile.segments or None)

    def update_ble_device(self, ble_device: BLEDevice) -> None:
        """Swap in a fresh BLEDevice (e.g. a new scanner path) for later connects."""
        self._ble_device = ble_device
        self._conn.update_ble_device(ble_device)

    def ingest_advertisement(self, advertisement_data: AdvertisementData) -> bool:
        """Update passive state from a BLE advertisement — no connection needed.

        Reads on/off from the Govee manufacturer data (identify.parse_broadcast_onoff, decoded
        with the profile's ``advert_on_mask`` on the plugs); returns True if `state.is_on` changed
        (so a caller can notify selectively). Ignored for ``ADVERT_SUPPRESS_SECONDS`` after a
        command or authoritative read."""
        mfg = getattr(advertisement_data, "manufacturer_data", None)
        if not mfg:
            return False
        self._learn_pact(from_advertisement(advertisement_data, self._ble_device.name))
        on = parse_broadcast_onoff(mfg, on_mask=self.profile.advert_on_mask)
        if on is None or on == self._state.is_on:
            return False
        if time.monotonic() < self._advert_ignore_until:
            return False  # fresher state from a command/read; advert lags
        _fold.apply_advert_power(self.profile, self._state, on)   # zones kept consistent (state only)
        self._notify()
        return True

    async def connect(self) -> None:
        """Establish the BLE session now (connect, handshake, unlock) instead of lazily on
        the first command. Idempotent; the connection's idle timer still applies afterwards."""
        # A secret set on the live session goes out on it first; without one, the session setup
        # below sends it (so it is never sent twice).
        await self._send_pending_unlock()
        await self._conn.connect()

    async def stop(self) -> None:
        """Disconnect and refuse further I/O (the owner is being torn down)."""
        await self._conn.close()

    def _mark_authoritative(self) -> None:
        self._advert_ignore_until = time.monotonic() + ADVERT_SUPPRESS_SECONDS

    def _finish_read(self, applied: bool, power_read: bool) -> None:
        """Close a read-back: ``applied`` (a live field came from the device) ends the optimistic
        state; only ``power_read`` (on/off came from the device) makes it authoritative over the
        lagging advertised on/off (see :meth:`ingest_advertisement`). A read that got nothing, or
        only brightness/mode, leaves the advert as the on/off source."""
        if applied:
            self._state.optimistic = False
        if power_read:
            self._mark_authoritative()

    def _zone_power_lock(self) -> contextlib.AbstractAsyncContextManager[None]:
        """``_bar_lock`` on a bar-switch device (whose zone write is a read-modify-write of both bars,
        :meth:`_set_bar_power`), else a no-op: every writer of ``zone_power`` takes it, so a bar write
        never builds its frame from bars that a concurrent power/scene write is changing."""
        if self.profile.bar_switch:
            return self._bar_lock
        return contextlib.nullcontext()

    def _mutated(self) -> None:
        """Record that a setter changed (or is applying) device state; see ``_mutation_seq``."""
        self._mutation_seq += 1

    async def _commit(
        self, frames: bytes | list[bytes], apply: Callable[[], None], *, authoritative: bool = True
    ) -> None:
        """Write ``frames`` and fold the change into state: the protocol every setter follows.

        One frame goes out with ``send``, a list with ``send_many`` (one connection-lock hold, each
        frame ack-verified in order). Once the write succeeded: bump ``_mutation_seq`` (an in-flight
        read is then discarded, :meth:`_read_fresh`), ``apply`` the optimistic state, make it
        authoritative over the lagging advertised on/off for ``ADVERT_SUPPRESS_SECONDS`` (unless
        ``authoritative`` is False, as for the gradual flag) and notify. A failed write raises and
        applies nothing. A secret set on the live session goes out first (:meth:`set_secret`)."""
        await self._send_pending_unlock()
        if isinstance(frames, bytes):
            await self._conn.send(frames)
        else:
            await self._conn.send_many(frames)
        self._mutated()
        apply()
        if authoritative:
            self._mark_authoritative()
        self._notify()

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
        """The device-reported brightness byte as a 0..100 percent (:func:`._fold.brightness_pct`)."""
        return _fold.brightness_pct(self.profile, raw, self._pact)

    # -- power / brightness -------------------------------------------------
    def _brightness_frame(self, pct: int) -> bytes:
        """33 04 for a 1..100 percent: verbatim on percent devices, mapped onto the raw
        range in force on devices with one (``brightness_range``, or ``brightness_by_pact`` for the
        advertised pact: :func:`._fold.brightness_range`; :func:`.brightness.percent_to_device`)."""
        span = _fold.brightness_range(self.profile, self._pact)
        if span is not None:
            return build.brightness_raw(percent_to_device(pct, *span))
        return build.brightness(pct)

    def _power_frames(self, on: bool) -> list[bytes]:
        """Frames for whole-device power: always a single frame where the device
        allows it - the global 33 01 switch (which on H60A6 also drives both
        zones; H6047's main switch sends it too - ``33 36`` is only its per-bar
        buttons, see :meth:`set_zone_power`). Only a zoned fixture without a
        zone-driving master switch falls back to one 33 30 frame per zone."""
        if self.profile.zones and not self.profile.global_power and not self.profile.bar_switch:
            return [build.zone_power(z.power_index, on) for z in self.profile.zones]
        if self.profile.relay:
            return [self._relay_power_frame(on)]
        return [build.switch(on)]

    def _relay_power_frame(self, on: bool) -> bytes:
        """A plug's main-switch frame for its goodsType (:data:`.profile.RelayPower`)."""
        kind = self.profile.relay_power
        if kind == "outlet0":
            return build.switch(on, relay=True)
        if kind == "all_outlets":
            return build.plug_all_outlets(on)
        if kind == "total_switch":
            return build.plug_total_switch(on)
        _unhandled(kind, "relay_power")

    def _apply_power(self, on: bool) -> None:
        """Optimistic state for whole-device power and the zones it drives (:func:`._fold.apply_power`)."""
        _fold.apply_power(self.profile, self._state, on)

    async def set_power(self, on: bool) -> None:
        """Switch the device on or off.

        Raises GoveeBleNotSupported without POWER; connection errors propagate. A plug with
        :attr:`.DeviceProfile.power_resync` then re-reads as its app does (:meth:`_resync_after_power`);
        a failure there is only logged."""
        self._require(Capability.POWER)
        async with self._zone_power_lock():
            await self._commit(self._power_frames(on), lambda: self._apply_power(on))
        await self._resync_after_power()

    async def _resync_after_power(self) -> None:
        """gt 50 / 90 (:attr:`.DeviceProfile.power_resync`): after every acked power write the app
        re-runs its connect read list (``BleOpV2.onEventSwitchV2:380-382`` / ``BleOpV4.onEventSwitchV2:
        299-301`` -> ``G()`` = ``BleOpV2.o0:141-171`` / ``BleOpV4.l0:124-147``): ``aa 06``, ``aa 07 03``,
        ``33 b5``, then per-outlet timer and delay reads, which the library does not model. The power
        write already succeeded and its state is applied, so a failure here neither raises nor undoes
        it. Other profiles send nothing."""
        if not self.profile.power_resync:
            return
        try:
            await _readback.read_versions(self, self._read_reply)
            await self._conn.send(self._sync_time_frame())
        except (GoveeBleCommandRejected, GoveeBleTimeout) as err:
            _LOGGER.debug("%s (%s): post-power re-read not answered: %s", self.address, self._sku, err)
        except GoveeBleError as err:
            _LOGGER.warning(
                "%s (%s): power written but the post-power re-read failed: %s",
                self.address, self._sku, str(err) or type(err).__name__,
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
        the power frames (already on). ``brightness`` is clamped to 1..100 as in
        :meth:`set_brightness`; ``kelvin`` is clamped and rounded as in :meth:`set_color_temp`.

        Raises GoveeBleInvalidValue before any I/O for both ``rgb`` and ``kelvin``, an ``rgb`` that is
        not an (r, g, b) triple of 0..255 ints, or a non-int brightness or kelvin;
        GoveeBleNotSupported without the capability a requested part needs (POWER, BRIGHTNESS, RGB,
        COLOR_TEMP) or when the colour scheme can't express it; GoveeBleError if a device that
        reports its own segment count (H6062) does not answer that read."""
        if rgb is not None and kelvin is not None:
            raise GoveeBleInvalidValue("turn_on: pass either rgb or kelvin, not both")
        pick = _check_rgb(rgb) if rgb is not None else None
        pct = _check_pct(brightness) if brightness is not None else None
        kelvin = self._check_kelvin(kelvin) if kelvin is not None else None
        if pick is not None or kelvin is not None:
            await self._ensure_segment_count()
        frames: list[bytes] = []
        if power:
            self._require(Capability.POWER)
            frames += self._power_frames(True)
        if pct is not None:
            self._require(Capability.BRIGHTNESS)
            frames.append(self._brightness_frame(pct))
        span = self._kelvin_range()   # once: the frame and the state use the same range
        if pick is not None:
            self._require(Capability.RGB)
            frames += self._color_rgb_frames(pick, span)
        elif kelvin is not None:
            self._require(Capability.COLOR_TEMP)
            frames.append(self._build_or_unsupported(
                build.color_temp, kelvin, self.profile.color_scheme, self.segment_count,
                pieces=self.profile.segments or None))
        if not frames:
            return

        def apply() -> None:
            if power:
                self._apply_power(True)
            if pct is not None:
                self._state.brightness = pct
            if pick is not None:
                _fold.apply_rgb(self.profile, self._state, pick, span)
            elif kelvin is not None:
                _fold.apply_kelvin(self.profile, self._state, kelvin)

        async with self._zone_power_lock():
            await self._commit(frames, apply)
        if power:
            await self._resync_after_power()

    async def turn_off(self) -> None:
        """Power off; equivalent to ``set_power(False)``."""
        await self.set_power(False)

    async def set_brightness(self, pct: int) -> None:
        """Set brightness in percent (clamped to 1..100; 0 is never sent).

        Raises GoveeBleNotSupported without BRIGHTNESS; GoveeBleInvalidValue for a non-int."""
        self._require(Capability.BRIGHTNESS)
        level = _check_pct(pct)

        def apply() -> None:
            self._state.brightness = level

        await self._commit(self._brightness_frame(level), apply)

    # -- colour -------------------------------------------------------------
    async def set_rgb(self, rgb: tuple[int, int, int]) -> None:
        """Set the whole-device colour (clears colour temperature and active scene).

        Raises GoveeBleNotSupported without RGB; GoveeBleInvalidValue (before any I/O) unless
        ``rgb`` is an (r, g, b) triple of ints in 0..255; GoveeBleError if a device that reports
        its own segment count (H6062) does not answer that read."""
        self._require(Capability.RGB)
        pick = _check_rgb(rgb)
        await self._ensure_segment_count()
        span = self._kelvin_range()   # once: the frame and the state use the same range
        await self._commit(self._color_rgb_frames(pick, span),
                           lambda: _fold.apply_rgb(self.profile, self._state, pick, span))

    async def set_color_temp(self, kelvin: int) -> None:
        """Set the whole-device colour temperature (clears rgb and active scene).

        Raises GoveeBleNotSupported without COLOR_TEMP and GoveeBleInvalidValue for a non-int;
        GoveeBleError if a device that reports its own segment count (H6062) does not answer
        that read. Like the app, an out-of-range value is clamped to the device's Kelvin range
        (:attr:`min_kelvin` / :attr:`max_kelvin`; not rejected) and then rounded to the nearest 100 K,
        the only values the app sends; the Kelvin actually sent is what ``state`` keeps, and the frame
        carries the app's tint."""
        self._require(Capability.COLOR_TEMP)
        sent = self._check_kelvin(kelvin)
        await self._ensure_segment_count()
        frame = self._build_or_unsupported(
            build.color_temp, sent, self.profile.color_scheme, self.segment_count,
            pieces=self.profile.segments or None)
        await self._commit(frame, lambda: _fold.apply_kelvin(self.profile, self._state, sent))

    async def set_segment_rgb(self, indices: Sequence[int], rgb: tuple[int, int, int]) -> None:
        """Set the colour of selected segments (indices 0 .. :attr:`segment_count` - 1).

        Raises GoveeBleNotSupported without SEGMENTS; GoveeBleInvalidValue for a bad index or
        unless ``rgb`` is an (r, g, b) triple of ints in 0..255; GoveeBleError if a device that
        reports its own segment count (H6062) does not answer that read."""
        self._require(Capability.SEGMENTS)
        pick = _check_rgb(rgb)
        r, g, b = pick
        await self._ensure_segment_count()
        n = self.segment_count
        frame = self._build_or_unsupported(
            build.segment_rgb, _mask(indices, n), r, g, b, self.profile.color_scheme, n,
            self._kelvin_range())

        carried = self._carried(indices, build.COLOR_MASK_CAP)

        def apply() -> None:
            _fold.update_segments(self._state, carried, rgb=pick)
            self._state.scene_code = None   # a segment colour write leaves scene mode, as set_rgb does

        await self._commit(frame, apply)

    async def set_segment_brightness(self, indices: Sequence[int], pct: int) -> None:
        """Set brightness in percent (clamped to 1..100; 0 is never sent) on selected segments (indices
        as in :meth:`set_segment_rgb`). The app's segment slider runs 1..100 (``ViewColorMode.t0:232-235``
        / ``DelegateColorV2Part.t0:459-462``: ``setProgressRange({1, 100})``).

        Raises GoveeBleNotSupported without SEGMENTS or for colour schemes without per-segment
        brightness (the h61a8 0x0b scheme); GoveeBleInvalidValue for a non-int ``pct`` or a bad index;
        GoveeBleError if a device that reports its own segment count (H6062) does not answer
        that read."""
        self._require(Capability.SEGMENTS)
        level = _check_pct(pct)
        await self._ensure_segment_count()
        n = self.segment_count
        frame = self._build_or_unsupported(
            build.segment_brightness, _mask(indices, n), level, self.profile.color_scheme, n)

        carried = self._carried(indices, build.BRIGHTNESS_MASK_CAP)

        def apply() -> None:
            _fold.update_segments(self._state, carried, brightness=level)
            self._state.scene_code = None   # the 33 05 15 02 write is a colour-mode write too

        await self._commit(frame, apply)

    def _carried(self, indices: Sequence[int], mask_cap: int) -> list[int]:
        """The ``indices`` a 0x15 frame's segment mask carries: past the body's ``mask_cap`` bytes the app's
        writers drop the positions (coerceAtMost; :func:`.build.mask_positions`), so the device leaves
        those segments as they were, and so does the state. Other schemes' masks carry every index."""
        if self.profile.color_scheme != "h60a6":
            return list(indices)
        reach = build.mask_positions(self.segment_count or build.LEGACY_MASK_SEGMENTS, mask_cap)
        return [i for i in indices if i < reach]

    def _build_or_unsupported(self, builder: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs) -> _T:
        """Run a frame builder; when the colour scheme can't express the request (the builder's
        GoveeBleNotSupported) re-raise it prefixed with this SKU. Every builder call that depends on
        the colour scheme goes through here. Bad caller input (GoveeBleInvalidValue) and a builder's
        own programming error (a plain ValueError, e.g. ``frame()``'s payload length) propagate
        unchanged: neither means "not supported"."""
        try:
            return builder(*args, **kwargs)
        except GoveeBleNotSupported as exc:
            if str(exc).startswith(f"{self._sku}: "):
                raise   # already names this SKU
            raise GoveeBleNotSupported(f"{self._sku}: {exc}") from exc

    async def set_segment_color_temp(self, indices: Sequence[int], kelvin: int) -> None:
        """Colour temperature on selected segments (0x15 family only — the CCT frame
        carries the segment mask). ``kelvin`` is clamped and rounded as in :meth:`set_color_temp`.

        Raises GoveeBleNotSupported without SEGMENTS/COLOR_TEMP or for non-maskable schemes;
        GoveeBleInvalidValue for a non-int ``kelvin`` or a bad index; GoveeBleError if a device
        that reports its own segment count (H6062) does not answer that read."""
        self._require(Capability.SEGMENTS)
        self._require(Capability.COLOR_TEMP)
        sent = self._check_kelvin(kelvin)
        await self._ensure_segment_count()
        n = self.segment_count
        frame = self._build_or_unsupported(
            build.segment_color_temp, _mask(indices, n), sent, self.profile.color_scheme, n)

        def apply() -> None:
            self._state.scene_code = None   # a colour-mode write leaves scene mode, as set_color_temp does

        await self._commit(frame, apply)

    # -- zones --------------------------------------------------------------
    def _zone(self, name: str) -> Zone:
        for z in self.profile.zones:
            if z.name == name:
                return z
        raise GoveeBleUnknownName(f"{self._sku}: unknown zone {name!r}")

    def zone_color_temp_kelvin(self, name: str) -> int | None:
        """Last known colour temperature (Kelvin) of zone ``name``, or None when the zone is not
        known to be in colour-temperature mode or the device doesn't report it per zone (only
        H6047 does: one kelvin per bar from ``aa 05 15``).

        Raises GoveeBleInvalidValue (also a GoveeBleNotSupported) for an unknown zone."""
        return self._state.zone_color_temp_kelvin.get(self._zone(name).power_index)

    def zone_is_on(self, name: str) -> bool | None:
        """Last known power state of zone ``name``, or None if unknown.

        Raises GoveeBleInvalidValue (also a GoveeBleNotSupported) for an unknown zone."""
        return self._state.zone_power.get(self._zone(name).power_index)

    def _apply_zone_power(self, zone_power: dict[int, bool]) -> None:
        """Optimistic state for a zone power write (:func:`._fold.apply_zone_power`)."""
        _fold.apply_zone_power(self.profile, self._state, zone_power)

    async def set_zone_power(self, name: str, on: bool) -> None:
        """Switch one zone on or off.

        On bar-switch devices the other bar's state is read first if unknown and preserved;
        GoveeBleError if that read-back fails. Raises GoveeBleInvalidValue (also a
        GoveeBleNotSupported) for an unknown zone."""
        z = self._zone(name)
        if self.profile.bar_switch:
            async with self._bar_lock:
                await self._set_bar_power(z.power_index, on)
            return

        def apply() -> None:
            self._apply_zone_power({**self._state.zone_power, z.power_index: on})

        await self._commit(build.zone_power(z.power_index, on), apply)

    async def _set_bar_power(self, index: int, on: bool) -> None:
        """Bar-switch read-modify-write; the caller holds ``_bar_lock`` (which every other writer of
        ``zone_power`` on a bar-switch device takes too, :meth:`_zone_power_lock`)."""
        # One frame carries BOTH bars, so the other bar's current state must be
        # known - never assume it (assuming "on" turned the other bar ON).
        zp = self._state.zone_power
        discarded = False
        if len(zp) < 2:
            discarded = await self._read_bar_switch() == "stale"
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
        await self._commit(build.bar_switch(target[0], target[1]), lambda: self._apply_zone_power(target))

    async def _read_bar_switch(self) -> _ReadResult:
        """Read the bar switch into state: "applied" (bars and the derived on/off were read back),
        "stale" (the reply was discarded: a setter applied state meanwhile) or "none" (no usable
        reply)."""
        def apply(bars: tuple[bool, bool]) -> None:
            _fold.apply_bar_switch(self._state, bars)

        return await self._read_command(
            "bar-switch", build.bar_switch_query(), parse.parse_bar_switch, apply)

    async def set_zone_rgb(self, name: str, rgb: tuple[int, int, int]) -> None:
        """Set the colour of one zone (its segment mask).

        Raises GoveeBleNotSupported without SEGMENTS/RGB or for a zone without segments;
        GoveeBleInvalidValue for an unknown zone (also a GoveeBleNotSupported) or unless ``rgb`` is
        an (r, g, b) triple of ints in 0..255; GoveeBleError if a device that reports its own
        segment count (H6062) does not answer that read."""
        self._require(Capability.SEGMENTS)
        self._require(Capability.RGB)
        z = self._zone(name)
        if not z.segments:
            raise GoveeBleNotSupported(f"{self._sku}: zone {name!r} has no segments")
        pick = _check_rgb(rgb)
        r, g, b = pick
        await self._ensure_segment_count()
        n = self.segment_count
        kelvin_range = self._kelvin_range()
        frame = self._build_or_unsupported(
            build.segment_rgb, _mask(z.segments, n), r, g, b, self.profile.color_scheme, n, kelvin_range)

        def apply() -> None:
            _fold.update_segments(self._state, z.segments, rgb=pick)
            self._state.scene_code = None   # a zone colour write leaves scene mode, as set_rgb does
            # A pick equal to a tint colour went out as the zone's CCT frame (build.rgb_as_kelvin):
            # the bar then reports that Kelvin, like set_zone_color_temp.
            _fold.apply_zone_kelvin(self.profile, self._state, {z.power_index: build.rgb_as_kelvin(
                r, g, b, self.profile.color_scheme, kelvin_range)})

        await self._commit(frame, apply)

    async def set_zone_color_temp(self, name: str, kelvin: int) -> None:
        """Colour temperature for one zone (its segment mask). Physically supported on the
        0x15 family (H60A6/H6047), whose CCT frame carries a segment mask. ``kelvin`` is clamped
        and rounded as in :meth:`set_color_temp`.

        Raises GoveeBleNotSupported without SEGMENTS/COLOR_TEMP, for a zone without segments or for
        a colour scheme whose CCT frame carries no segment mask; GoveeBleInvalidValue for an unknown
        zone (also a GoveeBleNotSupported) or a non-int ``kelvin``; GoveeBleError if a device that
        reports its own segment count (H6062) does not answer that read."""
        self._require(Capability.SEGMENTS)
        self._require(Capability.COLOR_TEMP)
        sent = self._check_kelvin(kelvin)
        z = self._zone(name)
        if not z.segments:
            raise GoveeBleNotSupported(f"{self._sku}: zone {name!r} has no segments")
        await self._ensure_segment_count()
        n = self.segment_count
        frame = self._build_or_unsupported(
            build.segment_color_temp, _mask(z.segments, n), sent, self.profile.color_scheme, n)

        def apply() -> None:
            self._state.scene_code = None   # a zone colour write leaves scene mode, as set_color_temp does
            _fold.apply_zone_kelvin(self.profile, self._state, {z.power_index: sent})

        await self._commit(frame, apply)

    # -- scenes -------------------------------------------------------------
    async def set_scene(self, code: int) -> None:
        """Activate a scene by its BLE code (no upload).

        Raises GoveeBleNotSupported without SCENES."""
        self._require(Capability.SCENES)
        async with self._scene_lock, self._zone_power_lock():
            await self._activate_scene(code)

    async def _activate_scene(self, code: int) -> None:
        """Send the ``33 05 04`` activation and apply its state (the device is on, in that scene, with
        no solid colour); the caller holds ``_scene_lock`` and :meth:`_zone_power_lock`."""
        await self._commit(build.scene_activate(code),
                           lambda: _fold.apply_scene(self.profile, self._state, code))

    def scene_upload_frames(self, scene: Scene, *, mtu: int = 20) -> list[bytes] | None:
        """The a3/a4 frames that upload ``scene`` to this device (per its profile's scene
        dialect and supported scene versions), or None when it is activate-only. ``mtu`` is the
        link's write size (``GoveeConnection.write_payload_size``), which only the H60A6's 0xA4
        graffiti upload uses. Pure: no I/O."""
        return _scene_upload_frames(scene, self.profile.scene_dialect, self.scene_versions, mtu=mtu)

    async def set_scene_by_name(self, name: str) -> None:
        """Upload (if the scene needs it) and activate a bundled scene by name.

        Raises GoveeBleNotSupported without SCENES, and GoveeBleInvalidValue (also a
        GoveeBleNotSupported) for an unknown name, sending nothing. The bundled catalog is loaded in
        a worker thread, never on the event loop.

        The upload and the activation go out back to back: the app sends them as one composed
        controller (``BaseSceneViewMode.getSceneComposeController`` -> ``Compose4DefWrite4Multi``, an
        ``AbsComposeControllerWithOpResult`` that ``AbsComposeComm`` writes controller by
        controller). So no other scene write (``_scene_lock``) and no read-back (``_read_gate``: a
        poll waits for the activation) lands between them; other setters are not held back."""
        self._require(Capability.SCENES)
        scene = (await async_load_scenes(self._sku)).get(name)
        if scene is None:
            raise GoveeBleUnknownName(f"{self._sku}: unknown scene {name!r}")
        frames = self.scene_upload_frames(scene)   # planned before any I/O: a bad scene sends nothing

        def sized(size: int) -> list[bytes]:
            # Re-planned for the link it goes out on: the 0xA4 graffiti frames take its MTU.
            return self.scene_upload_frames(scene, mtu=size) or []

        async with self._scene_lock, self._zone_power_lock(), self._read_gate:
            await self._send_pending_unlock()   # before the upload, not between it and the activation
            if frames:
                await self._conn.send_unacked(sized)
            await self._activate_scene(scene.code)

    # -- misc settings ------------------------------------------------------
    async def set_gradual(self, on: bool) -> None:
        """Toggle the gradual/fade-on-BLE↔wifi-handoff flag (0xA3). Only SKUs whose profile
        sets ``gradual`` (H61A8, H6047, H6641) support it; others raise GoveeBleNotSupported."""
        if not self.profile.gradual:
            raise GoveeBleNotSupported(f"{self._sku}: gradual (BLE/wifi fade) not supported")

        def apply() -> None:
            self._state.gradual = on

        await self._commit(build.gradual(on), apply, authoritative=False)

    # -- read-back ----------------------------------------------------------
    async def update(self) -> DeviceState:
        """Poll the device and fold the replies into state; returns the current state.

        Reads are not serialized against setters (the connection lock is per-operation), so a
        setter can complete while a read is in flight. Each read therefore snapshots
        ``_mutation_seq`` before it is issued and is DISCARDED (DEBUG log; no state merge, no
        ``_mark_authoritative``) if a setter applied state since (:meth:`_read_fresh`). A discarded
        read means "state is newer than this poll": ``update()`` still returns the current
        (setter-applied) state and notifies, but does not consume the one-time "first poll" step
        (``_polled_once``); callers simply poll again later. Static device-info is exempt (never
        raced by setters)."""
        discarded_before = self._discarded_reads
        await self._send_pending_unlock()
        if not self._scenes_warm and Capability.SCENES in self.capabilities:
            # Load the bundled scene catalog off the loop once, so the sync scene_names /
            # active_scene properties are cache hits when read on the event loop.
            self._scenes_warm = True
            try:
                await async_load_scenes(self._sku)
            except GoveeBleError as err:
                _LOGGER.debug("%s (%s): scene catalog not loaded: %s", self.address, self._sku, err)
        state_read = False   # this poll's state read brought a live field back (_finish_read)
        if self.profile.readback == "status":
            state_read = await self._read_status()
        elif self.profile.readback == "polled":
            state_read = await self._read_polled()
        elif self.profile.readback == "plug":
            state_read = await self._read_plug()
        elif self.profile.readback == "none":
            pass
        else:
            _unhandled(self.profile.readback, "readback")
        if self.profile.ic_num_read_only and not self.profile.color_readback_live_ic:
            await self._read_reply(build.ic_count_query())   # the screen's connect read; it sizes nothing
        # Segment / single-colour read-back layered on top (spec Change 7).
        if self.profile.color_readback == "mechanism_b":
            await self._read_mechanism_b()
        elif self.profile.color_readback == "mechanism_a_direct":
            if self.profile.section_count_read and self._segment_count is None:
                _LOGGER.debug("%s (%s): segment count unknown; skipping segment colour read-back",
                              self.address, self._sku)
            else:
                await self._read_mechanism_a_direct()
        elif self.profile.color_readback == "":
            pass   # no segment colour read-back ("" is ColorReadBack's "none")
        else:
            _unhandled(self.profile.color_readback, "color_readback")
        if self.profile.gradual:
            def apply_gradual(on: bool) -> None:
                self._state.gradual = on

            await self._read_command("gradual", build.gradual_query(),
                                     parse.parse_gradual, apply_gradual)
        elif self.profile.gradual_read_only:
            await self._read_reply(build.gradual_query())   # the screen's connect read; nothing is exposed
        # Device-info (mac/hw/fw/serial) — static-ish, read once the device answers one of them.
        if not self._device_info_read and self.profile.readback != "none":
            try:
                await self._read_device_info(state_read=state_read)
            except GoveeBleError as err:
                # Flag stays False so the next poll retries; the state read above is still good.
                _LOGGER.debug("%s (%s): device-info read failed (%s); retrying next poll",
                              self.address, self._sku, err)
        if self._discarded_reads != discarded_before:
            _LOGGER.debug(
                "%s (%s): poll partially discarded (state newer than poll)", self.address, self._sku)
        elif not self._polled_once and self.profile.readback != "none" and not self._state.optimistic:
            # Only a poll that read something back consumes the one-time line (an all-silent poll
            # would log "on=None bri=None ..." and hide the first real read-back).
            _LOGGER.info("%s (%s) first poll: %s", self.address, self._sku, _fold.summary(self._state))
            self._polled_once = True
        elif _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug("%s (%s) state: %s", self.address, self._sku, _fold.summary(self._state))
        self._notify()
        return self._state

    def _apply_mode_reply(self, reply: bytes) -> None:
        """Fold an aa 05 mode report into state (:func:`._fold.apply_mode_reply`): active scene, or
        the colour / colour temperature of the solid-colour sub-mode."""
        _fold.apply_mode_reply(self.profile, self._state, reply)

    async def _read_plug(self) -> bool:
        """Poll a plug's relay state (aa 01 -> relay bitmask) so state.is_on reflects the device
        rather than the last command sent. On means one of the profile's ``relay_on_mask`` bits is
        set (:func:`.parse.parse_relay_on`): bit 0 on the single-outlet SKUs, as the app's
        BleOpV1/BleOpV3 detail screens read it. Returns whether the relay state was read back."""
        prof = wire_profile(self.profile)

        def apply(on: bool) -> None:
            self._state.is_on = on

        applied = await self._read_command(
            "plug power", build.power_query(), lambda r: parse.parse_relay_on(r, prof), apply) == "applied"
        self._finish_read(applied, power_read=applied)
        return applied

    async def _read_device_info(self, *, state_read: bool = False) -> None:
        """Populate the static device-info fields (``aa 06``, ``aa 07 03``, ``aa 07 02``;
        :func:`._readback.read_device_info`). Best-effort, and done once the device has answered;
        unanswered reads leave fields None. ``state_read``: the same poll's state read brought a
        live field back."""
        answered = await _readback.read_device_info(self, self._read_reply)
        # Done once the reads completed without raising on a device that answered in this poll: one
        # of these reads, or the poll's state read. An unanswered selector on a responsive device is
        # not retried every poll (each costs INFO_READ_TIMEOUT), but a poll on which the device was
        # silent throughout (wrong cipher, lost notifications, out of range) or a link drop mid-read
        # leaves it for the next poll, even when an earlier poll did read state.
        self._device_info_read = answered or state_read

    def _merge_device_info(
        self,
        *,
        serial: str | None = None,
        wifi_mac: str | None = None,
        firmware: str | None = None,
        hardware: str | None = None,
    ) -> None:
        """Copy each non-None device-info field onto state (:func:`._fold.merge_device_info`)."""
        _fold.merge_device_info(
            self._state, serial=serial, wifi_mac=wifi_mac, firmware=firmware, hardware=hardware)

    async def _read_mechanism_b(self) -> None:
        """H61A8's per-segment colour via BulbGroupColorV2 (``aa a5 <seq>``, V2;
        :func:`._readback.walk_mechanism_b`), applied whole unless a setter raced it."""
        await self._read_fresh("segment colour", lambda: _readback.walk_mechanism_b(self, self._read_reply),
                               self._apply_segments)

    def _apply_segments(self, segments: list[Segment]) -> None:
        """Fold a whole segment-colour read-back into state."""
        self._state.segments = segments

    async def _read_mechanism_a_direct(self) -> None:
        """H6047/H6641/H6062/H6061 per-segment colour via Controller4ColorInfoByGroup's direct
        per-group requests (``aa a5 <group>``; :func:`._readback.walk_mechanism_a_direct`), applied
        whole unless a setter raced it."""
        await self._read_fresh("segment colour",
                               lambda: _readback.walk_mechanism_a_direct(self, self._read_reply),
                               self._apply_segments)

    def _wants_color_groups(self) -> bool:
        """Whether the 0xAC status read asks for the 0xA5 colour groups (``status_color_groups_pact``):
        always without a gate or before a pact has been advertised, else only for that pact type with
        at least that pact code (H60A6: ``VM4LightH60A6.J5``)."""
        gate = self.profile.status_color_groups_pact
        if gate is None or self._pact is None:
            return True
        return self._pact[0] == gate[0] and self._pact[1] >= gate[1]

    def _status_types(self) -> tuple[int, ...]:
        """The TLV types the 0xAC status read requests: the profile's ``status_read``, without the 0xA5
        colour groups where :meth:`_wants_color_groups` rules them out."""
        types = self.profile.status_read
        if self._wants_color_groups():
            return types
        return tuple(t for t in types if t != reassemble.TYPE_COLOR_GROUP)

    async def _query_status(self) -> tuple[reassemble.StatusReply, list[bytes], list[bytes]]:
        async with self._read_gate:   # never between a scene's upload and its activation
            await self._send_pending_unlock()   # a secret set on the live session goes first
            frames = await self._conn.query(
                build.status_query(types=self._status_types()), timeout=STATUS_READ_TIMEOUT)
        ac = [f for f in frames if f and f[0] == build.PRO_MULTI_AC]
        status = reassemble.parse_status(ac, pieces=self.segment_count, decode=wire_profile(self.profile))
        return status, ac, frames

    async def _read_status(self) -> bool:
        # `applied` (returned): a live field came back (state no longer optimistic). `power_read`:
        # on/off came back, so the advertised on/off is suppressed for a while (_finish_read).
        # Neither is set by an empty burst (the "state left stale" case) nor by the static
        # device-info fields.
        applied = power_read = False

        def apply(st: reassemble.StatusReply) -> None:
            nonlocal applied, power_read
            applied, power_read = _fold.apply_status(self.profile, self._state, st, self._pact)

        # The burst is the whole poll, as the app's connect read is: its 0x05 TLV carries the mode
        # (_fold.apply_status), so no aa 05 read follows it.
        await self._read_fresh("status", lambda: _readback.status_burst(self, self._query_status), apply)
        self._finish_read(applied, power_read)
        return applied

    async def _read_polled(self) -> bool:
        """The polled read-back (aa 01 / aa 04 / aa 36 / aa 41 / aa 05); returns whether a live field
        came back (see :meth:`_read_status`)."""
        def apply_power(on: bool) -> None:
            self._state.is_on = on

        def apply_brightness(raw: int) -> None:
            self._state.brightness = self._brightness_pct(raw)

        # Always read power: a device turned on externally (app, remote, wall
        # switch) must be noticed even when we last saw it off.
        # A light is on only for a byte of 1, as the app's control screens read it (parse_light_power).
        power_read = await self._read_command(
            "power", build.power_query(), parse.parse_light_power, apply_power) == "applied"
        applied = power_read   # see _read_status / _finish_read
        if await self._read_command("brightness", build.brightness_query(),
                                    parse.parse_brightness, apply_brightness) == "applied":
            applied = True
        if self.profile.bar_switch and await self._read_bar_switch() == "applied":
            applied = power_read = True   # the bars set is_on (device on iff either bar is on)
        if self.profile.section_count_read:
            # The app's connect order: aa 01, aa 04, aa 41, aa 05 (ControllerStrategyConfig.O).
            await self._read_section_count()
        if self.capabilities & _MODE_REPORT_CAPS and await self._read_mode() == "applied":
            applied = True
        self._finish_read(applied, power_read)
        return applied

    async def _read_mode(self) -> _ReadResult:
        """Read the aa 05 mode report into state (:meth:`_apply_mode_reply`)."""
        prof = wire_profile(self.profile)
        return await self._read_command("mode", build.mode_query(), lambda r: parse.mode_reply(r, prof),
                                        lambda m: _fold.apply_mode(self.profile, self._state, m))

    async def _read_section_count(self) -> bool:
        """Read the segment count the device reports (``aa 41`` -> ``sectionNum``) and adopt it
        as :attr:`segment_count`, as the app does on every connect. Returns False (state left as
        it was) when the device does not answer or reports a count the masks cannot carry."""
        reply = await self._read_reply(build.section_count_query())
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

    async def _read_fresh(
        self, what: str, read: Callable[[], Awaitable[_T | None]], apply: Callable[[_T], None]
    ) -> _ReadResult:
        """One read-back under the protocol every reader follows (BUG-4, see ``_mutation_seq``):
        snapshot ``_mutation_seq``, ``read`` (None: no usable reply), then ``apply`` the value only
        if no setter applied state meanwhile; otherwise it is discarded and logged as a stale
        ``what`` read. Returns which of the three happened."""
        seq = self._mutation_seq
        value = await read()
        if value is None:
            return "none"
        if self._stale(seq, what):
            return "stale"
        apply(value)
        return "applied"

    async def _read_command(
        self, what: str, frame: bytes,
        parser: Callable[[bytes], _T | None], apply: Callable[[_T], None],
    ) -> _ReadResult:
        """:meth:`_read_fresh` of one ``aa`` read: the reply to ``frame``, decoded by ``parser`` (None: not
        a usable reply)."""
        async def read() -> _T | None:
            reply = await self._read_reply(frame)
            return None if reply is None else parser(reply)

        return await self._read_fresh(what, read, apply)

    async def _read_reply(self, frame: bytes) -> bytes | None:
        """Send one ``aa`` read and return its reply - the ``aa`` frame with the request's own command
        (:func:`.parse.request_command`) - or None after ``INFO_READ_TIMEOUT``. A single-reply read:
        ``idle_gap=None``, so a stray ``aa`` frame (a late reply to an earlier read) can't cut the wait
        short to the burst idle gap."""
        command_type = parse.request_command(frame)
        async with self._read_gate:   # never between a scene's upload and its activation
            await self._send_pending_unlock()   # a secret set on the live session goes first
            frames = await self._conn.query(
                frame, opcode=build.PRO_READ, terminal=command_type, timeout=INFO_READ_TIMEOUT,
                idle_gap=None)
        return parse.find_reply(frames, command_type)

    async def read_secret(self) -> bytes | None:
        """Read the 8-byte account-lock secret off the device (aa b1), or None.

        For the SETUP/bootstrap case: an unbound plug returns its secret without one
        being supplied, so call this BEFORE set_secret() (while `_secret` is None the
        post-handshake unlock step sends nothing, avoiding the secret chicken-and-egg).
        The frame builder is build.secret_read()."""
        reply = await self._read_reply(build.secret_read())
        return parse.parse_secret(reply) if reply else None


def make_device(
    ble_device: BLEDevice,
    sku: str,
    advertisement_data: AdvertisementData | GoveeAdvertisement | None = None,
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
