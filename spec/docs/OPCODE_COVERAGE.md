# Opcode coverage — what the builder catalogue implements

`spec/govee_commands.py` provides **462 builders** across **99 opcodes**, one function per (opcode, variant) the Govee Home app emits. Every builder is traced to the decompiled app (controller cited in its docstring) and carries a byte-exact self-check run by the reference self-test. This file is the index: opcode → builders, the device families each serves, the verification tier, and whether the spec's grammar (`govee_ble_frame.ksy` and `govee_state.ksy`) types that opcode's layout.

**Verification tiers.** `firmware-confirmed` — the opcode is handled by at least one of the six decompiled device firmwares (H6047 BlueX/RivieraWaves ARM Thumb-2; H6006/H6008/H5083/H60A1/H60A6 Realtek), so both ends agree; see `FIRMWARE_VALIDATION.md`. `source-modeled` — traced from the app only (the authority for what to send), not seen in a firmware image. A few opcodes also have `bench` hardware confirmation, noted inline.

**In grammar?** The row's byte is read in the space its builders put it in: a command (byte 1 of a `33` / `3A` / `AA` frame), a sub-mode (byte 2 of `33 05`) or a proType (byte 0 of any other head); a byte the builders use only as a commByte or a status-read type counts as a command. `typed`: the spec has a body type for it (a `write_command` case for a write, a `govee_state` reply or `read_request` selector case for a read, a `mode_write` case for a sub-mode, a tx route with a body type for a proType); an overloaded byte is typed only under the profile form that names its layout (the `cmd_XX` wrappers). `named`: a member of `govee_common::command` / `sub_mode` / `pro_type` whose body is untyped (its bytes stay in the message's `rest`). `builder only`: neither. Upload values (the commByte payloads of A1 / A3 / A4) are typed by `govee_upload.ksy`, not counted here; see `SCENE_UPLOAD_ENCODING.md`.

## Opcodes with builders

| Opcode | Builders | Dir | Tier | In grammar? | Families (sample) |
|---|---|---|---|---|---|
| `0x00` | 2 | R | firmware-confirmed | typed | h6057, h60ax, new-detail light stack (ShareVM subclasses), new-detail lights using HeartFactory type 2 |
| `0x01` | 4 | W | firmware-confirmed | typed | H5080, H5082, H5083, H5085, H5086, H5089 |
| `0x02` | 3 | W | firmware-confirmed | typed | H5086, H5089, h5080, h5080 (H5089, goodsType 307) |
| `0x03` | 1 | W | source-modeled | builder only | h6105, h6113, h6114, h6129, h613839, h6159 |
| `0x04` | 2 | RW | firmware-confirmed | typed | Every light that reads brightness directly on connect (newdetail connect strategies, snapshot). H60A6 family gets brightness from the 0xAC status TLV instead (not traced here)., Group control / apply paths, every light; NOT the control screen (documented only) |
| `0x05` | 42 | W | firmware-confirmed | typed | 123 when Support4DreamColorV1.m != 2 and .l holds (Bv :9115-9122), 17/18 on old firmware (Dy :9445-9448, Fy/Ey :9730-9735); 13 on old firmware (Qy :11721-11724); 0 on new firmware, 21 only when Support4DreamColorLightV2.a is false, else 0 (Av :8960-8963), 39/40/108 when not NewRgb2TL (:1714-1724); 303 (H60A6) unless pactType 1 and pactCode >= 2 (kw :14902-14905); goodsType-0 H6188 and the updated H6001 (Hu :10225-10300), H6001, H6008 |
| `0x07` | 8 | RW | source-modeled | typed | H6067, H61A9, H7031, H7090, barelightv1, base_h71xx |
| `0x08` | 1 | W | source-modeled | builder only | h6101 (H6101) |
| `0x09` | 4 | RW | firmware-confirmed | typed | H6050, H6051, H6055, H6058, H7090, bulblightstringv1 (H7002/H7005) |
| `0x0a` | 2 | RW | source-modeled | named | bulblightstringv1, dreamcolorlightv1, h6104, h612526, h6127, h613839 |
| `0x0b` | 4 | RW | source-modeled | typed | bulblightstringv1, h6057 (H6057), h60ax (round-2 controller), rgblight |
| `0x0d` | 2 | RW | source-modeled | builder only | H7004, H7004 (ratios 4/6/8), H7017, H7017 (ratios 3..8) |
| `0x0e` | 2 | RW | source-modeled | named | stringlightv2 goodsType 78, stringlightv2 goodsType 78 (only when its protocol is uiv1/uiv4, via :1383-1384), tvlightv1 H6179 goodsType 12 (always via :1408-1409, subject to the OldRgbBk rewrite), tvlightv1 H6179, goodsType 12 (only when OldRgbBkUtil.isOldRgbBk is false; see conflicts) |
| `0x0f` | 3 | RW | source-modeled | named | bulblightstringv1, dreamcolorlightv1, h612526, h612526 (H6125, H6126), old firmware only: OldDreamColorUtil.checkSupportNewDreamColorFuc(sku, soft) false (split_rgbiclight/com/govee/h612526/sku/Model.java:56-66), h7022 |
| `0x11` | 12 | RW | firmware-confirmed | named | (a) The 30 lighting/plug add-device Wi-Fi screens that extend AbsBleWifiChooseActivity. None overrides m3. Examples: split_h6047/com/govee/h6047/add/WifiChooseAc.java, split_h61d3/com/govee/h61d3/add/WifiChooseActivity.java, split_plugnew/com/govee/h5080/add/WifiChooseAc.java, split_plugnew/com/govee/h5086/add/WifiChooseActivity.java. Only split_h6061/com/govee/cubelight/add/WifiChooseAc.java:153 (H606A goodsType) and split_rgbiclight/com/govee/dreamcolorlightv1/add/v2/WifiChooseAc.java:160 override q3()., (b) Connect-step pipeline StepWIFIConnect (StepWIFIConnect.a() = 3, StepWIFIConnect.java:42-45), fed by DeviceWifiInfo from base-classes10/com/govee/base2light/ac/AbsWifiChooseActivity.java:216-218 and others. The SKU list is not enumerated., Pairing / add-device path only, not the control screen, so the library is not required to match it., base2light AbsUI4BleIot pact UIs (base-classes11/com/govee/base2light/pact/AbsUI4BleIot.java:1957), base2light AbsUI4BleIot pact UIs (base-classes11/com/govee/base2light/pact/AbsUI4BleIot.java:1962), bulblightstringv1 |
| `0x12` | 12 | RW | firmware-confirmed | typed | base2light AbsUI4BleIot pact UIs (base-classes11/com/govee/base2light/pact/AbsUI4BleIot.java:2020), base2light AbsUI4BleIot pact UIs (base-classes11/com/govee/base2light/pact/AbsUI4BleIot.java:2022), bulblightstringv1, bulblightstringv2/h7031 (split_bulblightstringv1/com/govee/bulblightstringv2/newAdjust/h7031/ControllerStrategyConfig.java), dreamcolorlightv1, h5080 |
| `0x13` | 7 | RW | firmware-confirmed | typed | H5080, H5082, H5083, H5085, H5089, H601E |
| `0x14` | 5 | RW | source-modeled | typed | bulblightv3 goodsType 311 (H601E/H601F; Support.isH601EF split_bulblightstringv1/com/govee/bulblightv3/pact/Support.java:191-193), bulblightv3 new-detail (only INightModeFuc implementer: split_bulblightstringv1/com/govee/bulblightv3/newdetail/NewDetailVm.java:99). Registered goodsTypes 31/64/111/311/328/342/348/408 (Support.D split_bulblightstringv1/com/govee/bulblightv3/pact/Support.java:56); treat this as approximate for which ones reach the setting entry., dreamcolorlightv2 goodsType 21 (connect batch), dreamcolorlightv2 goodsType 21 (split_rgbiclight/com/govee/dreamcolorlightv2/pact/Support.java:63: g lists 21); route DeviceItem.v -> Model.t /rgbic/newdetail -> rgbiclight AcNewDetail (VM4Light), dreamcolorlightv2 models H6163, H6125, H6126 when the device reports goodsType 0 (devices.yaml:866 lists them). Inferred from JADX lost blocks; end-to-end control-screen reach not traced., other version-1 families from round 2 (supportv1UIV1 dreamcolorlightv1 gt 17/18/70/73, barelightv1 gt 13) |
| `0x15` | 4 | RW | firmware-confirmed | typed | H5086, h5080 (H5080/H5082/H5083/H5085/H5160), h5080 gt 43 (H5083NewDetail). While BLE-connected, the delete button is shown only when !isOldH5080 (H5083TimerSetActivity.M0 :413-425)., h5080 gt 50 (UiV2), gt 90 (UiV4), gt 307 (Ui4H5089), h6104 (H6104), not H5086 (h5086 TimerDeleteController sends 33 15 [index] with no outlet byte) |
| `0x16` | 6 | RW | source-modeled | named | H5086, H61D3, dreamcolorlightv1, h5080 (H5083), h5080 (H5083, H5089), h6057 (H6057) |
| `0x17` | 3 | RW | firmware-confirmed | named | H5086, base2light WifiPresent (generic Wi-Fi provisioning), h6057, hollowlamp |
| `0x19` | 1 | R | source-modeled | builder only | H5086 |
| `0x1a` | 2 | RW | source-modeled | builder only | H5086 |
| `0x1b` | 10 | RW | source-modeled | builder only | H5089, eco automation, eco automation (target SKU not stated in source), h5080, h5080 (H5089), light71xx (base_h71xx NlCtrShareViewModel / NlCtrShareVmNew; SKU not resolved) |
| `0x1f` | 4 | RW | source-modeled | builder only | H5083, H5086, H5089, h5080, h5080 (H5089) |
| `0x20` | 2 | RW | source-modeled | typed | H6067, base2newth_bbq, bulblightstringv1, bulblightv3, dreamcolorlightv1, dreamcolorlightv2 |
| `0x21` | 2 | RW | source-modeled | typed | H5127, Wi-Fi lights (stringlightv2, bulblightstringv1, h7022, h6047, tablelampv1, ...) |
| `0x22` | 1 | R | source-modeled | builder only | — |
| `0x23` | 2 | RW | firmware-confirmed | typed | bulblightstringv1, bulblightstringv2/h7031 (split_bulblightstringv1/com/govee/bulblightstringv2/newAdjust/h7031/ControllerStrategyConfig.java), dreamcolorlightv1, h6057, h6061, h60ax |
| `0x24` | 5 | RW | firmware-confirmed | typed | H6008, H60A1, H60A6, H682x (goodsType 279/280), h6057, h61d3 (VM4LightH61d3 newdetail control screen, BLE connect batch) |
| `0x25` | 2 | RW | firmware-confirmed | typed | bulblightv3 goodsTypes 111, 348, 408, and 342 only for SKU H14A1/R14A1/H14C2/R14C2 (split_bulblightstringv1/com/govee/bulblightstringv1/bulblight/Support.java:29-32; split_bulblightstringv1/com/govee/bulblightv3/pact/Support.java:45,48,167-193). Control screen: bulblightv3 Model.t sends every goodsType except 311 to bulblight.AcNewDetail with VM4Light (split_bulblightstringv1/com/govee/bulblightv3/pact/Model.java:29-38; split_bulblightstringv1/com/govee/bulblightstringv1/bulblight/AcNewDetail.java:29,54-55)., h6057 |
| `0x26` | 6 | RW | source-modeled | named | bulblightv3: H601E/H601F (goodsType 311), bulblightv3: H601E/H601F (goodsType 311, Support.isH601EF split_bulblightstringv1/com/govee/bulblightv3/pact/Support.java:191-193,215-219; not pact (1,1)), h6057, h6057: H6057 (class never constructed in decomp), h61d3 |
| `0x27` | 5 | RW | source-modeled | named | H5086, H71xx (base_h71xx; SKU unresolved in decomp-7.7.02), h6057 (H6057 newdetail VM; sleep / wake-up timer screens opened from it) |
| `0x28` | 4 | RW | source-modeled | named | H5086, H5089, h5080, h6057 |
| `0x29` | 3 | RW | source-modeled | named | H5089, h6057 |
| `0x30` | 8 | RW | firmware-confirmed | typed | H6038 (h6047 family, goodsType 288), H6038 (h6047 family, goodsType 288, split_h6047/com/govee/h6047/newdetail/NewDetailConfig.java:142), H6057, H6057 (split_hollowlamp h6057), base_h71xx appliances (not a light family), compose DirectionController: no caller in decomp-7.7.02 |
| `0x31` | 11 | RW | source-modeled | named | Add-device layout path only., H6057, H6061 (split_h6061/com/govee/cubelight/add/CalibrationAc.java:419) and H6066 (split_h6061/com/govee/cubelight/add/h6066/InstallShape4H6066Activity.java:644), both through CalibrationDialog.showSetDeviceDialog. Add-device layout path only., H6069 (3-arg ctor): CalibrationDialogH6069 and RecognitionSolveH6069Ac., H606A / H6063 (2-arg ctor): CalibrationDialogH606A.showSetDeviceDialog from split_h6061/com/govee/cubelight/add/h606A/H606AInstallShapeAc.java:345, split_h6061/com/govee/cubelight/add/h606A/H606ACheckPreViewAc.java:269 and split_h6061/com/govee/cubelight/add/h6063/GraphicalIdentificationActivity.java:217., H610A |
| `0x32` | 4 | RW | source-modeled | named | H6057, H6057 newDetail whiteVoiceSwitchOp (H6057DetailVM.java:680-700), h6057, none live (class referenced only in its own file), video-mode framework AbsVideoNewDetailVm (no decomp-7.7.02 subclass enables it: h6104 VideoVm keeps every support*() default false, AbsVideoNewDetailVm.java:273-291,580-617; split_rgblight/com/govee/h6104/newdetail/viewmodel/VideoVm.java:12-21) |
| `0x33` | 4 | RW | source-modeled | named | H6057, H6062, H7090, h6057, h6057 (H6057), striplight |
| `0x34` | 2 | RW | source-modeled | named | H6047, H6048, h6057 |
| `0x35` | 1 | W | source-modeled | named | h6057 (H6057) |
| `0x36` | 3 | W | source-modeled | typed | H1771, H601E, H601F, H6038, H6043, H6047 |
| `0x37` | 1 | W | source-modeled | named | h6057 (H6057) |
| `0x38` | 2 | W | source-modeled | builder only | h6057, h6057 (H6057) |
| `0x39` | 2 | RW | source-modeled | named | H6039 (h6047 family), H6039 (h6047 family, goodsType 277), device splicing UI (DeviceSplicingViewModel) |
| `0x40` | 5 | RW | source-modeled | named | H6671, H6672, h70b1, h70bx, rgbiclight |
| `0x41` | 5 | RW | firmware-confirmed | typed | H7031, H7090, dreamcolorlightv1 legacy adjust v2 (BleOpV3), dreamcolorlightv1 legacy adjust v2 (UiV3), h6061 (cube), h61d3 |
| `0x42` | 9 | RW | firmware-confirmed | named | H1270, H12D0, H60A6, dreamcolorlightv1, h60ax, h70bx splicing |
| `0x43` | 3 | RW | source-modeled | named | H61C3, H6640, h61d3, h61d3: H6640 (242), H6641 (247), both settings and detail-screen calibration dialog, h705a, h70bx |
| `0x44` | 8 | RW | source-modeled | named | H6038, H6038 (goodsType 288): split_h6047/com/govee/h6047/newdetail/h6038/VM4H6038.java:844; split_h6047/com/govee/h6047/newdetail/h6038/H6038CalibrationAc.java:429 (Companion.d() or a(), by its flag), H6039, H6043, H6043 (goodsType 196, Support.isGoodsTypeH6043 split_h6047/com/govee/h6047/pact/Support.java:182-184): split_h6047/com/govee/h6047/add/LightPosAc.java:353-354 (CalibrationController(true) only inside goodsType == 196), :135-138 (T1 -> CalibrationController(false)), and T1 is reached only from d2 :172-173 and onClickDone :242-243, both gated on goodsType == 196, H6047 |
| `0x45` | 4 | RW | source-modeled | builder only | H6061, H6063, H6066, H6067, H6069, H606A |
| `0x46` | 4 | W | firmware-confirmed | named | caliStripLen strip-length calibration, same goodsType rule as build_check_ic (0x46 outside the 0x5A set); settings / add-device path, not the control card, h60ax H60A1 / H60A4 / H60A6 / H60C1 / H1250 / H1270 / H12D0, h61d3, h70bx, newdetail devices showing ResetDeviceNetOpView in settings, rgbiclight (SettingAc) |
| `0x47` | 1 | W | source-modeled | builder only | newdetail lights with part-scenes support (per-SKU gating not traced), reported on H6039/H6038/H6048 (goodsType 277/288/298) |
| `0x48` | 2 | RW | source-modeled | builder only | unreferenced (no decompiled caller) |
| `0x49` | 4 | R | source-modeled | builder only | Wi-Fi lights using the ReadControllerFactory connect step (SKU set not resolved) |
| `0x4a` | 1 | W | source-modeled | builder only | com.govee.dgw gateway (not a light/plug family) |
| `0x50` | 2 | W | source-modeled | named | H6069, movie-feast host / members (only constructor is in undecompiled base.apk classes3.dex; no decompiled SKU binding) |
| `0x52` | 3 | W | source-modeled | builder only | H6065 Glide Y, goodsType 121 (split_h6061/com/govee/h6067/pact/Support.java:346-348), add-device calibration path only. Sent only when the shape list has at least one non-editable shape and shape position i == 0. CalibrationAc.w1 then follows it with the single-frame 0x44 build_h6065_calibration (:578); CalibrationDialog.I follows it with CalibrationAllController (:333)., H6065 Govee Glide Y, goodsType 121 (split_h6061/com/govee/h6067/pact/Support.java:84, :346-352), add-device calibration path only. Sent only from CalibrationDialog.I(true), after the dialog's own BLE connect (onEventBleConnect -> H(true)), and only when goodsType == 121 (:318-327). If BLE was already connected, W() -> H(false) sends only the single-frame 0x44 build_h6065_calibration (:214-219, :328)., H6066 (split_h6061/com/govee/cubelight/pact/Support.java:60), add-device install path only. Sent from InstallShape4H6066Activity.java:464 when BLE is connected, or from CalibrationDialog.H :198 in CalibrationAll mode, which only InstallShape4H6066Activity.java:455 opens. Not the control screen. |
| `0x54` | 2 | RW | source-modeled | named | movie-feast members whose movieFeastVersion != 1: server capability movieFeast.version (base-classes10/com/govee/base2light/capability/ecosystem/LightEcosystemCapabilityCenter.java:276-290 N/O), legacy fallback 0 for goodsTypes not in Constant.w0 and not goodsType 120 pact (1,2) (base-classes10/com/govee/base2light/capability/legacy/LegacyEcosystemCapabilityProvider.java:89-91), same as build_movie_feast_on_off |
| `0x60` | 5 | RW | source-modeled | named | movie-feast hosts with Constant.movieFeastVersion(goodsType, info) == 1 (base-classes10/com/govee/base2light/Constant.java:126-128 -> LightEcosystemCapabilityCenter.O, per-SKU list not traced); other hosts use MovieOpenController 0x54, music-feast members on the FeastUI / DelegateEntryFeast path with goodsType != 87 (per-SKU list not traced), newdetail devices with the carousel feature (no SKU binding traced), same as build_feast_module_movie_on_off, same as build_feast_module_switch |
| `0x62` | 3 | W | source-modeled | named | H7045, H7046, generic DIY framing (live for other protocol codes, e.g. 0x54 in round2 v387); for 0x62 no reachable SKU (DiyAdvancedCombParse is never registered) |
| `0x63` | 2 | W | source-modeled | named | bulblightv3, daysync devices (DaySyncConfig.A, base-classes11/com/govee/base2light/pact/newdetail/config/fuc/DaySyncConfig.java:291-310), goodsType:112, goodsType:359, goodsType:364, goodsType:365 |
| `0x64` | 2 | W | source-modeled | builder only | daysync-capable base2light devices (h60ax, h61d3, hollowlamp, bulblightv3), helper for 0xA3 V1 multi-packet writes |
| `0x65` | 1 | W | source-modeled | builder only | daysync-capable base2light devices |
| `0x70` | 4 | W | source-modeled | named | H5310, H5901 |
| `0x71` | 1 | R | source-modeled | named | none citable: no decompiled caller. com.govee.onlinesound (the music-lib feature) is not in decomp-7.7.02. OnlineMusicConfig declares goodsType 277 / H6039 (split_h6047/com/govee/h6047/pact/OnlineMusicConfig.java:34-35,44-47) and goodsType 202 / H61D5 (split_h61d3/com/govee/h61d3/pact/OnlineMusicConfig.java:29-30,39-41; H61D3Constants.java:64) |
| `0x79` | 2 | RW | source-modeled | named | none citable: no decompiled caller (music-lib SKUs as for 0x71), none citable: no decompiled caller (music-lib SKUs as for 0x71: OnlineMusicConfig goodsType 277/H6039 and 202/H61D5) |
| `0x7b` | 1 | R | source-modeled | builder only | SKUs with an ICreateEffectGif DIY object (no implementation decompiled; SKUs unknown) |
| `0x82` | 1 | W | source-modeled | builder only | newdetail lights with ICarouselFuc (SKU set not resolved) |
| `0x83` | 4 | RW | source-modeled | builder only | display-capable newdetail lights, display-capable newdetail lights (IDisplayFuc; no implementer decompiled), dreamcolorlightv1, legacy mic-mode users (AbsMicFragmentV4, MicDynamic, MicDj, MicSoft, DelegateMicPickup), rgblight |
| `0x84` | 6 | RW | source-modeled | named | Display/alarm-clock lights using newdetail EditDisplayAlarmClockActivity (SKUs not named in the decompiled class), IDisplayFuc display devices (SKU set is a gap; see build_alarm_clock_edit), IDisplayFuc display devices: DelegateDisplay is added to the newdetail control screen only when the detail VM implements IDisplayFuc (base-classes11/com/govee/base2light/pact/newdetail/content/success/DelegateCurMode.java:350-358); no IDisplayFuc implementer exists in decomp-7.7.02, so the SKU set is a gap |
| `0x85` | 1 | W | source-modeled | builder only | display-capable newdetail lights (SKU set not resolved) |
| `0x86` | 2 | W | source-modeled | builder only | DIY-new editor devices with ICreateEffectGif (SKU set not resolved), SKUs whose DIY object implements ICreateEffectGif with getCreateIconVersion != 0 (no implementation decompiled; SKUs unknown) |
| `0xa1` | 1 | R | source-modeled | builder only | unknown: no decompiled caller of ControllerReadSpecialInfoMultiBack (referenced only by its own file) |
| `0xa2` | 2 | R | source-modeled | typed | dreamcolorlightv2 goodsType 21 when Support4DreamColorLightV2.a(...) is false: Telink-OTA hardware below hard 1.00.01 or soft 1.06.00, or unparsable versions, dreamcolorlightv2 goodsType 21 when Support4DreamColorLightV2.a(...) is true (group size 3) |
| `0xa3` | 25 | RW | firmware-confirmed | typed | AI action-recognition effect editor (AiActionRecognitionView / AiActionRecognitionNewView -> AIEffectEditDialog). V1 value only when AIEffectEditDialog.g is false, i.e. AiEditSupport.c false: H6601 below 1.00.23 or hard != 30401, H6602 below soft 10027 or hard not in {30412,30413,30414} (base-classes10/com/govee/base2light/ai4edit/AiEditSupport.java:43-59). Host screens/SKUs otherwise unresolved., AI action-recognition effect editor (same as build_set_ai_action), AI action-recognition effect editor when AiEditSupport.c(sku, bleSoft, bleHard) is true: H6601 with bleSoft >= 10023 and bleHard == 30401; H6602 with bleSoft >= 10027 and bleHard in {30412, 30413, 30414}; any other SKU (base-classes10/com/govee/base2light/ai4edit/AiEditSupport.java:28-37,43-59; base-classes10/com/govee/base2light/ai4edit/AiActionRecognitionNewView.java:823-825; base-classes10/com/govee/base2light/ai4edit/AiActionRecognitionView.java:456-458)., BLE group control (several lights driven as one group), H6057 goodsType 104 control screen, H6057 snapshot path: split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:844 -> GradientColorControllerConfig.i (:159-227, x at :190), which does not check v(info) |
| `0xa4` | 3 | RW | source-modeled | typed | H1270, H1250 (cell graffiti), H60A6 (goodsType 303): graffiti control screen and DIY apply, H7045/H7046 (0x62; attribution from the log string and goodsType 338 only), hollowlamp straightfloorlamp package (SKU set not resolved), newdetail DIY (toNewDiyCommonGraffiti, cmd 0x58 and effectBytes[0] in {0x5E, 0x5D, 0x20}), newdetail scene and graffiti >255 IC devices; H61D3 scenes |
| `0xa5` | 7 | RW | firmware-confirmed | typed | H6119: goodsType 0, or 44 when pactType >= 10 && pactCode >= 1 (split_rgbiclight/com/govee/h6119/sku/Model.java:42-43; base-classes9/com/govee/base2home/pact/support/OldCarBkUtil.java:24-26), H6125/H6126 on soft >= 1.03.01 (base-classes9/com/govee/base2home/pact/support/OldDreamColorUtil.java:60-61,94-98), IoT-only colour SKUs (ColorModeControllerConfig.u non-null): payload empty, Newdetail lights whose LightMusicCapabilityCenter.g(info) (phoneMicSupportType) == 2, with a colour-instruction version Q0 configured and not supportColorModeOver56Segment, on the control screen (DelegateMicPickup.x0), Newdetail lights with phoneMicSupportType == 2 and LightColorCapabilityCenter.b2 (supportColorModeOver56Segment), control screen (DelegateMicPickup.x0); same-model group documented only, Same-model group (SameModelCmdOp.onSendMicCmd) and eco group (base-classes13/com/govee/eco/basegroup/viewmodel/GroupApplyVM$sendMicCmd$1.java:131-147): group paths, documented only |
| `0xa6` | 7 | RW | source-modeled | named | DaySyncConfig.A gate (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/DaySyncConfig.java:291-310): goodsTypes 346, 364, 376, 324 (map d, :48-63) and 390, 403, 404, 387, 388, 389, 400, 367, 375, 365, 395, 359, 368, 348, 408, 377, 112 (map e, :70-151), each behind a pact/firmware lambda (:177-288), H6047, H6059, H6062, h6047, h6047 (H6047) |
| `0xa7` | 4 | RW | source-modeled | builder only | H6050, H6051, H6055, H6058, H6059, H6073 |
| `0xa9` | 32 | RW | source-modeled | named | ai4edit-capable SKUs (not enumerated in decomp), eco automation HDMI action (HdmiObj.hdmiPort; SKUs not enumerated in decomp), no caller in decomp-7.7.02, video-mode framework AbsVideoNewDetailVm (no decomp-7.7.02 subclass enables it: h6104 VideoVm keeps every support*() default false, AbsVideoNewDetailVm.java:273-291,580-617; split_rgblight/com/govee/h6104/newdetail/viewmodel/VideoVm.java:12-21), video-mode framework, gated per SKU by cloud config (AbsVideoNewDetailVm.supportAiFilter :563-577, Config4DeiceFuc) |
| `0xaa` | 1 | W | source-modeled | named | — |
| `0xab` | 4 | RW | firmware-confirmed | typed | AbsBleWifiChooseActivity subclasses, including split_plugnew/com/govee/h5080/add/WifiChooseAc.java:24, which does not override q3, so it sends only when LightAddCapabilityCenter.getDynamicDomainType(sku) != 0, BaseWifiConfigActivity subclasses that override Y2 (H5083WifiChooseActivity does not: split_plugnew/com/govee/h5080/adjust/newdetail/H5083WifiChooseActivity.java:20), Connect-step Wi-Fi flow (WifiPresent / StepWIFIConnect), H5080 (goodsType 43), H5080-family plugs (h5080 v1/v2/v3, H5089, H5083 newdetail), H5082 (goodsType 50) |
| `0xac` | 15 | RW | source-modeled | typed | H1771, H5086, H6063, H6069, H606A, H6671 |
| `0xae` | 1 | R | source-modeled | builder only | video-mode newdetail lights |
| `0xaf` | 2 | W | source-modeled | builder only | unreferenced (no decompiled caller) |
| `0xb0` | 4 | RW | firmware-confirmed | typed | H5086, h5080, h5080 gt 43 (F0, outlet 0), h5080 gt 50 (UiV2), gt 90 (UiV4), gt 307 (Ui4H5089), not H5086 (DelayOnOffController 33 B0 [type, h, m], round-2 build_plug_pro_delay) |
| `0xb1` | 2 | RW | firmware-confirmed | typed | Control screen, legacy lights: dreamcolorlightv1 and stringlightv2 (Support.isSupportDeviceSecret), hollowlamp BleOpV3 (gt 27/37/42), Control screen: H5086 (SmartPlugProAdjustViewModel.s3), sent once per connect, gated on ack, Control screen: h5080 plugs (H5080/H5082/H5083/H5085/H5089/H5160/H5161) legacy BleOp hooks, no goodsType gate, non-empty secret only; H5083 newdetail via BleSecretInterceptor, Control screen: hollowlamp newdetail gt 59 (H6058NewDetailVm, always, possibly empty payload) and gt 27/37 (H605051NewDetailVm, non-empty only); gt 58 never, Documented only: h5080 card/widget switch [B2, 33 01] (split_plugnew/com/govee/h5080/pact/Model.java:150 = round-2 build_plug_relay_with_secret frame 1), scenes BleSecretBuilderV1, effect-op pairController, group GroupOp*, automation executeAction parsers, H5086, pairing only |
| `0xb3` | 1 | R | firmware-confirmed | typed | h5080 pairing / add-device flow only (PairAcV1), not on any control screen: absent from H5083NewDetailRepository.necessaryMessages (split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:322-338), BleOpV2.o0 (split_plugnew/com/govee/h5080/adjust/v2/BleOpV2.java:141-158), BleOpV4.l0 (split_plugnew/com/govee/h5080/adjust/v4_h5160/BleOpV4.java:124-134) and BleOp4H5089.V/Y (split_plugnew/com/govee/h5080/adjust/h5089/BleOp4H5089.java:145-163,173-185) |
| `0xb4` | 3 | RW | source-modeled | named | H5080, H5086, H5086 (TimerWriteController.Companion.a), h5080, h5080 gt 43 when isOldH5080 (outlet 0 only), h5080 gt 43, only when Support.isOldH5080 (sku H5080 and versionSoft <= 10008, split_plugnew/com/govee/h5080/pact/Support.java:141-143): H5083NewDetailViewModel.D0 |
| `0xb5` | 1 | W | firmware-confirmed | typed | H5080 (goodsType 43), H5082 (goodsType 50), H5083 (goodsType 43), H5085 (goodsType 43), H5086 (goodsType 195), H5089 (goodsType 307) |
| `0xb8` | 2 | W | source-modeled | builder only | AI action-recognition effect editor (AiActionRecognitionView / AiActionRecognitionNewView); SKU gating not traced, AI effect editor lights (SKU set not resolved) |
| `0xb9` | 2 | R | source-modeled | builder only | H6063 (goodsType 228 only: Support.supportDeviceGraphics split_h6061/com/govee/cubelight/pact/Support.java:784-786; CUBE_H6063 Support.java:198-199), h6061 (H6063, goodsType 228) |
| `0xba` | 2 | RW | source-modeled | builder only | base_h71xx/skubusiness appliance channel; no decompiled caller, SKUs unknown, dreamcolorlightv1, rgbiclight |
| `0xed` | 4 | W | source-modeled | named | h61d3 (recognition feature: VideoActivity, launched from ImageRecognitionActivity.java:393), h61d3 recognition sub-flow (VideoActivity), h61d3 recognition sub-flow (VideoActivity, ImageRecognitionActivity). The entry route '/61d3/take/photo/activity' has no navigation caller in decomp-7.7.02., not sent by the H6641 (247) or H703A (263) control screens |
| `0xee` | 1 | W | source-modeled | named | dreamcolorlightv1, gateway (BaseTxViewModel), kt newdetail devices with OtaType.isBKOtaV1 (AbsViewMode4OpWithOtaCheck / LightTxViewModel), stringlightv2 |
| `0xef` | 1 | R | source-modeled | typed | pact-based lights and plugs |
| `0xf0` | 4 | RW | source-modeled | builder only | DaySyncConfig.A devices; pairing / bind path, not the control screen, bulblightv3 (DaySyncConfig-gated goodsTypes), daysync devices, h60ax, h61d3, hollowlamp |
| `0xf1` | 1 | R | source-modeled | builder only | daysync devices |

