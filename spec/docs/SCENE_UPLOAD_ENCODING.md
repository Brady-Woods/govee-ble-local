# Govee scene/DIY upload ENCODING — implementer reference (G1–G7 + Aurora)

Source-grounded answers to the scene-upload encoding brief. Every claim cites decompiled Java
(`file:line`) under this repo; hardware = the H61A8 plaintext btsnoop. Companion to
`GOVEE_BLE_GATT_PROTOCOL.md` §4.4 (prose) and `devices.yaml scenes:` (machine-readable).

---

## 0. READ FIRST — there are TWO upload paths, and they encode differently

Conflating them is the main trap. Both use the 20-byte `0xA3` `makeSendBytesV1/V2` framing, but the
**commByte** and the **value** are produced completely differently:

| | **Path A — DIY editor** (dialect B) | **Path B — scene library** (dialect A) |
|---|---|---|
| Trigger | user edits+saves a DIY/graffiti effect | user picks a built-in/library scene (e.g. **Aurora**) |
| Entry | `OpDiyCommDialog4BleV2` / `IDiyParse.makeBleController` | `BaseSceneViewMode` → `ScenesOp.parseSceneV1` |
| Controller | `MultiDiyGraffitiController` (or device parser) | `SceneControllerNoEventV1` wrapping `MultiNewScenesController*` |
| **commByte@4** | device **protocol code** (`getProtocolCode`): H61A8=`0x03`, H60A6=`0x58`(88) | **scene-version comType**: V1=`1`, V2=`2`, V3=`7`, V6=`12` |
| **value** | **RE-ENCODED** model — `toBytes()` / `DiyGraffitiV2.g()` | base64-decoded `scenceParam` **~verbatim** (graffiti/V3 strip leading 2 bytes) |
| §1 capture | **this is what the H61A8 capture shows** | not captured; Aurora would be this |

The §1 H61A8 frame (`a3 00 01 10 03 …`) is **Path A**. A library scene like Aurora is **Path B** — do
**not** encode it with `DiyGraffitiV2.g()`.

---

## 0b. Per-device apply path — which parser each curated family uses (⚠️ TWO bypass `parseSceneV1`)

`parseSceneV1` (§Q4 table below) is **not** universal. Two curated families take a different entry, and
if you drive them off `parseSceneV1` + their `supportScenesOp` version array you will get the wrong
answer. The machine-readable form is `devices.yaml scenes.dispatch.family_apply`.

| family (curated SKU) | version array (static, per goodsType) | apply path | result |
|---|---|---|---|
| **h60ax** (H60A6) | `{0,1,2,3,5}` | `parseSceneV1`; graffiti/DIY (type 5) via the **DIY dialect** (§G5) | type 1/2/3 → comByte 1/2/7; graffiti/DIY → `0x58`, `0xA3`/`0xA4` |
| **h6047** (H6047) | `{0,1,2,3}` | `parseSceneV1` | type 1/2/3 → comByte 1/2/7 |
| **h61d3** (H6641) | `{0,1,2,3,10}` | `parseSceneV1` | type 5 v10 → CommonDiy byte0-dispatch |
| **dreamcolorlightv1** (H61A8) | `{1,2}` | `parseSceneV1(str, scene, 1, 2)` | type 1/2 → comByte 1/2 |
| **tablelampv1** (H6052=gt22 / H6078=gt128) | H6052 `{0,1,4,5}` · H6078 `{0,1,5,9}` | **type 5 → `Diy.parseScenes4Diy` (bypass)**; else `parseSceneV1` | see Q2/Q3 |
| **bulblightv3** (H6006, H6008) | `supportScenesOp` else-branch = **`{0}`** | **`parseScene(sceneM,{1})` / `parseEffect(…,{1})` — hardcoded `{1}` (bypass)** | see Q1 |

