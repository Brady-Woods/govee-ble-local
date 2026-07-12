# Changelog

Notable changes to `govee-ble-local`. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project follows
[Semantic Versioning](https://semver.org/) and is versioned in `pyproject.toml`. Entries are grouped
**Added / Changed / Fixed / Spec** (Spec = the Kaitai `spec/*.ksy` + `spec/devices.yaml` protocol
model, from which the shipped readers are generated).

## [Unreleased]

_Nothing yet._

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
