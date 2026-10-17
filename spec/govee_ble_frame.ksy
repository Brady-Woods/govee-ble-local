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
  STANDALONE (byte source: one GATT 2b11 write or 2b10 notification value;
  every direction, overload, MTU packet, mic frame and 0x05 head).
  [pro_type][body][checksum]: one value written to the vendor control
  characteristic 00010203-0405-0607-0809-0a0b0c0d2b11 (direction tx) or
  notified on ...0a0b0c0d2b10 (direction rx). Single-frame commands are 20
  bytes: the app's BleUtils.p allocates byte[20], sets bytes 0 and 1, copies
  the params from byte 2 and stamps the XOR of bytes 0..18 at byte 19
  (generate20Bytes). The 0xA4 / 0xA6 MTU packets, the 0xA5 mic frame and the
  0x05 head have other lengths; the body is sized from the frame, so every
  length reads.
  `route` (direction and proType) picks the body. A route with no case
  (0x05, 0x3F, 0x66, 0xA6 tx, 0xB0..0xBF, an unknown proType) is the sized
  body's raw bytes, which is lossless. `checksum_kind` picks the trailer:
  none (0x05), sum8 (0xA5, 0xB0..0xBF) or xor (everything else). Java
  reads an unknown proType as null, so there route and checksum_kind throw
  (NullPointerException) before the body: the raw reading of an unknown
  proType holds in Python and JS. (ksc 0.11 cannot put an imported enum on
  a value instance, which a raw byte plus an enum view would need.)
  PROCEDURAL: the client computes and checks the checksum (write, compute,
  write again), because Kaitai cannot fold over a byte range.
  Context is params only: `direction` comes from the characteristic or the
  capture's ATT direction (a device simulator reads with tx and writes with
  rx); `profile` is the device's govee_profile, whose selector value 0
  (unknown) leaves an overloaded body absent with its bytes in the
  message's rest. Frames after an E7 handshake are encrypted; the transport
  decrypts them before they are parsed.
  Group control (com.govee.ble.group) writes the same frames to each member
  over the group service, 20 ms apart; only the transport fan-out differs.
  Multi-byte integers are big-endian unless a field says otherwise
  (BleUtil.getSignedBytesFor2(value, hiFirst = true)).
doc-ref:
  - base-classes10/com/govee/base2kt/utils/BleUtils.java:1030-1041
  - base-classes10/com/govee/base2kt/utils/BleUtils.java:1260-1267
  - base-classes10/com/govee/base2kt/utils/BleUtils.java:1376-1378
  - base-classes12/com/govee/ble/group/BleGroupController.java:103-128
params:
  - id: direction
    type: u1
    enum: govee_common::direction
    doc: tx (client to device, 2b11) or rx (device to client, 2b10)
  - id: profile
    type: govee_profile
    doc: the device's decode context (one cached object per device facts)
seq:
  - id: pro_type
    type: u1
    enum: govee_common::pro_type
    doc: |
      byte 0, the frame family; an unknown value keeps its int (Python, JS)
      and is null (Java)
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
        'route::read_tx': read_request
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
    doc: |
      bytes 1..n-2 (1..n-1 when checksum_kind is none), typed by route; raw
      bytes for a route with no case
  - id: checksum
    type: u1
    if: checksum_kind != checksum_kind::none
    doc: |
      the last byte: the XOR or the 8-bit sum (checksum_kind) of every byte
      before it. PROCEDURAL: computed and checked by the client, not here.
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
    doc: |
      none for the 0x05 gid-info head (H60A6 / H60A1 send its reply with no
      XOR stamp), sum8 for the 0xA5 mic frame (AbsMicController.a) and the
      0xB0..0xBF group_pro heads (H6047 checks an additive sum), xor for
      every other proType, the MTU-length 0xA4 / 0xA6 packets included
      (their XOR covers their own length)
    doc-ref:
      - H60A6 FUN_9b04b8ac @0x9b04b8ac
      - base-classes10/com/govee/base2light/ble/mic/controller/AbsMicController.java:23-52
      - H6047 FUN_010105a4 @0x010105a4
      - H60A6 FUN_9b049e9c @0x9b049e9c