### Q1 — bulbs upload type-1 RGB (they are **not** activate-only); `{0}` is the wrong gate
`bulblightv3` apply = `Comm.makeSceneCmd4IotComm` → `Support.is2NewScenesMode` (`bulblightv3/pact/Support.java:137-147`):
```java
Category.Scene sceneM = ScenesM.p.m(sku, sceneCode);          // local catalog lookup
return sceneM != null ? ScenesOp.parseScene(sceneM, {1})      // blob present  -> UPLOAD
                      : ScenesOp.parseEffect(sku, code, {1}); // blob absent   -> activate-by-code
```
The `versionArray` here is a **hardcoded `{1}`** — `supportScenesOp` (which returns `{0}` for H6006/H6008,
`:322-323`) is **not read on the apply path**. `parseScene(sceneM,{1})` → `contains(1)&&sceneType==1` →
`parserScenes4Rgb` → **`MultiNewScenesControllerV1` (comByte `0x01`, strip 0, `0xA3` multi-frame)** once the
blob validates (`ScenesRgb.isValidProtocolBytes`). **⇒ the 55 type-1 RGB library scenes UPLOAD.** The `{0}`
from `supportScenesOp` is a UI/DIY-editor capability value (fed to `ScenesConfigBean`), not an apply gate.
**If your `_LIBRARY_SCENE` map blocks the bulbs, this is why:** it keyed on the device's `{0}`; the app
forces `{1}` for the RGB-library apply. Treat version 1 as always-available for bulbs (upload iff the blob
is present/valid; else activate-by-code).

### Q2 — H6052 cannot upload type-3 graffiti
H6052 = goodsType 22 (`isH6078(i)=i==128`, `Support.java:130`), `supportScenesOpSet(22)={0,1,4,5}` (`:193`).
Apply (`H6052ScenesViewMode.java:45-47`): `sceneType==5 ? Diy.parseScenes4Diy : null`, then
`parseSceneV1(type, code, param, {0,1,4,5})`. For a **type-3** scene the Diy branch is null (type≠5) and
`parseSceneV1` needs `contains(3)` — `{0,1,4,5}` lacks it → **`null` → not uploadable**. H6052's graffiti is
delivered as **type 5** (Q3), never type 3; type-3 library graffiti is simply incompatible with H6052.

### Q3 — H6052 type-5 comByte `9` = `DiyGraffitiV3.a()`, a **re-encode** (not a strip)
`Diy.parseScenes4Diy` (`tablelampv1/adjust/Diy.java:98`) tries `parseDiyProtocol` → `parseDiyGraffiti` →
`parseDiyGraffitiProfessional`(→`DiyGraffitiV3`) and wraps it in `MultipleDiyInScenesController`
(`:13-25`): comByte **3** = simple `DiyGraffiti.b()`, **4** = `DiyProtocol.toBytes`, **9** =
professional **`DiyGraffitiV3.a()`**. `a()` (`DiyGraffitiV3.java:154-173`) **rebuilds the bytes from the
parsed object graph** (recomputing per-layer length prefixes) — it is a genuine parse→re-encode, **not** a
header slice. Layout is in §G2 (`[brightness, R,G,B, layerCount] + per-layer[(len-2)u16LE, speed, action,
priority, nColors, {count,R,G,B, idx×count}×nColors]`), modelled as `graffiti_v3_value` in the ksy.
**Practical shortcut:** for canonical catalog params, `a()` is byte-identical to `decode(scenceParam)[1:]`
(strip the single `0x13` header) — so uploading an existing library scene needs no re-implementation of
`a()`; decode, drop byte 0, chunk with comByte 9. (Re-encode from scratch only when *creating* an effect.)
Do **not** conflate this comByte-9 Diy-in-scenes framing with H6078's version-9 library path
(`parseScenes4H6078Graffiti` → `MultiNewScenesControllerV7`, comByte **12** + a `subOpType` payload byte).

### Implementer checklist (no Java required)
1. Carry from the scene-library DTO: `sceneType`, `sceneCode`, `scenceParam` (+ `scenceParamId`,
   `bigEffectStr`). `versionArray` is **not** in the cloud/BLE — hardcode it per family from
   `devices.yaml scenes.dispatch.family_apply`.
2. Pick the apply path by family (`family_apply.path`): most → `parseSceneV1` (use `dispatch.table` below,
   §Q4); **bulbs → force `{1}`, type-1 only**; **H6052 type-5 → Diy path, comByte 9**.
3. If a row matches: `value = decode(scenceParam)` then apply the row's `strip`; build the `0xA3`
   (or `0xA4`-MTU for H60A6 graffiti, §G5) multi-frame with that comByte. Else: **activate-only**
   (`33 05 04 <sceneCode LE>`). The `33 05 04` activation is sent in **both** cases.
4. Frames are zero-padded to 20 B — never infer field ends from length; use the explicit counts/prefixes.

### Q4 — the `parseSceneV1` dispatch table (version, sceneType) → controller / comByte / strip
`ScenesOp.parseSceneV1(sceneType, sceneCode, scenceParam, versionArray)` (`ScenesOp.java:603-640`) — strict
AND-gate: first row where `versionArray.contains(version) && sceneType==type`; **no match ⇒ `null` ⇒
activate-only**. comBytes = each controller's `getCommandType()`.

