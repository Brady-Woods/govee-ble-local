# Firmware cross-validation of the Govee BLE spec

This document validates the spec (`govee_ble.ksy`, `GOVEE_BLE_GATT_PROTOCOL.md`, `devices.yaml`)
against **device firmware** — the other end of the wire. The rest of the spec is derived from the
decompiled **Govee Home Android app** (the phone → device direction). This pass adds an independent
oracle: the **device-side firmware**, reverse-engineered from the OTA images, which shows what the
hardware actually parses.

**Method.** Three sources, with a strict precedence for what the *library/spec* should encode:

1. **APK Java** (`govee-apks/decomp-7.7.02/…`) — authoritative for what the app sends (the spec's
   existing rule: match the app). Citations are `Class.method`.
2. **Device firmware** (`govee-apks/firmware-*/decompiled/…`) — an independent confirmation of what
   the device accepts/parses. It can *corroborate* a spec claim or *flag* a suspect one, but a
   firmware-only behaviour (something the device accepts but the app never sends) is **documented, not
   added to the spec** — the library models the app.
3. **The spec** — the derived artifact under test.

Firmware provenance (Ghidra, ARM Cortex-M Thumb; AmebaZ2 images loaded via `ltchiptool`-recovered
section maps — flash XIP at `0x9B00_0000`, SRAM at `0x10000480`):

| SKU | SoC | Dispatcher | Decomp |
|---|---|---|---|
| H6047 | Telink (BlueX/RivieraWaves) | `FUN_01005ce8` | `firmware-h6047/decompiled/H6047_decompiled.c` + notes |
| H6006 | RTL8720CF | `FUN_9b01f0d0` | `firmware-h6006/decompiled/H6006_ble_dispatcher.c` |
| H6008 | RTL8720CF (VU2) | `FUN_9b0244f0` | `firmware-h6008-wifi/decompiled/H6008_ble_dispatcher.c` |
| H5083 | RTL8720CF | `FUN_9b01ee34` | `firmware-h5083-wifi/decompiled/H5083_ble_dispatcher.c` |
| H60A1 | RTL8720CF | `FUN_9b025af4` | `firmware-h60a1-wifi/decompiled/H60A1_ble_dispatcher.c` |
| H60A6 | RTL8720CF | `FUN_9b02687c` | `firmware-h60a6-wifi/decompiled/H60A6_ble_dispatcher.c` |

(All firmware work is static analysis; none was confirmed on live hardware.)

---

## 1. Frame model — CONFIRMED on 6 devices

Every dispatcher implements the spec's §4.1 frame exactly:

| Spec claim | Firmware evidence |
|---|---|
| 20-byte application frame | Each dispatcher rejects non-20-byte frames: H6006 `if (*param_1 != 0x14)`, H6008/H60A1/H60A6 `(*frame != 0xa5) && (len != 0x14)`, H5083 `if (*param_1 != 0x14)`. `0x14` = 20. |
| `[proType][cmd][payload…][BCC]` | byte[0] = proType, byte[1] = command id, payload from byte[2] — identical across all six. |
| **BCC = XOR of bytes 0..18 → byte[19]** | Byte-identical loop in every device: e.g. H6008 `LAB_9b0245f8` `b=0; for(p;p!=buf+0x13;) b^=*p; buf[0x13]=b`; H60A6 `FUN_9b053f04(frame,0x13)`; matches `govee_reference.bcc()` and `Controller4Aes.c()` (`b ^= bArr[i]`, i=1..18). |
| proType `0x33` write / `0xAA` read | Both present and distinguished in every dispatcher. |
| multi-packet `0xA1` / `0xA3` | `0xA3` on H6006/H6008/H60A1/H60A6; `0xA1` on H5083/H60A1/H60A6 — both are device-accepted multi frames (spec `multi_a1` / `multi_a3`). |
| 20-byte reply | Reply built and sent as `0x14` bytes: H6006 `FUN_9b03d3e4(payload,0x14)`, H6008 `FUN_9b03cffc(…,0x14)`, H60A6 `FUN_9b04b8ac(…,0x14)`. |

