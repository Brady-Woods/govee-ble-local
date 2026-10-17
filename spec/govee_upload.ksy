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
  STANDALONE (spans frames, or off-BLE: one joined A1 / A3 / A4 upload value,
  or one decoded cloud scenceParam). The kind is not in the bytes (DIY and
  graffiti share commByte 0x58), so the caller passes it, with the form.
  - form wire: the value an A1 / A3 / A4 multi-packet upload carries, joined
    from its frames. Joining, the per-frame header strip and the END trim
    are PROCEDURAL (library). Every kind's value is typed.
  - form cloud: base64-decode(scenceParam) of a library scene, which the app
    checks before it builds any upload. `lead` is the byte(s) the app checks
    and strips (`contents`, so another lead fails the read). The body is
    typed only for the kinds whose type is the app's own check of the cloud
    blob: rgb_scene, rgbic_scene, scenes_graffiti_gate and h6052_params_v5.
    Every other kind's cloud body stays bytes (`value`): it is the value the
    app sends (or re-encodes), so read it again with form wire for its tree.
  The upload gate is "it reads", except where a type is labelled VALIDATOR:
  then it mirrors one app check exactly, and its answer is `is_valid` where
  it has one, else a successful read. Gates per kind:
  - rgb_scene: rgb_scene_value.is_valid (ScenesRgb.isValidProtocolBytes);
  - rgbic_scene: a successful read (ScenesRgbIC.isValidProtocolBytes);
  - scenes_graffiti: scenes_graffiti_gate.is_valid on the whole blob
    (ShareDiy.isValidGraffitiBytes), then the cloud body is the value;
  - compose: the cloud read, i.e. its lead (ShareDiy.isValidComposeBytes);
  - h60a6_diy / h60a6_graffiti: the cloud read (lead 0x50, BaseSceneViewMode
    .r3), then the try-in-order of the H60A6 parsers, PROCEDURAL: DIY first
    (the form wire h60a6_diy read and every layers[].effect.is_valid is
    exactly Pro4H60A6Diy.Companion.c), else graffiti (form wire
    h60a6_graffiti; graffiti_value is stricter than H60A6GraffitiParse.j);
  - h6052_graffiti: a successful h6052_params_v5 read of the same blob
    (DiyProtocolParser.parserParamsV5); the cloud body is then the value.
  The app re-encodes the H60A6 and H6052 type-5 values before it sends them
  (IDiyParse.toBytes, DiyGraffitiV3.a); that equals the cloud body for a
  canonical blob (every bundled one), not for any blob. Layouts verified
  byte-exact against the bundled catalogues: 59 H60A6 graffiti, 14 H60A6
  DIY, 6 H6052 gv3, 25 H6052 sceneType 3, 57 H6047 and 135 H61A8 rgbic.
  Every value type ends with `rest` (or a named data field), and every
  sized record with `slack`, so any value re-writes exactly.
doc-ref:
  - base-classes10/com/govee/base2light/ble/ScenesOp.java:377-450
  - base-classes10/com/govee/base2light/ble/ScenesOp.java:541-578
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
    doc: |
      the lead the app checks and strips (form cloud only); absent for a kind
      whose cloud blob is the value verbatim
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
    doc: |
      the value (form wire) or the cloud body after the lead (form cloud):
      typed by value_kind; bytes for compose (no reader traced) and for a
      cloud body that only form wire types
instances:
  value_kind:
    value: >-
      (form == upload_form::wire
      or kind == upload_kind::rgb_scene
      or kind == upload_kind::rgbic_scene
      or kind == upload_kind::scenes_graffiti_gate
      or kind == upload_kind::h6052_params_v5)
      ? kind : upload_kind::unknown
    doc: |
      the kind whose type reads `value`: the kind itself, except under form
      cloud for a kind whose type is not the app's check of the cloud blob
      (unknown: the body stays bytes)
