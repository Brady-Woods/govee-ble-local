"""Data-driven device descriptors.

A ``DeviceProfile`` carries everything the capability-driven :class:`~.device.Device`
needs for one SKU family. Values are spec-derived where the spec is
concrete (colour submode, scene_versions, kelvin) and hardware/Java-verified where the
spec was prose (segment counts, zones, scene dialect). The wire cipher is resolved at
connect from the advertisement, so ``encryption`` here is only the fallback default;
``requires_secret`` is the separate account-lock capability.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Literal, get_args

from ..exceptions import GoveeBleInvalidValue
from ..models import Capability, Encryption, Zone
from ..wire.build import ColorScheme
from .brightness import percent_to_raw

# scene dialects: "A" = library parseSceneV1 (comType from scene_versions);
# "B_h60a6" = H60A6 DIY (0x58; 0xA4-MTU graffiti / 0xA3 DIY by length gate);
# "B_h6052" = H6052 type-5 professional graffiti (commByte 9).
SceneDialect = Literal["A", "B_h60a6", "B_h6052"]
# "status" (0xAC burst) | "polled" (aa 01/04/05) | "plug" (aa 01 relay bitmask) | "none"
ReadBack = Literal["status", "polled", "plug", "none"]
ColorReadBack = Literal["", "mechanism_b", "mechanism_a_direct"]
# How the colour sub-modes of an ``aa 05`` mode report are decoded (the SKU's Info4Detail mode parse):
# "rgb_kelvin"      = 0x15 -> kelvin; 0x0d -> [r, g, b, kelvin u2be, ...], the rgblight form on scheme
#                     "h6188" (the default: H60A6, and a profile that doesn't choose one;
#                     wire.parse.parse_mode_0d_for);
# "rgb_kelvin_tint" = 0x15 / 0x0b / 0x02 / 0x0d all -> [r, g, b][kelvin s16be][tint r, g, b]: kelvin > 0
#                     = CCT, 0 = that RGB, < 0 = the tint as RGB (the 0x0d bulbs H6006 / H6008 / H6004
#                     and H6188: ModeParseStrategyConfig.a via OpInfo4Detail.parseModeValidBytes;
#                     wire.parse.parse_mode_rgb_kelvin_tint);
# "rgb_0d"          = 0x0d only -> [r, g, b], its Kelvin bytes never read; 0x15 and every other colour
#                     sub-mode is not parsed (H6052: H6052InfoDetail.G; wire.parse.parse_mode_0d_rgb);
# "kelvin"          = 0x15 / 0x0b / 0x02 / 0x0d all -> SubMode4Color [gradual][kelvin s16be], CCT only for
#                     a Kelvin > 0 (H6062: striplight OpInfo4Detail; H61A8: base OpInfo4Detail);
# "kelvin_15"       = 0x15 only, its Kelvin read signed (SubMode4Color, BleUtil.getSignedShort); any other
#                     colour sub-mode is not parsed (H6641, H6047);
# "rgb_kelvin_0d"   = 0x0d only -> [r, g, b, kelvin s16be], kelvin > 0 = CCT; 0x15 and every other
#                     colour sub-mode is not parsed (H6061: CubeNewInfo4Detail.U);
# "kelvin_15_rgb_kelvin_0d" = 0x15 -> SubMode4Color [gradual][kelvin s16be], 0x0d -> [r, g, b, kelvin
#                     s16be] (kelvin > 0 = CCT, else that RGB); any other colour sub-mode is not parsed
#                     (H60A6: pact_h60a0 h60a1.Info4Detail.Q).
ModeColorParse = Literal["rgb_kelvin", "rgb_kelvin_tint", "rgb_0d", "kelvin", "kelvin_15", "rgb_kelvin_0d",
                         "kelvin_15_rgb_kelvin_0d"]

# How a plug's whole-device power is written (its main switch on the app's detail screen):
# "outlet0"     = 33 01 11 / 10, the relay-0 encoding (goodsType 43: H5080 / H5083 / H5085, one outlet;
#                 and 1.3.0's frame for every plug);
# "all_outlets" = 33 01 FF / F0, every outlet (goodsType 50 H5082, 90 H5160 / H5161;
#                 SwitchControllerV2(on, 15));
# "total_switch"= 33 02 01 / 00 (goodsType 307 H5089; Controller4TotalSwitch).
RelayPower = Literal["outlet0", "all_outlets", "total_switch"]

_C = Capability

# Capabilities a colour scheme cannot express: wire.build has no frame for them on that scheme
# (its builder refuses), so the table (_index_by_sku) refuses a profile declaring one rather than
# fail at the setter. A custom profile is accepted, as in 1.3.0, and that setter raises
# GoveeBleNotSupported.
#  - h61a8: no colour temperature at all (build.color_temp / segment_color_temp).
#  - h6006 / h6188 (0x0d): no segment mask in any colour frame (Bytes.f / p / A), so no SEGMENTS.
#  - h6061: whole-device only; per-panel colour needs the user's layout, which is not modelled.
# Narrower gaps inside a capability (H61A8 has SEGMENTS but no per-segment brightness) stay with
# the builder, whose GoveeBleNotSupported the Device re-raises with the SKU.
_SCHEME_LACKS: Final[Mapping[str, frozenset[Capability]]] = MappingProxyType({
    "h61a8": frozenset({_C.COLOR_TEMP}),
    "h6006": frozenset({_C.SEGMENTS}),
    "h6188": frozenset({_C.SEGMENTS}),
    "h6061": frozenset({_C.SEGMENTS}),
})
# mechanism_a_direct's group size. Controller4ColorInfoByGroup takes it from its caller, and every
# caller the profiles model walks groups of 4: H6047 NewDetailVm.n5, H6641 H61D3Support.r(), H6062
# and H6061 ceil(n / 4). A table profile that leaves color_readback_per_batch at mechanism_b's 3 is
# a typo (_index_by_sku refuses it).
_DIRECT_GROUP_SIZE: Final = 4


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
    needs is in the set, and is activate-only otherwise (:func:`..scenes.scene_upload_params`)."""
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
    """Plug power: the goodsType's main-switch frame (:attr:`relay_power`), followed by a best-effort
    sync-time frame."""
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
    outlets' bits OR-ed together on the multi-outlet ones (see the plug profiles). 0xFF = any bit."""
    relay_power: RelayPower = field(default="outlet0", kw_only=True)
    """Plug power frame (see :data:`RelayPower`); only read when :attr:`relay` is set. The default keeps
    1.3.0's ``33 01 11 / 10``."""
    advert_on_mask: int | None = field(default=None, kw_only=True)
    """Plug advertisement on/off: the bits of the advertised switch byte that make the device "on";
    None (every light) = the byte equals 1 (``AbsBaseBleModel.A``).

    The plugs' device-list models decode it per goodsType (h5080 ``Model.A``,
    ``Model4Plug2.A``), which is not always the read-back's :attr:`relay_on_mask` (see the plug
    profiles)."""
    color_readback_ic_range: tuple[int, int] | None = field(default=None, kw_only=True)
    """The (min, max) clamp for the live IC count (see :attr:`color_readback_live_ic`)."""
    color_readback_ic_per_piece: int = field(default=1, kw_only=True)
    """IC count per colour piece (see :attr:`color_readback_live_ic`)."""
    mode_color_parse: ModeColorParse = field(default="rgb_kelvin", kw_only=True)
    """Decoding of the aa 05 colour sub-modes; see :data:`ModeColorParse`."""

    def __post_init__(self) -> None:
        """Validate each field: GoveeBleInvalidValue (a ValueError) on a value no Device can use.

        Fail at construction, not silently at poll time, on a typo. Values that are each valid but
        that the builders can't serve together (a capability the colour scheme has no frame for, a
        direct colour read-back in groups other than 4, a live IC read without its derivation) are
        accepted here, as in 1.3.0; the setter or read concerned then raises GoveeBleNotSupported or
        does without, and the table itself is checked for them when it is indexed
        (:func:`_index_by_sku`)."""
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
            ("mode_color_parse", ModeColorParse),
            ("relay_power", RelayPower),
        ):
            value = getattr(self, name)
            if value not in get_args(allowed):
                raise GoveeBleInvalidValue(f"{where}: {name}={value!r} not in {get_args(allowed)}")
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
        if self.color_readback_per_batch < 1:   # a group size of 0 would never finish a read-back
            raise GoveeBleInvalidValue(
                f"{where}: color_readback_per_batch={self.color_readback_per_batch!r}")
        ic_range = self.color_readback_ic_range
        if ic_range is not None and not 0 <= ic_range[0] <= ic_range[1]:
            raise GoveeBleInvalidValue(f"{where}: color_readback_ic_range={ic_range!r}")
        if self.color_readback_ic_per_piece < 1:
            raise GoveeBleInvalidValue(
                f"{where}: color_readback_ic_per_piece={self.color_readback_ic_per_piece!r}")


