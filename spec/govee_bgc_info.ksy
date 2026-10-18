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
  STANDALONE (other channel: GATT ...2b12 under service ...1910, not OTA 1912;
  no vendor frame): the encryption-info value read on connect; never raises.
  Firmware: H5083, H6006, H6008, H6047, H60A1 and H60A6 have no 2b12 UUID.
doc-ref:
  - base-classes13/com/govee/encryp/ble/Constants.java:11,14,28-30,36-38
  - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:47-78
  - H5083 uuid_2b10 @0x9b80f826
seq:
  - id: format
    type: u1
    if: _io.size >= 1
    doc: 1 = [format][encrypt_version], 2 = the 6-byte form; no other is read
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:89-98
  - id: encrypt_version
    type: u1
    enum: encrypt_version
    if: _io.size >= 2 and (format == 1 or format == 2)
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:94-95
      - base-classes13/com/govee/encryp/ble/BaseBgcInfo.java:33
  - id: broadcast_flag
    type: u1
    if: has_v2_tail
    doc: 1 = BLE broadcast version 3, else 2 (the app never reaches that reader)
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BaseBgcInfo.java:33
      - base-classes10/com/govee/base2light/CommEventGattCallback.java:35
  - id: pact_type
    type: u2
    if: has_v2_tail
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BaseBgcInfo.java:33
      - base-classes13/com/govee/encryp/BytesUtils.java:78-85
  - id: pact_code
    type: u1
    if: has_v2_tail
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BaseBgcInfo.java:33
      - base-classes13/com/govee/encryp/ble/BgcInfoV2.java:20-26
  - id: rest
    type: govee_common::echo
    doc: the bytes after the fields read here; the app reads none of them
instances:
  has_v2_tail:
    value: _io.size >= 6 and format == 2
  effective_encrypt_version:
    value: >-
      _io.size >= 2 and (format == 1 or format == 2)
      ? encrypt_version.to_i : 0
    doc: the version the app keeps; a short format-2 value still gives byte 1
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:93-99,115-118
  supports_encryption:
    value: effective_encrypt_version == 1 or effective_encrypt_version == 2
    doc: this record turns encryption on (the app ORs a second support check)
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:120-123
      - base-classes13/com/govee/encryp/ble/EncryptionUtils.java:32-37
  is_encrypt_v2:
    value: effective_encrypt_version == 2
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:125-127
  app_read_fails:
    value: >-
      _io.size >= 1
      and ((format == 1 and _io.size < 2) or (format == 2 and _io.size < 6))
    doc: the app throws on this short value and the connect stops there
    doc-ref:
      - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:94-98
      - base-classes13/com/govee/encryp/ble/BaseBgcInfo.java:33
      - base-classes13/com/govee/encryp/ble/EncryptionBluetoothGattCallback.java:127-137
enums:
  encrypt_version:
    0x00:
      id: not_reported
      doc: no encryption from this record; if the other check turns it on, V1
      doc-ref:
        - base-classes13/com/govee/encryp/ble/BgcInfoReader.java:115-123
        - base-classes13/com/govee/encryp/ble/EncryptionUtils.java:32-37
        - base-classes13/com/govee/encryp/ble/EncryptionManagerPool.java:62,85-88
    0x01:
      id: aes_rc4_psk
      doc: V1, E7 01 / E7 02 handshake on the app's fixed key; AES-128-ECB + RC4
      doc-ref:
        - base-classes13/com/govee/encryp/ble/EncryptionManagerPool.java:62,85-88
        - base-classes13/com/govee/encryp/ble/Controller4Aes.java:103-136
        - base-classes13/com/govee/encryp/ble/Safe.java:47-160
    0x02:
      id: aes_gcm
      doc: V2, AES-GCM session with the E7 19 / E7 1A handshake
      doc-ref:
        - base-classes13/com/govee/encryp/ble/BgcInfoReaderPool.java:90-97
        - base-classes13/com/govee/encryp/ble/EncryptionManagerPool.java:62-64,81-84
        - base-classes13/com/govee/encryp/ble/Controller4AesGcm.java:52,186
