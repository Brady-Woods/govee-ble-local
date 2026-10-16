meta:
  id: govee_ble_frame
  title: Govee BLE 20-byte application frame (vendor GATT protocol)
  file-extension: govee
  endian: be
  bit-endian: be
doc: |
  One Govee BLE application frame. Every frame written to / notified on the vendor
  control characteristic (00010203-0405-0607-0809-0a0b0c0d2b11 write / ...2b10 notify)
  is 20 bytes for single-frame commands; the 0xA4 MTU MIDDLE/END packets and the small terminator are shorter:

      [ pro_type ][ body (size = frame_len - 2) ][ checksum ]
        byte 0      bytes 1 .. n-2                 byte n-1

  checksum = XOR of bytes 0..18. Kaitai expressions cannot fold over a byte range, so
  `checksum` is captured as a raw field and documented rather than recomputed here; a
  validator/builder must compute it separately (see GOVEE_BLE_GATT_PROTOCOL.md §4.1).

  This spec is parse-oriented (Kaitai generates readers). The write side is symmetric:
  build the same layout and append the XOR checksum. Multi-byte integers (e.g. kelvin)
  are big-endian, matching BleUtil.getSignedBytesFor2(value, hiFirst=true).

  Group control (com.govee.ble.group): the same 20-byte frames are written to multiple
  group members over dedicated group service/characteristic UUIDs, serialized with ~20-100 ms
  spacing (BleController.K). The frame layout is identical; only the transport fan-out differs.
  Advertisement (manufacturer-data) parsing is a separate bitstream — see govee_adv.ksy.

seq:
  - id: pro_type
    type: u1
    enum: pro_type
    doc: Header byte (byte 0) — selects the frame family.
  - id: body
    size: _io.size - 2
    type:
      switch-on: pro_type
      cases:
        'pro_type::write': single_command
        'pro_type::read': read_command
        'pro_type::notify': notify_frame
        'pro_type::multi_write': multi_a1
        'pro_type::multi_read': multi_a1
        'pro_type::multi_write_v1': multi_a3
        'pro_type::multi_write_v2': multi_a4
        'pro_type::multi_reply_read': multi_ac
        'pro_type::handshake': handshake
  - id: checksum
    type: u1
    doc: XOR of bytes 0..18 (computed externally).

enums:
  pro_type:
    0x33: write            # app -> device command
    0xaa: read             # app -> device query (echoed back)
    0xee: notify           # device -> app push (byte 1 = sub-type)
    0x3a: write_read       # write-and-read variant
    0xab: bbq_status       # BBQ telemetry push (out of scope here)
    0xa1: multi_write      # MultiPackageManager chunked write
    0xa2: multi_read       # MultiPackageManager chunked read
    0xa3: multi_write_v1   # MultipleControllerCommV1 scene/DIY dialect (non-MTU; commByte@byte4)
    0xa4: multi_write_v2   # MTU-sized variant (makeSendBytesMtu; commByte@byte6). RARE: only cmd 88 + effect byte in {0x5E,0x5D,0x20}; no curated device uses it (see multi_a4).
    0xac: multi_reply_read # single-request / multi-reply status read
    0xe7: handshake        # AES/GCM session handshake
    0xa5: mic              # mic / music live-colour push. NOT a 20-byte XOR frame: it is a SHORT frame with an ADDITIVE checksum (see mic_color_frame), so it is NOT a case of `frame` above. AbsMicController.getProType = (byte)-91 = 0xA5 (base2light/ble/mic/controller/AbsMicController.java:55). Device-confirmed on H60A1/H60A6 (length-exempt byte[0]==0xA5 dispatcher path, FIRMWARE_VALIDATION.md) and on H6006/H6008. On the bulbs, the BLE event handler catches byte[0]==0xA5 && byte[1]==0x02 before any length or BCC check (H6006 _system_ble_event_handler 0x9b03df50; H6008 0x9b03d8a2) and calls ble_msg_group_test (H6006 0x9b03cf34, H6008 0x9b03cc64). That switches on byte 2. 0x83 (MicSetRgbController) passes bytes 3..5 = R,G,B to the colour-apply callback (H6006 0x9b0264b8 -> 0x9b023804; H6008 0x9b029638 -> 0x9b0277ac). 0x90 (MicColorCmdController) queues an internal message. Anything else is ignored. The additive checksum is not checked and no reply is sent. Distinct from command 0xA5 local_color_read (sent under proType 0xAA).

  command:
    # Shared catalog (BleProtocolConstants). Devices extend this set.
    0x01: switch
    0x04: brightness
    0x05: mode
    0x06: soft_version
    0x07: device_info
    0x09: sync_time
    0x0b: delay_close
    0x11: sleep
    0x12: wakeup
    0x0e: current_limit
    0x0f: light_num
    0x14: gradual                 # 33 14 = gradual write (gradual_write). ALSO BleProtocolConstants.SINGLE_WIFI_MAC (20): AA 14 = Wi-Fi MAC read on Wi-Fi SKUs (WifiMacController f = 20 / kt ControllerWifiMacAddress; H6047 PairAcV1:257, gt-64 bulbs ControllerStrategyConfig.h0:648-654); reply [mac6, forward order], no reply type here (see read_request)
    0x20: wifi_hard_version       # AA 20 (WifiHardVersionController f = 32 / kt ControllerWifiHardVersion): reply ASCII from frame byte 2. H6006/H6008/H60A1/H60A6 firmware return the same string as AA 07 03; H6047 handler 0x01005140 (see read_request)
    # NOT in this enum (a read_request / read_command with these bytes keeps the int in Python and reads null in Java):
    #   0x00 heart_beat: BleProtocolConstants.SINGLE_HEART_BEAT (0). AA 00 is the newdetail ShareVM heartbeat when I3() is
    #        false (H60A1/H60A6; ConnectHeartbeat.c(2)). Firmware-sourced, H60A1/H60A6: an explicit no-op case, the frame is
    #        echoed with the XOR re-stamped (status byte written only for 0x33 frames: H60A6 0x9b026c6a, H60A1 0x9b026140).
    #   0x21 wifi_soft_version: BleProtocolConstants.SINGLE_WIFI_SOFT_VERSION (33). AA 21 (WifiSoftVersionController f = 33 /
    #        kt ControllerWifiSoftVersion), sent on H6047 (PairAcV1:257, Compose4InfoBleIot) and the gt-64 bulbs
    #        (ControllerStrategyConfig.h0:634-647). Reply ASCII from frame byte 2; H6006/H6008 and H60A1/H60A6
    #        (ble_msg_get_wifi_swversion) return the same string as AA 06; H6047 handler 0x0100510e.
    #   0xef pact: BleProtocolConstants.SINGLE_PACT. AA EF reply [pact_type u2be][pact_code u1]; H6006 answers 00 01 01
    #        (0x9b03cc00), as do H60A1/H60A6 (0x9b045558 / 0x9b04a980); H5083 answers 00 02 02 (0x9b031b80); H6008
    #        does not answer. Not sent on the gt-64 control screen (see read_request).
    0x23: new_timer
    0x24: read_ic_point           # ALSO value_set_light_start (36): SetLightStartController 33 24 [max(index,1), u16be time], sent from the connect/bind steps. Firmware-sourced: on H6008 ('pair_effect_thread', FUN_9b029d70) and H60A1/H60A6 (govee_device_start_tmp_mode(0x24)) this is the pairing light effect, not an IC read
    0x25: preset_color            # BleProtocolConstants.SINGLE_PRE_SET_COLOR (37) = value_set_light_end. Firmware-sourced: on H6008 ('msg_set_dev_lamp_stop', FUN_9b029de8) and H60A1/H60A6 (33 25 01 -> MSG_STOP_PAIR_MODE) it stops the 0x24 light effect, not a preset colour (FIRMWARE_VALIDATION.md §2)
    0x26: take_photo_or_colortemp_type
    0x30: light_direction_or_zone
    0x31: camera_position
    0x32: check_camera
    0x34: swap_light
    0x36: compose_light_switch
    0x40: ic_num
    0x41: on_off_memory    # ALSO H6062 (striplight) `aa 41` section-count read: reply [IC s16be, sectionNum u8] (ControllerIcSegmentNum); hand-decoded in wire/parse.py parse_section_count, not modelled as a reply type here
    0x44: calibration
    0x46: check_ic                # ALSO SINGLE_RESET_DEVICE_NET (70). CAUTION, firmware-sourced: on H60A1/H60A6, 33 46 is a factory reset + reboot (Govee_GatewayConfig_Reset, HAL_Matter_Reset, 'Device rebooting ...'; H60A6 D60A6:454-468, H60A1 D60A1:389-400). Never send it to them as an IC check
    0xa2: bulb_string_color_read
    0xa3: gradual_wifi_ble
    0xa5: local_color_read
    0xa9: white_balance
    0xaa: check_light
    0xab: dynamic_api_support    # BleProtocolConstants.SINGLE_DYNAMIC_API_SUPPORT / MULTI_READ_AB (-85). Firmware-confirmed: H6006/H6008/H60A1/H60A6 all dispatch on it (FIRMWARE_VALIDATION.md §2). NOTE: distinct from pro_type 0xab (bbq_status, byte 0).
    0xb1: secret_read
    0xb2: secret_write
    0xb5: plug_sync_time
    0xee: ota_prepare
    # settings / timers / misc (added; overloaded bytes resolved by device context)
    0x0a: auto_time
    0x13: night_mode
    0x16: light_indicator
    0x17: wifi_link_start
    0x27: preview_effect
    0x28: delete_scene
    0x29: sort_scene
    0x33: volume
    0x35: without_interrupt
    0x37: prompt_tone
    0x39: check_direction
    0x42: write_check_ic          # Firmware-sourced, H60A6 (0x9b0269fe): calibration mode (33 42 01 / 02 xx / FF / 00, app CalibrationAcH60A6; no head check)
    0x43: check_ic_amount
    0x50: movie_feast_set_device
    0x54: movie_feast_on_off
    0x60: feast_carousel
    0x62: combination_effect
    0x63: daysync_op
    0x84: set_alarm_clock
    0xb0: plug_delay
    0xb3: plug_spec
    0xb4: plug_timer
    0xed: notify_recognition
    # music library (0x70-0x7A)
    0x70: music_lib_notify
    0x71: music_lib_player_state
    0x72: music_lib_preset_info
    0x73: music_lib_preset_color
    0x74: music_lib_preset_scenes
    0x75: music_lib_set_play_state
    0x76: music_lib_preview_effect
    0x77: music_lib_remove_scene
    0x78: music_lib_sort_scenes
    0x79: music_lib_volume
    0x7a: music_lib_switch

  sub_mode:
    # Byte after mode command 0x05. NOTE: values are device-specific; these are the
    # common assignments. 0x0b is 'color' on some families and 'game' on H6057.
    0x00: video
    0x02: color_legacy
    0x04: scene
    0x05: mic
    0x0a: diy
    0x0b: color_rgbic_0b
    0x0c: music_alt
    0x0d: color_cct_0d
    0x0e: music_string
    0x0f: music_lamp
    0x11: music_dreamcolor
    0x13: music
    0x15: color_rgbic_15

  handshake_op:
    0x01: v1_request_session_key   # AES (Controller4Aes)
    0x02: v1_confirm
    0x11: v2_single                # AES-GCM single-packet (Controller4AesGcm)
    0x19: v2_request_session_key   # AES-GCM
    0x1a: v2_confirm

  op15:
    # Byte after color sub-mode 0x15 (SubModeColorV1 OP_TYPE_*).
    0x00: set_mode
    0x01: set_color            # basic RGB, H60A1/H60A6 RGB, or H60A1/H60A6 CCT
    0x02: set_brightness       # per-segment brightness
    0x03: set_brightness_group
    0x04: set_color_group
    0x05: set_color_temp       # basic kelvin

  notify_sub:
    # Byte after the 0xEE notify header (device -> app push). SHARED across all device categories. The curated
    # LIGHTING SKUs register only 0x11 + 0x20 + 0x30 (ShareVM.x4:2425 = wifi; per-SKU VM overrides add brightness
    # + switch/zone). The rest fire for OTHER categories (sensor/TV/plug/camera); their DefParser parser is noted.
    0x01: light_status         # legacy inner code under the old 0x30 scheme (BleProtocolConstants); no standalone newdetail parser
    0x02: energy_saving
    0x03: battery
    0x04: music
    0x05: pressure_or_volume   # H5130 current pressure; volume detail on some devices
    0x11: wifi_connect         # DefParser.Q:94 -> status@data[0], 0=connected  => notify_wifi  (curated: ShareVM.x4)
    0x20: brightness           # DefParser.p:200 -> level@data[0]               => notify_level (curated: per-SKU VM)
    0x26: take_photo_exit
    0x30: device_info_or_zone  # DefParser.M/O:81 + H60A6 U5:336                 => notify_switch_zone (curated: per-SKU VM)
    0x40: device_status        # DefParser.v:219 nested selector@data[0]: 0=bool@1 / 1=u8@1 / 2=bool@1 / 3=signed-u16@1-2 / 4=bool@1
    0xed: recognition          # outbound 0x33 ED writer + state-event only; NO inbound decoder exists in the app

