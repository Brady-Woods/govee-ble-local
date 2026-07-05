"""Advertisement parsing — port of ``BaseBleProcessor`` + ``BleUtil``.

Determines, from a BLE advertisement, whether a device is a Govee device, its
SKU, and its broadcast "pact" info (protocol type/code, broadcast version, and
crucially the **encrypt flag** that decides whether the device uses the
encrypted command channel).

Ported exactly from the app:
- Local-name prefixes gate Govee devices; modern names are ``<prefix>_<SKU>_<id>``.
- Manufacturer data: the company-id low byte is a header whose bit ``0x40`` is
  the encrypt flag and low nibble is the broadcast version; the company-id high
  byte + first data byte are the Govee marker ``{0x88, 0xEC}``; then
  ``pactType`` (2 bytes) and ``pactCode`` (1 byte). (BleUtil.parseBleBroadcastPact)
"""
from __future__ import annotations

from dataclasses import dataclass

from .const import LOCAL_NAME_PREFIXES

# BleUtil.f41010h — the 2-byte Govee marker (company-id high byte, first data byte).
_MARKER = (0x88, 0xEC)
_ENCRYPT_BIT = 0x40  # BleUtil: z5 = (headerByte & 0x40) != 0


@dataclass(frozen=True)
class GoveeAdvertisement:
    """What the app extracts from a Govee advertisement."""

    sku: str                # e.g. "H5083" (from the local name)
    name: str               # full local name
    encrypted: bool         # uses the encrypted command channel (handshake+session key)
    broadcast_version: int  # header low nibble
    pact_type: int          # protocol type (from mfg data)
    pact_code: int          # protocol code (from mfg data)


def sku_from_local_name(name: str) -> str | None:
    """Extract the SKU from a Govee local name.

    Modern: ``ihoment_H5083_A2D1`` / ``Govee_H61A8_631F`` -> middle segment.
    Legacy: ``GVH60A6XXXX`` -> the ``H....`` slice after ``GV``.
    """
    if not name:
        return None
    if any(name.startswith(p) for p in ("ihoment_", "Govee_", "Minger_", "GBK_")):
        parts = name.split("_")
        if len(parts) == 3 and parts[1]:
            return parts[1].upper()
        return None
    # Legacy no-underscore forms: GVH60A6..., GVR..., GV...
    for p in ("GVH", "GVR", "GV"):
        if name.startswith(p):
            rest = name[len(p) - 1 :] if p == "GVH" else name[len(p) :]
            # GVH -> keep the 'H'; take the SKU as the first 5 chars (H + 4).
            token = ("H" + name[len(p):]) if p == "GVH" else name[len(p):]
            return token[:5].upper() if token else None
    return None


def parse_manufacturer_data(mfg: dict[int, bytes]) -> tuple[bool, int, int, int] | None:
    """Parse Govee manufacturer data -> (encrypted, broadcast_version, pact_type,
    pact_code), or None if it isn't a recognized Govee broadcast.

    `mfg` is bleak's AdvertisementData.manufacturer_data: {company_id: value}.
    The app parses the raw AD structure `[FF][hdr][markerHi][data...]`; from
    bleak's split form, company_id low byte == hdr and high byte == markerHi.
    """
    for company_id, value in mfg.items():
        header = company_id & 0xFF
        marker_hi = (company_id >> 8) & 0xFF
        if len(value) < 4:
            continue
        if marker_hi != _MARKER[0] or value[0] != _MARKER[1]:
            continue
        version = header & 0x0F
        if version == 0:
            continue
        encrypted = (header & _ENCRYPT_BIT) != 0
        pact_type = (value[1] << 8) | value[2]  # getUnsignedInt(hi, lo)
        pact_code = value[3]
        return encrypted, version, pact_type, pact_code
    return None


def identify(name: str | None, mfg: dict[int, bytes]) -> GoveeAdvertisement | None:
    """Identify a Govee device from its advertised name + manufacturer data."""
    if not name or not any(name.startswith(p) for p in LOCAL_NAME_PREFIXES):
        return None
    sku = sku_from_local_name(name)
    if sku is None:
        return None
    parsed = parse_manufacturer_data(mfg)
    if parsed is None:
        # Name looks Govee but no parseable pact info — treat as plaintext,
        # unknown pact (the app would fall through to legacy parsers).
        return GoveeAdvertisement(sku, name, encrypted=False, broadcast_version=0, pact_type=-1, pact_code=-1)
    encrypted, version, pact_type, pact_code = parsed
    return GoveeAdvertisement(sku, name, encrypted, version, pact_type, pact_code)
