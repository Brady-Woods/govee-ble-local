# Firmware cross-validation of the Govee BLE spec

This document validates the spec (the `.ksy` files, `GOVEE_BLE_GATT_PROTOCOL.md`, `devices.yaml`)
against **device firmware** — the other end of the wire. The rest of the spec is derived from the
decompiled **Govee Home Android app** (the phone → device direction). This pass adds an independent
oracle: the **device-side firmware**, reverse-engineered from the OTA images, which shows what the
hardware actually parses.

**Method.** Three sources, with a strict precedence for what the *library/spec* should encode:

1. **APK Java** (`govee-apks/decomp-7.7.02/…`) — authoritative for what the app sends (the spec's
   existing rule: match the app). Citations are `Class.method`.
2. **Device firmware** (`govee-apks/firmware-*/decompiled/…`) — an independent confirmation of what
   the device accepts/parses. It can *corroborate* a spec claim or *flag* a suspect one. A
   firmware-only behaviour (something the device answers but the app never sends) is **supported in the
   spec**, marked firmware-sourced and cited to the firmware handler (image + function/address), per
   project rule 6 (CLAUDE.md). The app stays the standard wherever it covers the same thing, and an
   extension never changes a frame the app sends. Firmware handlers with side effects (the `66 01`
   test entry, the `AA FF 08` ageing-flag read, `33 46` on H60A1/H60A6) are documented with a caution
   and are not normal reads (§2, §3, §7).
3. **The spec** — the derived artifact under test.

Firmware provenance (Ghidra, ARM Cortex-M Thumb). The five RTL8720CF images are AmebaZ2 images loaded
via `ltchiptool`-recovered section maps, with flash XIP at `0x9B00_0000` and SRAM at `0x10000480`.
H6047 is a flat image at `0x0100_0000`, with RAM code `0x20000bb0` loaded from file `0x2f39c`:

| SKU | SoC | Dispatcher | Decomp |
|---|---|---|---|
| H6047 | ARM Cortex-M (Thumb-2) BLE SoC, BlueX/RivieraWaves stack per strings (not Telink) | `FUN_01005ce8` | `firmware-h6047/decompiled/H6047_decompiled.c` + notes |
| H6006 | RTL8720CF | `FUN_9b01f0d0` | `firmware-h6006/decompiled/H6006_ble_dispatcher.c` |
| H6008 | RTL8720CF (VU2) | `FUN_9b0244f0` | `firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c` |
| H5083 | RTL8720CF | `FUN_9b01ee34` | `firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c` |
| H60A1 | RTL8720CF | `FUN_9b025af4` | `firmware-h60a1-wifi/decompiled/H60A1_ble_dispatcher.c` |
| H60A6 | RTL8720CF | `FUN_9b02687c` | `firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c` |

On the five RTL images a shared `_handle_write_msg` layer runs ahead of the per-SKU dispatcher in this
table. It does the BCC check and, except on H5083, the length check, answers the info reads and
handles head `0x66` (§1–§3).

(All firmware work is static analysis; none was confirmed on live hardware.)

---

## 1. Frame model — CONFIRMED on 6 devices

Every image implements the spec's §4.1 frame; the per-image differences are listed below the table:

