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
  up. `0x11` = ON, confirmed via live control (see below), not just the
  low-bit-convention guess this was originally flagged as.
- **The power command alone is not enough - confirmed via live testing.**
  `33 01 <val>` gets a real ACK from the device, but the relay does not
  actually flip unless it's immediately followed by a `33 B5` clock-sync
  write (see below). This was the actual root cause of "the switch
  toggles in Home Assistant but the plug doesn't respond" - not a
  Bluetooth/connectivity issue, and not (as first suspected) the ON/OFF
  value mapping. `GoveeBleClient.set_power()` now sends both writes for
  this device's `power_scheme: plug_relay`.
- **Clock-sync (`33 B5`) is required after every power command, not just
  once per connection like every other device's clock-sync (`33 09`).** A
  second, fresh capture - specifically toggling this plug via the real
  app while diagnosing the control failure above - showed every single
  power command immediately followed (~10-40ms later) by a `33 B5 <unix
  ts> 01 f9` write, with no exceptions across 11 toggles in two
  independent captures. See PROTOCOL.md §15.3.
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
