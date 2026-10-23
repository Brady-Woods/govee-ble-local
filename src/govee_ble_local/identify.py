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
  encrypt flag per MAC (``BleBroadVersionCache.c`` / ``d``), and the cipher is chosen from it on every
  frame read, written or notified (``EncryptionUtils.isEncryptionSupported``, from
  ``EncryptionBluetoothGattCallback`` and ``EncryptWriter.encryptWriteCharacteristic:42``); the screen
  writes it from the scan just before it connects (``AbsViewMode4Op.onScanEvent``), so in practice it holds
  for a session. A Device, built for a SKU its caller already knows, reads its advertisements that way,
  through :func:`bound_advertisement`, which a caller with no Device can call too.
- Other bound-device paths: the H5083 plug screen reads the flag the same way. The H5085 / H5089 plug screen
  (``BlePactV1.r``) also needs the stored name to match, and with a foreign name connects by MAC on the flag
  it remembered from earlier broadcasts. The light screen refuses a matched broadcast without a pact block
  (``AbsViewMode4Op.onScanEvent`` -> ``p0(999)``); the library uses the profile default then. The app
  re-learns the flag from every broadcast (``BleBroadVersionCache.d``); a Device re-learns it from each
  advertisement it ingests, for its next session, when every Govee pact entry agrees (:func:`_pact_entries`).
- Merged records: the app parses one broadcast at a time and takes its first Govee AD, for the pact
  (``AbsBaseBleModel.y`` -> ``GoodsType.parseBleBroadcastPactInfo``) and for the on/off (``AbsBaseBleModel.z``
  -> ``BleUtil.parseBleBroadOnOff``), both from each broadcast (``AbsBleItem.B``). A scanner's record can hold
  several Govee entries, so a Device learns the pact and the flag only when every pact entry agrees
  (:func:`_pact_entries`), and reads the on/off from the entry that last changed (:func:`_latest_govee_key`).
- Group control (``GroupScanOpImp``) matches scan results by name; the library has no group control.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Any

from bleak.backends.scanner import AdvertisementData

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

    sku: str                # e.g. "H5083" (from a Govee local name; bound_advertisement's is its caller's)
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


def _govee_entries(mfg: dict[int, bytes]) -> Iterator[tuple[int, Any]]:
    """Each Govee entry of bleak's ``manufacturer_data`` with its company id, in the dict's order, read by the
    spec's ``govee_manufacturer_data`` over the payload bleak split: ``company_id`` (2 bytes, little-endian)
    then the value. Entries without the Govee marker (``is_govee``) or with a company id that can't be two
    bytes are skipped."""
    for company_id, value in mfg.items():
        if not 0 <= company_id <= 0xFFFF:
            continue
        entry = _spec.read(_spec.ManufacturerData, company_id.to_bytes(2, "little") + bytes(value))
        if entry.is_govee:
            yield company_id, entry


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
    for _, entry in _govee_entries(mfg):
        if not entry.has_pact:   # the pact fields fit and is_govee_pact: protocol_version >= 1
            continue
        return bool(entry.encrypted), int(entry.protocol_version), int(entry.pact_type), int(entry.pact_code)
    return None


def _pact_entries(mfg: dict[int, bytes]) -> dict[int, tuple[bool, tuple[int, int]]]:
    """The encrypt flag and the ``(pact_type, pact_code)`` of every Govee pact entry of ``mfg``, by its header
    byte (the spec's ``flags``), in the dict's order: the entries :func:`parse_manufacturer_data` would
    consider, all of them.

    The app reads one broadcast and takes its first pact entry (``BleUtil.parseBleBroadcastPact:785-796``).
    A scanner's record can hold more than one: Home Assistant's (habluetooth, and BlueZ before it) keeps every
    manufacturer-data key it has heard for the address, each with its latest value. The header is part of the
    key, so a device whose header changed, or another device's frame merged under its address, leaves two
    Govee entries. Two entries never share a header (the key's high byte is the marker's 0x88).

    Package-internal: :class:`~govee_ble_local.devices.device.Device` re-learns its cipher and its pact from
    it, each only when every entry agrees on it."""
    return {
        int(entry.flags): (bool(entry.encrypted), (int(entry.pact_type), int(entry.pact_code)))
        for _, entry in _govee_entries(mfg) if entry.has_pact
    }


