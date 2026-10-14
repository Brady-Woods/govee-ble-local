"""Data-driven device descriptors.

A ``DeviceProfile`` carries everything the capability-driven :class:`~.device.Device`
needs for one SKU family. Values are spec-derived where the spec is
concrete (colour submode, scene_versions, kelvin) and hardware/Java-verified where the
spec was prose (segment counts, zones, scene dialect). The wire cipher is resolved at
connect from the advertisement, so ``encryption`` here is only the fallback default;
``requires_secret`` is the separate account-lock capability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, get_args

from ..models import Capability, Encryption, Zone
from ..wire.build import ColorScheme

# scene dialects: "A" = library parseSceneV1 (comType from scene_versions);
# "B_h60a6" = H60A6 DIY (0x58; 0xA4-MTU graffiti / 0xA3 DIY by length gate);
# "B_h6052" = H6052 type-5 professional graffiti (commByte 9).
SceneDialect = Literal["A", "B_h60a6", "B_h6052"]
# "status" (0xAC burst) | "polled" (aa 01/04/05) | "plug" (aa 01 relay bitmask) | "none"
ReadBack = Literal["status", "polled", "plug", "none"]
ColorReadBack = Literal["", "mechanism_b", "mechanism_a_direct"]

_C = Capability


@dataclass(frozen=True)
class DeviceProfile:
    skus: tuple[str, ...]
    capabilities: frozenset[Capability]
    color_scheme: ColorScheme = "h60a6"
    encryption: Encryption = Encryption.AES_RC4_PSK
    segments: int = 0
    zones: tuple[Zone, ...] = ()
    scene_versions: frozenset[int] = field(default_factory=frozenset)
    scene_dialect: SceneDialect = "A"
    min_kelvin: int | None = None
    max_kelvin: int | None = None
    requires_secret: bool = False
    relay: bool = False
    bar_switch: bool = False          # H6047: both bars in one 33 36 frame
    # Zoned fixture whose global 33 01 switch drives every zone (live-verified on H60A6
    # 2026-10-12: off clears both zones; on lights both even if each zone was off), so
    # whole-fixture power is ONE frame instead of one per zone.
    global_power: bool = False
    # Whole-device colour temperature is sent as ONE 0x15 CCT frame PER ZONE (in `zones` order)
    # instead of a single all-segments frame, when the device's advertised pact is
    # (pact_type, pact_code >= min_code). H60A6: pact_h60a0 Support.supportPartTem (pact type 1,
    # code >= 3) -> VM4LightH60A6.configColorStripController -> ControllerBuild.j sends the main
    # panel (mask bit 12) and then the ring (bits 0..11), each with its tint. None = single frame.
    cct_per_zone_min_pact: tuple[int, int] | None = None
    gradual: bool = False             # supports the 0xA3 gradual/fade-on-handoff flag (W/R)
    readback: ReadBack = "none"
    # Segment-colour read-back mechanism layered on top of `readback` (spec Change 7):
    # "" none | "mechanism_b" (H61A8 BulbGroupColorV2, 0xAA 0xA2/0xA5 per-batch, group size 3) |
    # "mechanism_a_direct" (H6047/H6641 Controller4ColorInfoByGroup, 0xAA 0xA5 per-group direct
    # request, group size 4 — a DIFFERENT controller from mechanism_b that happens to share the
    # 0xA5 wire opcode). 0x0d bulbs read colour/CCT from the polled 0x05 mode report instead.
    color_readback: ColorReadBack = ""
    color_readback_per_batch: int = 3   # group size per reply (mechanism_b V2=3, V1=4; mechanism_a_direct=4)
    # Static read-back piece count, when it differs from the WRITE/addressable `segments` count
    # (both per-SKU facts from devices.yaml — e.g. H6047: write=10 getGoodsType4ColorSegment,
    # read=12 getColorPieceSize). None -> falls back to `segments`. Ignored when
    # `color_readback_live_ic` is True (the live 0x40 read supplies the count instead).
    color_readback_segments: int | None = None
    # mechanism_a_direct only: read the live 0x40 IC count first and use the device's OWN
    # reported group count (ic_segment_read.segment) instead of a static color_readback_segments
    # — for SKUs whose true segmentation isn't knowable from the static device table (H6641).
    color_readback_live_ic: bool = False
    # Full-scale value of the brightness the device REPORTS (status TLV 0x04 /
    # aa 04 reply). Every curated SKU reports percent (all live read-backs are
    # 0..100); a 0..255 device would set 255 here. Writes are always percent.
    brightness_max: int = 100
    # Raw (min, max) brightness range for devices whose 33 04 / aa 04 byte is NOT a percent
    # (BrightnessConfig.Config version 0 = (20, 254), e.g. H6188). Writes map 1..100 onto it
    # and reads map back (devices/brightness.py, NumberUtil); overrides brightness_max.
    brightness_range: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        # Fail at import of the table, not silently at poll time, on a typo.
        for name, allowed in (
            ("color_scheme", ColorScheme),
            ("scene_dialect", SceneDialect),
            ("readback", ReadBack),
            ("color_readback", ColorReadBack),
        ):
            value = getattr(self, name)
            if value not in get_args(allowed):
                raise ValueError(
                    f"DeviceProfile{self.skus}: {name}={value!r} not in {get_args(allowed)}"
                )
        if self.cct_per_zone_min_pact is not None and not self.zones:
            raise ValueError(f"DeviceProfile{self.skus}: cct_per_zone_min_pact needs zones")
        if self.brightness_range is not None:
            lo, hi = self.brightness_range
            if not 0 <= lo < hi <= 0xFF:
                raise ValueError(f"DeviceProfile{self.skus}: brightness_range={self.brightness_range!r}")


_LIGHT = frozenset({_C.POWER, _C.BRIGHTNESS, _C.RGB, _C.COLOR_TEMP, _C.SCENES})

PROFILES: tuple[DeviceProfile, ...] = (
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
        # CCT: `15 01 ff ff ff <K> <tint> <mask>` (Bytes.r type 1); whole-device = main panel
        # frame then ring frame when pact >= (1, 3) (ControllerBuild.j, Support.supportPartTem).
        cct_per_zone_min_pact=(1, 3),
    ),
    # H6047 — bar light: plaintext, two bars (combined 33 36), 10 segments, dialect-A scenes.
    # Source-confirmed: H6047 (goodsType 119) does not dispatch the 0xAC status burst (that is
    # gated to the h6038-family newdetail SKUs); `ac 03 03 41 30 a5` returns zero frames for it
    # (confirmed live). Per-segment colour is read with direct per-group requests
    # (mechanism_a_direct: `aa a5 <group>`, Controller4ColorInfoByGroup), group size 4
    # (devices.yaml). Read-back piece count is getColorPieceSize = 12, distinct from the
    # write/addressable count of 10. `polled` covers power/brightness/scene. Not yet
    # live-verified against hardware.
    DeviceProfile(
        skus=("H6047",), capabilities=_LIGHT | {_C.SEGMENTS},
        color_scheme="h60a6", encryption=Encryption.NONE, segments=10,
        zones=(Zone("left", power_index=0, segments=tuple(range(0, 5))),
               Zone("right", power_index=1, segments=tuple(range(5, 10)))),
        scene_versions=frozenset({0, 1, 2, 3}), bar_switch=True,
        min_kelvin=2200, max_kelvin=6500, readback="polled",
        color_readback="mechanism_a_direct", color_readback_per_batch=4, color_readback_segments=12,
    ),
    # H61A8 — rope: plaintext, 0x0b scheme, 15 segments, no CCT, dialect-A scenes.
    # Mechanism-B per-segment colour read-back (spec Change 7): 0xAA 0xA5 (V2, adds
    # brightness) / 0xA2 (V1) per-batch frames, segment = (batch_seq-1)*per_batch+i.
    # 15 segments / 3-per-batch = 5 batches. Modeled from source; not yet live-verified
    # (no H61A8 hardware). `polled` covers power/brightness/scene.
    DeviceProfile(
        skus=("H61A8",),
        capabilities=frozenset({_C.POWER, _C.BRIGHTNESS, _C.RGB, _C.SEGMENTS, _C.SCENES}),
        color_scheme="h61a8", encryption=Encryption.NONE, segments=15,
        scene_versions=frozenset({0, 1, 2, 3}), readback="polled",
        color_readback="mechanism_b", color_readback_per_batch=3, gradual=True,
    ),
    # H6006 / H6008 — legacy bulbs: plaintext, 0x0d scheme, type-1 rgb scene upload (version {1}).
    DeviceProfile(
        skus=("H6006",), capabilities=_LIGHT, color_scheme="h6006",
        encryption=Encryption.NONE, min_kelvin=2000, max_kelvin=9000,
        scene_versions=frozenset({1}), readback="polled",
    ),
    DeviceProfile(
        skus=("H6008",), capabilities=_LIGHT, color_scheme="h6006",
        encryption=Encryption.NONE, min_kelvin=2700, max_kelvin=6500,
        scene_versions=frozenset({1}), readback="polled",
    ),
    # H6052 — table lamp: plaintext, 0x0d scheme; type-5 scenes via dialect B_h6052 (commByte 9).
    # Colour read-back comes from the polled 0x05 report like every 0x0d bulb: [R,G,B,kelvin u2be]
    # (live: `aa 05 0d ff ff ff 0a 8c` = CCT 2700 K).
    DeviceProfile(
        skus=("H6052",), capabilities=_LIGHT, color_scheme="h6006",
        encryption=Encryption.NONE, min_kelvin=2000, max_kelvin=9000,
        scene_versions=frozenset({0, 1, 4, 5}), scene_dialect="B_h6052", readback="polled",
    ),
    # H6641 — RGBIC strip (goodsType 247): plaintext, 0x15 scheme, dialect-A scenes.
    # Source-confirmed: H61D3Support.f0(247) is false, so connect goes to connectBleSuc rather
    # than the afterConnected 0xAC path (built only for goodsType 263); the 0xAC status burst is
    # never dispatched (`ac 03 03 41 30 a5` returns zero frames, confirmed live). Colour
    # read-back is mechanism_a_direct (`aa a5 <group>`, Controller4ColorInfoByGroup), group
    # size 4. The group count is read live via `color_readback_live_ic` (a 0x40 IC-count read
    # returns the device's own group count) instead of being derived client-side.
    # `polled` covers power/brightness/scene; not yet live-verified against hardware.
    DeviceProfile(
        skus=("H6641",), capabilities=_LIGHT | {_C.SEGMENTS}, color_scheme="h60a6",
        encryption=Encryption.NONE, segments=16, min_kelvin=2000, max_kelvin=9000,
        scene_versions=frozenset({0, 1, 2, 3, 10}), readback="polled",
        color_readback="mechanism_a_direct", color_readback_per_batch=4, color_readback_live_ic=True,
    ),
    # H6188 — legacy RGB strip (rgblight split, goodsType 0, SKU-string matched). Modeled from
    # source; not hardware-verified (no H6188 hardware). Source (Govee Home 7.7.02):
    #  - colour: 0x0d sub-mode (H6188BleProtocol.sub_mode_color=13); RGB = Bytes.f, CCT =
    #    Config.GM -> Bytes.A(kelvin, RGB_WHITE) = [0d, ff ff ff, kelvin u2be, tint] ("h6188").
    #  - single colour piece (ColorPieceConfig.n -> 1): no segments.
    #  - kelvin: KelvinConfig P1 -> version 0 = [2000, 9000].
    #  - brightness: BrightnessConfig "H6188" -> version 0 = raw [20, 254] (not percent).
    #  - read-back: ControllerStrategyConfig.H0 polls aa 04 / aa 01 / aa 05; the 0x0d report is
    #    ModeParseStrategyConfig.a ([r,g,b, kelvin u2be, tint]; kelvin != 0 => CCT).
    #  - scenes: the fixed Scenes4H6188 list, activated 33 05 04 <code> (DelegateSceneV1.P);
    #    nothing is uploaded, so scene_versions is empty.
    #  - no account-lock (ConnectDialog has no 0xB1/0xB2 step); cipher is runtime (BGC 2b12),
    #    plaintext by default.
    DeviceProfile(
        skus=("H6188",), capabilities=_LIGHT, color_scheme="h6188",
        encryption=Encryption.NONE, min_kelvin=2000, max_kelvin=9000,
        readback="polled", brightness_range=(20, 254),
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
    #    colour report is [R, G, B, kelvin u2be] (CubeNewInfo4Detail ctor parse strategy; kelvin > 0
    #    means CCT) even though writes use 0x15 - the 0x15 sub-mode is not parsed
    #    (CubeNewInfo4Detail.U). After aa 05, CubeVM4Light.l5 reads `aa a5 <group>` for groups
    #    1..ceil(n/4), Controller4ColorInfoByGroup = the library's mechanism_a_direct, group size 4.
    #  - no account-lock on control connects: 0xB2 is only sent by the pairing flows
    #    (ConnectDialog extends AbsConnectDialog4Secret, PairAcV1); cipher is runtime, plaintext
    #    by default.
    #  - phase 1 has no SEGMENTS (per-panel colour needs the user's layout) and no SCENES (cube
    #    scene activation and upload are not modelled yet).
    DeviceProfile(
        skus=("H6061",), capabilities=frozenset({_C.POWER, _C.BRIGHTNESS, _C.RGB, _C.COLOR_TEMP}),
        color_scheme="h6061", encryption=Encryption.NONE, segments=23,
        min_kelvin=2000, max_kelvin=9000, readback="polled",
        color_readback="mechanism_a_direct", color_readback_per_batch=4,
    ),
    # Plug family — power-only, AES + account-lock, relay encoding.
    # `plug` read-back polls the relay state (aa 01 -> raw relay bitmask; any bit set = on),
    # so state.is_on reflects the device, not just the last command. Not yet live-verified
    # (secret-gated H5083); the aa-01-vs-plug-spec answer + bit->on/off mapping need hardware.
    DeviceProfile(
        skus=("H5080", "H5082", "H5083", "H5085", "H5089", "H5160", "H5161"),
        capabilities=frozenset({_C.POWER}), encryption=Encryption.AES_RC4_PSK,
        requires_secret=True, relay=True, readback="plug",
    ),
)

_BY_SKU: dict[str, DeviceProfile] = {sku.upper(): p for p in PROFILES for sku in p.skus}


def profile_for(sku: str) -> DeviceProfile | None:
    """The profile for ``sku`` (case-insensitive), or None if unsupported."""
    return _BY_SKU.get(sku.upper())


def supported_skus() -> list[str]:
    """All supported SKUs, sorted."""
    return sorted(_BY_SKU)
