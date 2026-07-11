# Govee Bluetooth LE / GATT Protocol Reference

> Reverse-engineered from the decompiled Govee Home Android app (`com.govee.*`, JADX output).
> This document describes the BLE GATT transport, packet framing, command catalog,
> encryption handshake, OTA, and per-device specifics for all supported devices found
> in the APK. Obfuscated field names are noted where relevant; class references use the
> path under `sources/`.

---

## Table of Contents

1. [Overview & architecture](#1-overview--architecture)
2. [GATT profile — services & characteristics](#2-gatt-profile--services--characteristics)
3. [Connection lifecycle](#3-connection-lifecycle)
4. [Packet framing](#4-packet-framing)
5. [Command catalog (opcodes)](#5-command-catalog-opcodes)
6. [Common command byte layouts](#6-common-command-byte-layouts)
7. [The mode / sub-mode system](#7-the-mode--sub-mode-system)
8. [Notifications (device → app push)](#8-notifications-device--app-push)
9. [Encryption & session-key handshake](#9-encryption--session-key-handshake)
10. [OTA firmware update](#10-ota-firmware-update)
11. [Sensor / thermo-hygrometer profile](#11-sensor--thermo-hygrometer-profile)
12. [Per-device reference matrix](#12-per-device-reference-matrix)
13. [Implementation notes & class map](#13-implementation-notes--class-map)
14. [Validation notes (vs. a live implementation)](#14-validation-notes-vs-a-live-implementation)
15. [Dynamic feature-module (pact split) devices](#15-dynamic-feature-module-pact-split-devices)
16. [SubMode & device write-layout reference](#16-submode--device-write-layout-reference)
17. [Conventions, ABNF grammar & machine-readable artifacts](#17-conventions-abnf-grammar--machine-readable-artifacts)
18. [Device identification, protocol selection & pairing](#18-device-identification-protocol-selection--pairing)
19. [Advertisement / manufacturer-data structure](#19-advertisement--manufacturer-data-structure)
20. [Timing & reliability](#20-timing--reliability)
21. [Expanded command catalog](#21-expanded-command-catalog)

---

## 1. Overview & architecture

Govee BLE devices speak a **custom application protocol layered on top of GATT**. Almost all
control traffic flows over a **single vendor GATT service** using **20-byte packets** with a
1-byte XOR checksum. The app never uses standard SIG profiles for control; the service and
characteristic UUIDs are Govee/Telink-specific.

The Android code is organized in three layers:

| Layer | Package | Responsibility |
|-------|---------|----------------|
| **Transport** | `com.govee.ble` | GATT connect/scan/reconnect, MTU, notification enable, raw write. Singleton `BleController`. |
| **Encryption** | `com.govee.encryp.ble` | Optional AES / AES-GCM session handshake wrapping every write. |
| **Application** | `com.govee.base2light.ble` (+ per-device `com.govee.<model>.ble`) | Command controllers, packet building, mode/scene/DIY logic, OTA. |

Key transport facts (`sources/com/govee/ble/BleController.java`):
- Single shared `BleController` instance (`getInstance()`), one active connection at a time.
- Connect timeout 60 s (`f99385p`), service-discovery overtime 180 s (`f99397j`), auto-reconnect window 15 s (`f99387r`).
- Writes go through `BleController.L(serviceUUID, charUUID, byte[])` → `BleCommImp.sendMsg(...)` → `EncryptWriter.encryptWriteValue(...)`.

---

## 2. GATT profile — services & characteristics

### 2.1 Primary control service (used by nearly all lighting devices)

The service **`00010203-0405-0607-0809-0a0b0c0d1910`** exposes three characteristics whose
suffixes differ only in the last byte (`2b10`/`2b11`/`2b12`):

| Role | UUID | Notes |
|------|------|-------|
| **Control service** | `00010203-0405-0607-0809-0a0b0c0d1910` | Default for all lights. `AbsBle.java:103` |
| **Write characteristic** | `00010203-0405-0607-0809-0a0b0c0d2b11` | Command channel (write, usually no-response). This is the *write* target: `AbsBle.z()` (`AbsBle.java:800`), `EncryptionManagerV2$writeBytes$2:54`. `AbsBle.java:106` |
| **Notify characteristic** | `00010203-0405-0607-0809-0a0b0c0d2b10` | Device→app notifications arrive here (confirmed against live captures). In the APK this UUID is a declared-but-unreferenced constant (`encryp/ble/Constants.java:20`, getter `c()`), because the app enables notifications on **every** characteristic of the service rather than naming this one — see [§3](#3-connection-lifecycle). |
| **BGC-info characteristic** | `00010203-0405-0607-0809-0a0b0c0d2b12` | Read for `encryptVersion` / broadcast-capability (BGC) info — **not** OTA. `BgcInfoReader.java:67,72` reads char `2b12` on service `1910`. Distinct from the OTA `2b12` under service `1912` ([§2.2](#22-ota-services)). |
| **CCCD (descriptor)** | `00002902-0000-1000-8000-00805f9b34fb` | Standard Client Characteristic Configuration; written `0x01 0x00` to enable notify. `BleCommImp.java:29` |

Characteristic properties (Bluetooth SIG GATT terms):

| Characteristic | Properties | Security | Descriptors |
|----------------|-----------|----------|-------------|
| `…0d2b11` (write) | Write, Write Without Response | None (app-layer AES optional, §9) | — |
| `…0d2b10` (notify) | Notify | None | CCCD `2902` |
| `…0d2b12` (BGC info) | Read, Notify | None | CCCD `2902` |

> **Correction (was: "write+notify share `2b11`").** Static analysis shows `2b11` is the *write*
> target and the app subscribes notifications on *all* characteristics of the service, so it never
> names the notify characteristic. A live implementation confirms notifications are delivered on
> the separate **`2b10`** characteristic. Treat `2b10` = notify, `2b11` = write.

The write/notify pair is also used for the **encryption handshake** (`EncryptionManager.java:98-101`).

### 2.2 OTA services

| Role | UUID | Used by |
|------|------|---------|
| OTA V1 service (Telink-style) | `00010203-0405-0607-0809-0a0b0c0d1912` | `OtaManager.java:24` |
| OTA V1 characteristic | `00010203-0405-0607-0809-0a0b0c0d2b12` | `OtaManager.java:27` |
| OTA V2 service (TI OAD) | `F000FFC0-0451-4000-B000-000000000000` | `OtaManagerV2.java:31` |
| OTA V2 char — image block/identify | `F000FFC1-0451-4000-B000-000000000000` | `OtaManagerV2.java:34` |
| OTA V2 char — image control | `F000FFC2-0451-4000-B000-000000000000` | `OtaManagerV2.java:37` |

> **Note on the reused `2b12` suffix.** The characteristic `…0d2b12` appears under **two different
> services**: under the **OTA service `…0d1912`** it is the OTA data characteristic (this table);
> under the **control service `…0d1910`** it is the BGC-info / `encryptVersion` read characteristic
> ([§2.1](#21-primary-control-service-used-by-nearly-all-lighting-devices)). They are distinct
> `(service, characteristic)` endpoints despite the shared characteristic UUID.

### 2.3 Alternate UART service (a few devices)

| Role | UUID | Used by |
|------|------|---------|
| Service | `0000ffe0-0000-1000-8000-00805f9b34fb` | **H6104**, **H6101** |
| Characteristic | `0000ffe1-0000-1000-8000-00805f9b34fb` | (generic Telink/TI "transparent" UART) |

These devices use the **same 20-byte framing** but on the generic `ffe0/ffe1` service instead
of the vendor `1910/2b11` service.

### 2.4 Sensor / thermo service ("INTELLI_ROCKS")

Thermo-hygrometers, BBQ thermometers, and history-logging gateways use a second Telink
service whose 128-bit UUID is ASCII text:

| Role | UUID | ASCII | Used for |
|------|------|-------|----------|
| Service | `494e5445-4c4c-495f-524f-434b535f4857` | `INTELLI_ROCKS_HW` | TH / BBQ / history |
| Write char | `…-434b535f2011` | `INTELLI_ROCKS_ ` `\x20\x11` | commands |
| Notify char | `…-434b535f2012` | `INTELLI_ROCKS_ ` `\x20\x12` | telemetry |
| Chart request | `…-434b535f2014` | `INTELLI_ROCKS_ ` `\x20\x14` | history dump request |
| Chart notify | `…-434b535f2015` | `INTELLI_ROCKS_ ` `\x20\x15` | history dump data |

See [§11](#11-sensor--thermo-hygrometer-profile).

---

## 3. Connection lifecycle

Implemented in `BleController` + `AbsBluetoothGattCallback` (`sources/com/govee/ble/`).

```
Scan (ScanManager / BleScanCallbackImp21)
   │  match by MAC / advertised name / service data
   ▼
connectGatt(device, callback)              BleConnectImp.connectBle()
   ▼
onConnectionStateChange(STATE_CONNECTED)   AbsBluetoothGattCallback.h()
   ▼
gatt.discoverServices()                    (fires "discoveringService" event)
   ▼
onServicesDiscovered(GATT_SUCCESS)         → BTGattConnectEvent.connectedSuc
   ▼
enable notifications on ALL chars of svc     BleCommImp.c()/d()
   ├─ for each characteristic of service 1910 (2b10, 2b11, 2b12):
   ├─ setCharacteristicNotification(true)
   └─ write CCCD 0x2902 = ENABLE_NOTIFICATION_VALUE {0x01,0x00}
       (so device pushes on 2b10 are captured without naming it)
   ▼
(optional) requestMtu(N)                    BleController.J(int)  → onMtuChanged
   ▼
(optional) encryption handshake             §9
   ▼
CONNECTED — ready for commands. Heartbeat runnable polls to keep alive.
```

Details:
- **Notification enable has two paths** (`BleCommImp.sendMsg`): a "slow" path that writes the
  CCCD descriptor per characteristic (`c()`, `BleCommImp.java:45`), and a "fast-connect" path that
  only calls `setCharacteristicNotification` without descriptor writes (`d()`), selected per
  firmware version via `ShortMemoryMgr` broadcast-version cache. **Both iterate over *every*
  characteristic of the control service** — so `2b10`, `2b11`, and `2b12` are all subscribed. This
  is why the app never references the `2b10` notify characteristic by name; a leaner client should
  subscribe specifically to **`2b10`** for inbound notifications and write to `2b11`.
- **MTU**: default 20/23 bytes. `BleController.J(mtu)` calls `requestMtu`. The encryption layer
  tracks the negotiated MTU (`EncryptionManager.f115473c`, default 20) and splits ciphertext
  accordingly. BLE 5 / 2M PHY support is probed (`BleController.P()`).
- **Heartbeat / keep-alive**: `AbsHeartRunnable` periodically issues a heart command
  (`SINGLE_HEART = 0x01`) so the device does not auto-disconnect. Auto-disconnect status
  `19` triggers `EventAutoDisconnect`.
- **Reconnect**: `ReconnectInfo` retries within the 15 s window; skips reconnect while the app
  is backgrounded unless flagged.

---

## 4. Packet framing

All application packets are **exactly 20 bytes** unless the negotiated MTU is larger and a
device opts into MTU-sized writes. The last byte is always an **XOR checksum (BCC)**.

### 4.1 Checksum (BCC)

`checksum = data[0] ^ data[1] ^ … ^ data[18]`, stored in `data[19]`.

Reference implementations:
- `BleUtils.v(packet, 19)` (`sources/com/govee/base2kt/utils/BleUtils.java:1239`)
- `MultiPackageManager.b(bArr, 19)` (`sources/com/govee/ble/multi/MultiPackageManager.java:56`)

### 4.2 Single-command packet (`0x33` / `0xAA`)

Built by `BleUtils.generate20Bytes(proType, commandType, payload)` (`BleUtils.java:1369`),
used by every `AbsSingleController` (`AbsSingleController.java:20-32`).

```
byte  0        1             2 .. 18                    19
    ┌────────┬───────────┬───────────────────────────┬──────────┐
    │ proType│ commandType│ payload (≤17 bytes)        │ checksum │
    └────────┴───────────┴───────────────────────────┴──────────┘
```

| `proType` | value | meaning |
|-----------|-------|---------|
| `SINGLE_WRITE` | `0x33` | write command (app → device) |
| `SINGLE_READ`  | `0xAA` (−86) | read/query command |
| `NOTIFY`       | `0xEE` (−18) | unsolicited push frame (device → app), also "OTA prepare" |
| `SINGLE_WRITE_READ` | `0x3A` | write-and-read variant |
| BBQ status notify | `0xAB` (−85) | BBQ thermometer telemetry push |

- `proType` is chosen by `AbsSingleController.getProType()`: `write ? 0x33 : 0xAA`.
- On the response, the device echoes `proType`/`commandType`. For writes, success is indicated
  by `value[2] == 0` (`AbsSingleController.t()`).
- Read responses: the app strips bytes `[2..18]` (17 bytes) as the "valid" payload
  (`parseValidBytes`). Write responses strip `[3..18]` (16 bytes).

**`0xAA` (read) payloads are NOT the `0x33` write payloads.** A read *request* is `AA <cmd> <selector>` —
the byte after the command is a per-command **selector/index**, not the write field
(`AbsSingleController.f()` → `generate20Bytes(0xAA, cmd, p())`, `p()` default empty). Source-confirmed
selectors: **mode `AA 05 01`** (`AbsModeController.p()={1}` — the `0x01` is a **selector, not a
sub-mode**); **device-info `AA 07 <sel>`** (`0x02` serial, `0x03` hw, `0x04` sw, `0x10` basic, `0x11`
wifi — a controller per selector). ⚠️ **Source vs hardware:** `SwitchController`/`BrightnessController`
don't override `p()`, so the `base2light` source emits `AA 01 00` / `AA 04 00` (empty), **not** the
`AA 01 01` / `AA 04 01` some captures show — the trailing `0x01` for switch/brightness is unverified in
source (firmware may ignore the byte, or a non-`base2light` path builds it).

A read *reply* is `AA <cmd> <device-state…>`, parsed **per-(sub-mode, controller)** by `parseValidBytes`
(2-byte header stripped, 17 bytes routed by sub-mode) — **not** a single form, and it differs from the
write. Notably mode `0x15` (RGBIC CCT):
```
write : 33 05 15 01 FF FF FF <kHi> <kLo> …   (op, WHITE-POINT FF FF FF, then kelvin @ bytes 7-8)
reply : AA 05 15 01 <kHi> <kLo> …            (kelvin @ bytes 4-5, big-endian; NO FF FF FF)  e.g. 01 0A 8C = 2700 K
```
The write builds `[op, R,G,B(=white), kHi,kLo, …]`; the reply parser reads `[op, kHi, kLo]` (kelvin right
after op) — `SubModeColorV2.parse` / `ComposeChange2ColorMode.result`.

Other mode reply sub-modes (traced): **`0x0D`** (h60a6-color) has **no field decode** — the legacy
`SubModeColorV1.parse` reads only the op/gradual bit (`bArr[0]==1`), and both `0x0D`/`0x15` route there on
that stack, so `0x0D` is effectively write-only (its `[R,G,B,kHi,kLo,tR,tG,tB]` write body is never read
back; kelvin comes only from the compose `0x15` path). **Music `0x13`** reply is family-dependent:
H60A6 `SubModeMusicV1.parse` = `[musicCode][value][autoColorFlag][specColorFlag][R][G][B]` (truncates
early for new-music codes / specified-color); the general `SubModeNewMusic.parse` reads only
`[musicCode][value]`; `0x16` `SubModeAbsMusic` = `[u16 count LE][value]`. (No `base2light` class uses
sub-mode `0x11`.) The `.ksy` models `0xAA` via a distinct `read_command` type (`mode_read` →
`cct_read_reply` / `music_read_reply`), not the `single_command` write payloads.

There is also a 3-argument form `generate20Bytes(proType, cmd, subCmd, payload)`
(`BleUtils.java:1363`) placing an extra sub-command byte at index 2 and payload from index 3.

### 4.3 Multi-packet transfer (`0xA1` / `0xA2`)

For payloads larger than a single packet (DIY animations, scene tables, graffiti, up to
**4080 bytes**), `MultiPackageManager` (`sources/com/govee/ble/multi/MultiPackageManager.java`)
fragments the data into 16-byte chunks:

**Write sequence (header `0xA1`, −95):**
```
START:  [0xA1, comType, 0x00, packetCount, 0…, checksum]
DATA_i: [0xA1, comType, i(1-based), <16 data bytes>, checksum]     // repeat
END:    [0xA1, comType, 0xFF, 0…, checksum]
```
~300 ms is inserted between packets (`Thread.sleep(300)`).

**Read sequence (header `0xA2`, −94):**
```
REQUEST: [0xA2, comType, 0x00, 0…, checksum]          // app → device
HEADER:  [0xA2, comType, 0x00, amount, …]             // device → app (chunk count)
DATA_i:  [0xA2, comType, i, <16 data bytes>]          // device → app
END:     [0xA2, comType, 0xFF, …]                     // device → app
```

Result events: `MultiWriteResponse` (success = `bArr[2] == 0`) and `MultiReadResponse`
(reassembled chunks). See `MultiPackageManager.g()` / `j()` / `i()`.

**Other multi-write dialects** (declared in `BleProtocolConstants`, used by newer RGBIC devices
via `MultipleControllerCommV1/V2/V3`):

| constant | value | purpose |
|----------|-------|---------|
| `MULTIPLE_WRITE` / `MULTI_WRITE` | `0xA1` / `0xA3` | primary / V1 |
| `MULTIPLE_WRITE_V1` | `0xA3` (−93) | V1 chunked write |
| `MULTIPLE_WRITE_V2` | `0xA4` (−92) | V2 chunked write |
| `MULTIPLE_READ` | `0xA2` (−94) | chunked read |
| `MULTI_READ_AB` / `MULTI_READ_AC` | `0xAB` / `0xAC` | multi-reply reads |
| `MTU_MULTIPLE_WRITE` | `0xA6` (−90) | single-shot write when MTU is large enough |

> **The `0xA1` layout above is the `MultiPackageManager` dialect only.** Newer RGBIC scene/DIY
> uploads use a **different** `0xA3` frame layout — see §4.4. Do not assume one layout for all
> multi-writes.

### 4.4 Scene / DIY upload dialects (`0xA3`)

Scene tables and new-DIY uploads go through `MultipleControllerCommV1` (the scene/DIY multi-packet
controller), **not** `MultiPackageManager`. The form used by every curated device is `0xA3`
(`makeSendBytesV2/V1`, `:785`/`:781`). A **second builder emits `0xA4` (MTU-sized, `makeSendBytesMtu`)**
with a different byte layout, but it is reached only for new-DIY effects behind a specific parse-byte
gate — see the ⚠️ box below, which is the authoritative statement of when each is used.

> **Choosing upload-vs-activate + the comByte/strip per scene** is a `(sceneType, versionArray)` dispatch.
> The full rule — the `parseSceneV1` table, plus the **two curated-device bypasses** (bulbs force a
> hardcoded `{1}`; H6052 type-5 uses the DIY path at comByte `9`) — is machine-readable at
> `devices.yaml scenes.dispatch` (`table` + `bypasses` + `family_apply`) and documented in
> `SCENE_UPLOAD_ENCODING.md` §0b (per-device paths, Q4 table, implementer checklist).

The `0xA3` frame shape:

```
START:  A3 | 00(seq) | 01 | packetCount | commBytes… | inline value | CK@19
MIDDLE: A3 | seq(1,2,…)   | 17 value bytes            | CK@19
END:    A3 | FF(seq)      | final ≤17 value bytes      | CK@19   (data-bearing)
```
- byte 1 = seq (`0x00` start, `1,2,…` middles, `0xFF` end); byte 2 = literal `0x01`;
  byte 3 = **packetCount** = total frame count (start + middles + end).
- `commBytes` occupies byte 4… (length *L*); the **value begins at byte `4+L`**; START carries
  `15 − L` inline value bytes. For the usual `L = 1`: commByte@4, value@5, 14 inline.
- The `0xFF` END frame **carries the final data chunk** (it is *not* an empty terminator), except
  when the payload happens to end exactly on a frame boundary.

> **Correction (earlier drafts were wrong on two points, proven against H60A6 hardware):**
> (1) byte 4 is a **commByte** that is *not always* a scene-version constant — for the DIY/graffiti
> path it is a **device protocol code**; (2) the value is the controller's `getValue()`, which for
> the DIY/graffiti path is a **re-encoded** payload, not the raw cloud blob.

There are **two `0xA3` dialects**, differing in `commBytes` and how `value` is produced:

| | (A) Legacy generic-scene | (B) DIY / graffiti — H60A6 & newer |
|---|---|---|
| entry | `makeWriteMultipleBytes(AbsMultipleControllerV1)` → commByte = `getCommandType()` (`:902-911`) | `MultiController.f.a(bytes, 88)` (→`0xA3`) **or** `Compose4DefWrite4Multi.d((byte)88,…)` (→`0xA4` MTU) — commByte = device protocol code `88` (see ⚠️ below) |
| **byte 4** | scene-version constant **V1=`0x01`, V2=`0x02`, V3=`0x07`, V6=`0x0C`** (`MULTI_V*_NEW_SCENES`), picked by `sceneType` in `ScenesOp.parseSceneV1` | **device graffiti/DIY protocol code** — H60A6 = **`0x58`** (`H60A6DiyParse.java:468`, `getProtocolCode()=88`) |
| **value** | decoded cloud `scenceParam` (V3/graffiti strips the leading 2 bytes) | effect parsed then **re-serialized** — `H60A6GraffitiParse.toBytes()` = `[0x20, bgR,bgG,bgB, brightness, showType, layerCount, …]`; raw `0x50…` header consumed, so value starts `0x20` |
| routing | `SceneControllerNoEventV1` ← `ScenesOp.parseSceneV1` (`:603`) | `BaseSceneViewMode.handleDiyNewConfig` (~`:1055`) → polymorphic `iDiyParse.makeBleController` (→ `0xA3`; `0xA4` only if effect byte ∈ `{0x5E,0x5D,0x20}`, see ⚠️) |

**Worked H60A6 example** (explains the hardware A/B result): a scene whose `scenceParam` decodes to
`50 20 00 FF…` is a graffiti effect (`byte0=0x50`, parsed by `KmpH60A6DiyProtocol`). The **working**
upload uses dialect **B**:
```
A3 00 01 <count> 58 <20 …re-encoded graffiti…>    # byte4 = 0x58 protocol code; value = toBytes() (starts 0x20)
```
The same scene sent with dialect **A** — a generic comType (`0x02` = `MultiNewScenesControllerV2`)
plus the *raw* blob, `A3 00 01 <count> 02 50 20 …` — is **rejected** (panel off). Note
`0x58 = 0x50 | 0x08` arithmetically, so a client that ORs `0x08` onto the raw blob's `0x50` byte 0
*accidentally* reproduces byte 4 — but the app's mechanism is a **constant protocol code `88` + a
re-encoded value**, not a bitwise OR (there is no `value[0] |= 0x08` in the APK). Where the raw and
re-encoded forms diverge (e.g. value byte 2), the re-encoded form is authoritative.

> ⚠️ **`0xA3` vs `0xA4` (MTU) — mechanism resolved; `0xA3` is the effective form for every curated
> device.** Dialect B has two builders, but which one runs is **gated by the effect's protocol byte, not
> by MTU negotiation** (an earlier draft said "depends on MTU negotiation" — that was wrong):
> - **`0xA3` form** — `MultiController.f.a(bytes, code)` → `makeSendBytesV1((byte) -93, code, bytes)`,
>   and `-93 & 0xFF = 0xA3`; commByte at **byte 4** (the layout above). This is what both the H60A6
>   (`A3 00 01 … 58 …`) and H61A8 (`A3 00 01 10 03 …`) hardware captures matched.
> - **`0xA4` MTU form** — `Compose4DefWrite4Multi.d((byte)88, toBytes(), mtu,…)` →
>   `makeSendBytesMtu((byte) -92, 88, …)`, `-92 & 0xFF = 0xA4`; commByte at **byte 6** (MTU layout).
> - **The gate.** The `0xA4` path is reachable **only** through
>   `Compose4DefWrite4Multi.makeWriteController(b6, b10, …)` (`:196-209`), which requires **`b6 == 88`
>   (command `0x58`) AND `NewDiyEditConfig.isUseMtuCommand(b10) == true`** — and that returns true only
>   for effect bytes **∈ `{0x5E, 0x5D, 0x20}`** (`NewDiyEditConfig.java:57-61, 140-142`). Scene
>   controllers report `useMtuController() == false` (`AbsMultipleControllerV14Scenes:137-140`) and never
>   take it.
> - **Even when `0xA4` is chosen the frame is still 20 bytes unless a larger MTU was *cached*.** Chunk
>   size = `MtuConfig.getAvailableMtuSize = max(getMtu(sku) − 3, 20)`, and `getMtu` defaults to `23 ⇒ 20`
>   (`MtuConfig.java:18,48,62-65`). `MtuConfig` is an app-internal per-SKU store written only by
>   `saveMtuData(…)`; it is **not** the GATT-negotiated ATT MTU. A negotiated 247/512 MTU does not by
>   itself widen these writes.
>
> **Second route to `0xA4` (correction to an earlier draft).** Beyond the `makeWriteController` gate
> above, **`H60A6GraffitiParse.makeBleController` calls `Compose4DefWrite4Multi.d((byte)88, toBytes(), mtu)`
> directly** (`:238`; `isNeedMtuPackage()==true` `:204`), *bypassing* that gate. So **every H60A6
> `sceneType 5` graffiti scene uploads as `0xA4`.** Which type-5 scenes are graffiti is decided by
> **parser order**, not `byte1` alone: `H60A6DiyParse` is tried **first** and wins iff `Pro4H60A6Diy.c`
> parses — `byte0==0x50` **and** `(u16le@bytes[1..2]) + 3 == len` (the DIY length gate) **and** the layers
> parse; only on its failure does `H60A6GraffitiParse` (needs `byte1==0x20`) run. **⇒ on the value
> (`=decode(param)[1:]`): if `u16le@0 + 2 == len` → DIY (`0xA3`); else → graffiti (`0xA4`).** Catalog split:
> **59 graffiti / 14 DIY**. Aurora → graffiti/`0xA4` (gate fails, `35 ≠ 188`); `Halloween B` → DIY/`0xA3`
> *despite* `byte1==0x20` (gate holds, `0x20 + 2 == 34`). dialect-A devices (H6006/H6008/H6047/H61A8/H6641) and
> H6052 type-5 → `0xA3`. So `0xA3` is the default for *most* scenes but is **not universal — H60A6 graffiti
> is `0xA4`.** (The btsnoop showing only `0xA3` reflects DIY-type / non-graffiti captures, not a
> graffiti-scene apply.) Frame *size* is still MTU-gated (20 default; up to 512 if `saveMtuData` cached —
> H60A6 registers 512 support at `NewDetailConfig:751`).

**The three `0xA3` sub-layouts (byte 2 is the discriminator).** All scene/DIY uploads share the `0xA3`
proType, but `MultipleControllerCommV1` has three builders with different header shapes. **Byte 2 tells
them apart** (`0x00` / `0x01` / `0x02`):

| Builder | byte 2 | START header | START inline value | `0xFF` terminator | Chosen when / used by |
|---|:--:|---|---|---|---|
| `makeSendBytesV0` (`:743`) | `00` | `[A3, 00, 00, packs+2, commByte, 0×14]` | **none** — all value in MIDDLE/END | **empty** (bytes 2–18 = 0, *not* data-bearing) | `controller.p() == 0` — rare; **not** the scene/DIY path (`p()` = 1 there) |
| `makeSendBytesV1`/`V2` (`:781`/`:785`) | `01` | `[A3, 00, 01, packetCount, commBytes…]` | `15 − L` bytes at byte `4+L` | **data-bearing** (final chunk) | the common path — **generic scenes + DIY-graffiti** (H61A8, H6047, H6052, bulbs) |
| `makeSendBytesV3` / `K` (`:851`/`:111`) | `02` | `[A3, 00, 02, pktCount, commByte, ctrlNum, ctrlIdx, verify×8 @7]` | **4 bytes** @ byte 15 (after the verify block) | **data-bearing** | multi-controller scenes — `sceneV1.isNeedMulMulPackage()==true` (`BaseSceneViewMode:820`); returns `List<List<byte[]>>`, one START…`FF` group per controller |

- **V1 = V2 with a 1-byte `commBytes`** — `makeSendBytesV1` delegates to `makeSendBytesV2` (`:781-783`); V2 exists so `commBytes` can be longer (e.g. `{commandType, p()}` = 2 bytes, `:919`). The frame-shape diagram above is this (V1/V2) form.
- **V0's `0xFF` terminator is the only empty one**; V1/V2 and V3 all carry the last value chunk in the `0xFF` frame.
- **V3 inserts an 8-byte `verify` block** (6-byte LE timestamp + 2 random, `BleUtils.getSignedBytesFor6`) at bytes 7–14, leaving only 4 value bytes in its START; the H60A6/H61A8 captures are the **V1/V2** form (byte 2 = `01`), not V3.

**Worked H61A8 example — the value layer (`DiyGraffitiV2.g()`), byte-exact.** The H60A6 example above
shows dialect-B *framing*; this one decodes the **value** — the part a client must build itself. Every
link below was read first-hand from the decompiled Java:

- `dreamcolorlightv1/ble/OpDiyCommDialog4BleV2.o()` (`:32`): `new MultiDiyGraffitiController(g.f(), g.g())`, where `g` is a `DiyGraffitiV2` (`:19`) — so the uploaded value **is** `DiyGraffitiV2.g()`.
- `base2light/ble/controller/MultiDiyGraffitiController`: `g()` returns that `byte[]` verbatim (`:26-28`); `getCommandType() = (byte) 3` (`:31-33`); `p() = 1` (`:44-45`) → framed by `makeSendBytesV1(0xA3, 3, value)` (the V1/V2 sub-layout).
- `base2light/ac/diy/DiyGraffitiV2.g()` (`:210-259`): the value serializer.

Captured START frame (real plaintext H61A8 btsnoop):
```
a3 00 01 10 03 09 00 64 00 00 00 1c 05 00 ff 00 00 01 02 39
```
**Frame layer** (`makeSendBytesV2`): `A3` proType · `00` seq · `01` marker · `10` packetCount (= 16) ·
`03` commByte · 14 value bytes @ 5–18 · `39` = XOR(bytes 0..18), hand-verified.

**Value layer** — the 14 inline bytes are the first 14 of `g()`'s output:

| value offset | byte(s) | `g()` field |
|:--:|---|---|
| 0 | `09` | subEffect |
| 1 | `00` | speed |
| 2 | `64` | baseColorBrightness (= 100) |
| 3–5 | `00 00 00` | baseColor R,G,B (black) |
| 6 | `1c` | group count (= 28) |
| 7 | `05` | group 0 — pixelCount (= 5) |
| 8–10 | `00 ff 00` | group 0 — R,G,B (green) |
| 11–13 | `00 01 02` | group 0 — pixel indices 0,1,2 (indices 3,4 spill into seq = 1) |

**`g()` record format:** header `[subEffect, speed, brightness, baseR, baseG, baseB, groupCount]`, then
`groupCount` groups of `[pixelCount, R, G, B, index × pixelCount]`. (The 16-bit-index variant `h()` is
used when `isBigDataEffect()`.)

> **Provenance / scope.** The START frame is **observed and byte-exact** (BCC `0x39` hand-verified). The
> full **~259-byte payload** (28 groups) is *inferred* from the group-count byte (`0x1c` = 28) plus the
> `g()` loop — the capture was a 3-frame prefix, so the tail is not observed end-to-end. The value
> **format is from the code**, not reverse-engineered from the wire bytes.

> **Not H61A8-only.** `RgbIcGraffitiShare0x08.toBytes()` and `DIYGraffitiParser.toBytes()` both delegate
> to `DiyGraffitiV2.g()/h()`, so this is the shared RGBIC graffiti value format. The commByte is
> **device-specific** (G1): H61A8 = `0x03`, H60A6 = `0x58` — same dialect B, different protocol code.

**Dialect A — choosing the scene `comType` from `sceneType` (legacy generic-scene path only).** For
dialect A the byte-4 `comType` is the scene's **`sceneType`** field (from the scene-library API),
mapped through `ScenesOp.parseSceneV1(sceneType, sceneCode, scenesParam, versionArray)` (`:603`) to a
controller whose `getCommandType()` is the on-wire value, gated by the device's `versionArray`:

| `sceneType` | Parser | Controller | **`comType`** | Value (from Base64 `scenesParam`) |
|-------------|--------|------------|---------------|-----------------------------------|
| `1` (RGB) | `parserScenes4Rgb` | `MultiNewScenesControllerV1` | **`1`** | verbatim |
| `2` (RGBIC) | `parserScenes4Rgbic` | `MultiNewScenesControllerV2` | **`2`** | verbatim |
| `3` (graffiti) | `parserScenes4Graffiti` | `MultiNewScenesControllerV3` | **`7`** | **strip 2** |
| `6` (compose) | `parserScenes4Compose` | `MultiNewScenesControllerV5` (`H60B0` if `byte0=0x51`) | **`10`** (or `90`) | strip 1 (compound for H60B0) |
| `5` (diy) | version-gated — needs `versionArray` (sub-table below) | `…V4/V6/V7/V8/V10` / `MultiDiyFixedDeviceController` | **`4`/`12`/`86`/`88`** | strip 1 (+ prepend `subOpType` for V7/V8/V10-int) |
| `0` (static), `4` (cube) | *(no `parseSceneV1` branch)* | → `null` ⇒ **activate-only** | (none) | — |

> **⚠️ The `comType` is an *input*, not a blob field.** For dialect A the byte-4 value is
> `getCommandType()` of the controller that `parseSceneV1(sceneType, sceneCode, scenceParam,
> versionArray)` (`:603-640`) selects — a function of **`sceneType`** (scene-library API DTO field,
> `CategoryV1.LightEffect.sceneType` `:112`) and, **for `sceneType==5` only**, the device
> **`versionArray`** (`ScenesConfigBean.j()` `:224`). It **cannot be derived from `scenceParam`**. For
> `sceneType` 1/2/3/6 the comType is a pure function of `sceneType` (`{1,2,7,10}` — comType `12` is a
> *type-5* value, not compose); `versionArray` only gates *support*. A client that "can't compute the
> commByte" almost always just hasn't carried `sceneType` from the API.

**`sceneType==5` comType sub-table** (`ScenesOp.java:617-636`, `CommonDiyParseConfig.java:107-146`; each
controller's `getCommandType()` is a **constant** — the `subOpType` ctor arg 3/9/11/83/84/86/90 is a
payload field, *not* the comType):

| `versionArray` contains | controller (via helper) | **comType** |
|:--:|---|:--:|
| `7` | `MultiNewScenesControllerV6` | `12` |
| `8` | `MultiNewScenesControllerV7` | `12` |
| `9` | `MultiNewScenesControllerV7` | `12` |
| `10` | `V7` / `V4` / `MultiDiyFixedDeviceController` — by blob `byte[0]` (also the `0xA4`-MTU branch, §4.4 ⚠️) | `12` / `4` / `88` |
| `11` | `MultiNewScenesControllerV8` | `86` |
| `12` | `V10` (blob `byte[0]`∈{64,65}) else `V4` | `88` / `4` |
| `13` | `MultiNewScenesControllerV10` | `88` |

> **H60A6 does NOT use dialect A for its graffiti scene-library effects.** Its `versionArray` is
> **`{0,1,2,3,5}`** (`pact_h60a0/pact/Support.supportScenesOpSet`, goodsType 303 — *not* `{1,2,3}`; the
> `0`/`5` match no `parseSceneV1` branch, so only sceneTypes 1/2/3 → comType 1/2/7 could take dialect A).
> Its graffiti effects (`scenceParam` byte0 = `0x50`) instead route to
> **dialect B** (`makeBleController` → protocol code `0x58`, re-encoded value). Uploading them via
> dialect A (a generic `comType` like `0x02` + the raw blob) is **rejected by the device** — this is
> exactly the hardware A/B result. So per device, first determine which dialect applies (blob
> byte0 = `0x50` ⇒ graffiti/DIY ⇒ dialect B); only then does the `sceneType→comType` table apply.

**Applying a library scene — upload-vs-activate decision.** `BaseSceneViewMode.j3` (`:721-760`) tries
**two paths in order**. **First `q3` (DIY / dialect B, `:1008-1057`):** if `sceneType==5` **AND** decoded
`byte0==0x50` **AND** `DiyNewConfig.isSupportDiyNewEdit(goodsType)`, it walks the device's `IDiyParse`
list and uploads via **dialect B** — commByte = the matched parser's `getProtocolCode()` (e.g.
**H60A6 = `0x58`**, list `[H60A6DiyParse, H60A6GraffitiParse]`, `H60A6DiyConfig:36-37,82`), value =
**re-encoded `toBytes()`** — then returns; **`parseSceneV1` is never reached**. So **type-5 `0x50` scenes
(e.g. H60A6 "Aurora") are dialect B, not dialect A**; `parseSceneV1`'s type-5 branches (`versionArray`∋
`7…13`) apply only to *other* families (H610B/H6092/…), never to H60A6 (whose `versionArray {0,1,2,3,5}`
would make `parseSceneV1` return `null` for type-5 anyway). Within `q3` the concrete parser — hence
`0xA3` vs `0xA4` — is **blob-deterministic** (not MTU-, not coin-flip): `H60A6DiyParse` (→`MultiController`,
**`0xA3`**) matches only if `Pro4H60A6Diy.c` parses (its `byte3` = `layerSize` must fit the blob,
`Pro4H60A6Diy.java:78,83,104`); `H60A6GraffitiParse` (→`Compose4DefWrite4Multi`, **`0xA4`-MTU**, commByte
`0x58`) matches iff **`byte1==0x20`** (`:298`, `b={0x50}` `:47`). **Confirmed from the catalog:** H60A6
Aurora decodes to `50 20 00 FF 00 0C…` → `byte1=0x20` ⇒ **`H60A6GraffitiParse` → `0xA4`-MTU, commByte
`0x58`** (`Pro4H60A6Diy` also fails its length gate: `bytes[1..2]`=`0x0020`=32, `32+3≠188`). So the
`0xA3`-vs-`0xA4` choice is **fully param-deterministic** — and because `H60A6DiyParse` is tried **first**,
the test is the **DIY length gate**, not `byte1`: if `Pro4H60A6Diy.c` parses (`(u16le@bytes[1..2]) + 3 == len`)
→ DIY → `0xA3`; else, if `byte1==0x20`, graffiti → `0xA4` — **both commByte `0x58`**. So Christmas
(`50 36 00 02…`, `54+3=57=len`) → `0xA3`, and even `Halloween B` → `0xA3` *despite* `byte1==0x20`
(`32+3=35=len`), whereas Aurora → `0xA4` (`32+3=35 ≠ 188`). Catalog split **59 graffiti / 14 DIY**. Only the
`0xA4` frame *size* (20 vs up to MTU 512) depends on cached MTU, not the param.
**Otherwise, `parseSceneV1` (dialect A, `:756-781`):**
```java
sceneV1 = ScenesOp.parseSceneV1(sceneType, sceneCode, scenceParam, versionArray);
if (sceneV1 != null) add SceneControllerNoEventV1(sceneV1);  // 0xA3 UPLOAD — conditional (dialect A value)
add createSceneModeController(...);                          // 33 05 04 <sceneCode LE> — ALWAYS
```
So the app **uploads iff the param parses**, and the `33 05 04 <sceneCode>` activation is always sent
in both cases. `parseSceneV1` returns null only when no `(versionArray, sceneType)` branch matches, the
param is empty, Base64 fails, or the per-type validator fails — and those validators gate on
**`byte[0]` + length + `sceneType`** (`ShareDiy.isValidComposeBytes` → `bytes[0]==10`;
`isValidGraffitiBytes` → `byte[0]==1 && len≥9`; `ScenesRgbIC`/`ScenesRgb` anchor on `byte[0]`), **never on
byte 3**.

> **`byte 3 = 0xFF` is NOT an "activate-only" marker.** A grep for any `[3]`-index gate across
> `ScenesOp`/`ShareDiy`/`ScenesRgbIC`/`ScenesRgb`/the scene-content dir returns **zero**; byte 3 is
> ordinary payload. A client that treats `byte[3]==0xFF` as "resident / activate-only" is applying an
> invented rule and will skip a required upload. There is **no residency/first-use cache** that changes
> the write across applies (`SceneBigEffectManager` is only a param-*download* cache for the
> `bigEffectStr==1 && param empty` fetch case). A library scene uploads via **dialect A** (decoded
> `scenceParam`, comType@byte4) **only on the `parseSceneV1` path** — a **type-5 `0x50`** scene (e.g.
> H60A6 Aurora) instead takes `q3`/**dialect B** (commByte `0x58`, re-encoded `toBytes()` — same machinery
> as the DIY editor), never activate-only. See `SCENE_UPLOAD_ENCODING.md` §3b.

### 4.5 Single-request / multi-reply status read (`0xAC`)

Bulk status read-back (e.g. the H60A6 "new-detail" reader) uses proType `0xAC`
(`value_single_read_multi_reply` / `MULTI_READ_AC = −84`;
`AbsSingleReadMultiReplyController.java:22`, `AbsController4BleSingleSendMultiBack.java:191`).

**Request** — an ordinary 20-byte frame `[0xAC, commandType, <ext…>@2, CK@19]`
(`BleUtils.p()`, `BleUtils.java:1031`). The ext bytes are a **length-prefixed list of the
sub-commands being requested**: `[N, cmd₁, cmd₂, …]` (`Compose4BaseInfoSingleRead.c()` prepends the
count). Confirmed against the **H60A6 feature split**: single-zone request
`AC 03 02 41 30` = commandType `0x03`, then `[len=2, 0x41, 0x30]` (read power-on-memory + per-zone
state); dual-zone request `AC 03 03 41 30 A5` = `[len=3, 0x41, 0x30, 0xA5]` (adds per-segment color)
(`pact_h60a0/adjust/h60a6/VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.java:158-191`). This
matches the live-implementation captures exactly.

**Reply** — a burst of `0xAC` frames, tag byte at **byte 1** (`BleUtils.f0()`, `BleUtils.java:679`;
`AbsController4BleSingleSendMultiBack` constants `FIRST_DATA_LEN=12`, `DATA_LEN=17`,
`FIRST_DATA_OFFSET=7`):

```
first chunk (tag 0x00): [0xAC, 0x00, totalLo, totalHi, lastLen, cmd, sub, <12 data bytes @7..18>, CK]
next  chunks (tag 0x01…): [0xAC, tag,  <17 data bytes @2..18>, CK]
terminator (tag 0xFF):   delivers the concatenated buffer to the parser
```

The reassembled buffer is a **header-less TLV stream** — `[type, len, value]`, `i += 2 + len` —
parsed by `Compose4BaseInfoSingleRead.u()` (`:292`). `u()` itself handles `type 1` = on/off,
`type 4` = brightness, `type 5` = **mode** block (`[sub_mode, params]`, delegated to the mode lambda),
`type 7` = **device info** (`w()`: `sub 0x10` = `[uid(8), sw(3), hw(3), dsp(u16 LE)]`; `sub 0x11` =
`[Wi-Fi MAC(6, forward), sw(3), hw(3)]` — same layout as the `aa 07` reply; note the UID in `sub 0x10`
is byte-**reversed** while the Wi-Fi MAC in `sub 0x11` is **forward**, per §5.2), plus
`0x11`/`0x12` sleep/wakeup and `0x23` timers. **Every other type** (`0x30` zone, `0x41` seg/IC,
**`0xA5` colour group**, …) is passed to a caller fallback lambda with the full `[type,len,value]`
re-prepended. `0xA5` = **one colour group** `[group_index (1-based), record × count]`, record =
**4-byte `[brightness, R, G, B]`** on part-brightness SKUs (all curated: H60A6/H6047/H6641) or 3-byte
`[R, G, B]` on colour-only SKUs; **count = `(len − 1) / record_size`** (from the TLV `len`); global
segment index = `(group_index − 1) × count + k`. All of these are modelled in `govee_ble.ksy`
(`status_tlv` → `status_switch`/`status_brightness`/`mode_status`/`device_info_read`/`status_zone`/
`status_seg_info`/`color_group_status`); only the `0x05` `params` sub-layout stays device-specific.

> **Trailing padding:** the reassembled buffer is zero-padded to the last frame's 20-byte boundary, so it
> usually ends in `0x00` bytes. `u()` stops once fewer than 2 bytes remain; no real TLV type is `0x00`.
> A consumer must therefore terminate on a `0x00` type byte (or EOF), **not** parse to end-of-buffer — the
> `.ksy` models this as `status_reply` `repeat-until: '_.type == 0 or _io.eof'` (parsing to EOS would throw
> on a lone trailing zero, which has no room for its length byte).

---

## 5. Command catalog (opcodes)

The command byte (`commandType`, packet byte 1) is defined in
`sources/com/govee/base2light/ble/controller/BleProtocolConstants.java`. The most important
shared opcodes:

### 5.1 Core control

| Command | Byte | Direction | Meaning |
|---------|------|-----------|---------|
| `SINGLE_HEART` | `0x01` | W | heartbeat / keep-alive (also `SINGLE_MAIN_SWITCH`) |
| power (`SINGLE_SWITCH`) | `0x01` | W/R | on/off (payload `01`/`00`) |
| `SINGLE_BRIGHTNESS` | `0x04` | W/R | brightness |
| `SINGLE_MODE` | `0x05` | W/R | mode + sub-mode (color/scene/music/DIY) |
| `SINGLE_SOFT_VERSION` | `0x06` | R | firmware version string |
| `SINGLE_DEVICE_INFO` | `0x07` | R | device info block |
| `SINGLE_SYNC_TIME` | `0x09` | W | set device RTC |
| `SINGLE_AUTO_TIME` | `0x0A` | W/R | auto-timer |
| `SINGLE_DELAY_CLOSE` | `0x0B` | W/R | sleep/off timer |
| `SINGLE_SLEEP` | `0x11` | W/R | sleep schedule |
| `SINGLE_WAKEUP` | `0x12` | W/R | wake-up schedule |
| `NIGHT_MODE` | `0x13` | W/R | night mode |
| `SINGLE_NEW_TIME_V1` | `0x23` | W/R | new-format timer |

### 5.2 Device / Wi-Fi info

| Command | Byte | Meaning |
|---------|------|---------|
| `SINGLE_WIFI_MAC` | `0x14` | Wi-Fi MAC |
| `SINGLE_WIFI_HARD_VERSION` | `0x20` | Wi-Fi module HW version |
| `SINGLE_WIFI_SOFT_VERSION` | `0x21` | Wi-Fi module SW version |
| `SINGLE_WIFI_DSP_VERSION` | `0x22` | DSP version |
| `SINGLE_GET_DEVICE_INFO` | `0x41` | device info (WiFi devices) |
| device-info sub-values | `UUID=0x02, HARD=0x03, SOFT=0x04, DSP=0x07` | sub-selectors within `0x07`/`0x41` |

**Info-read sub-selectors under read command `0x07`** (frames `AA 07 <selector>`), verified in the
`base2light` controllers:

| Selector | Controller | Response layout |
|----------|-----------|-----------------|
| `0x02` | `SnController.java:42` | serial/UID: 8 bytes, **reversed** (`toAddressBytes(hFirst=false)`) |
| `0x10` (16) | `BasicInfoController.java:39` | 8-byte UID (reversed) + two 3-byte firmware versions formatted `X.YY.ZZ` |
| `0x11` (17) | `BasicWifiInfoController.java:46` | 6-byte **Wi-Fi MAC forward/high-first** (`parseWifiMac(...,true)`) + two 3-byte versions `X.YY.ZZ` |

Note the endianness asymmetry: the UID/serial (`0x02`,`0x10`) is byte-reversed, but the Wi-Fi MAC
(`0x11`) is forward.

### 5.3 IC / addressable-LED (RGBIC families)

| Command | Byte | Meaning |
|---------|------|---------|
| `SINGLE_IC_SEGMENT_NUM` / `IC_NUM` | `0x40` | segment/IC count |
| `SINGLE_WRITE_CHECK_IC` | `0x42` | refresh IC |
| `SINGLE_CHECK_IC` / `CHECK_LAST_IC` | `0x46` | detect IC chip |
| `SINGLE_WRITE_CHECK_IC_AMOUNT` | `0x43` | IC count check |

### 5.4 Multi-packet mode payloads (sub-command types carried inside `0xA1`/`0xA3` frames)

| constant | value | meaning |
|----------|-------|---------|
| `MULTI_V1_NEW_SCENES` … `MULTI_V4_NEW_SCENES` | `1,2,7,10` | scene tables (versioned) |
| `MULTI_DIY` / `MULTI_V1_NEW_DIY` | `2` / `4` | DIY animation upload |
| `MULTI_V1_NEW_DIY_GRAFFITI` | `3` | graffiti/pixel DIY |
| `MULTI_V1_NEW_COLOR` | `0x40` | full color table |
| `MULTI_V1_NEW_MUSIC` | `0x41` | music effect table |
| `MULTI_WIFI` | `0x11` | Wi-Fi provisioning payload |

### 5.5 Secrets / encryption / OTA

| Command | Byte | Meaning |
|---------|------|---------|
| `SINGLE_CHECK_SECRET_KEY` | `0xB2` (−78) | pairing secret check |
| `SINGLE_READ_SECRET_KEY` | `0xB1` (−79) | read pairing secret |
| `SINGLE_OTA_PREPARE` | `0xEE` (−18) | enter OTA mode |
| `SINGLE_PACT` | `0xEF` (−17) | protocol/pact negotiation |
| `SINGLE_DYNAMIC_API_SUPPORT` | `0xAB` (−85) | query dynamic-API capability |

> **Note.** The `commandType` namespace is reused per feature; the same numeric value means
> different things under different `proType`/device families. Always interpret
> `(proType, commandType)` together, and consult the per-device tables in [§12](#12-per-device-reference-matrix)
> for device-specific opcodes (e.g. camera calibration `0x30–0x32` on TV backlights).

### 5.6 Device-family opcode overrides

Some families override the shared opcodes above. Confirmed cases:

| Family | Feature | Opcode | Layout | Source |
|--------|---------|--------|--------|--------|
| **Plugs** (H5080/82/83/85/89/5160/61) | sync time | **`0xB5`** (−75), **not** `0x09` | `33 B5 <4-byte BE unix seconds> 01 <tzHour> <tzMin>` | `h5080/ble/controller/SyncTimeController.java:25`, `h5080/ble/BleConstants.java:29` |
| Plugs | power | `0x01` | `33 01 <on/off>` (`SwitchControllerV2`) | `h5080/ble/controller/SwitchControllerV2.java:32` |
| TV / HDMI, H604A | compose / bar light switch | `0x36` (54, `value_compose_light_switch`) | write `33 36 <state…>` (up to 3 booleans, or `[index,state]`); read-back `AA 36` | `pact_h605b/ble/controller/ComposeLightController.java:47/60`, `h604a/ble/BleProtocol.java:24` |

> **On `0x30` "zone power" — CONFIRMED (in a feature split).** This write was absent from `base.apk`
> because **H60A6 ships as a separate dynamic feature module** (`split_pact_h60ax.apk`), not in the
> base decompile. In that split it is built inline via the base helper
> `Controller4ExtBytes.f((byte)0x30, [zoneIndex, state])` → on-wire `[0x33, 0x30, zoneIndex(0|1),
> state(0|1)]` (`pact_h60a0/adjust/h60a6/VM4LightH60A6.java:1203-1205`). It is **readable**: a notify
> frame `[0xEE, 0x30, main, zone0, zone1, …]` carries each state in **bit 1** (mask `0x02`) of its
> byte (`VM4LightH60A6.java:336-344`). See [§15](#15-dynamic-feature-module-pact-split-devices).
> (Opcode `0x30`/48 is still overloaded elsewhere — light-direction read on TV backlights, etc. — so
> interpret it per device.)

---

## 6. Common command byte layouts

Exact 20-byte frames for the most common operations (checksum `= XOR[0..18]`, shown as `CK`).
`..` = zero padding to byte 18.

### Power on/off — `SwitchController` (`SwitchController.java`)
```
ON : 33 01 01 .. CK
OFF: 33 01 00 .. CK
Read: AA 01 00 .. CK   → response payload[0] = on/off
```

### Brightness — `BrightnessController` (`BrightnessController.java`)
```
SET: 33 04 <level> .. CK        // level is a single byte; range 0–100 or 0–255 per model
Read: AA 04 00 .. CK
```

### Sync time — `SyncTimeController` (`SyncTimeController.java:42`)
```
33 09 <hour> <min> <sec> <week> 01 <tzHourOffset> <tzMinOffset> .. CK   // lights
33 B5 <ts3> <ts2> <ts1> <ts0> 01 <tzHourOffset> <tzMinOffset> .. CK     // plugs (H5080 family)
```
Lights use command `0x09` (H/M/S/week/tz); the plug family uses `0xB5` with a **4-byte big-endian
Unix timestamp** then `01` + tz offsets. (The tz-hour byte is signed, e.g. `0xF9` = −7 for UTC−7 —
it is not a fixed constant.)

### Heartbeat — `HeartController`
```
AA 01 00 .. CK      // periodic keep-alive; device echoes status
```

### Read firmware version — `SoftVersionController`
```
AA 06 00 .. CK      // response payload = ASCII version, e.g. "1.00.17"
```

### Mode: solid color (older RGB devices, e.g. H6159) — `SubModeColor.getWriteBytes()`
```
33 05 02 <R> <G> <B> <wholeColorFlag> <R2> <G2> <B2> .. CK
        │  └───────── primary RGB ───┘ │      └─ segment/BK RGB ─┘
        └ sub-mode = 0x02 (color)      └ 1 = apply to whole strip
```
(`SubModeColor.java:67` in `com/govee/h6159/ble/`.) The **sub-mode byte varies per device** —
see [§7](#7-the-mode--sub-mode-system).

### Mode: scene
```
33 05 04 <sceneId ...> .. CK      // sub-mode = 0x04, payload = scene table index/effect id
```

---

## 7. The mode / sub-mode system

Command `0x05` (`SINGLE_MODE`) selects an operating **mode**; its payload begins with a
**sub-mode type byte** followed by mode-specific data. The dispatch is
`AbsMode.parse()` → `parseSubMode(subType, data)` (`AbsMode.java:17-25`).

```
33 05 <subModeType> <sub-mode payload …> CK
```

Each device defines its own `Mode.java` mapping `subModeType → SubMode*` handler, and each
`SubMode*` returns its type byte from `subModeCommandType()`. **These bytes are not globally
constant** — this is the single biggest source of per-device divergence.

### 7.1 Sub-mode type bytes observed across the codebase

| Sub-mode | Common byte(s) | Notes |
|----------|----------------|-------|
| **Solid color** | `0x02`, `0x0B` (11), `0x0D` (13), `0x15` (21) | `0x02` = classic RGB; `0x0B`/`0x15` = RGBIC (with segment/whole selectors); `0x0D` = lamps/panels/string |
| **Scene** | `0x04` (most), `0x09` (bulb-string) | scene id / effect index |
| **Music / mic** | `0x01, 0x03, 0x05, 0x0C, 0x0E, 0x0F, 0x11, 0x12, 0x13, 0x14, 0x19` | highly device-specific; often two variants (IC vs non-IC, V1/V2, Telink vs BK chip) |
| **DIY (new)** | `0x0A` (10) | new-style DIY reference; upload via multi-packet |
| **DIY (old)** | `0x07` | legacy DIY (H6127/H6129) |
| **Video / ambient** | `0x00` | TV backlights & camera-sync devices |
| **Game** | `0x0B` | H6057 |
| **Background color** | `0x0D` | `SubModeColor4Bk` on RGBIC-BK strips |

### 7.2 Worked example — H6159 (`com/govee/h6159/ble/Mode.java`)

```
parseSubMode(b, data):
   b == 0x03 → SubModeMusic
   b == 0x0E → ParamsSubMode4Music
   b == 0x04 → SubModeScenes
   b == 0x0A → SubModeNewDiy
   else      → SubModeColor       (0x02)
```

Contrast with H612526 (`color = 0x0B`), H6104 (`color = 0x02`, `music = 0x01`, `video = 0x00`),
tablelamp/homelight (`color = 0x0D`, `music = 0x0F`), and the dreamcolor family
(`color = 0x0B`, `color-v2 = 0x15`, `music = 0x11/0x13`). The full mapping per device is in
[§12](#12-per-device-reference-matrix).

---

## 8. Notifications (device → app push)

Unsolicited frames arrive via `onCharacteristicChanged` on the control characteristic and are
routed by `AbsNotify` (`sources/com/govee/base2light/ble/comm/AbsNotify.java`).

Frame format:
```
byte 0    1        2 .. 18          19
   ┌─────┬────────┬───────────────┬────┐
   │0xEE │ subType│ payload         │ CK │
   └─────┴────────┴───────────────┴────┘
```

- `AbsNotify.parse()` requires `bytes[0] == 0xEE` (the `NOTIFY` proType), then strips it and
  dispatches on the **next** byte (`subType`).
- Each `AbsNotifyParse` subclass declares the `subType` it handles via `c()` and receives the
  remaining bytes via `d()` → `e()`.

Common notify sub-types:

| subType | Handler | Meaning |
|---------|---------|---------|
| `0x20` (32) | `DefParser.p` → per-SKU VM | **brightness/level** push: `level = value[0]` (`.ksy` `notify_level`) |
| `0x30` (48) | `DefParser.M/O` + per-SKU VM | **switch / zone** push: H60A6 `main = bit0 value[1]`, `zone0 = value[2]`, `zone1 = value[3]` (`.ksy` `notify_switch_zone`) |
| `0x40` (64) | `DeviceStatusNotifyParse` (`:9`) | device connect/status block (10 bytes) → `EventDeviceStatus` |
| `0x11` (17) | `WifiNotifyParse` (per device) | Wi-Fi connect result (`value[0]==0` = connected) (`.ksy` `notify_wifi`) |
| `0x01–0x07` | `NOTIFY_DETAIL_*` | light-status / battery / energy-saving / music / volume detail |
| `0xED` (−19) | `NOTIFY_RECOGNITION` | AI / video-recognition status |
| `0xEE` (−18) | OTA prepare ack | OTA state |

The three the curated lighting SKUs actually push are `0x11` / `0x20` / `0x30` (`ShareVM.x4:2425` + per-SKU
VM overrides); the `.ksy` `notify_frame` switches on these. Other subtypes are cross-category and modelled
as annotations on the `notify_sub` enum.

BBQ thermometers instead push telemetry with `proType = 0xAB` (−85), handled in
`base2newth/bbq/ble/AbsBleComm.java:54`.

For **solicited** bulk status (brightness / zone / per-segment color / device info in one burst),
newer devices use the `0xAC` single-request/multi-reply read documented in
[§4.5](#45-single-request--multi-reply-status-read-0xac) rather than the `0xEE` push channel.

---

## 9. Encryption & session-key handshake

Encryption is **optional and per-device**, gated by
`EncryptionUtils.isEncryptionSupported(bleAddress)`
(`sources/com/govee/encryp/ble/EncryptionUtils.java:36`), which consults a registered
`IEncryptionSupport` and the per-device broadcast-capability record (`BgcInfoReader`).
When not supported, `EncryptWriter` writes the raw 20-byte frame unchanged
(`EncryptWriter.java:44-46`).

Two implementations exist:
- **V1 — AES** (`EncryptionManager` + `AESEncryptionStrategy` + `Controller4Aes`)
- **V2 — AES-GCM** (`EncryptionManagerV2` + `Controller4AesGcm`)

### 9.1 V1 handshake (AES)

All handshake frames use header byte `0xE7` (−25) at byte 0 and a payload command at byte 1,
padded with random bytes, XOR-checksummed at byte 19 (`Controller4Aes.a()` at
`Controller4Aes.java:51`). The 20-byte frame is then encrypted with the **same block+stream scheme
as all other payloads** (§9.2) — `Safe.d()`: AES/ECB/NoPadding on bytes `[0..15]` and RC4 on the
trailing bytes `[16..19]` (`Controller4Aes.e()`/`f()` both call `Safe.d`). The only difference from
post-handshake traffic is the **key**: the handshake uses a *fixed pre-shared key* from
`LibTools.c()`, whereas subsequent payloads use the negotiated 16-byte session key.

> **Correction (was: "AES/ECB/NoPadding … on the whole 20-byte frame").** AES/ECB cannot operate on
> 20 bytes (not a multiple of the 16-byte block). The handshake frame is enciphered as AES-ECB on
> the first 16 bytes + RC4 on the last 4 — identical to §9.2.

**The fixed key** is *not* a hard-coded literal in the Java. `LibTools.c()`
(`LibTools.java:84`) returns `AESUtils.decode(R.string.app_communication, R.string.app_session)` —
an AES-decoded, obfuscated string resource resolved at runtime. A live implementation reports the
decoded value as ASCII **`MakingLifeSmarte`** (16 bytes); this is consistent with a 16-byte AES key
but cannot be confirmed from static Java alone (it lives behind the resource decode).

```
1. App → device:  encrypt_AES(  [0xE7, 0x01, <random pad…>, CK]  )   // request session key
2. Device → app:  cipher → decrypt → [0xE7, 0x01, <16-byte session key>]
   (Controller4Aes.g(): validates bytes[0]==0xE7 && bytes[1]==0x01, extracts key[2..17])
3. App → device:  encrypt_AES(  [0xE7, 0x02, <random pad…>, CK]  )   // confirm
4. Device → app:  ACK → decrypt → bytes[0]==0xE7 && bytes[1]==0x02   (Controller4Aes.h())
```

After the handshake, the 16-byte **session key** encrypts all subsequent payloads.

### 9.2 Payload cipher (`Safe.java`)

- Full 16-byte blocks → **AES/ECB/NoPadding** with the session key (`Safe.c()`/`Safe.a()`).
- A trailing partial block (< 16 bytes) → **RC4 stream cipher** keyed by the session key
  (`Safe.f()` = RC4 KSA, `Safe.g()` = RC4 PRGA XOR). This lets arbitrary-length payloads be
  encrypted without padding growth.
- `EncryptionManager.c(data)` returns the ciphertext, split into ≤ MTU pieces
  (1 or 2 GATT writes; `EncryptWriter.b()`/`a()`).

### 9.3 V2 handshake (AES-GCM)

`EncryptionManagerV2` follows the same request/confirm shape but negotiates an AES-GCM session
(authenticated encryption with nonce/tag). It also keeps the `0xE7` (−25) header, but uses
**different sub-opcodes** than V1 (`Controller4AesGcm.java`):

| Phase | V1 (AES) frame | V2 (AES-GCM) frame |
|-------|----------------|--------------------|
| request session key | `[0xE7, 0x01, …]` | `[0xE7, 0x19, …]` (25; multi-packet, GCM; `Controller4AesGcm.f()`/`i()`) |
| confirm / data-channel | `[0xE7, 0x02, …]` | `[0xE7, 0x1A, …]` (26; `Controller4AesGcm.a()`) |
| single-packet variant | — | `[0xE7, 0x11, …]` (17; `Controller4AesGcm.g()`/`d()`) |

V2 frames carry a package counter and a `0xFF` terminator (multi-packet), and the payload is
GCM-encrypted with a nonce/IV + auth tag rather than ECB+RC4. This confirms the `e7 1a` opcode
observed in live captures.

It is used by the newer encrypted transport `com.govee.ctlchannel` (a Kotlin/coroutine
multi-connect client — `VirtualBleClient`, `CommBleGattCallback extends
EncryptionBluetoothGattCallback`) where service/characteristic UUIDs are supplied **dynamically
per message** (`BgcMsg(serviceUUID, characteristicUUID, value)`), rather than hard-coded. Devices
seen on this path include H70B3/B4/B5, H6810/6811, H6800, H6840.

### 9.4 Higher-level "secret" (not the same thing)

Several packages (`SecretKeyController`, group operations, `secretCode`/`bleExt` fields) exchange
a **Base64-encoded pairing/group secret** over the ordinary GATT channel. This is device-binding
/ group-authorization material — **distinct from** the AES session encryption above. BBQ
thermometers use `SecretKeyControllerV2` with read `0xB1` / write `0xB2` and an 8-byte Base64
secret.

This per-device secret — what it is, how it is read from the device, how it is stored/retrieved via
the **cloud account API**, and how a client presents it — is documented in full in
[§18.5](#185-pairing--the-per-device-secret-secretcode).

---

## 10. OTA firmware update

Three OTA transports exist; the device family/chipset determines which. All are triggered by
first putting the device into OTA mode via `SINGLE_OTA_PREPARE` (`0xEE`).

### 10.1 V1 — Telink style (`OtaManager.java`)

Runs on service `…0d1912` / characteristic `…0d2b12`.

```
START:  [0x01, 0xFF]
DATA_i: [idxLo, idxHi, <16 firmware bytes>, crc16Lo, crc16Hi]     // 20 bytes
END:    [0x02, 0xFF, idxLo, idxHi, ~idxLo, ~idxHi, 0x00, 0x00 (+crc)]
```

- Packet index is a 16-bit little-endian counter at bytes 0–1.
- **CRC-16/MODBUS** (reflected poly `0xA001`) over bytes `[0 .. len-3]`, stored little-endian in
  the last two bytes (`OtaPacketParser.b()`/`c()` at `OtaPacketParser.java:27-45`).
- Firmware is split into 16-byte chunks; the final short chunk is padded with `0xFF`.
- Flow control: the app waits for a per-packet result callback (`OtaFlag.CommResultCallback`),
  retries a failed packet up to 2×, and posts `FileTransportEvent` progress.

### 10.2 V2 — TI OAD (`OtaManagerV2.java`)

Runs on the TI OAD service `F000FFC0` with image-block char `F000FFC1` and image-control char
`F000FFC2`. Standard TI Over-the-Air-Download image header + block transfer.

### 10.3 V3 / V4 — "Frk" (`ota/v3`, `ota/v4`)

`OtaManagerV3` / `OtaPacketParserV3` and `Ota4FrkAi` implement a third vendor's OTA (A/B-area
capable, `Ota4FrkSupportABArea`). Used by the newest SoCs. A cloud-assisted "auto OTA pact"
reporting path also exists (`ota/auto`).

---

## 11. Sensor / thermo-hygrometer profile

Sensors deviate from the lighting model in two ways: they may use the **INTELLI_ROCKS service**
([§2.4](#24-sensor--thermo-service-intelli_rocks)) for GATT, and stand-alone hygrometers publish
telemetry primarily via **BLE advertisement** rather than GATT.

### 11.1 BLE-broadcast telemetry (no connection needed)

`com.govee.widget.view.temHumDevice.ble` contains `IThBroadParse` implementations that decode
the manufacturer-specific advertisement of Govee hygrometers:

- **Company flag** `0x88 0xEC` → BLE company ID **0xEC88 (Govee)** (`IThBroadParse.COMPANY_SELF_FLAG`).
- The 62-byte scan record is walked as TLV to find the Govee payload
  (`BleUtil.parseThBleValidBytePos*`).

Model coverage includes H5051/52/53, H5071/72/74/75, H5100–H5112, H5106 (adds pressure),
H5107 (dual probe), H5109, H5140 (CO₂), H5171/74/77/79, H5220, R5112, B5178 (multi-probe).

### 11.2 Temperature / humidity encoding (packed 3-byte)

Used both in advertisements and GATT sub-records
(`BleUtil.parseThValue` / `BleUtils.l0` at `BleUtils.java:928`):

```
V = big-endian 24-bit int of the 3 bytes
sign = MSB of byte[0]            // 1 → negative
temp = V / 1000                  // scaled ×10 by callers → 0.1 °C units
hum  = V % 1000                  // scaled ×10 → 0.1 %RH units
0xFFFFFF = invalid / no reading
```

Variants: a **4-byte "thp"** form adds a pressure/PM field (H5106); CO₂ (H5140) is a separate
2-byte big-endian value; battery is a trailing unsigned byte.

### 11.3 History / chart logs

Devices with data logging (e.g. H5086 energy, gateway-attached TH) pull history over the
INTELLI_ROCKS **chart** characteristics (`…2014` request / `…2015` notify), using the standard
20-byte framing:

```
PREPARE:    33 02 ..                  // cmd 0x02 → device returns record count
TIME_RANGE: 33 01 <start 4B BE> <end 4B BE> ..   // cmd 0x01 → dumps records in range
```
(`ChartPrepareInfoController.java:48`, `ChartTimeRangeController.java:53`.)

### 11.4 Gateways

`H5042` (Wi-Fi Gateway 1s), `H5043/44`, and `H5151` connect to sub-sensors (TH, leak) and relay
their data. `H5042/H5043` use the **light** service `1910/2b11`; `H5151` uses the **INTELLI_ROCKS**
service as primary (`4857/2011`, notify `2012`) plus a secondary light-profile comm for Wi-Fi
setup. TH alarm/calibration is command `0x08` (sub-byte 0 = alarm thresholds, sub-byte 1 =
calibration offset); sub-device TH ranges on H5151 are written multi-packet with `proType 0xFE`,
cmd `0x05`.

---

## 12. Per-device reference matrix

**Legend.** UUID "default" = service `…0d1910` / char `…0d2b11`. Sub-mode bytes are the
`subModeCommandType()` values used under command `0x05`. "Transport": S = single-packet
(`0x33/0xAA`), M = multi-packet (`0xA1/0xA2`). Device-specific opcodes are `commandType`
values not in the shared catalog.

> **⚠️ Confidence.** Rows in this matrix are a mix of **controller-verified** (read directly from
> the family's `SubMode`/controller classes) and **survey-derived** (`confidence: medium` — extracted
> by broad grep across families, not confirmed against a builder). The per-row confidence lives in
> [`devices.yaml`](../devices.yaml) (`families[].confidence`); treat any row you cannot trace
> to a cited controller as *medium* until verified. The deeply-analysed feature-split families
> (H6047, H6641, H61A8, H5083, H5122, H6008, H6052, H60A6, H6006) are `high`; most of the broad
> catalog rows below are `medium`.

### 12.1 RGB / RGBIC strips & bulbs

| Model(s) | Category | UUID | Transport | color / scene / music / DIY | Device-specific opcodes |
|----------|----------|------|-----------|-----------------------------|--------------------------|
| **H6159** (H6110/H6109/H614A/B/E) | RGBIC strip | default | S+M | `02 / 04 / 03(+0E) / 0A` | — (BK color 0x0D) |
| **H6160** (H6163/H6117) | RGBIC strip | default | S+M (`AbsBle4Old`) | `02(+0B) / 04 / 03(+01,0E) / 0A` | BulbStringColor read `0xA2` |
| **H6102** | LED strip | default | S+M | `02 / 04 / 01 / 0A` | Limit `0x0E` |
| **H6104** | RGBIC video strip | **ffe0/ffe1** | S+M | `02 / 04 / 01 / 0A` (video `0x00`) | Direction `0x13`, IP `0x15`, MultiDIY `0x02` |
| **H6105** | LED strip | default | S+M | `02 / 04 / 03 / 0A` | — |
| **H6113** | LED strip | default | S+M | `02 / — / 03 / 0A` (no scene) | MultiDIY `0x02` |
| **H6114** | LED strip | default | S+M | `02 / 04 / 03 / 0A` | MultiDIY `0x02` |
| **H6119** | RGBIC strip/string | default | S+M | `02(+21) / 04 / 12 / 0A` | BulbStringColor read `0xA5`, Gradual `0x14` |
| **H612526** (H6125/H6126) | RGBIC strip | default | S+M | `0B / 04 / 12 / 0A` | LightNum `0x0F`, ReadColor `0xA2`, Gradual `0x14` |
| **H6127** (H6107/H6116/H6161) | RGBIC strip | default | S+M | `02 / 04 / 01(+03) / 07(+0A)` | dual DIY (old `07` + new `0A`) |
| **H6129** | RGBIC strip | default | S+M | `02 / 04 / 03 / 07(+0A)` | dual DIY |
| **H613839** (H6138/9, H613A–F) | RGBIC strip | default | S+M(+PtReal) | `02(+0D bk) / 04 / 03(+0E) / 0A` | runtime music V1/V2; `secretCode` |
| **H6181** | RGB light | default | S+M | `02 / 04 / mic 0xFF(stub) / 0A` | — |
| **H6182** | RGB (Wi-Fi) | default | S+M+Notify | `02 / 04 / 03 / 0A` | Wi-Fi notify `0x11` |
| **H6185** | RGB (mic) | default | S+M | `02 / 04 / mic 05 / 0A` | MicController `0x05` |
| **H6101** | RGB (calibrated) | **ffe0/ffe1** | **S only** | `02 / — / 01 / —` (video `0x00`) | Calibration `0x08`, CalibrationOk read `0x01` |

### 12.2 Shared light abstractions & generic strips

| Package / Model(s) | Category | UUID | Transport | color / scene / music / DIY | Notes |
|--------------------|----------|------|-----------|-----------------------------|-------|
| **rgblight** | RGB single-zone (many SKUs) | default | S+M+Notify | *(shared `base2light.pact.newdetail.sub.*`)* | modern transport-only package |
| **rgbiclight** | RGBIC (many SKUs) | default | S+M+Notify | *(shared newdetail)* | modern transport-only |
| **dreamcolorlightv1** | RGBIC "dreamcolor" (~66 SKUs) | default | S+M(V1) | `0B(+21) / 04 / 11(+19,22) / 0A` | Limit 14, LightNum 15, Gradual 20/`0xA3`, IC 64/66/70, BulbColor `0xA2` |
| **dreamcolorlightv2** | RGBIC dreamcolor (BK/Telink) | default | S+M(V1) | `0B(+21) / 04 / 17(+12,19) / 0A` | Gradual 20/`0xA3`, BulbColor `0xA2` |
| **stringlightv2** | String / fairy / curtain | default (+group) | S+M+Group | `0D(+2 oldv0) / 04 / 14(+19), mic 5 / 0A` | Limit 14; group `SecretKeyController` |
| **bulblightstringv1** (H7002/H7005) | Addressable bulb-string | default | S+M | `0B / 09 / 14 / —` | BulbNum `0x0F`, BulbColor `0xA2`, MultiScene cmd `0x01` |
| **barelightv1** (H6145–47/H6171) | Neon/bare RGBIC | default | S+M | `0B(+21) / 04 / 12 / 0A` | **no notify path**; Gradual 20/`0xA3`, LightNum 15 |

### 12.3 Lamps, panels, car, misc

| Model(s) | Category | UUID | Transport | color / scene / music / DIY | Device-specific opcodes |
|----------|----------|------|-----------|-----------------------------|--------------------------|
| **H6052/H6078** (tablelampv1) | Table lamp | default | S+M+Notify | `0D / 04 / 0F / 0A` | LightIndicator |
| **H6148** (homelightv1) | Home light | default | S+M+Notify | `0D / 04 / 0F / 0A` | — |
| **H6050/51/55/58/59/73** (hollowlamp) | Shape/panel lamp | default (+group) | S+M+Group | `0D / 04 / 0E/0F/13 / 0A` | LocalColor `0xA5`, AutoInduction `0xA6`, Sensitive `0xA7`, MultiMusic `0x41` |
| **H6118/H6194** (carlightv1) | Car light | default (+group) | S+M+Group | `0D / 04 / 0E/13 / 0A` | — |
| **H604A–D** (h604a) | RGBIC strip | default | S+M+Notify | `15 / 04 / 13 / 0A` (video `0x00`) | Swap `0x34`, StartTime `0x35`, Compose `0x36`, IcNum `0x40`, MultiMusic `0x41`, CaliBelt `0xA7`, CheckLight `0xAA` |
| **H6057** (h6057) | Night-light (presets) | default | S+M+Notify | `0D / 04 / 13 / 0A` (game `0x0B`) | PreSet `0x24–0x29`, GetDetail `0x34`, PlayVoice `0x31`, EnergySaving `0x16`, LocalColor `0xA5` |
| **H1161** (pickupbox) | Audio pickup hub (**non-light**) | default | S+M+Notify | music-only `0F`; no color/scene/DIY | Open `0x10`, Brightness `0x11`, MultiDevice `0x30`, DeviceNum `0x40`, Info/MultiMusic `0x41`, Clear `0x42`, OpDevice `0x43` |
| **H1162/63/67/68** (h1162) | Music/HDMI Sync Box | default | S+M | *(shared light modes)* | — |

### 12.4 Splicing panels & grow lights (H70xx)

| Model(s) | Category | UUID | Transport | color / scene / music / DIY | Device-specific opcodes |
|----------|----------|------|-----------|-----------------------------|--------------------------|
| **H70B1** | Splicing/glide panel | default | S+M | `0D / 04 / 13 / 0A` | Splicing controller |
| **H70B2** | Splicing panel | default (reuses H70B1) | S+M | inherits H70B1 | MTU negotiation |
| **H70BC / H70Bx** | Splicing container | (delegates) | S+M | inherits H70B1 | ChangeSplicing `0x40`, ReadSubVersion `0x43` |
| **H705a/b/D/E/F** (+H3401, H61Cx, H706x, H80xx) | RGBIC strip/bulb | default | S+M | *(shared newDetail)* | CheckIcNum |
| **H7004** | Grow light (red/blue) | default | **S only** | RedBlue `41=1 / 61=2 / 81=3` | RedBlueController |
| **H7017** | Grow / plant light | default | **S only** | plant-mode (custom) | RedBlueController, PlantMode |
| **H7022** | Multi-bulb string | default | S+M | scenes cmd `0x01` (multi) | BulbNum, ReadBulbColor |

### 12.5 TV backlights & HDMI sync boxes (camera / video)

| Model(s) | Category | UUID | color / scene / music / DIY / video | Camera & special opcodes |
|----------|----------|------|-------------------------------------|--------------------------|
| **H6179** (tvlightv1) | TV backlight V1 | default | `0D / 04 / 0E / 0A / —` | LimitController (segment) |
| **H6198/H6199** (pact_tvlightv2) | TV backlight V2 (camera) | default | `0B/21 / 04 / 0C/13 / 0A / 00` | **CheckCam `0x32`, CamPos `0x31`, LightDir `0x30`**, StartTime `0x34/0x35`, Gradual `0xA3` |
| **H6046/H6053/H6056** (pact_tvlightv3) | TV backlight V3 | default | `0D/21 / 04 / 0C/13 / 0A / —` | SwapLight `0x34`, SingleLight/Heart `0x33` |
| **H6049/H6054** (pact_tvlightv4) | TV backlight V4 (camera) | default | `0D/21 / 04 / 0C/13 / 0A / 00` | **CheckCam `0x32`, CamPos `0x31`, LightDir `0x30`**, Swap `0x34`, StartTime `0x35` |
| **H605B/C/D, H6601–04, H6608, H8604** (pact_h605b) | HDMI sync box / TV backlight | default | `15 / 04 / 13 / 0A / 00` | CheckCam `0x32`, CamPos `0x31`, LightDir/HDMI `0x30`, Cali `0x44`, WhiteBal/AI/HDMI `0xA9`, SoundSrc `0x32`, UsbCheck `0x37/0x38/0x50`, Install `0x23`, CaliLight `0x25` |

### 12.6 Sensors, gateways, plugs

| Model(s) | Category | GATT service | Data path | Notes |
|----------|----------|--------------|-----------|-------|
| H5051/71/72/74/75, H5100–5112, H5106/07/09, H5140, H5171/74/77/79, H5220, R5112, B5178 (widget/temHumDevice) | Thermo-hygrometer | — (advertisement) | **BLE broadcast** | packed 3-byte temp/hum; CO₂/pressure variants |
| **H5042** | Wi-Fi Gateway 1s | light `1910/2b11` | GATT + IoT | TH warn `0x08`/sub0, cali `0x08`/sub1; sub-sensor H5109 |
| **H5043/H5044** | Gateway (leak + TH) | light `1910/2b11` | GATT + IoT + broadcast | TH read `0x34`, IoT TH `0x04`, id `0x32` |
| **H5080** (H5082/83/85/89/5160/61) | Smart plug | light `1910/2b11` | GATT | switch `0x01` / timer / delay; **sync-time `0xB5`** (4-byte BE epoch), not `0x09` |
| **H5086** | Smart plug + energy history | light `1910/2b11` **+ INTELLI_ROCKS** `2014/2015` | GATT + chart | history prepare `0x02`, time-range `0x01`(+8B start/end) |
| **H5151** | Wi-Fi TH gateway | **INTELLI_ROCKS** `4857/2011` (notify `2012`) + light GW comm | GATT | sub-TH range `proType 0xFE`/cmd `0x05` (multi); binds H5112/H5044 |
| **H5055/5181/5183/5198/5199** (base2newth/bbq) | BBQ meat thermometer | **INTELLI_ROCKS** `4857/2011` | GATT, notify `proType 0xAB` | syncTime `0x10` (+4B LE minutes); secret `0xB1`/`0xB2` (8-byte Base64) |

### 12.7 Encrypted multi-connect transport

| Package | Category | UUID | Encryption |
|---------|----------|------|------------|
| **ctlchannel** | Shared multi-connect BLE client (Kotlin) for H70B3/B4/B5, H6810/6811, H6800, H6840 | **dynamic per-message** (`BgcMsg`) | **AES-GCM (V2)** via `EncryptionBluetoothGattCallback`; only CCCD `0x2902` is hard-coded |

---

## 13. Implementation notes & class map

Where to look in the decompiled tree:

| Concern | Class(es) |
|---------|-----------|
| Connection / scan / reconnect / MTU | `com/govee/ble/BleController.java`, `ble/connect/BleConnectImp.java`, `ble/scan/*` |
| GATT callback (state, discovery, RSSI) | `com/govee/ble/AbsBluetoothGattCallback.java` (extends `encryp/ble/EncryptionBluetoothGattCallback`) |
| Notification enable / raw write | `com/govee/ble/comm/BleCommImp.java` |
| Multi-packet A1/A2 | `com/govee/ble/multi/MultiPackageManager.java` |
| Packet builders / bit helpers / checksum | `com/govee/base2kt/utils/BleUtils.java` |
| Opcode constants | `com/govee/base2light/ble/controller/BleProtocolConstants.java` |
| Single-command base | `com/govee/base2light/ble/controller/AbsSingleController.java` |
| Concrete controllers (power, brightness, time…) | `com/govee/base2light/ble/controller/*Controller.java` |
| Mode / sub-mode | `com/govee/base2light/ble/controller/AbsMode.java`, per-device `Mode.java` + `SubMode*.java` |
| Notifications | `com/govee/base2light/ble/comm/AbsNotify.java`, `AbsNotifyParse.java`, `DeviceStatusNotifyParse.java` |
| Encryption V1 (AES) | `com/govee/encryp/ble/EncryptionManager.java`, `Controller4Aes.java`, `AESEncryptionStrategy.java`, `Safe.java` |
| Encryption V2 (GCM) | `com/govee/encryp/ble/EncryptionManagerV2.java`, `Controller4AesGcm.java` |
| Encrypt gate / write splitter | `com/govee/encryp/ble/EncryptionUtils.java`, `EncryptWriter.java` |
| OTA V1 / V2 / V3 | `com/govee/base2light/ble/ota/OtaManager.java`, `ota/v2/OtaManagerV2.java`, `ota/v3/*` |
| Per-device drivers | `com/govee/<model>/ble/Ble.java` + `BleComm.java` + `Mode.java` |
| Sensor broadcast parse | `com/govee/widget/view/temHumDevice/ble/*`, `base2home/pact/BleUtil.java` |

### Building a minimal client (summary)

1. Scan for the device (by MAC or advertised name/service data).
2. `connectGatt` → discover services → find service `…0d1910`, characteristic `…0d2b11`.
3. Enable notifications: `setCharacteristicNotification(true)` + write CCCD `…2902` = `{0x01,0x00}`.
4. *(If the device requires it)* perform the AES/GCM session handshake ([§9](#9-encryption--session-key-handshake)).
5. Send commands as 20-byte frames `[0x33, cmd, payload…, XOR]`; parse notifications
   `[0xEE, subType, …]` and read responses `[0xAA, cmd, …]`.
6. Use the multi-packet `0xA1/0xA2` protocol for DIY/scene/graffiti payloads.
7. Consult [§12](#12-per-device-reference-matrix) for the device's UUID set, sub-mode bytes, and
   special opcodes.

---

## 14. Validation notes (vs. a live implementation)

This document was cross-checked against an independent, "verified-live" client
(`govee_ble_local`). Each item below was then re-confirmed in the decompiled source; the doc
sections above have been corrected/expanded accordingly.

| # | Item | Verdict | Resolution (source-confirmed) |
|---|------|---------|-------------------------------|
| 1 | Notify on `2b10`, BGC-info on `2b12` | **Doc was wrong/incomplete** → fixed §2.1/§2.2/§3 | Service `1910` has 3 chars: `2b10`=notify, `2b11`=write, `2b12`=BGC/`encryptVersion` read. The app enables notify on *all* chars (`BleCommImp.c()`), so it never names `2b10` (a dead constant in `Constants.java`), but device pushes arrive there. OTA `2b12` is under a *different* service (`1912`). |
| 2 | Handshake cipher | **Doc §9.1 was wrong** → fixed | Handshake frames use AES-ECB(16)+RC4(4) via `Safe.d()`, same as payloads (§9.2); "AES/ECB/NoPadding on 20 bytes" is impossible. Only the key differs (fixed PSK vs session key). |
| 3 | `scene_chunks` `0xA3` framing | **Superseded by hardware — re-fixed §4.4** | Two `0xA3` dialects exist. My first fix documented only the *legacy* one (comType@byte4 = version constant 1/2/7/12) and said "no `\|0x08`". H60A6 hardware proved it uses **dialect B** (DIY/graffiti): byte4 = device protocol code **`0x58`** (const 88, `≈0x50\|0x08` by coincidence), value = **re-encoded** graffiti `toBytes()` (not the raw blob), `0xFF` terminator data-bearing. §4.4 now documents both dialects; the legacy generic-scene framing is rejected by H60A6. **Still open:** the graffiti path has two builders — `0xA3` (commByte@byte4, hardware-tested) and `0xA4`-MTU (commByte@byte6, decompiled `makeSendBytesMtu`); the live default needs an official-app btsnoop (see §4.4 ⚠️). |
| 4 | Plug sync-time `0xB5` | **Code correct; doc gap** → added §5.6/§6/§12.6 | Plug family uses `0xB5` with `[4-byte BE epoch, 0x01, tzHour, tzMin]`. The trailing `f9` seen live is a signed tz-hour byte (UTC−7), not a constant. Sent on connect / before timer writes. |
| 5 | Device-info selectors `0x10`/`0x11` | **Code correct; doc gap** → added §5.2 | Under read `0x07`: `0x02`/`0x10` = UID/serial (reversed), `0x11` = Wi-Fi MAC (forward) + versions `X.YY.ZZ`. |
| 6 | PSK literal | **Code plausibly correct; unverifiable from Java** → noted §9.1 | `LibTools.c()` = `AESUtils.decode(app_communication, app_session)` — a runtime-decoded resource, not a static literal. Live value `MakingLifeSmarte` is consistent but not statically provable. |
| 7 | `0xAC` status burst | **Code correct; now fully confirmed** → §4.5 + §15 | 20-byte request `[0xAC, cmd, ext…, CK]`; ext = length-prefixed cmd list. H60A6 split confirms `AC 03 02 41 30` / `AC 03 03 41 30 A5` byte-for-byte. Reply chunks tag@byte1 (12 data@off7 first, 17 data@off2 rest, `0xFF` terminator) → TLV stream. |
| 8 | `0x30` zone / `0x36` bar-switch | **BOTH confirmed** (were: `0x30` "not found") → §5.6 + §15 | `0x36` (`value_compose_light_switch`) real & readable (`AA 36`) — H6047 split. `0x30` zone-power **also confirmed**: `[0x33,0x30,zone,state]`, readable (bit 1 of each notify byte) — it was missing from base.apk only because **H60A6 ships as a separate feature split** (`split_pact_h60ax.apk`). Correction to the earlier "no `h6047`/`h60a6` package" note: those packages exist, delivered as on-demand modules. |
| 9 | GCM handshake opcode `e7 1a` | **Confirmed** → added §9.3 | V2 (GCM) keeps header `0xE7` with sub-opcodes `0x19` (request), `0x1A` (confirm/data), `0x11` (single-packet variant) — vs V1's `0x01`/`0x02`. |

**Confirmed consistent (no change):** BCC = XOR[0..18]@19; proType `0x33/0xAA/0xA1/0xA2/0xEE`;
payload cipher AES-ECB block + RC4 remainder (§9.2); session key = decrypted reply bytes `[2..18]`;
`e7` two-step (`01` request / `02` confirm) for V1; opcodes power `0x01`, brightness `0x04`, mode
`0x05`, secret `0xB1/0xB2`; sub-mode color `0x0D/0x15/0x0B`, scene `0x04`; scene write
`33 05 04 <lo> <hi>` little-endian; read replies echo `0xAA`.

**Documented-but-unimplemented (library scope, not defects):** OTA (§10), sensor/INTELLI_ROCKS
profile (§11), and the AES-GCM V2 handshake (§9.3) are all real in the APK but out of scope for the
`govee_ble_local` client (which raises `GoveeBleNotSupported` when a device reports
`encryptVersion == 2`).

---

## 15. Dynamic feature-module (pact split) devices

### 15.1 Delivery model

`com.govee.home` is shipped as an **Android App Bundle** and installed as **split APKs**. Beyond the
`base.apk` + ABI/density `split_config.*` splits, the app declares **~50 on-demand *device* feature
modules** named `pact_<family>` (Play Feature Delivery / `SplitInstallManager`). When you add a
device, the app downloads just that device's module.

This has a direct consequence for anyone reverse-engineering from a plain `base.apk` decompile
(§1–§14): **the base app contains the shared BLE transport and framework, but most device-specific
protocol logic is *not* in it** — it lives in the per-device split. In `base.apk` the packages
`com/govee/rgblight` and `com/govee/rgbiclight` are transport-only "modern pact abstraction" shells
(noted in [§12.2](#122-shared-light-abstractions--generic-strips)); the concrete `Mode`/`SubMode*`/
`*Controller` classes that fill them in are delivered by the `pact_*` splits. This is exactly why
the H60A6 zone-power (`0x30`) and bulk-status (`0xAC`) logic was absent from the base decompile
([§4.5](#45-single-request--multi-reply-status-read-0xac), [§5.6](#56-device-family-opcode-overrides))
— it ships in `split_pact_h60ax.apk`.

**Splits reuse everything in §1–§11 unchanged**: same GATT service/characteristics (`…1910` /
write `…2b11` / notify `…2b10`), same 20-byte `[proType, cmd, payload…, XOR@19]` framing, same
`0xA1`/`0xA3` multi-packet and `0x05` mode dialects, same optional encryption. A split only adds:
its device's **sub-mode byte map**, **device-specific opcodes**, **effect/scene tables**, and
**spec "errata"** (segment counts, kelvin ranges, feature flags). None of the analyzed lighting
splits override the GATT UUIDs.

Modules can be enumerated/fetched from Play with the app's own delivery call
(`fdfe/delivery?doc=com.govee.home&mn=<module>`); see `fetch_modules.py` in the repo root.

### 15.2 Module catalog (declared `pact_*` feature modules)

The app declares these device modules (from the SplitInstall manifest). Categories are **[analyzed]**
where a split was decompiled here, otherwise **inferred** from Govee model-number conventions:

| Category | Modules |
|----------|---------|
| **Lighting** — strips / bulbs / bars / panels / neon / net / string / lamps / TV backlight | `pact_bulblightv3` **[analyzed: bulbs]**, `pact_h6047` **[analyzed: TV light bars/panels]**, `pact_h60ax` **[analyzed: RGBIC incl. H60A6]**, `pact_h61d3` **[analyzed: RGBIC strips/neon/net]**, `pact_fanlight`, `pact_straightfloorlamp`, `pact_h3a5x`, `pact_h6020`, `pact_h6061`, `pact_h6062`, `pact_h6071`, `pact_h6079`, `pact_h6092`, `pact_h6099`, `pact_h60b0`, `pact_h60b1`, `pact_h6609`, `pact_h6630`, `pact_h6840`, `pact_h7006`, `pact_h7040`, `pact_h7050`, `pact_h7056`, `pact_h7075`, `pact_h7080`, `pact_h7086`, `pact_h7090`, `pact_h70b3`, `pact_h70dx`, `pact_h7111`, `pact_h7120`, `pact_h7129`, `pact_h7130`, `pact_h7140`, `pact_h7149`, `pact_h7150`, `pact_h7161`, `pact_h7170`, `pact_h7180`, `pact_h7184` |
| **Smart plug** | `pact_plugv1` |
| **Sensors** (presence / TH / leak) | `pact_h512x` **[analyzed: presence/pressure/radar]**, `pact_thnew` (thermo-hygrometer), `pact_h5182`, `pact_h5185`, `pact_h5901` |
| **BBQ / cooking thermometers** | `pact_bbqnew`, `pact_bbqv1` |
| **Appliances** | `pact_h7160` **[analyzed: humidifier]**, `pact_h7172` **[analyzed: ice maker (H7172/H717D/H7178)]** |

> **Scope of this section.** Per request, only **lighting / smart-plug / button** devices are in
> scope; **BBQ (`pact_bbqnew`,`pact_bbqv1`) and hygrometer/sensor (`pact_thnew`,`pact_h512x`, …)
> splits are excluded**, as are appliance splits (`pact_h7160` humidifier, `pact_h7172` ice maker).
> `pact_h512x` was probed and identified as a presence/pressure/radar **sensor** (not a plug/button),
> so it is excluded. No dedicated *button* module is present in the fetched set; the **smart-plug**
> logic is `pact_plugv1` (not among the fetched splits — the base app also carries the earlier
> H5080 plug family, documented in [§12.6](#126-sensors-gateways-plugs)).

### 15.3 Analyzed lighting / bulb devices

All four use the **default GATT UUIDs** (service `…1910`, write `…2b11`, notify `…2b10`) and the
standard framing. Sub-mode bytes below are the `subModeCommandType()` values under command `0x05`.

| Split | Models (SKUs) | Category | color / scene / music / DIY / video sub-mode | Notable device opcodes |
|-------|---------------|----------|-----------------------------------------------|------------------------|
| **`pact_h60ax`** | H60A0, H60A1/H80A1, H60A4/H80A4, **H60A6**, H60C1, H1270, H1232, H1250, H1252, H12D0 | RGBIC ceiling / **dual-zone glide/panel** (BLE+WiFi) | `0x15` / `0x04` / `0x13` / `0x0A` / — (H60A6 uses color-sub `0x0D` for part-color) | **zone power `0x30`** `[zone,state]` (readable, bit 1); **bulk status `0xAC`**; per-seg color read `0xA5`; on/off-memory `0x41`; child switch `0x36`; head calibration `0x42` |
| **`pact_h6047`** | H6047, H6043, H6042 (TV light bars), H6039, H6038 (glide bars), H6048/H8048 (hexa/wall panels) | RGBIC TV light bars / panels (BLE+WiFi) | `0x15` / `0x04` / `0x13` / `0x0A` / **`0x00`** | **compose/bar switch `0x36`** (H6047 `[left,right]`; others `[position(0/1),state]`, read `AA 36`); swap `0x34`; camera pos/install `0x31`/`0x32`; light-direction `0x30`; calibration `0x44`; white-balance `0xA9` |
| **`pact_bulblightv3`** | bulbs: H6001–H601F, H6002/03/04/05/08/09, H8015/H801x, H14xx, H1401/H1501 (gens v1/v2/v3/h6001) | Smart **bulbs** (RGBWW / tunable-white); v1 = WiFi/IoT-only | `0x0D` (color+CCT) / `0x04` / mic `0x05` / `0x0A` (h6001 legacy color = `0x02`) | **color-temp** via sub `0x0D`; colorTemType `0x26` (H601EF); legacy brightness scaled 1–100→20–254 |
| **`pact_h61d3`** | H61B0–H61F6, H1A42/43/44/45, H1AB1/2/3, H1B6A, H6640/41, H703A/B | RGBIC strips / neon rope / net lights (BLE+WiFi) | `0x15` / `0x04` / `0x13` / `0x0A` / — | IC count read `0x40` / check `0x46` (H6640 `0x43`) / point `0x24`; secret `0xB1`/`0xB2`; take-photo `0x26`; video-recognition `0xED`; strip-length cut/cali (H703A/B) |

**Color-temperature (CCT) encoding — the key bulb capability** (`pact_bulblightv3`). White/tunable
bulbs set CCT inside the color sub-mode `0x0D`:

```
33 05 0D <R> <G> <B> <kelvinHi> <kelvinLo> <R2> <G2> <B2> .. CK
         └─ primary RGB ─┘ └ 2-byte kelvin ┘ └ mapped RGB ┘
```
- Kelvin is a 2-byte value (`BleUtil.getSignedBytesFor2`); range **2700–6500 K** on v3
  (`bulblightv3/pact/Support.java`), wider on some strips (H6640/41 2000–9000; H1Axx neon 1000–10000).
- For pure white the primary RGB (bytes 1–3) is `ColorUtils.toWhite()` and **bytes 6–8 carry the
  kelvin→RGB tint** from `Constant.getTemColorByKelvin(kelvin, range)[2]` (`makeSubModeColor4Kelvin`).
  Bytes 6–8 are `00 00 00` **only** as the out-of-table fallback; for in-range kelvins the app sends a
  computed tint there — a client that always sends `(0,0,0)` will differ on firmwares that use bytes
  6–8.
- **The tint table is a fixed lookup, not a formula** (`Constant.java:429`, static map `Z1`): ARGB→kelvin
  at **100 K granularity over 1000–10000 K** (the incandescent black-body ramp; e.g. 1000 K ≈ `#ff3300`).
  `getTemColorByKelvin` (`:1020`) **clamps** the kelvin to the device range, then requires an **exact**
  table hit — a kelvin that is not a multiple of 100 returns `(0,0,0)`. So a client should **round the
  requested kelvin to the nearest 100** (and clamp to the SKU's range) before lookup. The value written
  in bytes 4–5 is the clamped/snapped kelvin (big-endian, `getSignedBytesFor2(…, true)`), not the raw input.
- **Which schemes carry CCT:** the `[R,G,B, kHi,kLo, R2,G2,B2]` tint layout is the **`0x0D`** scheme
  (bulbs `bulblightv3`, lamps `tablelampv1`/`homelightv1`). RGBIC devices on the **`0x15`** scheme encode
  CCT differently — `SubModeColorV1` opType `SET_COLOR_TEMP_H60A1` → `[0x15, 0x01, R,G,B, kHi,kLo,
  tintR,tintG,tintB, <mask>]` (kelvin + tint + segment mask). The **`0x0B`** scheme (dreamcolor) has
  **no** CCT path at all. See §16.
- Legacy H6001 firmware has no kelvin field — it sends `33 05 02 FF FF FF 01 <R> <G> <B>` (white flag
  + approximated RGB).
- The IoT/WiFi path expresses the same as JSON `colorwc {color, colorTemInKelvin}` (v3) /
  `colorTem` (v1), and DIY CCT sub-effects use `[subEffectType, brightness%, kelvinHi, kelvinLo]`.

**H60A6 dual-zone resolution** (settles the earlier review). H60A6 is a two-zone light; each zone is
toggled with `[0x33, 0x30, zoneIndex(0|1), state]` and the whole state is read back either via the
`0xAC` bulk read (`AC 03 03 41 30 A5`) or the `0x30` notify (state in **bit 1** of each byte). It has
**13 segments**, color-temp **2700–6500 K**, DIY version 3, and part-color (equal-split) support
gated on protocol `(pactType 1, pactCode ≥ 2)`. Per-segment color arrives as `0xA5` reply groups of
`[segIndex, R, G, B]`. Full detail: `pact_h60a0/adjust/h60a6/VM4LightH60A6.java`.

**Encryption.** None of these lighting splits add payload encryption; they use the base XOR framing
plus (H61D3) the base Base64 secret-key pairing (`0xB1`/`0xB2`) and, for IoT, Base64-wrapped BLE
frames in the `ptReal` cloud command (not a cipher).

---

## 16. SubMode & device write-layout reference

Exact write-frame layouts for the `SubMode*`/controller classes, confirmed against source. These
complete the command surface that §7/§15 name but did not fully specify. All are the **payload**
after the `[proType, cmd]` header (colour/mode sub-modes ride inside `33 05 …`; others are their own
`cmd`). `CK` = XoR checksum @byte19; `.. ` = zero pad to byte 18.

### 16.1 Colour sub-mode layouts (under `33 05 <subByte> …`)

| Scheme (subByte) | Devices | Write layout | Source |
|------------------|---------|--------------|--------|
| **`0x02`** (legacy RGB) | old bulbs (h6001), older strips | `02 R G B <whiteFlag> R2 G2 B2` | `h6001/ble/SubModeColor` |
| **`0x0B`** (dreamcolor) | dreamcolor RGBIC | `0b R G B <mask0> <mask1> 00 00` — **no CCT, no per-seg brightness** | `dreamcolorlightv1/ble/SubModeColor.getWriteBytes` |
| **`0x0D`** (RGBWW) | bulbs (bulblightv3), lamps (tablelamp/homelight) | RGB: `0d R G B 00 00 00 00 00` · CCT: `0d FF FF FF <kHi> <kLo> <R2> <G2> <B2>` | `bulblightv3/ble/SubModeColor.getWriteBytes` |
| **`0x15`** (RGBIC, opType-tagged) | h60ax, h6047, h61d3, … | `15 <opType> …` — see below | `pact_h60a0/ble/v1/SubModeColorV1.getWriteBytes` |

**`0x15` opType (the byte after `0x15`)** — from `SubModeColorV1` (`OP_TYPE_*` constants):

| opType | Meaning | Payload |
|--------|---------|---------|
| `0x00` | apply mode | `15 00` |
| `0x01` | set colour (basic) | `15 01 R G B <mask0> <mask1>` |
| `0x01` | set colour (H60A1/H60A6 form) | `15 01 R G B 00 00 00 00 00 <mask0> <mask1>` |
| `0x01` | set colour-temp (H60A1/H60A6) | `15 01 R G B <kHi> <kLo> <tintR> <tintG> <tintB> <mask0> <mask1>` |
| `0x02` | set per-segment brightness | `15 02 <pct 0–100> <mask0> <mask1>` |
| `0x05` | set colour-temp (basic) | `15 05 <kLo> <kHi>` |

### 16.2 Segment-selection bitmask

Colour/brightness on the `0x0B`/`0x15` schemes end with a **2-byte segment bitmask**
(`BleUtils.makeBytes4SelectPosByOneBit` / `makeSelectedTwoBytes`): **bit *i* = segment *i***,
**0-based**, **LSB-first**, little-endian across the two bytes. "Whole device" = all segment bits
set — e.g. 13 segments → `ff 1f`. (`0x0B` uses the same scheme inline at bytes 4–5.)

### 16.3 Smart-plug relay switch (`33 01 …`, plug family)

Plugs use `SwitchControllerV2` (cmd `0x01`) with a **relay-mask payload**, not the light `01`/`00`:

```
33 01 11   relay0 ON       33 01 10   relay0 OFF        (payload = 0x10 | onBit)
33 01 22 / 20   relay1 ON/OFF   (base 0x20, onBit<<1)
33 01 44 / 40   relay2 ON/OFF   (base 0x40, onBit<<2)
33 01 FF / F0   all relays ON/OFF (base 0xF0, low-nibble = on-bits)
```
(`h5080/ble/controller/SwitchControllerV2.q()`.) Single-outlet plugs (H5080/82/83/85/89/5160/61) use
relay 0 → `0x11`/`0x10`. Lights keep the plain `33 01 01`/`33 01 00`.

### 16.4 Secret-key pairing (`0xB1` read / `0xB2` write)

`SecretKeyController` (shared by plugs, string lights, BBQ, …): write `33 b2 <secret…>`, read
`aa b1 …`. The stored `secretCode` is a **Base64 string that is decoded to raw bytes before sending**
(`q() = Encode.decryByBase64(secretCode)`) — the frame carries the **raw** secret (typically 8 bytes),
not the Base64 text. (`base2light/ble/controller/SecretKeyController.java`.)

### 16.5 Read-query payloads

Read frames are `AA <cmd> <p()>`, where `p()` is the read payload (default = none → `00`):

| Query | Frame | Note |
|-------|-------|------|
| power | `AA 01 00` | `SwitchController` doesn't override `p()` → `00`. A trailing `01` is a tolerated don't-care. |
| brightness | `AA 04 00` | as above (`BrightnessController`) |
| **mode** | `AA 05 01` | `AbsModeController.p()` returns `[1]` — the `01` **is** required here. |

**Mode read-back carries live kelvin.** The `AA 05 01` reply is a mode/sub-mode report; under colour
sub-mode `0x15` its layout is `aa 05 15 01 <kelvin u16 big-endian> …` — i.e. **kelvin at reply bytes
`[4:6]`**, a *different* layout from the CCT write (`33 05 15 01 FF FF FF <kelvin>`, no `FF FF FF` on
read). Parsed by `OpInfo4Detail.parseModeValidBytes` → `SubMode4Color` (`isColorTem` flag at value[0],
`kelvin = getSignedShort(v[1], v[2])`, big-endian). A client should read current CCT from here rather
than tracking it optimistically on write. (`base2light/pact/newdetail/OpInfo4Detail.java:744-813`,
`sub/SubMode4Color.java:55-59`.)

> These layouts were verified against the decompiled `SubMode*`/controller classes and reconcile the
> full command surface of the `govee_ble_local` client (closing review items §B/§C). The scene
> upload dialects (§4.4) and CCT tint table (§15.3) close the two blocking items (R2, V1); the
> scene placeholder/upload-vs-activate model + cloud `effect-strs` API are captured in
> `devices.yaml → scenes`.

---

## 17. Conventions, ABNF grammar & machine-readable artifacts

### 17.1 Documentation conventions

This spec aligns to recognised standards per layer, so it reads like a protocol spec rather than an
ad-hoc note:

- **GATT layer (§2–§3)** follows **Bluetooth SIG** service-spec structure — Service → Characteristics
  → Properties (Read / Write / Write Without Response / Notify) → Descriptors (§2.1).
- **Framing & grammar (§4–§7, §16)** use **RFC-style packet diagrams** (byte-offset rulers) and the
  **ABNF grammar** (RFC 5234) in §17.3.
- **Binary layer** is mirrored by a **Kaitai Struct** definition (`../govee_ble.ksy`) — a
  language-agnostic binary-format spec that generates parsers and HTML docs from one source.
- **Device catalogue** is mirrored by a **machine-readable registry** (`../devices.yaml`, validated
  by `../devices.schema.json`) intended to be the single source of truth the per-device tables
  (§12, §15) and a client library are generated from.

### 17.2 Machine-readable artifacts (package root — `../` from this `docs/` folder)

| File | Format | Purpose | Tooling |
|------|--------|---------|---------|
| `../devices.schema.json` | JSON Schema 2020-12 | Structure/validation for the device registry | `jsonschema` |
| `../devices.yaml` | YAML | Device registry: `name_parsing`, `advertisement`, `timing`, `command_catalog`, and per-family UUIDs/sub-mode bytes/opcodes/specs/confidence/sources | `yaml` + schema |
| `../govee_ble.ksy` | Kaitai Struct | Binary layout of the 20-byte frame + `0xA1`/`0xA3`/`0xAC`/`0xE7` variants, mode/colour payloads, and notify sub-types | `kaitai-struct-compiler` |
| `../govee_adv.ksy` | Kaitai Struct | Advertisement (manufacturer-data) parse: AD walk, company `0xEC88`, encrypted-flag, protocol version, pactType/pactCode | `kaitai-struct-compiler` |

Prose companions (in this `docs/` folder unless noted):
- **`SCENE_UPLOAD_ENCODING.md`** — the scene/DIY upload encoding + dispatch reference (paths,
  `parseSceneV1` table, per-family bypasses, re-serializer field layouts, chunking). Referenced from §4.4.
- **`USING_THE_KSY.md`** — how to compile and consume the `.ksy` in your own project (any target
  language): the decrypt → reassemble → parse pipeline, worked examples, and gotchas.
- **`../govee_reference.py`** (package root) — executable reference codec: the three things Kaitai can't do
  (decrypt AES-ECB+RC4 · reassemble `0xAC`/multi-frame · XOR checksum) + dispatch wiring for the parametric
  `.ksy` types, driven by `../devices.yaml → client_profile`. Doubles as the round-trip test harness.

Every claim in the machine artifacts cites decompiled Java `file:line`.

```console
# from the package root (spec/); the artifacts live there, this doc is in spec/docs/
# validate the registry against its schema
python -c "import json,yaml,jsonschema;jsonschema.validate(yaml.safe_load(open('devices.yaml')),json.load(open('devices.schema.json')))"

# generate a parser + docs from the binary spec
kaitai-struct-compiler -t python govee_ble.ksy      # → govee_ble_frame.py
kaitai-struct-compiler -t html   govee_ble.ksy      # → govee_ble_frame.html
```

> **Precedence.** Where this Markdown and the machine-readable artifacts disagree, the artifacts are canonical
> for their layer (registry for device facts, `.ksy` for byte layout) — the prose tables should be
> regenerated from them. The registry carries a `confidence` field (`high` = classes read directly,
> `medium` = survey, `low` = inferred) and `sources` citations per family.

### 17.3 Frame grammar (ABNF, RFC 5234)

`OCTET` is the core rule `%x00-FF`. The `checksum` (XOR of the preceding 19 octets) is a semantic
constraint ABNF cannot express; it is noted in comments.

```abnf
frame        = pro-type body checksum          ; exactly 20 octets
pro-type     = %x33 / %xAA / %xEE / %x3A       ; single-command / notify
             / %xA1 / %xA2 / %xA3 / %xAC       ; multi-packet dialects
             / %xE7                            ; encryption handshake
body         = 18OCTET
checksum     = OCTET                           ; = XOR(frame[0..18])

; ── single command (pro-type = %x33 write | %xAA read) ──
single-cmd   = command payload
command      = OCTET                           ; opcode (see command catalog §5)
payload      = *17OCTET
mode-cmd     = %x05 sub-mode *OCTET            ; command = 0x05
sub-mode     = OCTET                           ; device-specific (see registry)

; ── colour sub-mode payloads (ride under %x05) ──
color-legacy = %x02 3OCTET flag 3OCTET         ; R G B whiteFlag R2 G2 B2
color-0b     = %x0B 3OCTET seg-mask            ; dreamcolor
color-0d     = %x0D 3OCTET kelvin 3OCTET       ; RGBWW: RGB kelvinBE tintRGB
color-15     = %x15 op15 *OCTET                ; RGBIC, op-tagged
op15         = %x00 / %x01 / %x02 / %x05
seg-mask     = 2OCTET                          ; bit i = segment i, LSB-first, LE
kelvin       = 2OCTET                          ; big-endian
flag         = OCTET

; ── multi-packet 0xA1 (MultiPackageManager) ──
a1-start     = %xA1 com-type %x00 count 16OCTET
a1-data      = %xA1 com-type index 16OCTET
a1-end       = %xA1 com-type %xFF 16OCTET

; ── multi-packet 0xA3 (scene / DIY) ──
a3-start     = %xA3 %x00 %x01 count com-type 14OCTET
a3-data      = %xA3 index 17OCTET
a3-end       = %xA3 %xFF 17OCTET
com-type     = %x01 / %x02 / %x07 / %x0C       ; sceneType 1 / 2 / 3 / 6

; ── single-request / multi-reply read (0xAC) ──
ac-request   = %xAC command len-prefix 1*OCTET ; e.g. AC 03 02 41 30
len-prefix   = OCTET                           ; count of requested sub-commands

; ── handshake (0xE7) ──
hs-frame     = %xE7 hs-op *OCTET
hs-op        = %x01 / %x02                      ; V1 (AES) request / confirm
             / %x11 / %x19 / %x1A               ; V2 (AES-GCM)

count        = OCTET
index        = OCTET
```

---

## 18. Device identification, protocol selection & pairing

How the app recognises a device over the air and decides which protocol dialect to speak.

### 18.1 Scan & filter

`ScanManager` + `BleScanCallbackImp21` (API 21+) / `LeScanCallbackImp` (legacy) deliver results as
`ScanEvent{ device, byte[] scanRecord(62), rssi }`. Results are filtered by **MAC**, advertised
**name**, **service UUID**, and **RSSI** (≈3 s debounce; per-family min-RSSI, e.g. −45)
(`ble/scan/*`).

### 18.2 Name → SKU

The advertised name encodes the SKU; `BaseBleProcessor` (`base2home/main/choose/BaseBleProcessor.java`)
derives `skuCode` by prefix:

| Prefix | Rule | Example → skuCode |
|--------|------|-------------------|
| `ihoment_` / `Govee_` / `Minger_` | `split('_')` = `[brand, skuCode, tail]` | `ihoment_H6159_1A2B` → `H6159` |
| `GBK_` | `split('_')` = `[GBK, skuCode, tail]` | `GBK_H5083_1234` → `H5083` |
| `GVH` / `GVR` | `substring(2)`; skuCode up to `_` or 5 chars | `GVH6159_XXXX` → `H6159` |
| `GV` (fallback) | `skuCode = "H" + substring(2,6)` | `GV5122ABCD` → `H5122` |

### 18.3 SKU → goodsType → (pactType, pactCode) → dialect

`Pact.getInstance().d(skuCode)` → **goodsType** (integer id). `GoodsType.parseBleBroadcastPactInfo`
+ `Pact.c(goodsType)` pick the **highest supported `(pactType, pactCode)`**, which selects the
controller/mode dialect (e.g. scene `versionArray` → `comType`, [§4.4](#44-scene--diy-upload-dialects-0xa3)).
The registry records `goods_types` per family so a client can map advertisement → family.

### 18.4 Capability discovery at connect

- **`BgcInfoReader`** reads `encryptVersion` from BGC-info char `…2b12` on service `…1910`:
  `0` = none, `1` = AES (§9.1), `2` = AES-GCM (§9.3) → gates `isEncryptionSupported`.
- **`isFastConnectSupported`** decides the notification-enable path ([§3](#3-connection-lifecycle));
  cached in `ShortMemoryMgr`.
- **`SINGLE_PACT` (`0xEF`)** negotiates the protocol/pact at the application layer.

### 18.5 Pairing & the per-device secret (`secretCode`)

Families tagged `encryption: secret_key_pairing` (e.g. H60A6, H6641, H5083, string lights, hollow
lamps) gate control on a **per-device 8-byte secret** ("`secretCode`"). This is Govee's anti-hijack /
"safe bind" mechanism — it is **not** the AES cipher key ([§9](#9-encryption--session-key-handshake);
that is a fixed PSK). After connecting, a client must present this secret or the device ignores
commands.

**What it is:** 8 bytes, device-generated. Base64-encoded when stored/transported; raw 8 bytes on the
wire. Exchanged with `SecretKeyController(V1)`:
- **read `0xB1`** — device → app: reply `[0x01]` + 8 secret bytes (`SecretKeyControllerV1.parseValidBytes`, `:44`).
- **auth write `0xB2`** — app → device: the 8 raw bytes, sent right after connect to authorize the session.

**How the app obtains it** — two paths, plus a local cache:

1. **Original pairing client** (`AbsPairAc4SecretV1`): connects, **reads `0xB1`** from the device
   (`:115` sends a read when no secret is known; `:427` stores the returned `secretCode`), caches it
   in `SecretKeyConfig` (a persisted `HashMap<bleAddress, secretCode>`), then **binds** the device —
   uploading the secret to the account.
2. **Any other client** (second phone, reinstall, or a third-party local client): fetches the secret
   from the **account device record** and then **writes `0xB2`** to authorize.

**Cloud API (account-authenticated).** Two hosts from `Constant.java` (prod; dev/pda variants exist):
the **app API** `app.govee.com` (`:165`, account-level calls) and the **device API**
`device.govee.com` (`:177`). The `device/rest/…` paths below are served by **`device.govee.com`**, not
the app host.

| Purpose | Host + Method / path | Where the secret is |
|---------|----------------------|---------------------|
| **Upload (bind)** | `device.govee.com` — `POST device/rest/devices/v1/bind` (multi: `…/v1/multi-bind`) | inside the `deviceExt` JSON of `DeviceBindRequest` — `BindExt.secretCode = scanBleInfo.j()` (`AddMultipleDeviceReq.java:118`) |
| **Retrieve** | `device.govee.com` — account device-list (returns each device's `deviceExt`/`bleExt`) + `device/rest/devices/v1/settings` | `bleExt.getSecretCode()` → surfaced via `DefInfo`/`ExtInfo` (`DefInfo.java:98`) |

> **So: is it "the per-device secret you pull from the cloud"?** Yes. For any client that did not do
> the original BLE pairing, the practical source is the **Govee account API device record
> (`bleExt.secretCode`)**; the client then writes it back over `0xB2`. Endpoint paths from
> `UrlConstants.java` (`f30910o` bind, `f30929x0` multi-bind, `f30928x` settings). The exact modern
> device-list path is served by the account device service (`IDeviceNet`); the `secretCode` travels
> in the per-device `deviceExt`/`bleExt` blob either way. See `devices.yaml → secret` for the
> machine-readable version.

### 18.6 Capability provenance — hard-coded per `goodsType`, not a cloud profile

How the app maps a device to its protocol/capabilities (decompiled-verified):

- **Identity** = `{sku, goodsType, pactType, pactCode, spec}`, from the cloud device-list (`AbsDevice`,
  `base2home/main/AbsDevice.java:33-44`) **or** derived **offline from the BLE advertisement**: local name
  `Govee_<SKU>_<suffix>` → SKU → `goodsType` via a compiled table (`Pact.d(sku)` →
  `Constant4L5.hasKnowSupportGoodsType`); `pactType`/`pactCode` parsed from advert bytes
  (`GoodsType.parseBleBroadcastPactInfo`, `BaseBleProcessor.c():98-105`). No cloud call is needed to identify a device.
- **Handler selection** = `goodsType` (+ `pactType`/`pactCode`) into the **compiled** `Pact.b(goodsType, Protocol[])`
  registry (`base2home/pact/Pact.java`), registered at startup per family `*ApplicationImp`; `ModelMaker` for UI.
  Nothing is fetched at runtime — `goodsType` is purely a compile-time dispatch key.
- **Capabilities are hard-coded per `goodsType`** in the family `Support`/`Config` classes — segment count
  (`getColorPieceSize` / `ColorPieceConfig`), kelvin range, opcode/effect sets — gated by firmware-version
  string compares. **There is no cloud capability/spec profile.** The cloud endpoints that look capability-ish
  are not: `devices/ic-settings` = a UI tutorial (`Guide{popDes, guideUrl}`); `querySkuResource` = theme images;
  the `updateDeviceIc` / `updateDeviceSegmentCount` POSTs push *locally-determined* values UP for persistence.
- **The only capability read live from the device** is the **IC / segment count**, via BLE opcode **`0x40`**
  (`ControllerOnlyReadIcSegmentNum` → `{IC count u16, segment}`; `ControllerIcNum` → IC-group list; refresh `0x42`).
  IC-driven families (h61d3/H6641) compute segment count from it (`H61D3Support.e()` → `ceil(IC/d)`, `d`∈{3,4});
  static families (H60A6, H6047) ignore it and use the hard-coded `getColorPieceSize`.

**Implication for a local (no-cloud) client:** derive `goodsType` from the advertisement, then key a compiled
per-`goodsType` table (exactly as the app does) for mechanism + record layout + static counts; read `0x40` live
only for the IC-driven families. The cloud is needed only for account niceties (persisted names, shared settings,
`bleExt.secretCode` retrieval), never for protocol/capability decisions.

## 19. Advertisement / manufacturer-data structure

Passive (connectionless) identity + state, parsed from the 62-byte scan record
(`base2home/pact/BleUtil.java:829` `parseBleBroadcastPact`). Walk AD structures `[len][type][data]`;
in the **manufacturer AD (type `0xFF`, len ≥ 6)**:

```
data offset:  +0        +1  +2        +3  +4        +5  +6      +7
            ┌─────────┬───────────┬───────────────┬────────────┬──────────┐
            │  flags  │ company id (0x88 0xEC)     │ pactType   │ pactCode │
            └─────────┴───────────┴───────────────┴────────────┴──────────┘
  flags: bit6 (0x40) = encrypted ;  low nibble (0x0F) = protocol version (≥1)
  pactType = u16 big-endian ;  pactCode = u8
```

- **Custom layout note:** the `flags` byte *precedes* the `0x88 0xEC` marker — this is not a standard
  leading 2-byte company identifier, so the payload is only meaningful once `0x88 0xEC` is matched.
- **Sensors** (thermo/hygro, §11) use a **service-data (type `0x03`) prefix** variant of the same walk.
- **Passive state listener:** `EventBleBroadcastListenerTrigger` toggles a broadcast listener that
  reads presence/protocol/state from advertisements without connecting.
- Machine-readable model: [`govee_adv.ksy`](../govee_adv.ksy).

## 20. Timing & reliability

All constants verified against source (`base2light/ble/comm/ControllerComm.java:20-38`,
`ble/BleController.java:41-47`); mirrored in `devices.yaml → timing`.

### 20.1 Constants

| Scope | Constant | Value |
|-------|----------|-------|
| Connection | connect timeout | 60 000 ms |
| | service-discovery timeout | 180 000 ms |
| | reconnect window | 15 000 ms |
| | auto-disconnect GATT status | 19 |
| Command | read timeout / write timeout | 3 000 ms / 6 000 ms |
| | read retries / write retries | 3 / 6 |
| | read interval / write interval | 100 ms / 200 ms |
| | encryption-handshake timeout | 6 000 ms |
| | fail-count → disconnect | > 10 |
| Multi-packet | inter-packet delay | 300 ms |
| OTA | inter-packet delay / retry max | 30 ms / 2 |
| MTU | default / requested | 23 / 512 |

### 20.2 Send → ACK → next (state machine)

Writes use **write-with-response**. Controllers queue in `AbsBleComm`; `ControllerComm.j()` posts a
`RunnableOvertime` (3 s read / 6 s write) and runs `RunnableSendMsg` (retry loop, 3×/6× at 100/200 ms).

```
enqueue ─▶ startNext ─▶ ControllerComm.j ─▶ post RunnableOvertime(3s/6s)
                                        └─▶ RunnableSendMsg  ──gatt.writeCharacteristic (with-response)
   ┌──────────────────────────────────────────────────────────────────────┐
   ▼                                                                        │
onCharacteristicChanged(resp) ─▶ match echoed [proType,cmd] & value[2]==0 ─▶ remove from queue ─▶ startNext
   ▲                                                                        │
   └── timeout: RunnableOvertime ─▶ Event4ResponseOverTime ─▶ retry; if fail-count>10 ─▶ disconnect ─┘
```

Heartbeat: `AbsHeartRunnable` polls periodically (per-device) with `SINGLE_HEART 0x01` to keep the
link alive; GATT status `19` → `EventAutoDisconnect`.

## 21. Expanded command catalog

The **authoritative, machine-readable catalog** is `devices.yaml`:
- top-level `command_catalog` — the shared opcodes (68 entries: power/brightness/mode, settings,
  timers, device-info, IC, scene, DIY, music library `0x70–0x7A`, feast, video/camera, calibration,
  secret, OTA/pact, wifi, daySync, alarm), each with opcode/category/direction/payload/meaning;
- per-family `opcodes[]` — device-specific commands.

This replaces the earlier "~40 opcodes" gap: the shared inventory now names every
`BleProtocolConstants` opcode (≈158 constants, many sharing byte values resolved by context), with
byte-level payloads for the curated families. Highlights of the curated families' device-specific
commands (full detail in the registry):

| Family (example SKU) | Notable device opcodes |
|----------------------|------------------------|
| `h6047` (H6047) | compose/bar switch `0x36`, swap `0x34`, camera `0x30/0x31/0x32`, calibration `0x44`, white-balance `0xA9` |
| `h60ax` (H60A6) | zone power `0x30`, per-segment colour read `0xA5`, on/off-memory `0x41`, child `0x36`, calibration `0x42`, bulk status `0xAC` |
| `h61d3` (H6641) | IC read `0x40` / check `0x46` / cut-cal `0x43`, IC-point `0x24`, take-photo `0x26`, video-recognition `0xED`, secret `0xB1/0xB2` |
| `dreamcolorlightv1` (H61A8) | limit `0x0E`, light-num `0x0F`, gradual `0x14`/`0xA3`, IC `0x40/0x42/0x46`, bulb-colour read `0xA2` |
| `bulblightv3` (H6008/H6006) | colour+CCT sub-mode `0x0D`, colour-temp-type `0x26` (H601EF); legacy `0x02` |
| `tablelampv1` (H6052) | light-indicator `0x16` |
| `h5080` (H5083 plug) | relay switch `0x01` (mask), sync-time `0xB5`, timer `0xB4` / V2 `0x13`, delay `0xB0`, spec `0xB3`, timer-count `0x12`, timer-delete `0x15`, not-disturb `0x16`, child-lock/indicator `0x1F`, OTA `0xAB` — **no `0x05` mode** (plug) |
| `h512x` (H5121/H5129 motion, H5130 pressure) | sensitivity `0x01` / interval `0x02` (H5121/H5129), light-sensor `0x03` (H5129); pressure `0x02–0x06` (H5130). Buttons **H5122/H5125/H5126 have no settings command set** (`goToSettingPage()=false`) — triggers arrive via cloud push |

---

*Generated from static analysis of the decompiled APK, then cross-validated against a live client
(§14), extended with the on-demand `pact_*` device feature splits (§15), completed with the
per-scheme write-layout reference (§16), formalised as machine-readable artifacts (§17), and
rounded out with device identification (§18), advertisement parsing (§19), timing & reliability
(§20), and the expanded command catalog (§21). Byte values and UUIDs are cited to their source
files; sub-mode bytes and device-specific opcodes were extracted per package and should be verified
against the specific firmware revision when implementing.*
