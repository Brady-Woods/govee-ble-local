# Changelog

Notable changes to `govee-ble-local`. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project follows
[Semantic Versioning](https://semver.org/) and is versioned in `pyproject.toml`. Entries are grouped
**Added / Changed / Fixed / Spec** (Spec = the Kaitai `spec/*.ksy` + `spec/devices.yaml` protocol
model, from which the shipped readers are generated).

## [Unreleased]

### Added
- `DeviceState.device_id`: the id the Govee app calls `device` (e.g. `2D:DB:5C:E7:53:F4:74:56`), read
  with `aa 07 02`. An alias of `serial_number`, which holds the same value.
- `build.soft_version_query` (`aa 06`), `build.INFO_HARD` (`aa 07 03`) and `parse.parse_soft_version`.
- New exceptions, defined in `govee_ble_local.exceptions` and exported in `govee_ble_local.__all__`:
  `GoveeBleCommandRejected` (the device acknowledged a write with a non-zero status),
  `GoveeBleHandshakeTimeout` and `GoveeBleHandshakeConnectionError` (both a `GoveeBleHandshakeError`, and
  also a `GoveeBleTimeout` / `GoveeBleConnectionError`), and `GoveeBleUnknownName` (an unknown zone or
  scene name; both a `GoveeBleInvalidValue` and a `GoveeBleNotSupported`).
- `GoveeCloudAuthError` (a `GoveeCloudError`): the Govee account rejected the email/password (HTTP 401/403,
  or a 401/403 `status` in the body), or rejected a saved token when there are no credentials to log in
  again, so an integration can tell bad credentials from connectivity problems. Both cloud errors now live in
  `govee_ble_local.exceptions` and are exported from `govee_ble_local` (catchable without the `cloud` extra)
  and from `govee_ble_local.cloud`; `govee_ble_local.cloud.account.GoveeCloudError` still imports.
- `Device.snapshot()`: a copy of the device state that later changes don't touch, to keep or compare;
  `Device.state` stays the live object.
- `GoveeAdvertisement.pact` (`(pact_type, pact_code)`, or None when the advertisement carried no pact info)
  and `GoveeAdvertisement.encryption_known`, so callers no longer test the `-1` sentinel; and
  `identify.from_advertisement()`, which identifies a bleak advertisement (or an already-parsed one) the
  way `Device` and `scanner.match` do.
- `scenes.async_load_scenes(sku)`: `load_scenes` without blocking the event loop (the first lookup per SKU
  reads the bundled JSON in a worker thread; later ones are cached).
- `transport.framelog.flush(timeout)`: waits, up to `timeout`, until the frame-capture writer has written
  every queued record, and returns whether it drained. It blocks, so call it off the event loop.
- `DeviceProfile` fields, keyword-only and defaulted: `relay_on_mask` and `advert_on_mask` (which bits of a
  plug's relay reply and of its advertised switch byte mean "on"; their defaults, 0xFF and None, keep
  1.3.0's rules of any relay bit and an advert byte of 1, so custom profiles behave as before),
  `color_readback_ic_range` and `color_readback_ic_per_piece` (the live IC count's clamp and divisor), and
  `mode_color_parse` (how the `aa 05` colour sub-modes are decoded, typed `devices.profile.ModeColorParse`:
  `"rgb_kelvin"`, the default, `"rgb_kelvin_tint"`, `"rgb_0d"`, `"kelvin"`, `"kelvin_15"` or
  `"rgb_kelvin_0d"`). `identify.parse_broadcast_onoff` gains a keyword-only `on_mask`; the default keeps the
  lights' `== 1` rule.
- Helpers: `wire.parse.parse_mode_rgb_kelvin_tint`, `parse_mode_0d_rgb`, `parse_mode_0d_for(frame, scheme)`
  and `SUBMODE4COLOR_SUB_MODES`, and the `wire.parse.GroupRecord` type; `wire.reassemble.burst_chunks`,
  `walk_status_tlvs` and `TlvCut` (the 0xAC completeness rule and TLV walk, shared by the runtime and
  `govee-ble-analyze`); `wire.describe.status_burst_report` / `StatusBurstReport`, with `truncated` and
  `redacted` counts, and the `wire.describe.Direction`, `Severity` and `Issue` types; and
  `crypto.FrameCipher`, the frame cipher bound to one session key with its key setup done once (exactly
  the bytes of `crypto.encrypt` / `crypto.decrypt`).
- Constants: `const.ADVERT_SUPPRESS_SECONDS` (15 s; moved from `devices.device`, still importable there),
  `const.DISCONNECT_TIMEOUT`, `const.QUERY_TERMINAL`, the parts `LOCAL_NAME_PREFIXES` is built from
  (`const.BRAND_MARKERS`, `GBK_PREFIX`, `GV_LONG_PREFIXES`, `GV_PREFIX`; the tuple is unchanged),
  `wire.build.PRO_NOTIFY` (0xEE), `wire.build.PRO_MULTI_A1` (0xA1), `wire.reassemble.MIN_CHUNK_LEN`,
  `SIGNED_LEN_MIN`, `TYPE_MODE`, `TYPE_SLEEP`, `TYPE_WAKEUP` and `TYPE_TIMERS`, and the
  `transport.framelog.Direction` type.
- `wire.reassemble.parse_status(frames, *, pieces=None)`: the device's colour-piece count, which sets how
  many records each 0xA5 colour group holds (see Fixed); and `wire.reassemble.group_record_count`, the app's
  rule for that count. `pieces` omitted keeps 1.3.0's reading (the count comes from each TLV's length).
- `wire.build.PRO_MULTI_A2` (0xA2, `multi_read`), `wire.describe.chunk_redacted` and
  `devices.brightness.percent_to_device` (see Fixed).
- `DeviceProfile.relay_power` (keyword-only, typed `devices.profile.RelayPower`: `"outlet0"`, the default and
  1.3.0's `33 01 11 / 10`, `"all_outlets"` or `"total_switch"`) and the builders `wire.build.plug_all_outlets`
  (`33 01 FF / F0`) and `plug_total_switch` (`33 02 01 / 00`), with `RELAY_ALL_ON` / `RELAY_ALL_OFF` and
  `CMD_TOTAL_SWITCH`; the `ModeColorParse` value `"kelvin_15_rgb_kelvin_0d"`; and
  `wire.reassemble.StatusReply.mode`, the 0xAC reply's 0x05 TLV (see Changed).
- `DeviceState.last_color_temp_kelvin`: the colour temperature the device last reported or was set to
  (> 0), kept while it is in another mode, as the app keeps its Kelvin. `color_temp_kelvin` still means
  "in colour-temperature mode" and is cleared as before, so Home Assistant's colour mode is unchanged. A
  report with a Kelvin of 0 or below (H60A6, H6062, H6641, H61A8) keeps it, as `SubMode4Color` takes a
  Kelvin only when it is > 0 (`OpInfo4Detail.parseModeValidBytes:766-778`; pact_h60a0 h60a1
  `Info4Detail.Q:399-406`); on H6047 it follows the bars only while both report the same Kelvin (h6047
  detail `Info4Detail` ctor strategy :173-185). Writes set it too. Modeled from the decompiled app.
- `wire.parse.parse_light_power`: an `aa 01` reply as a light reads it, on only for a byte of 1 (the spec's
  `switch_read_reply.light_is_on`); `parse_power` keeps the raw byte for the plugs' relay mask.
- `GoveeConnection(session_frames=...)`: writes sent once per connection after the unlock, whose refusal
  or missing ack is only logged; `GoveeConnection.write_payload_size`, the link's write size (ATT MTU - 3,
  20..509, 20 when the backend can't say); `send_unacked` also takes a function of that size.
- `wire.parse.request_command(frame)`: the command a built `aa` read request asks for (the frame reader's
  `read_command.command`), which is the command its reply carries; and `wire.parse.IcCount.ic_count_checked`,
  the IC count as the app's checking readers return it (the spec's `ic_count_checked`: None for a count of
  0x1000 or more).
- `wire.reassemble.BurstAssembler`: the app's 0xAC burst state machine, fed one record at a time
  (`feed` returns whether the app takes it; `status` is `"waiting"`, `"complete"` or `"failed"`), and the
  `wire.reassemble.TlvCut` reason `"tail"` (a walk that stops cleanly at the buffer's end).
- `DeviceProfile` fields, keyword-only and defaulted: `power_resync` (gt 50 / 90 plugs, see Fixed) and
  `status_color_groups_pact` (the pact the 0xAC status read needs before it asks for the 0xA5 colour
  groups). `scenes.scene_upload_frames` and `Device.scene_upload_frames` take a keyword-only `mtu`
  (default 20, today's frames).

### Changed
- Documented deviation: the connection keeps dropping an RX frame whose XOR checksum fails, although the app
  never checks it (`ComposeBleComm.K:190-211` drops only a frame that isn't 20 bytes), so a corrupted frame
  is not decoded as state.
- More of the library reads and writes through the spec's types (frame audit section F and the section-E
  additive forms):
  - every `aa` read waits for the reply its own request names (`parse.request_command`), not a hand-paired
    `build.CMD_*` key (12 sites);
  - read requests are the spec's `read_request_sized(selector_len)`;
  - segment masks are written by the spec's `segment_mask` (its width rule, LSB first), within the
    library's 7-byte cap;
  - the `mode_read` decoders use its `sub_mode` and `is_submode4color` instances and read the params with
    `mode_read_params`;
  - a direct `aa a5 <group>` reply is read by `color_group_direct_frame(record_count)`, and a 0xAC 0xA5
    group by `color_group_status_counted(pieces, 4, 1)`;
  - the TLV walk steps by `status_tlv.total_len`;
  - the live IC-count walk skips on the spec's `ic_count_checked` instead of a hand `>= 0x1000` test.
  Byte- and state-identical: equivalence run against c823fc5 (writers, readers, the TLV walk, parse_status,
  the mode decoders and folds, and every bench capture); the only differences are the new
  `IcCount.ic_count_checked` field and `_readback.live_ic_pieces` called directly with a count the walk
  never passes it.
- On the spec's section-E lockstep readers (2667e85): `build.secret_check` and `build.gradual` write the
  spec's `SecretKeyWrite` / `GradualWrite` in place, now that `single_command.params` types 33 B2 / A3 (same
  bytes); the 0xAC TLV walk reads 0x07 as the spec's `status_device_info` (`app_decodes` for the 0x10 / 0x11
  selectors the app's `w()` decodes) instead of stepping past the others itself. A 0x05 mode TLV that the
  spec's `mode_read` can't read (it types the value exactly; the app's per-sub-mode minimums aren't typed yet)
  is handed on raw, as before. Byte- and state-identical (equivalence run against 8c89c31, every bench
  capture identical). `govee-ble-analyze` no longer reports such 0x07 blocks, or 0xA5 groups whose len isn't
  1 + 4k, as spec gaps, now reports a 0x05 value `mode_read` can't read, and counts a TLV the walk hands on
  once in its TLV types (it counted a handed-on 0xA5 twice).
- The test coverage floor in `scripts/check.sh` is now 95% of statements and branches (`--cov-branch`;
  was 85% of lines), the same gate hass-govee-ble-local applies to the integration. Coverage today is
  98% with branches. Development tooling only.
- **The generated Kaitai classes are a build product, not committed source.** `src/govee_ble_local/_generated/`
  is gitignored. An in-tree PEP 517 backend (`_build_backend/govee_build_backend.py`, `[build-system]
  backend-path`) wraps setuptools and runs `tools/gen_kaitai.sh` before every sdist, wheel and editable
  build. The sdist and wheel ship the result, and a wheel built from an sdist (which carries no `spec/` or
  `tools/`) reuses the classes the sdist ships; with neither, the build fails with a message saying how to
  get them. Building from the repository (`pip install -e .`, `pip install .`, `python -m build`) therefore
  needs the JVM `kaitai-struct-compiler` 0.11; installing a released wheel or sdist doesn't, and the
  published wheel has the same files as before. A git or VCS URL (`govee-ble-local @ git+https://...@ref`)
  or a GitHub "Source code" archive is a repository build too, and needs bash and the JVM compiler, which
  Home Assistant doesn't have: to test an unreleased build in Home Assistant, pin the wheel attached to a
  prerelease GitHub Release, never a git ref. A `pip install --dry-run` of a git ref doesn't catch this,
  because preparing metadata skips the generation step. `scripts/check.sh`'s drift check and
  `gen_kaitai.sh --check` are gone; the readwrite build now clears `_generated/` first, so no leftover
  module can ship. CI and the release workflow run `scripts/smoke_artifacts.sh`: the sdist must carry the
  classes and no `spec/` or `tools/`, and the wheel, and a wheel built from the sdist, are installed into
  fresh venvs and must import and build a frame, so a wheel without the classes can't be published. `pytest`
  stops at once, saying to run the generator, when the classes are missing or older than `spec/*.ksy` or
  `tools/gen_kaitai.sh` (an ignored `_generated/` survives a `git switch` or `git pull`). A repository build
  that can't run the generator now ends with one message naming bash, the JVM compiler and the PyPI release,
  instead of a `CalledProcessError` traceback.
- **The `aa 05` mode decoders read the spec's `mode_read`, and the app's sign and CCT tests come from the
  spec.** `wire.parse.mode_reply` (an `aa 05` reply) and `wire.parse.mode_value` (the 0xAC 0x05 TLV) return
  the same `mode_read`, and the per-sub-mode decoders (`scene_of`, `kelvin_of`, `bar_kelvin_of`,
  `gradual_kelvin_of`, `cct_0d_of`, `rgb_kelvin_tint_of`, `rgb_0d_of`, `rgb_kelvin_0d_of`, `sub_mode`; all
  new) read it. The `parse_*` frame functions are unchanged wrappers. So the burst's mode no longer goes
  through an `aa 05` frame built by hand (`devices._fold.mode_reply_frame` is gone, and no library module
  calls the raw `wire.build.frame` helper now). The colour decoders no longer check the frame header or
  slice it by hand. The Kelvin sign tests use `mode_color_0d_typed`'s `kelvin_signed`,
  `report_kelvin_signed`, `is_cct` and `is_cct_positive` instead of `int.from_bytes(..., signed=True)` and
  `>= 0x8000`, for H6062, the bulbs and H6188, H6061 and H60A6. No decode changes: an equivalence run over
  7,360 20-byte replies through every decoder, 104,895 reply folds and 90,045 TLV folds (every profile, TLV
  values 0-20 bytes long) matched the previous code. Only frames shorter than 20 bytes, which the
  connection drops before decoding, decode differently. Bench 2026-10-16 (Mac, read-only): H6006, H6008,
  H6047, H6052, H60A6 and H61A8 polled cleanly with the new decoders. Today's bench captures (447 replies:
  55 `aa 05`, and 10 bursts whose 0x05 TLVs are 4 bytes long) decode the same through the old and the new
  code.
- **The library reads the spec owner's gap batch (c706502) instead of hand-kept values.**
  - **Enums:** `wire.build`'s `INFO_*`, `POWER_*`, `RELAY_*`, `CMD_TOTAL_SWITCH`, `COMM_*`, the op15 colour
    variants, the mode request selector, the 0xAC request command and the 0xA3 start marker now come from
    `device_info_selector`, `light_power`, `relay_write`, `plug_command`, `multi_comm_byte`,
    `op15_color_variant`, `mode_read_selector`, `multi_ac_command` and `a3_start_marker`. The
    `mode_color_0d_typed` kinds come from `mode_0d_kind`, `reassemble.TYPE_*` from `status_tlv_type`, and the
    connection's received-proType set from `rx_pro_type` (it now also knows 0x3A, 0xA6 and 0xAB, so those no
    longer log an "unrecognised frame" warning; they were queued all along). The analyzer's
    `KNOWN_STATUS_REPLY_TLVS` is the `status_tlv_type` enum, which adds 0x16, 0x39 and 0xA3.
  - **Types:** H5089's `33 02` main switch is written with `total_switch_write`, so no frame body is
    written raw any more. The `aa 41` section count is read with `section_count_read`. A 0xAC reply chunk is
    read with `multi_ac_reply_chunk` (`reassemble.read_ac_chunk`, new), whose `last_len` validation is the
    app's whole-burst reject, so the hand-kept chunk offsets are gone. A write ack is read with `rx_frame`'s
    `write_ack` (`wire.parse.parse_write_ack`, new), for the connection's rejection check and the analyzer.
    The BGC-info value is read with `bgc_info` (`wire.parse.bgc_encrypt_version`, new; lenient for a short
    format-2 value, as chosen), and the handshake key with `handshake_key_reply_data`. The analyzer routes
    on `rx_frame.pro_type` and judges a masked chunk from the spec chunk's data and checksum. The scene
    upload reads each blob's lead byte with `h60a6_scene_param`, `h6052_graffiti_param`,
    `compose_scene_param` and `scenes_graffiti_param`. An advertisement is read with
    `manufacturer_data_lenient`, so the 4-byte length gate is gone (item 41's additive route).
  - **Instances:** `mode_read.scene_code`, `bar_switch_read.left_on` / `right_on`, `gradual_read.is_on`,
    `secret_read_reply.is_valid`, `notify_wifi.is_connected`, `status_zone.zone_a_on` / `zone_b_on`, the
    device-info `uid_is_invalid` / `uid_has_leading_pair` / `wifi_mac_is_invalid`, `version3.text` /
    `is_zero`, and `manufacturer_data.is_govee_pact` / `has_switch_state` / `switch_byte`.
  - The H60A6 and H6061 0x0d report is read as kind 3 (`rgb_kelvin_0d`, `[r, g, b][kelvin]`, the five
    bytes the app reads) instead of kind 2.
  - Also from the 4017e19 frame audit:
    - the zone, bar and gradual writers' 1/0 states come from `light_power`;
    - `status_query` names its TLV types with `status_tlv_type` (it had borrowed the `command` enum);
    - the TLV walk reads the stream with `status_reply_safe` (the same TLVs; `[]` for an empty buffer)
      and ends on `status_tlv_type.padding`;
    - the analyzer's mode-reply exemption uses `mode_read_selector.request` and `mode_read.scene_code`.
    These are byte- and state-identical (equivalence run against f182c20, and the bench captures).
  - `devices.yaml`'s h5080 `by_goods_type` now pins the plug profiles' `relay_power`, `relay_on_mask`
    and `advert_on_mask` (a drift test, keyed by its `models`). A second drift test checks every colour
    profile's `mode_color_parse` against the strategy `devices.yaml` resolves for its goodsType (the family
    by the record's goodsType, by SKU only at 0; then `assigned_goods_type`; then `by_goods_type`,
    `by_model`, the family default, pact and firmware branches). The spec's `kelvin_pair_15` (H6047) is the
    library's `"kelvin_15"` on a bar-switch profile.
  - Still left for a lockstep spec change: the `mode_read` 0x04 switch case, the `command` enum's 0x02
    member, `pro_type` 0xA6, the root scan-record walk and the guard on `status_reply` itself.
  An equivalence run against the previous code found no other difference: the writers, every bundled scene's
  upload for every profile, 6,000 advertisements, the 0xAC bursts and TLV walks, the handshake replies, and
  today's bench captures (972 frames, 48 bursts, 230 encrypted replies) all decode the same. The decode
  changes are under Fixed.
- **More library sites read the spec's own fields instead of raw bytes** (an independent inventory of
  sites that ignored an existing spec field or instance).
  - `wire.parse.find_reply` matches a reply on the frame reader's `pro_type` and `command`. A reply
    whose body doesn't parse, such as an `aa 06` whose text isn't UTF-8, is still found.
  - `parse_direct_color_group` reads the reader's raw body, and `parse_section_count` matches its
    header via the reader (`on_off_memory`). Its body stays hand-decoded until the spec has a type.
  - `parse_active_scene` reads the code with `scene_payload`; `mode_read` has no 0x04 case yet.
  - The new `parse_device_info_selector` reads the `aa 07` selector for `_readback`.
  - `wire.build`: the 0x0b colour writer uses `mode_payload`'s own `color_rgbic_0b` type (a u2le mask).
    `status_query` names the requested types with the `command` enum.
  - The 0xAC TLV walk takes its cut reasons from `status_tlv`: `len`'s `valid` (max 0x7f) and how far
    the reader got. It drops its dead negative-length re-check after a whole `status_reply` parse.
    `TlvCut` gains `negative_len`, and the analyzer uses it. A skipped 0x07 block, and the device info
    `parse_status` folds, are read with `status_device_info`. A colour group with no piece count uses
    `color_group_status.records`.
  - The analyzer labels unknown values from the reader's fields.
  - The handshake reply is read by the frame reader.
  - The H60A6 scene upload picks DIY or graffiti with the spec's `h60a6_scene_value.is_diy`, and keeps
    the app's graffiti fallback for a DIY that doesn't parse (spec item 26).
  - The device's mode read hands the reader's `mode_read` straight to the fold.
  - A 0x15 colour write's mask cap is the spec's `op15_color_write.mask_cap`, which the writer already
    enforces: 7 bytes for variants 12 and 11, 10 for variant 5. No limit changes. The library builds
    variants 12 and 11 at that same 7-byte cap, and H6061's variant 5 with a fixed 3-byte mask.
    `MAX_MASK_BYTES` (56 segments) stays for the masks the spec has no cap for yet: op15 brightness and
    the device's segment count.
  No decode or frame changes, apart from the IC count under Fixed. An equivalence run over every
  20-byte case matched the previous code:
  - 2,435 frames through these readers and the analyzer;
  - the 0x0b and status-query writers;
  - 4,014 TLV walks and 5,000 status parses;
  - 1,800 handshake replies;
  - 3,088 H60A6 scene choices, including the 84 bundled scenes.
  Today's bench captures (810 frames, 40 bursts, 193 encrypted replies) also decode the same. Bench
  2026-10-16 (Mac, read-only): H6006, H6008, H6047, H6052, H60A6 and H61A8 polled cleanly with these
  changes, including the encrypted H60A6 and H6008 handshakes and the `aa 06` / `aa 07` reads. H6641,
  which uses the live IC count, was out of range.
