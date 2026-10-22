meta:
  id: govee_ble_frame
  title: Govee BLE application frame (one GATT write or notification)
  license: MIT
  ks-version: '0.11'
  imports:
    - govee_common
    - govee_profile
    - govee_state
  bit-endian: le
  endian: be
doc: |
  STANDALONE (byte source: one GATT 2b11 write or 2b10 notification value):
  one frame [pro_type][body][checksum], usually 20 bytes; route picks the body.
  PROCEDURAL: the client computes and checks the checksum.
doc-ref:
  - base-classes10/com/govee/base2kt/utils/BleUtils.java:1030-1041,1260-1267,1376-1378
params:
  - id: direction
    type: u1
    enum: govee_common::direction
    doc: tx (client to device, 2b11) or rx (device to client, 2b10)
  - id: profile
    type: govee_profile
    doc: decode context; an unknown (0) selector leaves its body in rest
seq:
  - id: pro_type
    type: u1
    enum: govee_common::pro_type
  - id: body
    size: >-
      _io.size - (checksum_kind == checksum_kind::none ? 1 : 2)
    type:
      switch-on: route
      cases:
        'route::write_tx': write_command(profile)
        'route::write_read_tx': write_command(profile)
        'route::write_rx': write_ack
        'route::write_read_rx': write_ack
        'route::read_tx': read_request(profile)
        'route::read_rx': read_reply(profile)
        'route::status_read_tx': status_request
        'route::status_read_rx': status_chunk(true)
        'route::wifi_multi_tx': wifi_multi_request
        'route::wifi_multi_rx': status_chunk(false)
        'route::notify_rx': notify(profile)
        'route::handshake_tx': handshake_request
        'route::handshake_rx': handshake_reply
        'route::mic_tx': mic_command
        'route::mic_rx': mic_reply
        'route::multi_write_tx': mpm_packet
        'route::multi_write_rx': mpm_result
        'route::multi_read_rx': mpm_packet
        'route::multi_write_v1_tx': a3_packet
        'route::multi_write_v1_rx': upload_ack
        'route::multi_write_v2_tx': a4_packet
        'route::multi_write_v2_rx': upload_ack
        'route::mtu_multiple_write_rx': upload_ack
        'route::multiple_write_3f_tx': matter_packet
        'route::multiple_write_3f_rx': matter_result
        'route::factory_test_tx': factory_test_request
        'route::factory_test_rx': factory_test_reply
    doc: typed by route; raw bytes for a route with no case
  - id: checksum
    type: u1
    if: checksum_kind != checksum_kind::none
    doc: XOR or 8-bit sum (checksum_kind) of every byte before it
instances:
  route:
    value: >-
      (direction == govee_common::direction::rx ? 0x100 : 0)
      | pro_type.to_i
    enum: route
    doc: the body's route, one row per (direction, proType); 0x100 = rx
  checksum_kind:
    value: >-
      pro_type == govee_common::pro_type::gid_info
      ? checksum_kind::none.to_i
      : (pro_type == govee_common::pro_type::mic
      or (pro_type.to_i >= 0xb0 and pro_type.to_i <= 0xbf)
      ? checksum_kind::sum8.to_i
      : checksum_kind::xor.to_i)
    enum: checksum_kind
    doc: none (0x05), sum8 (0xA5, 0xB0..0xBF), xor for every other proType
    doc-ref:
      - H60A6 FUN_9b04b8ac @0x9b04b8ac
      - H6047 FUN_010105a4 @0x010105a4
      - H60A6 FUN_9b049e9c @0x9b049e9c
