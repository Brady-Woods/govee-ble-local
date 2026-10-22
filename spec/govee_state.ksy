meta:
  id: govee_state
  title: Govee device-state record (aa reply body or 0xAC TLV value)
  license: MIT
  ks-version: '0.11'
  imports:
    - govee_common
    - govee_profile
  bit-endian: le
  endian: be
doc: |
  SHARED PAYLOAD (not an entry point): an `aa <command>` reply body or a 0xAC
  status TLV value with tag <command>, one message in two envelopes (source).
doc-ref:
  - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:217-244
  - base-classes10/com/govee/base2light/ble/comm/ComposeBleComm.java:198
  - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:247-345
params:
  - id: command
    type: u1
    enum: govee_common::command
    doc: the reply's command byte, or the TLV's tag
  - id: source
    type: u1
    enum: source
    doc: the envelope
  - id: profile
    type: govee_profile
    doc: the device's decode context
seq:
  - id: body
    type:
      switch-on: command
      cases:
        'govee_common::command::heartbeat': heartbeat
        'govee_common::command::power': power_state(source, profile.power)
        'govee_common::command::brightness': govee_common::level
        'govee_common::command::mode': mode_report(decode)
        'govee_common::command::soft_version': govee_common::version_text
        'govee_common::command::device_info': device_info_record(source)
        'govee_common::command::cmd_12': cmd_12_state(profile.forms.f_12)
        'govee_common::command::cmd_13': cmd_13_state(profile.forms.f_13)
        'govee_common::command::cmd_14': cmd_14_state(profile.forms.f_14)
        'govee_common::command::wifi_hard_version': govee_common::version_text
        'govee_common::command::wifi_soft_version': govee_common::version_text
        'govee_common::command::new_timer': timer_slots
        'govee_common::command::cmd_30': >-
          cmd_30_state(profile.forms.f_30, source)
        'govee_common::command::cmd_34': >-
          cmd_34_state(profile.forms.f_34, source)
        'govee_common::command::cmd_36': >-
          cmd_36_state(profile.forms.f_36, source)
        'govee_common::command::ic_num': ic_count
        'govee_common::command::cmd_41': >-
          cmd_41_state(profile.forms.f_41, source)
        'govee_common::command::color_group_rgb': >-
          cmd_a2_state(profile.forms.f_a2, profile.group, source)
        'govee_common::command::gradual': gradual_state
        'govee_common::command::color_group': >-
          cmd_a5_state(profile.forms.f_a5, profile.group, source)
        'govee_common::command::cmd_b0': cmd_b0_state(profile.forms.f_b0)
        'govee_common::command::secret_read': secret_state
        'govee_common::command::plug_spec': plug_spec
        'govee_common::command::pact': pact_state
        'govee_common::command::debug': debug_state
    if: not _io.eof and (source == source::read_reply or is_tlv_typed)
    doc: the typed record; absent when empty or untyped (bytes in rest)
  - id: rest
    type: govee_common::echo
    doc: every byte after the record (request echo, stale bytes, zero fill)
