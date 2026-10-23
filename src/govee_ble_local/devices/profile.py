"""Data-driven device descriptors.

A ``DeviceProfile`` carries everything the capability-driven :class:`~.device.Device`
needs for one SKU family. The supported table, :data:`PROFILES`, is generated from the spec:
``spec/devices.yaml``'s supported ``client_profile`` rows (``_generated/devices.py``, built by
``tools/gen_devices.py``) resolved by :mod:`._resolve`, one profile per group of SKUs whose
resolved facts are identical. A row's SKUs include those the committed catalogue snapshot
(``spec/govee_catalog.json``) routes to it by their goodsType, where no row lists them (no cloud
call at run time). :func:`profile_for` routes a SKU to its row, by the advertised pact
or the goodsType where several rows list it. The wire cipher is resolved at connect from the
advertisement, so ``encryption`` here is only the fallback default; ``requires_secret`` is the
separate account-lock capability.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from types import MappingProxyType
from typing import Any, Final, Literal, NamedTuple, get_args

from .._generated import devices as _devices
from ..exceptions import GoveeBleInvalidValue
from ..models import Capability, Encryption, Zone
from ..transport.connection import HeartbeatTiming
from ..wire import _spec
from ..wire.build import DEFAULT_WHITE_TYPE, ColorScheme, WhiteType
from . import _resolve
from .brightness import percent_to_raw

# scene dialects: "A" = library parseSceneV1 (comType from scene_versions);
# "B_h60a6" = H60A6 DIY (0x58; 0xA4-MTU graffiti / 0xA3 DIY by length gate);
# "B_h6052" = H6052 type-5 professional graffiti (commByte 9).
SceneDialect = Literal["A", "B_h60a6", "B_h6052"]
# "status" (0xAC burst) | "polled" (aa 01, then aa 04 with Capability.BRIGHTNESS and aa 05 with a capability
# the mode report carries: device._MODE_REPORT_CAPS) | "plug" (aa 01 relay bitmask) | "none"
ReadBack = Literal["status", "polled", "plug", "none"]
# How the writes reach the device (DeviceProfile.session): "held" = one link across commands and reads (the
# control screens'); "per_command" = a session of its own per write (the device-list card's).
SessionMode = Literal["held", "per_command"]
ColorReadBack = Literal["", "mechanism_b", "mechanism_a_direct"]
# How a live IC count divides into pieces (DeviceProfile.color_readback_ic_rounding): "ceil" rounds up,
# "floor" is Java's integer division.
IcRounding = Literal["ceil", "floor"]
# How a Kelvin firmware gate compares the device's version (DeviceProfile.kelvin_version_compare): "string" =
# KelvinConfig's compareTo (Config.z2 / Z2 / X2), "numeric" = BleUtils.isHigherVersion (Config.Y2).
KelvinCompare = Literal["string", "numeric"]
# How the colour sub-modes of an ``aa 05`` mode report are decoded (the SKU's Info4Detail mode parse): a
# name of the spec's mode_color_parse table (devices.yaml ``mode_color_parse``, which maps each colour
# sub-mode to a color_decode; :func:`wire_profile`), e.g. "kelvin_15_rgb_kelvin_0d" (H60A6) or
# "rgb_kelvin_tint" (the 0x0d bulbs). The one other name is the library's 1.x default for a profile that
# doesn't choose one, "rgb_kelvin" (0x15 -> [gradual][kelvin u2be]; 0x0d -> [r, g, b, kelvin u2be, tint],
# any non-zero Kelvin = CCT, read unsigned: wire.parse.mode_color unsigned_kelvin), which no app screen uses.
ModeColorParse = str
_DEFAULT_PARSE: Final = "rgb_kelvin"
# The colour page the SKU's control screen shows a reported colour on (DeviceProfile.color_page; devices.yaml
# clientRow color_page): "stock" = the newdetail DelegateColorV2, whose W1 shows a one-colour report that is a
# Kelvin tint in range as its colour temperature; "none" = the screen shows no colour page.
ColorPage = Literal["stock", "none"]

# How a plug's whole-device power is written (its main switch on the app's detail screen):
# "outlet0"     = 33 01 11 / 10, the relay-0 encoding (goodsType 43: H5080 / H5083 / H5085, one outlet;
#                 and 1.3.0's frame for every plug);
# "all_outlets" = 33 01 FF / F0, every outlet (goodsType 50 H5082, 90 H5160 / H5161;
#                 SwitchControllerV2(on, 15));
# "total_switch"= 33 02 01 / 00 (goodsType 307 H5089; Controller4TotalSwitch).
RelayPower = Literal["outlet0", "all_outlets", "total_switch"]

# 1.x's 0xAC status request, the H60A6's (DeviceProfile.status_read): 0x41 seg info, 0x30 zones, 0xA5 colour
# groups.
_CMD = _spec.Common.Command
# The keep-alive reads' command codes (DeviceProfile.heartbeat): aa 01, aa 00, aa 33 and aa 36.
_HEARTBEATS: Final = frozenset(int(_CMD[name]) for name in _resolve.HEARTBEATS)
_H60A6_STATUS_READ: Final = (int(_CMD.cmd_41), int(_CMD.cmd_30), int(_CMD.color_group))
# 1.x's 0xAC reply decode, the H60A6 status screen's (DeviceProfile.status_decode): its devices.yaml row's
# wire facts (h60ax goodsType 303): the zone bits, the power memory and the colour groups (four [brightness,
# r, g, b] records a group, counted by the piece count), the V2 music layout and an empty or short 0x05
# failing the read.
_H60A6_STATUS_DECODE: Final = (
    ("empty_fails", True), ("f_30", "zone_power"), ("f_41", "power_memory"), ("f_a5", "color_group"),
    ("group_rule", "by_pieces"), ("has_brightness", True), ("music", "v2_default"), ("num_per_group", 4))
# status_decode's enum params (govee_profile param -> its govee_common enum); num_per_group is a count and
# has_brightness / empty_fails are flags.
_C = _spec.Common
_STATUS_ENUMS: Final[Mapping[str, Any]] = MappingProxyType({
    "f_30": _C.Form30, "f_36": _C.Form36, "f_40": _C.Form40, "f_41": _C.Form41, "f_a1": _C.FormA1,
    "f_a3": _C.FormA3,
    "f_a5": _C.FormA5, "group_rule": _C.GroupCountRule, "music": _C.MusicDecode})
_STATUS_FLAGS: Final = ("has_brightness", "empty_fails")

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
    :class:`~.device.Device` takes its profile at construction, and another row's only where a later
    advertised pact routes its SKU there and the two differ in nothing but what a pact picks
    (:func:`reroute`). The table of supported SKUs
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
    device reports, and so does the device's own read of it on a :attr:`section_count_read`,
    :attr:`special_info_read`, :attr:`color_writes_live_ic` or :attr:`light_num_per_unit` profile
    (``Device.segment_count``)."""
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
    (build.rgb_as_kelvin; Config.Yt / bu / Zt / fu). Those writers test every pick whatever the screen
    offers, so a profile with RGB on such a scheme carries a range even without COLOR_TEMP (the legacy_02
    H7310 / H7318 / H6165), where it only picks the frame."""
    max_kelvin: int | None = None
    """Highest colour temperature of the SKU's Kelvin range, inclusive (see :attr:`min_kelvin`)."""
    requires_secret: bool = False
    """Account-locked: with a secret set (``Device.set_secret``), every session sends the ``33 b2``
    unlock right after the handshake; with none, the empty check where the screen sends it
    (:attr:`empty_secret_check`), else nothing."""
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
    """Group size per reply: mechanism_b V2 = 3 (the default; V1 = 4); mechanism_a_direct = the
    group size its screen walks (devices.yaml wire ``group.num_per_group``: 4 on every current SKU)."""
    color_readback_segments: int | None = None
    """Static read-back piece count, when it differs from the WRITE/addressable :attr:`segments`
    count (a per-SKU fact from devices.yaml).

    The newdetail screens read getColorPieceSize pieces, while H6047's old detail screen reads
    getBulbStringMaxNum = the 10 write segments, so it leaves this None. None -> falls back to
    :attr:`segments`. Ignored when :attr:`color_readback_live_ic` is True (the live 0x40 read
    supplies the count instead), but as a :attr:`color_readback_ic_walk_only` profile's walk where that
    read gives no count."""
    color_readback_live_ic: bool = False
    """mechanism_a_direct: read the live 0x40 IC count first and derive the colour-piece count
    from it instead of a static :attr:`color_readback_segments`, as the app does for SKUs whose
    segmentation isn't knowable from the static device table (H6641). On a ``readback="status"`` profile
    the count comes from the status read's TLV 0x40 (requested in :attr:`status_read`, read as
    :attr:`status_decode`'s ``f_40`` says) or from :attr:`ic_reads_around_status`, and the pieces size the
    status read's colour groups (else :attr:`color_readback_ic_raw`'s IC count).

    The IC count is clamped to :attr:`color_readback_ic_range` and divided by
    :attr:`color_readback_ic_per_piece`, rounding as :attr:`color_readback_ic_rounding` says (up by
    default), unless :attr:`color_readback_ic_table` or :attr:`color_readback_ic_pair_cap` gives the rule;
    the group count is then ceil(pieces / group size). The reply's third byte is not used."""
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
    None (every light) = the byte equals 1 (``AbsBaseBleModel.z``). Both read as the spec's ``govee_state``
    ``power_state.is_on`` from the advert, with this mask as ``power_rule.advert_on_mask``.

    The plugs' device-list models decode it per goodsType (h5080 ``Model.z``,
    ``Model4Plug2.z``), which is not always the read-back's :attr:`relay_on_mask` (devices.yaml's plug
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
    knows the pact; the library may not). Applied to the read in force, :attr:`status_read` or the
    advertised pact's :attr:`status_read_by_pact` entry."""
    power_resync: bool = field(default=False, kw_only=True)
    """Plug (gt 50 / 90): after every acked power write, re-read ``aa 06`` and ``aa 07 03`` and re-send
    the ``33 b5`` time sync, as the app re-runs its connect read list then (``BleOpV2`` /
    ``BleOpV4.onEventSwitchV2`` -> ``G()``). Best effort: a failure is only logged."""
    kelvin_by_hardware: tuple[tuple[str, int, int], ...] = field(default=(), kw_only=True)
    """Kelvin ranges that replace [:attr:`min_kelvin`, :attr:`max_kelvin`] from a hardware version up:
    ``(min_hard_version, min_kelvin, max_kelvin)`` entries, highest version first, of which the first
    the device's hardware version (``aa 07 03``) is at least applies, compared as
    :attr:`kelvin_version_compare` says, as the app does (a devices.yaml row's ``by_firmware``; strings for
    ``KelvinConfig.Config.z2``: ``versionHard >= "3.08.01"``). Until the hardware version is read, the
    range valid on every revision applies: the base range cut to each entry's (``Device.min_kelvin`` /
    ``max_kelvin`` give the range in force). Needs both :attr:`min_kelvin` and :attr:`max_kelvin`; each
    entry must overlap the base range, and so must the range valid on every revision."""
    kelvin_by_software: tuple[tuple[str, int, int], ...] = field(default=(), kw_only=True)
    """The same from a soft version up: ``(min_soft_version, min_kelvin, max_kelvin)`` entries, applied with
    the device's soft version (``aa 06``) exactly as :attr:`kelvin_by_hardware` is with the hardware
    version (``KelvinConfig.Config.Z2``: bulblightv3 goodsType 348 from ``"1.00.15"``, as strings). The same
    rules; a profile has entries of one kind only."""
    kelvin_version_compare: KelvinCompare = field(default="string", kw_only=True)
    """How :attr:`kelvin_by_hardware` / :attr:`kelvin_by_software` compare the device's version with an
    entry's (a devices.yaml Kelvin ``by_firmware`` item's ``compare``). ``"string"`` (the default):
    the strings, as ``KelvinConfig.Config.z2`` / ``Z2`` / ``X2``'s ``compareTo`` do, so a hardware
    ``10.00.00`` is below ``3.08.01``. ``"numeric"``: each as one int of its digits, the dots removed, 0
    when empty or unparsable (``_resolve.parse_version``), at least the entry's, as ``Config.Y2``'s
    ``BleUtils.isHigherVersion`` does (h60ax goodsType 235, H60A1 / H80A1 from hardware ``"1.04.03"``), so
    ``1.4.3`` (143) is below ``1.04.03`` (10403)."""
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
    color_page: ColorPage | None = field(default=None, kw_only=True)
    """How the control screen shows a colour its mode report carries (a devices.yaml row's ``color_page``,
    set on every row with COLOR_TEMP whose decode reports a colour). ``"stock"``: the screen's newdetail
    ``DelegateCurMode.createView`` builds ``DelegateColorV2``, whose ``W1`` shows a report of one colour that
    is a Kelvin tint in the device's range (``KelvinConfig.i``, inclusive) as the colour temperature
    ``KelvinConfig.c`` of it, else as the colour; so on such a profile, with COLOR_TEMP, a reported RGB that
    is one of the app's tint colours in :func:`~._fold.kelvin_range` reads as that Kelvin
    (``color_temp_kelvin``, no ``rgb_color``), whatever the decode, from an ``aa 05`` reply and a status
    TLV 0x05 alike, as a pick of that colour does (:func:`~._fold.apply_rgb`), while every segment the same
    read brought (the burst's TLV 0xA5 groups, the poll's ``aa a5`` walk) shows that colour, as ``W1`` tests
    the pieces; where they all show another colour, the page shows theirs, read the same way
    (:func:`~._fold._as_shown`). A black colour, the report's with no pieces or a piece's, reads as
    (1, 1, 1), as the page's copy of them holds it (:func:`~._fold._on_page`). A 0x15 report, which carries
    a Kelvin and no colour, and on the h60a1 screen's rows a 0x0d report of a Kelvin above 0, whose tint the
    pieces replace, show those pieces' colour the same way where they all show one, and else read as decoded
    (:func:`~._fold._shown_over_pieces`). ``"none"`` (the screen shows
    no colour page: H8015's illumination-only list) and None (a profile built by hand, or a decode that
    reports no colour) read the report as the decode does. Source-traced, not live-verified."""
    scene_versions_by_firmware: tuple[tuple[str, str, tuple[int, ...]], ...] = field(
        default=(), kw_only=True)
    """Scene sets that replace :attr:`scene_versions` from a firmware version up: ``(min_soft_version,
    min_hard_version, versions)`` entries, ``""`` for no gate on that version, of which each one whose gates
    the device's soft (``aa 06``) and hard (``aa 07 03``) versions meet applies, in order, the last winning,
    after :attr:`scene_versions_by_pact` (a devices.yaml row's ``by_firmware``: the dreamcolorlightv1 rows'
    part-brightness gates, ``Support.getSupportScenesOpSet``). Versions compare as the app's
    ``BleUtil.parseVersion`` does (the digits, dots removed, as one int: ``1.06.00`` is 10600, and ``1.6.0``
    160, below it). A version not yet read meets every gate on it, as the newest firmware would, so until the
    device-info read the last entry's set applies (1.x's assumption), and a single unread version does not
    hold back the other's gates (``Device.update`` reads a missing one again on the next poll); a read
    version that does not parse meets none."""
    scene_versions_by_pact: tuple[tuple[tuple[int, int], tuple[int, ...]], ...] = field(
        default=(), kw_only=True)
    """Scene sets that replace :attr:`scene_versions` on an advertised pact: ``((pact_type, pact_code),
    versions)`` entries (a devices.yaml row's ``by_pact``; no current row has one). Applied before
    :attr:`scene_versions_by_firmware`,
    the order of the spec's resolution (``govee_reference.client_row``); a pact not yet heard applies none."""
    status_read: tuple[int, ...] = field(default=_H60A6_STATUS_READ, kw_only=True)
    """``readback="status"``: the TLV types the 0xAC status read requests, in request order (command codes;
    a devices.yaml row's ``status_read``); empty is the bare read ``ac 03 00``, which requests none and gets
    the TLVs the firmware chooses (the h70dx screen's). The default is the H60A6's ``0x41, 0x30, 0xA5``,
    1.x's request. On an advertised pact with a :attr:`status_read_by_pact` entry that entry's read replaces
    it. The 0xA5 colour groups are left out while :attr:`status_color_groups_pact` rules them out (a list is
    kept as a tuple)."""
    status_decode: tuple[tuple[str, str | int | bool], ...] = field(
        default=_H60A6_STATUS_DECODE, kw_only=True)
    """``readback="status"``: how the 0xAC reply is read, a devices.yaml row's wire facts as ``(govee_profile
    param, value)`` pairs: the forms of the TLVs the status stream reads (``f_30``, ``f_36``, ``f_40``,
    ``f_41``, ``f_a1``, ``f_a3``, ``f_a5``: govee_common enum names), the colour-group form where it reads
    TLV 0xA5 as colour groups (``f_a5`` ``"color_group"`` with ``group_rule``, ``num_per_group`` and
    ``has_brightness`` true), and the TLV 0x05's ``music`` layout and ``empty_fails`` rule. ``f_a5``
    ``"single_record"`` (H703A / H703B) reads it as one ``[brightness, r, g, b]`` record, the whole-device
    colour of a one-piece device, and needs no group facts. A form left out is unknown: that TLV is not read,
    and a 0xA5 TLV read neither way is skipped. The default is the H60A6 status screen's, 1.x's decode (a list
    is kept as a tuple)."""
    light_effect_stop: bool = field(default=False, kw_only=True)
    """A light whose screen writes ``33 25 01`` (``light_effect_stop``) on connect (a devices.yaml row's
    ``connect_writes``; bulblight ``VM4Light$connectBleSuc$1.j`` -> ``VM4Light.i5``): every session sends it
    after the handshake and any unlock, as a plug's time sync is sent, without waiting for its ack. The app
    sends it once per screen connect, once its connect reads succeed. The library connects for every poll
    after the idle disconnect and makes those reads on its polls, so it goes out before each poll's reads; it
    only stops a running ``33 24`` effect (the spec's ``light_effect_stop``). Source-modelled, not
    live-verified."""
    empty_secret_check: bool = field(default=False, kw_only=True)
    """With :attr:`requires_secret` (ignored otherwise): the screen sends its ``33 b2`` check on every
    connect, an empty one while the account holds no secret (a devices.yaml row's
    ``empty_secret_check``: hollowlamp ``H6058NewDetailVm.T4`` and h6071 ``VM4Light``'s connect strategy
    put ``Controller4Secret.Companion.a`` of the account's ``secretCode``, ``""`` when it has none, first
    in the connect read). So with no secret set every session sends the empty check
    (``wire.build.empty_secret_check``: ``33 b2``, 17 zero bytes and the xor) right after the handshake,
    where a profile without it sends no unlock; but one that ``Device.read_secret`` sets up does not, as the
    pairing screen reads the secret with no check (``AbsPairAc4SecretV1.T1:107-113``), and the next other
    operation on it sends the empty check first (one that overlaps the read waits for it; a session another
    operation sets up meanwhile sends the check at setup). A device that refuses it (a non-zero status)
    fails the session with GoveeBleAuthError, as the app's connect read fails on it
    (``AbsControllerNoEvent4Single.onResult`` -> ``AbsComposeControllerWithOpResult.onResult``); a
    missing ack is logged and the session goes on, as for a set secret's check. Source-traced, not
    live-verified."""
    gradual_read_only: bool = field(default=False, kw_only=True)
    """Ignored with :attr:`gradual`. Without it: the screen still reads the ``aa a3`` gradual flag at connect
    (the default ``Compose4InfoBleIot`` read) but offers no gradual control, so
    :meth:`~.device.Device.update` reads it on every poll, as it does for :attr:`gradual`, and discards the
    reply (``state.gradual`` stays None; a devices.yaml row whose ``connect_reads`` lists ``gradual``
    without the capability)."""
    kelvin_by_pact: tuple[tuple[tuple[int, int], int, int], ...] = field(default=(), kw_only=True)
    """Kelvin ranges that replace [:attr:`min_kelvin`, :attr:`max_kelvin`] on an advertised pact:
    ``((pact_type, pact_code), min_kelvin, max_kelvin)`` entries (a devices.yaml row's ``by_pact``
    ``kelvin_range``: goodsType 261 at pact (4, 1)). Until a pact is heard, the range valid on every pact
    applies: the base range cut to each entry's (``Device.min_kelvin`` / ``max_kelvin`` give the range in
    force). Needs both :attr:`min_kelvin` and :attr:`max_kelvin`, and no :attr:`kelvin_by_hardware`; each
    entry must overlap the base range, and so must the range valid on every pact."""
    brightness_by_pact: tuple[tuple[tuple[int, int], tuple[int, int] | None], ...] = field(
        default=(), kw_only=True)
    """Brightness ranges that replace :attr:`brightness_range` on an advertised pact: ``((pact_type,
    pact_code), (min, max))`` entries, None for percent (a devices.yaml row's ``by_pact``
    ``brightness_range``: stringlightv2 goodsType 1 reports percent at pact (2, 1)). ``Device`` writes and
    reads brightness with the range in force, so with entries and no pact heard it writes none (the write
    raises GoveeBleNotSupported) and reads none (``state.brightness`` stays None): the byte's meaning waits
    for the pact. Such a SKU routes without a pact, so a ``Device`` set up from a pact-less record
    (``create_device`` with no advertisement, or one without a Govee pact block: the stringlightv2 SKUs
    H6121 / H6141 / H6142 / H6154 / H6195, H6170 / H6196 / H613G) refuses every brightness command until
    ``Device.ingest_advertisement`` hears the pact, ``turn_on(brightness=...)`` included, which then sends
    nothing, not even the power-on; power, colour and colour temperature work meanwhile."""
    ic_num_read_only: bool = field(default=False, kw_only=True)
    """Ignored with :attr:`color_readback_live_ic` or :attr:`color_readback_ic_sections`. Without them: the
    screen reads the ``aa 40`` IC count at
    connect, but it does not size the pieces (``getColorPieceSize`` whatever the IC: h6609 goodsType 181), so
    :meth:`~.device.Device.update` reads it on every poll, before the colour read-back as the screen reads
    it before the ``aa a5`` groups, and discards the reply; the read-back walks the static pieces, or with
    :attr:`special_info_read` the segment count the device reports (goodsType 180; a devices.yaml row with
    ``ic_pieces: fixed``)."""
    scene_power_on: bool = field(default=False, kw_only=True)
    """A scene write is led by the power-on switch (``33 01 01``) unless the device is known to be on: the
    screen prepends it to a scene apply while its stored switch state is off, and an unknown state reads as
    off (``ApplyOffNeedOpenConfig``: H6009 / H6010; ``ApplyOffNeedOpenHelper.b`` / ``.d``;
    ``DeviceSwitchConfig.getSwitch``; a devices.yaml ``by_model`` ``scene_power_on``). ``Device.set_scene``
    and ``set_scene_by_name`` send it first, ahead of any upload, while ``state.is_on`` is not True."""
    heartbeat: int | None = field(default=None, kw_only=True)
    """The keep-alive read the SKU's control screen sends while the link is up, as its command code, one of
    the four keep-alive command codes (``0x01`` aa 01, ``0x00`` aa 00, ``0x33`` aa 33, ``0x36`` aa 36: a
    devices.yaml row's ``heartbeat``, the screen's ``ConnectHeartbeat`` type or ``IHeart``), or None for none
    (the default, so a profile built by hand sends none, as in 1.5.0). The firmware of several SKUs drops a
    link that has had no write for 10-11 s (spec devices.yaml timing.heartbeat). A :class:`~.device.Device`
    sends ``aa <heartbeat>`` every ``const.HEARTBEAT_INTERVAL`` (2 s) while its link is up, never between the
    reads of one poll or the frames of one scene write, and never keeping a link up longer than its idle
    delay would. To send none on any SKU whose row has one, clear this one field:
    ``dataclasses.replace(profile, heartbeat=None)``; :attr:`heartbeat_timing` (``"after_quiet"`` on H5080 /
    H5083 / H5085) is then ignored and needs no reset."""
    heartbeat_timing: HeartbeatTiming = field(default="fixed_rate", kw_only=True)
    """When :attr:`heartbeat` goes out: ``"fixed_rate"``, every interval from the session's start (the light
    screens' and legacy plug screens' ``AbsBle.HeartRunnable``), or ``"after_quiet"``, an interval after the
    last operation that sent a frame, each one restarting the wait (goodsType 43's ctlchannel
    ``HeartHandler.e``, which restarts it on every send: H5080, H5083 and H5085). Checked either way, and
    ignored without a :attr:`heartbeat`: then no beat goes out at all, on either timing."""
    light_nonzero: bool = field(default=False, kw_only=True)
    """``readback="polled"``: the ``aa 01`` reply reads on for any non-zero byte (the spec's
    ``power_rule.light_nonzero``). It is the rule the library's poll follows: as with :attr:`relay_on_mask`,
    the row's heartbeat rule where it has one (its wire ``power.light_heartbeat_nonzero``: H1168's aa 01
    heartbeat, ``HeartControllerWithBattery.parseValidBytes``, which replaces the connect read's ``== 1``
    within 2 s), else its connect read's (``power.light_nonzero``: the legacy
    ``SwitchController.parseValidBytes`` on the H1162 / H1163 / H1167 standalone screen, the H70BX connect
    read ``AfterConnectSuccessReadDeviceInfo.w``). False, the default: on only for a byte of 1
    (``ControllerSwitch.Companion.c``), as every other light screen reads it. Ignored on a plug, whose byte is
    a relay mask (:attr:`relay_on_mask`). It covers the poll only: on such a row an advertisement's switch
    byte still reads on only for 1 (:meth:`~.device.Device.advertised_on`), as the app's device list decodes
    it for every light (``AbsBaseBleModel.z:393-397`` over ``BleUtil.parseBleBroadOnOff:719-744``; the h1162
    ``Model.z:149-157`` calls it, and the H1168 and H70BX models inherit it), so a byte of 2 reads off in
    the advertisement and on in the poll."""
    white_type: WhiteType = field(default=DEFAULT_WHITE_TYPE, kw_only=True)
    """The slot of the ``h60a6`` colour-temperature frame (``33 05 15 01 <slot> <K> <tint> <mask>``), the
    white type the SKU's colour page hands ``Bytes.r``: a devices.yaml row's ``white_type``, the app's
    ColorTemperatureType (:data:`..wire.build.WhiteType`). ``"rgb_white"`` (the default, every SKU before
    1.8.0) leads ``ff ff ff``; ``"rgb_black"`` ``00 00 00``; ``"rgb_self_tem_color"`` (H61E5 / H61E6)
    ``00 00 00`` over every segment and, over some, the tint as the colour with Kelvin and tint zero. It
    applies to every colour-temperature frame the profile sends: :meth:`~.device.Device.set_color_temp`,
    ``turn_on(kelvin=)``, an RGB pick that is a Kelvin tint colour (whole or on some segments), and the
    segment and zone Kelvin where :attr:`whole_device_kelvin` doesn't refuse them (it does on H61E5 / H61E6,
    so there only a tint-colour pick on some segments takes the partial form). Only an ``h60a6`` profile may
    set another than the default."""
    whole_device_kelvin: bool = field(default=False, kw_only=True)
    """The SKU's screen writes colour temperature over the whole device only, never over part of it (a
    devices.yaml row's ``color_temp_control``: ``whole_device``, whose colour page's Kelvin pick always sets
    every piece, h61d3 ``ColorMode.canClickColorTem`` on H61E5 / H61E6; or ``illumination``, see
    :attr:`kelvin_with_brightness`). :meth:`~.device.Device.set_segment_color_temp` and
    :meth:`~.device.Device.set_zone_color_temp` then raise GoveeBleNotSupported: a Kelvin on some segments
    is a frame the app never sends, whatever :attr:`segment_color_temp` says. A table profile with it and
    :attr:`~..models.Capability.SEGMENTS` also has :attr:`segment_color_temp` False (the row says so, and the
    resolver refuses true beside a ``color_temp_control``), so a caller that offers colour temperature per
    segment checks :attr:`segment_color_temp` alone, and no table profile with it has :attr:`zones`. False
    (the default, every SKU before 1.8.0): the masked Kelvin is the app's."""
    kelvin_with_brightness: bool = field(default=False, kw_only=True)
    """The SKU's screen sets colour temperature on its illumination tab alone (a devices.yaml row's
    ``color_temp_control: illumination``), which writes the Kelvin frame and then ``33 04`` <the current
    brightness> as one write (``View4IlluminationMode.I0``: the Kelvin controller, then
    ``BrightnessConfig.b`` of ``illuminationCur()[0]``, in one ``Compose4DefWrite``). So
    :meth:`~.device.Device.set_color_temp` sends both, each frame acked in order, re-sending the brightness
    ``state`` holds (read first, with the profile's own read, when unknown), and
    :meth:`~.device.Device.turn_on` with a ``kelvin`` sends the Kelvin frame before ``33 04`` (the given
    brightness, else the held one). The tab writes every piece, so it needs :attr:`whole_device_kelvin`.
    False (the default, every SKU before 1.8.0): the Kelvin frame alone."""
    mode_decode: tuple[tuple[str, str | bool], ...] = field(default=(), kw_only=True)
    """The ``aa 05`` mode report's music layout and empty-value rule, where the screen's parse sets them: a
    devices.yaml polled row's wire ``mode`` (h60a1 ``Info4Detail.Q`` on H60A1 / H80A1: ``music`` v2_default,
    ``empty_fails``), as ``(govee_profile mode_decode param, value)`` pairs: ``music`` a govee_common
    music_decode member name, ``empty_fails`` a flag (each once; a list is kept as a tuple). Empty (the
    default, every SKU before 1.8.0) is the spec's default decode: the music sub-mode 0x13 is not decoded. The
    colour sub-modes are :attr:`mode_color_parse`'s, and ``empty_fails`` governs only a status TLV 0x05, so
    of the reply only a music report decodes differently; a music report sets no colour or scene in
    ``state``. A status profile's TLV 0x05 takes :attr:`status_decode`'s."""
    color_readback_ic_sections: bool = field(default=False, kw_only=True)
    """mechanism_a_direct only, and ignored with :attr:`color_readback_live_ic`: read the ``aa 40`` IC
    reply first and walk as many pieces as its two section bytes sum to, as Java bytes (the spec's
    ``ic_count.section_sum``), or the static pieces (:attr:`color_readback_segments`, else the segment
    count) when the sum is 0, in Java's ``sum / group + (sum % group != 0)`` groups, none below 1 (the
    rgbiclight screen, ``VM4Light.N5``: goodsTypes 141 and 204). No walk when the read fails or its
    reader would throw (an IC field of 0x1000 or more). Writes keep the static :attr:`segments`; a
    devices.yaml row with ``ic_pieces: sections``."""
    detail_power_read: bool = field(default=False, kw_only=True)
    """``readback="polled"`` only: the poll reads the power from the ``aa 34`` device-detail read in place of
    ``aa 01``, after the ``aa 04`` brightness read, as the H6057 screen's connect list orders them
    (``Compose4InfoBleIotH6057``), and takes its light-open flag (byte 0 bit 7, the spec's
    ``device_detail.light_open``: ``Controllers.d``, ``H6057Info4Detail.H``): the screen's on / off and the
    light switch's state (:attr:`light_switch_power`). So white noise playing with the light closed reads
    off, as the screen shows it. A devices.yaml row with ``power_read: aa34_detail`` (H6057)."""
    light_switch_power: bool = field(default=False, kw_only=True)
    """The whole-device power is the H6057 header's light switch: :meth:`~.device.Device.set_power`,
    ``turn_on`` and ``turn_off`` write ``33 30 <1 / 0>`` (the spec's ``cmd_30_write`` under form_30
    ``light_switch``: ``H6057DetailVM.h5``, ``BleProtocol.command_light_switch``), the switch whose state
    :attr:`detail_power_read` reads back, in place of the ``33 01`` main switch. It switches the light alone,
    so ``turn_off`` leaves the white-noise speaker playing, as the screen's light switch does (the main
    switch, which would stop it too, is never sent). That read is its only read-back: the advertised switch
    byte is the device-list card's, whose toggle is the main switch, so no advertisement's on/off is taken
    for it (:meth:`~.device.Device.advertised_on` is None). A devices.yaml row's ``power_write:
    light_switch`` (h6057 104, the user's decision of 2026-10-22). The whole-device power frame only: no zone
    or relay frame goes with it.
    False (the default): the ``33 01`` main switch (or a zoned fixture's ``33 30`` per zone, or a plug's
    relay frame)."""
    session: SessionMode = field(default="held", kw_only=True)
    """How the writes reach the device: ``"held"`` (the default: the control screens' rows, and a profile
    built by hand) keeps one link across commands and reads, acks each write and runs the :attr:`heartbeat`;
    ``"per_command"`` is the app's device-list card (a devices.yaml ``session: per_command`` row, for a SKU
    whose control screen has no BLE control: H70B2, H6630, H8630, H6631). On it each write is a session of its
    own (``GoveeConnection.send_once``): connect, write the one frame, read the reply for at most the
    write-ack timeout (a refusal raises GoveeBleCommandRejected), disconnect, each session at least
    ``const.PER_COMMAND_SESSION_GAP`` after the last one's release (``CardCommManagerProxy.s``). So
    ``Device.update`` makes no connection, ``Device.connect`` opens nothing, a read (``Device.read_secret``)
    raises GoveeBleNotSupported, ``turn_on`` sends power and brightness as two sessions, power first, and a
    brightness while the device is not known to be on goes after a power-on session (the card's switch, then
    its slider), with no other write between the two. :attr:`heartbeat` (None on those rows),
    ``idle_disconnect`` and :attr:`heartbeat_timing` do nothing with it: no link outlives its write. It takes
    :attr:`capabilities` POWER and BRIGHTNESS only and no :attr:`zones` (GoveeBleInvalidValue otherwise): the
    card sends nothing else, and a scene upload or a bar's read-modify-write would hold a link past its
    session."""
    segment_brightness: bool = field(default=True, kw_only=True)
    """With :attr:`~..models.Capability.SEGMENTS`: the screen's colour page offers segment brightness, the
    ``33 05 15 02`` slider (a devices.yaml row's ``color_functions`` with ``segment_brightness``, or none
    traced: the 1.x rule). False where the page hides it (h61d3 202, H61D2-H61D6: ``Config.I[202][3]`` false,
    so ``DelegateColorV2Part.Z0`` leaves the slider unwired) or the colour scheme has no such frame:
    :meth:`~.device.Device.set_segment_brightness` then raises GoveeBleNotSupported before any I/O, and
    :attr:`~.device.Device.supports_segment_brightness` is False. Segment colour is unaffected. True: the
    default, every SKU before 1.8.0, and a profile built by hand."""
    status_read_by_pact: tuple[tuple[tuple[int, int], tuple[int, ...]], ...] = field(default=(), kw_only=True)
    """``readback="status"``: the 0xAC status reads that replace :attr:`status_read` on an advertised pact,
    ``((pact_type, pact_code), types)`` entries (command codes, in request order; a devices.yaml row's
    ``by_pact`` ``status_read`` other than its own read without the colour groups): the H7070 screen asks
    for ``41 0c`` at pact (1, 1) and ``41 0c 42`` at (1, 2) / (2, 1) (``VM4LightH7070.T4``,
    ``Support.supportEffectChange``). A pact with no entry, or none heard yet, takes :attr:`status_read`
    (the spec's ``client_row``). Each pact once; each read distinct codes (lists are kept as tuples)."""
    color_readback_ic_rounding: IcRounding = field(default="ceil", kw_only=True)
    """How :attr:`color_readback_live_ic` divides the clamped IC count by :attr:`color_readback_ic_per_piece`:
    ``"ceil"`` rounds up (the default, every profile before 1.9.0: ``H61D3Support.e``), ``"floor"`` is Java's
    integer division (h705a ``Support.b`` for goodsTypes 125 / 240: IC / 4). A devices.yaml ``ic_pieces``
    ``rounding``."""
    color_readback_ic_table: tuple[tuple[int, int], ...] = field(default=(), kw_only=True)
    """:attr:`color_readback_live_ic` by the IC count's value, in place of the clamp and divisor: ``(IC,
    pieces)`` entries, each IC once, and :attr:`color_readback_ic_otherwise` at any other count (h6609
    ``Support.getColorPieceSize(173, IC)``: 18 at 36, else 14; a devices.yaml ``ic_pieces`` ``table``). Empty
    (the default): the clamp and divisor (a list is kept as a tuple)."""
    color_readback_ic_pair_cap: int | None = field(default=None, kw_only=True)
    """:attr:`color_readback_live_ic` from two strips, in place of the clamp and divisor: where the aa 40
    reply's IC-pair reader returns a second pair (``ControllerIcNum.e``), its first IC is not 0 (the list
    ``VM4Light.h5`` keeps) and its second is above 0, ``min(IC 1, cap) + min(IC 2, cap)`` pieces, else
    :attr:`color_readback_ic_otherwise`; no count where that reader throws (``H61D3Support.e`` and
    ``ColorModeH1A45`` for goodsType 375: cap 15, else 30; a devices.yaml ``ic_pieces`` ``pair_max``). None
    (the default): the reply's one IC count."""
    color_readback_ic_otherwise: int | None = field(default=None, kw_only=True)
    """The pieces at an IC count :attr:`color_readback_ic_table` doesn't list, and on a reply without the
    second strip :attr:`color_readback_ic_pair_cap` needs: 1 or more, and set with either of them."""
    color_readback_ic_pair_reader: bool = field(default=False, kw_only=True)
    """The screen reads the aa 40 reply with the IC-pair reader (``ControllerIcNum.e``; a devices.yaml row's
    wire ``f_40: ic_pair``), which throws where either IC field is 0x1000 or more or the reply is under 3
    bytes (:attr:`parse.IcCount.ic_pair` None, the spec's ``app_pair_fails``): no count there, whichever rule
    gives the pieces (the h705a screens, H1A45). :attr:`color_readback_ic_pair_cap` needs it. False (the
    default): the reader of bytes 0..1 alone (h61d3 ``ReadIcController``)."""
    color_readback_ic_skip_zero: bool = field(default=False, kw_only=True)
    """An IC count of 0 is no count, as a failed read: the screen's IC check skips it and keeps the count it
    has (h705a ``VM4H608A.S4``, goodsType 184), so :attr:`~.device.Device.segment_count` keeps its count and
    the walk is skipped (a devices.yaml ``ic_pieces`` ``skip_zero``). Not with
    :attr:`color_readback_ic_pair_cap`, whose rule says what a first IC of 0 gives. False (the default): 0 is
    a count."""
    color_writes_live_ic: bool = field(default=False, kw_only=True)
    """With :attr:`color_readback_live_ic`: the live pieces size the writes too, as the screen's strip takes
    them from the stored IC, which each aa 40 read updates (``ColorPieceConfig.c``; the h705a screens resize a
    built strip when a read changes it, ``ColorMode.Y`` / ``ColorModeV1.K``, while the h61d3 and H7067 strips
    keep the count they were built with until a calibration or settings event, a first-session difference
    their rows' docs name): :attr:`~.device.Device.segment_count` becomes each poll's count (at most
    ``build.piece_cap`` of :attr:`color_scheme`: 112 on the 0x15 bodies, more on ``h3010``'s uncapped 0xA3
    mask), so it is the whole-device mask and the segment index bound, and a write that
    carries a mask before any count is known reads ``aa 40`` first (on a status profile its IC read: the
    status read whose TLV 0x40 carries it, or the first of :attr:`ic_reads_around_status`) and raises
    GoveeBleError, sending nothing, when the device gives no usable count, as a ``section_count_read`` profile
    does. :attr:`segments` is then only the count before the first read. Every IC-sized devices.yaml row
    (``ic_pieces`` object form) sets it but a ``readback_only`` rule's (:attr:`color_readback_ic_walk_only`);
    False (the default): the writes keep :attr:`segments`."""
    special_info_read: bool = field(default=False, kw_only=True)
    """``readback="polled"`` only, and not with :attr:`section_count_read`: the device reports its own
    segment count in the 0xAC special-info read (``ac a1``, its reply's segment IC record
    ``[segments][IC per segment ...]``: the spec's ``segment_ic_info``; a devices.yaml row whose
    ``connect_reads`` lists ``special_info``), which the goodsType 180 screen (H706A-C, H806A, H806C) makes
    on every connect and stores (``VM4Light.x5`` -> ``Info4Detail.z0`` -> ``SegmentIcDataConfig``). That
    count is the colour page's piece count, so the width of every mask (``ColorModeV2.d0``), and the pieces
    the ``aa a5`` walk reads (``Support.b(180)``, ``VM4Light.F5``). As for :attr:`section_count_read`,
    :meth:`~.device.Device.update` reads it on each poll, before ``aa 05`` and the walk as the app does, a
    mask write reads it first while it is unknown and sends nothing without it, and :attr:`segments` is only
    the profile's upper bound. Source-modelled, not live-verified."""
    kelvin_from_pieces: bool = field(default=False, kw_only=True)
    """``color_readback="mechanism_a_direct"`` only: the colour-temperature state comes from the read-back's
    pieces, not the ``aa 05`` report's Kelvin (a devices.yaml row's ``kelvin_readback: pieces``; goodsType
    180: ``VM4Light.g5`` -> ``ColorUtils.r`` of ``SubMode4Color.d``, then ``SubMode4Color.s``). Once a poll's
    ``aa a5`` walk has read every piece, and the poll's ``aa 05`` report showed the colour mode,
    ``state.color_temp_kelvin`` is the Kelvin of their one colour when they all show the same colour and it is
    one of the app's tint colours (its whole 1000-10000 K table, which may lie outside :attr:`min_kelvin` /
    :attr:`max_kelvin`), else None (:func:`._fold.apply_piece_kelvin`). In another mode (a scene, music, DIY)
    that Kelvin, when there is one, goes to ``state.last_color_temp_kelvin`` only, as g5 syncs it whatever the
    mode. A poll whose walk lost a group, or whose ``aa 05`` read went unanswered, leaves both as they were.
    Source-modelled, not live-verified."""
    heartbeat_power: bool = field(default=False, kw_only=True)
    """Follow the screen's ``aa 01`` keep-alive power: ``readback="status"`` with the ``aa 01`` keep-alive
    (:attr:`heartbeat` ``0x01``) and no :attr:`zones` only (the reply is the one switch), and ignored
    otherwise. The screen reads the 0xAC status once per connect and then takes the power from each keep-alive
    reply (``ShareVM.N3`` -> ``J3``, on at byte 2 == 1; ``BleComm.G`` hands it the aa 01 replies of a type-1
    heartbeat). With it, :meth:`~.device.Device.update` makes the status read once per link, and a poll on a
    link it brought state back on reads nothing more, while each keep-alive reply that arrives with no other
    frame sent since its beat sets ``state.is_on`` (a reply that may predate a write or a read is dropped) as
    a power read: it stamps :attr:`~.device.Device.power_read_at`, unless the stamp was dropped after the beat
    went out (the read the reply answers was issued then), and reaches the state callbacks. So the state a
    poll returns on a fresh link is its status read's, whose TLV 0x01 stands in for the reply, and a poll on a
    warm link reads nothing back. Where a reply changes ``state.is_on``, the screen's re-read follows on a
    profile with :attr:`heartbeat_reread_pieces`; elsewhere the mode and colour on a link that stays up are
    the status read's and the library's own writes, as the screen's (it sets the switch alone). Set by a
    devices.yaml status row's ``heartbeat_power: true``: straightfloorlamp 69, h6069 284, rgbiclight 351 /
    352, h7086 293, h7020 296, h7076 313 and h3700 383 (the user's decision 17), h7020 316 / 318 / 337 / 391 /
    411, on 296's screen (the user's decision 26), and h61d3 263 (behind ``allow_modelled``). False (the
    default: every other profile, among them the status rows on the aa 00 keep-alive, H60A6's, whose reply
    carries no switch): the status read each poll, the reply left unread."""
    heartbeat_reread_pieces: int = field(default=0, kw_only=True)
    """With :attr:`heartbeat_power`: the re-read the screen makes when a keep-alive reply changes the switch
    (``J3`` -> ``Compose4Off2On``), over this many pieces; 0 (the default): none, the screen sets the switch
    alone (h7086 293, h7020 296, h7076 313, h3700 383: ``J3`` with no compose). On such a change the
    :class:`~.device.Device` re-reads at once, on the link the reply came on and never another (no connect:
    ``GoveeConnection.query``'s ``on_link``), holding the heartbeat as a compose in flight holds the app's
    (``AbsBle.I``): the ``aa 05`` mode read, folded as a poll's, then, once it is answered, the screen's ``aa
    a5`` colour groups 1 .. ceil(pieces / :attr:`color_readback_per_batch`), four-byte records
    (``Controller4ColorInfoByGroup.generateReadController``; ``Compose4Off2On.w`` adds them), ending at the
    first that goes unanswered as the compose does (``AbsComposeControllerWithOpResult.onOvertime``). Those
    are the device's segment colours where the profile has SEGMENTS over exactly these pieces
    (straightfloorlamp 69: 7, rgbiclight 351 / 352: 15), and are read and not applied elsewhere (h6069 284's
    140, h61d3 263's one piece: their state's colour is the mode report's). h6069 284's 140 is the screen's
    count when no shape layout is stored, and the layout's size otherwise (``CubeVM4Light.l5``); the library
    has none, so it walks 140 (an open question for the user, devices.yaml's row doc). The compose's timer,
    sleep and wake reads are left out, as the library's connect reads leave the setting reads out. A change
    that comes while a re-read runs sets the power and sends no re-read of its own, as the app skips a
    ``Compose4Off2On`` that comes while a compose is in flight (``ComposeYieldGuard.skipIncomingIfBusy``). A
    devices.yaml status row's ``heartbeat_reread: { pieces }``: 7 on straightfloorlamp 69, 140 on h6069 284,
    15 on rgbiclight 351 / 352 and 1 on h61d3 263."""
    ic_reads_around_status: tuple[bool, bool] = field(default=(False, False), kw_only=True)
    """``readback="status"`` with :attr:`color_readback_live_ic`: the poll's ``aa 40`` reads ``(before,
    after)`` the 0xAC status read, which give the live IC in place of a TLV 0x40 (a devices.yaml status row's
    ``connect_reads`` / ``post_status_reads`` ``ic_num``: h7020
    ``NewDetailVM$afterConnectedSingleReadDeviceInfo4702ABC$1``, whose connect read sends aa 40 ahead of the
    0xAC read, as a mandatory step of the same read, and aa 40 again once it succeeds, ``NewDetailVM.s5``). An
    unanswered or unusable first read ends the poll's status read, as it fails the app's. ``(False, False)``
    (the default): a live-IC status profile takes the IC from the status read's TLV 0x40."""
    color_readback_ic_raw: bool = field(default=False, kw_only=True)
    """``readback="status"`` with :attr:`color_readback_live_ic`: the status read's TLV 0xA5 colour groups are
    placed over the IC count itself, while the IC rule's pieces size the writes (a devices.yaml status row's
    ``readback_pieces: ic_count``: ``Support.getColorPieceSize(gt, IC)`` = IC on the h7050 and h7020 status
    screens, where the colour page writes ``ColorPieceConfig.b``'s pieces). False (the default): the rule's
    pieces size both."""
    light_num_per_unit: int = field(default=0, kw_only=True)
    """``readback="polled"``: the screen reads the ``aa 0f`` string count at connect and stores the count
    times this as its IC, the pieces its colour strip takes at each pick (h7020 ``NewDetailVM.u5``: the bulbs
    per string, ``Support.getBulbStringSetting`` [1], or 1 on goodsType 149, where the count is the pieces; a
    devices.yaml row's ``light_num_pieces``). :meth:`~.device.Device.update` reads it on every poll, after
    ``aa 04`` and before ``aa 05``, and the pieces of a count of 0 or more become
    :attr:`~.device.Device.segment_count`, at most ``build.MAX_MASK_SEGMENTS`` (the whole-device mask and
    the segment index bound), while the ``aa a5`` walk takes them all (``NewDetailVM.D5``); a negative count
    (a Java byte) is none. A write that carries a mask before any count is known reads it first and raises
    GoveeBleError, sending nothing, without one, as a :attr:`section_count_read` profile does, and the walk
    is skipped until a count is known. :attr:`segments` is then only the count before the first read. 0
    (the default): no ``aa 0f`` read."""
    color_readback_ic_walk_only: bool = field(default=False, kw_only=True)
    """With :attr:`color_readback_live_ic` and without :attr:`color_writes_live_ic`: the IC rule sizes the
    walk alone, the screen's strip keeping the static pieces it was built with (h6609 goodsType 260: the
    ``H70C456789ColorMode`` strip is sized once by ``ColorPieceConfig.b``, its resize ``T()`` has no caller,
    while ``DetailViewModel.T5`` walks ``H70c4Support.b(SKU, IC)`` pieces; a devices.yaml ``ic_pieces``
    table with ``readback_only``). So a read that gives no count walks the static pieces
    (:attr:`color_readback_segments`, else the segment count), as the screen walks the IC it stored, where a
    live-IC profile otherwise skips the walk; and a walk of other than
    :attr:`~.device.Device.segment_count` pieces is not applied, as the strip applies a colour or brightness
    read-back only of its own length (``H70C456789ColorMode.updateColor`` / ``updateBrightness``), so it
    shows none of it. False (the default, every profile before 1.9.0): the live-IC walk is skipped without a
    count and applied whole."""
    status_segment_count: bool = field(default=False, kw_only=True)
    """``readback="status"``: the 0xAC status read's TLV 0xA1 (:attr:`status_decode` ``f_a1``
    ``segment_info``) reports the device's segment count, its first byte, which the screen takes as its
    colour page's piece count and so as every colour, colour-temperature and segment-brightness mask (the
    H707A / H707B / H707C strings, cut by the user: ``H707AInfo4Detail.G0`` -> ``Info4BleIotDevice.P0`` /
    ``SegmentIcDataCache``, ``H707AColorMode.X``). :attr:`~.device.Device.segment_count` becomes each read's
    count, at most ``build.MAX_MASK_SEGMENTS`` (a count of 0 leaves it, as it leaves the page), and a write
    that carries a mask before any count is known makes the status read first and raises GoveeBleError,
    sending nothing, when the device reports none, as a :attr:`section_count_read` profile does. A reply
    without TLV 0xA1 takes TLV 0x40's segment byte (:attr:`status_decode` ``f_40`` ``ic_pair``) where its IC
    differs from the one the screen holds, as the reply's lambda does (``n6``), the screen's IC being each
    read's 0x40 IC and 0xA1 IC sum, and before the first read none. :attr:`segments` is then only the count
    before the first read (a devices.yaml status row that requests ``cmd_a1``). It needs ``0xA1`` in
    :attr:`status_read` and ``f_a1`` ``segment_info`` in :attr:`status_decode`. False (the default): the
    status read sizes nothing."""
    segment_color_temp: bool = field(default=True, kw_only=True)
    """Whether :meth:`~.device.Device.set_segment_color_temp` may send a colour temperature to a piece
    selection. The control screen's All tab sends its Kelvin over every piece (``DelegateColorV2.G0``), and
    only its Part tab's Kelvin item over the selected ones, an item ``DelegateColorV2Part.Z0`` adds only
    where ``LightColorCapabilityCenter.C2`` holds: ``SupportPartColorTemConfig.b``'s goodsTypes (a
    devices.yaml row's ``segment_color_temp``), but false on the four SKUs whose Part-tab Kelvin item widens
    every pick to whole zones, so it sends none to part of one: H60A6 and H6047 (the user's decision 24;
    ``ColorModeH60A6.canClickColorTem``, ``ColorMode10Segment.canClickColorTem``), and H1232 and H1252
    (held, behind ``allow_modelled``; decision 25, D11; ``ColorModeH1232.canClickColorTem``,
    ``ColorModeH1252.canClickColorTem``). False: ``set_segment_color_temp`` raises GoveeBleNotSupported
    before any I/O, and the device's colour temperature is :meth:`~.device.Device.set_color_temp`'s. It does
    not govern :meth:`~.device.Device.set_zone_color_temp`, whose frame masks a zone's pieces: that needs
    SEGMENTS, COLOR_TEMP and a zone with segments only (and no :attr:`whole_device_kelvin`, which no zoned
    table profile has). It means nothing without SEGMENTS and COLOR_TEMP in :attr:`capabilities`. True by
    default, so a profile built by hand keeps 1.x's behaviour; the generated table sets it from the row (the
    default on a row without both capabilities). An RGB pick that is a Kelvin tint colour still goes to the
    selected pieces as the Kelvin frame (``Config.Yt``), as the app sends it, whatever this says."""
    segment_brightness_groups: tuple[tuple[int, ...], ...] = field(default=(), kw_only=True)
    """Disjoint groups of segment indices that :meth:`~.device.Device.set_segment_brightness` widens a
    selection to: every segment of each group holding a selected one goes into the ``33 05 15 02`` mask and
    the state fold, as the screen's strip widens it before the write (a devices.yaml row's
    ``segment_brightness_groups``: ``ColorMode14Rect4ReverseSelect.canClickBrightness`` and
    ``H605CColorMode.canClickBrightness``, {0-2} / {3-6} / {7-9} / {10-13}). Empty, the default: the
    selection as it is."""
    device_info_reads: bool = field(default=True, kw_only=True)
    """Whether :meth:`~.device.Device.update` makes the ``aa 06`` / ``aa 07 03`` / ``aa 07 02`` device-info
    reads (``_readback.read_device_info``), and later the re-read of a version a firmware-gated fact still
    waits for. True, the default, as in 1.6.1. False on a ``readback="status"`` row whose screen sends none of
    them (a devices.yaml status row's ``device_info_reads: none``: every status row but the H60A6's and the
    H6076's, whose reads are firmware-sourced): the versions and device id then come from the status reply's
    TLV 0x07 alone, on every poll, as on the screen (``Compose4BaseInfoSingleRead.w``)."""

    def __post_init__(self) -> None:
        """Validate each field: GoveeBleInvalidValue (a ValueError) on a value no Device can use.

        Fail at construction, not silently at poll time, on a typo. Values that are each valid but
        that the builders can't serve together (a capability the colour scheme has no frame for, a
        live IC read without its derivation) are accepted here, as in 1.3.0; the setter or read concerned
        then raises GoveeBleNotSupported or does without. A ``session="per_command"`` profile is the
        exception: it takes POWER and BRIGHTNESS only and no zones (:attr:`session`), as a scene upload or a
        bar's read-modify-write would hold a link past its session. The generated table's values are checked,
        those combinations included, when it is built (devices/_resolve.py), so none reaches this check."""
        if isinstance(self.skus, list):
            # 1.3.0 accepted a list; keep it as the declared tuple, so the immutable profile stays hashable.
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
            ("heartbeat_timing", HeartbeatTiming),
            ("white_type", WhiteType),
            ("session", SessionMode),
            ("color_readback_ic_rounding", IcRounding),
            ("kelvin_version_compare", KelvinCompare),
        ):
            value = getattr(self, name)
            if value not in get_args(allowed):
                raise GoveeBleInvalidValue(f"{where}: {name}={value!r} not in {get_args(allowed)}")
        if self.white_type != DEFAULT_WHITE_TYPE and self.color_scheme != "h60a6":
            raise GoveeBleInvalidValue(f"{where}: white_type={self.white_type!r} on color_scheme="
                                       f"{self.color_scheme!r} (only the h60a6 frame has the white slot)")
        if not isinstance(self.segment_brightness, bool):
            raise GoveeBleInvalidValue(f"{where}: segment_brightness={self.segment_brightness!r} (a bool)")
        if self.kelvin_with_brightness and not self.whole_device_kelvin:
            raise GoveeBleInvalidValue(f"{where}: kelvin_with_brightness without whole_device_kelvin (the "
                                       "illumination tab writes every piece)")
        if self.color_page is not None and self.color_page not in get_args(ColorPage):
            raise GoveeBleInvalidValue(f"{where}: color_page={self.color_page!r} not in {get_args(ColorPage)}"
                                       " or None")
        for name in ("mode_color_parse", "mode_color_parse_status"):
            value = getattr(self, name)
            if value not in _DECODES and not (value is None and name == "mode_color_parse_status"):
                raise GoveeBleInvalidValue(f"{where}: {name}={value!r} is not a mode_color_parse name "
                                           f"(spec/devices.yaml mode_color_parse, or {_DEFAULT_PARSE!r})")
        if self.brightness_range is not None and not _raw_range(self.brightness_range):
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
        if isinstance(self.status_read, list):
            object.__setattr__(self, "status_read", tuple(self.status_read))
        if not _status_types(self.status_read):
            raise GoveeBleInvalidValue(f"{where}: status_read={self.status_read!r} (distinct command codes)")
        by_pact = tuple((tuple(pact), tuple(types)) for pact, types in self.status_read_by_pact)
        object.__setattr__(self, "status_read_by_pact", by_pact)
        if (len({pact for pact, _types in by_pact}) != len(by_pact)
                or not all(_pact_key(pact) and _status_types(types) for pact, types in by_pact)):
            raise GoveeBleInvalidValue(f"{where}: status_read_by_pact={by_pact!r} (one entry per pact, each "
                                       "distinct command codes)")
        self._check_status_decode(where)
        self._check_mode_decode(where)
        groups = self.segment_brightness_groups
        if not (isinstance(groups, tuple) and all(
                isinstance(g, tuple) and g and all(_index(i) for i in g) for g in groups)
                and len({i for g in groups for i in g}) == sum(map(len, groups))):
            raise GoveeBleInvalidValue(f"{where}: segment_brightness_groups={groups!r} (a tuple of disjoint, "
                                       "non-empty tuples of segment indices)")
        if self.color_readback_per_batch < 1:   # a group size of 0 would never finish a read-back
            raise GoveeBleInvalidValue(
                f"{where}: color_readback_per_batch={self.color_readback_per_batch!r}")
        ic_range = self.color_readback_ic_range
        if ic_range is not None and not 0 <= ic_range[0] <= ic_range[1]:
            raise GoveeBleInvalidValue(f"{where}: color_readback_ic_range={ic_range!r}")
        if self.color_readback_ic_per_piece < 1:
            raise GoveeBleInvalidValue(
                f"{where}: color_readback_ic_per_piece={self.color_readback_ic_per_piece!r}")
        self._check_ic_rules(where)
        if self.color_writes_live_ic and not self.color_readback_live_ic:
            raise GoveeBleInvalidValue(f"{where}: color_writes_live_ic without color_readback_live_ic (the "
                                       "live pieces come from its aa 40 read)")
        if isinstance(self.ic_reads_around_status, list):
            object.__setattr__(self, "ic_reads_around_status", tuple(self.ic_reads_around_status))
        around = self.ic_reads_around_status
        status_ic = self.readback == "status" and self.color_readback_live_ic
        if (not (isinstance(around, tuple) and len(around) == 2 and all(isinstance(r, bool) for r in around))
                or ((any(around) or self.color_readback_ic_raw) and not status_ic)):
            raise GoveeBleInvalidValue(
                f"{where}: ic_reads_around_status={around!r}, color_readback_ic_raw="
                f"{self.color_readback_ic_raw!r} (two flags; either needs readback='status' with "
                "color_readback_live_ic)")
        reread = self.heartbeat_reread_pieces
        if isinstance(reread, bool) or not isinstance(reread, int) or reread < 0:
            raise GoveeBleInvalidValue(
                f"{where}: heartbeat_reread_pieces={reread!r} (0, no re-read, or more)")
        per_unit = self.light_num_per_unit
        if isinstance(per_unit, bool) or not isinstance(per_unit, int) or per_unit < 0:
            raise GoveeBleInvalidValue(
                f"{where}: light_num_per_unit={per_unit!r} (0, no aa 0f read, or more)")
        if self.color_readback_ic_walk_only and (
                not self.color_readback_live_ic or self.color_writes_live_ic):
            raise GoveeBleInvalidValue(f"{where}: color_readback_ic_walk_only needs color_readback_live_ic "
                                       "and no color_writes_live_ic (the live pieces size the walk alone)")
        if self.status_segment_count and (
                self.readback != "status" or int(_C.Command.cmd_a1) not in self.status_read
                or dict(self.status_decode).get("f_a1") != "segment_info"):
            raise GoveeBleInvalidValue(
                f"{where}: status_segment_count on readback={self.readback!r} with status_read="
                f"{self.status_read!r} (the count is the 0xAC status read's TLV 0xA1, which it must request "
                "and read as status_decode f_a1 segment_info)")
        for name in ("kelvin_by_hardware", "kelvin_by_software"):
            if isinstance(getattr(self, name), list):
                object.__setattr__(self, name, tuple(map(tuple, getattr(self, name))))
            self._check_kelvin_by_version(where, name)
        if self.kelvin_by_hardware and self.kelvin_by_software:
            raise GoveeBleInvalidValue(f"{where}: kelvin_by_hardware and kelvin_by_software (one kind only)")
        self._check_scene_versions_by_firmware(where)
        self._check_scene_versions_by_pact(where)
        self._check_ranges_by_pact(where)
        # heartbeat_timing is checked above whatever the heartbeat, and ignored without one, so
        # replace(profile, heartbeat=None) alone turns the beat off on every SKU.
        beat = self.heartbeat
        if beat is not None and (
                isinstance(beat, bool) or not isinstance(beat, int) or beat not in _HEARTBEATS):
            raise GoveeBleInvalidValue(
                f"{where}: heartbeat={beat!r} (0x00, 0x01, 0x33 or 0x36: a keep-alive read, or None)")
        card = frozenset(map(Capability, _resolve.CARD_CAPABILITIES))   # the card's 33 01 and 33 04
        if self.session == "per_command" and (not self.capabilities <= card or self.zones):
            raise GoveeBleInvalidValue(
                f"{where}: session='per_command' takes capabilities {sorted(c.value for c in card)} only and "
                f"no zones; got {sorted(c.value for c in self.capabilities)} and {len(self.zones)} zones")

    def _check_ic_rules(self, where: str) -> None:
        """The IC rules' fields: :attr:`color_readback_ic_table` ``(IC, pieces)`` entries of ints, each IC
        0 or more and once, the pieces 1 or more; :attr:`color_readback_ic_pair_cap` None or 1 or more, with
        :attr:`color_readback_ic_pair_reader` and without :attr:`color_readback_ic_skip_zero`; not both; and
        :attr:`color_readback_ic_otherwise` 1 or more where either is set, else None (a list is kept as a
        tuple)."""
        raw = self.color_readback_ic_table
        try:
            table = tuple(tuple(entry) for entry in raw)
        except TypeError:   # a flat pair or None: not (IC, pieces) entries
            raise GoveeBleInvalidValue(
                f"{where}: color_readback_ic_table={raw!r} ((IC, pieces) entries)") from None
        object.__setattr__(self, "color_readback_ic_table", table)
        cap, otherwise = self.color_readback_ic_pair_cap, self.color_readback_ic_otherwise

        def count(value: object, low: int) -> bool:
            return isinstance(value, int) and not isinstance(value, bool) and value >= low

        if not (all(len(e) == 2 and count(e[0], 0) and count(e[1], 1) for e in table)
                and len({e[0] for e in table}) == len(table)
                and (cap is None or (count(cap, 1) and self.color_readback_ic_pair_reader
                                     and not self.color_readback_ic_skip_zero))
                and not (table and cap is not None)
                and (count(otherwise, 1) if table or cap is not None else otherwise is None)):
            raise GoveeBleInvalidValue(
                f"{where}: color_readback_ic_table={table!r}, color_readback_ic_pair_cap={cap!r}, "
                f"color_readback_ic_otherwise={otherwise!r}, color_readback_ic_pair_reader="
                f"{self.color_readback_ic_pair_reader!r}, color_readback_ic_skip_zero="
                f"{self.color_readback_ic_skip_zero!r}")

    def _check_ranges_by_pact(self, where: str) -> None:
        """:attr:`kelvin_by_pact` / :attr:`brightness_by_pact`'s rules: ``(pact_type, pact_code)`` keys of
        non-negative ints, Kelvin ranges with 0 < min <= max over a base range and without
        :attr:`kelvin_by_hardware`, each overlapping the base range, with a range valid on every pact,
        brightness ranges that :attr:`brightness_range` could hold (lists are kept as tuples)."""
        kelvin = tuple((tuple(pact), lo, hi) for pact, lo, hi in self.kelvin_by_pact)
        bright = tuple((tuple(pact), None if span is None else tuple(span))
                       for pact, span in self.brightness_by_pact)
        object.__setattr__(self, "kelvin_by_pact", kelvin)
        object.__setattr__(self, "brightness_by_pact", bright)
        if not all(_pact_key(pact) and all(isinstance(k, int) and not isinstance(k, bool) for k in (lo, hi))
                   and 0 < lo <= hi for pact, lo, hi in kelvin):
            raise GoveeBleInvalidValue(f"{where}: kelvin_by_pact={kelvin!r}")
        if kelvin and (self.min_kelvin is None or self.max_kelvin is None or self.kelvin_by_hardware
                       or self.kelvin_by_software):
            raise GoveeBleInvalidValue(f"{where}: kelvin_by_pact needs min_kelvin and max_kelvin, and no "
                                       "kelvin_by_hardware / kelvin_by_software")
        if (kelvin and self.min_kelvin is not None and self.max_kelvin is not None
                and not _resolve.overlaps_every((self.min_kelvin, self.max_kelvin),
                                                [(lo, hi) for _pact, lo, hi in kelvin])):
            raise GoveeBleInvalidValue(f"{where}: kelvin_by_pact={kelvin!r}: each range must overlap "
                                       f"[{self.min_kelvin}, {self.max_kelvin}], and so must the range "
                                       "valid on every pact")
        if not all(_pact_key(pact) and (span is None or _raw_range(span)) for pact, span in bright):
            raise GoveeBleInvalidValue(f"{where}: brightness_by_pact={bright!r}")

    def _check_status_decode(self, where: str) -> None:
        """:attr:`status_decode`'s rules: ``(param, value)`` pairs, each param once and one of
        :data:`_STATUS_ENUMS` (a member name of its enum), ``num_per_group`` (1..255) or a flag (a bool);
        colour groups (``f_a5`` ``"color_group"``) need their rule, group size and 4-byte records, which is
        what the status read reads, and the one record (``"single_record"``) needs nothing more (a list is
        kept as a tuple)."""
        bad = f"{where}: status_decode={self.status_decode!r}"
        try:
            pairs: tuple[tuple[Any, ...], ...] = tuple(tuple(pair) for pair in self.status_decode)
            facts: dict[Any, Any] = dict(pair for pair in pairs if len(pair) == 2)
        except TypeError:
            raise GoveeBleInvalidValue(bad) from None
        object.__setattr__(self, "status_decode", pairs)
        ok = len(facts) == len(pairs)
        for name, value in facts.items():
            enum = _STATUS_ENUMS.get(name)
            if enum is not None:
                ok = ok and isinstance(value, str) and value in enum.__members__
            elif name == "num_per_group":
                ok = ok and isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 0xFF
            else:
                ok = ok and name in _STATUS_FLAGS and isinstance(value, bool)
        if facts.get("f_a5") == "color_group":
            ok = ok and facts.get("group_rule", "unknown") != "unknown" and "num_per_group" in facts \
                and facts.get("has_brightness") is True
        if not ok:
            raise GoveeBleInvalidValue(bad)

    def _check_mode_decode(self, where: str) -> None:
        """:attr:`mode_decode`'s rules: ``(param, value)`` pairs, each param once, ``music`` a member name of
        govee_common music_decode and ``empty_fails`` a bool (a list is kept as a tuple)."""
        bad = f"{where}: mode_decode={self.mode_decode!r}"
        try:
            pairs: tuple[tuple[Any, ...], ...] = tuple(tuple(pair) for pair in self.mode_decode)
            facts: dict[Any, Any] = dict(pair for pair in pairs if len(pair) == 2)
        except TypeError:
            raise GoveeBleInvalidValue(bad) from None
        object.__setattr__(self, "mode_decode", pairs)
        music = _STATUS_ENUMS["music"]
        if len(facts) != len(pairs) or not all(
                (name == "music" and isinstance(value, str) and value in music.__members__)
                or (name == "empty_fails" and isinstance(value, bool)) for name, value in facts.items()):
            raise GoveeBleInvalidValue(bad)

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
            if (not gates or not all(isinstance(g, str) and _resolve.parse_version(g) > 0 for g in gates)
                    or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in versions)):
                raise GoveeBleInvalidValue(f"{where}: scene_versions_by_firmware={gated!r}")

    def _check_kelvin_by_version(self, where: str, name: str) -> None:
        """:attr:`kelvin_by_hardware` / :attr:`kelvin_by_software` (``name``)'s rules: well-formed entries,
        highest version first (as :attr:`kelvin_version_compare` orders them), a base range, and an overlap
        with it for each entry and for the range valid on every revision."""
        gated: tuple[tuple[str, int, int], ...] = getattr(self, name)
        if not gated:
            return
        bad = f"{where}: {name}={gated!r}"
        for entry in gated:
            if not (isinstance(entry, tuple) and len(entry) == 3 and isinstance(entry[0], str) and entry[0]
                    and all(isinstance(k, int) and not isinstance(k, bool) for k in entry[1:])
                    and 0 < entry[1] <= entry[2]):
                raise GoveeBleInvalidValue(bad)
        lo, hi = self.min_kelvin, self.max_kelvin
        if lo is None or hi is None:
            raise GoveeBleInvalidValue(f"{bad} needs min_kelvin and max_kelvin")
        versions = [_resolve.version_key(entry[0], self.kelvin_version_compare) for entry in gated]
        if versions != sorted(versions, reverse=True) or len(set(versions)) != len(versions):
            raise GoveeBleInvalidValue(f"{bad}: list the versions highest first, once each")
        if any(k_lo > hi or k_hi < lo for _version, k_lo, k_hi in gated):
            raise GoveeBleInvalidValue(f"{bad}: each range must overlap [{lo}, {hi}]")
        if max(lo, *(e[1] for e in gated)) > min(hi, *(e[2] for e in gated)):
            raise GoveeBleInvalidValue(f"{bad}: no range is valid on every revision")


