meta:
  id: govee_advertisement
  title: Govee BLE advertisement (manufacturer-specific data) — passive identity & state
  endian: be
doc: |
  Connectionless parse of a BLE scan record. The app walks AD structures `[len][type][data]` and
  reads the Govee identity/state from the manufacturer-specific AD (type 0xFF). See
  GOVEE_BLE_GATT_PROTOCOL.md §19 and base-classes9/com/govee/base2home/pact/BleUtil.java:779-803
  (parseBleBroadcastPact). The root walk below is NOT the app's walk; scan_record models that one.

  NOTE: Govee's manufacturer-data layout is custom — a `flags` byte PRECEDES the `88 EC` marker
  (i.e. it is not a standard leading 2-byte company identifier). `manufacturer_data` therefore only
  makes sense when `is_govee` is true; for other vendors' 0xFF ADs the fields are meaningless.
  Termination (root walk only): a `len == 0` structure ends the walk below (zero padding). The app's walk
  instead steps over a 0 byte as a 1-byte pad and runs to the end of the 62-byte record (scan_record).

seq:
  - id: structures
    type: ad_structure
    repeat: until
    repeat-until: _.len == 0

types:
  ad_structure:
    seq:
      - id: len
        type: u1
        doc: length of (ad_type + data); 0 = end/padding
      - id: ad_type
        type: u1
        if: len > 0
      - id: data
        size: len - 1
        if: len > 0
        type:
          switch-on: ad_type
          cases:
            0xff: manufacturer_data

  # Standalone app-style walk (NOT the root `structures`, which stays repeat-until len == 0 for existing
  # readers). Construct it explicitly: GoveeAdvertisement.ScanRecord(KaitaiStream(BytesIO(record))).
  scan_record:
    doc: |
      A raw scan record walked the way every BleUtil advert parser walks it
      (base-classes9/com/govee/base2home/pact/BleUtil.java, e.g. parseBleBroadcastPact :779-803 and
      parseBleBroadOnOff :700-717): `while (i2 < 62) { len = scanRecord[i2]; ...; i2 += len + 1; }` with no
      stop on len 0, so a 0 byte is a 1-byte pad and the walk runs to the end of the record (repeat: eos).
      The root walk instead stops at the first len 0, so it misses an 0xFF AD in the scan-response half of
      a 62-byte Android record and raises on an unterminated one.
      The app's further gates are instances: is_app_record_size (the 62-byte record, :780-781) and
      scan_ad_structure.is_app_manufacturer_ad (AD len >= 6 and type 0xFF, :787). An AD whose length runs
      past the end of the record raises EndOfStreamError here; the app's loop ends there instead (i2 >= 62),
      except that for an AD of len >= 6 near the end it indexes past the record and throws.
    seq:
      - id: structures
        type: scan_ad_structure
        repeat: eos
    instances:
      is_app_record_size:
        value: '_io.size == 62'
        doc: "every BleUtil advert parser returns 'no info' unless the record is exactly 62 bytes (scanRecord.length != 62, BleUtil.java:780-781, :701-702)"
  scan_ad_structure:
    doc: "One AD [len][type][data] of scan_record. Same layout as ad_structure, but a 0xFF payload is read by manufacturer_data_lenient, so a short or foreign 0xFF AD cannot stop the walk (the app skips any AD it does not match)."
    seq:
      - id: len
        type: u1
        doc: length of (ad_type + data); 0 = a 1-byte pad (the app's walk steps over it)
      - id: ad_type
        type: u1
        if: len > 0
      - id: data
        size: len - 1
        if: len > 0
        type:
          switch-on: ad_type
          cases:
            0xff: manufacturer_data_lenient
    instances:
      is_app_manufacturer_ad:
        value: 'len >= 6 and ad_type == 0xff'
        doc: "the AD the app's parsers inspect: unsignedByte >= 6 && scanRecord[i2 + 1] == -1 (BleUtil.java:787, :707). A 0xFF AD of len 2..5 is skipped there"
  manufacturer_data_lenient:
    doc: |
      One manufacturer-specific (0xFF) AD payload read without failing on a short payload. Standalone: no
      existing reader uses it (scan_record's ADs do; a client can build it from bleak's split form, company
      id u2le + value, as it builds manufacturer_data). manufacturer_data's seq needs 6 bytes
      ([flags][company_id u2le][pact_type u2be][pact_code]) and raises EndOfStreamError on fewer, which is
      why a client gates the bleak value length (>= 4 bytes) before reading it. This type reads any length
      and exposes manufacturer_data as `data` only when it fits.
      App rule (BleUtil.parseBleBroadcastPact, base-classes9/com/govee/base2home/pact/BleUtil.java:779-803):
      an AD of len < 6 (payload < 5 bytes) is skipped (no pact, the walk goes on); version (flags & 15) 0 or
      a missing 0x88 0xEC marker is skipped too; otherwise the pact is returned. has_pact is that rule for
      every payload of 6+ bytes. For a payload of exactly 5 bytes (AD len 6) the app also accepts the pact,
      but reads pact_code from scanRecord[i2+7], the byte AFTER the AD (:795: the next AD's length byte or a
      pad byte), which neither this payload nor bleak's split value carries: has_pact is false there.
      A type for reading: `data` is a positional instance, which the read-write classes also write in _write.
    seq:
      - { id: payload, size-eos: true, doc: "the whole AD payload (flags first); reading it cannot fail" }
    instances:
      data:
        pos: 0
        type: manufacturer_data
        if: '_io.size >= 6'
        doc: "manufacturer_data over the same payload, read only when the payload holds its 6 seq bytes, so it never raises; null for a shorter payload. Its is_govee / encrypted / protocol_version / is_govee_pact / has_switch_state / switch_byte apply"
      is_govee:
        value: '_io.size >= 3 and payload[1] == 0x88 and payload[2] == 0xec'
        doc: "the 0x88 0xEC marker at payload bytes 1-2 (manufacturer_data.is_govee), false for a payload too short to hold it"
      has_pact:
        value: '_io.size >= 6 and data.is_govee_pact'
        doc: "the app accepts this AD's pact (version >= 1 and the marker, BleUtil.java:787-796) and the payload holds pact_type and pact_code (data.pact_type / data.pact_code). False for every payload the app skips, and for the 5-byte payload whose pact_code the app reads past the AD (see type doc)"
      has_switch_state:
        value: '_io.size >= 6 and data.has_switch_state'
        doc: "data.has_switch_state without the length gate (the switch byte is data.switch_byte)"
  manufacturer_data:
    doc: Govee manufacturer payload (valid only when is_govee).
    seq:
      - id: flags
        type: u1
        doc: bit6 (0x40) = encrypted; low nibble (0x0F) = protocol version (>= 1)
      - id: company_id
        type: u2le
        doc: bytes 0x88 0xEC on the wire => 0xEC88 for Govee
      - id: pact_type
        type: u2be
        doc: protocol type
      - id: pact_code
        type: u1
        doc: protocol code (sub-version)
      - id: rest
        size-eos: true
        doc: |
          DEVICE STATE — externally-keyed, NO universal layout (do not type as one struct).
          The identity parser (BleUtil.parseBleBroadcastPact:779-803) stops at pact_code; it never reads `rest`.
          Everything past pact_code is per-SKU state consumed by SKU-specific parsers at SKU-specific offsets
          (i2 = index of the AD's length byte; BleUtil = base-classes9/com/govee/base2home/pact/BleUtil.java):
            parseBleBroadOnOff      -> data[6] (scanRecord[i2+8]) = rest[0] (bleak value[4]); BleUtil.java:700-717
                                       (and the logging overload :719-744); also returns data[7]
                                       (scanRecord[i2+9]) = rest[1]. See has_switch_state / switch_byte.
            parseBleBroadDaySyncInfo-> data[7] (scanRecord[i2+9]) (:622-648)
            battery/charge parsers  -> data[7] (scanRecord[i2+9]) (parseBleBroadBatteryAndHighTem :598-620)
            parseBleBroadcastPact4MultiTh (:805-835) is a different record: the 0x88 0xEC marker sits in a
                                       preceding type-0x03 AD of len 3, and its 0xFF AD is [flags][pact_type
                                       u2be][pact_code] with no marker (scanRecord[i2+2..5]); not this type.
          The layout is selected by the SKU (resolved from pact_type+pact_code via the cloud/name table),
          not by any in-record discriminator. This is the same external-key-driven category as
          rgb_scene_value.rest: parametric per-SKU overlays are possible, but there is no single structure.
    instances:
      is_govee:
        value: company_id == 0xec88
      encrypted:
        value: (flags & 0x40) != 0
      protocol_version:
        value: flags & 0x0f
        doc: "flags low nibble (BleUtil.parseBleBroadcastPact: i4 = b2 & 15, base-classes9/com/govee/base2home/pact/BleUtil.java:790)"
      is_govee_pact:
        value: 'is_govee and protocol_version >= 1'
        doc: >-
          The app's pact gate: parseBleBroadcastPact returns the pact only for version (flags & 15) >= 1
          ('i4 == 1 || i4 >= 2') and the 0x88 0xEC marker (base-classes9/com/govee/base2home/pact/BleUtil.java:787-796;
          GoodsType.parseBleBroadcastPactInfo then accepts it, base-classes9/com/govee/base2home/pact/GoodsType.java:1551-1552).
          Length difference: the app inspects an AD of len >= 6 (payload >= 5 bytes, :787) and reads pact_code
          from scanRecord[i2+7], one byte PAST a len-6 AD (:795); this type needs a payload of 6 (AD len >= 7)
          and raises on a shorter one (see manufacturer_data_lenient).
      has_switch_state:
        value: 'is_govee and flags >= 2 and flags <= 0x7f and rest.size >= 1'
        doc: >-
          The advert carries the switch byte (BleUtil.parseBleBroadOnOff, base-classes9/com/govee/base2home/pact/BleUtil.java:700-717
          and :719-744): an AD of len >= 6 whose flags byte, read as a SIGNED Java byte, is >= 2 (so u1 2..0x7F;
          :707, :726-727), with the marker (:710, :730). No version-nibble or encrypted-bit test, so flags 0x41 carries
          it and 0x01 / 0xC2 do not. The app reads scanRecord[i2+8] (= rest[0]) even when that byte lies past a
          len-6 or len-7 AD; rest.size >= 1 keeps this to bytes inside the AD.
      switch_byte:
        value: rest[0]
        if: has_switch_state
        doc: >-
          The advert switch byte (scanRecord[i2+8], bleak value[4]): parseBleBroadOnOff returns it unsigned as
          bleBroadOnOff[1] (:711, :731); the per-SKU model decodes it, e.g. the h5080 plugs: goodsType 90 on
          for any bit, else bit 0 (split_plugnew/com/govee/h5080/pact/Model.java:254-266), and the 2-outlet
          Model4Plug2 bit 0 = outlet 0, bit 1 = outlet 1 (Model4Plug2.java:269-289). The app also returns
          rest[1] (bleBroadOnOff[2]), which no curated model reads.