| Spec claim | Firmware evidence |
|---|---|
| 20-byte application frame | The RTL images reject non-20-byte frames (`0x14` = 20), with per-image exceptions (`0xA5` mic frames; on H6008, H60A1 and H60A6, `0x3F` frames whose length is a multiple of 20; on H5083 the shared layer checks no length, so every head it handles passes at any length). H6047 does not length-check `0x33`/`0xAA` frames. Detail below. |
| `[proType][cmd][payload…][BCC]` | byte[0] = proType, byte[1] = command id, payload from byte[2] — identical across all six. |
| **BCC = XOR of bytes 0..18 → byte[19]** | Byte-identical loop in every device: e.g. H6008 `@0x9b0245f8` starts from 0, XORs in bytes 0..18 and stores the result in byte 19; H60A6 `FUN_9b053f04` over the frame with length `0x13` (19); matches `govee_reference.bcc()` and `Controller4Aes.c()` (which starts from byte 0 and XORs in bytes 1..18). |
| proType `0x33` write / `0xAA` read | Both present in every dispatcher. On H6047 both go into one table and each handler decides (below). |
| multi-packet `0xA1` / `0xA3` | `0xA3` on H6006/H6008/H60A1/H60A6 (and in H5083's shared layer). `0xA1` on H5083/H60A1/H60A6, and on H6006/H6008 only as the `A1 11 …` Wi-Fi-provisioning multi-package. Both are device-accepted multi frames (spec `govee_ble_frame` `mpm_packet` / `a3_packet`). Detail below. |
| 20-byte reply | Reply built and sent as `0x14` bytes: H6006 `FUN_9b03d3e4(payload,0x14)`, H6008 `FUN_9b03cffc(…,0x14)`, H60A6 `FUN_9b04b8ac(…,0x14)`. |

Per-image detail for the table:

- **Length check.** The RTL images reject non-20-byte frames, except as listed below (the `A5 02` mic
  frames that H6006/H6008 catch before any check, and the per-image cases).
  - H6006: `_system_ble_event_handler` (`0x9b03de60`) sends `A5 02 …` of any length to
    `ble_msg_group_test` (`0x9b03df50` → `0x9b03cf34`). Every other write goes to the shared
    `_handle_write_msg` (`0x9b03d750`), which tests the frame length against `0x14` (20) at `0x9b03d7a6`,
    and the dispatcher repeats that length test.
  - H6008: the event handler `0x9b03d830` catches `A5 02` the same way (`0x9b03d8a2` → `0x9b03cc64`).
    The shared write handler (`0x9b03d4e8`) then skips the length and BCC checks only for
    byte[0]==`0xA5` (`0x9b03d56c`) and allows `0x3F` frames whose length is a multiple of 20
    (`0x9b03d576-84`). The dispatcher `FUN_9b0244f0` tests only the frame length against `0x14` (`0x9b02451e`,
    `Invalid len %d`) and has no `0xA5` branch, so other `A5` frames are dropped.
  - H60A1/H60A6: the `group_pro` heads `0xB0`–`0xB5`/`0xBF` go to their handler first (H60A1
    `0x9b04665c-6a` → `0x9b038108`, H60A6 `0x9b04c258-66` → `0x9b03d350`). Any other frame is rejected
    when its length is not `0x14` (20), except `0xA5` (and `0xA4` on H60A6) and a `0x3F` frame whose
    length is a multiple of 20 (H60A1 `0x9b04667e-8c`, H60A6 `0x9b04c280-8e`).
  - H5083: the dispatcher tests the frame length against `0x14` (20). The shared layer has no length
    check and walks a `0x3F` write in len / 20 units (`FUN_9b03289c`); a shorter write that passes the
    shared BCC check on bytes 0..19 walks no unit and is answered with status 1.
  - H6047 has no length check on `0x33`/`0xAA` frames. It zero-fills a 0x100-byte buffer, copies 20
    bytes of whatever was written (`0x01005cfc-0x01005dc4`) and relies on the XOR BCC (`0x01005fca`).
    Only its `0xB0–0xBF` `group_pro` path checks len == 20 (`0x010105e0`), and its `0xA5` mic frame is
    variable-length.
- **`0x33` / `0xAA` on H6047.** The dispatcher sends both into one table (`0x01005fe4-0x01006032`) and
  each handler decides. For example, `0x06`, `0xa5` and `0xb1` answer only `0xAA`, while `0x07`,
  `0x14`, `0x20`, `0x21` answer either frame type (a `33` request gets a `33`-headed reply). For a
  `0x33` frame (except cmds `01`/`09`/`60`/`33`/`b2`), H6047 first runs `multi_ble_exit_group_control`
  (`0x01011ec0`).
- **`0xA1` / `0xA3`.**
  - H6006/H6008 accept `0xA1` only as the `A1 11 …` Wi-Fi-provisioning multi-package (`MULTI_WIFI` =
    0x11). On H6006 it is handled in the shared layer (`_handle_write_msg` `0x9b03d856` →
    `ble_msg_receive_mult_package` `0x9b03cd20`, which compares byte[1] with `0x11` at `0x9b03cd22`). On
    H6008 it is handled in the dispatcher (`FUN_9b0244f0` `0x9b024b86` → `FUN_9b02444c`, the same `0x11`
    compare). An `A1` frame with any other byte[1] is dropped with no reply.
  - H5083 takes both in its shared write layer, ahead of the dispatcher. `A1 11 …` carries the Wi-Fi
    credentials (`ble_msg_receive_mult_package` `H5083.bin@0x9b031c2c`); a successful END is acked
    `A1 11 00`. `A3` frames whose reassembled payload type is `0x08` are IoT/MQTT-topic provisioning
    (`0x9b032962`); the status comes later as `A3 08 <status>` (`send_iot_status` `0x9b031860`). Every
    `A3` frame is also forwarded to `FUN_9b01ee34`, which drops it. (The H5083 dispatcher's own `0xA1`
    branch is the DIY multi-package, §2.)

**Device-side nuance (not a spec change): the incoming BCC is checked.** The per-SKU dispatchers only
compute and stamp the BCC on the reply; they do not re-check it on input. On all five RTL images the
shared `_handle_write_msg` layer in front of them does check it. A frame whose XOR(bytes 0..18) differs
from byte 19 is logged `check pkg code:%d` and dropped with no reply; it never reaches the dispatcher:

- H6006 `H6006.bin@0x9b03d7ec` → `0x9b03c28e`. Every frame is checked, and frames that are not 20
  bytes are dropped first.
- H6008 `@0x9b03d590` → `0x9b03c48c`, after decryption.
- H5083 `@0x9b0328fe` → `0x9b031528`, on the decrypted frame. Every frame is checked, and the mismatch
  is also logged as `bcc:0x%2x, data[19] = %d`.
- H60A1 `@0x9b0466ca` → `0x9b044ba4`.
- H60A6 `@0x9b04c2cc` → `0x9b049fc0`.

Exempt from the check: proType `0xA5` (H6008/H60A1/H60A6), `0xA4` (H60A6) and the OTA heads
`0xB0`–`0xB5`/`0xBF` (H60A1/H60A6; H6008 drops these heads before the check, `0x9b03d554-6a`). On
H6006/H6008 the `A5 02` mic frames are taken by the BLE event handler before this layer, so they are
never checked (§3). The H6047 BLE image also validates incoming BCC: XOR of bytes 0..18 against byte
19 at `0x01005fca-0x01005fe2`; a mismatch is dropped with `check pkg error`. The app always appends a
correct BCC (`BleUtils.o`), so spec/library behaviour is unchanged. A bad-BCC write gets no reply.

---

## 2. Opcode inventory — per device, vs the spec `command` enum (`govee_common.ksy`)

Command bytes (byte[1]) each per-SKU dispatcher branches on, from the decompiled handlers. On the RTL
images these are not all the opcodes the device answers. A shared `_handle_write_msg` layer runs first
and answers `AA 06`, `AA 07`, `AA 14`, `AA 20`, `AA 21` and `AA EF` itself:

- H6006 `@0x9b03d830` → `0x9b03ccb4`
- H6008 `@0x9b03d5ac` → `0x9b03c9b8` (no `EF`)
- H5083 `@0x9b03291a` → `0x9b031bc0`
- H60A1 `@0x9b046714` → `0x9b045610`
- H60A6 `@0x9b04c316` → `0x9b04aa38`

`AA 06` returns the version as ASCII from byte 2 (H5083 `ble_msg_get_version` `0x9b0318dc`). `AA 07` is
`ble_msg_get_dev_info`, which writes its data from byte 3 and answers only these selectors:

- H6006 (`0x9b03cb88`) and H5083 (`0x9b031b08`): `02`/`03`/`06`.
- H6008 (`0x9b03c92c`): `02`/`03`/`06`, plus `0A`/`0B` with no data.
- H60A1/H60A6 (`@0x9b0454bc` / `@0x9b04a8e4`): `02`/`03`/`06`/`0A`/`0B`.

A frame the layer does not answer, including `AA 07` with any other selector (for example `04`, `10`
or `11`), is forwarded to the dispatcher with `cb(0x1008, buf, len)`. The H6006/H6008/H5083
dispatchers drop a forwarded `AA 07 xx` with no reply; H60A1/H60A6 echo it unchanged (`D60A1:573-585`,
`D60A6:614-623`). On H5083 these answers do not depend on pair state, but come only after the
`E7 01`/`E7 02` session exchange. H60A6 also answers proType `0xAC` in this layer
(`ble_msg_one_to_more` `@0x9b04c73e` → `ble_check_basic_info` `0x9b04bdd8`). Reply layouts, and which
of these reads the app never sends, are in §7.

| SKU | Opcodes dispatched (byte[1]) |
|---|---|
| H6047 | `01 04 05 06 07 09 0e 0f 11 12 14 20 21 23 33 34 35 36 37 38 40 60 a3 a5 a6 a8 a9 ac b1 b2 ee ef ff` |
| H6006 | Dispatcher `FUN_9b01f0d0`: `01 04 05 09 11 12 23 ab ff` (0x33: `01 04 05 09 11 12 23 ff`; 0xAA: `01 04 05 11 12 23 ab ff`). Ahead of it, the shared `_handle_write_msg` (`0x9b03d750` → `0x9b03ccb4`) answers 0xAA `06 07 14 20 21 ef` itself. |
| H6008 | Dispatcher `FUN_9b0244f0`: `01 04 05 09 11 12 23 24 25 46 ab ff` (0x33: `01 04 05 09 11 12 23 24 25 46 ff`; 0xAA: `01 04 05 11 12 23 ab ff`). `17` is not a byte[1] opcode: it is a byte[2] sub-type of `33 FF` (`0x9b024b20`). Ahead of the dispatcher, the shared write handler (`0x9b03d4e8` → `0x9b03c9b8`) answers 0xAA `06 07 14 20 21` (no `ef`). |
| H60A1 | `00 01 04 05 09 11 12 23 24 25 46 a3 a5 ab b1 b2 ff` |
| H60A6 | `00 01 04 05 09 11 12 23 24 25 30 41 42 46 a3 a5 ab b1 b2 ff` |
| H5083 | Dispatcher `FUN_9b01ee34`: `0xAA` → `01 12 13 b0 b1 b3`; `0x33` → `01 13 15 b0 b2 b5 ff`; `0xA1` DIY multi (byte[1] `02` is checked only on the `FF` END frame). Pair-state gated (below). No `04`/`05` handler. Smart-plug vocabulary; `0x02` is not an AA/33 opcode. Ahead of the dispatcher, the shared ble_msg write layer (`H5083.bin@0x9b03291a` → `0x9b031bc0`) answers 0xAA `06 07 14 20 21 ef` (`07` answers selectors `02 03 06` only). |

Cross-check against the spec `command` enum:

- **Every RGB-light opcode above is in the enum** *except* two, which the firmware exposed and the
  app's own catalog confirms are real (`0x00` is covered separately below):
  - **`0xAB`** — `BleProtocolConstants.MULTI_READ_AB` / `SINGLE_DYNAMIC_API_SUPPORT` (-85). Present on
    all four RGB combos (H6006/H6008/H60A1/H60A6). **→ added to the `command` enum** as
    `dynamic_api_support`.
  - **`0x25`**: same value as `BleProtocolConstants.SINGLE_PRE_SET_COLOR` (37) and present on
    H6008/H60A1/H60A6, but on these three it is not a preset colour. It stops the pairing light effect
    that `0x24` starts:
    - H60A1/H60A6: `33 25 01` posts device message `0xB6` `MSG_STOP_PAIR_MODE` (H60A1 `FUN_9b027004`,
      H60A6 `FUN_9b027d94`; handled in `_mqtt_response_handle`, H60A6 `@0x9b028f36`), which ends temp
      mode `0x24`.
    - H6008: `33 25` logs `msg_set_dev_lamp_stop %d` (`FUN_9b029de8`).

    The app names the pair `value_set_light_start` (36) / `value_set_light_end` (37). It sends `33 24`
    from its connect/bind steps (`SetLightStartController`, from `StepServiceBindSpecial` /
    `StepInitLightEffect`); no app sender of `33 25` was found for these three SKUs (the bulblight
    `VM4Light.i5` sender of `33 25 01`, `govee_commands.py` `build_set_light_end`, is gated by
    `Support.c` to other goodsTypes). `SINGLE_PRE_SET_COLOR` is the meaning
    for other families (H6057 `PreSetColorController`).
    **→ in the `command` enum**, first as `preset_color`; the clean spec names it `light_effect_stop`
    (its firmware meaning, with `-orig-id` `SINGLE_PRE_SET_COLOR` / `value_set_light_end`) and `0x24`
    `light_effect_start`. The H6008/H60A1/H60A6 meaning is firmware-sourced.
- `0x00` on H60A1/H60A6 is an explicit no-op case, not the default branch (tbh entry 0 → H60A1
  `0x9b02607e`, H60A6 `0x9b026b20`). The frame is sent back: AA is echoed, 33 gets status 0. The default
  branch instead logs `msg type error` and sets status 1. The app name is `SINGLE_HEART_BEAT` (0). It
  is the app's heartbeat on these SKUs: `ShareVM.I3()` is false, so `ConnectHeartbeat.c` gives `0x00`
  for argument 2 and `BleComm.l` sends a `Controller4HeartCommon` for byte `0x00`, i.e. `AA 00`.
- H5083's `0x15` is the plug timer-delete write: `33 15` → internal msg `0xb5` MSG_SET_DEL_TIMER via
  `FUN_9b01f7a2`, which requires payload[0] = 0 and payload[1] ≤ 9. It is the app's h5080
  `timer_delete`. `0x02` is **not** an AA/33 opcode on H5083. The dispatcher compares byte[1] with 2
  only on an `0xA1` END frame (`A1 02 FF …`, "set muti package diy mode parameter",
  `H5083.bin@0x9b01f296`), i.e. `MULTI_DIY` = 2. Not a light-protocol gap; noted here, not added to the
  shared enum.
- **H5083 pair-state gate (firmware-sourced).** The H5083 dispatcher's opcodes are gated on pair state,
  byte `0x10000654` (read by `FUN_9b021acc`). It is zeroed on every connect and disconnect, set to 1 by
  `AA B1` and set to 2 by a matching `33 B2` (`0x9b01ef8a`). Below 2, only `AA 01`, `AA B1`, `33 B2`
  and `33 FF` are served; everything else is dropped with no reply. The shared-layer answers above do
  not depend on pair state.

Timers `0x23`, secret `0xb1`/`0xb2` and zone `0x30` (H60A6) are **confirmed present** with the enum's
meaning in the firmware that supports those features. On H6008/H60A1/H60A6, the images that dispatch
it, `0x24` is not an IC read, and on H60A1/H60A6 neither is `0x46`:

- `0x24` is the pairing light effect `[index, u16be countdown]`. H6008 logs `index = %d countdown = %d`
  and starts `pair_effect_thread` (`FUN_9b029d70`). H60A1/H60A6 store it at `0x1001d4c8` / `0x1001d7e0`
  and call `govee_device_start_tmp_mode(0x24)`. The app sender is `SetLightStartController`.
- On H60A1/H60A6, `0x46` is a factory reset: a `33 46` ack, then `Govee_GatewayConfig_Reset`,
  `HAL_Matter_Reset` and a reboot. The app name is `SINGLE_RESET_DEVICE_NET` = 70. **Caution:** a
  client that sends `33 46` to these SKUs as an IC check factory-resets them. H6008's `0x46`
  (`FUN_9b02c0bc`) was not traced.

Neither H60A1 nor H60A6 dispatches `0x40` (in the table above only H6047 lists it). Info `0x06`/`0x07`,
the Wi-Fi reads `0x14`/`0x20`/`0x21` and `0xEF` are absent from the RTL dispatcher rows because the
shared `_handle_write_msg` layer answers them first (above).

---

## 3. Discrepancies investigated — resolved in the spec's favour

The firmware showed three things the spec's `pro_type` enum did not list. Each was re-grounded in
the APK Java. Two are not app-emitted frames and stayed out of the enum then (match-the-app; the clean spec
names `0x66` as `factory_test`, CAUTION); the third, `0xA5`,
turned out to be a real app frame family the enum was missing (now added as `mic`, §5):

- **byte[0] == `0xA5` (length-exempt frame) — CORRECTED: this IS an app frame family.** H60A1/H60A6/H6047
  accept a frame whose byte[0] is `0xA5` and skip the 20-byte length rule. H6006/H6008 handle it one
  layer earlier. Their BLE event handler sends every `A5 02 …` write, whatever its length, to
  `ble_msg_group_test` before the length and BCC checks (H6006 `0x9b03df50` → `0x9b03cf34`; H6008
  `0x9b03d8a2` → `0x9b03cc64`). Byte 2 `0x83` (`MicSetRgbController`) applies bytes 3..5 as R,G,B
  through the colour-apply routine (H6006 `0x9b0264b8` → `0x9b023804`; H6008 `0x9b029638` →
  `0x9b0277ac`). Byte 2 `0x90` (`MicColorCmdController`) queues an internal message. The additive
  checksum is not checked and no reply is sent. (The H60A1/H60A6 handler, `group_ctrl_rec_ble_pkg`, is
  in §6.) An earlier draft of this note said the app never emits proType `0xA5`; that was wrong. The
  app's mic-colour framing uses it: `AbsMicController.getProType()` returns -91, i.e. `0xA5`
  (`base-classes10/com/govee/base2light/ble/mic/controller/AbsMicController.java:55-56`; the frame builders at
  `:23` and `:43` also put -91 (`0xA5`) in byte 0), and it is the base of `MicSetRgbController`, `MicColorCmdController`
  and the newdetail `MicController`. So the firmware's length-exempt `0xA5` path is the device side of the app's
  mic-colour push. **Spec:** the `pro_type` enum (`govee_common.ksy`) has `0xA5` as `mic`, and the frame
  reads it on routes `mic_tx` / `mic_rx` (`mic_command` / `mic_reply`, checksum_kind sum8). (This is
  distinct from *command* `0xA5` `color_group`, which is sent under proType `0xAA` as `AA A5 …`; the
  handshake is `0xE7`, below.)
- **byte[0] == `0x66`: an echo on H6047, a factory-test stub on the RTL images.**
  - H6047: an echo. The dispatcher (`0x010064be-0x010064d8`) exits group control and sends the frame
    (byte 1 + 17 payload bytes, new XOR) back to the writer through the BLE send routine `0x010167a0`.
    Nothing goes to the UART. On H6047, Razer enter/exit arrives as UART message `0x30` (`0x0100bd7c`);
    BLE set commands (`33 01` / `33 05` / `33 36`, via `0x0100bba4`) only cancel Razer mode and post
    `razer update` to the UART peer.
  - RTL images (all five): the shared `_handle_write_msg` routes head `0x66` to a test sub-dispatcher.
    `66 01` → `ble_msg_enter_test`, which logs `start` and replies `66 01 00…00 BCC`
    (`set_response_data` zeroes bytes 2..18, status 0). `66 02` returns 0 and is echoed. Any other
    `66 xx` is forwarded to the dispatcher (and dropped there on H6006/H6008/H5083). Sites: H6006
    `@0x9b03d866` → `0x9b03ccf2` → `0x9b03cc60`; H6008 `@0x9b03d5e6` → `0x9b03c9ee` → `0x9b03c998`;
    H5083 `@0x9b032956` → `0x9b031bfe` (`66 01` → `0x9b031ba0`, logs `DBG:start`, reply via
    `set_response_data` `0x9b0318b4`); H60A1 `@0x9b046740` → `0x9b04564e`; H60A6 `@0x9b04c342` →
    `0x9b04aa76`.

  No app-side `0x66` sender exists; every `razer`/`chroma` hit in the APK is UI/graffiti
  (`CellGraffitiDrawTools`, `GuideView`). **Caution:** on the RTL images this is a factory-test hook.
  `ble_msg_enter_test` was traced on H60A6 to log `start` and reply only, with no mode change, and it
  returns no device state; the library must still not send it, even under rule 6 (§7.3). Device-side
  only. It was left out of `pro_type` at first; the clean spec names it `factory_test` (FIRMWARE-SOURCED,
  CAUTION) and reads a captured `66` frame as `factory_test_request` / `factory_test_reply`.
- **Handshake proType.** The spec already says `0xE7` (`GOVEE_BLE_GATT_PROTOCOL.md §9.1`). My firmware
  H6047 note had guessed `0xA5`. The APK is decisive: `Controller4Aes.g()` accepts the reply iff
  `byte 0 == -25` and `byte 1 == 1` — `-25` = **`0xE7`**. **The spec was right all along**; this
  corrects the *firmware note*, and the spec MD needs no change.

---

## 4. Payload bodies validated against firmware

Where a handler body was decompiled (not just the dispatcher), the payload layout matches the spec:

- **`0x05` mode / colour (H6006).** `FUN_9b020cd0`→`FUN_9b0208de` parse the sub-mode payload;
  `FUN_9b023804` applies colour as R/G/B × brightness plus a colour-temperature term with Kelvin base
  `0x0a8c = 2700`. This matches the spec's `color_0d_write` "both-slot" form and the 2700 K floor
  (`KelvinConfig`) — **confirmed**.
- **Multi-packet reassembly (`0xA3`, H6006/H60A1/H60A6).** H6006's reassembler is `FUN_9b020d2c`. H60A1
  and H60A6 reassemble inline in the dispatcher (`D60A1:184-337`, `D60A6:739-940`). Both track the
  package index and log `lost some mult package` / `Wrong mult package index` on loss — the same
  staged reassembly the spec's `a3_packet` / §4.3 model and `reassemble_ac` assume. **Confirmed.**
- **`aa 05` mode-report reply bodies (H6006, H6008) — firmware, then hardware-confirmed.** Both
  dispatchers build the reply from the current sub-mode (`pcVar[2]`): sub-mode `0x0D` =
  `[0D][R][G][B][kelvin hi][kelvin lo]` with everything after it zero — **no tint bytes** (H6006
  `H6006_ble_dispatcher.c` case `0xd` at 634, bytes [3..7] at 645-649; H6008 `H6008_ble_dispatcher.c` case
  `0xd` at 396, [3..5] at 401-403, kelvin at 404-405). Kelvin is pre-zeroed, so 0 most likely means RGB mode.
  Other bodies, same on both: sub-mode `0x05` = `[00][x][y][z]`; sub-mode `0x0a` = a u32 little-endian at
  bytes 3..6 (H6006 587-601 / 617-632; H6008 371-379 / 386-394). **Hardware** (bench run 2026-10-15 by the
  parallel review session, macOS, real devices): H6006 after a 2700 K write read back `aa 05 0d ff ae 54 0a 8c
  00…` — in CCT mode the R/G/B bytes carry the 2700 K tint colour (`ffae54`) and kelvin is `0x0a8c` = 2700;
  after an RGB (16, 32, 48) write it read back `aa 05 0d 10 20 30 00 00 00…` (kelvin 0). The trailing tint
  bytes were `00` every time. H6008 (an encrypted link) returned the same `0x0d` layout. H6052 has no BLE
  firmware in this set (its OTA image is Wi-Fi only).
- **`0xA3` short upload (H6006) — firmware, then hardware-confirmed.** The device reassembler
  `FUN_9b020d2c` (`firmware-h6006/decompiled/H6006_a3_reassembler.c`) stores frame byte 3 of START as the
  packet count (START, middles and END all counted), and accepts the `0xFF` END only as the last frame,
  always copying 17 bytes from END bytes 2..18. So a two-frame START + empty END is accepted. **Hardware:**
  the same bench run sent H6006 `a3 00 01 02 01 83 01 ff e1 96 32 00 02 14 32 fa 64 32 fa eb` (START, packet
  count `02`) followed by an empty `a3 ff 00 … 5c` END; the device acked `a3 01 00` and the next mode read
  reported scene `0x046e`.
- Other opcode handler *bodies* (timers `0x23`, secret `0xb1/b2`, per-segment colour `0xa5`/`0x40`,
  zone `0x30`) were confirmed at the **dispatch/inventory** level but not re-traced byte-for-byte on
  every device (H60A1/H60A6 have since been traced; see §6); the spec's layouts for these remain
  APK-grounded (their `file:line` citations stand).

---

## 5. Spec changes made in this pass (spec/ only)

- `command` enum (then in `govee_ble.ksy`, now `govee_common.ksy`): added `0x25` (`preset_color`, now
  `light_effect_stop`), `0xab dynamic_api_support`
  (`BleProtocolConstants.SINGLE_PRE_SET_COLOR` / `SINGLE_DYNAMIC_API_SUPPORT`, firmware-confirmed as
  dispatched; on H6008/H60A1/H60A6 `0x25` stops the `0x24` pairing light effect instead, §2).
- `govee_reference.py`: extended with `build_*` / `read_*` / `parse_*` helpers for the control
  opcode set (framing layer), each round-tripped through the generated `.ksy` reader in the self-test.
- `pro_type` enum: `0xA5` (app mic-colour framing, §3) is in the enum as `mic` (`govee_common.ksy`).
  Its firmware side is `ble_msg_group_test` on H6006/H6008 (§3) and `group_ctrl_rec_ble_pkg` on
  H60A1/H60A6 (§6). `0x66` is not added: on the RTL images it is a factory-test stub that returns no
  state (§3), the app has no sender, and its purpose beyond that is unknown; if it is ever added it is
  firmware-sourced (rule 6); the clean spec later added it that way, as `factory_test` (CAUTION), with
  the `factory_test_request` / `factory_test_reply` bodies. The handshake is already `0xE7`. No
  library (`src/`) change. Device-side notes (the incoming-BCC check in the RTL shared layer, which
  drops bad-BCC frames while the per-SKU dispatchers do not re-check; the `0x66` test stub) live in
  this file; the main MD already documents the handshake correctly.

## 6. Open / not validated by firmware

- H5083 (SW1.00.30) is now traced in full, as data: `../firmware.yaml` image `h5083_sw1_00_30`
  (every answer, state field, timer and push, with citations). `0x02` is not an AA/33 opcode and
  `0xb4` is not dispatched.
- Per-device handler bodies for `0x23`/`0xb1`/`0xb2`/`0xa5`/`0x40`/`0x30`. These are now traced on
  H60A1/H60A6 (see the corrected headers of `H60A1_ble_dispatcher.c` / `H60A6_ble_dispatcher.c`).
  Neither image dispatches `0x40`, and only H60A6 dispatches `0x30`. The other RTL images are still
  confirmed by inventory only.
- The `0xA5` length-exempt frame — resolved on H6006/H6008/H60A1/H60A6 (H5083 was not checked):
  - H6006/H6008: it is the app's mic-colour push (§3). `A5 02 83 R G B sum` sets the colour;
    `A5 02 90 …` is also handled; neither gets a reply. The app sends it from the bulbs' control
    screen: `BulbLightApplication.create` registers `DelegateOnlyMic` for `Support4BulbLightV3.a`
    (goodsTypes 31/64/111/342/348/408), and `DelegateOnlyMic:63` → `DelegateMicPickup:1081` builds
    a `MicSetRgbController` with the payload `[r, g, b]`.
  - H60A1/H60A6: it is `group_ctrl_rec_ble_pkg` (H60A1 `FUN_9b037458`, H60A6 `FUN_9b03c6b0`). Layout:
    - Length = byte 19, which `_ble_service_msg_handler` stamps there for short writes.
    - Additive checksum (sum of bytes 0..len-2) in byte len-1.
    - Type at byte 2. `0x83` = RGB push (bytes 3..5), which switches to mode `0xA0` (read back as
      sub-mode `0x13`). `0x90` = handshake, answered with the 5-byte frame `A5 02 10 01 <sum>`.

    This is the device side of the app's `AbsMicController` frames `[A5][02][type][data][sum]`:
    `MicSetRgbController` type `-125` = `0x83`, `MicColorCmdController` type `-112` = `0x90` (§3).

---

## 7. Firmware-sourced reads

> **Firmware-sourced.** The handlers and reply layouts in this section come from the device firmware,
> not the app, and are static analysis only: none of it was confirmed on hardware. §7.1 lists reads the
> app never sends; §7.2 lists reads it sends only while pairing. Under project rule 6 these may be
> supported in the spec and library, each marked firmware-sourced and cited to the handler given
> here. The app stays the standard wherever it covers the same thing. The entries in §7.3 are test
> hooks or have side effects: they are documented with a caution and are **not** normal reads. §7.4
> lists survey entries that turned out to be app-sent.

Images: H6047 BLE HW3.02.01 SW1.00.12, H6006 SW1.00.59, H6008 SW1.01.25, H5083 SW1.00.30, H60A1
SW1.02.20, H60A6 SW1.00.41. In the RTL replies below, request bytes the handler does not write come
back unchanged and byte 19 is the re-stamped XOR. H6008 replies are re-encrypted unless the halfword
at `0x1000068a` is `0x2FC`.

### 7.1 Reads the app never sends

- **`AA 07 06` — BLE MAC address.**
  - Handlers: H6006 `_handle_write_msg` `0x9b03d840` → `0x9b03ccb4` → `ble_msg_get_dev_info`
    `0x9b03cb88` → `ble_msg_get_ble_macaddress` `0x9b03c988`. H6008 `0x9b03d5ba` → `0x9b03c9b8` →
    `0x9b03c92c` (TBB idx 4) → `0x9b03c7ec`. `ble_msg_get_ble_macaddress` on H60A1 `0x9b0452b0` and
    H60A6 `0x9b04a6d8` (tbb selector 6). H5083 `ble_msg_get_ble_macaddress` `0x9b0319cc` via
    `ble_msg_get_dev_info` `0x9b031b08`. H6047 `0x01005370` (cmd `0x07`), selector `06` →
    `0x010053ce` → `0x0101693c` (RivieraWaves `nvds_get(NVDS_TAG_BD_ADDRESS)` shape).
  - Reply: `aa 07 06` + the 6-byte BLE MAC at bytes 3..8. The byte order differs per image:
    byte-reversed relative to the device's `XX:XX:XX:XX:XX:XX` string on H6006/H6008/H60A1/H60A6; in
    the string's order on H5083 (forward parse `0x9b031576`); on H6047 in the getter's order (likely
    LSB-first), the same 6 bytes as uid bytes 0..5 of its `AA 07 02` reply (payload length 7). On
    H60A1/H60A6 a decode that does not fit returns -1001, and the frame is forwarded and echoed. H5083
    answers only after the `E7 01`/`E7 02` session but does not need the `33 B2` pair state.
  - App: none. decomp-7.7.02 has no controller with command `0x07` and selector `6`
    (`SPPMacController` is `AA 42 06`).
- **`AA 07 0A` / `AA 07 0B` — MCU software / hardware version (no data).**
  - Handlers: H6008 `0x9b03c92c` TBB idx 8 → `0x9b03c958` (`ble_msg_get_sw_mcu_version`), idx 9 →
    `0x9b03c966` (`ble_msg_get_hw_mcu_version`). H60A1 `0x9b045210` / `0x9b045260`; H60A6
    `0x9b04a638` / `0x9b04a688`.
  - Reply: the request echoed with byte 19 re-stamped. The handlers only log and return 0; nothing is
    written. H6006 does not answer (default branch, forwarded, dropped), nor do H5083 (only `02`/`03`/`06`,
    §2) and H6047 (`sel−2 > 4`, no reply, `0x01005396`).
  - App: `McuSoftVersionControllerV1` (`[0A]`) / `McuHardVersionControllerV1` (`[0B]`) exist but are
    sent only from the generic connect step `ReadControllerFactory.b()` (`StepReadInfo:211`), which is
    not on these SKUs' add flows or control screens.
- **`AA EF` — advertising protocol type (constants).**
  - Handlers: `ble_msg_get_adv_pro_type` on H6006 `0x9b03cc00` (sub-dispatcher `0x9b03ccb4`,
    the `0xEF` compare at `0x9b03ccd2`), H60A1 `0x9b045558`, H60A6 `0x9b04a980`; H5083 `0x9b031b80`. All log
    `adv protocol data is %d %d %d`. H6008 does not answer (forwarded, dropped, no reply).
  - Reply: `aa ef 00 01 01` on H6006/H60A1/H60A6 and `aa ef 00 02 02` on H5083, then request bytes
    5..18 and the XOR. Read the app's way (`PactController.parseValidBytes`): pactType = u16be(bytes
    2–3), pactCode = byte 4.
  - App: `PactController` (`SINGLE_PACT` = -17) exists, sent from `AbsBlePact.o()` (old adjust flows;
    on h5080 only via adjust/v1 `BlePactV1` under `AdjustAc`, goodsType != 43) and skubusiness
    `BlePactChecker`; kt `ControllerProtocol` serves H5086. None of these is on the
    H6006/H6008/H5083/H60A1/H60A6 control paths; H60A1/H60A6 take pactType/pactCode from the
    advertisement (`BleBroadcastProcessor:33-34`). The library already builds the request
    (`govee_commands.py read_pact()`).