**Device-side nuance (not a spec change):** the Realtek devices *compute and stamp* the BCC on the
reply but do **not reject** an incoming frame with a wrong BCC — they lean on the BLE link-layer CRC.
The Telink H6047 validates incoming BCC. The app always appends a correct BCC (`BleUtils.o`), so the
spec/library behaviour is unchanged; this only means a bad-BCC write is tolerated by the RTL parts.

---

## 2. Opcode inventory — per device, vs the spec `command` enum

Command bytes (byte[1]) each dispatcher branches on, from the decompiled handlers:

| SKU | Opcodes dispatched (byte[1]) |
|---|---|
| H6047 | `01 04 05 06 07 09 0e 0f 11 12 14 20 21 23 33 34 35 36 37 38 40 60 a3 a5 a6 a8 a9 ac b1 b2 ee ef ff` |
| H6006 | `01 04 05 09 11 12 23 ab ff` |
| H6008 | `01 04 05 09 11 12 17 23 24 25 46 ab ff` |
| H60A1 | `00 01 04 05 09 11 12 23 24 25 46 a3 a5 ab b1 b2 ff` |
| H60A6 | `00 01 04 05 09 11 12 23 24 25 30 41 42 46 a3 a5 ab b1 b2 ff` |
| H5083 | `01 02 12 13 15 b0 b1 b2 b3 b5 ff` (plug/hygrometer class — different opcode vocabulary) |

Cross-check against the spec `command` enum:

- **Every RGB-light opcode above is in the enum** *except* two, which the firmware exposed and the
  app's own catalog confirms are real:
  - **`0xAB`** — `BleProtocolConstants.MULTI_READ_AB` / `SINGLE_DYNAMIC_API_SUPPORT` (-85). Present on
    all four RGB combos (H6006/H6008/H60A1/H60A6). **→ added to the `command` enum** as
    `dynamic_api_support`.
  - **`0x25`** — `BleProtocolConstants.SINGLE_PRE_SET_COLOR` (37). Present on H6008/H60A1/H60A6.
    **→ added to the `command` enum** as `preset_color`.
- `0x00` on H60A1/H60A6 is the default/no-op branch (no `BleProtocolConstants` name); left undocumented.
- H5083's `0x02` and `0x15` are **plug/hygrometer-class** opcodes, not the shared light catalog
  (`BleProtocolConstants` reuses value `2` for several unrelated constants, e.g. `MULTI_DIY`). Not a
  light-protocol gap; noted here, not added to the shared enum.

All other enum entries (timers `0x23`, IC reads `0x24`/`0x40`/`0x46`, secret `0xb1`/`0xb2`, zone
`0x30`, info `0x07`, etc.) are **confirmed present** in the firmware that supports those features.

---

## 3. Discrepancies investigated — resolved in the spec's favour

The firmware showed three things the spec's `pro_type` enum does not list. Each was re-grounded in
the APK Java. Two are not app-emitted frames and stay out of the enum (match-the-app); the third, `0xA5`,
turned out to be a real app frame family the enum is missing:

- **byte[0] == `0xA5` (length-exempt frame) — CORRECTED: this IS an app frame family.** H60A1/H60A6/H6047
  accept a frame whose byte[0] is `0xA5` and skip the 20-byte length rule. An earlier draft of this note
  said the app never emits proType `0xA5`; that was wrong. The app's mic-colour framing uses it:
  `AbsMicController.getProType()` returns `(byte) -91` = `0xA5`
  (`base-classes10/com/govee/base2light/ble/mic/controller/AbsMicController.java:55-56`; the frame builders at
  `:23` and `:43` also write `bArr[0] = -91`), and it is the base of `MicSetRgbController`, `MicColorCmdController`
  and the newdetail `MicController`. So the firmware's length-exempt `0xA5` path is the device side of the app's
  mic-colour push. **Spec gap:** `govee_ble.ksy`'s `pro_type` enum has no `0xA5`. (This is distinct from *command*
  `0xA5` `local_color_read`, which is sent under proType `0xAA` as `AA A5 …`; the handshake is `0xE7`, below.)