## Builder detail (per opcode)


### `0x00` — firmware-confirmed

- **`read_connect_heartbeat`** (R): AA <c(type): 1->01, 2/other->00, 3->33, 4->36> (no selector) 00.. [BCC]
  - base-classes12/com/govee/common2light/capability/connect/ConnectHeartbeat.java:15-38 (a: true->1, false->2; b: keeps 1-4, else 2; c: 1->0x01, 2->0x00, 3->0x33, 4->0x36); base-classes11/com/govee/base2light/kt/ble/Controller4HeartCommon.java:13-22 (getCommandType = ctor byte)
- **`read_heartbeat`** (R): AA 00 (no selector), reply ignored
  - base-classes10/com/govee/base2light/ble/controller/HeartControllerNoEvent.java:10-12 (getCommandType 0; empty parse); base-classes10/com/govee/base2light/connect/factory/HeartFactory.java:23-26 (heartbeat type 2)

### `0x01` — firmware-confirmed

- **`build_h5086_chart_time_range`** (W): 33 01 [start_unix u32be][end_unix u32be] on service ...4857 / write char ...2014 (a distinct 0x01 overload on a separate GATT service)
  - split_plugnew/com/govee/h5086/ble/chartcontroller/ChartTimeRangeController.java:13-56 (getCommandType = 1 :42-44; q :52-56; d() = char ...2014 :33-35; e() = service ...4857 :38-40; n() = TRUE :47-49); base-classes10/com/govee/base2light/ble/controller/AbsOnlyWriteSingleController.java:41-42 (super(true), so proType is 0x33)
- **`build_music_old_0x01`** (W): 33 05 [01][code][sensitivity][R G B only when code is 1 or 2]
  - split_rgblight/com/govee/h6104/ble/SubModeMusic.java:42-45,51-63,87-88 (SubModeMusic.g / getWriteBytes / subModeCommandType = 1); split_rgblight/com/govee/h6104/ble/Mode.java:8-12 (Mode.parseSubMode b == 1 -> SubModeMusic.parse :75-80)
- **`build_plug_relay`** (W): 33 01 [relay_mask u8: high nibble selects relays, low nibble = new state; outlet0 0x10|on, outlet1 0x20|on<<1, outlet2 0x40|on<<2, all 0xFF on, 0xF0 off]
  - split_plugnew/com/govee/h5080/ble/controller/SwitchControllerV2.java:15-19,25-27,53-71 (SwitchControllerV2.<init>(on, outlet)/getCommandType/q); all outlets (g=15) at split_plugnew/com/govee/h5080/adjust/v1/UiV1.java:796, adjust/v2/UiV2.java:890, adjust/v3/UiV3.java:751, UiV4.java:768, Model4Plug2.java:171; per outlet at UiV2.java:903, UiV4.java:780, Ui4H5089.onOffSubChange:956-967, SmartRoomOp.makeSwitchOpBytesWithSubDevice:152-158, SmartRoomOp.makeSwitchOpBytes:136-139
- **`build_plug_relay_with_secret`** (W): frame 1: 33 B2 [secret 8 bytes, base64-decoded]; frame 2: 33 01 [relay_mask]
  - split_plugnew/com/govee/h5080/pact/Model.java:139-158 (l: [SecretKeyController(secretKey).g(), SwitchControllerV2(on, 15).g()] :150, sent on BleComm.P/Q :151-155); split_plugnew/com/govee/h5080/pact/WidgetBleIotOpSubV2.java:96-110 (b(bleAddress) = SecretKeyController :44-48,104-106, then SwitchControllerV2(on, a) :107; goodsType 307 with a == 2 sends Controller4NlSwitch instead :97-100)

### `0x02` — firmware-confirmed

- **`build_h5086_chart_prepare`** (W): 33 02 (no payload) on service 494e5445-4c4c-495f-524f-434b535f4857 / char ...434b535f2014
  - split_plugnew/com/govee/h5086/ble/chartcontroller/ChartPrepareInfoController.java:28-53 (UUID overrides d()/e(); getCommandType 2; q null); split_plugnew/com/govee/h5086/ble/AbsBleWithChartDataComm.java:201-202 (R)
- **`build_h5089_total_switch`** (W): 33 02 [on u8: 1 all on / 0 all off] 00.. [bcc]
  - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4TotalSwitch.java:14-35 (single ctor super(true); getCommandType 2; q()=[on]; parseValidBytes returns true); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/SwitchControllerConfig.java:108-112 (d: goodsType 307 -> Controller4ExtBytes.Companion.d([2, on])) and the IoT twin e :124-139; Controller4ExtBytes base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:85-100,127-130,144-147
- **`build_plug_total_switch`** (W): 33 02 [on 1 all on / 0 all off]
  - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4TotalSwitch.java:14-35 (getCommandType 2, q [on]); split_plugnew/com/govee/h5080/adjust/h5089/Ui4H5089.java:946 (control-screen executeOp)

### `0x03` — source-modeled

- **`build_music_flag_sens_03`** (W): 33 05 [03][colour_flag 0 auto / 1 rgb][sensitivity][R G B only when flag = 1]
  - split_rgblight/com/govee/h6160/ble/SubModeMusic.java:18-20,54-60,89-91 (SubModeMusic.a / getWriteBytes / subModeCommandType = 3); split_rgblight/com/govee/h6160/ble/Mode.java:15-19 (Mode.parseSubMode b == 3 -> SubModeMusic.parse :76-83)

### `0x04` — firmware-confirmed

- **`build_brightness_from_percent`** (W): 33 04 [level u8 = pct when range is [1,100], else lo + (pct-1) * (hi-lo+3)/100 in float32, truncated; pct coerced to 1..100]
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/BrightnessConfig.java:88-93,153-162 (BrightnessConfig.Companion.c / h); base-classes10/com/govee/base2home/util/NumberUtil.java:47-49 (NumberUtil.calculateProgress)
- **`read_brightness`** (R): AA 04 (no selector) 00.. [XOR@19 = 0xAE]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerBrightness.java:29-48 (ControllerBrightness.Companion.b ctor(false, null) / Companion.c); base-classes10/com/govee/base2light/ble/controller/BrightnessController.java:10-12,31-35 (BrightnessController() / parseValidBytes)

### `0x05` — firmware-confirmed

