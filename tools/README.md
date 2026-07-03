# tools

Developer/analysis utilities built on the `govee_ble_local` library. Run them
from the repo (they add `../src` to `sys.path`) or after `pip install -e .`.

| Tool | What it does |
| --- | --- |
| `decode_btsnoop.py <log>` | Decode a `btsnoop_hci.log` of Govee BLE traffic into a readable, decrypted timeline (reuses the library's crypto). No Bluetooth needed. |
| `fetch_scene_catalog.py --sku H60A6` | Regenerate a device's `scenes.yaml` from Govee's public scene API, preserving hand-curated `working`/`note` flags. |
| `live_probe.py <address> <command>` | Thin `GoveeBleClient` CLI for manual control/inspection (status, brightness, rgb, color-temp, zone, segment, scene, serial, truth-table). |
| `device_test.py [--scan\|--pick N] --mode auto\|interactive` | Real-device functional test suite, driven by the device profile. |

## Real-device test suite (`device_test.py`)

Scans for Govee devices, scrapes each advertisement (local name, RSSI,
manufacturer data), matches a profile, and lets you pick one:

```bash
python3 tools/device_test.py --scan                    # list candidates
python3 tools/device_test.py --pick 0 --mode auto      # automated, candidate 0
python3 tools/device_test.py --sku H60A6 --mode interactive   # prompts to pick
```

- **auto** — sends commands and verifies via status read-back where the
  protocol allows (identity, brightness, zones, scenes, segments). Capabilities
  with no read-back (RGB, color temp) are smoke-tested and reported
  `INCONCLUSIVE`. No human needed.
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