types:
  # ── proType 0x33 / 0xAA : single command ────────────────────────────────
  single_command:
    seq:
      - id: command
        type: u1
        enum: command
      - id: params
        size-eos: true
        type:
          switch-on: command
          cases:
            'command::mode': mode_payload
            'command::switch': switch_payload
            'command::brightness': brightness_payload
            'command::sync_time': sync_time_payload
            'command::plug_sync_time': plug_sync_time_payload
        doc: Unmatched commands parse as a raw byte array (payload up to 17 bytes).

  # 33 36 bar-write body, modeled as a STANDALONE type (not a single_command case on purpose: existing
  # clients read a 0x36 frame's `params` as raw bytes, so the frame-switch default must stay raw for 0x36).
  # Parse a 0x36 write body explicitly with this type when you want the fields.
  bar_switch_write:
    doc: >-
      33 36 bar on/off write (H6047 two-bar lights): body = [left, right] (Controller4ExtBytes
      [0x36, left, right], NewDetailVm.D5; confirmed byte-exact vs the app). Same [left, right] the AA 36
      reply echoes (bar_switch_read); the reply's 3rd byte (movie/feast switch) is read-only, not part of
      the write. left/right are logical positions (swappable at setup, h6047/add/LightPosAc), not fixed bars.
    seq:
      - { id: left, type: u1, doc: "1 = left bar on, 0 = off" }
      - { id: right, type: u1, doc: "1 = right bar on, 0 = off" }

  # ══════════════ STANDALONE WRITE BODIES (not wired into any switch-on dispatch) ══════════════
  # Each type is ONLY the bytes after the header the client keeps in code ([proType, command(, sub-mode, op)]);
  # the zero padding to byte 18 and the BCC at byte 19 stay in code (Kaitai cannot compute the BCC). A parser
  # cannot pick these from the header alone (0x30, 0x36, op15 0x01/0x04 and 0x0b are each shared by several
  # bodies), so the client chooses by device family / the write it is issuing. Every layout and cap was traced
  # in decomp-7.7.02 and adversarially re-checked; each round-trips the catalogue builders through the Kaitai
  # 0.11 read-write classes. Wiring them into single_command / color_15 / read_command waits on the library.

  # 33 30 per-zone power write body, STANDALONE (not a single_command case: command 0x30 is overloaded per
  # family, see light_direction_write). Header [0x33, 0x30] + zero padding + BCC stay in code.
  zone_power_write:
    doc: >-
      33 30 per-zone on/off write (pact_h60a0 family): body = [zone, state]. Built as
      Controller4ExtBytes.Companion.f(48, [zone, state]) (base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:121-124).
      H60A6 VM4LightH60A6.y5 (split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6.java:1373-1376) writes
      [!isFirst ? 1 : 0, on], called with isFirst=false (zone 1, DelegateHeaderH60A6.java:442) and isFirst=true
      (zone 0, DelegateHeaderH60A6.java:470); H1270 VM4LightH1270.F5 (h1270/VM4LightH1270.java:672-675) and H1250
      VM4LightH1250.n5 (h1250/VM4LightH1250.java:911-914) are identical; H12D0 VM4LightH12D0.B5
      (h12d0/VM4LightH12D0.java:618-622) takes the caller's array [2|1|0, on] (DelegateHeaderH12D0.java:580,608,636),
      so H12D0 has three zones. The snapshot path writes one frame per zone, [0, zone0_on] then [1, zone1_on]
      (VM4LightH60A6.java:1215-1216; H12D0 adds [2, zone2_on], VM4LightH12D0.java:905-907). The zone states come
      back in the 0xAC status TLV 0x30 (status_zone) and the EE 30 push (notify_switch_zone). The frame for
      [1, 0] is byte-identical to light_direction_write value=1 (33 30 01 00 .. 02): pick by device family.
    seq:
      - { id: zone, type: u1, doc: "zone index: 0, 1 (H60A6/H1270/H1250), or 2 (H12D0 only)" }
      - { id: state, type: u1, doc: "1 = zone on, 0 = off (id 'on' avoided: YAML-boolean)" }

  # 33 30 single-byte write body, STANDALONE. Same command byte as zone_power_write; the family decides.
  light_direction_write:
    doc: >-
      33 30 single-byte write: body = [value]. Command 0x30 is family-dependent. H6038 installation/calibration
      direction: CalibrationController.Companion.c(b) = Controller4ExtBytes.d([48, b])
      (split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:42-45), b = the index of
      the selected direction image (6 choices, H6038CalibrationAc.java:185-208,280-287; the send button is
      enabled only after a pick), sent from VM4H6038.N5 (VM4H6038.java:809-815, called at
      H6038CalibrationAc.java:316) or the add-device flow (H6038CalibrationAc.java:292-298). The compose
      DirectionController.b(b) builds the same [b] (base-classes10/com/govee/base2light/ble/controller/compose/DirectionController.java:34-36,60-61;
      DefWriteComposeController4Single.java:137-149 adds 33 44 02 00 in g), but nothing in this decomp calls it.
      On H6057 the same [value] is the MAIN-LIGHT SWITCH, not a direction: ControllerSingle.c(command_light_switch = 48,
      [on ? 1 : 0]) (split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:950-956;
      h6057/origin/ble/BleProtocol.java:7). On H71xx ReportChartController writes [1]
      (base-classes12/com/govee/base_h71xx/sku_base/ble/controller/ReportChartController.java:24-31).
    seq:
      - { id: value, type: u1, doc: "H6038: direction index 0-5; H6057: 1 = main light on, 0 = off; H71xx: 1 (chart report request)" }

  # 33 A3 / 33 14 gradual write body, STANDALONE. Same body under both command bytes.
  gradual_write:
    doc: >-
      Gradual (fade between colours) on/off write: body = [state]. Two command bytes, chosen by the device's
      GradualVersion (Config M table: 0 -> 0xA3, 1 -> 0x14, Config.java:5602-5613): 0xA3 = Bytes.w
      (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:564-566) and 0x14 = Bytes.v
      (Bytes.java:559-560), each [cmd, on ? 1 : 0] wrapped by Controller4ExtBytes.d
      (split_rgbiclight/com/govee/rgbiclight/mode/color/ColorControllerConfig.java:507-510). The same [on ? 1 : 0]
      comes from ControllerGradual (cmd 0x14, base-classes11/com/govee/base2light/kt/general_controller/ControllerGradual.java:60,71-72),
      pact_h60a0 compose GradualController (cmd 0xA3, split_h60ax/com/govee/pact_h60a0/ble/controller/compose/GradualController.java:24,42-43),
      the legacy GradualController (cmd 0x14, split_rgbiclight/com/govee/dreamcolorlightv1/ble/GradualController.java:13-16,22-24,31-33),
      and the night-mode page (33 14 01 / 33 14 00, NightModeViewModel.java:86-114, where it means enter/leave
      night mode). The AA A3 reply is gradual_read.
    seq:
      - { id: state, type: u1, doc: "1 = gradual on, 0 = off (id 'on' avoided: YAML-boolean)" }

  # 33 B2 account-lock secret write body, STANDALONE.
  secret_key_write:
    doc: >-
      33 B2 account-lock secret write: body = the 8-byte secret, NO selector byte. Every builder writes
      Encode.decryByBase64(secretCode) as the whole ext array: SecretKeyController.q
      (base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:22-26,71-78; command
      SINGLE_CHECK_SECRET_KEY = -78, BleProtocolConstants.java:110), h5086 SecretController.d
      (split_plugnew/com/govee/h5086/ble/controller/SecretController.java:66-75,86-88), h61d3 SecretController.d
      (split_h61d3/com/govee/h61d3/ble/controller/SecretController.java:67-76,87-89) and Controller4Secret.a
      (base-classes12/com/govee/base2light/vm4sku2op/Controller4Secret.java:28-46; Controller.x = -78). The
      secretCode is the base64 of the 8 bytes the AA B1 reply carried after its 0x01 selector
      (SecretKeyController.java:57-62; h5086 SecretController.java:41-44), so it decodes to 8 bytes. The write
      path does not check the length. The AA B1 reply is secret_read_reply.
    seq:
      - { id: secret, size: 8, doc: "8-byte account-lock token, as read from AA B1 (base64 off-wire). Not the wire cipher key." }

  # 33 36 is family-dependent too: H6047's 2-byte bar switch (bar_switch_write above), the 2-byte
  # [index][state] form of H6043/H6038/H6048/H60B2/H1771/H60A0 (bar_switch_write parses it; read its fields
  # as index/state), and H6057's 7-byte worry-free-at-night body below. Pick by device family.
  worry_free_at_night_write:
    doc: >-
      33 36 H6057 worry-free-at-night write: body = WorryFreeAtNightBean.o() = [state, open hour, open minute,
      close hour, close minute, sensitivity, brightness]
      (base-classes10/com/govee/base2light/ble/controller/WorryFreeAtNightBean.java:90-92; getters g/d/e/b/c/f/a
      return fields a/b/c/d/e/f/g, :34-59), sent as ControllerSingle.Companion.c(54, bytes)
      (split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:846-850; legacy
      split_hollowlamp/com/govee/h6057/origin/ble/WorryFreeAtNightController.java:76-78,87-93). A 7-byte body
      under 33 36 is this, not a bar switch. Catalogue: build_worry_free_at_night.
    seq:
      - { id: state, type: u1, doc: "1 = enabled, 0 = disabled (bean field a)" }
      - { id: open_hour, type: u1 }
      - { id: open_min, type: u1 }
      - { id: close_hour, type: u1 }
      - { id: close_min, type: u1 }
      - { id: sensitivity, type: u1, doc: "bean 'progress' (field f)" }
      - { id: brightness, type: u1 }

  # ─────────── STANDALONE op15 WRITE bodies (bytes AFTER the in-code header "33 05 15 <op>") ───────────
  # Not wired into color_15 / any switch-on: the client builds the header, the zero padding and the BCC
  # (BleUtils.p, base-classes10/com/govee/base2kt/utils/BleUtils.java:1030-1041) and invokes these with
  # the parameters it holds. Existing op15_color / op15_color_typed / op15_brightness are unchanged.
  op15_color_group_write:   # op 0x04 — [group][R,G,B x color_count]
    doc: |
      Group colour write body after "33 05 15 04". The app writes [0x15, 0x04, group, RGB x n] with
      n = min(4, colours given) and NOTHING after the colours (the ext array is sized 3 + 3n exactly):
        Bytes.m   base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:393-402
        SubModeColorV1 opType 7   split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:711-719
        colours: BleUtils.w0(colorSet, 4) base-classes10/com/govee/base2kt/utils/BleUtils.java:1271-1283
          (coerceAtMost(4, len) colours, 3 bytes each, R,G,B from ColorUtils.n).
        h6062 striplight SubModeColor.getWriteBytes split_h6062/com/govee/striplight/ble/SubModeColor.java:
          340-359 writes the same body (up to 4 colours, then the 17-byte array's zeros).
      Op 0x04 is NOT only this body: Bytes.D (Bytes.java:133-139) writes [0x15, 0x04, kelvin u2be] for
      colour-instruction version 9 (COLOR_MODE_0X15_0X04). The client picks this type from the write it is
      issuing, never from the op byte.
      A whole-device write is ceil(N/4) such frames, each colour set chunked by 4; the LAST chunk carries
      N % 4 colours (4 when N % 4 == 0), so color_count is 1..4 and is not in the stream (a black colour
      00 00 00 cannot be told from the zero padding): the client passes it.
      group = start + chunk index (0-based chunk index):
        new-detail path ColorModeControllerConfig.l (base-classes11/.../fuc/color/ColorModeControllerConfig.java:
          871-897, group = i + i8 at :895), start = LightColorCapabilityCenter.M0 = colorBrightnessGroupMode
          raw value (base-classes10/com/govee/base2light/capability/color/LightColorCapabilityCenter.java:
          866-873); ColorBrightnessGroupMode GROUP_INDEX_FROM_0(0) / GROUP_INDEX_FROM_1(1)
          (base-classes12/com/govee/common2light/capability/color/ColorBrightnessGroupMode.java:13-14).
          The hard-coded fallback (Config.N -> Config.A, base-classes11/.../fuc/color/Config.java:412-423,
          map built at :3118-3314) returns 1 for every one of its 39 goodsTypes, so groups are 1-based
          unless a cloud capability spec says GROUP_INDEX_FROM_0.
        H60A0/H60A1 SubModeColorV1.Companion.e/f (:176-197, :250-274): l(i7 + 1, ...) -> always 1-based
          (H60A0: 13 colours -> groups 1..4 with 4,4,4,1 colours).
        h6062 striplight SubModeColor.makeSubModeColor (SubModeColor.java:73-100, 124-150): f = i + 1 ->
          always 1-based.
    params:
      - { id: color_count, type: u1, doc: "colours in this group, 1..4 (w0 coerceAtMost 4); NOT in the stream" }
    seq:
      - id: group
        type: u1
        valid: { expr: 'color_count >= 1 and color_count <= 4' }
        doc: "group index = start (0 or 1 from colorBrightnessGroupMode; 1 for all hard-coded devices, H60A0/H60A1 and the h6062 family) + chunk index; the valid enforces the color_count param range (1..4)"
      - { id: colors, type: sc_rgb, repeat: expr, repeat-expr: color_count, doc: "colours of positions 4*chunk .. 4*chunk+color_count-1, R,G,B each" }

  op15_color_write:         # op 0x01 — every colour form, keyed by the client-held variant
    doc: |
      Colour write body after "33 05 15 01", parametric on the form (variant) and the segment-mask width.
      The forms share op byte 0x01 with no in-frame discriminator (chosen by device family + colour mode):
        variant 1  basic            [r,g,b][seg_mask]
                   SubModeColorV1 opType 1, split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:684-694
        variant 12 H60A1/H60A6 RGB  [r,g,b][00 x5][seg_mask]
                   Bytes.h base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:315-326;
                   SubModeColorV1 opType 12 :734-744
        variant 11 CCT              [slot r,g,b][kelvin u2be][tint r,g,b][seg_mask]
                   Bytes.r Bytes.java:474-501, slot by kind i5 (:480-486): kind 0 -> 00 00 00 (ColorUtils.F());
                   kind 3 -> 00 00 00 when every position is set, else slot = tint with kelvin 0 and tint
                   00 00 00 (:492, :495; an RGB write); any other kind (1, the usual one) -> ff ff ff (R()).
                   SubModeColorV1 opType 11 :720-733 (slot = ColorUtils.R() = ff ff ff, tint = tempRgb).
                   The family writers use the same layout with the user colour in the slot and kelvin / tint
                   left 0 for RGB (byte-identical to variant 12 then): h6047 SubModeColor.getWriteBytes
                   split_h6047/com/govee/h6047/ble/SubModeColor.java:184-200; h6062 striplight
                   SubModeColor.getWriteBytes split_h6062/com/govee/striplight/ble/SubModeColor.java:376-405,
                   SubModeColorV2.j :570-587, SubModeColorV3.OpColor.makeBytes/e :63-85, 136-141.
        variant 5  colour-instruction version 5 (COLOR_MODE_0X15_NO_TEMP_RGB, Config.J[5] = Config.Zt,
                   Config.java:5527-5531, 13124-13131), e.g. H6061 (goodsType 51):
                   RGB    Bytes.l Bytes.java:379-390 -> [r,g,b][00 00][seg_mask]
                   Kelvin Bytes.u Bytes.java:541-556 -> [tint r,g,b][kelvin u2be][seg_mask]
                     (kelvin = Constant.getColorTemKelvin(tint)); RGB is the kelvin == 0 case.
      seg_mask: LSB-first, position 8j+k at bit k of byte j (BleUtils.X, base-classes10/com/govee/base2kt/
      utils/BleUtils.java:406-438). Width: 2 bytes (makeSelectedTwoBytes, BleUtils.java:444-494 /
      base-classes10/com/govee/base2light/ble/BleUtil.java:412-443; 00 00 if the set is empty) when the set
      has <= 16 positions, else ceil(n/8) (BleUtils.X). Bytes.* key it on posSet.length > 16; SubModeColorV1
      on its segment count > 16 (:672); h6047 on its segment count > 16 (:199). The h6062 writers size it
      otherwise (SubModeColorV2.j: BleUtil.makeBytes4SelectPosByOneBit ceil(n/8), BleUtil.java:383-398, with
      no 2-byte floor, fed 24 positions by NumUtil.makeSelectByByteNum(list, 3) at :151); a 1-byte mask is
      sent as mask_width 2 with a 00 high byte, which gives the same zero-padded frame.
      Cap = 17 - (ext bytes before the mask, sub-mode and op included):
        variant 1  mask at ext[5]  -> 12 bytes (96 positions). No coerceAtMost: the ext goes uncut to
                   BleUtils.p via the h60ax ModeController (compose/AbsController.java:24-26, not the kt
                   AbsController.q); a 13th byte lands on frame[19] and is overwritten by the BCC, 14+ throws.
        variant 12 mask at ext[10] -> 7 bytes (56 positions): Bytes.h coerceAtMost(len, 17 - 10) :324 drops
                   the rest silently; SubModeColorV1 opType 12 has no coerce (8th byte -> BCC, 9+ throws).
        variant 11 mask at ext[10] -> 7 bytes: Bytes.r coerceAtMost(len, 17 - 10) :499; h6047 min(len, 7)
                   :200; h6062 SubModeColor indexes a 17-byte array (ArrayIndexOutOfBounds past 56 positions).
        variant 5  mask at ext[7]  -> 10 bytes (80 positions): Bytes.l :388 / Bytes.u :554 copy uncoerced
                   into a 17-byte array, so 11+ bytes throws ArrayIndexOutOfBounds (no frame).
      The frame-path op15_color (size-eos) and op15_color_typed (2-byte mask; variants 1/12/11) stay as is.
    params:
      - { id: variant, type: u1, doc: "1 basic / 12 H60A1-H60A6 RGB / 11 CCT / 5 colour-instruction version 5 (NOT SubModeColorV1 opType 5, set by Companion.k :338-346, which has no getWriteBytes branch)" }
      - { id: mask_width, type: u1, doc: "seg_mask bytes: 2 for <= 16 positions, else ceil(n/8); 2..mask_cap" }
    seq:
      - { id: r, type: u1, doc: "colour R; variant 11: slot R (ff = ColorUtils.R(), 00 = F(), the colour for a Bytes.r kind-3 partial write or an h6047/h6062 write); variant 5 Kelvin form: tint R" }
      - { id: g, type: u1 }
      - { id: b, type: u1 }
      - { id: reserved, contents: [0, 0, 0, 0, 0], if: 'variant == 12', doc: "Bytes.h leaves ext[5..9] zero; SubModeColorV1 opType 12 writes five (byte) 0 (:736). The writer must set it to 5 zero bytes" }
      - { id: kelvin, type: u2be, if: 'variant == 11 or variant == 5', doc: "big-endian (BleUtils.D(k, true) BleUtils.java:132-142 / BleUtil.getSignedBytesFor2(k, true) BleUtil.java:234-243); 0 = RGB (Bytes.l; Bytes.r kind-3 partial)" }
      - { id: tint_r, type: u1, if: 'variant == 11' }
      - { id: tint_g, type: u1, if: 'variant == 11' }
      - { id: tint_b, type: u1, if: 'variant == 11' }
      - id: seg_mask
        size: mask_width
        valid: { expr: '_.length >= 2 and _.length <= mask_cap and (variant == 1 or variant == 5 or variant == 11 or variant == 12)' }
        doc: "LSB-first segment mask (BleUtils.X order), mask_width bytes: at least 2 (makeSelectedTwoBytes), at most mask_cap; the valid also rejects an unknown variant"
    instances:
      mask_cap:
        value: 'variant == 1 ? 12 : variant == 5 ? 10 : 7'
        doc: "most mask bytes the app puts on the wire for this variant: 17 - ext bytes before the mask (5 / 7 / 10)"
      is_cct:
        value: '(variant == 11 or variant == 5) and kelvin != 0'
        doc: "kelvin != 0 (the ksy's is_cct convention): Bytes.u vs Bytes.l's 00 00, and Bytes.r kinds 0/1/3-full vs the kind-3 partial and h6047/h6062 RGB writes, which carry kelvin 0"

  # ── STANDALONE wide-mask / 0x0b write bodies (NOT reached from any switch-on; the client invokes them directly) ──
  # Each is ONLY the bytes after the header the library keeps in code ([proType 0x33, command 0x05, sub-mode(, op)]);
  # the zero padding to byte 18 and the BCC at byte 19 stay in code.
  op15_brightness_wide:   # op 0x02 WRITE after 33 05 15 02 — <pct> <seg_mask, mask_width bytes>
    doc: |
      Per-segment brightness write body after the header 33 05 15 02 (Bytes.G
      base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:172-181), reached via
      Config.JR (Config.java:10567-10570; Config.L[0] :5591-5595, L = map16 :5601, read by Config.D() :223-225)
      for RelativeBrightnessVersion RELATIVE_BRIGHTNESS_0X15(0)
      (base-classes12/com/govee/common2light/capability/color/RelativeBrightnessVersion.java:14) from
      ColorModeControllerConfig.t :1069-1097, and directly from ColorModeControllerConfig.z :1184-1195 (one
      frame per distinct brightness; posSet length = piece count, BleUtils.r :1077-1097). The app builds a
      17-byte ext array [0x15, 0x02, pct, mask...] (ControllerMode cmd 0x05; AbsController.q :47-60 truncates
      ext to 17; BleUtils.p :1030-1041 frames it). Mask (Bytes.java:178): makeSelectedTwoBytes (BleUtils.Y,
      always 2 bytes) when posSet has <= 16 entries, else BleUtils.X (ceil(n/8) bytes), copied at ext[3]
      with coerceAtMost(len, 14) (Bytes.java:179; 14 = 17 - 3), so positions 112+ are silently dropped.
      mask_width is therefore 2 for <= 16 pieces, else min(ceil(pieces/8), 14); never 0 or 1. pct is written
      raw ((byte) i, Bytes.java:177, no clamp). Same body as legacy split_h6047/com/govee/h6047/ble/
      SubModeColor.java:173-182 (Math.min(len, 14) at :181) and SubModeColorV1 opType 0
      split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:672-683 (no cap there; the legacy writers
      key the width on their segment count, not the posSet length). Standalone because color_15's switch
      reaches the fixed-u2le op15_brightness and cannot pass mask_width; with mask_width 2 this is
      byte-identical to op15_brightness, which stays as is.
    params:
      - { id: mask_width, type: u1, doc: "2 for <= 16 pieces (makeSelectedTwoBytes), else min(ceil(pieces/8), 14) (BleUtils.X; Bytes.java:179 coerceAtMost 14)" }
    seq:
      - { id: pct, type: u1, doc: "0-100, written raw (Bytes.java:177)" }
      - { id: seg_mask, size: mask_width, doc: "LSB-first (BleUtils.X/Y order): position 8j+k at bit k of byte j" }

  color_rgbic_0b_wide:    # sub-mode 0x0b WRITE after 33 05 0b — <R,G,B> <seg_mask, mask_width bytes>
    doc: |
      0x0b RGBIC colour write body after the header 33 05 0b, ColorInstructionVersion COLOR_MODE_0X0B(3)
      (base-classes12/com/govee/common2light/capability/color/ColorInstructionVersion.java:16; Bytes.d
      base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:270-281 via Config.cu
      Config.java:13616-13620, Config.J[3] :5542-5546, J = map14 :5582, read by Config.u() :731-733 from
      ColorModeControllerConfig :265-279). The app builds a 17-byte ext array [0x0b (BleProtocol
      music_code_yuedong = 11), R, G, B, mask...] (ControllerMode cmd 0x05; AbsController.q truncates ext to
      17). Mask (Bytes.java:278): makeSelectedTwoBytes (always 2 bytes) when posSet has <= 16 entries, else
      BleUtils.X (ceil(n/8) bytes), copied at ext[4] with coerceAtMost(len, 13) (Bytes.java:279; 13 = 17 - 4),
      so positions 104+ are silently dropped. mask_width is therefore 2 for <= 16 pieces, else
      min(ceil(pieces/8), 13); never 0 or 1. With mask_width 2 this is byte-identical to color_rgbic_0b
      (u2le), which stays as is. NOT the colour-version-6 kelvin form (color_0b_kelvin below). Transport
      note: ColorModeControllerConfig.j :689-712 sends the same 17-byte ext ([0x0b, body, zero pad]) as the
      payload of an 0xA3 multi-packet of type 0x6e (MultiController.java:48-61) followed by 33 05 15, so this
      body can also follow the 0x0b byte inside that A3 payload rather than a 33 05 0b header.
    params:
      - { id: mask_width, type: u1, doc: "2 for <= 16 pieces (makeSelectedTwoBytes), else min(ceil(pieces/8), 13) (BleUtils.X; Bytes.java:279 coerceAtMost 13)" }
    seq:
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }
      - { id: seg_mask, size: mask_width, doc: "LSB-first (BleUtils.X/Y order): position 8j+k at bit k of byte j" }

  color_0b_kelvin:        # sub-mode 0x0b colour-version-6 WRITE after 33 05 0b — fixed 7 bytes
    doc: |
      0x0b colour write body after the header 33 05 0b, ColorInstructionVersion COLOR_MODE_0X0B_KEVIN(6)
      (ColorInstructionVersion.java:19; Bytes.c
      base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:239-267 via Config.eu
      Config.java:13930-13934, Config.J[6] :5552-5556). NOT the color_rgbic_0b / color_rgbic_0b_wide shape:
      an 8-byte ext array [0x0b, R, G, B, kelvin hi, kelvin lo, m0, m1], no mask helper and no width choice.
      m0 bit k = position k (0-7, :251-258), m1 bit k = position 8+k (8-14 only, :259-265): position 15+ is
      never written, so bit 15 of seg_mask is always 0, and a posSet shorter than 15 entries throws
      ArrayIndexOutOfBounds in the app. kelvin = KelvinConfig.c(colour, info) (base-classes11/com/govee/
      base2light/pact/newdetail/config/fuc/KelvinConfig.java:119-122) = Constant.getColorTemKelvin(colour,
      min, max) (base-classes9/com/govee/base2home/Constant.java:591-594): the colour's Kelvin when it is a
      tint, else the low end of the device kelvinRange (not 0), big-endian (BleUtil.getSignedBytesFor2(k, true),
      Bytes.java:248-250). So kelvin != 0 does not mean CCT here. Like color_rgbic_0b_wide, the same 8-byte
      ext can instead travel as an 0xA3 type-0x6e multi-packet payload (ColorModeControllerConfig.j :689-712).
    seq:
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }
      - { id: kelvin, type: u2be, doc: "big-endian (Bytes.java:248-250)" }
      - { id: seg_mask, type: u2le, valid: { max: 0x7fff }, doc: "positions 0-14 only, bit i = position i (Bytes.java:251-265); bit 15 never set (mask the caller's value with 0x7fff, as build_color_0b_kelvin does)" }

  # ── 0xAA read REQUEST body: STANDALONE write-side type (NOT a case of read_command, which models the REPLY) ──
  read_request:
    doc: |
      0xAA read REQUEST body = the bytes after the 0xAA header: [command][selector...]. The library keeps the
      0xAA byte, the zero padding through byte 18 and the XOR BCC at byte 19 in code. Every app read frame is
      BleUtils.Companion.p(0xAA, getCommandType(), ext): byte 1 = command, ext copied from byte 2 only when
      non-null and non-empty, the rest zero, BCC at 19 (base-classes10/com/govee/base2kt/utils/BleUtils.java:1030-1041;
      generate20Bytes :1376-1378). Legacy controllers pass AbsSingleController.p()
      (base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:17-20, default null :54-56;
      read path AbsController.java:77 getValue -> f()); kt controllers pass their ctor ext
      (base-classes11/com/govee/base2light/kt/ble/AbsController.java:42-45), truncated to 17 bytes (q() :47-60).
      Selectors as the app sends them (body hex; legacy = base-classes10/com/govee/base2light/ble/controller/,
      kt = base-classes11/com/govee/base2light/kt/general_controller/):
        01       switch. EMPTY: legacy SwitchController() has no p() (SwitchController.java:9-11);
                 kt ControllerSwitch.Companion.b -> ext null (ControllerSwitch.java:38-47,69-71).
        04       brightness. EMPTY: legacy BrightnessController() (BrightnessController.java:10-12, no p());
                 kt ControllerBrightness.Companion.b -> ext null (ControllerBrightness.java:40-48,69-71).
        05 01    mode: legacy AbsModeController.p() = {1} (AbsModeController.java:34-37); kt ControllerMode.Companion.b
                 -> synthetic ctor mask 2 -> default ext {1} (ControllerMode.java:45-47,107-109);
                 ModeControllerNoEvent read (ModeControllerNoEvent.java:37-38). Only bare aa 05: the H60A0
                 compose ModeController.Companion.a, ext null
                 (split_h60ax/com/govee/pact_h60a0/ble/controller/compose/ModeController.java:26-28,38-44).
        06       soft version. EMPTY: legacy SoftVersionController() (SoftVersionController.java:9-11, no p());
                 kt ControllerSoftVersion() -> ext byte[0] (ControllerSoftVersion.java:16-18,27-29).
        07 ss    device info, ss: 02 SN (SnController.java:37-38), 03 hard version (HardVersionController.java:28-29;
                 kt ControllerHardVersion.java:28 default {3}), 04 soft version (SoftVersionInDeviceInfoController.java:18-19),
                 07 DSP (DspVersionInDeviceInfoController.java:20-21), 0A / 0B MCU sw / hw
                 (McuSoftVersionControllerV1.java:32-33, McuHardVersionControllerV1.java:32-33),
                 10 basic info (BasicInfoController.java:34-35), 11 basic Wi-Fi info (BasicWifiInfoController.java:42-43).
                 These are the selectors the app's controllers define. Live senders: 02 and 03 (H6047
                 PairAcV1:257; the H60Ax add flow, ConnectWithSecretDialog.m0:312-313; the h5080 / H5083 add flow,
                 PairAcV1.t2, split_plugnew/com/govee/h5080/add/PairAcV1.java:652; 03 is also kt
                 ControllerHardVersion in the default Compose4InfoBleIot strategy); 03 / 04 from split_h61d3
                 PairToDeviceInfoReadController (SoftHardController). 07 / 0A / 0B come only from the generic
                 connect step (ReadControllerFactory.a / .b, picked in StepReadInfo:211). BasicInfoController
                 (07 10) and BasicWifiInfoController (07 11) are never constructed in decomp-7.7.02 (no caller
                 outside their own files); H5086 sends 07 10 / 07 11 through its own DeviceBasicInfoController /
                 WifiInfoController (split_plugnew h5086 PairToDeviceInfoReadController:240,245).
                 Firmware-sourced, what each image answers (AA 07 data starts at frame byte 3):
                   H6047 BLE 1.00.12 (handler 0x01005370): 02, 03, 04 and 06. 00, 01, 05, 07, 0A, 0B, 10, 11
                     and above get NO reply (sel-2 > 4 returns, 0x01005396).
                   H5083 SW1.00.30 (ble_msg_get_dev_info @0x9b031b08): only 02, 03 and 06. Every other selector
                     is forwarded to the SKU dispatcher and dropped with no reply (0x9b032cc4 -> 0x9b01e2ac).
                   H6006 / H6008 (ble_msg_get_dev_info 0x9b03cb88 / 0x9b03c92c): 02, 03 and 06. H6008 also
                     answers 0A / 0B with no data, so the reply echoes the request. Every other selector,
                     including 04, 07, 10 and 11, gets no reply: the handler returns -1, the frame is forwarded,
                     and the dispatcher drops it (H6006 0x9b01f1f2, H6008 0x9b024560).
                   H60A1 / H60A6 (_handle_write_msg -> ble_msg_get_dev_info, H60A1.bin@0x9b0454bc,
                     H60A6.bin@0x9b04a8e4): 02 -> 8-byte id at frame bytes 3..10, byte-reversed from the
                     device's uuid string; 03 -> ASCII; 06; 0A / 0B -> no data (the request is echoed). 04, 07,
                     10 and 11 have no handler: they are forwarded to the SKU dispatcher, which echoes the
                     request unchanged.
                 06 is the 6-byte BLE MAC at frame bytes 3..8 (byte order per image: see device_info_read); no
                 app controller sends 07 06.
                 So 07 10 / 07 11 have no handler on any verified image: dropped with no reply on H6006 / H6008 /
                 H5083 / H6047, echoed all-zero on H60A1 / H60A6.
        14       Wi-Fi MAC. EMPTY: legacy WifiMacController (f = 20, WifiMacController.java:10), kt
                 ControllerWifiMacAddress (k = 20, ControllerWifiMacAddress.java:29). Sent on H6047 (PairAcV1:257,
                 Compose4InfoBleIot), the gt-64 bulbs (bulblight ControllerStrategyConfig.h0:648-654) and the
                 H60Ax add flow. Reply: 6 bytes at frame bytes 2..7 (validBytes[0..5]), forward order
                 (WifiMacController.parseValidBytes). Firmware H60A1/H60A6: ble_msg_get_wifi_mac_address
                 (H60A1.bin@0x9b04533c, H60A6.bin@0x9b04a764). Same byte as enum 'gradual' (33 14 is the gradual
                 write; overloaded by device family).
        20 / 21  Wi-Fi hw / sw version. EMPTY: legacy WifiHardVersionController / WifiSoftVersionController
                 (f = 32 / 33); kt ControllerWifiHardVersion / ControllerWifiSoftVersion (the latter k = 33). Sent on H6047 as
                 above, on the gt-64 bulbs (h0:634-647), in the default Compose4InfoBleIot strategy and in the
                 H60Ax add flow. Reply: ASCII from frame byte 2. On H6006/H6008/H60A1/H60A6, 20 is the same string
                 as 07 03 and 21 the same as 06. H6047 firmware (20 -> 0x01005140, 21 -> 0x0100510e, 14 ->
                 0x010050dc) answers 17 bytes, or later, unsolicited, from 0x0100b4fc if the Wi-Fi module has not
                 reported yet.
        36       H6047 bar switch. EMPTY: ComposeLightHeartController extends HeartController, no p()
                 (split_h6047/com/govee/h6047/ble/controller/ComposeLightHeartController.java:8-11);
                 OnOffController4H6048.a -> ext byte[0] (split_h6047/com/govee/h6047/newdetail/control/OnOffController4H6048.java:22-23,40).
        40       IC / segment count. EMPTY: kt ControllerIcNum read ctor (false, null) (ControllerIcNum.java:79,165-175);
                 kt ControllerOnlyReadIcSegmentNum() ext null (ControllerOnlyReadIcSegmentNum.java:52-58);
                 legacy IcNumController (IcNumController.java:8, AbsOnlyReadSingleController, no p()).
        41       EMPTY: kt power-on memory ControllerOnOffMemory.Companion.c -> ext null (ControllerOnOffMemory.java:49-51,119-121)
                 and the H6062 section-count read (split_h6062/com/govee/striplight/newdetail/ControllerIcSegmentNum.java:40-41,61-67);
                 the device decides which reply a bare aa 41 gets.
        41 02    legacy power-on memory OnOffMemoryController(type != -1, false): p() = {2} (OnOffMemoryController.java:18-22,63-66).
        a3       gradual. EMPTY: kt ControllerGradual4BleWifi.Companion.b -> (ext null, read) (ControllerGradual4BleWifi.java:47-48,104-114);
                 legacy GradualChangeBleWifiController(), no p() (GradualChangeBleWifiController.java:7-8,22-23).
        a5 gg    colour group with brightness, gg = 1-based group number (callers loop 1..maxGroup, e.g.
                 split_rgbiclight/com/govee/rgbiclight/VM4Light.java:937-958): kt Controller4ColorInfoByGroup.Companion.g / m
                 -> ext {(byte) group} (Controller4ColorInfoByGroup.java:112-113,134-136,197-199,210-211).
        a2 gg    colour group, colour only, gg 1-based: Controller4ColorInfoByGroup.Companion.j (Controller4ColorInfoByGroup.java:123-124).
        b1       secret. EMPTY: legacy SecretKeyController(byte) read, no p() (SecretKeyController.java:13-20).
        b3       plug spec. EMPTY: h5080 SpecController, no p() (split_plugnew/com/govee/h5080/ble/controller/SpecController.java:6-13).
        ef       pact read. EMPTY: PactController (AbsBlePact.o, BlePactChecker); not sent on the gt-64 control screen.
                 Reply [pact_type u2be][pact_code u1]: H6006 answers 00 01 01 (0x9b03cc00), as do H60A1 / H60A6
                 (0x9b045558 / 0x9b04a980); H5083 answers 00 02 02 (0x9b031b80); H6008 does not answer
                 (firmware-sourced).
      Writing: give the stream exactly 1 + len(selector) bytes. selector is size-eos, so the writer raises
      ConsistencyError when the stream does not end right after it. Reading: a body sliced from a captured
      20-byte frame (frame[1:19]) keeps the zero padding inside selector. Every selector above is non-zero, so
      trailing 00 bytes there are padding. command uses enum command: every command above is in it except 21 and
      ef (see the note under enum command); a command byte missing from the enum still round-trips in Python
      (resolve_enum keeps the int) but reads as null in Java.
    seq:
      - id: command
        type: u1
        enum: command
        doc: "frame byte 1 = the controller's getCommandType()"
      - id: selector
        size-eos: true
        valid: { expr: '_.length <= 17' }
        doc: "frame bytes 2..18: the read selector, 0..17 bytes (AbsSingleController.p() / kt ext, capped at 17 by kt AbsController.q:49; BleUtils.Companion.p writes the BCC over byte 19). EMPTY for most reads"

  # ── proType 0xAA (read) — DISTINCT from write; NOT the 0x33 payload types ──
  # A read REQUEST is [command][selector]; a read REPLY is [command][device-state echo]. The byte after
  # the command is a read selector (request) or a sub-mode/state (reply), NOT the write field. Parsed
  # per-controller via parseValidBytes after stripping the 2-byte header
  # (AbsSingleController.f() -> generate20Bytes(0xAA, cmd, p()); p() default empty).
  read_command:
    seq:
      - id: command
        type: u1
        enum: command
      - id: body
        size-eos: true
        type:
          switch-on: command
          cases:
            'command::mode': mode_read
            'command::switch': switch_read_reply
            'command::brightness': brightness_read_reply
            'command::device_info': device_info_read
            'command::soft_version': soft_version_read
            'command::compose_light_switch': bar_switch_read
            'command::secret_read': secret_read_reply
            'command::plug_spec': plug_spec_read
            'command::bulb_string_color_read': bulb_group_color_read      # 0xA2 mechanism-B V1 (H61A8)
            'command::local_color_read': bulb_group_color_read_v2         # 0xA5 mechanism-B V2 (H61A8)
            'command::ic_num': ic_segment_read                            # 0x40 live IC/segment count (ControllerOnlyReadIcSegmentNum)
            'command::gradual_wifi_ble': gradual_read                     # 0xA3 gradual-on flag (ControllerGradual4BleWifi.h)
        doc: >-
          read selector (request) or device-state echo (REPLY). body[0] == the controller's validBytes[0]
          (frame offset 2). Reply bodies are now typed for switch / brightness / device_info / bar / secret /
          plug_spec (below); mode 0x05 => mode_read. These model the REPLY; a read REQUEST carries only the
          selector (e.g. AA 07 10, AA 05 01) and is built by the client, not parsed inbound. The AA 14 (Wi-Fi MAC,
          6 bytes forward), AA 20 / AA 21 (ASCII version) and AA EF ([pact_type u2be][pact_code u1]) replies have
          no case here, so their body stays raw (layouts under read_request).

  mode_read:
    doc: |
      Mode (0x05) read. Byte 0 is EITHER a request selector (0x01) OR a reply sub-mode byte
      (0x15 color / 0x04 scene / 0x13 music / 0x0d bulb/lamp color). Disambiguate by value: 0x01 => request;
      a known sub-mode => reply.
      - 0x15 CCT reply => cct_read_reply: [op=01][kHi][kLo], kelvin big-endian @ frame bytes 4-5; DROPS
        the FF FF FF white point the 0x33 write carries. H6047 appends a second kelvin (right bar,
        bytes 6-7) and its byte 0 is the gradual flag: [gradual][kelvin left u2be][kelvin right u2be].
      - 0x0d => mode_color_0d_report: a DEVICE-SPECIFIC body, typed by mode_color_0d_typed(kind): kind 1
        H6052 [R,G,B]; kind 2 the 0x0d bulbs H6006/H6008 and the rgblight H6188 [R,G,B, kelvin, tint];
        kind 0 the default [gradual, kelvin]. (Only the LEGACY Mode/SubModeColorV1 stack reads 0x0d, and
        0x15, as op/gradual alone, bArr[0]==1.)
      - 0x13 music => music_read_reply (FAMILY-DEPENDENT; see that type).
    seq:
      - id: selector_or_sub_mode
        type: u1
        doc: "0x01 = read-request selector; else a reply sub-mode byte"
      - id: rest
        size-eos: true
        type:
          switch-on: selector_or_sub_mode
          cases:
            0x15: cct_read_reply
            0x13: music_read_reply
            0x0d: mode_color_0d_report
        doc: "0x15 => cct_read_reply; 0x13 => music_read_reply; 0x0d => mode_color_0d_report (DEVICE-SPECIFIC body, see that type); 0x01 = request."

  cct_read_reply:
    doc: >-
      Mode 0x15 CCT read reply. kelvin = u2be at frame bytes 4-5 (e.g. 0x0A8C=2700, 0x1964=6500); NO
      FF FF FF white point (unlike the write, which is [op, FF FF FF, kHi, kLo, ...]).
      Source: SubModeColorV2.parse / ComposeChange2ColorMode.result.
      H6047 (two bars) reads [gradual][kelvin left u2be][kelvin right u2be]: byte 0 is the gradual flag
      (== 1), the first kelvin is the LEFT bar (segments 0-4) and the second the RIGHT bar (segments
      5-9); 0 = that bar is not in colour-temperature mode. The app shows one whole-device kelvin only
      when both bars match. Source: h6047/ble/SubModeColor.parse, h6047/detail/Info4Detail
      (subMode4Color IParseStrategy: q(bArr[0]==1), D0/E0(getSignedShort)), NewDetailVm.A5.
    seq:
      - { id: op, type: u1, doc: "0x01 = CCT / white-point mode; on H6047 this byte is the gradual flag (== 1)" }
      - { id: kelvin, type: u2be, doc: "colour temperature in Kelvin (big-endian, hi-first); H6047: the LEFT bar" }
      - { id: kelvin_right, type: u2be, if: 'not _io.eof', doc: "H6047 only: the RIGHT bar's kelvin (0 = not CCT); other devices: zero padding" }
      - { id: trailing, size-eos: true, doc: "remaining frame bytes after the kelvins (padding; unused for CCT read-back)" }

  music_read_reply:
    doc: >-
      Mode 0x13 music read reply. FAMILY-DEPENDENT layout: H60A6 (SubModeMusicV1.parse:112-131) =
      [music_code][value][auto_color_flag(==0)][spec_color_flag(==0)][R][G][B], truncating to 2 bytes for
      new-music codes; base2light SubModeNewMusic.parse reads only [music_code][value]. (Sub-mode 0x16
      SubModeAbsMusic = [u16 count LE][value]; not 0x13.)
      Discriminator is a FLAG, not length (frames are zero-padded to 20 bytes, so length is unusable):
      SubModeMusicV1.parse (dreamcolorlightv1 :112-131) reads bArr[0..3] unconditionally, then reads RGB
      bArr[4..6] IFF bArr[3] != 0. So spec_color_flag gates the RGB triplet. For base2light devices bArr[2..3]
      are the zero padding => dynamic=0, spec_color_flag=0, no RGB (a correct superset — this is the H60A6
      parser applied universally).
    seq:
      - { id: music_code,      type: u1, doc: "bArr[0]: music effect/scene code" }
      - { id: value,           type: u1, doc: "bArr[1]: intensity/sensitivity (app clamps 0-99)" }
      - { id: dynamic,         type: u1, doc: "bArr[2]: H60A6 dynamic flag (SubModeMusicV1.setDynamic); base2light: 0 (padding)" }
      - { id: spec_color_flag, type: u1, doc: "bArr[3]: 0 = no specified colour (parse returns here); != 0 => RGB triplet follows" }
      - { id: color_r,         type: u1, if: 'spec_color_flag != 0', doc: "bArr[4]: specified-colour R (only when spec_color_flag != 0)" }
      - { id: color_g,         type: u1, if: 'spec_color_flag != 0', doc: "bArr[5]" }
      - { id: color_b,         type: u1, if: 'spec_color_flag != 0', doc: "bArr[6]" }

  mode_color_0d_report:
    doc: |
      Mode 0x05 sub-mode 0x0D colour read-back. Frame = [proType][05][0D][body…]; this type = the body
      (bytes after the 0x0D sub-mode byte). Body interpretation is DEVICE-SPECIFIC — chosen by the
      IParseStrategy each device's *InfoDetail installs on its SubMode4Color:
        * H6052 (tablelampv1, CUSTOM strategy) => [R, G, B]: a SINGLE colour (ColorUtils.G(vb0,vb1,vb2))
          fanned across the device's zones (getColorSize: H6052=2). This resolves the old "0x0d is write-only"
          note — H6052 DOES read colour back, as these 3 bytes, and only these: its Kelvin bytes are never
          read. Source: H6052InfoDetail.parseModeValidBytes :323-346 (validBytes[0]==0x0D -> colour strategy
          on validBytes[1:]) + :141-147; Govee Home 7.7.02 the same (H6052InfoDetail.G and its constructor).
        * DEFAULT SubMode4Color strategy (most other families) => [gradual_flag, kelvin u16-be], NOT RGB
          (iParseStrategy: q(vb0==1); s(getSignedShort(vb1,vb2))). For those devices reinterpret r as the
          gradual flag and g,b as the big-endian kelvin.
        * the 0x0d bulbs H6006 / H6008 (bulblight ModeParseStrategyConfig.a) and the rgblight H6188
          (rgblight ModeParseStrategyConfig.a) => [R, G, B, kelvin u16-be, tintR, tintG, tintB]; a kelvin > 0
          (read signed) => colour-temperature mode. Use mode_color_0d_typed(kind=2) for the full body.
      Modelled as the [R, G, B] form, the first three bytes of kinds 1 and 2. Trailing frame bytes are zero
      padding (unparsed).
    seq:
      - { id: r, type: u1, doc: "H6052: red. Default-strategy devices: gradual_flag (byte==1)." }
      - { id: g, type: u1, doc: "H6052: green. Default-strategy devices: kelvin hi." }
      - { id: b, type: u1, doc: "H6052: blue.  Default-strategy devices: kelvin lo." }

  # ── Mechanism-B per-group colour read-back (H61A8; 0xAA-notify commands 0xA2/0xA5) ──
  # MULTI-FRAME: the device sends maxGroup batch frames; the client accumulates them into a segment array
  # by batch number (offset = (batch_seq-1) * groups_per_batch). H61A8 = 15 segments = 5 batches x 3 groups.
  # Request = AA <cmd> <batch_seq> (AbsSingleController.p() = the batch number). This type models ONE batch frame.
  bulb_group_color_read:      # command 0xA2 (V1) — colour only, no brightness
    doc: |
      Mechanism-B V1 batch frame (BulbGroupColor.parseBytes). Reply = [AA][A2][body]; this type = body
      (validBytes after the 2-byte header). 4 colour groups per frame, 3 bytes each [R,G,B], POSITIONAL
      (segment = (batch_seq-1)*4 + i; no per-group index/brightness byte — that is the V1↔V2 difference).
      Source: BulbGroupColor.java:15-29 (loop is hardcoded 4), BulbStringColorController.java:32-44 (cmd 0xA2),
      consumer BleOpV1.java:318-345 (batch accumulate).
    seq:
      - { id: batch_seq, type: u1, doc: "1-based batch number (BulbGroupColor f109035a); client offset = (batch_seq-1)*4" }
      - { id: groups, type: bulb_rgb_group, repeat: expr, repeat-expr: 4, doc: "4 colour groups. Rest of frame = zero padding." }
  bulb_rgb_group:
    seq:
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }

  bulb_group_color_read_v2:   # command 0xA5 (V2) — adds per-segment brightness
    doc: |
      Mechanism-B V2 batch frame (BulbGroupColorV2.parseBytes). Reply = [AA][A5][body]; this type = body.
      Each group = 4 bytes [brightness, R, G, B] — the leading byte is BRIGHTNESS, confirmed at the consumer:
      BulbGroupColorV2.f109039c -> SubModeColorV2.f109110e = colors.brightnessSet (SubModeColorV2.f:70).
      Segment is POSITIONAL (= (batch_seq-1)*groups_per_batch + i), NOT the leading byte. groups_per_batch is
      a CLIENT controller constant (BulbStringColorControllerV2.f109046g, default 3) — NOT frame-encoded;
      modelled at 3 (H61A8: 15 segments / 3 = 5 batches). Trailing bytes = zero padding. (A V3 variant,
      BulbStringColorControllerV3, also exists.) Source: BulbGroupColorV2.java:18-39,
      BulbStringColorControllerV2.java:41-53, consumer BleOpV1.java:349-377.
    seq:
      - { id: batch_seq, type: u1, doc: "1-based batch number (f109037a; logged 'group', 1..maxGroup=5 for H61A8); client offset = (batch_seq-1)*3" }
      - { id: groups, type: bulb_rgb_group_v2, repeat: expr, repeat-expr: 3, doc: "3 groups = controller default (f109046g); count is client-set, not in the frame." }
  bulb_rgb_group_v2:
    seq:
      - { id: brightness, type: u1, doc: "per-segment brightness (BulbGroupColorV2 bArr2[0] -> SubModeColorV2 brightnessSet)" }
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }

  ic_segment_read:   # command 0x40 — live IC/segment capability read
    doc: |
      AA 40 reply (ControllerOnlyReadIcSegmentNum, base2light/kt/general_controller). The ONLY live-BLE
      capability read: reports the addressable IC (lamp-bead) count and the segment count, so a client can
      discover segmentation at runtime instead of relying on the per-SKU device table. FIXED layout (not
      parameterized). Source: ControllerOnlyReadIcSegmentNum.c():getSignedShort(it2[0],it2[1]) + it2[2].
    seq:
      - { id: ic_count, type: u2be, doc: "IC / lamp-bead count, the RAW 16 bits (big-endian, hi@byte0). The app reads it SIGNED (BleUtil.getSignedShort, ControllerOnlyReadIcSegmentNum:32): use ic_count_signed" }
      - { id: segment, type: u1, doc: "segment (group) count = it2[2]" }
    instances:
      ic_count_signed:
        value: 'ic_count >= 0x8000 ? ic_count - 0x10000 : ic_count'
        doc: "The IC count as the app reads it: getSignedShort sign-extends, so 0x8000-0xFFFF reads as -32768..-1. ic_count stays the raw u2be for existing readers."

  gradual_read:      # command 0xA3 — gradual (fade-between-colours) on/off
    doc: "AA A3 reply (ControllerGradual4BleWifi.h:100-101): gradual mode on iff byte0 == 1 (validBytes length 17; only byte0 is read)."
    seq:
      - { id: state, type: u1, doc: "1 = gradual enabled, else off (id 'on' avoided: YAML-boolean)" }

  # ── 0xAA READ replies (body[0] = validBytes[0] = frame offset 2). Verified vs each controller's parseValidBytes. ──
  switch_read_reply:
    doc: "AA 01 reply: state @ body[0]. LIGHTS: every curated control screen that reads it decodes on as body[0] == 1 (ControllerSwitch.Companion.c, base-classes11/com/govee/base2light/kt/general_controller/ControllerSwitch.java:32), at connect and in the aa 01 heartbeat (ShareVM.N3 frame[2] == 1, ShareVM.java:1008): use light_is_on. The legacy SwitchController.parseValidBytes reads it as != 0 (SwitchController.java:30). No curated or profiled control screen reaches it, but older detail screens for legacy goodsTypes do (e.g. the dreamcolorlightv1 adjust/v1-v2 connect reads, BleOpV1.java:735, BleOpV2.java:123, BleOpV3.java:709), as do scene / IoT command paths (BleSwitchCmdBuilderV1); the two agree for 0/1 and differ only for a byte >= 2. Which message carries a family's power (this reply, or the 0xAC status TLV 0x01) is devices.yaml client_profile.power_read. PLUGS: this SAME 0x01 reply is a RELAY BITMASK (bit i = relay i on), not boolean — interpret per device (h5080 SwitchControllerV2.parseValidBytes splits 8 bits, split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:29-44; the goodsType-43 H5083 screen decodes (bArr[0] & 1) != 0, H5083NewDetailRepository.loadParser, :162-167). Firmware-sourced for H5083 (single outlet): the byte is the outlet-0 state 0x00/0x01 (H5083.bin FUN_9b021668 = ldrb [0x10009440+8]; in code it is written only by 0x9b021640, idx 0, with 0/1). Bit 0 is outlet 0 and the upper bits are 0. The reply is NOT the write's [mask<<4 | on] form."
    seq:
      - { id: state, type: u1 }
    instances:
      light_is_on:
        value: 'state == 1'
        doc: "Lights: power as the curated control screens decode it (ControllerSwitch, == 1). Not for plugs, whose state is a relay mask."
  brightness_read_reply:
    doc: "AA 04 reply (BrightnessController.parseValidBytes): raw brightness 0-255. NO 0-255<->0-100 rescale in the BLE codec (percent mapping is UI-layer)."
    seq:
      - { id: brightness, type: u1 }
  bar_switch_read:
    doc: >-
      AA 36 reply (h6047 ComposeLightController / ComposeLightHeartController, the app's heartbeat):
      [left, right, movie/feast switch] on/off (bArr[0]==1 / bArr[1]==1 / bArr[2]==1). The 33 36 write uses
      the same [left, right] (Controller4ExtBytes [54, left, right], NewDetailVm.D5). The app treats the
      device as ON when left OR right is on (NewDetailVm heart parse, Info4Detail.g). The third byte (the
      movie/feast mode switch, EventFeastHeart) is not needed for control. The left/right order can be swapped
      during setup (h6047/add/LightPosAc), so the names are logical, not physical.
    seq:
      - { id: left, type: u1 }
      - { id: right, type: u1 }
      - { id: movie_feast, type: u1, if: 'not _io.eof', doc: "movie/feast switch (== 1 on); read-only here" }
  secret_read_reply:
    doc: "AA B1 reply (SecretKeyController.parseValidBytes): selector (0x01 = valid) then the 8-byte account-lock secret. The 33 B2 write sends the raw 8 bytes (no selector). This is the account-lock, NOT the wire cipher."
    seq:
      - { id: selector, type: u1 }
      - { id: secret, size: 8, doc: "8-byte account-lock token (atomic id; base64 off-wire). Not decodable further." }
      - { id: pad, size-eos: true, doc: "zero padding to the 20-byte frame" }
  plug_spec_read:
    doc: "AA B3 reply (h5080 SpecController:19): single spec byte @ body[0]. TRACED all uses: stored as model.L (EventSpec.h, PairAcV1.onEventSpec:347) and forwarded as the IoT spec identifier / adjust-screen string (Model.getSpec) — it is never bit-decoded. Plug OUTLET COUNT is NOT this byte: it is Support.getPlugNum(goodsType) = 50/307->2, 90->3, else 1."
    seq:
      - { id: spec, type: u1 }
      - { id: rest, size-eos: true }
  device_info_read:
    doc: |
      AA 07 reply (BasicInfoController / BasicWifiInfoController / SnController / HardVersionController).
      selector @ body[0] picks the body. 3-byte version fields render as X.YY.ZZ (major . %02d . %02d).
      The app's own connect-time reads are AA 06 (soft version), AA 07 03 (hard version) and AA 07 02
      (SN = device id), plus AA 21 / AA 20 / AA 14 for Wi-Fi. (h5080 / H5083: the app sends AA 06, 07 03 and
      07 02 only in the add flow, PairAcV1.t2, split_plugnew/com/govee/h5080/add/PairAcV1.java:652, not from
      the control screen.) AA 07 10 / 11 are the binary basic / Wi-Fi forms that BasicInfoController /
      BasicWifiInfoController expect. Neither controller has a caller in 7.7.02, and no verified firmware has
      a handler for them: H6006 / H6008 / H5083 / H6047 send no reply, and H60A1 / H60A6 echo the request
      unchanged, so the body reads as all zeros. On H60A6 the 0x10 / 0x11 bodies arrive only as 0xAC status
      TLV-7 records (status_device_info).
      Selectors with no case below (info stays raw):
        0x04  ASCII soft version, the same layout as 0x03 (app SoftHardController.Companion.f /
              SoftVersionInDeviceInfoController.parseValidBytes). H6047 firmware answers "1.00.12" (0x010053ca).
        0x06  FIRMWARE-SOURCED, no app sender: the 6-byte BLE MAC / device address, then the rest of the frame.
              Byte order per image: byte-reversed (LSB first) against the device's XX:XX:.. string on H6006 /
              H6008 (0x9b03c988 / 0x9b03c7ec) and H60A1 / H60A6 (H60A1.bin@0x9b0452b0, H60A6.bin@0x9b04a6d8;
              reverse decoder 0x9b04dfdc / 0x9b053f9c); in the device's printed-string order on H5083
              (ble_msg_get_ble_macaddress @0x9b0319cc); on H6047 the BLE stack's address bytes as its nvds
              getter returns them, likely LSB-first (0x01005370 -> 0x010053ce / 0x0101693c), the same bytes as
              uid 0..5 of the 07 02 reply.
      On H5083, H60A1 / H60A6 and H6006 / H6008 the bytes after the data are the request's bytes (zero in
      an app-built frame), not firmware padding: the reply is built in the request buffer.
    seq:
      - { id: selector, type: u1 }
      - id: info
        size-eos: true
        type:
          switch-on: selector
          cases:
            0x10: device_info_basic
            0x11: device_info_wifi
            0x02: device_info_sn
            0x03: device_info_hard_version
  device_info_basic:      # AA 07 10
    seq:
      - { id: uid, size: 8, doc: "8-byte device UID (atomic id; app renders MAC-style byte-REVERSED via toAddressBytes(hFirst=false), leading 00:00 stripped)" }
      - { id: sw_version, type: version3 }
      - { id: hw_version, type: version3 }
      - { id: dsp_version, type: u2le, doc: "DSP version (LE); 0 when absent (short replies pad to 0)" }
      - { id: pad, size-eos: true, doc: "zero padding to the 20-byte frame" }
  device_info_wifi:       # AA 07 11
    seq:
      - { id: wifi_mac, size: 6, doc: "6-byte Wi-Fi MAC (atomic id; FORWARD byte order via toAddressBytes(hFirst=true))" }
      - { id: wifi_sw_version, type: version3 }
      - { id: wifi_hw_version, type: version3 }
      - { id: pad, size-eos: true, doc: "zero padding to the 20-byte frame" }
  device_info_sn:         # AA 07 02
    seq:
      - { id: uid, size: 8, doc: "8-byte device UID (atomic id). Firmware sends the device's id byte-reversed. H6006 / H6008 (0x9b03c87c / 0x9b03c740) decode the device's colon-separated uuid string into (strlen+1)/3 bytes and store them in reverse order; H60A1 / H60A6 write the id at frame bytes 3..10, byte-reversed from the uuid string (reverse decoder 0x9b04dfdc / 0x9b053f9c). H6047 (0x010053da) sends its stored id byte-reversed. That stored id is [rand lo, rand hi, BLE addr reversed], created on the first AA 07 02 / AA B1 if absent (0x010029c4), so uid bytes 0..5 = the AA 07 06 address bytes and 6..7 = rand hi, rand lo" }
      - { id: pad, size-eos: true, doc: "zero padding to the 20-byte frame" }
  device_info_hard_version:   # AA 07 03 (HardVersionController.parseValidBytes: validBytes[0]==3, rest = ASCII)
    seq:
      - { id: version, type: strz, encoding: UTF-8, size-eos: true, doc: "ASCII hardware version (e.g. 1.04.03), NUL-terminated inside the zero padding; BleUtil.getStrData" }
  soft_version_read:      # AA 06 (SoftVersionController.parseValidBytes: validBytes = the whole body, ASCII)
    seq:
      - { id: version, type: strz, encoding: UTF-8, size-eos: true, doc: "ASCII firmware version (e.g. 1.00.41), NUL-terminated inside the zero padding; BleUtil.getStrData" }
  version3:
    doc: "3-byte firmware version rendered X.YY.ZZ (major . %02d . %02d); BasicInfoController.u:73."
    seq:
      - { id: major, type: u1 }
      - { id: minor, type: u1 }
      - { id: patch, type: u1 }

  # ── 0xAC status REPLY: parse the REASSEMBLED buffer (client de-chunks first) as a TLV stream ──
  #  Walker = Compose4BaseInfoSingleRead.u(): [type, len, value(len)], advance i += 2+len. No fixed header —
  #  offset 0 is the first type. Known types: 0x01 switch, 0x04 brightness, 0x05 mode block, 0x07 device-info,
  #  0x11 sleep, 0x12 wakeup, 0x23 timers, 0x30 zone on/off (2 bits), 0x41 power-on memory (H60A6), 0xA5 colour group.
  #  For 0xA5 feed value to color_group_read (record_count from the per-SKU count, see devices.yaml).
  status_reply:
    doc: |
      Reassembled 0xAC status reply = a sequence of [type, len, value] TLVs. The buffer is de-chunked from
      the 0xAC frames. Trimmed to the first chunk's lastLen as the app does (see multi_ac) it ends exactly
      at the last TLV; a client that joins the whole zero-padded last chunk instead gets TRAILING ZERO
      PADDING. Terminate on either: no real TLV type is 0x00, and the walker
      (Compose4BaseInfoSingleRead.u:299-326) also stops once fewer than 2 bytes remain. So parse until a
      type-0 sentinel or EOF — repeat: eos would instead throw on the padding (a lone trailing 0 byte has
      no room for its len). The trailing type-0 element, if present, is the padding sentinel (no len/value).
    seq:
      - id: tlvs
        type: status_tlv
        repeat: until
        repeat-until: '_.type == 0 or _io.eof'
  status_tlv:
    seq:
      - { id: type, type: u1 }
      - { id: len, type: u1, valid: { max: 0x7f }, if: 'type != 0' }   # type 0 = trailing zero padding: no len/value follow. The app reads len SIGNED (Compose4BaseInfoSingleRead.u:270) and new byte[len] throws for 0x80+ (:283)
      - id: value
        size: len
        if: 'type != 0'
        type:
          switch-on: type
          cases:
            0x01: status_switch
            0x04: status_brightness
            0x05: mode_status
            0x07: device_info_read      # decoded like the aa 07 reply; the app's status parser reads ONLY 0x10/0x11 here (see status_device_info). Kept for existing readers
            0x30: status_zone
            0x41: status_seg_info
            0xa5: color_group_status     # one colour group; curated devices are all 4-byte (see type doc)
        doc: |
          Full walker = Compose4BaseInfoSingleRead.u:292 — a header-less [type, len, value] walk
          (i += 2 + len). u() itself handles 0x01/0x04/0x05/0x07/0x11(sleep)/0x12(wakeup)/0x23(timers);
          EVERYTHING else (0x30, 0x41, 0xA5, ...) is passed to the caller's fallback lambda with the FULL
          [type,len,value] re-prepended. All value types below are modelled (the value is a bounded
          size:len substream, so record counts come from len — no external count needed):
            * 0x07 device-info. w() (Compose4BaseInfoSingleRead.w:370-437, Govee Home 7.7.02) parses ONLY
              selector 0x10 (basic: [uid8 (byte-REVERSED), sw3, hw3, dsp u16le]) and 0x11 (wifi: [mac6
              (FORWARD), sw3, hw3]); every other selector, including 0x02 (SN) and 0x03 (hard version), is
              logged and IGNORED with no body read (w:376-382). status_device_info models exactly that. The
              value is still typed device_info_read (the single-frame aa 07 layout), which also decodes
              0x02/0x03, so a client should ignore those here. Same 0x10/0x11 layout as the aa 07 reply ->
              RETIRES the old MAC-anchor heuristic.
            * 0x05 mode == [sub_mode, params] (delegated to the caller's .g() lambda; same shape as the
              0x05 mode frame body / SubMode dispatch).
            * 0xA5 colour group -> color_group_status. Record COUNT = (len-1)/record_size (from the TLV
              len); record_size is the only externally-keyed bit (3B vs 4B), fixed per SKU.
          Unmatched types stay raw.
  status_device_info:
    doc: |
      0xAC status TLV 0x07 as the app's status parser reads it (Compose4BaseInfoSingleRead.w:370-437, Govee
      Home 7.7.02): ONLY selector 0x10 (basic) and 0x11 (Wi-Fi) are decoded. Every other selector, including
      0x02 (SN) and 0x03 (hard version), which the single-frame aa 07 reply does handle, is logged and ignored
      with no body read (w:376-382), so it stays raw here. Standalone: status_tlv's 0x07 case keeps
      device_info_read for existing readers, since switching it changes which 0x07 blocks such a reader
      fails to parse.
    seq:
      - { id: selector, type: u1 }
      - id: info
        size-eos: true
        type:
          switch-on: selector
          cases:
            0x10: device_info_basic
            0x11: device_info_wifi
  status_switch:
    doc: "0xAC status TLV 0x01: power. The status parser decodes on as state != 0 (Compose4BaseInfoSingleRead.u: bArr2[0] != 0, base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:286-288): use is_on. Screens that take connect-time power from here and send no aa 01 read are devices.yaml client_profile.power_read: ac_status (H60A4/H60A6, H6039/H6038/H6048, H703A/H703B)."
    seq: [ { id: state, type: u1 } ]
    instances:
      is_on:
        value: 'state != 0'
        doc: "Power as Compose4BaseInfoSingleRead.u decodes TLV 0x01 (!= 0). Differs from the aa 01 control-screen rule (switch_read_reply.light_is_on, == 1) only for a byte >= 2."
  status_brightness: { seq: [ { id: brightness, type: u1 } ] }
  status_zone:       { doc: "0x30 in the 0xAC reply: zone0 = bit0 of byte0, zone1 = bit0 of byte1 (VM4LightH60A6.o:103)", seq: [ { id: zone_a, type: u1 }, { id: zone_b, type: u1 } ] }
  status_seg_info:   { doc: "0x41 in the 0xAC reply. On H60A6 this is the power-on memory record [02][value]; field ic_or_seg (name kept for existing readers) holds the value. Firmware ble_check_basic_info (H60A6.bin 0x9b04c0bc-0x9b04c0ce) fills it as [0x41][02][02][FUN_9b02bfe4 = UserConfig+4, logged 'start mode'], the field that 33 41 02 <v> writes (app Controller4OnOffMemory). VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.o:99-100 stores byte1 via Info4Detail.Y, which SettingAcH60a6.W3 (syncOnOffMemoryResult) also sets", seq: [ { id: b0, type: u1 }, { id: ic_or_seg, type: u1 } ] }
  mode_status:
    doc: "0xAC TLV 0x05 — device's active mode: [sub_mode, params]. Same [sub_mode, params] shape as the 0x05 mode frame body (SubMode dispatch, u():349 -> .g() lambda). sub_mode seen = the active mode: colour (0x15/0x0D), CCT, scene (0x04), music (0x13), diy (0x0a). params layout is per sub_mode / device (see mode_read / color_* types); left size-eos here."
    seq:
      - { id: sub_mode, type: u1, enum: sub_mode }
      - { id: params, size-eos: true }
  color_group_status:
    doc: |
      0xAC TLV 0xA5 — ONE colour group (Controller4ColorInfoByGroup). group_index @ value[0] is 1-based
      (VM read loop iterates group 1..maxGroup; request byte = group number). Records fill the rest of the
      TLV: COUNT = (len-1)/record_size (the walker's own count is a caller-supplied per-SKU constant
      'oneGroupColorSize', but len == 1 + count*record_size, so len is authoritative and robust for a
      short last group). record_size is caller/SKU-selected: 4-byte [brightness,R,G,B] via generateReadController
      -> q():174 when the SKU supports part-brightness; 3-byte [R,G,B] via generateReadController4OnlyColor
      -> p():157 otherwise. ALL CURATED devices are 4-byte: H60A6 (VM4LightH60A6:302), H6047 (VM4H6038:872),
      H6641 (VM4LightH61d3:269, count=4). 3-byte is used by non-curated colour-only families (tvlightv2,
      rgblight, bulblightstringv1, h7022, h6160). Modelled 4-byte here (the curated form); for a 3-byte SKU
      each record is color_group_rec_rgb. Global segment index (client-assembled) = (group_index-1)*count + k.
      NOTE the reply's group_index byte is read but DISCARDED in the 4-byte callback path — the client tracks
      the group by its per-group READ REQUEST, not this byte.
    seq:
      - { id: group_index, type: u1, doc: "1-based group number (K(value[0]))" }
      - { id: records, type: color_group_rec, repeat: eos, doc: "count = (len-1)/4; 4-byte [brightness,R,G,B]" }
  color_group_rec:       # 4-byte record (part-brightness SKUs; all curated)
    seq:
      - { id: brightness, type: u1 }
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }
  color_group_rec_rgb:   # 3-byte record (colour-only SKUs; reference — not wired into the 0xA5 case)
    seq:
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }

  # ── multi-packet per-segment colour/brightness WRITE value (comType 0x40; H6047/H61A8/H60A6 write path) ──
  # Client-built value carried via the 0xA3 multi dialect (a3_start), commByte 0x40. The write-side mirror of the
  # 0xA5 read: instead of one group per segment, it GROUPS segments by distinct colour / distinct brightness value,
  # each group tagged (0x00 = colour, 0x01 = brightness) with its member segment indices. Source:
  # AbsMultipleControllerV14ColorStrip:29-99 (MultipleColorStripControllerV1/V2). Feed a REASSEMBLED value here.
  color_strip_write:
    doc: "[group_count][group × group_count]; group_count = (#distinct colours) + (#distinct brightness values, if a brightnessSet was set)."
    seq:
      - { id: group_count, type: u1 }
      - { id: groups, type: color_strip_group, repeat: expr, repeat-expr: group_count }
  color_strip_group:
    doc: "tag 0x00 = colour group [0, count, R, G, B, idx×count]; tag 0x01 = brightness group [1, count, brightness, idx×count]. idx = 1-byte segment indices."
    seq:
      - { id: tag, type: u1, doc: "0 = colour, 1 = brightness" }
      - { id: pixel_count, type: u1 }
      - { id: r, type: u1, if: 'tag == 0' }
      - { id: g, type: u1, if: 'tag == 0' }
      - { id: b, type: u1, if: 'tag == 0' }
      - { id: brightness, type: u1, if: 'tag == 1' }
      - { id: indices, type: u1, repeat: expr, repeat-expr: pixel_count, doc: "segment indices this colour/brightness applies to" }

  switch_payload:
    seq:
      - id: state
        type: u1
        doc: "Lights 0x01/0x00. Plugs (SwitchControllerV2.q) use [relay mask << 4 | on bits]: relay0 0x11/0x10, relay1 0x22/0x20, relay2 0x44/0x40, all 0xFF/0xF0. Firmware-sourced for H5083 (MSG_SET_TURN, H5083.bin@0x9b01f6b4): the outlet switches to (b & 1) only when bit 4 is set. So 0x11/0x10 (app H5083NewDetailViewModel.x0) and 0xFF/0xF0 work, while 0x22/0x20, 0x44/0x40 and the light form 0x01/0x00 are ignored (still acked 33 01 00 once paired; 33 01 is dropped before the 33 B2 pairing)."

  brightness_payload:
    seq:
      - id: level
        type: u1
        doc: 0-100 (some models 0-255; some rescale 1-100 -> 20-254).

  sync_time_payload:
    doc: Lights, command 0x09.
    seq:
      - { id: hour,   type: u1 }
      - { id: minute, type: u1 }
      - { id: second, type: u1 }
      - { id: week,   type: u1 }
      - { id: marker, type: u1, doc: constant 0x01 }
      - { id: tz_hour, type: s1, doc: local UTC offset hours (incl. DST) }
      - { id: tz_min,  type: s1, doc: local UTC offset minutes }

  plug_sync_time_payload:
    doc: Plug family, command 0xB5.
    seq:
      - { id: unix_seconds, type: u4, doc: big-endian epoch seconds }
      - { id: marker, type: u1, doc: constant 0x01 }
      - { id: tz_hour, type: s1 }
      - { id: tz_min,  type: s1 }

  # ── mode (0x05) sub-mode payloads ───────────────────────────────────────
  mode_payload:
    seq:
      - id: sub_type
        type: u1
        enum: sub_mode
      - id: params
        size-eos: true
        type:
          switch-on: sub_type
          cases:
            'sub_mode::color_legacy': color_legacy
            'sub_mode::color_rgbic_0b': color_rgbic_0b
            'sub_mode::color_cct_0d': color_cct_0d
            'sub_mode::color_rgbic_15': color_15
            'sub_mode::scene': scene_payload

  color_legacy:  # 0x02
    seq:
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }
      - { id: white_flag, type: u1 }
      - { id: r2, type: u1 }
      - { id: g2, type: u1 }
      - { id: b2, type: u1 }

  color_rgbic_0b:  # 0x0b (dreamcolor) — RGB + 2-byte segment mask (LSB-first, 0-based)
    seq:
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }
      - { id: seg_mask, type: u2le, doc: bit i = segment i selected; all-ones = whole device }

  color_cct_0d:  # 0x0d (bulbs/lamps) — RGB + kelvin + mapped-RGB tint
    doc: |
      0x0D colour write. RGB form = [R,G,B, 0...] (Bytes.f). Two CCT forms, both carrying tint =
      Constant.getTemColorByKelvin(kelvin) (exact 100 K table hit, else zeros):
        white-slot [ff,ff,ff, kelvin u2be, tint]  H6188 (Config.GM -> Bytes.A; library color_scheme "h6188");
        both-slot  [tint, kelvin u2be, tint]      H6008/H6006/H6052 (Config.bu -> Bytes.p; library "h6006").
      The app only sends whole 100 K steps, clamped to the device range (KelvinConfig.calibrationKelvin).
    seq:
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }
      - { id: kelvin, type: u2, doc: big-endian; 0 for plain RGB }
      - { id: tint_r, type: u1, doc: kelvin->RGB from Constant.Z1; 0 when out-of-table }
      - { id: tint_g, type: u1 }
      - { id: tint_b, type: u1 }

  color_15:  # 0x15 (RGBIC) — op-type tagged (byte after 0x15)
    seq:
      - id: op_type
        type: u1
        enum: op15
      - id: data
        size-eos: true
        type:
          switch-on: op_type
          cases:
            'op15::set_color': op15_color
            'op15::set_brightness': op15_brightness
            'op15::set_color_temp': op15_color_temp

  op15_color:      # op 0x01 — R,G,B then an EXTERNALLY-KEYED trailer (client-built write)
    doc: |
      SubModeColorV1.getWriteBytes op 0x01 — a client-built WRITE. Three forms share op byte 0x01 with
      NO in-frame discriminator; the form is chosen by device family + colour mode (the opType arg i2),
      and frames are zero-padded to 20 bytes (BleUtils.o :1006-1016), so length cannot recover it either:
        basic            (i2==1,  :686) = [r,g,b][seg_mask u2le]
        H60A1/H60A6 RGB  (i2==12, :736) = [r,g,b][00 00 00 00 00][seg_mask u2le]
        H60A1/H60A6 CCT  (i2==11, :725) = [r,g,b][kelvin u2be][tintR][tintG][tintB][seg_mask u2le]
      RGB vs CCT are byte-identical in length and op byte. This trailer is therefore EXTERNALLY-KEYED
      (like rgb_scene_value.rest / adv manufacturer_data.rest): the encoder knows the form from i2; a
      decoder of an isolated padded frame cannot. seg_mask (makeSelectedTwoBytes, <=16-seg legacy stack)
      is the last meaningful 2 bytes before zero padding.
    seq:
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }
      - { id: trailer, size-eos: true, doc: "form per doc above; basic=<seg_mask>; H60A1/H60A6=<5-byte ext><seg_mask>; then zero pad" }

  op15_brightness: # op 0x02 — <pct> <seg_mask>
    seq:
      - { id: pct, type: u1, doc: 0-100 }
      - { id: seg_mask, type: u2le }

  op15_color_temp: # op 0x05 — <kelvin little-endian>
    seq:
      - { id: kelvin_le, type: u2le }

  scene_payload:  # 0x04
    seq:
      - id: effect
        type: u2le
        doc: scene effect id (little-endian). Full scene/DIY tables use the 0xA3 multi dialect.

  # ── proType 0xEE : notify ───────────────────────────────────────────────
  notify_frame:
    seq:
      - id: sub_type
        type: u1
        enum: notify_sub
        doc: "0xEE notify sub-type (see notify_sub enum)"
      - id: data
        size-eos: true
        type:
          switch-on: sub_type
          cases:
            'notify_sub::brightness': notify_level
            'notify_sub::wifi_connect': notify_wifi
            'notify_sub::device_info_or_zone': notify_switch_zone
        doc: >-
          push payload. The curated LIGHTING SKUs push EXACTLY three (verified: ShareVM.x4:2425 registers
          wifi; per-SKU VM overrides add brightness + switch/zone): notify_level (0x20), notify_wifi (0x11),
          notify_switch_zone (0x30). Any other sub_type is a cross-category notify (sensor/TV/plug/camera) that
          these SKUs do not register — its DefParser parser is cited in the notify_sub enum; left raw here.
  notify_level:
    doc: "0xEE 20 brightness push (DefParser.p): level 0-255 @ data[0] (unsigned)."
    seq:
      - { id: level, type: u1 }
  notify_wifi:
    doc: "0xEE 11 wifi-connect push (DefParser.Q): status @ data[0]; 0 = connected."
    seq:
      - { id: status, type: u1 }
  notify_switch_zone:
    doc: |
      0xEE 30 switch/zone push — FAMILY-DEPENDENT. H60A6 (VM4LightH60A6.U5): data[0]=detail (ignored),
      main switch = bit0 of data[1], zone0 = bit0 of data[2], zone1 = bit0 of data[3]. Generic (DefParser.M):
      a single flags byte @ data[1] carries up to 4 sub-switches in bits 1-4 (DefParser.O = one switch in bit 1).
      Interpret per device (flags_a = that generic flags byte). ParseUtils.getBit2Boolean numbers bits from 1, so
      "bit 1" is the LSB (0x01). As live power (devices.yaml client_profile.power_live: push_30): bit 0 of flags_a
      (frame[3]) on H60A1/H60A4/H60A6, H6039, H6078 and H601E/H601F; bit 0 of flags_c (frame[5]) on H60A0; per
      bar flags_b == 1 / flags_c == 1 on H6038/H6048.
    seq:
      - { id: detail, type: u1 }
      - { id: flags_a, type: u1 }
      - { id: flags_b, type: u1 }
      - { id: flags_c, type: u1 }
      - { id: rest, size-eos: true }

  # ── proType 0xA1 / 0xA2 : MultiPackageManager chunk ─────────────────────
  multi_a1:
    doc: comType@1, position@2, 16 data bytes@3..18. position 0x00 = start (data[0] = packet count), 0xFF = end.
    seq:
      - { id: com_type, type: u1 }
      - { id: position, type: u1 }
      - { id: data, size: 16, doc: "16 value bytes (frame bytes 3..18); on the start packet (position 0x00) data[0] = total packet count" }

  # ── proType 0xA3 : MultipleControllerCommV1 scene/DIY dialect ───────────
  multi_a3:
    seq:
      - id: seq_no
        type: u1
        doc: 0x00 = start frame, 1..N = data, 0xFF = end.
      - id: frame
        size-eos: true
        type:
          switch-on: seq_no
          cases:
            0: a3_start
        doc: seq 0 => a3_start; otherwise 17 data bytes.

  # a3_start models the makeSendBytesV1/V2 START (marker byte2 == 0x01) — the form used by every curated
  # device (generic scenes + DIY-graffiti). Two other 0xA3 sub-layouts exist, distinguished by byte2:
  #   0x00 = makeSendBytesV0 (:743): header [A3,00,00,packs+2,commByte,0x00...]; NO inline value (value
  #          only in middle/end); the 0xFF terminator is EMPTY (not data-bearing). Used when controller.p()==0.
  #   0x02 = makeSendBytesV3 / K (:851/:111): header [A3,00,02,pktCount,commByte,ctrlNum,ctrlIdx,verify(8)@7];
  #          only 4 inline value bytes @ byte15; data-bearing 0xFF end; one START..FF group per controller
  #          (List<List<byte[]>>). Used by multi-controller scenes (isNeedMulMulPackage()==true).
  # See GOVEE_BLE_GATT_PROTOCOL.md 4.4 "three 0xA3 sub-layouts".
  a3_start:
    seq:
      - { id: marker, type: u1, doc: "byte2 discriminator: 0x01 here (makeSendBytesV1/V2). 0x00=V0, 0x02=V3 have different layouts — see comment above." }
      - id: packet_count
        type: u1
        doc: |
          Total frame count, START and the 0xFF END included. The END carries data only when the value is
          longer than START's inline 15 - len(commBytes) bytes: then it holds the final chunk (up to 17
          bytes, a full 17 included) and the count is 1 + the frames after START. Otherwise the whole
          value rides in START, packet_count is 2 and the END is empty (MultipleControllerCommV1.makeSendBytesV2
          always appends its separate END frame bArr2 and starts the count at 2).
          Device side, static firmware analysis (not hardware-verified): the H6006 firmware's a3 reassembler
          (FUN_9b020d2c, firmware-h6006/decompiled/H6006_a3_reassembler.c) stores START byte 3 as the packet
          count, accepts the 0xFF END only when (frames after START, END included) + 1 == count, and copies
          17 bytes from END bytes 2..18. So count 2 with an empty END is accepted, and the count includes
          START and END.
      - { id: comm_byte, type: u1, doc: "commByte @ byte4: legacy scene version constant (V1=1 V2=2 V4=4 V3=7 V5=10 compose V6=12 H610B; comType 12 is shared by V6/V7) OR a device DIY/graffiti protocol code (H60A6 = 0x58). NOT value[0]|0x08. NOTE: the graffiti scene builder can instead emit proType 0xA4 (multi_a4), where commByte moves to byte6. See GOVEE_BLE_GATT_PROTOCOL.md 4.4" }
      - { id: inline_value, size: 14, doc: "first (15 - commBytes.length) value bytes; 14 for a 1-byte commByte. Value = controller.getValue() (DIY/graffiti = re-encoded toBytes(), not the raw cloud blob). Opaque here — reassemble across frames then parse with the scene-upload VALUE types below (diy_value / graffiti_v3_value / rgbic_scene_value / rgb_scene_value / graffiti_v2_value per commByte)" }

  # ── proType 0xA4 : MTU-sized MultipleControllerCommV1 dialect (makeSendBytesMtu, :409) ──
  # RARELY USED. Reached ONLY via Compose4DefWrite4Multi.makeWriteController when command == 88 (0x58)
  # AND NewDiyEditConfig.isUseMtuCommand(effectByte) is true (effectByte in {0x5E,0x5D,0x20}). None of the
  # curated devices (H60A6/H61A8/H6047/H6641/H6008/H6052/H6006) reach it on their normal BLE routes —
  # they all emit 0xA3. Frame size = MtuConfig.getAvailableMtuSize = max(getMtu(sku)-3, 20), default 20
  # (a per-SKU CACHE of the negotiated ATT MTU, default 23: saveMtuData stores the onMtuChanged result only
  # on a successful exchange, so a failed/absent exchange leaves getMtu at 23 => usable size 20), so packets
  # are 20 bytes unless a larger MTU was negotiated and cached. This models the START packet at MTU=20 (20-byte):
  # [A4][00][00][01][02][00][commByte@6][value@7..][BCC]. The terminator is a SEPARATE short packet
  # [A4 FF FF <BCC>] (not data-bearing) — unlike the 0xA3 form whose 0xFF terminator carries the last
  # chunk. Middle/large-MTU packets use a 2-byte-seq layout not modelled here. See GOVEE_BLE_GATT_PROTOCOL.md 4.4.
  multi_a4:
    doc: |
      0xA4 MTU frame (MultipleControllerCommV1.makeSendBytesMtu :409). ONE BLE write; a scene value spans
      several frames. seq_marker (bytes 1-2, u16 LE) discriminates the form:
        0x0000 = START · 0xFFFF = END · else = MIDDLE (packet index, 1-based).
      START also carries marker 0x01 (byte3), packet_count (bytes 4-5 = TOTAL frame count incl. START+END;
      the small case len<=mtuSize-8 is always 2), commByte (byte6), then value from byte7 (up to mtuSize-8
      bytes). MIDDLE/END carry value from byte3 (mtuSize-4 bytes, END = remainder). The final byte of every
      frame is the BCC (parsed by the frame-level `checksum`). No separate terminator in the multi-packet
      case — the END (FF FF) frame is the last data packet. Reassemble value = START.value ++ MIDDLE.value
      (ascending seq) ++ END.value, then parse with the scene-upload VALUE types.
      Verified against makeSendBytesMtu: Aurora (187 B, MTU 20) => 12 frames = START(12) + 10*MIDDLE(16) + END(15).
    seq:
      - { id: seq_marker, type: u2le, doc: "0x0000 START · 0xFFFF END · else MIDDLE packet index (u16 LE)" }
      - { id: start, type: a4_start_head, if: seq_marker == 0 }
      - { id: value, size-eos: true, doc: "this frame's value chunk (checksum is outside this body substream); concatenate across frames (START@7, MIDDLE/END@3)" }
  a4_start_head:
    seq:
      - { id: marker, contents: [0x01] }
      - { id: packet_count, type: u2le, doc: "total frame count incl. START and END (small case = 2)" }
      - { id: comm_byte, type: u1, doc: "device DIY/graffiti protocol code (H60A6 graffiti = 0x58) — byte6 here, vs byte4 in the 0xA3 a3_start form" }

  # ── proType 0xAC : single-request / multi-reply status read ─────────────
  multi_ac:
    doc: |
      Request: [0xAC, command, N, cmd_1 .. cmd_N] (N = count of requested sub-commands),
      e.g. AC 03 02 41 30 (H60A6 single-zone) / AC 03 03 41 30 A5 (dual-zone).
      Reply: a burst of 0xAC frames, tag @ byte1 (AbsController4BleSingleSendMultiBack.g/.i/.h):
      first chunk [AC, 00, pkts u2le, lastLen, cmd, sub, 12 data bytes @ offset 7], where pkts =
      total packet count incl. the first and the last chunk and lastLen = valid bytes in the last
      chunk; middle chunks (tag = position) 17 data bytes @ offset 2; the last chunk (tag 0xFF, or
      its position pkts-1, which isSameController/onResult also accept) is data-bearing: lastLen bytes
      @ offset 2. pkts == 1: lastLen (<= 12) bytes @ offset 7, no more chunks. Reassembled buffer is a
      TLV stream (type,len,value). Modelled here as the request form.
    seq:
      - { id: command, type: u1 }
      - { id: count, type: u1, doc: "N = number of requested sub-command types" }
      - { id: requested_types, type: u1, repeat: expr, repeat-expr: count, doc: "the N requested type bytes (e.g. 0x41, 0x30, 0xA5)" }
      - { id: pad, size-eos: true, doc: "zero padding to the 20-byte request frame" }

  # ── proType 0xE7 : encryption handshake ─────────────────────────────────
  handshake:
    seq:
      - id: op
        type: u1
        enum: handshake_op
      - id: data
        size-eos: true
        doc: |
          V1 (AES): random-padded, then AES-ECB(16)+RC4(4) with the fixed PSK.
          V2 (GCM): package-counter + AES-GCM. See GOVEE_BLE_GATT_PROTOCOL.md §9.

  # ── proType 0xA5 : mic / music live-colour push (NOT a 20-byte frame) ───
  # DELIBERATELY NOT a case of `frame`: this frame is SHORT (no zero-pad to 20) and its trailer is an
  # 8-bit ADDITIVE checksum (sum of all preceding bytes & 0xFF), not the XOR-over-0..18 that `frame`'s
  # `checksum` documents. AbsMicController.a builds [0xA5, cmd, data…] then appends sum8
  # (base2light/ble/mic/controller/AbsMicController.java:23-52). Parse a raw 0xA5 frame with this type.
  # Example (MicSetRgbController, cmd 0x02, sub 0x83): A5 02 83 R G B <sum8> — 7 bytes. The device side
  # is the length-exempt byte[0]==0xA5 path in the H60A1/H60A6 dispatchers (group_ctrl_rec_ble_pkg); H6006/H6008
  # catch A5 02 earlier, in the BLE event handler (see pro_type 0xa5 above and FIRMWARE_VALIDATION.md §3).
  mic_color_frame:
    seq:
      - { id: pro_type, contents: [0xa5] }
      - { id: command, type: u1, doc: "mic sub-command (e.g. 0x02 set-rgb, with sub 0x83; 0x90 colour-cmd)" }
      - { id: data, size: _io.size - 3, doc: "command-specific body (e.g. [0x83, R, G, B] for set-rgb)" }
      - { id: checksum, type: u1, doc: "8-bit ADDITIVE checksum: sum(all preceding bytes) & 0xFF (AbsMicController.a; NOT the frame XOR)" }

  # ══════════════════════════════════════════════════════════════════════════
  # Scene-upload VALUE layer — parse a REASSEMBLED (de-chunked) upload value.
  #
  # These types are deliberately NOT wired into `frame`. A scene upload spans
  # many 20-byte frames (0xA3: START + MIDDLE… ; 0xA4-MTU: START + MIDDLE… + END),
  # and the value is interleaved with a per-frame header + BCC in every frame.
  # Kaitai parses one contiguous stream, so it cannot itself strip-and-join
  # across frames. A client must FIRST reassemble the value (drop each frame's
  # header/terminator/BCC, concatenate the value chunks in seq order), THEN feed
  # that buffer to the matching type below.
  #
  # Pick the type by dialect / commByte (see GOVEE_BLE_GATT_PROTOCOL.md §4.4 and
  # SCENE_UPLOAD_ENCODING.md G2):
  #   dialect-B / DIY  (value = base64-decode(scenceParam)[1:], commByte = device code)
  #     H60A6  commByte 0x58 .... h60a6_scene_value  (auto-splits DIY vs graffiti)
  #     H6052  commByte 0x09 .... graffiti_v3_value
  #     RGBIC DIY-editor 0x03 ... graffiti_v2_value  (DiyGraffitiV2.g)
  #   dialect-A library scene (value = decoded scenceParam, per-type header strip)
  #     sceneType 2 rgbic    comType 2 ... rgbic_scene_value    (ScenesRgbIC, strip 0)
  #     sceneType 1 rgb      comType 1 ... rgb_scene_value      (ScenesRgb,   strip 0)
  #     sceneType 3 graffiti comType 7 ... scenes_graffiti_value (ParamsV1,   strip 2)
  #
  # Layouts verified byte-exact against the govee-ble-local catalogs:
  #   59 H60A6 graffiti · 14 H60A6 DIY · 6 H6052 gv3 · 57 H6047 rgbic · 135 H61A8 rgbic.

  # ── H60A6 type-5 auto-splitter (H60A6DiyParse is tried BEFORE H60A6GraffitiParse) ──
  h60a6_scene_value:
    doc: |
      H60A6 type-5 value = decode(scenceParam)[1:]. Disambiguated by the DIY length gate
      (Pro4H60A6Diy.c runs first): if u16le@0 + 2 == total size ⇒ DIY (diy_value, proType 0xA3);
      else ⇒ graffiti (graffiti_value, proType 0xA4-MTU). commByte 0x58 in both cases.
    seq:
      - id: body
        size-eos: true
        type:
          switch-on: is_diy
          cases:
            true: diy_value
            false: graffiti_value
    instances:
      declared_len: { pos: 0, type: u2le }
      is_diy: { value: 'declared_len + 2 == _io.size' }

  # ── H60A6 graffiti (H60A6GraffitiParse.toBytes) — proType 0xA4-MTU, commByte 0x58 ──
  graffiti_value:
    doc: 'H60A6 graffiti effect: background colour + per-layer colour-index pixel map.'
    seq:
      - { id: marker, contents: [0x20] }
      - { id: bg_r, type: u1 }
      - { id: bg_g, type: u1 }
      - { id: bg_b, type: u1 }
      - { id: brightness, type: u1 }
      - { id: show_type, type: u1 }
      - { id: layer_count, type: u1 }
      - { id: layers, type: graffiti_layer, repeat: expr, repeat-expr: layer_count }
  graffiti_layer:
    seq:
      - { id: rec_len, type: u2le }
      - { id: body, size: rec_len, type: graffiti_layer_body }
  graffiti_layer_body:
    seq:
      - { id: graffiti_type, type: u1 }          # 0x03
      - { id: inner_len, type: u2le }
      - { id: color_count, type: u2le }
      - { id: groups, type: graffiti_color_group, repeat: expr, repeat-expr: color_count }
      - { id: action, type: u1 }
      - { id: speed, type: u1 }
      - { id: bg_brightness, type: u1 }
      - { id: priority, type: u1 }
      - { id: duration, type: u2le }
      - { id: reserved, contents: [0, 0, 0, 0], doc: "always 00 00 00 00 (H60A6GraffitiParse.toBytes trailer pad)" }
  graffiti_color_group:
    seq:
      - { id: pixel_count, type: u2le }
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }
      - { id: pixel_indices, type: u1, repeat: expr, repeat-expr: pixel_count, doc: "LED/pixel indices painted by this colour (1 byte each)" }

  # ── H60A6 DIY (Pro4H60A6Diy.d) — proType 0xA3, commByte 0x58 ──
  diy_value:
    doc: 'H60A6 DIY effect: total-length prefix + background palette + per-layer sub-effect bodies.'
    seq:
      - { id: total_len, type: u2le }            # count of bytes after this field (== size - 2)
      - { id: layer_size, type: u1 }
      - { id: bg_color_size, type: u1 }
      - { id: bg_colors, type: sc_rgb, repeat: expr, repeat-expr: bg_color_size }
      - { id: bg_brightness, type: u1 }
      - { id: layers, type: diy_layer, repeat: expr, repeat-expr: layer_size }
  diy_layer:
    seq:
      - { id: layer_len, type: u2le }
      - { id: effect, size: layer_len, type: diy_sub_effect }
  diy_sub_effect:
    doc: |
      H60A6 DIY layer body = sub_effect_id + effect params (Layer.b, .../h60a6/diy/protocol/*). Map
      (pinyin = decompiled class name, English = translation): 1 LiuDong4Area (flow, zone) ·
      2 LiuDong4Line (flow, line) · 3 KuoSan (diffuse) · 4 XuanZhuan (rotate) · 5 SuiJiHuXi (random breathing) ·
      6 SuiJiLiuXing (random meteor) · 7 SuiJiJianBian (random gradient) · 8 Fade · 9 HuXi (breathing) ·
      10 ShanShuo (blink / twinkle). All except LiuDong4Line share the shape
      `[fixed params][color_count:u1][color_count × RGB]`; the arg to diy_palette is that fixed-param
      size. Verified: consumes every DIY layer body in the catalog exactly (ids 1,2,5,7 present).
    seq:
      - { id: sub_effect_id, type: u1 }
      - id: effect
        type:
          switch-on: sub_effect_id
          cases:
            1: diy_liudong_area     # LiuDong4Area = flow/zone
            2: diy_line             # LiuDong4Line = flow/line
            3: diy_kuosan           # KuoSan = diffuse
            4: diy_xuanzhuan        # XuanZhuan = rotate
            5: diy_suijihuxi        # SuiJiHuXi = random breathing
            6: diy_suijiliuxing     # SuiJiLiuXing = random meteor
            7: diy_suijijianbian    # SuiJiJianBian = random gradient
            8: diy_palette2         # Fade
            9: diy_palette2         # HuXi = breathing
            10: diy_palette2        # ShanShuo = blink/twinkle
  # DIY sub-effects (Layer.b, .../h60a6/diy/protocol/*): a fixed named header then a count-prefixed RGB palette.
  # Field ids are the decompiled setters (single-letter where the app assigns no further semantics). colours = 3-byte RGB.
  diy_liudong_area:      # 1 LiuDong4Area
    seq:
      - { id: i, type: u1 }
      - { id: t0, type: u1 }
      - { id: t1, type: u1 }
      - { id: x, type: u1 }
      - { id: w, type: u1 }
      - { id: h, type: u1 }
      - { id: r0, type: u1 }
      - { id: r1, type: u1 }
      - { id: q0, type: u1 }
      - { id: q1, type: u1 }
      - { id: m0, type: u1 }
      - { id: m1, type: u1 }
      - { id: u, type: u1 }
      - { id: v, type: u1 }             # bool
      - { id: color_count, type: u1 }
      - { id: colors, type: sc_rgb, repeat: expr, repeat-expr: color_count }
  diy_kuosan:            # 3 KuoSan
    seq:
      - { id: i, type: u1 }
      - { id: t, type: u1 }
      - { id: s, type: u1 }
      - { id: direction, type: u1 }
      - { id: l0, type: u1 }
      - { id: l1, type: u1 }
      - { id: q, type: u1 }
      - { id: r, type: u1 }             # bool
      - { id: color_count, type: u1 }
      - { id: colors, type: sc_rgb, repeat: expr, repeat-expr: color_count }
  diy_xuanzhuan:         # 4 XuanZhuan
    seq:
      - { id: i, type: u1 }
      - { id: direction, type: u1 }
      - { id: angle, type: u2le }       # getSignedInt(b2,b3)
      - { id: p, type: u1 }             # bool
      - { id: color_count, type: u1 }
      - { id: colors, type: sc_rgb, repeat: expr, repeat-expr: color_count }
  diy_suijihuxi:         # 5 SuiJiHuXi
    seq:
      - { id: i, type: u1 }
      - { id: o, type: u1 }
      - { id: n, type: u1 }             # bool
      - { id: color_count, type: u1 }
      - { id: colors, type: sc_rgb, repeat: expr, repeat-expr: color_count }
  diy_suijiliuxing:      # 6 SuiJiLiuXing
    seq:
      - { id: i, type: u1 }
      - { id: direction, type: u1 }
      - { id: r, type: u1 }
      - { id: s, type: u1 }
      - { id: q, type: u1 }
      - { id: p, type: u1 }             # bool
      - { id: color_count, type: u1 }
      - { id: colors, type: sc_rgb, repeat: expr, repeat-expr: color_count }
  diy_suijijianbian:     # 7 SuiJiJianBian
    seq:
      - { id: i, type: u1 }
      - { id: l0, type: u1 }
      - { id: l1, type: u1 }
      - { id: color_count, type: u1 }
      - { id: colors, type: sc_rgb, repeat: expr, repeat-expr: color_count }
  diy_palette2:          # 8 Fade / 9 HuXi / 10 ShanShuo
    seq:
      - { id: i, type: u1 }
      - { id: m, type: u1 }
      - { id: color_count, type: u1 }
      - { id: colors, type: sc_rgb, repeat: expr, repeat-expr: color_count }
  diy_line:
    doc: 'LiuDong4Line (id 2, flow/line): [i, direction, on] + count-prefixed byte list + [l0,l1,r,t,s] + RGB palette.'
    seq:
      - { id: speed, type: u1 }           # i()
      - { id: direction, type: u1 }
      - { id: on_flag, type: u1 }         # bool (id 'on' avoided: YAML-boolean)
      - { id: seg_count, type: u1 }
      - { id: seg, type: u1, repeat: expr, repeat-expr: seg_count, doc: "per-segment index bytes" }
      - { id: l0, type: u1 }
      - { id: l1, type: u1 }
      - { id: r, type: u1 }
      - { id: t, type: u1 }               # bool
      - { id: s, type: u1 }               # bool
      - { id: color_count, type: u1 }
      - { id: colors, type: sc_rgb, repeat: expr, repeat-expr: color_count }

  # ── H6052 type-5 (DiyGraffitiV3.a) — proType 0xA3, commByte 0x09 ──
  graffiti_v3_value:
    doc: 'H6052 graffiti-v3 effect: brightness + base colour + per-layer colour-index pixel map.'
    seq:
      - { id: brightness, type: u1 }
      - { id: base_r, type: u1 }
      - { id: base_g, type: u1 }
      - { id: base_b, type: u1 }
      - { id: layer_count, type: u1 }
      - { id: layers, type: gv3_layer, repeat: expr, repeat-expr: layer_count }
  gv3_layer:
    seq:
      - { id: body_len, type: u2le }             # == layer byte-length - 2
      - { id: body, size: body_len, type: gv3_layer_body }
  gv3_layer_body:
    seq:
      - { id: speed, type: u1 }
      - { id: action, type: u1 }
      - { id: priority, type: u1 }
      - { id: color_count, type: u1 }
      - { id: groups, type: gv3_color_group, repeat: expr, repeat-expr: color_count }
  gv3_color_group:
    seq:
      - { id: pixel_count, type: u1 }
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }
      - { id: pixel_indices, type: u1, repeat: expr, repeat-expr: pixel_count, doc: "LED/pixel indices painted by this colour (1 byte each)" }

  # ── RGBIC DIY-editor graffiti (DiyGraffitiV2.g) — proType 0xA3, commByte 0x03 ──
  #    START frame byte-exact vs plaintext btsnoop (H61A8, BCC 0x39); full payload
  #    (group_count groups) inferred from the g() loop, not observed end-to-end.
  graffiti_v2_value:
    doc: |
      General RGBIC DIY-graffiti value (DiyGraffitiV2.g; shared by RgbIcGraffitiShare0x08 and
      DIYGraffitiParser). Distinct from the dialect-A library rgbic format (rgbic_scene_value).
      commByte is device-specific (H61A8 = 0x03). Header + flat colour-index group list.
    seq:
      - { id: sub_effect, type: u1 }
      - { id: speed, type: u1 }
      - { id: brightness, type: u1 }
      - { id: base_r, type: u1 }
      - { id: base_g, type: u1 }
      - { id: base_b, type: u1 }
      - { id: group_count, type: u1 }
      - { id: groups, type: gv2_color_group, repeat: expr, repeat-expr: group_count }
  gv2_color_group:
    seq:
      - { id: pixel_count, type: u1 }
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }
      - { id: pixel_indices, type: u1, repeat: expr, repeat-expr: pixel_count, doc: "LED/pixel indices painted by this colour (1 byte each)" }

  # ── dialect-A rgbic library scene (ScenesRgbIC) — proType 0xA3, comType 2 ──
  rgbic_scene_value:
    doc: |
      dialect-A rgbic library scene (ScenesRgbIC.isValidProtocolBytes) = decoded scenceParam VERBATIM.
      effect_count, then that many length-prefixed effect records (fully structured — see
      rgbic_effect_record). Verified: parses all 57 H6047 + 135 H61A8 catalog scenes with exact consumption.
    seq:
      - { id: effect_count, type: u1 }
      - { id: effects, type: rgbic_effect, repeat: expr, repeat-expr: effect_count }
  rgbic_effect:
    seq:
      - { id: sub_len, type: u1 }
      - { id: record, size: sub_len, type: rgbic_effect_record }
  rgbic_effect_record:
    doc: |
      One rgbic effect record — FULLY interpreted (source of truth = ParamsV2.RgbICEffect parser :967 /
      serializer k() :706; ScenesRgbIC.f :3337 is only a length-validator, which is why an earlier draft
      mislabeled these bytes). Layout: style byte (packed nibbles) · mode + mode-dependent 2-byte value ·
      a brightness block (bright_count × 6-byte BrightnessEffect) · a ColorEffect (colour-IC byte + speed +
      duration + the RGB palette = the ACTUAL colours) · then InAreaMove(3) + AreaMove(4) movement blocks.
      Verified: parses all 327 dialect-A rgbic catalog scenes (H6047/H61A8/H6641) with exact consumption;
      colours decode correctly (e.g. H6047 "Action" → red then blue).
    seq:
      - { id: style, type: u1, doc: "packed nibbles hi|lo (ParamsV2 e()/b(); ScenesRgbIC.h) — speed / order" }
      - { id: mode, type: u1, doc: "f49707c; selects mode_val meaning" }
      - { id: mode_val, type: u2le, doc: "mode-dependent value: mode 0/1 = signed u16 (f49708d/f49709e); mode 2/3 = two bytes (f49710f/g or f49712h/i)" }
      - { id: bright_algo, type: u1, doc: "brightness algorithm/type byte (ScenesRgbIC.d high/low nibbles)" }
      - { id: bright_count, type: u1 }
      - { id: brightness_effects, type: brightness_effect, repeat: expr, repeat-expr: bright_count }
      - { id: color_ic, type: u1, doc: "makeColorIcByte: packed order + colour-type (ScenesRgbIC.e)" }
      - { id: speed, type: u1 }
      - { id: duration, type: u1 }
      - { id: color_count, type: u1 }
      - { id: colors, type: sc_rgb, repeat: expr, repeat-expr: color_count }
      - { id: in_area_move, type: in_area_move }
      - { id: area_move, type: area_move }
  brightness_effect:
    doc: "ParamsV2.BrightnessEffect (parser :269): 6 unsigned bytes f49670a..f49675f (level/index params; the app stores each as its own field but does not attach further semantics)."
    seq:
      - { id: f_a, type: u1 }
      - { id: f_b, type: u1 }
      - { id: f_c, type: u1 }
      - { id: f_d, type: u1 }
      - { id: f_e, type: u1 }
      - { id: f_f, type: u1 }
  in_area_move:
    doc: "ParamsV2.InAreaMoveEffect.c() :524: move_flags (canMove<<4 | order) + 2 movement params (f49695c/d)."
    seq:
      - { id: move_flags, type: u1 }
      - { id: p_c, type: u1 }
      - { id: p_d, type: u1 }
  area_move:
    doc: "ParamsV2.AreaMoveEffect.c() :233: move_flags (canMove<<4 | order) + 3 movement params (f49663c/d/e)."
    seq:
      - { id: move_flags, type: u1 }
      - { id: p_c, type: u1 }
      - { id: p_d, type: u1 }
      - { id: p_e, type: u1 }

  # ── dialect-A rgb library scene (ScenesRgb) — proType 0xA3, comType 1 ──
  rgb_scene_value:
    doc: |
      dialect-A rgb library scene (ScenesRgb.isValidProtocolBytes) = decoded scenceParam verbatim.
      CONFIG-TABLE-DRIVEN: byte0 indexes a hardcoded config e(byte0) = [_, mode, color_count]
      (ScenesRgb.e — NOT present in the value bytes) which selects the body shape:
        mode 1: size == 2 + effect_count*(color_count + 5)                     (effect_count @ byte1)
        mode 0: size == effect_count*5 + 3 + color_count*color_num             (color_num @ byte[eff*5+2])
      Because mode/color_count come from the external table, the body is NOT self-describing from bytes
      alone; modelled as byte0 + opaque remainder. See GOVEE_BLE_GATT_PROTOCOL.md §4.4.
    seq:
      - { id: config_tag, type: u1 }
      - { id: rest, size-eos: true, doc: 'body per the config table above (mode 0/1); not self-describing from bytes alone' }

  # ── dialect-A graffiti library scene (ParamsV1 / DiyProtocolParser.parserParamsV1) — proType 0xA3, comType 7 ──
  #    H6052 sceneType-3. Wire value = decode(scenceParam)[2:] (drops the [0x01, effect_hi] header; ScenesOp.n():483).
  scenes_graffiti_value:
    doc: |
      dialect-A graffiti library scene. Wire value = decode(scenceParam)[2:] (strips the 0x01 header +
      effect_hi byte; ScenesOp.n():483). Structure = ParamsV1.a() / DiyProtocolParser.parserParamsV1:1345 —
      effect_lo, speed, brightness, background RGB, seg_count, then seg_count colour groups. Verified:
      parses all 25 H6052 sceneType-3 catalog scenes with exact consumption.
    seq:
      - { id: effect_lo, type: u1 }
      - { id: speed, type: u1 }
      - { id: brightness, type: u1 }
      - { id: bg_r, type: u1 }
      - { id: bg_g, type: u1 }
      - { id: bg_b, type: u1 }
      - { id: seg_count, type: u1 }
      - { id: groups, type: scenes_graffiti_group, repeat: expr, repeat-expr: seg_count }
  scenes_graffiti_group:
    seq:
      - { id: color_count, type: u1 }
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }
      - { id: pixel_indices, type: u1, repeat: expr, repeat-expr: color_count, doc: 'LED/pixel indices painted by this colour (1 byte each)' }

  # ── shared 3-byte RGB triple ──
  sc_rgb:
    seq:
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }

  # ── per-segment COLOUR read-back, mechanism (A) — one reassembled 0xA5 group ──
  #  Feed the TLV value (bytes after [type 0xA5, len]) from a reassembled 0xAC status reply.
  #  Devices: H60A6, H6047, H6641 (base2light Controller4ColorInfoByGroup). record_count and the total
  #  segment count come from a per-SKU table (getColorPieceSize / getOneGroupColorSize=4), NOT the stream.
  #  Mechanism (B) H61A8 (0xAA-notify BulbGroupColor: 0xA2=3-byte / 0xA5=4-byte) and (C) H6052 (0x0D
  #  mode-report single colour) are NOT this type — see devices.yaml "IC / segment" note.
  color_group_read:
    doc: |
      One 0xA5 group of the per-segment colour reply: group_index then record_count records. PARAMETRIC —
      the caller passes the two bits that are NOT in the byte stream, and the type switches internally:
        * record_count  = records in this group (= oneGroupColorSize, usually 4; last group = N - count*(g-1)).
        * has_brightness = 1 -> 4-byte [brightness,R,G,B] (color_group_record); 0 -> 3-byte [R,G,B]
          (color_group_record_rgb). Selected per SKU by supportPartBrightness. ALL CURATED devices
          (H60A6/H6047/H6641) are has_brightness=1 (4-byte). 3-byte is only non-curated colour-only families
          (tvlightv2, rgblight, bulblightstringv1, h7022, h6160). [record_size can't be auto-detected from the
          TLV len: len 13 == 4*3+1 == 3*4+1, so 3B and 4B are ambiguous — hence the explicit param.]
    params:
      - { id: record_count, type: u1 }
      - { id: has_brightness, type: u1 }
    seq:
      - { id: group_index, type: u1 }
      - id: records
        repeat: expr
        repeat-expr: record_count
        type:
          switch-on: has_brightness
          cases:
            1: color_group_record          # 4-byte [brightness,R,G,B]
            0: color_group_record_rgb       # 3-byte [R,G,B]
  color_group_record:
    seq:
      - { id: brightness, type: u1 }
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }
  color_group_record_rgb:            # 3-byte variant (no brightness): H6641 non-part-brightness / H61A8 V1
    seq:
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }

  # ─────────── PARAMETRIC client-invoked types (Tier C: discriminator is NOT in the frame) ───────────
  # A type reached through a frame `switch-on` case cannot receive a parameter, so these are STANDALONE:
  # the client invokes them directly, passing the one bit of context it holds (from devices.yaml). This
  # keeps 100% of byte STRUCTURE in the grammar — nothing is hand-parsed by the client.
  op15_color_typed:    # SubModeColorV1 op 0x01 WRITE — typed counterpart of the size-eos `op15_color`
    doc: |
      op 0x01 colour write, parametric on the opType the client is issuing (SubModeColorV1.getWriteBytes):
        variant 1  = basic            -> [r,g,b, seg_mask]
        variant 12 = H60A1/H60A6 RGB  -> [r,g,b, 00 00 00 00 00, seg_mask]
        variant 11 = H60A1/H60A6 CCT  -> [r,g,b, kelvin(BE), tintR, tintG, tintB, seg_mask]
      (The frame-path `op15_color` is size-eos because a captured frame carries no opType; use THIS when you
      are building/decoding a known write. Devices.yaml `color_scheme` + the write op pick the variant.)
    params:
      - { id: variant, type: u1 }
    seq:
      - { id: r, type: u1 }
      - { id: g, type: u1 }
      - { id: b, type: u1 }
      - { id: zeros, size: 5, if: 'variant == 12' }
      - { id: kelvin, type: u2be, if: 'variant == 11' }
      - { id: tint_r, type: u1, if: 'variant == 11' }
      - { id: tint_g, type: u1, if: 'variant == 11' }
      - { id: tint_b, type: u1, if: 'variant == 11' }
      - { id: seg_mask, type: u2le }
  mode_color_0d_typed:   # 0x0D colour-report body — typed counterpart of mode_color_0d_report
    doc: |
      0x0D colour mode-report body, parametric on the device's parse strategy (devices.yaml `mode_0d_kind`):
        kind 0 = default SubMode4Color strategy  -> [gradual_flag, kelvin u16-be]          (mode_0d_kind: cct)
        kind 1 = H6052 custom strategy           -> [R, G, B]                               (mode_0d_kind: rgb)
        kind 2 = rgblight (H6188) and 0x0d bulb  -> [R, G, B, kelvin u16-be, tintR, tintG, tintB]
                 (H6006 / H6008) strategy                                        (mode_0d_kind: rgb_kelvin_tint)
      Kind 2 is ModeParseStrategyConfig.a, the same rule in two splits: rgblight (installed by VM4Light.v5
      for H6188) and bulblight (the non-H6001 branch). kelvin = getSignedShort(vb[3], vb[4]); a non-zero
      kelvin makes the strategy show the tint (vb[5..7]) rather than R/G/B, and kelvin 0 the R/G/B. The body
      mirrors the color_cct_0d write.
      The bulbs' path (Govee Home 7.7.02): bulblightv3 / bulblightv1 pact Model -> bulblight AcNewDetail ->
      VM4Light ($connectBleSuc$1 -> m5 -> j5 installs ModeParseStrategyConfig.a) ->
      OpInfo4Detail.parseModeValidBytes, which hands sub-modes 0x15 / 0x0b / 0x02 / 0x0d to that strategy and
      takes colour-temperature mode only for a kelvin > 0; H6188 the same (VM4Light$connectBleSuc$1 -> A5 ->
      v5). So a kelvin of 0x8000 or more, negative there, shows the tint as an RGB colour, not CCT; is_cct is
      the strategy's tint test (!= 0), not that one.
      Device side, static firmware analysis (not hardware-verified): the H6006 and H6008 firmware build
      the 0x0D reply as [0D][R][G][B][kelvin hi][kelvin lo] with every later byte zero, so the tint fields
      always arrive as 0 (H6006: firmware-h6006/decompiled/H6006_ble_dispatcher.c case 0xd at line 634,
      bytes at lines 645-649; H6008: firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c case 0xd at line
      396, bytes at lines 401-405). The kelvin is pre-zeroed there, so 0 most likely means RGB mode.
      Kind 1 is H6052 (tablelampv1, goodsType 22): Model.u -> H6052NewDetailActivity -> H6052NewDetailVM ->
      H6052InfoDetail.G, whose constructor installs a strategy reading only vb[0..2]; the kelvin bytes are
      never read. (tablelampv1 ble.SubModeColor.parse, which does read a kelvin at vb[3..4], serves the IoT /
      group op dialogs, not the control screen.)
    params:
      - { id: kind, type: u1 }
    seq:
      - { id: gradual_flag, type: u1, if: 'kind == 0' }
      - { id: kelvin, type: u2be, if: 'kind == 0', doc: "kind 0 kelvin, the RAW 16 bits (big-endian). The app reads it SIGNED (BleUtil.getSignedShort(vb1,vb2), SubMode4Color:47): use kelvin_signed" }
      - { id: r, type: u1, if: 'kind == 1 or kind == 2' }
      - { id: g, type: u1, if: 'kind == 1 or kind == 2' }
      - { id: b, type: u1, if: 'kind == 1 or kind == 2' }
      - { id: report_kelvin, type: u2be, if: 'kind == 2', doc: "kind 2 kelvin, the RAW 16 bits (big-endian); 0 = RGB mode. The app reads it SIGNED (BleUtil.getSignedShort(vb3,vb4), ModeParseStrategyConfig.a): use report_kelvin_signed" }
      - { id: tint_r, type: u1, if: 'kind == 2', doc: "kind 2: Kelvin->RGB tint the app displays in CCT mode" }
      - { id: tint_g, type: u1, if: 'kind == 2' }
      - { id: tint_b, type: u1, if: 'kind == 2' }
    instances:
      is_cct:
        value: 'kind == 2 and report_kelvin != 0'
        doc: "kind 2 only: the strategy shows the tint (report_kelvin != 0, ModeParseStrategyConfig.a); OpInfo4Detail.parseModeValidBytes takes CCT only for report_kelvin_signed > 0"
      kelvin_signed:
        value: 'kelvin >= 0x8000 ? kelvin - 0x10000 : kelvin'
        if: 'kind == 0'
        doc: "kind 0 kelvin as the app reads it (getSignedShort sign-extends: 0x8000-0xFFFF reads as -32768..-1)"
      report_kelvin_signed:
        value: 'report_kelvin >= 0x8000 ? report_kelvin - 0x10000 : report_kelvin'
        if: 'kind == 2'
        doc: "kind 2 kelvin as the app reads it (getSignedShort sign-extends: 0x8000-0xFFFF reads as -32768..-1)"
      is_cct_positive:
        value: 'kind == 2 and report_kelvin_signed > 0'
        doc: "kind 2 only: CCT by the signed > 0 rule of OpInfo4Detail.parseModeValidBytes and of the rgb_kelvin_0d decoders (cube CubeNewInfo4Detail.U; h60a1 Info4Detail.Q:416-427 for H60A1/H60A4/H60A6), which read only [R,G,B,kelvin] and never the tint bytes. A kelvin <= 0 is RGB from r/g/b."
