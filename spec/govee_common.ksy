meta:
  id: govee_common
  title: Govee BLE shared enums and context-free leaf types
  license: MIT
  ks-version: '0.11'
  bit-endian: le
  endian: be
doc: |
  LIBRARY (no seq, never an entry point): the enums and context-free leaf
  types that the Govee spec files share.
types:
  rgb:
    doc: |
      One colour [r][g][b], each byte unsigned.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:145-158
      - base-classes10/com/govee/base2light/ble/BleUtil.java:333-335
    seq:
      - id: r
        type: u1
      - id: g
        type: u1
      - id: b
        type: u1
  bri_rgb:
    doc: |
      One 4-byte colour record [brightness][r][g][b], every byte unsigned.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:160-175
    seq:
      - id: brightness
        type: u1
        doc: per-segment brightness
      - id: r
        type: u1
      - id: g
        type: u1
      - id: b
        type: u1
  level:
    doc: |
      One raw brightness byte as the device reports it, with no rescale
      (curated SKUs send a percent).
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:293-295
      - base-classes11/com/govee/base2light/kt/general_parser/DefParser.java:200
    seq:
      - id: level
        type: u1
  version3:
    doc: |
      3-byte binary firmware version [major][minor][patch] of the 0x10 / 0x11
      device-info records.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:439-457
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:363-368
    seq:
      - id: major
        type: u1
      - id: minor
        type: u1
      - id: patch
        type: u1
    instances:
      is_zero:
        value: major == 0 and minor == 0 and patch == 0
        doc: all three bytes 0 = no version
      major_signed:
        value: 'major >= 0x80 ? major - 0x100 : major'
      minor_signed:
        value: 'minor >= 0x80 ? minor - 0x100 : minor'
      patch_signed:
        value: 'patch >= 0x80 ? patch - 0x100 : patch'
      text:
        value: >-
          is_zero ? '' : major_signed.to_s + '.' +
          (minor_signed > 9 ? '' : '0') + minor_signed.to_s + '.' +
          (patch_signed > 9 ? '' : '0') + patch_signed.to_s
        doc: |
          the version as the app renders it, e.g. 1 / 0 / 41 -> '1.00.41'
  version_text:
    doc: |
      An ASCII version string, NUL-terminated in the reply's zero fill; the
      client trims chars <= 0x20 at both ends (PROCEDURAL).
    doc-ref:
      - base-classes10/com/govee/base2light/ble/BleUtil.java:315-327
      - base-classes10/com/govee/base2kt/utils/BleUtils.java:239-269
      - base-classes10/com/govee/base2kt/utils/BleProtocolUtils.java:47-55
    seq:
      - id: raw
        terminator: 0
        eos-error: false
        doc: |
          the bytes before the first NUL, or all of them (H5083 fills the frame)
    instances:
      text:
        value: raw.to_s('UTF-8')
        doc: UTF-8; raises (only when read) on bytes that are not UTF-8
  mac:
    doc: |
      A 6-byte MAC: display order in aa 14 (Wi-Fi); FIRMWARE-SOURCED aa 07 06
      (BLE MAC) is reversed on H6006/H6008/H60A1/H60A6, display order on H5083.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/WifiMacController.java:28-46
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerWifiMacAddress.java:27-30
      - H6006 ble_msg_get_ble_macaddress @0x9b03c988
    seq:
      - id: addr
        size: 6
    instances:
      is_set:
        value: addr != [0, 0, 0, 0, 0, 0]
        doc: |
          not all zero; the app rejects or blanks an all-zero MAC
        doc-ref:
          - base-classes10/com/govee/base2light/ble/controller/WifiMacController.java:39-46
          - base-classes11/com/govee/base2light/kt/general_controller/ControllerWifiMacAddress.java:44-52
          - split_h6047/com/govee/h6047/newdetail/vm/AfterConnectSuccessReadWifiInfo.java:97-101
  section_count:
    doc: |
      The H6062 IC (lamp-bead) and section counts of the aa 41 reply and the
      EE 21 push.
    doc-ref:
      - split_h6062/com/govee/striplight/newdetail/ControllerIcSegmentNum.java:40-54
      - split_h6062/com/govee/striplight/ble/IcSegmentNumController.java:24-33
      - split_h6062/com/govee/striplight/ble/CheckIcNumParse.java:16-22
    seq:
      - id: ic_count
        type: s2
      - id: section_num
        type: s1
  timer_record:
    doc: |
      One 4-byte timer record [flags][hour][minute][repeat] (lights, h5080 plugs).
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerNewTimer.java:52-77
      - base-classes12/com/govee/base_h71xx/sku_base/adjust/component/bean/TimerInfo.java:39-50
      - base-classes12/com/govee/base_h71xx/sku_base/adjust/component/bean/TimerInfo.java:79-97
    seq:
      - id: flags
        type: u1
        doc: per family; H5083 high nibble 8 = enabled, low nibble 1 = on
      - id: hour
        type: u1
      - id: minute
        type: u1
      - id: repeat
        type: u1
        doc: repeat-day mask
  echo:
    doc: |
      The lossless tail of a message (request echo, stale bytes or zero fill);
      trailing zeros are stripped on read and restored on write.
    doc-ref:
      - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:247
      - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:228-233
      - firmware-h6006/decompiled/H6006_ble_dispatcher.c:285-290
    seq:
      - id: bytes
        size-eos: true
        pad-right: 0
  seg_mask:
    doc: |
      The segment mask of a colour or brightness write (bit k of byte j = 8j+k):
      2 bytes for <= 16 positions, else ceil(n / 8) bytes, cut to cap.
    doc-ref:
      - base-classes10/com/govee/base2kt/utils/BleUtils.java:406-438
      - base-classes10/com/govee/base2kt/utils/BleUtils.java:444-494
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:178-179
    params:
      - id: num_segments
        type: u2
        doc: |
          the piece count, rounded up to a multiple of 8 on the brightness path
      - id: cap
        type: u1
        doc: the most mask bytes the body carries
    seq:
      - id: bits
        type: b1
        valid:
          expr: not _ or _index < num_segments
        repeat: expr
        repeat-expr: len_mask * 8
        doc: |
          bit i = position i selected; a 1 at or past num_segments is invalid
    instances:
      len_full:
        value: 'num_segments <= 16 ? 2 : (num_segments + 7) / 8'
      len_mask:
        value: 'len_full > cap ? cap : len_full'
      is_truncated:
        value: len_full > cap
        doc: |
          positions cap*8 and up are dropped; some app writers throw instead