- **`AB 01 04` — Matter GID read (proType `0xAB`, H5083).**
  - Handler: shared write handler `0x9b032c7a` (a compare against 1, `DBG:Get matter req`) → `0x9b0327f0`
    selector 4 (`DBG:MATTER_GID_INFO`) → `0x9b0326f0` / `0x9b0326ac`.
  - Reply: AB-framed (byte 0 `0xAB`, XOR at 19), single or multi-packet, carrying `[04][gid ASCII]`.
    First frame `AB 00 <frame count u16 LE> <len> 01 <13 data bytes>`; continuations
    `AB <idx, or FF for the last> <17 data bytes>`. The handler then returns -1, so no extra echo is
    sent. `AB 01 01/02/03/05/06` are consumed with no reply; other selectors are forwarded and dropped.
  - App: `Controller4GidRead` (base-classes12 `one2multi`, `AbsAbReadController` proType -85, cmd 1,
    ext `[04]`) exists, but no H5083 caller was traced.
- **`33 07 ss` — device-info read with a `0x33` header (H6047; ss = `02`/`03`/`04`/`06`).**
  - Handler: `0x01005370`, which does not gate on frame type. Cmd `07` is not exempt, so the dispatcher
    first calls `multi_ble_exit_group_control` `0x01011ec0` (at `0x0100601c`): this read also exits
    group control.
  - Reply: the same bodies as `AA 07 ss`, with header `33 07` (the send passes `0x33` as the header at
    `0x010053be`).
  - App: none; the app always reads with `0xAA`.