| version | sceneType | handler (line) | controller | comByte | strip |
|:--:|:--:|---|---|:--:|---|
| 1 | 1 | `parserScenes4Rgb`→`l()` (774/439) | V1 | `0x01` | 0 |
| 2 | 2 | `parserScenes4Rgbic`→`m()` (456) | V2 | `0x02` | 0 |
| 3 | 3 | `parserScenes4Graffiti`→`n()` (473) | V3 | `0x07` | 2 (`:486`) |
| 6 | 6 | `parserScenes4Compose`→`o()` | V5 (H60B0 if byte0=0x51) | `0x0a` | 1 |
| 7 | 5 | `parseScenes4H610B` (697) | V6 | `0x0c` | 1 (also overwrites `value[3]=speed`) |
| 8 | 5 | `k()` (624) | V7 | `0x0c` | 1 + prepend `subOpType` |
| 9 | 5 | `parseScenes4H6078Graffiti` (675) | V7 | `0x0c` | 1 + prepend `subOpType` (3 simple / 9 prof) |
| 10 | 5 | `CommonDiyParseConfig.getSceneControl` (636) | V7/V4/MultiDiyFixed by byte0 | `0x0c/0x04/0x58` | compound |
| 11 | 5 | `parseScenes4H6092` (689) | V8 | `0x56` | 1 + prepend `subOpType` |
| 12 | 5 | `parseScenes4H6022Graffiti` (666) | byte0∈{0x40,0x41}?V10:V4 | `0x58 / 0x04` | 0 / 1 |
| 13 | 5 | `parseScenes4H1630Graffiti` (658) | V10 | `0x58` | 1 |

**Bypasses** (do not use `parseSceneV1` for these): **bulbs** — `is2NewScenesMode` (`bulblightv3 Support.java:137-147`)
calls `parseScene(sceneM,{1})`/`parseEffect(…,{1})` with a hardcoded `{1}`, ignoring `supportScenesOp={0}` ⇒
type-1 RGB uploads (V1/`0x01`). **H6052 type-5** — `H6052ScenesViewMode:45` → `Diy.parseScenes4Diy` →
`MultipleDiyInScenesController` (comByte 3 simple / 4 protocol / 9 professional `DiyGraffitiV3.a()`); its
`{0,1,4,5}` lacks version 3 so type-3 graffiti is not uploadable.

---

## G1 — commByte derivation
**Rule:** commByte@byte 4 = `controller.getCommandType()`, a `BleProtocolConstants` code, copied to
offset 4 by the builder (`MultipleControllerCommV1.makeSendBytesV2:802`). It returns a **constant** per
controller (never a payload byte).

