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
  STANDALONE (spans frames: Kaitai cannot join chunks, trim the last chunk to
  len_last or concatenate bytes): the data of one 0xAC status-read burst,
  joined by the client, walked as Compose4BaseInfoSingleRead.u walks it. There
  is no header: offset 0 is the first TLV's tag.

  The walk (u():253-285): while a byte remains, u() reads the tag and stops if
  that was the last byte; it reads the len byte SIGNED and stops if no byte
  follows it; then it copies len bytes and steps i += 2 + len. So a TLV is
  read only while 3 or more bytes remain, and the 0-2 bytes u() stops at are
  `tail` (a lone tag, a final [T][00], or a final [T][0x80+]). A stop is not a
  failure: o() runs and the read succeeds with the TLVs before it. Type 0 is
  an ordinary TLV: u() has no padding sentinel and hands [00][len] to the
  screen's fallback, which ignores it. u() throws, and the whole status read
  fails, on a len of 0x80 or more with a byte after it (new byte[negative])
  and on a value that runs past the buffer with a value byte present
  (arraycopy); this type raises at the same two places (len_value's valid,
  end of stream in value). Every other app failure is a handler that cannot
  read its value: the value still reads here and status_tlv.app_read_fails
  says so (R7). An empty or 1-2 byte buffer reads with tlvs absent and the
  bytes in tail (u() runs no full step and reports nothing).

  PROCEDURAL (the library's burst assembler): match and order the chunks and
  join their data (govee_ble_frame status_chunk). A complete multi-packet
  buffer is 12 + 17 * (num_packets - 2) + len_last bytes (len_last alone for
  one packet) and ends exactly at the last TLV. The real H60A6 burst
  ac 00 0a 00 0c 03 00 .. ac ff ..: 10 packets, len_last 12, 160 bytes. A
  client that joins the whole 17-byte window of the last chunk instead gets
  its zero padding on the end, which reads as type-0 TLVs and a tail and still
  re-writes exactly. Whether a burst's read failed is the OR of the TLVs'
  app_read_fails, also library code.

  Firmware: H60A6 builds the stream in ble_check_basic_info and chunks it with
  the same chunker it uses for 0xAB; a mode with no 0x05 record gives a
  zero-length [05][00] TLV.
doc-ref:
  - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:247-345
  - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:253-285
  - base-classes10/com/govee/base2light/ble/controller/AbsComposeControllerWithOpResult.java:335-342
  - base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:240-261
  - H60A6 FUN_9b04c11c @0x9b04c11c
  - H60A6 FUN_9b04bdd8 @0x9b04bdd8
  - H60A6 FUN_9b04b906 @0x9b04b906
  - H60A6 FUN_9b025dc8 @0x9b025dd6-0x9b025ddc
params:
  - id: profile
    type: govee_profile
    doc: the device's decode context, passed to every TLV value
seq:
  - id: tlvs
    type: status_tlv(profile)
    repeat: until
    repeat-until: _io.size - _io.pos < 3
    if: _io.size >= 3
    doc: |
      the TLVs u() steps over, in stream order; absent for a buffer of 0-2
      bytes
  - id: tail
    size-eos: true
    doc: |
      the 0-2 bytes u() stops at without dispatching them; data that must
      round-trip, not padding
types:
  status_tlv:
    doc: |
      One TLV as u() reads it: [tag][len_value][value]. u() decodes 0x01, 0x04
      and 0x07 itself, hands 0x05 to the screen's mode lambda (g) and 0x11 /
      0x12 / 0x23 to the per-SKU TimerConfig / ControllerNewTimer parsers, and
      gives every other tag, with [tag, len] re-prepended, to the screen's
      fallback lambda (h). The value is the shared govee_state over exactly
      len_value bytes (source status_tlv), so a TLV reads the same whether the
      stream or a per-TLV walk off a shared stream reads it; in such a walk,
      tag and len_value are set even when the value raises.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:286-341
    params:
      - id: profile
        type: govee_profile
        doc: the device's decode context
    seq:
      - id: tag
        type: u1
        enum: govee_common::command
        doc: |
          the TLV type: the command byte the request names (status_read's
          type list); an unknown tag keeps its int
      - id: len_value
        type: u1
        valid:
          max: 0x7f
        doc: |
          value length. u() reads it signed (:270): 0x80 and up is negative and
          new byte[len] throws (:283) when a byte follows it, which fails the
          whole read. As the last two bytes such a TLV is the tail instead.
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:269-285
      - id: value
        size: len_value
        type: govee_state(tag, govee_state::source::status_tlv, profile)
        doc: |
          the value, a bounded substream (u()'s arraycopy, :284); an overrun
          with a value byte present throws there and raises here
    instances:
      total_len:
        value: 2 + len_value
        doc: |
          Bytes this TLV takes in the stream, [tag][len_value] plus the value:
          the step u() takes to the next TLV (past the 2-byte head, then the
          value's length). It needs
          only tag and len_value, so a per-TLV walk has it even when the value
          did not read.
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:269-285
      app_read_fails:
        value: value.app_read_fails
        doc: |
          the app's whole status read fails on this value (an empty or short
          value whose handler indexes it unguarded; govee_state.app_read_fails
          lists the rules). The TLV still reads and re-writes.