- **`33 14` / `33 20` / `33 21`, and the deferred `aa 14` / `aa 20` / `aa 21` (H6047).**
  - Handlers: `0x010050dc` (`14`), `0x01005140` (`20`), `0x0100510e` (`21`), not gated on frame type,
    → `0x0100b7d8` (selectors 3/1/2). Deferred sender `0x0100b4fc` (UART message `0xa8`, UART table
    `0x0102c93c`).
  - Reply: immediately `<proType> <cmd>` + 17 bytes (`20`/`21`: a 7-byte version field + zeros; `14`:
    the 17-byte field at `+0x10`). If the Wi-Fi module has not reported yet (the flag at `+0x24` is not 1), there is no
    reply now: UART event `0xc8` is posted and the selector is kept at `+0x25`. Later an unsolicited
    `aa <cmd> …` arrives, always with an `0xAA` header; only the last pending selector is kept.
    `+0x24` is never cleared and boot code (`0x0100235e`) pre-requests the data, so the deferred case
    only arises if the phone reads before the module's first report. A `33` form also first runs
    `multi_ble_exit_group_control` (§1).
  - App: sends the `AA` forms on H6047 (`PairAcV1:257` via `WifiMacController` /
    `WifiHardVersionController` / `WifiSoftVersionController`; detail connect via `Compose4InfoBleIot`
    `ControllerWifi*`). Only the `0x33` forms and the deferred reply are firmware-only.
