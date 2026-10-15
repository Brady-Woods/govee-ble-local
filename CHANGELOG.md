# Changelog

Notable changes to `govee-ble-local`. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project follows
[Semantic Versioning](https://semver.org/) and is versioned in `pyproject.toml`. Entries are grouped
**Added / Changed / Fixed / Spec** (Spec = the Kaitai `spec/*.ksy` + `spec/devices.yaml` protocol
model, from which the shipped readers are generated).

## [Unreleased]

### Added
- **H6004** (BLE bulb, `bulblightv3` split, goodsType 64; #14): a profile-only addition that behaves
  like its H6008 sibling: power, percent brightness, RGB, colour temperature (2700-6500 K, the app's
  both-slot 0x0d frame with the Kelvin tint, 100 K steps), the cloud scene library (63 scenes,
  type-1 RGB upload; `scene_versions={0, 1}` per `ScenesDelegateRegister` / `Support4BulbLightV3.g`)
  and polled read-back. Not implemented yet: DIY, the phone-microphone music mode and the timers
  (4 timers, wake-up, sleep). Plaintext, no account lock. Modeled from the decompiled
  Govee Home 7.7.02 app (`Config.x[64]` -> `Config.bu` -> `Bytes.p`, `KelvinConfig` map 64 -> V2,
  `bulblightv3/pact/Support`); not hardware-verified. H6004 has a second, Wi-Fi-only hardware
  revision (goodsType 10) with no BLE path; the library can only drive the goodsType-64 one.
  `tools/field_test/plans/h6004.py` adds its field-test plan, and `scenes/H6004.json` is the bundled
  catalog (`tools/fetch_scenes.py`).
- **H6188** (legacy RGB strip, `rgblight` split): power, brightness, RGB, colour temperature
  (2000-9000 K), the app's nine built-in scenes, and polled read-back. Modeled from the decompiled
  Govee Home 7.7.02 app; not hardware-verified. Its siblings H6085/H6086/H6089 are Wi-Fi-only
  (soft-AP pairing, no BLE path in the app) and stay unsupported.
- Colour scheme `"h6188"`: same 0x0d RGB frame as `"h6006"`, but the CCT frame carries the app's
  Kelvin->RGB tint bytes (`Config.GM` -> `Bytes.A`); new `wire/cct.py` holds the tint table,
  generated from `Constant`'s colour->Kelvin map.
- `DeviceProfile.brightness_range`: raw (min, max) brightness for devices whose brightness byte is
  not a percent (H6188 = 20..254, `BrightnessConfig` version 0). Writes map 1..100 with
  `NumberUtil.calculateProgress`, reads with `calculatePercent` (Java `float` math, exact round trip).
  New builder `build.brightness_raw`.
- `parse.parse_mode_0d_rgblight`: the H6188 `aa 05 0d` report, decoded through the generated
  `ModeColor0dTyped(kind=2)` reader; any non-zero Kelvin means colour-temperature mode.
- Bundled `scenes/H6188.json`: the fixed `Scenes4H6188` list (activate-only, `33 05 04 <code>`).
  `tools/fetch_scenes.py` skips app-defined catalogs (`APP_DEFINED_SCENE_SKUS`) so a fetch can't
  overwrite them with the cloud library.
- `tools/field_test/`: guided hardware test for someone who owns the device (scan, every capability
  with read-back and a visual check, restore, JSON report + frame capture to send back). One
  runner with a test plan per SKU: the SKU is detected from the scanned device (or `--sku`), and each
  device adds `plans/<sku>.py` calling `register_plan(SkuPlan(...))` without touching the runner.
  Ships with the H6188 plan. Bluetooth addresses, serials and Wi-Fi MACs stay masked in both
  output files (see #17).
- `Device.segment_count` and `Device.set_segment_count()`: a segment count learned at runtime
  overrides the profile's static `segments` for whole-device masks, segment indices and the
  `aa a5` read-back walk (a profile's explicit `color_readback_segments` still wins for the walk).
  `None` reverts to the profile value. H6062 sets it from its `aa 41` read; for H6061 the caller sets it from a known layout.
- **H6062** (Glide wall light, `striplight` split, goodsType 41; #15): power, brightness (percent),
  RGB, colour temperature (2000-9000 K, with the app's tint), per-segment colour / brightness /
  colour temperature, polled read-back with per-segment colour, and the cloud-library scenes
  (dialect A, versions {0,1,2,3}; bundled `scenes/H6062.json`, fetched with `tools/fetch_scenes.py`;
  `ScenesOp.parseSceneV1`, activate `33 05 04 <code u2le>`, independent of the segment count). Frames
  are the `h60a6` 0x15 layout.
  Modeled from the decompiled Govee Home 7.7.02 app; not hardware-verified.
  - The device reports its own segment count with `aa 41` (`[IC s16be, sectionNum u8]`,
    `ControllerIcSegmentNum`, a mandatory step of the app's connect sequence:
    `AbsComposeControllerWithOpResult.onOvertime` fails the whole info read on a timeout). The
    library reads it on every poll (between `aa 04` and `aa 05`, the app's order) and before the
    first write that carries a segment mask, and uses `sectionNum` as the segment count: the mask
    width (2 bytes up to 16 segments, 3 for 17-24, ... 5 for 33), the segment index bound and the
    `aa a5` group walk of ceil(n/4) groups. If the device does not answer, no colour frame is sent
    (the app cannot control the device without the count), rather than guessing a mask.
  - New `DeviceProfile.section_count_read`, `build.section_count_query`, `parse.parse_section_count`.
    `profile.segments` (33) is only the upper bound (`Support.getBulbStringMaxNum`: 19 for pacts
    (1,x)/(2,x), 28 for (3,1), 33 for (4,1)).
  - `tools/field_test/plans/h6062.py` field-test plan.

### Changed
- **Out-of-range Kelvin is clamped to the device's range instead of raising `GoveeBleInvalidValue`**
  (`set_color_temp`, `set_segment_color_temp`, `set_zone_color_temp`, `turn_on(kelvin=)`), matching the
  app: `KelvinConfig.Companion.a` (`calibrationKelvin`) and `Constant.checkKelvinRange` clamp any int to the
  per-SKU range and never reject it. Order is clamp, then round to 100 K, then clamp again;
  `state.color_temp_kelvin` stores the value actually sent. Only a non-int (or, for a profile with no
  known range, a value that does not fit the wire's u16) still raises. `build.normalize_kelvin` now
  raises only for those cases instead of for any value outside 0..65535.
- New `Device.pact` property (`(pact_type, pact_code)` from the advertisement, or `None`) and
  `build.normalize_kelvin`.
- `tools/field_test/plans/h6188.py` drops the 2750 K "no tint" diagnostic step: 2750 K is now rounded to
  2800 K like the app, so the step no longer tests anything.
- Segment masks are no longer capped at 16 bits. `build.mask_width(segments)` follows the app's
  encoder (`BleUtils.makeSelectedTwoBytes` / `BleUtils.X`, picked by the `length > 16` branch in
  `color/Bytes`): up to 16 segments a 2-byte little-endian mask (unchanged), above that
  ceil(n/8) bytes, LSB first (byte j bit i = segment 8j+i). More than 7 bytes (56 segments, the
  most `Bytes.C` encodes) raises `GoveeBleInvalidValue`. `all_segments_mask` is now exact instead
  of silently clamping to 16 segments, and raises above 56. `segment_rgb`, `segment_color_temp` and
  `segment_brightness` take an optional `segments` count that sets the mask width; omitted, they
  keep the two-byte mask. `color_rgb` / `color_temp` use the width for their `segments` argument.
  The 0x0b (`h61a8`) frame keeps its fixed two-byte mask. No shipped device changes.
- Segment indices on a device with more than 16 segments run 0..n-1; up to 16 segments the
  0..15 range is unchanged.
- **H6047 gradual** (#13): `gradual=True` in the profile. `update()` reads `aa a3` and
  `set_gradual()` writes `33 a3 <on>`; the app reads it on every connect (`ControllerGradual4BleWifi`) and
  its colour screen writes it (`ColorModeControllerConfig.p` -> `Config.G[119]` -> `Bytes.w`).
- **H6047 reports a kelvin per bar** (#13). `aa 05 15` is `[gradual][kelvin left u2be][kelvin right u2be]`
  (`SubModeColor.parse`, `Info4Detail` parse strategy); new `parse.parse_bar_kelvin`,
  `DeviceState.zone_color_temp_kelvin` (keyed by zone `power_index`, like `zone_power`) and
  `Device.zone_color_temp_kelvin(name)`. `DeviceState.color_temp_kelvin` is the whole-device value and is
  set only while both bars agree, as in the app; a colour-mode reply clears both. Writes
  (`set_color_temp`, `set_zone_color_temp`, `set_rgb`, `set_zone_rgb`, `set_scene`) keep these in step
  until the next read.
- `tools/field_test/plans/h6047.py`: the H6047 field-test plan (whole and per-bar power, brightness,
  RGB, whole and per-bar CCT, gradual, 10-segment read-back; restores the bars, colours and gradual flag).

### Fixed
- **H60A6 whole-device colour temperature is one frame, like the app's Kelvin sliders.** The library sent
  two frames (main panel, mask `00 10`, then ring, mask `ff 0f`) whenever the advertised pact was
  (1, >= 3) or unknown, copied from `ControllerBuild.j`, which is only the colour-stripe/snapshot apply
  path (`VM4LightH60A6.configColorStripController`). A 7.7.02 trace of a plain whole-device Kelvin set
  shows one frame: the colour tab (All mode) is `DelegateColorV2.G0` (posSet = 13 trues,
  `ColorModeUtil.e`) -> `ColorViewModel.U` -> `ColorModeControllerConfig.c` -> `Config.Yt` ->
  `Bytes.r(tint, K, posSet, 1)`, and the illumination tab is `KelvinConfig.b` ->
  `ColorModeControllerConfig.h` -> `Config.N[0]` -> `Bytes.C(K, 13, 1)`. At 4000 K both give
  `33 05 15 01 ff ff ff 0f a0 ff d5 a1 ff 1f`. `set_color_temp` and `turn_on(kelvin=)` now send that one
  all-segments frame; `set_zone_color_temp` keeps its per-zone frames. `DeviceProfile.cct_per_zone_min_pact`
  (added unreleased) is removed with the two-frame gate. The app's pact gates are not modelled beyond this:
  below pact code 2 it sends the 0x0d form (`Config.kw`, `Bytes.p`), which the library does not, and it
  sends the 0x15 frame for an unknown pact like the library does.
  Live-checked on an H60A6 (pact 1/3): the single frame is acked and the read-back of the main panel
  and all 12 ring segments equals the tint table at 4000 K (`ff d5 a1`) and 2700 K (`ff ae 54`).
- **An RGB pick equal to a Kelvin tint colour is sent as CCT on every device, like the app.** The
  H6061-only rule now covers every colour scheme with a CCT mode. When the picked colour's full ARGB int
  is exactly one of the 178 colours of the app's Kelvin tint table (`Constant.k2` = `ColorUtils.e`, two
  per 100 K step) and its Kelvin is inside the SKU's inclusive range (`KelvinConfig.i` / `.c`), 7.7.02
  writes a CCT frame instead of the RGB one, and the library did not, so such a pick lit the device with
  a slightly different colour than the app (and `state` reported RGB instead of the Kelvin the device
  reads back). 0x15 family (H60A6 2700-6500 K, H6641 and H6062 2000-9000 K, H6047 2200-6500 K):
  `ColorModeControllerConfig.c` -> `Config.Yt` = `KelvinConfig.i(c) ? Bytes.r(c, KelvinConfig.c(c), ...)
  : Bytes.h(c, ...)`, i.e. `33 05 15 01 ff ff ff <K> <picked RGB> <mask>`: one frame, the picked colour as
  the tint (not the first tint of the Kelvin), the mask the RGB frame would use, for `set_rgb`, `turn_on(rgb=)`, `set_zone_rgb` and `set_segment_rgb`
  (H6047 reaches it via `NewDetailVm.configColorController` -> `H6047ColorControllerConfig.a` -> `Yt`).
  0x0d family (H6188 2000-9000 K, H6008/H6004 2700-6500 K, H6052/H6006 2000-9000 K): `Config.bu` =
  `KelvinConfig.i(c) ? Bytes.p(c, KelvinConfig.c(c)) : Bytes.f(c)`, i.e. `33 05 0d <picked RGB> <K>
  <first tint of K>` for both the `h6006` and `h6188` schemes (not the white-slot `Bytes.A` form, which
  stays the H6188 slider). Afterwards `color_temp_kelvin` is the Kelvin and `rgb_color` is `None` (both
  bars on H6047). One mechanism for all schemes: `build.rgb_as_kelvin` takes the range from the profile;
  `segment_color_temp` takes an optional `tint`; `set_color_temp` is unchanged. Not modelled: colour-set /
  mixed-colour picks and the real-time drag frames (`a5 02 83`, RGB only); H61A8 is not determined.
- **CCT frames now send the Kelvin tint the Govee app sends** (#19), which the library left as zeros
  everywhere except H6188. Following the app's own colour-control path for each SKU:
  - **H6047, H6641, H60A6** (0x15 white-slot form `15 01 ff ff ff <K> <tint> <mask>`, `Bytes.r` type 1;
    `ControllerBuild.d`, `Config.Yt`): `color_temp`, `segment_color_temp`, `set_zone_color_temp`
    always fill the tint.
  - **H60A6 whole-device CCT is one all-segments frame** (see the separate H60A6 entry above); per-zone
    CCT is one frame per zone (main panel mask bit 12, ring bits 0-11).
  - **H6008, H6006, H6052**: new 0x0d both-slot form `0d <tint> <K u2be> <tint>` (`Config.bu` ->
    `Bytes.p`), replacing `0d ff ff ff <K> 00 00 00`. H6188's `0d ff ff ff <K> <tint>` form is unchanged.
  - Kelvin is clamped to the device range and rounded to the nearest 100 K before sending
    (`KelvinConfig.calibrationKelvin`; the app's slider only produces 100 K steps, and its builders
    recover K from the tint table). `set_color_temp(2750)` therefore sends 2800 K and reports 2800 K in
    `state`.
- `parse.parse_mode_0d` (the 0x0d bulb/lamp read-back) now treats any non-zero Kelvin as CCT, as the
  app's parsers do (`ModeParseStrategyConfig`, `SubModeColor.parse`), instead of requiring `ff ff ff`
  in the colour slot; the app's own CCT write puts the tint there, so the old rule read it back as RGB.
- **H6047 reported two phantom segments, 10 and 11** (#13). Its goodsType 119 uses the old detail screen
  (`h6047/pact/Model.t` -> `NewDetailAc`), whose read-back walks `Support.getBulbStringMaxNum(119)` = 10
  pieces in groups of 4 (`NewDetailVm.n5`: `aa a5 01..03`, `SubMode4Color` trims the last group to 2
  records). The profile used `getColorPieceSize` = 12, which only the newdetail screens (277/288/298)
  call, so the last group kept 4 records. `color_readback_segments` is dropped: read-back is the same 10
  segments as the write.
- **H6047 whole-device power is `33 01 <on>`**, as the app's main switch sends it
  (`NewDetailVm.switchTrigger` -> `ControllerSwitch`). The library sent `33 36 <on> <on>`, which the app
  only uses for the per-bar buttons; `set_zone_power` still sends `33 36`. Powering on leaves the per-bar
  state unknown until the next `aa 36` read (the app does not say which bars `33 01` lights), so the
  next zone change re-reads the bars instead of assuming; powering off marks both bars off. `aa 36`
  read-back now sets `is_on` to "either bar on", as the app's heartbeat does.
  The `aa 01` poll stays: the app's connect read (`Compose4InfoBleIot`) includes `ControllerSwitch`, and a
  timeout there fails its whole connect, so an H6047 answers it (a miss would cost one 2 s read timeout and
  is non-fatal; `aa 36` still supplies power).
- **H6061** (Glide Hexa light panels, `cubelight` split, goodsType 51), phase 1 = whole-device control
  only ([#16](https://github.com/Brady-Woods/govee-ble-local/issues/16), tracked in #17): power,
  percent brightness, RGB, colour temperature (2000-9000 K) and polled read-back. Modeled from the
  decompiled Govee Home 7.7.02 app; not hardware-verified. No SEGMENTS or SCENES capability yet, and
  no secret (the app writes 0xB2 only while pairing). New colour scheme `"h6061"`:
  - RGB is `ceil(n/4)` frames `33 05 15 04 <group from 1> R G B x<=4`, the last carrying the
    remaining panels (`Bytes.m`, `BleUtils.w0`; `ColorModeControllerConfig.k` -> `w` -> `l`); with
    the default 23 panels that is 6 frames, the last with 3 colours. New `build.color_rgb_frames`;
    `Device.set_rgb` / `turn_on(rgb=)` send the frames with `send_many`.
  - CCT is `33 05 15 01 <tint RGB> <kelvin u2be> <24-bit mask>`, the first n mask bits set, LSB first
    (`ff ff 7f` for 23 panels; `Config.Zt` -> `Bytes.u`, `ColorPieceConfig.k`). The tint sits in the
    colour slot, not the `ff ff ff` white-slot form of H60A6 / H6047 / H6641.
  - An RGB pick that is exactly one of the app's 178 Kelvin tint colours (two per 100 K step, within
    2000-9000 K) is sent as the CCT frame, with the picked colour as the tint, as the app does
    (`ColorModeControllerConfig.k`: `ColorUtils.q` + `KelvinConfig.i`, then `Config.Zt`); the state
    then reports that Kelvin. New `wire.cct.kelvin_for_tint` and `build.rgb_as_kelvin`.
  - Panel count n defaults to 23, the app's value when no layout is stored (`ColorPieceConfig.f`,
    `Support.getBulbStringMaxNum`). `Device.set_segment_count(n)` sets it for a known layout
    (1-23; more is refused because the app sends no colour for a longer set).
  - Read-back polls `aa 01` / `aa 04` / `aa 05`: the colour comes back as mode `0x0d`
    `[R, G, B, kelvin u2be]` (kelvin > 0 = CCT) although writes use `0x15`
    (`CubeNewInfo4Detail`), then `aa a5 <group>` for groups 1..`ceil(n/4)` fills `state.segments`
    (`CubeVM4Light.l5`).
  - `tools/field_test/plans/h6061.py`: power, brightness, RGB (6 group frames) and CCT at
    2000 / 4000 / 6500 / 9000 K.

### Spec
- `devices.yaml`: `color_temp_write` opcode for `h60ax`, `h6047`, `h61d3`, `bulblightv3` and
  `tablelampv1`, with the decompiled sources. `govee_ble.ksy` (+ generated docstring): `color_cct_0d`
  documents both CCT forms.
- `govee_ble.ksy`: `mode_color_0d_typed` kind 2 (rgblight `[R,G,B, kelvin u2be, tint RGB]` with an
  `is_cct` instance); `color_cct_0d` / `mode_color_0d_report` docs cover the H6188 forms. Readers
  regenerated (kaitai-struct-compiler 0.11.0; the diff is only these additions).
- `devices.yaml`: new `h6188` family with citations; note on the Wi-Fi-only H6085/H6086/H6089.
  `devices.schema.json`: `mode_0d_kind` gains `rgb_kelvin_tint`.
- `devices.yaml`: new `striplight` family (H6062, goodsType 41; the shared package's H610A/H610B have
  their own flows and are not modelled). `govee_ble.ksy`: comment that opcode `0x41` is
  `on_off_memory` in the shared enum but the H6062's `aa 41` section-count read on the striplight
  split (hand-decoded in `parse.parse_section_count`; generated readers unchanged).
- `devices.yaml`: the `bulblightv3` family now records H6004's two hardware revisions: goodsType 10
  (`bulblightstringv1/bulblightv1`, Wi-Fi/IoT-only, `IotSkuItemV2.needBle()` false) versus goodsType
  64 (BLE + Wi-Fi), with the citations; H6003 is goodsType 9 (IoT-only).
- `devices.yaml` `h6047` family re-audited against 7.7.02 (#13): kelvin per SKU (H6043/H6042 =
  2000-9000 K, only H6047 is 2200-6500 K); read-back count depends on the screen (old detail screen
  `getBulbStringMaxNum`: H6047 = 10, H6042 = 5; newdetail screens `getColorPieceSize`), so the
  "read 12 != write 10" claim is gone and `getGoodsType4ColorSegment` is the only one that never returns
  5; whole-device power `33 01` vs per-bar `33 36`; `aa 36` third byte (movie/feast); `aa 05 15` and
  `a3` documented; swap light `0x34` and the connect-time `aa ac` sound-pickup read documented, not
  modelled; left/right order is logical (`LightPosAc`); "observed plaintext" no longer claimed for H6047;
  line citations updated (`Support.isGoodsTypeH6047` is at :186). `govee_ble.ksy`: `cct_read_reply` gains
  `kelvin_right` and the gradual-flag meaning of byte 0, `bar_switch_read` gains `movie_feast`. Readers
  regenerated (the diff is only these two additions).
- `devices.yaml`: new `h6061` family (Glide Hexa, phase 1) with citations: the group RGB frame, the
  tint-in-colour-slot CCT frame with its 24-bit mask, the `0x0d` colour report and the `aa a5` reads.

## [1.2.1] — 2026-10-13

### Fixed
- Installs alongside current Home Assistant again ([#8](https://github.com/Brady-Woods/govee-ble-local/issues/8)): the `cryptography<49` ceiling conflicted
  with HA 2026.10's `cryptography==50.0.1`, so the integration failed to load. The requirement is
  now `cryptography>=41.0.0` with no upper bound: HA installs integration requirements under its
  own `package_constraints.txt`, so inside HA the version always follows HA's pin, and a ceiling
  could only break again when HA moves. The `pip-audit` ignores for PYSEC-2026-3552/3553/3554 are
  gone with it (fixed in the `cryptography` versions now resolved).
- BLE name -> SKU extraction now matches the app (`BaseBleProcessor.parse`). The bare `GV` form
  returned the wrong SKU (`GV61881A2B` -> `61881`, `GV6188_1A2B` -> `6188_`; the app reads
  `"H" + name[2:6]` -> `H6188`), so `discover()`/`match()` could never match such a device.
  Also aligned with the app: `GVR` keeps its `R` (`GVR6184_XXXX` -> `R6184`); `GVH`/`GVR` names
  without `_` need >= 11 characters and `GV` names >= 10 (shorter names are no longer guessed);
  `ihoment_`/`Govee_`/`Minger_` are matched anywhere in the name, not only as a prefix.
  Verified against live advertisements (H60A6 and H6008 in the `GVH…` form; H5083, H6047, H6052
  in the brand form): same SKUs as 1.2.0, and no non-Govee device newly matched.

## [1.2.0] — 2026-10-12

First release published to PyPI. `1.1.0` was bumped on a development branch but never tagged or
released, so its changes ship here too (see the `1.1.0` entry below).

Hardening release candidate: input validation, connection/lifecycle reliability, secret handling
and packaging. Behaviour changes that can affect callers are listed under **Behaviour changes**.

### Added
- `GoveeBleInvalidValue` (a `ValueError` subclass, also a `GoveeBleError`): raised for bad setter
  input: colour channels, kelvin outside the device's `min_kelvin`/`max_kelvin`, segment/zone
  indices and masks, scene payloads, secret length. Builders and AES/RC4 key lengths are validated too.
- `Device.connect()`, an `idle_disconnect` argument on `Device`/`create_device`, and
  `Device.scene_upload_frames(scene)` (the planned upload frames, or `None`).
- `__version__` and more public exports: `Encryption`, `Scene`, `StateCallback`, `profile_for`,
  `GoveeBleInvalidValue`.
- `app_version` argument on the cloud account (overrides the `appVersion` request header).
- `govee-ble-analyze --from-frames-log` input is validated (malformed lines are skipped, reported
  and counted).
- Dev tooling: `dev` and `audit` extras, `scripts/audit.sh`, a `[tool.vulture]` config, an
  independent AES reference test (`pycryptodome`, test-only), ruff/mypy-strict/bandit/pip-audit
  gates in `scripts/check.sh` and CI, and a tag-triggered PyPI trusted-publishing workflow.

### Changed
- `kaitaistruct>=0.11` (was unpinned below); SPDX `license = "MIT"` metadata; classifiers, `Issues`
  URL and a markdown `readme` content type for PyPI.
- The sdist no longer bundles `tests/`; it carries only `src/`, `pyproject.toml`, `README.md`,
  `LICENSE` and `CHANGELOG.md`.
- Profile fields are `Literal`-typed and validated at construction.
- Segment merge / scene planning moved into `wire/` and `scenes/`; `Device` uses public
  `scene_upload_frames`; the private scene alias was removed.
- CI: SHA-pinned actions with minimal permissions, Python 3.11-3.13 matrix, lowest-dependency job;
  optional test dependencies fail instead of skipping when `CI` is set.

### Fixed
- A disconnect racing an in-flight acknowledgement no longer leaves the write waiting or raises a
  non-library error; a link lost while the write lock is held raises a typed error.
- A slow poll can no longer overwrite state set by a newer setter (mutation-sequence guard).
- Relay power: power frames are acknowledged and the resulting state is applied; time sync is
  best-effort.
- Bar-switch devices (H6047): the read-modify-write of the two-bar frame is serialized; the other
  bar's state is read before use.
- Lifecycle leaks: client/link no longer leaked on handshake, device-info and disconnect error
  paths; detached disconnects are bounded in time.
- Plaintext receive queue (no decrypt with a stale session key); a queued frame arriving at the
  deadline is not lost; the end-of-burst sentinel is never dropped.
- Device-info link errors no longer fail `update()`; device info is retried.
- Encryption fallback when the advertised mode is wrong; secret length is checked.
- State callbacks no longer break the notify pump or each other (exceptions are logged).
- Colour groups with an invalid index are skipped instead of producing negative segment indices;
  V1 segment brightness stays `None`.
- Cloud client: response shapes are validated (status checked before JSON parsing, token type
  check); the owned session is closed when login fails; one malformed device secret no longer
  fails the whole account.
- Scene catalogs: SKUs are validated, malformed catalogs and bad base64 raise library errors, and
  catalog writes by `tools/fetch_scenes.py` are atomic.
- Hardware tools: SKU/path validation, argparse, btsnoop length caps, restore-in-`finally`;
  `tools/decrypt_btsnoop.py`/`btsnoop_mtu_scan.py` mask the account secret.

### Security
- Frame-log files are created `0600`, written with `O_NOFOLLOW`, refused if not a regular file,
  bounded (oldest records dropped when full) and flushed at interpreter exit; a dead writer is
  revived. Docs now state that captures are sensitive (the session key is derivable from them).
- Cloud responses are shape-validated; malformed device secrets are never echoed.
- Secret masking in captures shares one implementation and recomputes the frame checksum.
- CI runs `bandit` (Medium+) and `pip-audit`; the three accepted `cryptography` advisories are
  documented (AES-ECB/ARC4 only; PKCS#7/X.509 unused).

### Behaviour changes (may affect callers)
- Bar-switch devices: `set_zone_power` raises when the other bar's state is unknown and cannot be
  read, instead of assuming it is on.
- `GoveeBleAuthError` is no longer retried.
- `send(expect_ack=True)` rejects non-write frames.
- Setters raise `GoveeBleInvalidValue` (still a `ValueError`) for invalid input, including kelvin
  outside the profile's `min_kelvin`/`max_kelvin`. (Brightness percent is still clamped to 1..100.)
- `Capability` and `Encryption` are `StrEnum`: `str(member)` now returns the value
  (e.g. `"power"`), not `"Capability.POWER"`.
- `load_scenes` returns copies (mutating the result does not affect the cache) and raises library
  errors for malformed catalogs.
- Cloud `get_devices` returns `secret=None` and logs a warning for a malformed secret rather than
  failing.

## [1.1.0] — 2026-10-12

Reliability overhaul driven by five days of live frame captures (11 devices, 2 ESPHome proxies +
a local adapter) and validated live on the same hardware: steady-state polls went from ~7.7 s
with a 5 s stall (H60A6) and ~40 % failures to 1.5–4.5 s with no failures.

### Fixed
- **Secret leaked through `wire=` in frame captures.** Only the `plain=` field of the `33 b2` / `aa b1` secret frames was masked; on a plaintext link `wire=` is the same bytes, and on an encrypted link it is decryptable from the captured handshake plus the public PSK. Both fields (and the bad-checksum debug line) are now masked.
- `set_segment_brightness` raised a bare `ValueError` on colour schemes without per-segment
  brightness (H61A8); it now raises `GoveeBleNotSupported`, like `set_segment_color_temp`.
- **Handshake raced the device.** The device delivers its `e7 01` reply twice and re-sends the
  `e7 02` echo ~1 s later. The duplicate step-1 reply was consumed as the step-2 "ack", so the
  first query fired while the confirm was still in flight; ~90 % of H60A6 sessions then lost the
  `0xFF` terminator of their first status burst (5 s stall + retry on every poll). Replies are
  now matched by content, the real confirm is awaited, and late retransmits are dropped.
- Frames failing the XOR checksum are dropped at the notify pump (they were the source of the
  intermittent "unknown proType 0x67/0x7f/0xa5" warnings: handshake echoes decrypted with the
  session key that happened to pass the checksum).
- A link that stops acknowledging writes (half-dead while bleak still reports "connected") is
  torn down and the write retried once on a fresh link, instead of failing the command.
- A disconnect wakes pending waits immediately; a late disconnect callback from a superseded
  client no longer clears the current one.
- The idle-disconnect timer can no longer fire after newer activity (it used to disconnect
  right after a command and force a reconnect); its task is now held so it can't be GC'd.
- `establish_connection` gets `ble_device_callback`, so retries use the freshest proxy path.
- Every public operation has a hard deadline (`OPERATION_TIMEOUT`) and raises only
  `GoveeBleError` subclasses (bleak/asyncio errors no longer leak out of `send`/`query`).
- 0xAC reassembly joins only the contiguous chunk prefix (a gap used to splice later chunks at
  the wrong offset and decode garbage as real state) and decodes a truncated burst up to its last
  whole TLV, so a lost tail yields partial-but-true state without a retry; queries end on a short
  idle gap instead of the full timeout when a terminator is lost.
- 0x0d bulbs (H6006/H6008/H6052): the mode report is `[r, g, b, kelvin u2be]`; white + kelvin is
  colour-temperature mode. H6052's "mechanism C" decoded it as RGB-only (reporting white instead
  of 2700 K, live capture `aa 05 0d ff ff ff 0a 8c`) and issued a duplicate mode query; all three
  bulbs now read colour/CCT back from the report they already send.
- Polled devices always re-read power (previously skipped once a device was off, so a light
  turned on externally was never noticed).
- H6047: changing one bar no longer assumes the other bar is ON when its state is unknown (it
  turned the other bar on); the bar state is read back (`aa 36`) and polled.
- Advertised on/off is ignored for 15 s after a command/read (stale broadcasts flipped state).
- Brightness is never sent as 0; the reported-brightness scale is an explicit profile field
  (`brightness_max`) instead of a `>100 means 0-255` guess.
- Scene uploads are paced instead of blasted (dropped frames over proxies).
- Account-lock secrets are masked in frame captures (logs/diagnostics).
- `FrameLog` file writes moved off the event loop.
- Cloud client: request timeouts, the documented-but-missing re-login on 401, no response bodies
  in error messages, no forced login when a token is supplied.

### Added
- `Device.turn_on(brightness=, rgb=, kelvin=, power=)` / `turn_off()` and
  `GoveeConnection.send_many()`: multi-attribute changes go out as one batched, ack-verified
  operation on one connection. Whole-fixture power is always one frame: H60A6 uses the global
  `33 01` switch (live-verified to drive both zones: off clears them, on lights both even when
  each was off; previously one `33 30` frame per zone), H6047 the combined `33 36` bar frame.
- `GoveeBleAuthError` is raised when a device rejects its secret key.
- `Device.encryption` (the negotiated channel mode); colour temperature read back from the mode
  report; segment writes update `state.segments`.
- `tools/power_semantics_probe.py`: hardware experiment for global vs. zone power.
- Fake-peripheral test harness (`tests/fake_ble.py`) reproducing the captured failure modes;
  release workflow publishing to PyPI via trusted publishing.

### Removed
- Dead code: `ble/frame.py`, duplicate `checksum_ok`, unused `CONNECT_TIMEOUT`.

## [1.0.3] — 2026-07-12

Field-tested and confirmed live on H60A6 before merge (see PR discussion): a truncated 9-of-11-chunk
burst recovered fully on retry with no warning; an H6047/H6641-style zero-chunk case correctly
logged `state left stale` with chunk indices after the retry, rather than papering over it.

### Fixed
- `Device._read_status()` (the `0xAC` status read-back for `readback="status"` devices, e.g. H60A6)
  now **retries once** on an empty parse before leaving state stale. A dropped BLE notification
  mid-burst yields a truncated or empty reassembled buffer — reported in the field as
  `status reply did not parse (0 bytes): ... requested 1 bytes, but only 0 bytes available` and
  `status reply did not parse (148 bytes): ... requested 17 bytes, but only 12 bytes available`
  (`wire/reassemble.py` DEBUG log). This was already non-fatal (fields are applied conditionally,
  so a failed parse can't erase known-good state) and already surfaced a WARNING
  (`"... state left stale"`), but a single dropped notification cost a whole poll cycle; a fresh
  query is independent and usually succeeds immediately. Both the retry-attempt DEBUG line and the
  final WARNING now also report which `0xAC` chunk indices were actually received (e.g.
  `0x00,0x01,0x02,0x04,0xff`, or `<none>`), to distinguish a dropped-mid-burst from a
  completely-empty read on any future occurrence.

## [1.0.2] — 2026-07-12

### Fixed
- **H6047/H6641's `1.0.1` colour read-back fix reused the wrong controller's decoder.** `1.0.1`
  correctly identified that neither SKU dispatches the `0xAC` status burst, but wired their
  per-segment colour read-back onto H61A8's `mechanism_b` (`BulbGroupColorV2`) decoder —
  a *different, unrelated controller* that only happens to share the `0xA5` wire opcode with
  the one H6047/H6641 actually use (`Controller4ColorInfoByGroup`, confirmed via a full re-read
  of `devices.yaml`: *"SAME PARSER ... but TWO different TRANSPORTS"*, explicitly distinct from
  H61A8's separately-listed `BulbGroupColor`/mechanism-B). The two decoders disagree on group
  size (3 vs 4), so segment indices were silently misassigned. Caught by the test suite itself
  before any further guessing — a second wrong per-batch value (5, conflating two unrelated
  "5"s from two different code paths) was caught the same way and never shipped.
- New `color_readback="mechanism_a_direct"` (`wire.parse.parse_direct_color_group`,
  `Device._read_mechanism_a_direct`) correctly decodes `Controller4ColorInfoByGroup`'s per-group
  reply — group size **4**, source-confirmed independently for both SKUs
  (`devices.yaml`: *"4/group"* / *"4 records/group"*), not extrapolated from H61A8.
- **H6641's group-count approximation is now resolved, not just documented**: a live `0x40`
  IC-count read (`build.ic_count_query`, `wire.parse.parse_ic_count`, the already-modeled
  `ic_segment_read` ksy type) returns the device's own precomputed group count directly —
  no client-side ceil-division/divisor guess needed. New `DeviceProfile.color_readback_live_ic`
  flag gates this per-SKU (H6641 only; H6047 keeps its static, source-confirmed piece count).
- `Device._read_mechanism_b` is now H61A8-only again (its docstring no longer over-claims
  sharing with H6047/H6641).

## [1.0.1] — 2026-07-12

### Fixed
- **H6047 and H6641 never actually answer the `0xAC` status burst** — they were wrongly modeled
  as mechanism-A (the same `0xAC → 0xA5` path as H60A6), which produced a deterministic
  zero-frame read on real hardware (source-confirmed, not a wire/timing issue: `H6047`, goodsType
  119, routes to `Compose4InfoBleIot`, `Support.isGoodsTypeH6047:177`; `H6641`, goodsType 247,
  never reaches the `afterConnected` `0xAC` dispatch at all, `H61D3Support.f0(247)=false`). Both
  read per-segment colour via **direct per-group requests** instead (`AA A5 <group>`,
  `Controller4ColorInfoByGroup` — the same decode H61A8 already used as `mechanism_b`).
  `DeviceProfile.readback` for both switches `"status"` → `"polled"` (power/brightness/scene via
  `aa 01/04/05`); `color_readback="mechanism_b"` added for both, reusing the existing decoder.
  New `DeviceProfile.color_readback_segments` field lets H6047's batch math use its true
  read-back piece count (`getColorPieceSize`=12) instead of its write/addressable count (10),
  which differ for this SKU.
  **Two related approximations remain open (flagged in `profile.py`, not fixed by this change):**
  H6641's true colour-group *count* is IC-driven (needs a live `0x40` read this library doesn't
  perform yet — falls back to the write-mask width as an approximation), and its per-reply
  *record count* assumption (3, matching H61A8's `BulbStringColorControllerV2`) is unconfirmed —
  H6641 may use an unmodeled `V3` controller with a different count/layout. Neither SKU is yet
  live-verified.

### Spec
- `devices.yaml` / `docs/GOVEE_BLE_GATT_PROTOCOL.md`: documented the H6047/H6641 `0xAC`
  non-dispatch (source citations above) and their `per_segment_color_read` direct-request
  transport; reconciled `H61D3Support.e()`'s divisor (`d ∈ {3,4}` → `d ∈ {3,4,5}`, H6641/247 = 5)
  against the `ceil(IC/5)` group-count math.

## [1.0.0] — 2026-07-11

First public release. Prior `v1`/`v2`/`v3` were internal rewrite generations (never tagged or
published); `1.0.0` is the initial Semantic-Versioning baseline, committing to the
`govee_ble_local.__all__` public API. This release is the ground-up **v3-generation rewrite**: a
data-driven `DeviceProfile` table + one capability-gated `Device` class over a spec-first `wire/`
layer (build / parse / reassemble) and the shipped, ksy-generated readers. Clean break from the
old `GoveeBleClient` API.

### Added
- Capability-driven `Device` + `create_device()` / `discover()` / `DeviceProfile` public API.
- Colour temperature per zone/segment: `Device.set_zone_color_temp()` / `set_segment_color_temp()`
  + `build.segment_color_temp()` (the 0x15 CCT frame's segment mask, previously all-segments only).
- Read-back: plug relay power, H6047 segment colours (mechanism-A status), and device-info
  (`serial` / `wifi_mac` / `firmware` / `hardware`), plus `DeviceState.ble_mac`.
- `Device.set_gradual()` — the `0xA3` gradual/fade-on-BLE↔wifi-handoff flag (curated: H61A8;
  read back into `DeviceState.gradual` on `update()`).
- `Device.read_secret()` and `Device.ingest_advertisement()` restored (v2 parity).
- Diagnostics: a coherent `govee_ble_local.*` log-level scheme, a `govee_ble_local.frames`
  frame-tier logger for full-session capture (incl. over a Home Assistant Bluetooth proxy),
  the `govee-ble-analyze` console script + a frames-log→JSONL converter, and `docs/DIAGNOSTICS.md`.
- Live H60A6 tools: CCT choreography + a segment-map probe.

### Changed
- The reassembled `0xAC` status buffer is now parsed **entirely by the generated `StatusReply`
  reader** — the hand TLV-walk (`walk_tlvs`), `_add_color_group`, and the MAC-anchor device-info
  heuristic are all retired. Only the cross-frame de-chunk stays hand-done (Kaitai can't join
  frames). The offline analyzer uses the same reader.
- README + version brought to the v3 API; description now "Govee devices" (plugs included).
- **Dependency hygiene:** `PyYAML` moved to the `test` extra — it isn't used at runtime (the device
  table is hardcoded Python; `yaml` appears only in the test suite). Runtime deps are now
  `bleak` / `bleak-retry-connector` / `cryptography` (HA defaults) + `kaitaistruct` (the one accepted
  pure-Python exception). Added `[project.urls]`.
- **Docs:** README install-from-source instructions (not yet on PyPI); a `pdoc` API reference
  (`.[docs]` extra + `tools/gen_docs.sh`); a `CONTRIBUTING.md` (branching / commit / changelog /
  release conventions); project conventions recorded for maintainers.

### Fixed
- H60A6 segment→zone map corrected (index 12 = main panel, 0–11 = background ring), making
  per-zone colour/CCT genuinely independent — live-verified.
- H60A6 `wifi_mac` / `hardware_version` read-back (regressed in the v3 clean break) restored,
  now from the modeled `0x07` device-info TLV; zeroed `aa 07` replies no longer clobber real
  values (`0.00.00` / all-zero → None).
- `reassemble()` de-duplicates doubled status chunks (devices double-deliver notifications).

### Spec
- Modeled the reassembled `0xAC` nested TLV values (source-verified, `Compose4BaseInfoSingleRead`):
  `status_tlv` switches `0x05→mode_status`, `0x07→device_info_read`, `0xA5→color_group_status`;
  `status_reply` uses `repeat: until type==0` + `if: type != 0` to survive the trailing zero-pad.
- Added `gradual_read` (0xA3) — now surfaced (`Device.set_gradual`) — and tag-grouped
  `color_strip_write`, which is modeled but **not exposed**: it has no curated-SKU binding in
  `devices.yaml` (needs source: which SKU uses `MultipleColorStripControllerV1` / comType 0x40, and
  whether it complements or replaces `set_segment_rgb`).
- Fixed a YAML-1.1 boolean id trap (`id: on` → parsed as `.true`); renamed to `state` / `on_flag`.
- `devices.yaml`: H60A6 segment→zone map; H6641 colour records are 4-byte; mechanism-B/C read-back
  and scene-dialect notes.
- Adopted the parametric **Tier-C** grammar variants (`op15_color_typed`, `mode_color_0d_typed`;
  joining `color_group_read`) so the ksy owns 100% of byte structure — the client passes one
  discriminator and the branch runs inside the grammar. `devices.yaml` gained per-family
  `client_profile` blocks (discriminators as data) + schema support. Readers regenerated; additive,
  no runtime behaviour change.
- `spec/` is now a self-documenting protocol package: added the canonical protocol docs
  (`spec/docs/GOVEE_BLE_GATT_PROTOCOL.md`, `SCENE_UPLOAD_ENCODING.md`, `USING_THE_KSY.md`) and an
  executable reference codec (`spec/govee_reference.py`) — the framing layer Kaitai can't express
  (AES-ECB+RC4 decrypt, `0xAC` reassemble, XOR BCC) + dispatch wiring, with a self-test.
