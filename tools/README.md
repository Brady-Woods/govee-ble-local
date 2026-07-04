# tools

Developer/analysis utilities built on the `govee_ble_local` library. Run them
from the repo (they add `../src` to `sys.path`) or after `pip install -e .`.

All Govee wire-format knowledge (both encode and decode) lives in one place:
`govee_ble_local.messages`. The tools below are the *capture-file* layer
(btsnoop → HCI → ATT) plus formatting; they call the library codec for the
actual frame decoding rather than carrying their own.

| Tool | What it does |
| --- | --- |
| `decode_btsnoop.py <log>` | Capture-file parser for `btsnoop_hci.log` (pure Python: btsnoop/HCI/L2CAP/ATT parsing, connection-handle/address/name resolution, auto-detects plaintext vs. AES/RC4-encrypted per device). Frame decoding delegates to `govee_ble_local.messages.deserialize`. Run directly for a quick single-device timeline; `extract_govee_session.py` builds on it. No Bluetooth, no tshark needed. |
| `extract_govee_session.py <log>` | Split a capture into per-device files: identifies Govee device(s) by advertised name, and for each writes a `_raw.log` (hex only) and `_annotated.log` (two aligned columns: hex \| decode - real values, padding noted by length, unknown fields called out explicitly). Multi-frame exchanges (status/metadata/scene) get an aggregate `^--` line via the shared `ChunkReassembler`. Repeating heartbeats collapse to their first occurrence by default (`--keep-heartbeat` to keep them all). `--generate-config` turns a capture into a device profile (see below). |
| `fetch_scene_catalog.py --sku H60A6` | Regenerate a device's `scenes.yaml` from Govee's public scene API, preserving hand-curated `working`/`note` flags. |
| `live_probe.py <address> <command>` | Thin `GoveeBleClient` CLI for manual control/inspection (status, brightness, rgb, color-temp, zone, segment-color, segment-brightness, scene, serial, truth-table). |
| `device_test.py [--scan\|--pick N] --mode auto\|interactive` | Real-device functional test suite, driven by the device profile. |

## Capturing and decoding a BLE session (`extract_govee_session.py`)

To get ground-truth traffic for a device (e.g. to verify or correct an
assumption about its protocol):

1. On the phone: Settings -> Developer options -> enable "Bluetooth HCI
   snoop log", then toggle Bluetooth off/on so the new capture starts clean.
2. Drive the Govee app against the real device (power, brightness, color,
   scenes, ...).
3. Pull a bugreport (`adb bugreport out.zip`) and extract
   `FS/data/misc/bluetooth/logs/btsnoop_hci.log.last` from it (this is the
   *previous* Bluetooth session - the file that was active during step 2,
   since the step-1 restart begins a new, empty `btsnoop_hci.log`).
4. `python3 tools/extract_govee_session.py btsnoop_hci.log.last --out-dir out/`

**Do not commit the raw `btsnoop_hci.log(.last)` file** - it captures every
BLE device your phone saw or connected to during that window (other
lights, headphones, etc), not just the one you're investigating. Only the
per-device files `extract_govee_session.py` writes are scoped to commit.

Findings get corroborated at three confidence levels in the annotated
output: unmarked = confirmed against this library's codec or a documented
source; `[PARTIAL]` = structure understood, some field(s) unconfirmed;
`[UNKNOWN]` = opcode/field not yet identified. To teach the tools a new
opcode, extend the codec in `src/govee_ble_local/messages.py` (the
`deserialize` dispatch and, for anything sendable, a `build_*` function) -
both the decoder tool and the library's bidirectional comms pick it up.

## Generating a device profile from a capture (`--generate-config`)

Turn a capture into a `devices/<sku>/` profile automatically:

```bash
python3 tools/extract_govee_session.py btsnoop_hci.log.last --generate-config
```

For each Govee device in the capture it derives the SKU and advertised-name
prefix from the local name, infers `capabilities` from the command types
actually observed (rgb, color-temp, zones, segments, scenes), and — **only if
`devices/<sku>/` doesn't already exist** — writes `device.yaml`, a `NOTES.md`
with the observed-opcode inventory, and the redacted capture under
`captures/`. If the profile already exists it prints a **capability diff**
(capture-implied vs declared) and writes nothing. Pass a directory argument to
target somewhere other than the packaged `devices/`. Scene catalogs are not in
the capture — run `fetch_scene_catalog.py --sku <sku>` afterwards for
`scenes.yaml`.

## Real-device test suite (`device_test.py`)

Scans for Govee devices, scrapes each advertisement (local name, RSSI,
manufacturer data), matches a profile, and lets you pick one:

```bash
python3 tools/device_test.py --scan                    # list candidates
python3 tools/device_test.py --mode auto               # sweep: best-signal device per supported model
python3 tools/device_test.py --pick 0 --mode auto      # automated, candidate 0
python3 tools/device_test.py --sku H60A6 --mode interactive   # prompts to pick
```

Selection: **interactive** mode prompts you to pick one device; **auto** mode
(non-interactive) automatically tests the strongest-signal device from *each*
supported model in range — a full sweep across every distinct model — unless
narrowed with `--pick`/`--sku`. Every check drives a real command through the
codec (`messages.build_*`) and verifies via status read-back (`parse_*`), so a
run exercises the codec end-to-end against hardware.

- **auto** — sends commands and verifies via status read-back. Identity,
  brightness, zones, scenes, and segments read back directly; RGB and color
  temperature are verified indirectly through the per-segment color data
  (`get_status(with_segments=True)` / `status.rgb_color`) — RGB by exact match,
  color temp by confirming the tint shifts bluer when cooler. Because that
  segment burst is drop-prone, RGB / color-temp / segments fall back to
  `INCONCLUSIVE` only when the chunks drop on a poll. No human needed.
- **interactive** — drives each capability and asks a human to confirm what
  they see on the physical device.

## BLE host requirements & gotchas

- **Run on a Linux/BlueZ host.** macOS aborts CLI Bluetooth access without a
  privacy entitlement, and CoreBluetooth hides device MACs.
- **One connection per device.** Govee lights accept a single BLE central at a
  time — stop other clients (a running Home Assistant, the Govee app) or the
  connect will fail with "device disappeared."
- **If connects fail after a scan finds the device**, reset the adapter:
  `bluetoothctl power off; sleep 3; bluetoothctl power on`.