enums:
  direction:
    1:
      id: tx
      doc: |
        client to device: a write on 00010203-0405-0607-0809-0a0b0c0d2b11
    2:
      id: rx
      doc: |
        device to client: a notification on ...0a0b0c0d2b10
  pro_type:
    0x05:
      id: gid_info
      doc: |
        FIRMWARE-SOURCED: Matter gid-info write (H60A1/H60A6); reply has no XOR
      doc-ref:
        - H60A6 FUN_9b04c190 @0x9b04c190
        - H60A6 FUN_9b04da6c @0x9b04da6c
        - H60A6 FUN_9b04b8ac @0x9b04b8ac
    0x33:
      id: write
      -orig-id: SINGLE_WRITE
      doc: |
        tx: single-frame command; rx: ack [33][command][result], result 0 = ok
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:174
        - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:140-145
        - base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:66-68
    0x3a:
      id: write_read
      -orig-id: SINGLE_WRITE_READ
      doc: |
        the write-and-read variant of 0x33, with the same ack layout
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:177
        - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:142
    0x3f:
      id: multiple_write_v1_3f
      -orig-id: MULTIPLE_WRITE_V1_3F
      doc: |
        FIRMWARE-SOURCED, CAUTION: Matter-factory write units, n x 20 bytes per
        write (one on H6006); only END or a unit the walk rejects is answered
        [3F][op][status], and a write failing the shared BCC check gets nothing.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:35
        - H60A6 FUN_9b04c190 @0x9b04c190
        - H60A6 FUN_9b04b254 @0x9b04b254
        - H6006 _handle_write_msg @0x9b03d7a6
    0x66:
      id: factory_test
      doc: |
        FIRMWARE-SOURCED, CAUTION: factory test (66 01 zeroes, 66 02 echoes).
        H6047 echoes any 66 frame and leaves group control.
      doc-ref:
        - H6006 code @0x9b03d866-0x9b03d876
        - H60A6 FUN_9b04aa76 @0x9b04aa76
        - H6047 code @0x010064be-0x010064d8
    0xa1:
      id: multi_write
      -orig-id: MULTIPLE_WRITE
      doc: |
        chunked write [A1][comType][position][16 bytes]; ack result 0 = success
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:33
        - base-classes12/com/govee/ble/multi/MultiPackageManager.java:64-66
        - base-classes12/com/govee/ble/multi/MultiPackageManager.java:72-80
    0xa2:
      id: multi_read
      -orig-id: MULTIPLE_READ
      doc: |
        multi-packet read stream [A2][comType][position..]
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:32
        - base-classes12/com/govee/ble/multi/MultiPackageManager.java:60-62
        - base-classes12/com/govee/ble/multi/MultiPackageManager.java:82-124
    0xa3:
      id: multi_write_v1
      -orig-id: MULTIPLE_WRITE_V1
      doc: |
        scene / DIY multi-packet write (non-MTU); also the A3 08 IoT-status push
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:34
        - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Multi.java:69-70
        - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Multi.java:80-82
    0xa4:
      id: multi_write_v2
      -orig-id: MULTIPLE_WRITE_V2
      doc: |
        MTU-sized multi-packet write, only for command 0x58 (effects 5E, 5D, 20)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:36
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerMTUMulti.java:25
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerMTUMulti.java:32-34
    0xa5:
      id: mic
      doc: |
        the mic push, a short sum8 frame [A5][command][data..], not 20-byte XOR
      doc-ref:
        - base-classes10/com/govee/base2light/ble/mic/controller/AbsMicController.java:55
        - H6006 _system_ble_event_handler @0x9b03df50
        - H60A6 FUN_9b03c6b0 @0x9b03c6b0
    0xa6:
      id: mtu_multiple_write
      -orig-id: MTU_MULTIPLE_WRITE
      doc: |
        the second MTU multi-packet head; body not traced
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:31
        - base-classes11/com/govee/base2light/kt/ble/Compose4DefWrite4Multi.java:225
        - base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:1398
    0xaa:
      id: read
      -orig-id: SINGLE_READ
      doc: |
        tx: request [AA][command][selector..]; rx: reply [AA][command][state]
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:151
        - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:144
        - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:176-179
    0xab:
      id: wifi_multi_reply
      -orig-id: MULTI_READ_AB
      doc: |
        chunked like 0xAC but no sub byte and 13 first-chunk data bytes
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:59
        - base-classes10/com/govee/base2light/ble/controller/AbsController4WifiSingleSendMultiBack.java:25-28
        - base-classes12/com/govee/base2newth/one2multi/AbsAbReadController.java:30-38
    0xac:
      id: status_read
      -orig-id: MULTI_READ_AC
      doc: |
        tx [AC][command][args]; rx: chunked replies joined into the status TLVs
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:60
        - base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:163-165
        - base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:202-226
    0xb0:
      id: group_pro_start
      doc: |
        FIRMWARE-SOURCED: group_pro START head; no app sender.
        H60A6 powers on into mode 0xA1, no reply; H6008 drops B0..BF.
      doc-ref:
        - H60A6 FUN_9b03d350 @0x9b03d350
        - H6047 FUN_010105a4 @0x010105a4
        - H6008 code @0x9b03d554-0x9b03d56a
    0xb1:
      id: group_pro_b1
      doc: |
        FIRMWARE-SOURCED group_pro head: 6 x RGB per zone (H6047)
      doc-ref:
        - H60A6 FUN_9b03d234 @0x9b03d234
        - H6047 FUN_010105a4 @0x010105a4
    0xb2:
      id: group_pro_b2
      doc: |
        FIRMWARE-SOURCED group_pro head: 6 x RGB gradient (18 bytes)
      doc-ref:
        - H60A6 FUN_9b03d1f8 @0x9b03d1f8
        - H6047 FUN_010105a4 @0x010105a4
    0xb3:
      id: group_pro_b3
      doc: |
        FIRMWARE-SOURCED group_pro head: main / sub mode u16le + 4 x RGB (H60A6)
      doc-ref:
        - H60A6 FUN_9b03d180 @0x9b03d180
        - H6047 FUN_010104fc @0x010104fc
    0xb4:
      id: group_pro_b4
      doc: |
        FIRMWARE-SOURCED group_pro head: 6 x RGB (H60A6), a colour block (H6047)
      doc-ref:
        - H60A6 FUN_9b03d150 @0x9b03d150
        - H6047 FUN_010104fc @0x010104fc
    0xb5:
      id: group_pro_b5
      doc: |
        FIRMWARE-SOURCED group_pro head: params; B6..BE have no handler
      doc-ref:
        - H60A6 FUN_9b03d104 @0x9b03d104
        - H6047 FUN_010105a4 @0x010105a4
    0xbf:
      id: group_pro_exit
      doc: |
        FIRMWARE-SOURCED group_pro EXIT head (H60A6)
      doc-ref:
        - H60A6 FUN_9b03d350 @0x9b03d350
    0xe7:
      id: handshake
      doc: |
        the encryption handshake; the frames after it are encrypted
      doc-ref:
        - base-classes13/com/govee/encryp/ble/Controller4Aes.java:112
        - base-classes13/com/govee/encryp/ble/Controller4Aes.java:135
        - base-classes13/com/govee/encryp/ble/Controller4AesGcm.java:186
    0xee:
      id: notify
      -orig-id: NOTIFY
      doc: the device push [EE][sub][data]
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:79
  command:
    0x00:
      id: heartbeat
      -orig-id: SINGLE_HEART_BEAT
      doc: |
        heartbeat (H60A1 / H60A6); as a 0xAC TLV tag, 0 is ordinary, not padding
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:125
        - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:241-242
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:254-285
    0x01:
      id: power
      -orig-id: [SINGLE_SWITCH, SINGLE_HEART, SINGLE_MAIN_SWITCH]
      doc: |
        33 01 write, AA 01 reply and push, TLV 0x01 (value byte 0 != 0 = on)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:161
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:286-288
    0x02:
      id: cmd_02
      doc: |
        per form_02: H5089 total switch [state] or H5086 chart prepare (empty)
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4TotalSwitch.java:23-25
        - split_plugnew/com/govee/h5086/ble/chartcontroller/ChartPrepareInfoController.java:41-43
    0x04:
      id: brightness
      -orig-id: SINGLE_BRIGHTNESS
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:108
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:293-295
    0x05:
      id: mode
      -orig-id: SINGLE_MODE
      doc: |
        33 05 [sub_mode] write; AA 05 reply and TLV 0x05 are [sub_mode][params]
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:130
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:300-308
    0x06:
      id: soft_version
      -orig-id: SINGLE_SOFT_VERSION
      doc: AA 06 firmware version read; ASCII reply
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:159
    0x07:
      id: device_info
      -orig-id: SINGLE_DEVICE_INFO
      doc: |
        AA 07 [selector]; TLV 0x07 records: only 0x10 / 0x11 are decoded
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:113
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:309-310
    0x09:
      id: sync_time
      -orig-id: SINGLE_SYNC_TIME
      doc: 33 09 time sync, body per form_09
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:162
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:169-182
    0x0a:
      id: auto_time
      -orig-id: SINGLE_AUTO_TIME
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:106
    0x0b:
      id: delay_close
      -orig-id: SINGLE_DELAY_CLOSE
      doc: delayed power-off
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:111
    0x0e:
      id: current_limit
      doc: |
        CAUTION, firmware-sourced: on H6047 any 33 0e saves the flag and reboots
      doc-ref:
        - firmware-h6047/decompiled/H6047_decompiled.c:4677
    0x0f:
      id: light_num
      doc-ref:
        - H6047 handler @0x01004ca0
    0x11:
      id: sleep
      -orig-id: SINGLE_SLEEP
      doc: |
        sleep timer; layout per the SKU's sleep-config version (not typed)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:158
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:311-316
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:277-329
    0x12:
      id: cmd_12
      -orig-id: [SINGLE_WAKEUP]
      doc: |
        per form_12: the wake-up timer or the h5080 timer count (AA 12)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:166
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:332-388
        - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:220
        - split_plugnew/com/govee/h5080/ble/controller/NewTimerCountController.java:19-31
    0x13:
      id: cmd_13
      -orig-id: [NIGHT_MODE]
      doc: |
        per form_13: night mode or the h5080 plug timer (33 13 add, AA 13 page)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:78
        - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:234-246
        - split_plugnew/com/govee/h5080/ble/controller/NewTimerControllerV2.java:87-102
        - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:111-143
    0x14:
      id: cmd_14
      -orig-id: [SINGLE_WIFI_MAC]
      doc: |
        per form_14: the fade switch 33 14 [on] or the Wi-Fi MAC read (AA 14)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:169
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerGradual.java:59-61
        - base-classes10/com/govee/base2light/ble/controller/WifiMacController.java:9-11
    0x15:
      id: plug_timer_delete
      doc: |
        H5083 timer delete 33 15 [outlet = 0][slot < 10]; later slots shift down
      doc-ref:
        - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:153
        - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:264
        - H5083 FUN_9b021a54 @0x9b021a54
    0x16:
      id: cmd_16
      -orig-id: [VALUE_SETTING_LIGHT_INDICATOR, EFFECT_DIRECTION]
      doc: |
        light-indicator setting or the H61D3 effect direction; body not typed
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:188
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:18
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerEffectDirection.java:61-62
    0x17:
      id: wifi_link_start
    0x20:
      id: wifi_hard_version
      -orig-id: SINGLE_WIFI_HARD_VERSION
      doc: |
        ASCII; H6006/H6008/H60A1/H60A6 answer with the AA 07 03 string
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:168
        - H6047 handler @0x01005140
        - H6047 handler @0x0100b4fc
    0x21:
      id: wifi_soft_version
      -orig-id: SINGLE_WIFI_SOFT_VERSION
      doc: |
        ASCII; H6006/H6008/H60A1/H60A6 answer with the AA 06 string
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:171
        - split_bulblightstringv1/com/govee/bulblightstringv1/bulblight/ControllerStrategyConfig.java:634-647
        - H6047 handler @0x0100510e
    0x23:
      id: new_timer
      -orig-id: SINGLE_NEW_TIME_V1
      doc: |
        the four switch timers: 33 23 [slot][timer_record]; AA 23 [slot | FF]
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:142
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerNewTimer.java:52-77
        - firmware-h6006/decompiled/H6006_ble_dispatcher.c:446-495
    0x24:
      id: light_effect_start
      -orig-id: [SINGLE_READ_IC_POINT, value_set_light_start]
      doc: |
        33 24 [index][time u16be]: pairing light effect, not an IC read
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:240
        - firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c:111-116
        - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:411-430
    0x25:
      id: light_effect_stop
      -orig-id: [SINGLE_PRE_SET_COLOR, value_set_light_end]
      doc: |
        33 25 01 stops the 0x24 effect; other values are acked no-ops
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:147
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:239
        - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:431-435
    0x26:
      id: take_photo_or_colortemp_type
    0x27:
      id: preview_effect
      -orig-id: SET_PREVIEW_EFFECT
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:104
    0x28:
      id: delete_scene
      -orig-id: SINGLE_DELETE_SCENE
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:112
    0x29:
      id: sort_scene
      -orig-id: SINGLE_SORT_SCENE
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:160
    0x30:
      id: cmd_30
      -orig-id: [SINGLE_LIGHT_SWITCH, CALIBRATION, NOTIFY_DETAIL]
      doc: |
        per form_30: zone power 33 30 [zone][state] or a one-byte direction
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:128
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:9
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:102-104
    0x31:
      id: camera_position
    0x32:
      id: check_camera
      -orig-id: MSG_TYPE_READ_INSTALL_CAMERA
      doc: camera installation check
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:30
    0x33:
      id: volume
      -orig-id: SINGLE_VOLUME
      doc: H6047 AA 33 reply [bar1 << 4 | bar0][flag]
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:165
        - H6047 handler @0x010069f0
    0x34:
      id: swap_light
      doc: swap the two bars' logical positions (H6047)
      doc-ref:
        - H6047 handler @0x01005080
    0x35:
      id: without_interrupt
      -orig-id: SINGLE_WITHOUT_INTERRUPT
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:172
        - H6047 handler @0x01004b22
    0x36:
      id: cmd_36
      -orig-id: [SINGLE_WORRY_FREE_AT_NIGHT]
      doc: |
        per form_36: bars [left][right], [index][state] or H6057 worry-free
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:173
        - split_h6047/com/govee/h6047/detail/NewDetailVm.java:696-718
        - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:846-850
    0x37:
      id: prompt_tone
      -orig-id: SINGLE_PROMPT_TONE
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:150
    0x39:
      id: check_direction
      -orig-id: PROTOCOL_CHECK_DIRECTION
      doc: |
        calibration direction check; TLV 0x39 byte 0 = the direction (H6039)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:100
        - split_h6047/com/govee/h6047/newdetail/vm/VM4Light.java:309-320
    0x40:
      id: ic_num
      -orig-id: [IC_NUM, SINGLE_IC_SEGMENT_NUM]
      doc: |
        AA 40 reply [ic s16be][segment]; TLV 0x40 starts the same
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:24
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerOnlyReadIcSegmentNum.java:30-37
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerIcNum.java:141-153
    0x41:
      id: cmd_41
      -orig-id: [SINGLE_ON_OFF_MEMORY, SINGLE_GET_DEVICE_INFO]
      doc: |
        per form_41: power-off memory [02][mode] or the H6062 section count
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:143
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:99-100
        - split_h6062/com/govee/striplight/newdetail/ControllerIcSegmentNum.java:40-54
    0x42:
      id: write_check_ic
      -orig-id: SINGLE_WRITE_CHECK_IC
      doc: |
        firmware-sourced H60A6 calibration: 01 enter, 02 step, FF save, 00 exit
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:175
        - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:483-548
        - H60A6 code @0x9b0269fe
    0x43:
      id: check_ic_amount
      -orig-id: [SINGLE_WRITE_CHECK_IC_AMOUNT, OP_COMMAND_TYPE_0X43]
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:176
    0x44:
      id: calibration
      doc: |
        H6048 calibration; its ack's result is frame byte 3
      doc-ref:
        - split_h6047/com/govee/h6047/newdetail/control/CalibrationController4H6048.java:23-24
        - split_h6047/com/govee/h6047/newdetail/control/CalibrationController4H6048.java:44-46
    0x46:
      id: reset_device_net
      -orig-id: [SINGLE_CHECK_IC, SINGLE_RESET_DEVICE_NET]
      doc: |
        CAUTION, destructive: a factory reset and reboot on H60A1 / H60A6 and a
        network / Matter reset on H6008; never send it as an IC check.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:156
        - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:454-468
        - firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c:117-119
    0x50:
      id: movie_feast_set_device
    0x54:
      id: movie_feast_on_off
      -orig-id: movie_feast_on_off
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:195
    0x60:
      id: feast_carousel
      -orig-id: MODULE_FEAST
      doc: feast module (H6047 AA 60 [sub])
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:26
        - H6047 handler @0x010049bc
    0x62:
      id: combination_effect
      -orig-id: MULTI_COMBINATION_EFFECT
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:40
    0x63:
      id: daysync_op
      -orig-id: DAYSYNC_OP
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:16
    0x70:
      id: music_lib_notify
      -orig-id: SINGLE_MUSIC_LIB_NOTIFY
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:131
    0x71:
      id: music_lib_player_state
      -orig-id: SINGLE_MUSIC_LIB_READ_PLAYER_STATE
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:132
    0x72:
      id: music_lib_preset_info
      -orig-id: SINGLE_MUSIC_LIB_READ_PRESET_INFO
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:134
    0x73:
      id: music_lib_preset_color
      -orig-id: SINGLE_MUSIC_LIB_READ_PRESET_COLOR
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:133
    0x74:
      id: music_lib_preset_scenes
      -orig-id: SINGLE_MUSIC_LIB_READ_PRESET_SCENES
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:135
    0x75:
      id: music_lib_set_play_state
      -orig-id: SINGLE_MUSIC_LIB_SET_PLAY_STATE
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:137
    0x76:
      id: music_lib_preview_effect
      -orig-id: SINGLE_MUSIC_LIB_SET_PREVIEW_EFFECT
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:138
    0x77:
      id: music_lib_remove_scene
      -orig-id: SINGLE_MUSIC_LIB_REMOVE_PRESET_SCENES
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:136
    0x78:
      id: music_lib_sort_scenes
      -orig-id: SINGLE_MUSIC_LIB_SORT_PRESET_SCENES
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:141
    0x79:
      id: music_lib_volume
      -orig-id: SINGLE_MUSIC_LIB_SET_VOLUME
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:140
    0x7a:
      id: music_lib_switch
      -orig-id: SINGLE_MUSIC_LIB_SET_SWITCH
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:139
    0x84:
      id: set_alarm_clock
      -orig-id: SET_DEVICE_ALARM_CLOCK
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:102
    0xa2:
      id: color_group_rgb
      doc: |
        per form_a2: AA A2 [group] with rgb records, or the H61A8 V1 batch
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:44-49
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:122-131
    0xa3:
      id: gradual
      -orig-id: SINGLE_GRADUAL_CHANGE_WIFI_BLE
      doc: |
        fade switch (GradualVersion 0): 33 A3 [on]; TLV 0xA3 byte 0 == 1 = on
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:122
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:564-566
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerGradual4BleWifi.java:94-96
    0xa5:
      id: color_group
      -orig-id: MSG_READ_BULB_LIGHT_COLOR_WITH_BRIGHTNESS
      doc: |
        per form_a5: AA A5 / TLV 0xA5 bri_rgb group, or the H61A8 V2 batch
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:28
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:37-42
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:160-175
    0xa6:
      id: special_function
      -orig-id: SINGLE_SPECIAL_FUC
      doc: |
        H6062 position write 33 A6 01 [v]; ack result at frame byte 3
      doc-ref:
        - split_h6062/com/govee/striplight/newdetail/ControllerPos.java:56-58
        - split_h6062/com/govee/striplight/newdetail/ControllerPos.java:73-75
        - H6047 handler @0x01004a28
    0xa9:
      id: white_balance
    0xaa:
      id: check_light
    0xab:
      id: dynamic_api_support
      -orig-id: SINGLE_DYNAMIC_API_SUPPORT
      doc: |
        H60A1 / H60A6 answer AA AB with [02]
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:118
        - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:566-571
        - firmware-h6006/decompiled/H6006_ble_dispatcher.c:504-505
    0xb0:
      id: cmd_b0
      doc: |
        per form_b0: the h5080 plug on / off delay (AA B0 read, 33 B0 write)
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/TurnOnOffDelayController.java:90-92
        - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:182-188
        - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:158-187
    0xb1:
      id: secret_read
      -orig-id: SINGLE_READ_SECRET_KEY
      doc: |
        account-lock secret read, reply [selector][8 bytes]; not the cipher
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:152
        - base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:57-66
        - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:574-610
    0xb2:
      id: secret_write
      -orig-id: SINGLE_CHECK_SECRET_KEY
      doc: 33 B2 writes back the 8 secret bytes of the AA B1 reply
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:110
        - base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:22-26
    0xb3:
      id: plug_spec
      doc: h5080 plug spec read, reply [spec]
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SpecController.java:6-13
    0xb4:
      id: plug_timer
      doc: |
        h5080 plug timer read AA B4 [01 or 00][FF]
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/NewTimerController.java:67-69
    0xb5:
      id: plug_sync_time
      doc: 33 B5 [unix u32be][01][tz_h][tz_m] (h5080)
    0xed:
      id: notify_recognition
      -orig-id: NOTIFY_RECOGNITION
      doc: outbound 33 ED only; no inbound decoder
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:91
    0xee:
      id: ota_prepare
      -orig-id: SINGLE_OTA_PREPARE
      doc: |
        H6047 33 EE stops the running effect and acks 00
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:145
        - H6047 handler @0x01004f00
    0xef:
      id: pact
      -orig-id: SINGLE_PACT
      doc: |
        pact read, reply [pact_type u16be][pact_code]; old adjust flows only
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:146
        - base-classes10/com/govee/base2light/ble/controller/PactController.java:9-20
        - H6006 ble_msg_get_adv_pro_type @0x9b03cc00
    0xff:
      id: debug
      doc: |
        FIRMWARE-SOURCED, CAUTION: test hooks; AA FF 08 reads the ageing flag.
        33 FF stops advertising (H6006 / H6008) or the device service (H6047).
      doc-ref:
        - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:662-672
        - firmware-h6006/decompiled/H6006_ble_dispatcher.c:195-251
        - firmware-h6047/decompiled/H6047_decompiled.c:4707
  sub_mode:
    0x00:
      id: video
    0x02:
      id: color_legacy
      -orig-id: sub_mode_operate_type_2
      doc: |
        colour sub-mode 0x02, decoded by mode_decode.c02
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:766
    0x04:
      id: scene
      -orig-id: sub_mode_scenes
      doc: the scene code u16le
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:208
        - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4Scene.java:24-29
    0x05:
      id: mic
      doc: H6006 reply AA 05 05 [00][r g b]
      doc-ref:
        - firmware-h6006/decompiled/H6006_ble_dispatcher.c:587-602
    0x0a:
      id: diy
      -orig-id: sub_mode_new_diy
      doc: the DIY code s16le
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:203
        - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4Diy.java:36-40
    0x0b:
      id: color_rgbic_0b
      doc: |
        colour sub-mode 0x0b; write per form_0b, report by mode_decode.c0b
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:766
    0x0c:
      id: music_alt
    0x0d:
      id: color_cct_0d
      doc: bulb / lamp colour sub-mode; report by mode_decode.c0d
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:766
    0x0e:
      id: music_string
    0x0f:
      id: music_lamp
    0x11:
      id: music_dreamcolor
    0x13:
      id: music
      -orig-id: sub_mode_music
      doc: |
        report by mode_decode.music; H60A6 stores it as mode 0xA0
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:202
    0x14:
      id: color_floor_lamp_14
      doc: |
        the straightfloorlamp colour sub-mode; report by mode_decode.c14
      doc-ref:
        - split_hollowlamp/com/govee/straightfloorlamp/ble/BleProtocol.java:11
        - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:281
    0x15:
      id: color_rgbic_15
      -orig-id: sub_mode_color
      doc: colour [op][data]; report by mode_decode.c15
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:199
        - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:766
    0xa1:
      id: scene_a1
      doc: |
        untyped scene twin of 0x04; H60A6 enters it while group_pro is active
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:786
        - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:280-321
        - H60A6 FUN_9b03d350 @0x9b03d350
  op15:
    0x00:
      id: set_mode
      doc: |
        switch to the colour mode: [15 00] and nothing after it
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:699-701
        - split_h60ax/com/govee/pact_h60a0/ble/controller/compose/ComposeChange2ColorMode.java:32
    0x01:
      id: set_color
      doc: |
        colour write, layout per form_15_01 (no in-frame discriminator)
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:684-694
    0x02:
      id: set_brightness
      doc: per-segment brightness [pct][seg_mask]
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:172-181
    0x03:
      id: set_brightness_group
      doc: brightness levels [level x num_levels]
    0x04:
      id: set_color_group
      doc: |
        per form_15_04: group colours [group][rgb x n] or a Kelvin [kelvin u2be]
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:393-402
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:133-139
    0x05:
      id: set_color_temp
      doc: |
        per form_15_05: Kelvin, side brightness [side][value] or a gradient
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:695-698
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:184-199
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:404-420
  notify_sub:
    0x01:
      id: light_status
      doc: legacy code from the old 0x30 scheme; no current parser
    0x02:
      id: energy_saving
      doc: used by other device categories
    0x03:
      id: battery
      doc: used by other device categories
    0x04:
      id: music
      doc: used by other device categories
    0x05:
      id: pressure_or_volume
      doc: H5130 current pressure; the volume detail on some devices
    0x11:
      id: wifi_connect
      doc: |
        Wi-Fi status: [00] connected, or [01][reason]
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_parser/DefParser.java:89-95
        - H5083 FUN_9b031804 @0x9b031804
        - H6006 send_wifi_status @0x9b03c694
    0x20:
      id: brightness
      -orig-id: brightness_notify
      doc: brightness push, level in data byte 0
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:193
        - base-classes11/com/govee/base2light/kt/general_parser/DefParser.java:200
    0x21:
      id: ic_count
      -orig-id: SINGLE_NOTIFY_IC
      doc: H6062 IC change [ic s16be][segment s8]
      doc-ref:
        - split_h6062/com/govee/striplight/ble/BleProtocol.java:13
        - split_h6062/com/govee/striplight/ble/CheckIcNumParse.java:16-22
    0x22:
      id: ic_and_piece
      -orig-id: SINGLE_NOTIFY_IC_AND_PIECE
      doc: |
        H6062 IC-and-piece change: sub_ic_1 at data byte 0, sub_ic_2 at byte 2
      doc-ref:
        - split_h6062/com/govee/striplight/ble/BleProtocol.java:14
        - split_h6062/com/govee/striplight/ble/IcAndPieceNotifyParse.java:24-31
    0x24:
      id: earphone
      doc: |
        FIRMWARE-SOURCED: H6047 earphone status push [1 plug-in / 0 pull-out]
      doc-ref:
        - H6047 FUN_01010b80 @0x01010b80
    0x26:
      id: take_photo_exit
      doc: used by other device categories
    0x30:
      id: switch_zone
      -orig-id: NOTIFY_DETAIL
      doc: |
        switch / zone push per form_ee_30; H60A6 [01][power][zone0][zone1]
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:81
        - base-classes11/com/govee/base2light/kt/general_parser/DefParser.java:81
        - H60A6 FUN_9b0257e0 @0x9b0257e0
    0x40:
      id: device_status
      -orig-id: value_connect_status_notify
      doc: |
        status selector in data byte 0; on H6047 the slave states (form_ee_40)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:210
        - base-classes11/com/govee/base2light/kt/general_parser/DefParser.java:219
        - H6047 FUN_01011a04 @0x01011a04
    0xed:
      id: recognition
      doc: outbound 33 ED and a state event only; no inbound decoder
  handshake_op:
    0x01:
      id: v1_request_session_key
      doc: AES V1 step 1; the reply carries the session key
      doc-ref:
        - base-classes13/com/govee/encryp/ble/Controller4Aes.java:104-119
    0x02:
      id: v1_confirm
      doc: AES V1 step 2
      doc-ref:
        - base-classes13/com/govee/encryp/ble/Controller4Aes.java:121-136
    0x11:
      id: v2_single
      doc: AES-GCM single packet
    0x19:
      id: v2_request_session_key
      doc: AES-GCM
      doc-ref:
        - base-classes13/com/govee/encryp/ble/Controller4AesGcm.java:186
    0x1a:
      id: v2_confirm
      doc: AES-GCM
      doc-ref:
        - base-classes13/com/govee/encryp/ble/Controller4AesGcm.java:52
  device_info_selector:
    0x02:
      id: sn
      -orig-id: VALUE_DEVICE_INFO_UUID
      doc: |
        8-byte device id; firmware sends it byte-reversed
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/SnController.java:37-38
    0x03:
      id: hard_version
      -orig-id: VALUE_DEVICE_INFO_HARD_VERSION
      doc: ASCII hardware version
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/HardVersionController.java:28-29
    0x04:
      id: soft_version
      -orig-id: VALUE_DEVICE_INFO_SOFT_VERSION
      doc: |
        ASCII soft version, the 0x03 layout
      doc-ref:
        - split_h61d3/com/govee/h61d3/ble/controller/SoftHardController.java:66-68
        - base-classes10/com/govee/base2light/ble/controller/SoftVersionInDeviceInfoController.java:18-19
        - H6047 handler @0x01005370
    0x06:
      id: ble_mac
      doc: |
        FIRMWARE-SOURCED: the BLE MAC at frame bytes 3..8; order per image (mac)
      doc-ref:
        - H6006 ble_msg_get_ble_macaddress @0x9b03c988
        - H5083 ble_msg_get_ble_macaddress @0x9b0319cc
        - H6047 handler @0x01005370
    0x07:
      id: dsp_version
      -orig-id: VALUE_DEVICE_INFO_DSP_VERSION
      doc: |
        DSP version, read by the generic connect step
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/DspVersionInDeviceInfoController.java:20-21
        - base-classes10/com/govee/base2light/connect/factory/ReadControllerFactory.java:28
        - split_h60ax/com/govee/pact_h60a0/adjust/v1/ComposeReadAllInfoV1.java:103-111
    0x08:
      id: other_info
      -orig-id: OTHER_INFO
      doc: |
        pact_h60a0 'other info', adjust-v1 read only; no verified image answers
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/ble/controller/compose/ControllerOtherInfo.java:24-26
        - split_h60ax/com/govee/pact_h60a0/adjust/v1/ComposeReadAllInfoV1.java:76-86
        - H60A6 code @0x9b04a8f8
    0x0a:
      id: mcu_soft_version
      -orig-id: SINGLE_DEVICE_INFO_MCU_SOFT_VERSION
      doc: |
        MCU soft version; H6008 / H60A1 / H60A6 echo it with no data
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/McuSoftVersionControllerV1.java:32-33
        - H6008 code @0x9b03c958
        - H60A6 ble_msg_get_sw_mcu_version @0x9b04a638
    0x0b:
      id: mcu_hard_version
      -orig-id: SINGLE_DEVICE_INFO_MCU_HARD_VERSION
      doc: echoed with no data (H6008 / H60A1 / H60A6)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/McuHardVersionControllerV1.java:32-33
        - H6008 code @0x9b03c966
        - H60A6 ble_msg_get_hw_mcu_version @0x9b04a688
    0x10:
      id: basic
      doc: |
        [uid 8][sw version3][hw version3][dsp u16le]; AA 07 10 only on H5086
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:405-436
        - split_plugnew/com/govee/h5086/ble/controller/DeviceBasicInfoController.java:39-63
        - base-classes10/com/govee/base2light/ble/controller/BasicInfoController.java:34-35
    0x11:
      id: wifi
      doc: |
        [wifi mac 6][sw version3][hw version3]; AA 07 11 only on H5086
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:384-403
        - split_plugnew/com/govee/h5086/ble/controller/WifiInfoController.java:29-42
        - base-classes10/com/govee/base2light/ble/controller/BasicWifiInfoController.java:42-43
  color_decode:
    0:
      id: not_decoded
      doc: |
        no colour decoded (or not traced); the bytes stay in the message's rest
    1:
      id: gradual_kelvin_s16be
      doc: |
        [gradual][kelvin s16be]; kelvin > 0 = CCT, colour from the Kelvin
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4Color.java:42-49
        - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:771-779
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:399-405
    2:
      id: gradual_kelvin_s16be_nocct
      doc: |
        the same bytes, stored with no CCT test
      doc-ref:
        - split_rgbiclight/com/govee/h6127/newdetail/InfoDetail.java:117-118
        - split_hollowlamp/com/govee/hollowlamp/newdetails/h6058/H6058InfoDetail.java:271-273
        - split_h705a/com/govee/h705a/h3401/H3401DetailInfo.java:214-215
    3:
      id: gradual_only
      doc: '[gradual] only; no Kelvin and no colour read'
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/adjust/h60c1/Info4DetailH60C1.java:125-131
        - split_h6047/com/govee/h6047/newdetail/Info4Detail.java:415-417
        - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057Info4Detail.java:127-135
    4:
      id: kelvin_s16be
      doc: '[kelvin s16be] with no gradual byte; kelvin > 0 = CCT'
      doc-ref:
        - split_bulblightstringv1/com/govee/bulblightv3/newdetail/Info4Detail.java:160-167
        - split_bulblightstringv1/com/govee/bulblightv3/newdetail/NewDetailVm.java:1280-1288
    5:
      id: skip_kelvin_s16be_illumination
      doc: |
        [unused][kelvin s16be]: the H60A0 white light's Kelvin, never device CCT
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a0/Info4DetailH60A0.java:123-130
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a0/Info4DetailH60A0.java:399-406
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a0/VM4LightH60A0.java:203-211
    6:
      id: gradual_kelvin_pair_s16be
      doc: |
        [gradual][kelvin left][kelvin right] s16be; device Kelvin only if equal
      doc-ref:
        - split_h6047/com/govee/h6047/detail/Info4Detail.java:173-185
        - split_h6047/com/govee/h6047/detail/Info4Detail.java:848-858
        - split_h6047/com/govee/h6047/detail/NewDetailVm.java:616-652
    7:
      id: skip_kelvin_pair_s16be
      doc: |
        [unused][kelvin A][kelvin B] s16be; the Kelvin is A only when A == B
      doc-ref:
        - split_tvlightv4/com/govee/pact_tvlightv3/detail/Info4Detail.java:176-183
        - split_tvlightv4/com/govee/pact_tvlightv4/detail/Info4Detail.java:176-183
    8:
      id: rgb_kelvin_pair_s16be
      doc: |
        [rgb1][k1 s16be][rgb2][k2 s16be]: k != 0 = tint; CCT only if k1 == k2
      doc-ref:
        - split_tvlightv4/com/govee/pact_tvlightv3/detail/Info4Detail.java:185-202
        - split_tvlightv4/com/govee/pact_tvlightv4/detail/Info4Detail.java:185-202
    9:
      id: gradual_kelvin_triple_s16be
      doc: |
        [gradual][kelvin A][B][C] s16be; the device Kelvin is never set
      doc-ref:
        - split_h6099/com/govee/pact_h605b/newdetail/Info4Detail.java:179-185
        - split_h6099/com/govee/pact_h605b/newdetail/viewmodel/NewDetailVm.java:1223-1246
    10:
      id: gradual_kelvin_triple_s16be_nocct
      doc: the same three Kelvins, which nothing consumes
      doc-ref:
        - split_h6099/com/govee/pact_h605b/newdetail/Info4Detail.java:179-185
        - split_h6099/com/govee/pact_h605b/newdetail/viewmodel/NewDetailVm.java:1224
    11:
      id: rgb_kelvin_tint
      doc: |
        [r g b][kelvin s16be][tint r g b]; kelvin > 0 = CCT, 0 = RGB, < 0 = tint
      doc-ref:
        - split_rgblight/com/govee/rgblight/mode/ModeParseStrategyConfig.java:35-46
        - split_bulblightstringv1/com/govee/bulblightstringv1/bulblight/mode/ModeParseStrategyConfig.java:55-62
        - split_rgbiclight/com/govee/rgbiclight/h6160/newdetail/Vm4Light.java:578-588
    12:
      id: rgb_kelvin_tint_positive
      doc: |
        the same layout; tint read only when kelvin > 0, else the RGB
      doc-ref:
        - split_hollowlamp/com/govee/hollowlamp/newdetails/h60501/H60501InfoDetail.java:121-148
        - split_hollowlamp/com/govee/hollowlamp/newdetails/h60501/H605051NewDetailVm.java:772-786
    13:
      id: rgb_kelvin_0d
      doc: |
        [r g b][kelvin s16be]; kelvin > 0 = CCT from the Kelvin, else the RGB
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:416-426
        - split_h6061/com/govee/cubelight/newdetails/CubeNewInfo4Detail.java:142-153
        - split_rgblight/com/govee/homelightv1/newdetail/Info4Detail.java:113-125
    14:
      id: rgb_kelvin_0d_nocct
      doc: the same 5 bytes; always the RGB, the Kelvin never tested
      doc-ref:
        - split_h70b1/com/govee/h70bx/Info4Detail.java:125-133
    15:
      id: rgb
      doc: |
        [r g b] only, never CCT
      doc-ref:
        - split_hollowlamp/com/govee/tablelampv1/newAdjust/h6052/H6052InfoDetail.java:113-121
        - split_rgblight/com/govee/rgblight/mode/ModeParseStrategyConfig.java:67-77
    16:
      id: rgb_flag1_tint
      doc: |
        [r g b][flag][tint r g b]; flag == 1 = CCT at the tint's table Kelvin
      doc-ref:
        - split_rgblight/com/govee/rgblight/mode/ModeParseStrategyConfig.java:52-62
        - split_rgbiclight/com/govee/rgbiclight/h6160/newdetail/Vm4Light.java:562-575
    17:
      id: rgb_flagnz_tint
      doc: |
        [r g b][flag][tint r g b]; flag != 0 = CCT, tint Kelvin in 2000..9000
      doc-ref:
        - split_rgblight/com/govee/h6104/newdetail/Info4Detail.java:165-176
        - base-classes10/com/govee/base2kt/utils/ColorUtils.java:255-258
    18:
      id: rgb_flag_tint_nokelvin
      doc: |
        [r g b][flag][tint r g b]; flag != 0 = CCT shown as the tint, no Kelvin
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/AbsMode.java:17-23
        - split_rgblight/com/govee/h6101/ble/SubModeColor.java:81-85
        - split_rgbiclight/com/govee/h6102/ble/SubModeColor.java:82-86
    19:
      id: rgb_pair_flag_lookup
      doc: |
        [colour A][flag][colour B]: B if flag != 0, else A; Kelvin by lookup
      doc-ref:
        - split_bulblightstringv1/com/govee/bulblightstringv1/bulblight/mode/ModeParseStrategyConfig.java:26-53
        - base-classes10/com/govee/base2light/connect/constant/KelvinHelp.java:52-57
    20:
      id: bri_rgb_pair
      doc: |
        [bri1 s8][r1 g1 b1][bri2 s8][r2 g2 b2], one per lamp head; never CCT
      doc-ref:
        - split_h7050/com/govee/h7050/adjust/newAdjust/h60616263/H70606162Info4Detail.java:375-427
        - split_h7050/com/govee/h7050/adjust/newAdjust/h60616263/H70606162Info4Detail.java:247-261
  music_decode:
    0:
      id: not_decoded
      doc: the screen's music decode is not traced; bytes stay in rest
    1:
      id: v1_flagged
      doc: |
        [music_code][value][dynamic][flag], then [r][g][b] when flag != 0
      doc-ref:
        - split_rgbiclight/com/govee/dreamcolorlightv1/ble/SubModeMusicV1.java:87-98
    2:
      id: v2_default
      doc: |
        V2 default: extra byte for code 4; new-music codes 49, 71, 72, 81, 113
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4MusicV2.java:49-73
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:122-125
        - base-classes10/com/govee/base2light/ble/music/AbsNewMusicEffect.java:114-116
  group_count_rule:
    0:
      id: unknown
      doc: no colour-group decode for this device (body raw)
    1:
      id: from_len
      doc: |
        records = (len - 1) / record size
      doc-ref:
        - split_floorlamp/com/govee/h60b1/detail/H60B1ViewModel.java:159-160
        - split_h60ax/com/govee/pact_h60a0/adjust/h60c1/VM4LightH60C1$afterConnectedSingleReadDeviceInfo$1.java:112-113
        - split_h6840/com/govee/h6609/h6850/H6850DetailVM$afterConnectedSingleReadDeviceInfo$1.java:87-88
    2:
      id: fixed
      doc: |
        num_per_group records in every group (and every direct aa a5 / a2 reply)
      doc-ref:
        - split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:574
        - split_rgbiclight/com/govee/rgbiclight/VM4Light.java:711
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/VM4Light$afterConnectedSingleReadDeviceInfo$1.java:88
    3:
      id: by_pieces
      doc: |
        ceil(num_pieces / num_per_group) groups; the last holds the remainder
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:105-121
  form_01:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: light
      doc: |
        a light's 1 / 0
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerSwitch.java:60-62
        - base-classes10/com/govee/base2light/ble/controller/SwitchController.java:37-39
    2:
      id: relays
      doc: |
        h5080 relay byte: outlet select in the high nibble, on bits in the low
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:53-71
  form_02:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: total_switch
      doc: H5089 total switch [state]
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4TotalSwitch.java:32-34
    2:
      id: chart_prepare
      doc: H5086 chart prepare, no payload
      doc-ref:
        - split_plugnew/com/govee/h5086/ble/chartcontroller/ChartPrepareInfoController.java:51-52
  form_09:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: datetime_epoch
      doc: |
        TimerConfig version 0 (the default), 15 bytes
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTime.java:31-35
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:463-479
    2:
      id: epoch_be
      doc: |
        TimerConfig version 1; the layout H60A6 firmware reads
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTimeStamp.java:29-33
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:465-467
        - H60A6 FUN_9b02ad30 @0x9b02ad30
    3:
      id: clock
      doc: |
        TimerConfig version 2
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTime.java:37-41
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:468-478
  form_12:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: wake
      doc: the wake-up timer, untyped
    2:
      id: timer_count
      doc: h5080 timer count (AA 12 [group] -> [group][count]), on every h5080 screen
      doc-ref:
        - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:220
        - split_plugnew/com/govee/h5080/ble/controller/NewTimerCountController.java:19-31
  form_13:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: night_mode
      doc: untyped
    2:
      id: plug_timer
      doc: |
        h5080 plug timer (33 13 add; AA 13 [group][page] page read), on the
        goodsType 43, 50 and 90 screens
      doc-ref:
        - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:234-246
        - split_plugnew/com/govee/h5080/ble/controller/NewTimerControllerV2.java:53-66
        - split_plugnew/com/govee/h5080/ble/controller/NewTimerControllerV2.java:87-102
  form_14:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: gradual
      doc: the fade switch (GradualVersion 1)
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5602-5613
    2:
      id: wifi_mac
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/WifiMacController.java:28-46
  form_30:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: zone_power
      doc: pact_h60a0 per-zone power (H60A6, H1270, H1250, H12D0)
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:121-124
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6.java:1373-1376
    2:
      id: light_direction
      doc: |
        one-byte value (H6038 calibration direction, H6057 main light, H71xx)
      doc-ref:
        - split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:42-45
        - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:950-956
  form_36:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: bar_pair
      doc: H6047 [left][right] bar switch
      doc-ref:
        - split_h6047/com/govee/h6047/detail/NewDetailVm.java:698
    2:
      id: bar_index
      doc: '[index][state] (H6043 / H6038 / H6048 / H60B2 / H1771 / H60A0)'
      doc-ref:
        - split_h6047/com/govee/h6047/detail/NewDetailVm.java:696
    3:
      id: worry_free
      doc: H6057 worry-free-at-night, 7 bytes
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/WorryFreeAtNightBean.java:90-92
  form_40:
    0:
      id: unknown
      doc: |
        not known, or the status screen does not read TLV 0x40; the value
        still reads, and only app_read_fails depends on the form
    1:
      id: ic_pair
      doc: |
        the IC pair reader: it throws below 3 bytes or on a count of 0x1000+
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerIcNum.java:64-74,141-161
        - split_h61d3/com/govee/h61d3/adjustNew/VM4Light$afterConnectedSingleReadDeviceInfo$1.java:125-126
    2:
      id: one_count
      doc: one 16-bit IC count from bytes 0-1; it throws below 2 bytes
      doc-ref:
        - split_rgbiclight/com/govee/rgbiclight/h667172/VM4LightH667172$afterConnectedSingleReadDeviceInfo$1.java:96-97
        - split_h7050/com/govee/h7050/newdetail/h7045/H7045DetailVM$afterConnectedSingleReadDeviceInfo$1.java:80-81
    3:
      id: device_count
      doc: |
        the H70B3 one-byte device count: only an empty value throws; the
        screen skips it at splicing state 1
      doc-ref:
        - split_h70b1/com/govee/h70b3/adjust/vm/VM4H70B345.java:887-895
        - split_h70b1/com/govee/h70b3/adjust/vm/VM4H70B345Splicing.java:487-489
  form_41:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: power_memory
      doc: the power-off memory record [02][mode]
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:99-100
    2:
      id: section_count
      doc: the H6062 section count [ic s16be][section s8]
      doc-ref:
        - split_h6062/com/govee/striplight/newdetail/ControllerIcSegmentNum.java:40-54
  form_a2:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: bulb_batch_v1
      doc: H61A8 mechanism-B V1 batch [batch][4 x rgb]
      doc-ref:
        - split_rgbiclight/com/govee/dreamcolorlightv1/ble/BulbGroupColor.java:11-25
    2:
      id: color_group_rgb
      doc: |
        a colour-only group [group][rgb x n]
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:145-158
  form_a3:
    0:
      id: unknown
      doc: |
        not known, or the status screen does not read TLV 0xA3; the value
        still reads, and only app_read_fails depends on the form
    1:
      id: gradual
      doc: byte 0 read as the gradual flag; an empty value throws
      doc-ref:
        - split_h7075/com/govee/h7075/h707A/H707ABCDetailVM$afterConnectedSingleReadDeviceInfo$1.java:141-142
        - split_hollowlamp/com/govee/straightfloorlamp/adjust/VM4Light$afterConnectedSingleReadDeviceInfo$1.java:118-119
  form_a5:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: color_group
      doc: |
        a group [group][bri_rgb x n] (H6047, H6641, H6062, H6061, H60A6)
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:160-175
    2:
      id: bulb_batch_v2
      doc: H61A8 mechanism-B V2 batch [batch][3 x bri_rgb]
      doc-ref:
        - split_rgbiclight/com/govee/dreamcolorlightv1/ble/BulbGroupColorV2.java:16-33
  form_b0:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: plug_delay
      doc: the h5080 plug on / off delay
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/TurnOnOffDelayController.java:90-92
  form_0b:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: rgb_mask
      doc: |
        [rgb][seg_mask(num_segments, 13)], colour-instruction version 3
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:270-281
        - base-classes12/com/govee/common2light/capability/color/ColorInstructionVersion.java:16
    2:
      id: rgb_kelvin
      doc: |
        [rgb][kelvin u2be][positions 0..14 as bits], colour-instruction v6
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:239-267
        - base-classes12/com/govee/common2light/capability/color/ColorInstructionVersion.java:19
    3:
      id: game
      doc: the H6057 game sub-mode (untraced; body raw)
  form_15_01:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: basic
      doc: '[rgb][seg_mask cap 12]'
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:684-694
    2:
      id: kelvin_tint
      doc: |
        [rgb][kelvin u2be][tint rgb][seg_mask cap 7]; zeros for plain RGB
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:474-501
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:315-326
        - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:720-744
    3:
      id: kelvin
      doc: |
        [rgb or tint][kelvin u2be][seg_mask cap 10], colour-instruction v5
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:379-390
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:541-556
        - base-classes12/com/govee/common2light/capability/color/ColorInstructionVersion.java:18
  form_15_04:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: rgb_group
      doc: '[group][rgb x n]'
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:393-402
    2:
      id: kelvin_v9
      doc: '[kelvin u2be], colour-instruction version 9'
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:133-139
  form_15_05:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: kelvin
      doc: |
        [kelvin u2be] (H60A0)
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:695-698
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a0/ControllerBuildH60A0.java:62-66,93-98
    2:
      id: side_brightness
      doc: |
        [side][value] (relative-brightness version 1, goodsType 80)
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:184-199
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:2817-2821
    3:
      id: gradient
      doc: |
        [rgb x 3][seg_mask(num_segments, 6)] (gradient version 0)
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:404-420
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/GradientColorControllerConfig.java:65-92
  form_ee_30:
    0:
      id: unknown
      doc: |
        not known: the four bytes still read; is_on and the zone flags are false
    1:
      id: main_bit0_a
      doc: |
        power = bit 0 of flags_a; zones = bit 0 of flags_b / flags_c on H60A6
    2:
      id: bit0_c
      doc: |
        power = bit 0 of flags_c (H60A0)
    3:
      id: bars_bc
      doc: per bar flags_b == 1 / flags_c == 1 (H6038 / H6048)
    4:
      id: generic_flags
      doc: a flags byte in data byte 1, sub-switches in bits 1-4
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_parser/DefParser.java:81
  form_ee_40:
    0:
      id: unknown
      doc: not known; push body raw
    1:
      id: slave_states
      doc: FIRMWARE-SOURCED H6047 [5 group-slave states]
      doc-ref:
        - H6047 FUN_01011a04 @0x01011a04