def _govee_record(mfg: dict[int, bytes]) -> dict[int, bytes]:
    """``mfg`` cut to its Govee entries (:func:`_govee_entries`): company id to value, in the dict's order.

    Package-internal: a Device keeps the last one it ingested, to tell which entry changed
    (:func:`_latest_govee_key`)."""
    return {company_id: bytes(mfg[company_id]) for company_id, _ in _govee_entries(mfg)}


def _latest_govee_key(record: dict[int, bytes], previous: dict[int, bytes], latest: int | None) -> int | None:
    """The company id of the Govee entry the latest broadcast carried, as far as two successive records show
    it: the first entry of ``record`` (a :func:`_govee_record`) whose value differs from the one ``previous``
    held under the same id; else ``latest``, the one found before (None when none has been).

    The app reads the latest broadcast's own Govee AD (``AbsBleItem.B``). Home Assistant's record (habluetooth
    ``base_scanner._async_on_advertisement_internal``, as BlueZ) merges each frame into the previous record,
    ``{**prev, **new}``: a key keeps its first slot and holds the value of the last frame that carried it,
    and a stale key (the header before an OTA) or a foreign one (another device's frame merged under the
    address) stays frozen at its last value. So only an entry whose value changed is known to come from the
    latest frame. A key ``previous`` lacks doesn't count: another device's frame merged once, or a key another
    scanner's record brings back, would take over for good. An OTA's new header counts from its first
    change, which on the modelled images is its first power change (the switch byte is the only state byte
    that moves). A foreign key that does change (another device's frame filed under the address again, with
    another value) takes over until the device's own entry next changes, since its identical frames fire no
    callback. That is a known limitation of the proxy-path mis-attribution (habluetooth#645), kept by
    decision.

    Package-internal: ``Device.advertised_on`` reads the on/off from this entry."""
    for company_id, value in record.items():
        before = previous.get(company_id)
        if before is not None and before != value:
            return company_id
    return latest


def parse_broadcast_onoff(mfg: dict[int, bytes], *, on_mask: int | None = None) -> bool | None:
    """The device's on/off state from its advertisement — PASSIVE, no connection.

    Port of BleUtil.parseBleBroadOnOff + AbsBaseBleModel.z: the byte right after
    pactType(2)+pactCode(1) in the Govee mfg data (scanRecord i+8; bleak value
    index 4) == 1 means ON. Only a broadcast whose header byte (the company-id low
    byte, scanRecord i+2), read as a signed Java byte, is >= 2 carries it: a v0/v1
    header (or one with bit 7 set) is skipped, and the app logs that the broadcast has
    no switch state. Returns True/False, or None if the advertisement doesn't carry it
    (not a Govee broadcast / header below 2 / too short).

    ``on_mask`` decodes the byte as a bitmask instead: ON when any of its bits is set. The plugs'
    device-list models do that (h5080 ``Model.z``, ``Model4Plug2.z``; see
    ``DeviceProfile.advert_on_mask``). ``Device.advertised_on`` applies the device's own mask.

    The first Govee entry that carries the byte is read, as the app reads one broadcast. A merged record's
    first entry may be a stale one; ``Device.advertised_on`` hands it the entry that last changed
    (:func:`_latest_govee_key`)."""
    for _, entry in _govee_entries(mfg):
        # has_switch_state: a header byte >= 2 read signed (BleUtil.parseBleBroadOnOff:
        # (signed) scanRecord[i6 + 2] >= 2) and a byte after pact_code, its switch_byte.
        if not entry.has_switch_state:
            continue
        switch = int(entry.switch_byte)
        return switch == 1 if on_mask is None else bool(switch & on_mask)
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


