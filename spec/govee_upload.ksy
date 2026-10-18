meta:
  id: govee_upload
  title: Govee multi-packet upload value or decoded cloud scene blob
  license: MIT
  ks-version: '0.11'
  imports:
    - govee_common
  bit-endian: le
  endian: be
doc: |
  STANDALONE (spans frames, or off-BLE): one joined A1 / A3 / A4 upload value
  or one decoded cloud scenceParam, typed by the caller's kind and form.
doc-ref:
  - base-classes10/com/govee/base2light/ble/ScenesOp.java:377-450, 541-578
  - base-classes11/com/govee/base2light/pact/newdetail/content/scene/BaseSceneViewMode.java:1152-1209
  - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:833-859
params:
  - id: kind
    type: u1
    enum: upload_kind
    doc: which value the bytes are (not in the bytes)
  - id: form
    type: u1
    enum: upload_form
    doc: the byte source
seq:
  - id: lead
    type:
      switch-on: kind
      cases:
        'upload_kind::scenes_graffiti': lead_scenes_graffiti
        'upload_kind::compose': lead_compose
        'upload_kind::h60a6_diy': lead_h60a6
        'upload_kind::h60a6_graffiti': lead_h60a6
        'upload_kind::h6052_graffiti': lead_h6052
        'upload_kind::h6052_params_v5': lead_h6052
    if: form == upload_form::cloud
    doc: the lead the app checks and strips; absent when the blob is the value
  - id: value
    size-eos: true
    type:
      switch-on: value_kind
      cases:
        'upload_kind::rgb_scene': rgb_scene_value
        'upload_kind::rgbic_scene': rgbic_scene_value
        'upload_kind::scenes_graffiti': scenes_graffiti_value
        'upload_kind::scenes_graffiti_gate': scenes_graffiti_gate
        'upload_kind::h60a6_diy': diy_value
        'upload_kind::h60a6_graffiti': graffiti_value
        'upload_kind::h6052_graffiti': graffiti_v3_value
        'upload_kind::h6052_params_v5': h6052_params_v5
        'upload_kind::graffiti_v2': graffiti_v2_value
        'upload_kind::graffiti_v2_wide': graffiti_v2_wide_value
        'upload_kind::color_strip': color_strip_value
        'upload_kind::bulb_diy': bulb_diy_value
        'upload_kind::wifi_credentials': wifi_credentials_value
        'upload_kind::iot_config': iot_config_value
    doc: the wire value or the cloud body after the lead; bytes when untyped
instances:
  value_kind:
    value: >-
      (form == upload_form::wire
      or kind == upload_kind::rgb_scene
      or kind == upload_kind::rgbic_scene
      or kind == upload_kind::scenes_graffiti_gate
      or kind == upload_kind::h6052_params_v5)
      ? kind : upload_kind::unknown
    doc: kind, or unknown for a cloud body that only form wire types