- **`AA 01` reply byte 3 — group-control flag (H6047).**
  - Handler: `0x01004f32`, query path `0x01004fb0-0x01004fd4`.
  - Reply: `aa 01 [power 0/1] [power ? group-control-active flag *(0x20005370+6) : 0] 00.. BCC`.
    `multi_ble_exit_group_control` (`0x01011ec0`) clears the flag.
  - App: sends `AA 01 00` (`SwitchController` / `HeartController`) but decodes only the power byte;
    byte 3 is firmware-only data.
- **`AA 30` — zone state (H60A6).**
  - Handler: per-SKU dispatcher `0x30`, AA branch (`D60A6:228-233`): byte 2 = `FUN_9b02b464`
    (UserConfig+9), byte 3 = `FUN_9b02b498` (UserConfig+0xA).
  - Reply: `aa 30 <zone0 byte> <zone1 byte>`, then the rest echoed and the XOR. The app decodes bit 0
    of each byte (`VM4LightH60A6…$1.o:103-104`). H60A1 has no `0x30` handler (default branch, echoed).
  - App: no single-frame read. H60A6 gets the same values from `AC 03` TLV `0x30`
    (`ble_check_basic_info` uses the same getters, `0x9b04c0ae` / `0x9b04c0b6`), so the app's `AC`
    path stays the standard and this is only a firmware-sourced fallback.
