meta:
  id: govee_profile
  title: Govee per-device decode context
  license: MIT
  ks-version: '0.11'
  imports:
    - govee_common
  bit-endian: le
  endian: be
doc: |
  CONTEXT (not a byte source): the out-of-band facts a Govee frame does not
  carry, as params only; never read or written. The library builds ONE object
  per set of device facts with a single keyword factory (devices.yaml
  client_profile resolved by_model / by_goods_type / by_pact / by_firmware, its
  `wire` block, plus runtime facts such as the live aa 40 piece count) and
  caches it: generated parents check user-type params by identity, so a copy
  of a profile is rejected and a changed fact needs a new object (and new
  trees). PROCEDURAL: profile resolution lives in the library.
  Value 0 of every selector is `unknown`: no case matches it and the body
  stays raw (field absent, bytes in the message's rest). One exception:
  f_ee_30 picks only the rule of notify_30's derived instances, so its four
  bytes are read under every form and unknown derives nothing. The all-zero
  profile is the analyzer profile (GENERIC).
  `mode_status` is the same object as `mode` unless devices.yaml gives the
  family a mode_color_parse_status (the 0xAC TLV 0x05 parsed differently from
  the aa 05 reply).
params:
  - id: forms
    type: command_forms
    doc: one selector per overloaded command byte
  - id: counts
    type: device_counts
    doc: the counts a write's mask or level list needs
  - id: group
    type: color_group_form
    doc: the colour-group record count rule (aa a5 / aa a2 / TLV 0xA5)
  - id: power
    type: power_rule
    doc: the power rules of the aa 01 reply (connect read and heartbeat)
  - id: mode
    type: mode_decode
    doc: the mode-report decode of the aa 05 reply
  - id: mode_status
    type: mode_decode
    doc: the mode-report decode of the 0xAC TLV 0x05 value
types:
  command_forms:
    doc: |
      CONTEXT: the meaning of each overloaded command byte (or write layout)
      for this device, one selector per byte. A selector grows a member only
      when a second meaning is traced. Each enum's members cite the senders.
    params:
      - id: f_01
        type: u1
        enum: govee_common::form_01
        doc: 33 01 state byte (light 1/0 vs the plug relay byte)
      - id: f_02
        type: u1
        enum: govee_common::form_02
        doc: 33 02 (H5089 total switch vs H5086 chart prepare)
      - id: f_09
        type: u1
        enum: govee_common::form_09
        doc: 33 09 time-sync body (TimerConfig version)
      - id: f_12
        type: u1
        enum: govee_common::form_12
        doc: 33 / aa 12 (wake-up timer vs H5083 timer count)
      - id: f_13
        type: u1
        enum: govee_common::form_13
        doc: 33 / aa 13 (night mode vs H5083 plug timer)
      - id: f_14
        type: u1
        enum: govee_common::form_14
        doc: 33 / aa 14 (gradual vs Wi-Fi MAC)
      - id: f_30
        type: u1
        enum: govee_common::form_30
        doc: 33 30, aa 30, TLV 0x30 (zone power vs light direction)
      - id: f_36
        type: u1
        enum: govee_common::form_36
        doc: 33 36, aa 36, TLV 0x36 (bar pair vs bar index vs worry-free)
      - id: f_41
        type: u1
        enum: govee_common::form_41
        doc: aa 41, TLV 0x41 (power memory vs section count)
      - id: f_a2
        type: u1
        enum: govee_common::form_a2
        doc: aa a2 (H61A8 batch vs colour-only group)
      - id: f_a5
        type: u1
        enum: govee_common::form_a5
        doc: aa a5, TLV 0xA5 (colour group vs H61A8 batch)
      - id: f_b0
        type: u1
        enum: govee_common::form_b0
        doc: 33 / aa b0 (H5083 plug delay)
      - id: f_0b
        type: u1
        enum: govee_common::form_0b
        doc: 33 05 0b colour write layout
      - id: f_15_01
        type: u1
        enum: govee_common::form_15_01
        doc: 33 05 15 01 colour write form
      - id: f_15_04
        type: u1
        enum: govee_common::form_15_04
        doc: 33 05 15 04 group colour vs colour-instruction-9 kelvin
      - id: f_15_05
        type: u1
        enum: govee_common::form_15_05
        doc: 33 05 15 05 Kelvin vs side brightness vs gradient
      - id: f_ee_30
        type: u1
        enum: govee_common::form_ee_30
        doc: EE 30 live-power rule (the bytes are read under every form)
      - id: f_ee_40
        type: u1
        enum: govee_common::form_ee_40
        doc: EE 40 push layout
  device_counts:
    doc: |
      CONTEXT: the counts a write needs that the frame does not carry.
    params:
      - id: num_segments
        type: u2
        doc: |
          the posSet length of the segment mask (seg_mask.num_segments): the
          piece count, or the brightness path's count rounded up to a multiple
          of 8 (see govee_common::seg_mask)
      - id: num_levels
        type: u1
        doc: the level count of a 33 05 15 03 brightness-levels write
  color_group_form:
    doc: |
      CONTEXT: how a colour-group reply is counted (see govee_state
      color_group). Controller4ColorInfoByGroup.Companion.q / p read exactly
      the count their caller passes; the reply does not carry it.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:145-175
    params:
      - id: rule
        type: u1
        enum: govee_common::group_count_rule
        doc: the record-count rule of the device's status screen
      - id: num_per_group
        type: u1
        doc: |
          records per full group: the caller's oneGroupColorSize (4 on every
          curated SKU: Support.getOneGroupColorSize; H61D3Support.t)
        doc-ref:
          - split_h60ax/com/govee/pact_h60a0/pact/Support.java:183-185
          - split_h6047/com/govee/h6047/pact/Support.java:155-157
          - split_h61d3/com/govee/h61d3/pact/H61D3Support.java:1576-1578
      - id: has_brightness
        type: bool
        doc: |
          the 0xA5 record (aa a5 reply, TLV 0xA5): true = 4-byte [brightness,
          R, G, B] records (q(); the SKU's reads go through
          generateReadController, command 0xA5); false = 3-byte [R, G, B]
          (p()). All 80 generator calls in 7.7.02 take the default command
          byte; the aa a2 colour-only read (generateReadController4OnlyColor,
          form_a2 color_group_rgb) is always 3-byte, whatever this says.
        doc-ref:
          - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:37-49
      - id: num_pieces
        type: u2
        doc: |
          the colour-piece count (Support.getColorPieceSize: 13 on a part-colour
          H60A6), or the live count; 0 when not known. Rule by_pieces counts
          the last group by it; on the TLV path rules by_pieces and fixed
          place each group by it (SubMode4Color.j; govee_state color_group)
        doc-ref:
          - split_h60ax/com/govee/pact_h60a0/pact/Support.java:154-159
  power_rule:
    doc: |
      CONTEXT: when the aa 01 reply byte means "on". A light (both masks 0) is
      on when the byte == 1 (ControllerSwitch.Companion.c; the aa 01 heartbeat
      ShareVM.N3 frame[2] == 1). A plug's byte is a relay bitmask (bit i =
      relay i, SwitchControllerV2.parseValidBytes) and the device is on when
      (byte & mask) != 0. The 0xAC TLV 0x01 rule (!= 0) is fixed by the
      envelope (govee_state source), not by this rule.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerSwitch.java:32
      - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:29-45
    params:
      - id: relay_on_mask
        type: u1
        doc: |
          the connect-read rule; 0 = a light (state == 1). Per goodsType
          (devices.yaml by_goods_type relay_on_mask): 43 (H5080 / H5083 /
          H5085) 0x01, the bit-0 rule of H5083NewDetailRepository.loadParser
          and the FrameV1 screens; 50 (H5082) and 307 (H5089) 0xFF
          (EventSwitchV2.i ORs all eight bits at the connect read); 90 (H5160 /
          H5161) 0x07 (BleOpV4.r0 ORs bits i < Support.getPlugNum(90) = 3).
        doc-ref:
          - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:167
          - split_plugnew/com/govee/h5080/ble/controller/EventSwitchV2.java:48-59
          - split_plugnew/com/govee/h5080/adjust/v2/BleOpV2.java:363
          - split_plugnew/com/govee/h5080/adjust/h5089/BleOp4H5089.java:462
          - split_plugnew/com/govee/h5080/adjust/v4_h5160/BleOpV4.java:464-481
          - split_plugnew/com/govee/h5080/pact/Support.java:126-131
      - id: relay_heartbeat_on_mask
        type: u1
        doc: |
          the aa 01 HEARTBEAT rule; 0 = the same as relay_on_mask. goodsTypes
          50 and 307 = 0x03 (MIGRATION item 13): the h5080 BleComm's heart
          controller is HeartControllerV2, and BleOpV2.onEventHeartV2 /
          BleOp4H5089.onEventHeartV2 set open = i(0) | i(1), recomputed only
          when bit 0 or 1 differs from the cached outlets. The heartbeat sends
          the same aa 01 request as the connect read, and a reply no queued
          controller claims goes to it (AbsBleComm.parse -> e). goodsType 90
          uses its 3 bits on both paths (onEventHeartV2 on the first
          m0().size() = 3 bits: 0x07); goodsType 43 reads bit 0 only
          (BleOpV1 / BleOpV3.onEventHeartV2 take i(0): 0x01).
        doc-ref:
          - split_plugnew/com/govee/h5080/ble/BleComm.java:14-16
          - split_plugnew/com/govee/h5080/adjust/v1/BleOpV1.java:208-232
          - split_plugnew/com/govee/h5080/adjust/v3/BleOpV3.java:202-225
          - split_plugnew/com/govee/h5080/ble/controller/HeartControllerV2.java:17-35
          - split_plugnew/com/govee/h5080/adjust/v2/BleOpV2.java:216-231
          - split_plugnew/com/govee/h5080/adjust/h5089/BleOp4H5089.java:331-341
          - split_plugnew/com/govee/h5080/adjust/v4_h5160/BleOpV4.java:211-215
          - base-classes10/com/govee/base2light/ble/controller/HeartController.java:4
          - base-classes10/com/govee/base2light/ble/comm/AbsBleComm.java:164-167
          - base-classes10/com/govee/base2light/ble/comm/AbsBleComm.java:255-262
  mode_decode:
    doc: |
      CONTEXT: how the device's screen decodes the mode report (aa 05 reply or
      0xAC TLV 0x05), per colour sub-mode, as devices.yaml mode_color_parse
      maps it (the keys of mode_color_decode are color_decode's members). A
      sub-mode whose decode is not_decoded is "not support" on that screen:
      the layout is absent and the bytes stay in the message's rest. The
      library's default parse (0x15 Kelvin unsigned) matches no screen and is
      not a decode here: the library derives the unsigned value itself (s2 &
      0xFFFF; PORT-review item 3).
    params:
      - id: c02
        type: u1
        enum: govee_common::color_decode
        doc: sub-mode 0x02
      - id: c0b
        type: u1
        enum: govee_common::color_decode
        doc: sub-mode 0x0b
      - id: c0d
        type: u1
        enum: govee_common::color_decode
        doc: sub-mode 0x0d
      - id: c14
        type: u1
        enum: govee_common::color_decode
        doc: sub-mode 0x14 (straightfloorlamp)
      - id: c15
        type: u1
        enum: govee_common::color_decode
        doc: sub-mode 0x15
      - id: music
        type: u1
        enum: govee_common::music_decode
        doc: sub-mode 0x13
      - id: empty_fails
        type: bool
        doc: |
          the screen's 0x05 parser reads the value unguarded, so an EMPTY value
          throws there and fails the whole status read, and so does a value
          shorter than the bytes its decode reads (H60A6 / H60A1 / H60A4 /
          H12D0: h60a1 Info4Detail.Q(value, true) reads value[0] first, then
          the default SubMode4Color / SubMode4Scene / SubMode4Diy /
          SubMode4MusicV2 strategies). govee_state.app_read_fails uses it.
          False where the screen is not traced (Q4 default: not_decoded).
        doc-ref:
          - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:380-433
          - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:82-83
