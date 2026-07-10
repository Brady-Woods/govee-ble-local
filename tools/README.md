# tools

Developer/analysis utilities. Run them from the repo root (most add `../src` to
`sys.path`, or `pip install -e .` first).

| Tool | What it does |
| --- | --- |
| `gen_kaitai.sh` | Compile `spec/*.ksy` → `tests/spec_gen/*.py` with the pure-JS Kaitai compiler (no JVM). The generated readers are **committed**, so running the tests needs only `pip install -e .[test]`. Re-run only after editing a `.ksy`. First run downloads a userland Node into `.toolchain/` (gitignored). |
| `fetch_scenes.py [SKU…]` | Fetch Govee's built-in scene library per SKU (public endpoint) into `src/govee_ble_local/scenes/<SKU>.json`. `--resolve` (with `GOVEE_EMAIL`/`GOVEE_PASSWORD`) bakes real blobs for placeholder scenes via the authenticated `effect-strs` endpoint. `--audit` prints per-SKU upload readiness (upload / activate / blocked / placeholder) with no cloud call. |
| `decrypt_btsnoop.py <log>` | Decrypt a Govee AES-RC4-PSK btsnoop capture: derives the per-connection session key from the `e7` handshake, decrypts every ATT write/notify, BCC-validates, resolves device MAC per ACL handle. `--starts` dumps multi-packet START frames; `--dump a1,a3,a4`; `--all`. |
| `btsnoop_mtu_scan.py <log>` | Decryption-free scan: ATT Exchange-MTU values + write-length histogram (`--protype` adds a first-byte histogram of 20-byte writes). Works on encrypted links (ATT headers are plaintext). |
| `analyze_frame_log.py <frames.jsonl>` | Parse a captured plaintext frame log (from `GOVEE_FRAME_LOG` / `--capture`) against the generated Kaitai reader; reports a coverage histogram and flags frames the `.ksy` doesn't model (non-zero exit on a hard issue). |
| `h60a6_live_check.py` | Comprehensive live H60A6 command check over ONE persistent connection: power / brightness / RGB / kelvin / zones / all segments / scene dialects, each with ACK + status read-back. `--capture PATH` logs all wire frames. `GOVEE_H60A6_ADDRESS=…` or `--address`. |

## Capturing a BLE session (for ground-truth traffic)

1. Phone: Settings → Developer options → enable "Bluetooth HCI snoop log", then toggle Bluetooth off/on.
2. Drive the Govee app against the real device (power, brightness, colour, scenes…).
3. Pull `adb bugreport out.zip` and extract `FS/data/misc/bluetooth/logs/btsnoop_hci.log.last`.
4. `python3 tools/decrypt_btsnoop.py btsnoop_hci.log.last` (add `--starts` for scene/DIY uploads).

**Do not commit raw `btsnoop_hci.log(.last)` files** — they capture every BLE device the phone saw.

## BLE host requirements & gotchas

- **Run on a Linux/BlueZ host.** macOS aborts CLI Bluetooth without a privacy entitlement and hides MACs.
- **One connection per device.** Govee lights accept a single BLE central — stop Home Assistant / the Govee
  app or the connect fails with "device disappeared."
- **If connects fail after a scan finds the device**, reset the adapter: `bluetoothctl power off; sleep 3;
  bluetoothctl power on`.

## Spec (`spec/`) ↔ library

`spec/` is the machine-readable protocol reference: `govee_ble.ksy` / `govee_adv.ksy` (Kaitai frame + advert),
`devices.yaml` (+ `devices.schema.json`, the device/capability table). `gen_kaitai.sh` compiles the `.ksy` to
the committed readers under `tests/spec_gen/`; the offline suite (`pytest`) round-trips the library's frames
through those readers and validates `devices.yaml` against its schema.