types:
  write_command:
    doc: |
      0x33 write / 0x3A write-and-read body [command][params][rest], params
      typed per command and profile form (unknown form: absent, bytes in rest).
      CAUTION: 0x0e (H6047 reboot), 0x46 (factory reset), 0xff (factory tests).
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:22-30
      - base-classes11/com/govee/base2light/kt/ble/AbsController.java:42-60
      - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:140-145
    params:
      - id: profile
        type: govee_profile
    seq:
      - id: command
        type: u1
        enum: govee_common::command
      - id: params
        type:
          switch-on: command
          cases:
            'govee_common::command::power': power_write(profile.forms.f_01)
            'govee_common::command::cmd_02': cmd_02_write(profile.forms.f_02)
            'govee_common::command::brightness': level_write
            'govee_common::command::mode': mode_write(profile)
            'govee_common::command::sync_time': >-
              sync_time_write(profile.forms.f_09)
            'govee_common::command::cmd_13': cmd_13_write(profile.forms.f_13)
            'govee_common::command::cmd_14': cmd_14_write(profile.forms.f_14)
            'govee_common::command::plug_timer_delete': plug_timer_delete
            'govee_common::command::new_timer': timer_slot_write
            'govee_common::command::light_effect_start': light_effect_start
            'govee_common::command::light_effect_stop': light_effect_stop
            'govee_common::command::cmd_30': cmd_30_write(profile.forms.f_30)
            'govee_common::command::cmd_36': cmd_36_write(profile.forms.f_36)
            'govee_common::command::gradual': gradual_write
            'govee_common::command::cmd_b0': cmd_b0_write(profile.forms.f_b0)
            'govee_common::command::secret_write': secret_write
            'govee_common::command::plug_sync_time': epoch_time
        doc: absent for an untyped command or an unknown (0) form
      - id: rest
        type: govee_common::echo
        doc: zero fill in an app frame
  power_write:
    doc: |
      33 01 power write, per form_01: a light's 1 / 0 or a plug's relay byte
      (H5083 acts only when bit 4 is set and acks the rest).
    doc-ref:
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:304-315
      - H5083 MSG_SET_TURN @0x9b01f6b4
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:265-272
    params:
      - id: form
        type: u1
        enum: govee_common::form_01
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_01::light': light_power_write
            'govee_common::form_01::relays': relay_power_write
  light_power_write:
    doc: A light's 33 01 [state].
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerSwitch.java:60-62
      - base-classes10/com/govee/base2light/ble/controller/SwitchController.java:37-39
    seq:
      - id: state
        type: u1
        enum: light_power
  relay_power_write:
    doc: An h5080 plug's 33 01 relay byte; the H5083 screen sends 0x11 / 0x10.
    doc-ref:
      - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:53-71
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:247-251
    seq:
      - id: state
        type: u1
        enum: relay_write
  cmd_02_write:
    doc: '33 02, per form_02: H5089 total switch or H5086 chart prepare.'
    params:
      - id: form
        type: u1
        enum: govee_common::form_02
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_02::total_switch': total_switch_write
            'govee_common::form_02::chart_prepare': chart_prepare
  total_switch_write:
    doc: H5089 33 02 all-outlet on / off [state], 1 / 0 (not the relay byte).
    doc-ref:
      - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4TotalSwitch.java:14-34
      - split_plugnew/com/govee/h5080/adjust/h5089/Ui4H5089.java:946,951
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/SwitchControllerConfig.java:112,133
    seq:
      - id: state
        type: u1
        enum: light_power
  chart_prepare:
    doc: H5086 33 02 chart prepare, no payload.
    doc-ref:
      - split_plugnew/com/govee/h5086/ble/chartcontroller/ChartPrepareInfoController.java:41-52
  level_write:
    doc: |
      33 04 [level] brightness write, raw 0..100 (H61A8 20..254); firmware
      powers an off light on.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/BrightnessConfig.java:683-687
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:255-317
      - H6006 MSG_SET_BRIGHTNESS @0x9b02036c
    seq:
      - id: level
        type: u1
  sync_time_write:
    doc: |
      33 09 time sync, per form_09 (the device's timer-config version picks it).
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:169-182,463-479
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:345-352
      - H6047 FUN_010031b4 @0x010031b4
    params:
      - id: form
        type: u1
        enum: govee_common::form_09
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_09::datetime_epoch': sync_datetime_epoch
            'govee_common::form_09::epoch_be': epoch_time
            'govee_common::form_09::clock': sync_clock
  sync_clock:
    doc: |
      The 7-byte clock body (timer-config version 2): local time, weekday, UTC
      offset.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTime.java:37-41
      - base-classes10/com/govee/base2light/ble/controller/SyncTimeInfo.java:17-40
      - base-classes4/com/ihoment/base2app/util/TimeZoneUtil.java:12-20
    seq:
      - id: hour
        type: u1
      - id: minute
        type: u1
      - id: second
        type: u1
      - id: week
        type: u1
        doc: 1..7, Sunday 7
      - id: marker
        type: u1
        doc: 1 in every app frame; firmware applies the zone only when it is 1
      - id: tz_hour
        type: s1
        doc: local UTC offset hours (DST included)
      - id: tz_min
        type: s1
        doc: offset minutes, same sign as tz_hour (UTC-3:30 = fd e2)
    instances:
      marker_is_valid:
        value: marker == 1
        doc-ref:
          - base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTime.java:34
  sync_datetime_epoch:
    doc: 'The 15-byte timer-config version 0 body: clock, date and unix time.'
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTime.java:31-35
      - base-classes12/com/govee/base2light/util/ByteUtils.java:217-219
      - base-classes10/com/govee/base2light/ble/BleUtil.java:260-274
    seq:
      - id: clock
        type: sync_clock
      - id: day
        type: u1
      - id: month
        type: u1
        doc: 1..12
      - id: year
        type: u2le
      - id: unix_seconds
        type: u4le
  epoch_time:
    doc: |
      [unix u32be][marker][tz_h][tz_m]: the 33 09 version-1 body and the plug
      time sync 33 B5.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTimeStamp.java:29-56
      - split_plugnew/com/govee/h5080/ble/controller/SyncTimeController.java:37
      - base-classes12/com/govee/base_h71xx/sku_base/ble/controller/SyncTimeController.java:34
    seq:
      - id: unix_seconds
        type: u4
      - id: marker
        type: u1
        doc: 1 in every app frame; firmware applies the zone only when it is 1
      - id: tz_hour
        type: s1
      - id: tz_min
        type: s1
    instances:
      marker_is_valid:
        value: marker == 1
  cmd_13_write:
    doc: 33 13, per form_13 (night mode is untyped; h5080 timer add)
    params:
      - id: form
        type: u1
        enum: govee_common::form_13
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_13::plug_timer': plug_timer_write
  plug_timer_write:
    doc: |
      The h5080 timer add 33 13 [outlet][slot][timer_record]; the H5083 screen
      sends outlet 0, and H5083 takes only outlet 0 and slot < 10, paired.
    doc-ref:
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:267-269
      - split_plugnew/com/govee/h5080/ble/controller/NewTimerControllerV2.java:100-102
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:260-262
      - H5083 FUN_9b01f780 @0x9b01f780
    seq:
      - id: outlet
        type: u1
      - id: slot
        type: u1
      - id: timer
        type: govee_common::timer_record
  plug_timer_delete:
    doc: H5083 timer delete 33 15 [outlet = 0][slot]; later slots shift down.
    doc-ref:
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:152-153
      - H5083 FUN_9b021a54 @0x9b021a54
    seq:
      - id: outlet
        type: u1
      - id: slot
        type: u1
  timer_slot_write:
    doc: |
      33 23 [slot][timer_record], one of four switch timers; firmware ignores
      slot >= 4 (H6008 compares it signed).
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerNewTimer.java:93-97
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:163-193
      - firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c:92-110
    seq:
      - id: slot
        type: u1
      - id: timer
        type: govee_common::timer_record
  cmd_14_write:
    doc: 33 14, per form_14 (the gradual switch; Wi-Fi SKUs never write 33 14)
    params:
      - id: form
        type: u1
        enum: govee_common::form_14
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_14::gradual': gradual_write
  gradual_write:
    doc: |
      Gradual (colour fade) on / off [state], sent as 33 14 or 33 A3 by the
      device's gradual version; the night-mode page also writes 33 14 01 / 00.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5602-5613
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:559-566
      - base-classes10/com/govee/base2light/ac/nightmode/NightModeViewModel.java:86-114
    seq:
      - id: state
        type: u1
        enum: light_power
  light_effect_start:
    doc: |
      33 24 [index][time], the pairing light effect started while connecting
      (index at least 1; H6006 has no handler).
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/SetLightStartController.java:26-39
      - firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c:111-116
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:411-430
    seq:
      - id: index
        type: u1
      - id: time
        type: u2
        doc: countdown, big-endian
  light_effect_stop:
    doc: |
      33 25 [value]: 01 stops the 0x24 light effect; other values are no-ops.
    doc-ref:
      - firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c:87-90
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:431-435
    seq:
      - id: value
        type: u1
  cmd_30_write:
    doc: |
      33 30, per form_30; zone_power [1][0] and light_direction 1 are the same
      bytes.
    params:
      - id: form
        type: u1
        enum: govee_common::form_30
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_30::zone_power': zone_power_write
            'govee_common::form_30::light_direction': light_direction_write
  zone_power_write:
    doc: pact_h60a0 per-zone on / off [zone][state], one frame per zone.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:121-124
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6.java:1215-1216,1373-1376
      - split_h60ax/com/govee/pact_h60a0/adjust/h12d0/VM4LightH12D0.java:618-622,905-907
    seq:
      - id: zone
        type: u1
        doc: 0, 1 (H60A6 / H1270 / H1250) or 2 (H12D0 only)
      - id: state
        type: u1
        enum: light_power
  light_direction_write:
    doc: |
      33 30 [value]: H6038 install direction, H6057 main-light switch, H71xx
      chart report.
    doc-ref:
      - split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:42-45
      - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:950-956
      - base-classes12/com/govee/base_h71xx/sku_base/ble/controller/ReportChartController.java:24-31
    seq:
      - id: value
        type: u1
        doc: H6038 direction 0..5; H6057 1 / 0; H71xx 1
  cmd_36_write:
    doc: 33 36, per form_36
    params:
      - id: form
        type: u1
        enum: govee_common::form_36
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_36::bar_pair': bar_pair_write
            'govee_common::form_36::bar_index': bar_index_write
            'govee_common::form_36::worry_free': worry_free_write
  bar_pair_write:
    doc: H6047 two-bar on / off [left][right], by logical bar position.
    doc-ref:
      - split_h6047/com/govee/h6047/detail/NewDetailVm.java:698,718
      - H6047 code @0x01006934
    seq:
      - id: left
        type: u1
        enum: light_power
      - id: right
        type: u1
        enum: light_power
  bar_index_write:
    doc: |
      [index][state], the 33 36 form of H6043 / H6038 / H6048 / H60B2 / H1771 /
      H60A0.
    doc-ref:
      - split_h6047/com/govee/h6047/detail/NewDetailVm.java:696,716
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:614-623
    seq:
      - id: index
        type: u1
      - id: state
        type: u1
        enum: light_power
  worry_free_write:
    doc: H6057 worry-free-at-night settings, 7 bytes.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/WorryFreeAtNightBean.java:34-59,90-92
      - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:846-850
      - split_hollowlamp/com/govee/h6057/origin/ble/WorryFreeAtNightController.java:76-93
    seq:
      - id: state
        type: u1
        doc: 1 = enabled, 0 = disabled
      - id: open_hour
        type: u1
      - id: open_min
        type: u1
      - id: close_hour
        type: u1
      - id: close_min
        type: u1
      - id: sensitivity
        type: u1
      - id: brightness
        type: u1
  cmd_b0_write:
    doc: 33 B0, per form_b0
    params:
      - id: form
        type: u1
        enum: govee_common::form_b0
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_b0::plug_delay': plug_delay_write
  plug_delay_write:
    doc: |
      h5080 plug delay 33 B0 [outlet = 0][type 0 | 1][hour][minute], paired
      sessions only.
    doc-ref:
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:166-167
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:240-241
      - H5083 FUN_9b01f7c4 @0x9b01f7c4
    seq:
      - id: outlet
        type: u1
      - id: delay_type
        type: u1
      - id: hour
        type: u1
      - id: minute
        type: u1
  secret_write:
    doc: |
      33 B2 account-lock secret: the 8 bytes the AA B1 reply carried (not the
      wire cipher).
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:22-35,57-78
      - base-classes12/com/govee/base2light/vm4sku2op/Controller4Secret.java:28-46
      - base-classes11/com/govee/base2light/pact/AbsPairAc4SecretV1.java:110-112,204-206
    seq:
      - id: secret
        size: 8
        doc: the AA B1 token, raw (the app stores it as base64)
  mode_write:
    doc: |
      33 05 [sub_mode][params] mode write; untyped sub-modes (05 mic, 13 music,
      14 floor-lamp colour) stay in rest.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerMode.java:45-58
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:318-340
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:318-426
    params:
      - id: profile
        type: govee_profile
    seq:
      - id: sub_mode
        type: u1
        enum: govee_common::sub_mode
      - id: params
        type:
          switch-on: sub_mode
          cases:
            'govee_common::sub_mode::color_legacy': color_02_write
            'govee_common::sub_mode::scene': scene_select
            'govee_common::sub_mode::diy': scene_select
            'govee_common::sub_mode::color_rgbic_0b': >-
              color_0b_write(profile.forms.f_0b, profile.counts.num_segments)
            'govee_common::sub_mode::color_cct_0d': color_0d_write
            'govee_common::sub_mode::color_rgbic_15': color_15_write(profile)
  color_02_write:
    doc: |
      33 05 02 legacy colour: [rgb] for RGB, [ff ff ff][01][tint rgb] for a
      Kelvin.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:212-222
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:587-604
    seq:
      - id: color
        type: govee_common::rgb
      - id: white_flag
        type: u1
        doc: 1 for the Kelvin form, 0 for RGB
      - id: tint
        type: govee_common::rgb
  scene_select:
    doc: 33 05 04 / 0a scene or DIY select [effect u16le][music_code u16le].
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/content/scene/SubModeScenes.java:59-77
      - split_hollowlamp/com/govee/tablelampv1/newAdjust/h6052/H6052ScenesViewMode.java:72-74
      - H6006 FUN_9b0208de @0x9b0208de
    seq:
      - id: effect
        type: u2le
        doc: the scene (or DIY) code
      - id: music_code
        type: u2le
        doc: white-noise music code, 0 when its switch is off
  color_0b_write:
    doc: |
      33 05 0b colour, per form_0b; rgb_mask needs a segment count, else rest.
    params:
      - id: form
        type: u1
        enum: govee_common::form_0b
      - id: num_segments
        type: u2
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_0b::rgb_mask': color_0b_rgb_mask(num_segments)
            'govee_common::form_0b::rgb_kelvin': color_0b_rgb_kelvin
        if: form != govee_common::form_0b::rgb_mask or num_segments != 0
  color_0b_rgb_mask:
    doc: '[rgb][seg_mask(num_segments, len_cap)]: a colour for the masked segments.'
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:270-281
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5542-5546
      - base-classes12/com/govee/common2light/capability/color/ColorInstructionVersion.java:16
    params:
      - id: num_segments
        type: u2
    seq:
      - id: color
        type: govee_common::rgb
      - id: mask
        type: govee_common::seg_mask(num_segments, len_cap)
    instances:
      len_cap:
        value: 13
        doc: mask byte cap; the app cuts a longer mask to 13 bytes
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:279
  color_0b_rgb_kelvin:
    doc: |
      [rgb][kelvin u16be][15 position bits]; kelvin is set for RGB too, so
      kelvin != 0 does not mean CCT.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:239-267
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5552-5556,13930-13934
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/KelvinConfig.java:119-122
    seq:
      - id: color
        type: govee_common::rgb
      - id: kelvin
        type: u2
      - id: positions
        type: b1
        repeat: expr
        repeat-expr: 15
        doc: bit i = position i selected, LSB first
      - id: reserved
        type: b1
        valid: 'false'
        doc: bit 15, never set by the app
  color_0d_write:
    doc: |
      33 05 0d [rgb][kelvin u16be][tint rgb]: RGB, or a white slot or tint plus
      a Kelvin for CCT (H6006 / H6008 ignore the tint).
    doc-ref:
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:382-408
      - H6006 MSG_SET_COLOR @0x9b020200
    seq:
      - id: color
        type: govee_common::rgb
      - id: kelvin
        type: u2
        doc: 0 for plain RGB
      - id: tint
        type: govee_common::rgb
  color_15_write:
    doc: 33 05 15 [op][data]; data is typed when the profile gives its context.
    params:
      - id: profile
        type: govee_profile
    seq:
      - id: op
        type: u1
        enum: govee_common::op15
      - id: data
        type:
          switch-on: op
          cases:
            'govee_common::op15::set_color': >-
              op15_color(profile.forms.f_15_01, profile.counts.num_segments)
            'govee_common::op15::set_brightness': >-
              op15_brightness(profile.counts.num_segments)
            'govee_common::op15::set_brightness_group': >-
              op15_levels(profile.counts.num_levels)
            'govee_common::op15::set_color_group': >-
              op15_group(profile.forms.f_15_04)
            'govee_common::op15::set_color_temp': >-
              op15_05(profile.forms.f_15_05, profile.counts.num_segments)
        if: is_typed
    instances:
      is_typed:
        value: >-
          op == govee_common::op15::set_color ?
          (profile.forms.f_15_01 != govee_common::form_15_01::unknown
          and profile.counts.num_segments != 0) :
          (op == govee_common::op15::set_brightness ?
          profile.counts.num_segments != 0 :
          (op == govee_common::op15::set_color_temp ?
          (profile.forms.f_15_05 != govee_common::form_15_05::gradient
          or profile.counts.num_segments != 0) : true))
        doc: the profile gives this op's form and segment count
  op15_color:
    doc: |
      33 05 15 01 colour in three forms (form_15_01), no in-frame discriminator:
      basic, kelvin_tint ([slot][kelvin][tint]) and kelvin ([rgb][kelvin]).
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:684-744
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:315-326,379-390,474-501,541-556
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:12960-12968,13124-13131
    params:
      - id: form
        type: u1
        enum: govee_common::form_15_01
      - id: num_segments
        type: u2
    seq:
      - id: color
        type: govee_common::rgb
        doc: the colour; kelvin_tint the slot; kelvin form the tint for CCT
      - id: kelvin
        type: u2
        if: form != govee_common::form_15_01::basic
        doc: 0 = RGB
      - id: tint
        type: govee_common::rgb
        if: form == govee_common::form_15_01::kelvin_tint
      - id: mask
        type: govee_common::seg_mask(num_segments, len_cap)
    instances:
      len_cap:
        value: >-
          form == govee_common::form_15_01::basic ? 12 :
          (form == govee_common::form_15_01::kelvin_tint ? 7 : 10)
        doc: mask byte cap, 17 minus the bytes before the mask
      is_cct:
        value: form != govee_common::form_15_01::basic and kelvin != 0
  op15_brightness:
    doc: |
      33 05 15 02 [pct][seg_mask(num_segments, len_cap)]: one brightness for
      the masked segments.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:172-181
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5591-5595,10567-10570
      - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:672-683
    params:
      - id: num_segments
        type: u2
    seq:
      - id: pct
        type: u1
        doc: 0..100, raw
      - id: mask
        type: govee_common::seg_mask(num_segments, len_cap)
    instances:
      len_cap:
        value: 14
        doc: mask byte cap; the app cuts a longer mask to 14 bytes
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:179
  op15_levels:
    doc: |
      33 05 15 03 [level x num_levels]: one brightness per group, at most 15.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:153-169
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5583-5589
      - H6047 code @0x01005510
    params:
      - id: num_levels
        type: u1
    seq:
      - id: levels
        type: u1
        valid:
          expr: num_levels <= 15
        repeat: expr
        repeat-expr: num_levels
        doc: the valid is the app's 15-level cap
  op15_group:
    doc: 33 05 15 04, per form_15_04
    params:
      - id: form
        type: u1
        enum: govee_common::form_15_04
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_15_04::rgb_group': op15_rgb_group
            'govee_common::form_15_04::kelvin_v9': op15_kelvin_v9
  op15_rgb_group:
    doc: |
      [group][rgb x 4]: up to four colours per frame, group counted from the
      device's start (1 on every hard-coded SKU); a missing colour reads black.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:393-402
      - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:176-197,250-274,711-719
      - base-classes12/com/govee/common2light/capability/color/ColorBrightnessGroupMode.java:13-14
    seq:
      - id: group
        type: u1
      - id: colors
        type: govee_common::rgb
        repeat: expr
        repeat-expr: 4
  op15_kelvin_v9:
    doc: '[kelvin u16be], colour instruction version 9.'
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:133-139
    seq:
      - id: kelvin
        type: u2
  op15_05:
    doc: |
      33 05 15 05, per form_15_05 (no in-frame discriminator; the H60A6 and
      H60A1 firmware have no op-05 handler).
    doc-ref:
      - H60A6 FUN_9b025124 @0x9b025206-0x9b025210
      - H60A6 FUN_9b025124 @0x9b0255e2-0x9b025612
      - firmware-h60a1-wifi/decompiled/H60A1_ble_dispatcher.c:732-800
    params:
      - id: form
        type: u1
        enum: govee_common::form_15_05
      - id: num_segments
        type: u2
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_15_05::kelvin': op15_kelvin
            'govee_common::form_15_05::side_brightness': op15_side_brightness
            'govee_common::form_15_05::gradient': op15_gradient(num_segments)
  op15_kelvin:
    doc: '[kelvin u2be]: the H60A0-family colour-temperature write (high byte first).'
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:44-57,695-698
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a0/ControllerBuildH60A0.java:62-66,93-98
      - base-classes10/com/govee/base2light/ble/BleUtil.java:234-239
    seq:
      - id: kelvin
        type: u2be
  op15_side_brightness:
    doc: '[side][value]: per-half relative brightness, goodsType 80 only.'
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:184-199
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:2817-2821,5591-5601,10727-10730
      - base-classes12/com/govee/common2light/capability/color/RelativeBrightnessVersion.java:14-15
    seq:
      - id: side
        type: u1
        doc: 3 = both halves, 2 = position 0 not selected, 1 = position 0 only
      - id: value
        type: u1
        doc: the brightness, raw
  op15_gradient:
    doc: |
      [rgb x 3][seg_mask(num_segments, len_cap)]: the version-0 gradient write
      (goodsTypes 326 / 338 / 354 / 364).
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:404-420
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/GradientColorControllerConfig.java:65-92,117-121
    params:
      - id: num_segments
        type: u2
    seq:
      - id: colors
        type: govee_common::rgb
        repeat: expr
        repeat-expr: 3
      - id: mask
        type: govee_common::seg_mask(num_segments, len_cap)
    instances:
      len_cap:
        value: 6
        doc: |
          mask byte cap: the 6 bytes left in the 17-byte value. The app copies
          the whole mask with no cut, so a longer one throws.
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:416-418
  write_ack:
    doc: |
      The 0x33 / 0x3A write ack [command][result][rest]; 0 = success, at byte 3
      for the 33 A6 and 33 44 acks.
      FIRMWARE-SOURCED: H6047 answers 33 07 / 14 / 20 / 21 reads (body in rest).
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:176-179,211-248
      - split_h6062/com/govee/striplight/newdetail/ControllerPos.java:56-58,73-75
      - H6047 handler @0x01005370
    seq:
      - id: command
        type: u1
        enum: govee_common::command
      - id: result
        type: u1
        doc: 0 = success
      - id: result_p1
        type: u1
        if: uses_byte_3 and not _io.eof
        doc: the result of the 33 A6 and 33 44 acks (uses_byte_3)
      - id: rest
        type: govee_common::echo
        doc: zero on most acks
    instances:
      uses_byte_3:
        value: >-
          command == govee_common::command::special_function
          or command == govee_common::command::calibration
        doc: the H6062 33 A6 and H6048 33 44 acks put the result at byte 3
      is_ok:
        value: >-
          uses_byte_3 ? (_io.size >= 3 and result_p1 == 0) : result == 0
        doc: the app accepts the write (20-byte frame)
  read_request:
    doc: |
      The 0xAA read request [command][selector][rest]; most reads send no
      selector, and an untyped selector stays in rest. The selector of 0x12,
      0x13 and 0xB0 is typed per profile form; any other form leaves it in rest.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:17-20
      - base-classes11/com/govee/base2light/kt/ble/AbsController.java:30-60
      - base-classes10/com/govee/base2kt/utils/BleUtils.java:1005-1017
    params:
      - id: profile
        type: govee_profile
    seq:
      - id: command
        type: u1
        enum: govee_common::command
      - id: selector
        type:
          switch-on: command
          cases:
            'govee_common::command::mode': mode_query
            'govee_common::command::device_info': device_info_query
            'govee_common::command::cmd_12': cmd_12_query(profile.forms.f_12)
            'govee_common::command::cmd_13': cmd_13_query(profile.forms.f_13)
            'govee_common::command::color_group_rgb': group_query
            'govee_common::command::color_group': group_query
            'govee_common::command::new_timer': timer_query
            'govee_common::command::cmd_b0': cmd_b0_query(profile.forms.f_b0)
            'govee_common::command::debug': debug_query
        doc: absent for an untyped command
      - id: rest
        type: govee_common::echo
        doc: an untyped selector and the zero fill
  mode_query:
    doc: AA 05 [selector], the mode read.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/AbsModeController.java:34-37
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerMode.java:45-58,107-109
      - split_h60ax/com/govee/pact_h60a0/ble/controller/compose/ModeController.java:26-44
    seq:
      - id: selector
        type: u1
        doc: 1 = the mode read; 0 = a bare AA 05
  device_info_query:
    doc: AA 07 [selector] device-info read.
    seq:
      - id: selector
        type: u1
        enum: govee_common::device_info_selector
  cmd_12_query:
    doc: AA 12, per form_12 (the wake-up read sends no selector)
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerWakeUp.java:133-141
    params:
      - id: form
        type: u1
        enum: govee_common::form_12
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_12::timer_count': timer_count_query
  timer_count_query:
    doc: |
      The h5080 timer-count read AA 12 [group]; the reply counts the group's
      timers and echoes the group. The group is the outlet (2 is the H5089's
      night light).
    doc-ref:
      - split_plugnew/com/govee/h5080/ble/controller/NewTimerCountController.java:23-25
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:336
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:147-149
    seq:
      - id: group
        type: u1
        doc: |
          The goodsType-43 screen (H5080, H5083, H5085) sends no group byte.
          FIRMWARE-SOURCED on H5083: the zero fill reads group 0, the one group
          with timers
        doc-ref:
          - split_plugnew/com/govee/h5080/pact/Model.java:191-197
          - H5083 FUN_9b01ee34 @0x9b01f072-0x9b01f078
  cmd_13_query:
    doc: AA 13, per form_13 (the night-mode read sends no selector)
    doc-ref:
      - split_bulblightstringv1/com/govee/bulblightv3/newdetail/NewDetailVm.java:773-781
    params:
      - id: form
        type: u1
        enum: govee_common::form_13
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_13::plug_timer': plug_timer_page_query
  plug_timer_page_query:
    doc: |
      The h5080 AA 13 page read [group][page], one page of four timer records:
      pages 0 .. ceil(count / 4) - 1 of the group's AA 12 count. The H5083
      screen sends group 0.
    doc-ref:
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:206-209
      - split_plugnew/com/govee/h5080/adjust/v2/BleOpV2.java:514-516
      - split_plugnew/com/govee/h5080/ble/controller/NewTimerControllerV2.java:91-93
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:111-143
    seq:
      - id: group
        type: u1
      - id: page
        type: u1
        doc: slots 4 * page .. 4 * page + 3; on H5083 a page of 4 or more reads all zero
  cmd_b0_query:
    doc: AA B0, per form_b0
    params:
      - id: form
        type: u1
        enum: govee_common::form_b0
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_b0::plug_delay': plug_delay_query
  plug_delay_query:
    doc: |
      The h5080 plug delay read AA B0 [outlet][type]; the H5083 screen reads
      types 0 and 1 of outlet 0.
    doc-ref:
      - split_plugnew/com/govee/h5080/ble/controller/TurnOnOffDelayController.java:90-92
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:326-327
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:158-188
    seq:
      - id: outlet
        type: u1
      - id: delay_type
        type: u1
        doc: the delay kind the reply echoes
  group_query:
    doc: AA A5 / AA A2 [group] colour-group read, group 1-based.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:111-136,197-211
      - split_rgbiclight/com/govee/rgbiclight/VM4Light.java:937-958
    seq:
      - id: group
        type: u1
  timer_query:
    doc: 'AA 23 [slot | FF]: one timer slot (0..3) or FF for all four.'
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerNewTimer.java:52-77
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:446-495
    seq:
      - id: slot
        type: u1
  debug_query:
    doc: |
      FIRMWARE-SOURCED, CAUTION: AA FF [selector] factory debug read (08 = the
      ageing flag; H60A6 runs an extra routine on flag 0); no app sender.
    doc-ref:
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:662-672
    seq:
      - id: selector
        type: u1
  read_reply:
    doc: |
      The 0xAA read reply (and the unsolicited AA 01 power push)
      [command][state].
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:176-179,217-244
      - H60A6 FUN_9b027818 @0x9b027818
      - H5083 FUN_9b01fd18 @0x9b01fd18
    params:
      - id: profile
        type: govee_profile
    seq:
      - id: command
        type: u1
        enum: govee_common::command
      - id: state
        size-eos: true
        type: govee_state(command, govee_state::source::read_reply, profile)
        doc: govee_state record; its rest keeps the firmware's request echo
  status_request:
    doc: |
      The 0xAC request [command][args][rest]; AC 03 [N][types] is the status TLV
      read.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerSingleRead.java:80-95
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:166-179,230
      - H60A6 FUN_9b04c11c @0x9b04c11c
    seq:
      - id: command
        type: u1
        enum: status_command
        doc: echoed at byte 5 of the first reply chunk
      - id: args
        type:
          switch-on: command
          cases:
            'status_command::status_read': status_read_args
      - id: rest
        type: govee_common::echo
        doc: zero fill
  status_read_args:
    doc: The status read's requested TLV types.
    seq:
      - id: num_types
        type: u1
        doc: |
          0 = the bare read ac 03 00: no ext bytes (Compose4BaseInfoSingleRead.Companion.a
          passes none, so ControllerSingleRead sends the command and the zero fill), and
          the reply carries the TLVs the firmware chooses. The h70dx screen's
          (VM4H70DX.e5), which parses each TLV that arrives (VM4H70DX.g5).
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:156-158,215-237
          - base-classes11/com/govee/base2light/kt/general_controller/ControllerSingleRead.java:80-83
          - base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:157-160
          - split_h70b1/com/govee/h70dx/vm/VM4H70DX.java:463-489,502-540
      - id: types
        type: u1
        enum: govee_common::command
        repeat: expr
        repeat-expr: num_types
  status_chunk:
    doc: |
      One 0xAC status-read (has_sub) or 0xAB Wi-Fi multi-reply chunk: the first,
      a middle or the last packet.
      PROCEDURAL: the client joins the chunks, the last cut to len_last.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:77-79,100-149,175-238,273-308
      - base-classes10/com/govee/base2light/ble/controller/AbsController4WifiSingleSendMultiBack.java:25-28
      - base-classes10/com/govee/base2light/ble/controller/Controller4ReadWifiFuncList.java:70-94
    params:
      - id: has_sub
        type: bool
        doc: true for 0xAC (a sub byte at frame byte 6), false for 0xAB
    seq:
      - id: index
        type: u1
        doc: 0 first, 1..num_packets-2 middle, 0xFF or num_packets-1 last
      - id: num_packets
        type: u2le
        if: is_first
        doc: chunk count, first and last included
        doc-ref:
          - base-classes10/com/govee/base2kt/utils/BleUtils.java:212-230
      - id: len_last
        type: u1
        if: is_first
        doc: data bytes in the last chunk (this one in a one-packet reply)
        doc-ref:
          - base-classes10/com/govee/base2kt/utils/BleUtils.java:272-274
      - id: command
        type: u1
        if: is_first
        doc: the echoed request command (status_command values for 0xAC)
      - id: sub
        type: u1
        if: has_sub and is_first
        doc: the reader's sub-command, 0 for the status read
      - id: data
        size-eos: true
        doc: the data window; the app takes len_first_data, 17 or len_last
    instances:
      is_first:
        value: index == 0
      is_end_tag:
        value: index == 0xff
        doc: tagged last chunk; a last chunk may carry num_packets-1 instead
      is_single_read_head:
        value: >-
          has_sub and index == 0
          and command == status_command::status_read.to_i
          and sub == 0
        doc: first chunk of the status read (command 3, sub 0)
      len_window:
        value: 'is_first ? (has_sub ? 12 : 13) : 17'
        doc: data bytes carried, 12 (13 for 0xAB) in a first chunk, else 17
      len_last_fits:
        value: 'len_last <= (num_packets == 1 ? len_window : 17)'
        if: is_first
        doc: the app's bound on len_last; false fails the read
      len_first_data:
        value: 'num_packets == 1 ? len_last : len_window'
        if: is_first and len_last_fits
        doc: data bytes the app takes from this first chunk
  wifi_multi_request:
    doc: |
      The 0xAB request [command][args][rest]: AB 03 Wi-Fi function list, AB 02
      pact report.
      FIRMWARE-SOURCED: AB 01 [selector] reads Matter info (selector 4 = GID).
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/Controller4ReadWifiFuncList.java:65-84
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:214
      - H6006 govee_ble_matter_req @0x9b03d53c
    seq:
      - id: command
        type: u1
        enum: wifi_multi_command
      - id: args
        type:
          switch-on: command
          cases:
            'wifi_multi_command::pact_report': pact_report
      - id: rest
        type: govee_common::echo
  pact_report:
    doc: 'The H5083 OTA pact report: the pact the device should advertise.'
    seq:
      - id: pact_type
        type: u2le
      - id: pact_code
        type: u1
  notify:
    doc: |
      The 0xEE device push [sub][data][rest]; an untyped sub stays in rest.
      FIRMWARE-SOURCED: EE 24 is the H6047 earphone push [1 in / 0 out].
    doc-ref:
      - base-classes10/com/govee/base2light/ble/comm/AbsNotify.java:33-40
      - base-classes10/com/govee/base2light/ble/comm/AbsNotifyParse.java:23-31
      - H6047 FUN_01010b80 @0x01010b80
    params:
      - id: profile
        type: govee_profile
    seq:
      - id: sub
        type: u1
        enum: govee_common::notify_sub
      - id: data
        type:
          switch-on: sub
          cases:
            'govee_common::notify_sub::wifi_connect': wifi_status
            'govee_common::notify_sub::brightness': govee_common::level
            'govee_common::notify_sub::ic_count': govee_common::section_count
            'govee_common::notify_sub::ic_and_piece': ic_and_piece
            'govee_common::notify_sub::switch_zone': >-
              notify_30(profile.forms.f_ee_30)
            'govee_common::notify_sub::device_status': >-
              notify_40(profile.forms.f_ee_40)
      - id: rest
        type: govee_common::echo
  wifi_status:
    doc: |
      EE 11 [status][reason], the Wi-Fi result: connected iff status == 0.
      FIRMWARE-SOURCED H5083 reasons: 1 bad blob, 2-3 connect, 4-6 post-connect.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_parser/DefParser.java:89-95
      - H5083 FUN_9b031804 @0x9b031804
      - H6006 send_wifi_status @0x9b03c694
    seq:
      - id: status
        type: u1
      - id: reason
        type: u1
        doc: 0 when connected
    instances:
      is_connected:
        value: status == 0
  ic_and_piece:
    doc: H6062 EE 22 IC-and-piece change (dreamcolorlightv1 reuses the sub).
    doc-ref:
      - split_h6062/com/govee/striplight/ble/IcAndPieceNotifyParse.java:24-31
      - split_rgbiclight/com/govee/dreamcolorlightv1/ble/BleProtocol.java:11
    seq:
      - id: sub_ic_1
        type: u1
      - id: skipped
        type: u1
        doc: not read by the app
      - id: sub_ic_2
        type: u1
  notify_30:
    doc: |
      EE 30 switch / zone push [detail][flags_a][flags_b][flags_c], read per
      form_ee_30.
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6.java:553-561
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a0/VM4LightH60A0.java:228-236
      - base-classes11/com/govee/base2light/kt/general_parser/DefParser.java:76-87
    params:
      - id: form
        type: u1
        enum: govee_common::form_ee_30
    seq:
      - id: detail
        type: u1
      - id: flags_a
        type: u1
      - id: flags_b
        type: u1
      - id: flags_c
        type: u1
    instances:
      is_on:
        value: >-
          form == govee_common::form_ee_30::bit0_c ? (flags_c & 1) != 0 :
          (form == govee_common::form_ee_30::bars_bc ?
          (flags_b == 1 or flags_c == 1) :
          (form == govee_common::form_ee_30::unknown ? false :
          (flags_a & 1) != 0))
        doc: power by the form's rule; false for an unknown form
      zone_a_on:
        value: >-
          form == govee_common::form_ee_30::main_bit0_a ? (flags_b & 1) != 0 :
          (form == govee_common::form_ee_30::bit0_c ? (flags_a & 1) != 0 :
          (form == govee_common::form_ee_30::bars_bc ? flags_b == 1 :
          (form == govee_common::form_ee_30::generic_flags ?
          (flags_a & 2) != 0 : false)))
        doc: zone 0 / bar 0 / sub-switch 1 by the form's rule
      zone_b_on:
        value: >-
          form == govee_common::form_ee_30::main_bit0_a ? (flags_c & 1) != 0 :
          (form == govee_common::form_ee_30::bit0_c ? (flags_b & 1) != 0 :
          (form == govee_common::form_ee_30::bars_bc ? flags_c == 1 :
          (form == govee_common::form_ee_30::generic_flags ?
          (flags_a & 4) != 0 : false)))
        doc: zone 1 / bar 1 / sub-switch 2 by the form's rule
  notify_40:
    doc: EE 40, per form_ee_40.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_parser/DefParser.java:215-230
    params:
      - id: form
        type: u1
        enum: govee_common::form_ee_40
    seq:
      - id: body
        type:
          switch-on: form
          cases:
            'govee_common::form_ee_40::slave_states': slave_states
  slave_states:
    doc: FIRMWARE-SOURCED, H6047 EE 40 [5 group-slave states]
    doc-ref:
      - H6047 FUN_01011a04 @0x01011a04
    seq:
      - id: states
        type: u1
        repeat: expr
        repeat-expr: 5
  handshake_request:
    doc: |
      The E7 V1 handshake request [op][random fill], PSK-encrypted by the
      transport (V2 AES-GCM is not supported by the verified firmwares).
    doc-ref:
      - base-classes13/com/govee/encryp/ble/Controller4Aes.java:47-88,121-136
      - H60A6 FUN_9b054438 @0x9b054438
      - H5083 FUN_9b032fb4 @0x9b032fb4
    seq:
      - id: op
        type: u1
        enum: govee_common::handshake_op
      - id: fill
        size-eos: true
        doc: random bytes
  handshake_reply:
    doc: |
      The E7 reply [op][key][rest], decrypted by the transport; V1 step 1
      carries the 16-byte session key.
    doc-ref:
      - base-classes13/com/govee/encryp/ble/Controller4Aes.java:104-119
      - H5083 FUN_9b032fb4 @0x9b032fb4
    seq:
      - id: op
        type: u1
        enum: govee_common::handshake_op
      - id: key
        type:
          switch-on: op
          cases:
            'govee_common::handshake_op::v1_request_session_key': session_key
      - id: rest
        type: govee_common::echo
        doc: frame byte 18, not read by the app
  session_key:
    doc: the device-chosen key every later frame is encrypted with
    seq:
      - id: key
        size: 16
  mic_command:
    doc: |
      The 0xA5 mic / live-colour push, a short frame [A5][command][data][sum8],
      e.g. A5 02 83 R G B sum.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/mic/controller/AbsMicController.java:23-55
      - H6006 _system_ble_event_handler @0x9b03df50
      - H60A6 FUN_9b03c6b0 @0x9b03c6b0
    seq:
      - id: command
        type: u1
        doc: 0x02 (data byte 0 = 0x83 set RGB, 0x90 colour command)
      - id: data
        size-eos: true
        doc: e.g. [83][R][G][B]
  mic_reply:
    doc: |
      FIRMWARE-SOURCED reply to A5 02 90: the 5-byte A5 02 10 01 <sum>; no other
      A5 gets one.
    doc-ref:
      - H60A6 FUN_9b03c6b0 @0x9b03c6b0
      - H6047 FUN_01001cf8 @0x01001cf8
    seq:
      - id: command
        type: u1
      - id: data
        size-eos: true
  mpm_packet:
    doc: |
      An A1 / A2 multi-package chunk: position 0 START (data-packet count), 1..n
      16-byte data in order, 0xFF END.
      PROCEDURAL: the client splits and joins the value.
    doc-ref:
      - base-classes12/com/govee/ble/multi/MultiPackageManager.java:46-124
      - H6006 code @0x9b03cd20-0x9b03cec4
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:194-234
    seq:
      - id: com_type
        type: u1
        enum: comm_byte
      - id: position
        type: u1
      - id: num_packets
        type: u1
        if: position == 0
      - id: chunk
        size-eos: true
        doc: value bytes, 16 per data packet
  mpm_result:
    doc: |
      The A1 write response [com_type][result][rest], 0 = success (H60A6 echoes
      the END instead, which reads as a failure).
    doc-ref:
      - base-classes12/com/govee/ble/multi/MultiPackageManager.java:75-80
      - H6006 code @0x9b03cdc8-0x9b03cde6
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:714-720
    seq:
      - id: com_type
        type: u1
        enum: comm_byte
      - id: result
        type: u1
      - id: rest
        type: govee_common::echo
        doc: the END frame's bytes the firmware echoes, or zeros
    instances:
      is_ok:
        value: result == 0
  a3_packet:
    doc: |
      The 0xA3 upload frame [index][start if index 0][chunk]: 0 START, 1..n
      middle, 0xFF END.
      PROCEDURAL: the client splits and joins the value (see govee_upload).
    doc-ref:
      - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:677-859
      - firmware-h6006/decompiled/H6006_a3_reassembler.c:38-176
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:739-833
    seq:
      - id: index
        type: u1
      - id: start
        type: a3_start
        if: index == 0
      - id: chunk
        size-eos: true
        doc: 17 per later frame; START inline 14 (V1 / V2), 4 (V3), 0 (V0)
  a3_start:
    doc: |
      The 0xA3 START head [marker][num_packets][comm_byte], plus
      [ctrl_num][ctrl_idx][verify] for V3.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:74-136
      - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:677-831
    seq:
      - id: marker
        type: u1
        enum: a3_marker
        doc: firmware ignores it
      - id: num_packets
        type: u1
        doc: frame count, START and END included
      - id: comm_byte
        type: u1
        enum: comm_byte
        doc: the upload's protocol code (scene version or device DIY code)
        doc-ref:
          - base-classes12/com/govee/base_h71xx/light71xx/delegate/bean/LightMultipleControl.java:9
          - base-classes12/com/govee/base_h71xx/network/bean/AtmospherePreviewMultipleControl.java:16
      - id: ctrl_num
        type: u1
        if: marker == a3_marker::v3
      - id: ctrl_idx
        type: u1
        if: marker == a3_marker::v3
      - id: verify
        size: 8
        if: marker == a3_marker::v3
  a4_packet:
    doc: |
      The MTU-sized 0xA4 upload frame [index u16le][start if 0][chunk] (0 START,
      0xFFFF END), used for H60A6 graffiti.
      PROCEDURAL: the client splits and joins the value.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:385-430
      - base-classes11/com/govee/base2light/kt/ble/Compose4DefWrite4Multi.java:225-240
      - H60A6 FUN_9b04b528 @0x9b04b528
    seq:
      - id: index
        type: u2le
      - id: start
        type: a4_start
        if: index == 0
      - id: chunk
        size-eos: true
  a4_start:
    doc: The 0xA4 START head (frame bytes 3..6).
    seq:
      - id: marker
        contents: [0x01]
      - id: num_packets
        type: u2le
        doc: frame count, START and END included
      - id: comm_byte
        type: u1
        enum: comm_byte
        doc: 0x58 on H60A6
  upload_ack:
    doc: |
      The 0xA3 / 0xA4 / 0xA6 ack and push [command][result][rest], 0 = success.
      FIRMWARE-SOURCED: A3 08 [status] pushes the IoT-config result (0 = OK).
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Multi.java:31-33,69-70,80-95
      - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4MultiImpV2.java:25-33
      - H6006 send_iot_status @0x9b03c71c
    seq:
      - id: command
        type: u1
        enum: comm_byte
        doc: the acked commByte (A4 acks 0x58)
      - id: result
        type: u1
        doc: 0 = success
      - id: rest
        type: govee_common::echo
    instances:
      is_ok:
        value: result == 0
  matter_packet:
    doc: |
      FIRMWARE-SOURCED, CAUTION: one 20-byte 0x3F Matter-factory unit
      [index][start if index 0][chunk]: 0 START, then 1..n in order, 0xFF END.
      At END the firmware joins the chunks and runs the START's op; no app
      sender, never send it. H5083, H6008, H60A1 and H60A6 also take n units
      back to back in one write (n x 20 bytes, each with its own XOR); H6006
      takes one unit per write.
      PROCEDURAL: the client splits and joins the value, and reads each 20-byte
      unit of a longer write through the root. A START write too short for its
      head (under 6 bytes) does not read.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:35
      - H5083 FUN_9b03289c @0x9b032a7c
      - H6006 _handle_write_msg @0x9b03d7a6
      - H6006 _handle_write_msg @0x9b03da24
      - H6008 code @0x9b03d576-0x9b03d584
      - H6008 code @0x9b03d5f4
      - H60A1 _handle_write_msg @0x9b04667e-0x9b04668c
      - H60A1 _handle_write_msg @0x9b04674a
      - H60A6 FUN_9b04c190 @0x9b04c280-0x9b04c28e
      - H60A6 FUN_9b04c190 @0x9b04c34c
    seq:
      - id: index
        type: u1
      - id: start
        type: matter_start
        if: index == 0
      - id: chunk
        size-eos: true
        doc: 17 bytes per unit, 14 in START
  matter_start:
    doc: FIRMWARE-SOURCED, the 0x3F START head (unit bytes 2..4).
    doc-ref:
      - H5083 FUN_9b03289c @0x9b032ac0-0x9b032ade
    seq:
      - id: reserved
        type: u1
        doc: not read; the firmware keeps it with the data
      - id: num_packets
        type: u1
        doc: unit count, START and END included
      - id: op
        type: u1
        doc: the op END runs; the ops differ per image
  matter_result:
    doc: |
      FIRMWARE-SOURCED: the 0x3F reply [op][status][rest], sent after END or
      after a unit the walk rejects (out of order, END before the count, no
      buffer, or a bad XOR on a later unit of the same write); the other units
      get none. A write whose first 20 bytes fail the XOR is dropped by the
      shared BCC check with no reply, so a bad XOR on H6006 (one unit per
      write) is never answered.
    doc-ref:
      - H5083 FUN_9b03289c @0x9b0328fe
      - H5083 FUN_9b03289c @0x9b032c0c-0x9b032c3a
      - H5083 FUN_9b0324b4 @0x9b0324b4
      - H6006 _handle_write_msg @0x9b03d7ec
      - H6006 FUN_9b03d174 @0x9b03d174
      - H60A6 FUN_9b04c190 @0x9b04c2cc
      - H60A6 FUN_9b04c190 @0x9b04c5d6-0x9b04c606
      - H60A6 FUN_9b04b254 @0x9b04b254
    seq:
      - id: op
        type: u1
        doc: the op of the last START
      - id: status
        type: u1
        doc: |
          1 for a rejected unit, an unknown op or no START; else the op's own
          result, per op and image. Ops 1, 2 and 4 give 0 on success, else 1
          or 2. Op 3 (the gid set) gives 0, even when a length over its limit
          stores nothing. Op 5 (the encrypted gid, H5083 and H6006) gives 1 on
          H5083 and 0 on H6006 for a length over its limit, 0xFF on a gid MD5
          mismatch, and otherwise the low byte of the AES ECB init or crypt
          error or of the gid store's result (0 when stored)
        doc-ref:
          - H5083 FUN_9b0324b4 @0x9b0324d0-0x9b0324da
          - H5083 FUN_9b033be8 @0x9b033be8-0x9b033bf8
          - H5083 FUN_9b0324b4 @0x9b03251c-0x9b03257a
          - H5083 FUN_9b033e2c @0x9b033ef2-0x9b033efe
          - H5083 FUN_9b033e2c @0x9b033f6a-0x9b033f76
          - H5083 FUN_9b033e2c @0x9b033f8c-0x9b033fa8
          - H6006 FUN_9b03d174 @0x9b03d1d4-0x9b03d1d8
          - H6006 FUN_9b03d174 @0x9b03d230-0x9b03d2cc
          - H6006 FUN_9b03f130 @0x9b03f274-0x9b03f27c
          - H6006 FUN_9b03f130 @0x9b03f30e-0x9b03f314
          - H6006 FUN_9b03f130 @0x9b03f32a-0x9b03f346
          - H60A6 FUN_9b04b254 @0x9b04b268-0x9b04b2ae
      - id: rest
        type: govee_common::echo
        doc: zero
  factory_test_request:
    doc: |
      FIRMWARE-SOURCED, CAUTION, test-only: the 0x66 request [op][rest]. 66 01
      logs the start and is answered, with no state change; 66 02 is echoed; any
      other op goes on to the dispatcher. No app sender; never send it.
    doc-ref:
      - H5083 FUN_9b031bfe @0x9b031bfe
      - H6006 FUN_9b03ccf2 @0x9b03ccf2
      - H6008 FUN_9b03c9ee @0x9b03c9ee
      - H60A1 FUN_9b04564e @0x9b04564e
      - H60A6 FUN_9b04aa76 @0x9b04aa76
    seq:
      - id: op
        type: u1
        enum: factory_test_op
      - id: rest
        type: govee_common::echo
  factory_test_reply:
    doc: |
      FIRMWARE-SOURCED: the 0x66 reply [op][status if 01][rest]. 66 01 is
      answered 66 01 00 with bytes 2..18 zeroed; 66 02 comes back as sent (H6047
      echoes every 66 frame).
    doc-ref:
      - H5083 FUN_9b0318b4 @0x9b0318b4
      - H6006 ble_msg_enter_test @0x9b03cc60
      - H60A6 set_response_data @0x9b04a44c
      - H6047 code @0x010064be-0x010064d8
    seq:
      - id: op
        type: u1
        enum: factory_test_op
      - id: status
        type: u1
        if: op == factory_test_op::enter_test
        doc: 0
      - id: rest
        type: govee_common::echo
        doc: zero after 66 01; the request's bytes after 66 02
enums:
  route:
    0x005:
      id: gid_info_tx
      doc: FIRMWARE-SOURCED 0x05 gid-info request, no checksum; raw
    0x105:
      id: gid_info_rx
      doc: 0x05 reply [05][0 | 1][18 x 00], no checksum; raw
    0x033:
      id: write_tx
    0x133:
      id: write_rx
    0x03a:
      id: write_read_tx
    0x13a:
      id: write_read_rx
    0x03f:
      id: multiple_write_3f_tx
      doc: FIRMWARE-SOURCED, CAUTION Matter-factory unit (n x 20 bytes per write)
    0x13f:
      id: multiple_write_3f_rx
      doc: the 3F reply [3F][op][status], sent only at END or on a unit the walk rejects
    0x066:
      id: factory_test_tx
      doc: FIRMWARE-SOURCED, CAUTION factory-test hook 66 01 / 66 02
    0x166:
      id: factory_test_rx
      doc: 66 01 00 .. / the 66 02 echo
    0x0a1:
      id: multi_write_tx
    0x1a1:
      id: multi_write_rx
    0x1a2:
      id: multi_read_rx
    0x0a3:
      id: multi_write_v1_tx
      doc: A3 scene / DIY upload
    0x1a3:
      id: multi_write_v1_rx
      doc: A3 ack and the A3 08 push
    0x0a4:
      id: multi_write_v2_tx
      doc: A4 MTU upload
    0x1a4:
      id: multi_write_v2_rx
      doc: A4 ack (H60A6 A4 58)
    0x0a5:
      id: mic_tx
    0x1a5:
      id: mic_rx
    0x0a6:
      id: mtu_multiple_write_tx
      doc: A6 MTU multi-packet write; body not traced (raw)
    0x1a6:
      id: mtu_multiple_write_rx
    0x0aa:
      id: read_tx
    0x1aa:
      id: read_rx
      doc: read reply or the unsolicited AA 01 push
    0x0ab:
      id: wifi_multi_tx
    0x1ab:
      id: wifi_multi_rx
    0x0ac:
      id: status_read_tx
    0x1ac:
      id: status_read_rx
    0x0b0:
      id: group_pro_start_tx
      doc: FIRMWARE-SOURCED B0 group_pro head (sum8); raw
    0x1b0:
      id: group_pro_start_rx
      doc: H6047 b0 01 reply (sum8); raw
    0x0b1:
      id: group_pro_b1_tx
      doc: FIRMWARE-SOURCED B1 group_pro head (sum8); raw
    0x0b2:
      id: group_pro_b2_tx
      doc: FIRMWARE-SOURCED B2 group_pro head (sum8); raw
    0x0b3:
      id: group_pro_b3_tx
      doc: FIRMWARE-SOURCED B3 group_pro head (sum8); raw
    0x1b3:
      id: group_pro_b3_rx
      doc: H6047 b3 01 03 reply (sum8); raw
    0x0b4:
      id: group_pro_b4_tx
      doc: FIRMWARE-SOURCED B4 group_pro head (sum8); raw
    0x1b4:
      id: group_pro_b4_rx
      doc: H6047 b4 01 reply (sum8); raw
    0x0b5:
      id: group_pro_b5_tx
      doc: FIRMWARE-SOURCED B5 group_pro head (sum8); raw
    0x0bf:
      id: group_pro_exit_tx
      doc: FIRMWARE-SOURCED BF group_pro exit (sum8); raw
    0x0e7:
      id: handshake_tx
    0x1e7:
      id: handshake_rx
    0x1ee:
      id: notify_rx
  checksum_kind:
    0:
      id: none
      doc: no checksum byte (the 0x05 gid-info head)
      doc-ref:
        - H60A6 FUN_9b04c190 @0x9b04c190
        - H60A6 FUN_9b04b8ac @0x9b04b8ac
    1:
      id: xor
      doc: XOR of every earlier byte, over the full length for 0xA4 / 0xA6
      doc-ref:
        - base-classes10/com/govee/base2kt/utils/BleUtils.java:1260-1267
        - base-classes10/com/govee/base2kt/utils/BleUtils.java:1030-1041
    2:
      id: sum8
      doc: 8-bit sum of every earlier byte (0xA5, 0xB0..0xBF)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/mic/controller/AbsMicController.java:23-52
        - H6047 FUN_010105a4 @0x010105a4
        - H60A6 FUN_9b053f18 @0x9b053f18
  light_power:
    0x00:
      id: light_off
    0x01:
      id: light_on
  relay_write:
    0x10:
      id: relay0_off
      doc: H5083 switches its outlet off
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:57-59
    0x11:
      id: relay0_on
      doc: H5083 switches its outlet on
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:57-59
    0x20:
      id: relay1_off
      doc: ignored by H5083 (bit 4 clear)
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:60-62
    0x22:
      id: relay1_on
      doc: ignored by H5083 (bit 4 clear)
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:60-62
    0x40:
      id: relay2_off
      doc: ignored by H5083 (bit 4 clear)
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:63-65
    0x44:
      id: relay2_on
      doc: ignored by H5083 (bit 4 clear)
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:63-65
    0xf0:
      id: all_off
      doc: H5083 switches its outlet off
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:66-69
    0xff:
      id: all_on
      doc: H5083 switches its outlet on
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:66-69
  status_command:
    0x01:
      id: cmd_01
      doc: meaning set by the sub byte (h5086 timers 0x13, chart 0x0d)
      doc-ref:
        - split_plugnew/com/govee/h5086/ble/controller/TimerReadController.java:92-98
        - split_plugnew/com/govee/h5086/ble/controller/RecentOneHourChartController.java:150-156
        - base-classes10/com/govee/base2light/ai4edit/controller/ReadAiEditInfoController.java:54-55
    0x02:
      id: device_self
      doc: cubelight device-self read
      doc-ref:
        - split_h6061/com/govee/cubelight/ble/DeviceSelfController.java:25-26
    0x03:
      id: status_read
      doc: the status TLV read (sub 0); its reply is a govee_status_stream
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerSingleRead.java:80-95
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:166-179,230
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4DetailInfo.java:104-105
    0x06:
      id: bluetooth_name
      doc: Bluetooth-name read (H6020 uses sub 1)
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ReadBluetoothName.java:49-50
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ReadBluetoothNameMsg.java:211-212
        - split_h6079/com/govee/h6020/newDetial/H6020VM4Light$readBluetoothName$2.java:94
    0x07:
      id: combo_state
      doc: combo-state read (H6020 uses sub 1)
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ReadComboState.java:211-212
        - split_h6079/com/govee/h6020/newDetial/H6020VM4Light.java:2223
    0x09:
      id: day_sync
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncVM.java:319
  wifi_multi_command:
    0x01:
      id: matter_info
      doc: FIRMWARE-SOURCED AB 01 [selector] Matter info / GID read
    0x02:
      id: pact_report
      doc: H5083 OTA pact report
    0x03:
      id: wifi_func_list
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/Controller4ReadWifiFuncList.java:80-84
  factory_test_op:
    0x01:
      id: enter_test
      doc: FIRMWARE-SOURCED, CAUTION, logs the start and is answered 66 01 00; no state change
      doc-ref:
        - H5083 FUN_9b031bfe @0x9b031bfe
        - H60A6 FUN_9b04aa76 @0x9b04aa76
    0x02:
      id: echo
      doc: FIRMWARE-SOURCED, echoed as sent
      doc-ref:
        - H5083 FUN_9b031bfe @0x9b031bfe
        - H60A6 FUN_9b04aa76 @0x9b04aa76
  a3_marker:
    0x00:
      id: v0
      doc: no inline value and an empty END
      doc-ref:
        - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:677-713
        - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:833-843
    0x01:
      id: v1
      doc: inline value in START; the form every curated device uses
      doc-ref:
        - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783
    0x02:
      id: v3
      doc: one START..END group per controller, data-bearing END
      doc-ref:
        - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:74-136
        - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:785-831
  comm_byte:
    0x01:
      id: v1_new_scenes
      -orig-id: MULTI_V1_NEW_SCENES
      doc: sceneType 1 (rgb) library scene, sent verbatim
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:71
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV1.java:10-11
        - base-classes10/com/govee/base2light/ble/ScenesOp.java:377-392,543-544
    0x02:
      id: v2_new_scenes
      -orig-id: [MULTI_V2_NEW_SCENES, MULTI_DIY]
      doc: sceneType 2 (rgbic) library scene; also the legacy A1 DIY com_type
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:43,73
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV2.java:10-11
        - base-classes10/com/govee/base2light/ble/controller/MultipleDiyController.java:16-17
    0x03:
      id: v1_new_diy_graffiti
      -orig-id: MULTI_V1_NEW_DIY_GRAFFITI
      doc: DIY graffiti, including the RGBIC editor's graffiti_v2
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:67
        - base-classes10/com/govee/base2light/ble/controller/MultiDiyGraffitiController.java:25-26
        - base-classes10/com/govee/base2light/ac/diyNew/parse/DIYGraffitiParser.java:143-144
    0x04:
      id: v1_new_diy
      -orig-id: MULTI_V1_NEW_DIY
      doc: DIY, cube scenes and the bulb DIY value (H6006 / H6008)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:66
        - base-classes10/com/govee/base2light/ble/controller/MultipleDiyControllerV1.java:16-17
        - firmware-h6006/decompiled/H6006_a3_reassembler.c:75-141
    0x07:
      id: v3_new_scenes
      -orig-id: MULTI_V3_NEW_SCENES
      doc: sceneType 3 scene minus its 2-byte lead (H60A6, H6047 ignore it)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:74
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV3.java:10-11
        - base-classes10/com/govee/base2light/ble/ScenesOp.java:411-429,549-550
    0x08:
      id: iot_config
      -orig-id: MULTI_V1_NEW_DYNAMIC_API
      doc: FIRMWARE-SOURCED IoT config [08][len u16be][URL]; no app sender
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:69
        - H5083 FUN_9b0317b4 @0x9b0317b4
        - H6006 code @0x9b03d93e-0x9b03d986
    0x09:
      id: v1_graffiti_multi_layer
      -orig-id: MULTI_V1_GRAFFITI_MULTI_LAYER
      doc: H6052 professional graffiti (scene minus its 0x13 lead)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:64
        - base-classes10/com/govee/base2light/ble/MultipleDiyInScenesController.java:16-18
        - split_hollowlamp/com/govee/tablelampv1/newAdjust/h6052/diy/H6052DiyProGraffitiParse.java:110-111
    0x0a:
      id: v4_new_scenes
      -orig-id: MULTI_V4_NEW_SCENES
      doc: sceneType 6 (compose) library scene minus its 0x0a lead
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:75
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV5.java:14-15
        - base-classes10/com/govee/base2light/ble/ScenesOp.java:431-450,552-553
    0x0b:
      id: preview_effect
      -orig-id: MULTI_PREVIEW_EFFECT
      doc: H6057 preset colours
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:58
        - split_hollowlamp/com/govee/h6057/origin/ble/MultiPreSetColorController.java:26-27
    0x0c:
      id: diy_protocol_scenes
      -orig-id: [MULTI_DIY_PROTOCOL_SCENES, MULTI_DIY_PROTOCOL_2_SCENE_MODE]
      doc: H610B, H6057 and H6078 scenes (lead byte = sub-op 3 / 9)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:47-48
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV6.java:14-15
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV7.java:5-20
    0x11:
      id: wifi
      -orig-id: MULTI_WIFI
      doc: A1 Wi-Fi provisioning blob (SSID, password, run mode, zone, API)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:76
        - base-classes10/com/govee/base2light/ble/controller/MultipleWifiController.java:130-131
        - H5083 FUN_9b0315fc @0x9b0315fc
    0x31:
      id: cube_multi_device
      doc: cubelight multi-device (also h6067, H606A)
      doc-ref:
        - split_h6061/com/govee/cubelight/ble/MultiDeviceController.java:63-64
    0x40:
      id: v1_new_color
      -orig-id: MULTI_V1_NEW_COLOR
      doc: colour-strip value; H6047 aborts on an index >= 10
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:65
        - base-classes10/com/govee/base2light/ble/controller/MultipleColorStripControllerV1.java:16-17
        - H6047 FUN_01005af0 @0x01005af0
    0x41:
      id: v1_new_music
      -orig-id: MULTI_V1_NEW_MUSIC
      doc: music mode; a second commByte is the first inline byte
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:70
        - base-classes10/com/govee/base2light/ble/controller/MultiMusicController.java:78-79
        - base-classes10/com/govee/base2light/ble/controller/MultipleController4Music.java:44-45
    0x42:
      id: music_compose
      doc: music compose (V1 framing)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/MultiControllerNoEvent.java:19
        - base-classes10/com/govee/base2light/ble/controller/MusicComposeController.java:36
    0x50:
      id: set_device_4_movie_feast
      -orig-id: MULTI_SET_DEVICE_4_MOVIE_FEAST
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:62
        - base-classes10/com/govee/base2light/ble/controller/MultiSetSubDeviceController4MovieFeast.java:22-23
    0x51:
      id: rgb_list_set
      -orig-id: MULTI_RGB_LIST_SET
      doc: H6047 handles [n][RGB x n] (no app sender traced)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:61
        - H6047 FUN_01011b04 @0x01011b04
    0x52:
      id: cube_calibration
      doc: cubelight calibration (also h6067)
      doc-ref:
        - split_h6061/com/govee/cubelight/ble/CalibrationAllController.java:41-42
    0x56:
      id: v1_new_diy_h7033
      -orig-id: MULTI_V1_NEW_DIY_h7033
      doc: also the H6092 library scene (lead byte = sub-op 11)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:68
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV8.java:5-20
        - base-classes10/com/govee/base2light/ble/ScenesOp.java:627-633
    0x57:
      id: graffiti_in_series
      -orig-id: MULTI_GRAFFITI_IN_SERIES
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:52
        - base-classes10/com/govee/base2light/ble/controller/MultiDiyGraffitiControllerV3.java:10-11
    0x58:
      id: cube_in_diy
      -orig-id: MULTI_CUBE_IN_DIY
      doc: new-DIY SKUs' DIY / graffiti code (H60A6 DIY on A3, graffiti on A4)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:41
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6DiyParse.java:253-254
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6GraffitiParse.java:198-199
    0x59:
      id: cube_in_diy_4_70dx_or_v1_operate_diy_h6092
      -orig-id: [MULTI_CUBE_IN_DIY_4_70dx, MULTI_V1_OPERATE_DIY_h6092]
      doc: effect-play value, V3 framing (marker 2)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:42,72
        - base-classes11/com/govee/base2light/effectPlay/effect/PlayEffectM.java:687
    0x5a:
      id: graffiti_coordinate
      -orig-id: MULTI_GRAFFITI_COORDINATE
      doc: H60B0 compose scene (lead 0x51)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:49
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerH60B0.java:34-35
        - base-classes10/com/govee/base2light/ble/ScenesOp.java:444-446
    0x5b:
      id: splicing_graffiti_multi_common
      -orig-id: MULTI_SPLICING_GRAFFITI_MULTI_COMMON
      doc: V3 framing (marker 2), from effect play and eco scenes
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:63
        - base-classes11/com/govee/base2light/effectPlay/effect/PlayEffectM.java:125
        - base-classes13/com/govee/eco/executeAction/parser/SceneParser4H70B1BC.java:171,214
    0x60:
      id: carousel_or_feast_sub_device
      -orig-id: [MULTI_CAROUSEL, MULTI_MUSIC_FEAST_SET_SUB_DEVICE]
      doc: H6047 handles [n <= 5][slave records] (no app sender traced)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:37,56
        - H6047 FUN_01011ef4 @0x01011ef4
    0x62:
      id: combination_effect
      -orig-id: MULTI_COMBINATION_EFFECT
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:40
        - base-classes10/com/govee/base2light/ac/diyNew/views/combination/DiyAdvancedCombParse.java:164-165
    0x63:
      id: daysync_music_mode
      doc: day-sync music mode, V3 framing
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:840
        - base-classes11/com/govee/base2light/pact/feast/BleProtocol.java:35
    0xa4:
      id: part_scenes
      doc: partial scenes; an 0xA3 comm byte, not proType 0xA4
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV9.java:15-16
        - base-classes11/com/govee/base2light/pact/newdetail/content/scene/partScenes/viewModel/PartScenesViewModel.java:123,140
    0xa5:
      id: local_color_setting
      doc: hollowlamp / H6057 local colours; not proType or command 0xA5
      doc-ref:
        - split_hollowlamp/com/govee/hollowlamp/ble/LocalColorSettingMultiControllerV1.java:42-43
