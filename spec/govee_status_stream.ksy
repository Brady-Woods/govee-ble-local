meta:
  id: govee_status_stream
  title: Govee 0xAC status TLV stream
  license: MIT
  ks-version: '0.11'
  imports:
    - govee_common
    - govee_profile
    - govee_state
  bit-endian: le
  endian: be
doc: |
  STANDALONE (spans frames: the client joins the burst's chunk data): one 0xAC
  status-read burst as a TLV stream; offset 0 is the first TLV's tag.
  Firmware: H60A6 builds the stream in ble_check_basic_info.
  Firmware: an H60A6 mode with no 0x05 record sends an empty [05][00] TLV.
doc-ref:
  - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:247-345
  - H60A6 FUN_9b04bdd8 @0x9b04bdd8
  - H60A6 FUN_9b025dc8 @0x9b025dd6-0x9b025ddc
params:
  - id: profile
    type: govee_profile
seq:
  - id: tlvs
    type: status_tlv(profile)
    repeat: until
    repeat-until: _io.size - _io.pos < 3
    if: _io.size >= 3
  - id: tail
    size-eos: true
    doc: the 0-2 bytes the app's walk stops at; data to round-trip, not padding
types:
  status_tlv:
    doc: |
      One TLV, [tag][len_value][value], its value read as govee_state.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:286-341
    params:
      - id: profile
        type: govee_profile
    seq:
      - id: tag
        type: u1
        enum: govee_common::command
        doc: the command byte the request named
      - id: len_value
        type: u1
        valid:
          max: 0x7f
        doc: signed in the app; 0x80+ with a byte after it fails the whole read
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:269-285
      - id: value
        size: len_value
        type: govee_state(tag, govee_state::source::status_tlv, profile)
        doc: an overrun with a value byte present fails the app's read too
    instances:
      total_len:
        value: 2 + len_value
        doc: the walk's step to the next TLV
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:269-285
      app_read_fails:
        value: value.app_read_fails
        doc: the app's whole status read fails on this value (it reads here)