def _where(p: DeviceProfile) -> str:
    """The profile error messages' prefix, e.g. ``DeviceProfile[H6006]``."""
    return f"DeviceProfile[{', '.join(p.skus)}]"


def _incoherent(p: DeviceProfile) -> str | None:
    """Why the builders can't serve ``p``'s fields together, or None.

    Checked for the table (:func:`_index_by_sku`), where it is a typo. A custom profile may combine
    them, as in 1.3.0: the setter for a capability its scheme can't express raises
    GoveeBleNotSupported, and a direct read-back walks the group size it was given."""
    lacking = p.capabilities & _SCHEME_LACKS.get(p.color_scheme, frozenset())
    if lacking:
        return f"color_scheme={p.color_scheme!r} cannot express {sorted(c.name for c in lacking)}"
    if p.color_readback == "mechanism_a_direct" and p.color_readback_per_batch != _DIRECT_GROUP_SIZE:
        return (f"color_readback_per_batch={p.color_readback_per_batch!r} (mechanism_a_direct "
                f"reads groups of {_DIRECT_GROUP_SIZE})")
    if p.color_readback_live_ic and p.color_readback_ic_range is None:
        return "color_readback_live_ic needs color_readback_ic_range (the app's IC clamp)"
    return None


_LIGHT = frozenset({_C.POWER, _C.BRIGHTNESS, _C.RGB, _C.COLOR_TEMP, _C.SCENES})