- **Every frame the library sends is written by the spec's generated Kaitai classes, and every frame it
  reads is parsed by the same classes.** `govee_ble_local/_generated/` now holds Kaitai 0.11 read-write
  classes (`ReadWriteKaitaiStruct`), generated by `tools/gen_kaitai.sh`, whose default build is now
  read-write (`--read-only --out DIR` still writes auto-reading readers to a scratch directory).
  Each `wire.build` builder is a `GoveeBleFrame` tree of the spec's own types. Where the frame's read
  dispatch has no case for a write body or types it as the reply, the spec's standalone write type is used:
  `ZonePowerWrite`, `BarSwitchWrite`, `GradualWrite`, `SecretKeyWrite` and `Op15ColorGroupWrite` for the
  untyped bodies; `ReadRequest` for every `aa` request; `Op15ColorWrite`, `Op15BrightnessWide` and
  `ColorRgbic0bWide` for the colour bodies. The `e7` handshake frame is written the same way. Only what
  Kaitai can't express stays in code: the frame checksum, upload chunking and packet counts, the app's
  tint/Kelvin choices and the caller-input checks. Every frame is byte-identical
  (`tests/test_wire_build_vs_spec.py`, every builder against the app-traced catalogue). Cost: building a
  frame takes about 20-55 µs and parsing one about 10-15 µs (about 1 and 3 µs before).
- Code that uses `govee_ble_local._generated` directly (not public API): `from_bytes()` and
  `Cls(..., stream)` no longer read; call `_read()` on the result.
