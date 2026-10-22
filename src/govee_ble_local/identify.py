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

The app reads an advertisement two ways, and so does the library:
- The add-device scan identifies a device by its name (``BaseBleProcessor.parse``): :func:`identify`,
  :func:`from_advertisement`, and through them ``scanner.py``. A non-Govee name is not a Govee device.
- The light control screen of a bound device finds it by its stored MAC (``AbsViewMode4Op.s0``,
  ``AbsViewMode4OpWithOtaCheck.s2``; the name only when no MAC is stored) and parses the broadcast's pact
  block with the stored goodsType (``AbsViewMode4OpWithOtaCheck.j1`` ->
  ``GoodsType.parseBleBroadcastPactInfo``), whatever the name. ``AbsViewMode4OpWithOtaCheck.w2`` stores the
  encrypt flag per MAC (``BleBroadVersionCache.c`` / ``d``), and the cipher is chosen from it at connect
  (``EncryptionUtils.isEncryptionSupported``). A Device, built for a SKU its caller already knows, reads its
  advertisements that way (:func:`_bound_advertisement`).
- Other bound-device paths: the H5083 plug screen reads the flag the same way. The H5085 / H5089 plug screen
  (``BlePactV1.r``) also needs the stored name to match, and with a foreign name connects by MAC on the flag
  it remembered from earlier broadcasts. The light screen refuses a matched broadcast without a pact block
  (``AbsViewMode4Op.onScanEvent`` -> ``p0(999)``); the library uses the profile default then. The app
  re-learns the flag from every broadcast; a Device fixes its cipher when it is built.
- Group control (``GroupScanOpImp``) matches scan results by name; the library has no group control.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .const import BRAND_MARKERS, GBK_PREFIX, GV_LONG_PREFIXES, GV_PREFIX
from .wire import _spec

# The spec's ``govee_manufacturer_data`` (spec/govee_manufacturer_data.ksy) is the AD structure's
# payload ``[flags][company id u2le][pact_type u2be][pact_code][device_state]``, where the Govee marker
# 0x88 0xEC is the company id (``is_govee``); bleak splits that payload into the company id it hands
# over (``flags`` | the marker's first byte << 8) and the value after it. Every field is read only when
# it fits, so a payload of any length reads, and a short entry carries no pact (``has_pact``).
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

    sku: str                # e.g. "H5083" (from a Govee local name, else a bound device's caller's)
    name: str               # full local name (a bound device's may be any name, or "")
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
    """Each Govee entry of bleak's ``manufacturer_data``, read by the spec's ``govee_manufacturer_data``
    over the payload bleak split: ``company_id`` (2 bytes, little-endian) then the value. Entries
    without the Govee marker (``is_govee``) or with a company id that can't be two bytes are skipped."""
    for company_id, value in mfg.items():
        if not 0 <= company_id <= 0xFFFF:
            continue
        entry = _spec.read(_spec.ManufacturerData, company_id.to_bytes(2, "little") + bytes(value))
        if entry.is_govee:
            yield entry


def parse_manufacturer_data(mfg: dict[int, bytes]) -> tuple[bool, int, int, int] | None:
    """Parse Govee manufacturer data into its broadcast header fields.

    Returns ``(encrypted, broadcast_version, pact_type, pact_code)``, or None if it isn't a
    recognized Govee broadcast.

    `mfg` is bleak's AdvertisementData.manufacturer_data: {company_id: value}.
    The app parses the raw AD structure `[FF][hdr][markerHi][data...]`; from
    bleak's split form, company_id low byte == hdr and high byte == markerHi. Read by the spec's
    ``govee_manufacturer_data`` type (:func:`_govee_entries`); a broadcast version of 0 is not a pact
    broadcast (BleUtil.parseBleBroadcastPact).
    """
    for entry in _govee_entries(mfg):
        if not entry.has_pact:   # the pact fields fit and is_govee_pact: protocol_version >= 1
            continue
        return bool(entry.encrypted), int(entry.protocol_version), int(entry.pact_type), int(entry.pact_code)
    return None


def parse_broadcast_onoff(mfg: dict[int, bytes], *, on_mask: int | None = None) -> bool | None:
    """The device's on/off state from its advertisement — PASSIVE, no connection.

    Port of BleUtil.parseBleBroadOnOff + AbsBaseBleModel.z: the byte right after
    pactType(2)+pactCode(1) in the Govee mfg data (scanRecord i+8; bleak value
    index 4) == 1 means ON. Only a broadcast whose header byte (the company-id low byte, scanRecord
    i+2), read as a signed Java byte, is >= 2 carries it: a v0/v1 header (or one with bit 7 set) is
    skipped, and the app logs that the broadcast has no switch state. Returns True/False, or None if the advertisement doesn't carry it
    (not a Govee broadcast / header below 2 / too short).

    ``on_mask`` decodes the byte as a bitmask instead: ON when any of its bits is set. The plugs'
    device-list models do that (h5080 ``Model.z``, ``Model4Plug2.z``; see
    ``DeviceProfile.advert_on_mask``). Both decodes are the spec's: the entry's ``device_state`` read as a
    ``govee_state`` ``power_state`` from the advert (``source`` advert, ``is_on``), with the mask as the
    ``govee_profile`` ``power_rule.advert_on_mask`` (0 = a light)."""
    for entry in _govee_entries(mfg):
        # has_switch_state: a header byte >= 2 read signed (BleUtil.parseBleBroadOnOff:
        # (signed) scanRecord[i6 + 2] >= 2) and a byte after pact_code, its switch_byte.
        if not entry.has_switch_state:
            continue
        rule = _spec.profile(advert_on_mask=on_mask or 0).power
        return bool(_spec.read(_spec.State.PowerState, entry.device_state, _spec.State.Source.advert,
                               rule).is_on)
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


def _bound_advertisement(adv: object, fallback_name: str | None, sku: str) -> GoveeAdvertisement | None:
    """Read the advertisement of a device its caller has already identified as ``sku``, as the app's control
    screen reads a bound device's: by the Govee manufacturer data, whatever the local name.

    A Govee name gives exactly what :func:`from_advertisement` gives. Otherwise the first Govee pact block of
    the manufacturer data (:func:`parse_manufacturer_data`) is read, labelled with ``sku`` and the advertised
    name (else ``fallback_name``, else ""); None when there is none, as before. "First" is the dict's order,
    which is the app's first AD only while the dict holds one advertisement's entries; a merged record read
    under a Govee name has the same limit. The app's path is ``AbsViewMode4OpWithOtaCheck.s2`` / ``j1`` (see
    the module docstring): it doesn't read the name of a device it found by its stored MAC. Here the caller
    vouches that ``adv`` is the device's own, as the app's MAC match does. A non-Govee name reaches a Device
    this way when a scanner pins another device's advertisement on it: Home Assistant's merged record for an
    H6008 once carried a neighbour's name.

    Package-internal (not in ``__all__``): :func:`identify` and the scanner keep the add-device name gate."""
    found = from_advertisement(adv, fallback_name)
    if found is not None:
        return found
    parsed = parse_manufacturer_data(getattr(adv, "manufacturer_data", None) or {})
    if parsed is None:
        return None
    encrypted, version, pact_type, pact_code = parsed
    name = getattr(adv, "local_name", None) or fallback_name or ""
    return GoveeAdvertisement(sku.upper(), name, encrypted, version, pact_type, pact_code)
