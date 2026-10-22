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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Final, Literal, Never, ParamSpec, TypedDict, TypeVar

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from ..const import (
    ADVERT_SUPPRESS_SECONDS,
    IDLE_DISCONNECT_DELAY,
    INFO_READ_TIMEOUT,
    STATUS_READ_TIMEOUT,
    ZONE_POWER_FRESH_SECONDS,
)
from ..exceptions import (
    GoveeBleAuthError,
    GoveeBleCommandRejected,
    GoveeBleError,
    GoveeBleInvalidValue,
    GoveeBleNotSupported,
    GoveeBleTimeout,
    GoveeBleUnknownName,
)
from ..identify import GoveeAdvertisement, _bound_advertisement, parse_broadcast_onoff
from ..identify import _pact_encrypt_flags
from ..models import Capability, DeviceState, Encryption, Segment, Zone
from ..scenes import Scene, async_load_scenes, load_scenes, scene_names_by_code
from ..scenes import scene_upload_frames as _scene_upload_frames
from ..transport.connection import ConnectBudget, GoveeConnection
from ..wire import build, parse, reassemble
from . import _fold, _readback
from .brightness import percent_to_device
from .profile import DeviceProfile, profile_for, refuses_pact, wire_profile

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


class _BudgetKw(TypedDict, total=False):
    connect_budget: ConnectBudget


