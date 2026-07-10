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
    # Mechanism-A status read-back (0xAC -> 0xA5 groups), the SAME path as H60A6 — so
    # `status` populates power/brightness/zones + per-segment colours. Read-back group
    # count (getColorPieceSize=12) can exceed the write segment count (10); the parser
    # takes N from the reply, not a fixed count. Not yet live-verified on H6047.
    DeviceProfile(
        skus=("H6047",), capabilities=_LIGHT | {_C.SEGMENTS},
        color_scheme="h60a6", encryption=Encryption.NONE, segments=10,
        zones=(Zone("left", power_index=0, segments=tuple(range(0, 5))),
               Zone("right", power_index=1, segments=tuple(range(5, 10)))),
        scene_versions=frozenset({0, 1, 2, 3}), bar_switch=True,
        min_kelvin=2200, max_kelvin=6500, readback="status",
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
    # Mechanism A read-back (0xAC -> 0xA5 groups, Controller4ColorInfoByGroup) — the SAME
    # path as the H60A6, so per-segment control + read-back come for free via wire.reassemble.
    # Count is IC-driven (N = ceil(IC_count/5), read live via 0x40); `segments=16` is only the
    # whole-device mask width (16-bit) — read-back reports the actual group count. NOTE: wired
    # from the shared mechanism-A source; not yet live-verified on an H6641.
    DeviceProfile(
        skus=("H6641",), capabilities=_LIGHT | {_C.SEGMENTS}, color_scheme="h60a6",
        encryption=Encryption.NONE, segments=16, min_kelvin=2000, max_kelvin=9000,
        scene_versions=frozenset({0, 1, 2, 3, 10}), readback="status",
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
