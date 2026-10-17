meta:
  id: govee_manufacturer_data
  title: Govee manufacturer-specific advertising data (AD type 0xFF payload)
  license: MIT
  ks-version: '0.11'
  bit-endian: le
  endian: be
doc: |
  STANDALONE (other channel: the payload of one manufacturer-specific AD,
  type 0xFF, as the OS hands it over: the AD's bytes after its type byte;
  from_bytes works, no params). Passive, connectionless identity and state.
  Layout: [flags][company_id u2le][pact_type u2be][pact_code][device_state].
  Govee's layout is custom: the flags byte comes BEFORE the 0x88 0xEC marker,
  so the marker is not a standard leading company identifier, and the fields
  mean something only when is_govee is true. Other vendors' 0xFF payloads
  read without error and give is_govee false.
  Bleak (and Android's ScanRecord) split the payload into a company id (its
  first two bytes, little-endian: flags | 0x88 << 8) and a value. The client
  rebuilds the payload as company_id.to_bytes(2, 'little') + value
  (PROCEDURAL) and reads it with this type; a scan record reaches it through
  govee_scan_record.
  LENIENT: every field is read only when its bytes are there, so a short or
  foreign payload never raises; the bytes not used by a field are in
  device_state, so every payload re-writes byte-exactly. To write, allocate a
  buffer of the payload's length: the field guards read the stream size.
  App reader (the standard): BleUtil.parseBleBroadcastPact walks the 62-byte
  record and inspects a 0xFF AD only when its length byte is >= 6
  (is_app_manufacturer_ad); version (flags & 15) 0 or a missing marker skips
  the AD and the walk goes on; otherwise the pact is returned (has_pact),
  and GoodsType.parseBleBroadcastPactInfo accepts it.
  Other record layouts this type does not decode (is_govee false there):
  - parseBleBroadcastPact4MultiTh (thermo-hygrometers): the marker sits in a
    preceding type-0x03 AD of len 3 (govee_scan_record uuid16_list), and the
    0xFF AD is [flags][pact_type u2be][pact_code] with no marker;
  - checkBroadcastData and parseH512xBleAddBroadcast: the 0xFF AD starts
    with 88 EC, the standard company-id position.
  Firmware (the payload each analysed image sends; which SKU sends what is
  behaviour data, not grammar):
  - H5083: 43 88 EC 00 02 02 <outlet> (flags 0x43: encrypted, version 3;
    pact 2/2). FUN_9b029774 sets flags |= 0x40 and pact 2/2 only when the
    name is 18 chars; FUN_9b029850 rewrites the switch byte on every relay
    change. H5083 has no 2b12, so this encrypted bit is its only cipher
    announcement.
  - H6047: 03 88 EC 00 01 01 <power> (plaintext, version 3, pact 1/1),
    built by FUN_01006748 and rebuilt by FUN_010068bc on a power change.
  - H60A6: only the pact is firmware-sourced: aa ef reports the advertised
    pact 0x0001 / 01; the advert bytes are not in the analysed dispatcher.
doc-ref:
  - base-classes9/com/govee/base2home/pact/BleUtil.java:779-803
  - base-classes9/com/govee/base2home/pact/GoodsType.java:1547-1552
  - base-classes9/com/govee/base2home/pact/BleUtil.java:805-835
  - base-classes9/com/govee/base2home/pact/BleUtil.java:121-138
  - base-classes9/com/govee/base2home/pact/BleUtil.java:957-968
  - H5083 data @0x100007a6
  - H5083 FUN_9b029774 @0x9b029774
  - H5083 FUN_9b029850 @0x9b029850
  - firmware-h6047/decompiled/H6047_decompiled.c:5649-5773
  - firmware-h6047/decompiled/H6047_decompiled.c:5776-5807
  - H60A6 FUN_9b04aa38 @0x9b04aa38
