# H5083 — device notes

Govee's smart plug family - on/off only, no color, no brightness, no
zones/segments/scenes. The first non-light device this library supports,
and the first to need a genuinely different `power_scheme`.

## Model-specific behavior

- **"Vestigial handshake."** Performs the real `0xE7`/PSK/AES-RC4 exchange
  at every connect, but the resulting session key is never used - every
  subsequent frame is provably plaintext (checksum-verified directly on the
  wire bytes). Same pattern as H61A8/H6047/H6008 - see PROTOCOL.md §13.5.
- **Different power encoding: `33 01 <0x10=off|0x11=on>`, not
  `<0x00|0x01>`.** Every light/strip device documented so far uses the
  simple binary encoding; this device's power opcode carries the same
  low-bit on/off convention but with a constant `0x1` tag in the next bit
  up. Confirmed via a real, repeated manual toggle test (`0x10, 0x11, 0x10,
  0x11, ...`, always ACKed). **Which literal value is actually ON vs. OFF
  was not independently verified against physical device state** - a plug
  has no other observable state to cross-check against (no rendered
  color/brightness). `0x11` (bit set) = ON follows the low-bit convention
  used everywhere else in this protocol and is what this library encodes,
  but treat it as the working hypothesis, not a confirmed fact, until
  checked against a physical plug. See PROTOCOL.md §15.3.
- **No working status readback** (`status_scheme: none`) - the `aa`-field
  family is present but not understood for this device either.
  `tools/device_test.py`'s new `power` check therefore sends real OFF/ON
  commands and gets real ACKs, but can only report `INCONCLUSIVE` in
  `--mode auto` (no way to read the resulting state back) - use
  `--mode interactive` to visually confirm the plug's actual state.
- **Two opcodes seen only on this device, not yet decoded**: a one-shot
  `33 B2` command sent immediately after the handshake (real ACK, purpose
  unknown - possibly a session/registration step), and a paired `aa B0`
  query (two fixed sub-queries every poll cycle, response always equals
  the query - no observed state change in this capture). Also a metadata
  *write* (`ab 02 ...`, distinct from the normal `ab 01 <field_id>`
  *query*) whose response was never captured. None of these are wired
  into the codec - see PROTOCOL.md §15.3 for the raw bytes.

## Full protocol reference

The complete reverse-engineered protocol is documented in this library's
[`PROTOCOL.md`](https://github.com/Brady-Woods/govee-ble-local/blob/master/PROTOCOL.md)
§15.3.
