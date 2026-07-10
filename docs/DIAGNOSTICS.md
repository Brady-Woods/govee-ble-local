# Diagnostics & session capture

The library logs through the standard `logging` module under the `govee_ble_local`
namespace, so a consumer (e.g. a Home Assistant integration) can turn it up to understand
*why* a device isn't responding, and can capture a full protocol session for offline
analysis — including over a Home Assistant **Bluetooth proxy**, which reaches devices a
workstation can't.

> ⚠️ **No redaction.** INFO/DEBUG and the frame tier log the account-lock **secret**
> (`aa b1` / `33 b2`), the device **serial**, and the **Wi-Fi / BLE MAC** verbatim. Fine for
> your own logs; a log you paste into a public bug report **will contain those**. Scrub before
> sharing.

## Log levels

| Level | What you get |
|-------|--------------|
| **ERROR** | Control genuinely failed and you must act: the device **rejected** a write (non-zero result), no ACK after all retries, handshake failed, or an unsupported (AES-GCM V2) device. |
| **WARNING** | Handled but unexpected — the "why is state stale / what's this device doing" signal: an RX frame the spec can't model (unknown proType/command/sub-type), a malformed status burst, or a status read that came back with no usable data. |
| **INFO** | Lifecycle milestones, low volume, safe to leave on: connected, session ready (+ encryption mode), first successful poll (one-line state summary), disconnect. |
| **DEBUG** | Per-operation protocol flow: each command sent (decoded, e.g. `TX write/mode/color_rgbic_15`), each ACK result, each read-back with the resulting state, reconnect/idle events, and `soft` coverage gaps (known opcode, unmodelled payload). |
| **`govee_ble_local.frames`** (its own logger, at DEBUG) | The full-session firehose: **every** TX/RX frame — direction, decoded label, plaintext hex, on-air (wire) hex, encryption mode. This is the capture surface. Off unless you enable it explicitly; guarded so it costs nothing when off. |

## Enabling in Home Assistant

```yaml
# configuration.yaml
logger:
  logs:
    govee_ble_local: debug            # protocol-flow trace ("why did control fail?")
    govee_ble_local.frames: debug     # + the full per-frame session capture
```

Reproduce the issue, then grab the log (Settings → System → Logs, or Download Diagnostics).
Leave `govee_ble_local.frames` off for day-to-day debugging — it's verbose.

## Capturing a full session for analysis / new-device work

Two capture surfaces feed the same analyzer:

1. **Frame-tier logger** (no filesystem — best for HA / a proxy). Enable
   `govee_ble_local.frames: debug`, reproduce, save the log lines, then:

   ```bash
   govee-ble-analyze --from-frames-log session.log -o session.jsonl
   ```

   This converts the logger output to the JSONL fixture format **and** prints the coverage
   report. Keep `session.jsonl` as a unit-test fixture or a starting point for a new device.

2. **JSONL frame log** (a file — best for local dev / CI). Pass a path when constructing the
   device (`create_device(..., frame_log="/path/session.jsonl")`) or set
   `GOVEE_FRAME_LOG=/path/session.jsonl` in the environment, then:

   ```bash
   govee-ble-analyze session.jsonl
   ```

Both hooks sit above the BLE client, so a session captured **over a Bluetooth proxy** is
identical to a local one.

## Reading the report

`govee-ble-analyze` prints:

- a **coverage** histogram (direction + decoded label per frame kind),
- the reassembled **0xAC status TLV** inventory,
- **coverage gaps** (`soft`: known opcode, payload not modelled) and **malformed bursts**
  (dropped/duplicated frames — a transport artifact, not a spec gap), and
- **ISSUES** — valid frames the spec does *not* represent (unknown opcodes, device
  rejections, unknown status TLV types). A non-empty ISSUES list is the actionable output for
  extending the protocol model; the command exits non-zero when it's non-empty.
