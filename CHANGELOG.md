# Changelog

Notable changes to `govee-ble-local`. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project follows
[Semantic Versioning](https://semver.org/) and is versioned in `pyproject.toml`. Entries are grouped
**Added / Changed / Fixed / Spec** (Spec = the Kaitai `spec/*.ksy` + `spec/devices.yaml` protocol
model, from which the shipped readers are generated).

## [Unreleased]

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