def _raw_range(span: tuple[int, ...]) -> bool:
    """A raw ``(min, max)`` brightness range a Device can use: 100 % must map to a byte too, or
    set_brightness(100) would fail on valid input."""
    if len(span) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) for v in span):
        return False
    lo, hi = span
    return 0 <= lo < hi <= 0xFF and percent_to_raw(100, lo, hi) <= 0xFF


def _index(value: object) -> bool:
    """A segment index: a non-negative int, not a bool."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _pact_key(pact: tuple[int, ...]) -> bool:
    """A ``(pact_type, pact_code)`` key of non-negative ints."""
    return len(pact) == 2 and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in pact)


def _status_types(types: object) -> bool:
    """A status read's TLV types: a tuple of distinct command codes (ints in 0..0xFF), empty for the bare
    read."""
    return isinstance(types, tuple) and len(set(types)) == len(types) and all(
        isinstance(t, int) and not isinstance(t, bool) and 0 <= t <= 0xFF for t in types)


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
    if "status_read" in kw:
        kw["status_read"] = tuple(int(_CMD[name]) for name in kw["status_read"])
    if "status_read_by_pact" in kw:
        kw["status_read_by_pact"] = tuple((pact, tuple(int(_CMD[name]) for name in names))
                                          for pact, names in kw["status_read_by_pact"])
    kw["heartbeat"] = None if kw["heartbeat"] == "none" else int(_CMD[kw["heartbeat"]])
    return DeviceProfile(skus=skus, **kw)


class _Route(NamedTuple):
    """One BLE row's route for a SKU: its goodsType, its accepted pacts (None = any), its profile, and
    whether it is taken only on an advertised pact (``_resolve.Route.needs_pact``: the target row of a
    pact-conditioned reassignment, a route of a SKU a pairing write names at an exact pact, a
    ``pact_required`` row, or a pact route, H60A6's at (1, 1))."""
    goods_type: int
    pacts: frozenset[tuple[int, int]] | None
    profile: DeviceProfile
    needs_pact: bool


def _accepts(route: _Route, pact: tuple[int, int] | None) -> bool:
    """Whether ``route`` serves an advertisement with ``pact`` (None: none advertised): at one of its pacts
    (any, on a ``pacts: any`` row), and with none only where the route doesn't need one (``_resolve`` module
    doc). A pact-conditioned reassignment's target row needs it: the app takes the rewrite to that row only
    for a record whose pact meets the gate, and keeps a record without one at its source goodsType. So does
    each row of a SKU a pairing write names at an exact pact: without the pact the library can't tell the
    row's facts from those the app runs at the write's pact (H615A-E's (65535, 255)). And so does a
    ``pact_required`` row: its goodsType also runs, at pacts the row doesn't list, a path the row doesn't
    serve (the (1,1) of straightfloorlamp 69, the h70b3 rows and pact_tvlightv2 24, the (1,2) / (1,3) of
    h70b6 329), so a pact-less advert may be one of those. So does a route only a pact branch's support
    admits (dreamcolorlightv2 21 at (2,1), whose row is ``modelled`` at its other pacts): a pact-less advert
    may be a unit at one of those. A pact route is its pact's alone, so a pact-less advert takes the row's own
    route (H60A6: the 13 segments)."""
    if pact is None:
        return not route.needs_pact
    return route.pacts is None or pact in route.pacts


def _table(families: tuple[_resolve.Family, ...], admitted: frozenset[str]
           ) -> tuple[tuple[DeviceProfile, ...], dict[str, tuple[_Route, ...]]]:
    """The profiles of the ``admitted`` rows of ``families`` and ``{SKU upper-cased: its routes}``, in
    devices.yaml order."""
    profiles: list[DeviceProfile] = []
    routes: dict[str, list[_Route]] = {}
    for skus, facts, members in _resolve.profiles(families, _devices.MODE_COLOR_PARSE, admitted,
                                                  _devices.SCENE_CATALOGS):
        p = _from_spec(skus, facts)
        profiles.append(p)
        for route in members:
            routes.setdefault(route.sku.upper(), []).append(
                _Route(route.goods_type, route.pacts, p, route.needs_pact))
    return tuple(profiles), {sku: tuple(hits) for sku, hits in routes.items()}


_ADMITTED: Final = _table(_devices.FAMILIES, _resolve.ADMITTED)
_MODELLED: Final = _table(_devices.FAMILIES, _resolve.MODELLED)
PROFILES: Final[tuple[DeviceProfile, ...]] = _ADMITTED[0]
"""The supported SKUs' profiles, generated from spec/devices.yaml (module doc): one per group of SKUs whose
resolved facts are identical, in devices.yaml order: the ``hardware_verified`` and ``app_traced`` rows (the
``modelled`` ones only come through ``allow_modelled``). A SKU's evidence, citations and bench notes are its
devices.yaml row's ``doc`` and ``doc-ref``, and for a SKU only the catalogue snapshot routes, also the
snapshot's goodsType for it."""


def profile_for(sku: str, *, pact: tuple[int, int] | None = None, goods_type: int | None = None,
                allow_modelled: bool = False) -> DeviceProfile | None:
    """The profile for ``sku`` (case-insensitive), or None if unsupported.

    The candidates are the spec's BLE rows that list ``sku`` and accept the advertised ``pact``
    (``(pact_type, pact_code)``, ``GoveeAdvertisement.pact``; None: none advertised, which any row accepts
    but one whose route needs a pact, ``_accepts``), at ``goods_type`` where given.
    Exactly one candidate (or several with identical facts) is the SKU's profile; several that differ are
    unsupported: pass the goodsType to pick one. The rows are the ``hardware_verified`` and ``app_traced``
    ones; ``allow_modelled`` also takes ``modelled`` rows (inferred, not traced), and as the build refuses a
    SKU that rows of both kinds list (``_resolve.routes``), it only adds SKUs, never changes a route. A SKU is
    served only at its rows' pacts, as the app's scan serves it (``AbsBaseBleModel.e``: ``checkProtocol`` ->
    ``Pact.i`` refuses a pact the goodsType doesn't accept), but for the 1.x pact fallback (decision D3,
    ``_resolve.PACT_FALLBACK_SKUS``): at a pact no row accepts, each of the 18 SKUs 1.4.0 shipped still routes
    as a unit advertising no pact does, to its one ``hardware_verified`` / ``app_traced`` profile there, as in
    1.x, so no released unit is demoted (a pact route, such as H60A6's at (1, 1), is taken at its pact alone).
    A SKU whose routes all need a pact is unsupported without one, whatever the goodsType: one only
    pact-conditioned reassignments' target rows route, as the app keeps a pact-less unit at its source
    goodsType; one a pairing write names at an exact pact (H615A-E), whose pact-less advert may carry
    that pact, where the app runs other frames than the row's; one only ``pact_required`` rows route
    (H6076 / H8076 / R16D0, H70B6, H6198 / H6199, and the h70b3 rows' H70B3 / H70B4 / H70B5 / H6810 / H6811 /
    H8811), whose pact-less advert may carry a pact where the app runs a path the row doesn't serve; and,
    without ``allow_modelled``, one a row admits at some pacts alone (a pact branch's ``support``: H611A /
    H6143 / H6144 at (2,1), ``modelled`` at (1,1) / (1,2) and with no pact)."""
    key = sku.upper()
    for table in (_ADMITTED, _MODELLED) if allow_modelled else (_ADMITTED,):
        hits = {id(r.profile): r.profile for r in table[1].get(key, ())
                if _accepts(r, pact) and (goods_type is None or r.goods_type == goods_type)}
        if hits:
            return next(iter(hits.values())) if len(hits) == 1 else None
    if pact is not None and key in _resolve.PACT_FALLBACK_SKUS:   # D3, the 1.x compatibility rule
        loose = {id(r.profile): r.profile for r in _ADMITTED[1].get(key, ())
                 if _accepts(r, None) and (goods_type is None or r.goods_type == goods_type)}
        if len(loose) == 1:
            return next(iter(loose.values()))
    return None


def refuses_pact(profile: DeviceProfile, sku: str, pact: tuple[int, int]) -> bool:
    """Whether ``sku``'s routes to ``profile`` (any support level) all refuse ``pact``, and the 1.x pact
    fallback doesn't route it there either: :func:`profile_for` would not give ``profile`` for this
    advertisement. A route's profile with only its heartbeat cleared (the documented opt-out,
    ``dataclasses.replace(profile, heartbeat=None)``) counts as that route's. False for a profile no route
    of ``sku`` carries (built by hand: nothing to check)."""
    key = sku.upper()
    mine = [r for table in (_ADMITTED, _MODELLED) for r in table[1].get(key, ())
            if _routed(r.profile, profile)]
    if not mine or any(_accepts(r, pact) for r in mine):
        return False
    return not _routed(profile_for(key, pact=pact), profile)


# The DeviceProfile fields an advertised pact picks within one row (a row's by_pact branches: its status read,
# scene set and Kelvin and brightness ranges, _resolve._PACT_FACTS), and the SKU list: a Device reads each
# per call, with the pact. A later pact that routes the SKU to another row whose profile differs only in
# these is followed (reroute); any other difference would change a held link (the cipher fallback, the
# keep-alive, the unlock, the session frames) or the entities a caller built from the capabilities, zones
# and segments.
_PACT_PICKED: Final = frozenset({
    "skus", "status_read", "status_read_by_pact", "status_color_groups_pact", "scene_versions",
    "scene_versions_by_pact", "min_kelvin", "max_kelvin", "kelvin_by_pact", "brightness_range",
    "brightness_by_pact"})


def reroute(profile: DeviceProfile, sku: str, pact: tuple[int, int]) -> DeviceProfile | None:
    """The profile a Device built on ``profile`` for ``sku`` takes when an advertisement carries ``pact``, or
    None to keep ``profile``. Where ``pact`` doesn't route ``sku`` to ``profile`` (:func:`refuses_pact`: a
    routed profile, or it with only its heartbeat cleared) and :func:`profile_for` routes it, at the same
    admission (``allow_modelled`` where ``profile`` is a ``modelled`` row's), to another profile that differs
    only in what a pact picks within a row (:data:`_PACT_PICKED`), that profile, with the heartbeat cleared
    where ``profile``'s is: the row the app's screen runs for a unit at that pact (the BK rows: H6118 /
    H6194 / H6179 at (10, 1) take goodsType 39's percent brightness, at (1, 1) goodsType 6 / 12's raw one).
    None for a hand-built profile, a pact the SKU's rows refuse, and a row whose capabilities, zones,
    segments or session facts differ (``Device._learn_pact`` then warns and keeps its profile)."""
    if not refuses_pact(profile, sku, pact):
        return None
    key = sku.upper()
    modelled = any(_routed(r.profile, profile) for r in _MODELLED[1].get(key, ()))
    other = profile_for(key, pact=pact, allow_modelled=modelled)
    if other is None:
        return None
    if profile.heartbeat is None:
        other = replace(other, heartbeat=None)
    same = all(getattr(profile, f.name) == getattr(other, f.name) for f in fields(DeviceProfile)
               if f.name not in _PACT_PICKED)
    return other if same else None


def _routed(routed: DeviceProfile | None, profile: DeviceProfile) -> bool:
    """``profile`` is ``routed``, or ``routed`` with only its heartbeat cleared (the documented opt-out,
    ``dataclasses.replace(profile, heartbeat=None)``), which keeps the row's routing."""
    return routed is not None and (routed is profile or (
        profile.heartbeat is None and replace(routed, heartbeat=None) == profile))


def supported_skus(*, allow_modelled: bool = False) -> list[str]:
    """All supported SKUs, sorted: every SKU a ``hardware_verified`` or ``app_traced`` row routes (and with
    ``allow_modelled`` a ``modelled`` one), at one pact at least. A SKU that several rows list needs the pact
    or the goodsType to pick its profile, and one whose routes all need a pact (a pact-conditioned
    reassignment's target rows, a SKU a pairing write names at an exact pact, a ``pact_required`` row's, or a
    row's that a pact branch's support alone admits) needs its pact (:func:`profile_for`)."""
    skus = set(_ADMITTED[1])
    if allow_modelled:
        skus |= set(_MODELLED[1])
    return sorted(skus)


def _decode(profile: DeviceProfile, name: str) -> dict[str, int]:
    """The mode_decode params of the parse ``name`` on ``profile``."""
    decode = dict(_DECODES[name])
    if profile.bar_switch:
        decode["c15"] = _BAR_KELVIN
    return decode


def wire_profile(profile: DeviceProfile) -> Any:
    """The spec's ``govee_profile`` for the device facts a reply's decode depends on: the mode decode
    (:attr:`DeviceProfile.mode_color_parse`, the bars' 0x15 on a :attr:`DeviceProfile.bar_switch`
    device, and :attr:`DeviceProfile.mode_decode`'s music layout and empty-value rule) for the aa 05 report,
    the status TLV 0x05's (``mode_status``: :attr:`DeviceProfile.mode_color_parse_status`, else the same),
    the aa 01 power rule (on a ``readback="plug"`` profile its relay mask,
    :attr:`DeviceProfile.relay_on_mask`, as the aa 01 heartbeat rule the library's poll follows:
    ``power_state.is_on_heartbeat``; else a light's,
    masks 0, with :attr:`DeviceProfile.light_nonzero`, the poll's rule, as both ``light_nonzero`` and
    ``light_heartbeat_nonzero``), and on a ``readback="status"`` profile the 0xAC reply's decode
    (:attr:`DeviceProfile.status_decode`: its forms and colour-group form, and the TLV 0x05's music layout and
    empty-value rule in ``mode_status``), which ``reassemble.status_profile`` reads the status stream with.
    Cached per set of facts (``_spec.profile``)."""
    status = profile.mode_color_parse_status
    mode_status = None if status is None else _decode(profile, status)
    reads: dict[str, Any] = {}
    if profile.readback == "status":
        facts = dict(profile.status_decode)
        reads = {name: int(enum[facts[name]]) for name, enum in _STATUS_ENUMS.items()
                 if name in facts and name != "music"}
        reads.update({name: facts[name] for name in ("num_per_group", "has_brightness") if name in facts})
        colours = mode_status if mode_status is not None else _decode(profile, profile.mode_color_parse)
        mode_status = {**colours, "music": int(_C.MusicDecode[facts.get("music", "not_decoded")]),
                       "empty_fails": bool(facts.get("empty_fails", False))}
    reply = dict(profile.mode_decode)   # the aa 05 reply's music layout and empty-value rule
    if "music" in reply:
        reads["music"] = int(_C.MusicDecode[reply["music"]])
    if "empty_fails" in reply:
        reads["empty_fails"] = bool(reply["empty_fails"])
    # The aa 01 reply's rule follows the read the profile makes: the plug read's relay mask, else a light's
    # (masks 0, govee_profile power_rule); each is already the rule the poll follows, so it fills both the
    # connect-read and the heartbeat params.
    mask = profile.relay_on_mask if profile.readback == "plug" else 0
    return _spec.profile(**_decode(profile, profile.mode_color_parse),
                         mode_status=None if mode_status is None else tuple(sorted(mode_status.items())),
                         relay_on_mask=mask, relay_heartbeat_on_mask=mask,
                         light_nonzero=profile.light_nonzero, light_heartbeat_nonzero=profile.light_nonzero,
                         **reads)
