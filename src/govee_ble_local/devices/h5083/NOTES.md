# H5083 — device notes

Govee's smart plug family - on/off only, no color, no brightness, no
zones/segments/scenes. The first non-light device this library supports,
and the first to need a genuinely different `power_scheme`.

## ⚠️ CONTROL DOES NOT WORK YET - handshake authentication is unimplemented

**This device cannot currently be controlled by this library.** Live
testing (2026-07-05, real H5083, strong signal) proved the device
**silently ignores every command** we send after connecting. Root cause,
confirmed by decrypted byte-level comparison against real-app captures:

- Our `messages.build_handshake(step)` returns only `[0xE7, step]`, which
  frames to an **all-zeros** payload. The real Govee app's handshake
  carries **16-17 bytes of real, per-session-random challenge material**,
  and step 2 is a *computed response* to the device's step-1 reply - a
  genuine challenge-response authentication.
- H60A6 tolerates our zeros stub because *its* handshake is device-driven
  (the device generates and returns the session key; the client's payload
  is irrelevant). H5083 requires the client to prove itself, so it replies
  to our stub - making the client *think* the handshake succeeded - but
  never authenticates the session, then drops all subsequent commands.
- Verified: our command bytes reach the device byte-for-byte identical to
  the app's (raw HCI/btmon capture); a random-nonce handshake payload does
  not help; no handshake at all does not help; and no W1->N1->W2
  relationship is derivable from the PSK + BLE captures. The
  challenge-response key/algorithm lives inside the Govee app and is not
  recoverable from BLE logs - it needs app reverse-engineering.

Everything below documents the *observed protocol* (what the app does),
which is accurate, but **none of it has been confirmed working from this
library**, precisely because the handshake gate above blocks it.

## Model-specific behavior (observed in app captures; control still blocked)

- **Handshake.** Performs a real `0xE7`/PSK/AES-RC4 challenge-response at
  every connect. Post-handshake application frames are plaintext
  (checksum-verified on the wire). Same family as H61A8/H6047/H6008 - see
  PROTOCOL.md §13.5. **Our implementation of this handshake is a stub (see
  the warning above) and does not authenticate on this device.**
- **Power encoding: `33 01 <0x10=off|0x11=on>`, not `<0x00|0x01>`.** This
  device's power opcode carries the low-bit on/off convention with a
  constant `0x1` tag in the next bit up. `0x11`=ON is the reading from the
  app capture (app toggles correlate with `0x11`/`0x10`); **not** verified
  against physical device state, since we cannot yet drive the device.
- **Clock-sync (`33 B5`) follows every power command in the app capture.**
  Unlike other devices' once-per-connection `33 09`, the app sends a
  `33 B5 <unix ts> 01 f9` write immediately (~10-40ms) after every
  `33 01` power write - observed with no exceptions across 11 toggles in
  two independent captures. `GoveeBleClient.set_power()` mirrors this for
  `power_scheme: plug_relay`. Whether it is actually *required* for the
  relay to flip is **unverified** (the earlier "confirmed live" claim was
  wrong - the device never ACKed us because the handshake never
  authenticated).
- **No status readback** (`status_scheme: none`) - the `aa`-field family
  is present but not understood for this device.
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