def _budget_kw(budget: ConnectBudget | None) -> _BudgetKw:
    """The ``connect_budget`` keyword of a transport call (:meth:`Device.update`'s limits), passed only
    when a poll set one: every other call keeps the transport's call shape."""
    return {} if budget is None else {"connect_budget": budget}


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
        channel from its encrypt flag, read from its Govee manufacturer data whatever the advertised name;
        without one (or without a Govee pact block) the profile default is used. Later advertisements
        re-learn it for the next session (:meth:`ingest_advertisement`). ``sku`` names the
        device (default: the profile's first SKU). ``secret`` is the 8-byte account-lock key, sent
        after every handshake on a profile that requires one (:meth:`set_secret` can supply it
        later). ``frame_log=<path>`` persists a JSONL session capture. ``idle_disconnect`` is the
        seconds of inactivity after which the link is dropped: None uses the library default
        (``const.IDLE_DISCONNECT_DELAY``), 0 or less keeps the link up. While the link is up the device sends
        its profile's keep-alive read (:attr:`.DeviceProfile.heartbeat`), which never keeps it up longer. A
        profile with ``heartbeat=None`` sends none, whatever its ``heartbeat_timing``: to turn it off on a
        SKU whose profile has one, ``Device(dataclasses.replace(profile, heartbeat=None), ...)``.

        Raises GoveeBleInvalidValue if ``secret`` is not exactly 8 bytes."""
        self.profile = profile
        self._ble_device = ble_device
        self._sku = (sku or profile.skus[0]).upper()
        self._secret = self._checked_secret(secret)
        # set_secret() changed the secret: send its 33 b2 unlock on the live session (see set_secret).
        self._unlock_pending = False
        self._scenes_warm = False        # bundled scene catalog loaded off the loop (update()), and it worked
        # The last scene catalog load's failure (its message), which the sync scene properties raise with no
        # I/O until update() loads the catalog again off the loop (_scene_lookup).
        self._scenes_error: str | None = None
        self._state = DeviceState(optimistic=True)
        self._segment_count: int | None = None   # runtime-reported; overrides profile.segments
        self._device_info_read = False   # device-info (mac/hw/fw/sn) read once, lazily
        self._polled_once = False        # for the one-time INFO "first poll" line
        # Mutation sequence (BUG-4): bumped by every state-mutating setter when it applies its
        # optimistic state (_commit). Each read snapshots it and discards the reply if it moved
        # (_read_fresh), so a stale read never overwrites a newer setter. Not a lock: setters never
        # wait.
        self._mutation_seq = 0
        # A write that may have switched the device off raised, an acked zone-off may have switched it off
        # (_commit), or a status reply came before the device applied an acked power write (_read_status):
        # is_on may say on for a device that is off, so _wake_power does not trust it until a read or an acked
        # write over every zone gives the on/off again. _power_unsure_seq counts the times it was set, so only
        # a read issued after the last of them clears it (_finish_read).
        self._power_unsure = False
        self._power_unsure_seq = 0
        # H60A6 (global_power): for each zone a 33 01 / 33 30 write (or a 33 04 wake) set since the last
        # status reply carrying it, the powers it may end at once the firmware's message thread has applied
        # them (_post_zone_power). The firmware acks them before that thread applies them (see _read_status),
        # so the next reply may predate them.
        self._zone_power_posted: dict[int, set[bool]] = {}
        # Serializes the bar-switch read-modify-write in set_zone_power (one frame carries both
        # bars). Lock order: _scene_lock, then this, then _read_gate, then the connection lock;
        # never take it from inside a connection operation. release() takes all four, in that order.
        self._bar_lock = asyncio.Lock()
        # Serializes scene writes, so a scene's upload and its activation are never split by another
        # scene's (the connection lock is per-operation). Outermost lock (see _bar_lock).
        self._scene_lock = asyncio.Lock()
        # Write calls and release() calls are numbered as they start, before any lock or I/O (_ticket), and
        # _link_write is (link, number): the latest-numbered write call whose frames went out on that link
        # (GoveeConnection.link_number, _wrote). release() keeps a link a write called after it wrote on.
        self._tickets = 0
        self._link_write = (0, -1)
        # Held by a scene write across its upload and activation, and briefly by every read-back
        # (_read_reply, _query_status), so no poll lands between the two (see set_scene_by_name).
        # Innermost device lock: taken after _scene_lock and _bar_lock, before the connection lock.
        self._read_gate = asyncio.Lock()
        self._discarded_reads = 0        # cumulative count of discarded stale reads
        self._callbacks: list[StateCallback] = []
        self._advert_ignore_until = 0.0   # monotonic; see ADVERT_SUPPRESS_SECONDS
        # (pact_type, pact_code) from the Govee advertisement, or None until one is heard.
        self._pact: tuple[int, int] | None = None
        # Parsed once, used twice; read as a bound device's, whatever its name (_bound_advertisement).
        adv = _bound_advertisement(advertisement_data, ble_device.name, self._sku)
        self._learn_pact(adv)
        enc = self._resolve_encryption(adv)
        # The mode each session starts from: the constructor's, then the last one an advertisement handed to
        # the connection (_learn_cipher); and whether the last advertisement that said anything about it was
        # ambiguous (its WARNING is logged once per spell).
        self._advert_encryption = enc
        self._cipher_ambiguous = False
        self._conn = GoveeConnection(
            ble_device,
            encryption=enc,
            unlock_frames=self._unlock_frames if profile.requires_secret else None,
            session_frames=self._session_frames if profile.relay or profile.light_effect_stop else None,
            session_ack=profile.relay,
            frame_log=frame_log,
            idle_disconnect=IDLE_DISCONNECT_DELAY if idle_disconnect is None else idle_disconnect,
            # The screen's keep-alive while the link is up (spec devices.yaml timing.heartbeat). Without one
            # the connection ignores the timing and arms nothing: no beat at all.
            heartbeat=None if profile.heartbeat is None else build.heartbeat(profile.heartbeat),
            heartbeat_timing=profile.heartbeat_timing,
        )

    def _learn_pact(self, adv: GoveeAdvertisement | None) -> bool:
        """Remember the advertised ``(pact_type, pact_code)``; the app reads the same pair
        (``BleUtil.parseBleBroadcastPact``) into the device model. Exposed as :attr:`pact`; it decides
        whether the 0xAC status read asks for colour groups (:meth:`_wants_color_groups`), and picks the
        profile's pact entries for the scene set and the Kelvin and brightness ranges. An advertisement
        without pact info changes nothing. Returns whether :attr:`pact` changed.

        The profile was routed when the device was built (``create_device``) and is kept: a pact heard
        later that none of its rows accepts, where the app's scan would refuse the device or take another
        row (``profile.refuses_pact``), is logged as a warning, once per change of pact."""
        if adv is None or adv.pact is None or adv.pact == self._pact:
            return False
        self._pact = adv.pact
        if refuses_pact(self.profile, self._sku, adv.pact):
            _LOGGER.warning(
                "%s (%s): advertises pact %d,%d, which no row of its profile accepts (the app's scan refuses "
                "it there or takes another row); it keeps the profile it was built with",
                self._ble_device.address, self._sku, *adv.pact,
            )
        return True

    @property
    def pact(self) -> tuple[int, int] | None:
        """The advertised ``(pact_type, pact_code)``, or None if no advertisement carrying it
        has been seen (e.g. a device built without advertisement data). An advertisement that changes
        it notifies the callbacks (:meth:`ingest_advertisement`)."""
        return self._pact

    def _resolve_encryption(self, adv: GoveeAdvertisement | None) -> Encryption:
        """The command-channel mode the parsed advertisement announces (its encrypt flag), else the
        profile default: without an advertisement, or when it carried no pact info, whose plaintext
        ``encrypted`` is a guess (unknown, not "plaintext")."""
        if adv is None or not adv.encryption_known:
            return self.profile.encryption
        return Encryption.AES_RC4_PSK if adv.encrypted else Encryption.NONE

    def _learn_cipher(self, mfg: dict[int, bytes]) -> None:
        """Re-learn the advertised encrypt flag for the next session, as the app overwrites its per-MAC
        flag from each parsed broadcast (``BleBroadVersionCache.d:109-142``, from
        ``AbsViewMode4OpWithOtaCheck.w2``). The app reads that flag on every GATT callback and write
        (``EncryptionUtils.isEncryptionSupported``, ``EncryptWriter.encryptWriteCharacteristic:42``), but
        writes it from the scan just before it connects (``AbsViewMode4Op.onScanEvent:1956-1985``). Here a
        session already up keeps its mode, and a BGC version of 1 or 2 still overrides the flag
        (:meth:`.GoveeConnection.set_advertised_encryption`); the INFO line names both.

        Learned only when every Govee pact entry of the advertisement carries the same flag
        (:func:`.identify._pact_encrypt_flags`). An advertisement with none says nothing, as a broadcast
        without a pact block writes nothing in the app (``GoodsType.parseBleBroadcastPactInfo:1551-1561``).
        One whose entries disagree is a merged record (another header heard earlier, or another device's
        frame) whose latest broadcast can't be told: the cipher stays, with a WARNING once until an
        advertisement agrees again."""
        flags = _pact_encrypt_flags(mfg)
        heard = set(flags.values())
        if len(heard) > 1:
            if not self._cipher_ambiguous:
                self._cipher_ambiguous = True
                _LOGGER.warning(
                    "%s (%s): advertises Govee headers %s, encrypted and plaintext (a merged advertisement "
                    "record: an earlier header, or another device's frame); the advertised mode stays %s "
                    "(a BGC version of 1 or 2 overrides it)",
                    self.address, self._sku, ", ".join(f"0x{header:02x}" for header in flags),
                    self._advert_encryption.value,
                )
            return
        if not heard:
            return
        self._cipher_ambiguous = False
        mode = Encryption.AES_RC4_PSK if heard.pop() else Encryption.NONE
        if mode is self._advert_encryption:
            return
        self._advert_encryption = mode
        upcoming = self._conn.set_advertised_encryption(mode)
        _LOGGER.info(
            "%s (%s): advertises the %s command channel; the next session uses %s%s",
            self.address, self._sku, mode.value, upcoming.value,
            "" if upcoming is mode else " (its BGC version overrides the advertised flag)",
        )

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
        hardware or soft version is read (:attr:`DeviceProfile.kelvin_by_hardware` /
        :attr:`DeviceProfile.kelvin_by_software`) or a pact is advertised
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
        """The Kelvin range for this device's firmware versions and advertised pact, as far as they are
        known."""
        return _fold.kelvin_range(self.profile, self._state.hardware_version, self._pact,
                                  self._state.firmware_version)

    @property
    def encryption(self) -> Encryption:
        """The command-channel mode (advertisement + BGC refined): while a link is up, its session's; with
        none, the one the next session starts from, which a later advertisement may have changed
        (:meth:`ingest_advertisement`)."""
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
        """Sorted names of the bundled scenes the Govee app lists at the scene set in force
        (:attr:`scene_versions`, devices.yaml scenes.list_filter); empty without the SCENES capability.

        The app's list drops each lightEffect whose op type is not in the set and plays the first one
        left: a scene with none left is hidden there and not listed, and one that plays another of its
        effects there keeps its name (:meth:`set_scene_by_name` sends that effect). Names never change;
        only which scenes are listed. Before the device-info read the set is the newest firmware's (a
        version not yet read meets every gate, :func:`._fold.scene_versions`), so that read can only
        shrink the list (:meth:`set_scene_by_name` reads a version the set depends on first, so it never
        plays a scene the read hides); a version the read did not bring back is read again on each later
        :meth:`update` until it is in. A change of the advertised :attr:`pact` can move it either way, on a
        profile with :attr:`DeviceProfile.scene_versions_by_pact`. Both notify the callbacks. Sorted by
        name, so the order is stable.

        The first lookup (of any SKU) reads the bundled scene store from disk; :meth:`update` (and
        :meth:`set_scene_by_name`) load it in a worker thread first, so on the event loop this is
        a lookup in memory, with no I/O at any scene set, once a poll has loaded it. Where a load
        failed (:meth:`update`'s, or a first read here), this raises that failure, a GoveeBleError,
        with no I/O, until the next :meth:`update` loads the store again in a worker thread: a store
        the loader keeps as unreadable or malformed, and one it does not keep, which the next lookup
        would read again (a transient read error, a RecursionError, a store nested too deep
        included)."""
        if Capability.SCENES not in self.capabilities:
            return []
        return sorted(self._scene_lookup(load_scenes))

    @property
    def active_scene(self) -> str | None:
        """Name of the active scene among the scenes listed at the scene set in force (as
        :attr:`scene_names`), or None if no scene (or an uncatalogued code) is active, or the device has
        no SCENES capability. The code is looked up among the effects the listed scenes play at that set,
        so a hidden scene's code, or the code of an effect a listed scene does not play there, is None too:
        the app's list highlights the scene whose effect at position 0, the one it plays, has the reported
        code (``SceneListAdapter``, ``getSceneCode(0, sku)``; ``SceneDataVM.i1`` only picks the category
        page). With no I/O once loaded, as :attr:`scene_names`, whose failure it raises too."""
        code = self._state.scene_code
        if code is None or Capability.SCENES not in self.capabilities:
            return None
        return self._scene_lookup(scene_names_by_code).get(code)

    def _scene_lookup(self, lookup: Callable[..., _T]) -> _T:
        """``lookup(sku, scene_versions=...)`` at the scene set in force (:attr:`scene_versions`), for the
        sync scene properties; after a failed load, a fresh GoveeBleError of its message, with no I/O (the
        store is not read on the event loop again: :meth:`update` loads it)."""
        if self._scenes_error is not None:
            raise GoveeBleError(self._scenes_error)
        versions = self.scene_versions
        try:
            return lookup(self._sku, scene_versions=versions)
        except GoveeBleError as err:
            self._scenes_warm, self._scenes_error = False, str(err)
            raise

    def register_callback(self, cb: StateCallback) -> Callable[[], None]:
        """Register a state-change callback; returns a function that unregisters it.

        The callback gets the live :attr:`state` (call :meth:`snapshot` to keep a copy). The state
        setters and :meth:`update` call it, and so does :meth:`ingest_advertisement` when an advertisement
        changed the on/off or the :attr:`pact` (its return value reports only the on/off). A callback
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
        ``VM4Light.i5``, once the connect reads succeed), sent without waiting for its ack
        (``session_ack``): the library connects for every poll after the idle disconnect, so a device that
        doesn't ack it would cost an ack timeout per poll. A plug's rejection or missing ack is only logged
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

    async def _send_pending_unlock(
        self, budget: ConnectBudget | None = None, *, ticket: int | None = None,
    ) -> None:
        """Send the ``33 b2`` unlock for a secret set while the session was already up (see
        :meth:`set_secret`). Without a live session nothing is sent: the next session setup sends
        it. A rejection is a GoveeBleAuthError, as at session setup; an unacked unlock is only
        logged, as at session setup (``GoveeConnection._prepare_session``). ``ticket``: the write call it goes
        out for (:meth:`_commit`), recorded once it went out (:meth:`_wrote`). ``budget``: the
        calling poll's connect limits (:meth:`update`); a timeout that leaves the session down (the
        write retry's reconnect ran out of them) is raised, and the next session setup sends the
        unlock."""
        if not self._unlock_pending:
            return
        self._unlock_pending = False   # taken: a concurrent operation doesn't send it twice
        secret = self._secret
        if not secret or not self._conn.is_connected:
            return   # the next session setup sends it (or there is none)
        done = False   # the write finished: acked, rejected, or unacked (treated as sent)
        try:
            await self._conn.send(build.secret_check(secret), **_budget_kw(budget))
            done = True
        except GoveeBleCommandRejected as err:
            done = True
            raise GoveeBleAuthError(
                f"{self._sku}: device rejected the secret key - it is wrong or the device was re-paired"
            ) from err
        except GoveeBleTimeout:
            if budget is not None and not self._conn.is_connected:
                raise   # the poll's connect limit ran out on the write retry's reconnect: nothing sent
            done = True
            _LOGGER.debug("%s (%s): no ack for unlock frame (continuing)", self.address, self._sku)
        finally:
            # A cancelled or failed write on a session that is still up never reached the device:
            # pending again, so the next operation sends it first. Once the session is gone the next
            # setup sends it anyway, and a set_secret() during the write has re-armed it itself.
            if not done and self._conn.is_connected and self._secret == secret:
                self._unlock_pending = True
            if done and ticket is not None:
                self._wrote(ticket)

    def _check_kelvin(self, kelvin: int) -> int:
        """The Kelvin that goes on the wire (and into ``state``) for ``kelvin``: clamped to the
        device's range (:attr:`min_kelvin` / :attr:`max_kelvin`, which may follow its firmware versions),
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
        with the profile's ``advert_on_mask`` on the plugs), the advertised pact (:attr:`pact`) and the
        encrypt flag, all whatever the advertised name. The flag picks the command channel of the next
        session, never of the one that is up, and only when every Govee pact entry agrees on it
        (:meth:`_learn_cipher`); a BGC version of 1 or 2 still overrides it (:attr:`encryption`).
        Returns True if `state.is_on` changed (so a caller can notify selectively). The callbacks also
        fire when the advertised :attr:`pact` changes: once per advertisement, with the pact already
        stored, whether or not the on/off changed with it. A change of the flag fires nothing.

        The on/off is ignored for ``ADVERT_SUPPRESS_SECONDS`` after a command or authoritative read,
        which are fresher than the advert's. The pact is not: no command or read sets it. Any change of
        the pact counts, since :attr:`pact` is itself exposed and the values it picks (:attr:`min_kelvin`
        / :attr:`max_kelvin`, :attr:`scene_versions`, the brightness range and whether brightness can be
        written yet, the 0xAC status read's colour groups) may change with it. An advertisement without
        pact info, or with the pact already heard, leaves it as it was."""
        mfg = getattr(advertisement_data, "manufacturer_data", None)
        if not mfg:
            return False
        adv = _bound_advertisement(advertisement_data, self._ble_device.name, self._sku)
        pact_changed = self._learn_pact(adv)
        self._learn_cipher(mfg)
        on = parse_broadcast_onoff(mfg, on_mask=self.profile.advert_on_mask)
        # Within the suppress window a command/read is fresher than the lagging advertised on/off.
        power_changed = False
        if on is not None and on != self._state.is_on and time.monotonic() >= self._advert_ignore_until:
            _fold.apply_advert_power(self.profile, self._state, on)   # zones kept consistent (state only)
            power_changed = True
        if power_changed or pact_changed:
            self._notify()
        return power_changed

    async def connect(self) -> None:
        """Establish the BLE session now (connect, handshake, unlock) instead of lazily on
        the first command. Idempotent; the connection's idle timer still applies afterwards."""
        # A secret set on the live session goes out on it first; without one, the session setup
        # below sends it (so it is never sent twice).
        await self._send_pending_unlock()
        await self._conn.connect()

    async def release(self) -> bool:
        """Drop the BLE link now instead of at the idle timer, and end the warm window a write opened
        (``const.ACTIVE_IDLE_DELAY``), for a caller that knows no follow-up is coming (a voice command, an
        automation, one device of a burst), so the link's connection slot frees at once.

        The Device stays usable: the next operation connects again (link, handshake, unlock, session frames),
        as after an idle disconnect, with no warm window left over (a read gets the base idle delay; a write
        opens its own). It takes the Device's locks in their order (``_scene_lock``, ``_bar_lock``,
        ``_read_gate``, then the connection's), so it waits for each link operation already running or queued
        before it (each lock is FIFO), and never lands inside a sequence: a :meth:`set_scene_by_name` (its
        version read and catalog load, its upload and its activation), or an H6047 bar's ``aa 36`` read and
        the ``33 36`` built from it. It can land between the steps of a call no device lock spans (a poll's
        reads, a plug's power write and its re-read): those later steps reconnect (a plug's re-read ends with
        its ``33 b5`` time sync, a write, which opens a fresh warm window on the new link). A write called
        after it either queues behind it and reconnects or, if it reaches the link first (a setter that takes
        no device lock, or is handed one, while release() waits for one), keeps the link and its window: the
        release then drops nothing and returns False. That is the link it wrote on, whether the device acked
        the write or refused it (a GoveeBleCommandRejected still reached the device); a write its caller
        cancels (before or after its frames go out), or one whose link has gone since, keeps nothing. The
        heartbeat stops with the link; the next session arms its own.

        Returns True when a link was up and was dropped, False when it dropped nothing: no link, after
        :meth:`stop` (it never reopens the connection), or a write called after it wrote on the link that is
        up. Never raises a GoveeBleError: the teardown is bounded by ``const.DISCONNECT_TIMEOUT`` and its
        errors are logged at DEBUG. Cancelled while it waits for a lock, it leaves the queue and drops
        nothing; cancelled during the teardown, the teardown runs on, detached, to that bound."""
        ticket = self._ticket()
        async with self._scene_lock, self._bar_lock, self._read_gate:
            # Asked under the connection lock, after every operation queued before the drop: a write called
            # after this release that ended on the link that is up, acked or refused, keeps it, with its
            # window. One cancelled (while it waited or after its frames went out), or that wrote on a link
            # since gone, does not.
            dropped = await self._conn.disconnect(keep=lambda: self._kept_for(ticket))
            if dropped:
                _LOGGER.debug("%s (%s): link released by caller", self.address, self._sku)
            return dropped

    async def stop(self) -> None:
        """Disconnect and refuse further I/O (the owner is being torn down). To drop the link and keep the
        Device usable, use :meth:`release`."""
        await self._conn.close()

    def _mark_authoritative(self) -> None:
        self._advert_ignore_until = time.monotonic() + ADVERT_SUPPRESS_SECONDS

    def _finish_read(self, applied: bool, power_read: bool, since: int) -> None:
        """Close a read-back: ``applied`` (a live field came from the device) ends the optimistic
        state; only ``power_read`` (on/off came from the device) makes it authoritative over the
        lagging advertised on/off (see :meth:`ingest_advertisement`). A read that got nothing, or
        only brightness/mode, leaves the advert as the on/off source.

        ``since``: ``_power_unsure_seq`` when the read that gave the on/off was issued. ``is_on`` is the
        device's again (``_power_unsure`` cleared, see :meth:`_wake_power`) only if nothing set
        ``_power_unsure`` after that: a write that failed while a multi-read poll was past its power read
        leaves it set."""
        if applied:
            self._state.optimistic = False
        if power_read:
            if since == self._power_unsure_seq:
                self._power_unsure = False
            self._mark_authoritative()

    def _set_power_unsure(self) -> None:
        """``is_on`` may say on for a device that is off (see ``_power_unsure``)."""
        self._power_unsure = True
        self._power_unsure_seq += 1

    def _wrote(self, ticket: int) -> None:
        """Record that write call ``ticket``'s frames went out on the link that is up (``_link_write``)."""
        link = self._conn.link_number
        if not link:
            return
        at, latest = self._link_write
        self._link_write = (link, max(latest, ticket) if at == link else ticket)

    def _kept_for(self, ticket: int) -> bool:
        """Whether a write called after call ``ticket`` wrote on the link that is up (:meth:`release`)."""
        at, latest = self._link_write
        return at != 0 and at == self._conn.link_number and latest > ticket

    def _ticket(self) -> int:
        """The next call number: a write call or :meth:`release` takes one as it starts, before any lock or
        I/O (see ``_link_write``)."""
        self._tickets += 1
        return self._tickets

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
        self, frames: bytes | list[bytes], apply: Callable[[], None], *, authoritative: bool = True,
        zones: Mapping[int, bool] | None = None, fixture: bool = False, ticket: int | None = None,
    ) -> None:
        """Write ``frames`` and fold the change into state: the protocol every setter follows.

        ``ticket``: the write call's number (:meth:`_ticket`), taken before its first lock or I/O; None takes
        one now, for a setter that reaches this with no await before it. Once the write ends, acked or
        failed (a refusal reached the device), it is recorded with the link its frames went out on
        (:meth:`_wrote`), which a :meth:`release` called before it keeps; a cancelled one is not recorded,
        even after its frames went out.

        One frame goes out with ``send``, a list with ``send_many`` (one connection-lock hold, each
        frame ack-verified in order). Once the write succeeded: bump ``_mutation_seq`` (an in-flight
        read is then discarded, :meth:`_read_fresh`), ``apply`` the optimistic state, make it
        authoritative over the lagging advertised on/off for ``ADVERT_SUPPRESS_SECONDS`` (unless
        ``authoritative`` is False, as for the gradual flag) and notify. A failed write raises and
        applies nothing. A secret set on the live session goes out first (:meth:`set_secret`).

        A power write names the zones it targets, for ``DeviceState.zone_power_confirmed``: ``zones``, the
        power its frames give each zone they set (``apply`` folds it in), and ``fixture`` for a whole-device
        power write or a scene activation, which targets every zone. When the write raises, it may have
        reached the device: the transport writes a frame on every attempt and once more on a fresh link, so
        even a refusal can follow an attempt that landed without an ack, and a raise does not say whether a
        frame went out. So every zone it targets loses its stamp, whatever power it is held at
        (:func:`._fold.unconfirm_zones`): ``zones``, or every zone with ``fixture``. A write whose ``zones``
        are all off may also have switched the device off, so ``_power_unsure`` is set; an acked one whose
        ``zones`` cover every profile zone clears it (the state's on/off is the device's).

        An acked one whose ``zones`` are all off and cover only some zones (an H60A6 ``33 30`` off) sets it
        too, unless the device was known to be on and every other zone is held lit with a stamp. The
        firmware switches the main switch off when that zone-off leaves no zone lit (H60A6 FUN_9b0278dc zone
        mode @0x9b027972-0x9b027986: both zone flags 0 -> FUN_9b02b3fc(0), then FUN_9b033e28), so a zone the
        library assumed lit, or has forgotten, may have been the last one; ``apply`` derives ``is_on`` from
        the zones, which does not make it so.

        A zone the frames wake without the state saying so (:meth:`_wake_power`, checked again once the
        frames went out: a power-off may have applied while this write waited for the link) loses a stamp
        of "off", acked or not (:func:`._fold.unconfirm_contradicted`). On a
        :attr:`.DeviceProfile.global_power` fixture the zone power the frames hand the firmware's message
        thread is kept for the next status read, acked or not (``_zone_power_posted``,
        :meth:`_read_status`)."""
        mine = self._ticket() if ticket is None else ticket
        await self._send_pending_unlock(ticket=mine)
        batch = [frames] if isinstance(frames, bytes) else frames
        wake = self._wake_power(batch)
        try:
            if isinstance(frames, bytes):
                await self._conn.send(frames)
            else:
                await self._conn.send_many(frames)
        except BaseException as err:
            if not isinstance(err, asyncio.CancelledError):
                self._wrote(mine)   # a refusal: it went out on a link that is still up
            wake = {**wake, **self._wake_power(batch)}
            _fold.unconfirm_zones(self._state, None if fixture else (zones or ()))
            _fold.unconfirm_contradicted(self._state, wake)
            self._post_zone_power(batch, zones, wake, acked=False)
            if zones and not any(zones.values()):
                self._set_power_unsure()
            raise
        self._wrote(mine)
        wake = {**wake, **self._wake_power(batch)}   # a power-off applied while this write waited
        known_on = self._state.is_on is True and not self._power_unsure
        self._mutated()
        apply()
        _fold.unconfirm_contradicted(self._state, wake)
        self._post_zone_power(batch, zones, wake, acked=True)
        if zones is not None and len(zones) == len(self.profile.zones):
            self._power_unsure = False
        elif zones and not any(zones.values()) and not (known_on and self._others_confirmed_lit(zones)):
            self._set_power_unsure()
        if authoritative:
            self._mark_authoritative()
        self._notify()

    def _others_confirmed_lit(self, zones: Mapping[int, bool]) -> bool:
        """Whether every profile zone outside ``zones`` is held lit with a ``zone_power_confirmed`` stamp, so
        a zone-off of ``zones`` leaves a zone lit and the main switch on (see :meth:`_commit`)."""
        st = self._state
        return all(st.zone_power.get(z.power_index) is True and z.power_index in st.zone_power_confirmed
                   for z in self.profile.zones if z.power_index not in zones)

    def _post_zone_power(self, frames: Sequence[bytes], zones: Mapping[int, bool] | None,
                         wake: Mapping[int, bool], *, acked: bool) -> None:
        """Keep the zone power these frames hand the H60A6's message thread for the next status read
        (``_zone_power_posted``; :meth:`_read_status`), FIRMWARE-SOURCED: the dispatcher (FUN_9b02687c) posts
        a ``33 01`` as MSG_SET_TURN 0xB2 (FUN_9b027dc2 @0x9b026b1c), a ``33 30`` as MSG_SET_TURN_MULT 0xB7
        (FUN_9b027daa @0x9b02708a) and the wake of a ``33 04`` on a device whose main switch is off as
        MSG_SET_TURN(1) (@0x9b026c5a-0x9b026c5e), and the thread applies them in order. On a
        :attr:`.DeviceProfile.global_power` fixture ``zones`` is what its ``33 01`` / ``33 30`` frames give
        each zone they set, and ``wake`` the zones a ``33 04`` may light (:meth:`_wake_power`). A ``33 05``
        wake sets the flags in the dispatcher itself (case 5), so it posts nothing.

        Each zone keeps the powers it may end at once the posted writes are applied: an acked ``33 01`` /
        ``33 30``'s alone (the device has it, after every write before it), joined by the power of each later
        write that may or may not have reached it or posted anything: one that raised, and a ``33 04`` wake,
        which posts only if the main switch is off when it is dispatched."""
        if not self.profile.global_power:
            return
        for i, on in (zones or {}).items():
            if acked:
                self._zone_power_posted[i] = {on}
            else:
                self._zone_power_posted.setdefault(i, set()).add(on)
        if wake and any(f[0] == build.PRO_WRITE and f[1] == build.CMD_BRIGHTNESS for f in frames):
            for i, on in wake.items():
                self._zone_power_posted.setdefault(i, set()).add(on)

    def _wake_power(self, frames: Sequence[bytes]) -> dict[int, bool]:
        """The zones ``frames`` light on the device without the library folding it in, as a power for
        :func:`._fold.unconfirm_contradicted`: every zone, on, for a write that wakes a zoned device not
        known to be on (``is_on`` not True, ``_power_unsure``, or on an H60A6 a posted "on" no status reply
        has carried yet); else none. The state keeps the zones' power as it was, so a zone it holds off
        loses its stamp. FIRMWARE-SOURCED, per profile:

        - :attr:`.DeviceProfile.global_power` (H60A6): a ``33 04`` brightness or ``33 05`` mode write. With
          the main switch off (FUN_9b02b430 == 0) the dispatcher (FUN_9b02687c, ``H60A6_ble_dispatcher.c``)
          case 4 posts MSG_SET_TURN(1) (FUN_9b027dc2 @0x9b026c5a-0x9b026c5e; FUN_9b0278dc mode 0 sets the
          main and both zone flags) whatever the level: a level above 100 only skips the brightness set
          (@0x9b026bda-0x9b026bdc -> 0x9b026cd2, which joins the main-switch check at 0x9b026c50). Case 5
          sets them itself (FUN_9b02b3fc, FUN_9b02b43c, FUN_9b02b470), except a music (0x13) write with
          byte 3 = 0. The 2026-10-12 bench saw colour and brightness writes wake the fixture (devices.yaml
          ``global_switch``). The check reads the main switch as the message thread has left it, so a
          ``33 01`` on or ``33 30`` zone-on acked before it may not have switched it on yet
          (``_zone_power_posted``, :meth:`_read_status`): until a status reply carries it, a device known to
          be on may still wake. A ``33 04`` then lights every zone (its MSG_SET_TURN(1) is queued after the
          posted writes). A ``33 05`` lights them all before the thread applies the writes still queued, and
          which of the posted ones those are is not known, so every zone counts as lit.
        - :attr:`.DeviceProfile.bar_switch` (H6047): a ``33 04`` or ``33 05``. While both bar flags are off
          (0x0102a5a8, the bar 0 | bar 1 the aa 01 query answers) each handler runs the power-on sequence
          FUN_01002750, whose FUN_01002720(1) sets both bar flags: the 0x04 handler (0x01005454) at
          0x01005482-0x0100548a (``H6047_firmware_BLE_notes.md``), and the 0x05 handler (0x01005510), whose
          every 0x33 sub-type reaches 0x010057fc-0x01005802."""
        p = self.profile
        posted_on = any(True in powers for powers in self._zone_power_posted.values())
        if not p.zones or (self._state.is_on is True and not self._power_unsure and not posted_on):
            return {}
        wakes = (build.CMD_BRIGHTNESS, build.CMD_MODE) if p.global_power or p.bar_switch else ()
        if any(f[0] == build.PRO_WRITE and f[1] in wakes for f in frames):
            return dict.fromkeys((z.power_index for z in p.zones), True)
        return {}

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

    def _brightness_pct(self, raw: int) -> int | None:
        """The device-reported brightness byte as a 0..100 percent, None while its range waits for the
        advertised pact (:func:`._fold.brightness_pct`)."""
        return _fold.brightness_pct(self.profile, raw, self._pact)

    # -- power / brightness -------------------------------------------------
    def _brightness_frame(self, pct: int) -> bytes:
        """33 04 for a 1..100 percent: verbatim on percent devices, mapped onto the raw
        range in force on devices with one (``brightness_range``, or ``brightness_by_pact`` for the
        advertised pact: :func:`._fold.brightness_range`; :func:`.brightness.percent_to_device`).
        GoveeBleNotSupported while that range waits for the pact (:func:`._fold.brightness_pending`): which
        byte the app writes depends on it."""
        if _fold.brightness_pending(self.profile, self._pact):
            raise GoveeBleNotSupported(f"{self._sku}: brightness waits for the advertised pact, which picks "
                                       "its range")
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

    def _power_zones(self) -> tuple[int, ...]:
        """The power indices of the zones an acked :meth:`_power_frames` write sets on the device, case by
        case as :meth:`_power_frames` picks the frames, so their power is learned from the device
        (``DeviceState.zone_power_confirmed``):

        - one ``33 30`` per zone: each sets its own zone, so every zone;
        - a plug's relay frame: no zone;
        - ``33 01``: every zone, FIRMWARE-SOURCED for each profile that has zones and sends it.

          - H60A6 (:attr:`.DeviceProfile.global_power`, the only profile with it): the 33 01 dispatch
            posts MSG_SET_TURN (FUN_9b027dc2), whose case of _mqtt_response_handle (FUN_9b028bc8
            @0x9b028ea4-0x9b028eb0) sets the main flag and both zone flags from its byte (FUN_9b0278dc
            @0x9b02791c-0x9b027930: FUN_9b02b3fc, FUN_9b02b43c, FUN_9b02b470), the zone flags aa 30
            reads (FUN_9b02b464 / FUN_9b02b498). The bench showed it too (2026-10-12: off clears both
            zones, on lights both).
          - H6047 (:attr:`.DeviceProfile.bar_switch`; the H6042, the other one, has no zones): the
            power handler sets both bar flags from its byte (H6047 FUN_01004f32 @0x01004f5c-0x01004f60
            -> FUN_01002720 @0x01002720, through FUN_01002d14), the flags aa 36 reads (FUN_01006934,
            FUN_01002cf4). Only an "off" stamps: after an "on" the library forgets the bars, as the
            app does (:func:`._fold.apply_power`).

          The app models neither: its switch ack sets only the main switch (``VM4LightH60A6.K5:265-277``,
          ``NewDetailVm.F5:178-191``) and takes the zones from the next ee 30 push
          (``VM4LightH60A6.t5:553-561``) or aa 36 heartbeat. A new zoned profile that sends ``33 01``
          needs the same evidence for its firmware."""
        p = self.profile
        zones = tuple(z.power_index for z in p.zones)
        if p.zones and not p.global_power and not p.bar_switch:
            return zones   # one 33 30 per zone
        if p.relay:
            return ()      # the relay frame
        return zones       # 33 01

    def _apply_power(self, on: bool) -> None:
        """State for an acked whole-device power write: the power, the zones it drives and the zones its
        frames set (:meth:`_power_zones`; :func:`._fold.apply_power`)."""
        _fold.apply_power(self.profile, self._state, on, confirmed=self._power_zones())

    async def set_power(self, on: bool) -> None:
        """Switch the device on or off.

        Raises GoveeBleNotSupported without POWER; connection errors propagate. A plug with
        :attr:`.DeviceProfile.power_resync` then re-reads as its app does (:meth:`_resync_after_power`);
        a failure there is only logged."""
        self._require(Capability.POWER)
        ticket = self._ticket()
        with self._conn.hold_heartbeat():   # the power write and its re-read: no heartbeat between them
            async with self._zone_power_lock():
                await self._commit(self._power_frames(on), lambda: self._apply_power(on),
                                   zones=dict.fromkeys(self._power_zones(), on), fixture=True, ticket=ticket)
            await self._resync_after_power()

    async def _resync_after_power(self) -> None:
        """gt 50 / 90 (:attr:`.DeviceProfile.power_resync`): after every acked power write the app
        re-runs its connect read list (``BleOpV2.onEventSwitchV2:380-382`` / ``BleOpV4.onEventSwitchV2:
        299-301`` -> ``G()`` = ``BleOpV2.o0:141-171`` / ``BleOpV4.l0:124-147``): ``aa 06``, ``aa 07 03``,
        ``33 b5``, then per-outlet timer and delay reads, which the library does not model. The power
        write already succeeded and its state is applied, so a failure here neither raises nor undoes
        it. Other profiles send nothing. The caller holds the heartbeat across the power write and this
        (``GoveeConnection.hold_heartbeat``)."""
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
        COLOR_TEMP) or when the colour scheme can't express it, and for a ``brightness`` while the
        device's brightness range waits for the advertised pact (as :meth:`set_brightness`), before
        any write, so the power-on is not sent either; GoveeBleError if a device that reports its own
        segment count (H6062) does not answer that read."""
        if rgb is not None and kelvin is not None:
            raise GoveeBleInvalidValue("turn_on: pass either rgb or kelvin, not both")
        pick = _check_rgb(rgb) if rgb is not None else None
        pct = _check_pct(brightness) if brightness is not None else None
        kelvin = self._check_kelvin(kelvin) if kelvin is not None else None
        ticket = self._ticket()
        # H6062's aa 41 read and the write it sizes, and as set_power the power write and its re-read: no
        # heartbeat between them.
        with self._conn.hold_heartbeat():
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
                await self._commit(frames, apply, fixture=power,
                                   zones=dict.fromkeys(self._power_zones(), True) if power else None,
                                   ticket=ticket)
            if power:
                await self._resync_after_power()

    async def turn_off(self) -> None:
        """Power off; equivalent to ``set_power(False)``."""
        await self.set_power(False)

    async def set_brightness(self, pct: int) -> None:
        """Set brightness in percent (clamped to 1..100; 0 is never sent).

        Raises GoveeBleNotSupported without BRIGHTNESS, and before any I/O while the device's brightness
        range waits for the advertised pact: on a profile with ``brightness_by_pact`` (the pact picks the
        byte the app writes) until an advertisement carrying the pact is heard (:attr:`pact`;
        :meth:`ingest_advertisement`, or the advertisement given at construction). GoveeBleInvalidValue
        for a non-int."""
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
        ticket = self._ticket()
        with self._conn.hold_heartbeat():   # H6062's aa 41 read and its write: no beat between
            await self._ensure_segment_count()
            span = self._kelvin_range()   # once: the frame and the state use the same range
            await self._commit(self._color_rgb_frames(pick, span),
                               lambda: _fold.apply_rgb(self.profile, self._state, pick, span), ticket=ticket)

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
        ticket = self._ticket()
        with self._conn.hold_heartbeat():   # H6062's aa 41 read and its write: no beat between
            await self._ensure_segment_count()
            frame = self._build_or_unsupported(
                build.color_temp, sent, self.profile.color_scheme, self.segment_count,
                pieces=self.profile.segments or None)
            await self._commit(frame, lambda: _fold.apply_kelvin(self.profile, self._state, sent),
                               ticket=ticket)

    async def set_segment_rgb(self, indices: Sequence[int], rgb: tuple[int, int, int]) -> None:
        """Set the colour of selected segments (indices 0 .. :attr:`segment_count` - 1).

        Raises GoveeBleNotSupported without SEGMENTS; GoveeBleInvalidValue for a bad index or
        unless ``rgb`` is an (r, g, b) triple of ints in 0..255; GoveeBleError if a device that
        reports its own segment count (H6062) does not answer that read."""
        self._require(Capability.SEGMENTS)
        pick = _check_rgb(rgb)
        r, g, b = pick
        ticket = self._ticket()
        with self._conn.hold_heartbeat():   # H6062's aa 41 read and its write: no beat between
            await self._ensure_segment_count()
            n = self.segment_count
            frame = self._build_or_unsupported(
                build.segment_rgb, _mask(indices, n), r, g, b, self.profile.color_scheme, n,
                self._kelvin_range())

            carried = self._carried(indices, build.COLOR_MASK_CAP)

            def apply() -> None:
                _fold.update_segments(self._state, carried, rgb=pick)
                self._state.scene_code = None   # a segment colour write leaves scene mode, as set_rgb does

            await self._commit(frame, apply, ticket=ticket)

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
        ticket = self._ticket()
        with self._conn.hold_heartbeat():   # H6062's aa 41 read and its write: no beat between
            await self._ensure_segment_count()
            n = self.segment_count
            frame = self._build_or_unsupported(
                build.segment_brightness, _mask(indices, n), level, self.profile.color_scheme, n)

            carried = self._carried(indices, build.BRIGHTNESS_MASK_CAP)

            def apply() -> None:
                _fold.update_segments(self._state, carried, brightness=level)
                self._state.scene_code = None   # the 33 05 15 02 write is a colour-mode write too

            await self._commit(frame, apply, ticket=ticket)

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
        ticket = self._ticket()
        with self._conn.hold_heartbeat():   # H6062's aa 41 read and its write: no beat between
            await self._ensure_segment_count()
            n = self.segment_count
            frame = self._build_or_unsupported(
                build.segment_color_temp, _mask(indices, n), sent, self.profile.color_scheme, n)

            def apply() -> None:
                # A colour-mode write leaves scene mode, as set_color_temp does.
                self._state.scene_code = None

            await self._commit(frame, apply, ticket=ticket)

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

    def _apply_zone_power(self, zone_power: dict[int, bool], confirmed: tuple[int, ...]) -> None:
        """State for an acked zone power write whose frame set the ``confirmed`` zones
        (:func:`._fold.apply_zone_power`)."""
        _fold.apply_zone_power(self.profile, self._state, zone_power, confirmed=confirmed)

    async def set_zone_power(self, name: str, on: bool) -> None:
        """Switch one zone on or off.

        On bar-switch devices the other bar's state is read first if unknown, or not confirmed by the device
        within ``const.ZONE_POWER_FRESH_SECONDS``, and preserved; GoveeBleError if it is unknown and that
        read-back fails (a held one whose re-read fails on a live link or goes unanswered is sent as held;
        a connect that fails raises). Raises
        GoveeBleInvalidValue (also a GoveeBleNotSupported) for an unknown zone."""
        z = self._zone(name)
        if self.profile.bar_switch:
            # The bar read and the 33 36 it feeds are one read-modify-write: no heartbeat between them.
            ticket = self._ticket()
            async with self._bar_lock:
                with self._conn.hold_heartbeat():
                    await self._set_bar_power(z.power_index, on, ticket)
            return

        def apply() -> None:
            # 33 30 sets its own zone only (H60A6 FUN_9b0278dc @0x9b027966-0x9b027996): the other
            # zone's power is carried along as it was, keeping the stamp it had and getting no new one.
            self._apply_zone_power({**self._state.zone_power, z.power_index: on}, (z.power_index,))

        await self._commit(build.zone_power(z.power_index, on), apply, zones={z.power_index: on})

    async def _set_bar_power(self, index: int, on: bool, ticket: int) -> None:
        """Bar-switch read-modify-write; the caller holds ``_bar_lock`` (which every other writer of
        ``zone_power`` on a bar-switch device takes too, :meth:`_zone_power_lock`) and took the call's
        ``ticket`` before it (:meth:`_commit`).

        The ``33 36`` carries both bars, and the app builds it from bars its ``aa 36`` heartbeat keeps current
        (h6047 ``NewDetailVm.D5:669-720`` sends the stored left / right flags, ``Info4Detail.D`` / ``F``,
        which ``NewDetailVm.A3`` parseHeart ``:574-612`` sets from every ``ComposeLightHeartController``
        reply).
        So the bars are read (``aa 36``) first when either is unknown, or when the other bar's power was not
        learned from the device within ``const.ZONE_POWER_FRESH_SECONDS`` (``zone_power_confirmed``): it may
        have been switched since (the app, a remote). When that read gets no reply, fails on a live link, or
        is discarded, a held other bar is sent as held, however old, as the app sends the bars of its last
        heartbeat (so a bar switched elsewhere since is switched back); a connect that fails raises, as
        before, without a second connect for the write. An unknown other bar raises, and so does a read that
        fails while a bar is unknown."""
        # One frame carries BOTH bars, so the other bar's current state must be
        # known - never assume it (assuming "on" turned the other bar ON).
        zp = self._state.zone_power
        other = 1 - index
        discarded = False
        if len(zp) < 2:
            discarded = await self._read_bar_switch() == "stale"
            zp = self._state.zone_power
        elif not _fold.confirmed_within(self._state, other, ZONE_POWER_FRESH_SECONDS):
            # Both bars held: a re-read that fails falls back to the held bar, as an unanswered one does; the
            # write's own reconnect-and-retry then decides (each transport call has its own deadline).
            # The write connects anyway, so the link comes up first: a connect that fails raises here, as
            # the write's own would, instead of being tried again for the write.
            await self._conn.connect()
            try:
                discarded = await self._read_bar_switch() == "stale"
            except GoveeBleError as err:
                _LOGGER.debug("%s (%s): bar re-read failed (%s); sending the held other bar",
                              self.address, self._sku, str(err) or type(err).__name__)
            zp = self._state.zone_power
        if other not in zp:
            if discarded:
                raise GoveeBleError(f"{self._sku}: state changed during read; retry")
            raise GoveeBleError(
                f"{self._sku}: state of the other bar is unknown (read-back failed); "
                "not changing it blindly")
        target = {0: zp.get(0, False), 1: zp.get(1, False)}
        target[index] = on
        # 33 36 sets both bars (H6047 FUN_01006934 @0x01006956-0x01006962), so both are confirmed.
        await self._commit(build.bar_switch(target[0], target[1]),
                           lambda: self._apply_zone_power(target, (0, 1)), zones=target, ticket=ticket)

    async def _read_bar_switch(self, budget: ConnectBudget | None = None) -> _ReadResult:
        """Read the bar switch into state: "applied" (bars and the derived on/off were read back),
        "stale" (the reply was discarded: a setter applied state meanwhile) or "none" (no usable
        reply)."""
        def apply(bars: tuple[bool, bool]) -> None:
            _fold.apply_bar_switch(self._state, bars)

        return await self._read_command(
            "bar-switch", build.bar_switch_query(), parse.parse_bar_switch, apply, budget)

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
        ticket = self._ticket()
        with self._conn.hold_heartbeat():   # as set_segment_rgb; no zoned profile reads aa 41
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

            await self._commit(frame, apply, ticket=ticket)

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
        ticket = self._ticket()
        with self._conn.hold_heartbeat():   # as set_segment_rgb; no zoned profile reads aa 41
            await self._ensure_segment_count()
            n = self.segment_count
            frame = self._build_or_unsupported(
                build.segment_color_temp, _mask(z.segments, n), sent, self.profile.color_scheme, n)

            def apply() -> None:
                # A zone colour write leaves scene mode, as set_color_temp does.
                self._state.scene_code = None
                _fold.apply_zone_kelvin(self.profile, self._state, {z.power_index: sent})

            await self._commit(frame, apply, ticket=ticket)

    # -- scenes -------------------------------------------------------------
    async def set_scene(self, code: int) -> None:
        """Activate a scene by its BLE code (no upload).

        Raises GoveeBleNotSupported without SCENES."""
        self._require(Capability.SCENES)
        ticket = self._ticket()
        async with self._scene_lock, self._zone_power_lock():
            await self._activate_scene(code, self._scene_power_frames(), ticket)

    def _scene_power_frames(self) -> list[bytes]:
        """The power-on that leads a scene write on a :attr:`.DeviceProfile.scene_power_on` device not
        known to be on (``ApplyOffNeedOpenHelper.b``: the app's stored switch state, off when unknown), else
        none."""
        if self.profile.scene_power_on and self._state.is_on is not True:
            return self._power_frames(True)
        return []

    async def _activate_scene(self, code: int, lead: list[bytes], ticket: int) -> None:
        """Send the ``33 05 04`` activation, after the ``lead`` frames (:meth:`_scene_power_frames`), and
        apply its state (the device is on, in that scene, with no solid colour); the caller holds
        ``_scene_lock`` and :meth:`_zone_power_lock` and took the call's ``ticket`` before them
        (:meth:`_commit`)."""
        activate = build.scene_activate(code)
        await self._commit([*lead, activate] if lead else activate,
                           lambda: _fold.apply_scene(self.profile, self._state, code),
                           zones=dict.fromkeys(self._power_zones(), True) if lead else None, fixture=True,
                           ticket=ticket)

    def scene_upload_frames(self, scene: Scene, *, mtu: int = 20) -> list[bytes] | None:
        """The a3/a4 frames that upload ``scene`` to this device (per its profile's scene
        dialect and supported scene versions), or None when it is activate-only. ``mtu`` is the
        link's write size (``GoveeConnection.write_payload_size``), which only the H60A6's 0xA4
        graffiti upload uses. Pure: no I/O. It plans whatever effect ``scene`` holds, as the app's
        upload does (``ScenesOp.parseSceneV1``); take the Scene from
        ``load_scenes(sku, scene_versions=device.scene_versions)`` to send what the app sends."""
        return _scene_upload_frames(scene, self.profile.scene_dialect, self.scene_versions, mtu=mtu)

    async def set_scene_by_name(self, name: str) -> None:
        """Upload (if the scene needs it) and activate a bundled scene by name, one the Govee app
        lists at the scene set in force (:attr:`scene_names`), with the effect it plays there.

        Raises GoveeBleNotSupported without SCENES, and GoveeBleUnknownName (a GoveeBleInvalidValue
        and a GoveeBleNotSupported) for a name not listed at that set, a scene hidden there included,
        sending nothing. A scene that plays another of its effects at that set uploads and activates
        that effect. :attr:`scene_versions` is taken once per call: the name is looked up, and the
        upload planned, at that one set, so an advertisement that changes the pact mid-call can't split
        them. Where the set depends on a firmware version not read yet
        (:attr:`DeviceProfile.scene_versions_by_firmware`), that version is read first, on the same
        connection, so the name is looked up and planned at the device's own set, not the newest
        firmware's (:attr:`scene_names` may list the newest firmware's scenes until then). The bundled
        catalog is loaded in a worker thread, never on the event loop.

        The upload and the activation go out back to back: the app sends them as one composed
        controller (``BaseSceneViewMode.getSceneComposeController`` -> ``Compose4DefWrite4Multi``, an
        ``AbsComposeControllerWithOpResult`` that ``AbsComposeComm`` writes controller by
        controller). So no other scene write (``_scene_lock``), no read-back (``_read_gate``: a
        poll waits for the activation) and no heartbeat (the app skips it while a compose is in flight,
        ``AbsBle.I``) lands between them; other setters are not held back. ``_scene_lock`` is held for
        the whole call, the version read and the catalog load included, so a :meth:`release` called
        after it starts drops the link after the activation."""
        self._require(Capability.SCENES)
        ticket = self._ticket()
        # The whole call under _scene_lock, the version read and the catalog load (a worker-thread hop)
        # included: a release() called after it starts waits for it, and drops the link after the activation.
        async with self._scene_lock:
            await self._scene_by_name(name, ticket)

    async def _scene_by_name(self, name: str, ticket: int) -> None:
        """:meth:`set_scene_by_name`'s body: the caller holds ``_scene_lock`` and took the call's
        ``ticket``."""
        soft, hard = self._versions_missing(scenes=True)
        if soft or hard:   # the set depends on a version not read yet: read it before the lookup
            await self._read_missing_versions(soft, hard)
        versions = self.scene_versions   # taken once per call
        scene = (await async_load_scenes(self._sku, scene_versions=versions)).get(name)
        if scene is None:
            raise GoveeBleUnknownName(f"{self._sku}: unknown scene {name!r} at scene set {sorted(versions)}")
        dialect = self.profile.scene_dialect
        frames = _scene_upload_frames(scene, dialect, versions)   # before any I/O: a bad scene sends nothing

        def sized(size: int) -> list[bytes]:
            # Re-planned for the link it goes out on: the 0xA4 graffiti frames take its MTU.
            return _scene_upload_frames(scene, dialect, versions, mtu=size) or []

        async with self._zone_power_lock(), self._read_gate:
            with self._conn.hold_heartbeat():
                # Before the upload, not between it and the activation.
                await self._send_pending_unlock(ticket=ticket)
                lead = self._scene_power_frames()
                if frames:
                    if lead:   # the app prepends the switch to the whole composed write, upload included
                        # The switch is folded in only with the activation: until then a zone it lights that
                        # the state holds off loses its stamp, and if the switch or the upload raises, every
                        # zone does (a whole-device power write that may have reached the device, as in
                        # _commit).
                        lit = dict.fromkeys(self._power_zones(), True)
                        _fold.unconfirm_contradicted(self._state, lit)
                        try:
                            await self._conn.send_many(lead)
                            await self._conn.send_unacked(sized)
                        except BaseException:
                            _fold.unconfirm_zones(self._state)
                            raise
                        finally:
                            # As _commit does; counted as raised either way (no profile has both a global
                            # switch and this lead).
                            self._post_zone_power(lead, lit, {}, acked=False)
                        lead = []
                    else:
                        await self._conn.send_unacked(sized)
                await self._activate_scene(scene.code, lead, ticket)

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
    async def update(
        self, *, connect_attempts: int | None = None, connect_timeout: float | None = None,
    ) -> DeviceState:
        """Poll the device and fold the replies into state; returns the current state.

        Reads are not serialized against setters (the connection lock is per-operation), so a
        setter can complete while a read is in flight. Each read therefore snapshots
        ``_mutation_seq`` before it is issued and is DISCARDED (DEBUG log; no state merge, no
        ``_mark_authoritative``) if a setter applied state since (:meth:`_read_fresh`). A discarded
        read means "state is newer than this poll": ``update()`` still returns the current
        (setter-applied) state and notifies, but does not consume the one-time "first poll" step
        (``_polled_once``); callers simply poll again later. Static device-info is exempt (never
        raced by setters).

        ``connect_attempts`` and ``connect_timeout`` hold this poll's connects to tighter limits than
        the transport's, so a background poll at a device that doesn't answer gives the device lock
        up sooner, and a command queued behind it keeps more of its caller's deadline (the command's
        own ``const.OPERATION_TIMEOUT`` starts only once it holds the lock). They hold every connect
        this call makes: normally only its first read's, plus a reconnect when the link is lost
        mid-poll. Setters and other calls keep the defaults.

        * ``connect_attempts``: bleak-retry-connector's ``max_attempts`` for each establishment this
          call makes, i.e. how many attempts that time out or fail outright it makes before giving
          up. bleak-retry-connector also tries again after a transient failure (device not found, no
          free connection slot, ``ESP_GATT_CONN_FAIL_ESTABLISH``, ``le-connection-abort-by-local``)
          regardless, giving up only at the 9th, so pass ``connect_timeout`` to bound the time. A link
          that never came up is not tried again by the session retry. None:
          ``const.CONNECT_MAX_ATTEMPTS`` (3) per establishment, and the session retry
          (``const.SESSION_ATTEMPTS``) may establish once more. So ``connect_attempts=3`` is not the
          default: a link that never comes up is established once, not twice (one whose session
          setup failed is still re-established once, as at the default).
        * ``connect_timeout``: seconds each connect (the link, the BGC read, the handshake and the
          unlock, with their retries) may take. Then the link the library holds is torn down, its
          disconnect (and any an earlier operation's cancellation left running) waited for up to
          ``const.DISCONNECT_TIMEOUT`` (5 s) more and abandoned at that bound, and only then is
          GoveeBleTimeout raised, with the device lock free and the connection ready for the next
          operation. When that wait would run past the operation's ``const.OPERATION_TIMEOUT`` (a
          ``connect_timeout`` above ``OPERATION_TIMEOUT`` - ``DISCONNECT_TIMEOUT``, 40 s, or a later
          connect of the same operation, such as the pending unlock's write retry), the deadline ends
          it instead: GoveeBleTimeout ("operation exceeded 45s") is raised at the deadline, and the
          teardown is left to its abandon at its own ``DISCONNECT_TIMEOUT`` bound, as on any
          deadline. A call cancelled by its caller during that wait gives the lock up at once,
          leaving the teardown to the same abandon. A connect cancelled inside bleak is cleaned up by
          the BLE backend, which only ``const.OPERATION_TIMEOUT`` bounds (the lock is held until it is
          done): BlueZ disconnects it; bleak's CoreBluetooth backend (macOS) leaves the pending
          connection request, so the device may stay connected to the host. The limit is per
          connect, not per call: the reads after the connect are not counted, and when the link drops
          between reads the next read connects again with the whole limit, so a call that connects k
          times can take k times ``connect_timeout`` (plus each connect's teardown) in its connects.
          None: no limit of its own. Each operation's ``const.OPERATION_TIMEOUT`` (45 s) bounds it
          either way.

        Raises GoveeBleInvalidValue, before any I/O, unless ``connect_attempts`` is an integer >= 1
        and ``connect_timeout`` a finite number > 0 (where given)."""
        budget = (None if connect_attempts is None and connect_timeout is None
                  else ConnectBudget(connect_attempts, connect_timeout))
        # The poll is the app's connect read, during which it sends no heartbeat: none between its reads.
        with self._conn.hold_heartbeat():
            return await self._update(budget)

    async def _update(self, budget: ConnectBudget | None) -> DeviceState:
        """:meth:`update`'s body, its connect limits resolved, with the heartbeat held."""
        discarded_before = self._discarded_reads
        await self._send_pending_unlock(budget)
        if not self._scenes_warm and Capability.SCENES in self.capabilities:
            # Load the bundled scene catalog off the loop, so the sync scene_names / active_scene
            # properties are cache hits when read on the event loop: once it works, a failed load again
            # at each poll (the properties raise its failure meanwhile, with no I/O).
            try:
                await async_load_scenes(self._sku)
            except GoveeBleError as err:
                self._scenes_error = str(err)
                _LOGGER.debug("%s (%s): scene catalog not loaded: %s", self.address, self._sku, err)
            else:
                self._scenes_warm, self._scenes_error = True, None
        state_read = False   # this poll's state read brought a live field back (_finish_read)
        if self.profile.readback == "status":
            state_read = await self._read_status(budget)
        elif self.profile.readback == "polled":
            state_read = await self._read_polled(budget)
        elif self.profile.readback == "plug":
            state_read = await self._read_plug(budget)
        elif self.profile.readback == "none":
            pass
        else:
            _unhandled(self.profile.readback, "readback")
        if self.profile.ic_num_read_only and not self.profile.color_readback_live_ic:
            # The screen's connect read; it sizes nothing.
            await self._read_reply(build.ic_count_query(), budget)
        # Segment / single-colour read-back layered on top (spec Change 7).
        if self.profile.color_readback == "mechanism_b":
            await self._read_mechanism_b(budget)
        elif self.profile.color_readback == "mechanism_a_direct":
            if self.profile.section_count_read and self._segment_count is None:
                _LOGGER.debug("%s (%s): segment count unknown; skipping segment colour read-back",
                              self.address, self._sku)
            else:
                await self._read_mechanism_a_direct(budget)
        elif self.profile.color_readback == "":
            pass   # no segment colour read-back ("" is ColorReadBack's "none")
        else:
            _unhandled(self.profile.color_readback, "color_readback")
        if self.profile.gradual:
            def apply_gradual(on: bool) -> None:
                self._state.gradual = on

            await self._read_command("gradual", build.gradual_query(),
                                     parse.parse_gradual, apply_gradual, budget)
        elif self.profile.gradual_read_only:
            # The screen's connect read; nothing is exposed.
            await self._read_reply(build.gradual_query(), budget)
        # Device-info (mac/hw/fw/serial) — static-ish, read once the device answers one of them; then a
        # version a firmware-gated fact tests that the read did not bring back, alone, on each later poll.
        missing = self._versions_missing()
        if (not self._device_info_read or any(missing)) and self.profile.readback != "none":
            try:
                if not self._device_info_read:
                    await self._read_device_info(budget, state_read=state_read)
                else:
                    await self._read_missing_versions(*missing, budget=budget)
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

    async def _read_plug(self, budget: ConnectBudget | None = None) -> bool:
        """Poll a plug's relay state (aa 01 -> relay bitmask) so state.is_on reflects the device
        rather than the last command sent. On means one of the profile's ``relay_on_mask`` bits is
        set (:func:`.parse.parse_relay_on`): bit 0 on the single-outlet SKUs, as the app's
        BleOpV1/BleOpV3 detail screens read it. Returns whether the relay state was read back."""
        prof = wire_profile(self.profile)

        def apply(on: bool) -> None:
            self._state.is_on = on

        since = self._power_unsure_seq
        applied = await self._read_command(
            "plug power", build.power_query(), lambda r: parse.parse_relay_on(r, prof), apply,
            budget) == "applied"
        self._finish_read(applied, applied, since)
        return applied

    async def _read_device_info(
        self, budget: ConnectBudget | None = None, *, state_read: bool = False,
    ) -> None:
        """Populate the static device-info fields (``aa 06``, ``aa 07 03``, ``aa 07 02``;
        :func:`._readback.read_device_info`). Best-effort, and done once the device has answered;
        unanswered reads leave fields None. ``state_read``: the same poll's state read brought a
        live field back."""
        answered = await _readback.read_device_info(self, lambda f: self._read_reply(f, budget))
        # Done once the reads completed without raising on a device that answered in this poll: one
        # of these reads, or the poll's state read. An unanswered selector on a responsive device is
        # not retried every poll (each costs INFO_READ_TIMEOUT), but a poll on which the device was
        # silent throughout (wrong cipher, lost notifications, out of range) or a link drop mid-read
        # leaves it for the next poll, even when an earlier poll did read state.
        self._device_info_read = answered or state_read

    def _versions_missing(self, *, scenes: bool = False) -> tuple[bool, bool]:
        """The version reads a firmware-gated fact of the profile still waits for, ``(aa 06, aa 07 03)``:
        each version such a fact tests (:func:`._fold.gated_versions`; ``scenes``: the scene set's gates
        only) that is not read yet. Until it is read its gates count as met (the newest firmware's facts)."""
        soft, hard = _fold.gated_versions(self.profile, scenes=scenes)
        return (soft and self._state.firmware_version is None, hard and self._state.hardware_version is None)

    async def _read_missing_versions(self, soft: bool, hard: bool,
                                     budget: ConnectBudget | None = None) -> None:
        """Read the soft (``aa 06``) and / or hard (``aa 07 03``) version alone, the ones
        :meth:`_versions_missing` names (:func:`._readback.read_versions`): the device-info read is done
        once the device answered, but a version a firmware-gated fact tests is read until it is in, so one
        lost reply never holds the facts at the newest firmware's for the life of the Device."""
        _LOGGER.debug("%s (%s): reading the %s version a firmware gate tests", self.address, self._sku,
                      " and ".join(v for v, want in (("soft", soft), ("hard", hard)) if want))
        await _readback.read_versions(self, lambda f: self._read_reply(f, budget), soft=soft, hard=hard)

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

    async def _read_mechanism_b(self, budget: ConnectBudget | None = None) -> None:
        """H61A8's per-segment colour via BulbGroupColorV2 (``aa a5 <seq>``, V2;
        :func:`._readback.walk_mechanism_b`), applied whole unless a setter raced it."""
        await self._read_fresh(
            "segment colour",
            lambda: _readback.walk_mechanism_b(self, lambda f: self._read_reply(f, budget)),
            self._apply_segments)

    def _apply_segments(self, segments: list[Segment]) -> None:
        """Fold a whole segment-colour read-back into state."""
        self._state.segments = segments

    async def _read_mechanism_a_direct(self, budget: ConnectBudget | None = None) -> None:
        """H6047/H6641/H6062/H6061 per-segment colour via Controller4ColorInfoByGroup's direct
        per-group requests (``aa a5 <group>``; :func:`._readback.walk_mechanism_a_direct`), applied
        whole unless a setter raced it."""
        await self._read_fresh(
            "segment colour",
            lambda: _readback.walk_mechanism_a_direct(self, lambda f: self._read_reply(f, budget)),
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

    async def _query_status(
        self, budget: ConnectBudget | None = None,
    ) -> tuple[reassemble.StatusReply, list[bytes], list[bytes]]:
        async with self._read_gate:   # never between a scene's upload and its activation
            await self._send_pending_unlock(budget)   # a secret set on the live session goes first
            frames = await self._conn.query(
                build.status_query(types=self._status_types()), timeout=STATUS_READ_TIMEOUT,
                **_budget_kw(budget))
        ac = [f for f in frames if f and f[0] == build.PRO_MULTI_AC]
        status = reassemble.parse_status(ac, pieces=self.segment_count, decode=wire_profile(self.profile))
        return status, ac, frames

    async def _read_status(self, budget: ConnectBudget | None = None) -> bool:
        # `applied` (returned): a live field came back (state no longer optimistic). `power_read`:
        # on/off came back, so the advertised on/off is suppressed for a while (_finish_read).
        # Neither is set by an empty burst (the "state left stale" case) nor by the static
        # device-info fields.
        #
        # A reply may predate a power write the device acked just before (FIRMWARE-SOURCED). The H60A6
        # dispatcher acks a 33 01 / 33 30 (and a 33 04 wake) once it has posted the message
        # (_post_zone_power; _mqtt_response_pushDeviceMsg FUN_9b027b88, HAL_QueuePush @0x9b027c42; the ack
        # goes out at the dispatcher's end, FUN_9b04b8ac @0x9b026cac). Its own thread applies it later
        # (_mqtt_response_thread_func FUN_9b028fb0 pops s_mqtt_resp_handle_queue @0x9b028fc2 and runs
        # _mqtt_response_handle FUN_9b028bc8: 0xB2 @0x9b028ea4-0x9b028eb0, 0xB7 @0x9b028f2c-0x9b028f32 ->
        # FUN_9b0278dc), while the 0xAC reply is built from the flags as they are (ble_msg_one_to_more
        # FUN_9b04bdd8: TLV 0x01 @0x9b04be30, TLV 0x30 @0x9b04c0ae-0x9b04c0b6). "Just before" is: a write
        # that went out (acked or not) since the last status reply that carried its zones. The thread applies
        # them in order, and one that raised may or may not be among them, so this reply's TLV 0x30 vouches
        # for a zone only if it reports the one power every such write gave the zone (_fold.apply_status
        # unsettled; the read after it does). Unless it does so for every posted zone, its on/off is not
        # trusted to rule a wake out either (_power_unsure).
        applied = power_read = False
        since = self._power_unsure_seq

        def apply(st: reassemble.StatusReply) -> None:
            nonlocal applied, power_read
            posted = self._zone_power_posted
            unsettled = [i for i, on in st.zone_power.items() if posted.get(i, {on}) != {on}]
            applied, power_read = _fold.apply_status(self.profile, self._state, st, self._pact,
                                                     unsettled=unsettled)
            if power_read and any(i not in st.zone_power or i in unsettled for i in posted):
                self._set_power_unsure()
            for i in st.zone_power:
                posted.pop(i, None)

        # The burst is the whole poll, as the app's connect read is: its 0x05 TLV carries the mode
        # (_fold.apply_status), so no aa 05 read follows it.
        await self._read_fresh(
            "status", lambda: _readback.status_burst(self, lambda: self._query_status(budget)), apply)
        self._finish_read(applied, power_read, since)
        return applied

    async def _read_polled(self, budget: ConnectBudget | None = None) -> bool:
        """The polled read-back (aa 01 / aa 04 / aa 36 / aa 41 / aa 05); returns whether a live field
        came back (see :meth:`_read_status`)."""
        def apply_power(on: bool) -> None:
            self._state.is_on = on
            held = self._state.zone_power
            if self.profile.bar_switch and on != any(held.values()):
                # aa 01 answers bar 0 | bar 1 (H6047 FUN_01004f32, query path 0x01004fb0-0x01004fd4), so
                # this reply rules out the bars' held power: their stamps go, and the aa 36 read after it
                # (if answered) re-reads them. The H6042 (no zones; no firmware in hand) only ever loses a
                # stamp here.
                _fold.unconfirm_contradicted(self._state, dict.fromkeys(held, on))

        def apply_brightness(raw: int) -> None:
            self._state.brightness = self._brightness_pct(raw)

        # Always read power: a device turned on externally (app, remote, wall
        # switch) must be noticed even when we last saw it off.
        # A light is on only for a byte of 1, as the app's control screens read it (parse_light_power).
        since = self._power_unsure_seq   # when the read that gives the on/off is issued (_finish_read)
        power_read = await self._read_command(
            "power", build.power_query(), parse.parse_light_power, apply_power, budget) == "applied"
        applied = power_read   # see _read_status / _finish_read
        if await self._read_command("brightness", build.brightness_query(),
                                    parse.parse_brightness, apply_brightness, budget) == "applied":
            applied = True
        if self.profile.bar_switch:
            bars_since = self._power_unsure_seq
            if await self._read_bar_switch(budget) == "applied":
                applied = power_read = True   # the bars set is_on (device on iff either bar is on)
                since = bars_since
        if self.profile.section_count_read:
            # The app's connect order: aa 01, aa 04, aa 41, aa 05 (ControllerStrategyConfig.O).
            await self._read_section_count(budget)
        if self.capabilities & _MODE_REPORT_CAPS and await self._read_mode(budget) == "applied":
            applied = True
        self._finish_read(applied, power_read, since)
        return applied

    async def _read_mode(self, budget: ConnectBudget | None = None) -> _ReadResult:
        """Read the aa 05 mode report into state (:meth:`_apply_mode_reply`)."""
        prof = wire_profile(self.profile)
        return await self._read_command("mode", build.mode_query(), lambda r: parse.mode_reply(r, prof),
                                        lambda m: _fold.apply_mode(self.profile, self._state, m), budget)

    async def _read_section_count(self, budget: ConnectBudget | None = None) -> bool:
        """Read the segment count the device reports (``aa 41`` -> ``sectionNum``) and adopt it
        as :attr:`segment_count`, as the app does on every connect. Returns False (state left as
        it was) when the device does not answer or reports a count the masks cannot carry."""
        reply = await self._read_reply(build.section_count_query(), budget)
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
        known. Every caller holds the heartbeat across this read and the write it sizes
        (``GoveeConnection.hold_heartbeat``), as :meth:`update` holds its reads: the app reads the count
        in its connect read, during which it sends none (``ShareVM.R`` -> ``updateSkipHeart``)."""
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
        budget: ConnectBudget | None = None,
    ) -> _ReadResult:
        """:meth:`_read_fresh` of one ``aa`` read: the reply to ``frame``, decoded by ``parser`` (None: not
        a usable reply)."""
        async def read() -> _T | None:
            reply = await self._read_reply(frame, budget)
            return None if reply is None else parser(reply)

        return await self._read_fresh(what, read, apply)

    async def _read_reply(self, frame: bytes, budget: ConnectBudget | None = None) -> bytes | None:
        """Send one ``aa`` read and return its reply - the ``aa`` frame with the request's own command
        (:func:`.parse.request_command`) - or None after ``INFO_READ_TIMEOUT``. A single-reply read:
        ``idle_gap=None``, so a stray ``aa`` frame (a late reply to an earlier read) can't cut the wait
        short to the burst idle gap. ``budget``: the calling poll's connect limits (:meth:`update`), or
        None for the transport's."""
        command_type = parse.request_command(frame)
        async with self._read_gate:   # never between a scene's upload and its activation
            await self._send_pending_unlock(budget)   # a secret set on the live session goes first
            frames = await self._conn.query(
                frame, opcode=build.PRO_READ, terminal=command_type, timeout=INFO_READ_TIMEOUT,
                idle_gap=None, **_budget_kw(budget))
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
    """Build a capability-driven Device for `sku`, or None if unsupported at the advertisement's pact.

    As ``registry.create_device`` (which raises instead): the pact of ``advertisement_data`` picks the
    SKU's row (``profile_for(sku, pact=...)``), so a pact the SKU's rows refuse gives None."""
    adv = _bound_advertisement(advertisement_data, ble_device.name, sku)
    p = profile_for(sku, pact=adv.pact if adv is not None else None)
    if p is None:
        return None
    return Device(
        p, ble_device, adv if adv is not None else advertisement_data, sku=sku, secret=secret,
        frame_log=frame_log, idle_disconnect=idle_disconnect,
    )