types:
  lead_scenes_graffiti:
    doc: |
      The 2-byte lead of a sceneType 3 (graffiti) cloud blob, which the app
      strips before it sends the rest on 0xA3 commByte 0x07.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/ScenesOp.java:411-429
      - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV3.java:10-11
    seq:
      - id: marker
        contents: [0x01]
        doc-ref:
          - base-classes10/com/govee/base2light/ac/diy/local/ShareDiy.java:99
          - base-classes10/com/govee/base2light/ac/diy/v2/DiyProtocolParser.java:1402
      - id: effect_hi
        type: u1
        doc: first of the effect-code pair; the value's effect_lo is second
        doc-ref:
          - base-classes10/com/govee/base2light/ac/diy/v2/DiyProtocolParser.java:1405
  lead_compose:
    doc: |
      VALIDATOR (read-side): the 0x0a lead of a sceneType 6 (compose) cloud
      blob, the app's whole check before its 0xA3 commByte 0x0a upload.
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/local/ShareDiy.java:81-83
      - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV5.java:5-7
      - base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerV14Scenes.java:29-38
    seq:
      - id: marker
        contents: [0x0a]
  lead_h60a6:
    doc: |
      The 0x50 lead of an H60A6 sceneType 5 cloud blob; the app tries the DIY
      parse, then the graffiti parse, and sends on commByte 0x58.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/content/scene/BaseSceneViewMode.java:1163, 1192-1203
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6DiyConfig.java:36-37
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6DiyParse.java:543-560
    seq:
      - id: marker
        contents: [0x50]
        doc-ref:
          - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/Pro4H60A6Diy.java:32
          - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6GraffitiParse.java:47
  lead_h6052:
    doc: |
      The 0x13 lead of an H6052 sceneType 5 professional-graffiti cloud blob
      (the app's 0x00 and 0x01 lead forms are not modelled).
    doc-ref:
      - split_hollowlamp/com/govee/tablelampv1/newAdjust/h6052/H6052ScenesViewMode.java:42-83
      - split_hollowlamp/com/govee/tablelampv1/adjust/Diy.java:95-109
      - base-classes10/com/govee/base2light/ac/diy/local/ShareDiy.java:153-159
    seq:
      - id: marker
        contents: [0x13]
        doc-ref:
          - base-classes10/com/govee/base2light/ac/diy/v2/DiyProtocolParser.java:1618
  rgb_scene_value:
    doc: |
      VALIDATOR (read-side): a sceneType 1 (rgb) scene blob, sent verbatim on
      0xA3 commByte 0x01 only when is_valid.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/ScenesOp.java:377-392
      - base-classes10/com/govee/base2light/ble/ScenesRgb.java:1642-1656
      - firmware-h6006/decompiled/H6006_a3_reassembler.c:70-74
    seq:
      - id: color_count
        type: b3
      - id: reserved
        type: b1
      - id: mode
        type: b3
        doc: 1 or 0 picks the body shape; 2..7 are never valid
      - id: follow_sys_brightness
        type: b1
        doc: not part of the upload check
        doc-ref:
          - base-classes10/com/govee/base2light/ble/ScenesRgb.java:1637-1640
      - id: body
        size-eos: true
        doc: its shape follows mode (see is_valid); records not decoded
    instances:
      effect_count:
        value: 'body.length >= 1 ? body[0] : 0'
        doc: byte 1; 0 for a 1-byte blob
      color_num:
        value: >-
          body.length > effect_count * 5 + 1
          ? body[effect_count * 5 + 1] : 0
        doc: mode 0, byte effect_count x 5 + 2; 0 when past the end
      is_valid:
        value: >-
          body.length >= 1
          and ((mode == 1
          and effect_count * (color_count + 5) + 2 == body.length + 1)
          or (mode == 0 and body.length > effect_count * 5 + 1
          and effect_count * 5 + 3 + color_count * color_num
          == body.length + 1))
        doc: the app's check, exactly; false for a blob too short to read
        doc-ref:
          - base-classes10/com/govee/base2light/ble/ScenesRgb.java:1676-1701
  rgbic_scene_value:
    doc: |
      VALIDATOR (read-side): a sceneType 2 (rgbic) scene blob, sent verbatim on
      0xA3 commByte 0x02; a successful read is the app's check passing.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/ScenesOp.java:394-409
      - base-classes10/com/govee/base2light/ble/ScenesRgbIC.java:2783-2807
      - base-classes10/com/govee/base2light/ble/ScenesRgbIC.java:2655-2676
    seq:
      - id: num_effects
        type: u1
      - id: effects
        type: rgbic_effect
        repeat: expr
        repeat-expr: num_effects
      - id: rest
        type: govee_common::echo
        doc: not read by the app
  rgbic_effect:
    seq:
      - id: len_record
        type: u1
      - id: record
        size: len_record
        type: rgbic_effect_record
  rgbic_effect_record:
    doc: |
      One rgbic effect: style, mode, brightness, colour and movement blocks.
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/v2/ParamsV2.java:767-822
      - base-classes10/com/govee/base2light/ac/diy/v2/ParamsV2.java:531
    seq:
      - id: style
        type: u1
        doc: packed nibbles, speed / order
      - id: mode
        type: u1
        doc: selects mode_param's meaning; 0..3, not checked by the gate
      - id: mode_param
        type: s2be
        doc: modes 0 / 1 signed; modes 2 / 3 two unsigned bytes, high first
      - id: bright_algo
        type: u1
        doc: packed high / low nibbles
      - id: num_brightness_effects
        type: u1
      - id: brightness_effects
        type: brightness_effect
        repeat: expr
        repeat-expr: num_brightness_effects
      - id: color_ic
        type: u1
        doc: packed colour order and colour type
      - id: speed
        type: u1
      - id: duration
        type: u1
      - id: num_colors
        type: u1
      - id: colors
        type: govee_common::rgb
        repeat: expr
        repeat-expr: num_colors
      - id: in_area_move
        type: in_area_move
      - id: area_move
        type: area_move
      - id: slack
        size-eos: true
        doc: not read by the app
  brightness_effect:
    doc: One brightness effect, six bytes the app stores with no meaning.
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/v2/ParamsV2.java:180-215
    seq:
      - id: f_a
        type: u1
      - id: f_b
        type: u1
      - id: f_c
        type: u1
      - id: f_d
        type: u1
      - id: f_e
        type: u1
      - id: f_f
        type: u1
  in_area_move:
    doc: |
      In-area movement block; move_flags packs can-move (bit 4) and order.
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/v2/ParamsV2.java:392-396
    seq:
      - id: move_flags
        type: u1
      - id: p_c
        type: u1
      - id: p_d
        type: u1
  area_move:
    doc: |
      Area movement block; move_flags packs can-move (bit 4) and order.
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/v2/ParamsV2.java:163-167
    seq:
      - id: move_flags
        type: u1
      - id: p_c
        type: u1
      - id: p_d
        type: u1
      - id: p_e
        type: u1
  scenes_graffiti_value:
    doc: |
      The sceneType 3 (graffiti) value after its lead, sent on 0xA3 commByte
      0x07; stricter than the app's gate (scenes_graffiti_gate).
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/v2/DiyProtocolParser.java:1391-1433
      - base-classes10/com/govee/base2light/ble/ScenesOp.java:422-425
    seq:
      - id: effect_lo
        type: u1
        doc: second of the effect-code pair (the lead's effect_hi is first)
      - id: speed
        type: u1
      - id: brightness
        type: u1
      - id: background
        type: govee_common::rgb
      - id: num_groups
        type: u1
      - id: groups
        type: pixel_group
        repeat: expr
        repeat-expr: num_groups
      - id: rest
        type: govee_common::echo
  pixel_group:
    doc: One colour of a graffiti pixel map, one byte per pixel index.
    seq:
      - id: num_pixels
        type: u1
      - id: color
        type: govee_common::rgb
      - id: pixels
        type: u1
        repeat: expr
        repeat-expr: num_pixels
        doc: LED / pixel indices of this colour
  scenes_graffiti_gate:
    doc: |
      VALIDATOR (read-side): the app's sceneType 3 check on the whole blob,
      lead included; the read never fails and is_valid is the answer.
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/local/ShareDiy.java:96-115
    seq:
      - id: blob
        size-eos: true
      - id: heads
        type: 'gate_step(_index == 0 ? 9 : heads[_index - 1].next_at, blob)'
        repeat: expr
        repeat-expr: num_heads
        doc: one zero-width step per group header the app's walk reads
    instances:
      num_heads:
        value: 'blob.length >= 9 ? blob[8] : 0'
        doc: byte 8; 0 for a blob shorter than 9
      is_valid:
        value: >-
          blob.length >= 9 and blob[0] == 1
          and (num_heads == 0 or heads[num_heads - 1].in_range)
        doc: the app's answer; the last index in range implies all are
  gate_step:
    doc: One zero-width step of the app's group-header walk.
    params:
      - id: at
        type: u4
        doc: index of this group's header byte (its pixel count)
      - id: blob
        type: bytes
    instances:
      in_range:
        value: at < blob.length
      next_at:
        value: 'in_range ? at + blob[at] + 4 : at'
        doc: past the header, RGB and pixel bytes; stays at `at` past the end
  diy_value:
    doc: |
      VALIDATOR (read-side): an H60A6 DIY value on 0xA3 commByte 0x58; the
      app's parse passes exactly when it reads and every layer effect is_valid.
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/Pro4H60A6Diy.java:65-131, 171-221
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6DiyParse.java:471-477, 827-835
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6DiyConfig.java:31
    seq:
      - id: len_body
        type: u2le
        valid:
          expr: _ + 2 == _io.size
      - id: num_layers
        type: u1
      - id: num_bg_colors
        type: u1
      - id: bg_colors
        type: govee_common::rgb
        repeat: expr
        repeat-expr: num_bg_colors
      - id: bg_brightness
        type: u1
      - id: layers
        type: diy_layer
        repeat: expr
        repeat-expr: num_layers
      - id: rest
        type: govee_common::echo
        doc: not checked by the app
  diy_layer:
    seq:
      - id: len_effect
        type: u2le
      - id: effect
        size: len_effect
        type: diy_sub_effect
  diy_sub_effect:
    doc: One H60A6 DIY layer body, a sub-effect id and its params.
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/protocol/Layer.java:89-149
    seq:
      - id: sub_effect_id
        type: u1
      - id: effect
        type:
          switch-on: sub_effect_id
          cases:
            1: diy_liudong_area
            2: diy_line
            3: diy_kuosan
            4: diy_xuanzhuan
            5: random_breath
            6: diy_suijiliuxing
            7: random_breath
            8: diy_palette2
            9: diy_palette2
            10: diy_palette2
      - id: slack
        size-eos: true
        doc: not read by the app
    instances:
      is_valid:
        value: sub_effect_id >= 1 and sub_effect_id <= 10
        doc: the app parses ids 1..10 only; another id fails the DIY parse
        doc-ref:
          - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/protocol/Layer.java:103-147
          - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/Pro4H60A6Diy.java:120-121
  palette:
    doc: a count-prefixed RGB palette
    seq:
      - id: num_colors
        type: u1
      - id: colors
        type: govee_common::rgb
        repeat: expr
        repeat-expr: num_colors
  diy_liudong_area:
    doc: sub-effect 1, LiuDong4Area (flow, zone)
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/protocol/LiuDong4Area.java:1-205
    seq:
      - id: i
        type: u1
      - id: t0
        type: u1
      - id: t1
        type: u1
      - id: x
        type: u1
      - id: w
        type: u1
      - id: h
        type: u1
      - id: r0
        type: u1
      - id: r1
        type: u1
      - id: q0
        type: u1
      - id: q1
        type: u1
      - id: m0
        type: u1
      - id: m1
        type: u1
      - id: u
        type: u1
      - id: v
        type: u1
        doc: a boolean
      - id: palette
        type: palette
  diy_line:
    doc: sub-effect 2, LiuDong4Line (flow, line)
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/protocol/LiuDong4Line.java:1-181
    seq:
      - id: speed
        type: u1
      - id: direction
        type: u1
      - id: on_flag
        type: u1
        doc: a boolean
      - id: num_segs
        type: u1
      - id: segs
        type: u1
        repeat: expr
        repeat-expr: num_segs
        doc: segment indices
      - id: l0
        type: u1
      - id: l1
        type: u1
      - id: r
        type: u1
      - id: t
        type: u1
        doc: a boolean
      - id: s
        type: u1
        doc: a boolean
      - id: palette
        type: palette
  diy_kuosan:
    doc: sub-effect 3, KuoSan (diffuse)
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/protocol/KuoSan.java:1-146
    seq:
      - id: i
        type: u1
      - id: t
        type: u1
      - id: s
        type: u1
      - id: direction
        type: u1
      - id: l0
        type: u1
      - id: l1
        type: u1
      - id: q
        type: u1
      - id: r
        type: u1
        doc: a boolean
      - id: palette
        type: palette
  diy_xuanzhuan:
    doc: sub-effect 4, XuanZhuan (rotate)
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/protocol/XuanZhuan.java:1-115
    seq:
      - id: i
        type: u1
      - id: direction
        type: u1
      - id: angle
        type: u2le
      - id: p
        type: u1
        doc: a boolean
      - id: palette
        type: palette
  random_breath:
    doc: |
      sub-effects 5 SuiJiHuXi (random breathing) and 7 SuiJiJianBian (random
      gradient), which share this shape
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/protocol/SuiJiHuXi.java:39-56
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/protocol/SuiJiJianBian.java:40-58
    seq:
      - id: i
        type: u1
      - id: e
        type: u1
        doc: SuiJiJianBian keeps this byte as e[0]
      - id: f
        type: u1
        doc: a boolean for sub-effect 5; SuiJiJianBian keeps this byte as e[1]
      - id: palette
        type: palette
  diy_suijiliuxing:
    doc: sub-effect 6, SuiJiLiuXing (random meteor)
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/protocol/SuiJiLiuXing.java:1-131
    seq:
      - id: i
        type: u1
      - id: direction
        type: u1
      - id: r
        type: u1
      - id: s
        type: u1
      - id: q
        type: u1
      - id: p
        type: u1
        doc: a boolean
      - id: palette
        type: palette
  diy_palette2:
    doc: sub-effects 8 Fade, 9 HuXi (breathing) and 10 ShanShuo (blink)
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/protocol/Fade.java:38-53
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/protocol/HuXi.java:1-83
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/protocol/ShanShuo.java:1-83
    seq:
      - id: i
        type: u1
      - id: m
        type: u1
      - id: palette
        type: palette
  graffiti_value:
    doc: |
      An H60A6 graffiti value on 0xA4 (MTU) commByte 0x58; stricter than the
      app's parse, which sends its own re-encoding instead.
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6GraffitiParse.java:293-309, 101-147, 240, 312-325
      - base-classes12/com/govee/base2light/widget/graffiti/present/LayerDataH60A6.java:158-197
    seq:
      - id: marker
        contents: [0x20]
      - id: background
        type: govee_common::rgb
      - id: brightness
        type: u1
      - id: show_type
        type: u1
      - id: num_layers
        type: u1
      - id: layers
        type: graffiti_layer
        repeat: expr
        repeat-expr: num_layers
      - id: rest
        type: govee_common::echo
  graffiti_layer:
    seq:
      - id: len_body
        type: u2le
      - id: body
        size: len_body
        type: graffiti_layer_body
  graffiti_layer_body:
    seq:
      - id: graffiti_type
        type: u1
        doc: 0x03; the app's walk stops at a layer of another type
      - id: len_inner
        type: u2le
      - id: num_groups
        type: u2le
      - id: groups
        type: h60a6_pixel_group
        repeat: expr
        repeat-expr: num_groups
      - id: action
        type: u1
      - id: speed
        type: u1
      - id: bg_brightness
        type: u1
      - id: priority
        type: u1
      - id: duration
        type: u2le
      - id: reserved
        contents: [0, 0, 0, 0]
      - id: slack
        size-eos: true
  h60a6_pixel_group:
    doc: One colour of the H60A6 graffiti pixel map, one byte per pixel index.
    seq:
      - id: num_pixels
        type: u2le
      - id: color
        type: govee_common::rgb
      - id: pixels
        type: u1
        repeat: expr
        repeat-expr: num_pixels
        doc: LED / pixel indices of this colour
  graffiti_v3_value:
    doc: |
      The H6052 graffiti-v3 value on 0xA3 commByte 0x09; stricter than the
      app's parse (h6052_params_v5), which reads at most three layers.
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/DiyGraffitiV3.java:117-147
      - base-classes10/com/govee/base2light/ac/diy/v2/DiyProtocolParser.java:198
      - base-classes10/com/govee/base2light/ble/MultipleDiyInScenesController.java:16-18
    seq:
      - id: brightness
        type: u1
      - id: base
        type: govee_common::rgb
      - id: num_layers
        type: u1
      - id: layers
        type: gv3_layer
        repeat: expr
        repeat-expr: num_layers
      - id: rest
        type: govee_common::echo
  gv3_layer:
    seq:
      - id: len_body
        type: u2le
      - id: body
        size: len_body
        type: gv3_layer_body
  gv3_layer_body:
    doc: One graffiti-v3 layer; a read past its end fails the app's parse.
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/v2/DiyProtocolParser.java:179-203
    seq:
      - id: speed
        type: u1
      - id: action
        type: u1
      - id: priority
        type: u1
      - id: num_groups
        type: u1
      - id: groups
        type: pixel_group
        repeat: expr
        repeat-expr: num_groups
      - id: slack
        size-eos: true
        doc: not read by the app
  h6052_params_v5:
    doc: |
      VALIDATOR (read-side): the app's parse of the H6052 cloud body after its
      0x13 lead (form cloud only); a failed read means no upload is built.
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/v2/DiyProtocolParser.java:1604-1663
      - base-classes10/com/govee/base2light/ble/BleUtil.java:333-335
      - split_hollowlamp/com/govee/tablelampv1/newAdjust/h6052/H6052ScenesViewMode.java:63-75
    seq:
      - id: brightness
        type: u1
      - id: base
        type: govee_common::rgb
      - id: num_layers
        type: u1
        doc: ignored by the app's parse
      - id: first
        type: h6052_params_v5_layer(len_blob)
      - id: second
        type: h6052_params_v5_layer(len_blob)
        if: first.has_body and len_blob >= first.len_body + 10
        doc: only when the first had a body and 2 bytes follow it
      - id: third
        type: h6052_params_v5_layer(len_blob)
        if: >-
          first.has_body and len_blob >= first.len_body + 10
          and second.has_body
          and len_blob >= first.len_body + second.len_body + 12
        doc: only when the second had a body and 2 bytes follow it
      - id: rest
        type: govee_common::echo
        doc: not checked by the app
    instances:
      len_blob:
        value: _io.size + 1
        doc: the blob size, lead included (the app's size tests use it)
  h6052_params_v5_layer:
    doc: One layer of the H6052 parse, a length then a body when has_body.
    params:
      - id: len_blob
        type: u4
        doc: the whole blob's size, lead included
    seq:
      - id: len_body
        type: u2le
      - id: body
        size: len_body
        type: gv3_layer_body
        if: has_body
    instances:
      has_body:
        value: len_body > 0 and len_body < len_blob
  graffiti_v2_value:
    doc: |
      The RGBIC DIY-graffiti value on 0xA3 (H61A8 commByte 0x03), confirmed
      on air for its START frame only.
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/DiyGraffitiV2.java:195-243
      - base-classes10/com/govee/base2light/ac/diyNew/parse/RgbIcGraffitiShare0x08.java:279-282, 511-519
      - base-classes10/com/govee/base2light/ble/controller/MultiDiyGraffitiController.java:25-26
    seq:
      - id: sub_effect
        type: u1
      - id: speed
        type: u1
      - id: brightness
        type: u1
        doc: of the base colour
      - id: base
        type: govee_common::rgb
      - id: num_groups
        type: u1
      - id: groups
        type: pixel_group
        repeat: expr
        repeat-expr: num_groups
      - id: rest
        type: govee_common::echo
  graffiti_v2_wide_value:
    doc: |
      The big-data form of graffiti_v2_value, with a u16le group count and
      two-byte pixel indices from 256 up.
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/DiyGraffitiV2.java:246-330
      - base-classes10/com/govee/base2light/ac/diyNew/parse/DIYGraffitiParser.java:143-145, 157-160, 344-356
    seq:
      - id: sub_effect
        type: u1
      - id: speed
        type: u1
      - id: brightness
        type: u1
      - id: base
        type: govee_common::rgb
      - id: num_groups
        type: u2le
      - id: groups
        type: wide_pixel_group
        repeat: expr
        repeat-expr: num_groups
      - id: rest
        type: govee_common::echo
  wide_pixel_group:
    doc: |
      One colour of the wide value: indices below 256 as one byte each, then
      the rest as u16le.
    seq:
      - id: num_pixels
        type: u2le
        doc: count of all indices
      - id: color
        type: govee_common::rgb
      - id: num_wide_pixels
        type: u2le
        doc: count of the indices >= 256
      - id: pixels
        type: u1
        repeat: expr
        repeat-expr: num_pixels - num_wide_pixels
      - id: wide_pixels
        type: u2le
        repeat: expr
        repeat-expr: num_wide_pixels
  color_strip_value:
    doc: |
      The per-segment colour-strip value on 0xA3 commByte 0x40, its segments
      grouped by distinct colour, brightness, kelvin or gradient.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerV14ColorStrip.java:23-93
      - base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerColorTemStrip.java:59-186
      - base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerGradientColorTemStrip.java:40-193
    seq:
      - id: num_groups
        type: u1
      - id: groups
        type: color_strip_group
        repeat: expr
        repeat-expr: num_groups
      - id: rest
        type: govee_common::echo
  color_strip_group:
    doc: |
      One group: its tag, the segment count, the tag's body and the segment
      indices.
    seq:
      - id: tag
        type: u1
        enum: strip_group_tag
      - id: num_indices
        type: u1
      - id: kelvin
        type: u2be
        if: tag == strip_group_tag::kelvin
      - id: color
        type: govee_common::rgb
        if: tag == strip_group_tag::color or tag == strip_group_tag::kelvin
      - id: brightness
        type: u1
        if: tag == strip_group_tag::brightness
      - id: gradient
        type: govee_common::rgb
        repeat: expr
        repeat-expr: 3
        if: tag == strip_group_tag::gradient
        doc: one segment's three colours; a short last chunk is not modelled
      - id: indices
        type: u1
        repeat: expr
        repeat-expr: num_indices
  bulb_diy_value:
    doc: |
      The bulb DIY value on 0xA3 commByte 0x04; H6006 firmware has no bounds
      check, so over 10 colours or 3 mix pairs overruns its stack struct.
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/DiyProtocol.java:36-66
      - base-classes10/com/govee/base2light/ble/controller/MultipleDiyControllerV1.java:15-18
      - firmware-h6006/decompiled/H6006_a3_reassembler.c:75-141
    seq:
      - id: effect_code
        type: u1
      - id: sub_effect_code
        type: u1
      - id: speed
        type: u1
      - id: len_colors
        type: u1
        doc: 3 x the colour count
      - id: colors
        type: govee_common::rgb
        repeat: expr
        repeat-expr: len_colors / 3
      - id: len_mix
        type: u1
        doc: 2 x the mix-pair count
      - id: mix_effects
        type: mix_pair
        repeat: expr
        repeat-expr: len_mix / 2
      - id: rest
        type: govee_common::echo
  mix_pair:
    doc: one DIY mix-effect entry
    seq:
      - id: value_0
        type: u1
      - id: value_1
        type: u1
  wifi_credentials_value:
    doc: |
      The Wi-Fi provisioning value on 0xA1 com_type 0x11; H5083 firmware takes
      ssid < 0x22 bytes, password 0 or 8..0x41 bytes and an API of 1..0x80.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/MultipleWifiController.java:20-104, 129-132, 152-198
      - H5083 FUN_9b0315fc @0x9b0315fc
    seq:
      - id: len_ssid
        type: u1
      - id: ssid
        size: len_ssid
        doc: UTF-8
      - id: len_password
        type: u1
      - id: password
        size: len_password
        doc: UTF-8
      - id: run_mode
        type: u1
      - id: tz_hours
        type: s1
        doc: UTC offset, whole hours
      - id: iot_version
        type: u1
      - id: tz_minutes
        type: s1
        doc: UTC offset, minutes part
      - id: dynamic_api
        type: dynamic_api
        if: _io.size - _io.pos >= 2
        doc: present with an API string or a Matter extension ([00 00] then)
      - id: rest
        type: govee_common::echo
        doc: extension [use_matter], [use_matter][security_type] or a wifi tag
  dynamic_api:
    doc: The dynamic-API block; the firmware takes its server URL from it.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/BleUtil.java:234-244
    seq:
      - id: len_api
        type: u2be
      - id: api
        size: len_api
        doc: the API / server URL, UTF-8
  iot_config_value:
    doc: |
      FIRMWARE-SOURCED: the IoT server-URL value on 0xA3 commByte 0x08 (no app
      7.7.02 sender); H5083 and H6006 take it (H6006's layout not traced).
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:69
      - H5083 FUN_9b0317b4 @0x9b0317b4
      - H6006 handler @0x9b03d93e-0x9b03d986
    seq:
      - id: len_url
        type: u2be
        doc: 1..0x80
      - id: url
        size: len_url
        doc: H5083 stores it (cfg+0x70) and reports the result as an A3 08 push
      - id: rest
        type: govee_common::echo
enums:
  upload_kind:
    0:
      id: unknown
      doc: no value type; the bytes stay raw
    1:
      id: rgb_scene
      doc: sceneType 1 library scene; 0xA3 commByte 0x01; no lead
    2:
      id: rgbic_scene
      doc: sceneType 2 library scene; 0xA3 commByte 0x02; no lead
    3:
      id: scenes_graffiti
      doc: sceneType 3 library scene; 0xA3 commByte 0x07; lead [01][effect_hi]
    4:
      id: scenes_graffiti_gate
      doc: the sceneType 3 cloud blob, whole, for its VALIDATOR (no lead)
    5:
      id: compose
      doc: sceneType 6 library scene; 0xA3 commByte 0x0a; lead 0x0a
    6:
      id: h60a6_diy
      doc: H60A6 sceneType 5 DIY; 0xA3 commByte 0x58; lead 0x50
    7:
      id: h60a6_graffiti
      doc: H60A6 sceneType 5 graffiti; 0xA4-MTU commByte 0x58; lead 0x50
    8:
      id: h6052_graffiti
      doc: H6052 sceneType 5 graffiti v3; 0xA3 commByte 0x09; lead 0x13
    9:
      id: h6052_params_v5
      doc: the H6052 0x13 cloud blob, for its VALIDATOR; lead 0x13
    10:
      id: graffiti_v2
      doc: RGBIC DIY graffiti; 0xA3, H61A8 commByte 0x03
    11:
      id: graffiti_v2_wide
      doc: RGBIC DIY graffiti, big-data form; protocol code 0x03
    12:
      id: color_strip
      doc: per-segment colour strip; 0xA3 commByte 0x40
    13:
      id: bulb_diy
      doc: bulb DIY; 0xA3 commByte 0x04
    14:
      id: wifi_credentials
      doc: Wi-Fi provisioning; 0xA1 com_type 0x11
    15:
      id: iot_config
      doc: IoT / server-URL provisioning; 0xA3 commByte 0x08 (firmware side)
  upload_form:
    1:
      id: wire
      doc: the joined value of an A1 / A3 / A4 upload
    2:
      id: cloud
      doc: a decoded cloud scenceParam (lead, then the body)
  strip_group_tag:
    0:
      id: color
    1:
      id: brightness
    2:
      id: kelvin
      doc: kelvin u16be, then the colour
    3:
      id: gradient
      doc: three colours of one segment