seq:
  - id: flags
    type: u1
    if: _io.size >= 1
    doc: |
      bit 6 (0x40) = encrypted; low nibble (0x0F) = protocol version (the
      broadcast version, >= 1 for a pact). scanRecord[i2 + 2] in the app.
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:788-790
  - id: company_id
    type: u2le
    if: _io.size >= 3
    doc: |
      the marker: bytes 0x88 0xEC on the wire = 0xEC88 for Govee (BleUtil.h
      = {-120, -20}, compared at scanRecord[i2 + 3] / [i2 + 4])
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:36
      - base-classes9/com/govee/base2home/pact/BleUtil.java:792-794
  - id: pact_type
    type: u2
    if: _io.size >= 5
    doc: |
      protocol type, big-endian (getUnsignedInt(scanRecord[i2 + 5],
      scanRecord[i2 + 6]))
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:795
      - base-classes9/com/govee/base2home/pact/BleUtil.java:360-367
  - id: pact_code
    type: u1
    if: _io.size >= 6
    doc: protocol code, the sub-version (scanRecord[i2 + 7])
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:795
  - id: device_state
    size-eos: true
    doc: |
      DATA, not an echo (a zero here is a real value, such as an off switch
      byte, so it is a plain byte field): the bytes after pact_code, or, for a
      payload too short for the identity fields, the bytes of the partial
      field. Per-SKU state with NO universal layout: the identity parser
      stops at pact_code, and SKU-specific parsers read fixed offsets here
      (i2 = the index of the AD's length byte):
      - parseBleBroadOnOff (and its logging overload): device_state[0]
        (scanRecord[i2 + 8], bleak value[4]) and device_state[1]
        (scanRecord[i2 + 9]); see has_switch_state / switch_byte;
      - parseBleBroadDaySyncInfo: device_state[1] (scanRecord[i2 + 9]);
      - parseBleBroadBatteryAndHighTem (battery / charge): device_state[1].
      The layout is chosen by the SKU (resolved from pact_type and pact_code
      through the cloud / name table), not by any in-record discriminator.
      A per-SKU overlay is possible, but there is no single structure, so it
      is not typed here.
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:700-717
      - base-classes9/com/govee/base2home/pact/BleUtil.java:719-744
      - base-classes9/com/govee/base2home/pact/BleUtil.java:622-648
      - base-classes9/com/govee/base2home/pact/BleUtil.java:598-620
instances:
  is_govee:
    value: _io.size >= 3 and company_id == 0xec88
    doc: |
      the 0x88 0xEC marker at payload bytes 1-2; false for a payload too short
      to hold it
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:792-794
  encrypted:
    value: _io.size >= 1 and (flags & 0x40) != 0
    doc: |
      the encrypt flag (b2 & SignedBytes.a, 0x40): the device uses the
      encrypted command channel
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:789
      - base-classes9/com/google/common/primitives/SignedBytes.java:11
  protocol_version:
    value: flags & 0x0f
    if: _io.size >= 1
    doc: the flags low nibble (the app masks the flags byte with 15)
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:790
  is_govee_pact:
    value: is_govee and protocol_version >= 1
    doc: |
      The app's pact gate: parseBleBroadcastPact returns the pact only for
      version >= 1 (it tests 1, then 2 and up) and the marker, and
      GoodsType.parseBleBroadcastPactInfo then accepts it. No length test:
      see has_pact.
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:787-796
      - base-classes9/com/govee/base2home/pact/GoodsType.java:1551-1552
  has_pact:
    value: _io.size >= 6 and is_govee_pact
    doc: |
      The app accepts this AD's pact, and the payload holds pact_type and
      pact_code. False for every payload the app skips, and for a payload of
      exactly 5 bytes (AD len 6): the app accepts that one too, but reads
      pact_code from scanRecord[i2 + 7], the byte AFTER the AD (the next AD's
      length byte or a pad byte), which neither this payload nor bleak's
      split value carries.
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:787-796
  is_app_manufacturer_ad:
    value: _io.size >= 5
    doc: |
      the AD the app's 0xFF parsers inspect: length byte >= 6 (unsignedByte
      >= 6 && scanRecord[i2 + 1] == -1), so a payload of 5 bytes or more. A
      0xFF AD of len 2..5 is skipped and the walk goes on. For an AD the
      record cuts short this is the payload delivered, not the length byte.
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:787
      - base-classes9/com/govee/base2home/pact/BleUtil.java:707
  has_switch_state:
    value: _io.size >= 7 and is_govee and flags >= 2 and flags <= 0x7f
    doc: |
      The advert carries the switch byte (parseBleBroadOnOff and its logging
      overload): an inspected AD whose flags byte, read as a SIGNED Java
      byte, is >= 2 (so u1 2..0x7F), with the marker. No version-nibble or
      encrypted-bit test, so flags 0x41 carries it and 0x01 / 0xC2 do not.
      The app reads scanRecord[i2 + 8] even when it lies past a len-6 or
      len-7 AD; the size test keeps this to bytes inside the payload.
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:707-711
      - base-classes9/com/govee/base2home/pact/BleUtil.java:726-731
  switch_byte:
    value: device_state[0]
    if: has_switch_state
    doc: |
      The advert switch byte (scanRecord[i2 + 8], bleak value[4]):
      parseBleBroadOnOff returns it unsigned as bleBroadOnOff[1]; the per-SKU
      model decodes it. The h5080 plugs: goodsType 90 is on for any bit, else
      bit 0; the 2-outlet Model4Plug2: bit 0 = outlet 0, bit 1 = outlet 1. The
      app also returns device_state[1] (bleBroadOnOff[2]), which no curated
      model reads.
    doc-ref:
      - base-classes9/com/govee/base2home/pact/BleUtil.java:711
      - base-classes9/com/govee/base2home/pact/BleUtil.java:731
      - split_plugnew/com/govee/h5080/pact/Model.java:254-266
      - split_plugnew/com/govee/h5080/pact/Model4Plug2.java:269-290
