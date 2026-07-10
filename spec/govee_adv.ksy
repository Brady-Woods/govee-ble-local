meta:
  id: govee_advertisement
  title: Govee BLE advertisement (manufacturer-specific data) — passive identity & state
  endian: be
doc: |
  Connectionless parse of a BLE scan record. The app walks AD structures `[len][type][data]` and
  reads the Govee identity/state from the manufacturer-specific AD (type 0xFF). See
  GOVEE_BLE_GATT_PROTOCOL.md §19 and base2home/pact/BleUtil.java:829 (parseBleBroadcastPact).

  NOTE: Govee's manufacturer-data layout is custom — a `flags` byte PRECEDES the `88 EC` marker
  (i.e. it is not a standard leading 2-byte company identifier). `manufacturer_data` therefore only
  makes sense when `is_govee` is true; for other vendors' 0xFF ADs the fields are meaningless.
  Termination: a `len == 0` structure marks the end of meaningful data (zero padding).

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
          The identity parser (BleUtil.parseBleBroadcastPact:829) stops at pact_code; it never reads `rest`.
          Everything past pact_code is per-SKU state consumed by SKU-specific parsers at SKU-specific offsets:
            parseBleBroadOnOff      -> data[3] (scanRecord[i6+5])
            parseBleBroadDaySyncInfo-> data[7] (scanRecord[i6+9])
            battery/charge parsers  -> data[7] (scanRecord[i6+9])
            parseBleBroadcastPact4MultiTh -> data[6..8] (scanRecord[i6+8..10])
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
