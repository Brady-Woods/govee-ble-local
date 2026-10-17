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
  SHARED PAYLOAD (not an entry point): the body of an `aa <command>` reply and
  the value of a 0xAC status TLV with tag <command> are one message in two
  envelopes, so one dispatch serves both. It is reached through the frame's
  read_reply (source read_reply: frame bytes 2..n-2, 17 bytes for a 20-byte
  frame) and the status stream's status_tlv (source status_tlv: exactly len
  bytes, no padding). `source` carries the rules that differ:
  - power: == 1 (aa 01, the control screens) or != 0 (TLV 0x01, u():286-288);
  - the mode decode: profile.mode or profile.mode_status;
  - TLV 0x07 decodes only the 0x10 / 0x11 records (w():370-437);
  - the aa 01 aux byte (H6047 feast flag) exists only in the reply;
  - the colour-group index checks apply only to the TLV (item 1).
  App side, a reply's body is validBytes = frame bytes 2..18
  (AbsControllerNoEvent4Single.onResult, 20-byte frames only), parsed by each
  controller's parseValidBytes; the TLV walk Compose4BaseInfoSingleRead.u
  decodes 0x01 / 0x04 / 0x05 / 0x07 / 0x11 / 0x12 / 0x23 itself and hands
  every other type, with [type, len] re-prepended, to the screen's fallback
  lambda.
  `body` is absent for a zero-length value (H60A6 sends [05][00]), so an
  empty TLV value never raises; `app_read_fails` says where the app's whole
  read fails instead. `rest` keeps every byte after the typed record (the
  request echo, stale buffer bytes, or what the app does not read), so the
  message re-writes exactly. An unmatched command, or a form the profile does
  not know, leaves the record absent with its bytes in `rest`.
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
    doc: the device's decode context (one cached object per device facts)
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
    doc: |
      the typed record; absent for an empty value, an unmatched command, a
      form the profile does not know, or a TLV tag with no traced value reader
      (is_tlv_typed). Not typed (bytes in rest): 0x11 sleep
      (TimerConfig.Companion.m picks one of 9 layouts by the SKU's sleep-config
      version), the 0x12 wake-up form, 0x16, 0x39 and every other command.
  - id: rest
    type: govee_common::echo
    doc: |
      every byte after the record: the request echo (firmware builds most
      replies in the request buffer), stale bytes, or bytes the app does not
      read; for an aa reply also the zero fill
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
    doc: |
      The TLV tags whose value has a traced reader, so the record is typed
      for source status_tlv too: u()'s own decodes (0x01, 0x04, 0x05 through
      the mode lambda, 0x07, 0x23 through ControllerNewTimer.d), and the
      screen fallbacks that read a value (0x30, 0x36, 0x40, 0x41, 0xA3,
      0xA5; tag 0 is an ordinary TLV the screens ignore). Every other tag's
      value goes to a fallback that does not read it, so it stays raw (rest):
      it never raises where the app does not throw.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:286-341
  app_read_fails:
    value: >-
      source == source::status_tlv and (_io.size == 0 ? (
      command == govee_common::command::power
      or command == govee_common::command::brightness
      or command == govee_common::command::device_info
      or command == govee_common::command::new_timer
      or command == govee_common::command::gradual
      or command == govee_common::command::ic_num
      or (command == govee_common::command::mode and decode.empty_fails)
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
      and body.as<ic_count>.is_short)
      or (command == govee_common::command::color_group
      and profile.forms.f_a5 == govee_common::form_a5::color_group
      and body.as<cmd_a5_state>.body.as<color_group>.app_read_fails)))
    doc: |
      Status TLVs only (false for a reply): the app's whole status read fails
      on this value. Nothing catches a throw between u() and the notify
      subscriber; EventBus only logs it, the compose times out into
      result2Fail and the success callback that applies the state never runs
      (F4). The value still reads and re-writes here (R7). It is true for:
      - an EMPTY value whose handler reads value[0]: u() for 0x01 / 0x04 /
        0x07 (bArr2[0]) and 0x23 (ControllerNewTimer.d); the screen lambdas
        for 0x05 (when mode_status.empty_fails: h60a1 Info4Detail.Q reads
        value[0] first), 0xA3 (value[0] == 1), 0x40 (getSignedShort of
        value[0..1]) and, where the profile gives the form, 0x30, 0x36
        (bit flags), 0x41 (bArr2[1]) and 0xA5 (q() reads value[0]);
      - a non-empty 0x05 value shorter than its decode reads, when
        mode_status.empty_fails (Q's strategies index the params unguarded:
        0x15 < 3, 0x0d < 5, 0x04 / 0x0a < 2, 0x13 by the music rule);
      - a 0x23 value short of its records (ControllerNewTimer.d copies them
        unguarded) and a 0x40 value under 2 bytes (getSignedShort);
      - a 0xA5 group the app cannot read or place (color_group.app_read_fails).
      A shorter-than-needed value of the other typed records raises here, as
      the app's read throws there (w() for a short 0x10 / 0x11 record; the
      H60A6 lambda for a 1-byte 0x30 / 0x41).
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:286-334
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:347-353
      - base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:240-261
      - base-classes10/com/govee/base2light/ble/comm/ComposeBleComm.java:190-211
      - base-classes10/com/govee/base2light/ble/AbsBle.java:623-650
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:380-433
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:94-124
types:
  heartbeat:
    doc: |
      AA 00 (newdetail ShareVM heartbeat) / TLV tag 0: no record. H60A1 / H60A6
      echo the request (an explicit no-op case); a TLV with tag 0 is an
      ordinary TLV whose value the screen ignores. Its bytes are the message's
      rest.
    doc-ref:
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:241-242
      - firmware-h60a1-wifi/decompiled/H60A1_ble_dispatcher.c:559-560
  power_state:
    doc: |
      AA 01 reply / TLV 0x01: the power byte, then (reply only) one aux byte.
      LIGHTS: the curated control screens decode on as byte == 1
      (ControllerSwitch.Companion.c; the aa 01 heartbeat ShareVM.N3 frame[2] ==
      1); the legacy SwitchController.parseValidBytes reads != 0 (older detail
      screens of legacy goodsTypes, scene / IoT command paths); the two agree
      for 0 / 1. The 0xAC TLV 0x01 is != 0 (u():286-288); devices.yaml
      client_profile.power_read says which message carries a family's power.
      PLUGS: the same byte is a relay BITMASK (bit i = relay i:
      SwitchControllerV2.parseValidBytes at the connect read and
      HeartControllerV2.parseValidBytes in the heartbeat split the 8 bits,
      ((bArr[0] >> i) & 1) != 0), not the write's [mask << 4 | on] form.
      Firmware H5083 (one outlet): the byte is outlet 0's state 0x00 / 0x01
      (FUN_9b021668 = ldrb [0x10009440 + 8]), the upper bits 0. is_on and
      is_on_heartbeat apply the profile's power_rule; they replace the old
      per-goodsType instances (relay0_is_on for gt 43 = mask 0x01;
      any_relay_is_on for the gt 50 / 307 connect read = 0xFF;
      any_relay_0_1_is_on for their heartbeat = 0x03; any_relay_0_2_is_on for
      gt 90 = 0x07; light_is_on = mask 0; status_switch.is_on = TLV source).
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerSwitch.java:32
      - base-classes11/com/govee/base2light/pact/newdetail/ShareVM.java:1008
      - base-classes10/com/govee/base2light/ble/controller/SwitchController.java:30
      - split_rgbiclight/com/govee/dreamcolorlightv1/adjust/v1/BleOpV1.java:735
      - split_rgbiclight/com/govee/dreamcolorlightv1/adjust/v2/BleOpV2.java:123
      - split_rgbiclight/com/govee/dreamcolorlightv1/adjust/v2/BleOpV3.java:709
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:286-288
      - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:29-45
      - split_plugnew/com/govee/h5080/ble/controller/HeartControllerV2.java:17-35
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:162-167
      - H5083 FUN_9b021668 @0x9b021668
      - split_plugnew/com/govee/h5080/pact/Model.java:195-196
      - split_plugnew/com/govee/h5080/adjust/v3/BleOpV3.java:210
      - split_plugnew/com/govee/h5080/adjust/v2/BleOpV2.java:156-158
      - split_plugnew/com/govee/h5080/adjust/h5089/BleOp4H5089.java:621
      - split_plugnew/com/govee/h5080/adjust/AdjustAc.java:98-109
      - split_plugnew/com/govee/h5080/adjust/v4_h5160/UiV4.java:97
      - split_plugnew/com/govee/h5080/adjust/v4_h5160/BleOpV4.java:64
      - H5083 code @0x9b021640
    params:
      - id: source
        type: u1
        enum: source
      - id: power
        type: govee_profile::power_rule
    seq:
      - id: state
        type: u1
        doc: the power byte (a light's 1 / 0, or a plug's relay bitmask)
      - id: aux
        type: u1
        if: source == source::read_reply and _io.size >= 2
        doc: |
          reply byte 3: on H6047 the feast / group flag when on, else 0
          (handler 0x01004f32: power = cfg+4 | cfg+5, flag = 0x20005370+6);
          elsewhere the request echo (zero in an app-built frame)
        doc-ref:
          - H6047 handler @0x01004f32
    instances:
      is_on:
        value: >-
          source == source::status_tlv ? state != 0 :
          (power.relay_on_mask == 0 ? state == 1 :
          (state & power.relay_on_mask) != 0)
        doc: |
          on at the connect read (reply) or in the status TLV: the TLV rule !=
          0; a light (mask 0) == 1; a plug (state & relay_on_mask) != 0
      is_on_heartbeat:
        value: >-
          power.relay_heartbeat_on_mask == 0 ? is_on :
          (state & power.relay_heartbeat_on_mask) != 0
        doc: |
          on in the aa 01 heartbeat: relay_heartbeat_on_mask when set (gt 50 /
          307: bits 0..1, BleOpV2.onEventHeartV2 open = i(0) | i(1)), else the
          connect-read rule
        doc-ref:
          - split_plugnew/com/govee/h5080/adjust/v2/BleOpV2.java:216-217
          - split_plugnew/com/govee/h5080/adjust/h5089/BleOp4H5089.java:331-332
      relay0_is_on:
        value: (state & 0x01) != 0
        doc: |
          relay 0 (bit 0), element 0 of SwitchControllerV2 / HeartControllerV2;
          the goodsType-43 rule (H5083NewDetailRepository (bArr[0] & 1) != 0;
          FrameV1 BleOpV1 .get(0)); outlet 0 of the multi-outlet plugs; the
          only bit H5083 firmware sets
        doc-ref:
          - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:167
          - split_plugnew/com/govee/h5080/adjust/v1/BleOpV1.java:350
          - split_plugnew/com/govee/h5080/adjust/v3/BleOpV3.java:303
          - split_plugnew/com/govee/h5080/adjust/v1/BleOpV1.java:217
          - split_plugnew/com/govee/h5080/adjust/v2/BleOpV2.java:369
          - split_plugnew/com/govee/h5080/adjust/h5089/BleOp4H5089.java:463
          - split_plugnew/com/govee/h5080/adjust/v4_h5160/BleOpV4.java:477
      relay1_is_on:
        value: (state & 0x02) != 0
        doc: relay 1 (outlet 1 of the two-outlet plugs and of goodsType 90)
        doc-ref:
          - split_plugnew/com/govee/h5080/adjust/v2/BleOpV2.java:370
          - split_plugnew/com/govee/h5080/adjust/h5089/BleOp4H5089.java:464
          - split_plugnew/com/govee/h5080/adjust/v4_h5160/BleOpV4.java:477
      relay2_is_on:
        value: (state & 0x04) != 0
        doc: |
          relay 2 (outlet 2 of goodsType 90: BleOpV4.r0 for i < E =
          Support.getPlugNum(90) = 3)
        doc-ref:
          - split_plugnew/com/govee/h5080/adjust/v4_h5160/BleOpV4.java:464-481
          - split_plugnew/com/govee/h5080/pact/Support.java:126-131
      relay3_is_on:
        value: (state & 0x08) != 0
        doc: |
          relay 3; parsed by the app but read by no screen on its own (it counts
          only in the gt 50 / 307 connect-read OR, EventSwitchV2.i)
        doc-ref:
          - split_plugnew/com/govee/h5080/ble/controller/EventSwitchV2.java:48-59
      relay4_is_on:
        value: (state & 0x10) != 0
        doc: relay 4; counts only in the gt 50 / 307 connect-read OR
      relay5_is_on:
        value: (state & 0x20) != 0
        doc: relay 5; counts only in the gt 50 / 307 connect-read OR
      relay6_is_on:
        value: (state & 0x40) != 0
        doc: relay 6; counts only in the gt 50 / 307 connect-read OR
      relay7_is_on:
        value: (state & 0x80) != 0
        doc: relay 7; counts only in the gt 50 / 307 connect-read OR
  mode_report:
    doc: |
      The mode report [sub_mode][params]: the AA 05 reply body and the 0xAC TLV
      0x05 value are the same body (pact_h60a0 h60a1 Info4Detail.Q reads both;
      u() hands the screen's mode lambda exactly len bytes, u():300-308). The
      params' layout is the screen's parse strategy, which the frame does not
      carry: it comes from the profile's mode_decode per colour sub-mode
      (devices.yaml mode_color_parse). The app hands its strategies the bytes
      after the sub-mode byte, or a prefix of them: OpInfo4Detail.x takes
      BleUtils.s(validBytes, 1, true) = validBytes[1 .. length-2], 15 of the
      16 params of a 17-byte reply body (no colour strategy reads that far);
      Q(value, true) copies value[1:].
      Short forms read: every layout field is read only while its bytes are
      there (H60A6 firmware sends [05][00] and 5-byte music values), and
      is_short says the decode wanted more (PORT-review item 2). The value is
      this type's own stream (mode_report is the first field of govee_state),
      so len_params = _io.size - 1.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:283-284
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:300-308
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:82-83
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:380-433
      - base-classes10/com/govee/base2kt/utils/BleUtils.java:1105-1110
      - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:755-761
      - split_h6062/com/govee/striplight/newdetail/OpInfo4Detail.java:423
      - split_tvlightv4/com/govee/pact_tvlightv4/detail/Info4Detail.java:174-191
      - base-classes10/com/govee/base2kt/utils/BleUtils.java:1021-1026
      - split_fanlight/com/govee/fanlight/adjust/VM4Light.java:751
    params:
      - id: decode
        type: govee_profile::mode_decode
    seq:
      - id: sub_mode
        type: u1
        enum: govee_common::sub_mode
        doc: |
          the sub-mode byte. A byte that is not a sub_mode member keeps its int
          in Python and reads null in Java.
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
        doc: the params; absent for another sub-mode (bytes in rest)
    instances:
      len_params:
        value: _io.size - 1
        doc: the bytes after the sub-mode byte in the value / reply body
      is_color_sub_mode:
        value: >-
          sub_mode == govee_common::sub_mode::color_rgbic_15
          or sub_mode == govee_common::sub_mode::color_rgbic_0b
          or sub_mode == govee_common::sub_mode::color_legacy
          or sub_mode == govee_common::sub_mode::color_cct_0d
        doc: |
          The sub-modes the newdetail mode parser hands to the device's
          SubMode4Color parse strategy: OpInfo4Detail.x tests for 0x15, 0x0b,
          0x02 and 0x0d, then parseModeValidBytes on the params, keeping
          the Kelvin only when > 0; striplight (H6062) OpInfo4Detail.u has the
          same set. Not every family dispatches this way: h60a1 Info4Detail.Q
          decodes colour only for 0x15 and 0x0d, the cube CubeNewInfo4Detail.U
          only for 0x0d. A parse rule, not the app's colour-tab test
          (OpInfo4Detail.l picks the colour tab for {0x14, 0x15, 0x02, 0x0d}).
          Was mode_read.is_submode4color.
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:766-779
          - split_h6062/com/govee/striplight/newdetail/OpInfo4Detail.java:428
          - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:399
          - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:416
          - split_h6061/com/govee/cubelight/newdetails/CubeNewInfo4Detail.java:503
          - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:281
          - split_h6062/com/govee/striplight/newdetail/OpInfo4Detail.java:197
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
        doc: |
          the params end before a byte this sub-mode's decode reads (Q's
          strategies then throw ArrayIndexOutOfBounds); false for a sub-mode
          the profile does not decode
  scene_report:
    doc: |
      Params of sub-mode 0x04 (scene) or 0x0a (DIY): [code u16le]
      [music_code u16le]. SubMode4Scene's default strategy reads
      BleUtils.M(vb[1], vb[0]) = vb[1] << 8 | vb[0] (unsigned; H60A1 / H60A6
      Info4Detail.Q, H6061 CubeNewInfo4Detail, OpInfo4Detail.x for 0x04);
      SubMode4Diy reads BleUtils.J, the same bytes as a signed short
      (diy_code). The newdetail scene object reads the same u16le
      (SubModeScenes.parse) and a parameter after it. Sub-mode 0xA1 is not
      read here: only the base OpInfo4Detail.x screens hand it to
      SubMode4Scene, and their mode map shows colour mode for it (bytes in
      rest). Firmware: H6006 / H6008 report [scene u32le] for 04 and 0a (the
      u32 that 33 05 04 stored, so music_code is its high half there); H6047
      [code lo][code hi].
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4Scene.java:24-29
      - base-classes10/com/govee/base2kt/utils/BleUtils.java:277-279
      - base-classes10/com/govee/base2kt/utils/BleUtils.java:232-235
      - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4Diy.java:36-40
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:408-409
      - split_h6061/com/govee/cubelight/newdetails/CubeNewInfo4Detail.java:519-520
      - base-classes11/com/govee/base2light/pact/newdetail/content/scene/SubModeScenes.java:95-102
      - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:786
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:575-586
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:617-633
      - H6047 FUN_010045b0 @0x010045b0
    params:
      - id: len_params
        type: u2
    seq:
      - id: code
        type: u2le
        if: len_params >= 2
        doc: |
          the scene / DIY code (SubMode4Scene: unsigned)
      - id: music_code
        type: u2le
        if: len_params >= 4
        doc: |
          the code's companion (the music code of a 33 05 04 / 0a activation;
          the u32's high half on H6006 / H6008)
    instances:
      diy_code:
        value: 'code >= 0x8000 ? code - 0x10000 : code'
        if: len_params >= 2
        doc: the code read signed, as SubMode4Diy stores a 0x0a code
      is_short:
        value: len_params < 2
        doc: SubMode4Scene / SubMode4Diy read two bytes unguarded
  color_report:
    doc: |
      Params of a colour sub-mode (0x02 / 0x0b / 0x0d / 0x14 / 0x15) under the
      screen's decode `kind` (devices.yaml mode_color_decode; 20 decodes on 12
      wire layouts). Each layout exposes the same accessors where its bytes
      have them: kelvin (s2, signed natively, as getSignedShort reads it),
      color, tint and is_cct (the decode's colour-temperature test). The
      colour the screen derives from a Kelvin (tint tables, clamps) is client
      policy (PROCEDURAL). not_decoded: the layout is absent and the bytes are
      the message's rest.
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
      [gradual]: decode gradual_only. Only body byte 0 is read; no Kelvin and
      no colour (the screen's colour comes from its 0xA5 segment reads).
    params:
      - id: len_params
        type: u2
    seq:
      - id: gradual
        type: u1
        if: len_params >= 1
        doc: the gradual flag (== 1 means on)
    instances:
      is_gradual_on:
        value: len_params >= 1 and gradual == 1
      is_cct:
        value: 'false'
        doc: never a colour temperature
      is_short:
        value: len_params < 1
  gradual_kelvin:
    doc: |
      [gradual][kelvin s16be]: decodes gradual_kelvin_s16be (test
      kelvin_positive) and gradual_kelvin_s16be_nocct (test none). The
      SubMode4Color default strategy: q(vb[0] == 1); s(getSignedShort(vb[1],
      vb[2])), so 0x8000-0xFFFF reads as -32768..-1. Kelvin > 0 is CCT on
      every screen of the positive decode (OpInfo4Detail.x, h60a1
      Info4Detail.Q, h61d3 Info4Detail.W, h705a Info4Detail.V, the H6105
      screen, pact_h605b, pact_tvlightv2, striplight OpInfo4Detail.u, H6078,
      H6059, H7002, H7022); those screens assign their Kelvin only inside the
      > 0 branch, while SubMode4Color.b still stores the non-positive value.
      Was cct_read_reply (sub-mode 0x15; op = the gradual flag) and
      mode_color_0d_typed kind 0.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4Color.java:42-49
      - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4Color.java:239-241
      - base-classes10/com/govee/base2light/ble/BleUtil.java:304-306
      - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:766-779
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:399-405
      - split_h61d3/com/govee/h61d3/adjustNew/Info4Detail.java:568-572
      - split_h61d3/com/govee/h61d3/adjust/newdetail/H61d3NewInfo4Detail.java:427-430
      - split_h705a/com/govee/h705a/newDetail/Info4Detail.java:451-454
      - split_rgbiclight/com/govee/rgbiclight/h61050729/Info4Detail.java:388-391
      - split_h6099/com/govee/pact_h605b/newdetail/Info4Detail.java:777-780
      - split_tvlightv4/com/govee/pact_tvlightv2/newdetail/Info4Detail.java:562-565
      - split_h6062/com/govee/striplight/newdetail/OpInfo4Detail.java:428-438
      - split_hollowlamp/com/govee/tablelampv1/newAdjust/H6078InfoDetail.java:318-322
      - split_hollowlamp/com/govee/hollowlamp/newdetails/H6059InfoDetail.java:342-345
      - split_bulblightstringv1/com/govee/bulblightstringv1/detail/Info4Detail.java:567-570
      - split_bulblightstringv1/com/govee/h7022/detail/Info4Detail.java:704-707
      - split_rgbiclight/com/govee/rgbiclight/VM4Light.java:650-656
      - split_rgbiclight/com/govee/dreamcolorlightv1/pact/Model4BleIot.java:41-46
      - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:519
      - split_rgbiclight/com/govee/dreamcolorlightv1/ble/Mode.java:100-103
      - split_rgbiclight/com/govee/dreamcolorlightv1/ble/SubModeColorV2.java:564-568
      - base-classes10/com/govee/base2light/ble/BleUtil.java:285-302
      - split_rgbiclight/com/govee/h6102/adjust/AdjustAc.java:395-405
      - split_h61d3/com/govee/h61d3/adjust/newdetail/H61d3NewInfo4Detail.java:111
      - split_h61d3/com/govee/h61d3/adjustNew/Info4Detail.java:117
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
        doc: the gradual flag (== 1 means on)
      - id: kelvin
        type: s2
        if: len_params >= 3
        doc: colour temperature, signed (getSignedShort)
    instances:
      is_gradual_on:
        value: len_params >= 1 and gradual == 1
      is_cct:
        value: >-
          test == cct_test::kelvin_positive and len_params >= 3 and kelvin > 0
        doc: colour temperature at `kelvin` (kelvin > 0 on the positive decode)
      is_short:
        value: len_params < 3
  kelvin_only:
    doc: |
      [kelvin s16be] with no gradual byte: decode kelvin_s16be (bulblightv3
      H601E / H601F); kelvin > 0 = CCT, all pieces shown as its tint.
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
      [byte 0, stored but never used][kelvin s16be]: decode
      skip_kelvin_s16be_illumination (H60A0). The Kelvin is the illumination
      (white) light's, kept only when > 0 (Info4DetailH60A0.v), never a
      whole-device CCT, so is_cct is false (test none).
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
        doc: body byte 0 (stored as field u, whose getter has no caller)
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
      [byte 0][kelvin A s16be][kelvin B s16be]: decodes
      gradual_kelvin_pair_s16be (H6047 old detail: byte 0 is the gradual flag,
      q(bArr[0] == 1); A is the LEFT bar, segments 0-4, B the RIGHT bar,
      segments 5-9) and skip_kelvin_pair_s16be (the new TV-backlight layout:
      byte 0 not read; A / B the two halves). Both read signed
      (getSignedShort). The whole-device Kelvin is A only when A == B; a bar
      whose Kelvin is 0 is not in colour-temperature mode. Was cct_read_reply
      kelvin_signed / kelvin_right_signed.
    doc-ref:
      - split_h6047/com/govee/h6047/detail/Info4Detail.java:174-184
      - split_h6047/com/govee/h6047/detail/NewDetailVm.java:616-652
      - split_tvlightv4/com/govee/pact_tvlightv3/detail/Info4Detail.java:176-183
      - split_tvlightv4/com/govee/pact_tvlightv3/detail/NewDetailVm.java:609-653
      - split_tvlightv4/com/govee/pact_tvlightv4/detail/NewDetailVm.java:261-280
      - H6047 FUN_010045b0 @0x010045b0
    params:
      - id: has_gradual
        type: bool
        doc: byte 0 is the gradual flag (true) or a byte the screen skips
      - id: test
        type: u1
        enum: cct_test
      - id: len_params
        type: u2
    seq:
      - id: gradual
        type: u1
        if: len_params >= 1
        doc: the gradual flag (has_gradual), else not read by the screen
      - id: kelvin
        type: s2
        if: len_params >= 3
        doc: Kelvin A (H6047 left bar)
      - id: kelvin_b
        type: s2
        if: len_params >= 5
        doc: Kelvin B (H6047 right bar)
    instances:
      is_gradual_on:
        value: has_gradual and len_params >= 1 and gradual == 1
      is_cct:
        value: >-
          test == cct_test::pair_equal and len_params >= 5
          and kelvin == kelvin_b and kelvin > 0
        doc: a whole-device colour temperature (A == B, > 0)
      is_short:
        value: len_params < 5
  rgb_kelvin_pair:
    doc: |
      [r1 g1 b1][kelvin1 s16be][r2 g2 b2][kelvin2 s16be]: decode
      rgb_kelvin_pair_s16be (the old TV-backlight layout). Per half, kelvin !=
      0 shows that Kelvin's tint, else the RGB; the whole-device Kelvin is set
      only when kelvin1 == kelvin2.
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
      [gradual][kelvin A][kelvin B][kelvin C] s16be: decodes
      gradual_kelvin_triple_s16be (pact_h605b goodsType 82: zones 0-11 / 12-17
      / 18-23 repainted with the tint of A / B / C where != 0) and its _nocct
      twin (172: nothing consumes them). The whole-device Kelvin is never set
      from the report (is_cct false).
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
      [r g b]: decode rgb (H6052InfoDetail.G's strategy reads only vb[0..2],
      ColorUtils.G, fanned across the device's zones; the Kelvin bytes are
      never read; rgblight ModeParseStrategyConfig.c). Never CCT. Was
      mode_color_0d_typed kind 1 / mode_color_0d_report.
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
      [r g b][kelvin s16be], exactly the 5 bytes the decoders read: decodes
      rgb_kelvin_0d (test kelvin_positive: h60a1 Info4Detail.Q b == 13, colour
      from vb[0..2], kelvin = getSignedShort(vb[3], vb[4]), <= 0 -> the RGB,
      else CCT with the colour derived from the Kelvin; the cube strategy; CCT
      for g() > 0) and rgb_kelvin_0d_nocct (test none: h70bx, always the RGB).
      Was mode_color_0d_typed kind 3.
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:416-427
      - split_h6061/com/govee/cubelight/newdetails/CubeNewInfo4Detail.java:141-153
      - split_h6061/com/govee/cubelight/newdetails/CubeNewInfo4Detail.java:503-517
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
      [r g b][kelvin s16be][tint r g b]: decodes rgb_kelvin_tint
      (ModeParseStrategyConfig.a: rgblight H6188 and the 0x0d bulbs H6006 /
      H6008, run by OpInfo4Detail.x; the rgbiclight/h6160 copy) and
      rgb_kelvin_tint_positive (H60501InfoDetail: the tint read only when
      kelvin > 0). The strategy shows the tint when the Kelvin is non-zero
      (shows_tint), else r/g/b; OpInfo4Detail.parseModeValidBytes takes CCT
      only for kelvin > 0 (is_cct). So a negative Kelvin shows the tint bytes
      as an RGB colour. Firmware: H6006 / H6008 build [0D][R][G][B][kelvin hi]
      [kelvin lo] with every later byte zero, so the tint arrives as 0. Was
      mode_color_0d_typed kind 2 (is_cct there = shows_tint).
    doc-ref:
      - split_rgblight/com/govee/rgblight/mode/ModeParseStrategyConfig.java:35-46
      - split_bulblightstringv1/com/govee/bulblightstringv1/bulblight/mode/ModeParseStrategyConfig.java:55-62
      - split_rgbiclight/com/govee/rgbiclight/h6160/newdetail/Vm4Light.java:578-588
      - split_hollowlamp/com/govee/hollowlamp/newdetails/h60501/H60501InfoDetail.java:121-148
      - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:766-779
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:634-650
      - firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c:396-406
    params:
      - id: tint_if_positive
        type: bool
        doc: the tint is read only when kelvin > 0 (the _positive decode)
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
        doc: the Kelvin's tint colour the strategy shows in CCT mode
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
      [r g b][flag][tint r g b], no Kelvin bytes: decodes rgb_flag1_tint (test
      flag_eq_1: the tint read only when flag == 1, CCT then; rgblight
      ModeParseStrategyConfig.b, rgbiclight/h6160), rgb_flagnz_tint (test
      flag_nonzero: h6104), rgb_flag_tint_nokelvin (test flag_nonzero: the
      legacy AbsBleAdjustAcV1 screens; a colour-temperature state with no
      Kelvin number) and rgb_pair_flag_lookup (test none: colour = flag != 0 ?
      tint : color, its Kelvin a KelvinHelp table lookup, PROCEDURAL). The
      Kelvin of the tint is a table lookup in every decode (client policy).
    doc-ref:
      - split_rgblight/com/govee/rgblight/mode/ModeParseStrategyConfig.java:52-62
      - split_rgbiclight/com/govee/rgbiclight/h6160/newdetail/Vm4Light.java:562-575
      - split_rgblight/com/govee/h6104/newdetail/Info4Detail.java:165-176
      - base-classes10/com/govee/base2light/ble/controller/AbsMode.java:17-23
      - split_bulblightstringv1/com/govee/bulblightstringv1/bulblight/mode/ModeParseStrategyConfig.java:26-53
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
      [bri1 s8][r1 g1 b1][bri2 s8][r2 g2 b2]: decode bri_rgb_pair
      (H70606162Info4Detail, goodsType 98): one record per lamp head, the
      brightness bytes kept as signed Java bytes. Never a colour temperature on
      the heads; the default strategy that runs first on the same body may set
      an illumination Kelvin (client policy).
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
      Params of sub-mode 0x13 (music) under the screen's music decode.
      Not decoded: absent, bytes in rest. (Sub-mode 0x16 SubModeAbsMusic is
      another layout, not 0x13.)
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
      Decode v1_flagged, SubModeMusicV1.parse (dreamcolorlightv1): [music_code]
      [value][dynamic][spec_color_flag] read unconditionally, then [r][g][b]
      only when the flag is not 0. The discriminator is the flag, not the
      length (replies are zero-padded). Was music_read_reply.
    doc-ref:
      - split_rgbiclight/com/govee/dreamcolorlightv1/ble/SubModeMusicV1.java:87-98
    params:
      - id: len_params
        type: u2
    seq:
      - id: music_code
        type: u1
        if: len_params >= 1
        doc: bArr[0], the music effect code
      - id: value
        type: u1
        if: len_params >= 2
        doc: bArr[1], the sensitivity (the app clamps it to 0..99)
      - id: dynamic
        type: u1
        if: len_params >= 3
        doc: bArr[2], the dynamic flag
      - id: spec_color_flag
        type: u1
        if: len_params >= 4
        doc: bArr[3]; not 0 = a specified colour follows
      - id: color
        type: govee_common::rgb
        if: len_params >= 7 and spec_color_flag != 0
        doc: bArr[4..6], the specified colour
    instances:
      sensitivity:
        value: 'value > 99 ? 99 : value'
        if: len_params >= 2
        doc: the sensitivity clamped to 0..99, as the app keeps it
      is_short:
        value: len_params < 4 or (spec_color_flag != 0 and len_params < 7)
  music_v2_default:
    doc: |
      Decode v2_default: the bytes SubMode4MusicV2's default strategy reads,
      as h60a1 Info4Detail builds it: SubMode4MusicV2(0,
      newMusicCode4H705EDFList, 1, null), so the extra-byte code is 4 and the
      new-music list {49, 71, 72, 81, 113}. It reads [effect_code][sensitivity]
      and stops for a new-music code; otherwise [sub_effect_code] (only when
      effect_code == 4), then [color_flag], then [r][g][b] (only when
      color_flag != 0). So a value needs 2 params for a new-music code, else 3
      (4 for code 4), or 6 (7 for code 4) with a colour; a shorter one makes
      the strategy throw (is_short). The H60A6 / H60A1 / H60A4 / H12D0 screens
      install no strategy of their own; H1270 / H1250 install their own music
      strategy (another rule). Verified against the F4 app port (q_h60a6).
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4MusicV2.java:49-73
      - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4MusicV2.java:76-78
      - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4MusicV2.java:163-167
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:122-125
      - base-classes10/com/govee/base2light/ble/music/AbsNewMusicEffect.java:114-116
      - split_h60ax/com/govee/pact_h60a0/adjust/h1270/VM4LightH1270.java:374
      - split_h60ax/com/govee/pact_h60a0/adjust/h1250/VM4LightH1250.java:455
    params:
      - id: len_params
        type: u2
    seq:
      - id: effect_code
        type: u1
        if: len_params >= 1
        doc: params[0], unsigned
      - id: sensitivity_raw
        type: u1
        if: len_params >= 2
        doc: params[1], unsigned (the app stores min(it, 99))
      - id: sub_effect_code
        type: u1
        if: len_params >= 3 and not is_new_music and effect_code == 4
        doc: params[2], read only when effect_code == 4
      - id: color_flag
        type: u1
        if: len_params >= 3 and len_params > ofs_flag and not is_new_music
        doc: params[2] (params[3] after code 4); not 0 = a colour follows
      - id: color
        type: govee_common::rgb
        if: >-
          len_params >= 6 and len_params >= ofs_flag + 4 and not is_new_music
          and color_flag != 0
        doc: the colour (ColorUtils.H(r, g, b))
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
        doc: coerceAtMost(coerceAtLeast(0, params[1]), 99)
      is_short:
        value: >-
          len_params < 2 or (not is_new_music and (len_params <= ofs_flag
          or (color_flag != 0 and len_params < ofs_flag + 4)))
  device_info_record:
    doc: |
      AA 07 reply / TLV 0x07: [selector][info]. The selector picks the record
      (enum device_info_selector). The app's connect-time reads are AA 06, AA
      07 03 (hard version) and AA 07 02 (device id), plus AA 21 / 20 / 14 for
      Wi-Fi (h5080 / H5083: only in the add flow, PairAcV1.t2). The binary
      0x10 / 0x11 records are read by the light screens only as 0xAC TLV 0x07
      records (Compose4BaseInfoSingleRead.w), and as AA 07 10 / 11 only on
      H5086 (its own DeviceBasicInfoController / WifiInfoController, the same
      layouts); no verified firmware handles AA 07 10 / 11 (H6006 / H6008 /
      H5083 / H6047 send no reply; H60A1 / H60A6 echo the request, all zero).
      With source status_tlv, `info` is read ONLY for 0x10 / 0x11: w() logs
      and ignores every other selector (0x02 and 0x03 included) with no body
      read, so their bytes are the rest. A 0x10 / 0x11 record too short for
      w()'s arraycopy raises, as the app throws.
      Not typed (bytes in rest): 0x07 DSP and 0x08 other info (no verified
      image answers them), 0x0A / 0x0B (H6008 / H60A1 / H60A6 echo the request
      with no data; McuSoftVersionControllerV1 reads getStrData of bytes 2..18
      selector included, so the echo reads as an empty string).
      Firmware per selector (AA 07 data starts at frame byte 3): H6047 answers
      02 / 03 / 04 / 06 (others no reply, sel - 2 > 4); H5083 02 / 03 / 06;
      H6006 / H6008 02 / 03 / 06 (H6008 also 0A / 0B, echoed); H60A1 / H60A6
      02 / 03 / 06 / 0A / 0B (04 / 07 / 10 / 11 echoed by the dispatcher).
    doc-ref:
      - split_plugnew/com/govee/h5080/add/PairAcV1.java:652
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:370-437
      - split_plugnew/com/govee/h5086/ble/controller/PairToDeviceInfoReadController.java:240
      - split_plugnew/com/govee/h5086/ble/controller/PairToDeviceInfoReadController.java:245
      - split_plugnew/com/govee/h5086/ble/controller/ReadInfoController.java:64
      - base-classes10/com/govee/base2light/ble/controller/McuSoftVersionControllerV1.java:37-49
      - H6047 handler @0x01005370
      - H5083 ble_msg_get_dev_info @0x9b031b08
      - H6006 ble_msg_get_dev_info @0x9b03cb88
      - H6008 ble_msg_get_dev_info @0x9b03c92c
      - H60A1 ble_msg_get_dev_info @0x9b0454bc
      - H60A6 ble_msg_get_dev_info @0x9b04a8e4
      - H6047 code @0x010053ca
      - H6047 code @0x010053ce
      - H6047 code @0x0101693c
      - H60A1 code @0x9b04dfdc
    params:
      - id: source
        type: u1
        enum: source
    seq:
      - id: selector
        type: u1
        enum: govee_common::device_info_selector
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
        doc: |
          the two selectors the app's status parser reads a body for (w());
          for any other selector w() logs and returns, and u() walks on
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:370-382
  uid_record:
    doc: |
      AA 07 02: the 8-byte device id (atomic). Firmware sends it byte-reversed:
      H6006 / H6008 decode the colon-separated uuid string and store it in
      reverse; H60A1 / H60A6 write it at frame bytes 3..10 byte-reversed;
      H6047 sends its stored id [rand lo, rand hi, BLE addr reversed]
      byte-reversed (created on the first AA 07 02 / AA B1 if absent), so uid
      bytes 0..5 = the AA 07 06 address bytes.
    doc-ref:
      - H6006 code @0x9b03c87c
      - H6008 code @0x9b03c740
      - H60A6 code @0x9b053f9c
      - H6047 code @0x010053da
      - H6047 code @0x010029c4
      - H60A1 code @0x9b04dfdc
    seq:
      - id: uid
        size: 8
    instances:
      uid_is_invalid:
        value: uid == [0, 0, 0, 0, 0, 0, 0, 0]
        doc: |
          all-zero = no device id: SnController.parseValidBytes sends a failure
          for it (NumberUtil.isInvalidUuid)
        doc-ref:
          - base-classes10/com/govee/base2light/ble/controller/SnController.java:48-53
          - base-classes10/com/govee/base2home/util/NumberUtil.java:300-307
      uid_has_leading_pair:
        value: uid[7] == 0 and uid[6] == 0
        doc: |
          the rendered id (toAddressBytes(uid, false), uid[7] first) starts with
          '00:00:', which SnController drops ONCE (replaceFirst); meaningful
          only when not uid_is_invalid
        doc-ref:
          - base-classes10/com/govee/base2light/ble/BleUtil.java:547-580
          - base-classes10/com/govee/base2light/ble/controller/SnController.java:54-57
  basic_info:
    doc: |
      Selector 0x10: [uid 8][sw version3][hw version3][dsp u16le]. w()
      (TLV 0x07) reads the uid byte-REVERSED via toAddressBytes(hFirst =
      false) with one leading '00:00:' pair stripped, skips an all-zero uid
      (NumberUtil.isInvalidUuid), and renders both versions with x(). H5086's
      DeviceBasicInfoController reads the same uid at 1..8, sw at 9, hw at 12.
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
        doc: the DSP version (little-endian)
    instances:
      uid_is_invalid:
        value: uid == [0, 0, 0, 0, 0, 0, 0, 0]
        doc: |
          all-zero = no device id: the 0xAC TLV 0x07 path skips it. The
          single-frame BasicInfoController (no caller) has no such test.
        doc-ref:
          - base-classes10/com/govee/base2home/util/NumberUtil.java:300-307
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:405-415
          - base-classes10/com/govee/base2light/ble/controller/BasicInfoController.java:43-50
      uid_has_leading_pair:
        value: uid[7] == 0 and uid[6] == 0
        doc: |
          the rendered id starts with '00:00:', which the app drops ONCE (Regex
          replaceFirst); render uid[5]..uid[0] then
        doc-ref:
          - base-classes10/com/govee/base2light/ble/BleUtil.java:547-580
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:410-412
  wifi_info:
    doc: |
      Selector 0x11: [wifi mac 6, forward order (toAddressBytes(hFirst =
      true))][wifi sw version3][wifi hw version3] (the Wi-Fi MODULE's
      versions). w() always sets the Wi-Fi MAC ('' when all zero); H5086's
      WifiInfoController reads mac at 1..6, sw at 7, hw at 10.
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
        doc: |
          all-zero = none: the TLV 0x07 path renders ''
          (DeviceUtil.checkWifiMacValid = any byte != 0). The single-frame
          BasicWifiInfoController (no caller) has no such test.
        doc-ref:
          - base-classes4/com/ihoment/base2app/util/DeviceUtil.java:49-56
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:384-386
  timer_slots:
    doc: |
      AA 23 reply / TLV 0x23: [slot][timer_record], or [FF][4 x timer_record]
      for an all-slots read (ControllerNewTimer.Companion.d: b == -1 -> 4
      records from byte 1, else one). Firmware: H6006 / H6008 answer a
      single-slot read (0..3) with slot 0's record and the requested slot
      echoed; H60A6 AA 23 <slot | FF> (FUN_9b02b64c). The captured H60A6 TLV
      0x23 is 16 bytes: the app reads byte 0 as the slot and one record, and
      ignores the rest (here: rest). The records are capped at what the value
      holds, so a short value reads (is_short) where d() throws.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerNewTimer.java:52-77
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:446-495
      - firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c:303-335
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:437-444
      - H6047 handler @0x01004d6e
    seq:
      - id: slot
        type: u1
        doc: the slot (0..3), or 0xFF = all four
      - id: records
        type: govee_common::timer_record
        repeat: expr
        repeat-expr: 'num_wanted > num_fit ? num_fit : num_wanted'
        doc: the records read (the app's count, capped at what the value holds)
    instances:
      is_all_slots:
        value: slot == 0xff
      num_wanted:
        value: 'slot == 0xff ? 4 : 1'
        doc: the records ControllerNewTimer.d copies
      num_fit:
        value: (_io.size - 1) / 4
        doc: the whole records the value holds
      is_short:
        value: num_wanted > num_fit
        doc: the value ends inside a record d() copies (it throws there)
  timer_count:
    doc: |
      Form timer_count of 0x12: the H5083 AA 12 reply [group echo][count]
      (firmware byte 3 = FUN_9b0219e4(byte 2)); the app keeps
      BleUtils.L(bArr[1]) and reads ceil(count / 4) AA 13 pages.
    doc-ref:
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:147-149
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:197-223
    seq:
      - id: group
        type: u1
        doc: the request's group byte echoed
      - id: count
        type: u1
        doc: the number of timers
  plug_timer_page:
    doc: |
      Form plug_timer of 0x13: the H5083 AA 13 reply [page | group << 4]
      [4 x timer_record]. The app takes the low nibble as the page
      (BleUtil.parseOneByteBy4Bit(bArr[0])[1]), copies bytes 1..16 as four
      records, keeps the non-zero ones (TimerInfo.Companion.c) and numbers them
      from page * 4. Firmware: records are the u32s at base+0x20+4*slot, slots
      >= count are zero, and a page >= 4 returns 16 zero record bytes.
    doc-ref:
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:232-246
      - base-classes10/com/govee/base2light/ble/BleUtil.java:518-520
      - base-classes12/com/govee/base_h71xx/sku_base/adjust/component/bean/TimerInfo.java:52-60
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:111-143
    seq:
      - id: page_and_group
        type: u1
      - id: records
        type: govee_common::timer_record
        repeat: expr
        repeat-expr: 4
    instances:
      page:
        value: page_and_group & 0x0f
        doc: the page (low nibble), as the app reads it
      group:
        value: page_and_group >> 4
        doc: the group (high nibble)
  plug_delay_state:
    doc: |
      Form plug_delay of 0xB0: the h5080 AA B0 reply [outlet][type][hour]
      [minute][remaining seconds u24be]. The app builds DelayOnOffInfo(I([b2]),
      I([b3]), I([b4, b5, b6], true)) and posts it to one LiveData when
      bArr[1] == 0, else to the other. Firmware (H5083): all zero unless the
      time is valid; a remaining time over 86400 s reads 0.
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
        doc: seconds left, big-endian (BleUtils.I(.., true))
  cmd_12_state:
    doc: |
      Command 0x12 under form_12: timer_count -> timer_count; wake: the
      wake-up record (TimerConfig.Companion.n, per-SKU layout version), not
      typed (bytes in rest).
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
      Command 0x13 under form_13: plug_timer -> plug_timer_page; night_mode is
      not typed (bytes in rest).
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
      Command 0x14 under form_14: gradual -> gradual_state; wifi_mac -> the
      AA 14 Wi-Fi MAC (govee_common::mac, forward order).
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
      Command 0x30 under form_30: zone_power -> zone_bits (the aa 30 reply on
      H60A6, firmware-sourced, and TLV 0x30); light_direction ->
      direction_state for the TLV only (H6038; no reply decoder is traced).
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
  cmd_36_state:
    doc: |
      Command 0x36 under form_36: bar_pair -> bar_state for the aa 36 reply
      (H6047); bar_index -> light_flags for TLV 0x36 (H6038 / H6048 / H1771).
      Other combinations (an aa 36 reply under bar_index, H6057 worry-free)
      have no traced reader: bytes in rest.
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
      Command 0x41 under form_41: power_memory -> power_memory (aa 41 reply
      on H60A6, firmware-sourced, and TLV 0x41); section_count ->
      govee_common::section_count for the H6062 aa 41 reply only (no TLV
      reader).
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
      Command 0xA2 under form_a2: bulb_batch_v1 -> bulb_batch_v1 (H61A8);
      color_group_rgb -> color_group with 3-byte records, whatever
      group.has_brightness says (Controller4ColorInfoByGroup.p, the
      generateReadController4OnlyColor read).
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
      Command 0xA5 under form_a5: color_group -> color_group (4-byte records;
      H6047, H6641, H6062, H6061, H60A6); bulb_batch_v2 -> bulb_batch_v2
      (H61A8, aa reply only). Nothing in the reply names the controller that
      asked, so the form decides (both share the opcode, not the layout).
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
      One colour group [group_index][records] (Controller4ColorInfoByGroup):
      the TLV 0xA5 value, or the AA A5 / AA A2 <group> reply body. q() (4-byte
      [brightness, R, G, B]) / p() (3-byte [R, G, B]) read exactly the count
      their caller passes, ignore any bytes after them and throw on a value
      too short for them; has_brightness picks the record. The count:
      - TLV, rule from_len: (len - 1) / record size (H60B1ViewModel,
        VM4LightH60C1, H6850DetailVM);
      - TLV, rule fixed: num_per_group (VM4H6038, VM4H6048, the h6047 /
        rgbiclight / h60a1 VM4Light);
      - TLV, rule by_pieces (VM4LightH60A6): num_per_group, except the last of
        ceil(num_pieces / num_per_group) groups, which holds the remainder
        (num_pieces less the full groups before it; the lambda tests for the
        last group with the group byte read signed);
      - REPLY: the caller's group size, num_per_group, for every group
        (VM4LightH60A6.q5 passes oneGroupColorSize; the H6047 / H6641 / H6062 /
        H6061 senders 4); from_len counts from the body.
      The records read are capped at what the value holds (num_records), and
      is_short says the app's count did not fit (q() / p() throw on their
      arraycopy; the app takes only 20-byte frames, so a short reply never
      reaches q()).
      Placement. A reply's group byte is never checked against the request
      (the app matches on proType and command alone; Companion.i drops the
      group byte and the caller places the records by the group it asked
      for, trimming the last group to the piece count, SubMode4Color.j), so
      no group check applies to a reply (PORT-review item 1). On the TLV path
      the screens place the records by the value's own group byte through
      SubMode4Color.j, which throws for group 0 and for a group starting past
      the last piece, and copies nothing for one starting exactly at the end
      (is_placed false): the H60A6 lambda (rule by_pieces; its last group
      takes its own copy, and every group byte of 0x80 or more throws) and
      the fixed-rule h6047 screens (VM4Light, VM4H6038, VM4H6048, with
      Support.getColorPieceSize as num_pieces). app_rejects_group flags the
      throw where num_pieces is known (places_by_group); the value still
      reads (R7).
      Other 0xA5 layouts are not this type: Companion.r [group][brightness]
      [3 x R, G, B] (the gradient read; VM4LightH1630, H707ABC),
      Compose4BaseInfoSingleRead.Companion.d 4-byte records with no group
      byte (h7085), the h61d3 adjustNew inline read. VM4LightH1250 tests the
      last group differently (unsigned group byte vs pieces / 4 + 1) and is
      not modelled. Was color_group_status / color_group_status_counted /
      color_group_read / color_group_direct_frame.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:145-175
      - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:58-75
      - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:111-136
      - split_floorlamp/com/govee/h60b1/detail/H60B1ViewModel.java:159-160
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:105-142
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6.java:504-530
      - split_h60ax/com/govee/pact_h60a0/pact/Support.java:154-159
      - split_h60ax/com/govee/pact_h60a0/pact/Support.java:183-185
      - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4Color.java:113-147
      - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:176-179
      - split_h6047/com/govee/h6047/detail/NewDetailVm.java:343-380
      - split_h61d3/com/govee/h61d3/adjustNew/VM4Light.java:636-669
      - split_h6062/com/govee/striplight/newdetail/VM4Light.java:393-396
      - split_h6061/com/govee/cubelight/newdetails/CubeVM4Light.java:409-411
      - split_h7050/com/govee/h7050/newdetail/h1630/VM4LightH1630$afterConnectedSingleReadDeviceInfo$1.java:104-117
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:182-212
      - split_h60ax/com/govee/pact_h60a0/adjust/h1250/VM4LightH1250$afterConnectedSingleReadDeviceInfo$1.java:107-139
      - H60A6 FUN_9b025cf8 @0x9b025cf8
      - H6047 code @0x01004c20
      - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:177-194
      - split_outdoorlight/com/govee/h7080/adjust/h7085/VM4Light.java:164-169
      - split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:604-613
      - split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:572-579
      - split_h6047/com/govee/h6047/newdetail/vm/VM4Light.java:302-308,332
      - split_h6047/com/govee/h6047/newdetail/h6048/VM4H6048.java:390-396,419
      - split_h6047/com/govee/h6047/pact/Support.java:114-119
      - base-classes10/com/govee/base2kt/utils/BleUtils.java:272-274
      - split_h6079/com/govee/h6020/newDetial/H6020VM4Light.java:637
      - split_rgbiclight/com/govee/rgbiclight/VM4Light.java:958
      - split_tvlightv4/com/govee/pact_tvlightv2/newdetail/VM4Light.java:493
      - split_h7050/com/govee/h7050/newdetail/h1630/VM4LightH1630.java:271
      - split_h7075/com/govee/h7075/h707A/H707ABCDetailVM.java:374
    params:
      - id: group
        type: govee_profile::color_group_form
      - id: source
        type: u1
        enum: source
      - id: has_brightness
        type: bool
        doc: |
          4-byte [brightness, R, G, B] records (q()) when true, else 3-byte
          [R, G, B] (p()): group.has_brightness for 0xA5, false for aa a2
    seq:
      - id: group_index
        type: u1
        doc: |
          the 1-based group number (the request byte); q() passes it unsigned
          (BleUtils.L) to the callback
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
        doc: bytes per record
      num_groups:
        value: >-
          group.num_per_group == 0 ? 0 :
          (group.num_pieces + group.num_per_group - 1) / group.num_per_group
        doc: |
          ceil(num_pieces / num_per_group), the app's i2 (its integer division
          throws for a group size of 0; here 0)
      is_last_group:
        value: group_index == num_groups
        doc: the group that holds the remainder (the app's b == i2)
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
        doc: |
          the TLV screen places the records by the value's group with
          SubMode4Color.j and the piece count is known
      num_records_wanted:
        value: >-
          group.rule == govee_common::group_count_rule::from_len
          ? (_io.size - 1) / len_record :
          (checks_group and is_last_group
          ? group.num_pieces - (num_groups - 1) * group.num_per_group
          : group.num_per_group)
        doc: the record count the app passes to q() / p()
      num_records:
        value: >-
          num_records_wanted > (_io.size - 1) / len_record
          ? (_io.size - 1) / len_record : num_records_wanted
        doc: the records read (the wanted count, capped at what the value holds)
      is_short:
        value: num_records_wanted > (_io.size - 1) / len_record
        doc: the value is too short for the app's count (q() / p() throw)
      group_index_signed:
        value: 'group_index >= 0x80 ? group_index - 0x100 : group_index'
        doc: value[0] as the Java byte the app compares with num_groups
      app_rejects_group:
        value: >-
          places_by_group and (group_index == 0
          or (checks_group and group_index >= 0x80)
          or (not is_last_group
          and (group_index - 1) * group.num_per_group > group.num_pieces))
        doc: |
          SubMode4Color.j throws for this group (group 0, a group that starts
          past the last piece, or on the H60A6 lambda a byte of 0x80 or more)
      is_placed:
        value: >-
          not places_by_group or is_last_group
          or (group_index - 1) * group.num_per_group != group.num_pieces
        doc: |
          false for the exact-end no-op group (start == num_pieces, not the
          last group): SubMode4Color.j copies nothing
      app_read_fails:
        value: places_by_group and (is_short or app_rejects_group)
        doc: |
          the TLV screen's colour-group parse throws on this group (q() / p()
          on a short value, or SubMode4Color.j)
  bulb_batch_v1:
    doc: |
      Form bulb_batch_v1 of 0xA2: H61A8 mechanism-B V1 batch frame
      (BulbGroupColor.parseBytes): [batch_seq][4 x rgb], POSITIONAL (segment =
      (batch_seq - 1) * 4 + i; no group index or brightness byte). The client
      accumulates maxGroup batches. Request AA A2 <batch_seq>.
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
      Form bulb_batch_v2 of 0xA5: H61A8 mechanism-B V2 batch frame
      (BulbGroupColorV2.parseBytes): [batch_seq][3 x bri_rgb], the leading
      byte of each record the per-segment BRIGHTNESS (SubModeColorV2
      brightnessSet); positional, 3 groups per batch (a client controller
      constant, BulbStringColorControllerV2, default 3; H61A8 = 5 batches x 3).
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
      AA 40 reply and TLV 0x40 (same prefix): [ic_count s16be][segment], read
      by ControllerOnlyReadIcSegmentNum (the signed short of bytes 0..1, then
      byte 2 as a Java byte), and a second [ic_count_2][segment_2] pair at
      bytes 3..5 that ControllerIcNum.Companion.e also reads when the value
      holds 6 bytes or more. The only live-BLE capability read: the
      addressable IC (lamp-bead) count. Firmware H6047 answers [00][IC count
      low byte], so segment = 0 there. `ic_count` is the old ic_count_signed
      (the field is signed now).
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerOnlyReadIcSegmentNum.java:30-37
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerIcNum.java:141-157
      - H6047 handler @0x010051e8
      - split_h6047/com/govee/h6047/newdetail/Info4Detail.java:266-281
    seq:
      - id: ic_count
        type: s2
        if: _io.size >= 2
        doc: the IC count, signed as the app reads it (BleUtil.getSignedShort)
      - id: segment
        type: u1
        if: _io.size >= 3
        doc: |
          the segment (group) count, byte 2 (segment_signed is the Java byte
          the readers keep); H6671 / H6672 read only the IC count from TLV
          0x40
      - id: ic_count_2
        type: s2
        if: _io.size >= 6
        doc: |
          the second strip's IC count, bytes 3..4 (ControllerIcNum.Companion.e,
          through the same byte-order check as ic_count); 0 = none
      - id: segment_2
        type: u1
        if: _io.size >= 6
        doc: the second strip's segment byte, byte 5 (a Java byte in e)
    instances:
      segment_signed:
        value: 'segment >= 0x80 ? segment - 0x100 : segment'
        if: _io.size >= 3
        doc: |
          byte 2 as the Java byte ControllerOnlyReadIcSegmentNum.c and
          ControllerIcNum.Companion.e keep (0x80 and up read negative)
      has_second_pair:
        value: _io.size >= 6 and ic_count_2 != 0
        doc: e adds the second pair only when its IC count is not 0
      ic_total:
        value: 'ic_count + (has_second_pair ? ic_count_2 : 0)'
        if: >-
          _io.size >= 2 and not ic_count_byte_order_suspect
          and not (has_second_pair and ic_count_2 >= 0x1000)
        doc: |
          the IC count the goodsType-375 (H61D3 VM4Light) and H6609 / H6250
          (TLV 0x40) screens use: the first pair's count plus the second's
          when present; null when either pair's byte-order check fires (the
          checker throws)
        doc-ref:
          - split_h61d3/com/govee/h61d3/adjustNew/VM4Light.java:550-563
          - split_h61d3/com/govee/h61d3/pact/H61D3Support.java:786-788
          - split_h6840/com/govee/h6609/h6250/NewDetailVm$afterConnectedSingleReadDeviceInfo$1.java:75-87
      is_short:
        value: _io.size < 2
        doc: no IC count (getSignedShort of two bytes throws)
      ic_count_byte_order_suspect:
        value: _io.size >= 2 and ic_count >= 0x1000
        doc: |
          The app's IC-count byte-order check: true when the signed count is >=
          getSignedShort(0x10, 0) = 0x1000; a negative count passes. When it
          fires the checker re-reads bytes 1..2 of a fresh 2-byte array, which
          throws: the app rejects the reply and never swaps. Checkers: h61d3
          ReadIcController.Companion.a (VM4Light.j5's goodsTypes, IoT
          Info4Detail.L), kt ControllerIcNum.Companion.a (through f / d / e),
          legacy IcNumController.u, h7020 Controller4IcNumber.Companion.a. Not
          checked: ControllerIcNum.Companion.c (VM4LightH667172.j5,
          H3401DetailVM.Y4), the dreamcolorlightv1 ReadIcController,
          ControllerOnlyReadIcSegmentNum.Companion.c (no caller).
        doc-ref:
          - split_h61d3/com/govee/h61d3/ble/controller/ReadIcController.java:49-64
          - split_h61d3/com/govee/h61d3/adjustNew/VM4Light.java:542-549
          - split_h61d3/com/govee/h61d3/adjustNew/Info4Detail.java:326-332
          - base-classes11/com/govee/base2light/kt/general_controller/ControllerIcNum.java:64-74
          - base-classes10/com/govee/base2light/ble/controller/IcNumController.java:17-24
          - split_h7050/com/govee/h7020/newDetail/controller/Controller4IcNumber.java:47-62
          - base-classes11/com/govee/base2light/kt/general_controller/ControllerIcNum.java:94-107
          - split_rgbiclight/com/govee/rgbiclight/h667172/VM4LightH667172.java:343-348
          - split_h705a/com/govee/h705a/h3401/H3401DetailVM.java:488-497
          - split_rgbiclight/com/govee/dreamcolorlightv1/ble/ReadIcController.java:10-36
          - split_h7075/com/govee/h7075/ble/controller/CheckIc4SettingController.java:48-58
          - base-classes10/com/govee/base2light/ble/BleUtil.java:304-306
      ic_count_checked:
        value: ic_count
        if: _io.size >= 2 and not ic_count_byte_order_suspect
        doc: |
          the IC count as the checking readers return it, null when the check
          fires (the checker throws before it returns a count). The per-SKU
          IntRange clamp (ReadIcController.Companion.d; H61D3Support.s) stays
          client logic.
        doc-ref:
          - split_h61d3/com/govee/h61d3/ble/controller/ReadIcController.java:68-95
          - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:122-127
          - split_h61d3/com/govee/h61d3/adjustNew/VM4Light.java:583-589
          - split_h61d3/com/govee/h61d3/adjustNew/Info4Detail.java:302-324
          - split_h61d3/com/govee/h61d3/ble/controller/ReadIcController.java:102-110
          - split_h61d3/com/govee/h61d3/pact/H61D3Support.java:1547-1561
  gradual_state:
    doc: |
      AA A3 / TLV 0xA3 (and 0x14 under form gradual): the gradual (fade) flag.
      ControllerGradual4BleWifi.Companion.h decodes on as bArr[0] == 1 (it also
      requires validBytes.length == 17, which every 20-byte aa a3 frame
      meets); straightfloorlamp and H1771 read TLV 0xA3 value[0] == 1.
      Firmware: H60A6 light config byte 5, H60A1 byte 4; H6047 cfg+0x1b.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerGradual4BleWifi.java:31-37
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerGradual4BleWifi.java:94-96
      - split_hollowlamp/com/govee/straightfloorlamp/adjust/VM4Light$afterConnectedSingleReadDeviceInfo$1.java:118-119
      - split_hollowlamp/com/govee/hollowlamp/newdetails/h1771/H1771DetailVM$afterConnectedSingleReadDeviceInfo$1.java:126-127
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:550-561
      - H6047 handler @0x01004cdc
    seq:
      - id: state
        type: u1
    instances:
      is_on:
        value: state == 1
        doc: any byte other than 1 is off
  bar_state:
    doc: |
      Form bar_pair of 0x36: the H6047 AA 36 reply (ComposeLightController /
      the heartbeat ComposeLightHeartController): [left][right][movie / feast
      switch]. The device is on when left OR right is on. The positions are
      logical (swappable at setup, h6047 LightPosAc). Firmware: [bar0][bar1]
      [power ? feast flag : 0].
    doc-ref:
      - split_h6047/com/govee/h6047/ble/controller/ComposeLightController.java:27-29
      - split_h6047/com/govee/h6047/ble/controller/ComposeLightHeartController.java:13-17
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
        doc: the movie / feast switch (== 1 on), read-only
    instances:
      left_on:
        value: left == 1
        doc: bArr[0] == 1, any other byte off
      right_on:
        value: right == 1
        doc: bArr[1] == 1
      is_on:
        value: left_on or right_on
        doc: the heartbeats report the device on when either bar is
  light_flags:
    doc: |
      Form bar_index of TLV 0x36: one flags byte. H6038 / H6048: two lights,
      bit 0 and bit 1 (ParseUtils.getBit2Boolean positions 1 / 2, yellowLight /
      blueLight). H1771: three flags from BleUtil.parseBytes4Bit(value[0])
      entries 7 / 6 / 5, i.e. bits 0 / 1 / 2.
    doc-ref:
      - split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:591-593
      - split_h6047/com/govee/h6047/newdetail/h6048/VM4H6048.java:397-399
      - split_hollowlamp/com/govee/hollowlamp/newdetails/h1771/H1771DetailVM$afterConnectedSingleReadDeviceInfo$1.java:132-149
      - base-classes10/com/govee/base2light/ble/BleUtil.java:474-486
      - base-classes12/com/govee/base2light/util/ParseUtils.java:55-57
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
      Form zone_power of 0x30: zone power as bit 0 of each byte
      (ParseUtils.getBit2Boolean(b, 1)); the other bits are not zone power.
      H60A6 / H1270 / H1250 read two zones (TLV value[0], value[1]); H12D0
      three. Firmware H60A6 AA 30: b2 = FUN_9b02b464 (UserConfig+9), b3 =
      FUN_9b02b498 (+0xA); in a reply zone_c is the request echo.
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:102-104
      - split_h60ax/com/govee/pact_h60a0/adjust/h1270/VM4LightH1270$afterConnectedSingleReadDeviceInfo$1.java:78-80
      - split_h60ax/com/govee/pact_h60a0/adjust/h1250/VM4LightH1250$afterConnectedSingleReadDeviceInfo$1.java:104-106
      - split_h60ax/com/govee/pact_h60a0/adjust/h12d0/VM4LightH12D0$afterConnectedSingleReadDeviceInfo$1.java:78-81
      - base-classes12/com/govee/base2light/util/ParseUtils.java:55-57
      - base-classes12/com/govee/base2light/util/ParseUtils.java:72-74
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
      Form light_direction of TLV 0x30: the H6038 calibration direction,
      value[0] as one byte, logged as calibration (requested at :525).
    doc-ref:
      - split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:579-590
      - split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:525
    seq:
      - id: value
        type: u1
  power_memory:
    doc: |
      Form power_memory of 0x41: the power-off memory (power-on mode) record
      [02][mode]; the app reads byte 1 on every family that requests 0x41
      (H60A6, H6671 / H6672, H6038 / H6048). Firmware H60A6
      ble_check_basic_info fills [0x41][02][02][start mode = UserConfig+4],
      the field 33 41 02 <v> writes; AA 41 answers [02][mode]. The app's own
      aa 41 parser, h60a1 Info4Detail.R, takes the same byte but has no
      caller. Was status_seg_info (misnamed).
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:99-100
      - split_rgbiclight/com/govee/rgbiclight/h667172/VM4LightH667172$afterConnectedSingleReadDeviceInfo$1.java:108-115
      - split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:564-571
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:436-442
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:471-482
      - H60A6 ble_check_basic_info @0x9b04c0bc
      - H60A6 code @0x9b0270b8
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
      AA B1 reply: [selector][8-byte account-lock secret]; the 33 B2 write
      sends the 8 bytes alone. The account lock, NOT the wire cipher.
      Firmware: H60A6 answers b2 = 0 + 8 random bytes for the first request
      (or one more than 5000 ms later) and b2 = 1 + the key inside the 5 s
      window; H6047 [00] at step 1, [01][key 8] later; H5083 the same window
      with the request echoed at 11..18.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:57-66
      - split_plugnew/com/govee/h5086/ble/controller/SecretController.java:41-47
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:574-610
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:358-376
      - H6047 code @0x0100587c
    seq:
      - id: selector
        type: u1
      - id: secret
        size: 8
        doc: the 8-byte token (base64 off-wire); not decodable further
    instances:
      is_valid:
        value: selector == 1
        doc: |
          the reply carries a secret only when byte 0 == 1 (value[0] == 1 and
          length > 8, else sendFail; the length test holds once this parses)
  plug_spec:
    doc: |
      AA B3 reply (h5080 SpecController): one spec byte, stored as model.L and
      forwarded as the IoT spec id (Model.getSpec); never bit-decoded. The
      outlet count is not this byte (Support.getPlugNum(goodsType): 50 / 307
      -> 2, 90 -> 3, else 1). Firmware H5083: [00][request echo].
    doc-ref:
      - split_plugnew/com/govee/h5080/ble/controller/SpecController.java:6-19
      - split_plugnew/com/govee/h5080/pact/Support.java:126-131
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:153-156
    seq:
      - id: spec
        type: u1
  pact_state:
    doc: |
      AA EF reply: [pact_type u16be][pact_code]. PactController reads pactType
      = v(validBytes[0], validBytes[1]) big-endian and pactCode =
      validBytes[2] & 0xFF; kt ControllerProtocol.Companion.c the same. The
      curated screens take the pact from the advertisement instead. Firmware
      (ble_msg_get_adv_pro_type): 00 01 01 on H6006 / H60A1 / H60A6 / H6047,
      00 02 02 on H5083; the rest of the frame is the request echoed.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/PactController.java:9-20
      - base-classes10/com/govee/base2light/ble/controller/PactController.java:33-40
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerProtocol.java:26-48
      - H6006 ble_msg_get_adv_pro_type @0x9b03cc00
      - H60A1 ble_msg_get_adv_pro_type @0x9b045558
      - H60A6 ble_msg_get_adv_pro_type @0x9b04a980
      - H5083 ble_msg_get_adv_pro_type @0x9b031b80
    seq:
      - id: pact_type
        type: u2
      - id: pact_code
        type: u1
  debug_state:
    doc: |
      FIRMWARE-SOURCED, CAUTION (test-only; no app sender): the AA FF reply
      [selector][..]. Selector 08 is the factory ageing flag; H6047 answers 17
      zero bytes for any other selector (selector reads 0).
    doc-ref:
      - firmware-h60a1-wifi/decompiled/H60A1_ble_dispatcher.c:497-505
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:662-672
      - firmware-h6047/decompiled/H6047_decompiled.c:5955
    seq:
      - id: selector
        type: u1
      - id: body
        type:
          switch-on: selector
          cases:
            0x08: aging_flag
  aging_flag:
    doc: |
      FIRMWARE-SOURCED, CAUTION: AA FF 08 reply byte 3, the factory ageing
      flag (_factory_getAgingFlag: H60A1 FUN_9b02ca34, H60A6 FUN_9b02de4c; when
      it reads 0 the device also runs FUN_9b0313dc / FUN_9b037884(0) on H60A1
      and FUN_9b033c3c / FUN_9b03cadc(0) on H60A6, which stores 0 to
      0x1001fee9); H6047 1 if the flash word at 0x79400 is not 0xFFFFFFFF.
      Static analysis only.
    doc-ref:
      - H60A1 FUN_9b02ca34 @0x9b02ca34
      - H60A6 FUN_9b02de4c @0x9b02de4c
      - H60A6 FUN_9b03cadc @0x9b03cadc
    seq:
      - id: flag
        type: u1
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
      doc: the decode applies no colour-temperature test (is_cct false)
    1:
      id: kelvin_positive
      doc: kelvin > 0 (OpInfo4Detail.x and the screens' > 0 branches)
    2:
      id: flag_eq_1
      doc: flag == 1
    3:
      id: flag_nonzero
      doc: flag != 0
    4:
      id: pair_equal
      doc: the two Kelvins are equal and > 0
