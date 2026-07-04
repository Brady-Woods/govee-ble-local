"""Modular device support: capability + scene definitions loaded from YAML.

A device is described by data (a ``device.yaml`` + ``scenes.yaml`` in a
``devices/<sku>/`` folder), not code, so adding a model is mostly authoring
config. Anything genuinely model-specific in the protocol is documented in
that folder's notes (and, rarely, handled with a code override elsewhere).

YAML is imported lazily so that ``govee_ble_local.protocol`` (packet
analysis) stays dependency-free — only loading a profile pulls in PyYAML.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import messages
from .const import ZONE_LOWER, ZONE_UPPER

if TYPE_CHECKING:
    # Only for type hints - this module deliberately stays free of the
    # bleak dependency at runtime (client.py is the only thing that needs it).
    from .client import GoveeBleClient

_LOGGER = logging.getLogger(__name__)

# Packaged device folders live next to this module.
_PACKAGED_DIR = Path(__file__).resolve().parent / "devices"
# Optional colon-separated extra search dirs for user-provided devices.
_USER_PATH_ENV = "GOVEE_BLE_LOCAL_DEVICE_PATH"


@dataclass(frozen=True)
class Scene:
    """One selectable scene/effect.

    ``code`` is the 16-bit scene id; ``param`` is the base64 ``scenceParam``
    upload blob (present -> a reliable full upload is possible; absent -> only
    bare activation, which needs the device to already have it cached).
    ``working=False`` marks scenes that don't render correctly over BLE.
    """

    name: str
    code: int
    param: str | None = None
    working: bool = True
    note: str | None = None

    @property
    def scene_id(self) -> tuple[int, int]:
        """Little-endian (low, high) byte pair used by the activate command."""
        return (self.code & 0xFF, (self.code >> 8) & 0xFF)


@dataclass(frozen=True)
class Capabilities:
    """What a device can do — used to generate the right entities/behavior."""

    brightness: bool = True
    rgb: bool = False
    color_temp: tuple[int, int] | None = None  # (min_kelvin, max_kelvin)
    zones: tuple[str, ...] = ()  # e.g. ("upper", "lower"); () = single-zone
    segments: int = 0  # 0 = no per-segment addressing
    scenes: bool = False


@dataclass(frozen=True)
class DeviceProfile:
    """Full support definition for one device model."""

    sku: str
    name: str
    local_name_prefixes: tuple[str, ...]
    capabilities: Capabilities
    protocol: messages.Protocol = messages.Protocol()
    scenes: tuple[Scene, ...] = ()
    notes: str | None = None
    source_dir: Path | None = None

    def matches_local_name(self, local_name: str | None) -> bool:
        if not local_name:
            return False
        return any(local_name.startswith(pfx) for pfx in self.local_name_prefixes)

    def scene_by_name(self, name: str) -> Scene | None:
        lowered = name.casefold()
        for scene in self.scenes:
            if scene.name.casefold() == lowered:
                return scene
        return None

    def selectable_scenes(self) -> list[Scene]:
        """Scenes safe to expose (working), sorted case-insensitively by name."""
        return sorted((s for s in self.scenes if s.working), key=lambda s: s.name.casefold())


_ZONE_NAME_TO_ID = {"upper": ZONE_UPPER, "lower": ZONE_LOWER}


async def set_power(client: "GoveeBleClient", profile: DeviceProfile, on: bool) -> None:
    """Turn a device fully on/off: per-zone if it has zones, else the global
    power opcode. Centralizes logic tools/device_test.py would otherwise
    duplicate across power_off()/_restore_neutral()."""
    if profile.capabilities.zones:
        for zone_name in profile.capabilities.zones:
            await client.set_zone(_ZONE_NAME_TO_ID[zone_name], on)
    else:
        await client.set_power(on)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml  # lazy: only needed when actually loading a profile

    with path.open("r", encoding="utf-8") as fh:
        loaded: dict[str, Any] = yaml.safe_load(fh) or {}
    return loaded


def _parse_capabilities(raw: dict[str, Any]) -> Capabilities:
    ct = raw.get("color_temp")
    color_temp = None
    if ct:
        color_temp = (int(ct["min_kelvin"]), int(ct["max_kelvin"]))
    return Capabilities(
        brightness=bool(raw.get("brightness", True)),
        rgb=bool(raw.get("rgb", False)),
        color_temp=color_temp,
        zones=tuple(raw.get("zones", ()) or ()),
        segments=int(raw.get("segments", 0)),
        scenes=bool(raw.get("scenes", False)),
    )


def _parse_protocol(raw: dict[str, Any]) -> messages.Protocol:
    """Build a messages.Protocol from a device.yaml `protocol:` section
    (absent/empty -> Protocol() defaults, today's H60A6-only behavior).
    Validation (is this a combination anything actually implements) happens
    inside Protocol.__post_init__ itself, so a bad device.yaml raises
    ValueError right here at load time - not confusingly mid-connection."""
    return messages.Protocol(
        encryption=raw.get("encryption", "aes_rc4_psk"),
        color_scheme=raw.get("color_scheme", "h60a6"),
        status_scheme=raw.get("status_scheme", "full"),
        power_scheme=raw.get("power_scheme", "binary"),
    )


def _parse_scenes(raw_list: list[dict[str, Any]]) -> tuple[Scene, ...]:
    scenes: list[Scene] = []
    for entry in raw_list:
        scenes.append(
            Scene(
                name=entry["name"],
                code=int(entry["code"]),
                param=entry.get("param"),
                working=bool(entry.get("working", True)),
                note=entry.get("note"),
            )
        )
    return tuple(scenes)


def load_profile(device_dir: str | os.PathLike[str]) -> DeviceProfile:
    """Load a DeviceProfile from a folder containing device.yaml (+ scenes.yaml)."""
    device_dir = Path(device_dir)
    definition = _load_yaml(device_dir / "device.yaml")

    scenes: tuple[Scene, ...] = ()
    scenes_ref = definition.get("scenes_file", "scenes.yaml")
    scenes_path = device_dir / scenes_ref
    if scenes_path.exists():
        scenes = _parse_scenes(_load_yaml(scenes_path).get("scenes", []))

    notes = None
    notes_ref = definition.get("notes")
    if notes_ref and (device_dir / notes_ref).exists():
        notes = (device_dir / notes_ref).read_text(encoding="utf-8")

    match = definition.get("match", {})
    return DeviceProfile(
        sku=definition["sku"],
        name=definition["name"],
        local_name_prefixes=tuple(match.get("local_name_prefixes", ()) or ()),
        capabilities=_parse_capabilities(definition.get("capabilities", {})),
        protocol=_parse_protocol(definition.get("protocol", {})),
        scenes=scenes,
        notes=notes,
        source_dir=device_dir,
    )


def _search_dirs() -> list[Path]:
    dirs = [_PACKAGED_DIR]
    extra = os.environ.get(_USER_PATH_ENV)
    if extra:
        dirs.extend(Path(p) for p in extra.split(os.pathsep) if p)
    return dirs


def available_skus() -> list[str]:
    """SKUs (folder names) discoverable in the packaged + user search dirs."""
    skus: list[str] = []
    for base in _search_dirs():
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if (child / "device.yaml").is_file():
                skus.append(child.name)
    return skus


def load_by_sku(sku: str) -> DeviceProfile | None:
    """Load a profile by SKU/folder name (case-insensitive), or None."""
    target = sku.casefold()
    for base in _search_dirs():
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.name.casefold() == target and (child / "device.yaml").is_file():
                return load_profile(child)
    return None


def match_local_name(local_name: str | None) -> DeviceProfile | None:
    """Find the packaged/user profile whose prefix matches a BLE local name."""
    if not local_name:
        return None
    for base in _search_dirs():
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not (child / "device.yaml").is_file():
                continue
            profile = load_profile(child)
            if profile.matches_local_name(local_name):
                return profile
    return None
