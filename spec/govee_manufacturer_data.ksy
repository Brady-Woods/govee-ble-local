meta:
  id: govee_manufacturer_data
  title: Govee manufacturer-specific advertising data (AD type 0xFF payload)
  license: MIT
  ks-version: '0.11'
  bit-endian: le
  endian: be
doc: |
  STANDALONE (other channel: one 0xFF AD's payload, after its type byte).
  Govee identity and state; the flags byte comes before the 88 EC marker.
  PROCEDURAL: bleak splits the payload; rejoin it as company id u2le + value.
  FIRMWARE-SOURCED: H5083: 43 88 EC 00 02 02 <outlet> (its only cipher sign).
  FIRMWARE-SOURCED: H6047: 03 88 EC 00 01 01 <power> (plaintext).
doc-ref:
  - base-classes9/com/govee/base2home/pact/BleUtil.java:779-803
  - H5083 data @0x100007a6
  - firmware-h6047/decompiled/H6047_decompiled.c:5649-5773
seq:
  - id: flags
    type: u1
    if: _io.size >= 1
    doc: bit 6 (0x40) = encrypted; low nibble = protocol version
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:788-790
  - id: company_id
    type: u2le
    if: _io.size >= 3
    doc: the marker, 0xEC88 (bytes 88 EC) for Govee
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:36
      - base-classes9/com/govee/base2home/pact/BleUtil.java:792-794
  - id: pact_type
    type: u2
    if: _io.size >= 5
    doc: protocol type, big-endian
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:795
      - base-classes9/com/govee/base2home/pact/BleUtil.java:360-367
  - id: pact_code
    type: u1
    if: _io.size >= 6
    doc: protocol code (the sub-version)
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:795
  - id: device_state
    size-eos: true
    doc: per-SKU state with no common layout; the app reads bytes 0 and 1
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:700-717
      - base-classes9/com/govee/base2home/pact/BleUtil.java:622-648
      - base-classes9/com/govee/base2home/pact/BleUtil.java:598-620
instances:
  is_govee:
    value: _io.size >= 3 and company_id == 0xec88
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:792-794
  encrypted:
    value: _io.size >= 1 and (flags & 0x40) != 0
    doc: the device uses the encrypted command channel
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:789
      - base-classes9/com/google/common/primitives/SignedBytes.java:11
  protocol_version:
    value: flags & 0x0f
    if: _io.size >= 1
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:790
  is_govee_pact:
    value: is_govee and protocol_version >= 1
    doc: the app's pact gate; no length test (see has_pact)
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:787-796
      - base-classes9/com/govee/base2home/pact/GoodsType.java:1551-1552
  has_pact:
    value: _io.size >= 6 and is_govee_pact
    doc: the accepted pact is in the payload (a 5-byte one lacks pact_code)
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:787-796
  is_app_manufacturer_ad:
    value: _io.size >= 5
    doc: the app inspects only a 0xFF AD whose length byte is >= 6
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:787
      - base-classes9/com/govee/base2home/pact/BleUtil.java:707
  has_switch_state:
    value: _io.size >= 7 and is_govee and flags >= 2 and flags <= 0x7f
    doc: |
      the app's switch-byte gate: signed flags >= 2, no version or cipher test
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:707-711
      - base-classes9/com/govee/base2home/pact/BleUtil.java:726-731
  switch_byte:
    value: device_state[0]
    if: has_switch_state
    doc: per-SKU; H5080 plugs use bit 0 (outlet 0) and bit 1 (outlet 1)
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:711
      - split_plugnew/com/govee/h5080/pact/Model.java:254-266
      - split_plugnew/com/govee/h5080/pact/Model4Plug2.java:269-290