- **`AA 41` — power-on (start) mode (H60A6).**
  - Handler: dispatcher `0x41` non-33 branch (`D60A6:476-481`, `0x9b0270b8`): byte 2 = `02`, byte 3 =
    `FUN_9b02bfe4` (UserConfig+4); logs `Get start mode %d`.
  - Reply: `aa 41 02 <start mode>`, then the rest echoed and the XOR. This matches `AC 03` TLV `0x41`
    `[02][mode]` and the write `33 41 02 <v>`. H60A1 lands in default and echoes.
  - App: writes it (`Controller4OnOffMemory`, `CommOpV1:1833`) and reads it from `AC 03` TLV `0x41`
    (`VM4LightH60A6…$1.o:99-100`); there is no single-frame read. Fallback only.

### 7.2 Reads the app sends only while pairing

The app sends these in the add/pair flow only, not from the control screen. The firmware answers them
at any time (H5083: after the `E7` session), so under rule 6 they are firmware-supported reads on the
control path. `AA 07 02` is rule 6's own example.

- **`AA 07 02` — device id.** H6006 `ble_msg_get_sn` `0x9b03c87c` (via `0x9b03cba0`); H6008
  `0x9b03c740` (TBB idx 0); H5083 `ble_msg_get_sn` `0x9b031924` (via `0x9b031b08`). Reply:
  `aa 07 02` + the id decoded from the colon-separated uuid string and stored byte-reversed from byte 3. On
  H6006/H6008 that is (strlen(uuid)+1)/3 bytes (8 for the 23-char uuid the slot allows); a decode
  longer than 16 bytes returns -1001 and gets no reply. On H5083 it is 8 bytes at 3..10 (uuid string
  at `0x1000b140`, filled from the end by `0x9b0315b8`). App: `SnController` / `SnControllerV2`, sent
  only while pairing (H6006/H6008: `ReadControllerFactory.c()`, not the gt-64 control-screen list
  `h0`; H5083: h5080 `PairAcV1.t2`, `add/PairAcV1.java:652`).
