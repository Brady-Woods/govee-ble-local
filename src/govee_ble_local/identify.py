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

from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from kaitaistruct import KaitaiStream

from ._generated.govee_advertisement import GoveeAdvertisement as _GA  # type: ignore[attr-defined]
from .const import BRAND_MARKERS, GBK_PREFIX, GV_LONG_PREFIXES, GV_PREFIX

# The spec-generated advertisement reader (spec/govee_adv.ksy), untyped. Its ``manufacturer_data`` is
# the AD structure's payload ``[flags][company id u2le][pact_type u2be][pact_code][rest]``, where the
# Govee marker 0x88 0xEC is the company id (``is_govee``); bleak splits that payload into the
# company id it hands over (``flags`` | the marker's first byte << 8) and the value after it.
_MFG: Any = _GA.ManufacturerData
# Value bytes the type needs after the company id: the marker's second byte, pact_type (2), pact_code.
_PACT_VALUE_LEN = 4
# pact_type / pact_code of a Govee-named advertisement whose manufacturer data has no parseable
# pact info (see GoveeAdvertisement.pact).
_NO_PACT = -1


@dataclass(frozen=True)
class GoveeAdvertisement:
    """What the app extracts from a Govee advertisement.

    A Govee-named advertisement without parseable pact info (no or foreign manufacturer data) is
    still identified, with ``pact_type`` and ``pact_code`` set to the sentinel -1 and
    ``broadcast_version`` 0; ``encrypted`` is then False only as a guess, not a reading. Use
    :attr:`pact` and :attr:`encryption_known` rather than testing the sentinel.
    """

    sku: str                # e.g. "H5083" (from the local name)
    name: str               # full local name
    encrypted: bool         # uses the encrypted command channel; meaningful only if encryption_known
    broadcast_version: int  # header low nibble (0 when the pact is unknown)
    pact_type: int          # protocol type (from mfg data); -1 when unknown, see `pact`
    pact_code: int          # protocol code (from mfg data); -1 when unknown, see `pact`

    @property
    def pact(self) -> tuple[int, int] | None:
        """``(pact_type, pact_code)`` as the app reads them (``BleUtil.parseBleBroadcastPact``), or None
        when the advertisement carried no parseable pact info."""
        if self.pact_type == _NO_PACT:
            return None
        return self.pact_type, self.pact_code

    @property
    def encryption_known(self) -> bool:
        """True when :attr:`encrypted` was read from the broadcast header; False when it is only the
        plaintext guess made for an advertisement without parseable pact info."""
        return self.pact is not None


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
    if any(m in name for m in BRAND_MARKERS) or name.startswith(GBK_PREFIX):
        parts = name.split("_")
        while parts and not parts[-1]:
            parts.pop()   # Java String.split("_") drops trailing empty strings (leading ones stay)
        if len(parts) == 3 and parts[1]:
            return parts[1].upper()
        return None
    if name.startswith(GV_LONG_PREFIXES):
        idx = name.find("_")
        if idx == -1:
            return name[2:7].upper() if len(name) >= 11 else None
        return name[2:idx].upper() or None
    if name.startswith(GV_PREFIX):
        return ("H" + name[2:6]).upper() if len(name) >= 10 else None
    return None


def _govee_entries(mfg: dict[int, bytes]) -> Iterator[Any]:
    """Each Govee entry of bleak's ``manufacturer_data``, read by the spec's ``manufacturer_data`` type
    over the payload bleak split: ``company_id`` (2 bytes, little-endian) then the value. Entries
    without the Govee marker (``is_govee``), too short for the pact fields, or with a company id that
    can't be two bytes are skipped."""
    for company_id, value in mfg.items():
        if not 0 <= company_id <= 0xFFFF or len(value) < _PACT_VALUE_LEN:
            continue
        entry = _MFG(KaitaiStream(BytesIO(company_id.to_bytes(2, "little") + bytes(value))))
        if entry.is_govee:
            yield entry


def parse_manufacturer_data(mfg: dict[int, bytes]) -> tuple[bool, int, int, int] | None:
    """Parse Govee manufacturer data into its broadcast header fields.

    Returns ``(encrypted, broadcast_version, pact_type, pact_code)``, or None if it isn't a
    recognized Govee broadcast.

    `mfg` is bleak's AdvertisementData.manufacturer_data: {company_id: value}.
    The app parses the raw AD structure `[FF][hdr][markerHi][data...]`; from
    bleak's split form, company_id low byte == hdr and high byte == markerHi. Read by the spec's
    ``manufacturer_data`` type (:func:`_govee_entries`); a broadcast version of 0 is not a pact
    broadcast (BleUtil.parseBleBroadcastPact).
    """
    for entry in _govee_entries(mfg):
        version = int(entry.protocol_version)
        if version == 0:
            continue
        return bool(entry.encrypted), version, int(entry.pact_type), int(entry.pact_code)
    return None


def parse_broadcast_onoff(mfg: dict[int, bytes], *, on_mask: int | None = None) -> bool | None:
    """The device's on/off state from its advertisement — PASSIVE, no connection.

    Port of BleUtil.parseBleBroadOnOff + AbsBaseBleModel.A: the byte right after
    pactType(2)+pactCode(1) in the Govee mfg data (scanRecord i+8; bleak value
    index 4) == 1 means ON. Only a broadcast whose header byte (the company-id low
    byte, scanRecord i+2), read as a signed Java byte, is >= 2 carries it: a v0/v1
    header (or one with bit 7 set) is skipped, and the app logs that the broadcast has
    no switch state. Returns True/False, or None if the advertisement doesn't carry it
    (not a Govee broadcast / header below 2 / too short).

    ``on_mask`` decodes the byte as a bitmask instead: ON when any of its bits is set. The plugs'
    device-list models do that (h5080 ``Model.A``, ``Model4Plug2.A``; see
    ``DeviceProfile.advert_on_mask``)."""
    for entry in _govee_entries(mfg):
        rest = bytes(entry.rest)   # the per-SKU state after pact_code; the switch byte is its first
        if not rest:
            continue
        if not 2 <= int(entry.flags) <= 0x7F:   # BleUtil.parseBleBroadOnOff: (signed) scanRecord[i6 + 2] >= 2
            continue
        return rest[0] == 1 if on_mask is None else bool(rest[0] & on_mask)
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
        # Name looks Govee but no parseable pact info: unknown pact, plaintext as a guess (the app
        # would fall through to legacy parsers). GoveeAdvertisement.pact is None for it.
        return GoveeAdvertisement(sku, name, encrypted=False, broadcast_version=0,
                                  pact_type=_NO_PACT, pact_code=_NO_PACT)
    encrypted, version, pact_type, pact_code = parsed
    return GoveeAdvertisement(sku, name, encrypted, version, pact_type, pact_code)


def from_advertisement(adv: object, fallback_name: str | None = None) -> GoveeAdvertisement | None:
    """Identify one advertisement: bleak's ``AdvertisementData``, or anything else carrying
    ``local_name`` / ``manufacturer_data`` (read defensively with ``getattr``, so a partial object or
    Home Assistant's service info is tolerated). An already-parsed :class:`GoveeAdvertisement` is
    returned as is.

    The name is the advertised local name, else ``fallback_name`` (the ``BLEDevice`` name). Without
    manufacturer data the device is identified by name alone, with an unknown pact
    (:attr:`GoveeAdvertisement.pact` None). None when it is not a Govee device."""
    if isinstance(adv, GoveeAdvertisement):
        return adv
    name = getattr(adv, "local_name", None) or fallback_name
    mfg = getattr(adv, "manufacturer_data", None) or {}
    return identify(name, mfg)