types:
  write_command:
    doc: |
      0x33 write and 0x3A write-and-read [command][params][rest]. The app frames
      every single write as [33][command][ext][zero fill][XOR]
      (AbsSingleController.g; kt AbsController.getNextCommBytes, whose ext
      constructors cut ext to 17 bytes); getProType is 51 = 0x33, or 58 =
      0x3A when the controller's write_read flag is set. `params` is typed per
      command; an overloaded byte goes through its cmd_XX wrapper, chosen by
      profile.forms (value 0 = unknown: absent, bytes in rest).
      Not typed (bytes in rest): 0x00 heartbeat (no params; H60A1 / H60A6 ack
      it with status 0), 0x11 sleep and 0x12 wake (TimerConfig.Companion.m / n
      pick one of 9-10 layouts by a per-SKU config version), 0x13 night mode,
      0x41 power-off memory [02][mode] (H60A6 stores byte 3 and ignores byte
      2), 0x44 calibration, 0xA6 special function, and the CAUTION bytes 0x0e
      (H6047 reboot), 0x46 (factory or network reset) and 0xff (factory tests),
      whose docs are in govee_common::command. A body shorter than its type
      raises; every app and firmware frame is 20 bytes.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:22-30
      - base-classes11/com/govee/base2light/kt/ble/AbsController.java:42-60
      - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:140-145
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:277-388
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:471-482
    params:
      - id: profile
        type: govee_profile
    seq:
      - id: command
        type: u1
        enum: govee_common::command
        doc: frame byte 1, the controller's getCommandType()
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
        doc: the typed params; absent for an untyped command or unknown form
      - id: rest
        type: govee_common::echo
        doc: the bytes after the params (zero fill in an app frame)
  power_write:
    doc: |
      33 01 power write, per form_01. A light writes 1 / 0 (light_power); an
      h5080 plug writes the relay byte [outlet-select nibble << 4 | on bits]
      (relay_write). The aa 01 REPLY is a relay bitmask (govee_state
      power_state), not this byte.
      Firmware: the bulbs (H6006 / H6008) do not validate the byte and apply
      it asynchronously after the ack (MSG_SET_TURN); H6047 logs and does not
      store a value above 1 but still runs the on / off sequence and acks 00;
      H5083 (one outlet, paired sessions only) switches only when bit 4 is
      set, so 0x11 / 0x10 and 0xFF / 0xF0 work while 0x22 / 0x20, 0x44 / 0x40
      and the light form 01 / 00 are ignored but acked 33 01 00.
    doc-ref:
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:304-315
      - H6047 code @0x01004f32
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
    doc: |
      a light's 33 01 [state]: kt ControllerSwitch [z ? 1 : 0]; the legacy
      SwitchController.q writes its boolean the same way
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerSwitch.java:60-62
      - base-classes10/com/govee/base2light/ble/controller/SwitchController.java:37-39
    seq:
      - id: state
        type: u1
        enum: light_power
  relay_power_write:
    doc: |
      an h5080 plug's 33 01 [relay byte] (SwitchControllerV2.q); the H5083
      screen writes 0x11 for on and 0x10 for off (H5083NewDetailViewModel.x0)
    doc-ref:
      - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:53-71
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:247-251
    seq:
      - id: state
        type: u1
        enum: relay_write
  cmd_02_write:
    doc: |
      33 02, per form_02: H5089 (goodsType 307) total switch, or H5086's chart
      prepare with no payload (ChartPrepareInfoController, on its own
      494e5445-... service)
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
    doc: |
      33 02 H5089 (goodsType 307) total (all-outlet) on / off [state]:
      Controller4TotalSwitch is a write (super(true)) with getCommandType 2
      and q() = [isTrue(f) ? 1 : 0], and it ignores the reply; sent from
      Ui4H5089.onOffChange (new Controller4TotalSwitch(!on)). The newdetail /
      IoT path writes the same [0x02, on ? 1 : 0] through
      Controller4ExtBytes.d (SwitchControllerConfig, goodsType 307). The
      light-style 1 / 0, not the 33 01 relay byte.
    doc-ref:
      - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4TotalSwitch.java:14-34
      - split_plugnew/com/govee/h5080/adjust/h5089/Ui4H5089.java:946,951
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/SwitchControllerConfig.java:112,133
    seq:
      - id: state
        type: u1
        enum: light_power
  chart_prepare:
    doc: |
      H5086 33 02 chart prepare: no payload (ChartPrepareInfoController q()
      returns null); every byte after the command is the message's rest
    doc-ref:
      - split_plugnew/com/govee/h5086/ble/chartcontroller/ChartPrepareInfoController.java:41-52
  level_write:
    doc: |
      33 04 [level] brightness write (ControllerBrightness). Raw: 0..100 on
      the curated SKUs (H61A8 Telink hardware 20..254); the percent mapping is
      the client's. Firmware: H60A6 ignores a value above 100 but acks it with
      status 0; H6047 stores only <= 100; every 33 04 powers on a light that
      is off (H6006 MSG_SET_BRIGHTNESS, H60A6, H6047).
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/BrightnessConfig.java:683-687
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:255-317
      - H6006 MSG_SET_BRIGHTNESS @0x9b02036c
      - H6047 code @0x01005454
    seq:
      - id: level
        type: u1
  sync_time_write:
    doc: |
      33 09 time sync, per form_09 (TimerConfig.Companion.f -> Config.b:
      version 1 for goodsTypes 235 / 302 / 303 / 64 / 356 / 373 / 342 / 348 /
      408, version 2 for H6160 / H6104 / H7023 / H7318 / H7004 / H7017,
      goodsType 12 / 101 and some goodsType-0 SKUs, version 0 for every other
      screen). Compose4BaseInfoSingleRead queues it right before the AC 03
      status read, which goes out only after its ack (6000 ms overtime; a
      non-zero result or a timeout fails the whole read).
      Firmware: H60A6 / H6006 / H6008 read the epoch_be layout (bytes 2..5
      unix u32be, byte 6 == 1 applies tz = 60 * tz_h + tz_m) and set the clock
      only while s_ntp_need_sync allows; H6008 sign-extends bytes 3..5, so a
      time with a byte >= 0x80 decodes wrong; H60A6 answers status 1 when the
      handler fails. H6047 reads the clock layout (week 0 -> 1, >= 8 -> 7).
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:169-182
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:463-479
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:223-236
      - H60A6 FUN_9b02ad30 @0x9b02ad30
      - H6006 FUN_9b028a60 @0x9b028a60
      - H6008 FUN_9b02c140 @0x9b02c140
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
      the 7-byte clock body [hour][minute][second][week][marker][tz_h][tz_m]:
      ControllerSyncTime.b (TimerConfig version 2), the legacy light
      SyncTimeController.q and the H60A0 compose SyncTimeController. week =
      DAY_OF_WEEK - 1 with Sunday 7 (SyncTimeInfo); the zone plus DST offset is
      split by truncating division, both parts signed (UTC-3:30 = fd e2).
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTime.java:37-41
      - base-classes10/com/govee/base2light/ble/controller/SyncTimeController.java:38
      - split_h60ax/com/govee/pact_h60a0/ble/controller/compose/SyncTimeController.java:14
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
        doc: local UTC offset minutes, signed like tz_hour
    instances:
      marker_is_valid:
        value: marker == 1
        doc: the constant 0x01 every app sync-time builder writes
        doc-ref:
          - base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTime.java:34
  sync_datetime_epoch:
    doc: |
      the 15-byte body of TimerConfig version 0 (ControllerSyncTime.a): the
      clock body, then [day][month][year u16le][epoch u32le]. A firmware that
      reads the epoch_be layout takes hour..week as the epoch.
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
      [unix u32be][marker][tz_h][tz_m]: the 33 09 version-1 body
      (ControllerSyncTimeStamp.a, k = 9) and the h5080 / h5086 / H71xx plug
      time sync 33 B5 (SyncTimeController). H5083 handles 33 B5 in paired
      sessions and always acks 0.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTimeStamp.java:29-56
      - split_plugnew/com/govee/h5080/ble/controller/SyncTimeController.java:37
      - split_plugnew/com/govee/h5086/ble/controller/SyncTimeController.java:26
      - base-classes12/com/govee/base_h71xx/sku_base/ble/controller/SyncTimeController.java:34
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:245-251
    seq:
      - id: unix_seconds
        type: u4
        doc: epoch seconds, big-endian
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
        doc: the constant 0x01 every app plug / stamp sync-time builder writes
  cmd_13_write:
    doc: 33 13, per form_13 (night mode is untyped; H5083 timer add)
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
      H5083 timer add 33 13 [outlet = 0][slot][timer_record]
      (H5083NewDetailViewModel.z0: the record's enableAndSwitch, hour, minute,
      repeat). Firmware (paired only) gates outlet == 0 and slot < 10, acks
      01 otherwise, and stores the record as the u32 it receives.
    doc-ref:
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:265-268
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
    doc: |
      H5083 timer delete 33 15 [outlet = 0][slot] (H5083NewDetailViewModel.B0);
      the firmware clears the slot and shifts the later ones down
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
      33 23 [slot][timer_record], one of the four switch timers (the
      ControllerNewTimer write constructor). Firmware: H6006 drops slot >= 4
      with no reply; H6008 compares the slot signed, so 0x80..0xFF pass (an
      out-of-range store acked 0) and 4..0x7F get no reply; H60A6 ignores
      slot >= 4 with status 0.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerNewTimer.java:93-97
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:163-193
      - firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c:92-110
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:437-444
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
      Gradual (fade between colours) on / off [state]. Two command bytes, by
      the device's GradualVersion (Config M table: 0 -> 0xA3, 1 -> 0x14):
      0xA3 = Bytes.w, GradualChangeBleWifiController and the compose
      GradualController (and per-split copies); 0x14 = Bytes.v, kt
      ControllerGradual and the legacy GradualController. Each is [cmd, on ? 1
      : 0], wrapped by Controller4ExtBytes.d. The night-mode page writes
      33 14 01 / 00 to enter / leave night mode. The h5042 / h5043
      Controller4DeleteSubDevData writes 33 14 [index], but nothing calls it
      (the hubs delete with 33 11). The AA A3 reply is govee_state
      gradual_state. Firmware: H6047 stores a value < 2 and acks [state >= 2].
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5602-5613
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:559-566
      - base-classes10/com/govee/base2light/ble/controller/GradualChangeBleWifiController.java:11-35
      - split_h60ax/com/govee/pact_h60a0/ble/controller/compose/GradualController.java:23-44
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerGradual.java:59-72
      - split_rgbiclight/com/govee/dreamcolorlightv1/ble/GradualController.java:13-33
      - split_rgbiclight/com/govee/rgbiclight/mode/color/ColorControllerConfig.java:507-510
      - base-classes10/com/govee/base2light/ac/nightmode/NightModeViewModel.java:86-114
      - base-classes2/com/govee/h5043/ble/controller/Controller4DeleteSubDevData.java:22-24,39-41
      - H6047 code @0x01004cdc
    seq:
      - id: state
        type: u1
        enum: light_power
  light_effect_start:
    doc: |
      33 24 [max(index, 1)][time u16be] (SetLightStartController, from the
      connect / bind steps): the pairing light effect on H6008 and H60A1 /
      H60A6 (govee_device_start_tmp_mode(0x24) when time != 0 or index == 0);
      H6006 has no handler and sends no reply
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
      33 25 [value]: 01 stops the 0x24 light effect (H6008
      msg_set_dev_lamp_stop; H60A1 / H60A6 MSG_STOP_PAIR_MODE); any other byte
      is a no-op acked 0
    doc-ref:
      - firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c:87-90
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:431-435
    seq:
      - id: value
        type: u1
  cmd_30_write:
    doc: |
      33 30, per form_30. The frame of zone_power_write [1, 0] is
      byte-identical to light_direction_write value 1 (33 30 01 00 .. 02).
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
    doc: |
      pact_h60a0 per-zone on / off [zone][state], built as
      Controller4ExtBytes.Companion.f(48, [zone, state]). H60A6 VM4LightH60A6.y5
      writes [!isFirst ? 1 : 0, on] (zone 1 from DelegateHeaderH60A6:442, zone
      0 from :470); H1270 and H1250 are identical; H12D0 takes the caller's
      [2 | 1 | 0, on], so it has three zones. The snapshot path writes one
      frame per zone. The states come back in TLV 0x30 and the EE 30 push.
      H60A1 has no 0x30 case (33 ack status 1).
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:121-124
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6.java:1373-1376
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/DelegateHeaderH60A6.java:442,470
      - split_h60ax/com/govee/pact_h60a0/adjust/h12d0/VM4LightH12D0.java:618-622
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6.java:1215-1216
      - split_h60ax/com/govee/pact_h60a0/adjust/h1270/VM4LightH1270.java:672-675
      - split_h60ax/com/govee/pact_h60a0/adjust/h1250/VM4LightH1250.java:911-914
      - split_h60ax/com/govee/pact_h60a0/adjust/h12d0/DelegateHeaderH12D0.java:579-580,608,636
      - split_h60ax/com/govee/pact_h60a0/adjust/h12d0/VM4LightH12D0.java:905-907
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:223-236
    seq:
      - id: zone
        type: u1
        doc: 0, 1 (H60A6 / H1270 / H1250) or 2 (H12D0 only)
      - id: state
        type: u1
        enum: light_power
  light_direction_write:
    doc: |
      33 30 [value]. H6038: the installation / calibration direction,
      CalibrationController.Companion.c(b) = Controller4ExtBytes.d([48, b]),
      b = the index of the picked direction image (6 choices); the compose
      DirectionController.b builds the same byte but has no caller. H6057: the
      main-light switch ControllerSingle.c(48, [on ? 1 : 0]). H71xx:
      ReportChartController writes [1]. The H6038 pick is sent from VM4H6038.N5
      (H6038CalibrationAc enables the send button only after a pick) or the
      add-device flow; the compose DirectionController's g() adds 33 44 02 00.
    doc-ref:
      - split_h6047/com/govee/h6047/newdetail/h6038/H6038CalibrationAc.java:185-208,280-298,316
      - split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:809-815
      - base-classes10/com/govee/base2light/ble/controller/compose/DefWriteComposeController4Single.java:137-149
      - split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:7
      - split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:42-45
      - base-classes10/com/govee/base2light/ble/controller/compose/DirectionController.java:34-36,60-61
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
    doc: |
      H6047 two-bar on / off [left][right] (Controller4ExtBytes [0x36, left,
      right], NewDetailVm.D5), the pair the AA 36 reply echoes. left / right
      are logical positions (swappable at setup, LightPosAc). The reply's third
      byte (the movie / feast switch) is read-only, not part of the write.
      Firmware sets each bar 0 / 1 and runs the off sequence when both are 0.
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
      [index][state], the form of H6043 / H6038 / H6048 / H60B2 / H1771 /
      H60A0 (Controller4ExtBytes [54, index, state]). H60A1 / H60A6 have no
      0x36 case (33 ack status 1).
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
    doc: |
      H6057 worry-free-at-night, 7 bytes: WorryFreeAtNightBean.o() = [state,
      open hour, open minute, close hour, close minute, sensitivity,
      brightness], sent as ControllerSingle.Companion.c(54, bytes) (legacy
      WorryFreeAtNightController the same)
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/WorryFreeAtNightBean.java:34-59,90-92
      - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:846-850
      - split_hollowlamp/com/govee/h6057/origin/ble/WorryFreeAtNightController.java:76-93
    seq:
      - id: state
        type: u1
        doc: 1 = enabled, 0 = disabled (bean field a)
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
        doc: the bean's 'progress' (field f)
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
      h5080 plug delay write 33 B0 [outlet = 0][type 0 | 1][hour][minute]
      (H5083NewDetailViewModel.F0); the firmware gate is outlet == 0 (ack 01
      otherwise), paired sessions only
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
      33 B2 account-lock secret, the 8 bytes with no selector: every builder
      writes Encode.decryByBase64(secretCode) as the whole ext
      (SecretKeyController.q, command SINGLE_CHECK_SECRET_KEY = -78; h5086 and
      h61d3 SecretController.d; vm4sku2op Controller4Secret.a). The secretCode
      is the base64 of the 8 bytes the AA B1 reply carried after its 0x01
      selector; the write path does not check the length. The two-argument
      constructor keeps -78 for a commandType of -1, which every caller
      passes. The bbq SecretKeyController.i [len][secret] is never sent (its
      callers are abstract classes with no subclass). Not the wire cipher.
      Firmware: a match moves H5083 to pair state 2 from any state; H60A6,
      H6047 and H5083 ack a mismatch with status 1.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:22-35,57-78
      - split_plugnew/com/govee/h5086/ble/controller/SecretController.java:41-44,66-88
      - split_h61d3/com/govee/h61d3/ble/controller/SecretController.java:67-89
      - base-classes12/com/govee/base2light/vm4sku2op/Controller4Secret.java:28-46
      - base-classes11/com/govee/base2light/pact/AbsPairAc4SecretV1.java:110-112,204-206
      - base-classes12/com/govee/base2newth/bbq/ble/controller/SecretKeyController.java:60-75
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:306-329
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:676-701
    seq:
      - id: secret
        size: 8
        doc: the token read from AA B1 (base64 off the wire)
  mode_write:
    doc: |
      33 05 [sub_mode][params]: ControllerMode (cmd 0x05). Firmware handling of
      the sub-mode: H60A6 takes 04 scene, 0a DIY, 0d (stored as mode 0x15), 13
      (stored as mode 0xA0) and 15 with ops 01..04 (op 05 hits 'Unknow msg');
      H60A1 takes 04 / 0a / 13 / 15 only (no 0x0d); the bulbs 04 / 05 / 0a /
      0d (H6006 acks any other sub-mode with status 1, H6008 with 0); H6047 04
      / 0a / 13 / 15 and acks any other with 00. Untyped here (bytes in rest):
      05 mic (H6006 [01] enter / [00][R G B] colour), 13 music, 14 the floor
      lamp colour.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerMode.java:45-58
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:318-340
      - firmware-h60a1-wifi/decompiled/H60A1_ble_dispatcher.c:648-837
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:318-426
      - H6047 code @0x010057f6
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
      33 05 02 colour (legacy colour version 4): Bytes.a [r g b] then zeros
      for RGB, Bytes.y [ff ff ff][01][tint r g b] for a Kelvin (the tint is
      Constant.getTemColorByKelvin's table colour, zeros on a table miss)
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
    doc: |
      33 05 04 / 33 05 0a [effect u16le][music_code u16le]:
      SubModeScenes.getWriteBytes with e == false, [f][a][c ? b : 0], f = 0x04
      by default. H6052 activates a sceneType-5 scene with f = 0x0a
      (H6052ScenesViewMode.k3 sets f = 10), after any upload. With e == true
      the app sends [code u16le][music u32le][voice type]; its last four
      bytes are then the message's rest. The bulb firmware stores bytes 3..6
      as one u32 LE id shared by scene and DIY (effect | music_code << 16)
      and never fails; H6047 stores 2 or 4 bytes for 0x0a by its last A3 type.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/content/scene/SubModeScenes.java:59-77
      - split_hollowlamp/com/govee/tablelampv1/newAdjust/h6052/H6052ScenesViewMode.java:72-74
      - H6006 FUN_9b0208de @0x9b0208de
      - H6008 FUN_9b025588 @0x9b025588
      - H6047 code @0x01005510
    seq:
      - id: effect
        type: u2le
        doc: the scene (or DIY) code
      - id: music_code
        type: u2le
        doc: the scene's white-noise music code when its switch is on, else 0
  color_0b_write:
    doc: |
      33 05 0b colour, per form_0b (the H6057 game form is untyped). rgb_mask
      also needs a segment count (its mask width); without one the bytes are
      the message's rest.
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
    doc: |
      [rgb][seg_mask(num_segments, 13)]: Bytes.d, ColorInstructionVersion
      COLOR_MODE_0X0B(3) (Config.cu, Config.J[3]); ext [0x0b (BleProtocol
      music_code_yuedong = 11), R, G, B, mask], the mask copied at ext[4] with
      coerceAtMost(len, 13), so positions 104 and up are dropped. With <= 16
      pieces this is the old 2-byte u2le form. H61A8 never sends 0x0b (F1: its
      screen writes 0x15). ColorModeControllerConfig.j can also send this ext
      as an 0xA3 type-0x6e multi-packet followed by 33 05 15.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:270-281
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5542-5546
      - base-classes12/com/govee/common2light/capability/color/ColorInstructionVersion.java:16
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:689-712
    params:
      - id: num_segments
        type: u2
    seq:
      - id: color
        type: govee_common::rgb
      - id: mask
        type: govee_common::seg_mask(num_segments, 13)
  color_0b_rgb_kelvin:
    doc: |
      [rgb][kelvin u16be][positions 0..14 as bits]: Bytes.c,
      COLOR_MODE_0X0B_KEVIN(6) (Config.eu, Config.J[6]), an 8-byte ext with no
      mask helper: m0 bit k = position k, m1 bit k = position 8 + k for 8..14
      only, so bit 15 is never written; a posSet shorter than 15 throws in the
      app. kelvin = KelvinConfig.c(colour) = Constant.getColorTemKelvin: the
      colour's Kelvin when it is a tint, else the low end of the device range
      (not 0), big-endian, so kelvin != 0 does not mean CCT here. The same
      8-byte ext can instead travel as an 0xA3 type-0x6e payload
      (ColorModeControllerConfig.j).
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5552-5556
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:13930-13934
      - base-classes9/com/govee/base2home/Constant.java:591-594
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:239-267
      - base-classes12/com/govee/common2light/capability/color/ColorInstructionVersion.java:19
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
        doc: positions[i] = position i selected (LSB first)
      - id: reserved
        type: b1
        valid: 'false'
        doc: bit 15, never set by the app
  color_0d_write:
    doc: |
      33 05 0d [rgb][kelvin u16be][tint rgb]. RGB form [R G B, 0 ...]
      (Bytes.f); two CCT forms carry tint = Constant.getTemColorByKelvin(kelvin)
      (an exact 100 K table hit, else zeros): white slot [ff ff ff][kelvin]
      [tint] on H6188 (Config.GM -> Bytes.A) and [tint][kelvin][tint] on H6008
      / H6006 / H6052 (Config.bu -> Bytes.p). The app sends whole 100 K steps
      clamped to the device range (KelvinConfig.calibrationKelvin).
      Firmware (H6006 / H6008): sets mode 0x0d first, then applies RGB = bytes
      3..5 and the Kelvin; the tint is never read; it powers an off bulb on.
    doc-ref:
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:382-408
      - H6006 MSG_SET_COLOR @0x9b020200
    seq:
      - id: color
        type: govee_common::rgb
      - id: kelvin
        type: u2
        doc: big-endian; 0 for plain RGB
      - id: tint
        type: govee_common::rgb
  color_15_write:
    doc: |
      33 05 15 [op][data] (SubModeColorV1 OP_TYPE / Bytes.*). The op's body is
      typed when the profile gives what it needs: op 01 needs form_15_01 and a
      segment count, op 02 a segment count, op 04 form_15_04, op 05
      form_15_05 (and a segment count for its gradient form). Op 00
      (set_mode, SubModeColorV1 opType 3) carries nothing. Otherwise the bytes
      are the message's rest.
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
        doc: |
          the profile gives this op's context (a mask needs the segment count:
          seg_mask rejects every set bit at num_segments 0)
  op15_color:
    doc: |
      33 05 15 01 colour, one type for the three forms, which share op 0x01
      with no in-frame discriminator (form_15_01, by device family and colour
      instruction version):
      - basic: [rgb][mask cap 12] (SubModeColorV1 opType 1, mask at ext[5],
        no coerceAtMost: the h60ax compose AbsController passes the ext uncut
        to BleUtils.p, so a 13th byte lands on the BCC, more throws);
      - kelvin_tint: [slot rgb][kelvin][tint rgb][mask cap 7]. Bytes.r (slot
        ff ff ff for kind 1; 00 00 00 for kind 0 or a full kind-3 write; a
        kind-3 partial write is RGB) and SubModeColorV1 opType 11. RGB is kelvin
        0 and tint 0, byte-identical to opType 12 / Bytes.h [rgb][00 x5][mask];
        the h6047 / h6062 writers put the user colour in the slot (h6062
        SubModeColorV2.j / SubModeColorV3.OpColor too). Bytes.h / Bytes.r
        coerceAtMost(len, 7) and h6047 min(len, 7); SubModeColorV1 opType 12 has
        no coerce (an 8th byte lands on the BCC, more throws); the h6062 writer
        throws past 56 positions (17-byte array).
      - kelvin: colour instruction version 5 (COLOR_MODE_0X15_NO_TEMP_RGB,
        Config.Zt): Bytes.l [rgb][00 00][mask] for RGB, Bytes.u [tint
        rgb][kelvin][mask]; cap 10, copied uncoerced (11+ bytes throw).
      H61A8 (goodsType 19, F1) writes the kelvin_tint form: Bytes.h for RGB,
      Bytes.r slot ff ff ff for CCT and for a tint pick in its Kelvin range
      (2000..9000, or 2700..6500 from hardware 3.08.01), e.g. 33 05 15 01 ff ff
      ff 0a 8c ff ae 54 ff 7f .. de for 2700 K on 15 segments.
      Firmware H6047 reads RGB at bytes 2..4, the Kelvin at 5..6 and the mask
      u16le at 10..11 and never the tint; H60A6's mask is u16le at frame bytes
      12..13 (bit 12 = the panel).
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:684-744
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:315-326
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:474-501
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:379-390
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:541-556
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:12960-12968
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:13124-13131
      - split_h6047/com/govee/h6047/ble/SubModeColor.java:184-200
      - split_h6062/com/govee/striplight/ble/SubModeColor.java:376-405
      - split_h6062/com/govee/striplight/ble/SubModeColorV2.java:570-587
      - split_h6062/com/govee/striplight/ble/SubModeColorV3.java:63-85,136-141
      - split_h60ax/com/govee/pact_h60a0/ble/controller/compose/AbsController.java:24-26
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/KelvinConfig.java:402-411
      - H6047 code @0x01005510
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
        doc: big-endian (BleUtils.D(k, true)); 0 = RGB
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
        doc: 17 - the ext bytes before the mask (5 / 10 / 7)
      is_cct:
        value: form != govee_common::form_15_01::basic and kelvin != 0
        doc: a Kelvin write (the RGB forms carry kelvin 0)
  op15_brightness:
    doc: |
      33 05 15 02 [pct][seg_mask(num_segments, 14)]: Bytes.G (Config.JR for
      RelativeBrightnessVersion 0, and ColorModeControllerConfig.z, one frame
      per distinct brightness), mask at ext[3] with coerceAtMost(len, 14);
      pct is written raw. num_segments is colorPosSetMaxLen (the piece count
      rounded up to a multiple of 8; seg_mask's doc). The legacy h6047
      SubModeColor (min(len, 14)) and SubModeColorV1 opType 0 (uncapped) write
      the same body. Firmware H6047 stores pct for set bits k < 10.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:172-181
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:1069-1097,1184-1195
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5591-5595,10567-10570
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/ColorPieceConfig.java:262-265,454-469
      - split_h6047/com/govee/h6047/ble/SubModeColor.java:173-182
      - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:672-683
    params:
      - id: num_segments
        type: u2
    seq:
      - id: pct
        type: u1
        doc: 0..100, raw
      - id: mask
        type: govee_common::seg_mask(num_segments, 14)
  op15_levels:
    doc: |
      33 05 15 03 [level x num_levels]: Bytes.F (Config.K[0] = IR, the strip
      and snapshot brightness sets), coerceAtMost(15, len) levels, then zeros.
      H61A8 sends it (F1). Firmware H6047 reads only the first byte, as the
      brightness of every segment.
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
        doc: one brightness per level; the valid is the app's 15-level cap
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
      [group][rgb x 4]: Bytes.m and SubModeColorV1 opType 7 write [0x15, 0x04,
      group, RGB x n] with n = min(4, colours) (BleUtils.w0) and nothing after;
      the h6062 SubModeColor writes the same. A whole-device write is ceil(N /
      4) frames; the last carries N % 4 colours (4 when N % 4 == 0), which is
      not in the stream: an absent colour reads as black (zero fill). group =
      start + chunk index; start is the capability's colorBrightnessGroupMode
      (GROUP_INDEX_FROM_0 / _1), 1 for every hard-coded goodsType, H60A0 /
      H60A1 and h6062 (13 colours on H60A0 = groups 1..4 with 4, 4, 4, 1).
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:393-402
      - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:176-197,250-274,711-719
      - base-classes10/com/govee/base2kt/utils/BleUtils.java:1271-1283
      - split_h6062/com/govee/striplight/ble/SubModeColor.java:73-100,124-150,340-359
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:3118-3314
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:871-897
      - base-classes10/com/govee/base2light/capability/color/LightColorCapabilityCenter.java:866-873
      - base-classes12/com/govee/common2light/capability/color/ColorBrightnessGroupMode.java:13-14
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:412-423
    seq:
      - id: group
        type: u1
      - id: colors
        type: govee_common::rgb
        repeat: expr
        repeat-expr: 4
  op15_kelvin_v9:
    doc: |
      [kelvin u16be]: Bytes.D, colour instruction version 9
      (COLOR_MODE_0X15_0X04)
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:133-139
    seq:
      - id: kelvin
        type: u2
  op15_05:
    doc: |
      33 05 15 05, per form_15_05: three app writers share op byte 0x05 with
      no in-frame discriminator. H60A6 firmware has no op-05 handler.
    doc-ref:
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:318-340
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
    doc: |
      [kelvin u16be]: SubModeColorV1 opType 2 (OP_TYPE_SET_COLOR_TEMP; not
      OP_TYPE 5, OP_TYPE_MAKE_ALL_COLOR_MODE_COMM, which has no write) and the
      H60A0 screen's ControllerBuildH60A0.b (a tint on the last position, K =
      ColorUtils.h) / .d (the white-light slider), all
      getSignedBytesFor2(kelvin, true) = high byte first
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:44-57
      - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:695-698
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a0/ControllerBuildH60A0.java:62-66,93-98
      - base-classes10/com/govee/base2light/ble/BleUtil.java:234-239
    seq:
      - id: kelvin
        type: u2
  op15_side_brightness:
    doc: |
      [side][value]: Bytes.H, relative-brightness version 1
      (RELATIVE_BRIGHTNESS_H610A; Config.L[1] = KR, Config.z[80] = 1, so
      goodsType 80 only), sent by ColorModeControllerConfig.t
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:184-199
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5591-5601,10727-10730
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:2817-2821
      - base-classes12/com/govee/common2light/capability/color/RelativeBrightnessVersion.java:14-15
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:1069-1097
    seq:
      - id: side
        type: u1
        doc: |
          3 = both halves (or a posSet shorter than 2), 2 = position 0 not
          selected, 1 = position 0 only
      - id: value
        type: u1
        doc: the brightness, raw
  op15_gradient:
    doc: |
      [rgb x 3][seg_mask(num_segments, 6)]: Bytes.n, GradientColor version 0
      (GradientColorControllerConfig.e[0], keyed for goodsTypes 326, 338, 354
      and 364; whether their screens reach it is not traced). The mask lands
      at ext byte 11 of 17 uncoerced, so a mask longer than 6 bytes throws in
      the app (is_truncated).
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
        type: govee_common::seg_mask(num_segments, 6)
  write_ack:
    doc: |
      The device's 0x33 / 0x3A write ack [command][result][rest]. The app
      matches [proType, command] (isSameController) and, for a 20-byte frame,
      fails the write when byte p() of validBytes (frame bytes 2..18) is not
      0; p() is 0, so the result is frame byte 2 (legacy AbsSingleController.t
      value[2] == 0). With o() true the 16 bytes after it go to h() (only the
      H7031 SceneController sets o()). Two writes override p() = 1, so their
      result is frame byte 3: H6062 ControllerPos (33 A6 01 v) and H6048
      CalibrationController4H6048 (33 44).
      Firmware status: H60A6 answers 1 only for an unknown opcode, a failed
      33 09 or a mismatched 33 B2 (an ignored write, e.g. 33 04 > 100 or 33 23
      slot >= 4, still gets 0); H6006 1 only for an unsupported 33 05
      sub-mode, H6008 always 0; H6047 1 for 33 a3 / 33 0e with v >= 2 and a 33
      b2 mismatch; H5083 1 when a 13 / 15 / B0 gate fails or B2 mismatches.
      H6006 and H5083 echo a 33 FF request verbatim (its byte 2 is then the
      result). FIRMWARE-SOURCED: H6047 also answers the 0x33-framed reads 33
      07 ss / 14 / 20 / 21, which the app never sends, with read-reply bodies;
      they stay in rest.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:176-179
      - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:211-248
      - base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:37-48,66-68
      - split_bulblightstringv1/com/govee/bulblightstringv2/newAdjust/h7031/controller/SceneController.java:48-50
      - split_h6062/com/govee/striplight/newdetail/ControllerPos.java:56-58,73-75
      - split_h6047/com/govee/h6047/newdetail/control/CalibrationController4H6048.java:23-24,44-46
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:703-709
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:361-370
      - firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c:268-271
      - H6047 FUN_01004880 @0x01004880
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:278-304
      - H6047 handler @0x01005370
    seq:
      - id: command
        type: u1
        enum: govee_common::command
        doc: frame byte 1, the echoed command
      - id: result
        type: u1
        doc: frame byte 2, 0 = success (p() = 0)
      - id: result_p1
        type: u1
        if: uses_byte_3 and not _io.eof
        doc: frame byte 3, the result of the two p() = 1 writes
      - id: rest
        type: govee_common::echo
        doc: frame bytes after the result (zero on most acks)
    instances:
      uses_byte_3:
        value: >-
          command == govee_common::command::special_function
          or command == govee_common::command::calibration
        doc: a p() = 1 write (0xA6 ControllerPos, 0x44 H6048 calibration)
      is_ok:
        value: >-
          uses_byte_3 ? (_io.size >= 3 and result_p1 == 0) : result == 0
        doc: the app accepts the write (for a 20-byte frame)
  read_request:
    doc: |
      0xAA read request [command][selector][rest]: BleUtils.p(0xAA,
      getCommandType(), ext), ext from byte 2 only when non-empty. Legacy
      controllers pass AbsSingleController.p() (default null), kt controllers
      their ctor ext (cut to 17 bytes). Typed selectors: 05 mode_query, 07
      device_info_query, a2 / a5 group_query, 23 timer_query, ff debug_query.
      Every other selector stays byte-exact in rest; most reads send none (01,
      04, 06, 14, 20, 21, 36, 40, 41, a3, b1, b3, ef), and a 00 selector byte
      and the zero fill are the same bytes on the wire (h5080 AA B0 00 00 = a
      bare AA B0). Others the app sends: AA 41 02 (legacy OnOffMemoryController
      p() = {2}), AA B4 [h == 1 ? 01 : 00, FF], AA B0 [outlet, type], AA 13
      [group, page] (H5083), and the module reads BleUtils.o(0xAA, module,
      command, ext) such as AA 60 01 01. Firmware overwrites byte 2 of most
      requests without reading it (AA 01 / 04 on the bulbs and H5083); which
      images answer which info read is in govee_common::device_info_selector and
      govee_state. The selector senders: empty for SwitchController / kt
      ControllerSwitch, BrightnessController / ControllerBrightness,
      SoftVersionController / ControllerSoftVersion, the H6047
      ComposeLightHeartController and OnOffController4H6048 (36),
      ControllerIcNum / ControllerOnlyReadIcSegmentNum / IcNumController (40),
      ControllerOnOffMemory and the H6062 ControllerIcSegmentNum (41: the device
      decides which reply a bare aa 41 gets), ControllerGradual4BleWifi /
      GradualChangeBleWifiController (a3), SecretKeyController (b1) and
      SpecController (b3). The ext is copied unchecked: 18 bytes lose the last
      one to the XOR, 19 or more throw.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/SwitchController.java:9-11
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerSwitch.java:38-47,69-71
      - base-classes10/com/govee/base2light/ble/controller/BrightnessController.java:10-12
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerBrightness.java:40-48,69-71
      - base-classes10/com/govee/base2light/ble/controller/ModeControllerNoEvent.java:37-38
      - base-classes10/com/govee/base2light/ble/controller/SoftVersionController.java:9-11
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerSoftVersion.java:16-18,27-29
      - split_h6047/com/govee/h6047/ble/controller/ComposeLightHeartController.java:8-11
      - split_h6047/com/govee/h6047/newdetail/control/OnOffController4H6048.java:22-23,40
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerIcNum.java:79,165-175
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerOnlyReadIcSegmentNum.java:52-58
      - base-classes10/com/govee/base2light/ble/controller/IcNumController.java:8
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerOnOffMemory.java:49-51,119-121
      - split_h6062/com/govee/striplight/newdetail/ControllerIcSegmentNum.java:40-41,61-67
      - base-classes10/com/govee/base2light/ble/controller/OnOffMemoryController.java:18-22,63-66
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerGradual4BleWifi.java:47-48,104-114
      - base-classes10/com/govee/base2light/ble/controller/GradualChangeBleWifiController.java:7-8,22-23
      - base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:13-20
      - split_plugnew/com/govee/h5080/ble/controller/SpecController.java:6-13
      - base-classes10/com/govee/base2light/ble/controller/AbsSingleFeastController.java:22-23
      - base-classes10/com/govee/base2light/ble/controller/MovieOpenControllerV2.java:22-29
      - base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:17-20
      - base-classes10/com/govee/base2light/ble/controller/AbsController.java:76-77
      - base-classes11/com/govee/base2light/kt/ble/AbsController.java:30-60
      - base-classes10/com/govee/base2kt/utils/BleUtils.java:1005-1017
      - base-classes10/com/govee/base2light/ble/controller/AbsSingleModuleController.java:15-18
      - split_plugnew/com/govee/h5080/ble/controller/NewTimerController.java:67-69
      - split_plugnew/com/govee/h5080/ble/controller/TurnOnOffDelayController.java:90-92
      - split_plugnew/com/govee/h5080/adjust/v1/BleOpV1.java:151
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:208,326-327
    seq:
      - id: command
        type: u1
        enum: govee_common::command
        doc: frame byte 1, the command the reply carries
      - id: selector
        type:
          switch-on: command
          cases:
            'govee_common::command::mode': mode_query
            'govee_common::command::device_info': device_info_query
            'govee_common::command::color_group_rgb': group_query
            'govee_common::command::color_group': group_query
            'govee_common::command::new_timer': timer_query
            'govee_common::command::debug': debug_query
        doc: the typed selector; absent for every other command
      - id: rest
        type: govee_common::echo
        doc: an untyped selector (aa 1b 01, aa 49 xx ...) and the zero fill
  mode_query:
    doc: |
      AA 05 [selector]: 01 from legacy AbsModeController.p() and kt
      ControllerMode.Companion.b / c (default ext {1}, e.g. the H6057 newdetail
      connect read); a bare AA 05 (selector byte 00, the zero fill) from the
      pact_h60a0 adjust-v1 read ComposeReadAllInfoV1 (compose ModeController,
      ext null)
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/AbsModeController.java:34-37
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerMode.java:45-58,107-109
      - split_h60ax/com/govee/pact_h60a0/ble/controller/compose/ModeController.java:26-44
      - split_h60ax/com/govee/pact_h60a0/adjust/v1/ComposeReadAllInfoV1.java:148
    seq:
      - id: selector
        type: u1
        doc: 1 = the mode read; 0 = a bare AA 05
  device_info_query:
    doc: AA 07 [selector] device-info read (who sends and answers each)
    seq:
      - id: selector
        type: u1
        enum: govee_common::device_info_selector
  group_query:
    doc: |
      AA A5 / AA A2 [group], the 1-based group number (callers loop
      1..maxGroup): Controller4ColorInfoByGroup.Companion.g / m (0xA5) and .j
      (0xA2, colour only); H61A8's batch reads use the same byte
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:111-136,197-211
      - split_rgbiclight/com/govee/rgbiclight/VM4Light.java:937-958
    seq:
      - id: group
        type: u1
  timer_query:
    doc: |
      AA 23 [slot | FF]: one timer slot (0..3) or FF for all four
      (ControllerNewTimer); H6006 / H6008 answer a single-slot read with slot
      0's record and the requested slot number echoed
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerNewTimer.java:52-77
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:446-495
    seq:
      - id: slot
        type: u1
  debug_query:
    doc: |
      FIRMWARE-SOURCED, CAUTION: AA FF [selector] (08 = the factory ageing
      flag; H60A6 with flag 0 also runs FUN_9b033c3c); no app sender
    doc-ref:
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:662-672
    seq:
      - id: selector
        type: u1
  read_reply:
    doc: |
      The 0xAA read reply (and the unsolicited AA 01 power push of the bulbs,
      H60A6 and H5083) [command][state]. The app matches it on [proType,
      command] alone and parses validBytes = frame bytes 2..18 of a 20-byte
      frame; the state is the shared govee_state record, whose rest keeps the
      request echo the firmware leaves in its reply buffer.
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
        doc: the reply's state (data that fills the message; ends with its rest)
  status_request:
    doc: |
      0xAC request [command][args][rest]. The status read is [AC 03 N type_1 ..
      type_N] (ControllerSingleRead, built by Compose4BaseInfoSingleRead),
      e.g. AC 03 02 41 30 (H60A6 single zone) / AC 03 03 41 30 A5; the reply
      is the chunked status TLV burst. Firmware: H60A6 drops a byte 1 other
      than 3 ('msg type error') and answers only 41 / 30 / A5 of the
      requested types after its fixed TLV set; H60A1 has no AC handler (the
      frame is echoed); H6047 drops AC frames.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerSingleRead.java:80-95
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:166-179,230
      - H60A6 FUN_9b04c11c @0x9b04c11c
      - H6047 code @0x010064dc
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
        doc: the zero fill (no pad field)
  status_read_args:
    doc: '[N][N requested TLV types] (Compose4BaseInfoSingleRead.Companion.c)'
    seq:
      - id: num_types
        type: u1
      - id: types
        type: u1
        enum: govee_common::command
        repeat: expr
        repeat-expr: num_types
  status_chunk:
    doc: |
      One 0xAC or 0xAB reply chunk: the frame body (frame bytes 1..n-2) of the
      0xAC status-read reply (has_sub true) and of the 0xAB Wi-Fi multi-reply
      (has_sub false). AbsController4BleSingleSendMultiBack takes 20-byte
      frames only (ComposeBleComm.K):
      - first chunk, index 0: [00][num_packets u2le][len_last][command][sub]
        and a() = 12 data bytes from frame offset k() = 7. 0xAB
        (AbsController4WifiSingleSendMultiBack; Controller4ReadWifiFuncList)
        has no sub byte: a() = 13 data bytes from k() = 6 (its f() skips
        byte 6).
      - middle chunk, index = its position 1..num_packets-2: [index] and 17
        data bytes from offset 2 (i()).
      - last chunk, index 0xFF or its position num_packets-1: [index] and 17
        bytes, of which h() takes the first len_last (declared in the FIRST
        chunk); the rest is padding.
      Matching (isSameController): while no chunk is taken, only a first chunk
      whose command and sub are the reader's (f(): byte 1 == 0, byte 5 ==
      getCommandType(), byte 6 == d()); then only the next position, or 0xFF
      once every packet before the last is in (an early 0xFF is END_PKG_ORDER
      and is ignored). Only a matched first chunk reaches g(), which checks
      len_last, so len_last has no valid here: len_last_fits is false where
      g() returns -1 and the read fails (result2Fail). onResult takes any
      other value (not 20 bytes, or another first byte) as a joined buffer.
      BleMultipleReadHelper reads the same first-chunk layout in the same
      order. The AC readers override only d() and getCommandType(), never a()
      or k().
      Firmware: H60A6 chunks its AC and AB replies with one chunker (AB: no
      sub byte, data from byte 6); H5083 answers AB 01 04 with
      [AB 00 count_le len 01 + 13 bytes] then [AB index|FF + 17 bytes].
      PROCEDURAL: joining the chunks in order and trimming the last to
      len_last (the joined 0xAC data is the govee_status_stream input);
      firmware paces chunks 10 ms apart, 500 ms after every 4th.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:77-79
      - base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:100-149
      - base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:175-238
      - base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:273-308
      - base-classes10/com/govee/base2light/ble/controller/AbsComposeControllerWithOpResult.java:330-333
      - base-classes10/com/govee/base2light/ble/comm/ComposeBleComm.java:190-211
      - base-classes10/com/govee/base2light/ble/controller/AbsController4WifiSingleSendMultiBack.java:25-28
      - base-classes10/com/govee/base2light/ble/controller/Controller4ReadWifiFuncList.java:70-94
      - base-classes12/com/govee/ble/message/BleMultipleReadHelper.java:85-150
      - H60A6 FUN_9b04b906 @0x9b04b906
      - H60A6 FUN_9b04b8bc @0x9b04b8bc
      - H5083 FUN_9b0327f0 @0x9b0327f0
      - H5083 FUN_9b0326f0 @0x9b0326f0
    params:
      - id: has_sub
        type: bool
        doc: true for 0xAC (a sub byte at frame byte 6), false for 0xAB
    seq:
      - id: index
        type: u1
        doc: |
          frame byte 1: 0x00 the first chunk, 1..num_packets-2 a middle chunk
          (its position), 0xFF the last (the app also takes the last at its
          position num_packets-1)
      - id: num_packets
        type: u2le
        if: is_first
        doc: |
          chunk count including the first and the last (allBackPkgs:
          BleUtils.I([b2, b3], false), little-endian)
        doc-ref:
          - base-classes10/com/govee/base2kt/utils/BleUtils.java:212-230
      - id: len_last
        type: u1
        if: is_first
        doc: |
          data bytes in the LAST chunk (endPkgLen, b4 & 0xFF); for a one-packet
          reply, the bytes of this chunk
        doc-ref:
          - base-classes10/com/govee/base2kt/utils/BleUtils.java:272-274
      - id: command
        type: u1
        if: is_first
        doc: |
          frame byte 5: the request's command, echoed (getCommandType). 0xAC:
          a status_command value (3 = status_read). 0xAB: 3 =
          Controller4ReadWifiFuncList, 1 / 2 = the H5083 / H60A6 AB 01 / AB 02
          replies. The enum is not applied, because 0xAB reuses its values.
      - id: sub
        type: u1
        if: has_sub and is_first
        doc: |
          frame byte 6: the reader's d(): 0 for the status read, 5 for H6093's
          special info, 1 for H6020's (6, 1) / (7, 1), 0x13 / 0x0d for the
          h5086 timers / chart
      - id: data
        size-eos: true
        doc: |
          the chunk's data window: frame bytes 7..18 of an 0xAC first chunk
          (6..18 for 0xAB), 2..18 of the others. Data that must round-trip:
          the app takes len_first_data of a first chunk, 17 of a middle chunk
          and len_last of the last packet.
    instances:
      is_first:
        value: index == 0
        doc: the header chunk (f() matches byte 1 == 0)
      is_end_tag:
        value: index == 0xff
        doc: |
          the tagged last chunk (isSameController: byte 1 == -1); the last
          chunk may instead carry its position num_packets-1
      is_single_read_head:
        value: >-
          has_sub and index == 0
          and command == status_command::status_read.to_i
          and sub == 0
        doc: |
          a first chunk that f() matches for the status read (command 3, sub
          0: ControllerSingleRead as Compose4BaseInfoSingleRead builds it, and
          Controller4DetailInfo). False for every other reader, e.g. H6093's
          (3, 5) and H6020's (6, 1): the status read ignores them.
      len_window:
        value: 'is_first ? (has_sub ? 12 : 13) : 17'
        doc: |
          the data bytes this chunk carries for the app: a() (12, or 13 for
          0xAB) for a first chunk, 17 for the others
      len_last_fits:
        value: 'len_last <= (num_packets == 1 ? len_window : 17)'
        if: is_first
        doc: |
          g()'s bound on len_last: at most a() for a one-packet reply, else at
          most 17. Where it is false, g() returns -1 and the read fails at once.
      len_first_data:
        value: 'num_packets == 1 ? len_last : len_window'
        if: is_first and len_last_fits
        doc: |
          the data bytes the app takes from this first chunk: len_last for a
          one-packet reply (which completes the read), else a()
  wifi_multi_request:
    doc: |
      0xAB request [command][args][rest]: AB 03 is the app's Wi-Fi function
      list read (no ext); AB 02 [pact_type u16le][pact_code] the H5083 OTA pact
      report. FIRMWARE-SOURCED: AB 01 [selector] reads Matter info (H5083 sel
      4 = the GID; H60A6 1 QR, 2 status, 3 string, 4 GID, 5 Matter id, 6 QR
      manual); H6006 checks an extra additive matter checksum, needs byte 1 ==
      1 and answers selectors 2 / 4 only, so AB 02 / 03 get no reply there;
      H60A6 and H5083 store a reported pact only when it is newer.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/Controller4ReadWifiFuncList.java:65-84
      - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:214
      - H5083 FUN_9b0327f0 @0x9b0327f0
      - H5083 FUN_9b032308 @0x9b032308
      - H60A6 FUN_9b04baa0 @0x9b04baa0
      - H60A6 FUN_9b04b6c8 @0x9b04b6c8
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
    doc: '[pact_type u16le][pact_code], the pact the device should advertise'
    seq:
      - id: pact_type
        type: u2le
      - id: pact_code
        type: u1
  notify:
    doc: |
      The device push 0xEE [sub][data][rest] (AbsNotify / AbsNotifyParse strip
      EE and the sub-type). The curated lighting screens register 0x11 (Wi-Fi,
      ShareVM), 0x20 (brightness) and 0x30 (switch / zone); the rest are other
      categories' parsers (notify_sub). 0x24 (H6047 earphone [1 in / 0 out],
      FIRMWARE-SOURCED) is untyped. Firmware triggers (behaviour data): EE 30
      after H60A6 power changes, EE 20 from a non-BLE brightness change on
      H6006, EE 11 from the Wi-Fi provisioning result.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/comm/AbsNotify.java:33-40
      - base-classes10/com/govee/base2light/ble/comm/AbsNotifyParse.java:23-31
      - H60A6 FUN_9b0257e0 @0x9b0257e0
      - H6006 code @0x9b020bd0
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
      EE 11 [status][reason]: Wi-Fi connected iff status == 0 (DefParser.Q).
      FIRMWARE-SOURCED reason after status 1 (the app reads only status):
      H5083 1 = credential blob rejected, 2 / 3 = connect failure, 4 / 5 / 6 /
      1 = post-connect failure; H6006 pushes EE 11 01 01 when the A1 11 END
      fails to parse.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_parser/DefParser.java:89-95
      - H5083 FUN_9b031804 @0x9b031804
      - H6006 send_wifi_status @0x9b03c694
      - H6047 FUN_0100b58c @0x0100b58c
    seq:
      - id: status
        type: u1
      - id: reason
        type: u1
        doc: the failure reason (0 when connected)
    instances:
      is_connected:
        value: status == 0
  ic_and_piece:
    doc: |
      H6062 EE 22 IC-and-piece change: sub_ic_1 = data[0], sub_ic_2 = data[2],
      both unsigned (IcAndPieceNotifyParse). The dreamcolorlightv1 family
      uses the same sub-type for its own IC notify (SINGLE_NOTIFY_IC = 34).
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
      EE 30 [detail][flags_a][flags_b][flags_c], the switch / zone push. The
      four bytes are read for every device; their meaning is per form_ee_30
      (ParseUtils.getBit2Boolean numbers bits from 1, so 'bit 1' is the LSB):
      - main_bit0_a: power = bit 0 of flags_a (frame[3]); H60A6 zone 0 / 1 =
        bit 0 of flags_b / flags_c (VM4LightH60A6.t5); H60A1 / H60A4, H6039,
        H6078, H601E / H601F use the power bit only;
      - bit0_c (H60A0): power = bit 0 of flags_c (frame[5]), zones = bit 0
        of flags_a / flags_b (VM4LightH60A0.f5);
      - bars_bc (H6038 / H6048): bar 0 / 1 = flags_b == 1 / flags_c == 1,
        power = either bar (VM4H6038);
      - generic_flags (DefParser.M / O): flags_a bit 0 = main, bits 1..3 =
        sub-switches 1..3 (zone_a_on / zone_b_on = sub 1 / 2).
      Firmware H60A6 sends [01][power][zone0][zone1]; H6047 never sends EE 30.
      H60B0 (bit 7) and H6079 use other layouts (devices.yaml).
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6.java:553-561
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a0/VM4LightH60A0.java:228-236
      - split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:248-250
      - base-classes11/com/govee/base2light/kt/general_parser/DefParser.java:76-87
      - base-classes12/com/govee/base2light/util/ParseUtils.java:55-57,72-74
      - H60A6 FUN_9b0257e0 @0x9b0257e0
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
        doc: the live power by the form's rule (false for unknown)
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
    doc: |
      EE 40, per form_ee_40. The app's generic DefParser.v reads a nested
      selector at data[0] (0 = bool at 1, 1 = u8 at 1, 2 = bool at 1, 3 = signed
      u16 at 1..2, 4 = bool at 1; not typed); H6047 pushes its group-slave
      states.
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
      E7 request [op][fill]. V1: Controller4Aes.e [E7][01] plus random fill
      (Companion.a), and the step-2 confirm [E7][02] (Controller4Aes.h); the
      frame is then PSK-encrypted (AES-ECB over 16 bytes + RC4 over the rest;
      the transport's job). V2 (AES-GCM, E7 19 / 1A, a package counter plus
      AES-GCM) is not supported by the verified firmwares. Firmware: H6006 has
      no E7 handler (no reply); H60A6 arms the V1 gate on every connect and
      answers only op 01 / 02 with a valid BCC; H5083 accepts E7 only in session
      state 1 or 2.
    doc-ref:
      - base-classes13/com/govee/encryp/ble/Controller4Aes.java:47-88
      - base-classes13/com/govee/encryp/ble/Controller4Aes.java:121-136
      - H60A6 FUN_9b054438 @0x9b054438
      - H5083 FUN_9b032fb4 @0x9b032fb4
    seq:
      - id: op
        type: u1
        enum: govee_common::handshake_op
      - id: fill
        size-eos: true
        doc: the random fill (data that must round-trip)
  handshake_reply:
    doc: |
      E7 reply [op][key][rest], decrypted by the transport. V1 step 1:
      Controller4Aes.Companion.g decrypts the reply (Safe.b), accepts it only
      when byte 0 == E7 and byte 1 == 01, and copies frame bytes 2..17 as the
      session key (a key shorter than 16 bytes raises, as g's arraycopy
      throws); the BCC is not checked. Step 2 [E7][02] carries no key
      (H5083 echoes the request). The V2 GCM ops are not modelled (absent).
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
        doc: frame byte 18 (H5083 echoes the request's), not read by the app
  session_key:
    doc: the device-chosen key every later frame is encrypted with
    seq:
      - id: key
        size: 16
  mic_command:
    doc: |
      The 0xA5 mic / live-colour push, a SHORT frame [A5][command][data][sum8]
      (AbsMicController.a; getProType -91): e.g. MicSetRgbController A5 02 83
      R G B <sum>, 7 bytes, and H61A8's colour-wheel drag from software
      1.05.00 (F1: a5 02 83 ff 00 00 29). Firmware: the bulbs catch A5 02
      before any length or BCC check and switch on byte 2 (0x83 applies R G B,
      0x90 queues a message; no reply) and set a live mode the next non-A5
      write clears; H60A1 / H60A6 / H6047 take it length-exempt, check the sum
      (H6047 also byte 1 == 2 and length >= 4), and 0x83 on H60A6 sets mode
      0xA0 (powering on). Distinct from command 0xA5.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/mic/controller/AbsMicController.java:23-55
      - H6006 _system_ble_event_handler @0x9b03df50
      - H6006 ble_msg_group_test @0x9b03cf34
      - H60A6 FUN_9b03c6b0 @0x9b03c6b0
      - H6047 FUN_01001d78 @0x01001d78
    seq:
      - id: command
        type: u1
        doc: 0x02 (set-rgb with byte 2 = 0x83, colour-cmd 0x90)
      - id: data
        size-eos: true
        doc: the command's body, e.g. [83][R][G][B] (data that fills the frame)
  mic_reply:
    doc: |
      FIRMWARE-SOURCED reply to A5 02 90: the 5-byte A5 02 10 01 <sum> (H60A6
      / H60A1; H6047 a5 02 10 01 b8). No other A5 gets a reply.
    doc-ref:
      - H60A6 FUN_9b03c6b0 @0x9b03c6b0
      - H6047 FUN_01001cf8 @0x01001cf8
    seq:
      - id: command
        type: u1
      - id: data
        size-eos: true
        doc: the reply's body (data that fills the frame)
  mpm_packet:
    doc: |
      MultiPackageManager chunk [com_type][position][num_packets if START]
      [chunk]: A1 TX (written) and A2 RX (the device's read stream). position
      0x00 = START (byte 3 = the packet count), 1..n = data packets of 16
      bytes from byte 3 (taken only in order), 0xFF = END. App reading of A2
      (MultiPackageManager.g): the START opens a response, the END completes
      it when count == the data packets received. Firmware (A1 11 Wi-Fi
      provisioning, A1 02 DIY on H5083): count = the DATA packets only, DATA
      accepted when position == counter + 1, START and DATA get no reply.
      PROCEDURAL: the client splits and joins the value.
    doc-ref:
      - base-classes12/com/govee/ble/multi/MultiPackageManager.java:46-124
      - H6006 code @0x9b03cd20-0x9b03cec4
      - H5083 FUN_9b031c2c @0x9b031c2c
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:194-234
      - H60A6 FUN_9b026048 @0x9b026048
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
        doc: the value bytes (16 per data packet; data that fills the frame)
  mpm_result:
    doc: |
      The A1 write response [com_type][result][rest]: MultiPackageManager.g
      posts result = byte 2 == 0. Firmware: H6006 answers a good END with
      [A1][11][00][END bytes 3..18], H5083 the same (A1 02 with a zero tail),
      but H60A6 echoes the END frame (byte 2 = FF, which the app reads as a
      failure); START, DATA and a failed END get no reply.
    doc-ref:
      - base-classes12/com/govee/ble/multi/MultiPackageManager.java:75-80
      - H6006 code @0x9b03cdc8-0x9b03cde6
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:714-720
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:211-213
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
      MultipleControllerCommV1 0xA3 frame [index][start if index == 0][chunk]:
      index 0x00 = START, 1..n = middle frames, 0xFF = END. The START head is
      a3_start; its inline value and every later frame's 17 value bytes are
      `chunk`. The END carries the last chunk (up to 17 bytes) when the value
      is longer than the START's inline bytes; otherwise the whole value
      rides in the START, the count is 2 and the END is empty (V2 always
      appends its own END frame). Firmware (H6006 FUN_9b020d2c, H6047, H60A6,
      H5083): START byte 3 = the count including START and END, the marker
      byte is ignored, middle frames are taken in order, END bytes 2..18 are
      copied when (frames after START, END included) + 1 == count; H60A6's
      0x100-byte buffer overflows past 15 frames. PROCEDURAL: the client
      splits and joins the value (govee_upload reads the join).
    doc-ref:
      - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:677-859
      - firmware-h6006/decompiled/H6006_a3_reassembler.c:38-176
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:739-833
      - H6047 code @0x01006200
    seq:
      - id: index
        type: u1
      - id: start
        type: a3_start
        if: index == 0
      - id: chunk
        size-eos: true
        doc: |
          the value bytes: 17 in a middle or END frame; in the START the inline
          value (14 for V1 / V2 with one commByte, 4 for V3, zero fill for
          V0). A second commByte (MultipleController4Music [0x41, p()]) is the
          first inline byte.
  a3_start:
    doc: |
      The 0xA3 START head [marker][num_packets][comm_byte], plus for V3
      [ctrl_num][ctrl_idx][verify 8]. V0 (makeSendBytesV0, p() == 0) has no
      inline value and an empty END; V1 / V2 (makeSendBytesV2, also V1 with
      one commByte) is the form every curated device uses; V3 (K /
      makeSendBytesV3) sends one START..END group per controller with a
      data-bearing END. O and N (makeSendBytesMtuV1) also write marker 1 but
      have no caller.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:74-136
      - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:677-831
    seq:
      - id: marker
        type: u1
        enum: a3_marker
        doc: frame byte 2 (firmware ignores it)
      - id: num_packets
        type: u1
        doc: frame byte 3, the frame count including START and END
      - id: comm_byte
        type: u1
        enum: comm_byte
        doc: |
          frame byte 4: a legacy scene version constant (V1 = 1, V2 = 2, V4 =
          4, V3 = 7, V5 = 10, V6 / V7 = 12) or a device DIY / graffiti
          protocol code (H60A6 0x58; not value[0] | 0x08). The codes are scoped
          to a device family; base_h71xx LightMultipleControl and
          AtmospherePreviewMultipleControl pass a variable code (default 6,
          reassigned at runtime). The value is the controller's getValue()
          (DIY / graffiti re-encoded by toBytes, not the raw cloud blob).
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
      The MTU-sized 0xA4 frame (makeSendBytesMtu) [index u16le][start if 0]
      [chunk]: index 0x0000 = START, 0xFFFF = END, else the 1-based middle
      index. START value from byte 7 (up to mtuSize - 8 bytes), middle / END
      from byte 3 (mtuSize - 4, END the remainder); frame size =
      MtuConfig.getAvailableMtuSize = max(cached MTU - 3, 20), 20 unless a
      larger MTU was negotiated. Reached only via
      Compose4DefWrite4Multi.makeWriteController for command 0x58 with effect
      byte 0x5E / 0x5D / 0x20 (H60A6 graffiti). A 187-byte value at MTU 20 is
      START(12) + 10 x MIDDLE(16) + END(15). A value of at most mtuSize - 8
      bytes is one exact-length START plus a separate 4-byte terminator [A4 FF
      FF <BCC>] (count 2); a longer one has no separate terminator. Firmware:
      H60A6 exempts A4 from the length check (H60A1 does not), checks an XOR
      over len - 1 bytes and never reads the commByte. PROCEDURAL: split and
      join are the client's.
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
        doc: this frame's value bytes (data that fills the frame)
  a4_start:
    doc: '[01][num_packets u16le][comm_byte]: the 0xA4 START head (bytes 3..6)'
    seq:
      - id: marker
        contents: [0x01]
      - id: num_packets
        type: u2le
        doc: the frame count including START and END (2 for a short value)
      - id: comm_byte
        type: u1
        enum: comm_byte
        doc: byte 6 (byte 4 in the 0xA3 form); 0x58 on H60A6
  upload_ack:
    doc: |
      The 0xA3 / 0xA4 / 0xA6 ack and push [command][result][rest]. The app's
      multi controller matches [proType, command] and accepts a 20-byte frame
      whose byte b() is 0 (b() = 2; 3 for AbsControllerNoEvent4MultiImpV2).
      Firmware: H6006 acks commByte 1 / 4 with [A3][comm][00 x17] after the
      END; H60A6 answers the END with [A3][comm][0] for commByte 1 / 2 / 4 /
      0x58 and the END's own byte 2 otherwise (commByte 7 then reads as a
      failure), and a complete A4 upload with the constant [A4][58][00..];
      H6047 acks 01 / 02 / 03 / 0a / 40 / 51 / 60 with zeros, 04 with [00] and
      41 with the value's byte 0. FIRMWARE-SOURCED: the A3 08 push [A3][08]
      [status] reports the IoT-config result (0 = server connect OK).
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Multi.java:31-33,69-70,80-95
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerMTUMulti.java:25,32-34
      - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4MultiImpV2.java:25-33
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:122-126
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:934-940
      - H60A6 FUN_9b04c190 @0x9b04c190
      - H6047 code @0x01006312
      - H6006 send_iot_status @0x9b03c71c
      - H5083 FUN_9b031860 @0x9b031860
    seq:
      - id: command
        type: u1
        enum: comm_byte
        doc: the acked commByte (A4 acks 0x58)
      - id: result
        type: u1
        doc: frame byte 2, 0 = success
      - id: rest
        type: govee_common::echo
    instances:
      is_ok:
        value: result == 0
        doc: the app's acceptance for b() = 2
enums:
  route:
    0x005:
      id: gid_info_tx
      doc: |
        FIRMWARE-SOURCED 0x05 gid-info request (H60A6 / H60A1 read
        gid_info_len from the 3F buffer); no checksum byte; raw body
    0x105:
      id: gid_info_rx
      doc: the 0x05 reply [05][0 | 1][18 x 00], no XOR stamp; raw body
    0x033:
      id: write_tx
      doc: 0x33 write -> write_command
    0x133:
      id: write_rx
      doc: 0x33 ack -> write_ack
    0x03a:
      id: write_read_tx
      doc: 0x3A write-and-read -> write_command
    0x13a:
      id: write_read_rx
      doc: 0x3A ack -> write_ack
    0x03f:
      id: multiple_write_3f_tx
      doc: |
        FIRMWARE-SOURCED, CAUTION (Matter factory): one GATT write of n x 20
        bytes, each unit XOR-checked; raw body
    0x13f:
      id: multiple_write_3f_rx
      doc: the 3F reply [3F][op][status], sent only at END or on error; raw
    0x066:
      id: factory_test_tx
      doc: FIRMWARE-SOURCED, CAUTION factory-test entry 66 01 / 66 02; raw
    0x166:
      id: factory_test_rx
      doc: 66 01 00 .. / the 66 02 echo; raw
    0x0a1:
      id: multi_write_tx
      doc: A1 MultiPackageManager write -> mpm_packet
    0x1a1:
      id: multi_write_rx
      doc: A1 write response -> mpm_result
    0x1a2:
      id: multi_read_rx
      doc: A2 multi-packet read stream -> mpm_packet
    0x0a3:
      id: multi_write_v1_tx
      doc: A3 scene / DIY upload -> a3_packet
    0x1a3:
      id: multi_write_v1_rx
      doc: A3 ack and the A3 08 push -> upload_ack
    0x0a4:
      id: multi_write_v2_tx
      doc: A4 MTU upload -> a4_packet
    0x1a4:
      id: multi_write_v2_rx
      doc: A4 ack (H60A6 A4 58) -> upload_ack
    0x0a5:
      id: mic_tx
      doc: A5 mic push (sum8) -> mic_command
    0x1a5:
      id: mic_rx
      doc: A5 02 10 01 reply (sum8) -> mic_reply
    0x0a6:
      id: mtu_multiple_write_tx
      doc: A6 MTU multi-packet write; body not traced (raw)
    0x1a6:
      id: mtu_multiple_write_rx
      doc: A6 ack -> upload_ack
    0x0aa:
      id: read_tx
      doc: AA read request -> read_request
    0x1aa:
      id: read_rx
      doc: AA read reply or unsolicited AA 01 push -> read_reply
    0x0ab:
      id: wifi_multi_tx
      doc: AB request -> wifi_multi_request
    0x1ab:
      id: wifi_multi_rx
      doc: AB multi-reply chunk (no sub byte) -> status_chunk(false)
    0x0ac:
      id: status_read_tx
      doc: AC request -> status_request
    0x1ac:
      id: status_read_rx
      doc: AC status reply chunk -> status_chunk(true)
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
      doc: E7 handshake request -> handshake_request
    0x1e7:
      id: handshake_rx
      doc: E7 handshake reply -> handshake_reply
    0x1ee:
      id: notify_rx
      doc: EE device push -> notify
  checksum_kind:
    0:
      id: none
      doc: |
        no checksum byte: the 0x05 gid-info head (H60A6 / H60A1 build the
        reply without an XOR stamp, so byte 19 is 00)
      doc-ref:
        - H60A6 FUN_9b04c190 @0x9b04c190
        - H60A6 FUN_9b04b8ac @0x9b04b8ac
    1:
      id: xor
      doc: |
        the last byte is the XOR of every byte before it (BleUtils.w over bytes
        0..18 of a 20-byte frame; MTU-length 0xA4 / 0xA6 packets over their
        own length). PROCEDURAL: the library computes and checks it.
      doc-ref:
        - base-classes10/com/govee/base2kt/utils/BleUtils.java:1260-1267
        - base-classes10/com/govee/base2kt/utils/BleUtils.java:1030-1041
    2:
      id: sum8
      doc: |
        the last byte is the 8-bit additive sum of every byte before it: the
        0xA5 mic frame (AbsMicController.a) and the 0xB0..0xBF group_pro heads
        (H6047 FUN_010105a4 length and sum check; replies FUN_010104fc)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/mic/controller/AbsMicController.java:23-52
        - H6047 FUN_010105a4 @0x010105a4
        - H6047 FUN_010104fc @0x010104fc
        - H60A6 FUN_9b053f18 @0x9b053f18
  light_power:
    0x00:
      id: light_off
      doc: off, the app's (byte) 0 for false
    0x01:
      id: light_on
      doc: on, the app's (byte) 1 for true
  relay_write:
    0x10:
      id: relay0_off
      doc: outlet g = 0, off (16 | false); H5083 switches its outlet off
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:57-59
    0x11:
      id: relay0_on
      doc: outlet g = 0, on (16 | true); H5083 switches its outlet on
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:57-59
    0x20:
      id: relay1_off
      doc: outlet g = 1, off (32 | 0); ignored by H5083 (bit 4 clear)
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:60-62
    0x22:
      id: relay1_on
      doc: outlet g = 1, on (32 | 2); ignored by H5083 (bit 4 clear)
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:60-62
    0x40:
      id: relay2_off
      doc: outlet g = 2, off (64 | 0); ignored by H5083 (bit 4 clear)
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:63-65
    0x44:
      id: relay2_on
      doc: outlet g = 2, on (64 | 4); ignored by H5083 (bit 4 clear)
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:63-65
    0xf0:
      id: all_off
      doc: any other g (all outlets), off (-16 | 0)
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:66-69
    0xff:
      id: all_on
      doc: any other g (all outlets), on (-16 | 15)
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:66-69
  status_command:
    0x01:
      id: cmd_01
      doc: |
        Read by sub byte: h5086 TimerReadController (sub 0x13) and
        RecentOneHourChartController (sub 0x0d); ReadAiEditInfoController and
        the base_h71xx TimerReadController (AbsSingleReadMultiReplyController
        family). Neutral name: the meaning is the sub's.
      doc-ref:
        - split_plugnew/com/govee/h5086/ble/controller/TimerReadController.java:92-98
        - split_plugnew/com/govee/h5086/ble/controller/RecentOneHourChartController.java:150-156
        - base-classes10/com/govee/base2light/ai4edit/controller/ReadAiEditInfoController.java:54-55
        - base-classes12/com/govee/base_h71xx/sku_base/ble/controller/TimerReadController.java:27-33
    0x02:
      id: device_self
      doc: cubelight DeviceSelfController
      doc-ref:
        - split_h6061/com/govee/cubelight/ble/DeviceSelfController.java:25-26
    0x03:
      id: status_read
      doc: |
        The status TLV read [AC 03 N type_1 .. type_N]: ControllerSingleRead's
        default command (sub d() = 0, its q never set), built by
        Compose4BaseInfoSingleRead with the [N, types] bytes from Companion.c.
        Controller4DetailInfo is also (3, 0). ControllerReadSpecialInfoMultiBack
        .Companion.g reads (3, 5) (H6093), which is not the status read. The
        reply is the govee_status_stream TLV stream.
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerSingleRead.java:80-95
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:166-179
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:230
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4DetailInfo.java:104-105
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerReadSpecialInfoMultiBack.java:76-78
        - split_h6092/com/govee/h6092/newDetail/h6093/VM4LightH6093.java:709
    0x06:
      id: bluetooth_name
      doc: |
        Controller4ReadBluetoothName / Controller4ReadBluetoothNameMsg; H6020
        reads it as (6, 1) through ControllerSingleRead.Companion.a
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ReadBluetoothName.java:49-50
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ReadBluetoothNameMsg.java:211-212
        - split_h6079/com/govee/h6020/newDetial/H6020VM4Light$readBluetoothName$2.java:94
    0x07:
      id: combo_state
      doc: |
        Controller4ReadComboState; H6020 reads (7, 1) through
        ControllerSingleRead.Companion.a
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ReadComboState.java:211-212
        - split_h6079/com/govee/h6020/newDetial/H6020VM4Light.java:2223
    0x09:
      id: day_sync
      doc: DaySyncVM, through ControllerSingleRead
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncVM.java:319
  wifi_multi_command:
    0x01:
      id: matter_info
      doc: FIRMWARE-SOURCED AB 01 [selector] Matter info / GID read
    0x02:
      id: pact_report
      doc: AB 02 [pact_type u16le][pact_code] (H5083 OTA pact report)
    0x03:
      id: wifi_func_list
      doc: the app's AB 03 Wi-Fi function-list read (no ext)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/Controller4ReadWifiFuncList.java:80-84
  a3_marker:
    0x00:
      id: v0
      doc: |
        makeSendBytesV0 [A3 00 00 packs+2 commByte 0 ...]: no inline value, an
        empty END; chosen when the controller's p() == 0
      doc-ref:
        - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:677-713
        - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:833-843
    0x01:
      id: v1
      doc: |
        makeSendBytesV2 (and V1 with one commByte) [A3 00 01 count
        commBytes value ..], the form every curated device uses
      doc-ref:
        - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783
    0x02:
      id: v3
      doc: |
        K / makeSendBytesV3 [A3 00 02 count commByte ctrlNum ctrlIdx verify(8)
        value(4)], one START..END group per controller, data-bearing END
      doc-ref:
        - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:74-136
        - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:785-831
  comm_byte:
    0x01:
      id: v1_new_scenes
      -orig-id: MULTI_V1_NEW_SCENES
      doc: |
        the sceneType 1 (rgb) library scene (MultiNewScenesControllerV1;
        ScenesOp.parseSceneV1 -> l), the decoded scenceParam verbatim; also
        MultiDiyTempalteController4Rgb
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:71
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV1.java:10-11
        - base-classes10/com/govee/base2light/ble/ScenesOp.java:377-392,543-544
        - base-classes10/com/govee/base2light/ble/controller/MultiDiyTempalteController4Rgb.java:10-11
    0x02:
      id: v2_new_scenes
      -orig-id: [MULTI_V2_NEW_SCENES, MULTI_DIY]
      doc: |
        the sceneType 2 (rgbic) library scene (MultiNewScenesControllerV2),
        verbatim; also MultiDiyTempalteController and DiyRgbicV1Parse. As an
        A1 com_type: the legacy MultipleDiyController (MULTI_DIY); H5083
        takes A1 02 DIY packets and discards the value.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:43,73
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV2.java:10-11
        - base-classes10/com/govee/base2light/ble/ScenesOp.java:394-409,546-547
        - base-classes10/com/govee/base2light/ac/diyNew/parse/DiyRgbicV1Parse.java:101-102
        - base-classes10/com/govee/base2light/ble/controller/MultipleDiyController.java:16-17
    0x03:
      id: v1_new_diy_graffiti
      -orig-id: MULTI_V1_NEW_DIY_GRAFFITI
      doc: |
        DIY graffiti (MultiDiyGraffitiController / V2,
        MultipleDiyInScenesController for DiyGraffiti, the H6052 type-5 simple
        graffiti), the RGBIC DIY-editor graffiti_v2 (DIYGraffitiParser,
        RgbIcGraffitiShare0x08) and H6052DiyNormalGraffitiParse
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:67
        - base-classes10/com/govee/base2light/ble/controller/MultiDiyGraffitiController.java:25-26
        - base-classes10/com/govee/base2light/ble/MultipleDiyInScenesController.java:12-14
        - base-classes10/com/govee/base2light/ble/controller/MultiDiyGraffitiControllerV2.java:10-11
        - base-classes10/com/govee/base2light/ac/diyNew/parse/DIYGraffitiParser.java:143-144
        - base-classes10/com/govee/base2light/ac/diyNew/parse/RgbIcGraffitiShare0x08.java:266-267
        - split_hollowlamp/com/govee/tablelampv1/newAdjust/h6052/diy/H6052DiyNormalGraffitiParse.java:116-117
    0x04:
      id: v1_new_diy
      -orig-id: MULTI_V1_NEW_DIY
      doc: |
        DIY (MultipleDiyControllerV1 / V2, MultipleDiyInScenesController for
        DiyProtocol, DiyProtocolParseShare0x00); also the cube scenes
        (MultiNewScenesControllerV4) and the sceneType-5 blobs parseSceneV1
        reaches with a lead other than 0x40 / 0x41 (value = blob[1:]; lead
        0x00 in CommonDiyParseConfig); the bulb DIY value on H6006 / H6008
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:66
        - base-classes10/com/govee/base2light/ble/controller/MultipleDiyControllerV1.java:16-17
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV4.java:14-15
        - base-classes10/com/govee/base2light/ble/controller/MultipleDiyControllerV2.java:24-25
        - base-classes10/com/govee/base2light/ble/MultipleDiyInScenesController.java:20-22
        - base-classes10/com/govee/base2light/ac/diyNew/parse/DiyProtocolParseShare0x00.java:111-112
        - base-classes10/com/govee/base2light/ble/ScenesOp.java:209-241,604-611
        - base-classes10/com/govee/base2light/ble/CubeScenesComm.java:256,307,320
        - base-classes12/com/govee/base2light/widget/graffiti/bean/CommonDiyParseConfig.java:144
        - firmware-h6006/decompiled/H6006_a3_reassembler.c:75-141
    0x07:
      id: v3_new_scenes
      -orig-id: MULTI_V3_NEW_SCENES
      doc: |
        the sceneType 3 (graffiti) library scene (MultiNewScenesControllerV3),
        the scenceParam minus its 2-byte lead; H60A6 SW1.00.41 and H6047
        SW1.00.12 do not process it
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:74
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV3.java:10-11
        - base-classes10/com/govee/base2light/ble/ScenesOp.java:411-429,549-550
    0x08:
      id: iot_config
      -orig-id: MULTI_V1_NEW_DYNAMIC_API
      doc: |
        FIRMWARE-SOURCED as used: the IoT / Wi-Fi config value [08][len
        u16be][URL] (H5083, H6006 'wifiparam', H60A6 Wi-Fi); the result comes
        back as the A3 08 push. No app sender traced.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:69
        - H5083 FUN_9b0317b4 @0x9b0317b4
        - H6006 code @0x9b03d93e-0x9b03d986
    0x09:
      id: v1_graffiti_multi_layer
      -orig-id: MULTI_V1_GRAFFITI_MULTI_LAYER
      doc: |
        the H6052 type-5 professional graffiti (DiyGraffitiV3, the scenceParam
        minus its 0x13 lead; H6052DiyProGraffitiParse),
        MultiDiyGraffitiControllerV1, H6078ProGraffitiParse
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:64
        - base-classes10/com/govee/base2light/ble/MultipleDiyInScenesController.java:16-18
        - base-classes10/com/govee/base2light/ble/controller/MultiDiyGraffitiControllerV1.java:10-11
        - split_hollowlamp/com/govee/tablelampv1/newAdjust/h6052/diy/H6052DiyProGraffitiParse.java:110-111
        - split_hollowlamp/com/govee/tablelampv1/newAdjust/diy/parse/H6078ProGraffitiParse.java:94-95
    0x0a:
      id: v4_new_scenes
      -orig-id: MULTI_V4_NEW_SCENES
      doc: |
        the sceneType 6 (compose) library scene, minus its 0x0a lead, sent by
        MultiNewScenesControllerV5; also MultiDiyTempalteControllerV2
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:75
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV5.java:14-15
        - base-classes10/com/govee/base2light/ble/controller/MultiDiyTempalteControllerV2.java:19-20
        - base-classes10/com/govee/base2light/ble/ScenesOp.java:431-450,552-553
    0x0b:
      id: preview_effect
      -orig-id: MULTI_PREVIEW_EFFECT
      doc: H6057 preset colours (MultiPreSetColorController)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:58
        - split_hollowlamp/com/govee/h6057/origin/ble/MultiPreSetColorController.java:26-27
    0x0c:
      id: diy_protocol_scenes
      -orig-id: [MULTI_DIY_PROTOCOL_SCENES, MULTI_DIY_PROTOCOL_2_SCENE_MODE]
      doc: |
        MultiNewScenesControllerV6 (H610B) and V7 (H6057 / H6078 graffiti
        scenes, whose constructor replaces the lead byte with subOpType 3 / 9)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:47-48
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV6.java:14-15
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV7.java:5-20
        - base-classes10/com/govee/base2light/ble/ScenesOp.java:635-657
    0x11:
      id: wifi
      -orig-id: MULTI_WIFI
      doc: |
        the A1 com_type of MultipleWifiController: the Wi-Fi provisioning blob
        (H5083 [ssidLen][ssid][pwLen 0 or >= 8][pw][runMode][tzH][iotVer][tzM]
        [apiLen u16be][api]; H6047 [ssid_len][ssid][pwd_len][pwd][run_mode]..)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:76
        - base-classes10/com/govee/base2light/ble/controller/MultipleWifiController.java:130-131
        - H5083 FUN_9b0315fc @0x9b0315fc
    0x31:
      id: cube_multi_device
      doc: cubelight MultiDeviceController (also h6067, H606A)
      doc-ref:
        - split_h6061/com/govee/cubelight/ble/MultiDeviceController.java:63-64
    0x40:
      id: v1_new_color
      -orig-id: MULTI_V1_NEW_COLOR
      doc: |
        the colour-strip value (MultipleColorStripControllerV1,
        MultipleColorTemStripController,
        MultipleGradientColorTemStripController); H6047 aborts on an index >= 10
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:65
        - base-classes10/com/govee/base2light/ble/controller/MultipleColorStripControllerV1.java:16-17
        - base-classes10/com/govee/base2light/ble/controller/MultipleColorTemStripController.java:49-50
        - base-classes10/com/govee/base2light/ble/controller/MultipleGradientColorTemStripController.java:30-31
        - H6047 FUN_01005af0 @0x01005af0
    0x41:
      id: v1_new_music
      -orig-id: MULTI_V1_NEW_MUSIC
      doc: |
        MultiMusicController; MultipleController4Music sends [0x41, p()], so
        byte 5 is p(); H6047 [code][n][RGB x n]
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:70
        - base-classes10/com/govee/base2light/ble/controller/MultiMusicController.java:78-79
        - base-classes10/com/govee/base2light/ble/controller/MultipleController4Music.java:44-45
    0x42:
      id: music_compose
      doc: MusicComposeController via MultiControllerNoEvent makeSendBytesV1
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/MultiControllerNoEvent.java:19
        - base-classes10/com/govee/base2light/ble/controller/MusicComposeController.java:36
    0x50:
      id: set_device_4_movie_feast
      -orig-id: MULTI_SET_DEVICE_4_MOVIE_FEAST
      doc: MultiSetSubDeviceController4MovieFeast
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
      doc: cubelight CalibrationAllController (also h6067)
      doc-ref:
        - split_h6061/com/govee/cubelight/ble/CalibrationAllController.java:41-42
    0x56:
      id: v1_new_diy_h7033
      -orig-id: MULTI_V1_NEW_DIY_h7033
      doc: |
        also the H6092 library scene (MultiNewScenesControllerV8, whose
        constructor replaces the lead byte with subOpType 11) and PlayEffectM
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:68
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV8.java:5-20
        - base-classes10/com/govee/base2light/ble/ScenesOp.java:627-633
        - base-classes11/com/govee/base2light/effectPlay/effect/PlayEffectM.java:681
    0x57:
      id: graffiti_in_series
      -orig-id: MULTI_GRAFFITI_IN_SERIES
      doc: MultiDiyGraffitiControllerV3
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:52
        - base-classes10/com/govee/base2light/ble/controller/MultiDiyGraffitiControllerV3.java:10-11
    0x58:
      id: cube_in_diy
      -orig-id: MULTI_CUBE_IN_DIY
      doc: |
        the device DIY / graffiti protocol code of the new-DIY SKUs, H60A6
        among them (H60A6DiyParse on 0xA3, H60A6GraffitiParse on 0xA4); also
        MultiNewScenesControllerV10 / H70CX, MultiDiyFixedDeviceController,
        MultipleDiyControllerV3, SetAiActionController
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:41
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6DiyParse.java:253-254
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6GraffitiParse.java:198-199
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV10.java:19-20
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerH70CX.java:18-19
        - base-classes10/com/govee/base2light/ble/controller/MultiDiyFixedDeviceController.java:14-15
        - base-classes10/com/govee/base2light/ble/controller/MultipleDiyControllerV3.java:24-25
        - base-classes10/com/govee/base2light/ai4edit/controller/SetAiActionController.java:62-63
        - base-classes11/com/govee/base2light/kt/ble/Compose4DefWrite4Multi.java:240
        - base-classes13/com/govee/eco/execution/plan/PlanCmdOpManager.java:159
        - split_h60ax/com/govee/pact_h60a0/iot/OpNewDiyCommDialog4BleIot.java:113
    0x59:
      id: cube_in_diy_4_70dx_or_v1_operate_diy_h6092
      -orig-id: [MULTI_CUBE_IN_DIY_4_70dx, MULTI_V1_OPERATE_DIY_h6092]
      doc: PlayEffectM makeSendBytesV3((byte) -93, (byte) 89, ...) (marker 2)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:42,72
        - base-classes11/com/govee/base2light/effectPlay/effect/PlayEffectM.java:687
    0x5a:
      id: graffiti_coordinate
      -orig-id: MULTI_GRAFFITI_COORDINATE
      doc: MultiNewScenesControllerH60B0, a compose blob with lead 0x51
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:49
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerH60B0.java:34-35
        - base-classes10/com/govee/base2light/ble/ScenesOp.java:444-446
    0x5b:
      id: splicing_graffiti_multi_common
      -orig-id: MULTI_SPLICING_GRAFFITI_MULTI_COMMON
      doc: makeSendBytesV3 (marker 2) from PlayEffectM and the eco parsers
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:63
        - base-classes11/com/govee/base2light/effectPlay/effect/PlayEffectM.java:125
        - base-classes13/com/govee/eco/executeAction/parser/SceneParser4H70B1BC.java:171,214
        - base-classes13/com/govee/eco/executeAction/parser/MorningUpParser4H70B1BC.java:172,215
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
      doc: DiyAdvancedCombParse
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:40
        - base-classes10/com/govee/base2light/ac/diyNew/views/combination/DiyAdvancedCombParse.java:164-165
    0x63:
      id: daysync_music_mode
      doc: DaySyncUtils makeSendBytesV3((byte) -93, value_music_mode, ...)
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:840
        - base-classes11/com/govee/base2light/pact/feast/BleProtocol.java:35
    0xa4:
      id: part_scenes
      doc: |
        MultiNewScenesControllerV9 ((byte) -92) from PartScenesViewModel; byte
        4 of an 0xA3 frame, not the proType 0xA4
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV9.java:15-16
        - base-classes11/com/govee/base2light/pact/newdetail/content/scene/partScenes/viewModel/PartScenesViewModel.java:123,140
    0xa5:
      id: local_color_setting
      doc: |
        hollowlamp LocalColorSettingMultiControllerV1 ((byte) -91; also
        h6057/origin); not the proType 0xA5 or the command 0xA5
      doc-ref:
        - split_hollowlamp/com/govee/hollowlamp/ble/LocalColorSettingMultiControllerV1.java:42-43
