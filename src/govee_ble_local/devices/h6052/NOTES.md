# H6052 — device notes

A plain RGBWW bulb — no zones, no segments, no encryption. Initially
mischaracterized as sharing H60A6's segment-command layout (see below);
corrected after a clean, bug-free capture.

## Model-specific behavior

- **No handshake, no encryption.** A complete real capture shows zero
  `0xE7` frames - `GoveeBleClient` skips the handshake entirely.
- **H6006's color/color-temp byte layout** (`33 05 0D`), not H60A6's
  (`33 05 15`). An earlier analysis pass had grouped this SKU with
  H6047/H6641/H61A8 as sharing H60A6's `33 05 15` layout - that
  conclusion was traced to a real device-grouping bug in the extraction
  tooling (BLE connection handles being reused across unrelated
  connections; fixed - see PROTOCOL.md §14.2) that had merged a different
  device's traffic into this SKU's bucket in that earlier, buggy
  extraction. A clean re-capture (both btsnoop generations fed through
  correctly) shows no `33 05 15`, no segment-mask commands, and no `aa a5`
  per-segment status traffic at all for this device - it's a plain bulb,
  not a segmented strip.
- **Unusually wide color-temp range.** 2000K-9000K exercised live (vs. the
  2700-6500K range every other tested device uses) - both endpoints and
  several points in between confirmed with real ACKed commands.
- **Global (not zone) power** (`33 01 <0|1>`), standard brightness
  (`33 04 <pct>`), full scene upload confirmed (chunked `0xA3` burst).
- **No working status readback.** The `aa <field_id>` family exists
  (heartbeat) but isn't understood well enough to synthesize a
  `GoveeBleStatus` - same limitation as H6006/H6008.

## Full protocol reference

The complete reverse-engineered protocol is documented in this library's
[`PROTOCOL.md`](https://github.com/Brady-Woods/govee-ble-local/blob/master/PROTOCOL.md)
§15.2 (and the correction note in §14.2/§13 referencing the earlier
mischaracterization).