def bound_advertisement(
    advertisement_data: AdvertisementData | GoveeAdvertisement | None,
    sku: str,
    *,
    fallback_name: str | None = None,
) -> GoveeAdvertisement | None:
    """Read the advertisement of a device its caller has already identified as ``sku``, as the app's light
    control screen reads a bound device's: by the Govee manufacturer data, whatever the local name.

    This is the read :func:`~govee_ble_local.create_device` and the ``Device`` make of the advertisement they
    are given, so its :attr:`~GoveeAdvertisement.pact` is the pact ``create_device`` routes the profile by,
    and its :attr:`~GoveeAdvertisement.encrypted` (when :attr:`~GoveeAdvertisement.encryption_known`) is the
    flag that picks the cipher. A caller with no Device yet uses it to see what ``create_device`` would read,
    e.g. the pact it refused with ``GoveeBleNotSupported``.

    The result's ``sku`` is always ``sku`` (upper-cased), the device the caller vouches for, whatever SKU a
    Govee name carries; its ``name`` is the advertised local name, else ``fallback_name`` (the ``BLEDevice``
    name, as ``create_device`` passes it), else "". Neither changes the pact or the flag. Under a Govee name
    the pact, the flag and the name are what :func:`from_advertisement` reads. Otherwise they come from the
    first Govee pact block of the manufacturer data (:func:`parse_manufacturer_data`); None when there is none
    (a Govee name without one gives :attr:`~GoveeAdvertisement.pact` None instead).

    On a record with more than one Govee pact entry, the first in the dict's order gives the pact and the
    flag, as ``create_device`` and the Device constructor take them. That is the app's first pact AD
    (``BleUtil.parseBleBroadcastPact:779-803`` reads one broadcast and returns the first) only while the dict
    holds one advertisement's entries. A scanner's merged record keeps every manufacturer-data key it has
    heard for the address, and the header byte is part of the key, so after a header change the stale entry
    comes first. A Device re-learns its cipher and its pact from a later advertisement only when every Govee
    pact entry agrees on each (:meth:`.Device.ingest_advertisement`, :func:`_pact_entries`); this read and
    the constructor's stay on the first entry.

    App basis, for the light goodsTypes: the control screen finds a bound device by its stored MAC, not its
    name (``AbsViewMode4OpWithOtaCheck.s2:766-774``), and parses the broadcast with the stored goodsType
    (``AbsViewMode4OpWithOtaCheck.j1:570-591`` -> ``GoodsType.parseBleBroadcastPactInfo:1564-1578`` ->
    ``BleUtil.parseBleBroadcastPact``), keeping the encrypt flag (``AbsViewMode4OpWithOtaCheck.w2:171-178``);
    ``sku`` stands for that stored device. The plug screens are in the module docstring. The caller vouches
    that ``advertisement_data`` is the device's own, as the app's MAC match does. A non-Govee name reaches a
    Device this way when a scanner pins another device's advertisement on it: Home Assistant's merged record
    for an H6008 once carried a neighbour's name.

    Discovery doesn't use it: :func:`identify` and the scanner keep the add-device name gate."""
    found = from_advertisement(advertisement_data, fallback_name)
    if found is not None:
        return replace(found, sku=sku.upper())   # the bound device's, as the app's stored record labels it
    parsed = parse_manufacturer_data(getattr(advertisement_data, "manufacturer_data", None) or {})
    if parsed is None:
        return None
    encrypted, version, pact_type, pact_code = parsed
    name = getattr(advertisement_data, "local_name", None) or fallback_name or ""
    return GoveeAdvertisement(sku.upper(), name, encrypted, version, pact_type, pact_code)
