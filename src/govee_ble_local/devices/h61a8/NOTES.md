# H61A8 — device notes

A 20-segment LED rope/strip with DIY "Finger Sketch" gradient effects. The
third device this library was made to genuinely support, and the first to
prove encryption/color-scheme/status-scheme are truly independent protocol
axes rather than one bundled "device family": this device is plaintext for
all data (like H6006) but shares H60A6's color-command byte layout exactly.

## Model-specific behavior

- **"Vestigial handshake."** Every capture shows a real, successful
  `0xE7`/PSK/AES-RC4 handshake at connect - but the resulting session key is
  never used afterward. All subsequent frames, status and control alike, are
  provably plaintext: directly XOR-checksum-verified on the raw wire bytes,
  with no decryption step needed to make them decode cleanly. `protocol:
  encryption: handshake_only` in `device.yaml` tells `GoveeBleClient` to
  perform the same handshake ritual as the real app (rather than skip it
  outright) while never actually encrypting/decrypting frames with the
  resulting key. **This is the one part of the design most worth
  re-confirming on real hardware** - the choice to replicate the handshake
  rather than skip it is evidence-backed (the app does it every time) but
  not itself independently verified against a live device by this library.
- **Shares H60A6's color-command layout exactly.** `33 05 15 01 ...` for
  RGB, segment-color, segment-brightness, and scene-activate - confirmed
  byte-for-byte: a real capture frame `33 05 15 01 00 ff 00 00 00 00 00 00
  00 20 00 00 00 00 00` decodes as `build_segment_color(segment_mask=0x2000,
  r=0, g=255, b=0)` (segment #13, green) with zero adjustment needed.
- **Global (not zone) power.** `33 01 <0|1>` - no zone opcode traffic in any
  capture.
- **Real, working per-segment status readback.** `aa a5 <page>` (1-based,
  5 pages of 4 records = 20 segments) - `get_segment_status()` pages through
  all 5 and reassembles via `protocol.parse_segment_pages`, the exact same
  `[brightness_pct, r, g, b]` record shape H60A6's `0xAC` chunks 0x05-0x08
  use. No aggregate `GoveeBleStatus` (brightness/scene-id/MACs) is
  synthesized for this device - the underlying `aa`-field family isn't
  understood well enough for that yet (as with H6006) - so `get_status()`
  raises `UnsupportedStatusQuery`; use `get_segment_status()` instead.
- **Deliberately not wired as capabilities/sendable commands** (decode-only
  for now, per PROTOCOL.md §13.6/§14): the DIY/gradient "Finger Sketch"
  effect activation opcode (`33 05 0A <2-byte value>` - real and repeatable,
  but the value's exact meaning is unconfirmed), the `33 A3 <0|1>` toggle,
  the periodic `33 09` sub-format, and several still-unconfirmed `aa`
  status fields.

## Full protocol reference

The complete reverse-engineered protocol (encryption, framing, opcodes, and
the investigation history) is documented in this library's
[`PROTOCOL.md`](https://github.com/Brady-Woods/govee-ble-local/blob/master/PROTOCOL.md)
§13-§14.
