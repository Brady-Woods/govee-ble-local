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
  CONTEXT (not a byte source): the per-device facts a frame does not carry
  (0 = unknown; all zeros = GENERIC). PROCEDURAL: the library resolves it.
params:
  - id: forms
    type: command_forms
  - id: counts
    type: device_counts
  - id: group
    type: color_group_form
  - id: power
    type: power_rule
  - id: mode
    type: mode_decode
    doc: aa 05 reply decode
  - id: mode_status
    type: mode_decode
    doc: 0xAC TLV 0x05 decode (= mode unless mode_color_parse_status is set)
types:
  command_forms:
    doc: |
      CONTEXT: one selector per overloaded command byte or write layout.
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
      - id: f_34
        type: u1
        enum: govee_common::form_34
        doc: aa 34 (the H6057 device detail; H6047's 33 34 is a bar swap)
      - id: f_36
        type: u1
        enum: govee_common::form_36
        doc: 33 36, aa 36, TLV 0x36 (bar pair vs bar index vs worry-free)
      - id: f_40
        type: u1
        enum: govee_common::form_40
        doc: |
          TLV 0x40, the status screen's read (IC pair vs one count vs device
          count; the value is typed under every form)
      - id: f_41
        type: u1
        enum: govee_common::form_41
        doc: aa 41, TLV 0x41 (power memory vs section count)
      - id: f_a2
        type: u1
        enum: govee_common::form_a2
        doc: aa a2 (H61A8 batch vs colour-only group)
      - id: f_a3
        type: u1
        enum: govee_common::form_a3
        doc: |
          TLV 0xA3, the status screen's gradual-flag read (the value is typed
          under every form)
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
        doc: segment-mask length (piece count; brightness rounds up to 8s)
      - id: num_levels
        type: u1
        doc: level count of a 33 05 15 03 brightness-levels write
  color_group_form:
    doc: |
      CONTEXT: how a colour-group reply (aa a5 / aa a2 / TLV 0xA5) is counted,
      as the reply does not carry its count.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:145-175
    params:
      - id: rule
        type: u1
        enum: govee_common::group_count_rule
      - id: num_per_group
        type: u1
        doc: records per full group (4 on every curated SKU)
        doc-ref:
          - split_h60ax/com/govee/pact_h60a0/pact/Support.java:183-185
          - split_h6047/com/govee/h6047/pact/Support.java:155-157
          - split_h61d3/com/govee/h61d3/pact/H61D3Support.java:1576-1578
      - id: has_brightness
        type: bool
        doc: 0xA5 records [brightness R G B] if true, else [R G B]; aa a2 is RGB
        doc-ref:
          - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:37-49
      - id: num_pieces
        type: u2
        doc: colour-piece count (13 on a part-colour H60A6) or live; 0 = unknown
        doc-ref:
          - split_h60ax/com/govee/pact_h60a0/pact/Support.java:154-159
  power_rule:
    doc: |
      CONTEXT: when the aa 01 reply byte means on, at the connect read and
      in the aa 01 heartbeat, and when the advert's switch byte does: a light
      (masks 0) at byte == 1, or at any non-zero aa 01 byte under
      light_nonzero / light_heartbeat_nonzero; a plug (relay bitmask, bit i =
      relay i) at (byte & mask) != 0.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerSwitch.java:32
      - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:29-45
    params:
      - id: relay_on_mask
        type: u1
        doc: |
          connect-read mask; 0 = a light (on when the byte == 1, or != 0
          under light_nonzero)
        doc-ref:
          - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:167
          - split_plugnew/com/govee/h5080/ble/controller/EventSwitchV2.java:48-59
          - split_plugnew/com/govee/h5080/adjust/v4_h5160/BleOpV4.java:464-481
      - id: relay_heartbeat_on_mask
        type: u1
        doc: aa 01 heartbeat mask; 0 = the same as relay_on_mask
        doc-ref:
          - split_plugnew/com/govee/h5080/ble/controller/HeartControllerV2.java:17-35
          - split_plugnew/com/govee/h5080/adjust/v2/BleOpV2.java:216-231
          - split_plugnew/com/govee/h5080/adjust/h5089/BleOp4H5089.java:331-341
      - id: light_nonzero
        type: bool
        doc: |
          a light's (masks 0) connect read is on at any non-zero byte, as the
          legacy SwitchController and the H70BX connect read decode it; false =
          at == 1
        doc-ref:
          - base-classes10/com/govee/base2light/ble/controller/SwitchController.java:29-33
          - split_h70b1/com/govee/h70bx/controller/AfterConnectSuccessReadDeviceInfo.java:176-178
      - id: light_heartbeat_nonzero
        type: bool
        doc: |
          a light's (masks 0) aa 01 heartbeat is on at any non-zero byte, as
          HeartControllerWithBattery decodes it where the connect read takes
          == 1 (H1168's Compose4ReadAllInfo); false = as light_nonzero
        doc-ref:
          - base-classes10/com/govee/base2light/ble/controller/HeartControllerWithBattery.java:85-90
          - base-classes3/com/govee/home/main/device/moment/musicfeast/h1168/VM4MusicFeast.java:1418-1440
      - id: advert_on_mask
        type: u1
        doc: |
          the device list's advert switch-byte mask (devices.yaml
          advert_on_mask); 0 = a light (on when the byte == 1)
        doc-ref:
          - base-classes3/com/govee/kt/ui/device/base/AbsBaseBleModel.java:393-407
          - split_plugnew/com/govee/h5080/pact/Model.java:254-266
          - split_plugnew/com/govee/h5080/pact/Model4Plug2.java:269-290
  mode_decode:
    doc: |
      CONTEXT: the screen's mode-report decode per colour sub-mode cXX (aa 05
      reply or TLV 0x05); not_decoded leaves the bytes in the message's rest.
    params:
      - id: c02
        type: u1
        enum: govee_common::color_decode
      - id: c0b
        type: u1
        enum: govee_common::color_decode
      - id: c0d
        type: u1
        enum: govee_common::color_decode
      - id: c14
        type: u1
        enum: govee_common::color_decode
        doc: sub-mode 0x14 (straightfloorlamp)
      - id: c15
        type: u1
        enum: govee_common::color_decode
      - id: music
        type: u1
        enum: govee_common::music_decode
        doc: sub-mode 0x13
      - id: empty_fails
        type: bool
        doc: an empty or short 0x05 value fails the read; false where untraced
        doc-ref:
          - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:380-433
          - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:82-83