- `wire.build.plug_sync_time` refuses a time-zone hour or minute outside -128..127 (the spec's `s1` fields)
  with `GoveeBleInvalidValue`, instead of masking it to a byte. Real offsets are within -14..14 hours.
- **`aa 01` and `aa 04` are sent without the `01` selector byte**, as the app sends them
  (`ControllerSwitch.Companion.b`, `ControllerBrightness.Companion.b`, the plugs' `SwitchController` /
  `HeartGMessage`): `aa 01 00…` / `aa 04 00…`. No firmware image analysed reads byte 2 of these requests
  (it is overwritten with the state), so devices answer the same. **A light's `aa 01` reply now means on only
  for a byte of 1** (`ControllerSwitch.Companion.c`; the spec's `light_is_on`), as every curated control
  screen reads it; it was any non-zero byte. Firmware and the app only ever store 0 or 1, so no state reads
  differently in practice (the byte-2-or-more case is source-modelled). Plugs keep their relay-mask rule, and
  the H60A6's power still comes from its status burst, where any non-zero byte is on (`status_switch.is_on`,
  `Compose4BaseInfoSingleRead.u`). Live-checked on H6006, H6008, H6047 (on) and
  H6052, H61A8 (off).
- **Plug power is each goodsType's main switch** (Govee Home 7.7.02): the H5082 (goodsType 50) and the
  H5160 / H5161 (90) send `33 01 FF / F0`, which switches every outlet (`UiV2` / `UiV4.onOffChange` ->
  `SwitchControllerV2(on, 15)`), and the H5089 (307) sends `33 02 01 / 00` (`Ui4H5089.onOffChange` ->
  `Controller4TotalSwitch`). They all sent `33 01 11 / 10`, which switches outlet 0 only, so `set_power(False)`
  left the other outlets on and the next poll reported the plug on again. The H5080 / H5083 / H5085 (43) keep
  `33 01 11 / 10`. `set_power(True)` on a partly-on plug turns every outlet on. The `33 b5` time sync still
  follows. The H5082 and H5089 now have a profile each, so their `DeviceProfile.skus` is `("H5082",)` /
  `("H5089",)`. Not modelled: after ON the H5089 app re-applies its cached night-light mode. Source-modelled:
  no multi-outlet plug was in Bluetooth range (the H5083's frame is unchanged).
- **H6006 colour temperature is 2700-6500 K** (was 2000-9000): the H6006 is goodsType 64, which the app maps
  to `KelvinConfig` version 2 = [2700, 6500], as for the H6008. `set_color_temp` clamps to it, and an RGB pick
  equal to a tint colour outside it is sent as RGB, as the app does. Home Assistant's slider narrows to match.
- **The H60A6 poll is the 0xAC status burst alone**, the app's connect read: the mode comes from the burst's
  0x05 TLV and the `aa 05 01` read that followed is no longer sent (one frame fewer per poll). The mode is
  decoded as the H60A6's `Info4Detail.Q` does (`pact_h60a0` `h60a1.Info4Detail`): 0x15 `[gradual][kelvin
  s16be]`, 0x0d `[r, g, b][kelvin s16be]` with colour temperature only for a Kelvin above 0, else the RGB.
  Before, 0x0d took any non-zero Kelvin (read unsigned) as colour temperature. A 0x15 Kelvin of 0 or below
  still clears the Kelvin, as before; the app keeps its last one. Live-checked for a 0x15 report (2700 K)
  on the bench H60A6; the 0x0d branch is source-modelled.
- `tools/gen_kaitai.sh` generates Kaitai 0.11 read-write (serializer) classes into `_generated/` (see
  above). Its default build needs the JVM `kaitai-struct-compiler` 0.11.x on PATH
  (`brew install kaitai-struct-compiler`; `KSC=` overrides), because the npm build's `compile()` API has no
  read-write switch. `--read-only --out DIR` still writes auto-reading readers to a scratch directory and
  is the one mode that can fall back to the pure-JS compiler; it refuses `_generated/` as its output. Its
  `--check` mode is gone: nothing generated is committed to drift from (first entry in this section). Needed
  only to build from the repository (see that entry); released wheels and sdists don't need it. Its
  missing-compiler error now also names `bash scripts/install_ksc.sh` (Debian/Ubuntu) and the PyPI release,
  which ships the classes.
- Device info is read with `aa 06` (firmware version), `aa 07 03` (hardware version) and `aa 07 02`
  (device id), and nothing else: the `aa 07 11` Wi-Fi read and the `aa 07 10` basic fallback are no longer
  sent. No firmware image has a handler for those two (most drop the request, which cost a timeout on every
  poll; the H60A1 / H60A6 echo it back as zeros) and the app never sends them. The three that remain are
  handled by every firmware image analysed (H6006, H6008, H6047, H60A1, H60A6, H5083) and were answered
  live (2026-10-15) by the H60A6, H6047, H61A8, H6052, H6006, H6008 and H5083. The app sends `aa 06` /
  `aa 07 03` on the lights' control screens except the H60A6's (whose versions come from the 0xAC status
  TLV), not on the H5080 / H5083 / H5085's, and `aa 07 02` only while pairing; there they are kept as
  firmware-sourced extensions the app does not use (CLAUDE.md rule 6), marked as such in the code with the
  firmware handlers they rely on. Before these reads, only the H60A6 reported a
  firmware version, because the others ignore `aa 07 10`. `wifi_mac` now comes only from the H60A6's
  status TLV (the `aa 07 11` read never returned one).
- **The "device rejected write" log line moved from ERROR to DEBUG**, and the write raises the public
  `GoveeBleCommandRejected` instead of a private class, so the caller decides what to log. An integration
  that relied on the ERROR line, for example an always-on WARNING capture for diagnostics, must log
  `GoveeBleCommandRejected` (and `GoveeBleAuthError`, a rejected secret) itself, to a logger its capture
  covers (`docs/DIAGNOSTICS.md`). Until `hass-govee-ble-local` does, rejections are missing from its
  downloadable diagnostics.
- An RX frame with an unknown proType is logged at WARNING once per proType for the device's lifetime
  (until the integration reloads); repeats go to DEBUG and cost nothing when DEBUG is off, so a device that
  keeps pushing one can't flood the log.
- Handshake failures raise `GoveeBleHandshakeTimeout` / `GoveeBleHandshakeConnectionError`, which subclass
  the `GoveeBleTimeout` / `GoveeBleConnectionError` raised before, so `except GoveeBleHandshakeError` now
  fires. That includes a failed `e7` write (BleakError "Not connected", `BrokenPipeError`, `EOFError`, or
  bleak's characteristic-not-found error from a stale GATT cache), which 1.3.0 raised as
  `GoveeBleConnectionError("connect to <address> failed: ...")` (a `BrokenPipeError` escaped unwrapped); its
  message now reads `<address>: handshake write failed: ...`.
- A connection raises only `GoveeBleError` subclasses: an unexpected backend error while connecting or during
  an operation (e.g. a `RuntimeError` from `start_notify`), and a `BrokenPipeError` / `ConnectionResetError`
  from the BLE stack, become `GoveeBleConnectionError`, chained to the original, and the link is closed.
  Once a device is known to use AES-GCM (BGC v2), later calls raise `GoveeBleNotSupported` without opening
  a link each time. `GoveeConnection.send_many()` rejects a non-write frame with `GoveeBleInvalidValue`
  before any I/O. Not hardware-verified.
- Connection error messages changed; anyone matching on the text needs the new forms. `connect to <address>
  failed: ...` is now `<address>: connect failed: ...`, and `<address>: connect/handshake timed out` is now
  `<address>: connect timed out`. A write timeout raised by the BLE stack reads `<address>: BLE I/O timed out
  (...)`. An unexpected backend error reads `<address>: unexpected transport error: <repr>` during an
  operation and `<address>: connect failed: <repr>` while connecting.
- `discover()` raises `GoveeBleConnectionError` when the scan itself fails (no adapter, adapter powered
  off, BlueZ / D-Bus unavailable). That error is also the `BleakError`, `OSError` or `EOFError` 1.3.0 let
  through, so existing `except BleakError` handlers keep matching. A device reporting 0 dBm now sorts as
  the strongest, not with the no-RSSI ones.
- Typed argument errors: a request a colour scheme can't express (per-segment colour on a whole-device
  scheme, CCT on H61A8) raises `GoveeBleNotSupported` from the builders instead of a bare `ValueError`
  (not a `ValueError`, so `except ValueError` around a direct builder call no longer catches it), and
  `Device` re-raises it with the SKU on every path. Bad input (a non-integer brightness, an 0xA4 `mtu`
  outside 20..512 or an upload over its 16-bit packet count; at the `Device`, a malformed RGB, a non-int
  brightness, a `str` or other non-bytes secret) raises `GoveeBleInvalidValue` before any I/O, and
  `turn_on` now checks every argument before the segment-count read. `Device(secret=...)`,
  `create_device(secret=...)` and `set_secret()` take `bytes`, `bytearray` or `memoryview` and store
  `bytes`. An unknown zone or scene name raises `GoveeBleUnknownName`, still a `GoveeBleNotSupported`, so
  existing handlers keep matching.
- `device_profile_for` is deprecated in favour of `profile_for` (the same lookup): it emits a
  `DeprecationWarning` and is removed in 2.0. `hass-govee-ble-local`'s config flow still calls it.
- `DeviceProfile` validates its fields at construction and raises `GoveeBleInvalidValue` (a `ValueError`)
  for empty or non-string SKUs, `brightness_max < 1`, a `brightness_range` whose 100 % overflows a byte,
  `color_readback_per_batch < 1` and a malformed mask, IC range or IC divisor. A list of SKUs is still
  accepted, and so is a custom profile pairing a capability with a colour scheme that can't express it, as
  in 1.3.0; its setter now raises `GoveeBleNotSupported`. The built-in table is checked at import for those
  pairings, a `mechanism_a_direct` group size other than 4, `color_readback_live_ic` without
  `color_readback_ic_range` and a duplicate SKU. The new fields come after every 1.3.0 field, so the
  positional signature and `dataclasses.fields()` order are unchanged.
- `DeviceProfile.brightness_max` is deprecated (documented only, no warning); use `brightness_range`.
- **H6052 colour read-back is RGB only.** After a poll, a lamp in colour-temperature mode reports the report's
  colour as `rgb_color` (for a CCT the app or this library set, the Kelvin's tint) and no `color_temp_kelvin`,
  as the app's control screen reads it: a `0x0d` report is `[r, g, b]`, its Kelvin bytes never read, and
  `0x15` and the other colour sub-modes are ignored (`H6052NewDetailVM` -> `H6052InfoDetail.G`). `aa 05 0d ff
  ff ff 0a 8c` now gives RGB (255, 255, 255) instead of 2700 K. Modeled from the decompiled app. Live-verified
  on an H6052 (2026-10-15, macOS): after a 2700 K write the lamp reports `aa 05 0d ff ae 54 0a 8c ff ae 54`,
  which now reads as RGB (255, 174, 84) with no Kelvin; what the app displays for that report is
  source-modeled.
- 0xAC status read-back: a reply that would fail the app's read gives no state, as the app shows none
  (`Compose4BaseInfoSingleRead.u` throws, `AbsComposeControllerWithOpResult.onOvertime` fails the read, and
  the `VM4LightH60A6` success callback that applies state never runs). That covers a TLV the spec can't read,
  a TLV length byte of 0x80 or more (the app reads it signed) and a complete burst whose last TLV runs past
  the reply. `Device` retries once, then leaves state stale. A burst that lost a chunk or its last packet gives
  no state either (see the burst-assembly entry). Each poll that gets such a reply logs the existing `status
  read returned no usable data after retry` WARNING. Modeled from the decompiled app; not hardware-verified.
- `DeviceState.optimistic` on status-readback devices (H60A6) goes False only once a live field (power,
  brightness, zones, segments or the mode report) comes back. 1.3.0 cleared it after any 0xAC poll that
  wasn't discarded, even an empty or failed one; a poll that returned nothing, or only device info, now
  leaves it True. The one-time INFO `first poll` line also waits for the first poll that read something
  back.
- `MANUFACTURER_IDS` also lists 0x8841 and 0x8842, the encrypted v1/v2 broadcast headers the app accepts
  (`BleUtil.parseBleBroadcastPact`), so a Home Assistant manifest built from it matches those devices too.
  Modeled from the decompiled app; not hardware-verified.
- Signatures: `wire.build.status_query` takes `full` keyword-only; `handshake.build_step1` / `build_step2`
  drop their unused `payload` (the app never sends one); `Device.set_segment_*` accept any sequence of
  indices; whole-device colour builders called without a segment count use the scheme's default (23 panels
  for H6061, 15 for H61A8, not H60A6's 13). Removed: the superseded,
  unused `wire.parse.parse_mode_color_0d` (it dropped a 0x0d report's Kelvin), the unused
  `const.COMMAND_ACK_TIMEOUT`, and the internal `GoveeConnection(on_notify=...)` hook with its type alias
  `transport.connection.NotifyCallback`. `Device`'s frames are unchanged (it always passes its count); a
  direct builder call that omits `segments` now gets the scheme's default mask. Modeled from the decompiled
  app; not hardware-verified.
- Cloud account: a token-only `GoveeCloudAccount` (no email/password) whose token expires raises
  `GoveeCloudAuthError` instead of sending a login with empty credentials. Requests no longer follow HTTP
  redirects, so the password is never re-sent to a redirect target; a 3xx is reported as `login failed
  (HTTP 3xx)` / `<path> failed (HTTP 3xx)`. `CloudDevice.ble_mac` is documented as best effort.
- `govee-ble-analyze`: `-o` creates the converted JSONL with mode 0600 and never writes through a symlink
  (a failed write is `cannot write`, exit 2); `--help` keeps the module docs and documents exit codes
  0/1/2; `--from-frames-log` records carry the device address (`addr`). The "malformed 0xAC bursts
  skipped" count (and `analyze_status_bursts`' third element) counts only bursts that were not walked; the
  report now also counts complete bursts walked up to a TLV overrun (the runtime uses no state from them)
  and bursts from a redacted capture.
- Docs: README lists `profile_for` (`device_profile_for` as its deprecated alias), `create_device`'s
  `idle_disconnect` and `discover()`'s error. `docs/DIAGNOSTICS.md`'s log-level table matches the code
  (nothing at ERROR, every warning site listed, timeouts as `GoveeBleTimeout`) and its redaction note says
  what is masked (the account secret) and what is not.
- Encrypted sessions do the cipher key setup once per session (`crypto.FrameCipher`; identical bytes, less
  event-loop time). Log records keep the `govee_ble_local.devices.device` logger. Live-verified on encrypted
  H60A6, H6008 and H5083 sessions (2026-10-15, macOS): every session frame matches the per-frame cipher byte
  for byte.
- CI and the release workflow generate the Kaitai read-write classes from `spec/*.ksy` in every job
  that tests or builds. `scripts/install_ksc.sh` installs the pinned JVM `kaitai-struct-compiler` 0.11
  (the upstream `.deb`, checked against its published SHA-256, and refused unless it matches
  `tools/gen_kaitai.sh`'s pin), and `scripts/check.sh` now runs `tools/gen_kaitai.sh` before every other
  gate, so a local run needs that compiler too (`brew install kaitai-struct-compiler`). Development
  tooling only.

### Fixed
- **Segment masks follow the app's caps, up to 112 segments.** Each body carries at most its own mask bytes,
  and the app's writers drop the positions past them (`coerceAtMost`): the 0x15 RGB and colour-temperature
  frames 7 bytes (the spec's `op15_color_write.mask_cap` for variants 12 / 11: `Bytes.h:324`, `Bytes.r:499`),
  per-segment brightness 14 (`op15_brightness_wide.mask_cap`: `Bytes.G:179`). Before, the library refused
  every write above 56 segments. `build.MAX_MASK_SEGMENTS` is now 112 (`MAX_MASK_BYTES` 14), so a device
  or section count up to 112 is accepted; a colour write on more than 56 segments reaches the first 56, as
  in the app, and the segment state folds only the positions a frame carried. New:
  `build.COLOR_MASK_CAP`, `build.BRIGHTNESS_MASK_CAP`, `build.mask_positions(segments, mask_cap)`, and
  `build.mask_width(segments, mask_cap=14)`. No curated SKU has more than 16 segments, so their frames
  are unchanged. Source-modelled.
- **A successful 0xAC status read applies the app's switch and brightness defaults.** Each read starts from
  a fresh `DeviceBaseInfoFromDevice` (switch false, brightness 100; `DeviceBaseInfoFromDevice.java:70-79`),
  and the H60A6 success callback applies it whole (`h60a1.Info4Detail.n0:641-677`: `g(info.j())`, the
  switch; `i = info.b()`, the brightness). So a successful read whose stream has no 0x01 TLV now reads power
  off, and no 0x04 TLV brightness 100; before, those stayed as they were. A failed read still applies
  nothing. Captured bursts always carry both. Source-modelled.
- **gt 50 / 307 plugs (H5082, H5089) read power from relays 0 and 1**, the app's `aa 01` heartbeat rule
  (`HeartControllerV2` -> `BleOpV2.onEventHeartV2:216-231` / `BleOp4H5089.onEventHeartV2:331-341`, `open =
  i(0) | i(1)`; the spec's `switch_read_reply.any_relay_0_1_is_on`), which the library's repeated poll
  follows: `relay_on_mask` 0x03. Before, any relay bit (0xFF), the connect-read rule. Source-modelled.
- **Library scenes upload only what the app's validators accept** (the spec's section-E types). A type-1 RGB
  scene uploads only when `ScenesRgb.isValidProtocolBytes` accepts its blob (`ScenesOp.l:377-392`; the spec's
  `rgb_scene_value.is_valid`), a type-2 RGBIC one only when `ScenesRgbIC.isValidProtocolBytes` does
  (`ScenesOp.m:394-409`; `rgbic_scene_value`, which reads exactly that), and a type-3 graffiti one only when
  `ShareDiy.isValidGraffitiBytes` does (`ScenesOp.n:411-429`; `scenes_graffiti_check.is_valid`: size >= 9,
  lead 0x01, the group-header walk). Any other blob is only activated, as in the app; before, types 1 and 2
  went out unchecked and type 3 checked only the lead byte. Every bundled scene plans as before (631
  uploads, 25 activate-only, every profile); only a cloud or custom blob the app would not upload changes.
  Source-modelled; not hardware-verified.
- **0xAC status bursts are assembled as the app assembles them** (`AbsController4BleSingleSendMultiBack`
  as `ControllerSingleRead` runs it: `isSameController:202-225`, `onResult:273-308`). The first chunk must
  carry the request's command 3 and sub 0 (`f():100-103`); then each index is taken only in order, and the
  last packet only once every packet before it is in. A duplicate, an early or out-of-order chunk, an early
  0xFF or a first chunk of another command is ignored, as the app ignores it; a lastLen that doesn't fit
  fails the read at once. Only a complete burst is decoded: one that lost a chunk or its last packet now
  gives no state (one retry, then the state is left stale), where the library used to decode the prefix
  that arrived (the app applies nothing: `l()` never runs and the read times out). `query` ends on a
  complete or failed burst, ignores `terminal` for 0xAC, and only a chunk the app takes arms the idle gap,
  so a stray early 0xFF or a late chunk of an earlier burst no longer ends the read before the burst
  arrives. `burst_chunks` keeps its signature. Every captured H60A6 burst (16 reads) decodes as before.
  Modeled from the decompiled app; the failure paths are not hardware-verified.
- **The 0xAC TLV walk follows `Compose4BaseInfoSingleRead.u` at its edges.**
  - The buffer ending before a TLV's value starts (no len byte, or none after it, whatever the len) is a
    clean stop that keeps what was read (`u():257-267, :271-282`); before, it gave no state.
  - A 0xA5 colour group whose len isn't 1 + 4k is read with the app's record count, extra bytes ignored
    (VM `.o:112-116` -> `Controller4ColorInfoByGroup.Companion.q`); before, it stopped the walk.
  - With the piece count, a group the app can't place fails the read (group 0, or one starting past the
    last piece: `SubMode4Color.j:113-147` throws), and one starting exactly at the end is skipped; before,
    group 0 was dropped and a group past the end added phantom segments.
  - The last 0x07 device-info block wins (`w():370-437`): a basic block sets the serial when its UID is
    valid and always sets the versions (None when zero); a Wi-Fi block always sets the Wi-Fi MAC. Before,
    the first block won.
  - Not changed, waiting for spec types: a type-0 byte still ends the walk (u() reads it as an ordinary
    TLV), and a short 0x05 mode value is still padded.
  Captured bursts decode as before. Modeled from the decompiled app; not hardware-verified.
- **H61A8 segment colours are placed by the batch requested**, as goodsType 19's screen places them
  (rgbiclight `VM4Light.N5:946-962` -> `Controller4ColorInfoByGroup.Companion.h` / `i`, which drop the
  reply's group byte, -> `P5` with the loop's group -> `SubMode4Color.j`). Before, the reply's own batch
  byte placed them and a byte outside the walk dropped the batch (the legacy dreamcolorlightv1 `BleOpV3`
  screen, which goodsType 19 never opens). Bench replies carry the requested batch, so they read as before.
- **A raw brightness byte reads as the control screen shows it** (`DelegateHeader.i0:230-239` ->
  `ViewBrightness.b:353-365`): on a raw range (H6188's 20..254) the byte is clamped to the range before
  `calculatePercent`, so it reads 1..100 (before: 0 for a byte below 18); on the percent range [1, 100] the
  byte itself is shown. `SameModelCmdOp.E1` agrees on a raw range. Source-modelled (no H6188 on the bench).
- **The plugs send the `33 b5` time sync as their app does.** Every plug screen sends it in its connect
  read and waits for its ack (`SyncTimeController`, an `AbsOnlyWriteSingleController`): gt 43
  `H5083NewDetailRepository.necessaryMessages:323-339`, gt 50 / 90 `BleOpV2.o0:141-171` /
  `BleOpV4.l0:124-147`, gt 307 `BleOp4H5089.V:145-163`. The library now sends it once per connection,
  acked, right after the `33 b2` unlock (`Device._session_frames`); a refusal or no ack is only logged.
  Before, it went out unacked after every power write and never on connect. After each acked power
  write, gt 50 (H5082) and gt 90 (H5160 / H5161) re-run their connect read list
  (`BleOpV2.onEventSwitchV2:380-382` / `BleOpV4.onEventSwitchV2:299-301` -> `G()`), so those plugs
  (`power_resync`) re-read `aa 06` and `aa 07 03` and re-send `33 b5`; their per-outlet timer and delay
  reads are not modelled. gt 43 and gt 307 send nothing after a power write. Differences kept: on gt 50
  / 90 the library reads `aa 06` / `aa 07 03` once per device rather than before `33 b5` on every
  connect. Live-verified on an H5083 (gt 43, 2026-10-17, macOS): it acks `33 b5` (status 0) about 60 ms
  after the unlock, a power write is followed by nothing more, and the next session sends `33 b2` then
  `33 b5` again. gt 50 / 90 / 307 are source-modelled.
- **Per-segment brightness is 1..100, the app's slider range** (`ViewColorMode.t0:232-235`,
  `DelegateColorV2Part.t0:459-462`: `setProgressRange({1, 100})`). `Device.set_segment_brightness` and
  `build.segment_brightness` clamp 0 and below to 1 (before: 0 was sent), as `set_brightness` already did
  for the whole device. Affects the 0x15-scheme segment devices (H60A6, H6047, H6641, H6062).
- **The H60A6 status read asks for the 0xA5 colour groups only on the pacts the app does**: its
  request is `[0x41, 0x30]` plus 0xA5 if `J5()`
  (`VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.invokeSuspend:158-161`; `VM4LightH60A6.J5:691-697`
  -> `Support.supportPartColor4H60A6:434-436`: goodsType 303, pact type 1, pact code >= 2). An H60A6
  advertising pact (1, 1) now gets `ac 03 02 41 30`; (1, >= 2) and a device whose pact hasn't been heard
  yet keep `ac 03 03 41 30 a5`. A reply without 0xA5 leaves the segments as they were.
- **The H60A6 graffiti (0xA4) upload is sized to the link's MTU**, as the app sizes it
  (`H60A6GraffitiParse.makeBleController:240` -> `Compose4DefWrite4Multi.d:233-246` with
  `MtuConfig.getAvailableMtuSize` = ATT MTU - 3, at least 20). The frames are planned once the link is up
  (`send_unacked` with a function of `write_payload_size`), so a reconnect re-sizes them. A link that
  reports no larger size (a BLE proxy, BlueZ before 5.62) keeps today's 20-byte frames. The 0xA3 DIY
  upload is unchanged.
- **Kelvin, version text and section count are read as the app reads them** (the spec owner's gap
  batch). All of these are app-matching and source-modelled: they change only values the bench devices
  don't report.
  - H6641's 0x15 Kelvin is read signed (a plain `SubMode4Color`, `BleUtil.getSignedShort`): 0x8000 or
    more is no colour temperature.
  - H6047's two bar Kelvins are read signed (`cct_read_reply.kelvin_signed` / `kelvin_right_signed`;
    h6047 `Info4Detail` D0/E0 `getSignedShort`). A negative bar Kelvin is treated as "not in colour
    temperature", like 0.
  - H61A8's colour reports are read as its screen reads them, with `mode_color_parse` `"kelvin"` (as
    H6062 and fd4f794's `devices.yaml`). goodsType 19 opens the rgbiclight `AcNewDetail`
    (dreamcolorlightv1 `SubLib4BleIot` -> `Model4BleIot.t:41-46`), whose base
    `OpInfo4Detail.parseModeValidBytes:766-778` sends 0x15, 0x0b, 0x02 and 0x0d alike to `SubMode4Color`:
    `[gradual][kelvin s16be]`, colour temperature only for a Kelvin > 0. Before, only 0x15 gave a Kelvin, read
    unsigned (dreamcolorlightv1 `SubModeColorV2.parse`), which is the legacy AdjustAc screen goodsType 19
    never opens. Modeled from the decompiled app; not hardware-verified (the bench H61A8 rejects the
    library's colour write, so it sent no colour report).
  - Device-info version text is the spec's `version3.text`, each byte read signed
    (`Compose4BaseInfoSingleRead.v:363-368`, as chosen), so a byte of 0x80 or more renders negative.
  - The `aa 41` section count's `sectionNum` is a signed byte (`section_count_read`). The device already
    refused a count of 0x80 or more, so its segment count is unchanged.
  - A graffiti (sceneType 3) library scene is uploaded only when its blob leads with 0x01
    (`scenes_graffiti_param`; `ScenesOp.n` -> `ShareDiy.isValidGraffitiBytes`); otherwise it is only
    activated, as in the app. All 25 bundled graffiti scenes pass. The app's other two checks (size >= 9
    and a walk of the group headers) have no spec type yet and aren't made.
- **`wire.parse.parse_ic_count` reads the IC count signed, as the app does.** It uses the spec's
  `ic_segment_read.ic_count_signed`, after `ControllerOnlyReadIcSegmentNum` (`BleUtil.getSignedShort`).
  An `aa 40` count of 0x8000 or more is now negative, as in the app. Before, it was returned unsigned,
  and only `_readback.live_ic_pieces` sign-extended it. Live-IC colour read-back (H6641) is unchanged,
  because `live_ic_pieces` already read it signed. App-matching and source-modelled: not
  hardware-verified for counts of 0x8000 or more, which no bench device reports.
- `govee-ble-analyze` knows the 0xAC status TLVs 0x36 (bar switch; the H6038 / H6048 status read requests
  it, `VM4H6038`) and 0x40 (IC count; H6671 / H6672, `VM4LightH667172`), so their bursts are no longer reported
  as spec gaps with exit code 1. New constants `wire.reassemble.TYPE_BAR_SWITCH` and `TYPE_IC_NUM`.
- Segment setters refuse an index at or past the device's segment count (H60A6 13, H6047 10, H61A8 15),
  with `GoveeBleInvalidValue` as for any bad index. The app's position set has exactly that many positions
  and its mask encoders can't set a bit past it (`BleUtils.Y` / `BleUtils.X`); the library took any index up
  to 15 on a device of 16 segments or fewer, so `set_segment_brightness([13, 14, 15], ...)` on an H60A6 sent
  the mask `e0 00`, which the app never sends. Frames for valid indices are unchanged. (H6641 still uses its
  static 16 until its count is read live.)
- A custom `DeviceProfile` with `brightness_range=(1, 100)` writes the percent as it is
  (`BrightnessConfig.Companion.c`); it wrote one more for 51..100. No table profile uses that range.
- `wire.parse.parse_section_count` reads the H6062's IC count signed (`BleUtil.getSignedShort`), as its
  docstring said. Only diagnostics read it.
- 0xAC status burst: the last packet is the first of tag 0xFF and tag `packets - 1` to arrive once the
  others are in, as the app takes it (`AbsController4BleSingleSendMultiBack.isSameController`). The library
  always preferred 0xFF, so a corrupted burst carrying both spliced the wrong chunk in (a phantom segment).
  The H60A6 sends only 0xFF, so its bursts decode as before.
- `GoveeConnection.query` ends a 0xAC burst as soon as it is complete by the app's rule
  (`wire.reassemble.BurstAssembler`): a one-packet reply, or a last packet tagged with its position, no longer
  waits for the 0.6 s idle gap. The same frames come back.
- RX frames of proType 0xA2 (`multi_read`, device to app in the spec) are no longer logged at WARNING as
  unknown.
- `govee-ble-analyze`: each 0xAC chunk's length and BCC are checked like any frame's (an artifact, and the
  chunk is left out of burst reassembly, as the runtime drops it; a chunk zeroed by the capture masking is
  not), and the TX status request is read with the spec reader (label `status-request/<N>`).
- H60A6 scene upload plan (type 5): as the app's new-DIY path (`BaseSceneViewMode.r3`, parser order
  `H60A6DiyConfig`), the decoded blob must start with 0x50, it goes out as a DIY (0xA3) only when the spec's
  `diy_value` reads it with its length prefix covering the rest, else as a graffiti (0xA4) when
  `graffiti_value` reads it, else the scene is only activated. Before, a blob of any first byte was uploaded,
  by the length check alone. All 748 bundled scenes plan the same frames as before. The app re-serialises
  what it parsed (`IDiyParse.toBytes`); that is not modelled.
- H60A6 status read: each 0xA5 colour group of the 0xAC reply is now read with the app's record count for
  the device's 13 colour pieces: 4 per group and 1 in the last
  (`VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.o`; `pact_h60a0` `Support.getColorPieceSize` /
  `getOneGroupColorSize`), read by `Controller4ColorInfoByGroup.Companion.q`. Extra records in the last group
  no longer add segments 13-15, and a group short of its count fails the read, as it does in the app (no
  state is used from that burst). Before, the count came from each TLV's length. Source-modelled; the bench
  bursts carry the app's counts, so they decode as before.
- macOS: the connect failed on the H60A6 and H6008 (`Failed to discover descriptors for characteristic
  13 ... UUID not allowed`) because CoreBluetooth rejects descriptor discovery on a characteristic
  of the Generic Attribute service. The connection now resolves only the Govee service
  (`services=[SERVICE_UUID]`; bleak's BlueZ and WinRT backends apply the same filter). Live-verified on
  four H60A6s and two H6008s from a Mac.
- The device id of an H60A6 was reported as `"00"`: its `aa 07 10` UID is all zeros, which the parser
  rendered as a value and so skipped the `aa 07 02` fallback. An all-zero UID is now no id
  (`NumberUtil.isInvalidUuid`) and a single leading `00:00:` is dropped (`SnController`). Live-verified on
  four H60A6s.
- `create_device` ignored `DiscoveredDevice.advertisement` (the parsed `GoveeAdvertisement` that
  `discover()` returns): only bleak's raw advertisement set the encryption mode, so the natural
  `discover()` -> `create_device(..., d.advertisement)` flow fell back to the SKU profile. For an H6008
  (profile: plaintext; real firmware: encrypted, pact 1/1) every read was then sent in plaintext and
  went unanswered. Both advertisement types are accepted now. Live-verified on two H6008s.
- Transport: `BrokenPipeError`, `ConnectionResetError`, `EOFError` and a write's `TimeoutError` get the same
  reconnect-and-retry as `BleakError`; a write timeout is no longer reported as "operation exceeded 45s";
  `query()` keeps the frames already received when its budget runs out (a complete burst came back truncated
  under event-loop lag); a single-reply read waits its full 2 s (a stray late reply cut it to the 0.6 s burst
  gap); and messages name the type of a message-less error. Not hardware-verified.
- The BGC-info characteristic is found the way the app does (`BgcInfoReader.a`): the first service with UUID
  1910, then its 2b12 characteristic. A firmware that also exposes 2b12 under the OTA service 1912 (on
  backends that don't filter services), or two 1910 services that each carry 2b12, made bleak's
  collection-wide lookup raise and every connect fail. Modeled from the decompiled app; not
  hardware-verified.
- A secret set with `set_secret()` on a live session is sent (`33 b2`) before the first frame of any setter,
  scene upload or read-back on it, as the app's pairing flow does (`AbsPairAc4SecretV1.onEventSecretKey`).
  In 1.3.0 a secret set on an open session was sent only at the next session setup, so the commands on that
  link went out without it. It is not lost if that write is cancelled or fails while the session stays up; a
  finished write (acked, rejected or unacked) is not resent. An unchanged secret, or one set on a profile
  without `requires_secret`, queues no `33 b2`. Modeled from the decompiled app; not hardware-verified.
- Scene uploads: a short value (14 bytes or fewer, including 15 bundled H6004/H6006/H6008 scenes such as H6006
  "Asleep") is sent as a START frame (packet count 2) plus a separate empty END frame
  (`MultipleControllerCommV1.makeSendBytesV2`), not one 0xFF frame with no START. A compose scene (sceneType
  6) is uploaded only when its blob starts with 0x0a (`ScenesOp.o`); others, H60B0-style 0x51 blobs included,
  are activate-only (no current profile reaches this). Modeled from the decompiled app. Live-verified on an
  H6006 (2026-10-15, macOS): "Asleep" went out as `a3 00 01 02 01 83 01 ff e1 96 ...` then `a3 ff 00 ...`, the
  device acked the upload and the activation, and the next read reported scene 1134. The compose gate is not
  hardware-verified (no profile reaches it).
- Colour frames the app never sends are refused: per-segment colour and brightness on the 0x0d bulb schemes
  (`h6006`, `h6188`) raise instead of building a 0x15 RGBIC frame, an unknown colour scheme is refused
  instead of getting H60A6's frames, and an explicit H6061 panel count of 0 is rejected even for a Kelvin
  tint (it became 23 panels). Modeled from the decompiled app; not hardware-verified.
- 0xAC status burst framing follows `AbsController4BleSingleSendMultiBack`: a burst is complete only when
  every packet its first chunk declares has arrived, so a burst that lost the chunks before the 0xFF chunk
  no longer splices it in at the wrong offset and a lone 0xFF chunk isn't read as the first; a one-packet
  reply is complete by itself and is trimmed to its declared length, the last packet adds only its declared
  byte count, and an impossible last-packet length is rejected. Modeled from the decompiled app; not
  hardware-verified.
- Read-back values as the app reads them: 0xAC zone power is bit 0 of each zone byte
  (`ParseUtils.getBit2Boolean`); a 0x07 block whose selector isn't basic (0x10) or Wi-Fi (0x11) is stepped
  past even when its body doesn't parse, and the Wi-Fi block's versions no longer become the device's
  (`Compose4BaseInfoSingleRead.w`); gradual (`aa a3`) and H6047 bar power (`aa 36`) are on only for a byte of
  1 (`ControllerGradual4BleWifi.h`, `ComposeLightController.parseValidBytes`); device-reported brightness is
  clamped to 0..100. Modeled from the decompiled app. H6047 bar power is live-verified (2026-10-15, macOS:
  left bar off, then on, read back per bar); the other rules are not hardware-verified.
- H6006 / H6008 / H6004 / H6188 mode report decoded as the app does (`ModeParseStrategyConfig.a` via
  `OpInfo4Detail.parseModeValidBytes`): sub-modes 0x15, 0x0b, 0x02 and 0x0d all read `[r, g, b][kelvin
  s16be][tint]`; a Kelvin > 0 is CCT, 0 is the RGB, and a negative one shows the tint as RGB. `aa 05 15 01 0a
  8c` now gives RGB (1, 10, 140) instead of 2700 K, and a 0x0b / 0x02 report updates the colour. Modeled from
  the decompiled app. Live-verified on an H6006 and an encrypted H6008 (2026-10-15, macOS): their 0x0d reports
  arrive as `[r, g, b][kelvin][00 00 00]` and decode to 2700 K after a CCT write and to RGB (16, 32, 48) after
  a colour write. 0x15 / 0x0b / 0x02 reports and the H6004 / H6188 are not hardware-verified.
- H6061: an `aa 05 15` report no longer clears the known RGB colour or stores a nonsense Kelvin (e.g. 511 or
  65454), since `CubeNewInfo4Detail.U` decodes only the 0x0d report; and that report's Kelvin is a signed
  short (`BleUtil.getSignedShort`), so 0x8000 or more reads as RGB, not a 32768 K colour temperature.
  Modeled from the decompiled app; not hardware-verified.
- Other mode reports: on H61A8 an `aa 05 0d` report no longer sets an RGB colour (it is a
  `SubMode4Color` `[gradual][kelvin s16be]` report, see the H61A8 entry above); H6062 reads 0x0b / 0x02 / 0x0d
  with the `SubMode4Color` parse (`OpInfo4Detail.parseModeValidBytes`); H6641 and H6047 ignore 0x0d
  (`Info4Detail.parseModeValidBytes`). A scene report clears stale colour state and a colour-temperature
  report of 0 clears the Kelvin. Modeled from the decompiled app. A colour-temperature report of 0 clearing
  the Kelvin is live-verified on an H60A6 (2026-10-15: 4000 K, then red, then `aa 05 15 01 00 00` read back no
  Kelvin); the rest is not hardware-verified.
- Plugs follow the app per goodsType. The plug profile is split by goodsType into H5080/H5083/H5085,
  H5082/H5089 and H5160/H5161, so `profile_for` returns three different plug profiles. Relay read-back: bit
  0 on H5080/H5083/H5085, any bit on H5082/H5089, bits 0-2 on H5160/H5161 (`BleOpV1.onEventSwitchV2`,
  `EventSwitchV2.i`, `BleOpV4.Z`; 1.3.0: any set bit meant on). Advertised on/off: bit
  0 on H5080/H5083/H5085 and any bit on H5160/H5161 (h5080 `Model.A`), bit 0 or 1 on H5082/H5089
  (`Model4Plug2.A`); before, only a byte of exactly 1 meant on, so 0x11 read as off. Modeled from the
  decompiled app; not hardware-verified.
- Advertisements: on/off is read only from broadcast headers 2 and above (`BleUtil.parseBleBroadOnOff`), so
  v0/v1 adverts no longer flip power, and `identify.parse_broadcast_onoff()` returns None for a header byte
  (the company-id low byte) below 2 or above 0x7F (negative as the app's signed byte); the advertised on/off
  is ignored for 15 s only after an on/off read or a command, not after an empty, failed, brightness-only or
  mode-only read; an advertised power change keeps zone and bar power consistent, so a later H6047 bar write
  no longer lights an untouched bar; and a local name ending in `_` gives the app's SKU
  (`BaseBleProcessor.parse`). Modeled from the decompiled app; not hardware-verified.
- Per-segment colour read-back: H6641 derives its piece and group count from the IC count as the app does
  (clamp 41..70, ceil(IC / 5) pieces, at most 4 reads; `ReadIcController`, `H61D3Support.e`), so it shows no
  phantom segments and never walks 255 groups; replies are placed by the group requested, on H61A8 too (see
  the mechanism-B entry). An `aa 40` IC count of 0x1000 or more (read signed) skips the segment walk, as
  the app never stores it (`ReadIcController.Companion.a`). A custom profile with `color_readback_live_ic` and
  no `color_readback_ic_range` uses the IC count unclamped. Modeled from the decompiled app; the H6641 change
  is not hardware-verified. The H61A8 15-segment read-back still assembles on hardware (2026-10-15, macOS).
- Device-info reads are tried again on the next poll when the device answered none of them and that poll's
  state read got nothing either. 1.3.0 marked them done after one unanswered attempt, so a device first
  polled while silent (wrong cipher, out of range) never reported its versions. A late Wi-Fi info reply can
  no longer overwrite the firmware or hardware version. Modeled from the decompiled app; not
  hardware-verified.
- State: segment colour, segment brightness and zone colour writes clear the active scene, matching bar
  Kelvins clear a stale RGB colour, concurrent whole-device power or scene writes and H6047 per-bar writes
  no longer undo each other, and `set_zone_power` on H60A6 replaces `state.zone_power` instead of editing
  the dict in place. Not hardware-verified.
- A scene's upload and its activation are one unit, as the app sends them in one composed controller:
  neither a poll nor a concurrent scene write can land between them. `set_scene_by_name` loads the bundled
  catalog off the event loop, `update()` warms it, a catalog that exists but can't be read raises
  `GoveeBleError` instead of `OSError` / `UnicodeDecodeError`, and `active_scene` is None on a device
  without `SCENES`. Modeled from the decompiled app; not hardware-verified.
- Frame capture: creating one (`frame_log=` / `GOVEE_FRAME_LOG`, built on the event loop) no longer touches
  the filesystem. The directory check runs on the writer thread and disables only that capture, with one
  warning (other captures on the same path keep writing). It warns once when it appends to a group- or
  world-accessible file (POSIX; the mode is kept), reports a write failure again after the path recovered
  and a full queue on each new overflow, and registers its exit-time flush at the first capture, not import.
- Cloud: `CloudDevice`'s repr leaves out the BLE `secret`, so logging the device list can't leak the
  account-lock key. A JSON null `sku` / `device` / `deviceName` no longer becomes "None", huge or
  non-integral `pactType` / `pactCode` / `goodsType` are ignored rather than raising `OverflowError` or
  being truncated, and a non-integral `scenceParamId` or a body nested too deeply for the JSON decoder
  raises `GoveeCloudError` instead of `OverflowError` / `RecursionError`.
- `govee-ble-analyze` reassembles 0xAC bursts per device, so overlapping bursts from several devices no
  longer produce phantom "TLV type not modelled" issues and exit code 1. A double-delivered burst isn't
  malformed, a burst missing interior or final chunks is, a poll that lost its 0xFF chunk is no longer
  folded into the next poll with the same first chunk, and a TLV that won't parse is reported as a gap.
- `govee-ble-analyze` no longer reports every burst of a redacted field-test capture as "malformed
  (dropped/merged frames)" (older captures, whose first-chunk header was zeroed, are judged by chunk index).
  `analyze()` skips and counts invalid records instead of raising `ValueError`, a deeply nested JSONL line
  or a 1-byte `ac` record no longer crashes the CLI, `--from-frames-log` keeps the logger name out of the
  address, and an unrecognised `dir` reads as `?`.
- Field test, report: error messages (failed sends, read-backs, restores, aborted runs) no longer contain the
  device's Bluetooth address, Wi-Fi MAC or the MAC digits of its advertised name. Every error string, and the
  report as saved, goes through one scrubber that masks the address in any form (BlueZ D-Bus paths included),
  any other MAC-shaped text and Govee-shaped names (`ihoment_H6188_xxxx`). Snapshots always redact the device
  id and BLE MAC too. A device identified as one SKU (`ihoment_H6006_6188` is an H6006) no longer matches
  another SKU's plan through the digits in its name, and Ctrl-C at a prompt no longer hangs the tool.
- Field test, capture: the frame capture keeps no wire bytes (the ciphertext and the `e7` handshake, from
  which the session key can be derived) and zeroes the `ac` status payloads like the device-info replies,
  keeping only each burst's 7-byte first-chunk header so `govee-ble-analyze` still spots a lost chunk. It is
  redacted only after `framelog.flush()` (at once if that fails, never left raw), and the report and capture
  go through a fresh temp file and an atomic rename, never a planted symlink.
- `tools/decrypt_btsnoop.py` decodes the exact-length 0xA3 / 0xA4 / 0xA6 multi-packet frames the app sends
  (MTU-sized and short; `MultipleControllerCommV1.makeSendBytesMtu0xA6`) instead of dropping them, and
  dumps 0xA6 by default (`--dump a1,a3,a4,a6`; `--starts` includes 0xA6). It takes a session key only from
  an `e7 01` reply after the host's `e7 01` on the same connection and keeps keys per connection; it and
  `btsnoop_mtu_scan.py` refuse captures that are not H4 (datalink 1002) instead of reporting nothing.
- `tools/fetch_scenes.py`: `--audit` honours the SKUs given, shows their real scene versions and puts every
  scene in one bucket; a re-fetch replaces stale upload metadata and picks up changed public blobs (keeping
  a `--resolve`d blob only over the 0xFF stub); a corrupt catalog or failed write fails that SKU instead of
  the run. `tools/power_semantics_probe.py` restores the H6047 with its own `33 36` bar frame and prints
  `RESTORE FAILED` when a restore isn't acked or the starting state was never read; `h60a6_live_check.py`
  matches segment colours by index and `--capture` uses `frame_log=`.
- `tools/field_test`: the report and frame capture meant for a public GitHub issue no longer leak the
  Bluetooth address. Error text (most transport errors start with the address) is scrubbed of the address in
  any case and form, the Wi-Fi MAC derived from it, every other MAC-shaped token (BlueZ's `dev_XX_XX_...` path
  included) and the MAC digits of Govee names (`ihoment_H6188_xxxx`), and the whole report is scrubbed again
  as it is saved. The capture keeps the first 0xAC chunk's header, so a lost chunk is still detectable, and
  zeroes every data byte.

### Spec
- `33 05 15 05` (op15_kelvin, H60A0): the Kelvin is now spelled `u2be` (it already read high byte first; the
  generated code is unchanged). The app writes it high byte first (ControllerBuildH60A0.d), and no firmware image
  handles op 05: on H60A6 it falls to the default branch, which logs 'Unknow msg'. The citations point at that
  binary path. The catalogue keeps one builder, `build_op15_kelvin_05`. `build_op15_color_temp` and
  `build_op15_color_temp_05` were byte-identical duplicates and are removed (759 self-checks). A reference check
  reads `33 05 15 05 0a 8c` back as 2700 K. The last decompiler-style quotes in devices.yaml notes are rewritten in
  spec terms.
- The spec is restructured into nine Kaitai files that compile in one `ksc` call
  (`-t python -w --python-package .`). They replace `govee_ble.ksy` and `govee_adv.ksy`, and every documented
  fact and citation of the old files has moved to a new home.
  - Shared files:
    - `govee_common.ksy` (LIBRARY): the shared enums (`command`, `pro_type`, `sub_mode`, the 18 `form_*`
      selectors, `color_decode`, `music_decode`, `group_count_rule` and others) and the context-free leaves
      (`rgb`, `level`, `seg_mask`, `echo` and others).
    - `govee_profile.ksy` (CONTEXT): `govee_profile(forms, counts, group, power, mode, mode_status)`, the device
      facts a reader needs. Value 0 of every selector is `unknown`, which leaves an overloaded body as raw bytes.
    - `govee_state.ksy` (SHARED PAYLOAD): `govee_state(command, source, profile)`, one type for an `aa` reply
      body and a 0xAC TLV value.
  - Six STANDALONE roots, one per byte source:
    - `govee_ble_frame(direction, profile)`: one GATT 2b11 write or 2b10 notification, `[pro_type][body]
      [checksum]`. `route` (direction and proType) picks the body; `checksum_kind` says xor, sum8 or none.
    - `govee_status_stream(profile)`: one joined 0xAC status burst, read as TLVs and a 0-2 byte tail.
    - `govee_upload(kind, form)`: a joined A1 / A3 / A4 value or a decoded cloud scenceParam.
    - `govee_manufacturer_data`: the 0xFF manufacturer AD payload (was `govee_advertisement`).
    - `govee_scan_record`: a raw advert or scan-response PDU.
    - `govee_bgc_info`: the GATT 2b12 value.
  - The rules:
    - Each message has one type path.
    - Context is params only: no `_root`, `_parent`, `io` / `pos` instances, `process` or opaque types.
    - Every message ends with a lossless `rest` or a named data field.
    - Every enum is applied to a field.
    - Every form the app reads or a firmware sends reads without raising; the app's rejections are `app_*`
      instances.
- `devices.yaml`: every family with a `client_profile` gets a `wire` block, from which the library builds
  `govee_profile`.
  - The block holds `forms`, `counts`, `group`, `power`, `mode` and `mode_status`, with `by_model` /
    `by_goods_type` / `by_pact` / `by_firmware` overrides.
  - It adds `relay_heartbeat_on_mask`: 0x03 for goodsTypes 50 / 307, 0x01 for 43, 0x07 for 90.
  - `ksy` and `mode_0d_kind` are removed; the `govee_common` enums hold their facts.
  - `devices.schema.json` gains the matching `$defs`, with the selector enums taken from `govee_common.ksy`.
- New `spec/checks/run.sh` (Suite S).
  - It runs ksc for python and java with `-w`, then javascript, and fails on any compiler output.
  - Then `checks/lint.py` checks the meta keys, the banned constructs, the six labelled roots and
    reachability, that every enum is applied, schema and `mode_color_decode` enum equality, and the doc-ref
    format.
- `govee_reference.py` uses the new classes.
  - `parse_frame(frame, direction, profile)` reads any frame length and checks the trailer by `checksum_kind`.
  - `parse_status` returns `govee_status_stream`.
  - New `make_profile(**facts)` and `checksum_ok`.
  - `parse_color_group` is removed.
  - The docs (`USING_THE_KSY.md`, `OPCODE_COVERAGE.md`, the GATT, firmware and scene-upload notes) name the
    new files and types.
- Review fixes to the new files:
  - `33 05 15 05` has three app layouts, so it gets the selector `f_15_05` (`form_15_05`): `kelvin` [kelvin
    u16be] (H60A0, SubModeColorV1 opType 2), `side_brightness` [side][value] (Bytes.H, goodsType 80) and
    `gradient` [3 x RGB][mask] (Bytes.n). The Kelvin was read little-endian; the app writes it high byte
    first. `govee_commands.build_op15_color_temp` now writes it big-endian too. devices.yaml sets `kelvin`
    on goodsType 188 and `side_brightness` on 80. The `op15` enum docs give the right opTypes (0x05 is
    opType 2, 0x00 is opType 3).
  - A 0x15 01 colour write, and a 0x0b rgb_mask write, stay raw (bytes in `rest`) when the profile has no
    segment count. Before, any set mask bit raised.
  - `checksum_kind` builds its enum from an integer, so the generated Java compiles.
  - `ic_count` (aa 40 / TLV 0x40) reads the second [IC][segment] pair at bytes 3..5 (`ic_count_2`,
    `segment_2`), with `segment_signed`, `has_second_pair` and `ic_total` (the sum the goodsType-375 and
    H6609 / H6250 screens use).
  - `color_group` flags a group the TLV screen cannot place (`app_rejects_group`, `app_read_fails`) under rule
    `fixed` too, when `num_pieces` is known. devices.yaml gives h6047 goodsTypes 277 / 288 / 298 their piece
    counts (12 / 24 / 22).
  - An aa a2 colour-only group (`f_a2: color_group_rgb`) always reads 3-byte records. Straightfloorlamp
    goodsType 30 sets that form, for its reads below the firmware gate.
  - Doc fixes: `f_ee_30` unknown still reads the EE 30 bytes; the plug heartbeat masks of goodsTypes 43 / 90
    are verified; two devices.yaml / schema cross-references name the new types; seven docs that copied
    decompiled code are prose now. In Java an unknown proType still reads as null (ksc 0.11 cannot put an
    imported enum on a value instance); the frame doc says so.
- Docs trimmed to essentials (docs only; generated code and bytes unchanged). The nine `.ksy` files go
  from 9405 to 6758 lines, `devices.yaml` from 3788 to 3513 (comments only), and `devices.schema.json`
  stays at 876 (shorter `description` strings): 14069 to 11147 lines in all. ksc's output differs only in
  docstrings, plus the order of two sibling imports in `govee_scan_record` and `govee_state`.
- `devices.yaml` striplight `color_temp_write` cites the BLE builder the colour-tab Kelvin slider runs
  (`ColorViewModel.U` -> `ColorModeControllerConfig.c` -> `Config.Yt` -> `Bytes.r`). It used to cite
  `ColorControllerConfig.b`, which builds the IoT command. The frame bytes are unchanged.
- `devices.yaml` dreamcolorlightv1: a comment records that `color_scheme: h61a8` (the 0x0b write) describes
  only colour version 3 (17 / 18 below their firmware gates, old H6163 / H6117, part brightness off). The 13
  goodsTypes 71 to 262 send the h60a6 0x15 frames. `color_scheme` is per family, so the value stays.
- LOCKSTEP (merge together with the library edits in spec/docs/LOCKSTEP_FRAME_AUDIT_E.md): the section-E forms
  that change what existing readers return.
  - `single_command.params` types 33 B2 / A3 / 14.
  - `mode_read.selector_or_sub_mode` takes `enum: sub_mode`.
  - `status_tlv` 0x07 reads `status_device_info`.
  - `status_tlv` 0x05 reads `mode_read` over the exact value, so `cct_read_reply.kelvin_right` is read at
    `_io.size >= 5`.
  - `color_group_status.records` is `(len - 1) / 4` plus `trailing`.
  - Without the library edits, 35 tests fail on each library branch (32 from the B2 / A3 builders, 3 from the
    0x07 walk). With them, both branches pass.
- `devices.yaml` now catalogues the light families and goodsTypes that Govee Home 7.7.02 routes but the file
  did not model: 148 more light goodsTypes (113 -> 261 non-zero) and 205 more SKUs.
  - How it was traced and checked:
    - Each family was traced in the complete 7.7.02 decompile from its routing (PFD module, SubLib keys,
      `Model.t`) through the control screen's connect reads, mode parser and colour builders, with every claim
      cited `path:line`.
    - Each trace was then re-traced independently from both ends before it went in. Corrections from that pass
      are applied, and every Java citation resolves (4244 citations).
  - 40 new families:
    - pact_rgbiclight: `chassislightv1` (11 / 162) and `h7090` (44 / 127).
    - pact_bulblightstringv1: `bulblightstringv2` (35 / 148 / 49 / 91).
    - pact_hollowlamp: `straightfloorlamp` (30 / 69 / 312 / 377).
    - pact_h6061: `h6069` (284). The cubelight screen sends it whole-device 0x0d frames, so it is its own
      entry.
    - pact_floorlamp: `h6071` (57 / 67), `h60b1` (300), `h60b0` (301).
    - pact_h6079: `h6079` (156 / 209 / 376), `h6020` (210), `h6022` (249), `h1741` (365).
    - pact_h6840: `h6840` (278 / 325 / 381 / 382) and `h6609` (173 / 181 / 260 / 362 / 374 / 378 / 392 / 398).
    - pact_h70b1: `h70b3` (229 / 230 / 231 / 255 / 256), `h6800` (272), `h70b6` (329 / 341), `h70dx`
      (267 / 268 / 269).
    - pact_h6099: `pact_h6099` (191 / 203 / 253 / 273 / 327 / 336).
    - pact_outdoorlight: `h7040` (36), `h7080` (45 / 55 / 56), `h7056` (304), `h7086` (293).
    - pact_h7050: `h7050`, `h7057`, `h1630`, `h7045` and `h7020`, all 42 of its goodsTypes.
    - pact_h7075: `h7075`, `h7078`, `h3751`, `h7072`, `h7076`, `h707a`, `h3700`, all 9 of its goodsTypes.
    - pact_fanlight: `fanlight` (359 / 368), including the fan opcodes (0x31).
    - pact_h6092: `h6092` (13 projector goodsTypes).
    - pact_h6630: `h6630` (285 / 286). Its control screen is IoT-only.
    - `h3a5x` (386) and `h682021` (280) are `untraced`, as h604a is: their feature splits are not in the
      capture.
  - Further goodsTypes and SKUs on existing families:
    - `striplight`: H610A (80) and H610B (114) run the same screen and parser as H6062.
    - `h6061`: 96 / 97 / 121 / 163 / 228. Panel counts and CCT mask widths differ per goodsType.
    - `dreamcolorlightv1`: the 13 further SubLib4BleIot keys, 71 to 262, all `kelvin`. AdjustAcV3 is never
      launched in 7.7.02.
    - `bulblightv3`: the R-prefixed SKUs (R1501, R14A1, R14C2, R1401, R14C0, R14C1). They route by goodsType
      to the same screen.
    - `h61d3` 263: the 0xAC status read and its TLV 0xA5 record.
  - New strategies:
    - `rgb_kelvin_15`: H6071 / H6075 read the rgb_kelvin_0d body on sub-mode 0x15.
    - `kelvin_14_15` and `gradual_14`: straightfloorlamp's colour sub-mode is 0x14.
    - `bri_rgb_pair_15`, with the new decode `bri_rgb_pair`: H7061's two [brightness][r, g, b] head records.
  - `devices.schema.json` accepts the new strategy and decode names, sub-mode "0x14", and `power_live`
    `heartbeat_aa33`, the aa 33 left / right heartbeat of H610A.
  - `encryption: secret_key_pairing` is set only where the control screen presents 0xB2 on connect: h6071,
    fanlight from firmware 1.00.20 / 1.00.11, and h6630's square path. A secret used only by pairing, group or
    room control is `none`, as for h6061 and striplight.
- Frame-audit section E: the spec forms the library still builds or decodes by hand. Each was traced in the
  Govee Home 7.7.02 decompile and checked by an adversarial reviewer. Additive only: 9 standalone types wired
  into no dispatch, and value instances on existing types. No `pos` instances, so a written type is never
  re-written from them. No enum is applied, no `switch-on` case is added, and no existing field or expression
  changes. The harness (2,006,142 cases) shows 0 differences, and both branches' suites pass (2053 each).
  - Requests:
    - `read_request_sized(selector_len)` and `read_request_frame` write `aa <cmd> <selector...>` with the zero
      fill the app's frames carry.
  - Segment masks:
    - `segment_mask(segment_count, mask_cap)` gives the app's width rule (2 bytes up to 16 segments, else
      ceil(n/8), capped per body) and the per-segment bits, LSB first.
    - `op15_brightness_wide.mask_cap` (14) and `color_rgbic_0b_wide.mask_cap` (13).
  - Writes:
    - `single_command_typed` types every 0x33 write body, including `33 B2` secret, `33 A3` and `33 14`
      gradual. `single_command` itself is unchanged; the in-place cases are in the lockstep branch.
  - Mode reports:
    - `mode_read.sub_mode` is the `sub_mode` enum view of the byte. `mode_read.is_submode4color` covers
      0x15 / 0x0b / 0x02 / 0x0d, as `OpInfo4Detail.x` hands them to SubMode4Color.
    - `mode_read_params` exposes the raw params.
    - `cct_read_reply.is_cct_positive` and `mode_color_0d_typed.is_cct_kelvin_positive` give the default
      SubMode4Color rule, signed kelvin > 0.
  - Status bursts:
    - `color_group_status_counted(piece_count, group_size, has_brightness)` counts a 0xA5 group's records
      from its group_index, as the app does.
    - `status_tlv.total_len` and `status_device_info.app_decodes` (selectors 0x10 / 0x11).
  - The `aa a5 <group>` direct reply:
    - `color_group_direct_frame(record_count)` reads it, keeping the padding so it round-trips.
  - Plugs:
    - `switch_read_reply.relay0_is_on` .. `relay7_is_on`.
    - `any_relay_is_on`, `any_relay_0_1_is_on` and `any_relay_0_2_is_on`: the per-goodsType read and
      heartbeat rules.
  - IC count:
    - `ic_segment_read.ic_count_byte_order_suspect` (signed count >= 0x1000) and `ic_count_checked`.
  - Library-scene upload validators, so a failed check can make a scene activate-only as the app does:
    - `scenes_graffiti_check` mirrors ShareDiy.isValidGraffitiBytes exactly: size >= 9, the 0x01 lead and
      the group-header walk.
    - `rgb_scene_value.is_valid` mirrors ScenesRgb.isValidProtocolBytes.
  - Held back as lockstep (they change what existing readers return): the single_command params cases,
    status_tlv 0x07 -> status_device_info and 0x05 -> mode_read, and the sub_mode enum applied to
    mode_read's field. They are measured on a separate branch for the library owner.
- `devices.yaml`: non-colour data and citations that the complete Govee Home 7.7.02 decompile contradicts
  are corrected. Every item was re-read in the code, and every Java citation in the file now resolves
  (`file:line` within the file).
  - `delivery`: 42 families move from `base_apk` to `feature_split`. 7.7.02 ships them in on-demand
    pact_* splits (pact_rgblight, pact_rgbiclight, pact_bulblightstringv1, pact_hollowlamp, pact_plugnew,
    pact_h705a, pact_h70b1, pact_tvlightv4, pact_h6099, pact_h604a), mapped by goodsType or SKU in
    `base2home/pfd/Config.b()`. Each line names its module and Config line. h1162 stays `base_apk`: no
    module claims its goodsTypes. The schema enum is unchanged; its description now says where the mapping
    lives.
  - h7004 `red_blue` is cmd 0x0d with payload `[red, blue]`, not 0x01. The plant screen writes
    `33 0d <red> 01` and reads `aa 0d` at connect. The 1/2/3 codes of the legacy SubModeRedBlue classes
    are never written. h7017 gains the same opcode.
  - h6185: the `mic` opcode entry is removed. Mic is mode sub-mode 0x05 (`33 05 05 01`), and 7.7.02 has
    no rhythm-point controller.
  - tvlightv1: the sub-modes were right, but only the legacy `Mode` (0x0a / 0x0d) was cited. Scene 0x04
    and music 0x0e are what the goodsType-12 control screen writes, now cited. `segment_limit` (0x0e,
    `ControllerLimit`) gains its payload and read.
  - h61d3: the connect switch is `VM4Light.d0` on `H61D3Support.l0` (goodsType 263), not `f0`, which is
    goodsType 299. The per-group read is pieces = ceil(IC / 5), groups = ceil(pieces / 4)
    (`VM4Light.w5` / `x5`), not "group count = ceil(IC / 5)". The library already reads 4 records per
    group.
  - bulblightv3: `checkSupportH6005Fuc` does pick H6001's colour sub-mode (0x0d when it holds, else
    0x02, on the h6001 automation and effect paths). It never applies to H6006 / H6008.
  - `secret` and the scene-dispatch sources: line numbers and field names updated to 7.7.02
    (`UrlConstants` p / y / f386y0, `Constant` e / f, `DefInfo` :106, `AbsPairAc4SecretV1` T1 /
    onEventSecretKey, `SecretKeyConfig` in base2home/scenes, `Diy.parseScenes4Diy` :95, `DiyGraffitiV3.a`
    :128). The matching §18.5 text in `GOVEE_BLE_GATT_PROTOCOL.md` is corrected too.
- `govee_reference.load_profiles` follows the app's family routing: cloud goodsType when non-zero, SKU only
  at goodsType 0. It used to map each SKU to the last family that listed it (H6102 -> dreamcolorlightv2).
  It now returns `{goodsType | goodsType-0 SKU: client_profile}`, rejects a key two families claim, and
  `profile_for(profiles, sku, goods_type)` does the lookup. The self-test checks the H6102 and H6001 routes.
- `devices.yaml` now records every light goodsType's colour-report strategy: how its Govee Home 7.7.02 control
  screen decodes the colour sub-modes 0x15 / 0x0d / 0x0b / 0x02 of a mode report it gets over BLE (the aa 05
  reply, or the 0xAC TLV 0x05).
  - How it was traced and checked:
    - Every goodsType and SKU-routed family was traced from its routing through to each colour decoder, with
      file:line citations.
    - Each trace was re-traced independently.
    - Spot-checkers re-traced over 330 samples from scratch across two rounds, and all of them matched.
    - The second round re-checked everything against the complete 7.7.02 decompile.
  - New top-level sections:
    - `mode_color_decode`: 19 body decodes. Each gives the byte layout, the Kelvin signedness, the rule for
      when the app treats the report as a colour temperature, and the matching ksy type.
    - `mode_color_parse`: 33 strategies, each a map from sub-mode to decode.
    - The library's strategy names are reused wherever the app's decode is exactly that strategy.
  - Per family: `client_profile.mode_color_parse`, with overrides `by_goods_type`, `by_model`,
    `mode_color_by_pact`, `mode_color_by_firmware` and `mode_color_parse_status`.
    - Coverage is 49 families and 113 goodsTypes.
    - h604a stays `untraced`: its feature split is not in the 7.7.02 capture.
  - `families[].assigned_goods_type` (18 items) records the goodsTypes the app assigns legacy SKUs itself, so
    a client can resolve their strategy:
    - BK-firmware rewrites to 39 / 40 / 17 / 44;
    - new-firmware rewrites to 17 / 18 / 19 / 20;
    - the two pairing binds, H613A-F to 39 and H615A-E to 40.
  - The section header records the app's routing rules:
    - family lookup by cloud goodsType, or by SKU at goodsType 0;
    - the device-list and scan-time pact checks;
    - when an entry is split by pact.
  - `devices.schema.json` gains every new property, strictly typed.
  - `mode_0d_kind` corrections:
    - tablelampv1 goodsType 128 (H6078) is `cct`, not the inherited `rgb`.
    - bulblightv3 goodsType 0 (old-firmware H6001) is `unmodelled`.
    - bulblightv3 goodsTypes 9 / 10 (IoT-only) and 311 (H601E/F, no 0x0d decode) are `none`.
  - Families gain the 46 goodsTypes and 43 models the app routes to them, each cited. H6184 leaves
    dreamcolorlightv2; it is chassislightv1.
  - Colour-report prose that the 7.7.02 code contradicts is corrected. Examples: s16be where the text said
    u16 BE / u2be; the right parser classes; source line numbers that ran past the end of their files.
  - `govee_ble.ksy` (doc text only): H61A8 (goodsType 19) reads the 0x15 Kelvin SIGNED. In 7.7.02 its screen
    is the rgbiclight `AcNewDetail`, whose `OpInfo4Detail.x` decodes 0x15 / 0x0b / 0x02 / 0x0d alike. The
    unsigned `SubModeColorV2.parse` serves only the legacy dreamcolorlightv1 screens, which goodsType 19
    never opens. This corrects the item-16 doc in the gap batch below.
  - `cct_read_reply`'s doc names the newdetail decoder and says the H6047 bar Kelvins are read signed.
  - Additivity: the readers are unchanged (harness: 2,006,142 cases, 0 differences). Both branches' suites
    pass (2037 each).
- Spec gap batch: the decodes the library still hand-codes, now in the spec so it can follow later. Each item
  was traced in the 7.7.02 decompile (firmware-sourced ones are marked with image and handler) and checked by
  an adversarial reviewer. Additive: no new enum is applied to a field, no `switch-on` gains a case, and no
  existing field, enum member or expression changes, except the two cases below. The additivity harness
  (1,975,202 cases against the 994ea64 readers) differs only in the 300 `mode_color_0d_typed(kind=3)` cases.
  Kind 3 used to read nothing and no caller passes it. The two intended `trailing` fields are excluded from the
  comparison. Both branches' suites pass with this spec (2037 each, the same as before).
  - New enums, applied to no field: `plug_command`, `light_power` / `relay_write` (the `33 01` value rules),
    `device_info_selector`, `status_tlv_type`, `rx_pro_type`, `encrypt_version`, `mode_0d_kind`,
    `op15_color_variant`, `mode_read_selector`, `multi_ac_command`, `multi_comm_byte` and `a3_start_marker`.
    Member names avoid the YAML 1.1 booleans (`light_on` / `light_off`).
  - New standalone types, wired into no dispatch:
    - `total_switch_write` (H5089 `33 02`).
    - `rx_frame`, a device-sent frame typed by proType that never raises on construction, with
      `multi_ac_reply_chunk` (the 0xAC chunk header, last_len checked as the app does) and `write_ack`.
    - `section_count_read` (`aa 41`, signed), `status_reply_safe` (an empty TLV buffer gives no entries) and
      `pact_read_reply` (`aa ef`).
    - `wifi_mac_read` (`aa 14`), `handshake_key_reply_data` (the E7 01 session key) and `bgc_info`.
    - `device_info_ble_mac` (`aa 07 06`) and `aging_flag_read` (`aa ff 08`), both firmware-sourced; the
      second is marked test-only.
    - The scene-param wrappers `scenes_graffiti_param`, `compose_scene_param`, `h60a6_scene_param` and
      `h6052_graffiti_param`.
    - In `govee_adv.ksy`: `scan_record` / `scan_ad_structure` (the app's scan-record AD walk) and
      `manufacturer_data_lenient`.
  - New instances on existing types:
    - `mode_read.scene_code`, and `cct_read_reply.kelvin_signed` / `kelvin_right_signed`.
    - `status_zone.zone_a_on` / `zone_b_on`, `bar_switch_read.left_on` / `right_on` and `gradual_read.is_on`.
    - `secret_read_reply.is_valid`, `sync_time_payload` / `plug_sync_time_payload.marker_is_valid` and
      `notify_wifi.is_connected`.
    - The uid / Wi-Fi-MAC validity tests on `device_info_basic` / `device_info_sn` / `device_info_wifi`.
    - `version3.text` with `is_zero` and the signed parts.
    - `manufacturer_data.is_govee_pact` / `has_switch_state` / `switch_byte`.
  - The two exceptions:
    - `mode_color_0d_report` and `bulb_group_color_read_v2` gain a `trailing` size-eos field. It holds the
      bytes after the decoded part and sits inside the body substream, so it never takes the BCC.
    - `mode_color_0d_typed` reads kind 3 as `[r, g, b][kelvin s16be]` from 5 bytes (h60a1 `Info4Detail`,
      cube `CubeNewInfo4Detail`).
  - User decisions applied:
    - `bgc_info` is lenient like today's library rule: it never raises, and `effective_encrypt_version`
      follows the library where the app throws (item 19).
    - `version3.text` matches the app's signed `x()` rendering, e.g. `81 05 F6` -> `-127.05.0-10` (item 21).
  - `devices.yaml` h5080 `client_profile.by_goods_type`, traced per goodsType: `relay_power`
    (`outlet0` / `all_outlets` / `total_switch`), `relay_on_mask` and `advert_on_mask` for 43, 50, 90 and 307.
    These come with the matching `devices.schema.json` properties.
  - Doc and citation fixes:
    - `device_info_read` / `read_request`: the H5086 `aa 07 10/11` senders are live, there are more
      `07 07` senders, and `device_info_selector` lists the `07 08` selector.
    - Corrected decode docs for `status_seg_info`, `bar_switch_write`, `op15_color_write` and the H60A6
      scene-upload rule.
    - Advert and GATT-doc line citations.
  - Catalogue: the `read_mode_bare` / `read_mode` docstrings, and their `OPCODE_COVERAGE.md` entries, cite
    the real senders (H6057 sends `aa 05 01`). The secret-key builder raises unless the secret is 0 or 8
    bytes.
  - Skipped:
    - 25-ext (per-goodsType 0x15 colour-strategy data in `devices.yaml`): the 0x15 signedness is recorded
      per SKU in `cct_read_reply.kelvin_signed`'s doc. A `by_goods_type` field needs each goodsType's detail
      screen traced first.
- Firmware-doc corrections. Every claim the spec makes about the six decompiled firmwares (H6047, H6006,
  H6008, H5083, H60A1, H60A6) was re-checked against the images by disassembly, and each correction had an
  adversarial check. Text and comments only: the ksy structure with docs removed, and its enums, are
  identical to before, so the generated readers and the wire behaviour don't change.
  - Corrected across `FIRMWARE_VALIDATION.md`, `GOVEE_BLE_GATT_PROTOCOL.md`, the ksy comments,
    `OPCODE_COVERAGE.md` and the catalogue docstrings:
    - H6047 is a BlueX/RivieraWaves ARM Thumb-2 SoC, not Telink.
    - Every Realtek image drops a bad-BCC frame in its shared write layer. The OTA heads `B0`–`B5`/`BF` skip
      that check on H60A1/H60A6 and are dropped on H6008.
    - A shared info layer answers `AA 06/07/14/20/21` (`EF` too, except on H6008) before the per-SKU
      dispatcher.
    - `AA 07` answers `02/03/06` only. H6008 and H60A1/H60A6 also take `0A/0B` with no data. `10/11` have no
      handler and get no reply, or an echo on H60A1/H60A6.
    - Byte 2 of an `AA 01/04/05` read is never read by the firmware. The reply bytes after the data are the
      request's bytes.
    - The bulbs accept `A1` only as `A1 11` (Wi-Fi).
    - `0x17` is a `33 FF` sub-type, not an opcode.
    - Head `0x66` is a test stub on the Realtek images.
    - The per-image opcode lists are corrected.
  - Cautions, marked firmware-sourced: `33 46` factory-resets and reboots H60A1/H60A6 whatever the payload
    (on the ksy `check_ic` enum and `build_check_ic` / `build_check_ic_step`). `33 FF`, `66 01/02` and
    `AA FF 08` are test-only.
  - `FIRMWARE_VALIDATION.md` §7 lists the firmware-sourced reads the app never sends, such as `AA 07 06`
    (BLE MAC), H60A6 `AA 30` / `AA 41` and `AA EF`, with the images and handler addresses for each.
  - `devices.yaml` h5080: the opcode set is labelled as the app's. The `0xA1` multi is the H5089
    night-light DIY write, not multi-outlet. A firmware note says H5083 SW1.00.30 dispatches no
    `0x16/0x1F/0xB4`.
- `spec/govee_reference.py`: `parse_frame`, `parse_status` and `parse_color_group` (and so `_selftest`) work
  with both reader builds. A read-write build (`tools/gen_kaitai.sh --read-write`) reads only on `_read()`, so
  those objects are read once; the read-only classes read on construction and are left alone.
- Standalone write-body types in `govee_ble.ksy`, so a client can serialize every library write with the Kaitai
  0.11 read-write classes (the header, zero padding and BCC stay in code). None is wired into a `switch-on`, so
  what the readers return is unchanged. Each layout and cap was traced in the 7.7.02 decompile and checked by
  an adversarial reviewer, and each round-trips the catalogue builders' frames through both the read-only and
  the read-write classes:
  - single-command bodies: `zone_power_write` (`33 30 [zone, state]`, 3 zones on H12D0),
    `light_direction_write` (`33 30 [value]`: H6038 direction, H6057 main-light switch), `gradual_write`
    (`[state]` under `33 a3` or `33 14`), `secret_key_write` (`33 b2`, 8 bytes, no selector) and
    `worry_free_at_night_write` (H6057's 7-byte `33 36` body). 0x30 and 0x36 are shared by several families,
    and `zone_power_write [1, 0]` is byte-identical to `light_direction_write [1]`, so the family decides.
  - op15: `op15_color_group_write` (`[group][RGB x 1..4]`, 1-based groups) and one `op15_color_write(variant,
    mask_width)` covering basic (variant 1), H60A1/H60A6 RGB (12), CCT (11) and colour-instruction version 5,
    with the mask width checked against each form's app cap (12 / 7 / 7 / 10 bytes: 17 minus the ext bytes
    before the mask).
  - wide masks: `op15_brightness_wide` (cap 14), `color_rgbic_0b_wide` (cap 13), and `color_0b_kelvin` (the
    fixed 7-byte colour-version-6 0x0b form, which the existing 0x0b case can't tell apart).
  - `read_request`: a writable `0xAA` request, `[command][selector]`, with every selector the app's
    controllers define. `aa 01` / `aa 04` are bare, as the app sends them.
- Per-family power and H60A6's 0x0D report, traced in the 7.7.02 decompile with an adversarial check per
  family:
  - Every curated control screen that reads the aa 01 reply decodes it `== 1` (`ControllerSwitch`). No
    curated or profiled control screen reaches the legacy `SwitchController` `!= 0`, though older detail
    screens for legacy goodsTypes (e.g. dreamcolorlightv1 `adjust/v1`-`v2`) and scene / IoT command paths do. The two rules are now
    instances: `switch_read_reply.light_is_on` (`== 1`) and `status_switch.is_on` (0xAC TLV 0x01, `!= 0`).
    `devices.yaml` `client_profile` gains `power_read` (`aa01` or `ac_status`), `power_live`
    (`heartbeat_aa01`, `heartbeat_aa36`, `push_30`) and a `by_goods_type` override map
    (`devices.schema.json`), filled for the 9 curated families. H60A4/H60A6 and H6039/H6038/H6048 send no
    aa 01 read and take connect-time power from the 0xAC status TLV 0x01. h60ax is recorded per goodsType,
    with its six untraced goodsTypes left out.
  - H60A1/H60A4/H60A6 decode the 0x0D report as `rgb_kelvin_0d` on every pact (h60a1 `Info4Detail.Q`:
    `[R,G,B,kelvin s16be]`, CCT only for a signed kelvin > 0, tint never read); new
    `mode_color_0d_typed.is_cct_positive`. 0x15 stays H60A6's main colour report on pacts (1,2)/(1,3).
- op15 segment masks in the reference and catalogue (the ksy `mask_width` param waits for a change made
  together with the library): `bleutils_x`, `make_selected_two_bytes`, `op15_mask` and `op15_mask_width`
  mirror `BleUtils.X` / `makeSelectedTwoBytes` (`ceil(n/8)` bytes above 16 segments, LSB first);
  `build_op15_color(segments=...)` writes the wide mask (the default keeps the 2-byte form); new
  `build_op15_color_bytes_h` / `build_op15_color_temp_bytes_r` mirror `Bytes.h` / `Bytes.r` (7-byte cap, as
  the app has); `build_op15_gradient_color` raises above 48 segments, where the app's `Bytes.n` throws,
  instead of silently returning the 48-segment frame; `append_bcc` raises on a payload over 19 bytes. All
  match the library's `wire.build` byte for byte over 3-56 segments. 760 builder self-checks.
- Spec-client audit fixes, each verified against the 7.7.02 decompile before applying:
  - Signed 16-bit fields: the app reads the 0x0D mode-report kelvin (kinds 0 and 2) and the `aa 40` IC
    count with `BleUtil.getSignedShort`. `govee_ble.ksy` keeps the raw `u2be` fields for existing readers
    and adds `kelvin_signed`, `report_kelvin_signed` and `ic_count_signed` instances with the app's
    reading; the `devices.schema.json` `mode_0d_kind` text no longer contradicts itself (`> 0` vs `!= 0`).
  - 0xAC status TLVs: `status_tlv.len` gets `valid: {max: 0x7f}` (the app reads it signed and fails from
    0x80), and a standalone `status_device_info` type records that the status parser decodes only selectors
    0x10 / 0x11 of a 0x07 block and ignores the rest (the 0x07 case still decodes as `device_info_read`
    for existing readers).
  - `devices.yaml` / GATT doc: BGC-info byte 0 is the record format and byte 1 the encryptVersion (the
    prose had them swapped); `GVR6184_XXXX` parses to `R6184`, not `H6184`; and the name-length gates
    (≥ 11 for GVH/GVR without `_`, ≥ 10 for the bare `GV` fallback) are documented.
  - `govee_commands.build_brightness_from_percent` computes in float32 like the app's `100.0f` (446
    off-by-one results change, none in the app's real raw ranges). Reader regenerated (additive: the three
    instances, `StatusDeviceInfo`, and the `len` check).
- `tests/test_wire_build_vs_spec.py` (new): every TX builder in `wire.build` and `transport.handshake` is
  compared byte for byte with its counterpart in the spec's app-traced catalogue (`spec/govee_commands.py`)
  or reference codec (`spec/govee_reference.py`) over seeded-random inputs: power, brightness, every colour
  scheme (the tint-pick-as-CCT path over all 178 tint colours), segments up to the 7-byte mask, zones,
  bars, every scene code, 0xA3 and 0xA4 uploads, plug and secret writes, every read request and the `e7`
  handshake. The 0x15 colour and CCT frames are bound at every segment count up to 56 to the catalogue's
  `Bytes.h` / `Bytes.r` builders (the wide mask, added to the spec in faa2cb7). Every builder now equals its
  catalogue counterpart; a frame that has to differ pending a decision goes in a ratchet that asserts the
  difference instead of being skipped (the `aa 01` / `aa 04` selector was the last; see Changed).
- The protocol constants in `wire.build` (`PRO_*`, `CMD_*`, `SUB_*`, `OP15_*`) and `transport.handshake`
  (`HANDSHAKE_OPCODE` and the e7 steps) are now taken from the generated reader's enums instead of being
  copied by hand; the names and values are unchanged (pinned by the test above). The device-info selectors
  (`INFO_*`) and power values (`POWER_*`, `RELAY_*`) stay literals: the ksy has no enum for them yet.
  `wire.build` is now the wire layer's one import of the generated reader.
- More decoders read through the generated readers instead of slicing bytes: `parse_direct_color_group` and
  the 0xAC colour groups (`color_group_read`); `parse_mode_0d` (`mode_color_0d_typed` kind 2, now the same
  decode as `parse_mode_0d_rgblight`; it no longer takes the 8-10-byte frames the transport never delivers);
  the `e7` handshake replies (`handshake`); `identify.parse_manufacturer_data` and `parse_broadcast_onoff`
  (`govee_adv` `manufacturer_data`; a company id outside 16 bits, which bleak never reports, is skipped); and
  the fallback 0xAC TLV walk (`status_tlv`, read off one shared stream). `tests/test_spec_reader_moves.py`
  pins each to the byte-level decode it replaced; offline they were identical on 1.8M advertisement, 450k
  handshake, 450k colour-group and 200k TLV-walk cases. The `wire.parse` module docstring now names the two
  replies still hand-decoded (the `aa 05 04` scene code and the `aa 41` section count) and why. No frame
  changed.
- Re-verified several spec facts against the 7.7.02 decompile (adversarially) and the account device-list,
  and corrected them: `switch_read_reply` keeps the legacy `SwitchController` `!= 0` semantics as primary and
  now notes the modern control-screen VM path (`ControllerSwitch.Companion.c` / `ShareVM.N3`) that decodes
  `== 1` (they differ only for a byte ≥ 2); `MtuConfig` is described as a per-SKU **cache of the negotiated
  ATT MTU** (default 23, usable size = MTU − 3 floored at 20), written by `saveMtuData` from `onMtuChanged`
  on a successful exchange — not an app value unrelated to negotiation. Updated `govee_ble.ksy`, the GATT doc
  and `SCENE_UPLOAD_ENCODING.md`; reader regenerated (one docstring).
- `spec/devices.yaml` goodsType annotations corrected from the account device-list: H6006 and H6008 are both
  goodsType 64 (was `H6006 = 20` / `328 = H6008`; 328 is H8015, 20 is the H6001 legacy old-bulb), and H61A8
  is goodsType 19 (was `207 = H61A8`; 207's SKU is cloud-only and is not H61A8). H6006's colour-temp write
  uses the 0x0D native path like H6008, not 0x02. goodsType 19 added to the `dreamcolorlightv1` set.
- `spec/govee_commands.py`: made ruff-clean and `mypy --strict`-clean under the repo config (style and
  typing only — annotations don't execute under `from __future__ import annotations`; the builders and the
  757 self-checks are unchanged).
- `spec/govee_commands.py` (new): the full command-builder catalogue — 463 functions, one per
  (opcode, variant) the Govee app emits across 99 opcodes, each traced to the decompiled app with a
  `file:line` citation and a byte-exact self-check. `govee_reference._selftest` runs all 757 checks.
  Companion to `govee_reference.py` (which keeps the framing primitives). Device-side cross-validated
  against six firmware images; see `docs/FIRMWARE_VALIDATION.md`.
- `spec/docs/OPCODE_COVERAGE.md` (new): opcode → builders index, with each opcode's verification tier
  (firmware-confirmed vs source-modeled), whether `govee_ble.ksy` models it, the constant-only opcodes,
  and the known gaps.
- `spec/docs/DEVICE_CATALOGUE.md` (new): the app's full goodsType universe (390 values) vs the 79 the
  spec models across 51 families, with the unmodeled lighting/plug families and the out-of-scope set,
  from a source-grounded, adversarially-verified cross-check of the app's `GoodsType`/`Pact` registry.
- `spec/docs/FIRMWARE_VALIDATION.md` (new): the device-firmware cross-validation of the frame model,
  checksum and opcode inventory (six images: H6047 Telink + five RTL8720 Realtek), with the resolved
  discrepancies (handshake is `0xE7`; `0xA5` is the app's mic framing; `0x66` is device-only).
- `govee_ble.ksy`: `command` enum `0x25 preset_color` and `0xab dynamic_api_support` (were in the MD /
  registry, missing from the enum); `pro_type 0xa5` + `mic_color_frame` (mic/music live-colour push — a
  short, additive-checksum frame, deliberately NOT a `frame` case); standalone `bar_switch_write`
  (`33 36` body `[left,right]`, not wired into `single_command` so a 0x36 frame's `params` stays raw);
  `a3_start.comm_byte` doc gains V5=10 (compose) / V6=12 (H610B, shared with V7). Reader regenerated.
- `spec/devices.yaml` / `devices.schema.json`: `mode_0d_kind` gains `rgb_kelvin_0d` (cube h6061 0x0d =
  `[R,G,B,kelvin s16be]`, no tint); the `rgb_kelvin_tint` and h6188 `mode_read_color` notes corrected to
  signed Kelvin (`getSignedShort`); the h60a6 `0xA3` END documented as empty for a short value
  (`makeSendBytesV2`). `GOVEE_BLE_GATT_PROTOCOL.md` §17.3 ABNF `com-type` gains `%x04` (V4), `%x0A` (V5).
- `govee_ble.ksy`: `soft_version_read` (`aa 06`, ASCII) and `device_info_hard_version` (`aa 07 03`,
  ASCII). Readers regenerated: these additions plus docstrings only (parsing unchanged).
- 0xAC status reply (GATT §4.5, the §14 validation table and the §17.3 ABNF; `govee_ble.ksy` `multi_ac` /
  `status_reply` docs), per `AbsController4BleSingleSendMultiBack`: bytes 2-3 of the first chunk are the
  total packet count (u16 LE) and byte 4 the last chunk's data length; the 0xFF chunk is the last data
  packet, not an empty terminator, and the app also accepts it tagged with its position (pkts - 1).
  Single-packet replies are documented, and the padding note says the shipped reassembler trims to lastLen,
  like the app.
- `spec/govee_reference.py`: `reassemble_ac` / `chunk_ac` follow the app (packet count, lastLen, a last
  packet tagged 0xFF or its position, 0xFF refused mid-burst) and reproduce both real H60A6 bursts byte for
  byte (`tests/test_spec_reference_crosscheck.py`); `chunk_ac`'s `sub` defaults to 0x00 (was 0x02), the
  H60A6 status read's `d()`. The self-test falls back to the library's generated classes (after
  `tools/gen_kaitai.sh` or `pip install -e .`), so a run from a checkout executes every parser check, and
  `--strict` fails if they are skipped.
- `govee_adv.ksy`: `parseBleBroadOnOff` reads `data[6]` (`scanRecord[i6+8]`, the first byte after
  pact_code; `BleUtil.java:749`), not `data[3]`.
- 0xA3 upload (`govee_ble.ksy` `a3_start.packet_count`, GATT §4.4, SCENE_UPLOAD_ENCODING.md G7): a value that
  fits START's 15 - len(commBytes) inline bytes gives packet count 2 and an empty 0xFF END
  (`MultipleControllerCommV1.makeSendBytesV2`); a longer value's END carries the final chunk, a full 17 bytes
  when it ends on a frame boundary. The H6006 firmware's a3 reassembler (`FUN_9b020d2c`) accepts both (static
  firmware analysis). Live-verified on an H6006 (2026-10-15, macOS), which acked a short upload (packet count
  2, empty END) and then reported the scene active.
- SCENE_UPLOAD_ENCODING.md §0: commByte is the controller comType (V1=1, V2=2, V3=7, V5=10 compose,
  V6=12 H610B), and compose is uploaded only when the decoded blob starts with 0x0a (`ScenesOp.o`).
- `mode_color_0d_typed` kind 2 (`rgb_kelvin_tint`) covers the 0x0d bulbs H6006/H6008 as well as H6188
  (bulblight `ModeParseStrategyConfig.a`, traced through `VM4Light` to `OpInfo4Detail.parseModeValidBytes`,
  which takes CCT only for a signed Kelvin > 0); kind 1 stays H6052, whose control screen reads only
  `[R,G,B]` (`H6052InfoDetail.G`). The H6006/H6008 firmware sends the tint as 0 and pre-zeroes the Kelvin
  (static firmware analysis). `mode_read`, `mode_color_0d_report` and `is_cct` docs match; `devices.yaml`
  bulblightv3 `mode_0d_kind` is `rgb_kelvin_tint` (was `cct`), and the schema and USING_THE_KSY.md follow.
- `devices.yaml` h5080 `relay_switch`: the read-back on-rule per goodsType (43 = bit 0; 50/307 = any of the
  8 bits; 90 = bits 0..2), matching the three plug profiles' `relay_on_mask` 0x01 / 0xFF / 0x07.

## [1.3.0] — 2026-10-15

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