PROFILES: Final[tuple[DeviceProfile, ...]] = (
    # H60A6 — ceiling pro: AES, dual zone, 13 segments, dialect-B scenes, full status read-back.
    # Segment map (live-verified): indices 0..11 are the background RGBIC ring; the HIGHEST
    # index (12) is the MAIN PANEL — an independently-addressable segment (read-back holds it
    # distinct from 0..11). So per-zone colour/CCT is real: main = mask 0x1000, background =
    # 0x0FFF, whole-device = 0x1FFF (both).
    DeviceProfile(
        skus=("H60A6",), capabilities=_LIGHT | {_C.SEGMENTS},
        color_scheme="h60a6", encryption=Encryption.AES_RC4_PSK, segments=13,
        zones=(Zone("main", power_index=0, segments=(12,)),
               Zone("background", power_index=1, segments=tuple(range(12)))),
        scene_versions=frozenset({0, 1, 2, 3, 5}), scene_dialect="B_h60a6",
        min_kelvin=2700, max_kelvin=6500, readback="status", global_power=True,
        # The poll is the app's connect read, the 0xAC status burst alone
        # (VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1); its 0x05 TLV is the mode, decoded by
        # pact_h60a0 h60a1.Info4Detail.Q (VM4LightH60A6.C1; the .n lambda hands it the TLV value), and its
        # 0x01 TLV the power (devices.yaml power_read: ac_status; no aa 01 read). Timing differences, all
        # frames the app also sends: the burst is re-read on every poll, where the app reads it once per
        # connect and then keeps the link with an aa 00 heartbeat (not sent here) and its ee 30 pushes
        # (ignored here).
        mode_color_parse="kelvin_15_rgb_kelvin_0d",
        # CCT: `15 01 ff ff ff <K> <tint> <mask>` (Bytes.r type 1), ONE frame: whole device = mask
        # 0x1FFF (DelegateColorV2.G0 -> Config.Yt, KelvinConfig.b -> Bytes.C, 7.7.02), per zone =
        # that zone's mask. An RGB pick (whole device, zone or segment) equal to a tint colour in
        # [2700, 6500] K is the same frame with the PICKED colour as the tint and the pick's own mask.
    ),
    # H6047 — bar light: plaintext, two bars (33 36 per bar, 33 01 whole device), 10 segments,
    # dialect-A scenes. Modeled from Govee Home 7.7.02; not yet live-verified (no hardware).
    #  - goodsType 119 uses the OLD detail screen (h6047/pact/Model.t -> detail/NewDetailAc), which
    #    does not dispatch the 0xAC status burst (that is the h6038-family newdetail SKUs);
    #    `ac 03 03 41 30 a5` returns zero frames for it (observed on hardware, CHANGELOG 1.0.1).
    #  - per-segment colour: direct per-group requests (mechanism_a_direct: `aa a5 <group>`,
    #    Controller4ColorInfoByGroup), group size 4. NewDetailVm.n5 walks
    #    Support.getBulbStringMaxNum(119) = 10 pieces = 3 groups and SubMode4Color trims the last
    #    group to 2 records, so read-back is the same 10 segments as the write
    #    (getColorPieceSize = 12 is only used by the newdetail screens 277/288/298): no
    #    `color_readback_segments`, no phantom segments 10 and 11.
    #  - power: the main switch sends 33 01 <on> (NewDetailVm.switchTrigger -> ControllerSwitch);
    #    33 36 <left> <right> only for the per-bar buttons (NewDetailVm.D5). The app shows the device
    #    on when either bar is on (aa 36 heartbeat). The aa 01 poll stays: the app's connect read
    #    (Compose4InfoBleIot) includes ControllerSwitch (aa 01), brightness (aa 04), gradual (aa a3)
    #    and mode (aa 05), and a timeout there fails the whole connect, so the device answers it.
    #  - an RGB pick equal to a Kelvin tint colour within [2200, 6500] K is that CCT frame with the picked
    #    colour as the tint (NewDetailVm.configColorController -> H6047ColorControllerConfig.a -> Config.Yt,
    #    7.7.02); the state then reports that Kelvin for both bars (or the one bar for a bar pick).
    #  - CCT: `15 01 ff ff ff <K> <tint> <mask>`; aa 05 15 reads back [gradual][kelvin left u2be]
    #    [kelvin right u2be] (SubModeColor.parse), so each bar has its own kelvin. The mode parse
    #    (detail/Info4Detail.parseModeValidBytes) knows only 0x15, 0x04, 0x0a and 0x13: a 0x0d report
    #    is "not support", so it is not decoded here either (mode_color_parse "kelvin_15").
    #  - gradual: read on connect (ControllerGradual4BleWifi) and written 33 a3 <on> by the colour
    #    screen (ColorModeControllerConfig.p -> Config.G[119] = version 0 -> Bytes.w).
    DeviceProfile(
        skus=("H6047",), capabilities=_LIGHT | {_C.SEGMENTS},
        color_scheme="h60a6", encryption=Encryption.NONE, segments=10,
        zones=(Zone("left", power_index=0, segments=tuple(range(0, 5))),
               Zone("right", power_index=1, segments=tuple(range(5, 10)))),
        scene_versions=frozenset({0, 1, 2, 3}), bar_switch=True,
        min_kelvin=2200, max_kelvin=6500, readback="polled",
        color_readback="mechanism_a_direct", color_readback_per_batch=4, gradual=True,
        mode_color_parse="kelvin_15",
    ),
    # H61A8 — rope: plaintext, 0x0b scheme, 15 segments, no CCT, dialect-A scenes.
    # Mechanism-B per-segment colour read-back (spec Change 7): 0xAA 0xA5 (V2, adds
    # brightness) / 0xA2 (V1) per-batch frames, segment = (batch_seq-1)*per_batch+i.
    # 15 segments / 3-per-batch = 5 batches. Modeled from source; read back live from the bench
    # H61A8 (2026-10-16: segments 0..14). `polled` covers power/brightness/scene.
    # The aa 05 mode report (Govee Home 7.7.02): goodsType 19 is one of dreamcolorlightv1 Support.f0's
    # goodsTypes (Support.java:164), which SubLib4BleIot.keys() serves (the legacy SubLib keys only
    # Support.Q0 = {17, 18, 70, 73}). Its transform builds Model4BleIot, whose t() (:41-46) opens
    # /rgbic/newdetail = com.govee.rgbiclight.AcNewDetail (pact_rgbiclight__ModuleRouter__Registered:96).
    # That screen decodes with base OpInfo4Detail.parseModeValidBytes (:766-778): sub-modes 0x15 / 0x0b /
    # 0x02 / 0x0d go to SubMode4Color, whose default parse (SubMode4Color.java:44-48) reads
    # [gradual][kelvin s16be] (BleUtil.getSignedShort), and the Kelvin is taken only when > 0. rgbiclight's
    # ModeParseStrategyConfig installs only music and scene strategies. So mode_color_parse "kelvin", as
    # H6062 (fd4f794's devices.yaml). The dreamcolorlightv1 Mode.parseSubMode / SubModeColorV2 path is
    # the legacy AdjustAc screen, which goodsType 19 never opens.
    DeviceProfile(
        skus=("H61A8",),
        capabilities=frozenset({_C.POWER, _C.BRIGHTNESS, _C.RGB, _C.SEGMENTS, _C.SCENES}),
        color_scheme="h61a8", encryption=Encryption.NONE, segments=15,
        scene_versions=frozenset({0, 1, 2, 3}), readback="polled",
        color_readback="mechanism_b", color_readback_per_batch=3, gradual=True,
        mode_color_parse="kelvin",
    ),
    # H6006 / H6008 — legacy bulbs: plaintext, 0x0d scheme, type-1 rgb scene upload (version {1}).
    # The aa 05 mode report (Govee Home 7.7.02): bulblightv3 / bulblightv1 pact Model -> bulblight
    # AcNewDetail -> VM4Light ($connectBleSuc$1.l -> m5 -> j5 installs ModeParseStrategyConfig.a, the
    # non-H6001 branch) -> OpInfo4Detail.parseModeValidBytes, which decodes 0x15, 0x0b, 0x02 and 0x0d
    # alike as [r, g, b][kelvin s16be][tint] and takes CCT only for a Kelvin > 0 (mode_color_parse
    # "rgb_kelvin_tint"). Static firmware analysis (not hardware-verified): both bulbs' firmware sends
    # the tint bytes as 0 and the Kelvin pre-zeroed, so a Kelvin of 0 is RGB mode.
    # Kelvin range: both bulbs are goodsType 64 (the account device-list), which KelvinConfig.Config maps to
    # version 2 (KelvinConfig.java:544-546, Config.V2:1389-1390) = [2700, 6500] (:407).
    DeviceProfile(
        skus=("H6006",), capabilities=_LIGHT, color_scheme="h6006",
        encryption=Encryption.NONE, min_kelvin=2700, max_kelvin=6500,
        scene_versions=frozenset({1}), readback="polled", mode_color_parse="rgb_kelvin_tint",
    ),
    DeviceProfile(
        skus=("H6008",), capabilities=_LIGHT, color_scheme="h6006",
        encryption=Encryption.NONE, min_kelvin=2700, max_kelvin=6500,
        scene_versions=frozenset({1}), readback="polled", mode_color_parse="rgb_kelvin_tint",
    ),
    # H6004 — BLE revision of the bulb (bulblightv3 split, goodsType 64). The other revision
    # (goodsType 10, bulblightv1) is Wi-Fi/IoT-only (IotSkuItemV2.needBle() = false) and has no
    # BLE path. Modeled from source; not hardware-verified (no H6004 hardware). Source (Govee
    # Home 7.7.02):
    #  - colour: 0x0d sub-mode; colour instruction version Config.x[64] -> Config.rv = 2 -> Config.bu:
    #    RGB = Bytes.f, CCT = Bytes.p = [0d, tint RGB, kelvin u2be, tint RGB] ("h6006" both-slot
    #    form, the same builder as H6008; the white-slot "h6188" form is not used here).
    #  - an RGB pick equal to a Kelvin tint colour within [2700, 6500] K is sent as CCT, `0d <picked RGB>
    #    <K> <first tint of K>` (Config.bu = KelvinConfig.i(c) ? Bytes.p : Bytes.f, 7.7.02); otherwise RGB.
    #  - kelvin: KelvinConfig map 64 -> Config.V2 = [2700, 6500] (bulblightv3 Support.getColorTemRange).
    #  - brightness: Support.getBrightnessRange = {1, 100, 0}: percent on the wire, no raw scaling
    #    (the 20..254 scaling is the legacy h6001 path; H6004 is not in h6001/sku/Sku).
    #  - read-back: polled aa 01 / aa 04 / aa 05; the mode report is decoded as on H6006 / H6008
    #    (bulblight VM4Light -> ModeParseStrategyConfig.a -> OpInfo4Detail.parseModeValidBytes:
    #    0x15 / 0x0b / 0x02 / 0x0d = [r,g,b, kelvin s16be, tint RGB], CCT for a Kelvin > 0;
    #    mode_color_parse "rgb_kelvin_tint"); no per-segment read (Support.getColorPieceSize = 0).
    #  - scenes: the cloud scene library (light-effect-libraries, the app's EffectAc), applied with
    #    the version array {0, 1} that ScenesDelegateRegister gives goodsType 64
    #    (Support4BulbLightV3.g; bulblightv3 Support.supportScenesOp(64) is the same), i.e. dialect A,
    #    ScenesOp.parseSceneV1: type-1 RGB scenes upload with comm byte 0x01, type 0 activate only
    #    (the IoT/group path, Support.is2NewScenesMode, uses {1}: the same upload set).
    #  - no account-lock; plaintext by default (cipher is resolved from the advertisement).
    DeviceProfile(
        skus=("H6004",), capabilities=_LIGHT, color_scheme="h6006",
        encryption=Encryption.NONE, min_kelvin=2700, max_kelvin=6500,
        scene_versions=frozenset({0, 1}), readback="polled", mode_color_parse="rgb_kelvin_tint",
    ),
    # H6052 — table lamp: plaintext, 0x0d scheme; type-5 scenes via dialect B_h6052 (commByte 9).
    # Colour read-back comes from the polled 0x05 report, decoded as the control screen does
    # (tablelampv1 Model.u -> H6052NewDetailActivity -> H6052NewDetailVM$connectBleSuc$1 ->
    # H6052InfoDetail.G, Govee Home 7.5.12 and 7.7.02): a 0x0d report is [R,G,B] only (the constructor's
    # parse strategy, ColorUtils.G(vb[0], vb[1], vb[2])) and its Kelvin bytes are never read; 0x15 and
    # the other colour sub-modes are "not support" (mode_color_parse "rgb_0d"). So a CCT the lamp
    # holds reads back as its colour: live `aa 05 0d ff ff ff 0a 8c` = RGB (255, 255, 255), no Kelvin.
    DeviceProfile(
        skus=("H6052",), capabilities=_LIGHT, color_scheme="h6006",
        encryption=Encryption.NONE, min_kelvin=2000, max_kelvin=9000, mode_color_parse="rgb_0d",
        scene_versions=frozenset({0, 1, 4, 5}), scene_dialect="B_h6052", readback="polled",
    ),
    # H6641 — RGBIC strip (goodsType 247): plaintext, 0x15 scheme, dialect-A scenes.
    # Source-confirmed: H61D3Support.f0(247) is false, so connect goes to connectBleSuc rather
    # than the afterConnected 0xAC path (built only for goodsType 263); the 0xAC status burst is
    # never dispatched (`ac 03 03 41 30 a5` returns zero frames, confirmed live). Colour
    # read-back is mechanism_a_direct (`aa a5 <group>`, Controller4ColorInfoByGroup), group
    # size 4. The piece count comes from a live 0x40 IC-count read, derived as the app does
    # (h61d3, Govee Home 7.5.12 names): VM4Light.C5 reads it with
    # ReadIcController.k.b(H61D3Support.q(247)) - bytes 0..1 as a signed short, clamped to
    # IntRange(41, 70) (ReadIcController.Companion.d) - and VM4Light.P5 walks
    # maxGroup = ceil(N / H61D3Support.r() = 4) groups for N = H61D3Support.e(247, IC) = ceil(IC / 5)
    # pieces. 7.7.02 is the same (H61D3Support.s / t, VM4Light.w5). The reply's third byte is
    # never used.
    # The mode parse (adjustNew/Info4Detail.parseModeValidBytes) knows only 0x15, 0x04, 0x0a and
    # music: a 0x0d report is not decoded, and 0x15 goes to a plain SubMode4Color, which reads the Kelvin
    # signed (getSignedShort; split_h61d3 installs no IParseStrategy; adjustNew/Info4Detail.java:117, per
    # the spec's cct_read_reply trace) (mode_color_parse "kelvin_15").
    # `polled` covers power/brightness/scene; not yet live-verified against hardware.
    DeviceProfile(
        skus=("H6641",), capabilities=_LIGHT | {_C.SEGMENTS}, color_scheme="h60a6",
        encryption=Encryption.NONE, segments=16, min_kelvin=2000, max_kelvin=9000,
        scene_versions=frozenset({0, 1, 2, 3, 10}), readback="polled",
        color_readback="mechanism_a_direct", color_readback_per_batch=4, color_readback_live_ic=True,
        color_readback_ic_range=(41, 70), color_readback_ic_per_piece=5, mode_color_parse="kelvin_15",
    ),
    # H6188 — legacy RGB strip (rgblight split, goodsType 0, SKU-string matched). Modeled from
    # source; not hardware-verified (no H6188 hardware). Source (Govee Home 7.7.02):
    #  - colour: 0x0d sub-mode (H6188BleProtocol.sub_mode_color=13); RGB = Bytes.f, CCT =
    #    Config.GM -> Bytes.A(kelvin, RGB_WHITE) = [0d, ff ff ff, kelvin u2be, tint] ("h6188").
    #  - an RGB pick equal to a Kelvin tint colour within [2000, 9000] K is sent as CCT with the "h6006"
    #    form `0d <picked RGB> <K> <first tint of K>`, NOT the white-slot Bytes.A form (Config.bu =
    #    KelvinConfig.i(c) ? Bytes.p : Bytes.f, 7.7.02); the slider keeps Bytes.A.
    #  - single colour piece (ColorPieceConfig.n -> 1): no segments.
    #  - kelvin: KelvinConfig P1 -> version 0 = [2000, 9000].
    #  - brightness: BrightnessConfig "H6188" -> version 0 = raw [20, 254] (not percent).
    #  - read-back: ControllerStrategyConfig.H0 polls aa 04 / aa 01 / aa 05. The mode report goes
    #    VM4Light$connectBleSuc$1.l -> A5 -> v5 (ModeParseStrategyConfig.a.a() for H6188) ->
    #    OpInfo4Detail.parseModeValidBytes, which decodes 0x15, 0x0b, 0x02 and 0x0d alike as
    #    [r,g,b, kelvin s16be, tint] and takes CCT only for a Kelvin > 0 (mode_color_parse
    #    "rgb_kelvin_tint", as the 0x0d bulbs).
    #  - scenes: the fixed Scenes4H6188 list, activated 33 05 04 <code> (DelegateSceneV1.P);
    #    nothing is uploaded, so scene_versions is empty.
    #  - no account-lock (ConnectDialog has no 0xB1/0xB2 step); cipher is runtime (BGC 2b12),
    #    plaintext by default.
    DeviceProfile(
        skus=("H6188",), capabilities=_LIGHT, color_scheme="h6188",
        encryption=Encryption.NONE, min_kelvin=2000, max_kelvin=9000,
        readback="polled", brightness_range=(20, 254), mode_color_parse="rgb_kelvin_tint",
    ),
    # H6062 — Glide wall light (striplight split, goodsType 41; pacts (1,1) (1,2) (2,1) (2,2) (3,1)
    # (4,1), Support.addSupportPact). Modeled from source; not hardware-verified (no H6062
    # hardware). Source (Govee Home 7.7.02):
    #  - frames: the 0x15 layout of "h60a6" (ColorControllerConfig.d -> Config.u().get(0) = Yt ->
    #    Bytes.h for colour, Bytes.r for CCT with the tint, Bytes.G for per-segment brightness);
    #    one detail screen (AcNewDetail) for every pact, so no pact takes a pre-0x15 path.
    #  - segments: the device's own count, `aa 41` -> [IC s16be, sectionNum u8]
    #    (ControllerIcSegmentNum, ControllerStrategyConfig.O; the app stores sectionNum with
    #    Info4BleIotDevice.P0 and ColorControllerConfig.c reads it back via J()). It is the mask
    #    width, the segment index bound and the `aa a5` group count (ceil(n/4)). `segments=33` is
    #    only the upper bound (Support.getBulbStringMaxNum: 19 for pacts (1,x)/(2,x), 28 for
    #    (3,1), 33 for (4,1)) used until the device has answered.
    #  - kelvin: [2000, 9000] (KelvinConfig version 0; no override for goodsType 41, and 7.7.02
    #    ships no LightCapabilityProvider implementation).
    #  - brightness: percent 1..100 (Support.getBrightnessRange; no BrightnessConfig entry).
    #  - power: 33 01 (ControllerSwitch), no zones.
    #  - read-back: polled; no 0xAC burst. Connect reads (ControllerStrategyConfig.O): versions,
    #    Wi-Fi versions/MAC, timers, wake-up, sleep, `aa 01`, `aa 04`, `aa 41`, `aa 05`. The
    #    colour report of `aa 05` uses the default SubMode4Color parse ([gradual, kelvin s16be]) for
    #    sub-modes 0x15, 0x0b, 0x02 and 0x0d alike (OpInfo4Detail.parseModeValidBytes; mode_color_parse
    #    "kelvin"), never the bulbs' [r, g, b, kelvin] 0x0d layout.
    #    Per-segment colour: `aa a5 <group 1..ceil(n/4)>` (Controller4ColorInfoByGroup).
    #  - scenes: cloud library, no striplight code of its own for goodsType 41 (NewDetailRegister
    #    register.13: ScenesConfigBean(0, 0, Support.supportScenesOpSet(41) = {0,1,2,3}), no view-model
    #    override, so the base SceneViewMode applies): SceneViewMode.Companion.getSceneComposeController
    #    -> ScenesOp.parseSceneV1 (dialect A, as H6047) for the upload, activate 33 05 04 <code u2le>
    #    (SubModeScenes.getWriteBytes). None of it depends on the segment count.
    #  - no account lock (plain ConnectDialog, no 0xB1/0xB2 step); cipher resolved at runtime.
    DeviceProfile(
        skus=("H6062",), capabilities=_LIGHT | {_C.SEGMENTS}, color_scheme="h60a6",
        encryption=Encryption.NONE, segments=33, min_kelvin=2000, max_kelvin=9000,
        scene_versions=frozenset({0, 1, 2, 3}), readback="polled",
        color_readback="mechanism_a_direct", color_readback_per_batch=4,
        section_count_read=True, mode_color_parse="kelvin",
    ),
    # H6061 — Glide Hexa light panels, phase 1: whole-device control only. Modeled from source; not
    # hardware-verified (no H6061 hardware). Source (Govee Home 7.7.02, split pact_h6061 =
    # com.govee.cubelight, goodsType 51, pact (1,1) / (1,2) / (1,3) in Support.addSupportPact):
    #  - panel count n: the size of the layout the user drew in the app (CubeColorModeV2.Y, from
    #    CubeDataSource), else the app's no-layout value 23 (ColorPieceConfig.f(51),
    #    Support.getBulbStringMaxNum(51)). The device never reports a count (no aa 40 / aa 41 read).
    #    `segments=23` is that default; Device.set_segment_count overrides it for a known layout.
    #  - RGB: ColorModeControllerConfig.k -> w -> l (Config.A[51] = 1 = group mode, not in Config.D)
    #    -> Bytes.m: ceil(n/4) frames `33 05 15 04 <group from 1> RGB x<=4` (23 panels: 6 frames,
    #    the last with 3 colours); colour set built by CubeColorModeV2.canClickColor.
    #  - CCT: Config.x[51] = 5 -> Config.J[5] = Config.Zt -> Bytes.u: `33 05 15 01 <tint RGB>
    #    <kelvin u2be> <mask>`, mask = BleUtils.X over ColorPieceConfig.k = 24 positions, first n
    #    set, LSB first (23 panels: `ff ff 7f`). Taken when the colour is a Kelvin tint and
    #    Config.I[51][4] (CCT) holds; tint = Constant.getTemColorByKelvin, Kelvin recovered by
    #    Constant.getColorTemKelvin (CubeColorModeV2.canClickColorTem).
    #  - an RGB pick is sent as that CCT frame when every panel gets one colour that is exactly one of
    #    the 178 Kelvin tint colours (Constant.k2 = ColorUtils.e, two per 100 K step) within the
    #    Kelvin range: ColorModeControllerConfig.k skips group mode when ColorUtils.q(colours) &&
    #    KelvinConfig.i(colour) && Config.I[51][4]. The wire carries the picked colour as the tint
    #    and Constant.getColorTemKelvin(colour) (build.rgb_as_kelvin, wire.cct.kelvin_for_tint).
    #  - kelvin: KelvinConfig has no 51 entry -> version 0 = [2000, 9000]. Brightness: percent 1..100
    #    (Support.getBrightnessRange {1, 100, 0}); power `33 01`.
    #  - read-back: CubeNewDetailsConfig.makeControllerStrategy polls aa 04 / aa 01 / aa 05; the 0x0d
    #    colour report is [R, G, B, kelvin s16be] (CubeNewInfo4Detail ctor SubMode4Color strategy,
    #    BleUtil.getSignedShort; kelvin > 0 means CCT) even though writes use 0x15. CubeVM4Light hands
    #    every aa 05 reply to CubeNewInfo4Detail.U, which decodes only mode bytes 13 (colour), 4, 10
    #    and 19 and logs any other, 0x15 included, as "is not support!", so a 0x15 report leaves the
    #    colour as it was (mode_color_parse "rgb_kelvin_0d"). After aa 05, CubeVM4Light.l5 reads
    #    `aa a5 <group>` for groups 1..ceil(n/4), Controller4ColorInfoByGroup = the library's
    #    mechanism_a_direct, group size 4.
    #  - no account-lock on control connects: 0xB2 is only sent by the pairing flows
    #    (ConnectDialog extends AbsConnectDialog4Secret, PairAcV1); cipher is runtime, plaintext
    #    by default.
    #  - phase 1 has no SEGMENTS (per-panel colour needs the user's layout) and no SCENES (cube
    #    scene activation and upload are not modelled yet).
    DeviceProfile(
        skus=("H6061",), capabilities=frozenset({_C.POWER, _C.BRIGHTNESS, _C.RGB, _C.COLOR_TEMP}),
        color_scheme="h6061", encryption=Encryption.NONE, segments=23,
        min_kelvin=2000, max_kelvin=9000, readback="polled",
        color_readback="mechanism_a_direct", color_readback_per_batch=4, mode_color_parse="rgb_kelvin_0d",
    ),
    # Plug family — power-only, AES + account-lock, relay encoding.
    # `plug` read-back polls the relay state (aa 01 -> relay bitmask, SwitchControllerV2.parseValidBytes
    # splits byte 0 into 8 per-outlet bits), so state.is_on reflects the device, not just the last
    # command. Which bits mean "on" is the detail screen's rule for the goodsType (AdjustAc.A2,
    # h5080/pact/Support.getPlugNum; Govee Home 7.5.12):
    #  - goodsType 43 (H5080, H5083, H5085; one outlet): bit 0 only. FrameV1 -> UiV1/UiV3 ->
    #    BleOpV1/BleOpV3.onEventSwitchV2 take `.get(0)`; the H5083 new detail screen
    #    (H5083NewDetailRepository.loadParser) reads `bArr[0] & 1`.
    #  - goodsType 50 (H5082) / 307 (H5089), two outlets: any of the 8 bits (BleOpV2.onEventSwitchV2 /
    #    BleOp4H5089.onEventSwitchV2 -> EventSwitchV2.i() ORs them all).
    #  - goodsType 90 (H5160, H5161): the first getPlugNum(90) = 3 bits (BleOpV4.Z ORs bits 0..2).
    # The advertised switch byte (BleUtil.parseBleBroadOnOff, scanRecord i+8) is decoded by the
    # device-list model, again per goodsType (advert_on_mask; Govee Home 7.5.12):
    #  - 43 and 90 use h5080 Model (SubLib keys Support.m = {43, 90}): Model.A reads
    #    `(b & 1) == 1`, but `(b & 0xFF) > 0` for goodsType 90 - so 90 is any bit here, not the
    #    read-back's 3 bits;
    #  - 50 and 307 use Model4Plug2 (SubLib4Plug2 keys Support.n = {50, 307}): Model4Plug2.A shows
    #    the device on when bit 0 or bit 1 (one per outlet) is set.
    # Not yet live-verified (secret-gated H5083): whether a reply ever carries the write encoding's
    # base nibble (0x10 = relay 0 off) needs hardware.
    DeviceProfile(
        skus=("H5080", "H5083", "H5085"),
        capabilities=frozenset({_C.POWER}), encryption=Encryption.AES_RC4_PSK,
        requires_secret=True, relay=True, readback="plug", relay_on_mask=0x01, advert_on_mask=0x01,
    ),
    # Whole-device power is each goodsType's main switch (Govee Home 7.7.02; source-modelled, not yet
    # verified on these plugs): gt 50 (H5082) and 90 (H5160 / H5161) send 33 01 FF / F0, every outlet
    # (h5080 UiV2.onOffChange:887-890, UiV4.onOffChange:765-768 -> SwitchControllerV2(!open, 15));
    # gt 307 (H5089) sends 33 02 01 / 00 (Ui4H5089.onOffChange:929-946 -> Controller4TotalSwitch). Not
    # modelled: after ON the H5089 app re-applies its cached night-light mode (BleOp4H5089).
    DeviceProfile(
        skus=("H5082",),
        capabilities=frozenset({_C.POWER}), encryption=Encryption.AES_RC4_PSK,
        requires_secret=True, relay=True, readback="plug", relay_on_mask=0xFF, advert_on_mask=0x03,
        relay_power="all_outlets",
    ),
    DeviceProfile(
        skus=("H5089",),
        capabilities=frozenset({_C.POWER}), encryption=Encryption.AES_RC4_PSK,
        requires_secret=True, relay=True, readback="plug", relay_on_mask=0xFF, advert_on_mask=0x03,
        relay_power="total_switch",
    ),
    DeviceProfile(
        skus=("H5160", "H5161"),
        capabilities=frozenset({_C.POWER}), encryption=Encryption.AES_RC4_PSK,
        requires_secret=True, relay=True, readback="plug", relay_on_mask=0x07, advert_on_mask=0xFF,
        relay_power="all_outlets",
    ),
)


def _index_by_sku(profiles: tuple[DeviceProfile, ...]) -> dict[str, DeviceProfile]:
    """``{SKU upper-cased: profile}`` for the profile table; GoveeBleInvalidValue if a SKU is listed
    twice (case-insensitive), which would otherwise resolve silently to whichever profile came last,
    or a profile combines fields the builders can't serve together (:func:`_incoherent`)."""
    by_sku: dict[str, DeviceProfile] = {}
    for p in profiles:
        problem = _incoherent(p)
        if problem is not None:
            raise GoveeBleInvalidValue(f"{_where(p)}: {problem}")
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