- **`build_color_0b_kelvin`** (W): 33 05 0B [R][G][B][kelvin u16be = KelvinConfig.c(colour)][mask positions 0-7][mask positions 8-14, bit 7 never set]
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:13930-13934 (Config.eu = J[6]); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:15671-15674 (Config.pu: goodsType 24 gets 6 when F() is 1, else 0)
- **`build_color_0b_white_tab`** (W): 33 05 0B [tint R G B = Constant.getTemColorByKelvin(K)[2], 00 00 00 when K is off the table or outside 1000..10000][mask u16le: (1<<n)-1 for 1<=n<=16, 00 00 for
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/KelvinConfig.java:72-99 (KelvinConfig.Companion.b); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:499-524 (ColorModeControllerConfig.Companion.h, n = ColorPieceConfig.Companion.c)
- **`build_color_cct_0d`** (W): 33 05 0D [R][G][B][kelvin u16be][tint R G B]. RGB pick (Bytes.f) = [R G B 00 00 00 00 00], no mask; colour-tab CCT (Bytes.p, tint pick or Kelvin tile) = [picked
  - base-classes11/com/govee/base2light/pact/newdetail/content/color/ColorViewModel.java:1388-1413,847-884 (ColorViewModel.p0 / U applyColorTem); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:207-241,499-524 (Companion.c / h)
- **`build_color_legacy`** (W): 33 05 02 [R][G][B][white_flag][R2][G2][B2]. RGB pick (Bytes.a) = [R G B 00 00 00 00]; colour-tab CCT tile (Bytes.o) = [FF FF FF 01 tint R G B] with tint = the p
  - base-classes11/com/govee/base2light/pact/newdetail/content/color/ColorViewModel.java:1388-1428 (ColorViewModel.p0: unified first, VM fallback); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:207-241 (ColorModeControllerConfig.Companion.c)
- **`build_color_rgbic_0b`** (W): 33 05 0B [R][G][B][seg_mask, bit i = position i, LSB-first: u16le for <=16 positions (BleUtils.Y), ceil(n/8) bytes capped at 13 for >16 (BleUtils.X)]. Legacy dr
  - base-classes11/com/govee/base2light/pact/newdetail/content/color/ColorViewModel.java:1388-1413 (ColorViewModel.p0); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:207-241 (ColorModeControllerConfig.Companion.c)
- **`build_diy_activate`** (W): 33 05 0A [code u2le][music_code u2le] (legacy classes send only the code; music_code=0 is the same frame)
  - split_h6047/com/govee/h6047/ble/SubModeNewDiy.java:29 (SubModeNewDiy.getWriteBytes); split_bulblightstringv1/com/govee/bulblightv3/ble/SubModeNewDiy.java:29 (SubModeNewDiy.getWriteBytes)
- **`build_diy_activate_custom_music`** (W): 33 05 0A [code u2le][music u4le][music_type]
  - base-classes11/com/govee/base2light/effectPlay/effect/mode/SubModeNewDiy.java:64 (getWriteBytes e true, :65-68); base-classes11/com/govee/base2light/pact/newdetail/content/diy/DiyApplyVM.java:1539-1544 (f(code,true,voiceType) when isSupportCustomMusic)
- **`build_game_mode_h6057`** (W): 33 05 0B [game_type u8] 00.. [XOR@19]. game_type 0 is always offered. 1..3 are offered only when Support.support540versionFunction(sku, soft) is true, i.e. SKU 
  - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:657-672 (H6057DetailVM.B5: ControllerMode.Companion.e([0x0b, gameCode]) at :662; nothing is sent unless isConnectSuc :658-661; applyController opMode 18 'set game mode' :668-672); split_hollowlamp/com/govee/h6057/origin/newDetail/H6057GameDelegate.java:254-261 (H6057GameDelegate.M: no resend when UIState game already equals the picked type :256-259, else B5(game.g()) :260); :238-244 (K/L item click -> M); :205-214 (F: game list 0 always, 1/2/3 behind support540versionFunction)
- **`build_mic_beat_color`** (W): 33 05 05 00 [R][G][B]
  - base-classes11/com/govee/base2light/pact/newdetail/content/mic/MicController.java:41 (MicController.q, k false); base-classes11/com/govee/base2light/pact/newdetail/content/mic/DelegateMicPickup.java:1056-1059 (pick-up mode 3 MicController(r,g,b))
- **`build_mic_enter`** (W): 33 05 05 01 (zero-padded; app sends 2-, 3-, 5- or 6-byte forms that are identical after padding)
  - split_bulblightstringv1/com/govee/bulblightv3/ble/SubModeMic.java:17 (SubModeMic.getWriteBytes); split_bulblightstringv1/com/govee/bulblightstringv1/h6001/ble/SubModeMic.java:12 (SubModeMic.getWriteBytes)
- **`build_mic_h6165`** (W): 33 05 05 00 01
  - split_rgblight/com/govee/h6165/ble/MicController.java:20 (MicController.q); split_rgblight/com/govee/h6165/sku/AbsEffectOp4Ble.java:186 (only construction: MicController(true))
- **`build_mic_h6192`** (W): 33 05 05
  - split_rgblight/com/govee/stringlightv2/ble/SubModeMicH6192.java:12 (SubModeMicH6192.getWriteBytes); split_rgblight/com/govee/stringlightv2/adjust/ui/MicUiModeH6192.java:27 (getSubMode)
- **`build_mic_mode_color`** (W): 33 05 05 01 01 [R][G][B]
  - base-classes11/com/govee/base2light/pact/newdetail/content/mic/SubModeMic.java:97 (SubModeMic.getWriteBytes, c true and not auto); base-classes11/com/govee/base2light/pact/newdetail/content/mic/DelegateMicPickup.java:1063-1073 (pick-up 4 ModeController(SubModeMic))
- **`build_mic_v4`** (W): 33 05 05 03 [dynamic]
  - base-classes11/com/govee/base2light/pact/newdetail/content/mic/MicControllerV4.java:20 (MicControllerV4.q); base-classes11/com/govee/base2light/pact/newdetail/content/mic/DelegateMicPickup.java:1077 (micSubMode==2)
- **`build_mic_via_music_0x13`** (W): 33 05 13 10
  - split_rgbiclight/com/govee/chassislightv1/ble/SubModeMicV2.java:11 (SubModeMicV2.getWriteBytes); split_rgbiclight/com/govee/chassislightv1/pact/AbsEffectOp4Ble.java:95-116 (makeChange2MicModeBytesBySelf: BK or H7092)
- **`build_music_code_only`** (W): APPLY sequence: first the MultipleController4Music 0xA3 multi-packet (comm 0x41, value = [sens] + effect bytes), then 33 05 13 [code] 00.. [XOR@19]. This builde
  - split_h6061/com/govee/cubelight/newdetails/music/H6061CubeMusicMode.java:171-229 (H6061CubeMusicMode.d with its flag false: [19, code] :181-184; value byte 0 = sens :216-218; MultipleController4Music(sens, code, value) -> Compose4DefWrite4Multi.makeWriteController4Multi :219-229); :164-168 (applyDefaultMusic -> d(effect, false)); split_h6061/com/govee/cubelight/newdetails/music/H6065CubeMusicMode.java:262-265,298-310 (H6065CubeMusicMode.d)
- **`build_music_color`** (W): Codes 0 energy / 2 joy / 6 impact / 7 jet / 8 jump: 33 05 13 [code][sens][color_flag = !auto][R G B if !auto] 00.. [XOR@19] (call with sub_mode=0x13)
  - split_h6062/com/govee/striplight/newdetail/OldDevicePickSound.java:165-193 (OldDevicePickSound.q0, b() != 4 branch: no dyn byte); split_h6062/com/govee/striplight/ble/SubModeMusic.java:71-86 (SubModeMusic.getWriteBytes, a != 4: [19, a, b, !d, RGB if !d]; sub 19 :144-146, split_h6062/com/govee/striplight/ble/BleProtocol.java:19): LEGACY, no write caller (created only by split_h6062/com/govee/striplight/ble/Mode.java:30-31, :50-52, :66-68)
- **`build_music_dyn_before_sens`** (W): 33 05 13 [code][sub_effect...(hollowlamp V3: exactly 1 = dyn)][sens]
  - split_hollowlamp/com/govee/hollowlamp/ble/SubModeMusicV3.java:83-85 (SubModeMusicV3.getWriteBytes [19, a, f(), b, 0]; f() :46-58 is !c for code 75, !d for code 76, else 0; sub 19 :138-140; default code 75 = BleProtocol.f, ble/BleProtocol.java:13-15); split_hollowlamp/com/govee/hollowlamp/adjust/ui/MusicFragmentV3.java:37-43,63-67 (d1/e1 post EventBus Mode)
- **`build_music_dyn_color`** (W): Code 4 (rhythm): 33 05 13 04 [sens][dyn][color_flag = !auto][R G B if !auto] 00.. [XOR@19]. On the live control screen dyn = 0x00 when the user taps 'dynamic' a
  - split_h6062/com/govee/striplight/newdetail/OldDevicePickSound.java:165-193 (OldDevicePickSound.q0: [sub_mode_music, b(), e(), f() only if b() == 4, !a(), RGB via ColorUtils.m(d()) if !a()] -> ControllerMode.e, applyController opMode 8 'set music mode'); split_h6062/com/govee/striplight/newdetail/OldDevicePickSound.java:95-105 (OldDevicePickSound.M: l(0 when dynamic, else 1) at :103 (corrected from :104), then q0 :104); handlers K :77-81, L :83-93, N :107-117, O :119-129
- **`build_music_h6047`** (W): 33 05 13 [sens][code][x][y][pickup if gt 119]
  - split_h6047/com/govee/h6047/detail/mode/MusicMode.java:92 (MusicMode.d, :94-107); split_h6047/com/govee/h6047/detail/config/DetailConfig.java:89-98 (interceptor returns MusicMode)
- **`build_music_h6127_newdetail`** (W): 33 05 [01][code][sensitivity][R G B unless code is 0 or 3]
  - split_rgbiclight/com/govee/h6127/newdetail/OldMusicMode.java:131-134 (P -> ControllerMode.e(a0(...))), :207-211 (a0); base-classes11/com/govee/base2light/kt/general_controller/ControllerMode.java:61-65,102-105 (cmd-5 write)
- **`build_music_h6148`** (W): 33 05 0F [code u8, clamped 0..1][dyn u8][sens u8][color_flag u8, raw][R][G][B] 00.. [XOR@19]. The body is always 8 bytes and RGB is sent even when color_flag = 
  - split_rgblight/com/govee/homelightv1/newdetail/mode/H6148OldMusicMode.java:244-249 (H6148OldMusicMode.b0: c clamped 0..1, then [15, c] + [j(), h(), a(), R, G, B]); split_rgblight/com/govee/homelightv1/newdetail/mode/H6148OldMusicMode.java:169-186 (H6148OldMusicMode.Q: ControllerMode.Companion.e(b0(...)) :172; IMicFuc.applyController opMode 8 'set music mode' :182-185)
- **`build_music_h61a8`** (W): 33 05 13 [code][sensitivity], then for codes 5/4/6: [00 pad][color_flag = subEffect0][R G B only if flag != 0], or nothing after the pad if subEffectList is emp
  - split_rgbiclight/com/govee/rgbiclight/mode/music/MusicMode.java:82-136 (MusicMode.d: :90 the not-UIV1 flag, :97 the sub-mode from Protocols.b, :98-115 codes 5/4/6, :117-135 code 3); split_rgbiclight/com/govee/rgbiclight/Protocols.java:57-72 (Protocols.b: 19 unless supportv1UIV1/supportv2UIV1/Support4DreamColorLightV2.b; 12 for H6119 Telink), :75-89 (Protocols.c: [5,3,4,6] unless UIV1), :29-55 (Protocols.a)
- **`build_op15_brightness`** (W): 33 05 15 02 [pct u8][mask LSB-first, bit i = position i: u16le for <=16 positions (Y), ceil(n/8) bytes capped at 14 above (X)]
  - base-classes11/com/govee/base2light/pact/newdetail/content/color/ColorViewModel.java:1283-1318 (ColorViewModel.m0 applyRelativeBrightness); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:1069-1098 (Companion.t)
- **`build_op15_brightness_group`** (W): 33 05 15 03 [group][<=14 x level]
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:202 (Bytes.I); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:1144-1170 (Companion.x: 14 per group, group = base + i)
- **`build_op15_brightness_set`** (W): 33 05 15 03 [<=15 x level] (no group byte)
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:153 (Bytes.F); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:1173-1181 (Companion.y when Config.h(info)==TRUE; Config.B goodsTypes 235/299/339/345/349)
- **`build_op15_color_basic`** (W): 33 05 15 01 [R][G][B][mask: 2 bytes up to 16 segments (makeSelectedTwoBytes), else BleUtils.X ceil(n/8), at most 12 bytes]
  - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:670-672,684-693 (SubModeColorV1.getWriteBytes opType 1, form_15_01 basic); base-classes10/com/govee/base2kt/utils/BleUtils.java:1030-1041 (BleUtils.p copies the ext whole into the 20-byte frame)
- **`build_op15_color_bytes_h`** (W): 33 05 15 01 [R][G][B][00 x5][mask at ext byte 10: 2 bytes up to 16 positions (makeSelectedTwoBytes), else BleUtils.X ceil(n/8), at most 7 bytes]
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:315-326 (Bytes.h: coerceAtMost(len, 17 - 10), so positions 56+ are dropped); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:12960-12968 (Config.Yt, when the colour is not a Kelvin tint)
- **`build_op15_color_group`** (W): 33 05 15 04 [group][<=4 x R G B]
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:393 (Bytes.m); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:879-896 (Companion.l: 4 colours per group, group = base + i)
- **`build_op15_color_temp_04`** (W): 33 05 15 04 [kelvin u2be] [00 x13] [bcc]
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:133-139 (Bytes.D: [21, 4] + BleUtils.D(k, true)); base-classes10/com/govee/base2kt/utils/BleUtils.java:132-142 (D with its flag true: high byte first)
- **`build_op15_color_temp_bytes_r`** (W): 33 05 15 01 [slot R G B: ff ff ff; 00 00 00 for kind 0; kind 3: 00 00 00 when every position is set, else the tint with kelvin and tint zeroed][kelvin u16be][tint R G B][mask as Bytes.h]
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:474-501 (Bytes.r; kind = its white-type argument, :480-486); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:12967 (Config.Yt); GradientColorControllerConfig.k :334 (kind 1)
- **`build_op15_color_v5`** (W): 33 05 15 01 [R G B][kelvin u16be: 00 00 for an RGB pick (Bytes.l), Constant.getColorTemKelvin(colour) for a tint (Bytes.u)][mask LSB-first, copied whole: 2 byte
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:13124-13131 (Config.Zt = J[5]); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:379-390,541-556 (Bytes.l / u)
- **`build_op15_gradient_color`** (W): 33 05 15 05 [R1 G1 B1] [R2 G2 B2] [R3 G3 B3] [segment mask, LE bit i = segment i: 2 bytes for <=16 positions, ceil(n/8) bytes above] [00..] [bcc]
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:404-420 (Bytes.n: [21, 5] + ColorUtils.n(c) for each colour + mask); base-classes10/com/govee/base2kt/utils/ColorUtils.java:314-316 (n: R, G, B)
- **`build_op15_kelvin_05`** (W): 33 05 15 05 [kelvin u16be]; colour tab: K = ColorUtils.h(colour, min, max) for a tint with the last position selected; white-light tab: the raw slider K
  - split_h60ax/com/govee/pact_h60a0/adjust/h60a0/ControllerBuildH60A0.java:51-67 (ControllerBuildH60A0.b, tint on the last position); split_h60ax/com/govee/pact_h60a0/adjust/h60a0/ControllerBuildH60A0.java:93-99 (ControllerBuildH60A0.d, white-light tab)
- **`build_op15_relative_brightness`** (W): 33 05 15 05 [side u8: 3 if <2 positions or pos[0] and pos[1]; 2 if !pos[0]; else 1] [value u8] [00 x12] [bcc]
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:184-199 (Bytes.H: [21, 5, side, value] in 17 bytes); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5591-5601 (L: 0 -> JR = Bytes.G 15 02, 1 -> KR), :10727-10730 (KR)
- **`build_op15_set_mode`** (W): 33 05 15 00
  - split_h60ax/com/govee/pact_h60a0/ble/v1/SubModeColorV1.java:699 (SubModeColorV1.getWriteBytes opType 3; Companion.m sets it :360-364); split_h60ax/com/govee/pact_h60a0/ble/controller/compose/ComposeChange2ColorMode.java:32 (adds the write, then a read)
- **`build_op15_side_brightness`** (W): 33 05 15 05 [side u8: 3 when posSet has fewer than 2 entries or positions 0 and 1 are both selected, 2 when position 0 is unselected, 1 when only position 0 is 
  - base-classes11/com/govee/base2light/pact/newdetail/content/color/ColorViewModel.java:1283-1302 (ColorViewModel.m0); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:1069-1098 (Companion.t)
- **`build_operate_mode_20`** (W): 33 05 20 (no further bytes)
  - base-classes11/com/govee/base2light/pact/ble/SubModeOperate.java:227-230 (getWriteBytes = [subModeCommandType]), :423-425 (32); base-classes4/com/govee/starProjector/ControlConfig4H609X.java:68-96 (d selects e for goodsType 167; e: MultiController.a(j().k(false), 0x56) then ControllerMode.Companion.e([32]))
- **`build_scene_ext_carousel`** (W): 33 05 04 [effect u2le] [music_code u4le: code if switch else 0] [music_voice_type u8] [00 x9] [bcc]
  - base-classes11/com/govee/base2light/pact/newdetail/content/scene/SubModeScenes.java:60-69 (getWriteBytes with e true: getSignedBytesFor4(b, or 0 when c is false; flag false), then d as one byte); base-classes11/com/govee/base2light/pact/newdetail/content/carousel/CarouselEffectManager.java:892-927 (l: sets e = true and the voice type when DeviceDetailConfig.k0(info).r(); static scenes embed modeControllerNoEvent.getNextCommBytes() into carouselEffect.effectStr via k at :924-926)
- **`build_scene_newdetail`** (W): 33 05 04 [effect u2le] [music_code u2le: getMusicCode if getMusicSwitch else 0; always 0 for SameModeInfo] [00 x12] [bcc]
  - base-classes11/com/govee/base2light/pact/newdetail/content/scene/SubModeScenes.java:59-77 (getWriteBytes, e false: [f=4][getSignedBytesFor2(a,false)][getSignedBytesFor2(b, or 0 when c is false; flag false)]), :15-21 (fields, f = 4); base-classes11/com/govee/base2light/pact/newdetail/content/scene/BaseSceneViewMode.java:722-740 (Z2: effect = getSceneCode, b = getMusicCode, c = getMusicSwitch unless SameModeInfo)
- **`build_video_h6101`** (W): 33 05 00 [2 for mode 2, else 0][0 when a is set, else 1]
  - split_rgblight/com/govee/h6101/ble/SubModeVideo.java:38 (SubModeVideo.getWriteBytes); split_rgblight/com/govee/h6101/adjust/VideoFragment.java:42-58 (VideoFragment.s0/t0 posts the Mode)
- **`build_video_h6104`** (W): 33 05 00 00 [!a][b]
  - split_rgblight/com/govee/h6104/ble/SubModeVideo.java:38 (SubModeVideo.getWriteBytes); split_rgblight/com/govee/h6104/adjust/VideoFragment.java:41-57 (VideoFragment.s0/t0 posts the Mode)

### `0x07` — source-modeled

- **`build_diy_old_07`** (W): 33 05 [07][diyCode][effect][subEffect][speed][<=4 x R G B, zero padded to 12 bytes]
  - split_rgbiclight/com/govee/h6127/ble/SubModeOldDiy.java:16-39,45-51,63-65 (ctor packs b[0..15]; getWriteBytes prepends 7); base-classes10/com/govee/base2light/ac/diy/DiyProtocol.java:75-93 (b diyCode, c effect, f subEffect, e speed)
- **`read_device_other_info`** (R): AA 07 08 00 x16 [XOR = 0xA5]
  - split_h60ax/com/govee/pact_h60a0/ble/controller/compose/ControllerOtherInfo.java:24-26,29-31,39-41 (ext [08], super(false) read, cmd 7); split_h60ax/com/govee/pact_h60a0/adjust/v1/ComposeReadAllInfoV1.java:76-85 (consumed at split_h60ax/com/govee/pact_h60a0/adjust/v1/BleOpV1.java:81-83)
- **`read_device_sn`** (R): AA 07 02 [00 x16] [bcc=AF]
  - base-classes10/com/govee/base2light/ble/controller/SnController.java:32 (SnController.getCommandType); split_h61d3/com/govee/h61d3/ble/controller/SnController.java:69 (SnController.getCommandType)
- **`read_dsp_version`** (R): AA 07 07 [00 x16] [bcc=AA]
  - base-classes10/com/govee/base2light/ble/controller/DspVersionInDeviceInfoController.java:15 (DspVersionInDeviceInfoController.getCommandType); base-classes11/com/govee/base2light/kt/general_controller/ControllerDspVersionInDeviceInfo.java:67 (ControllerDspVersionInDeviceInfo.getCommandType)
- **`read_hard_version`** (R): AA 07 03 [00 x16] [bcc=AE]
  - base-classes10/com/govee/base2light/ble/controller/HardVersionController.java:23 (HardVersionController.getCommandType); base-classes11/com/govee/base2light/kt/general_controller/ControllerHardVersion.java:31 (ControllerHardVersion.getCommandType)
- **`read_mcu_hard_version`** (R): AA 07 0B [00 x16] [bcc=A6]
  - base-classes10/com/govee/base2light/ble/controller/McuHardVersionControllerV1.java:26 (McuHardVersionControllerV1.getCommandType); base-classes12/com/govee/base_h71xx/sku_base/ble/controller/McuHardVersionController.java:18 (McuHardVersionController.getCommandType)
- **`read_mcu_soft_version`** (R): AA 07 0A [00 x16] [bcc=A7]
  - base-classes10/com/govee/base2light/ble/controller/McuSoftVersionControllerV1.java:26 (McuSoftVersionControllerV1.getCommandType); base-classes12/com/govee/base_h71xx/sku_base/ble/controller/McuSoftVersionController.java:18 (McuSoftVersionController.getCommandType)
- **`read_soft_version_in_device_info`** (R): AA 07 04 [00 x16] [bcc=A9]
  - split_h61d3/com/govee/h61d3/ble/controller/SoftHardController.java:82 (SoftHardController.getCommandType, Companion.e ext [04] :66-69); base-classes10/com/govee/base2light/ble/controller/SoftVersionInDeviceInfoController.java:13 (SoftVersionInDeviceInfoController.getCommandType)

### `0x08` — source-modeled

- **`build_h6101_calibration`** (W): 33 08 (no payload)
  - split_rgblight/com/govee/h6101/ble/CalibrationController.java:11-17 (getCommandType 8, q null); split_rgblight/com/govee/h6101/adjust/CalibrationAc.java:137-139 (Z0)

### `0x09` — firmware-confirmed

- **`build_string_scene_09`** (W): 33 05 [09][index+1][15 x 00]
  - split_bulblightstringv1/com/govee/bulblightstringv1/ble/SubModeScenes.java:22-27,49-51 (getWriteBytes: 17 bytes [9, a+1, 0...]); split_bulblightstringv1/com/govee/h7022/adjust/mode/SubModeScenes.java:41-46 (same body)
- **`build_sync_time_stamp`** (W): 33 09 [unix_seconds u32be][0x01][tz_hour s8][tz_min s8] 00.. [BCC]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTimeStamp.java:29-32 (Companion.a: getSignedBytesFor4(now/1000, true) BE), :44-48 (<init>: [ts0..ts3, 1, tzH, tzM], k = 9), :54-56 (getCommandType = k); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:181 (only caller; version 1), :465-467 (v1 goodsType list)
- **`build_sync_time_v0`** (W): 33 09 [hour][minute][second][week 1=Mon..7=Sun][0x01][tz_hour s8][tz_min s8][day][month 1-12][year u16le][unix_seconds u32le] 00.. [BCC]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerSyncTime.java:31-35 (ControllerSyncTime.Companion.a), :71-73 (getCommandType = 9, hard-coded); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:169-182 (TimerConfig.Companion.f: version 0, null info, or any version other than 1/2 -> a()), :463-479 (Config.Companion.b)
- **`read_daysync_device_info`** (R): AC 09
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerSingleRead.java:44-72 (ControllerSingleRead.Companion.makeSingleRead); base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncVM.java:319-323 (DaySyncVM.Companion.f)

### `0x0a` — source-modeled

- **`build_auto_time`** (W): 33 0A [on 0/1][openH][openM][closeH][closeM][group][repeat]
  - base-classes10/com/govee/base2light/ble/controller/AutoTimeController.java:62-64 (AutoTimeController.q; ctor :20-29; getCommandType :45-47 = 10); base-classes11/com/govee/base2light/kt/general_controller/ControllerTimer.java:52-55 (ControllerTimer.<init>, the 8-argument write form; Companion.a:29-31 passes command 10)
- **`read_auto_time`** (R): AA 0A [group]
  - base-classes10/com/govee/base2light/ble/controller/AutoTimeController.java:50-52 (AutoTimeController.p; read ctor :15-18); base-classes11/com/govee/base2light/kt/general_controller/ControllerTimer.java:42-46 (ControllerTimer.<init>(byte,int); synthetic :48-50 defaults command 10)

### `0x0b` — source-modeled

- **`build_delay_close`** (W): 33 0B [on 0/1][hours][minutes]  (from total minutes m: max(0,m)//60, max(0,m)%60)
  - base-classes10/com/govee/base2light/ble/controller/DelayCloseController.java:46-50 (DelayCloseController.q; ctor :15-19; getCommandType :27-29 = 11); base-classes11/com/govee/base2light/kt/general_controller/ControllerDelayClose.java:74-76 (ControllerDelayClose.<init>(write, on, minutes); getCommandType :90-92 = BleProtocol.music_code_yuedong = 11, base-classes11/com/govee/base2light/pact/feast/BleProtocol.java:25)
- **`build_game_h6057`** (W): 33 05 [0B][gameCode]
  - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:657-662 (H6057DetailVM.B5: ControllerMode.Companion.e([0x0b, gameCode])); split_hollowlamp/com/govee/h6057/origin/newDetail/H6057GameDelegate.java:254-260 (control-screen game delegate)
- **`read_delay_close`** (R): AA 0B (no selector)
  - base-classes10/com/govee/base2light/ble/controller/DelayCloseController.java:11-13 (DelayCloseController.<init>(), p() null); base-classes11/com/govee/base2light/kt/general_controller/ControllerDelayClose.java:51-53,57-66 (ControllerDelayClose.Companion.b / c)
- **`read_mode_bare`** (R): AA 05 (no selector)
  - split_h60ax/com/govee/pact_h60a0/ble/controller/compose/ModeController.java:26-28 (compose ModeController.Companion.a: read, ext null; framed by compose AbsController.java:23-26 -> BleUtils.Companion.p(0xAA, 5, null)); split_h60ax/com/govee/pact_h60a0/adjust/v1/ComposeReadAllInfoV1.java:148 (pact_h60a0 adjust-v1 connect read, from BleOpV1.V :47-51). Not H6057: kt ControllerMode.Companion.b (ControllerMode.java:45-47) passes mask 2, so ext defaults to [01] (:107-109) and Compose4InfoBleIotH6057$iControllerStrategy$1.java:165 sends AA 05 01 (read_mode)

### `0x0d` — source-modeled

- **`build_plant_red_blue`** (W): 33 0D [red u8 @2][blue u8 @3, always 01 on the live path] + zero pad + BCC @19
  - split_rgblight/com/govee/rgblight/h7004/ConnectSuccess4Plant.java:392-400 (ConnectSuccess4Plant.J: Controller4ExtBytes.Companion.d([0x0d, red, 01])); base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:85-100 (Controller4ExtBytes.Companion.d: write, cmd = bytes[0], payload = bytes[1..])
- **`read_plant_red_blue`** (R): AA 0D (no selector) + zero pad + BCC @19
  - split_rgblight/com/govee/rgblight/ControllerStrategyConfig.java:1345-1351 (ControllerStrategyConfig.k1: Controller4ExtBytes.Companion.c([0x0d])); base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:66-82 (Controller4ExtBytes.Companion.c: read, no payload)

### `0x0e` — source-modeled

- **`build_current_limit`** (W): 33 0E [1 = limit on | 0 = off] [00 x16] [bcc]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerLimit.java:48-51,59-61,71-73 (ControllerLimit(on) -> (true, [on 1|0]); getCommandType 14; Companion.d); split_rgblight/com/govee/rgblight/SettingAc.java:113-125 (g4 via ShareVM.doCommController), :148-159 (l4 toggle), :172-178 (q4 gate = Support.l(BleWifiDeviceInfo.g())), :365-381 (Z1 adds the op only when q4)
- **`read_current_limit`** (R): AA 0E [00 x17] [bcc]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerLimit.java:29-46 (Companion.b: ControllerLimit(false, null); c: reply byte 0 == 1); base-classes11/com/govee/base2light/kt/ble/AbsController.java:42-44 (getNextCommBytes with null payload -> zeros)

### `0x0f` — source-modeled

- **`build_light_num`** (W): 33 0F [n]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerSegment.java:61-63 (write ctor [n]), :73-75 (getCommandType = 15; no caller); split_bulblightstringv1/com/govee/bulblightstringv1/ble/BulbNumController.java:22,32-34 (getCommandType / q = [f])
- **`build_light_num_h612526`** (W): 33 0F, empty payload (bytes 2..18 zero), XOR at byte 19 (0x3C)
  - split_rgbiclight/com/govee/h612526/newdetail/ControllerLightNum.java:31-33 (ControllerLightNum.<init>(): the synthetic ctor with mask 3), :39-41 (synthetic ctor: mask bit 1 gives a null payload, mask bit 2 gives the write flag true), :35-37 (super(write, payload)), :43-45 (getCommandType = 15); split_rgbiclight/com/govee/h612526/newdetail/Vm4Light.java:508-514 (Vm4Light.W4: ControllerLightNum with mask 3 in the connect list), :190-194 (Vm4Light.d5 listener)
- **`read_light_num`** (R): AA 0F (no selector)
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerSegment.java:40-48 (Companion.b: read; no caller); split_rgbiclight/com/govee/dreamcolorlightv1/ble/LightNumController.java:11 (getCommandType = 15, AbsOnlyReadSingleController)

### `0x11` — firmware-confirmed

- **`build_multi_wifi_a1`** (W): proType 0xA1 multi-packet write, comType 0x11. Every frame has the XOR of bytes 0..18 at byte 19. START = [A1 11 00 packs 00], zero-padded: BleUtils.Companion.p
  - base-classes10/com/govee/base2light/ble/controller/MultipleWifiController.java:20-57 (MultipleWifiController.<init>(ssid,password,runModeV)); base-classes10/com/govee/base2light/ble/controller/MultipleWifiController.java:59-104 (MultipleWifiController.<init>(ssid,password,runModeV,apiStr))
- **`build_sleep`** (W): 33 11 [enable][startBri][closeTime min][curTime min]
  - base-classes10/com/govee/base2light/ble/controller/SleepController.java:56-58 (SleepController.q; ctor :17-23; getCommandType :45-47 = 17); base-classes11/com/govee/base2light/kt/general_controller/ControllerSleep.java:121-123 (ControllerSleep.<init>(int,int,int,int) via Companion.a:46-48; k = 17 at :127) = TimerConfig sleep version 0/default (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:145-146,163-164)
- **`build_sleep_color`** (W): 33 11 [enable][startBri][closeTime][curTime][defaultLight][R][G][B][kelvin u16: BE (v1) / LE (v3)]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerSleep.java:96-107 (ControllerSleep.Companion.g; default-args mask wrapper :35-43); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:148 (TimerConfig sleep v1, mask 12 -> kelvin BE)
- **`build_sleep_effect`** (W): 33 11 [enable][startBri][closeTime][curTime][openLampEffect][lampEffectType][effectCode u16 LE]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerSleep.java:51-56 (ControllerSleep.Companion.b) = TimerConfig sleep v6 (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:157-158)
- **`build_sleep_effect_lc_music`** (W): 33 11 [enable][startBri][closeTime][curTime][openLampEffect][lampEffectType][effectCode u16 LE][openVoice][voiceVolume][voiceCode u8]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerSleep.java:88-93 (ControllerSleep.Companion.f) = TimerConfig sleep v8 (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:161-162)
- **`build_sleep_fanlight`** (W): 33 11 [enable][lightOpen][startBri][closeTime][curTime][defaultLight][R][G][B][kelvin u16 BE][fanOpen][fanExeOpen][fanCloseTime][fanCloseCurTime][fanGear]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerSleep.java:59-67 (ControllerSleep.Companion.c) = TimerConfig sleep v7 (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:159-160)
- **`build_sleep_h6093`** (W): 33 11 [enable][startBri][closeTime][curTime][openVoice][voiceCode u8][voiceVolume][defaultLight][R][G][B][laserSwitch]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerSleep.java:70-75 (ControllerSleep.Companion.d) = TimerConfig sleep v4 (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:153-154)
- **`build_sleep_h60a0`** (W): 33 11 [enable][startBri][closeTime][curTime][defaultLight][R][G][B]
  - base-classes10/com/govee/base2light/ble/controller/SleepWithColorController.java:41-45 (SleepWithColorController.Companion.b; getCommandType :70-72 = 17)
- **`build_sleep_h6630`** (W): 33 11 [enable][startBri][closeTime][curTime][openLampEffect][lampEffectType][effectCode u16 LE][openVoice][voiceVolume][voiceCode u32 LE][version]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerSleep.java:78-85 (ControllerSleep.Companion.e) = TimerConfig sleep v5 (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:155-156)
- **`build_sleep_v1`** (W): 33 11 [enable][startBri][closeTime][curTime][openVoice 0/1][voiceCode u8][voiceVolume][defaultLight][R][G][B]
  - base-classes10/com/govee/base2light/ble/controller/SleepControllerV1.java:43-51 (SleepControllerV1.q; ctor :20-27; command inherited SleepController:45-47)
- **`build_sleep_v2`** (W): 33 11 [enable][startBri][closeTime][curTime][defaultLight][R][G][B][kelvin u16 BE][openVoice 0/1][voiceVolume][voiceCode u16 LE]
  - base-classes10/com/govee/base2light/ble/controller/SleepControllerV2.java:80-93 (SleepControllerV2.q; ctor :23-30); base-classes11/com/govee/base2light/kt/general_controller/online/ControllerOnlineSleep.java:53-62 (ControllerOnlineSleep.Companion.b; byte-identical, no caller in decomp)
- **`read_sleep`** (R): AA 11 (no selector)
  - base-classes10/com/govee/base2light/ble/controller/SleepController.java:13-15 (SleepController.<init>(), p() null; inherited by SleepControllerV1/V2); base-classes11/com/govee/base2light/kt/general_controller/ControllerSleep.java:117-119 (ControllerSleep.<init>(), null extBytes)

### `0x12` — firmware-confirmed

- **`build_wakeup`** (W): 33 12 [enable][endBri][hour][minute][repeat][wakeTime min]
  - base-classes10/com/govee/base2light/ble/controller/WakeUpController.java:70-72 (WakeUpController.q; ctor :19-27; getCommandType :59-61 = 18); base-classes11/com/govee/base2light/kt/general_controller/ControllerWakeUp.java:123-126 (ControllerWakeUp.<init>(int,int,int,int,byte,int,byte) via Companion.h:113-115, command defaults to 18 at :128-130) = TimerConfig wake-up version 0/default (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:247-248,267-268)
- **`build_wakeup_color`** (W): 33 12 [enable][endBri][hour][minute][repeat][wakeTime][defaultLight][R][G][B][kelvin u16: BE (v1) / LE (v3)]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerWakeUp.java:44-57 (ControllerWakeUp.Companion.a; default-args mask wrapper :33-41); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:250 (TimerConfig wake-up v1, mask 12 -> kelvin BE)
- **`build_wakeup_effect`** (W): 33 12 [enable][endBri][hour][minute][repeat][wakeTime][openLampEffect][lampEffectType][effectCode u16 LE]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerWakeUp.java:68-73 (ControllerWakeUp.Companion.c) = TimerConfig wake-up v6 (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:259-260)
- **`build_wakeup_effect_lc_music`** (W): 33 12 [enable][endBri][hour][minute][repeat][wakeTime][openLampEffect][lampEffectType][effectCode u16 LE][openVoice][voiceVolume][voiceCode u8]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerWakeUp.java:60-65 (ControllerWakeUp.Companion.b) = TimerConfig wake-up v9 (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:265-266)
- **`build_wakeup_fanlight`** (W): 33 12 [enable][lightOpen][endBri][hour][minute][repeat][wakeTime][defaultLight][R][G][B][kelvin u16 BE][fanOpen][fanExeOpen][fanGear]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerWakeUp.java:76-84 (ControllerWakeUp.Companion.d) = TimerConfig wake-up v7 (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:261-262)
- **`build_wakeup_h6093`** (W): 33 12 [enable][endBri][hour][minute][repeat][wakeTime][openVoice][voiceCode u8][voiceVolume][defaultLight][R][G][B][laserSwitch]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerWakeUp.java:87-94 (ControllerWakeUp.Companion.e) = TimerConfig wake-up v4 (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:255-256) and v8 (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:263-264)
- **`build_wakeup_h60a0`** (W): 33 12 [enable][endBri][hour][minute][repeat][wakeTime][defaultLight][R][G][B]
  - base-classes10/com/govee/base2light/ble/controller/WakeUpWithColorController.java:41-45 (WakeUpWithColorController.Companion.b; getCommandType :70-72 = 18)
- **`build_wakeup_h6630`** (W): 33 12 [enable][endBri][hour][minute][repeat][wakeTime][openLampEffect][lampEffectType][effectCode u16 LE][openVoice][voiceVolume][voiceCode u32 LE][version]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerWakeUp.java:97-104 (ControllerWakeUp.Companion.f) = TimerConfig wake-up v5 (base-classes11/com/govee/base2light/pact/newdetail/config/fuc/TimerConfig.java:257-258)
- **`build_wakeup_v1`** (W): 33 12 [enable][endBri][hour][minute][repeat][wakeTime][openVoice 0/1][voiceCode u8][voiceVolume][defaultLight][R][G][B]
  - base-classes10/com/govee/base2light/ble/controller/WakeUpControllerV1.java:50-58 (WakeUpControllerV1.q; ctor :20-27)
- **`build_wakeup_v2`** (W): 33 12 [enable][endBri][hour][minute][repeat][wakeTime][defaultLight][R][G][B][kelvin u16 BE][openVoice 0/1][voiceVolume][voiceCode u16 LE]
  - base-classes10/com/govee/base2light/ble/controller/WakeUpControllerV2.java:84-97 (WakeUpControllerV2.q; ctor :23-30); base-classes11/com/govee/base2light/kt/general_controller/online/ControllerOnlineWakeUp.java:46-55 (ControllerOnlineWakeUp.Companion.b; byte-identical, no caller in decomp)
- **`read_plug_timer_count`** (R): AA 12 [index]
  - split_plugnew/com/govee/h5080/ble/controller/NewTimerCountController.java:23-25 (NewTimerCountController.p; getCommandType :19-21 = BleConstants.SINGLE_NEW_TIMER_COUNT = 18; extends AbsOnlyReadSingleController, read-only)
- **`read_wakeup`** (R): AA 12 (no selector)
  - base-classes10/com/govee/base2light/ble/controller/WakeUpController.java:15-17 (WakeUpController.<init>(); inherited by WakeUpControllerV1/V2); base-classes11/com/govee/base2light/kt/general_controller/ControllerWakeUp.java:119-121 (ControllerWakeUp.<init>(), empty extBytes)

### `0x13` — firmware-confirmed

- **`build_direction_h6104`** (W): 33 13 [direction u8]
  - split_rgblight/com/govee/h6104/ble/DirectionController.java:11-15,36-37 (DirectionController(int).q); caller split_rgblight/com/govee/h6104/adjust/AdjustAc.java:669
- **`build_night_mode`** (W): 33 13 [brightness (UI 10..100)][0 default colour] | 33 13 [brightness][1][R][G][B]
  - base-classes10/com/govee/base2light/ac/nightmode/NightModeViewModel.java:190-215 (P: literal 19, Controller4ExtBytes.e); base-classes10/com/govee/base2light/ac/nightmode/NightModeAc.java:327
- **`build_plug_timer_v2`** (W): 33 13 [outlet][slot][enSw: bit7 enable | bit0 on][hour][minute][repeat weekday mask]
  - split_plugnew/com/govee/h5080/ble/controller/NewTimerControllerV2.java:100-102 (q); callers split_plugnew/com/govee/h5080/adjust/v1/UiV1.java:835 and split_plugnew/com/govee/h5080/adjust/h5089/Ui4H5089.java:856,1011
- **`read_night_mode`** (R): AA 13 (no selector)
  - split_bulblightstringv1/com/govee/bulblightv3/newdetail/NewDetailVm.java:773-781 (f5: Controller4ExtBytes.c([19]))
- **`read_night_mode_or_direction`** (R): AA 13 (no selector)
  - split_bulblightstringv1/com/govee/bulblightv3/newdetail/NewDetailVm.java:773-781 (f5, Controller4ExtBytes.Companion.c([0x13])); in the connect read list at :497; split_rgblight/com/govee/h6104/ble/DirectionController.java:17-20 (read ctor super(false), no p()); callers split_rgblight/com/govee/h6104/adjust/AdjustAc.java:585 and split_rgblight/com/govee/h6104/ble/BleReadManager.java:62
- **`read_plug_timer_h5089`** (R): AA 13 [outlet<<4 | package_index]
  - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4H5089TimerInfo.java:15-19,44-46 (ctor (pkg -> f, outlet -> g), p() = [(g<<4)|f]); caller split_plugnew/com/govee/h5080/adjust/h5089/BleOp4H5089.java:616-618
- **`read_plug_timer_v2`** (R): AA 13 [outlet][package_index 0..3]
  - split_plugnew/com/govee/h5080/ble/controller/NewTimerControllerV2.java:87-93 (read ctor (pkg -> f, outlet -> h), p() = [h, f]); caller split_plugnew/com/govee/h5080/adjust/v1/BleOpV1.java:481-497

### `0x14` — source-modeled

- **`build_gradual`** (W): 33 A3 [1 = gradual on | 0 = off] [00 x16] [bcc]
  - base-classes11/com/govee/base2light/pact/newdetail/content/color/ColorViewModel.java:1123-1150 (i0 applyGradual: unified ColorModeControllerConfig.p first, else the VM's configGradualController); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:985-1012 (p: version from LightColorCapabilityCenter.q1 -> legacy -> Config.I)
- **`build_gradual_ble`** (W): 33 14 [1 = gradual on | 0 = off] [00 x16] [bcc]
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Bytes.java:558-560 (Bytes.v = [20, on]); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/Config.java:5608-5612 (M[1] = hK), :14343-14345 (hK -> Bytes.v)
- **`build_night_mode_page`** (W): 33 14 [page_active u8: 01 = page entered / 00 = page left] 00 x16 [BCC 0x26 / 0x27]
  - base-classes10/com/govee/base2light/ac/nightmode/NightModeViewModel.java:86-99 (NightModeViewModel.C: Controller4ExtBytes.e([20, 1])), :101-114 (NightModeViewModel.E: e([20, 0])); base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:102-118 (Controller4ExtBytes.Companion.e: write, cmd = bytes[0], ext = rest)
- **`build_night_mode_page_open_h601e`** (W): 3 frames in order: 33 14 01 [BCC 0x26]; 33 36 01 01 [BCC 0x05]; 33 36 00 00 [BCC 0x05]
  - base-classes10/com/govee/base2light/ac/nightmode/NightModeViewModel.java:51-54 (callback D runs Q0 whatever the result), :86-99 (NightModeViewModel.C); base-classes10/com/govee/base2light/ac/nightmode/NightModeAc.java:136-143 (NightModeAc.Q0: 311 == bleWifiDeviceInfo.g() -> V0().L())
- **`read_wifi_mac_or_gradual`** (R): AA 14 [00 x17] [bcc]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerWifiMacAddress.java:27-30 (k = 20, read), :36-38 (getCommandType); split_rgbiclight/com/govee/rgbiclight/ControllerStrategyConfig.java:1053-1056 (zF = Support4DreamColorLightV2.f: goodsType 21), :1086-1089 (-> V1), :1328-1334 (V1 adds ControllerWifiMacAddress, handler g2), :1309-1313 (gradual is read as AA A3 via ControllerGradual4BleWifi.Companion.b)

### `0x15` — firmware-confirmed

- **`build_h5080_timer_delete`** (W): 33 15 [group (0 for the 1-arg ctor)][index]
  - split_plugnew/com/govee/h5080/ble/controller/TimerDeleteController.java:10-30 (ctors; getCommandType 21; q [g, f]); split_plugnew/com/govee/h5080/adjust/v2/UiV2.java:927
- **`build_h5086_timer_delete`** (W): 33 15 [index]
  - split_plugnew/com/govee/h5086/ble/TimerDeleteController.java:22-36 (Companion.a -> TimerDeleteController([index]); getCommandType 21)
- **`build_plug_timer_delete`** (W): 33 15 [outlet u8][timer_index u8]
  - split_plugnew/com/govee/h5080/ble/controller/TimerDeleteController.java:10-18 (TimerDeleteController.<init>: f = index, g = outlet; the 1-arg ctor sets g = 0); split_plugnew/com/govee/h5080/ble/controller/TimerDeleteController.java:24-26 (TimerDeleteController.getCommandType = 21 = 0x15)
- **`read_h6104_ip`** (R): AA 15 (no selector)
  - split_rgblight/com/govee/h6104/ble/IPController.java:12-35 (read ctor super(false); getCommandType 21; parseValidBytes); split_rgblight/com/govee/h6104/add/calibration/CalibrationWifiActivity.java:336

### `0x16` — source-modeled

- **`build_abs_music_16`** (W): 33 05 [16][musicCode u16 LE][sensitivity (default 99)]
  - base-classes10/com/govee/base2light/ble/controller/SubModeAbsMusic.java:21,76-80,97-99 (default 99, getWriteBytes [22, lo, hi, d], type 22); base-classes12/com/govee/base2light/util/ByteUtils.java:217-219 (getLowHighBytes = LE)
- **`build_effect_direction`** (W): 33 16 [direction u8]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerEffectDirection.java:61-63 (ControllerEffectDirection(int): [direction]), :51-53 (Companion.d), :73-75 (cmd 22); split_h61d3/com/govee/h61d3/adjustNew/setting/NewSettingActivity.java:755 (NewSettingActivity: Compose4DefWrite.a(ControllerEffectDirection.Companion.d(i)))
- **`build_energy_saving`** (W): 33 16 [on]
  - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:787,815-819 (K4 case A0 TAG_SAVE_ENERGY_MODE: ControllerSingle.c(22, [on 1|0])); split_hollowlamp/com/govee/hollowlamp/newdetails/h6058/ControllerEnergySaving.java:46-47,56-63 via split_hollowlamp/com/govee/hollowlamp/newdetails/h6058/H6058NewDetailVm.java:429
- **`build_light_indicator`** (W): 33 16 [enable][startH][startM][endH][endM]; forever -> 33 16 [enable][FF][FF][FF][FF]
  - split_hollowlamp/com/govee/tablelampv1/ble/NewLightIndicatorController.java:45-48,64-66 (Companion.d; getCommandType 22) via split_hollowlamp/com/govee/tablelampv1/newAdjust/H6078NewDetailVM.java:742; split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:230-240 (v0)
- **`build_plug_not_disturb_h5086`** (W): 33 16 [enable][start_h][start_m][end_h][end_m][forever 1/0] (time bytes are NOT forced to FF when forever)
  - split_plugnew/com/govee/h5086/ble/controller/LightController.java:53 (LightController.Companion.d); caller split_plugnew/com/govee/h5086/adjust/SmartPlugProAdjustViewModel.java:1133-1136 (Z4)
- **`read_light_indicator`** (R): AA 16 (no selector)
  - split_hollowlamp/com/govee/tablelampv1/ble/NewLightIndicatorController.java:32-42 (Companion.b) via split_hollowlamp/com/govee/tablelampv1/newAdjust/H6078NewDetailVM.java:607; split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:328-330 (createSingleReadMsg(22, null))

### `0x17` — firmware-confirmed

- **`build_plug_pro_exception_state`** (W): 33 17 01 (constant)
  - split_plugnew/com/govee/h5086/ble/controller/ExceptionStateController.java:44-47,54-56 (ExceptionStateController.Companion.d/getCommandType; kt AbsController framing base-classes11/com/govee/base2light/kt/ble/AbsController.java:43-44); sent at split_plugnew/com/govee/h5086/adjust/SmartPlugProAdjustViewModel.java:709 (x3, clearChartData success callback)
- **`build_wifi_link_start`** (W): 33 17 [start u8: 1=start link, 0=stop]
  - base-classes10/com/govee/base2light/ble/controller/WifiLinkStarController.java:7-9,16-19,21-24 (WifiLinkStarController.<init>/getCommandType/q); base-classes10/com/govee/base2light/ble/controller/WifiLinkStarControllerV1.java:6-27 (subclass, callbacks only); sent at base-classes10/com/govee/base2light/connect/present/WifiPresent.java:114
- **`read_plug_pro_exception_state`** (R): AA 17 [no selector] [bcc=BD]
  - split_plugnew/com/govee/h5086/ble/controller/ExceptionStateController.java:32-42,54-56 (ExceptionStateController.Companion.b/getCommandType); issued in split_plugnew/com/govee/h5086/ble/controller/ReadInfoController.java:97-101

### `0x19` — source-modeled

- **`read_h5086_electric`** (R): AA 19 (no selector)
  - split_plugnew/com/govee/h5086/ble/controller/DeviceElectricController.java:33-51,62-64 (Companion.b read; Companion.d reply; getCommandType ControllerProtocol.w = 25); split_plugnew/com/govee/h5086/ble/controller/ControllerProtocol.java:34

### `0x1a` — source-modeled

- **`build_h5086_time_monitor`** (W): 33 1A [on][hour][minute][id u32 BE]
  - split_plugnew/com/govee/h5086/ble/controller/TimeMonitorController.java:47-51,87-89 (Companion.d: [on, h, m, getSignedBytesFor4(id, true)]; getCommandType ControllerProtocol.x = 26); split_plugnew/com/govee/h5086/adjust/SmartPlugProAdjustViewModel.java:1085 (control-screen caller)
- **`read_h5086_time_monitor`** (R): AA 1A (no selector)
  - split_plugnew/com/govee/h5086/ble/controller/TimeMonitorController.java:34-45 (Companion.b read), :53-68 (Companion.e reply); split_plugnew/com/govee/h5086/ble/controller/ReadInfoController.java:69

### `0x1b` — source-modeled

- **`build_h5089_nl_brightness`** (W): 33 1B [01][02 brightness][level 0..127]
  - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4NlSwitch.java:29-32,72-81 (int ctor; q [1, i()=2, Byte.parseByte(level)]); split_plugnew/com/govee/h5080/adjust/h5089/H5089OpManager.java:267-270 (H5089OpManager.B)
- **`build_h5089_nl_switch`** (W): 33 1B 01 01 [on u8] 00.. [bcc]
  - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4NlSwitch.java:34-37,43-45,66-71 (Controller4NlSwitch(boolean); getCommandType b0(); q()=[h()=1, m()=1, on]); split_plugnew/com/govee/h5080/adjust/h5089/H5089OpManager.java:502-504 (f0)
- **`build_nl71xx_brightness`** (W): 3A 1B 01 02 [level u8] 00.. [bcc]
  - base-classes12/com/govee/base_h71xx/light71xx/controller/BrightnessOnOffController.java:44-46,66-72 with BrightnessOnOff.Companion.a base-classes12/com/govee/base_h71xx/light71xx/model/BrightnessOnOff.java:32-36 (defaultSetBrightness true -> [1, i()=2, brightness]); base-classes13/com/govee/eco/executeAction/parser/BrightnessNightLightActionParser.java:33,41 (BrightnessOnOff(0, bri, true))
- **`build_nl71xx_switch`** (W): 3A 1B 01 01 [on u8] 00.. [bcc]
  - base-classes12/com/govee/base_h71xx/light71xx/controller/BrightnessOnOffController.java:44-46,66-72 with BrightnessOnOff.Companion.a base-classes12/com/govee/base_h71xx/light71xx/model/BrightnessOnOff.java:32-36 (defaultSetBrightness false -> [1, m()=1, onOff]); eco NightLightActionParser.java:185; base-classes13/com/govee/eco/executeAction/parser/FunctionSwitchLightActionParser.java:88-89 (x: ControllerSingle.Companion.d(0x1b, [1, 1, sw])); base-classes11/com/govee/base2light/kt/general_controller/ControllerSingle.java:44-53; selected for type 6 at base-classes13/com/govee/eco/executeAction/parser/ActionParserLibrary.java:509
- **`build_nl_mode_color`** (W): 3A 1B 05 0D [R][G][B][kelvin u16be] 00.. [bcc]
  - base-classes12/com/govee/base_h71xx/light71xx/controller/LightRgbController.java:27-30,40-46,66-75 (write when LightModeInfo non-null; proType v1()=0x3A; q()=LightModeInfo.Companion.a); base-classes12/com/govee/base_h71xx/light71xx/model/LightModeInfo.java:72-106 ([sub=5, (optional g byte), modeByte, data]); ModeColor.d base-classes12/com/govee/base_h71xx/light71xx/model/ModeColor.java:96-102
- **`build_nl_mode_default`** (W): 3A 1B 05 FC 00.. [bcc] (no mode data)
  - base-classes13/com/govee/eco/executeAction/parser/DefaultNightLightActionParser.java:87-88 (y: LightRgbController(LightModeInfo(DEFAULT))), sent over BLE at t :66-71 and IoT CmdMultiSync at v :76-78; dispatched at base-classes13/com/govee/eco/executeAction/parser/ActionParserLibrary.java:322; base-classes12/com/govee/base_h71xx/light71xx/model/LightModeInfo.java:92-95 (DEFAULT -> empty data); base-classes12/com/govee/base_h71xx/light71xx/model/LightSubMode.java:85-101 (getByteValue -> k() = T1)
- **`build_nl_mode_diy`** (W): 3A 1B 05 0A 00 00 00.. [bcc]
  - base-classes12/com/govee/base_h71xx/light71xx/model/ModeDiy.java:45-49 (Companion.b [0, 0]) via LightRgbController; split_plugnew/com/govee/h5080/adjust/h5089/H5089OpManager.java:204-215 (L, trailing frame after the multi-packet DIY upload), :374 (G)
- **`build_nl_mode_scene`** (W): 3A 1B 05 13 00 [scene_id u8] 00.. [bcc]
  - base-classes12/com/govee/base_h71xx/light71xx/controller/LightRgbController.java:44-46,66-75; ModeScene.c base-classes12/com/govee/base_h71xx/light71xx/model/ModeScene.java:70-72; split_plugnew/com/govee/h5080/adjust/h5089/H5089OpManager.java:386-398 (H), :340-384 (G)
- **`read_nl_mode`** (R): AA 1B 05 00.. [bcc]
  - base-classes12/com/govee/base_h71xx/light71xx/controller/LightRgbController.java:27-30,44-51 (null LightModeInfo -> read; proType O0() = 0xAA; p()=[LightSubCmd.Companion.a() = g() = V1 = 5]); issued at split_plugnew/com/govee/h5080/adjust/h5089/H5089OpManager.java:304
- **`read_nl_switch`** (R): AA 1B 01
  - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4NlSwitch.java:25-27,47-63 (read ctor; p [1]; parseValidBytes); split_plugnew/com/govee/h5080/adjust/h5089/H5089OpManager.java:303 (connect read list)

### `0x1f` — source-modeled

- **`build_plug_child_lock`** (W): 33 1F [02][on]
  - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4ChildLock.java:24-35,56-59 (write ctor; getCommandType 31; q [2, on]); split_plugnew/com/govee/h5080/adjust/h5089/H5089OpManager.java:498-499 (H5089OpManager.e0: control-screen caller)
- **`read_h5086_child_lock`** (R): AA 1F (no selector)
  - split_plugnew/com/govee/h5086/ble/controller/ChildLockController.java:26-43 (Companion.b -> ChildLockController(false, null); reply callback c :26-30); split_plugnew/com/govee/h5086/ble/controller/ReadInfoController.java:113
- **`read_h5089_child_lock`** (R): AA 1F 02
  - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4ChildLock.java:20-22,37-54 (read ctor; p [2]; parseValidBytes); split_plugnew/com/govee/h5080/adjust/h5089/H5089OpManager.java:302 (connect read list)
- **`read_plug_child_lock`** (R): AA 1F 02 00.. [bcc]
  - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4ChildLock.java:20-22,37-40 (read ctor super(false); p()=[2]); issued at split_plugnew/com/govee/h5080/adjust/h5089/H5089OpManager.java:302; split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailRepository.java:328-329 (necessaryMessages g(D(), [2]); added only when the supportLockAndLightIndicator gate passes)

### `0x20` — source-modeled

- **`build_breath_train_mode_20`** (W): 33 05 [20][phase 01 start / 02 end]
  - base-classes11/com/govee/base2light/pact/newdetail/content/breath/BreathTrainRunningAc.java:245-256 (J2: ControllerMode.e([32, 1]) after the A3 op 0x70 BreathTrainSettingInfo upload); base-classes11/com/govee/base2light/pact/newdetail/content/breath/BreathTrainRunningAc.java:404-409 (long-press end: ControllerMode.e([32, 2]))
- **`read_wifi_hard_version`** (R): AA 20 [00 x17] [bcc=8A]
  - base-classes10/com/govee/base2light/ble/controller/WifiHardVersionController.java:23 (WifiHardVersionController.getCommandType); base-classes11/com/govee/base2light/kt/general_controller/ControllerWifiHardVersion.java:34 (ControllerWifiHardVersion.getCommandType)

### `0x21` — source-modeled

- **`build_h5127_sync_triggers`** (W): header 33 21 [00][pkts=ceil(n/5) min 1][n][extra>0][extra]; data 33 21 [seq 1..pkts][5 x (kind u8, value u16be), 0xFF fill, 15 B @3-17][00 @18]; empty list 33 2
  - base-classes12/com/govee/base2light/synctriggers/h5127/ControllerSyncTriggers.java:24 (ControllerSyncTriggers.getCommandType)
- **`read_wifi_soft_version`** (R): AA 21 (no selector)
  - base-classes10/com/govee/base2light/ble/controller/WifiSoftVersionController.java:9-11,23-31 (default command 33; parseValidBytes BleUtil.getStrData); split_rgblight/com/govee/stringlightv2/adjust/v2/BleOpV3.java:126-127 (control-screen read)

### `0x22` — source-modeled

- **`read_wifi_dsp_version`** (R): AA 22 [00 x17] [bcc=88]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerWifiDspVersionInDeviceInfo.java:63 (ControllerWifiDspVersionInDeviceInfo.getCommandType)

### `0x23` — firmware-confirmed

- **`build_new_timer`** (W): 33 23 [group 0-3][enableAndType: bit7 enable, bit0 1=on/0=off][hour][minute][repeat]
  - base-classes10/com/govee/base2light/ble/controller/NewTimerV1Controller.java:118-122 (NewTimerV1Controller.q; ctor :19-23; getCommandType :78-80 = 35); base-classes11/com/govee/base2light/kt/general_controller/ControllerNewTimer.java:93-97 (ControllerNewTimer.<init>(byte,int,Timer) via Companion.b:40-43; command BleProtocol.music_code_taozui = 35, base-classes11/com/govee/base2light/pact/feast/BleProtocol.java:23)
- **`read_new_timer`** (R): AA 23 [group: 0-3, or 0xFF = all 4]
  - base-classes10/com/govee/base2light/ble/controller/NewTimerV1Controller.java:83-85 (NewTimerV1Controller.p; read ctor :14-17); base-classes11/com/govee/base2light/kt/general_controller/ControllerNewTimer.java:35-37 (ControllerNewTimer.Companion.a -> <init>(35, 255) :82-86)

### `0x24` — firmware-confirmed

- **`build_h682x_unbind`** (W): 33 24 01
  - base-classes10/com/govee/base2light/ble/H682xDeviceUnbindController.java:22-30 (getCommandType = 36 / q = [01]); base-classes10/com/govee/base2light/connect/ui/PairActivity.java:646 (v2), reached from d2 :326-328 for goodsType 279/280
- **`build_ic_points_h61d3`** (W): value = [count u16be][(x u16be, y u16be) x count]; frames: START [A3 00 01 n 24 value[0:14]], MIDDLE [A3 i 17 bytes], END [A3 FF rest], each 20 bytes with XOR a
  - split_h61d3/com/govee/h61d3/adjust/newdetail/VM4LightH61d3.java:1290-1303 (VM4LightH61d3.y5: MultiController(bytes, BleProtocol.k)); split_h61d3/com/govee/h61d3/ble/controller/MultiController.java:15-21 (makeSendBytesV1(BleProtocol.i = 0xA3, comm, bytes))
- **`build_set_light_start`** (W): 33 24 [max(index,1)][time u2be]
  - base-classes10/com/govee/base2light/ble/controller/SetLightStartController.java:12-16 (ctor: index = max(index, 1)), :26 (getCommandType = value_set_light_start = 36), :35-39 (q = [index, time u16be (getSignedBytesFor2, flag true)]); base-classes10/com/govee/base2light/connect/step/StepInitLightEffect.java:27; StepServiceBindSpecial.java:25 (callers; args ScanBleInfo f(), l())
- **`read_ic_point`** (R): AA 24, no selector, bytes 2..18 zero, XOR 0x8E.
  - split_h61d3/com/govee/h61d3/ble/controller/ReadIcPointController.java:30-34 (Companion.c: callback(reply byte 0 == 1)), :38-47 (Companion.b, null payload), :55-58 (super(false, payload) = read), :64-66 (getCommandType = BleProtocol.k = 36, split_h61d3/com/govee/h61d3/ble/BleProtocol.java:17); split_h61d3/com/govee/h61d3/adjust/newdetail/VM4LightH61d3$connectBleSuc$1.java:89-93,110-113 (VM4LightH61d3 connect batch -> EventRecognitionState(1, flag), base-classes11/com/govee/base2light/light/EventRecognitionState.java:28-31)
- **`read_preset_info`** (R): AA 24 (no selector)
  - split_hollowlamp/com/govee/h6057/origin/ble/PreSetInfoController.java:68 (PreSetInfoController.getCommandType); base-classes10/com/govee/base2light/ble/controller/AbsOnlyReadSingleController.java:5-7 (super(false) -> read)

### `0x25` — firmware-confirmed

- **`build_set_light_end`** (W): 33 25 01, zero-padded, XOR@19 = 0x17.
  - split_bulblightstringv1/com/govee/bulblightstringv1/bulblight/VM4Light.java:34,966-983 (VM4Light.i5; import kt AbsController); split_bulblightstringv1/com/govee/bulblightstringv1/bulblight/VM4Light$connectBleSuc$1.java:43-73 (VM4Light$connectBleSuc$1.j, i5 at :62)
- **`read_preset_color`** (R): AA 25 [page u8, 1-based]
  - split_hollowlamp/com/govee/h6057/origin/ble/PreSetColorController.java:17-20,62,66-68 (<init>(page,count)/getCommandType/p = [page]); base-classes10/com/govee/base2light/ble/controller/AbsOnlyReadSingleController.java:5-7

### `0x26` — source-modeled

- **`build_color_tem_type`** (W): 33 26 [type: 1|2]
  - split_bulblightstringv1/com/govee/bulblightv3/newdetail/controller/Controller4ColorTemTypeSetting.java:15-17,30-33,65-71 (j=38, k=1, l=2; AbsControllerNoEvent4Single write); caller split_bulblightstringv1/com/govee/bulblightv3/newdetail/SettingAcNewDetail.java:426
- **`build_take_photo_start`** (W): 33 26 01 64 FF FF 00
  - split_h61d3/com/govee/h61d3/ble/controller/TakePhotoController.java:27-34,43-48 ([1,100]+ColorUtils.n(-256)=FF FF 00, base-classes10/com/govee/base2kt/utils/ColorUtils.java:314-315; cmd BleProtocol.l=38); caller split_h61d3/com/govee/h61d3/adjust/recognition/ImageRecognitionActivity.java:429 (when i==1)
- **`build_take_photo_stop`** (W): 33 26 00
  - split_h61d3/com/govee/h61d3/ble/controller/TakePhotoController.java:37-39 (b=[0]); callers split_h61d3/com/govee/h61d3/adjust/newdetail/VM4LightH61d3.java:1283, adjust/recognition/CameraActivity.java:563, ImageRecognitionActivity.java:580
- **`read_color_tem_type`** (R): AA 26 (no selector)
  - split_bulblightstringv1/com/govee/bulblightv3/newdetail/controller/Controller4ColorTemTypeSetting.java:37-40 (b() read, null payload); caller split_bulblightstringv1/com/govee/bulblightv3/newdetail/NewDetailVm$connectBleSuc$1.java:146-154 (gated by Support.supportColorTemSetting)
- **`read_h6057_preset_scene_page`** (R): AA 26 [page]
  - split_hollowlamp/com/govee/h6057/origin/ble/PreSetSceneController.java:17-20,65-70 (AbsOnlyReadSingleController, p()=[page]; base-classes10/com/govee/base2light/ble/controller/AbsOnlyReadSingleController.java:5-7)
- **`read_preset_scene`** (R): AA 26 [page u8, 1-based]
  - split_hollowlamp/com/govee/h6057/origin/ble/PreSetSceneController.java:17-20,65,69-71 (<init>(page,count)/getCommandType/p = [page]); base-classes10/com/govee/base2light/ble/controller/AbsOnlyReadSingleController.java:5-7

### `0x27` — source-modeled

- **`build_h6057_preview_effect`** (W): 33 27 [effect_type u8: 0 sleep, 1 wake-up][brightness u8][voice_code u8, 0 when voice is off][voice_volume u8][default_light u8][R][G][B][laser u8 1/0] zero-pad
  - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:1094-1121 (H6057DetailVM.onEventPreviewEffect -> ControllerSingle.Companion.c(BleProtocol.controller_type_preview_effect = 39, split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:14)); base-classes11/com/govee/base2light/kt/general_controller/ControllerSingle.java:37-41 (ControllerSingle.Companion.c, write, proType 0x33)
- **`build_power_warning`** (W): 33 27 [open 1/0/FF=unchanged][watts u2be | FF FF]
  - split_plugnew/com/govee/h5086/ble/controller/PowerWarningController.java:49-58 (Companion.d); split_plugnew/com/govee/h5086/adjust/SmartPlugProAdjustViewModel.java:1220-1227 (g5, caller)
- **`build_wash_remind`** (W): 3A|33 27 [2 if reset else open 1/0][washHour u16be]
  - base-classes12/com/govee/base_h71xx/sku_base/ble/controller/WashRemindController.java:27-36,42-51,62-76 (<init>/getCommandType/getProType/q); base-classes12/com/govee/base_h71xx/sku_base/BleProtocolConstants.java:93,1257-1259 (e = DiyConstant.W, v1())
- **`read_op27`** (R): AA 27 (no selector)
  - split_plugnew/com/govee/h5086/ble/controller/PowerWarningController.java:37-46,75-77 (Companion.b -> PowerWarningController(false,null); getCommandType = ControllerProtocol.A = 39); split_plugnew/com/govee/h5086/ble/controller/ReadInfoController.java:86 (issued at connect)
- **`read_power_warning`** (R): AA 27 (no selector)
  - split_plugnew/com/govee/h5086/ble/controller/PowerWarningController.java:37-46 (Companion.b), :75-77 getCommandType=ControllerProtocol.A; split_plugnew/com/govee/h5086/ble/controller/ReadInfoController.java:86 (caller)

### `0x28` — source-modeled

- **`build_delete_preset_scene`** (W): 33 28 [scene_code u16le]
  - split_hollowlamp/com/govee/h6057/origin/ble/DeletePreSetSceneController.java:9-11,21-27 (<init>/getCommandType/q = ctor bytes); split_hollowlamp/com/govee/h6057/view/preset/PresetEditActivity.java:449-457 (L1 builds [code low byte, code high byte], code = scenesItem.d)
- **`build_h5089_sleep`** (W): 33 28 [enable][start_bri][close_time][cur_time][default_light][R][G][B][kelvin u16le] 00.. [bcc]
  - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4NlSleep.java:53-57,63-65,79-86 (write ctor; getCommandType 40; q()); base base-classes10/com/govee/base2light/ble/controller/SleepController.java:17-23; caller split_plugnew/com/govee/h5080/adjust/h5089/Ui4H5089.java:829
- **`build_power_warning_v2`** (W): 33 28 [kind 1=alarm,2=cut-off][open 1/0][watts u16be | FFFF=null]
  - split_plugnew/com/govee/h5086/ble/controller/PowerWarningControllerV2.java:24-33,44-46 (Companion.a kind 2 / Companion.b kind 1, getCommandType=40); split_plugnew/com/govee/h5086/adjust/SmartPlugProAdjustViewModel.java:1153-1160 (b5, kind 2, log 'set 0x28 power-off power')
- **`read_h5089_sleep`** (R): AA 28 (no selector)
  - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4NlSleep.java:48-51 (no-arg ctor); base-classes10/com/govee/base2light/ble/controller/SleepController.java:13-15 (super(false) -> 0xAA)

### `0x29` — source-modeled

- **`build_h5089_wakeup`** (W): 33 29 [enable][endBri][hour 0-23][min 0-59][repeat][wakeTime][defaultLight][R][G][B][kelvin u16le, 0 unless CCT]
  - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4NlWakeUp.java:64-68,74-76,89-96 (<init>/getCommandType=LIGHT_TIMER_4_WAKE_UP=41/q); base-classes10/com/govee/base2light/ble/controller/WakeUpController.java:19-27,45-51 (write ctor, u/v clamps)
- **`build_sort_preset_scenes`** (W): 33 29 [N][code0 u16le]..[codeN-1 u16le], N<=8
  - split_hollowlamp/com/govee/h6057/origin/ble/SortPreSetSceneController.java:9-11,21-27 (<init>/getCommandType/q = ctor bytes); split_hollowlamp/com/govee/h6057/view/preset/PresetEditActivity.java:492-513 (O1 builds [size, code lo, code hi, ...])
- **`read_h5089_wakeup`** (R): AA 29 (no selector)
  - split_plugnew/com/govee/h5080/ble/controller/h5089/Controller4NlWakeUp.java:59-62 (no-arg ctor); base-classes10/com/govee/base2light/ble/controller/WakeUpController.java:15-17 (super(false) -> 0xAA)

### `0x30` — firmware-confirmed

- **`build_h6057_light_switch`** (W): 33 30 [on u8: 1 on / 0 off]
  - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:950-967 (H6057DetailVM.h5: ControllerSingle.Companion.c(command_light_switch, [1 when the light is off, else 0]) :956, applyController(Compose4DefWrite) :962); split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:7 (command_light_switch = 48)
- **`build_light_direction`** (W): 33 30 [direction index] 00 x16 [XOR@19]
  - split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:42-45 (Companion.c = Controller4ExtBytes.d([MSG_TYPE_READ_LIGHT_DIRECTION = 48, b])); base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:84-100 (d: write flag true)
- **`build_light_direction_exit_calibration`** (W): [33 30 [value]] + [33 44 02 00]
  - base-classes10/com/govee/base2light/ble/controller/compose/DefWriteComposeController4Single.java:137-143 (Companion.g = [DirectionController.b(b), CalibrationController.a()]); :190 makeSetDirectionAndExitCalibrationModeController; base-classes10/com/govee/base2light/ble/controller/compose/CalibrationController.java:30-31,41-42,58-59 (a()=[2,0], write, cmd 68)
- **`build_light_switch_h6057`** (W): 33 30 [01 on | 00 off] 00 x16 [XOR@19]
  - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:950-962 (h5: ControllerSingle.Companion.c(command_light_switch = 48, [1 when the light is off, else 0])); split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:7 (command_light_switch = 48)
- **`build_op30_byte`** (W): 33 30 [value u8]
  - base-classes10/com/govee/base2light/ble/controller/compose/DirectionController.java:34-35,60-61 (DirectionController.Companion.b, getCommandType=48); DefWriteComposeController4Single.java:147-148 (makeSetDirectionController); split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:43-44 (Companion.c = Controller4ExtBytes.d([48,b])); callers split_h6047/com/govee/h6047/newdetail/h6038/H6038CalibrationAc.java:298, VM4H6038.java:812
- **`build_zone_power`** (W): 33 30 [zone][01 on | 00 off] 00 x15 [XOR@19]
  - split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6.java:1373-1376 (y5: Controller4ExtBytes.f(48, [zone: 0 when its first flag is set, else 1; state 1|0])); split_h60ax/com/govee/pact_h60a0/adjust/h60a6/DelegateHeaderH60A6.java:442,470 (y5(false, ..) zone 1, y5(true, ..) zone 0)
- **`read_light_direction`** (R): AA 30 00 x17 [XOR@19] (never sent in 7.7.02; documented only)
  - split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:34-40 (Companion.b, Controller4ExtBytes.b read; no caller); base-classes10/com/govee/base2light/ble/controller/compose/DirectionController.java:26-30,60-61 (only referenced from the base DefWriteComposeController4Single, which nothing imports)
- **`read_op30`** (R): AA 30 (no selector)
  - base-classes10/com/govee/base2light/ble/controller/compose/DirectionController.java:28-29 (Companion.a, read; no caller in decomp); split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:35-39 (Companion.b = Controller4ExtBytes.b([48]); no caller)

### `0x31` — source-modeled

- **`build_cube_shape_layout`** (W): 0xA3 V1 framing with comm 0x31 (p() = 1; START [A3 00 01 total 31 value[0:14]], MIDDLE [A3 i 17B], END [A3 FF <=17B], XOR@19). value = [n u8] + n x [type u8]
  - split_h6061/com/govee/cubelight/ble/MultiDeviceController.java:17-53 (MultiDeviceController.<init>), :59-65 (g, getCommandType = 49), :71-73 (p); base-classes10/com/govee/base2light/ble/BleUtil.java:234-244 (BleUtil.getSignedBytesFor2)
- **`build_cube_shape_layout_h606a`** (W): 0xA3 V1 framing with comm 0x31 (p() = 1). BLE value g() returns field i = [angle s16le][n u8] + n x [x s16le][y s16le], with no per-shape type or angle. angle = list[0
  - split_h6061/com/govee/cubelight/ble/MultiDeviceControllerH606A.java:20-47 (MultiDeviceControllerH606A.<init>(list, offset)), :49-76 (<init>(list, offset, list2)), :152-158 (g, getCommandType = 49), :164-166 (p); base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783 (makeSendBytesV1/V2), :833-843 (makeWriteMultipleBytes)
- **`build_fanlight_fan_gear`** (W): 33 31 01 [gear u8]
  - base-classes13/com/govee/eco/executeAction/parser/FanMorningParamActionParser.java:110-112 (exeOpen 1 -> c(49, [1, gear])); base-classes13/com/govee/eco/executeAction/parser/FanParamActionParser.java:170-179 (first frame c(49, [1, gear]))
- **`build_fanlight_fan_param`** (W): list of frames: 33 31 01 [gear]; 33 31 02 [order]; opt 33 31 04 [shake]; opt 33 31 03 01 00 [mode]
  - base-classes13/com/govee/eco/executeAction/parser/FanParamActionParser.java:170-179 (y), :46-49 (Companion.a: goodsType 368 && versionSoft.compareTo("1.00.11") >= 0), :52-70 (default gear 6; IoT sends all frames in one CmdPtReal, BLE BlueMsg TYPE_FAN_PARAM); base-classes13/com/govee/eco/executeAction/parser/ActionParserLibrary.java:339-340,490-493
- **`build_fanlight_fan_switch`** (W): 33 31 00 [open 0|1]
  - base-classes13/com/govee/eco/executeAction/parser/FanSwitchActionParser.java:45-47 (y: ControllerSingle.c(49, [0, open])), :37 (default open = 1), :41-55 (same bytes via IoT CmdPtReal and BLE BlueMsg); base-classes13/com/govee/eco/executeAction/parser/FanMorningParamActionParser.java:110-112 (exeOpen not 1 -> c(49, [0, 0]))
- **`build_h6057_play_voice`** (W): 33 31 [music_code u8]
  - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:675-705 (G5 whiteVoiceSwitchOp; :694 ControllerSingle.c(49 with [code] to play, or 50 with an empty payload to pause)), :591-600 (voiceOp -> G5); split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:12-13 (pause 50, play 49)
- **`build_h6067_shape_layout`** (W): 0xA3 V1 framing with comm 0x31 (p() = 1; START [A3 00 01 total 31 value[0:14]], MIDDLE [A3 i 17B], END [A3 FF <=17B], XOR@19). value = [n u8] + n x [type u8]
  - split_h6061/com/govee/h6067/ble/MultiDeviceController.java:18-65 (MultiDeviceController.<init>(list, goodsType)), :71-77 (g, getCommandType = 49), :83-85 (p); base-classes10/com/govee/base2light/ble/BleUtil.java:234-244 (BleUtil.getSignedBytesFor2)
- **`build_op31_byte`** (W): 33 31 [value u8]
  - split_h6062/com/govee/striplight/ble/LightPosController.java:16-19,21-23,33-35,42-44 (write ctor, changePos, cmd SINGLE_LIGHT_POS=49, q()=[f]); callers split_h6062/com/govee/striplight/add/v2/LightCalibrationAc4Add.java:177, split_h6062/com/govee/striplight/newdetail/SettingAc.java:419 (Controller4ExtBytes.d([49,i])); split_rgbiclight/com/govee/h7090/ble/LightPosController.java:12-18; callers split_rgbiclight/com/govee/h7090/add/LightCalibrationAc4Add.java:190, split_rgbiclight/com/govee/h7090/adjust/newDetail/SettingActivity.java:322 (Controller4ExtBytes.d([49,i]))
- **`build_strip_light_pos`** (W): 33 31 [pos 0|1]
  - split_h6062/com/govee/striplight/newdetail/SettingAc.java:401-424 (x4 tryChangePos; :418 toggle, :419 Controller4ExtBytes.d([49, pos])), :383-386,304-306 (u4: goodsType == 80); split_rgbiclight/com/govee/h7090/adjust/newDetail/SettingActivity.java:318-322 (x3 write), :440-441 (item gated by Support.supportChangePos)
- **`read_op31`** (R): AA 31 (no selector)
  - split_h6062/com/govee/striplight/ble/LightPosController.java:12-14 (read ctor); caller split_h6062/com/govee/striplight/add/v2/PairAcV2.java:127; base-classes11/com/govee/base2light/kt/general_controller/ControllerLightPos.java:31-38,52-53,77-78 (makeController4Read, default args: cmd 49, read); callers split_h6062/com/govee/striplight/newdetail/ControllerStrategyConfig.java:623 (makeH610A, goodsType 80 :432-437), split_rgbiclight/com/govee/h7090/adjust/newDetail/ControllerStrategyConfig.java:292
- **`read_strip_light_pos`** (R): AA 31 (no selector)
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerLightPos.java:31-39 (makeController4Read, default args: mask bit 1 -> cmd 49), :52-64 (a: read (false, cmd), listener b0); split_h6062/com/govee/striplight/newdetail/ControllerStrategyConfig.java:623-627 (makeH610A), :306-312 (n0 -> Info4BleIot.K)

### `0x32` — source-modeled

- **`build_h6057_pause_voice`** (W): 33 32 [no payload, bytes 2-18 zero] [XOR@19 = 0x01]
  - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:694 (H6057DetailVM.G5 :675-706, !play branch: ControllerSingle.Companion.c(controller_type_pause_voice) with an empty payload); split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:12-13 (controller_type_pause_voice = 50, controller_type_play_voice = 49)
- **`build_pause_voice_h6057`** (W): 33 32 (empty payload)
  - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:694 (ControllerSingle.Companion.c(play_voice = 49 with [musicCode], or pause_voice = 50 with an empty payload); constants split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:12-13); base-classes11/com/govee/base2light/kt/general_controller/ControllerSingle.java:38-41,50-57
- **`build_pause_voice_h6057_v1`** (W): 33 32 [code]
  - split_hollowlamp/com/govee/h6057/origin/ble/Controller4PauseVoice.java:8 (AbsOnlyWriteSingleController), :29-31 (ctor), :37-39 (cmd 50), :41-43 (q = [f])
- **`read_check_camera`** (R): AA 32 (no selector)
  - base-classes12/com/govee/base2light/videomode/newdetail/funtion/CheckCameraVmInterface.java:57-58 (getReadCameraStatusCamera = Controller4ExtBytes.c([50])); readCameraStatus :71-83; AbsVideoNewDetailVm.java:426-427,521-522; caller base-classes12/com/govee/base2light/videomode/newdetail/AbsVideoMode.java:331; base-classes10/com/govee/base2light/ble/controller/CheckCameraController.java:11-18,36-37,51-52 (super(false), cmd 50, q()=[]; no caller in decomp)

### `0x33` — source-modeled

- **`build_lr_switch`** (W): 33 33 [op: 0x00 none | 0x01 first | 0x10 second | 0x11 both]
  - split_h6062/com/govee/striplight/ble/SwitchLRController.java:18-44,63-65 (SwitchLRController(int).q; makeSwitchSet/makeNewOp); caller split_h6062/com/govee/striplight/add/v2/LightCalibrationAc4Add.java:64; split_rgbiclight/com/govee/h7090/ble/SwitchLRController.java:46-48 (write-only copy); caller split_rgbiclight/com/govee/h7090/add/LightCalibrationAc4Add.java:243
- **`build_volume`** (W): 33 33 [volume u8]
  - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:714-719 (J5, ControllerSingle.Companion.c(BleProtocol.command_volume = 51, [volume]); constant split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:11)
- **`build_volume_h6057`** (W): 33 33 [volume]
  - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057DetailVM.java:714-719 (J5: ControllerSingle.c(command_volume, [volume])); split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:11 (command_volume = 51)
- **`read_lr_switch`** (R): AA 33 (no selector)
  - split_h6062/com/govee/striplight/ble/SwitchLRController.java:14-16 (read ctor super(false)); caller split_h6062/com/govee/striplight/add/v2/PairAcV2.java:127; base-classes11/com/govee/base2light/kt/general_controller/ControllerSwitchLR.java:39-51 (Companion.a); callers split_h6062/com/govee/striplight/newdetail/ControllerStrategyConfig.java:640 and split_rgbiclight/com/govee/h7090/adjust/newDetail/ControllerStrategyConfig.java:314

### `0x34` — source-modeled

- **`build_swap_light`** (W): 33 34 (empty payload)
  - split_h6047/com/govee/h6047/ble/controller/SwapLightController.java:13-19 (getCommandType = BleProtocol.value_swap_light_pos = 52, split_h6047/com/govee/h6047/ble/BleProtocol.java:39; q() = []); split_h6047/com/govee/h6047/add/LightPosAc.java:193 (setup, Ble.s.q0 with a SwapLightController)
- **`read_h6057_detail`** (R): AA 34 (no selector)
  - split_hollowlamp/com/govee/h6057/origin/newDetail/controller/Controllers.java:60-70 (Controllers.f -> Controller4ExtBytes.Companion.b([52])); base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:48-64 (Companion.b, write=false -> 0xAA)

### `0x35` — source-modeled

- **`build_without_interrupt`** (W): 33 35 [on]
  - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:787,789-793 (K4 case z0 TAG_NO_DISTURBANCE: ControllerSingle.c(command_no_disturb, [on 1|0])); split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:9 (command_no_disturb = 53)

### `0x36` — source-modeled

- **`build_bar_switch_index`** (W): 33 36 [index/zone u8][on 1/0]
  - split_h6047/com/govee/h6047/ble/controller/ComposeLightControllerV1.java:32-34 (q); callers split_h6047/com/govee/h6047/add/LightPosAc.java:202,214 and split_h6047/com/govee/h6047/newdetail/h6048/H6048CalibrationAc.java:255,260; split_h6047/com/govee/h6047/detail/NewDetailVm.java:696,716 (D5, H6043: [54, index, b]) and :301 (h5, non-H6047: [54,0,D],[54,1,F])
- **`build_bar_switch_pair`** (W): 33 36 [left 1/0][right 1/0]
  - split_h6047/com/govee/h6047/ble/controller/ComposeLightController.java:34-36 (q, write-only); callers split_h6047/com/govee/h6047/add/LightPosAc.java:204,216,362; split_h6047/com/govee/h6047/detail/NewDetailVm.java:293-298 (h5, isGoodsTypeH6047: Controller4ExtBytes.e([54, D, F]))
- **`build_child_switch`** (W): 33 36 [zone u8: 0 = main light (isMain), 1 = background/secondary light; value is !isMain] [on u8: 1 on / 0 off] 00 x15 [BCC]
  - split_h60ax/com/govee/pact_h60a0/adjust/h60a0/VM4LightH60A0.java:1023-1033 (VM4LightH60A0.k5: Controller4ExtBytes.Companion.f(0x36, [0 when its first flag is set, else 1; on 1|0])); split_h60ax/com/govee/pact_h60a0/adjust/h60a0/DelegateHeaderH60A0.java:448-460 (DelegateHeaderH60A0.m0: main button -> k5(true, !X4().s()))

### `0x37` — source-modeled

- **`build_prompt_tone`** (W): 33 37 [on]
  - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:787,802-806 (K4 case E0 TAG_HINT_VOICE: ControllerSingle.c(command_hint_voice, [on 1|0])); split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:5 (command_hint_voice = 55)

### `0x38` — source-modeled

- **`build_guide_light_h6057`** (W): 33 38 [on]
  - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:787,828-832 (K4 case B0 TAG_INDICATOR_LIGHT: ControllerSingle.c(command_indicator_light, [on 1|0])); split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:6 (command_indicator_light = 56)
- **`build_init_light`** (W): 33 38 [value]; H6057 sends 1|0
  - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057SettingActivity.java:787,830-832 (live: ControllerSingle.c(command_indicator_light, [on])); split_hollowlamp/com/govee/h6057/origin/ble/BleProtocol.java:6 (command_indicator_light = 56)

### `0x39` — source-modeled

- **`build_check_direction`** (W): 33 39 [direction u8]
  - base-classes10/com/govee/base2light/ble/controller/CheckDirectionFinishController.java:14-17,25-26,36-37 (write ctor, cmd 57, q()=[f]); caller base-classes4/com/govee/splicing/splicingUi/DeviceSplicingViewModel.java:267 (changeDirection); base-classes11/com/govee/base2light/kt/general_controller/ControllerDirection.java:31-32,48-49 (Companion.b; no caller in decomp)
- **`read_check_direction`** (R): AA 39 (no selector)
  - base-classes10/com/govee/base2light/ble/controller/CheckDirectionFinishController.java:10-12 (read ctor); caller base-classes4/com/govee/splicing/splicingUi/DeviceSplicingViewModel.java:393 (readDeviceDirection); base-classes11/com/govee/base2light/kt/general_controller/ControllerDirection.java:26-27 (Companion.a; no caller)

### `0x40` — source-modeled

- **`build_splicing_config`** (W): 33 40 01 [p1][layout][MAC_le x N] (payload <= 17 bytes)
  - base-classes4/com/govee/splicing/controller/ControllerChangeSplicing.java:94-108 (Companion.d: [1, p1, a(p2, p1), reversed MACs]); base-classes4/com/govee/splicing/controller/ControllerChangeSplicing.java:71-81 (Companion.a: layout byte 0x60/0x6C/0x90/0x9C/0xD8)
- **`build_splicing_mode`** (W): 33 40 02 01 R G B (enter) | 33 40 02 00 (exit)
  - base-classes4/com/govee/splicing/controller/SplicingController.java:57-69 (getCommandType = g / q: [2,0] when colour is -1, else [2,1,R,G,B] via ColorUtils.n); base-classes4/com/govee/splicing/splicingUi/DeviceSplicingViewModel.java:418-426 (Z: enter with DeviceInfo colour), :438 (a0: exit via makeSplicingController)
- **`build_splicing_release`** (W): 33 40 01 FF
  - base-classes10/com/govee/base2light/ble/H70B1DeviceUnbindController.java:22-30 (getCommandType = 64 / q = [01, ff]); base-classes4/com/govee/splicing/controller/ControllerReleaseSplicing.java:52-59 (getCommandType = cmd, default KmpDiyConstant.d0 = 64 :24-28 / q = [01, ff]; no caller)
- **`build_splicing_sub_device_mac`** (W): 33 40 05 [b][MAC_le]
  - base-classes4/com/govee/splicing/controller/ControllerChangeSplicing.java:111-116 (Companion.e: mergeByteArray([5, b], reversed MAC)); base-classes4/com/govee/splicing/controller/ControllerChangeSplicing.java:64-69 (makeSubDeviceBtMacController: cmd KmpDiyConstant.d0 = 64)
- **`read_status_with_ic`** (R): AC 03 [n][types...]; TLV 0x40 = IC count in the 0xAC reply burst
  - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/Compose4BaseInfoSingleRead.java:166-179 (Compose4BaseInfoSingleRead.c: extBytes [len, *types]); base-classes11/com/govee/base2light/kt/general_controller/ControllerSingleRead.java:80-95 (ControllerSingleRead: command 3)

### `0x41` — firmware-confirmed

- **`build_music_effect_a3_v2`** (W): value [head u8...][n][n x (R,G,B)][params u8...][pad zero bytes], framed as A3 V2 comm [0x41, music_code] (START value[0:13], MIDDLE 17B, END <=17B)
  - base-classes10/com/govee/base2light/ble/music/AbsNewMusicEffect.java:452 (abstract toBytes), :252-261 (RgbMusicOnlyColor codes); base-classes10/com/govee/base2light/ble/music/RgbicMusicFenLi.java:84-99 (toBytes; siblings listed in notes)
- **`build_on_off_memory`** (W): 33 41 02 [mode u8] 00 x15 [XOR]
  - OnOffMemoryController(int type, boolean write): h = 65, q() = [2, type] when g != -1 base-classes10/com/govee/base2light/ble/controller/OnOffMemoryController.java:18-22,75-78; callers split_rgbiclight/com/govee/dreamcolorlightv1/adjust/v2/UiV3.java:2726 (BLE), :2728 (IoT ptReal)
- **`build_on_off_memory_legacy`** (W): 33 41 [on]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerOnOffMemory.java:79-81 (Companion.h: mode -1 -> [on]); split_bulblightstringv1/com/govee/bulblightstringv2/newAdjust/h7031/H7031NewDetailVM.java:380 (V4 onSwapOnOffMemoryEvent: h(-1, on), gated on Support.isSupportOnOffMemory)
- **`read_on_off_memory`** (R): AA 41 02 00 x16 [XOR]
  - OnOffMemoryController(0, false).p() = [2] because g != -1 base-classes10/com/govee/base2light/ble/controller/OnOffMemoryController.java:18-22,64-66; caller split_rgbiclight/com/govee/dreamcolorlightv1/adjust/v2/BleOpV3.java:367-371 (gated by Support.supportPowerOffMemory)
- **`read_on_off_memory_or_section_count`** (R): AA 41 (no selector)
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerOnOffMemory.java:49-58 (Companion.c: ControllerOnOffMemory(false, null)); split_rgbiclight/com/govee/rgbiclight/ControllerStrategyConfig.java:993; split_h61d3/com/govee/h61d3/adjustNew/VM4Light.java:576; split_h61d3/com/govee/h61d3/adjust/newdetail/VM4LightH61d3$connectBleSuc$1.java:84; split_hollowlamp/com/govee/straightfloorlamp/adjust/VM4Light$connectBleSuc$1.java:145; split_bulblightstringv1/com/govee/bulblightstringv2/newAdjust/h7031/ControllerStrategyConfig.java:306,389; split_rgbiclight/com/govee/h7090/adjust/newDetail/VM4Light.java:525; split_h6061/com/govee/cubelight/newdetails/CubeVM4Light$connectBleSuc$2.java:143

### `0x42` — firmware-confirmed

- **`build_head_calibration`** (W): 33 42 [op][step?]: 01 enter, 00 exit, 02 + 1|2 step, FF commit
  - split_h60ax/com/govee/pact_h60a0/add/CalibrationAcH60A6.java:258,306 (s2/v2: Controller4ExtBytes.f(0x42, payload); payloads [01] :392, [00] :178/:186/:426, [02 02] :207, [02 01] :212, [ff] then [00] :108-109); split_h60ax/com/govee/pact_h60a0/adjust/h60a6/VM4LightH60A6.java:805-817 (b5: [FF] then [00])
- **`build_head_calibration_enter`** (W): 33 42 [op=0x01 enter]
  - split_h60ax/com/govee/pact_h60a0/add/CalibrationAcH60A6.java:392 (CalibrationAcH60A6.K1 -> v2([01])); split_h60ax/com/govee/pact_h60a0/add/CalibrationAcH60A6.java:303-329 (CalibrationAcH60A6.v2: add flow Ble.s Controller4ExtBytes.Companion.f(0x42, payload) :306; detail flow -> VM4LightH60A6.H5 :320)
- **`build_head_calibration_exit`** (W): 33 42 [op=0x00 exit, drop uncommitted steps]
  - split_h60ax/com/govee/pact_h60a0/add/CalibrationAcH60A6.java:177-181 (CalibrationAcH60A6.j2 back -> v2([00])); split_h60ax/com/govee/pact_h60a0/add/CalibrationAcH60A6.java:183-190 (CalibrationAcH60A6.k2 skip -> v2([00]))
- **`build_head_calibration_finish`** (W): frame1 33 42 [0xFF commit]; frame2 33 42 [0x00 exit] (one Compose4DefWrite, order FF then 00)
  - split_h60ax/com/govee/pact_h60a0/add/CalibrationAcH60A6.java:100-115 (CalibrationAcH60A6.f2 add flow: a list [f(0x42, [ff]), f(0x42, [00])] -> Compose4DefWrite.Companion.c -> Ble.s.h0); split_h60ax/com/govee/pact_h60a0/add/CalibrationAcH60A6.java:84-92 (f2 detail flow -> VM4LightH60A6.b5 :87)
- **`build_head_calibration_step`** (W): 33 42 02 [dir: 0x01=+1 right arrow ivYou | 0x02=-1 left arrow ivZuo]
  - split_h60ax/com/govee/pact_h60a0/add/CalibrationAcH60A6.java:161-174 (CalibrationAcH60A6.i2: ivZuo -> m2, ivYou -> n2); split_h60ax/com/govee/pact_h60a0/add/CalibrationAcH60A6.java:206-213 (m2 s2([02 02]); n2 s2([02 01]))
- **`build_refresh_ic`** (W): 33 42 (no payload)
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerRefreshIc.java:41-47 (synthetic ctor mask 3 -> write, payload null; getCommandType = 66; no caller); base-classes10/com/govee/base2light/ble/controller/RefreshIcController.java:11-18 (getCommandType = 66 / q = byte[0])
- **`build_splicing_cancel_bt`** (W): 33 42 0B 00 x16 [XOR]
  - CancelBTController: super(true), cmd f0 = 66, q() = [11] base-classes4/com/govee/splicing/controller/CancelBTController.java:16-25,41-44; DeviceSplicingViewModel exit when the main SPP MAC is known; with an empty MAC it sends SplicingController instead base-classes4/com/govee/splicing/splicingUi/DeviceSplicingViewModel.java:436-442
- **`build_spp_mac_write`** (W): 33 42 0C [mac[5] mac[4] mac[3] mac[2] mac[1] mac[0]] 00 x10 [XOR]
  - SPPMacController(String btMac): super(!isEmpty) (write when non-empty); q() = [0x0C] + BleUtils.d(mac, true) base-classes4/com/govee/splicing/controller/SPPMacController.java:32-36,68-71; BleUtils.c/d split on ':', parse hex, reverse base-classes10/com/govee/base2kt/utils/BleUtils.java:554-568,592-608
- **`read_spp_mac`** (R): AA 42 06 00 x16 [XOR]
  - SPPMacController() -> "" -> super(false) (read); p() = [6] base-classes4/com/govee/splicing/controller/SPPMacController.java:26-40,45-52; KmpDiyConstant.f0 = 66 base-classes4/com/govee/shared/protocol/oldgraffiti/KmpDiyConstant.java:78

### `0x43` — source-modeled

- **`build_check_ic_amount`** (W): 33 43 [type][ic u2be]
  - base-classes10/com/govee/base2light/ble/controller/CheckICAmountController.java:26-34 (getCommandType = 67 / q = [type, hi, lo]; no caller); split_rgbiclight/com/govee/rgbiclight/Compose4LightCali.java:16-21 (anonymous kt AbsController [type, hi, lo], getCommandType = 67)
- **`build_check_ic_h6640`** (W): 33 43 [check_type u8] 00.. [BCC] when check_type != 4; 33 43 04 [icPos u16be] 00.. [BCC] when check_type == 4. No channel/sub byte.
  - split_h61d3/com/govee/h61d3/adjustNew/setting/h6640/CheckIcController.java:15-18,29-35,46-48 (h6640 CheckIcController.Companion.a/getCommandType: k=0, l=1, n=3, m=4; cmd BleProtocol.t = 67); split_h61d3/com/govee/h61d3/ble/BleProtocol.java:29,35-37 (BleProtocol.t = 67, never reassigned)
- **`read_sub_device_version`** (R): AA 43 (no selector)
  - base-classes4/com/govee/splicing/controller/ControllerReadSubDeviceVersion.java:112-115 (ctor super(false, null)), :125 (getCommandType, default KmpDiyConstant.g0 = 67 :74-81)

### `0x44` — source-modeled

- **`build_calibration`** (W): 33 44 [1|0]
  - split_h6047/com/govee/h6047/ble/controller/CalibrationController.java:36,46-48 (getCommandType = value_calibration = 68 / q = [f] as 1|0); split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:29-32 (a: Controller4ExtBytes.d([68, 0])), :47-50 (d: [68, 1])
- **`build_calibration_done_h6048`** (W): 33 44 (empty); write-ack status read at frame[3] (p() = 1)
  - split_h6047/com/govee/h6047/newdetail/control/CalibrationController4H6048.java:22-25 (Companion.a -> (true, byte[0])), :40-42 (cmd value_calibration = 68, split_h6047/com/govee/h6047/ble/BleProtocol.java:18), :44-46 (p() = 1); base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:229 (ack at valid-byte index p())
- **`build_calibration_mode`** (W): 33 44 02 [1 into | 0 exit]
  - base-classes10/com/govee/base2light/ble/controller/compose/CalibrationController.java:30-38 (Companion.a = [2, 0] exit / b = [2, 1] into), :58 (getCommandType = 68); base-classes10/com/govee/base2light/ble/controller/compose/DefWriteComposeController4Single.java:97-107,136-143 (makeCalibrationInto / Exit, makeSetDirectionAndExitCalibrationMode; no callers)
- **`build_calibration_onoff`** (W): 33 44 [on] (1 = enter/start calibration, 0 = exit)
  - split_h6047/com/govee/h6047/ble/controller/CalibrationController.java:27-30 (super(true)), :36-38 (cmd value_calibration), :46-48 (q = [f]); split_h6047/com/govee/h6047/newdetail/h6038/controller/CalibrationController.java:29-31 (a() = Controller4ExtBytes.d([68, 0])), :47-49 (d() = d([68, 1]))
- **`build_cube_calibration`** (W): 33 44 [start][shapeType]
  - split_h6061/com/govee/cubelight/ble/CalibrationController.java:21,25-27 (getCommandType = 68 / q = [f, g]); split_h6061/com/govee/cubelight/add/CalibrationAc.java:383 (start = l1(i), cumulative Shape.o(type) offset :431-438)
- **`build_h6065_calibration`** (W): 33 44 [head = 15][idx...]
  - split_h6061/com/govee/h6067/ble/CalibrationControllerH6065.java:20,24-37 (getCommandType = 68 / q = [g, f...]); split_h6061/com/govee/h6067/add/CalibrationAc.java:578; split_h6061/com/govee/h6067/add/CalibrationDialog.java:328 (callers, head 15)
- **`build_h6067_calibration`** (W): 33 44 [index][00]
  - split_h6061/com/govee/h6067/ble/CalibrationController.java:18,22-24 (getCommandType = 68 / q = [f, g]; g never assigned); split_h6061/com/govee/h6067/add/CalibrationAc.java:568; split_h6061/com/govee/h6067/add/CalibrationDialog.java:322 (callers)
- **`read_splicing_status`** (R): AA 44 (no selector)
  - base-classes4/com/govee/splicing/controller/SubDeviceReadSplicingStatusController.java:77-79 (ctor super(false, null)), :90 (getCommandType, default KmpDiyConstant.h0 = 68 :56-60)

### `0x45` — source-modeled

- **`build_cube_install`** (W): 33 45 [install_flag u8: 00 = InstallController(true) entering install view | FF = InstallController(false) leaving]; write-only, ack frame[2]==0
  - split_h6061/com/govee/cubelight/ble/InstallController.java:9-11,17-23 (ctor stores the flag in f, getCommandType=69, q [00 when f is set, else ff]); split_h6061/com/govee/h6067/ble/InstallController.java:9-28 (identical)
- **`build_install_mode`** (W): 33 45 [00 entering install view | FF leaving]
  - split_h6061/com/govee/cubelight/ble/InstallController.java:9-23 (getCommandType 69; q [00 when f is set, else ff]); split_h6061/com/govee/h6067/ble/InstallController.java:9-23 (same body)
- **`build_montage_brightness`** (W): 33 45 [level u8: its low 8 bits, unclamped]; ack validBytes[0]==0
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerMontageBrightness.java:50-53 (Companion.d), :61-63 (ctor(int) -> extBytes [level]), :73-75 (getCommandType=69); base-classes11/com/govee/base2light/kt/ble/AbsController.java:30-45 (ctor/getNextCommBytes -> BleUtils.Companion.p)
- **`read_montage_brightness`** (R): AA 45 (no selector)
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerMontageBrightness.java:38-48 (Companion.b read (false,null)), :30-36 (Companion.c reply); base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:122-132,190-192,221-227 (g/k reply callback)

### `0x46` — firmware-confirmed

- **`build_check_ic_step`** (W): 33 46 [step u8] zero-padded, XOR at 19: 0 start, 3 cancel, 4 confirm. 33 5A [step] for the LightStripLenCaliConfig.c goodsTypes.
  - base-classes10/com/govee/base2light/caliStripLen/CaliLastIcController.java:40-52,61-76,103-106 (CaliLastIcController.Companion.a/b/f: makeCancel default 3, makeConfirm default 4, makeStart default 0, one-byte payload, write); base-classes10/com/govee/base2light/caliStripLen/LightStripLenCaliConfig.java:37-45,268-271 (LightStripLenCaliConfig.a confirm, b cancel, k start; cmd from c at :47-59)
- **`build_reset_device_net`** (W): 33 46, bytes 2..18 zero, XOR 0x75. (33 4E only for version 2, which no caller passes.)
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerResetDeviceNet.java:35-46,48-63 (ControllerResetDeviceNet.<init> mask -> version 1; getCommandType 0x46 unless version 2; getNextCommBytes = p(0x33, cmd, null)); base-classes11/com/govee/base2light/pact/newdetail/setting/common/ResetDeviceNetOpView.java:187 (ResetDeviceNetOpView, ControllerResetDeviceNet(0, 1, no listener))
- **`build_splicing_change_package`** (W): 33 46 [1|0]
  - base-classes4/com/govee/splicing/controller/ControllerChangeSplicing.java:84-86 (Companion.b = [on 1|0]); base-classes4/com/govee/splicing/controller/ControllerChangeSplicing.java:34-42 (makeChangePackageController: cmd KmpDiyConstant.j0 = 70)
- **`build_strip_len_cali_crop`** (W): 33 46|5A [5|6][n][ic u2be x n] (payload <= 17 bytes)
  - base-classes10/com/govee/base2light/caliStripLen/CaliLastIcController.java:85-95 (Companion.d: [op, n] + ic u2be per entry); base-classes10/com/govee/base2light/caliStripLen/LightStripLenCaliConfig.java:62-84 (d: n = 1 for gt 367/384/387-389/400/403/404), :87-90 (e: list)

### `0x47` — source-modeled

- **`build_part_scenes_47`** (W): 33 05 [47][segId u16 LE][5 x segment flag (1 = applied)]
  - base-classes11/com/govee/base2light/pact/newdetail/content/scene/SubModePartScenes.java:34-42,53-55 (getWriteBytes [71, LE2(segId)] + flags; type 71); parse empty :47-48; base-classes10/com/govee/base2light/ble/BleUtil.java:234-244 (getSignedBytesFor2(v, false) = LE)

### `0x48` — source-modeled

- **`build_part_scenes_info`** (W): 33 48 [extBytes (<=17, truncated by kt AbsController.q)]
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerPartScenesInfo.java:49-53,61-63,69-71 (Companion.d); base-classes11/com/govee/base2light/kt/ble/AbsController.java:30-34,47-60
- **`read_part_scenes_info`** (R): AA 48 (no selector)
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerPartScenesInfo.java:37-47 (Companion.b read (false,null)), :29-35 (Companion.c raw passthrough), :69-71 (getCommandType=72); constant base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:99

### `0x49` — source-modeled

- **`read_wifi_hard_version_v2`** (R): AA 49 03 (selector 03 = Wi-Fi hard version)
  - base-classes10/com/govee/base2light/ble/controller/WifiHardVersionControllerV2.java:17-20 (cmd 73), :22-25 (p [03]), :27-38 (parseValidBytes); base-classes10/com/govee/base2light/connect/factory/ReadControllerFactory.java:40
- **`read_wifi_info_v2`** (R): AA 49 [selector 1 MAC / 2 soft version / 3 hard version]
  - base-classes10/com/govee/base2light/ble/controller/WifiMacControllerV2.java:17-25 (getCommandType 73; p [1]); base-classes10/com/govee/base2light/ble/controller/WifiSoftVersionControllerV2.java:17-25 (p [2])
- **`read_wifi_mac_v2`** (R): AA 49 01 (selector 01 = Wi-Fi MAC)
  - base-classes10/com/govee/base2light/ble/controller/WifiMacControllerV2.java:17-20 (cmd 73), :22-25 (p [01]), :27-46 (parseValidBytes); base-classes10/com/govee/base2light/ble/controller/WifiMacController.java:6 (AbsOnlyReadSingleController), :39-46 (u: all-zero = fail)
- **`read_wifi_soft_version_v2`** (R): AA 49 02 (selector 02 = Wi-Fi soft version)
  - base-classes10/com/govee/base2light/ble/controller/WifiSoftVersionControllerV2.java:17-20 (cmd 73), :22-25 (p [02]), :27-38 (parseValidBytes); base-classes10/com/govee/base2light/connect/factory/ReadControllerFactory.java:40

### `0x4a` — source-modeled

- **`build_check_network`** (W): 33 4A [FF]
  - base-classes11/com/govee/base2light/form4Dbgw/Controller4CheckNetwork.java:25-44 (Companion.a fixes [0xFF]; super(true, payload); getCommandType 0x4a (74)); base-classes13/com/govee/dgw/activity/CheckNetActivity.java:299 (live)

### `0x50` — source-modeled

- **`build_h6069_set_device_recognition`** (W): 33 50 [count = len(X)] [loc0] [loc1] ... (ext truncated to 17 B)
  - split_h6061/com/govee/cubelight/ble/SetDeviceRecognitionController.java:9-15 (SetDeviceRecognitionController.<init>/getCommandType = H6069DiyParse.d); split_h6061/com/govee/cubelight/newdetails/diy/h6069/H6069DiyParse.java:41 (H6069DiyParse.d = 80)
- **`build_movie_feast_set_sub_device`** (W): 0xA3 V1: START A3 00 01 [count] 50 [value 0..13]; MIDDLE A3 [i] [17 B]; END A3 FF [<=17 B]; value opaque (caller-built writeBytes)
  - base-classes10/com/govee/base2light/ble/controller/MultiSetSubDeviceController4MovieFeast.java:22-24 (MultiSetSubDeviceController4MovieFeast.getCommandType = 80); base-classes10/com/govee/base2light/ble/controller/MultiSetSubDeviceController4MovieFeast.java:17-19 (MultiSetSubDeviceController4MovieFeast.g = raw writeBytes)

### `0x52` — source-modeled

- **`build_cube_calibration_all_h6066`** (W): 0xA3 V1 framing: p() = AbsMultipleControllerV1.h = 1, so makeWriteMultipleBytes uses makeSendBytesV1 -> makeSendBytesV2 with commBytes [52]. START = [A3 00 01
  - split_h6061/com/govee/cubelight/ble/CalibrationAllController.java:13-31 (CalibrationAllController.<init>), :41-43 (getCommandType = 82), :51-53 (p); base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerV1.java:5-6,10-13,20-23,48-50 (AbsMultipleControllerV1 h, <init>, getProType = -93, r)
- **`build_h6067_calibration_add_not`** (W): 0xA3 V1 framing with comm 0x52 (p() = 1). value = [head][shape_ics...][0x01][head][count][other_ics...], every field always written. The second ctor argument (shape index) is stored in field k and not sent.
  - split_h6061/com/govee/h6067/ble/CalibrationAddNotController.java:17-49 (CalibrationAddNotController.<init>), :58-60 (getCommandType = 82), :66-68 (p); base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783 (makeSendBytesV1/V2), :833-843 (makeWriteMultipleBytes)
- **`build_h6067_calibration_all`** (W): 0xA3 V1 framing with comm 0x52 (p() = 1; START [A3 00 01 total 52 value[0:14]], MIDDLE [A3 i 17B], END [A3 FF <=17B], XOR@19). The value is m+4+e bytes: [0] hea
  - split_h6061/com/govee/h6067/ble/CalibrationAllController.java:15-47 (CalibrationAllController.<init>), :57-59 (getCommandType = 82), :65-67 (p); base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783 (makeSendBytesV1/V2), :833-843 (makeWriteMultipleBytes), :982-1002 (S)

### `0x54` — source-modeled

- **`build_movie_feast_on_off`** (W): 33 54 [open: 1 on / 0 off]
  - base-classes10/com/govee/base2light/ble/controller/MovieOpenController.java:11-14,22-24,33-35 (MovieOpenController.<init>(int)/getCommandType=84/q=[open]); base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:22-24,27-30 (AbsSingleController.g/getProType)
- **`read_movie_feast_on_off`** (R): AA 54 (no selector; p() = null)
  - base-classes10/com/govee/base2light/ble/controller/MovieOpenController.java:7-9 (MovieOpenController.<init>() read); base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:17-19,54-56 (AbsSingleController.f/p = null)

### `0x60` — source-modeled

- **`build_carousel_frames`** (W): value = [voice_switch u8][count u8][concatenated decoded effectStr records, opaque server data]; frames START [A3 00 01 n 60 value[0:14]], MIDDLE [A3 i 17 bytes
  - base-classes11/com/govee/base2light/pact/newdetail/content/carousel/CarouselViewModel.java:585-608 (CarouselViewModel.d0: protocolVersion null or not 1/2 -> MultiController.Companion.a(value, 96), wrapped in Cmd4PtReal.makeCmdPtReal at :608); base-classes11/com/govee/base2light/pact/newdetail/content/carousel/CarouselEffectManager.java:1154-1173 (CarouselEffectManager.s: [count] + decoded records; voiceSwitch inserted at index 0 by d0)
- **`build_feast_module_movie_on_off`** (W): 33 60 [sub_command 0x01][open u8 1/0][0x01 constant, unnamed] zero-padded, XOR at 19.
  - base-classes10/com/govee/base2light/ble/controller/MovieOpenControllerV2.java:11-14,21-24,37-40 (MovieOpenControllerV2 write ctor, getCommandType = 1, q = [open, 1]); base-classes10/com/govee/base2light/ble/controller/AbsSingleFeastController.java:13-24 (u() = 0x60)
- **`build_feast_module_switch`** (W): 33 60 [sub_command 0x01][on u8 1/0] zero-padded, XOR at 19.
  - base-classes10/com/govee/base2light/ble/controller/SwitchControllerV2.java:13-16,23-26,36-39 (SwitchControllerV2 write ctor, getCommandType = 1, q = [open]); base-classes10/com/govee/base2light/ble/controller/AbsSingleFeastController.java:21-24 (u() = 0x60)
- **`read_feast_module_movie_on_off`** (R): AA 60 [sub_command 0x01][selector 0x01] zero-padded, XOR 0xCA.
  - base-classes10/com/govee/base2light/ble/controller/MovieOpenControllerV2.java:7-9,26-35 (read ctor super(false), p() = [1], parseValidBytes open = validBytes[0]); base-classes10/com/govee/base2light/ble/controller/AbsSingleModuleController.java:15-18,30-36 (f = generate20Bytes(0xAA, 0x60, 1, p()); read validBytes = frame[3..18])
- **`read_feast_module_switch`** (R): AA 60 [sub_command 0x01], no selector, zero-padded, XOR 0xCB.
  - base-classes10/com/govee/base2light/ble/controller/SwitchControllerV2.java:9-11,28-34 (read ctor super(false); parseValidBytes on = validBytes[0] != 0); base-classes10/com/govee/base2light/ble/controller/AbsSingleController.java:54-56 (p() default null)

### `0x62` — source-modeled

- **`build_combination_effect_apply_mode`** (W): 33 05 0A [diyId u16le]
  - base-classes10/com/govee/base2light/ac/diyNew/views/combination/DiyAdvancedCombParse.java:72-78 (DiyAdvancedCombParse.f mode controller); base-classes11/com/govee/base2light/kt/general_controller/ControllerMode.java:62-65,102-105 (ControllerMode.Companion.e, k=5)
- **`build_combination_effect_frames`** (W): large: START A4 00 00 01 [total u16le] 62 [value 0..mtu-9] [xor] (mtu B); MIDDLE A4 [i u16le] [mtu-4 B] [xor]; END A4 FF FF [rem] [xor] (rem+4 B, or mtu B); sma
  - base-classes10/com/govee/base2light/ac/diyNew/views/combination/DiyAdvancedCombParse.java:70-84 (DiyAdvancedCombParse.f: Compose4DefWrite4Multi.d with commByte 0x62 (98) at :79); base-classes10/com/govee/base2light/ac/diyNew/views/combination/DiyAdvancedCombParse.java:218-226 (DiyAdvancedCombParse.makeBleController)
- **`build_diy_in_scenes_multi_a3`** (W): 0xA3 V1 multi, 20-byte frames: START [A3 00 01 n comm value[0:14]], MIDDLE [A3 i 17 bytes], END [A3 FF rest], XOR at 19; comm = DIY protocol code (0x62 here), t
  - base-classes10/com/govee/base2light/ble/controller/DiyControllerNoEventV7.java:18-34 (protocol not 91/92 -> MultipleDiyInScenesController(code, bytes, protocol)); base-classes10/com/govee/base2light/ble/MultipleDiyInScenesController.java:28-30

### `0x63` — source-modeled

- **`build_daysync_node`** (W): [param_len u4le][node_id][time_code][t1][t2][brightness][tm+1][tm+1][action...] (payload record, not a frame)
  - base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:1426-1451 (DaySyncUtils.y); base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:1453-1480 (DaySyncUtils.z)
- **`build_daysync_scheme_incremental`** (W): A6 MTU transfer, cmd 63; value = [scheme_short_id][repeat_frequency: 0x00 every day | 0x80|bit i day i Mon=0..Sun=6][op_type=02][n_add][add/change node records 
  - base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:918-924 (DaySyncUtils.i -> h(info, scheme, [1,2], deleteNodes, addOrChangeNodes)); base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:878-914 (h: header x, n_add, add records from y, [n_delete]+ids; node list = add records + [delete block] last; third = F(deleteLens, addLens) for the 0x64 space check)

### `0x64` — source-modeled

- **`build_daysync_space_check`** (W): 0xA3 multi, commByte 0x64; value [scheme][n_del][(node u8, len u32 LE) x n_del][n_add][(node u8, len u32 LE) x n_add]
  - base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:154-172 (C), :188-209 (F), :1426-1451 (y/J record = [nodeId, LE4 len]); base-classes11/com/govee/base2light/kt/general_controller/MultiControllerOnResultParse.java:50-55 (b -> makeSendBytesV1 0xA3)
- **`build_multi_a3_v1`** (W): A3 00 01 [count][comm][value 0..13] | A3 i [17 bytes] ... | A3 FF [last <=17 bytes]
  - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-783 (makeSendBytesV1 / makeSendBytesV2)

### `0x65` — source-modeled

- **`build_daysync_weather_group`** (W): 0xA3 multi, commByte 0x65; value [scheme][count][(time_code, hour, minute) x count]; count and nodes omitted when there are none
  - base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:174-186 (E), :1112-1135 (o), :1453-1473 (z); base-classes11/com/govee/base2light/kt/general_controller/MultiController.java:54-61 (a -> 0xA3)

### `0x70` — source-modeled

- **`build_h5310_close_warn_via_gw`** (W): 33 70 [sno][0x16][0x01][e][f] 00.. (= [sno] + inner frame 33 16 01 e f [1:17]); on: e,f = 1,0; off: e,f = 2,1
  - base-classes12/com/govee/base2newth/gwsub/Controller4SubPtRealOp.java:18-23,34-45 (Controller4SubPtRealOp.<init> super(true) / getCommandType returns BleProtocol.V / i); base-classes12/com/govee/base2newth/data/controller/BleProtocol.java:26 (BleProtocol.V = 112)
- **`build_h5901_irrigation`** (W): 33 70 [sno][0x01][0x01][minutes u16le]
  - base-classes13/com/govee/eco/executeAction/parser/IrrigationActionParser.java:50-57,106-119 (IrrigationActionParser.B sends CmdPtReal / z / y sno lookup); base-classes10/com/govee/base2kt/utils/BleUtils.java:132-142 (BleUtils.Companion.D little-endian when z=false)
- **`build_h5901_switch_action`** (W): 33 70 [sno][0x01][00][00][00]
  - base-classes13/com/govee/eco/executeAction/parser/SwitchActionParserH5901.java:34-51,65-68 (SwitchActionParserH5901.B sno lookup / C / v sends CmdPtReal; the boolean is unused); base-classes11/com/govee/base2light/kt/general_controller/ControllerSingle.java:38-41,55-57 (ControllerSingle.Companion.c: write, mask 8 leaves its last flag false)
- **`build_h5901_sync_triggers`** (W): 33 70 [max(sno,0)][0x19][has177][has178][has179][has180]
  - base-classes12/com/govee/base2light/synctriggers/h5901/ControllerSyncTriggers4H5901.java:30-35,46-53 (ControllerSyncTriggers4H5901.Companion.a / getCommandType returns BleProtocol.V / q); base-classes10/com/govee/base2light/ble/controller/AbsOnlyWriteSingleController.java:41-43 (super(true))

### `0x71` — source-modeled

- **`read_music_lib_player_state`** (R): AA 71 00 x17 [XOR@19]
  - base-classes11/com/govee/base2light/kt/general_controller/online/ControllerOnlineMusicPlayerState.java:25-42 (Companion.a; super(false); getCommandType 113; getNextCommBytes BleUtils.p(pro, 0x71, null))

### `0x79` — source-modeled

- **`build_music_lib_volume`** (W): 33 79 [volume u8, not clamped] 00 x16 [XOR@19]
  - base-classes11/com/govee/base2light/kt/general_controller/online/ControllerOnlineVolume.java:53-59,62-65,77-90 (Companion.b / makeController4Write; getCommandType 121); base-classes10/com/govee/base2light/ble/controller/AbsControllerNoEvent4Single.java:139-145 (0x33)
- **`read_music_lib_volume`** (R): AA 79 00 x17 [XOR@19]
  - base-classes11/com/govee/base2light/kt/general_controller/online/ControllerOnlineVolume.java:45-51,67-75,83-90 (Companion.a / makeController4Read)

### `0x7b` — source-modeled

- **`read_diy_create_gif`** (R): AC 7B [0A][diy_code u16le][01][effect_op (0 if absent)]
  - base-classes10/com/govee/base2light/ac/diyNew/details/DiyEdit4DeviceCreateGifActivity.java:1948-1972 (DiyEdit4DeviceCreateGifActivity.g2: [10, lo, hi, 1, effectOP or 0]); base-classes12/com/govee/base2light/widget/diy/view/material/MaterialEditActivity.java:308-327 (MaterialEditActivity.E2: [10, lo(n0), hi(n0), 1]; reply F2 :330-334)

### `0x82` — source-modeled

- **`build_carousel_apply_mode`** (W): 33 05 [82][carouselId u32 LE][voiceId u32 LE][voiceType (0 when null)][op]
  - base-classes11/com/govee/base2light/pact/newdetail/content/carousel/CarouselViewModel.java:618-634 (CarouselViewModel.e0 -> ControllerMode.Companion.e); base-classes11/com/govee/base2light/pact/newdetail/content/carousel/CarouselViewModel.java:461-478 (X: e0(bean, 1) through applyController)

### `0x83` — source-modeled

- **`build_display_remind_effect`** (W): 33 83 [01][category][slot][switch][voiceType][voiceCode u32 LE][effectType][effectCode u32 LE]
  - base-classes11/com/govee/base2light/pact/newdetail/content/display/EditDisplayRemindController.java:42-64 (Companion.a); base-classes10/com/govee/base2kt/utils/BleUtils.java:162-175 (Companion.F(v, false) = LE)
- **`build_display_remind_global`** (W): 33 83 [00][closeAll][startH][startM][endH][endM][repeat][nba][nfl][env]
  - base-classes11/com/govee/base2light/pact/newdetail/content/display/EditDisplayRemindController.java:66-91,97-99 (Companion.b; getCommandType -125)
- **`build_mic_set_rgb`** (W): A5 02 83 [R][G][B][sum8] (7 bytes, unpadded, additive checksum)
  - base-classes10/com/govee/base2light/ble/mic/controller/AbsMicController.java:31-57 (AbsMicController.g, getProType 0xA5); base-classes10/com/govee/base2light/ble/mic/controller/MicSetRgbController.java:47-49 (MicSetRgbController.getCommandType)
- **`read_display_remind`** (R): AC 83 (single request, multi-frame 0xAC reply)
  - base-classes11/com/govee/base2light/pact/newdetail/content/display/activity/SetDisplayRemindActivity.java:827-831 (T1 -> makeSingleRead(0x83)); base-classes11/com/govee/base2light/kt/general_controller/ControllerSingleRead.java:44-72

### `0x84` — source-modeled

- **`build_alarm_clock_apply_effect`** (W): [optional switch-on frames] + [scene/DIY effect upload frames] + [33 84 op(0|1) alarm add/edit frame in place of the 33 05 mode frame]
  - base-classes11/com/govee/base2light/pact/newdetail/content/display/activity/EditDisplayAlarmClockActivity.java:1460-1517 (alarm frame built once at :1460 by EditAlarmClockController.Companion.a; effect_type 1 -> EventScenesEffect.sendEventScenesEffect :1489; effect_type 2 -> EventApplyDiy.sendEventApplyDiy :1500; effect_type 0, or 1/2 with no scene/DIY selected -> alarm frame alone via doCommController :1502-1517); base-classes10/com/govee/base2light/ac/effect/EventScenesEffect.java:22-27,44-46 (controller stored in field e)
- **`build_alarm_clock_delete`** (W): 33 84 02 [alarm_id] 00 x15 [XOR@19]
  - base-classes11/com/govee/base2light/pact/newdetail/content/display/EditAlarmClockController.java:77-82 (EditAlarmClockController.Companion.b); base-classes11/com/govee/base2light/pact/newdetail/content/display/activity/EditDisplayAlarmClockActivity.java:1229
- **`build_alarm_clock_edit`** (W): 33 84 [op 00 add | 01 edit][alarm_id][flags b7 on | b6 later_remind | b5 effect_switch | b4 voice_switch][hour][minute][repeat][brightness][effect_type 0 none/1
  - base-classes11/com/govee/base2light/pact/newdetail/content/display/EditAlarmClockController.java:35-74,105-115 (EditAlarmClockController.Companion.a; 16 ext bytes; getCommandType -124; super(true) -> 0x33); base-classes12/com/govee/base2light/util/ParseUtils.java:84-88 (setValue sets bit n-1: 8/7/6/5 -> 0x80/0x40/0x20/0x10)
- **`build_alarm_clock_edit_days`** (W): 33 84 [op 0|1][alarm_id][flags b7 on b6 later b5 effect b4 voice][hour][minute][repeat: 0x00 every day | 0x80|bit i day i Mon=0..Sun=6][brightness][effect_type 
  - base-classes12/com/govee/base2light/util/NumUtil.java:30-44 (NumUtil.getRepeat encoder); base-classes11/com/govee/base2light/pact/newdetail/content/display/activity/EditDisplayAlarmClockActivity.java:1271-1279 (h3: [Mon..Sun], views tvRepeatWeekMon..Sun :280-323)
- **`build_alarm_clock_switch`** (W): 33 84 03 [alarm_id][flags, same bits as op 0/1] 00 x14 [XOR@19]
  - base-classes11/com/govee/base2light/pact/newdetail/content/display/EditAlarmClockController.java:85-102 (EditAlarmClockController.Companion.c); base-classes11/com/govee/base2light/pact/newdetail/content/display/activity/DisplayAlarmClockListActivity.java:547 (switch flipped before building)
- **`read_alarm_clock_list`** (R): AC 84 00 x17 [XOR@19] (request); reply is multi-frame, see replies
  - base-classes11/com/govee/base2light/pact/newdetail/content/display/activity/DisplayAlarmClockListActivity.java:375-392 (H1: ControllerSingleRead.makeSingleRead(0x84) at :377, ShareVM.sendWriteCmd at :390); base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:158-165 (proType 0xAC)

### `0x85` — source-modeled

- **`build_alarm_clock_ring_action`** (W): 33 85 [00 close | 01 remind later]
  - base-classes11/com/govee/base2light/pact/newdetail/content/display/CloseAlarmClockController.java:26-41 (Companion.a [b]; getCommandType -123); base-classes11/com/govee/base2light/pact/newdetail/content/display/DisplayViewModel.java:537-541

### `0x86` — source-modeled

- **`build_diy_create_gif`** (W): 33 86 [01][b0 u16 LE][diyId u32 LE]
  - base-classes10/com/govee/base2light/ac/diyNew/details/DiyEdit4DeviceCreateGifActivity.java:1974-1989 (l2: ControllerSingle.c(0x86, [1, LE2(b0), LE4(diyId)]))
- **`build_diy_create_gif_v1`** (W): 33 86 [01][diy_code u16le][diy_id u32le]
  - base-classes10/com/govee/base2light/ac/diyNew/details/DiyEdit4DeviceCreateGifActivity.java:1974-1989 (DiyEdit4DeviceCreateGifActivity.l2 -> ControllerSingle.Companion.c(0x86, ...)); base-classes11/com/govee/base2light/kt/general_controller/ControllerSingle.java:38-41 (Companion.c write, proType 0x33)

### `0xa1` — source-modeled

- **`read_special_info_a1`** (R): AC A1 (no ext bytes), zero-padded, XOR at byte 19
  - base-classes11/com/govee/base2light/kt/general_controller/ControllerReadSpecialInfoMultiBack.java:100-109 (Companion.k), :112-117 (ctor p/q/r), :127-133 (d/getCommandType); base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:158-160 (getNextCommBytes = BleUtils.Companion.p(proType, command, ext)), :163-165 (getProType = 0xAC)

### `0xa2` — source-modeled

- **`read_bulb_group_color`** (R): AA A2 [group u8, 1-based, 1..ceil(pieces / 4)] [00 x16] [bcc]
  - split_rgbiclight/com/govee/rgbiclight/VM4Light.java:1476-1477 (o5 -> q5 unless goodsType 351/352), :1616-1631 (q5 passes t5 as the mode-reply handler), :650-669 (t5 -> N5 unless Support.j), :882-970 (N5: generateReadController4OnlyColor when Support.D is false); split_rgbiclight/com/govee/rgbiclight/ControllerStrategyConfig.java:610-617 (h2 queues the handler's list), :1335-1341 (V1 mode read)
- **`read_color_group`** (R): AA A5 [group u8, 1-based, 1..ceil(pieces / 3)] [00 x16] [bcc]
  - split_rgbiclight/com/govee/rgbiclight/VM4Light.java:942-949 (N5 zD branch: generateReadController); base-classes11/com/govee/base2light/kt/general_controller/Controller4ColorInfoByGroup.java:37-42 (default cmd -91 = 0xA5), :111-120 (g: payload [group])

### `0xa3` — firmware-confirmed

- **`build_abs_music_blob`** (W): START [A3 00 01 count 42 blob[0:14]], MIDDLE i [A3 i blob 17B], END [A3 FF last<=17B zero-padded], XOR@19 each; count = 2 if len(blob) <= 14 else ceil((len-14)/
  - base-classes10/com/govee/base2light/ble/controller/MultiControllerNoEvent.java:16-22,37-40 (MultiControllerNoEvent.<init>/getCommandType); base-classes10/com/govee/base2light/ble/controller/MusicComposeController.java:27-38 (MusicComposeController.<init>)
- **`build_breath_train_multi_a3`** (W): A3 00 01 [N] 70 [data0..13] | A3 [i=1..N-2] [17 data] | A3 FF [last <=17 data, zero-padded]; data = BreathTrainSettingInfo.b() = [len u16le][a][rounds u16le][10
  - base-classes11/com/govee/base2light/kt/general_controller/MultiController.java:36-39,54-61,76-78 (MultiController.Companion.a / <init> calls makeSendBytesV1(0xA3, op, data) / getCommandType returns field d = op); base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:715-782 (MultipleControllerCommV1.makeSendBytesV1 / makeSendBytesV2)
- **`build_color_strip_apply`** (W): build_color_strip_multi frames (posList null), then one single frame 33 05 [mode] with mode = 0x0C (OperateModeInfo.A) when LightColorCapabilityCenter.Q0(info) 
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:937-951 (Companion.m: MultiColorStripControllerNoEvent :948 + ControllerMode.e([OperateModeInfo.A when Q0 is 10, else 21]) :949-950); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:1114-1141 (Companion.w: k2 -> n; M0 non-null -> l; Y1 -> m; else null)
- **`build_color_strip_multi`** (W): 0xA3 V1 framing (AbsMultipleControllerV1.p() = h = 1 -> MultipleControllerCommV1.makeWriteMultipleBytes -> makeSendBytesV1/V2): START A3 00 01 [count] [comm 0x4
  - base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerV14ColorStrip.java:23-93 (AbsMultipleControllerV14ColorStrip.<init>(Colors)), :197-200 (g = k), :202-205 (p = AbsMultipleControllerV1.h); base-classes10/com/govee/base2light/ble/controller/MultipleColorStripControllerV1.java:7-9 (ctor super(colors)), :15-18 (getCommandType = 64 = 0x40)
- **`build_color_tem_strip_apply`** (W): build_color_tem_strip_multi frames, then one single frame 33 05 [mode], mode = 0x0C when Q0(info) == 10 else 0x15.
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:954-966 (Companion.n: MultiColorTemStripControllerNoEvent :963 + ControllerMode.e([0x0C when Q0 is 10, else 0x15]) :964-965); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:1114-1124 (w: k2 tested before M0 and Y1)
- **`build_color_tem_strip_multi`** (W): 0xA3 V1 framing (AbsMultipleControllerV1.p() = h = 1 -> MultipleControllerCommV1.makeWriteMultipleBytes -> makeSendBytesV1/V2): START A3 00 01 [count] [comm 0x4
  - base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerColorTemStrip.java:59-186 (AbsMultipleControllerColorTemStrip.<init>(Colors, Info4BleIotDevice)), :193-201 (g), :203-206 (p = AbsMultipleControllerV1.h); base-classes10/com/govee/base2light/ble/controller/MultipleColorTemStripController.java:19-23, :48-51 (getCommandType = 64 = 0x40)
- **`build_gradient_color_strip_apply`** (W): j(): a J-length colorSet is tripled per segment, otherwise min(len, 3J) colours are copied into a zeroed 3J array; a 3J-length brightnessSet keeps the first of 
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/GradientColorControllerConfig.java:230-265 (j), :430-455 (x: MultiGradientColorTemStripControllerNoEvent :441 + ControllerMode.e([21]) :443); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/GradientColorControllerConfig.java:412-416 (v = LightColorCapabilityCenter.q2), :418-428 (w: map f), :75-96 (map f)
- **`build_gradient_color_tem_strip_multi`** (W): 0xA3 V1 framing (AbsMultipleControllerV1.p() = h = 1 -> MultipleControllerCommV1.makeWriteMultipleBytes -> makeSendBytesV1/V2): START A3 00 01 [count] [comm 0x4
  - base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerGradientColorTemStrip.java:40-193 (AbsMultipleControllerGradientColorTemStrip.<init>(Colors)), :200-208 (g), :210-213 (p = AbsMultipleControllerV1.h); base-classes10/com/govee/base2light/ble/controller/MultipleGradientColorTemStripController.java:17-20, :29-32 (getCommandType = 64 = 0x40)
- **`build_h60ax_new_music_multi`** (W): START [A3 00 01 count 41 code effect[0:13]], MIDDLE i [A3 i effect 17B], END [A3 FF last<=17B zero-padded], XOR@19 each; count = 2 if len(effect) <= 13 else cei
  - split_h60ax/com/govee/pact_h60a0/ble/controller/compose/NewMusicMultipleController.java:19-34 (NewMusicMultipleController.<init>/getCommandType); base-classes10/com/govee/base2light/ble/controller/MultipleController4Music.java:20-24,38-46,55-58 (MultipleController4Music.<init>/g/getCommandType/p)
- **`build_h61d3_light_recognition_fixed`** (W): Two frames. START a3 00 01 02 5d | 64 ff ff ff 2c 01 02 02 01 01 06 ff 00 00 | XOR b2; END a3 ff | ff ff 00 00 ff 00 00 00 ff 00 ff ff 8b 00 ff 00 00 | XOR 28. 
  - split_h61d3/com/govee/h61d3/pact/H61D3Support.java:1627-1628 (H61D3Support.w0); split_h61d3/com/govee/h61d3/ble/controller/LightRecognitionMultiController.java:35-67 (LightRecognitionMultiController.Companion.a/<init>, default comm 93)
- **`build_hollowlamp_local_colors`** (W): 0xA3 V1 framing (AbsMultipleControllerV1.p() = h = 1 -> MultipleControllerCommV1.makeWriteMultipleBytes -> makeSendBytesV1/V2): START A3 00 01 [count] [comm 0xA
  - split_hollowlamp/com/govee/hollowlamp/ble/LocalColorSettingMultiControllerV1.java:14-32 (value), :38-40 (g), :42-44 (getCommandType = -91 = 0xA5), :52-54 (p = AbsMultipleControllerV1.h); split_hollowlamp/com/govee/hollowlamp/ble/LocalColor.java:20-27 (makeKelvinOrColorByColor), :41-48 (checkKelvinRange), :58-61 (getShowColor)
- **`build_hollowlamp_multi_music_v1`** (W): A3 V1 comm 0x41: START A3 00 01 [count] 41 + value[0:14]; value = [effect][n][n x (R,G,B)] + (n-1) zero bytes; MIDDLE A3 [i] 17B; END A3 FF [<=17B]; optional fo
  - split_hollowlamp/com/govee/hollowlamp/ble/MultiMusicController.java:15-36 (MultiMusicController.<init>: returns when i == 0; array i+1+3*len); split_hollowlamp/com/govee/hollowlamp/ble/MultiMusicController.java:46-48,60-62 (getCommandType = BleProtocol.e = 65, p = V1.h)
- **`build_multi_a3_v1_group`** (W): START A3 00 01 [count] [comm (1 byte V1 / cmd,p() V2)] [value head 15-len(comm)] XOR; MIDDLE A3 [i] [17 bytes] XOR; END A3 FF [last chunk + STALE bytes of previ
  - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1Group.java:86-147 (k: one 17-byte buffer allocated at :117, all 17 bytes copied into END :125-126); base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1Group.java:157-166 (makeWriteMultipleBytes: p()==0 -> i :44-80, else j :82-84 -> k); :149-155 (l, V2 comm [cmd, p()])
- **`build_music_h1771`** (W): apply: A3 V2 [0x41, code] with value [n][n x (R,G,B)][sub_effects...] (code 4: [n][n x (R,G,B)][sub_effects[0]]), then 33 05 13 [code][sens]; preview: 33 05 13 
  - split_hollowlamp/com/govee/hollowlamp/newdetails/h1771/H1771MusicMode.java:82-112 (H1771MusicMode.d: mode frame :84, preview :85-87, code-4 rebuild :99-110, send :111); split_hollowlamp/com/govee/hollowlamp/newdetails/h1771/H1771MusicMode.java:115-118 (getDefaultMusicEffect: codes 4, 52, 72, 81, 73 and the constant f)
- **`build_music_h6057`** (W): apply: A3 V2 [0x41, code] with value [n][n x (R,G,B)][sub_effects...], then 33 05 13 [code][sens]; preview: 33 05 13 [code][sens] only
  - split_hollowlamp/com/govee/h6057/origin/newDetail/H6057MusicMode.java:76-107 (H6057MusicMode.d: preview :78-84, mode :85-89, send :106); split_hollowlamp/com/govee/h6057/origin/newDetail/H6057MusicMode.java:110-113 (getDefaultMusicEffect: codes 57, 56, 84, 99, 85, 51, 100 with a 3-entry subEffectList)
- **`build_music_h6059`** (W): apply: A3 V2 [0x41, code] with value [n][n x (R,G,B)][sub_effects...], then 33 05 13 [code][sub_effects[0] if any][sens]; preview (sensitivity change): 33 05 13
  - split_hollowlamp/com/govee/hollowlamp/newdetails/H6059MusicMode.java:67-89 (H6059MusicMode.d: preview flag true :70-79, apply :81-88); split_hollowlamp/com/govee/hollowlamp/newdetails/H6059MusicMode.java:94 (getDefaultMusicEffect: codes 75 JieZou, 76 Mofa, subEffectList [1])
- **`build_music_mode_compose_v2`** (W): START [A3 00 01 count 41 code effect[0:13]], MIDDLE i [A3 i effect 17B], END [A3 FF last<=17B zero-padded], XOR@19 each; count = 2 if len(effect) <= 13 else cei
  - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/MusicModeComposeController.java:21-28 (MusicModeComposeController.<init>); base-classes10/com/govee/base2light/ble/controller/MusicControllerNoEvent3Result.java:15-21 (MusicControllerNoEvent3Result.<init>)
- **`build_music_multi_a3_v2`** (W): START A3 00 01 [count] 41 [music_code] value[0:13]; MIDDLE A3 [i] 17B; END A3 FF [last chunk <=17B zero-padded, empty when len(value) <= 13]; count = 2 if len(v
  - base-classes10/com/govee/base2light/ble/controller/MultipleController4Music.java:14-18,39-46,56-57 (MultipleController4Music.<init>/g/getCommandType/p); base-classes10/com/govee/base2light/ble/controller/AbsMultipleControllerV2.java:19-20 (AbsMultipleControllerV2.getProType 0xA3)
- **`build_music_v2_then_mode`** (W): A3 V2 [0x41, music_code] frames (as build_music_multi_a3_v2), then 33 05 [mode_payload, at most 17 bytes]
  - base-classes11/com/govee/base2light/pact/newdetail/ble/controller/MusicModeComposeController.java:21-28 (MusicModeComposeController.<init>: MusicControllerNoEvent3Result first, then the mode controller); base-classes10/com/govee/base2light/ble/controller/AbsComposeControllerWithOpResult.java:88-104 (b/c list order), :315-343 (onResult: next controller only after result 0; failure aborts)
- **`build_set_ai_action`** (W): A3 V1 frames (comm 0x58 at START byte 4, 14 value bytes in START). Value: [preview 1/0][len u16le = 3n+9][field_a u16le][field_b u16le][light_type u16le][n u8][
  - base-classes10/com/govee/base2light/ai4edit/controller/SetAiActionController.java:21-64 (SetAiActionController.c/d/getCommandType = 88); base-classes10/com/govee/base2light/ble/BleUtil.java:234-244 (BleUtil.getSignedBytesFor2, LE)
- **`build_set_ai_action_v2`** (W): Same A3 V1 framing as build_set_ai_action (comm 0x58). Value: [preview][len u16le = 3n+11][field_a u16le][field_b u16le][light_type u16le][n][n x R,G,B][speed][
  - base-classes10/com/govee/base2light/ai4edit/controller/SetAiActionControllerV2.java:15-31 (SetAiActionControllerV2.c); base-classes10/com/govee/base2light/ai4edit/controller/SetAiActionController.java:21-64 (SetAiActionController.c/d/getCommandType, inherited framing)
- **`build_shape_layout_h6067`** (W): 0xA3 V1 framing (AbsMultipleControllerV1.p() = h = 1 -> MultipleControllerCommV1.makeWriteMultipleBytes -> makeSendBytesV1/V2): START A3 00 01 [count] [comm 0x3
  - split_h6061/com/govee/h6067/ble/MultiDeviceController.java:18-65 (MultiDeviceController.<init>(List,int goodsType)); split_h6061/com/govee/h6067/ble/MultiDeviceController.java:71-77 (g returns field i; getCommandType = 49 = 0x31), :83-85 (p = AbsMultipleControllerV1.h)
- **`build_shape_layout_h606a`** (W): 0xA3 V1 framing (AbsMultipleControllerV1.p() = h = 1 -> MultipleControllerCommV1.makeWriteMultipleBytes -> makeSendBytesV1/V2): START A3 00 01 [count] [comm 0x3
  - split_h6061/com/govee/cubelight/ble/MultiDeviceControllerH606A.java:20-47 (MultiDeviceControllerH606A.<init>(List,int): value field i, ceil); split_h6061/com/govee/cubelight/ble/MultiDeviceControllerH606A.java:49-76 (MultiDeviceControllerH606A.<init>(List,int,List): same value, Math.round; list2 only feeds the cloud bytes B(list2))
- **`read_ai_edit_info`** (R): AC 01 [reset u8][game_id u16le][feature_id u16le], XOR@19.
  - base-classes10/com/govee/base2light/ai4edit/controller/ReadAiEditInfoController.java:53-65 (ReadAiEditInfoController.getCommandType/p); base-classes10/com/govee/base2light/ble/controller/AbsSingleReadMultiReplyController.java:17-20 (getProType = 0xAC)
- **`read_hollowlamp_local_colors`** (R): AA A5 [page u8, 1-based; default 1]
  - split_hollowlamp/com/govee/hollowlamp/ble/LocalColorReadControllerV1.java:26-48 (ctors: (int page), (AbsBle) and (boolean countOnly); f defaults to 1), :65-71 (getCommandType = -91 = 0xA5, p = [f]); split_hollowlamp/com/govee/hollowlamp/adjust/LocalColorSettingAc.java:408-415 (c1:413 BLE read, AbsBle ctor, auto-advances pages)

### `0xa4` — source-modeled

- **`build_mtu_a4_frames`** (W): Small case (len <= mtu-8): START [A4 00 00 01 02 00][comm][value][XOR] (len+8 bytes) + END [A4 FF FF A4]. Otherwise: START [A4 00 00 01][total u16le][comm][valu
  - base-classes10/com/govee/base2light/ble/comm/MultipleControllerCommV1.java:366-493 (makeSendBytesMtu); base-classes11/com/govee/base2light/kt/ble/Compose4DefWrite4Multi.java:231-247 (Companion.d: makeSendBytesMtu(proType 0xA4, cmd, value, max(20, mtu)) + ControllerMTUMulti with default proType 0xA4, then the mode controller)
- **`build_remote_pair`** (W): 33 A4 [on]
  - base-classes11/com/govee/base2light/kt/general_controller/Controller4RemotePair.java:49-52,67-69 (Companion.d; getCommandType -92); split_hollowlamp/com/govee/straightfloorlamp/adjust/VM4Light.java:1092,1098 (control screen d(false) / d(true))
- **`read_remote_pair`** (R): AA A4 (no selector)
  - base-classes11/com/govee/base2light/kt/general_controller/Controller4RemotePair.java:30-47 (Companion.b read; c parses unsigned valid[0]); split_hollowlamp/com/govee/straightfloorlamp/adjust/VM4Light.java:1082

### `0xa5` — firmware-confirmed

- **`build_mic_color_cmd_mode`** (W): 20 bytes: [0]=0x33 (AbsControllerNoEvent4Single.getProType, write) [1]=0x05 (ControllerMode, cmd 5) [2..18]=colour payload = Config.u()[colourVersion Q0](rgb, p
  - base-classes10/com/govee/base2light/ble/mic/controller/MicColorCmdController.java:9-12 (MicColorCmdController.<init>: data ctor -> AbsMicController(byte[]) -> super(true)); base-classes10/com/govee/base2light/ble/mic/controller/MicColorCmdController.java:18-21 (MicColorCmdController.g: returns field f, overriding AbsMicController.g A5/sum8 framing at AbsMicController.java:31-52)
- **`build_mic_color_cmd_over56`** (W): 20 bytes: [0]=0xA3 [1]=0x00 (header index) [2]=0x01 [3]=total frames (2 when len(payload) <= 14, else ceil((len-14)/17)+1; counts header + middles + 0xFF tail, 
  - base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:234-235 (Companion.c: Config.k(info) -> j()); base-classes11/com/govee/base2light/pact/newdetail/config/fuc/color/ColorModeControllerConfig.java:689-726 (Companion.j: MultiController.Companion.c(payload, 0x6E, ControllerMode.e([21])) at :712)
- **`read_barelightv1_group_colors`** (R): part brightness: AA A5 [g] for g = 1..ceil(pieces/3); otherwise AA A2 [g] for g = 1..ceil(pieces/4); zero-padded, XOR at byte 19
  - barelightv1 Model.itemClickOp -> /rgbic/newdetail, C0(17) when isRgbicBk4Ble split_rgbiclight/com/govee/barelightv1/pact/Model.java:54-71; OldRgbicBkUtil.isRgbicBk4Ble: SKU in l = {H6145,H6146,H6147,H6171} and pactType >= 10 and pactCode >= 1 base-classes9/com/govee/base2home/pact/support/OldRgbicBkUtil.java:18-21,32,36,59-69
- **`read_h6119_group_colors`** (R): list of AA A5 [g] 00 x16 [XOR], g = 1..ceil(pieces/3)
  - rgbiclight VM4Light.N5 A5 branch split_rgbiclight/com/govee/rgbiclight/VM4Light.java:948-954; connect t5 :650-668 (skipped when Support.j, split_rgbiclight/com/govee/rgbiclight/Support.java:278-283); heartbeat ShareVM.N3 base-classes11/com/govee/base2light/pact/newdetail/ShareVM.java:1005-1008 -> VM4Light.J3 :732-760 -> G5 :275-287 -> O5 :351-356
- **`read_h612526_group_colors`** (R): part brightness: AA A5 [g] for g = 1..5 (15 pieces / 3); otherwise AA A2 [g] for g = 1..4 (15 pieces / 4); each frame zero-padded with XOR at byte 19
  - h612526 Model.itemClickOp -> C0(17) -> /rgbic/newdetail split_rgbiclight/com/govee/h612526/sku/Model.java:56-75; rgbiclight VM4Light.N5 split_rgbiclight/com/govee/rgbiclight/VM4Light.java:882-970
- **`read_hollowlamp_local_color_count`** (R): AA A5 00.. [XOR 0x0F] (no selector); reply AA A5 [position][groupNum = preset-colour page count]; the app ignores the remaining bytes
  - split_hollowlamp/com/govee/hollowlamp/newdetails/H6059NewDetailVM$connectBleSuc$1$5$1.java:297-306 (connect batch: Controller4ExtBytes.Companion.b([0xA5])); split_hollowlamp/com/govee/hollowlamp/newdetails/H6059NewDetailVM$connectBleSuc$1$5$1.java:158-170 (w: position = reply byte 0, logged; groupNum = reply byte 1 -> T4().T)
- **`read_rgbic_group_colors`** (R): list of frames AA <A5 if Support.D else A2> [g] 00 x16 [XOR], g = 1..ceil(pieces / Support.d). pieces = sum(info4BleIot.q()) when the connect IC list is filled,
  - VM4Light.N5 split_rgbiclight/com/govee/rgbiclight/VM4Light.java:882-970 (zD :910, pieces :917-923, size :936, group count :938, loop :947-968); callers: t5 :650-668 (connect, Support.j gate); G5 :275-287 via J3 :732-760 (heartbeat switch change, no gate); O5 :351-356

### `0xa6` — source-modeled

- **`build_auto_induction`** (W): 33 A6 [on 1/0]
  - split_hollowlamp/com/govee/hollowlamp/ble/OnOffAutoInductionController.java:31-33 (q); callers split_hollowlamp/com/govee/hollowlamp/adjust/UiV5.java:1511,1517; split_hollowlamp/com/govee/hollowlamp/newdetails/H6059NewDetailVM.java:396-399 (O4, Controller4ExtBytes.Companion.d([0xA6, on 1|0]))
- **`build_exit_remote_pair`** (W): 33 A6 (no payload; q() returns null)
  - split_hollowlamp/com/govee/straightfloorlamp/ble/ExitRemoteController.java:7-26 (q returns null; BleUtils.p skips a null payload, base-classes10/com/govee/base2kt/utils/BleUtils.java:1030-1041); caller split_hollowlamp/com/govee/straightfloorlamp/adjust/v1/RemoteControlAc.java:235 (Q0)
- **`build_mtu_a6_package`** (W): START [A6][00 00][01][pkg_count][pkg_index 1-based][verify 8B][frame_count u16le][63][value...][XOR]: len+18 bytes when the value is at most mtu-18 bytes, else 
  - base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:926-949 (j: small package, START len+18 B + empty END); base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:951-1018 (k: START mtu B, MIDDLE [A6 seq u16le][mtu-4][xor], END [A6 FF FF][rest][xor]; END is taken when i == max-1 or pos+cap >= len)
- **`build_position_calibration_h6062`** (W): 33 A6 [01 sub-op][value: 1 when flag is false, else 0]
  - split_h6062/com/govee/striplight/ble/Special4PositionCalibrationController.java:35-37 (q); caller split_h6062/com/govee/striplight/add/v3/AcCalibrationPosition.java:237 (ctor arg: field P negated)
- **`read_logo`** (R): AA A6 (no selector)
  - base-classes10/com/govee/base2light/ble/controller/LogoController.java:12-14 (read ctor), :42-47 (parseValidBytes), :23-29 (parseLogo: auto = v[0] == 0); split_h6047/com/govee/h6047/detail/NewDetailVm.java:756,1265
- **`read_logo_or_auto_induction`** (R): AA A6 (no selector)
  - base-classes10/com/govee/base2light/ble/controller/LogoController.java:12-13 (read ctor super(false), no p(); reply parse :42-47); callers split_h6047/com/govee/h6047/detail/NewDetailVm.java:756,1265 and base-classes11/com/govee/base2light/light/LogoUIV2.java:130; split_hollowlamp/com/govee/hollowlamp/ble/OnOffAutoInductionController.java:9-10 (read ctor super(false); reply parse :26-27); callers split_hollowlamp/com/govee/hollowlamp/adjust/BleOpV3.java:87,89
- **`read_position_calibration_h6062`** (R): AA A6 [01 sub-op]
  - split_h6062/com/govee/striplight/ble/Special4PositionCalibrationController.java:26-28 (p); caller split_h6062/com/govee/striplight/add/v3/PairAc.java:133-134

### `0xa7` — source-modeled

- **`build_hollowlamp_sensitivity`** (W): 33 A7 [sensitivity u8; UI 1..30, controller writes the value's low 8 bits, unclamped]
  - split_hollowlamp/com/govee/hollowlamp/ble/SensitiveController.java:13-16 (write ctor stores the value in f), :22-24 (cmd BleProtocol.v), :31-33 (q [f]); split_hollowlamp/com/govee/hollowlamp/ble/BleProtocol.java:30 (v=-89)
- **`build_sensitivity`** (W): 33 A7 [level]
  - split_hollowlamp/com/govee/hollowlamp/ble/SensitiveController.java:13-16,22-24,31-33 (getCommandType BleProtocol.v = -89; q [f]); split_hollowlamp/com/govee/hollowlamp/ble/BleProtocol.java:30
- **`read_hollowlamp_sensitivity`** (R): AA A7 (no selector)
  - split_hollowlamp/com/govee/hollowlamp/ble/SensitiveController.java:9-11 (read ctor super(false)), :26-29 (parseValidBytes reads valid byte 0); caller split_hollowlamp/com/govee/hollowlamp/adjust/BleOpV3.java:88-89 (connect read set, version-gated); reply stored BleOpV3.java:327-336
- **`read_sensitivity`** (R): AA A7 (no selector)
  - split_hollowlamp/com/govee/hollowlamp/ble/SensitiveController.java:9-11,26-28 (read ctor; parseValidBytes reads valid byte 0); split_hollowlamp/com/govee/hollowlamp/adjust/BleOpV3.java:89 (connect read batch)

### `0xa9` — source-modeled

- **`build_ai_filter`** (W): 33 A9 10 0F [on][b][c][d][e][f u16le][g u16le][year u16le][month][day][hour][minute] (UTC; fills bytes 2..18)
  - base-classes12/com/govee/base2light/videomode/newdetail/controller/AiFilterController.java:40-46 (Companion.a; getter order f,g,d,a,h,e,c = bean fields a..g); bean base-classes12/com/govee/base2light/videomode/newdetail/bean/AiFilterStatusBean.java:43-92
- **`build_ai_identify_model`** (W): 33 A9 08 02 [model u16le]
  - base-classes10/com/govee/base2light/ai4edit/controller/ChangeAiModelController.java:16-30 (BleUtil.getSignedBytesFor2(i,false) LE, base-classes9/com/govee/base2home/pact/BleUtil.java:218-227); caller base-classes10/com/govee/base2light/ai4edit/BleIotOp.java:181
- **`build_ai_switch`** (W): 33 A9 04 01 [on]
  - base-classes10/com/govee/base2light/ai4edit/controller/Controller4AiSwitch.java:30-31,47-54 (getNextCommBytes BleUtils.p(..,[4,1,on])); caller base-classes10/com/govee/base2light/ai4edit/BleIotOp.java:260
- **`build_black_border_remove`** (W): 33 A9 0B 01 [on]
  - base-classes12/com/govee/base2light/videomode/cotroller/BlackBorderRemoveController.java:41-45,56-57,64-66; base-classes12/com/govee/base2light/videomode/newdetail/funtion/BlackBorderVmInterface.java:33
- **`build_black_screen`** (W): 33 A9 0A 06 [enable][1 short|2 long][short u16le][long u16le]
  - base-classes10/com/govee/base2light/balckscreen/controller/BlackScreenController.java:24-28,40-44,59-71 (r()); callers base-classes10/com/govee/base2light/balckscreen/data/BlackScreenViewModel.java:137; AbsVideoNewDetailVm.java:233
- **`build_black_screen_hdmi`** (W): 33 A9 0C 03 [enable][duration u16le]
  - base-classes10/com/govee/base2light/balckscreen/hdmi/controller/BlackScreenHdmiController.java:37-41,51-59,69-70; callers base-classes10/com/govee/base2light/balckscreen/hdmi/data/BlackScreenHdmiViewModel.java:130; AbsVideoNewDetailVm.java:236
- **`build_hdmi_port`** (W): 33 A9 03 01 [hdmi_port]
  - base-classes13/com/govee/eco/executeAction/parser/HdmiActionParser.java:116-117 (Controller4ExtBytes.f(-87,[3,1,port]))
- **`build_hdr_cali`** (W): 33 A9 0D (no data)
  - base-classes12/com/govee/base2light/videomode/newdetail/controller/HDRCaliController.java:34-35 (b=[13] write); base-classes12/com/govee/base2light/videomode/newdetail/funtion/HDRVmInterface.java:102-103
- **`build_video_auto_wb`** (W): 33 A9 12 01 [value]
  - base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoAutoWbController.java:35-36 (b=[18,1,i]); caller base-classes12/com/govee/base2light/videomode/newdetail/view/whiteBalance/autowb/AutoCaliWbVm.java:272 (wrapped in CmdPtReal, sent over IoT)
- **`build_video_auto_wb_old`** (W): 33 A9 12 (no data)
  - base-classes12/com/govee/base2light/videomode/newdetail/funtion/WhiteBalanceVmInterface.java:72-73,111-112 (updateWhiteBalanceAutoOld); caller base-classes12/com/govee/base2light/videomode/newdetail/funtion/WhiteBalanceInterface.java:54
- **`build_video_brightness`** (W): 33 A9 02 01 [brightness]
  - base-classes12/com/govee/base2light/videomode/newdetail/funtion/WholeRLBrightnessVmInterface.java:32; snapshot AbsVideoNewDetailVm.java:218
- **`build_video_color_cali`** (W): 33 A9 05 [3|4] [c0][c1][c2]([c3])
  - base-classes12/com/govee/base2light/videomode/newdetail/controller/ColorCaliController.java:30-37 (a=[5,3,..], b=[5,4,..]); base-classes12/com/govee/base2light/videomode/newdetail/funtion/ColorCaliVmInterface.java:32-34 (4-value form when supportRedColor(), default true :37-39)
- **`build_video_hdr_effect`** (W): 33 A9 11 02 [on][value]
  - base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoHdrEffectController.java:31-33 ([17,2,bean.b()=on, bean.a()=value]; HDREffectBean.java:17-23); base-classes12/com/govee/base2light/videomode/newdetail/funtion/HDRContrastVmInterface.java:43
- **`build_video_saturation`** (W): 33 A9 0E 01 [saturation]
  - base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoSaturationController.java:29-30; base-classes12/com/govee/base2light/videomode/newdetail/funtion/SaturationVmInterface.java:32
- **`build_video_sensitivity`** (W): 33 A9 01 01 [sensitivity]
  - base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoSensitivityController.java:29-30 (a=[1,1,i]); base-classes12/com/govee/base2light/videomode/newdetail/funtion/SensitivityVmInterface.java:32
- **`build_video_wb_cali_exit`** (W): 33 A9 14 (no data)
  - base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoAutoWbController.java:29-30,46-47 (a=[20], makeExitCaliController); caller base-classes12/com/govee/base2light/videomode/newdetail/view/whiteBalance/autowb/AutoCaliWbVm.java:290 (CmdPtReal over IoT)
- **`build_video_white_balance`** (W): 33 A9 00 03 [manual: 0 auto/1 manual][r][b]
  - base-classes12/com/govee/base2light/videomode/newdetail/funtion/WhiteBalanceVmInterface.java:77-79 (getWhiteBalanceControllerNormal, non-infinite branch; Controller4ExtBytes.e write); snapshot base-classes12/com/govee/base2light/videomode/newdetail/videomodel/AbsVideoNewDetailVm.java:200,210
- **`build_video_white_balance_advanced`** (W): 33 A9 0F 03 [adv_r][adv_g][adv_b]
  - base-classes12/com/govee/base2light/videomode/newdetail/funtion/WhiteBalanceVmInterface.java:66-68,104; snapshot AbsVideoNewDetailVm.java:205
- **`build_video_white_balance_v1`** (W): 33 A9 06 01 [progress]
  - base-classes12/com/govee/base2light/videomode/newdetail/funtion/WhiteBalanceVmInterface.java:79 (supportWhiteBalanceInfiniteProgress branch [-87,6,1,h]); same bytes base-classes10/com/govee/base2light/ble/controller/compose/Controller4WhiteBalance.java:31-32 (no reference outside its file)
- **`read_ai_filter`** (R): AA A9 10
  - base-classes12/com/govee/base2light/videomode/newdetail/controller/AiFilterController.java:66-67 (c=[16]; no caller in decomp)
- **`read_black_border_remove`** (R): AA A9 0B
  - base-classes12/com/govee/base2light/videomode/cotroller/BlackBorderRemoveController.java:36-39,52-53 (read ctor; no caller in decomp)
- **`read_black_screen`** (R): AA A9 0A
  - base-classes10/com/govee/base2light/balckscreen/controller/BlackScreenController.java:19-21,46-56 (q()=[10]; reply matched on byte2==0x0A); caller base-classes10/com/govee/base2light/balckscreen/data/BlackScreenViewModel.java:121
- **`read_black_screen_hdmi`** (R): AA A9 0C
  - base-classes10/com/govee/base2light/balckscreen/hdmi/controller/BlackScreenHdmiController.java:32-34,47-48; caller base-classes10/com/govee/base2light/balckscreen/hdmi/data/BlackScreenHdmiViewModel.java:116
- **`read_hdr_cali`** (R): AA A9 0D
  - base-classes12/com/govee/base2light/videomode/newdetail/controller/HDRCaliController.java:28-29 (a=[13] read); base-classes12/com/govee/base2light/videomode/newdetail/funtion/HDRVmInterface.java:106-120 (onHDRCaliSuc)
- **`read_video_brightness`** (R): AA A9 02
  - base-classes10/com/govee/base2light/ble/controller/VideoModeParamsController.java:65-66,107-110 (ParamsType.brightness p()=[2]; uncalled)
- **`read_video_check_wb_cali`** (R): AA A9 13
  - base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoCheckWbCaliController.java:30-31 (a=[19] read; no caller in decomp)
- **`read_video_color_cali`** (R): AA A9 05
  - base-classes12/com/govee/base2light/videomode/newdetail/controller/ColorCaliController.java:49-50 (d=[5] read; no caller in decomp)
- **`read_video_hdr_effect`** (R): AA A9 11
  - base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoHdrEffectController.java:45-46 (c=[17]; no caller in decomp)
- **`read_video_saturation`** (R): AA A9 0E
  - base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoSaturationController.java:41-42 (c=[14]; no caller in decomp)
- **`read_video_sensitivity`** (R): AA A9 01
  - base-classes12/com/govee/base2light/videomode/newdetail/controller/VideoSensitivityController.java:41-42 (c=[1] read; no caller in decomp); base-classes10/com/govee/base2light/ble/controller/VideoModeParamsController.java:63-64,107-110 (lingmindu p()=[1]; uncalled)
- **`read_video_white_balance`** (R): AA A9 00
  - base-classes10/com/govee/base2light/ble/controller/VideoModeParamsController.java:54-61,107-110 (VideoModeParamsController(ParamsType.whiteBalance).p()=[0]; no constructor call in decomp)
- **`read_video_white_balance_v1`** (R): AA A9 06
  - base-classes10/com/govee/base2light/ble/controller/VideoModeParamsController.java:67-68,107-110 (ParamsType.whiteBalanceV1 p()=[6]; uncalled)

### `0xaa` — source-modeled

- **`build_check_light_install`** (W): 33 AA (empty payload) -> 33 AA 00*17 99; ack frame[2]==0
  - base-classes10/com/govee/base2light/ble/controller/compose/CheckLightInstallController.java:10-12 (super(true), only ctor), :14-17 (getCommandType=-86); base-classes10/com/govee/base2light/ble/controller/compose/AbsController.java:17-19,26-30 (getNextCommBytes -> BleUtils.p(pro, cmd, null))

### `0xab` — firmware-confirmed

- **`build_ota_pact_report`** (W): AB 02 [pactType u16le][pactCode] 00x14 [XOR@19]
  - base-classes10/com/govee/base2light/ble/ota/auto/ReportOtaPactController.java:21-25 (ctor(pactCode, pactType): f=pactCode, g=pactType), :37-39 (cmd 2), :41-44 (getProType -85), :46-49 (isSameController: any 0xAB frame), :65-68 (q = [g lo, g hi, f]); base-classes10/com/govee/base2light/ble/ota/auto/ReportOtaPactComposeController.java:18-22 (ctor(pactType, pactCode)), :29-53 (cmd 2, getNextCommBytes = BleUtils.p, proType -85, q = [i lo, i hi, j])
- **`build_plug_ota_pact_report`** (W): AB 02 [pact_type u16le @2-3][pact_code u8 @4] 00x14 [XOR of bytes 0..18 @19]. The values are the APP's highest registered pact for the goodsType (Pact.d.c), not
  - base-classes10/com/govee/base2light/ble/ota/auto/ReportOtaPactController.java:21-25 (ReportOtaPactController.<init>: f=arg0, g=arg1, super(true)); base-classes10/com/govee/base2light/ble/ota/auto/ReportOtaPactController.java:37-49 (ReportOtaPactController.getCommandType=2 / getProType=-85 / isSameController)
- **`read_dynamic_api_support`** (R): AA AB 00x17 01. It is a read: super(false) gives proType 0xAA, and p() returns null, so there is no selector.
  - base-classes10/com/govee/base2light/ble/controller/DynamicApiSupportController.java:7-9 (DynamicApiSupportController.<init>: super(false)); base-classes10/com/govee/base2light/ble/controller/DynamicApiSupportController.java:17-19 (DynamicApiSupportController.getCommandType = -85)
- **`read_wifi_func_list`** (R): AB 03 00x17 A8. proType 0xAB comes from AbsController4WifiSingleSendMultiBack.getProType. The request payload is a = null (super(null, 1, null)). a()=13 and k()
  - base-classes10/com/govee/base2light/ble/controller/Controller4ReadWifiFuncList.java:66-68 (Controller4ReadWifiFuncList.<init>: super(null,1,null)); base-classes10/com/govee/base2light/ble/controller/Controller4ReadWifiFuncList.java:81-84 (Controller4ReadWifiFuncList.getCommandType = 3)

### `0xac` — source-modeled

- **`build_pick_mic`** (W): 33 AC [pick]
  - base-classes12/com/govee/base2light/videomode/newdetail/funtion/SoundEffectVmInterface.java:38-45 (updatePickMic -> Controller4ExtBytes.e([-84, i]))
- **`read_bluetooth_name`** (R): Request, 20 bytes: [0] 0xAC; [1] 0x06; [2..18] 0x00; [19] XOR = 0xAA. Two senders produce the same frame. Controller4ReadBluetoothName goes through AbsControlle
  - base-classes11/com/govee/base2light/kt/general_controller/Controller4ReadBluetoothName.java:45-51 (Controller4ReadBluetoothName.<init> / getCommandType = 6); base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:157-165 (getNextCommBytes / getProType)
- **`read_combo_state`** (R): Request, 20 bytes: [0] proType 0xAC (AbsSingleReadMultiReplyController.java:17-20); [1] command 0x07 (Controller4ReadComboState.java:211-213); [2..18] ext h cop
  - base-classes11/com/govee/base2light/kt/general_controller/Controller4ReadComboState.java:131-142 (Companion.makeReadController / Companion.a: ext null by default); base-classes11/com/govee/base2light/kt/general_controller/Controller4ReadComboState.java:201-218 (Controller4ReadComboState.<init> / getCommandType = 7 / p = h)
- **`read_cube_device_self`** (R): AC 02 00 00.. [BCC 0xAE]
  - split_h6061/com/govee/cubelight/ble/DeviceSelfController.java:25-27 (getCommandType = 2), :29-32 (p = [0]), :34-37 (parseValidBytes passes the buffer to the listener); base-classes10/com/govee/base2light/ble/controller/AbsSingleReadMultiReplyController.java:17-20 (proType 0xAC)
- **`read_detail_info`** (R): Request, 20 bytes: [0] proType 0xAC (AbsController4BleSingleSendMultiBack.java:162-165 getProType = -84); [1] command 0x03 (Controller4DetailInfo.java:104-106);
  - base-classes11/com/govee/base2light/kt/general_controller/Controller4DetailInfo.java:96-98 (Controller4DetailInfo.<init>: super((byte[]) null)); base-classes11/com/govee/base2light/kt/general_controller/Controller4DetailInfo.java:104-106 (Controller4DetailInfo.getCommandType = 3)
- **`read_h5086_recent_hour_chart`** (R): AC 01 0D 00.. [BCC] (ext = TLV type 0x0D)
  - split_plugnew/com/govee/h5086/ble/controller/RecentOneHourChartController.java:65-74 (Companion.d: ext [13]), :150-152 (d() = 13), :154-156 (getCommandType = 1), :77-141 (Companion.f/g parse); base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:157-165 (getNextCommBytes = BleUtils.p(0xAC, cmd, ext)), :100-149 (first frame [AC 00 packs u16le lastLen .. cmd d()] data @7), :175-225 (middle/last)
- **`read_h5086_timers`** (R): AC 01 13 00.. [BCC] (ext = TLV type 0x13)
  - split_plugnew/com/govee/h5086/ble/controller/TimerReadController.java:37-47 (Companion.b: ext [19]), :92-94 (d() = 19), :96-98 (getCommandType = 1), :49-83 (Companion.d/e parse); base-classes10/com/govee/base2light/ble/controller/AbsController4BleSingleSendMultiBack.java:100-165,175-225
- **`read_h6047_sound_way`** (R): AA AC (no selector)
  - split_h6047/com/govee/h6047/detail/NewDetailVm$connectBleSuc$1.java:45-49,152-159 (Controller4ExtBytes.c([-84]) when Support.isGoodsTypeH6047; callback l)
- **`read_sound_pickup`** (R): AA AC 00 x17 [BCC=06]: no selector
  - split_h6047/com/govee/h6047/detail/NewDetailVm$connectBleSuc$1.java:152-159 (NewDetailVm$connectBleSuc$1: Support.isGoodsTypeH6047 gate, Controller4ExtBytes.Companion.c([0xAC])); split_h6047/com/govee/h6047/detail/NewDetailVm$connectBleSuc$1.java:45-49 (NewDetailVm$connectBleSuc$1.l: Info4Detail.A0(reply byte 0), then a5)
- **`read_status_color_h667172`** (R): AC 03 [count=03][types 40 41 A5]
  - split_rgbiclight/com/govee/rgbiclight/h667172/VM4LightH667172$afterConnectedSingleReadDeviceInfo$1.java:160 (invokeSuspend: {BleProtocol.SINGLE_READ_IC=64, 65, -91}), :91-133 (o: TLV callback; 0x41 at :107-109; 0xA5 at :117-131), :135-144 (p -> Info4DetailH667172.r().j); split_rgbiclight/com/govee/rgbiclight/h667172/VM4LightH667172.java:112-117 (R4 launches the coroutine)
- **`read_status_color_h703ab`** (R): AC 03 [count=04][types 41 A5 40 16] (0x41 power-off memory, 0xA5 colour, 0x40 IC, 0x16 direction)
  - split_h61d3/com/govee/h61d3/adjustNew/VM4Light$afterConnectedSingleReadDeviceInfo$1.java:169 (invokeSuspend: types [65, BleProtocol.j = 0xA5, 64, 22]); split_h61d3/com/govee/h61d3/adjustNew/VM4Light$afterConnectedSingleReadDeviceInfo$1.java:100-124 (n: TLV callback, 0xA5 branch :114-124)
- **`read_status_color_rgbic_351`** (R): AC 03 [count=02][types 40 A5]
  - split_rgbiclight/com/govee/rgbiclight/VM4Light.java:720-726 (D5: [SINGLE_READ_IC, 0xA5] only for gt 351/352); split_rgbiclight/com/govee/rgbiclight/VM4Light.java:1852-1871 (x5 -> Compose4BaseInfoSingleRead.Companion.b)
- **`read_status_h1771`** (R): AC 03 [04][41 36 A3 A5]
  - split_hollowlamp/com/govee/hollowlamp/newdetails/h1771/H1771DetailVM.java:675-677 (d0 -> S4, no gate), :146-152 (S4); split_hollowlamp/com/govee/hollowlamp/newdetails/h1771/H1771DetailVM$afterConnectedSingleReadDeviceInfo$1.java:180-212 (types [hollowlamp BleProtocol.e = 65, 54, straightfloorlamp SINGLE_GRADUAL_CHANGE_4_WIFI_BLE = 0xA3, 0xA5]; companion.c with the U4 daysync list at :208)
- **`read_status_h61d3_263`** (R): AC 03 [04][41 A5 40 16]
  - split_h61d3/com/govee/h61d3/adjustNew/VM4Light.java:1065-1074 (d0: H61D3Support.l0 -> Y4, else connectBleSuc), :458-463 (Y4); split_h61d3/com/govee/h61d3/pact/H61D3Support.java:1340-1342 (l0: goodsType == 263)
- **`read_status_straightfloorlamp`** (R): AC 03 [02][A5 A3]
  - split_hollowlamp/com/govee/straightfloorlamp/adjust/VM4Light.java:816-827 (d0 gate) -> :290-296 (Z4); split_hollowlamp/com/govee/straightfloorlamp/pact/Support.java:171-173 (isH6076Iteration: goodsType == 69 && pactType == 2; pactType = Info4BleIotDevice.F() base-classes11/com/govee/base2light/kt/comm/Info4BleIotDevice.java:216-218)

### `0xae` — source-modeled

- **`read_relative_brightness`** (R): AA AE 01
  - base-classes11/com/govee/base2light/relativebrightness/controller/RelativeBrightnessController.java:61-63,76-78,112-115 (read ctor; q() = [1] when not write)

### `0xaf` — source-modeled

- **`build_exit_check_light_install`** (W): 33 AF (empty payload) -> 33 AF 00*17 9C; ack frame[2]==0
  - base-classes10/com/govee/base2light/ble/controller/compose/ExitCheckingInstallController.java:10-12 (super(true)), :14-17 (getCommandType=-81); base-classes10/com/govee/base2light/ble/controller/compose/AbsController.java:26-30 (getNextCommBytes)
- **`build_exit_checking_install`** (W): 33 AF [no payload]
  - base-classes10/com/govee/base2light/ble/controller/compose/ExitCheckingInstallController.java:10-17 (super(true); getCommandType 0xAF (-81)); base-classes10/com/govee/base2light/ble/controller/compose/AbsController.java:28-29 (BleUtils.p(pro, cmd, i))

### `0xb0` — firmware-confirmed

- **`build_plug_delay`** (W): 33 B0 [outlet u8][delay_type u8: 0 = delay-off/sleep, 1 = delay-on/wake-up][minutes/60 u8][minutes%60 u8]
  - split_plugnew/com/govee/h5080/ble/controller/TurnOnOffDelayController.java:22-28 (TurnOnOffDelayController.<init>(outlet, minutes, type): write); split_plugnew/com/govee/h5080/ble/controller/TurnOnOffDelayController.java:86-88 (TurnOnOffDelayController.getCommandType = -80 = 0xB0)
- **`build_plug_pro_delay`** (W): 33 B0 [delay_type u8: 0=delay-off,1=delay-on][hours u8][minutes u8]
  - split_plugnew/com/govee/h5086/ble/controller/DelayOnOffController.java:71-79,88-90 (DelayOnOffController.Companion.g (off) / h (on) / getCommandType); write at split_plugnew/com/govee/h5086/adjust/SmartPlugProAdjustViewModel.java:1105-1114 (X4: flag true -> h)
- **`read_plug_delay`** (R): AA B0 [outlet u8][delay_type u8]
  - split_plugnew/com/govee/h5080/ble/controller/TurnOnOffDelayController.java:16-20,90-102 (read <init>/p/parseValidBytes); reads per outlet for type 0 and 1 at split_plugnew/com/govee/h5080/adjust/v1/BleOpV1.java:151-152, adjust/v2/BleOpV2.java:153-154, adjust/v3/BleOpV3.java:137-138, adjust/h5089/BleOp4H5089.java:156-157,178-179
- **`read_plug_pro_delay`** (R): AA B0 [delay_type u8: 0=delay-off,1=delay-on]
  - split_plugnew/com/govee/h5086/ble/controller/DelayOnOffController.java:47-69 (DelayOnOffController.Companion.c (off) / e (on)); issued at split_plugnew/com/govee/h5086/ble/controller/ReadInfoController.java:102-112

### `0xb1` — firmware-confirmed

- **`build_secret_write`** (W): 20 bytes: [0]=0x33 [1]=0xB2 [2..9]=secret = Encode.decryByBase64(secretCode) (normally 8 bytes; empty when secretCode is "" or invalid base64 -> 33 B2 00.. XOR 
  - base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:22-26 (SecretKeyController.<init>(String): write, SINGLE_CHECK_SECRET_KEY; empty -> ""); base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:71-84 (SecretKeyController.q = Encode.decryByBase64; r returns writeSuc)
- **`read_secret`** (R): 20 bytes: [0]=0xAA [1]=0xB1 [2..18]=0x00 (AbsSingleController.p() null; kt field i null) [19]=XOR = 0x1B. Identical for base SecretKeyController(byte -1), h61d3 
  - base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:13-20 (SecretKeyController.<init>(byte): -1 -> SINGLE_READ_SECRET_KEY); base-classes10/com/govee/base2light/ble/controller/SecretKeyController.java:46-49 (SecretKeyController.getCommandType)

### `0xb3` — firmware-confirmed

- **`read_plug_spec`** (R): AA B3, no selector; byte 19 = XOR = 0x19
  - split_plugnew/com/govee/h5080/ble/controller/SpecController.java:11-13 (SpecController.getCommandType = -77 = 0xB3); base-classes10/com/govee/base2light/ble/controller/AbsOnlyReadSingleController.java:5-7 (read-only, q() null)

### `0xb4` — source-modeled

- **`build_plug_pro_timer`** (W): 33 B4 [group u8, no outlet bit][enable_and_switch u8: bit7 enable, bit0 turn on][hour u8][minute u8][repeat u8]
  - split_plugnew/com/govee/h5086/ble/controller/TimerWriteController.java:23-27,36-38 (round-1: Companion.a, cmd -76); split_plugnew/com/govee/h5080/adjust/newdetail/H5083NewDetailViewModel.java:160-167 (D0: GMessageSingleWrite(0xB4, [group, enableAndSwitch, hour, min, repeat]) :162)
- **`build_plug_timer`** (W): 33 B4 [timer_index | 0x10 if outlet != 0][enable_and_type: bit7 enable, bit0 turn_on][hour u8][minute u8][repeat u8]
  - split_plugnew/com/govee/h5080/ble/controller/NewTimerController.java:23-28 (NewTimerController.<init>(index, outlet, timer): write); split_plugnew/com/govee/h5080/ble/controller/NewTimerController.java:63-65 (NewTimerController.getCommandType = -76 = 0xB4)
- **`read_plug_timers`** (R): AA B4 [1 if outlet == 1 else 0][FF]
  - split_plugnew/com/govee/h5080/ble/controller/NewTimerController.java:17-21 (NewTimerController.<init>(group, outlet): read); split_plugnew/com/govee/h5080/ble/controller/NewTimerController.java:67-69 (NewTimerController.p)

### `0xb5` — firmware-confirmed

- **`build_plug_sync_time`** (W): 33 B5 [unix_seconds u32be @2-5][0x01 @6][tz_hour s8 @7][tz_min s8 @8] 00x10 [XOR of bytes 0..18 @19]. unix_seconds = currentTimeMillis()/1000, taken when the co
  - split_plugnew/com/govee/h5080/ble/controller/SyncTimeController.java:14 (SyncTimeController.f = now/1000); split_plugnew/com/govee/h5080/ble/controller/SyncTimeController.java:24-26 (SyncTimeController.getCommandType = -75)

### `0xb8` — source-modeled

- **`build_stop_ai_preview`** (W): 33 B8 [01][00]
  - base-classes10/com/govee/base2light/ai4edit/controller/StopPreViewAiActionController.java:13-26 (StopPreViewAiActionController.<init>/getCommandType/getNextCommBytes); base-classes10/com/govee/base2light/ai4edit/AIEffectEditDialog.java:743-771 (AIEffectEditDialog.a0: BLE Compose4DefWrite :751, IoT CmdPtRealAi(getValue()) :764)
- **`build_stop_preview_ai_action`** (W): 33 B8 [01][00]
  - base-classes10/com/govee/base2light/ai4edit/controller/StopPreViewAiActionController.java:13-26 (super(true); getCommandType -72; getNextCommBytes [1, 0]); base-classes10/com/govee/base2light/ai4edit/AIEffectEditDialog.java:747

### `0xb9` — source-modeled

- **`read_device_graphics`** (R): AA B9 (no selector)
  - split_h6061/com/govee/cubelight/newdetails/CubeVM4Light$connectBleSuc$2.java:149-157 (Controller4ExtBytes.b([-71]) when Support.supportDeviceGraphics); split_h6061/com/govee/cubelight/pact/Support.java:784-785 (goodsType == 228)
- **`read_device_graphics_md5`** (R): AA B9 (no selector)
  - split_h6061/com/govee/cubelight/newdetails/CubeVM4Light$connectBleSuc$2.java:149-167 (Controller4ExtBytes.Companion.b([0xB9]) + ReadParserListener); base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:49-64,140-142 (Companion.b read, getCommandType)

### `0xba` — source-modeled

- **`build_pair_process_complete`** (W): 3A BA [02] (proType 0x3A, outside the 0x33/0xAA frame model)
  - base-classes4/com/govee/skubusiness/ext/PairProcessCompleteExtKt.java:21-47 (PairProcessCompleteExtKt.writePairProcessComplete: GMessageSingleReadWrite(0xBA, [02], parser) at :45); base-classes13/com/govee/ctlchannel/message/GMessageSingleReadWrite.java:16-37 (getSendBytes = BleUtils.Companion.p(0x3A, u, v))
- **`read_low_blue_light`** (R): AA BA [method 1 SCALE / 0 OTHER]
  - base-classes11/com/govee/base2light/lowBlueLightControl/ControllerLowBlueLight.java:50-56,66-69,91-97 (read ctor defaults SCALE; p [method]; u parse); split_rgbiclight/com/govee/dreamcolorlightv1/adjust/v2/UiV3.java:326

### `0xed` — source-modeled

- **`build_recognition_start`** (W): 33 ED 00 [value u16le] 00 x14 [XOR@19] (the app always sends value 280 = 18 01)
  - split_h61d3/com/govee/h61d3/ble/controller/VideoRecognitionController.java:29-33,36-42 (Companion.b: [0, D(i,false)]; super(true) -> 0x33; cmd BleProtocol.m = -19); split_h61d3/com/govee/h61d3/ble/BleProtocol.java:19
- **`build_recognition_stop`** (W): 33 ED 01 00 x16 [XOR@19]
  - split_h61d3/com/govee/h61d3/ble/controller/VideoRecognitionController.java:24-27,36-42 (Companion.a = [1]); split_h61d3/com/govee/h61d3/adjust/recognition/VideoActivity.java:425 (P0), :593
- **`build_video_recognition_effect`** (W): 33 ED 00 [value u16le]. The only caller sends 280 -> 33 ED 00 18 01.
  - split_h61d3/com/govee/h61d3/ble/controller/VideoRecognitionController.java:29-33 (VideoRecognitionController.Companion.b: [0, value u16le via D with its flag false]); base-classes10/com/govee/base2kt/utils/BleUtils.java:132-141 (BleUtils.Companion.D with its flag false: low byte first)
- **`build_video_recognition_stop`** (W): 33 ED 01; bytes 3..18 zero; byte 19 = XOR = 0xDF
  - split_h61d3/com/govee/h61d3/ble/controller/VideoRecognitionController.java:24-27 (VideoRecognitionController.Companion.a: payload [01]); split_h61d3/com/govee/h61d3/ble/controller/VideoRecognitionController.java:36-38 (VideoRecognitionController.<init>: super(true, payload) = write)

### `0xee` — source-modeled

- **`build_ota_prepare`** (W): 33 EE [no payload, bytes 2-18 = 00] [bcc=DD]
  - split_rgbiclight/com/govee/dreamcolorlightv1/ble/OtaPrepareController.java:11-17 (OtaPrepareController.getCommandType/q); sent at split_rgbiclight/com/govee/dreamcolorlightv1/adjust/v1/UiV3.java:1223 and adjust/v2/UiV3.java:2717; split_rgblight/com/govee/stringlightv2/ble/OtaPrepareController.java:11-17 (OtaPrepareController.getCommandType/q; BleProtocol.SINGLE_OTA_PREPARE=-18 at split_rgblight/com/govee/stringlightv2/ble/BleProtocol.java:6); sent at split_rgblight/com/govee/stringlightv2/adjust/v2/UiV3.java:1560

### `0xef` — source-modeled

- **`read_pact`** (R): AA EF (no selector)
  - base-classes10/com/govee/base2light/ble/controller/PactController.java:9-20,27-41 (u/v helpers; getCommandType SINGLE_PACT; parseValidBytes); base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:146 (SINGLE_PACT = -17)

### `0xf0` — source-modeled

- **`build_daysync_after_bind`** (W): 33 05 [sub_mode F0][op FF, meaning not named in code] zero-padded, XOR 0x39.
  - base-classes10/com/govee/base2light/connect/constant/DaySyncAfterConnectConfig.java:23-33 (DaySyncAfterConnectConfig.getBindAfterController: Controller4ExtBytes.Companion.f(5, [SUB_MODE_DAYSYNC, ff]), gated by DaySyncConfig.A); base-classes11/com/govee/base2light/kt/general_controller/Controller4ExtBytes.java:120-147 (write, proType 0x33)
- **`build_daysync_mode`** (W): 33 05 [F0][op]: op0 [00]; op1 [01 FF 01 1E 00] (enable 1) or [01 FF e]; op2/3 [op][scheme]; op4 [04][scheme][repeat]; other [F0] alone
  - base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:214-262 (DaySyncUtils.G); base-classes11/com/govee/base2light/kt/general_controller/ControllerMode.java:62-65,102-105 (command 5, write)
- **`build_daysync_refresh_city`** (W): 33 F0 (no payload)
  - base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncVM.java:3301 (refreshCity4Device: ControllerSingle.c(0xF0) with an empty payload); base-classes11/com/govee/base2light/kt/general_controller/ControllerSingle.java:37-41
- **`read_daysync_running_node`** (R): AA F0 (no selector)
  - base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncVM.java:312 (ControllerSingle.a(0xF0)); base-classes11/com/govee/base2light/kt/general_controller/ControllerSingle.java:26-29 (a: read)

### `0xf1` — source-modeled

- **`read_daysync_running`** (R): AA F1 (no selector)
  - base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncVM.java:301-304 (ControllerSingle.a(0xF1) when DaySyncConfig.y(info)[2]); base-classes10/com/govee/base2light/ble/controller/BleProtocolConstants.java:180 (SUB_MODE_DAYSYNC_ING = -15)

### Builders without a single primary opcode (multi-packet / value-layer)

- **`build_brightness`**: 33 04 [level u8] 00*16 [BCC]. level is the value's low 8 bits. The caller passes a value already in the SKU's brightness range (newdetail BrightnessConfig.Companion.b checks 
- **`build_check_ic`**: 33 46|5A [check_type][channel u8, omitted when < 0][icPos_hi][icPos_lo] (bare [op] frames = ic_pos 0, no channel, since padding is zero)
- **`build_combination_effect_value`**: Value bytes only (not a frame): [0x00][distinct effect count][segLen = 1 if n<=0 else ceil(n/8)] + per distinct effect: [segment mask, segLen bytes, bit s%8 of 
- **`build_daysync_scheme_upload`**: Frames are MTU-sized, not 20-byte. Per big package: START [A6][00 00][01][pkg_count][pkg_index, 1-based][verify 8B][frames_in_pkg u16le][63][data...][XOR of all
- **`build_logo`**: 33 A6 [01][brightness][R][G][B] 00.. BCC (manual colour) | 33 A6 [00] 00.. BCC (auto; nothing follows the 00, so brightness and colour are dropped)
- **`build_low_blue_light`**: 33 BA [SetMethod SCALE=1/OTHER=0][on 0/1][b2 (always 100)][b3 (always 100)][level] + zero pad + XOR at byte 19
- **`build_music_code_sens`**: 33 05 [sub_mode: 13 | 11 | 0C | 0E | 0F | 01] [code] [sens] [sub_effect0 if given], zero-padded, XOR at byte 19
- **`build_relative_brightness`**: 33 AE [01][n: 06 SEGMENT6 | 04 SEGMENT4][left][top][right][bottom][strip_left][strip_right] 00.. [XOR]. The 6 value bytes are always sent whatever n is. For SEG
- **`build_scene`**: 33 05 04 [effect u2le][music_code u2le, 0000 when the white-noise switch is off]
- **`build_scene_cube`**: 33 05 04 [effect u2le][direction u8][speed u8] | 33 05 04 [effect u2le][direction u8][x u2le][y u2le][speed u8] | 33 05 04 [effect u2le][direction u8][p0 p1 p2 
- **`build_scene_h61d3`**: 33 05 04 [effect u2le][dirA][dirB] ([N][speed_0..speed_N-1] only when a speed config is set)
- **`build_scene_with_speed`**: 33 05 04 [effect u2le][speed u8]
- **`build_screen_bind_success`**: 33 BA [01] 00*16 BCC
- **`build_status_request`**: [0xAC][cmd=0x03][N=len(types)][types...] zero-padded, XOR at byte 19. Single send; the device answers with a 0xAC multi-frame TLV burst. Example: H6038 AC 03 04
- **`build_switch`**: 33 01 [on u8: 0x01 on / 0x00 off] 00 x16 [BCC]
- **`build_sync_time`**: 33 09 [hour][minute][second][week 1=Mon..7=Sun][0x01][tz_hour s8][tz_min s8] 00 x10 [BCC@19]
- **`build_worry_free_at_night`**: 33 36 [open 1/0][open_h][open_m][close_h][close_m][sensitivity 1-100 (bean field 'progress')][brightness 1-100] .. 00 [bcc]
- **`read_bar_switch`**: 
- **`read_device_basic_info`**: AA 07 10 [00 x16] [bcc=BD]
- **`read_device_info`**: AA 07 [selector] 00x16 [BCC]; selector 0x0A = MCU soft version, 0x0B = MCU hard version (also 0x02 SN, 0x03 hard version, 0x07 DSP version, 0x10 basic info, 0x1
- **`read_device_wifi_info`**: AA 07 11 [00 x16] [bcc=BC]
- **`read_gradual`**: 
- **`read_ic_segment`**: AA 40 (no selector, payload null; bytes 2..18 zero, XOR at 19)
- **`read_mode`**: 
- **`read_soft_version`**: 
- **`read_switch`**: AA 01 [no selector] 00 x17 [BCC=AB]

## Constant-only opcodes (defined by the app, no builder emits them)

- `0x01` AA 01 heartbeat parsers with no caller (HeartControllerWithBattery, Controller4HeartByOld)
- `0x05/0x00` SINGLE_MODE video sub-mode 0x00, residual classes: newdetail SubMode4Video, H6047 SubModeVideo, and the devices.yaml video claims
- `0x05/0x00 (H6047 SubModeVideo)` H6047 video sub-mode 0x00: report identity only (legacy write defined but uncalled; live parser stores the sub byte only)
- `0x05/0x00 (newdetail SubMode4Video)` newdetail SubMode4Video: w() write and parse defined; the class is unreferenced in 7.7.02
- `0x05/0x01, 0x05/0x02, 0x05/0x03 (H7004 SubModeRedBlue41/61/81)` H7004 legacy red:blue ratio identities 4:1 / 6:1 / 8:1: constant classes, never written or parsed
- `0x05/0x01, 0x05/0x03 (bulblightv1 SubModeScenesV1 / SubModeDiy)` bulblightv1 (H6003/H6004, goodsTypes 9/10, Wi-Fi only) scene 0x01 and DIY 0x03 identities: empty BLE writes; scenes and DIY go over IoT
- `0x05/0x0c` dreamcolorlightv2 SubModeMusicMultiV11 and SubModeMusicOldV0 (sub 0x0C = music_old_v0, not 0x11): write layouts defined, no emitter in 7.7.02
- `0x05/0x11` dreamcolorlightv2 legacy music sub-mode 0x11 classes (SubModeMusic, SubModeMusicMultiV1): write layouts defined, no emitter in 7.7.02
- `0x05/0xFF (H6181 SubModeMic)` H6181 mic flag sub-mode (BleProtocol.sub_mode_mic_flag = -1): local constant, never written and never decoded
- `0x05/0xff` H6181 sub_mode_mic_flag (-1): SubModeMic with a null write
- `0x0A` base BleProtocolConstants.SINGLE_DEVICE_INFO_MCU_SOFT_VERSION (r3-122)
- `0x0A` h5080 BleConstants.SINGLE_TEM_HUM (r3-121)
- `0x0B` base BleProtocolConstants.SINGLE_DEVICE_INFO_MCU_HARD_VERSION / VALUE_MCU_HARD_VERSION (r3-122)
- `0x0F` SINGLE_LIGHT_NUM: barelightv1 light_num claim (r3-068); also the h612526 light_num row
- `0x0a` Leftover 0x0A constant names: h5080 SINGLE_TEM_HUM, the DIY sub-mode value (sub_mode_new_diy, h61d3 BleProtocol.h), and the base/base_h71xx MCU soft-version selector. None of them emits a 0x0A command. The real 0x0A command (SINGLE_AUTO_TIME / AutoTimeController) is covered in round 1.
- `0x0b` Leftover 0x0B constant names: base SINGLE_DEVICE_INFO_MCU_HARD_VERSION / VALUE_MCU_HARD_VERSION, base_h71xx r0, and the colour sub-mode value (sub_mode_color = 11). None of them emits a 0x0B command. The real 0x0B command (SINGLE_DELAY_CLOSE / DelayCloseController) is covered in round 1.
- `0x0f` barelightv1 SINGLE_LIGHT_NUM = 15 is constant_only, with no emitter on the goodsType-13 path. Also checks the devices.yaml barelightv1 opcode claims.
- `0x11` Plug-family 0x11 collisions (h5080 SINGLE_DATE_RESET constant; H5086 ControllerProtocol.g and base_h71xx u0 are 0x07 Wi-Fi-info selectors) and the live EE 11 Wi-Fi connect notify
- `0x11` h5080 BleConstants.SINGLE_DATE_RESET (r3-121)
- `0x16` NotDisturbControllerV1 (base_h71xx 6-byte form with a forever flag): dead code
- `0x24` base BleProtocolConstants.SINGLE_GET_AUTO_OFF (r3-123)
- `0x35` Constant aliases only: H6047 MSG_TYPE_READ_START_TIME; base SINGLE_WITHOUT_INTERRUPT (dead WithoutInterruptController); INTERACTIVE_STATUS_NOTIFY (EE 35, unregistered parsers)
- `0x35` MSG_TYPE_READ_START_TIME (H6047)
- `0x36` Constant aliases only: H7090 value_compose_light_switch; base SINGLE_WORRY_FREE_AT_NIGHT; dead kt ControllerWorryFreeAtNight and h6057 WorryFreeAtNightController
- `0x36` kt ControllerWorryFreeAtNight (dead shared controller)
- `0x36` value_compose_light_switch (H7090)
- `0x37` Constant aliases only: H6047 value_check_usb_state; base SINGLE_PROMPT_TONE (dead h6057 PromptToneController)
- `0x37` value_check_usb_state (H6047)
- `0x38` Constant aliases only: H6047 value_check_usb; base SINGLE_GUIDE_LIGHT / PROTOCOL_INIT_LIGHT (dead InitLightController, GuideLightController)
- `0x38` value_check_usb (H6047)
- `0x41` SINGLE_GET_DEVICE_INFO / feast value_setting_device as the default command of kt ControllerSingGetDeviceInfo (dead must-retry read)
- `0x41` kt ControllerSingGetDeviceInfo (dead shared read controller)
- `0x42` SINGLE_ON_OFF_MEMORY_H70BX
- `0x42` h61d3 BleProtocol.q (r3-124)
- `0x42` h61d3 BleProtocol.q = 66 (the refresh/check-IC slot) is constant_only. Its getter and setter are never called.
- `0x43` striplight BleProtocol.SINGLE_BRIGHTNESS_4_PIECE / cubelight BleProtocol.value_direction_point (r3-125)
- `0x43` striplight SINGLE_BRIGHTNESS_4_PIECE = 67 and cubelight value_direction_point = 67 are constant_only. Neither is CHECK_IC_AMOUNT 0x43.
- `0x48` PART_SCENES_SEGMENT_IC_INFO (kt ControllerPartScenesInfo, uncalled)
- `0x50` h6047 BleProtocol.value_usb_check_overtime (80); constant only
- `0x50` value_usb_check_overtime (H6047)
- `0x61` (none) MULTI_CAROUSEL_V2
- `0x61` MULTI_CAROUSEL_V2 / feast music_feast_brightness
- `0x61` MUSIC_FEAST_V2 music_feast_brightness (feast.BleProtocol = 97) / MULTI_CAROUSEL_V2 (= 97)
- `0x61` music_feast_brightness = 97, feast v2 constant (base-classes11/com/govee/base2light/pact/feast/BleProtocol.java:27). No BLE feast emitter. Its only reference is the op byte 0x61 passed to the Wi-Fi cloud pt-url GetIotCmdPtUrlLinkInfo.getLongCmdPtUrlV4 for carousel protocolVersion 2 (base-classes11/com/govee/base2light/pact/newdetail/content/carousel/CarouselViewModel.java:593-602; base-classes11/com/govee/base2light/kt/iot/GetIotCmdPtUrlLinkInfo.java:1142-1148).
- `0x62` MUSIC_FEAST_V2 value_switch (feast.BleProtocol = 98); feast meaning only
- `0x62` value_switch = 98, feast v2 constant (base-classes11/com/govee/base2light/pact/feast/BleProtocol.java:39), zero references. The only 0x62 command in the app is DiyAdvancedCombParse.getProtocolCode = 98 (base-classes10/com/govee/base2light/ac/diyNew/views/combination/DiyAdvancedCombParse.java:79,164-166), the separate r3-095 combination-effect emitter.
- `0x63` MUSIC_FEAST_V2 value_music_mode (feast.BleProtocol = 99); collides with DAYSYNC_OP
- `0x63` value_music_mode = 99, feast v2 constant (base-classes11/com/govee/base2light/pact/feast/BleProtocol.java:35). No feast emitter. Every reference is a DaySyncUtils daysync comm-0x63 upload (base-classes11/com/govee/base2light/pact/newdetail/content/daySync/DaySyncUtils.java:581,679,840,849,1394,1398,1402; feast BleProtocol imported at :36), which is the separate r3-096 emitter.
- `0x64` MUSIC_FEAST_V2 value_device_num (feast.BleProtocol = 100); collides with DAYSYNC_LENGTH_CHECK_OP
- `0x64` value_device_num = 100, feast v2 constant (base-classes11/com/govee/base2light/pact/feast/BleProtocol.java:33). No feast emitter. Its references are DaySyncUtils.java:167 (daysync check-space comm 0x64, r3-096) and payload value 100 at base-classes11/com/govee/base2light/mode/music/config/H608aMusicConfig.java:78 and base-classes11/com/govee/base2light/kt/general_controller/ControllerEyeshadow.java:106 (payload bytes 2-3). None is a command.
- `0x66` MUSIC_FEAST_V2 value_clear_sub_device_v2 (feast.BleProtocol = 102)
- `0x66` value_clear_sub_device_v2 = 102, feast v2 constant (base-classes11/com/govee/base2light/pact/feast/BleProtocol.java:29), zero references. No feast getCommandType or A3 comm uses 102; the 'return 102' hits are music codes, for example base-classes10/com/govee/base2light/ble/music/h70b1/H70b1MusicIllusoryColors.java:11-13.
- `0x70` MUSIC_LIB_NOTIFY
- `0x70` SINGLE_MUSIC_LIB_NOTIFY (r3-102)
- `0x72` MUSIC_LIB_READ_PRESET_INFO
- `0x72` SINGLE_MUSIC_LIB_READ_PRESET_INFO (r3-102)
- `0x73` MUSIC_LIB_READ_PRESET_COLOR
- `0x73` SINGLE_MUSIC_LIB_READ_PRESET_COLOR (r3-102)
- `0x74` MUSIC_LIB_READ_PRESET_SCENES
- `0x74` SINGLE_MUSIC_LIB_READ_PRESET_SCENES (r3-102)
- `0x75` MUSIC_LIB_SET_PLAY_STATE
- `0x75` SINGLE_MUSIC_LIB_SET_PLAY_STATE (r3-102)
- `0x76` MUSIC_LIB_SET_PREVIEW_EFFECT
- `0x76` SINGLE_MUSIC_LIB_SET_PREVIEW_EFFECT (r3-102)
- `0x77` MUSIC_LIB_REMOVE_PRESET_SCENES
- `0x77` SINGLE_MUSIC_LIB_REMOVE_PRESET_SCENES (r3-102)
- `0x78` MUSIC_LIB_SORT_PRESET_SCENES
- `0x78` SINGLE_MUSIC_LIB_SORT_PRESET_SCENES (r3-102)
- `0x7A` SINGLE_MUSIC_LIB_SET_SWITCH (r3-102)
- `0x7a` MUSIC_LIB_SET_SWITCH
- `0x90` MIC_MODE_CHECK_SUPPORT (MicBleProtocol.mic_mode_check_support = -112): local identity of MicColorCmdController, never sent on the wire
- `0xA3` SINGLE_GRADUAL_CHANGE_4_WIFI_BLE = -93 package constants of h6047, barelightv1 and h6102. Declaration only, never referenced: split_h6047/com/govee/h6047/ble/BleProtocol.java:9; split_rgbiclight/com/govee/barelightv1/ble/BleProtocol.java:7; split_rgbiclight/com/govee/h6102/ble/BleProtocol.java:5. A repo-wide grep -w finds the name in these packages only at the declarations. The packages reach 0xA3 only through the shared base2light emitters (next entry).
- `0xA3 V1 comm 0x41 (base MultiMusicController)` base2light MultiMusicController (MULTI_V1_NEW_MUSIC)
- `0xA3 V1 comm 0x5D` H61D3_LIGHT_RECOGNITION (LightRecognitionMultiController) - unreachable in 7.7.02
- `0xA3 comm 0x0B` H6057 MultiPreSetColorController: 0xA3 V1 multi-packet with comm 0x0B, never instantiated (split_hollowlamp/com/govee/h6057/origin/ble/MultiPreSetColorController.java:12-16,22-28,36-38; a repo-wide grep -w finds the class only in its own file)
- `0xEE` SINGLE_OTA_PREPARE constants in h7090 / barelightv1 / dreamcolorlightv2 (r3-126)
- `0xEF` cubelight BleProtocol.value_protocol_version (cited as :16 in r3-125)
- `0xEF` cubelight value_protocol_version = -17 (split_h6061/com/govee/cubelight/ble/BleProtocol.java:16), declaration only. Repo-wide grep -w finds no reference, split_h6061 has no other -17, and split_h6061 uses none of PactController, AbsBlePact, AbsFrameBle(Iot), BlePactChecker or the uncalled kt ControllerProtocol. The cubelight control screen (CubeAcLightDetail, split_h6061/com/govee/cubelight/pact/Model.java:95-98; read strategy split_h6061/com/govee/cubelight/newdetails/CubeNewDetailsConfig$makeControllerStrategy$1.java:149-225) sends no AA EF.
- `0xaa` CHECK_LIGHT install (command byte; collides with 0xAA read proType) / H5086 EE AA read-all notify
- `0xaf` EXIT_CHECK_LIGHT install
- `0xef` cubelight BleProtocol.value_protocol_version (-17); constant only, separate from the generic SINGLE_PACT read
- `cross-unit (AC framing, build_status_request, library tests)` Consolidator notes affecting round-2 rows and the library
- `spec command 0x22` wifi_dsp_version
- `spec command 0x48` part_scenes_info
- `spec command 0xaf` exit_checking_install
- `spec sub_mode 0xFF` mic_h6181 (sub_mode 0xFF, identity only)
- `proType 0xA5 read (AbsMicController [A5 02 cmd sum8])` AbsMicController 0xA5 read form (dead in 7.7.02) and 0xA5 reply acceptance

## Known gaps / not yet covered (26)

From the final completeness critic. These are in-scope for lighting/plug device classes but not yet built or fully verified; most are rare sub-modes, device-specific overloads, or families whose controllers live in splits not present in this decompile.

- **proType 0xE7 (AES / AES-GCM session handshake) and the encrypted wire framing**: There is no builder or record in round 2 or round 3. The round-2 audit says so itself: 'Not re-audited: the e7 handshake (Controller4Aes)'. Still undocumented: (1) the v1 request frame [E7][op 01 requ
- **0x05/0x04 scene activate on SceneViewMode subclasses that override k3/U2**: build_scene_newdetail covers only the base BaseSceneViewMode Z2/k3 path and H6057. Two control-screen overrides confirmed to write different bodies have no builder. (a) Cube: CubeSceneViewMode.k3 send
- **proType 0xA1 comType 0x01 (H7002/H7005/H7022 preset-scene parameter multi-packet)**: On the control screen a scene apply is DiyControllerNoEventV0Old(ScenesModeController) followed by 33 05 09. Only the 33 05 09 frame is built (build_string_scene_09); round 2 notes that the multi-pack
- **0x05/0x04 local-scene SKUs (H6188, H6182 Telink, H6159/stringlightv2, car light)**: The rgblight LocalScenesConfig interceptors choose fixed local scene lists. For example, H6188 goes through H6188Config.d to Scenes4H6188.g = {4,5,13,23,24,9,16,7,17}. No round-2 or round-3 builder ci
- **0xA3 V1 comm 0x40 strip apply vs single-frame 33 05 15 (colour-path selection for the library SKUs)**: Round 3's own notes leave the library SKUs unresolved. (1) Which path H60A6 takes (colour strip, colour-temperature strip or gradient strip, the n/l/m paths) depends on k2/M0/Y1, and those values were
- **0x05/0x15/0x01 and 0x05/0x15/0x02 segment masks above 16 positions; mic colour payload mask**: The app's mask form above 16 positions is BleUtils.X, ceil(n/8) bytes: build_op15_color_bytes_h and build_op15_color_temp_bytes_r cap it at 7 bytes, and build_op15_color_basic (H60A0) copies it whole. So t
- **AA 05 mode-report replies for colour and scene sub-modes 0x02/0x04/0x0B/0x0D/0x15**: Round 2 and round 3 recorded mode-report reply decodes only for sub-modes 0x01, 0x03, 0x07, 0x09, 0x16 and 0xF0. No decoder the control screen uses for colour or scene reports was re-verified. These d
- **0xA3 V2 comm [0x41, code] music upload + 33 05 13: per-SKU routing and effect value**: These senders are known to put MultipleController4Music before the mode frame, but their goodsType gating was never traced: rgbiclight MusicMode, MusicMode4BareLightV1, MusicMode4DreamColorLightV2, h6
- **0xA3 V1 comm 0x42 abs-music blob + 33 05 16**: The framing is built (build_abs_music_blob / build_abs_music_16). Still untraced: which newdetail SKUs mount DelegateDevicePickSound / DelegateAbsMusic, so it is unknown whether any modelled SKU's con
- **0x05/0x15/0x05 gradient colour and the A3 0x40 gradient strip**: build_op15_gradient_color (33 05 15 05 [3 x RGB][mask]) and build_gradient_color_strip_apply rest on the q2 'supportGradientColor' gate. The round-3 builder says that gate's code (GradientColorControl
- **0x14 gradual (33 14) on dreamcolorlightv2 models at goodsType 0**: The round-3 build_gradual_ble says its attribution to H6163/H6125/H6126 reporting goodsType 0 is 'inferred from JADX lost blocks; end-to-end control-screen reach not traced'. So devices.yaml dreamcolo
- **0x05 mode read on the H6057 connect (AA 05 vs AA 05 01)**: The round-3 outputs contradict each other. Chunk 14 adds H6057 to read_mode_bare (AA 05, no selector) and says the connect read sends AA 05. Chunk 3 and the read_mode builder say AA 05 01. Resolved against decomp-7.7.02: H6057 sends AA 05 01. ControllerMode.Companion.b passes the synthetic-ctor mask 2, so its ext defaults to [01] (ControllerMode.java:45-47,107-109); read_mode_bare's real sender is the pact_h60a0 adjust-v1 read (compose ModeController.Companion.a, ext null; ComposeReadAllInfoV1.java:148).
- **heartbeat keep-alive AA 33 / AA 36 (ConnectHeartbeat types 3 and 4)**: ConnectHeartbeat.c maps heartbeat types 1-4 to commands 0x01, 0x00, 0x33 and 0x36. Round 2 says types 3 and 4 are 'never selected'. Round-3 chunk 14 says AA 33 and AA 36 'are also heartbeats on some f
- **proType of anonymous kt AbsController writes (0x25, 0x43, 0x46)**: JADX drops the boolean constructor argument, so these three sites show an AbsController built from the bytes alone, and the proType (0x33 write vs 0xAA read) cannot be cited from decomp-7.7.02. Round 3 confirmed 0x33 
- **plug and lamp connect-batch reply layouts (AA B4, AA B0, AA 16)**: The request bytes are built, but no reply decode was recorded in round 3 (chunk 18 has no replies) for these: (1) the AA B4 [outlet] FF timer-list reply (4 x 4-byte records); (2) the AA B0 [outlet][ty
- **proType 0xAB / cmd 0x03 Wi-Fi function-list reply**: Only the request (AB 03, XOR A8) is built. Not recorded: (1) the frame acceptance rule frame[0]==AB, frame[1]==00, frame[5]==03; (2) the data window (a()=13, k()=6); (3) multi-frame reassembly; (4) th
- **EE notifies with no recorded layout (0x19, 0x1B, 0x21, 0x22; the spec's notify_sub now types 0x21 / 0x22 with the H6062 layouts, 0x19 / 0x1B are still missing)**: These notify parsers are not recorded in round 2 or round 3, and at the time their sub-types were missing from the spec's notify_sub: striplight EE 0x21 CheckIcNumParse [ic s16be][segment u8]; striplight EE 0x22 IcAndPie
- **newdetail capability features with builders but no SKU binding (0x05/0x20, 0x05/0x47, 0x05/0x82, 0xA3 0x60, 0x83/0x84/0x85, 0x86, 0xA3 0x58 / 0xB8 / AC 01, 0xAC / 0xAE, 0x49, 0xA4)**: The bytes are built, but no modelled SKU is shown to reach these features. IBreathFunc and IDisplayFuc have no implementer in decomp-7.7.02. The part-scenes, ICarouselFuc, ICreateEffectGif and ReadCon
- **0x0E current limit on H6179 (tvlightv1)**: build_current_limit is cited to rgblight SettingAc.g4, gated by Support.l. Two things are open. First, it was never shown that SettingAc is opened from the H6179 control screen's settings rather than 
- **0x09 sync time and 0x05/0x0D kelvin range for H6006**: H6006's goodsType (64 or 20) is inferred, not cited. bulblight Support.d tests H6006 only inside its goodsType-64 branch. The goodsType decides which bytes the app sends: the TimerConfig version-1 Con
- **0xEF pact read (AA EF) per-SKU reach**: read_pact is built along with its reply [pactType u16be][pactCode]. Round 3 marks as a GAP 'the per-SKU split between this AbsBlePact/BlePactChecker path and newer pact paths'. So it is still unknown 
- **0x07 selector 0x08 OTHER_INFO (AA 07 08)**: The bulblightv3 NewDetailVm use, which round 2 mapped to H601E/H601F, was 'not re-traced' in round 3. The H60A0 fields set from bits 1 and 2 (Info4DetailH60A0 t and s) have no resolved meaning. The Io
- **0xA9 white balance (devices.yaml h6047)**: The only H6047-family white-balance parser is Info4Detail.p0, which requires frame[1] == A9 and frame[2] == 00. It is fed only from IoT ptBytes inside parseIotMsg. No BLE request or BLE notify for A9 
- **legacy and documentation-only emitters left uncited (0x05/0x15, 0x05/0x11, 0x05/0x13, 0xA5 mic read)**: These were never recorded, even as dead or legacy code. (1) h61d3 ColorStripComposeController: 33 05 15 01 [RGB][00 x5][BleUtils.X mask] and 33 05 15 02 [bri][2-byte mask], used by the h61d3 IoT colou
- **devices.yaml families with no decompiled source (h512x 0x01-0x06; pact_tvlightv2/v3/v4 0x30-0x35/0xA3/0xA9; pact_h605b 0x23/0x25/0x30-0x32/0x37/0x44/0xA9; h604a 0x34-0x36/0x40/0x41/0xA7/0xAA; h70b1/h70b2 control screens; h1162; h705a)**: These are in-scope modelled sensor, TV, HDMI, splicing and strip families, but their controllers live in splits that are not in decomp-7.7.02 (split_pact_h512x, split_pact_tvlightv2/v4, split_pact_h60
- **spec enum values with no in-scope emitter or a stale note (pro_type 0x3A, sub_mode 0x14, pro_type 0xA4 note)**: (1) spec pro_type 0x3a write_read: lighting has only the constant BleProtocolConstants.SINGLE_WRITE_READ = 58 (referenced nowhere) and the IoT routing test in GxControllerMessages. Every 3A frame found