> ### ⚠️ INPUTS — the commByte is NOT computable from the `scenceParam` blob
> For a **library scene (Path B)** the commByte is a function of metadata that lives **outside** the
> blob. You must carry, from the scene-library API + device:
> - **`sceneType`** — API DTO field (`CategoryV1.LightEffect.sceneType`, `:112`; via `getSceneType`, `:331`).
> - **`versionArray`** — a **static per-`goodsType` constant** (NOT a cloud field, NOT a BLE read).
>   `ScenesConfigBean.j()` (`:224`) returns it, but the ints are hardcoded per-SKU (e.g.
>   `pact_h60a0/pact/Support.supportScenesOpSet:461`). A client obtains only `goodsType` from the cloud
>   device record (`AbsDevice.goodsType`) and hardcodes the rest. **Concrete: H60A6 (goodsType 303) =
>   `{0,1,2,3,5}`** (`supportScenesOpSet`); **H61A8 = `{1,2}`** (hardcoded at
>   `dreamcolorlightv1/pact/Support.is2NewScenesMode:1108` — dreamcolor uses the legacy scene path, not
>   `ScenesConfigBean`). Only needed to disambiguate `sceneType==5`. *(Note: `parseSceneV1` tests
>   `.contains(1/2/3/6/7…)`; the `0` and `5` in H60A6's array match no branch, so H60A6's dialect-A
>   scenes are effectively sceneTypes 1/2/3 → comType 1/2/7; its graffiti effects use Path A instead.)*
>
> `ScenesOp.parseSceneV1(sceneType, sceneCode, scenceParam, versionArray)` (`:603-640`) dispatches on
> `(versionArray.contains(V) && sceneType==T)` → a controller whose `getCommandType()` **is** the commByte.
> With the blob alone you cannot compute byte 4. (If your client "can't compute the commByte," this is
> almost always the cause: it hasn't carried `sceneType`.)

**Path B (library) — full `sceneType → commByte` map:**

| `sceneType` | needs `versionArray`? | controller | **commByte** |
|:--:|:--:|---|:--:|
| 1 (rgb) | no | `MultiNewScenesControllerV1` | **1** |
| 2 (rgbic) | no | `MultiNewScenesControllerV2` | **2** |
| 3 (graffiti) | no | `MultiNewScenesControllerV3` | **7** |
| 6 (compose) | no | `MultiNewScenesControllerV5` (`H60B0` if `byte0=0x51`) | **10** (or `90`) |
| 0 (static), 4 (cube) | — | *no `parseSceneV1` branch* → `null` → **activate-only** | (none) |
| **5 (diy)** | **yes** | version-gated sub-dispatch ↓ | **4 / 12 / 86 / 88** |

For `sceneType==5`, the commByte depends on which `versionArray` entry matches (and, for versions 10/12,
the blob's `byte[0]`), all `getCommandType()`-constant (`ScenesOp.java:617-636`, `CommonDiyParseConfig.java:107-146`):

| `versionArray` has | helper | controller | **commByte** |
|:--:|---|---|:--:|
| 7 | `parseScenes4H610B` | `…V6` | **12** |
| 8 | `k()` | `…V7` | **12** |
| 9 | `parseScenes4H6078Graffiti` | `…V7` | **12** |
| 10 | `CommonDiyParseConfig.getSceneControl` | byte0-dispatch: `V7`/`V4`/`MultiDiyFixedDeviceController` | **12 / 4 / 88** |
| 11 | `parseScenes4H6092` | `…V8` | **86** |
| 12 | `parseScenes4H6022Graffiti` | byte0∈{64,65}?`V10`:`V4` | **88 / 4** |
| 13 | `parseScenes4H1630Graffiti` | `…V10` | **88** |

(Controller comTypes verified: `MultiNewScenesControllerV1/2/3/4/6/7/8/10.getCommandType()` = 1/2/7/4/12/12/86/88;
`MultiDiyFixedDeviceController` = 88. The `subOpType` constructor arg (3/9/11/83/84/86/90) is a **payload
field, not the commByte**. Version-10 is also the branch that can escalate to `0xA4`-MTU via
`useMtuController()` — see G5.)

**Path A (DIY editor)** commByte = the matched parser's `getProtocolCode()`, derivable from the blob's
`byte[0]`: `MultiDiyGraffitiController.getCommandType()=(byte)3` (`:31-33`) for H61A8; `88`(`0x58`,
`MULTI_CUBE_IN_DIY`) for H60A6. Code map (`BleProtocolConstants`): `MULTI_DIY=2`,
`MULTI_V1_NEW_DIY_GRAFFITI=3`, `MULTI_V1_NEW_DIY=4`, `MULTI_CUBE_IN_DIY=88`, `MULTI_COMBINATION_EFFECT=98`.

- **NOT `value[0]|0x08`** — H61A8's `0x03` has bit `0x08` clear; proven counterexample.
- **Multi-byte commBytes:** `makeSendBytesV2` takes `commBytes[]` of length `L`; START inlines `15−L` value bytes. The 2-byte case is `makeWriteMultipleBytes(AbsMultipleControllerV2)` passing `{commandType, p()}` (`:919`). Path A/H61A8 uses `L=1`.

## G2 — value re-serialization

> ### ⭐ Practical rule (verified byte-exact): uploaded value = `decode(scenceParam)[1:]`
> For the **dialect-B / DIY upload paths** (H60A6 graffiti `0x58`, H60A6 DIY `0x58`, H6052 type-5 cmd `9`),
> the effect re-serializers **round-trip canonical catalog params losslessly, dropping only the single
> leading header byte**. So the on-wire value = **base64-decode(`scenceParam`) with byte 0 stripped**
> (`0x50` for H60A6, `0x13` for H6052). Proven `==` byte-for-byte on **Aurora** (H60A6 graffiti, 187 B),
> **Christmas** (H60A6 DIY, 56 B), **Dark Clouds** (H6052, 57 B). **To upload an existing library scene a
> client need NOT reimplement any serializer — decode, drop byte 0, chunk.** (Holds only for canonical
> catalog params; *creating/editing* an effect from scratch needs the real field encoders below.)
>
> Re-serializer field layouts (for from-scratch builds / verification):
> - **H60A6 graffiti** (`H60A6GraffitiParse.toBytes:310`): `[0x20, bgR,bgG,bgB, brightness, showType, layerCount]` + per-layer `[recLen(u16LE), 0x03, innerLen(u16LE), colorCount(u16LE), {pixCnt(u16LE),R,G,B, idx×pixCnt}×N, action, speed, bgBrightness, priority, duration(u16LE), 00 00 00 00]`.
> - **H60A6 DIY** (`Pro4H60A6Diy.d:171`): `[totalLen(u16LE), layerSize, bgColorSize, {R,G,B}×bgColorSize, bgBrightness]` + per-layer `[layerLen(u16LE), subEffectId, sub-effect body]`. The **sub-effect body is a discriminated union on `subEffectId`** (`Layer.b`, `.../h60a6/diy/protocol/*`): `1 LiuDong4Area (flow/zone), 2 LiuDong4Line (flow/line), 3 KuoSan (diffuse), 4 XuanZhuan (rotate), 5 SuiJiHuXi (random breathing), 6 SuiJiLiuXing (random meteor), 7 SuiJiJianBian (random gradient), 8 Fade, 9 HuXi (breathing), 10 ShanShuo (blink/twinkle)` — pinyin = decompiled class name, English = translation. All but LiuDong4Line = `[fixed params (N bytes: 14/8/5/3/6/3/2/2/2 per id)][colorCount(u1)][colorCount × RGB]`; LiuDong4Line = `[i,dir,on][segCount(u1)][segCount B][l0,l1,r,t,s][colorCount(u1)][colorCount × RGB]`.
> - **H6052** (`DiyGraffitiV3.a:154`): `[brightness, R,G,B, layerCount]` + per-layer `[(layerSize-2)(u16LE), speed, action, priority, nColors, {count,R,G,B, idx×count}×nColors]`.
> - **dialect-A rgbic** (parser `ParamsV2.RgbICEffect:967`; `ScenesRgbIC.f:3337` is only a length-validator): `[effectCount(u1)]` + per-effect `[subLen(u1), record]`, record = `[style(nibbles), mode, mode_val(2), bright_algo, bright_count(u1), BrightnessEffect(6-B)×bright_count, colorIc, speed, duration, color_count(u1), RGB×color_count, InAreaMove(3-B), AreaMove(4-B)]`. The **RGB palette is `color_count × 3-B`** (the actual colours); the 6-B records are per-`ParamsV2` BrightnessEffects, not colours.
> - **dialect-A graffiti** (`ParamsV1` / `DiyProtocolParser.parserParamsV1:1345`; H6052 sceneType-3, wire value = `decode(param)[2:]`, strip drops `[0x01, effect_hi]` per `ScenesOp.n():483`): `[effect_lo, speed, brightness, bgR,G,B, segCount(u1)]` + per-group `[colorCount(u1), R,G,B, index×colorCount]`.
>
> These layouts are also modelled as **parseable Kaitai types** in `govee_ble.ksy` (scene-upload VALUE
> section): `graffiti_value`, `diy_value` (with a `diy_sub_effect` discriminated union over 10 sub-effects),
> `graffiti_v3_value`, `graffiti_v2_value`, `rgbic_scene_value` (full `rgbic_effect_record`),
> `rgb_scene_value`, `scenes_graffiti_value`, plus the `h60a6_scene_value` DIY-vs-graffiti auto-splitter. Feed
> a **reassembled** (de-chunked) value to the type matching the device/commByte. Compiled with `ksc 0.11` and
> verified: **all 543 non-activate catalog scenes deep-parse with exact byte consumption**. (Kaitai can't
> reassemble across the 20-byte frames — strip each frame's header/BCC and concatenate first.)

**Path A (`toBytes()`):** input is a **parsed model, re-serialized** (not the raw blob). For graffiti:
`DiyGraffitiV2.g()` (`DiyGraffitiV2.java:210-259`):
```
header: [subEffect][speed][baseBrightness][baseR][baseG][baseB][groupCount]
per group: [pixelCount][R][G][B][pixelIndex × pixelCount]
```
`h()` (`:261-347`) is the 16-bit-index variant, used when `isBigDataEffect()`. The H61A8 `05 00 XX YY`
"records" are `[pixelCount=5][R=00][G=XX][B=YY]` group headers + 5 indices. Shared:
`RgbIcGraffitiShare0x08.toBytes()` and `DIYGraffitiParser.toBytes()` also delegate to `DiyGraffitiV2`.
The raw cloud header (`0x50`) is consumed by the parser and a fresh header is emitted.

**Path B (near-verbatim, with a per-`(sceneType,version)` leading-byte transform):** value =
`Encode.decryByBase64(scenceParam)` (`Base64.NO_WRAP`, `Encode.java:31-33`), then a **leading-byte
transform** before it becomes the controller value (`v()`/`g()` return it unchanged thereafter). It is
**NOT uniformly "strip 2"** — the earlier draft was wrong on that:

| sceneType / version → controller | transform | source |
|---|---|---|
| 1→V1, 2→V2 | **none** (verbatim) | `ScenesOp:450,467` |
| 3→V3 (graffiti) | **strip 2** | `ScenesOp:484-487` |
| 6→V5 ; 5,v7→V6 ; 5,v13→V10(bool) | **strip 1** | `AbsMultipleControllerV14Scenes:47-57` (`need4IgnoreFirstBit`) |
| 5,v8/v9→V7 ; 5,v11→V8 ; 5,v12(byte0∈{0x40,0x41})→V10 | **strip 1 + prepend `subOpType`** (3/9/11/90) — net len 0, leading byte *replaced* | `V7/V8/V10` ctor `:5-12` |
| 5,v12(else)→V4 | **strip 1** (`copyOfRange(bytes,1,…)`) | `ScenesOp:672` |
| 6(byte0==0x51)→H60B0 ; 5,v10→`CommonDiyParseConfig` | compound / byte0-dispatched | `H60B0:16-31` / not fully traced |

Two mechanisms: an explicit `copyOfRange` in the `ScenesOp` helper (V3=2, H6022→V4=1), or the base-ctor
`need4IgnoreFirstBit` flag (=1). The `int subOpType` ctor strips 1 **and prepends** a byte. No
`toBytes()` re-encode.

## G3 — parse-type dispatch
`sceneType` is a **server DTO field** (`CategoryV1.LightEffect.sceneType`, via `getSceneType(idx,sku)`,
`CategoryV1.java:331-345`): `0 static,1 rgb,2 rgbic,3 graffiti,4 cube,5 diy,6 compose`. Two mechanisms:
- `sceneType ∈ {1,2,3,6}` (+ several `5` sub-variants): `ScenesOp.parseSceneV1` switch on
  `(versionArray.contains(V) && sceneType==T)` → a `MultiNewScenesController*` (`ScenesOp.java:603-640`).
- `sceneType == 5` (new-DIY): `BaseSceneViewMode` walks a registered `List<IDiyParse>`, each parser
  self-identifies by the blob's **header byte**.
`sceneType` gates **entry**; the concrete parser picks the `toBytes()` format; the **dialect (0xA3 vs
0xA4)** is decided separately (G5), independent of `sceneType`.

## G4 — authenticated effect-str
`effectStr` is the **same Base64 form as `scenceParam`, NOT upload-ready.** It is written back into
`scenceParam` and re-parsed exactly like a local param (`SceneDataVM$checkSceneBigEffect$1`:
`setSceneParam(…)`, `setBigEffectScene(…,0)`). Fetch **iff `bigEffectStr==1 && scenceParam` empty**
(`BaseSceneViewMode.java:476`, `SceneDataVM.java:1309`). Response DTO `SceneBigEffectBean` has **only**
`{scenceParamId, effectStr}` — no `sceneType`/`cmdVersion` (the client already has those from the
library DTO). Endpoint (scene-detail): `POST /bff-app/v1/devices/scenes/effect-strs` (`ScenesApi.java:127`).

## G5 — `0xA3` vs `0xA4`-MTU (per-parser; **H60A6 graffiti IS `0xA4`**)
Two routes reach `0xA4` (`makeSendBytesMtu`):
1. **General new-DIY** — via `Compose4DefWrite4Multi.makeWriteController(b6,b10,…)`, gated on **`b6==88`
   (cmd `0x58`) AND `NewDiyEditConfig.isUseMtuCommand(b10)`** (set `{0x5E,0x5D,0x20}` only,
   `NewDiyEditConfig.java:58-60,140`; `Compose4DefWrite4Multi.java:199,202`). Most effects miss this → `0xA3`.
2. **H60A6 graffiti (direct)** — `H60A6GraffitiParse.makeBleController:238` calls
   `Compose4DefWrite4Multi.d((byte)88, toBytes(), mtu)` **directly, bypassing that gate**;
   `isNeedMtuPackage()==true` (`:204`). So **every H60A6 `sceneType 5` graffiti scene uploads as `0xA4`.**
   Graffiti-vs-DIY is decided by **parser order** (`H60A6DiyParse` first, `H60A6DiyConfig:36-37,82`): on the
   value (`=decode(param)[1:]`), if `u16le@0 + 2 == len` → DIY (`H60A6DiyParse`→`MultiController`→`0xA3`);
   else → graffiti (`0xA4`). Catalog split **59 graffiti / 14 DIY**; e.g. Aurora → `0xA4` (`32+2 ≠ 187`), but
   `Halloween B` → `0xA3` *despite* `byte1=0x20` (`32+2 = 34 = len`). (`byte1=0x20` is necessary but **not
   sufficient** for graffiti — the DIY length gate is tried first.)

Everything else stays `0xA3`: dialect-A scene controllers (`AbsMultipleControllerV14Scenes.useMtuController()==false`,
`:138`) and H6052 type-5 (`SceneControllerNoEventV2`).

**Frame *size* depends on MTU, not type:** `MtuConfig.getAvailableMtuSize = max(getMtu(sku)−3, 20)`,
default `23⇒20` (`MtuConfig.java:18,64`) — an app-internal store, **not** the negotiated ATT MTU (so a
247/512 ATT MTU alone does not widen frames). H60A6 registers MTU support 512 (`NewDetailConfig:751`),
applied only if `saveMtuData` cached it.

> **Reconciling the btsnoop (only `0xA3`, 20-byte):** the captured H60A6 uploads were DIY-type (`0xA3`)
> or other devices — **not** a graffiti-scene apply. A graffiti scene (Aurora) uploads as `0xA4`.

**`0xA4` frame layout (`MultipleControllerCommV1.makeSendBytesMtu:409`, code-verified; the `.ksy` `multi_a4`
now models all three forms and round-trips synthesized Aurora frames) — two branches by `len` vs `mtuSize−8`:**
- **Small (`len ≤ mtu−8`):** START `[A4 00 00 01 02 00 commByte@6 value@7… BCC]` + a **separate 4-byte
  valueless terminator `A4 FF FF BCC`**. The `02 00` is just the frame count = **2** (START + terminator).
- **Large (`len > mtu−8`):** START `[A4 00 00 01 cntLo cntHi commByte@6 value@7… BCC]` — **`cnt` = TOTAL frame
  count incl. START+END, `u16 LE`** (`getLowHighBytes(i13)`); MIDDLE `[A4 seqLo seqHi value@3… BCC]` — **seq =
  packet index, `u16 LE`, 1-based**; END `[A4 FF FF value@3… BCC]` (data-bearing; **no separate terminator** —
  the `FF FF` END is itself the last packet). START carries `mtu−8` value bytes, each MIDDLE `mtu−4`, END the remainder.
- **Aurora** (187 B): `MtuConfig=20` (default) → **large**, **12 frames** = START(12) + 10×MIDDLE(16) + END(15)
  = 187 ✓; cached `512` → **small** (single 195-B START + 4-B terminator). commByte `0x58` @ byte 6 either way.
  Value = `decode(param)[1:]` — verified this equals `toBytes(parse(param))` byte-exact for all 94 catalog
  graffiti layers (`getLayerData` recomputes recLen/innerLen and zero-pads reserved, all matching source; G2).

## G6 — API fields that drive encoding
Per-effect DTO `CategoryV1.LightEffect` (`CategoryV1.java:94-115`): `sceneCode, scenceParam,
scenceParamId, sceneType, cmdVersion, bigEffectStr, checkEffectiveTime, colorHvalUrl, diyEffectCode[],
diyEffectStr, effectCodes[], effectUrl, effectiveTimes[], favor/favorId, hasToneColors, rule, rules[],
scenceName, specialEffect[], speedInfo, supportDirections[]`. **Required to encode an upload:**
`sceneType` + `sceneCode` + `scenceParam` + the device **`versionArray`** (`ScenesConfigBean.j()`).
`scenceParamId` only to fetch when big; `cmdVersion` only affects rgbic opType; `specialEffect[]`
overrides fields per-SKU. `bigEffectStr==1` = payload not inlined (fetch-required when param also empty).

## G7 — chunking + packetCount (confirmed)
From `makeSendBytesV2` (`:785-849`): `packetCount` (byte 3) = **total frames incl. START and the
data-bearing `0xFF` terminator**; START inline value = **`15 − len(commBytes)`**; MIDDLE/END = **17
value bytes**; the `0xFF` frame **carries the final chunk** (`arraycopy → bArr2[2..]` `:828`; sole `0xFF`
frame appended `:847`) — not a separate empty frame. Matches the H61A8 capture.

---

## Worked example — H61A8 START, byte-exact (Path A / DIY editor)
Chain (read first-hand): `OpDiyCommDialog4BleV2.o()` (`:32`) `new MultiDiyGraffitiController(g.f(), g.g())`
(`g`=`DiyGraffitiV2`) → controller cmd `3`, value=`g()` → `makeSendBytesV1(0xA3,3,value)`.
```
a3 00 01 10 03 09 00 64 00 00 00 1c 05 00 ff 00 00 01 02 39
└frame──────┘└──────────── value (first 14 B of g()) ─────┘ BCC
```
Frame: `A3` proType · `00` seq · `01` marker · `10` packetCount(=16) · `03` commByte · 14 value @5-18 ·
`39` = XOR(bytes 0..18) **(hand-verified)**. Value: `09` subEffect · `00` speed · `64` brightness(100) ·
`00 00 00` baseColor · `1c` groupCount(28) · then group0 `05`(px) `00 ff 00`(green) `00 01 02`(idx 0-2;
idx 3,4 spill into seq=1). **START observed & exact; full ~259-byte payload inferred** from groupCount +
the `g()` loop (capture was a 3-frame prefix).

---

## §3b — Aurora (library scene): activate-only vs parse+upload

**Decision rule (from `BaseSceneViewMode` ~:757-781, read first-hand):**
```
sceneV1 = ScenesOp.parseSceneV1(sceneType, sceneCode, scenceParam, versionArray);
if (sceneV1 != null) add SceneControllerNoEventV1(sceneV1)   // 0xA3 UPLOAD — conditional (Path B)
add createSceneModeController(...)                            // 33 05 04 <sceneCode LE> — ALWAYS
```
So the app **uploads iff the param parses**, and the `33 05 04 <sceneCode>` activation is sent in
**both** cases. `parseSceneV1` returns null iff: no `(versionArray, sceneType)` branch matches, param
empty, base64 fails, or the per-type validator fails — and the validators gate on **`byte[0]`** /
length / sceneType (`ShareDiy.isValidComposeBytes → bytes[0]==10`; `isValidGraffitiBytes → byte[0]==1 &&
len≥9`; etc.), **never on byte 3**.

1. **Apply path & upload:** yes it can upload (`SceneControllerNoEventV1`, `0xA3`), and it is a **different
   path** from §2's DIY-editor capture (different controller; **Path B / dialect A** — value is the
   decoded `scenceParam`, commByte = scene-version comType — NOT `DiyGraffitiV2.g()`).
2. **byte 3 = 0xFF:** **not a marker.** A grep for any `[3]`-index gate across `ScenesOp`, `ShareDiy`,
   `ScenesRgbIC`, `ScenesRgb`, and the scene-content dir returns **zero** (verified first-hand). A client
   using `byte[3]==0xFF → activate-only` is applying an invented rule and will skip a required upload —
   **the most plausible Aurora bug.**
3. **Residency/cache:** none that changes the write. Every apply rebuilds identically.
   `SceneBigEffectManager` is only a *param-download* cache for the `bigEffectStr==1 && param empty`
   fetch — never touched for a non-empty param like Aurora. No "upload once, then activate by code."

**Verdict:** the app has **no `byte3`-based activate-only rule**; upload is decided purely by parse
success on `byte[0]`/length/`sceneType`. Aurora's non-empty param is **not fetched** and, if it parses
(very likely for a real applicable library scene), is **uploaded (Path B) then activated** — meaning
activate-only would be the bug, **but the correct encoder is Path B (decode `scenceParam`, strip 2 bytes
iff graffiti, chunk `0xA3` with comType@4), not `DiyGraffitiV2.g()`**.

**To settle (a)/(b) definitively** (needs data not in the APK):
- **Empirical (best):** btsnoop the *official app* applying Aurora — `0xA3` frames before the `33 05 04`
  ⇒ parse+upload (b); only `33 05 04` ⇒ activate-only (a).
- **Static:** provide Aurora's decoded `scenceParam` **`byte[0]`** + its **`sceneType`**; then the
  validator table gives a definitive parse/null answer.

---

## Honest limits
- Aurora's `(a)/(b)` outcome is **not decidable from "188 B, byte3=0xFF" alone** — it needs `sceneType`
  (server DTO), decoded `byte[0]` (actual bytes), and the device `versionArray` (runtime config).
- H61A8 worked example: START byte-exact; full payload inferred (3-frame prefix).
- `0xA4` middle-packet layout is source-derived, never observed on the wire (no curated device uses it).
