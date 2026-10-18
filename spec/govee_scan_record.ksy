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
  STANDALONE (other channel: raw AD data, or Android's zero-padded 62-byte
  scan record). Its AD structures, walked to the end as the app walks them.
  FIRMWARE-SOURCED: H5083 adv [03 19 00 00][08 FF 43 88 EC 00 02 02 <outlet>].
  FIRMWARE-SOURCED: H6047 adv [11 09 'Govee_H6047_<addr1><addr0>'][08 FF ...].
doc-ref:
  - base-classes12/com/govee/ble/scan/BleScanCallbackImp21.java:219-231
  - H5083 data @0x100007a6
  - firmware-h6047/decompiled/H6047_decompiled.c:5649-5773
seq:
  - id: ad_structures
    type: ad_structure
    repeat: eos
instances:
  is_app_record_size:
    value: _io.size == 62
    doc: the app's advert parsers read only a record of exactly 62 bytes
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:780-781
      - base-classes9/com/govee/base2home/pact/BleUtil.java:701-702
types:
  ad_structure:
    doc: One AD [len][type][data]; len 0 is a 1-byte pad.
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:779-803
    seq:
      - id: len_ad
        type: u1
        doc: length of ad_type + data
      - id: ad_type
        type: u1
        enum: ad_type
        if: len_ad > 0 and not _io.eof
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
        doc: cut short when the record ends inside the AD
  flags_data:
    doc: |
      AD type 0x01 (Core Spec Supplement A.1.3); the app checks only 02 01 06.
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:957-968
    seq:
      - id: flags
        type: u1
        doc: bit 0 LE Limited, 1 LE General Discoverable, 2 no BR/EDR
      - id: rest
        type: govee_common::echo
  uuid16_list:
    doc: |
      AD type 0x03, 16-bit service UUIDs (u2le); thermo-hygrometers put the
      88 EC marker here instead of in their 0xFF AD.
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
        doc-ref:
          - base-classes9/com/govee/base2home/pact/BleUtil.java:813-818
          - base-classes9/com/govee/base2home/pact/BleUtil.java:162-166
  local_name:
    doc: |
      AD type 0x09, the complete local name, which carries the SKU.
      PROCEDURAL: the client trims chars <= 0x20 (Kaitai has no trim).
      FIRMWARE-SOURCED: H5083's scan response name is 'ihoment_H5083_XXYY'.
    doc-ref:
      - base-classes9/com/govee/base2home/main/choose/BaseBleProcessor.java:242-264
      - base-classes10/com/govee/base2home/util/StrUtil.java:596-598
      - H5083 FUN_9b01e354 @0x9b01e354
    seq:
      - id: raw
        size-eos: true
    instances:
      text:
        value: raw.to_s('UTF-8')
enums:
  ad_type:
    0x01:
      id: flags
      doc-ref:
        - base-classes9/com/govee/base2home/pact/BleUtil.java:957-968
    0x03:
      id: uuid16_complete
      doc-ref:
        - base-classes9/com/govee/base2home/pact/BleUtil.java:813-822
    0x09:
      id: complete_local_name
      doc-ref:
        - base-classes9/com/govee/base2home/pact/BleUtil.java:920-937
    0x19:
      id: appearance
      doc: |
        FIRMWARE-SOURCED: H5083 sends 03 19 00 00 (untyped)
      doc-ref:
        - H5083 data @0x100007a6
    0xff:
      id: manufacturer_specific
      doc-ref:
        - base-classes9/com/govee/base2home/pact/BleUtil.java:787
