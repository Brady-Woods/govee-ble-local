# Diagnostics & session capture

The library logs through the standard `logging` module under the `govee_ble_local`
namespace, so a consumer (e.g. a Home Assistant integration) can turn it up to understand
*why* a device isn't responding, and can capture a full protocol session for offline
analysis — including over a Home Assistant **Bluetooth proxy**, which reaches devices a
workstation can't.

> ⚠️ **Only the secret is redacted.** The account-lock **secret** (`33 b2` write / `aa b1` read
> reply) is masked in the frame tier and in the JSONL capture. Everything else is logged verbatim:
> every line carries the device's **BLE MAC**, and the frame tier and captures include the
> device-info replies (**serial / device id**, **Wi-Fi MAC**) and the handshake frames (the
> session key is derivable from those with the library's public PSK). Fine for your own logs;
> scrub a log before you paste it into a public bug report.

## Log levels

The library follows Home Assistant's guidance for libraries: a failure is **raised** as a typed
exception and the caller (the integration) decides whether and how to log it. So nothing is
logged at ERROR; the failure detail is at DEBUG, next to the exception. A consumer that keeps an
always-on WARNING capture of this logger (as the Home Assistant integration does for its
diagnostics) sees a device rejection there only if it logs the `GoveeBleCommandRejected` (and the
`GoveeBleAuthError` of a rejected secret key, the most common one on the plug family) itself, to a
logger its capture also covers: for Home Assistant, the integration's own package logger, which is
not a child of `govee_ble_local`.

| Level | What you get |
|-------|--------------|
| **ERROR** | Nothing. A failure is raised instead: the device **rejected** a write (non-zero result) → `GoveeBleCommandRejected` (a rejected secret-key check → `GoveeBleAuthError`); no ACK after all retries, the operation deadline, a connect that timed out, or a BLE I/O call that timed out → `GoveeBleTimeout`; the e7 handshake failed → `GoveeBleHandshakeTimeout` (no reply in time) / `GoveeBleHandshakeConnectionError` (the link dropped, or an e7 write failed on it) — also a `GoveeBleTimeout` / `GoveeBleConnectionError`; the link failed → `GoveeBleConnectionError`; an unsupported (AES-GCM V2) device → `GoveeBleNotSupported`. All subclass `GoveeBleError`. |
| **WARNING** | Handled but unexpected — the "why is state stale / what's this device doing" signal: an RX frame with an unknown proType (the first one of each proType for this device, until the integration reloads; repeats go to DEBUG), a status read that came back with no usable data even after its retry, a relay switched but its sync-time follow-up failed, a state callback that raised, frame-capture problems (capture directory missing or not writable; an existing capture file that is group- or world-accessible, warned once per file; a write to the capture file failed; the capture queue overflowed), and (cloud extra) a malformed device secret in the account's device list, which is ignored. |
| **INFO** | One line per device: its first successful poll that read state back, with a one-line state summary. |
| **DEBUG** | Per-operation protocol flow: each command sent (decoded, e.g. `TX write/mode/color_rgbic_15`), a missing ACK and the reconnect-and-retry it triggers, a rejected write (with its status byte), session attempts, "session ready" (+ encryption mode), link drops (`disconnected`), dropped junk frames (bad checksum, late handshake retransmits), each poll's resulting state, and status bursts that were truncated or did not parse. |
| **`govee_ble_local.frames`** (its own logger, at DEBUG) | The full-session firehose: **every** TX/RX frame — direction, decoded label, plaintext hex, on-air (wire) hex, encryption mode. This is the capture surface. Off unless you enable it explicitly; guarded so it costs nothing when off. |

`soft` coverage gaps (a known opcode whose payload isn't modelled) are not logged at runtime;
`govee-ble-analyze` reports them from a capture (see below).

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