types:
  lead_scenes_graffiti:
    doc: |
      The 2-byte lead of a sceneType 3 (graffiti) cloud blob:
      [0x01][effect_hi][scenes_graffiti_value]. ScenesOp.n (from
      parseSceneV1 :549-550, when versionArray has 3) checks the blob with
      ShareDiy.isValidGraffitiBytes (kind scenes_graffiti_gate) and sends
      bytes [2:] verbatim with MultiNewScenesControllerV3, 0xA3 commByte 0x07
      (v3_new_scenes). Library site: scenes._plan_upload, sceneType 3, strip 2.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/ScenesOp.java:411-429
      - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV3.java:10-11
    seq:
      - id: marker
        contents: [0x01]
        doc: lead byte 0x01 (isValidGraffitiBytes :99; parserParamsV1 :1402)
        doc-ref:
          - base-classes10/com/govee/base2light/ac/diy/local/ShareDiy.java:99
          - base-classes10/com/govee/base2light/ac/diy/v2/DiyProtocolParser.java:1402
      - id: effect_hi
        type: u1
        doc: |
          first byte of the effect-code pair: parserParamsV1 :1405 reads
          int[]{bytes[1], bytes[2]} = (effect_hi, scenes_graffiti_value
          .effect_lo); stripped with the lead
        doc-ref:
          - base-classes10/com/govee/base2light/ac/diy/v2/DiyProtocolParser.java:1405
  lead_compose:
    doc: |
      VALIDATOR (read-side): the lead of a sceneType 6 (compose) cloud blob,
      [0x0a][value]. ShareDiy.isValidComposeBytes is bytes[0] == 10, the
      whole check, so a blob whose lead reads is one ScenesOp.o (from
      parseSceneV1 :552-553, when versionArray has 6) sends with
      MultiNewScenesControllerV5, 0xA3 commByte 0x0a (v4_new_scenes), whose
      constructor drops byte 0 (AbsMultipleControllerV14Scenes,
      need4IgnoreFirstBit = true). A blob with lead 0x51 goes to
      MultiNewScenesControllerH60B0 instead (commByte 0x5a), not modelled;
      any other lead gets no controller, so the scene is only activated. An
      empty blob fails here; in the app its bytes[0] throws out of o
      (nothing in ScenesOp catches it), so no upload is built either. The
      compose value has no traced reader and stays bytes. Library site:
      scenes._plan_upload, sceneType 6 (strip 1).
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/local/ShareDiy.java:81-83
      - base-classes10/com/govee/base2light/ble/ScenesOp.java:431-450
      - base-classes10/com/govee/base2light/ble/controller/MultiNewScenesControllerV5.java:5-7
      - base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerV14Scenes.java:29-38
    seq:
      - id: marker
        contents: [0x0a]
  lead_h60a6:
    doc: |
      The lead of an H60A6 sceneType 5 (DIY or graffiti) cloud blob,
      [0x50][value]. BaseSceneViewMode.r3 rejects a blob shorter than 2
      bytes or with another lead (:1163), then hands the whole blob to the
      goodsType's IDiyParse list in order and sends with the first whose
      parse() is true (:1192-1203). H60A6 (goodsType 303) registers
      H60A6DiyParse, then H60A6GraffitiParse (H60A6DiyConfig :36-37,
      DiyNewConfig.addDiyEffectParse(303)). Both need size > 2 and re-check
      the lead (Pro4H60A6Diy :72; H60A6GraffitiParse :300, the lead set at
      :47). When r3 is false, the caller falls back to
      ScenesOp.parseSceneV1, which builds an upload for a sceneType 5 scene
      only if versionArray names a type-5 parser; otherwise only the
      scene-mode controller is sent. commByte 0x58 (cube_in_diy). Library
      site: scenes._h60a6_upload.
    doc-ref:
      - base-classes11/com/govee/base2light/pact/newdetail/content/scene/BaseSceneViewMode.java:1163
      - base-classes11/com/govee/base2light/pact/newdetail/content/scene/BaseSceneViewMode.java:1192-1203
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6DiyConfig.java:36-37
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6DiyParse.java:543-560
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/Pro4H60A6Diy.java:72
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6GraffitiParse.java:47
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6GraffitiParse.java:300
    seq:
      - id: marker
        contents: [0x50]
        doc: lead byte 0x50 = 80 (r3 :1163; Pro4H60A6Diy.e = 80, :32)
        doc-ref:
          - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/Pro4H60A6Diy.java:32
  lead_h6052:
    doc: |
      The lead of an H6052 sceneType 5 professional-graffiti cloud blob,
      [0x13][body]. The H6052 scene screen sends a sceneType 5 scene through
      tablelampv1 Diy.parseScenes4Diy (H6052ScenesViewMode :47), which tries
      ShareDiy.parseDiyProtocol (lead 0x00, commByte 0x04: the bulb_diy
      layout after the lead), parseDiyGraffiti (lead 0x01, commByte 0x03) and
      then parseDiyGraffitiProfessional; only that last, 0x13 form is
      modelled. Its parse is DiyProtocolParser.parserParamsV5 (kind
      h6052_params_v5); DiyGraffitiV3.a re-encodes it as graffiti_v3_value,
      which MultipleDiyInScenesController sends on 0xA3 commByte 0x09
      (v1_graffiti_multi_layer). For a catalogue blob a() re-encodes exactly
      the body. Library site: scenes._plan_upload, B_h6052 sceneType 5.
    doc-ref:
      - split_hollowlamp/com/govee/tablelampv1/newAdjust/h6052/H6052ScenesViewMode.java:42-83
      - split_hollowlamp/com/govee/tablelampv1/adjust/Diy.java:95-109
      - base-classes10/com/govee/base2light/ac/diy/local/ShareDiy.java:153-159
      - base-classes10/com/govee/base2light/ble/MultipleDiyInScenesController.java:16-18
    seq:
      - id: marker
        contents: [0x13]
        doc: lead byte 0x13 = 19 (parserParamsV5 :1618)
        doc-ref:
          - base-classes10/com/govee/base2light/ac/diy/v2/DiyProtocolParser.java:1618
  rgb_scene_value:
    doc: |
      VALIDATOR (read-side): a sceneType 1 (rgb) library scene, the decoded
      scenceParam verbatim (no lead), which ScenesOp.l sends with
      MultiNewScenesControllerV1, 0xA3 commByte 0x01 (v1_new_scenes), only
      when ScenesRgb.isValidProtocolBytes accepts it; otherwise the scene is
      only activated. That check is `is_valid`; a failed read (an empty
      blob, where the app's bytes[0] throws) is false too. Byte 0 is a
      bit-packed config that ScenesRgb.e splits into [bit 7, bits 6-4,
      bits 2-0] (bit 3 is not read). The body's shape follows them:
      mode 1: size == 2 + effect_count x (color_count + 5);
      mode 0: size == effect_count x 5 + 3 + color_count x color_num.
      The body stays bytes: no reader of its records is traced (the H6006
      firmware stores it without a check).
    doc-ref:
      - base-classes10/com/govee/base2light/ble/ScenesOp.java:377-392
      - base-classes10/com/govee/base2light/ble/ScenesRgb.java:1642-1656
      - base-classes10/com/govee/base2light/ble/ScenesRgb.java:1676-1701
      - firmware-h6006/decompiled/H6006_a3_reassembler.c:70-74
    seq:
      - id: color_count
        type: b3
        doc: bits 2-0 of byte 0, e()[2] (binaryString.substring(5))
      - id: reserved
        type: b1
        doc: bit 3 of byte 0, not read by the app
      - id: mode
        type: b3
        doc: |
          bits 6-4 of byte 0, e()[1] (substring(1, 4)): 1 or 0 picks the body
          shape; isValidProtocolBytes is false for 2..7 (:1687-1689)
      - id: follow_sys_brightness
        type: b1
        doc: |
          bit 7 of byte 0, e()[0]: ScenesRgb.d is e()[0] == 1, and
          followSysBrightness returns it for the decoded blob. Not part of the
          upload check
        doc-ref:
          - base-classes10/com/govee/base2light/ble/ScenesRgb.java:1637-1640
      - id: body
        size-eos: true
        doc: the body after byte 0; its shape follows mode, above
    instances:
      effect_count:
        value: 'body.length >= 1 ? body[0] : 0'
        doc: |
          byte 1, unsigned (:1688, :1690); 0 for a 1-byte blob, where the
          app's read throws and is_valid is false
      color_num:
        value: >-
          body.length > effect_count * 5 + 1
          ? body[effect_count * 5 + 1] : 0
        doc: |
          mode 0: the byte at effect_count x 5 + 2 (colorNumIndex,
          :1691-1693); 0 when that index is past the end, where the app's
          read throws and is_valid is false
      is_valid:
        value: >-
          body.length >= 1
          and ((mode == 1
          and effect_count * (color_count + 5) + 2 == body.length + 1)
          or (mode == 0 and body.length > effect_count * 5 + 1
          and effect_count * 5 + 3 + color_count * color_num
          == body.length + 1))
        doc: |
          ScenesRgb.isValidProtocolBytes, exactly. mode 1: byte 1 x
          (color_count + 5) + 2 == the blob size (:1688). mode 0:
          effect_count x 5 + 3 + color_count x color_num == the blob size
          (:1690-1698). Any other mode is false (:1688), and so is a blob too
          short for the bytes the app reads: its read throws and the catch
          returns false (:1699-1700; the jadx text drops that return, the
          bytecode returns 0)
        doc-ref:
          - base-classes10/com/govee/base2light/ble/ScenesRgb.java:1676-1701
  rgbic_scene_value:
    doc: |
      VALIDATOR (read-side): a sceneType 2 (rgbic) library scene, the
      decoded scenceParam verbatim (no lead), which ScenesOp.m sends with
      MultiNewScenesControllerV2, 0xA3 commByte 0x02 (v2_new_scenes). A
      successful read is exactly ScenesRgbIC.isValidProtocolBytes; a failed
      read is its false, and the scene is then only activated. The app's
      walk reads byte 0, then per effect the length byte and that many bytes
      (:2790-2797), and runs f() on them (:2655-2676), which touches the
      bytes rgbic_effect_record reads (17 + 6 x num_brightness_effects + 3 x
      num_colors); any out-of-range index throws and is caught as false
      (:2804-2805). Like this type it ignores the bytes after the last effect
      (rest) and after the end of each record (slack). Verified: reads all
      327 dialect-A rgbic catalogue scenes (H6047, H61A8, H6641) with exact
      consumption.
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
        doc: bytes after the last effect, which the app does not read
  rgbic_effect:
    seq:
      - id: len_record
        type: u1
      - id: record
        size: len_record
        type: rgbic_effect_record
  rgbic_effect_record:
    doc: |
      One rgbic effect record, as ParamsV2.b parses it and RgbICEffect.k
      writes it (ScenesRgbIC.f is only a length check): a style byte
      (packed nibbles), the mode and its 2-byte value, a brightness block
      (num_brightness_effects x 6-byte BrightnessEffect), a ColorEffect
      (colour-IC byte, speed, duration, the RGB palette: the actual
      colours), then the InAreaMove (3) and AreaMove (4) movement blocks.
      Colours decode correctly (e.g. H6047 "Action": red then blue).
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/v2/ParamsV2.java:767-822
      - base-classes10/com/govee/base2light/ac/diy/v2/ParamsV2.java:531
    seq:
      - id: style
        type: u1
        doc: |
          packed nibbles hi | lo (ParamsV2.e; ScenesRgbIC.h): speed / order
      - id: mode
        type: u1
        doc: |
          RgbICEffect.c; selects mode_param's meaning. ParamsV2.b returns
          null for a mode above 3 (ScenesRgbIC.f does not check it)
      - id: mode_param
        type: s2be
        doc: |
          mode 0 / 1: the signed value getSignedShort(b2, b3), high byte
          first (RgbICEffect.d / e); mode 2 / 3: two unsigned bytes, high
          byte first (f / g, or h / i)
      - id: bright_algo
        type: u1
        doc: brightness algorithm byte (ScenesRgbIC.d high / low nibbles)
      - id: num_brightness_effects
        type: u1
      - id: brightness_effects
        type: brightness_effect
        repeat: expr
        repeat-expr: num_brightness_effects
      - id: color_ic
        type: u1
        doc: makeColorIcByte, packed order + colour type (ScenesRgbIC.e)
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
        doc: bytes in the record after its fields, which the app does not read
  brightness_effect:
    doc: |
      ParamsV2.BrightnessEffect: 6 unsigned bytes (level / index params; the
      app stores each as its own field but attaches no further meaning).
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
      ParamsV2.InAreaMoveEffect.c: move_flags (canMove << 4 | order) and two
      movement params.
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
      ParamsV2.AreaMoveEffect.c: move_flags (canMove << 4 | order) and three
      movement params.
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
      A sceneType 3 (graffiti) library scene's upload value, the cloud blob's
      bytes [2:] (after lead_scenes_graffiti), sent verbatim on 0xA3 commByte
      0x07. Structure = ParamsV1.a / DiyProtocolParser.parserParamsV1:
      effect_lo, speed, brightness, the background colour, then num_groups
      colour groups. Verified: reads all 25 H6052 sceneType 3 catalogue
      scenes with exact consumption. Not the upload gate: it needs the last
      group whole, which the app does not check (scenes_graffiti_gate).
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/v2/DiyProtocolParser.java:1391-1433
      - base-classes10/com/govee/base2light/ble/ScenesOp.java:422-425
    seq:
      - id: effect_lo
        type: u1
        doc: second byte of the effect-code pair (lead's effect_hi is first)
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
    doc: |
      One colour of a graffiti pixel map: [num_pixels][r g b][pixel index x
      num_pixels], each index one byte. Shared by graffiti_v3_value (H6052
      gv3), graffiti_v2_value (DiyGraffitiV2.g) and scenes_graffiti_value.
    seq:
      - id: num_pixels
        type: u1
      - id: color
        type: govee_common::rgb
      - id: pixels
        type: u1
        repeat: expr
        repeat-expr: num_pixels
        doc: LED / pixel indices painted by this colour
  scenes_graffiti_gate:
    doc: |
      VALIDATOR (read-side): ShareDiy.isValidGraffitiBytes, exactly, the
      check ScenesOp.n (from parseSceneV1 :549-550) makes on a sceneType 3
      (graffiti) library scene's decoded scenceParam before it sends bytes
      [2:] with MultiNewScenesControllerV3; when it is false, n returns null
      and the scene is only activated. It reads the whole blob, lead
      included, under either form (no lead is stripped for this kind). The
      read never fails; is_valid is the app's answer. The app checks size
      >= 9 and byte 0 == 1 (:99), takes num_heads from byte 8 (:102), then
      walks the group headers from byte 9, reading each header byte (a
      group's pixel count) and stepping over the header, the RGB and that
      many pixel bytes: next = at + byte[at] + 4 (:103-107). A read past the
      end throws and is caught as false (:109-113). Nothing else is checked:
      the last group's RGB and pixel bytes may be missing, and bytes after
      the walk are ignored. scenes_graffiti_value also needs the last group
      whole, so it is stricter than this check.
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/local/ShareDiy.java:96-115
      - base-classes10/com/govee/base2light/ble/ScenesOp.java:411-429
    seq:
      - id: blob
        size-eos: true
        doc: the decoded scenceParam, whole
      - id: heads
        type: 'gate_step(_index == 0 ? 9 : heads[_index - 1].next_at, blob)'
        repeat: expr
        repeat-expr: num_heads
        doc: |
          one zero-width step per pass of the app's loop (:104-106), at = the
          index it reads; reads no bytes
    instances:
      num_heads:
        value: 'blob.length >= 9 ? blob[8] : 0'
        doc: |
          byte 8, unsigned (:102); 0 for a blob shorter than 9, which the app
          rejects before reading it
      is_valid:
        value: >-
          blob.length >= 9 and blob[0] == 1
          and (num_heads == 0 or heads[num_heads - 1].in_range)
        doc: |
          ShareDiy.isValidGraffitiBytes, exactly: size >= 9, lead 0x01, and
          every header index the walk reads inside the blob. The indexes rise
          by at least 4 a step and a step past the end stops there, so the
          last one is inside only when all of them are.
  gate_step:
    doc: one pass of the isValidGraffitiBytes walk (:105); reads no bytes
    params:
      - id: at
        type: u4
        doc: the index of this group's header byte (its pixel count)
      - id: blob
        type: bytes
        doc: the whole decoded blob
    instances:
      in_range:
        value: at < blob.length
        doc: the app's read of byte[at] does not throw
      next_at:
        value: 'in_range ? at + blob[at] + 4 : at'
        doc: |
          the next header index, at + pixel count + 4 (header, RGB and the
          pixel bytes); stays at `at` once past the end
  diy_value:
    doc: |
      VALIDATOR (read-side): an H60A6 DIY value, [len_body][num_layers]
      [num_bg_colors][background colours][bg_brightness][layers], as
      Pro4H60A6Diy.d builds it (the length prepended last) and H60A6DiyParse
      sends it on 0xA3 commByte 0x58 (MultiController). It is also the body
      of the cloud blob [0x50][this], which Pro4H60A6Diy.Companion.c parses:
      the 0x50 lead (:72), u16le(b1, b2) + 3 == the blob size (:83: the
      len_body valid, read-side), then the background colours and the layers
      by their u16le lengths; a layer that does not parse makes it null
      (:120), and any exception gives null (:127). Bytes after the last
      layer are not checked (rest). For a value this type has read, the DIY
      parse is non-null exactly when every layers[].effect.is_valid (Kaitai
      has no all(), so the library takes that conjunction). H60A6DiyParse
      .parse also needs size > 2 and, when the scene carries diyEffectCodes,
      code 797 (H60A6DiyConfig.b) among them; that gate has no wire form and
      is not modelled. Pro4H60A6Diy.d returns null for a DIY with no layers
      or no background colours, and toBytes then sends an empty value.
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/Pro4H60A6Diy.java:65-131
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/Pro4H60A6Diy.java:171-221
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6DiyParse.java:471-477
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6DiyParse.java:827-835
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6DiyConfig.java:31
    seq:
      - id: len_body
        type: u2le
        valid:
          expr: _ + 2 == _io.size
        doc: |
          the number of bytes after this field: the value's size - 2
          (Pro4H60A6Diy.java:83 reads it little-endian, unsigned)
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
        doc: bytes after the last layer, which the app does not check
  diy_layer:
    seq:
      - id: len_effect
        type: u2le
      - id: effect
        size: len_effect
        type: diy_sub_effect
  diy_sub_effect:
    doc: |
      One H60A6 DIY layer body, [sub_effect_id][params] (Layer.Companion.b).
      Ids (pinyin = the decompiled class name): 1 LiuDong4Area (flow, zone),
      2 LiuDong4Line (flow, line), 3 KuoSan (diffuse), 4 XuanZhuan (rotate),
      5 SuiJiHuXi (random breathing), 6 SuiJiLiuXing (random meteor),
      7 SuiJiJianBian (random gradient), 8 Fade, 9 HuXi (breathing),
      10 ShanShuo (blink). All but LiuDong4Line share the shape [fixed
      params][palette]. Field ids are the decompiled setters (single
      letters where the app assigns no further meaning). Verified: consumes
      every DIY layer body in the catalogue exactly (ids 1, 2, 5 and 7
      occur). Another id leaves `effect` absent, with its bytes in slack.
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
        doc: bytes in the layer after the effect, which the app does not read
    instances:
      is_valid:
        value: sub_effect_id >= 1 and sub_effect_id <= 10
        doc: |
          Layer.Companion.b parses ids 1..10 (:103-142) and returns null for
          any other id (:143-144; the id byte is compared as a signed byte,
          so no id of 0x80 or more is 1..10 either), and a null layer makes
          Pro4H60A6Diy.Companion.c null (:120-121). For a layer this type has
          read, is_valid is exactly Layer.b being non-null: for ids 1..10
          each sub-parser (none of which catches or returns null) reads the
          bytes its case type reads and throws past the layer's end (caught
          as null, Layer.java:145-147), as the read here fails; for another
          id this type reads the id only, where the app stops too. Not a
          `valid`: today's library reads such layers.
        doc-ref:
          - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/protocol/Layer.java:103-147
          - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/Pro4H60A6Diy.java:120-121
  palette:
    doc: a count-prefixed RGB palette, [num_colors][r g b x num_colors]
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
    doc: |
      sub-effect 2, LiuDong4Line (flow, line): [i, direction, on] + a
      count-prefixed byte list + [l0, l1, r, t, s] + an RGB palette
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/protocol/LiuDong4Line.java:1-181
    seq:
      - id: speed
        type: u1
        doc: i()
      - id: direction
        type: u1
      - id: on_flag
        type: u1
        doc: a boolean (id `on` avoided, a YAML 1.1 boolean)
      - id: num_segs
        type: u1
      - id: segs
        type: u1
        repeat: expr
        repeat-expr: num_segs
        doc: per-segment index bytes
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
        doc: getSignedInt(b2, b3)
      - id: p
        type: u1
        doc: a boolean
      - id: palette
        type: palette
  random_breath:
    doc: |
      sub-effects 5 SuiJiHuXi (random breathing: i, o, n where n is
      byte == 1) and 7 SuiJiJianBian (random gradient: i, l[0], l[1]): the
      same shape, three bytes and a palette
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/protocol/SuiJiHuXi.java:39-56
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/protocol/SuiJiJianBian.java:40-58
    seq:
      - id: i
        type: u1
      - id: param_1
        type: u1
        doc: SuiJiHuXi o; SuiJiJianBian l[0]
      - id: param_2
        type: u1
        doc: SuiJiHuXi n (a boolean); SuiJiJianBian l[1]
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
      An H60A6 graffiti value: a background colour and a per-layer
      colour-index pixel map, as H60A6GraffitiParse.toBytes builds it
      ([0x20] + the graffiti bytes) and sends it on 0xA4-MTU commByte 0x58
      (Compose4DefWrite4Multi.d). Also the body of the cloud blob
      [0x50][this]. H60A6GraffitiParse.parse needs size > 2, the 0x50 lead
      and value byte 0 == 0x20 (:300); its walk j() needs the 8-byte blob
      header (the arraycopy at :110 throws below it), then stops, keeping the
      layers before, at a layer that runs past the end or whose type is not
      3, or when fewer than 3 bytes are left before num_layers layers; it
      reads a layer's colour groups and the six bytes after them up to the
      end of the value, not within len_body (LayerDataH60A6.setLayerData; a
      len_body of 0 too), and it never reads the 4 reserved bytes. So this
      type is never looser than j() and can raise where j() does not; the
      app then sends its re-encoded toBytes(), not this value.
    doc-ref:
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6GraffitiParse.java:293-309
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6GraffitiParse.java:101-147
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6GraffitiParse.java:240
      - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/diy/H60A6GraffitiParse.java:312-325
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
        doc: the layer's size from graffiti_type on (j() takes it - 1 after)
      - id: body
        size: len_body
        type: graffiti_layer_body
  graffiti_layer_body:
    seq:
      - id: graffiti_type
        type: u1
        doc: 0x03; j() stops at a layer whose type is not 3 (:127)
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
        doc: always 00 00 00 00 (the H60A6GraffitiParse.toBytes trailer pad)
      - id: slack
        size-eos: true
        doc: bytes in the layer after the pad
  h60a6_pixel_group:
    doc: |
      One colour of the H60A6 graffiti pixel map: [num_pixels u2le][r g b]
      [pixel index x num_pixels], each index one byte
    seq:
      - id: num_pixels
        type: u2le
      - id: color
        type: govee_common::rgb
      - id: pixels
        type: u1
        repeat: expr
        repeat-expr: num_pixels
        doc: LED / pixel indices painted by this colour
  graffiti_v3_value:
    doc: |
      The H6052 graffiti-v3 value (DiyGraffitiV3.a), sent on 0xA3 commByte
      0x09 by MultipleDiyInScenesController: brightness, base colour and a
      per-layer colour-index pixel map. a() writes num_layers = the layers
      parserParamsV5 read and recomputes each length, and a colour repeated
      within a layer keeps its first group's place with the last group's
      pixels (LinkedHashMap.put, DiyProtocolParser :198). It is stricter
      than parserParamsV5, which ignores num_layers and reads at most three
      layers by their length prefixes (h6052_params_v5).
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
        doc: the layer's size - 2
      - id: body
        size: len_body
        type: gv3_layer_body
  gv3_layer_body:
    doc: |
      One graffiti-v3 layer, as DiyProtocolParser.b reads it: speed, action,
      priority, the colour count and the colour groups. b() has no try; a
      read past the body throws (caught by parserParamsV5 as null).
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
        doc: bytes in the layer after its groups, which the app does not read
  h6052_params_v5:
    doc: |
      VALIDATOR (read-side): DiyProtocolParser.parserParamsV5, exactly, on
      the H6052 0x13 cloud blob (form cloud: the lead is lead_h6052, and
      this reads the bytes after it; the app's size tests count the lead,
      len_blob). A successful read is exactly parserParamsV5 returning
      non-null: the app then sends DiyGraffitiV3.a() with
      MultipleDiyInScenesController, commByte 0x09. A failed read is its
      null (any exception is caught, :1660-1663): Diy.parseScenes4Diy
      returns null and the scene gets no upload, only its activation
      (H6052ScenesViewMode :63-75). The app needs size >= 7 and lead 0x13
      (:1614-1620) and reads byte 7 (:1627), so 8 bytes in effect. It reads
      the brightness and the base colour, skips byte 5 (the layer count),
      and reads at most three layers by their u16le length prefixes
      (:1627-1654; BleUtil.getUnsignedBytes(h, l) is u16le). A layer whose
      length is 0 or not below the blob size is skipped without reading its
      body, and the next length is read at the same offset (:1634, :1649),
      so it is skipped too: after the first such layer no layer is read. The
      second length is read only when 2 bytes follow the first layer
      (:1635), the third only when 2 bytes follow the second (:1650). A
      layer whose body runs past the end of the blob, or whose b() walk runs
      past the end of the body, throws. Bytes after the last layer read,
      after a layer's last colour group, and the layer count are not
      checked. Meaningful under form cloud only (under form wire it reads
      the bytes as if the lead had been stripped).
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/v2/DiyProtocolParser.java:1604-1663
      - base-classes10/com/govee/base2light/ble/BleUtil.java:333-335
      - split_hollowlamp/com/govee/tablelampv1/newAdjust/h6052/H6052ScenesViewMode.java:63-75
    seq:
      - id: brightness
        type: u1
        doc: the base brightness (:1621; ParamsV5.c, written first by a())
      - id: base
        type: govee_common::rgb
        doc: the base colour, blob bytes 2-4 (:1622-1624)
      - id: num_layers
        type: u1
        doc: |
          blob byte 5: not read by parserParamsV5; DiyGraffitiV3.a writes the
          number of layers read instead
      - id: first
        type: h6052_params_v5_layer(len_blob)
        doc: the length at blob bytes 6-7, read always (:1627)
      - id: second
        type: h6052_params_v5_layer(len_blob)
        if: first.has_body and len_blob >= first.len_body + 10
        doc: |
          read when the first layer had a body and 2 bytes follow it, at blob
          offset 8 + first.len_body (:1634-1648)
      - id: third
        type: h6052_params_v5_layer(len_blob)
        if: >-
          first.has_body and len_blob >= first.len_body + 10
          and second.has_body
          and len_blob >= first.len_body + second.len_body + 12
        doc: |
          read when the second layer had a body and 2 bytes follow it, at blob
          offset 10 + first.len_body + second.len_body (:1649-1654)
      - id: rest
        type: govee_common::echo
        doc: bytes after the last layer read, which the app does not check
    instances:
      len_blob:
        value: _io.size + 1
        doc: the decoded blob's size, lead included (the app's comparisons)
  h6052_params_v5_layer:
    doc: |
      One parserParamsV5 layer (:1627-1654): a u16le length, then, when the
      app copies it (has_body), that many bytes, which b() reads with the
      gv3_layer_body layout.
    params:
      - id: len_blob
        type: u4
        doc: the size of the whole decoded blob, which the length is tested on
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
        doc: |
          the app copies and reads the body only when 0 < length < blob size
          (:1628, :1643, :1650); otherwise it skips the layer
  graffiti_v2_value:
    doc: |
      The general RGBIC DIY-graffiti value, DiyGraffitiV2.g (used by
      RgbIcGraffitiShare0x08, isBigDataEffect false, and by
      OpDiyCommDialog4BleV2 through MultiDiyGraffitiController): a header
      and a flat colour-index group list. Distinct from the dialect-A
      library rgbic format (rgbic_scene_value). commByte is device-specific
      (H61A8 = 0x03, MultiDiyGraffitiController / protocol code 3), on 0xA3.
      The START frame is byte-exact against a plaintext H61A8 btsnoop
      (a3 00 01 10 03 09 00 64 00 00 00 1c 05 00 ff 00 00 01 02, BCC 0x39);
      the full value (28 groups) is inferred from the g() loop, not observed
      end to end.
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/DiyGraffitiV2.java:195-243
      - base-classes10/com/govee/base2light/ac/diyNew/parse/RgbIcGraffitiShare0x08.java:279-282
      - base-classes10/com/govee/base2light/ac/diyNew/parse/RgbIcGraffitiShare0x08.java:511-519
      - base-classes10/com/govee/base2light/ble/controller/MultiDiyGraffitiController.java:25-26
    seq:
      - id: sub_effect
        type: u1
      - id: speed
        type: u1
      - id: brightness
        type: u1
        doc: baseColorBrightness
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
      The big-data form of the RGBIC DIY-graffiti value, DiyGraffitiV2.h,
      which DIYGraffitiParser.toBytes sends (isBigDataEffect true, protocol
      code 0x03): the graffiti_v2_value header with a u16le group count,
      and groups whose pixel indices >= 256 are two bytes.
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/DiyGraffitiV2.java:246-330
      - base-classes10/com/govee/base2light/ac/diyNew/parse/DIYGraffitiParser.java:143-145
      - base-classes10/com/govee/base2light/ac/diyNew/parse/DIYGraffitiParser.java:157-160
      - base-classes10/com/govee/base2light/ac/diyNew/parse/DIYGraffitiParser.java:344-356
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
      One colour of DiyGraffitiV2.h: [num_pixels u16le][r g b][num_wide_pixels
      u16le], the indices below 256 as one byte each, then the num_wide_pixels
      indices >= 256 as u16le
    seq:
      - id: num_pixels
        type: u2le
        doc: all indices of this colour
      - id: color
        type: govee_common::rgb
      - id: num_wide_pixels
        type: u2le
        doc: the indices >= 256
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
      The per-segment colour-strip value, 0xA3 commByte 0x40 (v1_new_color):
      the write-side mirror of the 0xA5 colour read. Instead of one record
      per segment it groups segments by distinct colour, brightness, colour
      temperature or gradient, each group tagged with its member segment
      indices: [num_groups][group x num_groups], num_groups = distinct
      colours (or gradients) + distinct brightness values (when a
      brightnessSet is set) + distinct Kelvins. Built by
      AbsMultipleControllerV14ColorStrip
      (MultipleColorStripControllerV1), AbsMultipleControllerColorTemStrip
      (MultipleColorTemStripController) and
      AbsMultipleControllerGradientColorTemStrip
      (MultipleGradientColorTemStripController), each set in HashMap key
      order. Joined from the 0xA3 V1 frames.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerV14ColorStrip.java:23-93
      - base-classes10/com/govee/base2light/ble/controller/MultipleColorStripControllerV1.java:15-18
      - base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerColorTemStrip.java:59-186
      - base-classes10/com/govee/base2light/ble/controller/MultipleColorTemStripController.java:48-51
      - base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerGradientColorTemStrip.java:40-193
      - base-classes10/com/govee/base2light/ble/controller/MultipleGradientColorTemStripController.java:29-32
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
      [tag][num_indices][body by tag][segment index x num_indices]: tag 0
      [r g b]; tag 1 [brightness]; tag 2 [kelvin u16be][r g b of the colour];
      tag 3 [r g b x 3] (one segment's three gradient colours). An unknown
      tag has no body.
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
        doc: |
          colorSet chunked by 3 per segment (CollectionsKt.chunked); a short
          last chunk (a colorSet not a multiple of 3) is not modelled
      - id: indices
        type: u1
        repeat: expr
        repeat-expr: num_indices
        doc: the segment indices (posList entries when given) of this group
  bulb_diy_value:
    doc: |
      The bulb DIY value, DiyProtocol.toBytes: [effect_code][sub_effect_code]
      [speed][len_colors = 3 x n][n colours][len_mix = 2 x m][m mix pairs],
      sent on 0xA3 commByte 0x04 (v1_new_diy) by MultipleDiyControllerV1 /
      MultipleDiyInScenesController (bulblightv3 OpDiyCommDialog4BleIot).
      len_mix is 0 when there are no mix effects (the byte stays zero).
      Firmware (H6006): on commByte 4 it reads mode, sub_mode, speed and
      len_colors, then len_colors / 3 colours, len_mix, then len_mix / 2
      pairs; it has no bounds check (more than 10 colours or 3 pairs
      overruns a 0x29-byte stack struct). The cloud form with a 0x00 lead
      (ShareDiy.parseDiyProtocol, the H6052 Diy.parseScenes4Diy first try)
      is this layout after the lead; it is not modelled as a cloud form.
    doc-ref:
      - base-classes10/com/govee/base2light/ac/diy/DiyProtocol.java:36-66
      - base-classes10/com/govee/base2light/ble/controller/MultipleDiyControllerV1.java:15-18
      - base-classes10/com/govee/base2light/ble/MultipleDiyInScenesController.java:20-22
      - base-classes10/com/govee/base2light/ac/diy/local/ShareDiy.java:161-224
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
    doc: one DiyProtocol mixEffects entry, two unsigned bytes [0][1]
    seq:
      - id: value_0
        type: u1
      - id: value_1
        type: u1
  wifi_credentials_value:
    doc: |
      The Wi-Fi provisioning value, 0xA1 com_type 0x11 (MULTI_WIFI), built by
      MultipleWifiController: [len_ssid][ssid][len_password][password]
      [run_mode][tz_hours][iot_version][tz_minutes], then the dynamic-API
      block when the app has an api string or a Matter extension, then the
      extension bytes. An empty password is the single byte 0. Firmware
      (H5083 FUN_9b0315fc): ssid length < 0x22, password length < 0x42 and
      0 or >= 8, and the API used only when its length is 1..0x80 (else the
      server URL is reset). The A1 START [a1 11 00 n], DATA [a1 11 i 16B]
      and END [a1 11 ff] framing is PROCEDURAL.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/MultipleWifiController.java:20-104
      - base-classes10/com/govee/base2light/ble/controller/MultipleWifiController.java:129-132
      - base-classes10/com/govee/base2light/ble/controller/MultipleWifiController.java:152-198
      - H5083 FUN_9b0315fc @0x9b0315fc
    seq:
      - id: len_ssid
        type: u1
      - id: ssid
        size: len_ssid
        doc: UTF-8 (StrUtil.formatByUtf)
      - id: len_password
        type: u1
      - id: password
        size: len_password
        doc: UTF-8; empty when len_password is 0
      - id: run_mode
        type: u1
        doc: Constant.getRunModeV0 / V1
      - id: tz_hours
        type: s1
        doc: TimeZoneUtil.getTimeOffset
      - id: iot_version
        type: u1
        doc: Constant.getIotVersion
      - id: tz_minutes
        type: s1
        doc: TimeZoneUtil.getTimeOffset4Minute
      - id: dynamic_api
        type: dynamic_api
        if: _io.size - _io.pos >= 2
        doc: |
          present with an api string (the 4-argument constructor) and when
          x() / y() append a Matter extension without one ([00 00] then)
      - id: rest
        type: govee_common::echo
        doc: |
          the extension bytes after the API block: x() appends [use_matter],
          y() [use_matter][security_type], w() a wifi tag
  dynamic_api:
    doc: |
      [len_api u16be][api]: BleUtil.getSignedBytesFor2(len, true), high byte
      first; the firmware takes the server URL from it
    doc-ref:
      - base-classes10/com/govee/base2light/ble/BleUtil.java:234-244
    seq:
      - id: len_api
        type: u2be
      - id: api
        size: len_api
        doc: the API / server URL string, UTF-8
  iot_config_value:
    doc: |
      FIRMWARE-SOURCED: the IoT / server-URL provisioning value on 0xA3
      commByte 0x08 (MULTI_V1_NEW_DYNAMIC_API; no 0xA3 sender in 7.7.02):
      [len_url u16be 1..0x80][url]. H5083 parses it in its shared layer
      (FUN_9b0317b4, stored at cfg+0x70), then reports the result as an
      A3 08 push. The H6006 shared layer takes commByte 8 too ('wifiparam',
      then IOT_IOTCONFIG_Start); its value layout is not traced.
    doc-ref:
      - base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:69
      - H5083 FUN_9b0317b4 @0x9b0317b4
      - H6006 handler @0x9b03d93e-0x9b03d986
    seq:
      - id: len_url
        type: u2be
      - id: url
        size: len_url
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
      doc: RGBIC DIY graffiti (DiyGraffitiV2.g); 0xA3, H61A8 commByte 0x03
    11:
      id: graffiti_v2_wide
      doc: RGBIC DIY graffiti, big-data form (DiyGraffitiV2.h); code 0x03
    12:
      id: color_strip
      doc: per-segment colour strip; 0xA3 commByte 0x40
    13:
      id: bulb_diy
      doc: bulb DIY (DiyProtocol.toBytes); 0xA3 commByte 0x04
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
      doc: '[r g b] (AbsMultipleControllerV14ColorStrip)'
    1:
      id: brightness
      doc: '[brightness] (the brightnessSet groups)'
    2:
      id: kelvin
      doc: '[kelvin u16be][r g b] (AbsMultipleControllerColorTemStrip)'
    3:
      id: gradient
      doc: '[r g b x 3] (AbsMultipleControllerGradientColorTemStrip)'