instances:
  decode:
    value: 'source == source::status_tlv ? profile.mode_status : profile.mode'
    doc: the mode decode for this envelope
  is_tlv_typed:
    value: >-
      command == govee_common::command::heartbeat
      or command == govee_common::command::power
      or command == govee_common::command::brightness
      or command == govee_common::command::mode
      or command == govee_common::command::device_info
      or command == govee_common::command::new_timer
      or command == govee_common::command::cmd_30
      or command == govee_common::command::cmd_36
      or command == govee_common::command::ic_num
      or command == govee_common::command::cmd_41
      or command == govee_common::command::gradual
      or command == govee_common::command::color_group
    doc: TLV tags whose value has a traced app reader; other tags stay in rest
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:286-341
  app_empty_mode_fails:
    value: >-
      source == source::status_tlv and _io.size == 0
      and command == govee_common::command::mode and decode.empty_fails
    doc: |
      FIRMWARE-SOURCED: the empty TLV 0x05 value H60A6 sends on purpose in mode
      0xA0 (music: a 33 05 13 write or an A5 02 83 mic colour push) and in every
      other mode but 04, 0a, 0d, 13 and 15. Info4Detail.Q reads a first byte unguarded, so the app's read
      fails: one term of app_read_fails. PROCEDURAL: the client reads past
      this one failure, leaving the mode unknown, and fails on every other
      app_read_fails.
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:380-384
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:82-83
      - H60A6 FUN_9b025dc8 @0x9b025dd2-0x9b025ddc
      - H60A6 FUN_9b025dc8 @0x9b025de4-0x9b025e24
      - H60A6 FUN_9b04bdd8 @0x9b04be74-0x9b04be84
      - H60A6 FUN_9b03c6b0 @0x9b03c702-0x9b03c762
      - H60A6 FUN_9b025124 @0x9b02570e
  app_read_fails:
    value: >-
      app_empty_mode_fails or (source == source::status_tlv and (_io.size == 0 ? (
      command == govee_common::command::power
      or command == govee_common::command::brightness
      or command == govee_common::command::device_info
      or command == govee_common::command::new_timer
      or (command == govee_common::command::gradual
      and profile.forms.f_a3 == govee_common::form_a3::gradual)
      or (command == govee_common::command::ic_num
      and profile.forms.f_40 != govee_common::form_40::unknown)
      or (command == govee_common::command::cmd_30
      and profile.forms.f_30 != govee_common::form_30::unknown)
      or (command == govee_common::command::cmd_36
      and profile.forms.f_36 == govee_common::form_36::bar_index)
      or (command == govee_common::command::cmd_41
      and profile.forms.f_41 == govee_common::form_41::power_memory)
      or (command == govee_common::command::color_group
      and profile.forms.f_a5 == govee_common::form_a5::color_group)
      ) : (
      (command == govee_common::command::mode and decode.empty_fails
      and body.as<mode_report>.is_short)
      or (command == govee_common::command::new_timer
      and body.as<timer_slots>.is_short)
      or (command == govee_common::command::ic_num
      and profile.forms.f_40 == govee_common::form_40::ic_pair
      and body.as<ic_count>.app_pair_fails)
      or (command == govee_common::command::ic_num
      and profile.forms.f_40 == govee_common::form_40::one_count
      and body.as<ic_count>.is_short)
      or (command == govee_common::command::color_group
      and profile.forms.f_a5 == govee_common::form_a5::color_group
      and body.as<cmd_a5_state>.body.as<color_group>.app_read_fails))))
    doc: status TLV only; the app's whole status read fails on this value
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:286-341,347-353
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:380-433
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:94-124
types:
  heartbeat:
    doc: |
      AA 00 reply / TLV tag 0: no record (H60A1 / H60A6 echo the request).
    doc-ref:
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:241-242
      - firmware-h60a1-wifi/decompiled/H60A1_ble_dispatcher.c:559-560
  power_state:
    doc: |
      AA 01 reply / TLV 0x01: the power byte (a light's on flag or a plug's
      relay bitmask), then one aux byte in a reply.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerSwitch.java:32
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:286-288
      - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:29-45
    params:
      - id: source
        type: u1
        enum: source
      - id: power
        type: govee_profile::power_rule
    seq:
      - id: state
        type: u1
        doc: a light's 1 / 0, or a plug's relay bitmask (bit i = relay i)
      - id: aux
        type: u1
        if: source == source::read_reply and _io.size >= 2
        doc: H6047's feast / group flag when on; elsewhere the request echo
        doc-ref:
          - H6047 handler @0x01004f32
    instances:
      is_on:
        value: >-
          source == source::status_tlv ? state != 0 :
          (power.relay_on_mask == 0 ? state == 1 :
          (state & power.relay_on_mask) != 0)
        doc: on at the connect read (reply) or in the status TLV
      is_on_heartbeat:
        value: >-
          power.relay_heartbeat_on_mask == 0 ? is_on :
          (state & power.relay_heartbeat_on_mask) != 0
        doc: on in the aa 01 heartbeat (relay_heartbeat_on_mask when set)
        doc-ref:
          - split_plugnew/com/govee/h5080/adjust/v2/BleOpV2.java:216-217
          - split_plugnew/com/govee/h5080/adjust/h5089/BleOp4H5089.java:331-332
      relay0_is_on:
        value: (state & 0x01) != 0
        doc: relay 0 (outlet 0); the only bit H5083 firmware sets
        doc-ref:
          - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:162-167
          - split_plugnew/com/govee/h5080/adjust/v1/BleOpV1.java:217,350
          - H5083 FUN_9b021668 @0x9b021668
      relay1_is_on:
        value: (state & 0x02) != 0
        doc: outlet 1 of the two-outlet plugs and of goodsType 90
        doc-ref:
          - split_plugnew/com/govee/h5080/adjust/v2/BleOpV2.java:370
          - split_plugnew/com/govee/h5080/adjust/h5089/BleOp4H5089.java:464
          - split_plugnew/com/govee/h5080/adjust/v4_h5160/BleOpV4.java:477
      relay2_is_on:
        value: (state & 0x04) != 0
        doc: outlet 2 of goodsType 90 (three outlets)
        doc-ref:
          - split_plugnew/com/govee/h5080/adjust/v4_h5160/BleOpV4.java:464-481
          - split_plugnew/com/govee/h5080/pact/Support.java:126-131
      relay3_is_on:
        value: (state & 0x08) != 0
        doc: counts only in the gt 50 / 307 connect-read OR
        doc-ref:
          - split_plugnew/com/govee/h5080/ble/controller/EventSwitchV2.java:48-59
      relay4_is_on:
        value: (state & 0x10) != 0
        doc: counts only in the gt 50 / 307 connect-read OR
      relay5_is_on:
        value: (state & 0x20) != 0
        doc: counts only in the gt 50 / 307 connect-read OR
      relay6_is_on:
        value: (state & 0x40) != 0
        doc: counts only in the gt 50 / 307 connect-read OR
      relay7_is_on:
        value: (state & 0x80) != 0
        doc: counts only in the gt 50 / 307 connect-read OR
  mode_report:
    doc: |
      AA 05 reply / TLV 0x05: [sub_mode][params], the params laid out by the
      profile's mode_decode for that sub-mode.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:283-284,300-308
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:380-433
      - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:755-761
    params:
      - id: decode
        type: govee_profile::mode_decode
    seq:
      - id: sub_mode
        type: u1
        enum: govee_common::sub_mode
      - id: params
        type:
          switch-on: sub_mode
          cases:
            'govee_common::sub_mode::scene': scene_report(len_params)
            'govee_common::sub_mode::diy': scene_report(len_params)
            'govee_common::sub_mode::color_legacy': >-
              color_report(decode.c02, len_params)
            'govee_common::sub_mode::color_rgbic_0b': >-
              color_report(decode.c0b, len_params)
            'govee_common::sub_mode::color_cct_0d': >-
              color_report(decode.c0d, len_params)
            'govee_common::sub_mode::color_floor_lamp_14': >-
              color_report(decode.c14, len_params)
            'govee_common::sub_mode::color_rgbic_15': >-
              color_report(decode.c15, len_params)
            'govee_common::sub_mode::music': >-
              music_report(decode.music, len_params)
        doc: absent for another sub-mode (bytes in rest)
    instances:
      len_params:
        value: _io.size - 1
      is_color_sub_mode:
        value: >-
          sub_mode == govee_common::sub_mode::color_rgbic_15
          or sub_mode == govee_common::sub_mode::color_rgbic_0b
          or sub_mode == govee_common::sub_mode::color_legacy
          or sub_mode == govee_common::sub_mode::color_cct_0d
        doc: the sub-modes the newdetail parser hands to the colour strategy
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:766-779
          - split_h6062/com/govee/striplight/newdetail/OpInfo4Detail.java:428
          - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:399,416
      is_short:
        value: >-
          (sub_mode == govee_common::sub_mode::scene
          or sub_mode == govee_common::sub_mode::diy)
          ? params.as<scene_report>.is_short :
          ((sub_mode == govee_common::sub_mode::color_legacy
          or sub_mode == govee_common::sub_mode::color_rgbic_0b
          or sub_mode == govee_common::sub_mode::color_cct_0d
          or sub_mode == govee_common::sub_mode::color_floor_lamp_14
          or sub_mode == govee_common::sub_mode::color_rgbic_15)
          ? params.as<color_report>.is_short :
          (sub_mode == govee_common::sub_mode::music
          ? params.as<music_report>.is_short : false))
        doc: a byte the decode reads is missing (H60A6 sends 5-byte music)
  scene_report:
    doc: |
      Params of sub-mode 0x04 (scene) or 0x0a (DIY): [code u16le][music_code
      u16le].
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4Scene.java:24-29
      - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4Diy.java:36-40
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:575-586,617-633
    params:
      - id: len_params
        type: u2
    seq:
      - id: code
        type: u2le
        if: len_params >= 2
      - id: music_code
        type: u2le
        if: len_params >= 4
        doc: the music code; on H6006 / H6008 the high half of a u32le scene
    instances:
      diy_code:
        value: 'code >= 0x8000 ? code - 0x10000 : code'
        if: len_params >= 2
        doc: the code read signed, as the DIY strategy stores it
      is_short:
        value: len_params < 2
        doc: the app reads two bytes unguarded (it throws)
  color_report:
    doc: |
      Params of a colour sub-mode under the screen's decode `kind`
      (devices.yaml mode_color_decode); not_decoded leaves them in rest.
    params:
      - id: kind
        type: u1
        enum: govee_common::color_decode
      - id: len_params
        type: u2
    seq:
      - id: layout
        type:
          switch-on: kind
          cases:
            'govee_common::color_decode::gradual_only': gradual_only(len_params)
            'govee_common::color_decode::gradual_kelvin_s16be': >-
              gradual_kelvin(cct_test::kelvin_positive, len_params)
            'govee_common::color_decode::gradual_kelvin_s16be_nocct': >-
              gradual_kelvin(cct_test::none, len_params)
            'govee_common::color_decode::kelvin_s16be': >-
              kelvin_only(cct_test::kelvin_positive, len_params)
            'govee_common::color_decode::skip_kelvin_s16be_illumination': >-
              skip_kelvin(cct_test::none, len_params)
            'govee_common::color_decode::gradual_kelvin_pair_s16be': >-
              kelvin_pair(true, cct_test::pair_equal, len_params)
            'govee_common::color_decode::skip_kelvin_pair_s16be': >-
              kelvin_pair(false, cct_test::pair_equal, len_params)
            'govee_common::color_decode::rgb_kelvin_pair_s16be': >-
              rgb_kelvin_pair(len_params)
            'govee_common::color_decode::gradual_kelvin_triple_s16be': >-
              kelvin_triple(cct_test::none, len_params)
            'govee_common::color_decode::gradual_kelvin_triple_s16be_nocct': >-
              kelvin_triple(cct_test::none, len_params)
            'govee_common::color_decode::rgb_kelvin_tint': >-
              rgb_kelvin_tint(false, len_params)
            'govee_common::color_decode::rgb_kelvin_tint_positive': >-
              rgb_kelvin_tint(true, len_params)
            'govee_common::color_decode::rgb_kelvin_0d': >-
              rgb_kelvin(cct_test::kelvin_positive, len_params)
            'govee_common::color_decode::rgb_kelvin_0d_nocct': >-
              rgb_kelvin(cct_test::none, len_params)
            'govee_common::color_decode::rgb': rgb_only(len_params)
            'govee_common::color_decode::rgb_flag1_tint': >-
              rgb_flag_tint(cct_test::flag_eq_1, len_params)
            'govee_common::color_decode::rgb_flagnz_tint': >-
              rgb_flag_tint(cct_test::flag_nonzero, len_params)
            'govee_common::color_decode::rgb_flag_tint_nokelvin': >-
              rgb_flag_tint(cct_test::flag_nonzero, len_params)
            'govee_common::color_decode::rgb_pair_flag_lookup': >-
              rgb_flag_tint(cct_test::none, len_params)
            'govee_common::color_decode::bri_rgb_pair': bri_rgb_pair(len_params)
    instances:
      is_short:
        value: >-
          kind == govee_common::color_decode::gradual_only
          ? layout.as<gradual_only>.is_short :
          (kind == govee_common::color_decode::gradual_kelvin_s16be
          or kind == govee_common::color_decode::gradual_kelvin_s16be_nocct)
          ? layout.as<gradual_kelvin>.is_short :
          kind == govee_common::color_decode::kelvin_s16be
          ? layout.as<kelvin_only>.is_short :
          kind == govee_common::color_decode::skip_kelvin_s16be_illumination
          ? layout.as<skip_kelvin>.is_short :
          (kind == govee_common::color_decode::gradual_kelvin_pair_s16be
          or kind == govee_common::color_decode::skip_kelvin_pair_s16be)
          ? layout.as<kelvin_pair>.is_short :
          kind == govee_common::color_decode::rgb_kelvin_pair_s16be
          ? layout.as<rgb_kelvin_pair>.is_short :
          (kind == govee_common::color_decode::gradual_kelvin_triple_s16be
          or kind ==
          govee_common::color_decode::gradual_kelvin_triple_s16be_nocct)
          ? layout.as<kelvin_triple>.is_short :
          (kind == govee_common::color_decode::rgb_kelvin_tint
          or kind == govee_common::color_decode::rgb_kelvin_tint_positive)
          ? layout.as<rgb_kelvin_tint>.is_short :
          (kind == govee_common::color_decode::rgb_kelvin_0d
          or kind == govee_common::color_decode::rgb_kelvin_0d_nocct)
          ? layout.as<rgb_kelvin>.is_short :
          kind == govee_common::color_decode::rgb
          ? layout.as<rgb_only>.is_short :
          (kind == govee_common::color_decode::rgb_flag1_tint
          or kind == govee_common::color_decode::rgb_flagnz_tint
          or kind == govee_common::color_decode::rgb_flag_tint_nokelvin
          or kind == govee_common::color_decode::rgb_pair_flag_lookup)
          ? layout.as<rgb_flag_tint>.is_short :
          kind == govee_common::color_decode::bri_rgb_pair
          ? layout.as<bri_rgb_pair>.is_short : false
        doc: the layout's is_short; false when the decode is not_decoded
  gradual_only:
    doc: |
      [gradual]: decode gradual_only; no Kelvin or colour (the screen's colour
      comes from its 0xA5 reads).
    params:
      - id: len_params
        type: u2
    seq:
      - id: gradual
        type: u1
        if: len_params >= 1
    instances:
      is_gradual_on:
        value: len_params >= 1 and gradual == 1
      is_cct:
        value: 'false'
      is_short:
        value: len_params < 1
  gradual_kelvin:
    doc: |
      [gradual][kelvin s16be]: decodes gradual_kelvin_s16be (test
      kelvin_positive) and gradual_kelvin_s16be_nocct (test none).
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4Color.java:42-49,239-241
      - base-classes10/com/govee/base2light/ble/BleUtil.java:304-306
      - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:766-779
    params:
      - id: test
        type: u1
        enum: cct_test
      - id: len_params
        type: u2
    seq:
      - id: gradual
        type: u1
        if: len_params >= 1
      - id: kelvin
        type: s2
        if: len_params >= 3
        doc: colour temperature, read signed (0x8000 and up are negative)
    instances:
      is_gradual_on:
        value: len_params >= 1 and gradual == 1
      is_cct:
        value: >-
          test == cct_test::kelvin_positive and len_params >= 3 and kelvin > 0
      is_short:
        value: len_params < 3
  kelvin_only:
    doc: |
      [kelvin s16be] with no gradual byte: decode kelvin_s16be (bulblightv3
      H601E / H601F).
    doc-ref:
      - split_bulblightstringv1/com/govee/bulblightv3/newdetail/Info4Detail.java:160-167
    params:
      - id: test
        type: u1
        enum: cct_test
      - id: len_params
        type: u2
    seq:
      - id: kelvin
        type: s2
        if: len_params >= 2
    instances:
      is_cct:
        value: >-
          test == cct_test::kelvin_positive and len_params >= 2 and kelvin > 0
      is_short:
        value: len_params < 2
  skip_kelvin:
    doc: |
      [unused byte][kelvin s16be]: decode skip_kelvin_s16be_illumination
      (H60A0); the Kelvin is the white light's, never a device CCT.
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a0/Info4DetailH60A0.java:123-130
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a0/Info4DetailH60A0.java:399-406
    params:
      - id: test
        type: u1
        enum: cct_test
      - id: len_params
        type: u2
    seq:
      - id: skip
        type: u1
        if: len_params >= 1
        doc: body byte 0, stored but never used
      - id: kelvin
        type: s2
        if: len_params >= 3
    instances:
      is_cct:
        value: >-
          test == cct_test::kelvin_positive and len_params >= 3 and kelvin > 0
      is_short:
        value: len_params < 3
  kelvin_pair:
    doc: |
      [byte 0][kelvin A s16be][kelvin B s16be]: gradual_kelvin_pair_s16be
      (H6047, byte 0 = gradual flag) or skip_kelvin_pair_s16be (byte 0 skipped).
    doc-ref:
      - split_h6047/com/govee/h6047/detail/Info4Detail.java:174-184
      - split_h6047/com/govee/h6047/detail/NewDetailVm.java:616-652
      - split_tvlightv4/com/govee/pact_tvlightv3/detail/Info4Detail.java:176-183
    params:
      - id: has_gradual
        type: bool
        doc: byte 0 is the gradual flag (true) or a skipped byte
      - id: test
        type: u1
        enum: cct_test
      - id: len_params
        type: u2
    seq:
      - id: gradual
        type: u1
        if: len_params >= 1
      - id: kelvin
        type: s2
        if: len_params >= 3
        doc: Kelvin A (H6047 left bar, segments 0-4)
      - id: kelvin_b
        type: s2
        if: len_params >= 5
        doc: Kelvin B (H6047 right bar, segments 5-9)
    instances:
      is_gradual_on:
        value: has_gradual and len_params >= 1 and gradual == 1
      is_cct:
        value: >-
          test == cct_test::pair_equal and len_params >= 5
          and kelvin == kelvin_b and kelvin > 0
      is_short:
        value: len_params < 5
  rgb_kelvin_pair:
    doc: |
      [rgb][kelvin s16be] x 2: decode rgb_kelvin_pair_s16be (old TV backlight);
      each half shows its Kelvin's tint when the Kelvin is not 0, else its RGB.
    doc-ref:
      - split_tvlightv4/com/govee/pact_tvlightv3/detail/Info4Detail.java:185-202
      - split_tvlightv4/com/govee/pact_tvlightv4/detail/Info4Detail.java:185-202
    params:
      - id: len_params
        type: u2
    seq:
      - id: color
        type: govee_common::rgb
        if: len_params >= 3
      - id: kelvin
        type: s2
        if: len_params >= 5
      - id: color_b
        type: govee_common::rgb
        if: len_params >= 8
      - id: kelvin_b
        type: s2
        if: len_params >= 10
    instances:
      is_cct:
        value: >-
          len_params >= 10 and kelvin == kelvin_b and kelvin != 0
      is_short:
        value: len_params < 10
  kelvin_triple:
    doc: |
      [gradual][kelvin A][kelvin B][kelvin C] s16be: gradual_kelvin_triple_s16be
      (pact_h605b) and its _nocct twin; never a whole-device CCT.
    doc-ref:
      - split_h6099/com/govee/pact_h605b/newdetail/Info4Detail.java:179-185
      - split_h6099/com/govee/pact_h605b/newdetail/Info4Detail.java:284-303
    params:
      - id: test
        type: u1
        enum: cct_test
      - id: len_params
        type: u2
    seq:
      - id: gradual
        type: u1
        if: len_params >= 1
      - id: kelvin
        type: s2
        if: len_params >= 3
      - id: kelvin_b
        type: s2
        if: len_params >= 5
      - id: kelvin_c
        type: s2
        if: len_params >= 7
    instances:
      is_gradual_on:
        value: len_params >= 1 and gradual == 1
      is_cct:
        value: >-
          test == cct_test::kelvin_positive and len_params >= 3 and kelvin > 0
      is_short:
        value: len_params < 7
  rgb_only:
    doc: |
      [r g b]: decode rgb; the Kelvin bytes are never read, never a CCT.
    doc-ref:
      - split_hollowlamp/com/govee/tablelampv1/newAdjust/h6052/H6052InfoDetail.java:113-121
      - split_rgblight/com/govee/rgblight/mode/ModeParseStrategyConfig.java:67-77
    params:
      - id: len_params
        type: u2
    seq:
      - id: color
        type: govee_common::rgb
        if: len_params >= 3
    instances:
      is_cct:
        value: 'false'
      is_short:
        value: len_params < 3
  rgb_kelvin:
    doc: |
      [r g b][kelvin s16be]: decodes rgb_kelvin_0d (CCT when kelvin > 0) and
      rgb_kelvin_0d_nocct (h70bx, always the RGB).
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:416-427
      - split_h6061/com/govee/cubelight/newdetails/CubeNewInfo4Detail.java:141-153,503-517
      - split_h70b1/com/govee/h70bx/Info4Detail.java:125-133
    params:
      - id: test
        type: u1
        enum: cct_test
      - id: len_params
        type: u2
    seq:
      - id: color
        type: govee_common::rgb
        if: len_params >= 3
      - id: kelvin
        type: s2
        if: len_params >= 5
    instances:
      is_cct:
        value: >-
          test == cct_test::kelvin_positive and len_params >= 5 and kelvin > 0
      is_short:
        value: len_params < 5
  rgb_kelvin_tint:
    doc: |
      [r g b][kelvin s16be][tint r g b]: decodes rgb_kelvin_tint and
      rgb_kelvin_tint_positive (the tint read only when kelvin > 0).
    doc-ref:
      - split_rgblight/com/govee/rgblight/mode/ModeParseStrategyConfig.java:35-46
      - split_hollowlamp/com/govee/hollowlamp/newdetails/h60501/H60501InfoDetail.java:121-148
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:634-650
    params:
      - id: tint_if_positive
        type: bool
      - id: len_params
        type: u2
    seq:
      - id: color
        type: govee_common::rgb
        if: len_params >= 3
      - id: kelvin
        type: s2
        if: len_params >= 5
      - id: tint
        type: govee_common::rgb
        if: len_params >= 8 and (not tint_if_positive or kelvin > 0)
        doc: the Kelvin's tint colour; H6006 / H6008 firmware sends zeros
    instances:
      is_cct:
        value: len_params >= 5 and kelvin > 0
      shows_tint:
        value: >-
          len_params >= 5 and (tint_if_positive ? kelvin > 0 : kelvin != 0)
        doc: the strategy shows the tint, not r/g/b
      is_short:
        value: >-
          len_params < 5 or
          (len_params < 8 and (not tint_if_positive or kelvin > 0))
  rgb_flag_tint:
    doc: |
      [r g b][flag][tint r g b], no Kelvin bytes: decodes rgb_flag1_tint,
      rgb_flagnz_tint, rgb_flag_tint_nokelvin and rgb_pair_flag_lookup.
    doc-ref:
      - split_rgblight/com/govee/rgblight/mode/ModeParseStrategyConfig.java:52-62
      - split_rgblight/com/govee/h6104/newdetail/Info4Detail.java:165-176
      - base-classes10/com/govee/base2light/ble/controller/AbsMode.java:17-23
    params:
      - id: test
        type: u1
        enum: cct_test
      - id: len_params
        type: u2
    seq:
      - id: color
        type: govee_common::rgb
        if: len_params >= 3
      - id: flag
        type: u1
        if: len_params >= 4
      - id: tint
        type: govee_common::rgb
        if: >-
          len_params >= 7 and (test != cct_test::flag_eq_1 or flag == 1)
        doc: the tint (colour B of rgb_pair_flag_lookup)
    instances:
      is_cct:
        value: >-
          test == cct_test::flag_eq_1 ? (len_params >= 4 and flag == 1) :
          (test == cct_test::flag_nonzero and len_params >= 4 and flag != 0)
      uses_tint:
        value: len_params >= 4 and flag != 0
        doc: the flag selects the tint (colour B)
      is_short:
        value: >-
          len_params < 4 or
          (len_params < 7 and (test != cct_test::flag_eq_1 or flag == 1))
  bri_rgb_pair:
    doc: |
      [bri1 s8][r1 g1 b1][bri2 s8][r2 g2 b2]: decode bri_rgb_pair (goodsType
      98), one record per lamp head, never a colour temperature.
    doc-ref:
      - split_h7050/com/govee/h7050/adjust/newAdjust/h60616263/H70606162Info4Detail.java:247-261
      - split_h7050/com/govee/h7050/adjust/newAdjust/h60616263/H70606162Info4Detail.java:375-427
    params:
      - id: len_params
        type: u2
    seq:
      - id: bri
        type: s1
        if: len_params >= 1
      - id: color
        type: govee_common::rgb
        if: len_params >= 4
      - id: bri_b
        type: s1
        if: len_params >= 5
      - id: color_b
        type: govee_common::rgb
        if: len_params >= 8
    instances:
      is_cct:
        value: 'false'
      is_short:
        value: len_params < 8
  music_report:
    doc: |
      Params of sub-mode 0x13 (music) under the screen's music decode;
      not_decoded leaves them in rest.
    params:
      - id: kind
        type: u1
        enum: govee_common::music_decode
      - id: len_params
        type: u2
    seq:
      - id: layout
        type:
          switch-on: kind
          cases:
            'govee_common::music_decode::v1_flagged': >-
              music_v1_flagged(len_params)
            'govee_common::music_decode::v2_default': >-
              music_v2_default(len_params)
    instances:
      is_short:
        value: >-
          kind == govee_common::music_decode::v1_flagged
          ? layout.as<music_v1_flagged>.is_short :
          (kind == govee_common::music_decode::v2_default
          ? layout.as<music_v2_default>.is_short : false)
  music_v1_flagged:
    doc: |
      Decode v1_flagged: [music_code][value][dynamic][spec_color_flag], then
      [r][g][b] only when the flag is not 0 (replies are zero-padded).
    doc-ref:
      - split_rgbiclight/com/govee/dreamcolorlightv1/ble/SubModeMusicV1.java:87-98
    params:
      - id: len_params
        type: u2
    seq:
      - id: music_code
        type: u1
        if: len_params >= 1
      - id: value
        type: u1
        if: len_params >= 2
        doc: the sensitivity (the app clamps it to 0..99)
      - id: dynamic
        type: u1
        if: len_params >= 3
      - id: spec_color_flag
        type: u1
        if: len_params >= 4
        doc: not 0 = a specified colour follows
      - id: color
        type: govee_common::rgb
        if: len_params >= 7 and spec_color_flag != 0
    instances:
      sensitivity:
        value: 'value > 99 ? 99 : value'
        if: len_params >= 2
      is_short:
        value: len_params < 4 or (spec_color_flag != 0 and len_params < 7)
  music_v2_default:
    doc: |
      Decode v2_default: [effect_code][sensitivity], then, unless a new-music
      code: [sub_effect_code (code 4)][color_flag][r g b when color_flag != 0].
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4MusicV2.java:49-73,76-78,163-167
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:122-125
      - base-classes10/com/govee/base2light/ble/music/AbsNewMusicEffect.java:114-116
    params:
      - id: len_params
        type: u2
    seq:
      - id: effect_code
        type: u1
        if: len_params >= 1
      - id: sensitivity_raw
        type: u1
        if: len_params >= 2
      - id: sub_effect_code
        type: u1
        if: len_params >= 3 and not is_new_music and effect_code == 4
      - id: color_flag
        type: u1
        if: len_params >= 3 and len_params > ofs_flag and not is_new_music
        doc: not 0 = a colour follows
      - id: color
        type: govee_common::rgb
        if: >-
          len_params >= 6 and len_params >= ofs_flag + 4 and not is_new_music
          and color_flag != 0
    instances:
      is_new_music:
        value: >-
          len_params >= 1 and (effect_code == 49 or effect_code == 71
          or effect_code == 72 or effect_code == 81 or effect_code == 113)
        doc: the strategy stops after the first two bytes
      ofs_flag:
        value: 'effect_code == 4 ? 3 : 2'
        doc: the colour flag's offset in the params
      sensitivity:
        value: 'sensitivity_raw > 99 ? 99 : sensitivity_raw'
        if: len_params >= 2
      is_short:
        value: >-
          len_params < 2 or (not is_new_music and (len_params <= ofs_flag
          or (color_flag != 0 and len_params < ofs_flag + 4)))
  device_info_record:
    doc: |
      AA 07 reply / TLV 0x07: [selector][info]; the TLV reads info only for
      selectors 0x10 / 0x11 (other bytes stay in rest).
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:370-437
      - split_plugnew/com/govee/h5086/ble/controller/ReadInfoController.java:64
      - base-classes10/com/govee/base2light/ble/controller/McuSoftVersionControllerV1.java:37-49
    params:
      - id: source
        type: u1
        enum: source
    seq:
      - id: selector
        type: u1
        enum: govee_common::device_info_selector
        doc: firmware answers 02 / 03 / 06 (H6047 also 04); none answers 10 / 11
        doc-ref:
          - H6047 handler @0x01005370
          - H5083 ble_msg_get_dev_info @0x9b031b08
          - H60A6 ble_msg_get_dev_info @0x9b04a8e4
      - id: info
        type:
          switch-on: selector
          cases:
            'govee_common::device_info_selector::sn': uid_record
            'govee_common::device_info_selector::hard_version': >-
              govee_common::version_text
            'govee_common::device_info_selector::soft_version': >-
              govee_common::version_text
            'govee_common::device_info_selector::ble_mac': govee_common::mac
            'govee_common::device_info_selector::basic': basic_info
            'govee_common::device_info_selector::wifi': wifi_info
        if: >-
          source == source::read_reply
          or selector == govee_common::device_info_selector::basic
          or selector == govee_common::device_info_selector::wifi
        doc: the record; absent for an untyped selector (bytes in rest)
    instances:
      app_decodes:
        value: >-
          selector == govee_common::device_info_selector::basic
          or selector == govee_common::device_info_selector::wifi
        doc: the selectors the status TLV parser reads a body for
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:370-382
  uid_record:
    doc: |
      AA 07 02: the 8-byte device id, sent byte-reversed by the firmware.
    doc-ref:
      - H6006 code @0x9b03c87c
      - H60A6 code @0x9b053f9c
      - H6047 code @0x010053da
    seq:
      - id: uid
        size: 8
        doc: on H6047 bytes 0..5 are the AA 07 06 address bytes
    instances:
      uid_is_invalid:
        value: uid == [0, 0, 0, 0, 0, 0, 0, 0]
        doc: all-zero = no device id (the app reports a failure)
        doc-ref:
          - base-classes10/com/govee/base2light/ble/controller/SnController.java:48-53
          - base-classes10/com/govee/base2home/util/NumberUtil.java:300-307
      uid_has_leading_pair:
        value: uid[7] == 0 and uid[6] == 0
        doc: the rendered id starts '00:00:', which the app drops once
        doc-ref:
          - base-classes10/com/govee/base2light/ble/BleUtil.java:547-580
          - base-classes10/com/govee/base2light/ble/controller/SnController.java:54-57
  basic_info:
    doc: |
      Selector 0x10: [uid 8][sw version3][hw version3][dsp u16le]; the TLV
      path renders the uid byte-reversed.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:405-436
      - split_plugnew/com/govee/h5086/ble/controller/DeviceBasicInfoController.java:39-63
    seq:
      - id: uid
        size: 8
      - id: sw_version
        type: govee_common::version3
      - id: hw_version
        type: govee_common::version3
      - id: dsp_version
        type: u2le
    instances:
      uid_is_invalid:
        value: uid == [0, 0, 0, 0, 0, 0, 0, 0]
        doc: all-zero = no device id; the TLV 0x07 path skips it
        doc-ref:
          - base-classes10/com/govee/base2home/util/NumberUtil.java:300-307
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:405-415
          - base-classes10/com/govee/base2light/ble/controller/BasicInfoController.java:43-50
      uid_has_leading_pair:
        value: uid[7] == 0 and uid[6] == 0
        doc: the rendered id starts '00:00:', which the app drops once
        doc-ref:
          - base-classes10/com/govee/base2light/ble/BleUtil.java:547-580
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:410-412
  wifi_info:
    doc: |
      Selector 0x11: [wifi mac 6, forward order][wifi sw version3][wifi hw
      version3], the Wi-Fi module's MAC and versions.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:384-403
      - split_plugnew/com/govee/h5086/ble/controller/WifiInfoController.java:29-42
    seq:
      - id: wifi_mac
        size: 6
      - id: wifi_sw_version
        type: govee_common::version3
      - id: wifi_hw_version
        type: govee_common::version3
    instances:
      wifi_mac_is_invalid:
        value: wifi_mac == [0, 0, 0, 0, 0, 0]
        doc: all-zero = none; the TLV 0x07 path renders an empty MAC
        doc-ref:
          - base-classes4/com/ihoment/base2app/util/DeviceUtil.java:49-56
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:384-386
  timer_slots:
    doc: |
      AA 23 reply / TLV 0x23: [slot][timer_record], or [FF][4 x timer_record]
      for an all-slots read.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerNewTimer.java:52-77
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:446-495
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:437-444
    seq:
      - id: slot
        type: u1
        doc: 0..3 or 0xFF = all; H6006 / H6008 echo it but send slot 0's record
      - id: records
        type: govee_common::timer_record
        repeat: expr
        repeat-expr: 'num_wanted > num_fit ? num_fit : num_wanted'
        doc: the app's count, capped at what the value holds
    instances:
      is_all_slots:
        value: slot == 0xff
      num_wanted:
        value: 'slot == 0xff ? 4 : 1'
      num_fit:
        value: (_io.size - 1) / 4
      is_short:
        value: num_wanted > num_fit
        doc: the value ends inside a record the app copies (it throws)
  timer_count:
    doc: |
      Form timer_count of 0x12: the h5080 AA 12 reply [group echo][count].
    doc-ref:
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:147-149
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:197-223
      - split_plugnew/com/govee/h5080/ble/controller/NewTimerCountController.java:27-31
    seq:
      - id: group
        type: u1
        doc: the request's group byte echoed
      - id: count
        type: u1
        doc: the number of timers, read as ceil(count / 4) AA 13 pages
  plug_timer_page:
    doc: |
      Form plug_timer of 0x13: the h5080 AA 13 reply [page | group << 4][4 x
      timer_record].
    doc-ref:
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:232-246
      - split_plugnew/com/govee/h5080/ble/controller/NewTimerControllerV2.java:53-66
      - base-classes12/com/govee/base_h71xx/sku_base/adjust/component/bean/TimerInfo.java:52-60
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:111-143
    seq:
      - id: page_and_group
        type: u1
      - id: records
        type: govee_common::timer_record
        repeat: expr
        repeat-expr: 4
        doc: on H5083, zero past the timer count and all zero for a page >= 4
    instances:
      page:
        value: page_and_group & 0x0f
      group:
        value: page_and_group >> 4
  plug_delay_state:
    doc: |
      Form plug_delay of 0xB0: the h5080 AA B0 reply [outlet][type][hour]
      [minute][remaining seconds u24be].
    doc-ref:
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:178-193
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:158-187
    seq:
      - id: outlet
        type: u1
      - id: delay_type
        type: u1
        doc: the request's type byte (0 = one delay kind, else the other)
      - id: hour
        type: u1
      - id: minute
        type: u1
      - id: remaining_seconds
        type: b24be
        doc: H5083 sends 0 over 86400 s, and every field 0 for an invalid time
  cmd_12_state:
    doc: |
      Command 0x12 under form_12; the wake form is not typed (bytes in rest).
    params:
      - id: form
        type: u1
        enum: govee_common::form_12
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_12::timer_count': timer_count
  cmd_13_state:
    doc: |
      Command 0x13 under form_13; night_mode is not typed (bytes in rest).
    params:
      - id: form
        type: u1
        enum: govee_common::form_13
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_13::plug_timer': plug_timer_page
  cmd_14_state:
    doc: |
      Command 0x14 under form_14: the gradual flag or the AA 14 Wi-Fi MAC
      (forward order).
    params:
      - id: form
        type: u1
        enum: govee_common::form_14
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_14::gradual': gradual_state
            'govee_common::form_14::wifi_mac': govee_common::mac
  cmd_30_state:
    doc: |
      Command 0x30 under form_30; light_direction is read from the TLV only.
    params:
      - id: form
        type: u1
        enum: govee_common::form_30
      - id: source
        type: u1
        enum: source
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_30::zone_power': zone_bits
            'govee_common::form_30::light_direction': direction_state
        if: >-
          form != govee_common::form_30::light_direction
          or source == source::status_tlv
  cmd_34_state:
    doc: |
      Command 0x34 under form_34: device_detail for the aa 34 reply (H6057);
      other combinations stay in rest.
    params:
      - id: form
        type: u1
        enum: govee_common::form_34
      - id: source
        type: u1
        enum: source
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_34::device_detail': device_detail
        if: >-
          form == govee_common::form_34::device_detail
          and source == source::read_reply
  device_detail:
    doc: |
      Form device_detail of 0x34: the H6057 aa 34 reply, [switch flags][battery]
      [music flags][music code][volume][brightness] (Controllers.e). The screen's
      header has two switches: the light switch shows light_open, the main switch
      (33 01) shows main_switch_on.
    doc-ref:
      - split_hollowlamp/com/govee/h6057/origin/newDetail/controller/Controllers.java:30-58
      - split_hollowlamp/com/govee/h6057/origin/newDetail/controller/DeviceInfoNotifyParse.java:61-70
      - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:517-545,571-588
      - base-classes12/com/govee/base2light/util/ParseUtils.java:54-56,72-74
    seq:
      - id: switch_flags
        type: u1
        doc: |
          bit 7 the light open; bits 6-2 energy saving, power-on without
          interruption, worry-free, prompt tone and guide light (SwitchInfo)
      - id: battery
        type: u1
      - id: music_flags
        type: u1
        doc: |
          bit 6 Bluetooth audio, bit 5 game, bit 4 white noise, bit 3 group
          control; bit 7 playing, read only in white-noise mode (MusicTypeInfo)
      - id: music_code
        type: u1
      - id: volume
        type: u1
      - id: brightness
        type: u1
    instances:
      light_open:
        value: (switch_flags & 0x80) != 0
        doc: the light-open flag, the light switch's state (H6057Info4Detail.H)
        doc-ref:
          - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057Info4Detail.java:208-210
      white_noise_playing:
        value: (music_flags & 0x10) != 0 and (music_flags & 0x80) != 0
        doc: MusicTypeInfo isPlay (set only in white-noise mode)
        doc-ref:
          - split_hollowlamp/com/govee/h6057/origin/newDetail/controller/MusicTypeInfo.java:117-119
      main_switch_on:
        value: light_open or white_noise_playing
        doc: |
          the header main switch's state: the light open or white noise
          playing; it writes 33 01 01 only when both are off, else 33 01 00
        doc-ref:
          - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DelegateHeader.java:183-213
          - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:1231-1264
  cmd_36_state:
    doc: |
      Command 0x36 under form_36: bar_pair for the aa 36 reply (H6047),
      bar_index for TLV 0x36; other combinations stay in rest.
    params:
      - id: form
        type: u1
        enum: govee_common::form_36
      - id: source
        type: u1
        enum: source
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_36::bar_pair': bar_state
            'govee_common::form_36::bar_index': light_flags
        if: >-
          (form == govee_common::form_36::bar_pair
          and source == source::read_reply)
          or (form == govee_common::form_36::bar_index
          and source == source::status_tlv)
  cmd_41_state:
    doc: |
      Command 0x41 under form_41; section_count is the H6062 aa 41 reply only.
    params:
      - id: form
        type: u1
        enum: govee_common::form_41
      - id: source
        type: u1
        enum: source
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_41::power_memory': power_memory
            'govee_common::form_41::section_count': govee_common::section_count
        if: >-
          form != govee_common::form_41::section_count
          or source == source::read_reply
  cmd_a2_state:
    doc: |
      Command 0xA2 under form_a2; color_group_rgb always has 3-byte records.
    params:
      - id: form
        type: u1
        enum: govee_common::form_a2
      - id: group
        type: govee_profile::color_group_form
      - id: source
        type: u1
        enum: source
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_a2::bulb_batch_v1': bulb_batch_v1
            'govee_common::form_a2::color_group_rgb': >-
              color_group(group, source, false)
  cmd_a5_state:
    doc: |
      Command 0xA5 under form_a5 (the reply does not say which); bulb_batch_v2
      is reply only.
    params:
      - id: form
        type: u1
        enum: govee_common::form_a5
      - id: group
        type: govee_profile::color_group_form
      - id: source
        type: u1
        enum: source
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_a5::color_group': >-
              color_group(group, source, group.has_brightness)
            'govee_common::form_a5::bulb_batch_v2': bulb_batch_v2
        if: >-
          form != govee_common::form_a5::bulb_batch_v2
          or source == source::read_reply
  cmd_b0_state:
    doc: Command 0xB0 under form_b0 (plug_delay -> plug_delay_state).
    params:
      - id: form
        type: u1
        enum: govee_common::form_b0
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_b0::plug_delay': plug_delay_state
  color_group:
    doc: |
      One colour group [group_index][records]: the TLV 0xA5 value or the AA A5
      / AA A2 reply body, its record count set by group.rule.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:58-75,111-136,145-175
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:105-142
      - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4Color.java:113-147
    params:
      - id: group
        type: govee_profile::color_group_form
      - id: source
        type: u1
        enum: source
      - id: has_brightness
        type: bool
        doc: 4-byte [brightness][r][g][b] records, else 3-byte [r][g][b]
    seq:
      - id: group_index
        type: u1
        doc: the 1-based group number (the request byte)
      - id: records
        type:
          switch-on: has_brightness
          cases:
            true: govee_common::bri_rgb
            false: govee_common::rgb
        repeat: expr
        repeat-expr: num_records
    instances:
      len_record:
        value: 'has_brightness ? 4 : 3'
      num_groups:
        value: >-
          group.num_per_group == 0 ? 0 :
          (group.num_pieces + group.num_per_group - 1) / group.num_per_group
      is_last_group:
        value: group_index == num_groups
        doc: the group that holds the remainder
      checks_group:
        value: >-
          source == source::status_tlv
          and group.rule == govee_common::group_count_rule::by_pieces
        doc: the H60A6 status lambda's count and placement rules apply
      places_by_group:
        value: >-
          checks_group or (source == source::status_tlv
          and group.rule == govee_common::group_count_rule::fixed
          and group.num_pieces != 0)
        doc: the TLV screen places records by the group byte; piece count known
      num_records_wanted:
        value: >-
          group.rule == govee_common::group_count_rule::from_len
          ? (_io.size - 1) / len_record :
          (checks_group and is_last_group
          ? group.num_pieces - (num_groups - 1) * group.num_per_group
          : group.num_per_group)
        doc: the record count the app passes to its reader
      num_records:
        value: >-
          num_records_wanted > (_io.size - 1) / len_record
          ? (_io.size - 1) / len_record : num_records_wanted
        doc: the wanted count, capped at what the value holds
      is_short:
        value: num_records_wanted > (_io.size - 1) / len_record
        doc: the value is too short for the app's count (the app throws)
      group_index_signed:
        value: 'group_index >= 0x80 ? group_index - 0x100 : group_index'
        doc: the group byte read signed, as the app compares it
      app_rejects_group:
        value: >-
          places_by_group and (group_index == 0
          or (checks_group and group_index >= 0x80)
          or (not is_last_group
          and (group_index - 1) * group.num_per_group > group.num_pieces))
        doc: the app throws (group 0, past the last piece, >= 0x80 on H60A6)
      is_placed:
        value: >-
          not places_by_group or is_last_group
          or (group_index - 1) * group.num_per_group != group.num_pieces
        doc: false when the group starts exactly at the end (nothing is copied)
      app_read_fails:
        value: places_by_group and (is_short or app_rejects_group)
        doc: the TLV screen's colour-group parse throws on this group
  bulb_batch_v1:
    doc: |
      Form bulb_batch_v1 of 0xA2: the H61A8 batch [batch_seq][4 x rgb],
      positional (segment = (batch_seq - 1) * 4 + i).
    doc-ref:
      - split_rgbiclight/com/govee/dreamcolorlightv1/ble/BulbGroupColor.java:11-25
      - split_rgbiclight/com/govee/dreamcolorlightv1/ble/BulbStringColorController.java:28-37
      - split_rgbiclight/com/govee/dreamcolorlightv1/adjust/v1/BleOpV1.java:318-345
    seq:
      - id: batch_seq
        type: u1
        doc: 1-based batch number
      - id: records
        type: govee_common::rgb
        repeat: expr
        repeat-expr: 4
  bulb_batch_v2:
    doc: |
      Form bulb_batch_v2 of 0xA5: the H61A8 batch [batch_seq][3 x bri_rgb],
      positional, each record's first byte the segment brightness.
    doc-ref:
      - split_rgbiclight/com/govee/dreamcolorlightv1/ble/BulbGroupColorV2.java:16-33
      - split_rgbiclight/com/govee/dreamcolorlightv1/ble/BulbStringColorControllerV2.java:35-44
      - split_rgbiclight/com/govee/dreamcolorlightv1/adjust/v1/BleOpV1.java:349-377
    seq:
      - id: batch_seq
        type: u1
      - id: records
        type: govee_common::bri_rgb
        repeat: expr
        repeat-expr: 3
  ic_count:
    doc: |
      AA 40 reply / TLV 0x40: [ic_count s16be][segment], then a second
      [ic_count_2][segment_2] pair at bytes 3..5.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerOnlyReadIcSegmentNum.java:30-37
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerIcNum.java:141-157
      - H6047 handler @0x010051e8
    seq:
      - id: ic_count
        type: s2
        if: _io.size >= 2
        doc: the addressable IC (lamp-bead) count, read signed
      - id: segment
        type: u1
        if: _io.size >= 3
        doc: the segment (group) count; 0 on H6047 (firmware)
      - id: ic_count_2
        type: s2
        if: _io.size >= 6
        doc: the second strip's IC count; 0 = none
      - id: segment_2
        type: u1
        if: _io.size >= 6
    instances:
      segment_signed:
        value: 'segment >= 0x80 ? segment - 0x100 : segment'
        if: _io.size >= 3
      segment_2_signed:
        value: 'segment_2 >= 0x80 ? segment_2 - 0x100 : segment_2'
        if: _io.size >= 6
        doc: the second section byte, read as a Java byte
      section_sum:
        value: segment_signed + segment_2_signed
        if: _io.size >= 6 and not app_pair_fails
        doc: |
          the two section bytes (2 and 5) summed as Java bytes, as the sectioned
          IC-pair reader (ControllerIcNum.d) passes them on: the rgbiclight screen's
          colour read-back walks this many pieces when it is not 0, else its static
          count. Absent where that reader throws (app_pair_fails: the read never
          completes, so the screen walks nothing).
        doc-ref:
          - base-classes11/com/govee/base2light/kt/general_controller/ControllerIcNum.java:64-74,111-139
          - split_rgbiclight/com/govee/rgbiclight/ControllerStrategyConfig.java:329-339,981-991
          - split_rgbiclight/com/govee/rgbiclight/VM4Light.java:917-923,938-946
      has_second_pair:
        value: _io.size >= 6 and ic_count_2 != 0
      ic_total:
        value: 'ic_count + (has_second_pair ? ic_count_2 : 0)'
        if: not app_pair_fails
        doc: both pairs' count, as the H61D3 / H6609 / H6250 screens use it
        doc-ref:
          - split_h61d3/com/govee/h61d3/adjustNew/VM4Light.java:550-563
          - split_h61d3/com/govee/h61d3/pact/H61D3Support.java:786-788
          - split_h6840/com/govee/h6609/h6250/NewDetailVm$afterConnectedSingleReadDeviceInfo$1.java:75-87
      app_pair_fails:
        value: >-
          _io.size < 3 or ic_count_byte_order_suspect
          or (has_second_pair and ic_count_2 >= 0x1000)
        doc: the IC pair reader (TLV form_40 ic_pair) throws on this value
        doc-ref:
          - base-classes11/com/govee/base2light/kt/general_controller/ControllerIcNum.java:64-74,141-161
      is_short:
        value: _io.size < 2
        doc: |
          fewer than 2 bytes: no 16-bit IC count (the one-count readers throw;
          TLV form_40 one_count)
        doc-ref:
          - base-classes11/com/govee/base2light/kt/general_controller/ControllerIcNum.java:98-103
      ic_count_byte_order_suspect:
        value: _io.size >= 2 and ic_count >= 0x1000
        doc: the app's byte-order check fires (it rejects the value, no swap)
        doc-ref:
          - split_h61d3/com/govee/h61d3/ble/controller/ReadIcController.java:49-64
          - base-classes11/com/govee/base2light/kt/general_controller/ControllerIcNum.java:64-74
          - base-classes10/com/govee/base2light/ble/controller/IcNumController.java:17-24
      ic_count_checked:
        value: ic_count
        if: _io.size >= 2 and not ic_count_byte_order_suspect
        doc: the IC count the checking readers return
        doc-ref:
          - split_h61d3/com/govee/h61d3/ble/controller/ReadIcController.java:68-95,102-110
          - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:122-127
          - split_h61d3/com/govee/h61d3/pact/H61D3Support.java:1547-1561
  gradual_state:
    doc: |
      AA A3 reply / TLV 0xA3 (and 0x14 under form gradual): the gradual (fade)
      flag.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerGradual4BleWifi.java:31-37,94-96
      - split_hollowlamp/com/govee/straightfloorlamp/adjust/VM4Light$afterConnectedSingleReadDeviceInfo$1.java:118-119
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:550-561
    seq:
      - id: state
        type: u1
    instances:
      is_on:
        value: state == 1
        doc: any byte other than 1 is off
  bar_state:
    doc: |
      Form bar_pair of 0x36: the H6047 AA 36 reply [left][right][movie / feast
      switch]; left / right are logical positions (swappable at setup).
    doc-ref:
      - split_h6047/com/govee/h6047/ble/controller/ComposeLightController.java:27-29
      - split_h6047/com/govee/h6047/detail/NewDetailVm.java:593-595
      - H6047 handler @0x01006934
    seq:
      - id: left
        type: u1
      - id: right
        type: u1
      - id: feast
        type: u1
        if: _io.size >= 3
        doc: the movie / feast switch (== 1 on); firmware sends 0 when off
    instances:
      left_on:
        value: left == 1
        doc: any byte other than 1 is off
      right_on:
        value: right == 1
      is_on:
        value: left_on or right_on
  light_flags:
    doc: |
      Form bar_index of TLV 0x36: one flags byte, bits 0 / 1 the H6038 / H6048
      lights, bits 0 / 1 / 2 the H1771 flags.
    doc-ref:
      - split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:591-593
      - split_h6047/com/govee/h6047/newdetail/h6048/VM4H6048.java:397-399
      - split_hollowlamp/com/govee/hollowlamp/newdetails/h1771/H1771DetailVM$afterConnectedSingleReadDeviceInfo$1.java:132-149
    seq:
      - id: flags
        type: u1
    instances:
      bit0_on:
        value: (flags & 0x01) != 0
      bit1_on:
        value: (flags & 0x02) != 0
      bit2_on:
        value: (flags & 0x04) != 0
  zone_bits:
    doc: |
      Form zone_power of 0x30: zone power as bit 0 of each byte (two zones;
      three on H12D0).
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:102-104
      - split_h60ax/com/govee/pact_h60a0/adjust/h12d0/VM4LightH12D0$afterConnectedSingleReadDeviceInfo$1.java:78-81
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:223-236
    seq:
      - id: zone_a
        type: u1
      - id: zone_b
        type: u1
      - id: zone_c
        type: u1
        if: _io.size >= 3
        doc: H12D0's third zone (in a reply, the request echo)
    instances:
      zone_a_on:
        value: (zone_a & 1) == 1
      zone_b_on:
        value: (zone_b & 1) == 1
      zone_c_on:
        value: (zone_c & 1) == 1
        if: _io.size >= 3
  direction_state:
    doc: |
      Form light_direction of TLV 0x30: the H6038 calibration direction byte.
    doc-ref:
      - split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:579-590
      - split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:525
    seq:
      - id: value
        type: u1
  power_memory:
    doc: |
      Form power_memory of 0x41: the power-off memory record [02][mode], the
      byte 33 41 02 <v> writes.
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:99-100
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:471-482
      - H60A6 ble_check_basic_info @0x9b04c0bc-0x9b04c0ce
    seq:
      - id: memory_type
        type: u1
        doc: 0x02 (the 33 41 02 selector)
      - id: mode
        type: u1
        doc: the power-on mode
  secret_state:
    doc: |
      AA B1 reply: [selector][8-byte account-lock secret], not the wire cipher.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:57-66
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:574-610
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:358-376
    seq:
      - id: selector
        type: u1
      - id: secret
        size: 8
        doc: the token (base64 off-wire); not decodable further
    instances:
      is_valid:
        value: selector == 1
        doc: firmware sends 1 and the key only on a repeat read within 5 s
  plug_spec:
    doc: |
      AA B3 reply: one spec byte, the IoT spec id; not bit-decoded and not the
      outlet count.
    doc-ref:
      - split_plugnew/com/govee/h5080/ble/controller/SpecController.java:6-19
      - split_plugnew/com/govee/h5080/pact/Support.java:126-131
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:153-156
    seq:
      - id: spec
        type: u1
        doc: 0 on H5083 (firmware)
  pact_state:
    doc: |
      AA EF reply: [pact_type u16be][pact_code]; firmware sends 00 01 01
      (H5083: 00 02 02).
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/PactController.java:9-20,33-40
      - H6006 ble_msg_get_adv_pro_type @0x9b03cc00
      - H5083 FUN_9b031b80 @0x9b031b80
    seq:
      - id: pact_type
        type: u2
      - id: pact_code
        type: u1
  debug_state:
    doc: |
      FIRMWARE-SOURCED, CAUTION (test-only; no app sender): the AA FF reply
      [selector][..], selector 08 being the factory ageing flag.
    doc-ref:
      - firmware-h60a1-wifi/decompiled/H60A1_ble_dispatcher.c:497-505
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:662-672
      - firmware-h6047/decompiled/H6047_decompiled.c:5955
    seq:
      - id: selector
        type: u1
        doc: H6047 sends 17 zero bytes for any selector other than 08
      - id: body
        type:
          switch-on: selector
          cases:
            0x08: aging_flag
  aging_flag:
    doc: |
      FIRMWARE-SOURCED, CAUTION: AA FF 08 reply byte 3, the factory ageing flag;
      a 0 also runs further H60A1 / H60A6 routines (static analysis only).
    doc-ref:
      - H60A1 FUN_9b02ca34 @0x9b02ca34
      - H60A6 FUN_9b02de4c @0x9b02de4c
      - H60A6 FUN_9b03cadc @0x9b03cadc
    seq:
      - id: flag
        type: u1
        doc: H6047 reads 1 unless the flash word at 0x79400 is 0xFFFFFFFF
enums:
  source:
    1:
      id: read_reply
      doc: the body of an aa reply (frame bytes 2..n-2)
    2:
      id: status_tlv
      doc: the value of a 0xAC status TLV (exactly len bytes)
  cct_test:
    0:
      id: none
      doc: no colour-temperature test (is_cct false)
    1:
      id: kelvin_positive
    2:
      id: flag_eq_1
    3:
      id: flag_nonzero
    4:
      id: pair_equal
      doc: the two Kelvins are equal and > 0