- **`AA 06` — software version (H5083).** `ble_msg_get_version` `0x9b0318dc`. Reply: `aa 06` + ASCII
  version from `HAL_Ota_GetSWVersion` `0x9b02b1c8` (`%d.%02d.%02d`, e.g. `1.00.30`) at bytes 2.., at
  most 17 bytes, no NUL written. App: `SoftVersionController` in h5080 `PairAcV1.t2` (add flow) only,
  not the H5083 control screen.
- **`AA 07 03` — hardware version (H5083).** `ble_msg_get_hw_version` `0x9b031984` via `0x9b031b08`.
  Reply: `aa 07 03` + ASCII `1.02.00` (constant at `0x9b809cc7`) at bytes 3..9, no NUL. App:
  `HardVersionController`, add flow only.
- **`AA 14` / `AA 20` / `AA 21` — Wi-Fi MAC / hardware / software version (H5083).**
  `ble_msg_get_wifi_mac_address` `0x9b031a24`, `ble_msg_get_wifi_hwversion` `0x9b031a78`,
  `ble_msg_get_wifi_swversion` `0x9b031ac0`. Reply: `aa 14` + the 6-byte Wi-Fi MAC, forward order, at
  bytes 2..7; `aa 20` + ASCII at byte 2, same source as `AA 07 03`; `aa 21` + ASCII at byte 2, same
  source as `AA 06` (on this single-SoC image the Wi-Fi versions are the main versions). App:
  `WifiMacController` / `WifiHardVersionController` / `WifiSoftVersionController`, add flow only.
