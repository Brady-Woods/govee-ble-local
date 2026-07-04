# H6008 — device notes

A plain RGBWW bulb, and the first confirmed instance of a fourth distinct
protocol combination: a real handshake paired with H6006's legacy color
layout (every other "vestigial handshake" device documented so far -
H61A8, H6047 - shares H60A6's layout instead).

## Model-specific behavior

- **"Vestigial handshake."** Performs the real `0xE7`/PSK/AES-RC4 exchange
  at every connect, but the resulting session key is never used - every
  subsequent frame, status and control alike, is provably plaintext
  (checksum-verified directly on the wire bytes). See PROTOCOL.md §13.5
  for the pattern generally, and §15.1 for this device specifically
  confirming it pairs with H6006's color scheme, not H60A6's - the first
  time that particular combination has been observed.
- **H6006's color/color-temp byte layout** (`33 05 0D`), not H60A6's.
  Confirmed byte-exact live: solid RGB and color-temp exercised across
  2700K, 3000K, 3100K, 3600K, 6000K, 6500K, all real ACKed commands.
- **Global (not zone) power** (`33 01 <0|1>`), standard brightness
  (`33 04 <pct>`), and a full real scene upload-then-activate cycle
  (chunked `0xA3` burst + `33 05 04 <code>`) - multiple different scenes
  activated live.
- **No working status readback.** The `aa <field_id>` family exists
  (heartbeat, and several still-unconfirmed fields also seen on other
  devices) but isn't understood well enough to synthesize a
  `GoveeBleStatus` - same limitation as H6006. `get_status()` raises
  `UnsupportedStatusQuery`; verification relies on ACK receipt and
  `device_test.py --mode interactive`.

## Full protocol reference

The complete reverse-engineered protocol is documented in this library's
[`PROTOCOL.md`](https://github.com/Brady-Woods/govee-ble-local/blob/master/PROTOCOL.md)
§15.1.
