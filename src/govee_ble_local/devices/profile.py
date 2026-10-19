"""Data-driven device descriptors.

A ``DeviceProfile`` carries everything the capability-driven :class:`~.device.Device`
needs for one SKU family. The supported table, :data:`PROFILES`, is generated from the spec:
``spec/devices.yaml``'s supported ``client_profile`` rows (``_generated/devices.py``, built by
``tools/gen_devices.py``) resolved by :mod:`._resolve`, one profile per group of SKUs whose
resolved facts are identical. The wire cipher is resolved at connect from the advertisement, so
``encryption`` here is only the fallback default; ``requires_secret`` is the separate account-lock
capability.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, Literal, get_args

from .._generated import devices as _devices
from ..exceptions import GoveeBleInvalidValue
from ..models import Capability, Encryption, Zone
from ..wire import _spec
from ..wire.build import ColorScheme
from . import _resolve
from .brightness import percent_to_raw

# scene dialects: "A" = library parseSceneV1 (comType from scene_versions);
# "B_h60a6" = H60A6 DIY (0x58; 0xA4-MTU graffiti / 0xA3 DIY by length gate);
# "B_h6052" = H6052 type-5 professional graffiti (commByte 9).
SceneDialect = Literal["A", "B_h60a6", "B_h6052"]
# "status" (0xAC burst) | "polled" (aa 01/04/05) | "plug" (aa 01 relay bitmask) | "none"
ReadBack = Literal["status", "polled", "plug", "none"]
ColorReadBack = Literal["", "mechanism_b", "mechanism_a_direct"]
# How the colour sub-modes of an ``aa 05`` mode report are decoded (the SKU's Info4Detail mode parse): a
# name of the spec's mode_color_parse table (devices.yaml ``mode_color_parse``, which maps each colour
# sub-mode to a color_decode; :func:`wire_profile`), e.g. "kelvin_15_rgb_kelvin_0d" (H60A6) or
# "rgb_kelvin_tint" (the 0x0d bulbs). The one other name is the library's 1.x default for a profile that
# doesn't choose one, "rgb_kelvin" (0x15 -> [gradual][kelvin u2be]; 0x0d -> [r, g, b, kelvin u2be, tint],
# any non-zero Kelvin = CCT, read unsigned: wire.parse.mode_color unsigned_kelvin), which no app screen uses.
ModeColorParse = str
_DEFAULT_PARSE: Final = "rgb_kelvin"

# How a plug's whole-device power is written (its main switch on the app's detail screen):
# "outlet0"     = 33 01 11 / 10, the relay-0 encoding (goodsType 43: H5080 / H5083 / H5085, one outlet;
#                 and 1.3.0's frame for every plug);
# "all_outlets" = 33 01 FF / F0, every outlet (goodsType 50 H5082, 90 H5160 / H5161;
#                 SwitchControllerV2(on, 15));
# "total_switch"= 33 02 01 / 00 (goodsType 307 H5089; Controller4TotalSwitch).
RelayPower = Literal["outlet0", "all_outlets", "total_switch"]

# The spec's mode_color_parse table as govee_profile mode_decode params: name -> {c15: color_decode, ...} (a
# sub-mode left out is not decoded), plus the library default (_DEFAULT_PARSE).
_CD = _spec.Common.ColorDecode
_GRADUAL_KELVIN: Final = int(_CD.gradual_kelvin_s16be)
_DECODES: Final[Mapping[str, Mapping[str, int]]] = MappingProxyType({
    _DEFAULT_PARSE: {"c15": _GRADUAL_KELVIN, "c0d": int(_CD.rgb_kelvin_tint)},
    **{name: {"c" + sub_mode[2:]: int(_CD[decode]) for sub_mode, decode in strategy["sub_modes"].items()}
       for name, strategy in _devices.MODE_COLOR_PARSE.items()},
})
# A bar_switch device's 0x15 report carries a Kelvin per bar ([gradual][kelvin left s16be][kelvin right
# s16be]; h6047 Info4Detail subMode4Color strategy, split_h6047 detail/Info4Detail.java:179-180), whatever the
# profile's parse of the other sub-modes (the spec's kelvin_pair_15; 1.x's H6047 profile named kelvin_15).
_BAR_KELVIN: Final = int(_CD.gradual_kelvin_pair_s16be)


@dataclass(frozen=True)
class DeviceProfile:
    """Static descriptor of one SKU family: everything the capability-driven
    :class:`~.device.Device` needs to drive it.

    ``capabilities`` gate the setters; the other fields choose the frames each setter sends
    (``color_scheme``, ``relay``, ``bar_switch``, ...), the reads :meth:`~.device.Device.update`
    makes (``readback``, ``color_readback``, ...) and how their replies are decoded. A
    :class:`~.device.Device` reads its profile once, at construction. The table of supported SKUs
    is :data:`PROFILES`; :func:`profile_for` looks one up. Construction validates each field and
    raises GoveeBleInvalidValue (a ValueError) on a value no Device can use."""

    skus: tuple[str, ...]
    """The SKUs this profile covers; the first is the default SKU of a Device built from it (a list
    is accepted and kept as a tuple)."""
    capabilities: frozenset[Capability]
    """The features the SKUs support; a setter for anything else raises GoveeBleNotSupported."""
    color_scheme: ColorScheme = "h60a6"
    """The colour-frame family the builders in :mod:`..wire.build` emit for this SKU."""
    encryption: Encryption = Encryption.AES_RC4_PSK
    """Fallback command-channel mode, used when no advertisement says otherwise (the cipher is
    resolved at connect from the advertisement)."""
    segments: int = 0
    """Segment count: the whole-device mask width and the segment index bound (H6061: its panel
    count); 0 for a single-colour device. ``Device.set_segment_count`` overrides it with a count the
    device reports."""
    zones: tuple[Zone, ...] = ()
    """Independently switchable zones (empty for an unzoned device)."""
    scene_versions: frozenset[int] = field(default_factory=frozenset)
    """The SKU's scene versionArray (the app's supported-scene version set, e.g.
    Support.supportScenesOp): a library scene is uploaded only when the version its scene type
    needs is in the set, and is activate-only otherwise (:func:`..scenes.scene_upload_params`).
    :attr:`scene_versions_by_pact` (the advertised pact) and :attr:`scene_versions_by_firmware` (the
    read firmware versions) may replace it (``Device.scene_versions`` is the set in force)."""
    scene_dialect: SceneDialect = "A"
    """How a scene's parameters become upload frames; see :data:`SceneDialect`."""
    min_kelvin: int | None = None
    """Lowest colour temperature of the SKU's Kelvin range (KelvinConfig, Govee Home 7.7.02),
    inclusive.

    Besides clamping CCT writes, the range decides which RGB picks are sent as CCT: a picked colour
    that equals one of the app's Kelvin tint colours (wire.cct, Constant.k2) is a CCT frame only when
    its Kelvin lies in [min_kelvin, max_kelvin] (KelvinConfig.i), on every scheme with a CCT mode
    (build.rgb_as_kelvin; Config.Yt / bu / Zt)."""
    max_kelvin: int | None = None
    """Highest colour temperature of the SKU's Kelvin range, inclusive (see :attr:`min_kelvin`)."""
    requires_secret: bool = False
    """Account-locked: with a secret set (``Device.set_secret``), every session sends the ``33 b2``
    unlock right after the handshake."""
    relay: bool = False
    """Plug power: the goodsType's main-switch frame (:attr:`relay_power`); every session sends the
    ``33 b5`` time sync after the unlock (and :attr:`power_resync` re-sends it after a power write)."""
    bar_switch: bool = False
    """H6047: both bars in one 33 36 frame (per-bar buttons; whole-device power is 33 01), read via
    aa 36, and the aa 05 15 reply carries one kelvin per bar."""
    global_power: bool = False
    """Zoned fixture whose global 33 01 switch drives every zone (live-verified on H60A6
    2026-10-12: off clears both zones; on lights both even if each zone was off), so whole-fixture
    power is ONE frame instead of one per zone."""
    gradual: bool = False
    """Supports the 0xA3 gradual/fade-on-handoff flag (written and read back)."""
    readback: ReadBack = "none"
    """How :meth:`~.device.Device.update` reads the device's state; see :data:`ReadBack`."""
    color_readback: ColorReadBack = ""
    """Segment-colour read-back mechanism layered on top of :attr:`readback` (spec Change 7).

    ``""`` none | ``"mechanism_b"`` (H61A8 BulbGroupColorV2, 0xAA 0xA2/0xA5 per-batch, group size 3) |
    ``"mechanism_a_direct"`` (H6047/H6641 Controller4ColorInfoByGroup, 0xAA 0xA5 per-group direct
    request, group size 4 — a DIFFERENT controller from mechanism_b that happens to share the
    0xA5 wire opcode). 0x0d bulbs read colour/CCT from the polled 0x05 mode report instead."""
    color_readback_per_batch: int = 3
    """Group size per reply: mechanism_b V2 = 3 (the default; V1 = 4); mechanism_a_direct must
    set 4."""
    color_readback_segments: int | None = None
    """Static read-back piece count, when it differs from the WRITE/addressable :attr:`segments`
    count (a per-SKU fact from devices.yaml).

    The newdetail screens read getColorPieceSize pieces, while H6047's old detail screen reads
    getBulbStringMaxNum = the 10 write segments, so it leaves this None. None -> falls back to
    :attr:`segments`. Ignored when :attr:`color_readback_live_ic` is True (the live 0x40 read
    supplies the count instead)."""
    color_readback_live_ic: bool = False
    """mechanism_a_direct only: read the live 0x40 IC count first and derive the colour-piece count
    from it instead of a static :attr:`color_readback_segments`, as the app does for SKUs whose
    segmentation isn't knowable from the static device table (H6641).

    The IC count is clamped to :attr:`color_readback_ic_range` and divided (rounding up) by
    :attr:`color_readback_ic_per_piece`; the group count is then ceil(pieces / group size). The
    reply's third byte is not used."""
    section_count_read: bool = False
    """The device reports its own segment count with `aa 41` -> [IC s16be, sectionNum u8]
    (striplight ControllerIcSegmentNum; H6062).

    The app reads it on every connect (a mandatory step: a timeout fails the whole info read) and
    uses sectionNum for every mask, every segment index and the `aa a5` group walk, so Device reads
    it before the first write that needs it and on each poll, and :attr:`segments` is only the
    profile's upper bound."""
    brightness_max: int = 100
    """Deprecated (removed at the next major release); use :attr:`brightness_range`, which
    overrides it.

    Full-scale value of the brightness the device REPORTS (status TLV 0x04 / aa 04 reply). Every
    curated SKU reports percent (all live read-backs are 0..100) and leaves it at 100. Writes are
    always percent."""
    brightness_range: tuple[int, int] | None = None
    """Raw (min, max) brightness range for devices whose 33 04 / aa 04 byte is NOT a percent
    (BrightnessConfig.Config version 0 = (20, 254), e.g. H6188).

    Writes map 1..100 onto it and reads map back (devices/brightness.py, NumberUtil); overrides
    :attr:`brightness_max`."""

    # Fields added after 1.3.0 are keyword-only and come after every 1.3.0 field, so the positional
    # signature and the ``dataclasses.fields()`` / ``astuple()`` order of 1.3.0 stay as they were.
    relay_on_mask: int = field(default=0xFF, kw_only=True)
    """Plug read-back (aa 01): the relay bits of the reply byte that make the device "on" (see
    :attr:`relay`).

    The app's detail screen decides it per goodsType: bit 0 on the single-outlet screens, the
    outlets' bits OR-ed together on the multi-outlet ones (devices.yaml's plug rows). 0xFF = any bit."""
    relay_power: RelayPower = field(default="outlet0", kw_only=True)
    """Plug power frame (see :data:`RelayPower`); only read when :attr:`relay` is set. The default keeps
    1.3.0's ``33 01 11 / 10``."""
    advert_on_mask: int | None = field(default=None, kw_only=True)
    """Plug advertisement on/off: the bits of the advertised switch byte that make the device "on";
    None (every light) = the byte equals 1 (``AbsBaseBleModel.A``).

    The plugs' device-list models decode it per goodsType (h5080 ``Model.A``,
    ``Model4Plug2.A``), which is not always the read-back's :attr:`relay_on_mask` (devices.yaml's plug
    rows)."""
    color_readback_ic_range: tuple[int, int] | None = field(default=None, kw_only=True)
    """The (min, max) clamp for the live IC count (see :attr:`color_readback_live_ic`)."""
    color_readback_ic_per_piece: int = field(default=1, kw_only=True)
    """IC count per colour piece (see :attr:`color_readback_live_ic`)."""
    mode_color_parse: ModeColorParse = field(default=_DEFAULT_PARSE, kw_only=True)
    """Decoding of the aa 05 colour sub-modes: a name of the spec's mode_color_parse table; see
    :data:`ModeColorParse`."""
    status_color_groups_pact: tuple[int, int] | None = field(default=None, kw_only=True)
    """``readback="status"``: the 0xAC status read asks for the 0xA5 colour groups only while the
    advertised pact is this ``(pact_type, min_pact_code)``: the same pact type and a pact code at least
    that. None = always. A device whose pact has not been heard keeps asking for them (the app always
    knows the pact; the library may not)."""
    power_resync: bool = field(default=False, kw_only=True)
    """Plug (gt 50 / 90): after every acked power write, re-read ``aa 06`` and ``aa 07 03`` and re-send
    the ``33 b5`` time sync, as the app re-runs its connect read list then (``BleOpV2`` /
    ``BleOpV4.onEventSwitchV2`` -> ``G()``). Best effort: a failure is only logged."""
    kelvin_by_hardware: tuple[tuple[str, int, int], ...] = field(default=(), kw_only=True)
    """Kelvin ranges that replace [:attr:`min_kelvin`, :attr:`max_kelvin`] from a hardware version up:
    ``(min_hard_version, min_kelvin, max_kelvin)`` entries, highest version first, of which the first
    the device's hardware version (``aa 07 03``) is at least applies, compared as strings, as the app
    does (``KelvinConfig.Config.z2``: ``versionHard >= "3.08.01"``; a devices.yaml row's ``by_firmware``).
    Until the hardware version is read, the range valid on every revision applies: the base range cut to
    each entry's (``Device.min_kelvin`` / ``max_kelvin`` give the range in force). Needs both
    :attr:`min_kelvin` and :attr:`max_kelvin`; each entry must overlap the base range, and so must the
    range valid on every revision."""
    mode_reports_kelvin: bool = field(default=True, kw_only=True)
    """Whether the ``aa 05`` mode report carries the Kelvin of a colour-temperature write. False where it
    reads 0 after one (H61A8: ``15 01 00 00`` after either write, bench 2026-10-17): a colour report of
    Kelvin 0 or below then leaves ``color_temp_kelvin`` as the library set it, as the app's screen keeps
    what it set. Only an RGB or scene write, or a scene report, clears it. Since the device reports
    ``15 01 00 00`` after an RGB write too, a Kelvin the library sent stays in state across a colour
    change made elsewhere (the Govee app)."""
    mode_color_parse_status: ModeColorParse | None = field(default=None, kw_only=True)
    """Decoding of the 0xAC status read's TLV 0x05 where it differs from the aa 05 reply's (devices.yaml
    ``mode_color_parse_status``); None = :attr:`mode_color_parse`."""
    scene_versions_by_firmware: tuple[tuple[str, str, tuple[int, ...]], ...] = field(
        default=(), kw_only=True)
    """Scene sets that replace :attr:`scene_versions` from a firmware version up: ``(min_soft_version,
    min_hard_version, versions)`` entries, ``""`` for no gate on that version, of which each one whose gates
    the device's soft (``aa 06``) and hard (``aa 07 03``) versions meet applies, in order, the last winning,
    after :attr:`scene_versions_by_pact` (a devices.yaml row's ``by_firmware``: goodsType 19's
    ``Support.newVersion4Ble``). Versions compare field by field (``1.06.00``). A version not yet read meets
    every gate on it, as the newest firmware would, so until the device-info read the last entry's set
    applies (1.x's assumption), and a single unread version does not hold back the other's gates; a read
    version that does not parse meets none."""
    scene_versions_by_pact: tuple[tuple[tuple[int, int], tuple[int, ...]], ...] = field(
        default=(), kw_only=True)
    """Scene sets that replace :attr:`scene_versions` on an advertised pact: ``((pact_type, pact_code),
    versions)`` entries (a devices.yaml row's ``by_pact``: goodsType 19 at pact code 2, whose
    ``newVersion4Ble`` is 1 whatever the soft version). Applied before :attr:`scene_versions_by_firmware`,
    the order of the spec's resolution (``govee_reference.client_row``); a pact not yet heard applies none."""

    def __post_init__(self) -> None:
        """Validate each field: GoveeBleInvalidValue (a ValueError) on a value no Device can use.

        Fail at construction, not silently at poll time, on a typo. Values that are each valid but
        that the builders can't serve together (a capability the colour scheme has no frame for, a
        direct colour read-back in groups other than 4, a live IC read without its derivation) are
        accepted here, as in 1.3.0; the setter or read concerned then raises GoveeBleNotSupported or
        does without. The generated table's values are checked, those combinations included, when it is
        built (devices/_resolve.py), so none reaches this check."""
        if isinstance(self.skus, list):
            # 1.3.0 accepted a list; keep it as the declared tuple, so the frozen profile stays hashable.
            object.__setattr__(self, "skus", tuple(self.skus))
        if (not isinstance(self.skus, tuple) or not self.skus
                or not all(isinstance(sku, str) and sku for sku in self.skus)):
            raise GoveeBleInvalidValue(
                f"DeviceProfile(skus={self.skus!r}): skus must be non-empty SKU strings")
        where = _where(self)   # the error messages' prefix
        for name, allowed in (
            ("color_scheme", ColorScheme),
            ("scene_dialect", SceneDialect),
            ("readback", ReadBack),
            ("color_readback", ColorReadBack),
            ("relay_power", RelayPower),
        ):
            value = getattr(self, name)
            if value not in get_args(allowed):
                raise GoveeBleInvalidValue(f"{where}: {name}={value!r} not in {get_args(allowed)}")
        for name in ("mode_color_parse", "mode_color_parse_status"):
            value = getattr(self, name)
            if value not in _DECODES and not (value is None and name == "mode_color_parse_status"):
                raise GoveeBleInvalidValue(f"{where}: {name}={value!r} is not a mode_color_parse name "
                                           f"(spec/devices.yaml mode_color_parse, or {_DEFAULT_PARSE!r})")
        if self.brightness_range is not None:
            lo, hi = self.brightness_range
            # 100 % must map to a byte too, or set_brightness(100) would fail on valid input.
            if not 0 <= lo < hi <= 0xFF or percent_to_raw(100, lo, hi) > 0xFF:
                raise GoveeBleInvalidValue(f"{where}: brightness_range={self.brightness_range!r}")
        if self.brightness_max < 1:
            raise GoveeBleInvalidValue(f"{where}: brightness_max={self.brightness_max!r}")
        for name in ("relay_on_mask", "advert_on_mask"):
            mask = getattr(self, name)
            if mask is None and name == "advert_on_mask":
                continue
            # bool is an int: a mask given as True would otherwise be accepted as bit 0.
            if isinstance(mask, bool) or not isinstance(mask, int) or not 1 <= mask <= 0xFF:
                raise GoveeBleInvalidValue(f"{where}: {name}={mask!r}")
        gate = self.status_color_groups_pact
        if gate is not None and not (
                isinstance(gate, tuple) and len(gate) == 2
                and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 0xFF for v in gate)):
            raise GoveeBleInvalidValue(f"{where}: status_color_groups_pact={gate!r}")
        if self.color_readback_per_batch < 1:   # a group size of 0 would never finish a read-back
            raise GoveeBleInvalidValue(
                f"{where}: color_readback_per_batch={self.color_readback_per_batch!r}")
        ic_range = self.color_readback_ic_range
        if ic_range is not None and not 0 <= ic_range[0] <= ic_range[1]:
            raise GoveeBleInvalidValue(f"{where}: color_readback_ic_range={ic_range!r}")
        if self.color_readback_ic_per_piece < 1:
            raise GoveeBleInvalidValue(
                f"{where}: color_readback_ic_per_piece={self.color_readback_ic_per_piece!r}")
        if isinstance(self.kelvin_by_hardware, list):
            object.__setattr__(self, "kelvin_by_hardware", tuple(map(tuple, self.kelvin_by_hardware)))
        self._check_kelvin_by_hardware(where)
        self._check_scene_versions_by_firmware(where)
        self._check_scene_versions_by_pact(where)

    def _check_scene_versions_by_pact(self, where: str) -> None:
        """:attr:`scene_versions_by_pact`'s rules: ``((pact_type, pact_code), versions)`` entries of
        non-negative ints (a list is kept as a tuple)."""
        gated = tuple((tuple(pact), tuple(versions)) for pact, versions in self.scene_versions_by_pact)
        object.__setattr__(self, "scene_versions_by_pact", gated)
        for pact, versions in gated:
            if (len(pact) != 2
                    or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
                               for v in (*pact, *versions))):
                raise GoveeBleInvalidValue(f"{where}: scene_versions_by_pact={gated!r}")

    def _check_scene_versions_by_firmware(self, where: str) -> None:
        """:attr:`scene_versions_by_firmware`'s rules: ``(soft, hard, versions)`` entries with at least one
        ``a.bb.cc`` gate and non-negative int versions (a list is kept as a tuple)."""
        gated = tuple((soft, hard, tuple(versions))
                      for soft, hard, versions in self.scene_versions_by_firmware)
        object.__setattr__(self, "scene_versions_by_firmware", gated)
        for soft, hard, versions in gated:
            gates = [g for g in (soft, hard) if g != ""]
            if (not gates or not all(isinstance(g, str) and _resolve.version(g) is not None for g in gates)
                    or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in versions)):
                raise GoveeBleInvalidValue(f"{where}: scene_versions_by_firmware={gated!r}")

    def _check_kelvin_by_hardware(self, where: str) -> None:
        """:attr:`kelvin_by_hardware`'s rules: well-formed entries, highest version first, a base range,
        and an overlap with it for each entry and for the range valid on every revision."""
        gated = self.kelvin_by_hardware
        if not gated:
            return
        bad = f"{where}: kelvin_by_hardware={gated!r}"
        for entry in gated:
            if not (isinstance(entry, tuple) and len(entry) == 3 and isinstance(entry[0], str) and entry[0]
                    and all(isinstance(k, int) and not isinstance(k, bool) for k in entry[1:])
                    and 0 < entry[1] <= entry[2]):
                raise GoveeBleInvalidValue(bad)
        lo, hi = self.min_kelvin, self.max_kelvin
        if lo is None or hi is None:
            raise GoveeBleInvalidValue(f"{bad} needs min_kelvin and max_kelvin")
        versions = [entry[0] for entry in gated]
        if versions != sorted(versions, reverse=True) or len(set(versions)) != len(versions):
            raise GoveeBleInvalidValue(f"{bad}: list the versions highest first, once each")
        if any(k_lo > hi or k_hi < lo for _version, k_lo, k_hi in gated):
            raise GoveeBleInvalidValue(f"{bad}: each range must overlap [{lo}, {hi}]")
        if max(lo, *(e[1] for e in gated)) > min(hi, *(e[2] for e in gated)):
            raise GoveeBleInvalidValue(f"{bad}: no range is valid on every revision")


