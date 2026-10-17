meta:
  id: govee_scan_record
  title: Govee BLE scan record (advertising and scan-response AD structures)
  license: MIT
  ks-version: '0.11'
  imports:
    - govee_common
    - govee_manufacturer_data
  bit-endian: le
  endian: be
doc: |
  STANDALONE (other channel: a raw advertising or scan-response PDU's AD
  data, or Android's 62-byte scan record = advertising data + scan response,
  zero-padded, for tools and simulator emission; imports
  govee_manufacturer_data). The library itself reads bleak's split
  manufacturer data with govee_manufacturer_data directly.
  The walk is the one every BleUtil advert parser runs: while the index is
  below 62 it reads the length byte at the index and steps past that AD
  (length + 1 bytes), with no stop on length 0, so a 0 byte is a 1-byte pad
  and the walk runs to the end of the record (repeat eos). An AD whose length runs past the end is read with the bytes that
  are there (the app's loop ends there too, but for such a 0xFF AD of len
  >= 6 its parsers can index past the record and throw). Every byte is in a
  field, so any record re-writes byte-exactly.
  The app's further gates are instances: is_app_record_size here, and
  is_app_manufacturer_ad on the 0xFF payload.
  Other app walks: BleUtil.parseBleScanRecord (gateway models) stops at the
  first 0 byte and drops an AD that runs past the end; parseBleAdvertisement
  (H5140) stops at such an AD and keys the ADs by length.
  Firmware (the AD lists the analysed images send; which SKU sends what is
  behaviour data, not grammar):
  - H5083 advertising data: [03 19 00 00][08 FF 43 88 EC 00 02 02
    <outlet>]; scan response: the name 'ihoment_H5083_XXYY' (FUN_9b01e354,
    format 'ihoment_%s_%s%s').
  - H6047 advertising data: [11 09 'Govee_H6047_<addr1><addr0>'][08 FF 03 88
    EC 00 01 01 <power>] (FUN_01006748).
doc-ref:
  - base-classes12/com/govee/ble/scan/BleScanCallbackImp21.java:219-231
  - base-classes9/com/govee/base2home/pact/BleUtil.java:779-803
  - base-classes9/com/govee/base2home/pact/BleUtil.java:700-717
  - base-classes9/com/govee/base2home/pact/BleUtil.java:939-955
  - base-classes9/com/govee/base2home/pact/BleUtil.java:552-575
  - H5083 data @0x100007a6
  - H5083 FUN_9b01e354 @0x9b01e354
  - firmware-h6047/decompiled/H6047_decompiled.c:5649-5773
seq:
  - id: ad_structures
    type: ad_structure
    repeat: eos
instances:
  is_app_record_size:
    value: _io.size == 62
    doc: |
      every BleUtil advert parser returns 'no info' unless the record is
      exactly 62 bytes (scanRecord.length != 62)
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:780-781
      - base-classes9/com/govee/base2home/pact/BleUtil.java:701-702
types:
  ad_structure:
    doc: One AD [len][type][data]; len 0 is a 1-byte pad.
    seq:
      - id: len_ad
        type: u1
        doc: |
          length of ad_type + data; 0 = a 1-byte pad (the app's walk steps
          over it)
      - id: ad_type
        type: u1
        enum: ad_type
        if: len_ad > 0 and not _io.eof
        doc: absent for a pad, or when the record ends after len_ad
      - id: data
        size: >-
          _io.size - _io.pos < len_ad - 1 ? _io.size - _io.pos : len_ad - 1
        type:
          switch-on: ad_type
          cases:
            'ad_type::flags': flags_data
            'ad_type::uuid16_complete': uuid16_list
            'ad_type::complete_local_name': local_name
            'ad_type::manufacturer_specific': govee_manufacturer_data
        if: len_ad > 1 and not _io.eof
        doc: |
          the len_ad - 1 bytes after ad_type, or the bytes left when the record
          cuts the AD short. An AD type with no case is raw bytes.
  flags_data:
    doc: |
      AD type 0x01 (Bluetooth Core Specification Supplement A.1.3). Bits of
      `flags`: 0 LE Limited Discoverable, 1 LE General Discoverable, 2 BR/EDR
      Not Supported, 3-4 simultaneous LE and BR/EDR (controller, host). The
      app reads it only in the H512x matcher, which expects 02 01 06 as the
      record's first AD.
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:957-968
    seq:
      - id: flags
        type: u1
      - id: rest
        type: govee_common::echo
  uuid16_list:
    doc: |
      AD type 0x03, the complete list of 16-bit service UUIDs, little-endian.
      Govee thermo-hygrometers put the 88 EC marker here (UUID 0xEC88), which
      parseBleBroadcastPact4MultiTh and the H5140 parser look for before the
      0xFF AD (is_govee_marker).
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:813-822
      - base-classes9/com/govee/base2home/pact/BleUtil.java:156-169
    seq:
      - id: uuids
        type: u2le
        repeat: expr
        repeat-expr: _io.size / 2
      - id: rest
        type: govee_common::echo
        doc: an odd last byte
    instances:
      is_govee_marker:
        value: _io.size == 2 and uuids[0] == 0xec88
        doc: |
          the app's marker AD: type 3, length byte exactly 3, bytes 88 EC
        doc-ref:
          - base-classes9/com/govee/base2home/pact/BleUtil.java:813-818
          - base-classes9/com/govee/base2home/pact/BleUtil.java:162-166
  local_name:
    doc: |
      AD type 0x09, the complete local name, which carries the SKU
      (BaseBleProcessor.parse; the brand and prefix rules are library code).
      BaseBleProcessor.parse takes the BluetoothDevice name and, only when it
      is empty, BleUtil.parseBleName, which copies len_ad bytes from after the
      type byte (one byte past the AD) and trims chars <= 0x20
      (StrUtil.trim4String); the scan callback's SKU filter tries
      ScanRecord.getDeviceName first. Kaitai has no trim, so the client trims
      (PROCEDURAL).
    doc-ref:
      - base-classes9/com/govee/base2home/main/choose/BaseBleProcessor.java:242-264
      - base-classes12/com/govee/ble/scan/BleScanCallbackImp21.java:235-244
      - base-classes9/com/govee/base2home/pact/BleUtil.java:920-937
      - base-classes10/com/govee/base2home/util/StrUtil.java:596-598
    seq:
      - id: raw
        size-eos: true
        doc: the name bytes
    instances:
      text:
        value: raw.to_s('UTF-8')
        doc: |
          the name, UTF-8 (raises for bytes that are not UTF-8, but only when
          read, so the record still parses and re-writes)
enums:
  ad_type:
    0x01:
      id: flags
      doc: Flags
      doc-ref:
        - base-classes9/com/govee/base2home/pact/BleUtil.java:957-968
    0x03:
      id: uuid16_complete
      doc: Complete List of 16-bit Service UUIDs
      doc-ref:
        - base-classes9/com/govee/base2home/pact/BleUtil.java:813-822
    0x09:
      id: complete_local_name
      doc: Complete Local Name
      doc-ref:
        - base-classes9/com/govee/base2home/pact/BleUtil.java:920-937
    0x19:
      id: appearance
      doc: |
        FIRMWARE-SOURCED: Appearance; H5083 sends 03 19 00 00 (untyped)
      doc-ref:
        - H5083 data @0x100007a6
    0xff:
      id: manufacturer_specific
      doc: Manufacturer Specific Data (govee_manufacturer_data)
      doc-ref:
        - base-classes9/com/govee/base2home/pact/BleUtil.java:787
