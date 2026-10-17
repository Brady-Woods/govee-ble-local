meta:
  id: govee_bgc_info
  title: Govee BGC-info characteristic value (GATT 2b12)
  license: MIT
  ks-version: '0.11'
  imports:
    - govee_common
  bit-endian: le
  endian: be
doc: |
  STANDALONE (other channel: the value of GATT characteristic
  00010203-0405-0607-0809-0a0b0c0d2b12 under the control service ...1910,
  read once after connect; not a vendor frame, so no proType and no BCC; not
  the OTA 2b12 under service 1912).
  Parse the whole characteristic value with this type; write it to emit one.
  Layout: [format][encrypt_version]; format 2 adds [broadcast_flag]
  [pact_type u2be][pact_code] (6 bytes); then `rest`.
  The app reader: BgcInfoReader.a takes the first 1910 service and its 2b12
  characteristic (Constants.b / .e) and reads it; with no such
  characteristic it records status 3 ("no info") and encryptVersion stays 0.
  BgcInfoReader.d ignores an empty value; format 1 sets encryptVersion =
  byte 1 (unsigned); format 2 goes through BaseBgcInfo.Companion.a, which
  reads bytes 1..5 into BgcInfoV2(encryptVersion, flag = byte 2 == 1,
  pactType = bytes 3..4 big-endian (BytesUtils.c), pactCode = byte 5); any
  other format leaves encryptVersion at its reset value 0 (BgcInfoReader.f).
  BgcInfoV2's getters are swapped by name: d() returns pactType, c()
  pactCode. g(): encryption is supported when encryptVersion is 1 or 2;
  h(): the V2 cipher when it is 2 (EncryptionManagerPool.b takes
  EncryptionManagerV2 when BgcInfoReaderPool.supportEncryptV2).
  BgcInfoReader.d takes a callback but never calls it in this build, so
  CommEventGattCallback.d, which turns a BgcInfoV2 into BLE broadcast
  version (flag ? 3 : 2) and Protocol(pactType, pactCode), is not reached
  from this read.
  LENIENT ON PURPOSE (user decision for item 19, "lenient like today"):
  each field is read only when its bytes are there, so no value raises and
  effective_encrypt_version is always set. The app's index reads throw on a
  1-byte format-1 value and on a 1..5-byte format-2 value; nothing catches
  that throw, which leaves EncryptionBluetoothGattCallback
  .onCharacteristicRead before the connect continues (q / r).
  `app_read_fails` flags those values. Here such a value keeps byte 1 as
  encrypt_version when it is there (the library's rule, `data[1] if
  len(data) >= 2 and data[0] in (1, 2) else 0`) and leaves the format-2
  fields absent, with their bytes in `rest`.
  Firmware: the GATT tables of the images studied (H5083, H6006 / H6008,
  H6047 BLE, H60A1 / H60A6) hold the 2b10 and 2b11 UUIDs and no 2b12, so
  every form here is the app reader's. Which SKUs serve 2b12 is per-image
  GATT data (firmware.yaml gatt), not grammar.
doc-ref:
  - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:47-78
  - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:88-127
  - base-classes13/com/govee/encryp/ble/Constants.java:11,14,28-30,36-38
  - base-classes13/com/govee/encryp/ble/BaseBgcInfo.java:27-34
  - base-classes13/com/govee/encryp/ble/BgcInfoV2.java:10-26
  - base-classes13/com/govee/encryp/BytesUtils.java:78-85
  - base-classes13/com/govee/encryp/ble/BgcInfoReaderPool.java:37-53,90-97
  - base-classes13/com/govee/encryp/ble/EncryptionManagerPool.java:62-64
  - base-classes13/com/govee/encryp/ble/EncryptionBluetoothGattCallback.java:95-98,127-137
  - base-classes10/com/govee/base2light/CommEventGattCallback.java:32-39
  - H5083 uuid_2b10 @0x9b80f826
  - H5083 uuid_2b11 @0x9b80f87a
seq:
  - id: format
    type: u1
    if: _io.size >= 1
    doc: |
      record format: 1 = [format][encrypt_version], 2 = the 6-byte form; the
      app reads nothing more for any other value. Absent on an empty value.
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:89-98
  - id: encrypt_version
    type: u1
    enum: encrypt_version
    if: _io.size >= 2 and (format == 1 or format == 2)
    doc: |
      encryptVersion (byte 1, unsigned), which picks the session cipher;
      absent for another format or a 1-byte value
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:94-95
      - base-classes13/com/govee/encryp/ble/BaseBgcInfo.java:33
  - id: broadcast_flag
    type: u1
    if: has_v2_tail
    doc: |
      format 2: == 1 means BLE broadcast version 3, else 2 (BgcInfoV2.b(),
      read by CommEventGattCallback.d)
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BaseBgcInfo.java:33
      - base-classes10/com/govee/base2light/CommEventGattCallback.java:35
  - id: pact_type
    type: u2
    if: has_v2_tail
    doc: |
      format 2 pactType, big-endian (BytesUtils.c(byte 3, byte 4);
      BgcInfoV2.d())
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BaseBgcInfo.java:33
      - base-classes13/com/govee/encryp/BytesUtils.java:78-85
  - id: pact_code
    type: u1
    if: has_v2_tail
    doc: format 2 pactCode (byte 5; BgcInfoV2.c())
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BaseBgcInfo.java:33
      - base-classes13/com/govee/encryp/ble/BgcInfoV2.java:20-26
  - id: rest
    type: govee_common::echo
    doc: |
      every byte after the record: a short format-2 value's bytes 2.., an
      unknown format's bytes 1.., and anything after byte 5 (the app reads
      none of them)
instances:
  has_v2_tail:
    value: _io.size >= 6 and format == 2
    doc: |
      a format-2 value long enough for the flag and pact fields; a shorter
      one is where the app throws (app_read_fails)
  effective_encrypt_version:
    value: >-
      _io.size >= 2 and (format == 1 or format == 2)
      ? encrypt_version.to_i : 0
    doc: |
      BgcInfoReader.c as the app leaves it for a value it accepts:
      encrypt_version for format 1 / 2, else 0 (enum encrypt_version). Always
      set. Where the app throws (app_read_fails) it is 0 for a 1-byte value
      and byte 1 for a 2..5-byte format-2 value (the library's lenient rule).
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:93-99,115-118
  supports_encryption:
    value: effective_encrypt_version == 1 or effective_encrypt_version == 2
    doc: |
      BgcInfoReader.g: this record turns encryption on
      (EncryptionUtils.isEncryptionSupported ORs it with
      IEncryptionSupport.checkSupport)
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:120-123
      - base-classes13/com/govee/encryp/ble/EncryptionUtils.java:32-37
  is_encrypt_v2:
    value: effective_encrypt_version == 2
    doc: 'BgcInfoReader.h: the AES-GCM session (Controller4AesGcm)'
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:125-127
  app_read_fails:
    value: >-
      _io.size >= 1
      and ((format == 1 and _io.size < 2) or (format == 2 and _io.size < 6))
    doc: |
      The app's index reads throw on this value (a 1-byte format-1 value, a
      1..5-byte format-2 value) and the connect stops there; the fields still
      read here (see the type doc)
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:94-98
      - base-classes13/com/govee/encryp/ble/BaseBgcInfo.java:33
      - base-classes13/com/govee/encryp/ble/EncryptionBluetoothGattCallback.java:127-137
enums:
  encrypt_version:
    0x00:
      id: not_reported
      doc: |
        BgcInfoReader.c is 0: its reset value (f()), a value with another
        format byte, or a format-1 record whose byte 1 is 0. g() is false,
        so this record does not turn encryption on.
        EncryptionUtils.isEncryptionSupported still ORs the other source,
        IEncryptionSupport.checkSupport; if that turns encryption on, h() is
        false, so EncryptionManagerPool.b builds the V1 manager (aes_rc4_psk).
      doc-ref:
        - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:115-123
        - base-classes13/com/govee/encryp/ble/EncryptionUtils.java:32-37
        - base-classes13/com/govee/encryp/ble/EncryptionManagerPool.java:62,85-88
    0x01:
      id: aes_rc4_psk
      doc: |
        Encryption V1: g() true, h() false, so EncryptionManagerPool.b builds
        EncryptionManager (logged V1) with the Controller4Aes handshake (E7 01
        / E7 02, keyed by the app's fixed key LibTools.a.c()); cipher
        AES-128-ECB over the 16-byte blocks plus RC4 over the remainder
        (Safe.b / Safe.d with Safe.f / Safe.g).
      doc-ref:
        - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:120-127
        - base-classes13/com/govee/encryp/ble/EncryptionManagerPool.java:62,85-88
        - base-classes13/com/govee/encryp/ble/Controller4Aes.java:103-136
        - base-classes13/com/govee/encryp/ble/Safe.java:47-160
    0x02:
      id: aes_gcm
      doc: |
        Encryption V2: h() true, so BgcInfoReaderPool.supportEncryptV2 is
        true and EncryptionManagerPool.b builds EncryptionManagerV2 (logged
        V2) with the Controller4AesGcm handshake (E7 19 / E7 1A).
      doc-ref:
        - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:125-127
        - base-classes13/com/govee/encryp/ble/BgcInfoReaderPool.java:90-97
        - base-classes13/com/govee/encryp/ble/EncryptionManagerPool.java:62-64,81-84
        - base-classes13/com/govee/encryp/ble/Controller4AesGcm.java:52,186