def _where(p: DeviceProfile) -> str:
    """The profile error messages' prefix, e.g. ``DeviceProfile[H6006]``."""
    return f"DeviceProfile[{', '.join(p.skus)}]"


def _from_spec(skus: tuple[str, ...], facts: Mapping[str, Any]) -> DeviceProfile:
    """The profile of one group of SKUs from its resolved spec facts (:func:`._resolve.profiles`)."""
    kw = dict(facts)
    kw["capabilities"] = frozenset(map(Capability, kw["capabilities"]))
    kw["encryption"] = Encryption.AES_RC4_PSK if kw.pop("aes_fallback") else Encryption.NONE
    kw["zones"] = tuple(Zone(name, power_index=index, segments=segs) for name, index, segs in kw["zones"])
    kw["scene_versions"] = frozenset(kw["scene_versions"])
    return DeviceProfile(skus=skus, **kw)


PROFILES: Final[tuple[DeviceProfile, ...]] = tuple(
    _from_spec(skus, facts)
    for skus, facts in _resolve.profiles(_devices.FAMILIES, _devices.MODE_COLOR_PARSE))
"""The supported SKUs' profiles, generated from spec/devices.yaml (module doc): one per group of SKUs whose
resolved facts are identical, in devices.yaml order. A SKU's evidence, citations and bench notes are its
devices.yaml row's ``doc`` and ``doc-ref``."""


