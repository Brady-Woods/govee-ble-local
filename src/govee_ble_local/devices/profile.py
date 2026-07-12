"""Data-driven device descriptors.

A ``DeviceProfile`` carries everything the capability-driven :class:`~.device.Device`
needs — replacing the v2 per-SKU subclasses. Values are spec-derived where the spec is
concrete (colour submode, scene_versions, kelvin) and hardware/Java-verified where the
spec was prose (segment counts, zones, scene dialect). The wire cipher is resolved at
connect from the advertisement, so ``encryption`` here is only the fallback default;
``requires_secret`` is the separate account-lock capability.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Capability, Encryption, Zone
from ..wire.build import ColorScheme

# scene dialects: "A" = library parseSceneV1 (comType from scene_versions);
# "B_h60a6" = H60A6 DIY (0x58; 0xA4-MTU graffiti / 0xA3 DIY by length gate);
# "B_h6052" = H6052 type-5 professional graffiti (commByte 9).
SceneDialect = str
ReadBack = str  # "status" (0xAC burst) | "polled" (aa 01/04/05) | "none"

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
    gradual: bool = False             # supports the 0xA3 gradual/fade-on-handoff flag (W/R)
    readback: ReadBack = "none"
    # Segment-colour read-back mechanism layered on top of `readback` (spec Change 7):
    # "" none | "mechanism_b" (H61A8 0xAA 0xA2/0xA5 per-batch) | "mechanism_c" (H6052 0x0D).
    color_readback: str = ""
    color_readback_per_batch: int = 3   # mechanism-B groups per batch frame (V2 = 3, V1 = 4)
    # mechanism-B batch-count divisor, when the READ-BACK piece count differs from the
    # WRITE/addressable `segments` count (both are per-SKU facts from devices.yaml — e.g. H6047:
    # write=10 getGoodsType4ColorSegment, read=12 getColorPieceSize). None -> falls back to
    # `segments`. Leave None where the true count is only knowable live (e.g. H6641: IC-driven,
    # ceil(IC/5) via a 0x40 read this library does not yet perform — an explicit approximation,
    # not a fix; see the H6641 profile comment).
    color_readback_segments: int | None = None


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
        min_kelvin=2700, max_kelvin=6500, readback="status",
    ),
    # H6047 — bar light: plaintext, two bars (combined 33 36), 10 segments, dialect-A scenes.
    # SOURCE-CONFIRMED (was wrongly modeled as mechanism-A): H6047 (goodsType 119) does NOT
    # dispatch the 0xAC status burst — that's gated to the h6038-family's *newdetail* SKUs
    # (288/277/298). H6047 reads per-segment colour via a DIRECT per-group request instead
    # (mechanism A-direct: proType 0xAA, `aa a5 <group>`, Controller4ColorInfoByGroup via
    # Compose4InfoBleIot, Support.isGoodsTypeH6047:177) — same decode as H61A8's mechanism_b.
    # `ac 03 03 41 30 a5` returns ZERO frames for this SKU; confirmed live. Read-back piece
    # count = getColorPieceSize = 12 (distinct from the write/addressable count = 10).
    # `polled` covers power/brightness/scene; not yet live-verified against real hardware.
    DeviceProfile(
        skus=("H6047",), capabilities=_LIGHT | {_C.SEGMENTS},
        color_scheme="h60a6", encryption=Encryption.NONE, segments=10,
        zones=(Zone("left", power_index=0, segments=tuple(range(0, 5))),
               Zone("right", power_index=1, segments=tuple(range(5, 10)))),
        scene_versions=frozenset({0, 1, 2, 3}), bar_switch=True,
        min_kelvin=2200, max_kelvin=6500, readback="polled",
        color_readback="mechanism_b", color_readback_per_batch=3, color_readback_segments=12,
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
    # Mechanism-C colour read-back (spec Change 7): the 0x05 sub-mode 0x0D report body is
    # [R,G,B], a single colour fanned across the 2 zones (H6052InfoDetail custom strategy).
    # Modeled from source; not yet live-verified (no H6052 hardware). `polled` covers
    # power/brightness/scene.
    DeviceProfile(
        skus=("H6052",), capabilities=_LIGHT, color_scheme="h6006",
        encryption=Encryption.NONE, min_kelvin=2000, max_kelvin=9000,
        scene_versions=frozenset({0, 1, 4, 5}), scene_dialect="B_h6052", readback="polled",
        color_readback="mechanism_c",
    ),
    # H6641 — RGBIC strip (goodsType 247): plaintext, 0x15 scheme, dialect-A scenes.
    # SOURCE-CONFIRMED (was wrongly modeled as mechanism-A): H61D3Support.f0(247)=false routes
    # connect to connectBleSuc (adjustNew/VM4Light.b0:875), NOT the afterConnected 0xAC path
    # (built only for goodsType 263) — so 247 never dispatches the 0xAC status burst.
    # `ac 03 03 41 30 a5` returns ZERO frames for this SKU; confirmed live. Colour read-back is
    # per-group DIRECT instead (mechanism A-direct: proType 0xAA, `aa a5 <group>`, after a 0x40
    # IC read) — same decode as H61A8's mechanism_b.
    # GAP (not fixed here): the true group count is IC-driven — ceil(IC_count/5) from a live
    # 0x40 read (H61D3Support.e(), d=5 resolved) — which this library does not yet perform.
    # `segments=16` is only the whole-device write-mask width, NOT the read-back count; using it
    # for mechanism_b's batch math (below, no color_readback_segments override) is an
    # APPROXIMATION until a 0x40 read is wired. `polled` covers power/brightness/scene; not yet
    # live-verified against real hardware.
    # SEPARATE, ALSO-OPEN GAP: `color_readback_per_batch` here is 3, matching the shared V2
    # `bulb_group_color_read_v2` reader (BulbStringColorControllerV2.f109046g — a HARDCODED,
    # non-parametric client constant in the ksy, NOT the "d=5" IC-math figure above; that "5" is
    # mechanism-A's unrelated group-count divisor and does NOT apply to this per-request reply
    # format). Whether H6641 actually uses this V2 controller (3 records/reply, same as H61A8)
    # or a *V3* variant (BulbStringColorControllerV3, mentioned but not modeled in the ksy,
    # possibly a different per-reply count/layout) is UNCONFIRMED — 3 is a placeholder
    # extrapolation, not a source-verified value for this SKU.
    DeviceProfile(
        skus=("H6641",), capabilities=_LIGHT | {_C.SEGMENTS}, color_scheme="h60a6",
        encryption=Encryption.NONE, segments=16, min_kelvin=2000, max_kelvin=9000,
        scene_versions=frozenset({0, 1, 2, 3, 10}), readback="polled",
        color_readback="mechanism_b", color_readback_per_batch=3,
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
    return _BY_SKU.get(sku.upper())


def supported_skus() -> list[str]:
    return sorted(_BY_SKU)
