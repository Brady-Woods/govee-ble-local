# H60A6 — device notes

Govee Ceiling Light Pro. Two physical zones (upper ring, lower panel) and 12
individually-addressable segments. This is the reference device the library
protocol was reverse-engineered against.

## Model-specific behavior

- **Zone on/off** is read from the status terminator chunk: byte 14 = lower
  zone, byte 15 = upper zone (byte 13 is a static `0x02` and does **not**
  reflect power). Confirmed via a full 4-state truth table.
- **Per-segment status** requires the longer status query, whose 10-notification
  response burst drops its tail under BLE adapter contention. As a result the
  per-segment *read-back* is unreliable in practice; the Home Assistant
  integration exposes segment *commands* but not always live segment state.
- **Broken scenes** (`working: false` in `scenes.yaml`): two failure classes —
  a `0xFF`-placeholder header (Aurora, Dandelion, Desert, Fall, Green Wheat
  Field, Volcano) and oversized payloads (Ocean — causes a BLE disconnect;
  Winter). These are genuine, officially-supported scenes that render fine via
  Govee's own cloud path but fail over BLE; see the full protocol reference.

## Full protocol reference

The complete reverse-engineered protocol (encryption, framing, opcodes, status
chunk layout, and the investigation history) is documented in this library's
[`PROTOCOL.md`](https://github.com/Brady-Woods/govee-ble-local/blob/master/PROTOCOL.md).
