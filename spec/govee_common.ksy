meta:
  id: govee_common
  title: Govee BLE shared enums and context-free leaf types
  license: MIT
  ks-version: '0.11'
  bit-endian: le
  endian: be
doc: |
  LIBRARY (no seq, never an entry point): the enums every Govee spec file
  shares, and the context-free leaf types more than one message uses. A type
  here takes no context except plain params, because a nested type imported
  into another file has no root or parent link (ksc, PR 283).
  Multi-byte integers are big-endian unless a field says otherwise. Bit fields
  are LSB-first (bit-endian le): bit k of byte j is position 8j+k.
types:
  rgb:
    doc: |
      One colour [r][g][b], each an unsigned byte, as every Govee reader takes
      it (BleUtil.getUnsignedByte; ColorUtils.G / I of the three bytes). Used by
      the colour writes and reports, the 3-byte colour-group records (aa a2,
      Controller4ColorInfoByGroup.Companion.p) and the bulb batch replies.
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
      One 4-byte colour record [brightness][r][g][b]: the aa a5 / 0xAC TLV 0xA5
      colour-group record (Controller4ColorInfoByGroup.Companion.q: byte 0 into
      the brightness set, bytes 1..3 into the colour) and the H61A8 mechanism-B
      V2 batch record (BulbGroupColorV2: the leading byte is the per-segment
      brightness, SubModeColorV2 brightnessSet). Every byte unsigned.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:160-175
    seq:
      - id: brightness
        type: u1
        doc: per-record (per-segment) brightness
      - id: r
        type: u1
      - id: g
        type: u1
      - id: b
        type: u1
  level:
    doc: |
      One brightness byte, as the device reports it: the aa 04 reply
      (BrightnessController.parseValidBytes), the 0xAC TLV 0x04 value (u():
      bArr2[0] & 0xFF) and the EE 20 push (DefParser.p: data[0], unsigned).
      Raw: there is no 0..255 <-> 0..100 rescale in the BLE codec; every curated
      SKU sends a percent, and percent mapping is the UI's (and the library's
      device layer's) job.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:293-295
      - base-classes11/com/govee/base2light/kt/general_parser/DefParser.java:200
    seq:
      - id: level
        type: u1
  version3:
    doc: |
      3-byte binary firmware version [major][minor][patch], as the 0x10 / 0x11
      device-info records carry it. The app's live renderer is
      Compose4BaseInfoSingleRead.x (called for every version of the 0xAC TLV
      0x07 records, w :392, :398, :421, :427): '' when all three bytes are 0
      (is_zero), else (int) major + "." + v(minor) + "." + v(patch), each byte
      a SIGNED Java byte and v(i) = i > 9 ? "" + i : "0" + i. So 1/0/41 renders
      "1.00.41" and minor 0xF6 renders "0-10". `text` is that string (user
      decision: match the app's signed rendering). BasicInfoController.u
      (String.format("%02d"), "0.00.00" for all zero) has no caller in 7.7.02.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:439-457
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:363-368
      - base-classes10/com/govee/base2light/ble/controller/BasicInfoController.java:69-77
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
        doc: all three bytes 0 = no version (x() returns '', logs 'all zero')
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:447-456
      major_signed:
        value: 'major >= 0x80 ? major - 0x100 : major'
        doc: major as the app's (int) cast of the Java byte reads it
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:449
      minor_signed:
        value: 'minor >= 0x80 ? minor - 0x100 : minor'
        doc: minor as a signed Java byte (passed to v())
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:449
      patch_signed:
        value: 'patch >= 0x80 ? patch - 0x100 : patch'
        doc: patch as a signed Java byte (passed to v())
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:449
      text:
        value: >-
          is_zero ? '' : major_signed.to_s + '.' +
          (minor_signed > 9 ? '' : '0') + minor_signed.to_s + '.' +
          (patch_signed > 9 ? '' : '0') + patch_signed.to_s
        doc: |
          The version string exactly as Compose4BaseInfoSingleRead.x renders it
          (signed bytes; v() pads only values <= 9 with one '0'; '' for all
          zero).
        doc-ref:
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:363-368
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:439-457
  version_text:
    doc: |
      An ASCII version string, NUL-terminated inside the reply's zero fill: the
      aa 06 (soft version), aa 20 / aa 21 (Wi-Fi hw / sw version) and aa 07 03 /
      07 04 (hard / soft version) replies. The app reads them with
      BleUtil.getStrData (kt BleProtocolUtils.parseVersion = BleUtils.K): cut
      at the NUL, then String.trim() of chars <= 0x20 at both ends. Kaitai has
      no trim, so the client trims (PROCEDURAL). `text` decodes the bytes as
      UTF-8, the decoding the baseline library reads them with (PORT-review
      item 8): a sequence that is not UTF-8 raises there and here alike, but
      only when `text` is read, never while the message is parsed, so a reply
      with a byte >= 0x80 still reads and re-writes. The bytes after the NUL
      are not part of this type: they are the reply's lossless `rest`
      (request echo or zero fill).
      Firmware: H6006 / H6008 / H60A1 / H60A6 answer aa 20 with the aa 07 03
      string and aa 21 with the aa 06 one; H5083 sends the ASCII with no NUL
      when it fills the frame (strlen must be <= 17); H6047 sends '1.00.12' for
      aa 06 and aa 07 04 and '3.02.01' for aa 07 03.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/BleUtil.java:315-327
      - base-classes10/com/govee/base2kt/utils/BleUtils.java:239-269
      - base-classes10/com/govee/base2kt/utils/BleProtocolUtils.java:47-55
      - base-classes10/com/govee/base2light/ble/controller/WifiHardVersionController.java:28-30
      - base-classes10/com/govee/base2light/ble/controller/WifiSoftVersionController.java:28-30
      - H6006 code @0x9b03ca90
      - H6006 ble_msg_get_wifi_swversion @0x9b03cb0c
      - H5083 FUN_9b0318dc @0x9b0318dc
      - H5083 FUN_9b031984 @0x9b031984
      - H6047 handler @0x01005418
      - H6047 handler @0x01005370
    seq:
      - id: raw
        terminator: 0
        eos-error: false
        doc: |
          the bytes before the first NUL (all bytes when there is none); the
          NUL is consumed and the bytes after it are the message's rest
    instances:
      text:
        value: raw.to_s('UTF-8')
        doc: the version string, UTF-8 (raises for bytes that are not UTF-8)
  mac:
    doc: |
      A 6-byte MAC / device address, as one atomic id. The byte order depends
      on the message:
      - aa 14 (Wi-Fi MAC): forward (display) order at frame bytes 2..7.
        Legacy WifiMacController.parseValidBytes copies validBytes[0..5] and
        renders them with BleUtil.toAddressBytes(mac, true); kt
        ControllerWifiMacAddress feeds BleProtocolUtils.parseWifiMac, the same
        six bytes forward. Firmware forward too: H6006, H6008, H5083, H60A1,
        H60A6. H6047 answers a 17-byte field whose first 6 bytes are the MAC,
        or, before its Wi-Fi module has reported, later and unsolicited.
      - aa 07 06 (BLE MAC, FIRMWARE-SOURCED: no app controller sends 07 06):
        frame bytes 3..8; byte-reversed against the device's printed string on
        H6006 / H6008 / H60A1 / H60A6, printed order on H5083, the BLE stack's
        address bytes (likely LSB-first) on H6047. Static analysis only.
      The bytes alone do not say which order they are in; render per image.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/WifiMacController.java:9-11
      - base-classes10/com/govee/base2light/ble/controller/WifiMacController.java:28-46
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerWifiMacAddress.java:27-30
      - base-classes10/com/govee/base2kt/utils/BleProtocolUtils.java:31-36
      - base-classes10/com/govee/base2kt/utils/BleProtocolUtils.java:57-63
      - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4InfoBleIot$iControllerStrategy$1.java:191-197
      - split_bulblightstringv1/com/govee/bulblightstringv1/bulblight/ControllerStrategyConfig.java:648-654
      - H6006 code @0x9b03ca0c
      - H6008 code @0x9b03c844
      - H5083 FUN_9b031a24 @0x9b031a24
      - H60A1 ble_msg_get_wifi_mac_address @0x9b04533c
      - H60A6 ble_msg_get_wifi_mac_address @0x9b04a764
      - H6047 handler @0x010050dc
      - H6047 handler @0x0100b4fc
      - H6006 ble_msg_get_ble_macaddress @0x9b03c988
      - H6008 ble_msg_get_ble_macaddress @0x9b03c7ec
      - H60A1 ble_msg_get_ble_macaddress @0x9b0452b0
      - H60A6 ble_msg_get_ble_macaddress @0x9b04a6d8
      - H5083 ble_msg_get_ble_macaddress @0x9b0319cc
      - H6047 handler @0x01005370
      - H6047 code @0x010053ce
      - H6047 code @0x0101693c
      - H60A1 code @0x9b04dfdc
      - H60A6 code @0x9b053f9c
    seq:
      - id: addr
        size: 6
        doc: the six address bytes, in the order the message's doc gives
    instances:
      is_set:
        value: addr != [0, 0, 0, 0, 0, 0]
        doc: |
          Not all zero. Legacy WifiMacController.u fails the read on an all-zero
          MAC, and the H6047 connect read maps it to an empty string through
          ControllerWifiMacAddress.t; the default kt strategy and the gt-64
          bulbs store it unchecked.
        doc-ref:
          - base-classes10/com/govee/base2light/ble/controller/WifiMacController.java:39-46
          - base-classes11/com/govee/base2light/kt/general_controller/ControllerWifiMacAddress.java:44-52
          - split_h6047/com/govee/h6047/newdetail/vm/AfterConnectSuccessReadWifiInfo.java:97-101
          - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4InfoBleIot$iControllerStrategy$1.java:54-56
          - split_bulblightstringv1/com/govee/bulblightstringv1/bulblight/ControllerStrategyConfig.java:334-336
  section_count:
    doc: |
      [ic_count s16be][section_num s8]: the H6062 striplight IC (lamp-bead)
      count and section (segment) count. Two messages carry it:
      - the aa 41 reply on H6062 (ControllerIcSegmentNum.Companion.a reads
        BleUtil.getSignedShort(bArr[0], bArr[1]) and the Java byte bArr[2]
        from validBytes = frame bytes 2..18; ControllerStrategyConfig.X stores
        them as the IC count and the section count). The legacy
        IcSegmentNumController.parseValidBytes reads the same bytes and fails
        the read when ic_count <= 0 or section_num == 0;
      - the EE 21 push on H6062 (CheckIcNumParse.f, SINGLE_NOTIFY_IC = 33:
        getSignedShort(data[0], data[1]) and the byte data[2]).
    doc-ref:
      - split_h6062/com/govee/striplight/newdetail/ControllerIcSegmentNum.java:40-54
      - split_h6062/com/govee/striplight/newdetail/ControllerIcSegmentNum.java:65-67
      - split_h6062/com/govee/striplight/ble/BleProtocol.java:10
      - split_h6062/com/govee/striplight/newdetail/ControllerStrategyConfig.java:154-157
      - split_h6062/com/govee/striplight/newdetail/ControllerStrategyConfig.java:536
      - split_h6062/com/govee/striplight/ble/IcSegmentNumController.java:24-33
      - split_h6062/com/govee/striplight/ble/CheckIcNumParse.java:16-22
      - split_h6062/com/govee/striplight/ble/BleProtocol.java:13
      - base-classes10/com/govee/base2light/ble/BleUtil.java:304-306
    seq:
      - id: ic_count
        type: s2
        doc: IC count, signed as the app reads it (getSignedShort)
      - id: section_num
        type: s1
        doc: section count, the signed Java byte (0x80+ reads negative)
  timer_record:
    doc: |
      One 4-byte timer record [flags][hour][minute][repeat], the unit of the
      timer reads and writes:
      - lights: ControllerNewTimer (aa 23 / 33 23 / 0xAC TLV 0x23): Timer
        enableAndType, hour, minute, repeat (Companion.d copies 4 bytes per
        record; the write ctor sends [slot, enableAndType, hour, minute,
        repeat]);
      - H5083 plug (aa 13 page / 33 13 add): TimerInfo enableAndSwitch, hour,
        minute, repeat; TimerInfo.Companion.e reads enable = (flags & 0xF0) ==
        0x80 and open (switch on) = (flags & 0x0F) == 1, and clamps hour 0..23
        and minute 0..59 (a() / b()). Firmware stores the record as the u32 it
        receives (H5083 FUN_9b021a20 at base+0x20+4*slot).
      The meaning of `flags` is per family; the layout is the same.
    doc-ref:
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerNewTimer.java:52-77
      - base-classes11/com/govee/base2light/kt/general_controller/ControllerNewTimer.java:93-97
      - base-classes12/com/govee/base_h71xx/sku_base/adjust/component/bean/TimerInfo.java:39-50
      - base-classes12/com/govee/base_h71xx/sku_base/adjust/component/bean/TimerInfo.java:79-97
      - H5083 FUN_9b021a20 @0x9b021a20
    seq:
      - id: flags
        type: u1
        doc: enableAndType (lights) / enableAndSwitch (H5083)
      - id: hour
        type: u1
      - id: minute
        type: u1
      - id: repeat
        type: u1
        doc: repeat-day mask
  echo:
    doc: |
      The lossless tail of a message: every byte after the typed body, which
      is the request echo or the stale bytes the firmware leaves in its reply
      buffer, or the zero fill. Trailing zeros are stripped on read and
      restored on write, so a builder may leave `bytes` empty and the message
      still writes its full length. Every message type ends with one (R6).
      Firmware builds most replies in the request buffer, so the bytes after
      the data are the request's (zero in an app-built frame): H5083, H60A1 /
      H60A6, H6006 / H6008.
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
      The segment mask of a colour or brightness write (33 05 15 01, 15 02,
      0x0b), from the app's position set posSet (boolean[num_segments]): bit k
      of byte j is position 8j+k (BleUtils.X / Y reverse each 8-position chunk
      and parse it base 2), read LSB-first (bit-endian le).
      Width: 2 bytes (makeSelectedTwoBytes = BleUtils.Y, posSet zero-padded to
      16 positions, 00 00 for an empty set) when posSet has <= 16 positions,
      else ceil(n / 8) bytes (BleUtils.X, the last byte zero-padded). Bytes.G /
      .d / .h / .r / .l / .u / .C key it on posSet.length > 16; the legacy
      writers on their segment count > 16 (SubModeColorV1, h6047 SubModeColor),
      the same thing when posSet has one entry per segment.
      Each body carries at most `cap` bytes (op15_color 12 / 7 / 10 by form,
      op15 brightness 14, 0x0b mask 13): len_mask = min(len_full, cap) is what
      the truncating writers send (Bytes.G coerceAtMost 14, Bytes.d 13, Bytes.h
      7, Bytes.r 7, Bytes.C truncates to 7, h6047 14 / 7); the dropped bytes
      hold positions cap*8 and up. is_truncated marks a count past the cap,
      where the colour-instruction-5 writers Bytes.l / Bytes.u throw
      ArrayIndexOutOfBounds (no frame) and SubModeColorV1 opTypes 1 / 12 / 11
      append the whole mask (one byte past the cap lands on the BCC, more
      throws).
      num_segments is posSet.length. On the colour paths that is the piece
      count (ColorPieceConfig.c; the whole-device set of
      ColorModeControllerConfig, Bytes.C's boolean[i2]). On the per-segment
      brightness path (ColorModeControllerConfig.z) it is colorPosSetMaxLen,
      the piece count ROUNDED UP to a multiple of 8 (ColorPieceConfig.d ->
      LightColorCapabilityCenter.V0 / H0); that rounding changes neither the
      <= 16 test nor ceil(n / 8), so both counts give the same len_mask and the
      same bytes when no position at or above the piece count is set. A client
      that builds the brightness mask passes the rounded count, as the
      baseline library's segment_mask does (PORT-review item 6), so the valid
      below never rejects the app's bytes.
      Not this rule: Bytes.z (the 0x0b Kelvin-pick write) always calls
      makeSelectedTwoBytes, so above 16 pieces its mask is 00 00; and the
      per-family writers with no 2-byte floor
      (BleUtil.makeBytes4SelectPosByOneBit, or BleUtils.X alone) send 1 byte
      for <= 8 positions, the same zero-padded frame as len_mask 2.
    doc-ref:
      - base-classes10/com/govee/base2kt/utils/BleUtils.java:406-438
      - base-classes10/com/govee/base2kt/utils/BleUtils.java:444-494
      - base-classes10/com/govee/base2kt/utils/BleUtils.java:81-86
      - base-classes10/com/govee/base2light/ble/BleUtil.java:412-443
      - base-classes10/com/govee/base2light/ble/BleUtil.java:383-398
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:178-179
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:278-279
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:323-324
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:498-499
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:387-388
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:553-554
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:115-121
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:609-620
      - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:461-466
      - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:672
      - split_h6047/com/govee/h6047/ble/SubModeColor.java:180-181
      - split_h6047/com/govee/h6047/ble/SubModeColor.java:199-200
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:556-561
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:1184-1195
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/ColorPieceConfig.java:262-265
      - base-classes10/com/govee/base2light/capability/color/LightColorCapabilityCenter.java:1005-1022
      - base-classes10/com/govee/base2light/capability/color/LightColorCapabilityCenter.java:771-773
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5630-5634
      - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:10146-10148
    params:
      - id: num_segments
        type: u2
        doc: |
          posSet length: the piece count, or (brightness path) it rounded up
          to a multiple of 8; not in the stream
      - id: cap
        type: u1
        doc: the most mask bytes the body carries (12 / 7 / 10 / 14 / 13)
    seq:
      - id: bits
        type: b1
        valid:
          expr: not _ or _index < num_segments
        repeat: expr
        repeat-expr: len_mask * 8
        doc: |
          bits[i] = position i selected. Positions num_segments and up are
          always 0 in the app's bytes (posSet has num_segments entries; Y
          zero-pads to 16, X the last byte); the valid rejects a 1 there.
    instances:
      len_full:
        value: 'num_segments <= 16 ? 2 : (num_segments + 7) / 8'
        doc: |
          the encoder's length: 2 (makeSelectedTwoBytes) for <= 16 positions,
          else ceil(n / 8) (BleUtils.X)
      len_mask:
        value: 'len_full > cap ? cap : len_full'
        doc: the bytes the body carries, len_full cut to cap
      is_truncated:
        value: len_full > cap
        doc: |
          num_segments needs more than cap bytes: the coerceAtMost writers drop
          positions cap*8 and up; Bytes.l / Bytes.u throw
enums:
  direction:
    1:
      id: tx
      doc: |
        client to device: a GATT write on the control characteristic
        00010203-0405-0607-0809-0a0b0c0d2b11. A device simulator reads with tx.
    2:
      id: rx
      doc: |
        device to client: a notification on ...0a0b0c0d2b10. The device
        simulator writes with rx.
  pro_type:
    0x05:
      id: gid_info
      doc: |
        FIRMWARE-SOURCED: the Matter gid-info head. H60A6 / H60A1 read
        gid_info_len from the 3F buffer and store the encrypted gid
        (IOT_SYSCONFIG_SetGidEnc); the reply [05][0|1][18 x 00] carries no XOR
        stamp (the frame's checksum_kind none), and the dispatcher then echoes
        the frame as an unknown head. No app controller sends it.
      doc-ref:
        - H60A6 FUN_9b04c190 @0x9b04c190
        - H60A6 FUN_9b04da6c @0x9b04da6c
        - H60A6 FUN_9b04b8ac @0x9b04b8ac
    0x33:
      id: write
      -orig-id: SINGLE_WRITE
      doc: |
        tx: a single-frame command [33][command][params][zeros][XOR]
        (AbsSingleController.g, getProType 51 for a write). rx: the device's
        write ack [33][command][result][16 bytes]; the app matches it on
        [proType, command] (isSameController) and fails the write when result
        != 0 (AbsSingleController.t: value[2] == 0).
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:174
        - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:140-145
        - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:176-178
        - base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:66-68
    0x3a:
      id: write_read
      -orig-id: SINGLE_WRITE_READ
      doc: |
        the write-and-read variant: getProType 58 when the controller's
        write_read flag is set; its ack has the 0x33 ack layout
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:177
        - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:142
    0x3f:
      id: multiple_write_v1_3f
      -orig-id: MULTIPLE_WRITE_V1_3F
      doc: |
        FIRMWARE-SOURCED: a multi-frame write sent as ONE GATT write of n x 20
        bytes (Matter configuration). The app defines the constant (63) and no
        controller sends it. H60A6 / H60A1 (govee_mutlProtocol_matterPt_prase)
        check each 20-byte unit's XOR, START byte 3 = count and byte 4 = op,
        and answer only the END (or an error) with [3F][op][status]; H5083 and
        H6006 have the same head. H6008 accepts a 0x3F write of len % 20 == 0.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:35
        - H60A6 FUN_9b04c190 @0x9b04c190
        - H60A6 FUN_9b04b254 @0x9b04b254
        - H5083 FUN_9b03289c @0x9b03289c
        - H6006 code @0x9b03dbde-0x9b03dc16
    0x66:
      id: factory_test
      doc: |
        FIRMWARE-SOURCED, CAUTION (factory-test entry): 66 01 answers with bytes
        2..18 zeroed, 66 02 is echoed, any other 66 xx is forwarded and dropped
        (H6006, H6008, H60A1, H60A6, H5083). H6047 echoes any 66 frame and
        leaves group control. No app sender.
      doc-ref:
        - H6006 code @0x9b03d866-0x9b03d876
        - H6008 code @0x9b03d5e6-0x9b03d5f2
        - H60A6 FUN_9b04aa76 @0x9b04aa76
        - H60A1 code @0x9b04564e
        - H5083 FUN_9b031bfe @0x9b031bfe
        - H6047 code @0x010064be-0x010064d8
    0xa1:
      id: multi_write
      -orig-id: MULTIPLE_WRITE
      doc: |
        MultiPackageManager chunked write (A1 [comType][position][16 bytes]);
        rx: the write response [A1][comType][result], result == 0 = success
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:33
        - base-classes12/com/govee/ble/multi/MultiPackageManager.java:64-66
        - base-classes12/com/govee/ble/multi/MultiPackageManager.java:72-80
    0xa2:
      id: multi_read
      -orig-id: MULTIPLE_READ
      doc: |
        MultiPackageManager multi-packet read stream [A2][comType][position..]
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:32
        - base-classes12/com/govee/ble/multi/MultiPackageManager.java:60-62
        - base-classes12/com/govee/ble/multi/MultiPackageManager.java:82-124
    0xa3:
      id: multi_write_v1
      -orig-id: MULTIPLE_WRITE_V1
      doc: |
        MultipleControllerCommV1 scene / DIY dialect (non-MTU; commByte at byte
        4). rx: the ack [A3][command..], matched by isSameController
        [proType, command]; also the A3 08 IoT-status push.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:34
        - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Multi.java:69-70
        - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Multi.java:80-82
    0xa4:
      id: multi_write_v2
      -orig-id: MULTIPLE_WRITE_V2
      doc: |
        the MTU-sized dialect (makeSendBytesMtu; commByte at byte 6), sent only
        for command 0x58 with effect byte 0x5E / 0x5D / 0x20. rx: the
        ControllerMTUMulti ack; H60A6 answers a complete END with the constant
        [A4][58][00..].
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:36
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerMTUMulti.java:25
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerMTUMulti.java:32-34
        - H60A6 FUN_9b04c190 @0x9b04c190
    0xa5:
      id: mic
      doc: |
        the mic / music live-colour push: a SHORT frame [A5][command][data..]
        [sum8] (the frame's checksum_kind sum8), not a 20-byte XOR frame
        (AbsMicController.getProType = (byte) -91). Bulbs catch A5 02 before
        any length or BCC check and switch on byte 2 (0x83 MicSetRgbController:
        R, G, B to the colour-apply callback; 0x90 MicColorCmdController); no
        reply there. H60A1 / H60A6 take it on the length-exempt path and answer
        0x90 with the 5-byte A5 02 10 01 <sum>; H6047 the same (a5 02 10 01 b8).
        Distinct from command 0xA5 (color_group).
      doc-ref:
        - base-classes10/com/govee/base2light/ble/mic/controller/AbsMicController.java:55
        - H6006 _system_ble_event_handler @0x9b03df50
        - H6008 _system_ble_event_handler @0x9b03d8a2
        - H6006 ble_msg_group_test @0x9b03cf34
        - H6008 ble_msg_group_test @0x9b03cc64
        - H6006 code @0x9b0264b8
        - H6008 code @0x9b029638
        - H60A6 FUN_9b03c6b0 @0x9b03c6b0
        - H60A1 FUN_9b037458 @0x9b037458
        - H6047 FUN_01001cf8 @0x01001cf8
        - H6006 code @0x9b023804
        - H6008 code @0x9b0277ac
    0xa6:
      id: mtu_multiple_write
      -orig-id: MTU_MULTIPLE_WRITE
      doc: |
        the second MTU multi-packet head, passed to ControllerMTUMulti by
        Compose4DefWrite4Multi and DaySyncUtils; body not traced
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:31
        - base-classes11/com/govee/base2light/kt/ble/Compose4DefWrite4Multi.java:225
        - base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:1398
    0xaa:
      id: read
      -orig-id: SINGLE_READ
      doc: |
        tx: a read request [AA][command][selector..]; rx: its reply
        [AA][command][state] (getProType -86 for a read). The reply carries the
        request's proType and command, which is all the app matches it on.
        Firmware also pushes an unsolicited AA 01 on power changes (bulbs,
        H60A6, H5083).
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:151
        - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:144
        - base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:176-179
        - H60A6 FUN_9b027818 @0x9b027818
        - H5083 FUN_9b01fd18 @0x9b01fd18
    0xab:
      id: wifi_multi_reply
      -orig-id: MULTI_READ_AB
      doc: |
        the 0xAB multi-reply read, chunked like 0xAC but with no sub byte and
        13 first-chunk data bytes: AbsController4WifiSingleSendMultiBack (its
        subclass Controller4ReadWifiFuncList, command 3, sent by
        WifiFuncListGlobalAPI and AbsBleCommunicator); the base2newth sensor
        AbsAbReadController matches on proType 0xAB alone. The old name
        bbq_status was wrong (renamed per the clean-spec design 1.5).
        Firmware: H5083 AB 01 Matter-GID read and AB 02 [tL tH code]; H60A6
        chunks AB replies with the 0xAC chunker.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:59
        - base-classes10/com/govee/base2light/ble/controller/AbsController4WifiSingleSendMultiBack.java:25-28
        - base-classes10/com/govee/base2light/ble/controller/Controller4ReadWifiFuncList.java:70-94
        - base-classes12/com/govee/base2light/wifi/WifiFuncListGlobalAPI.java:318
        - base-classes12/com/govee/base2light/wifi/AbsBleCommunicator.java:136
        - base-classes12/com/govee/base2newth/one2multi/AbsAbReadController.java:30-38
        - H5083 FUN_9b0327f0 @0x9b0327f0
        - H60A6 FUN_9b04b906 @0x9b04b906
    0xac:
      id: status_read
      -orig-id: MULTI_READ_AC
      doc: |
        the single-request / multi-reply status read: tx [AC][command][args];
        rx the chunked reply burst (AbsController4BleSingleSendMultiBack), whose
        joined data is the status TLV stream
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:60
        - base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:163-165
        - base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:202-226
    0xb0:
      id: group_pro_start
      doc: |
        FIRMWARE-SOURCED: group_pro head B0 ('START CMD' on H60A6; the query on
        H6047, answered b0 01 with an additive sum). On H60A6 every B head is
        routed before the length and BCC checks, powers on and switches to mode
        0xA1, with no reply. H6008 drops B0..BF after decryption; H6006
        forwards them and its dispatcher drops them. No app sender found.
      doc-ref:
        - H60A6 FUN_9b03d350 @0x9b03d350
        - H60A1 FUN_9b038108 @0x9b038108
        - H6047 FUN_010105a4 @0x010105a4
        - H6047 FUN_010104fc @0x010104fc
        - H6008 code @0x9b03d554-0x9b03d56a
        - firmware-h6006/decompiled/H6006_ble_dispatcher.c:108-121
    0xb1:
      id: group_pro_b1
      doc: |
        FIRMWARE-SOURCED group_pro head B1 (H6047: 6 x RGB per zone; H60A6
        FUN_9b03d234); see group_pro_start
      doc-ref:
        - H60A6 FUN_9b03d234 @0x9b03d234
        - H6047 FUN_010105a4 @0x010105a4
    0xb2:
      id: group_pro_b2
      doc: |
        FIRMWARE-SOURCED group_pro head B2 (H6047: 6 x RGB gradient; H60A6
        FUN_9b03d1f8 copies 18 bytes); see group_pro_start
      doc-ref:
        - H60A6 FUN_9b03d1f8 @0x9b03d1f8
        - H6047 FUN_010105a4 @0x010105a4
    0xb3:
      id: group_pro_b3
      doc: |
        FIRMWARE-SOURCED group_pro head B3 (H60A6: main / sub mode u16le + 4 x
        RGB; H6047 colour block, answered b3 01 03); see group_pro_start
      doc-ref:
        - H60A6 FUN_9b03d180 @0x9b03d180
        - H6047 FUN_010104fc @0x010104fc
    0xb4:
      id: group_pro_b4
      doc: |
        FIRMWARE-SOURCED group_pro head B4 (H60A6: 6 x RGB; H6047 colour block,
        answered b4 01); see group_pro_start
      doc-ref:
        - H60A6 FUN_9b03d150 @0x9b03d150
        - H6047 FUN_010104fc @0x010104fc
    0xb5:
      id: group_pro_b5
      doc: |
        FIRMWARE-SOURCED group_pro head B5 (params; H60A6 FUN_9b03d104); see
        group_pro_start. B6..BE: no handler found (H6008 drops the whole range).
      doc-ref:
        - H60A6 FUN_9b03d104 @0x9b03d104
        - H6047 FUN_010105a4 @0x010105a4
    0xbf:
      id: group_pro_exit
      doc: |
        FIRMWARE-SOURCED group_pro head BF ('EXIT CMD' on H60A6); see
        group_pro_start
      doc-ref:
        - H60A6 FUN_9b03d350 @0x9b03d350
    0xe7:
      id: handshake
      doc: |
        the encryption handshake: Controller4Aes ([E7 01] session key, [E7 02]
        confirm) and Controller4AesGcm ([E7 19] / [E7 1A]); frames after it
        are encrypted (the transport decrypts before parsing)
      doc-ref:
        - base-classes13/com/govee/encryp/ble/Controller4Aes.java:112
        - base-classes13/com/govee/encryp/ble/Controller4Aes.java:135
        - base-classes13/com/govee/encryp/ble/Controller4AesGcm.java:186
        - base-classes13/com/govee/encryp/ble/Controller4AesGcm.java:52
    0xee:
      id: notify
      -orig-id: NOTIFY
      doc: the device push [EE][sub][data] (BleProtocolConstants.NOTIFY = -18)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:79
  command:
    0x00:
      id: heartbeat
      -orig-id: SINGLE_HEART_BEAT
      doc: |
        AA 00 / 33 00: the newdetail ShareVM heartbeat when I3() is false
        (H60A1 / H60A6; ConnectHeartbeat.c(2)). Firmware: H60A1 / H60A6 have an
        explicit no-op case (AA echoed, 33 acked status 0); H6006 / H6008 send
        no reply. As a 0xAC TLV tag, 0 is an ORDINARY TLV (u() has no padding
        sentinel; the value goes to the screen's fallback lambda, which on
        H60A6 ignores it).
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:125
        - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:241-242
        - firmware-h6006/decompiled/H6006_ble_dispatcher.c:507-519
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:254-285
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:335-341
        - H60A6 FUN_9b04c11c @0x9b04c11c
        - H60A6 code @0x9b026c6a
        - H60A1 code @0x9b026140
    0x01:
      id: power
      -orig-id: [SINGLE_SWITCH, SINGLE_HEART, SINGLE_MAIN_SWITCH]
      doc: |
        power: 33 01 write (light 1/0 or the plug relay byte, per form f_01),
        AA 01 reply (a light's state, == 1 on the control screens, or a plug's
        relay bitmask), 0xAC TLV 0x01 (value[0] != 0, u():286-288), and the
        unsolicited AA 01 push.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:161
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:286-288
    0x02:
      id: cmd_02
      doc: |
        Overloaded by device family (form_02): H5089 (goodsType 307) total
        switch 33 02 [state] (Controller4TotalSwitch.getCommandType = 2) or
        H5086's chart prepare 33 02 with no payload
        (ChartPrepareInfoController.getCommandType = 2, on its own service).
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4TotalSwitch.java:23-25
        - split_plugnew/com/govee/h5086/ble/chartcontroller/ChartPrepareInfoController.java:41-43
    0x04:
      id: brightness
      -orig-id: SINGLE_BRIGHTNESS
      doc: brightness (33 04 write, AA 04 reply, TLV 0x04)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:108
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:293-295
    0x05:
      id: mode
      -orig-id: SINGLE_MODE
      doc: |
        mode (33 05 <sub_mode> write, AA 05 [01] read, AA 05 reply and TLV
        0x05 [sub_mode][params], the latter handed to the screen's mode lambda
        with exactly len bytes, u():300-308)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:130
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:300-308
    0x06:
      id: soft_version
      -orig-id: SINGLE_SOFT_VERSION
      doc: AA 06 soft (firmware) version read; ASCII reply
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:159
    0x07:
      id: device_info
      -orig-id: SINGLE_DEVICE_INFO
      doc: |
        AA 07 <selector> device info (enum device_info_selector); TLV 0x07
        records, of which w() decodes only 0x10 / 0x11 (u():309-310)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:113
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:309-310
    0x09:
      id: sync_time
      -orig-id: SINGLE_SYNC_TIME
      doc: 33 09 time sync (three bodies by form_09; TimerConfig.Companion.f)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:162
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:169-182
    0x0a:
      id: auto_time
      -orig-id: SINGLE_AUTO_TIME
      doc: auto time setting
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
        current limit setting. CAUTION, firmware-sourced: on H6047 any 33 0e
        stores the flag, saves the config and reboots the device (NVIC system
        reset through AIRCR); AA 0e reads [flag].
      doc-ref:
        - firmware-h6047/decompiled/H6047_decompiled.c:4677
    0x0f:
      id: light_num
      doc: light count (H6047 AA 0f answers [10])
      doc-ref:
        - H6047 handler @0x01004ca0
    0x11:
      id: sleep
      -orig-id: SINGLE_SLEEP
      doc: |
        sleep timer (33 11 / AA 11; TLV 0x11 -> TimerConfig.Companion.m, whose
        layout is chosen by the SKU's sleep-config version, 9 parsers: not
        typed here)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:158
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:311-316
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:277-329
    0x12:
      id: cmd_12
      -orig-id: [SINGLE_WAKEUP]
      doc: |
        Overloaded by device family (form_12): the wake-up timer (33 12 / AA
        12; TLV 0x12 -> TimerConfig.Companion.n, per-SKU layout version) or the
        H5083 timer-count read (AA 12 -> [group echo][count],
        H5083NewDetailRepository loadParser.3 reads bArr[1]).
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:166
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:317-322
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:332-388
        - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:220
        - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:147-149
    0x13:
      id: cmd_13
      -orig-id: [NIGHT_MODE]
      doc: |
        Overloaded by device family (form_13): night mode, or the H5083 plug
        timer (33 13 [outlet][slot][flags][hour][minute][repeat] add; AA 13
        [group][page] read -> [page | group << 4][4 x timer_record]).
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:78
        - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:234-246
        - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:111-143
        - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:260-262
    0x14:
      id: cmd_14
      -orig-id: [SINGLE_WIFI_MAC]
      doc: |
        Overloaded by device family (form_14): the gradual (fade) switch on the
        GradualVersion-1 families (33 14 [on]: Bytes.v, kt ControllerGradual,
        the legacy GradualController; the night-mode page writes 33 14 01 / 00)
        or the Wi-Fi MAC read on Wi-Fi SKUs (AA 14: WifiMacController f = 20,
        kt ControllerWifiMacAddress; H6047 PairAcV1, the gt-64 bulbs).
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:169
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:559-560
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerGradual.java:59-61
        - base-classes10/com/govee/base2light/ble/controller/WifiMacController.java:9-11
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerWifiMacAddress.java:27-30
    0x15:
      id: plug_timer_delete
      doc: |
        H5083 timer delete 33 15 [outlet = 0][slot]: the firmware clears the
        slot and shifts the later ones down (gate payload[0] == 0 and
        payload[1] < 10)
      doc-ref:
        - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:153
        - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:264
        - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:275
        - H5083 FUN_9b021a54 @0x9b021a54
    0x16:
      id: cmd_16
      -orig-id: [VALUE_SETTING_LIGHT_INDICATOR, EFFECT_DIRECTION]
      doc: |
        Two meanings, neither body typed yet (so no form): the light-indicator
        setting, and the h61d3 adjustNew screen's effect flow direction
        (ControllerEffectDirection, command 22; its 0xAC TLV 0x16 value[0],
        unsigned, goodsType 263 gate H61D3Support.l0).
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:188
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:18
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerEffectDirection.java:61-62
        - split_h61d3/com/govee/h61d3/adjustNew/VM4Light$afterConnectedSingleReadDeviceInfo$1.java:141-150
        - split_h61d3/com/govee/h61d3/pact/H61D3Support.java:1340-1342
    0x17:
      id: wifi_link_start
      doc: Wi-Fi link start
    0x20:
      id: wifi_hard_version
      -orig-id: SINGLE_WIFI_HARD_VERSION
      doc: |
        AA 20 Wi-Fi hw version (WifiHardVersionController f = 32 / kt
        ControllerWifiHardVersion): ASCII from frame byte 2. H6006 / H6008 /
        H60A1 / H60A6 answer the AA 07 03 string; H6047 answers 17 bytes, or
        later and unsolicited before its Wi-Fi module reports.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:168
        - H6047 handler @0x01005140
        - H6047 handler @0x0100b4fc
    0x21:
      id: wifi_soft_version
      -orig-id: SINGLE_WIFI_SOFT_VERSION
      doc: |
        AA 21 Wi-Fi sw version (WifiSoftVersionController f = 33 / kt
        ControllerWifiSoftVersion), sent on H6047 (PairAcV1, Compose4InfoBleIot)
        and the gt-64 bulbs: ASCII from frame byte 2; H6006 / H6008 / H60A1 /
        H60A6 (ble_msg_get_wifi_swversion) answer the AA 06 string.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:171
        - split_bulblightstringv1/com/govee/bulblightstringv1/bulblight/ControllerStrategyConfig.java:634-647
        - H6047 handler @0x0100510e
    0x23:
      id: new_timer
      -orig-id: SINGLE_NEW_TIME_V1
      doc: |
        the four switch timers: 33 23 [slot][timer_record], AA 23 <slot | FF>
        -> [slot][record] or [FF][4 records] (ControllerNewTimer.Companion.d),
        TLV 0x23 (u():323-334 -> the same d()). Firmware: H6006 / H6008 answer
        a single-slot read with slot 0's record and the requested slot number
        echoed; H6008 compares the slot signed.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:142
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerNewTimer.java:52-77
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:323-334
        - firmware-h6006/decompiled/H6006_ble_dispatcher.c:446-495
        - firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c:303-335
    0x24:
      id: light_effect_start
      -orig-id: [SINGLE_READ_IC_POINT, value_set_light_start]
      doc: |
        33 24 [max(index, 1)][time u16be]: SetLightStartController, sent from
        the connect / bind steps. Firmware-true meaning: the pairing light
        effect (H6008 pair_effect_thread; H60A1 / H60A6
        govee_device_start_tmp_mode(0x24)), not an IC read. H6006 has no
        handler.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:240
        - firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c:111-116
        - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:411-430
        - firmware-h60a1-wifi/decompiled/H60A1_ble_dispatcher.c:340-359
    0x25:
      id: light_effect_stop
      -orig-id: [SINGLE_PRE_SET_COLOR, value_set_light_end]
      doc: |
        33 25 01: stops the 0x24 light effect (H6008 msg_set_dev_lamp_stop;
        H60A1 / H60A6 MSG_STOP_PAIR_MODE; any byte other than 01 is a no-op
        acked 0), not a preset colour.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:147
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:239
        - firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c:87-90
        - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:431-435
        - firmware-h60a1-wifi/decompiled/H60A1_ble_dispatcher.c:382-386
    0x26:
      id: take_photo_or_colortemp_type
      doc: take-photo / colour-temperature type (two family meanings, untyped)
    0x27:
      id: preview_effect
      -orig-id: SET_PREVIEW_EFFECT
      doc: preview effect
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:104
    0x28:
      id: delete_scene
      -orig-id: SINGLE_DELETE_SCENE
      doc: delete scene
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:112
    0x29:
      id: sort_scene
      -orig-id: SINGLE_SORT_SCENE
      doc: sort scenes
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:160
    0x30:
      id: cmd_30
      -orig-id: [SINGLE_LIGHT_SWITCH, CALIBRATION, NOTIFY_DETAIL]
      doc: |
        Overloaded by device family (form_30): pact_h60a0 per-zone power (33 30
        [zone][state]; AA 30 / TLV 0x30 = bit 0 of value[0], value[1] (and
        value[2] on H12D0)) or the single-byte direction value (H6038
        calibration direction, TLV 0x30 value[0]; H6057 main-light switch; the
        H71xx chart request). H6048 neither requests nor decodes 0x30.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:128
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:9
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:102-104
        - split_h60ax/com/govee/pact_h60a0/adjust/h12d0/VM4LightH12D0$afterConnectedSingleReadDeviceInfo$1.java:78-81
        - split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:579-590
    0x31:
      id: camera_position
      doc: camera position
    0x32:
      id: check_camera
      -orig-id: MSG_TYPE_READ_INSTALL_CAMERA
      doc: camera installation check
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:30
    0x33:
      id: volume
      -orig-id: SINGLE_VOLUME
      doc: volume (H6047 AA 33 answers [bar1 << 4 | bar0][flag])
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:165
        - H6047 handler @0x010069f0
    0x34:
      id: swap_light
      doc: swap the two bars' logical positions (H6047 AA 34 answers [swap])
      doc-ref:
        - H6047 handler @0x01005080
    0x35:
      id: without_interrupt
      -orig-id: SINGLE_WITHOUT_INTERRUPT
      doc: without-interrupt setting (H6047 AA 35 answers [ff])
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:172
        - H6047 handler @0x01004b22
    0x36:
      id: cmd_36
      -orig-id: [SINGLE_WORRY_FREE_AT_NIGHT]
      doc: |
        Overloaded by device family (form_36): H6047's bar switch (33 36
        [left][right]; AA 36 [left][right][feast]), the [index][state] form of
        H6043 / H6038 / H6048 / H60B2 / H1771 / H60A0 (whose 0xAC TLV 0x36 is
        bit flags of value[0]), or H6057's 7-byte worry-free-at-night write.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:173
        - split_h6047/com/govee/h6047/detail/NewDetailVm.java:696-718
        - split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:591-593
        - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:846-850
        - split_h6047/com/govee/h6047/newdetail/h6048/VM4H6048.java:344
    0x37:
      id: prompt_tone
      -orig-id: SINGLE_PROMPT_TONE
      doc: prompt tone
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:150
    0x39:
      id: check_direction
      -orig-id: PROTOCOL_CHECK_DIRECTION
      doc: |
        calibration direction check; H6039's 0xAC TLV 0x39 value[0] is the
        calibration direction (stored with the setter VM4H6038 uses for 0x30)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:100
        - split_h6047/com/govee/h6047/newdetail/vm/VM4Light.java:309-320
    0x40:
      id: ic_num
      -orig-id: [IC_NUM, SINGLE_IC_SEGMENT_NUM]
      doc: |
        IC / segment count: AA 40 reply [ic s16be][segment]
        (ControllerOnlyReadIcSegmentNum), the only live-BLE capability read;
        TLV 0x40 has the same prefix (H6671 / H6672 getSignedShort(value[0],
        value[1]); h61d3 ControllerIcNum.e [ic s16][section u8] then an optional
        second pair).
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:24
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerOnlyReadIcSegmentNum.java:30-37
        - split_rgbiclight/com/govee/rgbiclight/h667172/VM4LightH667172$afterConnectedSingleReadDeviceInfo$1.java:96-107
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerIcNum.java:141-153
        - split_h61d3/com/govee/h61d3/adjustNew/VM4Light$afterConnectedSingleReadDeviceInfo$1.java:125-134
    0x41:
      id: cmd_41
      -orig-id: [SINGLE_ON_OFF_MEMORY, SINGLE_GET_DEVICE_INFO]
      doc: |
        Overloaded by device family (form_41): the power-off memory record
        [02][mode] (33 41 02 <v> write, AA 41 reply on H60A6, TLV 0x41 on H60A6
        / H6671 / H6672 / H6038 / H6048, the app reads byte 1) or the H6062 AA
        41 section-count reply [ic s16be][section s8].
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:143
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:99-100
        - split_rgbiclight/com/govee/rgbiclight/h667172/VM4LightH667172$afterConnectedSingleReadDeviceInfo$1.java:108-115
        - split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:564-571
        - split_h6062/com/govee/striplight/newdetail/ControllerIcSegmentNum.java:40-54
    0x42:
      id: write_check_ic
      -orig-id: SINGLE_WRITE_CHECK_IC
      doc: |
        firmware-sourced, H60A6: calibration mode (33 42 01 enter / 02 xx step
        / FF commit / 00 exit; app CalibrationAcH60A6; AA 42 runs it too)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:175
        - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:483-548
        - H60A6 code @0x9b0269fe
    0x43:
      id: check_ic_amount
      -orig-id: [SINGLE_WRITE_CHECK_IC_AMOUNT, OP_COMMAND_TYPE_0X43]
      doc: IC amount check
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:176
    0x44:
      id: calibration
      doc: |
        calibration (H6048 CalibrationController4H6048, value_calibration = 68;
        its ack's result is frame byte 3, p() = 1)
      doc-ref:
        - split_h6047/com/govee/h6047/newdetail/control/CalibrationController4H6048.java:23-24
        - split_h6047/com/govee/h6047/newdetail/control/CalibrationController4H6048.java:44-46
    0x46:
      id: reset_device_net
      -orig-id: [SINGLE_CHECK_IC, SINGLE_RESET_DEVICE_NET]
      doc: |
        CAUTION, destructive. Firmware-true meaning: on H60A1 / H60A6, 33 46 is
        a factory reset and reboot (ack first, then Govee_GatewayConfig_Reset,
        HAL_Matter_Reset); on H6008 a network / Matter reset after the ack.
        Never send it as an IC check.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:109
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:156
        - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:454-468
        - firmware-h60a1-wifi/decompiled/H60A1_ble_dispatcher.c:388-400
        - firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c:117-119
    0x50:
      id: movie_feast_set_device
      doc: movie-feast sub-device setting
    0x54:
      id: movie_feast_on_off
      -orig-id: movie_feast_on_off
      doc: movie-feast on / off
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:195
    0x60:
      id: feast_carousel
      -orig-id: MODULE_FEAST
      doc: feast module (H6047 AA 60 <sub> answers per sub)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:26
        - H6047 handler @0x010049bc
    0x62:
      id: combination_effect
      -orig-id: MULTI_COMBINATION_EFFECT
      doc: combination effect
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:40
    0x63:
      id: daysync_op
      -orig-id: DAYSYNC_OP
      doc: day-sync operation
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:16
    0x70:
      id: music_lib_notify
      -orig-id: SINGLE_MUSIC_LIB_NOTIFY
      doc: music library
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:131
    0x71:
      id: music_lib_player_state
      -orig-id: SINGLE_MUSIC_LIB_READ_PLAYER_STATE
      doc: music library
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:132
    0x72:
      id: music_lib_preset_info
      -orig-id: SINGLE_MUSIC_LIB_READ_PRESET_INFO
      doc: music library
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:134
    0x73:
      id: music_lib_preset_color
      -orig-id: SINGLE_MUSIC_LIB_READ_PRESET_COLOR
      doc: music library
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:133
    0x74:
      id: music_lib_preset_scenes
      -orig-id: SINGLE_MUSIC_LIB_READ_PRESET_SCENES
      doc: music library
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:135
    0x75:
      id: music_lib_set_play_state
      -orig-id: SINGLE_MUSIC_LIB_SET_PLAY_STATE
      doc: music library
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:137
    0x76:
      id: music_lib_preview_effect
      -orig-id: SINGLE_MUSIC_LIB_SET_PREVIEW_EFFECT
      doc: music library
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:138
    0x77:
      id: music_lib_remove_scene
      -orig-id: SINGLE_MUSIC_LIB_REMOVE_PRESET_SCENES
      doc: music library
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:136
    0x78:
      id: music_lib_sort_scenes
      -orig-id: SINGLE_MUSIC_LIB_SORT_PRESET_SCENES
      doc: music library
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:141
    0x79:
      id: music_lib_volume
      -orig-id: SINGLE_MUSIC_LIB_SET_VOLUME
      doc: music library
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:140
    0x7a:
      id: music_lib_switch
      -orig-id: SINGLE_MUSIC_LIB_SET_SWITCH
      doc: music library
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:139
    0x84:
      id: set_alarm_clock
      -orig-id: SET_DEVICE_ALARM_CLOCK
      doc: alarm clock
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:102
    0xa2:
      id: color_group_rgb
      doc: |
        AA A2 <group>: a colour group with 3-byte [R, G, B] records
        (Controller4ColorInfoByGroup generateReadController4OnlyColor fills
        command -94; colour-only families), or H61A8's mechanism-B V1 batch
        (BulbStringColorController): per form_a2. Old name
        bulb_string_color_read.
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:44-49
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:122-131
    0xa3:
      id: gradual
      -orig-id: SINGLE_GRADUAL_CHANGE_WIFI_BLE
      doc: |
        the gradual (fade) switch on GradualVersion 0 (33 A3 [on]: Bytes.w,
        GradualChangeBleWifiController, the compose GradualController; AA A3
        read: kt ControllerGradual4BleWifi; TLV 0xA3 value[0] == 1 on
        straightfloorlamp / H1771)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:122
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:564-566
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerGradual4BleWifi.java:94-96
        - split_hollowlamp/com/govee/straightfloorlamp/adjust/VM4Light$afterConnectedSingleReadDeviceInfo$1.java:118-119
    0xa5:
      id: color_group
      -orig-id: MSG_READ_BULB_LIGHT_COLOR_WITH_BRIGHTNESS
      doc: |
        AA A5 <group> / TLV 0xA5: a colour group with 4-byte [brightness, R, G,
        B] records (Controller4ColorInfoByGroup generateReadController, command
        -91; the 0xAC status lambdas), or H61A8's mechanism-B V2 batch
        (BulbStringColorControllerV2): per form_a5. Old name local_color_read.
        Not the proType 0xA5 (mic) nor the 0xA3 commByte 0xA5.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:28
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:37-42
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:160-175
    0xa6:
      id: special_function
      -orig-id: SINGLE_SPECIAL_FUC
      doc: |
        H6062 position write 33 A6 01 v (ControllerPos); its ack's result is
        frame byte 3 (p() = 1). H6047 also answers AA A6 [en]([4]) (logo).
      doc-ref:
        - split_h6062/com/govee/striplight/newdetail/ControllerPos.java:56-58
        - split_h6062/com/govee/striplight/newdetail/ControllerPos.java:73-75
        - H6047 handler @0x01004a28
    0xa9:
      id: white_balance
      doc: white balance
    0xaa:
      id: check_light
      doc: light check
    0xab:
      id: dynamic_api_support
      -orig-id: SINGLE_DYNAMIC_API_SUPPORT
      doc: |
        dynamic-API support (H60A1 / H60A6 answer AA AB [02]; H6006 / H6008
        dispatch on it). Distinct from proType 0xAB.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:118
        - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:566-571
        - firmware-h6006/decompiled/H6006_ble_dispatcher.c:504-505
    0xb0:
      id: cmd_b0
      doc: |
        Per form_b0: the h5080 plug delay (TurnOnOffDelayController: AA B0
        [outlet][type] -> [outlet][type][hour][minute][remaining u24be]; 33 B0
        [outlet][type][hour][minute]). The H5083 connect reads AA B0 00 00 and
        AA B0 00 01.
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/TurnOnOffDelayController.java:90-92
        - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:182-188
        - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:158-187
    0xb1:
      id: secret_read
      -orig-id: SINGLE_READ_SECRET_KEY
      doc: |
        AA B1 account-lock secret read (reply [selector][8 bytes]); not the
        wire cipher. Firmware: the B1 5 s window (H60A6), the H6047 state flow.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:152
        - base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:57-66
        - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:574-610
    0xb2:
      id: secret_write
      -orig-id: SINGLE_CHECK_SECRET_KEY
      doc: 33 B2 account-lock secret write (the 8 bytes the AA B1 reply carried)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:110
        - base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:22-26
    0xb3:
      id: plug_spec
      doc: h5080 plug spec read (SpecController; reply [spec])
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SpecController.java:6-13
    0xb4:
      id: plug_timer
      doc: |
        h5080 plug timer (NewTimerController AA B4 [h == 1 ? 01 : 00, FF])
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/NewTimerController.java:67-69
    0xb5:
      id: plug_sync_time
      doc: h5080 plug time sync (33 B5 [unix u32be][01][tz_h][tz_m])
    0xed:
      id: notify_recognition
      -orig-id: NOTIFY_RECOGNITION
      doc: recognition notify (an outbound 0x33 ED writer; no inbound decoder)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:91
    0xee:
      id: ota_prepare
      -orig-id: SINGLE_OTA_PREPARE
      doc: |
        OTA prepare. H6047 33 ee stops the running effect and acks 00 for any
        frame type; AA ee answers [00].
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:145
        - H6047 handler @0x01004f00
    0xef:
      id: pact
      -orig-id: SINGLE_PACT
      doc: |
        AA EF pact (advertising protocol) read: PactController reply
        [pact_type u16be][pact_code]. Sent only from old adjust flows
        (AbsBlePact.o) and BlePactChecker. Firmware answers 00 01 01 on H6006 /
        H60A1 / H60A6 and H6047, 00 02 02 on H5083; H6008 does not answer.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:146
        - base-classes10/com/govee/base2light/ble/controller/PactController.java:9-20
        - H6006 ble_msg_get_adv_pro_type @0x9b03cc00
        - H5083 FUN_9b031b80 @0x9b031b80
        - H6047 handler @0x01004d38
    0xff:
      id: debug
      doc: |
        FIRMWARE-SOURCED, CAUTION (test hooks; no app controller uses command
        0xFF). AA FF 08 reads the factory ageing flag (H60A1 / H60A6; on H60A6 a
        flag of 0 also runs FUN_9b033c3c and stores 0); H6047 AA FF 08 answers
        [08][flash word at 0x79400 != ffffffff]. 33 FF is a factory test: it
        stops advertising on H6006 / H6008, clears the advertise-after-
        disconnect flag on H5083, and stops the device service on H6047.
      doc-ref:
        - firmware-h60a1-wifi/decompiled/H60A1_ble_dispatcher.c:497-505
        - firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c:662-672
        - firmware-h6047/decompiled/H6047_decompiled.c:4707
        - firmware-h6047/decompiled/H6047_decompiled.c:5955
        - firmware-h6006/decompiled/H6006_ble_dispatcher.c:195-251
        - firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c:291-304
        - H6047 handler @0x010059a4
        - H6047 code @0x01006dac
  sub_mode:
    0x00:
      id: video
      doc: video mode
    0x02:
      id: color_legacy
      -orig-id: sub_mode_operate_type_2
      doc: |
        colour sub-mode 0x02 (a SubMode4Color sub-mode on the newdetail
        screens, OpInfo4Detail.x; decoded by mode_decode.c02)
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:766
    0x04:
      id: scene
      -orig-id: sub_mode_scenes
      doc: scene (SubMode4Scene, the scene code u16le)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:208
        - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4Scene.java:24-29
    0x05:
      id: mic
      doc: mic (H6006 AA 05 05 [00][R G B])
      doc-ref:
        - firmware-h6006/decompiled/H6006_ble_dispatcher.c:587-602
    0x0a:
      id: diy
      -orig-id: sub_mode_new_diy
      doc: DIY (SubMode4Diy, the DIY code s16le)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:203
        - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4Diy.java:36-40
    0x0b:
      id: color_rgbic_0b
      doc: |
        colour sub-mode 0x0b (BleProtocol music_code_yuedong = 11). Its write
        layout is chosen by form_0b (rgb_mask / rgb_kelvin / H6057 game); its
        report by mode_decode.c0b.
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:766
    0x0c:
      id: music_alt
      doc: alternative music sub-mode
    0x0d:
      id: color_cct_0d
      doc: colour sub-mode 0x0d (bulbs / lamps; report by mode_decode.c0d)
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:766
    0x0e:
      id: music_string
      doc: string-light music sub-mode
    0x0f:
      id: music_lamp
      doc: lamp music sub-mode
    0x11:
      id: music_dreamcolor
      doc: dreamcolor music sub-mode
    0x13:
      id: music
      -orig-id: sub_mode_music
      doc: |
        music (report by mode_decode.music). H60A6 stores mode 0xA0 for it and
        reports it as 0x13.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:202
    0x14:
      id: color_floor_lamp_14
      doc: |
        the colour sub-mode of the straightfloorlamp package (its BleProtocol
        sub_mode_color = 20; report by mode_decode.c14); the colour tab of
        OpInfo4Detail.l takes {0x14, 0x15, 0x02, 0x0d}
      doc-ref:
        - split_hollowlamp/com/govee/straightfloorlamp/ble/BleProtocol.java:11
        - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:281
    0x15:
      id: color_rgbic_15
      -orig-id: sub_mode_color
      doc: colour sub-mode 0x15 [op][data] (report by mode_decode.c15)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:199
        - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:766
    0xa1:
      id: scene_a1
      doc: |
        the second scene byte: base OpInfo4Detail.x hands an 0xA1 report to
        SubMode4Scene like 0x04 (bulbs, H6188, H61A8), but the screen's mode
        map has no entry for it and shows colour mode (no params type: the
        bytes stay in the message's rest; h60a1 Info4Detail.Q does not read
        it). H60A6 stores mode 0xA1 while a group_pro (B) head is active.
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:786
        - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:280-321
        - H60A6 FUN_9b03d350 @0x9b03d350
  op15:
    0x00:
      id: set_mode
      doc: |
        switch to the colour mode, [0x15, 0x00] with nothing after it
        (SubModeColorV1 opType 3, OP_TYPE_SET_MODE; ComposeChange2ColorMode)
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:699-701
        - split_h60ax/com/govee/pact_h60a0/ble/controller/compose/ComposeChange2ColorMode.java:32
    0x01:
      id: set_color
      doc: |
        colour write; three forms share op 0x01 with no in-frame discriminator
        (form_15_01: basic / kelvin_tint / kelvin)
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:684-694
    0x02:
      id: set_brightness
      doc: per-segment brightness [pct][seg_mask] (Bytes.G)
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:172-181
    0x03:
      id: set_brightness_group
      doc: brightness levels [level x num_levels]
    0x04:
      id: set_color_group
      doc: |
        group colour write [group][rgb x n] (Bytes.m) or the colour-instruction
        version 9 kelvin [kelvin u2be] (Bytes.D): form_15_04
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:393-402
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:133-139
    0x05:
      id: set_color_temp
      doc: |
        three writers share op 0x05 (form_15_05): the H60A0 Kelvin [kelvin
        u2be] (SubModeColorV1 opType 2), the goodsType-80 side brightness
        [side][value] (Bytes.H) and the gradient [rgb x 3][mask] (Bytes.n)
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:695-698
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:184-199
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:404-420
  notify_sub:
    0x01:
      id: light_status
      doc: legacy inner code under the old 0x30 scheme; no newdetail parser
    0x02:
      id: energy_saving
      doc: energy saving (other categories)
    0x03:
      id: battery
      doc: battery (other categories)
    0x04:
      id: music
      doc: music (other categories)
    0x05:
      id: pressure_or_volume
      doc: H5130 current pressure; the volume detail on some devices
    0x11:
      id: wifi_connect
      doc: |
        Wi-Fi status: data[0] == 0 connected (DefParser.Q). Firmware sends
        [00] or [01][reason] (reason 1..6 on H5083: credential blob rejected,
        connect failure, post-connect failure).
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_parser/DefParser.java:89-95
        - H5083 FUN_9b031804 @0x9b031804
        - H6006 send_wifi_status @0x9b03c694
        - H6047 FUN_0100b58c @0x0100b58c
    0x20:
      id: brightness
      -orig-id: brightness_notify
      doc: brightness push, level at data[0] (DefParser.p)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:193
        - base-classes11/com/govee/base2light/kt/general_parser/DefParser.java:200
    0x21:
      id: ic_count
      -orig-id: SINGLE_NOTIFY_IC
      doc: H6062 IC change [ic s16be][segment s8] (CheckIcNumParse)
      doc-ref:
        - split_h6062/com/govee/striplight/ble/BleProtocol.java:13
        - split_h6062/com/govee/striplight/ble/CheckIcNumParse.java:16-22
    0x22:
      id: ic_and_piece
      -orig-id: SINGLE_NOTIFY_IC_AND_PIECE
      doc: |
        H6062 IC-and-piece change: sub_ic_1 = data[0], sub_ic_2 = data[2]
        (IcAndPieceNotifyParse)
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
      doc: take-photo exit (other categories)
    0x30:
      id: switch_zone
      -orig-id: NOTIFY_DETAIL
      doc: |
        the switch / zone push, FAMILY-DEPENDENT (form_ee_30): H60A6 [detail]
        [main bit 0][zone0 bit 0][zone1 bit 0] (VM4LightH60A6.U5); generic
        DefParser.M a flags byte at data[1]. Firmware: H60A6 EE 30 [01][power]
        [zone0][zone1]; H6047 never sends EE 30. Old name device_info_or_zone.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:81
        - base-classes11/com/govee/base2light/kt/general_parser/DefParser.java:81
        - H60A6 FUN_9b0257e0 @0x9b0257e0
    0x40:
      id: device_status
      -orig-id: value_connect_status_notify
      doc: |
        device status: DefParser.v nested selector at data[0]; on H6047 the
        group-slave states push (form_ee_40)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:210
        - base-classes11/com/govee/base2light/kt/general_parser/DefParser.java:219
        - H6047 FUN_01011a04 @0x01011a04
    0xed:
      id: recognition
      doc: outbound 0x33 ED writer and state event only; no inbound decoder
  handshake_op:
    0x01:
      id: v1_request_session_key
      doc: AES V1 step 1 (Controller4Aes); the reply carries the session key
      doc-ref:
        - base-classes13/com/govee/encryp/ble/Controller4Aes.java:104-119
    0x02:
      id: v1_confirm
      doc: AES V1 step 2 confirm (Controller4Aes.h)
      doc-ref:
        - base-classes13/com/govee/encryp/ble/Controller4Aes.java:121-136
    0x11:
      id: v2_single
      doc: AES-GCM single packet (Controller4AesGcm)
    0x19:
      id: v2_request_session_key
      doc: AES-GCM session key (Controller4AesGcm)
      doc-ref:
        - base-classes13/com/govee/encryp/ble/Controller4AesGcm.java:186
    0x1a:
      id: v2_confirm
      doc: AES-GCM confirm (Controller4AesGcm)
      doc-ref:
        - base-classes13/com/govee/encryp/ble/Controller4AesGcm.java:52
  device_info_selector:
    0x02:
      id: sn
      -orig-id: VALUE_DEVICE_INFO_UUID
      doc: |
        8-byte device id (SnController.p {2}); live in the add / pair flows.
        Firmware sends it byte-reversed.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/SnController.java:37-38
    0x03:
      id: hard_version
      -orig-id: VALUE_DEVICE_INFO_HARD_VERSION
      doc: ASCII hardware version (HardVersionController.p {3}; kt default {3})
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/HardVersionController.java:28-29
    0x04:
      id: soft_version
      -orig-id: VALUE_DEVICE_INFO_SOFT_VERSION
      doc: |
        ASCII soft version, the 0x03 layout. Live sender: split_h61d3
        SoftHardController.Companion.e {4}; base2light
        SoftVersionInDeviceInfoController / VersionSoftController have no
        caller. H6047 answers '1.00.12'.
      doc-ref:
        - split_h61d3/com/govee/h61d3/ble/controller/SoftHardController.java:66-68
        - base-classes10/com/govee/base2light/ble/controller/SoftVersionInDeviceInfoController.java:18-19
        - H6047 handler @0x01005370
        - split_h61d3/com/govee/h61d3/ble/controller/PairToDeviceInfoReadController.java:255
        - base-classes10/com/govee/base2light/ble/controller/VersionSoftController.java:18-19
        - H6047 code @0x010053ca
    0x06:
      id: ble_mac
      doc: |
        FIRMWARE-SOURCED (no app sender): the 6-byte BLE MAC at frame bytes
        3..8, byte order per image (govee_common::mac)
      doc-ref:
        - H6006 ble_msg_get_ble_macaddress @0x9b03c988
        - H6008 ble_msg_get_ble_macaddress @0x9b03c7ec
        - H60A1 ble_msg_get_ble_macaddress @0x9b0452b0
        - H60A6 ble_msg_get_ble_macaddress @0x9b04a6d8
        - H5083 ble_msg_get_ble_macaddress @0x9b0319cc
        - H6047 handler @0x01005370
        - H6047 code @0x010053ce
        - H6047 code @0x0101693c
        - H60A1 code @0x9b04dfdc
        - H60A6 code @0x9b053f9c
    0x07:
      id: dsp_version
      -orig-id: VALUE_DEVICE_INFO_DSP_VERSION
      doc: |
        DSP version (DspVersionInDeviceInfoController.p {7}), sent by the
        generic connect step ReadControllerFactory.a, dreamcolorlightv1 BleOpV3
        and the cubelight add flow
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/DspVersionInDeviceInfoController.java:20-21
        - base-classes10/com/govee/base2light/connect/factory/ReadControllerFactory.java:28
        - split_rgbiclight/com/govee/dreamcolorlightv1/adjust/v2/BleOpV3.java:375
        - split_h6061/com/govee/cubelight/add/PairAcV1.java:284
        - split_h60ax/com/govee/pact_h60a0/ble/controller/compose/DspVersionController.java:6
        - split_h60ax/com/govee/pact_h60a0/adjust/v1/ComposeReadAllInfoV1.java:103-111
    0x08:
      id: other_info
      -orig-id: OTHER_INFO
      doc: |
        pact_h60a0 'other info' (compose ControllerOtherInfo {8}), sent only by
        the adjust-v1 read ComposeReadAllInfoV1; no verified image answers it
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/ble/controller/compose/ControllerOtherInfo.java:24-26
        - split_h60ax/com/govee/pact_h60a0/adjust/v1/ComposeReadAllInfoV1.java:76-86
        - H60A1 code @0x9b0454d0
        - H60A6 code @0x9b04a8f8
        - H6047 code @0x01005396
    0x0a:
      id: mcu_soft_version
      -orig-id: SINGLE_DEVICE_INFO_MCU_SOFT_VERSION
      doc: |
        MCU soft version (McuSoftVersionControllerV1.p {10}, only from
        ReadControllerFactory.b). H6008 / H60A1 / H60A6 echo the request with
        no data.
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/McuSoftVersionControllerV1.java:32-33
        - H6008 code @0x9b03c958
        - H60A1 ble_msg_get_sw_mcu_version @0x9b045210
        - H60A6 ble_msg_get_sw_mcu_version @0x9b04a638
    0x0b:
      id: mcu_hard_version
      -orig-id: SINGLE_DEVICE_INFO_MCU_HARD_VERSION
      doc: MCU hard version (McuHardVersionControllerV1.p {11}); echoed, no data
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/McuHardVersionControllerV1.java:32-33
        - H6008 code @0x9b03c966
        - H60A1 ble_msg_get_hw_mcu_version @0x9b045260
        - H60A6 ble_msg_get_hw_mcu_version @0x9b04a688
    0x10:
      id: basic
      doc: |
        the basic-info record [uid 8][sw version3][hw version3][dsp u16le]: the
        0xAC TLV 0x07 record w() decodes, and AA 07 10 on H5086 only
        (DeviceBasicInfoController). No verified firmware answers AA 07 10.
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:405-436
        - split_plugnew/com/govee/h5086/ble/controller/DeviceBasicInfoController.java:39-63
        - split_plugnew/com/govee/h5086/ble/controller/DeviceBasicInfoController.java:112-114
        - split_plugnew/com/govee/h5086/ble/controller/ControllerProtocol.java:17
        - split_plugnew/com/govee/h5086/adjust/SmartPlugProAdjustViewModel.java:878
        - split_plugnew/com/govee/h5086/add/PairViewModel.java:148
        - base-classes10/com/govee/base2light/ble/controller/BasicInfoController.java:34-35
    0x11:
      id: wifi
      doc: |
        the Wi-Fi record [wifi mac 6][sw version3][hw version3]: TLV 0x07 (w())
        and AA 07 11 on H5086 only (WifiInfoController)
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:384-403
        - split_plugnew/com/govee/h5086/ble/controller/WifiInfoController.java:29-42
        - split_plugnew/com/govee/h5086/ble/controller/WifiInfoController.java:66-68
        - base-classes10/com/govee/base2light/ble/controller/BasicWifiInfoController.java:42-43
  color_decode:
    0:
      id: not_decoded
      doc: |
        the screen decodes no colour from this sub-mode (or it is not traced):
        the layout is absent and the bytes stay in the message's rest
    1:
      id: gradual_kelvin_s16be
      doc: |
        [gradual][kelvin s16be]; kelvin > 0 = colour temperature (the colour
        derived from the Kelvin, never read from the report). SubMode4Color's
        default IParseStrategy and screen-local copies; the > 0 test is
        OpInfo4Detail.x or the screen's own parser.
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4Color.java:42-49
        - base-classes9/com/govee/base2home/pact/BleUtil.java:333-335
        - base-classes11/com/govee/base2light/pact/newdetail/OpInfo4Detail.java:771-779
        - split_bulblightstringv1/com/govee/h7022/detail/Info4Detail.java:169-175
        - split_bulblightstringv1/com/govee/bulblightstringv1/detail/Info4Detail.java:153-162
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:399-405
    2:
      id: gradual_kelvin_s16be_nocct
      doc: |
        the same bytes, stored with no CCT test (no colour derived or read)
      doc-ref:
        - split_rgbiclight/com/govee/h6127/newdetail/InfoDetail.java:117-118
        - split_rgbiclight/com/govee/h612526/newdetail/InfoDetail.java:129-131
        - split_hollowlamp/com/govee/hollowlamp/newdetails/h6058/H6058InfoDetail.java:271-273
        - split_hollowlamp/com/govee/hollowlamp/newdetails/h1771/H1771DetailInfo.java:389-391
        - split_h705a/com/govee/h705a/h3401/H3401DetailInfo.java:214-215
    3:
      id: gradual_only
      doc: '[gradual] only; no Kelvin and no colour read'
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/adjust/h60c1/Info4DetailH60C1.java:125-131
        - split_h6047/com/govee/h6047/newdetail/Info4Detail.java:415-417
        - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057Info4Detail.java:127-135
        - split_hollowlamp/com/govee/hollowlamp/newdetails/h1771/H1771DetailInfo.java:425-427
        - split_h705a/com/govee/h705a/h3401/H3401DetailInfo.java:248-250
        - split_rgbiclight/com/govee/rgbiclight/h667172/Info4DetailH667172.java:114-121
    4:
      id: kelvin_s16be
      doc: '[kelvin s16be] with no gradual byte; kelvin > 0 = CCT (bulblightv3)'
      doc-ref:
        - split_bulblightstringv1/com/govee/bulblightv3/newdetail/Info4Detail.java:160-167
        - split_bulblightstringv1/com/govee/bulblightv3/newdetail/NewDetailVm.java:1280-1288
    5:
      id: skip_kelvin_s16be_illumination
      doc: |
        [byte 0, never used][kelvin s16be]: the H60A0 illumination (white)
        light's Kelvin, never a whole-device CCT
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a0/Info4DetailH60A0.java:123-130
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a0/Info4DetailH60A0.java:399-406
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a0/VM4LightH60A0.java:203-211
    6:
      id: gradual_kelvin_pair_s16be
      doc: |
        [gradual][kelvin left s16be][kelvin right s16be] (h6047 old detail
        screen, one Kelvin per bar); the whole-device Kelvin is set only when
        left == right
      doc-ref:
        - split_h6047/com/govee/h6047/detail/Info4Detail.java:173-185
        - split_h6047/com/govee/h6047/detail/Info4Detail.java:848-858
        - split_h6047/com/govee/h6047/detail/NewDetailVm.java:616-652
    7:
      id: skip_kelvin_pair_s16be
      doc: |
        [byte 0, not read][kelvin A s16be][kelvin B s16be] (the new TV-backlight
        layout); the Kelvin is A only when A == B
      doc-ref:
        - split_tvlightv4/com/govee/pact_tvlightv3/detail/Info4Detail.java:176-183
        - split_tvlightv4/com/govee/pact_tvlightv4/detail/Info4Detail.java:176-183
    8:
      id: rgb_kelvin_pair_s16be
      doc: |
        [r1 g1 b1][kelvin1 s16be][r2 g2 b2][kelvin2 s16be] (the old TV-backlight
        layout); per half kelvin != 0 = its tint, the whole-device Kelvin only
        when kelvin1 == kelvin2
      doc-ref:
        - split_tvlightv4/com/govee/pact_tvlightv3/detail/Info4Detail.java:185-202
        - split_tvlightv4/com/govee/pact_tvlightv4/detail/Info4Detail.java:185-202
    9:
      id: gradual_kelvin_triple_s16be
      doc: |
        [gradual][kelvin A][kelvin B][kelvin C] s16be (pact_h605b goodsType 82);
        the whole-device Kelvin is never set from the report; zones repainted
        after the last group read
      doc-ref:
        - split_h6099/com/govee/pact_h605b/newdetail/Info4Detail.java:179-185
        - split_h6099/com/govee/pact_h605b/newdetail/viewmodel/NewDetailVm.java:1223-1246
    10:
      id: gradual_kelvin_triple_s16be_nocct
      doc: the same three Kelvins, which nothing consumes (goodsType 172)
      doc-ref:
        - split_h6099/com/govee/pact_h605b/newdetail/Info4Detail.java:179-185
        - split_h6099/com/govee/pact_h605b/newdetail/viewmodel/NewDetailVm.java:1224
    11:
      id: rgb_kelvin_tint
      doc: |
        [r g b][kelvin s16be][tint r g b] (ModeParseStrategyConfig.a, the 0x0d
        bulbs H6006 / H6008 and the rgblight H6188, run by OpInfo4Detail.x):
        kelvin > 0 = CCT; kelvin == 0 = the RGB; kelvin < 0 = the tint bytes as
        an RGB colour
      doc-ref:
        - split_rgblight/com/govee/rgblight/mode/ModeParseStrategyConfig.java:35-46
        - split_bulblightstringv1/com/govee/bulblightstringv1/bulblight/mode/ModeParseStrategyConfig.java:55-62
        - split_rgbiclight/com/govee/rgbiclight/h6160/newdetail/Vm4Light.java:578-588
        - split_rgbiclight/com/govee/rgbiclight/h6160/newdetail/InfoDetail.java:258-260
    12:
      id: rgb_kelvin_tint_positive
      doc: |
        the same layout with the tint read only when kelvin > 0; kelvin <= 0 =
        the RGB (H60501InfoDetail)
      doc-ref:
        - split_hollowlamp/com/govee/hollowlamp/newdetails/h60501/H60501InfoDetail.java:121-148
        - split_hollowlamp/com/govee/hollowlamp/newdetails/h60501/H605051NewDetailVm.java:772-786
    13:
      id: rgb_kelvin_0d
      doc: |
        [r g b][kelvin s16be], 5 bytes; kelvin > 0 = CCT with the colour
        derived from the Kelvin, else the RGB (h60a1 Info4Detail.Q b == 13;
        cube CubeNewInfo4Detail; homelightv1; H6055)
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:416-426
        - split_h6061/com/govee/cubelight/newdetails/CubeNewInfo4Detail.java:142-153
        - split_rgblight/com/govee/homelightv1/newdetail/Info4Detail.java:113-125
        - split_hollowlamp/com/govee/hollowlamp/newdetails/h6055/H6055InfoDetail.java:112-121
    14:
      id: rgb_kelvin_0d_nocct
      doc: the same 5 bytes; always the RGB, the Kelvin never tested (h70bx)
      doc-ref:
        - split_h70b1/com/govee/h70bx/Info4Detail.java:125-133
    15:
      id: rgb
      doc: |
        [r g b] only, never CCT (H6052InfoDetail; rgblight
        ModeParseStrategyConfig.c)
      doc-ref:
        - split_hollowlamp/com/govee/tablelampv1/newAdjust/h6052/H6052InfoDetail.java:113-121
        - split_rgblight/com/govee/rgblight/mode/ModeParseStrategyConfig.java:67-77
    16:
      id: rgb_flag1_tint
      doc: |
        [r g b][flag][tint r g b], the tint read only when flag == 1; flag ==
        1 = CCT at the tint's table Kelvin (rgblight
        ModeParseStrategyConfig.b; rgbiclight/h6160)
      doc-ref:
        - split_rgblight/com/govee/rgblight/mode/ModeParseStrategyConfig.java:52-62
        - split_rgbiclight/com/govee/rgbiclight/h6160/newdetail/Vm4Light.java:562-575
    17:
      id: rgb_flagnz_tint
      doc: |
        [r g b][flag][tint r g b]; flag != 0 = CCT at the tint's table Kelvin
        within [2000, 9000] (h6104)
      doc-ref:
        - split_rgblight/com/govee/h6104/newdetail/Info4Detail.java:165-176
        - base-classes10/com/govee/base2kt/utils/ColorUtils.java:255-258
    18:
      id: rgb_flag_tint_nokelvin
      doc: |
        [r g b][flag][tint r g b]; flag != 0 = colour-temperature state shown
        as the tint, no Kelvin number (legacy AbsBleAdjustAcV1 screens)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/AbsMode.java:17-23
        - split_rgblight/com/govee/h6101/ble/SubModeColor.java:81-85
        - split_rgbiclight/com/govee/h6102/ble/SubModeColor.java:82-86
        - split_rgblight/com/govee/h613839/ble/SubModeColor.java:89-93
    19:
      id: rgb_pair_flag_lookup
      doc: |
        [colour A][flag][colour B]; colour = flag != 0 ? B : A, its Kelvin a
        table lookup (KelvinHelp, PROCEDURAL) (bulblight old-H6001)
      doc-ref:
        - split_bulblightstringv1/com/govee/bulblightstringv1/bulblight/mode/ModeParseStrategyConfig.java:26-53
        - base-classes10/com/govee/base2light/connect/constant/KelvinHelp.java:52-57
    20:
      id: bri_rgb_pair
      doc: |
        [bri1 s8][r1 g1 b1][bri2 s8][r2 g2 b2], one record per lamp head
        (H70606162Info4Detail goodsType 98); never a CCT on the heads
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
        SubModeMusicV1.parse (dreamcolorlightv1): [music_code][value][dynamic]
        [spec_color_flag] read unconditionally, then [r][g][b] iff the flag is
        not 0
      doc-ref:
        - split_rgbiclight/com/govee/dreamcolorlightv1/ble/SubModeMusicV1.java:87-98
    2:
      id: v2_default
      doc: |
        SubMode4MusicV2's default strategy as h60a1 Info4Detail builds it
        (SubMode4MusicV2(0, H705EDF list, 1, null): extra-byte code 4,
        new-music codes {49, 71, 72, 81, 113}); the H60A6 / H60A1 / H60A4 /
        H12D0 screens (F4)
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4MusicV2.java:49-73
        - base-classes11/com/govee/base2light/pact/newdetail/sub/SubMode4MusicV2.java:76-78
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/Info4Detail.java:122-125
        - base-classes10/com/govee/base2light/ble/music/AbsNewMusicEffect.java:114-116
  group_count_rule:
    0:
      id: unknown
      doc: no colour-group decode for this device (body raw)
    1:
      id: from_len
      doc: |
        records = (len - 1) / record size, the screens that count from len
        (H60B1ViewModel, VM4LightH60C1, H6850DetailVM)
      doc-ref:
        - split_floorlamp/com/govee/h60b1/detail/H60B1ViewModel.java:159-160
        - split_h60ax/com/govee/pact_h60a0/adjust/h60c1/VM4LightH60C1$afterConnectedSingleReadDeviceInfo$1.java:112-113
        - split_h6840/com/govee/h6609/h6850/H6850DetailVM$afterConnectedSingleReadDeviceInfo$1.java:87-88
    2:
      id: fixed
      doc: |
        records = num_per_group for every group (VM4H6038, VM4H6048, the h6047
        newdetail VM4Light, the rgbiclight VM4Light, the h60a1 VM4Light), and
        every direct aa a5 / aa a2 reply (the caller's group size)
      doc-ref:
        - split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:574
        - split_h6047/com/govee/h6047/newdetail/h6048/VM4H6048.java:392
        - split_h6047/com/govee/h6047/newdetail/vm/VM4Light.java:304
        - split_rgbiclight/com/govee/rgbiclight/VM4Light.java:711
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a1/VM4Light$afterConnectedSingleReadDeviceInfo$1.java:88
    3:
      id: by_pieces
      doc: |
        the H60A6 rule (VM4LightH60A6 status lambda): num_per_group records
        except the last of ceil(num_pieces / num_per_group) groups, which holds
        the remainder
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:105-121
  form_01:
    0:
      id: unknown
      doc: not known for this device; the 33 01 body stays raw
    1:
      id: light
      doc: |
        a light's 1 / 0 (kt ControllerSwitch [z ? 1 : 0]; legacy
        SwitchController.q)
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerSwitch.java:60-62
        - base-classes10/com/govee/base2light/ble/controller/SwitchController.java:37-39
    2:
      id: relays
      doc: |
        the h5080 plug relay byte [outlet-select nibble << 4 | on bits]
        (SwitchControllerV2.q)
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:53-71
  form_02:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: total_switch
      doc: H5089 total switch [state] (Controller4TotalSwitch.q)
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4TotalSwitch.java:32-34
    2:
      id: chart_prepare
      doc: H5086 chart prepare, no payload (ChartPrepareInfoController)
      doc-ref:
        - split_plugnew/com/govee/h5086/ble/chartcontroller/ChartPrepareInfoController.java:51-52
  form_09:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: datetime_epoch
      doc: |
        TimerConfig version 0 (every screen not listed below):
        ControllerSyncTime.a, 15 bytes [hour][min][sec][week][01][tz_h][tz_m]
        [day][month][year u16le][epoch u32le]
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTime.java:31-35
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:463-479
    2:
      id: epoch_be
      doc: |
        version 1 (goodsTypes 235 / 302 / 303 / 64 / 356 / 373 / 342 / 348 /
        408): ControllerSyncTimeStamp.a, [epoch u32be][01][tz_h][tz_m]; H60A6
        firmware reads exactly this (Govee_TimerService_UpdateTimeByBLE)
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTimeStamp.java:29-33
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:465-467
        - H60A6 FUN_9b02ad30 @0x9b02ad30
    3:
      id: clock
      doc: |
        version 2 (H6160 / H6104 / H7023 / H7318 / H7004 / H7017, goodsType 12
        / 101, some goodsType-0 SKUs): ControllerSyncTime.b, [hour][min][sec]
        [week][01][tz_h][tz_m]
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTime.java:37-41
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:468-478
  form_12:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: wake
      doc: the wake-up timer (layout per TimerConfig version; untyped)
    2:
      id: timer_count
      doc: H5083 timer count (AA 12 -> [group][count])
      doc-ref:
        - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:220
  form_13:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: night_mode
      doc: night mode (untyped)
    2:
      id: plug_timer
      doc: H5083 plug timer (33 13 add; AA 13 page)
      doc-ref:
        - split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:234-246
  form_14:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: gradual
      doc: the gradual switch (GradualVersion 1 -> 0x14, Config M table)
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5602-5613
    2:
      id: wifi_mac
      doc: the Wi-Fi MAC read (AA 14)
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
        the single-byte value (H6038 calibration direction; H6057 main light;
        H71xx chart request)
      doc-ref:
        - split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:42-45
        - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:950-956
  form_36:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: bar_pair
      doc: H6047 [left][right] bar switch (Controller4ExtBytes [54, l, r])
      doc-ref:
        - split_h6047/com/govee/h6047/detail/NewDetailVm.java:698
    2:
      id: bar_index
      doc: '[index][state] (H6043 / H6038 / H6048 / H60B2 / H1771 / H60A0)'
      doc-ref:
        - split_h6047/com/govee/h6047/detail/NewDetailVm.java:696
    3:
      id: worry_free
      doc: H6057 worry-free-at-night, 7 bytes (WorryFreeAtNightBean.o)
      doc-ref:
        - base-classes10/com/govee/base2light/ble/controller/WorryFreeAtNightBean.java:90-92
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
      doc: H61A8 mechanism-B V1 batch [batch][4 x rgb] (BulbGroupColor)
      doc-ref:
        - split_rgbiclight/com/govee/dreamcolorlightv1/ble/BulbGroupColor.java:11-25
    2:
      id: color_group_rgb
      doc: |
        a Controller4ColorInfoByGroup colour-only group [group][rgb x n]
        (Companion.p)
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:145-158
  form_a5:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: color_group
      doc: |
        a Controller4ColorInfoByGroup group [group][bri_rgb x n] (Companion.q;
        H6047, H6641, H6062, H6061, H60A6)
      doc-ref:
        - base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:160-175
    2:
      id: bulb_batch_v2
      doc: H61A8 mechanism-B V2 batch [batch][3 x bri_rgb] (BulbGroupColorV2)
      doc-ref:
        - split_rgbiclight/com/govee/dreamcolorlightv1/ble/BulbGroupColorV2.java:16-33
  form_b0:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: plug_delay
      doc: the h5080 plug delay (TurnOnOffDelayController)
      doc-ref:
        - split_plugnew/com/govee/h5080/ble/controller/TurnOnOffDelayController.java:90-92
  form_0b:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: rgb_mask
      doc: |
        [rgb][seg_mask(num_segments, 13)] (Bytes.d, ColorInstructionVersion
        COLOR_MODE_0X0B(3))
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:270-281
        - base-classes12/com/govee/common2light/capability/color/ColorInstructionVersion.java:16
    2:
      id: rgb_kelvin
      doc: |
        [rgb][kelvin u2be][positions 0..14 as bits] (Bytes.c,
        COLOR_MODE_0X0B_KEVIN(6))
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
      doc: '[rgb][seg_mask cap 12] (SubModeColorV1 opType 1)'
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:684-694
    2:
      id: kelvin_tint
      doc: |
        [rgb][kelvin u2be][tint rgb][seg_mask cap 7] (Bytes.r, SubModeColorV1
        opType 11); RGB is kelvin 0 and tint 0, byte-identical to opType 12
        [rgb][00 x5][mask] (Bytes.h). H61A8 (goodsType 19) writes this form
        (Config.J[0] = Yt, F1).
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:474-501
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:315-326
        - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:720-744
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5522-5526
    3:
      id: kelvin
      doc: |
        colour-instruction version 5 (COLOR_MODE_0X15_NO_TEMP_RGB): [rgb or
        tint][kelvin u2be][seg_mask cap 10] (Bytes.l / Bytes.u)
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:379-390
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:541-556
        - base-classes12/com/govee/common2light/capability/color/ColorInstructionVersion.java:18
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:13124-13131
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5527-5531
  form_15_04:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: rgb_group
      doc: '[group][rgb x n] (Bytes.m; SubModeColorV1 opType 7)'
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:393-402
    2:
      id: kelvin_v9
      doc: '[kelvin u2be] (Bytes.D, colour-instruction version 9)'
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:133-139
  form_15_05:
    0:
      id: unknown
      doc: not known; body raw
    1:
      id: kelvin
      doc: |
        [kelvin u2be] (SubModeColorV1 opType 2; the H60A0 screen's
        ControllerBuildH60A0.b / .d)
      doc-ref:
        - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:695-698
        - split_h60ax/com/govee/pact_h60a0/adjust/h60a0/ControllerBuildH60A0.java:62-66,93-98
    2:
      id: side_brightness
      doc: |
        [side][value] (Bytes.H, relative-brightness version 1 =
        RELATIVE_BRIGHTNESS_H610A, goodsType 80)
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:184-199
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:2817-2821
    3:
      id: gradient
      doc: |
        [rgb x 3][seg_mask(num_segments, 6)] (Bytes.n, GradientColor version
        0; keyed for goodsTypes 326 / 338 / 354 / 364, reach not traced)
      doc-ref:
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:404-420
        - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/GradientColorControllerConfig.java:65-92
  form_ee_30:
    0:
      id: unknown
      doc: |
        not known: notify_30 still reads its four bytes (one layout for every
        device) and is_on / zone_a_on / zone_b_on are false
    1:
      id: main_bit0_a
      doc: |
        power = bit 0 of flags_a (frame[3]): H60A1 / H60A4 / H60A6, H6039,
        H6078, H601E / H601F; zones in flags_b / flags_c bit 0 on H60A6
    2:
      id: bit0_c
      doc: |
        power = bit 0 of flags_c (frame[5]): H60A0
    3:
      id: bars_bc
      doc: per bar flags_b == 1 / flags_c == 1 (H6038 / H6048)
    4:
      id: generic_flags
      doc: DefParser.M, a flags byte at data[1] with sub-switches in bits 1-4
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
