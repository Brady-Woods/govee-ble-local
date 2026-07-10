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
    0x14: gradual
    0x20: wifi_hard_version
    0x23: new_timer
    0x24: read_ic_point
    0x26: take_photo_or_colortemp_type
    0x30: light_direction_or_zone
    0x31: camera_position
    0x32: check_camera
    0x34: swap_light
    0x36: compose_light_switch
    0x40: ic_num
    0x41: on_off_memory
    0x44: calibration
    0x46: check_ic
    0xa2: bulb_string_color_read
    0xa3: gradual_wifi_ble
    0xa5: local_color_read
    0xa9: white_balance
    0xaa: check_light
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
    0x42: write_check_ic
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
            'command::compose_light_switch': bar_switch_read
            'command::secret_read': secret_read_reply
            'command::plug_spec': plug_spec_read
            'command::bulb_string_color_read': bulb_group_color_read      # 0xA2 mechanism-B V1 (H61A8)
            'command::local_color_read': bulb_group_color_read_v2         # 0xA5 mechanism-B V2 (H61A8)
        doc: >-
          read selector (request) or device-state echo (REPLY). body[0] == the controller's validBytes[0]
          (frame offset 2). Reply bodies are now typed for switch / brightness / device_info / bar / secret /
          plug_spec (below); mode 0x05 => mode_read. These model the REPLY; a read REQUEST carries only the
          selector (e.g. AA 07 10, AA 05 01) and is built by the client, not parsed inbound.

  mode_read:
    doc: |
      Mode (0x05) read. Byte 0 is EITHER a request selector (0x01) OR a reply sub-mode byte
      (0x15 color / 0x04 scene / 0x13 music / 0x0d h60a6-color). Disambiguate by value: 0x01 => request;
      a known sub-mode => reply.
      - 0x15 CCT reply => cct_read_reply: [op=01][kHi][kLo], kelvin big-endian @ frame bytes 4-5; DROPS
        the FF FF FF white point the 0x33 write carries.
      - 0x0d (and 0x15 on the LEGACY Mode/SubModeColorV1 stack): parse reads ONLY op/gradual (bArr[0]==1)
        — no kelvin/RGB decode; 0x0d is effectively write-only. Kelvin is decoded only by the compose
        stack's 0x15 path (cct_read_reply). So no distinct 0x0d reply body is modelled.
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
    seq:
      - { id: op, type: u1, doc: "0x01 = CCT / white-point mode" }
      - { id: kelvin, type: u2be, doc: "colour temperature in Kelvin (big-endian, hi-first)" }
      - { id: trailing, size-eos: true, doc: "remaining frame bytes after kelvin (padding; unused for CCT read-back)" }

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
          note — H6052 DOES read colour back, as these 3 bytes. Source: H6052InfoDetail.parseModeValidBytes
          :323-346 (validBytes[0]==0x0D -> colour strategy on validBytes[1:]) + :141-147.
        * DEFAULT SubMode4Color strategy (most other families) => [gradual_flag, kelvin u16-be], NOT RGB
          (iParseStrategy: q(vb0==1); s(getSignedShort(vb1,vb2))). For those devices reinterpret r as the
          gradual flag and g,b as the big-endian kelvin.
      Modelled as the H6052 RGB form (the curated device). Trailing frame bytes are zero padding (unparsed).
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

  # ── 0xAA READ replies (body[0] = validBytes[0] = frame offset 2). Verified vs each controller's parseValidBytes. ──
  switch_read_reply:
    doc: "AA 01 reply (SwitchController.parseValidBytes): state @ body[0]. LIGHTS: 0=off else on. PLUGS (h5080 SwitchControllerV2): this SAME 0x01 reply is a RELAY BITMASK (bit i = relay i on), not boolean — interpret per device."
    seq:
      - { id: state, type: u1 }
  brightness_read_reply:
    doc: "AA 04 reply (BrightnessController.parseValidBytes): raw brightness 0-255. NO 0-255<->0-100 rescale in the BLE codec (percent mapping is UI-layer)."
    seq:
      - { id: brightness, type: u1 }
  bar_switch_read:
    doc: "AA 36 reply (h6047 ComposeLightController): [left,right] on/off (bArr[0]==1 / bArr[1]==1). The 33 36 write uses the same [left,right]."
    seq:
      - { id: left, type: u1 }
      - { id: right, type: u1 }
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
      AA 07 reply (BasicInfoController / BasicWifiInfoController / SnController). selector @ body[0] picks the
      body. 3-byte version fields render as X.YY.ZZ (major . %02d . %02d).
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
  device_info_basic:      # AA 07 10
    seq:
      - { id: uid, size: 8, doc: "8-byte device UID (atomic id; rendered MAC-style, leading 00:00 stripped)" }
      - { id: sw_version, type: version3 }
      - { id: hw_version, type: version3 }
      - { id: dsp_version, type: u2le, doc: "DSP version (LE); 0 when absent (short replies pad to 0)" }
      - { id: pad, size-eos: true, doc: "zero padding to the 20-byte frame" }
  device_info_wifi:       # AA 07 11
    seq:
      - { id: wifi_mac, size: 6, doc: "6-byte Wi-Fi MAC (atomic id)" }
      - { id: wifi_sw_version, type: version3 }
      - { id: wifi_hw_version, type: version3 }
      - { id: pad, size-eos: true, doc: "zero padding to the 20-byte frame" }
  device_info_sn:         # AA 07 02
    seq:
      - { id: uid, size: 8, doc: "8-byte device UID (atomic id)" }
      - { id: pad, size-eos: true, doc: "zero padding to the 20-byte frame" }
  version3:
    doc: "3-byte firmware version rendered X.YY.ZZ (major . %02d . %02d); BasicInfoController.u:73."
    seq:
      - { id: major, type: u1 }
      - { id: minor, type: u1 }
      - { id: patch, type: u1 }

  # ── 0xAC status REPLY: parse the REASSEMBLED buffer (client de-chunks first) as a TLV stream ──
  #  Walker = Compose4BaseInfoSingleRead.u(): [type, len, value(len)], advance i += 2+len. No fixed header —
  #  offset 0 is the first type. Known types: 0x01 switch, 0x04 brightness, 0x05 mode block, 0x07 device-info,
  #  0x11 sleep, 0x12 wakeup, 0x23 timers, 0x30 zone on/off (2 bits), 0x41 seg/IC info, 0xA5 colour group.
  #  For 0xA5 feed value to color_group_read (record_count from the per-SKU count, see devices.yaml).
  status_reply:
    doc: |
      Reassembled 0xAC status reply = a sequence of [type, len, value] TLVs. The buffer is a plain
      byte stream (the client de-chunks first), so it IS fully Kaitai-expressible — see the GAP on
      status_tlv for the nested value types (0x07 device-info, 0x05 mode) still left raw.
    seq:
      - { id: tlvs, type: status_tlv, repeat: eos }
  status_tlv:
    seq:
      - { id: type, type: u1 }
      - { id: len, type: u1 }
      - id: value
        size: len
        type:
          switch-on: type
          cases:
            0x01: status_switch
            0x04: status_brightness
            0x30: status_zone
            0x41: status_seg_info
        doc: |
          Typed for 0x01/0x04/0x30/0x41. GAP — the nested TLV VALUES below are Kaitai-expressible
          (this is the REASSEMBLED buffer, a plain byte stream) but are NOT yet modelled; called out
          for Java-source modelling so the runtime heuristics can be replaced by spec-driven parsing:
            * 0x07 device-info — a nested [selector, ...] block; observed selectors 0x06 = BLE MAC
              (6B, little-endian), 0x10 = basic [uid, sw, hw], 0x11 = wifi [mac, sw, hw], mirroring the
              aa 07 device_info_read sub-types. UNTIL modelled, the client extracts wifi_mac +
              hardware_version by a MAC-anchor heuristic (wire.reassemble.anchor_device_info: find the
              device's own BLE MAC little-endian, then +9..15 = MAC, +20..23 = version). Model from
              Compose4BaseInfoSingleRead.u + the per-selector 0x07 sub-parser.
            * 0x05 mode — a nested mode block (sub-mode + params), same shape as the 0x05 frame body.
            * 0xA5 colour group -> color_group_read, but record_count is per-SKU (externally-keyed, not
              in the TLV), so the client walks it (wire.reassemble.parse_status), not switched here.
          Unmatched types stay raw.
  status_switch:     { seq: [ { id: on, type: u1 } ] }
  status_brightness: { seq: [ { id: brightness, type: u1 } ] }
  status_zone:       { doc: "0x30 in the 0xAC reply: zone0 = bit0 of byte0, zone1 = bit0 of byte1 (VM4LightH60A6.o:103)", seq: [ { id: zone_a, type: u1 }, { id: zone_b, type: u1 } ] }
  status_seg_info:   { doc: "0x41 seg/IC info (VM4LightH60A6.o:99 reads byte1)", seq: [ { id: b0, type: u1 }, { id: ic_or_seg, type: u1 } ] }

  switch_payload:
    seq:
      - id: state
        type: u1
        doc: Lights 0x01/0x00. Plugs (SwitchControllerV2) use a relay mask 0x10|on, 0x20, 0x40, 0xF0.

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
      Interpret per device (flags_a = that generic flags byte).
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
      - { id: packet_count, type: u1, doc: "total frame count (start + middles + 0xFF end, data-bearing)" }
      - { id: comm_byte, type: u1, doc: "commByte @ byte4: legacy scene version constant (V1=1 V2=2 V3=7 V6=12) OR a device DIY/graffiti protocol code (H60A6 = 0x58). NOT value[0]|0x08. NOTE: the graffiti scene builder can instead emit proType 0xA4 (multi_a4), where commByte moves to byte6. See GOVEE_BLE_GATT_PROTOCOL.md 4.4" }
      - { id: inline_value, size: 14, doc: "first (15 - commBytes.length) value bytes; 14 for a 1-byte commByte. Value = controller.getValue() (DIY/graffiti = re-encoded toBytes(), not the raw cloud blob). Opaque here — reassemble across frames then parse with the scene-upload VALUE types below (diy_value / graffiti_v3_value / rgbic_scene_value / rgb_scene_value / graffiti_v2_value per commByte)" }

  # ── proType 0xA4 : MTU-sized MultipleControllerCommV1 dialect (makeSendBytesMtu, :409) ──
  # RARELY USED. Reached ONLY via Compose4DefWrite4Multi.makeWriteController when command == 88 (0x58)
  # AND NewDiyEditConfig.isUseMtuCommand(effectByte) is true (effectByte in {0x5E,0x5D,0x20}). None of the
  # curated devices (H60A6/H61A8/H6047/H6641/H6008/H6052/H6006) reach it on their normal BLE routes —
  # they all emit 0xA3. Frame size = MtuConfig.getAvailableMtuSize = max(getMtu(sku)-3, 20), default 20
  # (an app-internal per-SKU store, NOT the GATT-negotiated ATT MTU), so packets are 20 bytes unless a
  # larger MTU was cached via saveMtuData. This models the START packet at MTU=20 (a 20-byte frame):
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
      Reply: a burst of 0xAC frames, tag @ byte1; first chunk 12 data bytes @ offset 7,
      subsequent 17 data bytes @ offset 2, terminator tag 0xFF. Reassembled buffer is a
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
      - { id: on, type: u1 }              # bool
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
      One 0xA5 group of the per-segment colour reply: group_index then record_count records. record_count
      is supplied by the caller (= 4, except the last group = N - 4*(groups-1)); it is NOT the TLV length.
      Records are 4-byte [brightness,R,G,B] on part-brightness SKUs; on SKUs without it (e.g. H6641 ".p"
      firmware path) they are 3-byte [R,G,B] with no brightness — parse those with color_group_record_rgb.
    params:
      - { id: record_count, type: u1 }
    seq:
      - { id: group_index, type: u1 }
      - { id: records, type: color_group_record, repeat: expr, repeat-expr: record_count }
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