- **byte[0] == `0x66` (Razer/Chroma UART passthrough, H6047 only).** No app-side Razer BLE frame
  exists — every `razer`/`chroma` hit in the APK is UI/graffiti (`CellGraffitiDrawTools`, `GuideView`).
  Device-side only; left out of `pro_type`.
- **Handshake proType.** The spec already says `0xE7` (`GOVEE_BLE_GATT_PROTOCOL.md §9.1`). My firmware
  H6047 note had guessed `0xA5`. The APK is decisive: `Controller4Aes.g()` accepts the reply iff
  `bArrB[0] == -25 && bArrB[1] == 1` — `-25` = **`0xE7`**. **The spec was right all along**; this
  corrects the *firmware note*, and the spec MD needs no change.

---

## 4. Payload bodies validated against firmware

Where a handler body was decompiled (not just the dispatcher), the payload layout matches the spec:

- **`0x05` mode / colour (H6006).** `FUN_9b020cd0`→`FUN_9b0208de` parse the sub-mode payload;
  `FUN_9b023804` applies colour as R/G/B × brightness plus a colour-temperature term with Kelvin base
  `0x0a8c = 2700`. This matches the spec's `color_cct_0d` "both-slot" form and the 2700 K floor
  (`KelvinConfig`) — **confirmed**.
- **Multi-packet reassembly (`0xA3`, H6006/H60A1).** The device's reassembler (`FUN_9b020d2c` et al.)
  tracks a package index and emits `lost some mult package` / `package index` on loss — the same
  staged reassembly the spec's `multi_a3` / §4.3 model and `reassemble_ac` assume. **Confirmed.**
- **`aa 05` mode-report reply bodies (H6006, H6008) — firmware, then hardware-confirmed.** Both
  dispatchers build the reply from the current sub-mode (`pcVar[2]`): sub-mode `0x0D` =
  `[0D][R][G][B][kelvin hi][kelvin lo]` with everything after it zero — **no tint bytes** (H6006
  `H6006_ble_dispatcher.c` case `0xd` at 618, bytes [3..7] at 629-633; H6008 `H6008_ble_dispatcher.c` case
  `0xd` at 383, [3..5] at 388-390, kelvin at 391-392). Kelvin is pre-zeroed, so 0 most likely means RGB mode.
  Other bodies, same on both: sub-mode `0x05` = `[00][x][y][z]`; sub-mode `0x0a` = a u32 little-endian at
  bytes 3..6 (H6006 571-585 / 601-616; H6008 358-366 / 373-381). **Hardware** (bench run 2026-10-15 by the
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
  every device; the spec's layouts for these remain APK-grounded (their `file:line` citations stand).

---

## 5. Spec changes made in this pass (spec/ only)

- `govee_ble.ksy` `command` enum: added `0x25 preset_color`, `0xab dynamic_api_support`
  (`BleProtocolConstants.SINGLE_PRE_SET_COLOR` / `SINGLE_DYNAMIC_API_SUPPORT`, firmware-confirmed).
- `govee_reference.py`: extended with `build_*` / `read_*` / `parse_*` helpers for the control
  opcode set (framing layer), each round-tripped through the generated `.ksy` reader in the self-test.
- `pro_type` enum: `0xA5` (app mic-colour framing, §3) is a real app frame family the enum is missing —
  to be added on this branch. `0x66` stays out (device-only; no app sender). The handshake is already
  `0xE7`. No library (`src/`) change. Device-side notes (BCC RX-tolerance, `0x66`) live in this file; the
  main MD already documents the handshake correctly.

## 6. Open / not validated by firmware

- Exact payload bodies of the non-RGB H5083 opcodes (`0x02`, `0x13`, `0x15`, `0xb0–b5`) — H5083 is a
  different device class; its command vocabulary would need its own pass.
- Per-device handler bodies for `0x23`/`0xb1`/`0xb2`/`0xa5`/`0x40`/`0x30` (confirmed by inventory, not
  byte-traced on each device).
- The `0xA5` length-exempt device frame's purpose (OTA? legacy? dead code?) — needs more RE or a
  capture; it is not reachable from the app's normal control path.