def _index_by_sku(profiles: tuple[DeviceProfile, ...]) -> dict[str, DeviceProfile]:
    """``{SKU upper-cased: profile}`` for the profile table; GoveeBleInvalidValue if a SKU is listed
    twice (case-insensitive), which would otherwise resolve silently to whichever profile came last.
    The table's values were checked when it was built (devices/_resolve.py)."""
    by_sku: dict[str, DeviceProfile] = {}
    for p in profiles:
        for sku in p.skus:
            key = sku.upper()
            if key in by_sku:
                raise GoveeBleInvalidValue(f"SKU {sku!r} appears twice in the device profile table")
            by_sku[key] = p
    return by_sku


_BY_SKU: dict[str, DeviceProfile] = _index_by_sku(PROFILES)


def profile_for(sku: str) -> DeviceProfile | None:
    """The profile for ``sku`` (case-insensitive), or None if unsupported."""
    return _BY_SKU.get(sku.upper())


def supported_skus() -> list[str]:
    """All supported SKUs, sorted."""
    return sorted(_BY_SKU)


def _decode(profile: DeviceProfile, name: str) -> dict[str, int]:
    """The mode_decode params of the parse ``name`` on ``profile``."""
    decode = dict(_DECODES[name])
    if profile.bar_switch:
        decode["c15"] = _BAR_KELVIN
    return decode


def wire_profile(profile: DeviceProfile) -> Any:
    """The spec's ``govee_profile`` for the device facts a reply's decode depends on: the mode decode
    (:attr:`DeviceProfile.mode_color_parse`, and the bars' 0x15 on a :attr:`DeviceProfile.bar_switch`
    device) for the aa 05 report, the status TLV 0x05's (``mode_status``:
    :attr:`DeviceProfile.mode_color_parse_status`, else the same), and the plug's relay mask
    (:attr:`DeviceProfile.relay_on_mask`, the aa 01 heartbeat rule the library's poll follows:
    ``power_state.is_on_heartbeat``). Cached per set of facts (``_spec.profile``)."""
    status = profile.mode_color_parse_status
    mode_status = None if status is None else tuple(sorted(_decode(profile, status).items()))
    return _spec.profile(**_decode(profile, profile.mode_color_parse), mode_status=mode_status,
                         relay_on_mask=profile.relay_on_mask, relay_heartbeat_on_mask=profile.relay_on_mask)
