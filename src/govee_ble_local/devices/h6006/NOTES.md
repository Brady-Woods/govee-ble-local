# H6006 — device notes

A legacy plaintext-generation bulb — no zones, no segments, no encryption.
The second device this library was made to genuinely support (not just
decode), proving the protocol differences declared in `device.yaml` (see
`protocol:` section, `messages.Protocol`) actually drive real behavior.

## Model-specific behavior

- **No handshake, no encryption.** Every capture shows zero `0xE7` frames -
  `GoveeBleClient` skips the handshake entirely for `encryption: none` and
  sends/receives plain (checksummed, unencrypted) frames from the first
  write onward.
- **Different color/color-temp byte layout.** `33 05 0D <r> <g> <b>` for RGB,
  and `33 05 0D <tint r,g,b> <kelvin hi,lo> <tint r,g,b>` for color
  temperature - both structurally confirmed byte-exact against real capture
  data (see `captures/2026-07-03_manual-test_annotated.log`). The tint bytes
  themselves come from this project's own `kelvin_to_rgb` approximation
  (shared with H60A6's color scheme) rather than a lookup table matching the
  real app's tint exactly at every Kelvin value - a pre-existing, accepted
  gap (PROTOCOL.md §4.1), not something specific to this device.
- **Global (not zone) power.** `33 01 <0|1>` - no zone opcode traffic in any
  capture.
- **No working status readback.** The `aa <field_id>` family exists (heartbeat,
  version strings, MAC) but isn't understood well enough to synthesize a
  `GoveeBleStatus` - `get_status()`/`get_segment_status()` both raise
  `UnsupportedStatusQuery` immediately rather than attempting a query that
  can only time out. Verification for this device relies on ACK receipt and
  `device_test.py --mode interactive` (a human watching the bulb), not
  `--mode auto`'s read-back checks.
- **Scene activation** (`33 05 04 <id>`) confirmed identical to H60A6/
  upstream. Full scene upload (`0xA3` chunked burst) shares H60A6's exact
  code path and *should* work by construction, but wasn't itself captured
  live on this specific SKU - only bare activation is directly confirmed.

## Full protocol reference

The complete reverse-engineered protocol (encryption, framing, opcodes, and
the investigation history) is documented in this library's
[`PROTOCOL.md`](https://github.com/Brady-Woods/govee-ble-local/blob/master/PROTOCOL.md)
§12.
