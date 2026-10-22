meta:
  id: govee_status_stream
  title: Govee 0xAC multi-packet reply (status TLV stream or special-info record)
  license: MIT
  ks-version: '0.11'
  imports:
    - govee_ble_frame
    - govee_common
    - govee_profile
    - govee_state
  bit-endian: le
  endian: be
doc: |
  STANDALONE (spans frames: the client joins the burst's chunk data): one 0xAC
  multi-packet reply, read by its reader (the request's command, which the
  first chunk echoes with the reader's sub): the status read's TLV stream,
  offset 0 the first TLV's tag, or the special-info read's segment IC record.
  Firmware: H60A6 builds the stream in ble_check_basic_info.
  Firmware: an H60A6 mode with no 0x05 record sends an empty [05][00] TLV.
doc-ref:
  - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:247-345
  - base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:100-103,158-165
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerReadSpecialInfoMultiBack.java:100-109
  - H60A6 FUN_9b04bdd8 @0x9b04bdd8
  - H60A6 FUN_9b025dc8 @0x9b025dd6-0x9b025ddc
params:
  - id: profile
    type: govee_profile
    doc: the device's decode context (the TLV values'; the segment IC record takes none)
  - id: reader
    type: u1
    enum: govee_ble_frame::status_command
    doc: the reader whose reply this is; the app matches its command and sub on the first chunk
seq:
  - id: tlvs
    type: status_tlv(profile)
    repeat: until
    repeat-until: _io.size - _io.pos < 3
    if: reader == govee_ble_frame::status_command::status_read and _io.size >= 3
  - id: segment_ic
    type: segment_ic_info
    size-eos: true
    if: reader == govee_ble_frame::status_command::special_info
    doc: the special-info reply under sub segment_ic (govee_ble_frame status_sub)
  - id: tail
    size-eos: true
    doc: the 0-2 bytes the status read's walk stops at; data to round-trip, not padding
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
  segment_ic_info:
    doc: |
      The segment IC record [num_segments][IC per segment ...], each a Java
      byte, as goodsType 180 reads it (Info4Detail.z0) and stores it in
      SegmentIcDataConfig: num_segments is the colour page's piece count, the
      write mask's width and the aa a5 walk's pieces, and the IC sum is the
      device's IC. H707A-C read the same reply, and their status TLV 0xA1, with
      their own reader (H707AInfo4Detail.G0: unsigned bytes, each IC / 3).
    doc-ref:
      - split_h705a/com/govee/h705a/newDetail/VM4Light.java:768-787,1566-1572
      - split_h705a/com/govee/h705a/newDetail/Info4Detail.java:839-860
      - split_h705a/com/govee/h705a/pact/Support.java:317-323
      - split_h705a/com/govee/h705a/newDetail/ColorModeV2.java:339-365
      - split_h7075/com/govee/h7075/h707A/H707ABCDetailVM.java:266-275
      - split_h7075/com/govee/h7075/h707A/H707AInfo4Detail.java:310-323
    seq:
      - id: num_segments
        type: s1
        if: not _io.eof
        doc: the segment count (Info4Detail.z0's bArr[0])
      - id: ic_per_segment
        type: s1
        repeat: eos
        doc: each segment's IC count, in order
    instances:
      app_read_fails:
        value: _io.size == 0
        doc: z0 reads byte 0 unguarded, so an empty payload stores nothing
