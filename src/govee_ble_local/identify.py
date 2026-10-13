"""Advertisement parsing — port of ``BaseBleProcessor`` + ``BleUtil``.

Determines, from a BLE advertisement, whether a device is a Govee device, its
SKU, and its broadcast "pact" info (protocol type/code, broadcast version, and
crucially the **encrypt flag** that decides whether the device uses the
encrypted command channel).

Ported exactly from the app:
- The local name gates Govee devices and carries the SKU (:func:`sku_from_local_name`,
  BaseBleProcessor.parse); modern names are ``<brand>_<SKU>_<id>``.
- Manufacturer data: the company-id low byte is a header whose bit ``0x40`` is
  the encrypt flag and low nibble is the broadcast version; the company-id high
  byte + first data byte are the Govee marker ``{0x88, 0xEC}``; then
  ``pactType`` (2 bytes) and ``pactCode`` (1 byte). (BleUtil.parseBleBroadcastPact)
"""
from __future__ import annotations

from dataclasses import dataclass


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


# BaseBleProcessor.parse: these brand markers are matched ANYWHERE in the name (String.contains);
# GBK_/GVH/GVR/GV are prefixes (startsWith), tested in that order after the markers.
_BRAND_MARKERS = ("ihoment_", "Govee_", "Minger_")


def sku_from_local_name(name: str) -> str | None:
    """Extract the SKU from a Govee local name — a port of ``BaseBleProcessor.parse``.

    - name contains ``ihoment_`` / ``Govee_`` / ``Minger_``, or starts with ``GBK_``: split on
      ``_``; exactly 3 parts, SKU = the middle one (``ihoment_H5083_A2D1`` -> ``H5083``). (c(), d())
    - ``GVH`` / ``GVR`` with an ``_``: SKU = ``name[2:index('_')]`` (``GVH6188_1A2B`` -> ``H6188``);
      without one: needs >= 11 chars, SKU = ``name[2:7]`` (``GVH60A67457`` -> ``H60A6``). (e(), f())
    - otherwise ``GV``: needs >= 10 chars, SKU = ``"H" + name[2:6]`` (``GV61881A2B`` -> ``H6188``).
      (g())

    The SKU is upper-cased (the registry is case-insensitive); an empty SKU yields None.
    """
    if not name:
        return None
    if any(m in name for m in _BRAND_MARKERS) or name.startswith("GBK_"):
        parts = name.split("_")
        if len(parts) == 3 and parts[1]:
            return parts[1].upper()
        return None
    if name.startswith(("GVH", "GVR")):
        idx = name.find("_")
        if idx == -1:
            return name[2:7].upper() if len(name) >= 11 else None
        return name[2:idx].upper() or None
    if name.startswith("GV"):
        return ("H" + name[2:6]).upper() if len(name) >= 10 else None
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


def parse_broadcast_onoff(mfg: dict[int, bytes]) -> bool | None:
    """The device's on/off state from its advertisement — PASSIVE, no connection.

    Port of BleUtil.parseBleBroadOnOff + AbsBaseBleModel.A: the byte right after
    pactType(2)+pactCode(1) in the Govee mfg data (scanRecord i+8; bleak value
    index 4) == 1 means ON. Returns True/False, or None if the advertisement
    doesn't carry it (not a Govee broadcast / too short)."""
    for company_id, value in mfg.items():
        marker_hi = (company_id >> 8) & 0xFF
        if marker_hi != _MARKER[0] or len(value) < 5 or value[0] != _MARKER[1]:
            continue
        return value[4] == 1
    return None


def identify(name: str | None, mfg: dict[int, bytes]) -> GoveeAdvertisement | None:
    """Identify a Govee device from its advertised name + manufacturer data."""
    if not name:
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