- **`AA B3` — spec byte (H5083).** Dispatcher `FUN_9b01ee34` `0x9b01efbc` → `0x9b01f132`, pair state 2
  only. Reply: `aa b3 00` + request bytes 3..18 + XOR; the spec byte is always `00`. Unpaired, the frame
  is dropped (`pair state is false`). App: h5080 `SpecController` (cmd -77), add flow only.

### 7.3 Caution: test hooks and side effects

- **`AA FF 08` — ageing-flag read. Caution: test-only; do not send it in normal operation.**
  - H60A1/H60A6: per-SKU dispatcher `0xFF`, AA branch with byte 2 == `08`: H60A1 `D60A1:497-505`
    (`FUN_9b02ca34` `_factory_getAgingFlag`), H60A6 `D60A6:662-672` (`FUN_9b02de4c`
    `_factory_getAgingFlag`). Reply `aa ff 08 <aging flag>`, then the rest echoed and the XOR.
    **Side effect:** if the flag reads 0, the device also runs `FUN_9b0313dc` + `FUN_9b037884` with 0
    (H60A1) or `FUN_9b033c3c` + `FUN_9b03cadc` with 0 (H60A6; the latter stores 0 to `0x1001fee9`).
  - H6047: `0x010059a4` (cmd `0xff`), query path (proType != `0x33`) → `0x01006dac`. Reply
    `aa ff 08 [1 if the flash word at 0x79400 != 0xffffffff, else 0] 00.. BCC`; any other selector
    gets 17 zero bytes. No side effect was traced on this path, but what flash `0x79400` holds is not
    determined, so treat it as test-only too. Only the `0xAA` form is a read: `33 FF …` is H6047's set
    path, which stops the running device service (`0x01002568`).
  - App: no sender (no command-`0xFF` controller with ext `[08]`).
- **`66 01` / `66 02` (proType `0x66`) — factory-test entry. Caution: do not expose it in the library,
  even under rule 6.** All five RTL images; the sites are in §3. `66 01` → `ble_msg_enter_test`, reply
  `66 01 00×17 <XOR>` (`set_response_data` zeroes bytes 2..18, status 0); `66 02` is echoed; any other
  `66 xx` is forwarded (dropped on H6006/H6008/H5083). App: no sender. Listed for completeness only.
  (H6047's `0x66` is an echo, §3.)
- Writes with side effects are documented where the opcode is: `33 46` is a factory reset on
  H60A1/H60A6 (§2).

### 7.4 In the firmware survey, but not firmware-only

- H6047 `AA 07 04` (7-byte ASCII software version; `0x01005370` selector `04` → `0x010053ca`). The app
  defines and sends `07 04` on H61D3 (`split_h61d3` `PairViewModel:187` → `PairToDeviceInfoReadController`
  → `SoftHardController.Companion.e` with ext `[04]`), so it is app-sourced, with H6047 as firmware corroboration.
  H6047's own pairing and connect paths read `AA 06` instead.
- H6006/H6008 `AA 14` / `AA 20` / `AA 21`: live callers on gt 64 (`ControllerStrategyConfig.h0:634-654`).
  Handlers: H6006 `0x9b03ca0c` / `0x9b03ca90` / `0x9b03cb0c`; H6008 `0x9b03c844` / `0x9b03c89c` /
  `0x9b03c8e4`. Reply: `aa 14` + the Wi-Fi MAC, forward order, at bytes 2..7; `aa 20` = the `AA 07 03`
  string (H6006 `1.02.00`, H6008 `1.07.02`); `aa 21` = the `AA 06` string.
- H6047 `AA 14` / `AA 20` / `AA 21`: app-sent (only the `33` forms and the deferred reply are in §7.1).
- H6006/H6008 `A5 02 83` / `A5 02 90`: the app's mic-colour push, live on gt 64 (§3, §6).
