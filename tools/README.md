# tools

Developer/analysis utilities. Run them from the repo root (most add `../src` to
`sys.path`, or `pip install -e .` first).

| Tool | What it does |
| --- | --- |
| `gen_kaitai.sh` | Compile `spec/*.ksy` → `src/govee_ble_local/_generated/*.py` (read-only readers). The generated readers are **committed**, so running the tests needs only `pip install -e .[test]`. Re-run only after editing a `.ksy`. `--check` fails if the committed readers differ from a fresh build. `--read-write` writes Kaitai 0.11 read-write (serializer) classes to `build/kaitai_rw/` (gitignored; `--out DIR` to change), for building frames from the spec. Uses the JVM `kaitai-struct-compiler` 0.11.x when it is on PATH (`brew install kaitai-struct-compiler`; `KSC=` overrides), which is required for `--read-write`; otherwise read-only builds fall back to the pure-JS compiler with a userland Node under `.toolchain/` (gitignored). Both compilers produce byte-identical readers. `VERBOSE=1` shows the compiler's style warnings. |
| `gen_docs.sh` | Generate the public API reference (`docs/api/`, gitignored) from the package docstrings with pdoc; needs `pip install '.[docs]'`. |
| `kaitai_gen.js` | Node helper invoked by `gen_kaitai.sh` (not run directly) for its pure-JS fallback: compiles `spec/*.ksy` to read-only Python readers. The npm compiler's `compile()` API has no read-write switch, so `--read-write` always uses the JVM compiler. |
| `fetch_scenes.py [SKU…]` | Fetch Govee's built-in scene library per SKU (public endpoint) into `src/govee_ble_local/scenes/<SKU>.json`. `--resolve` (with `GOVEE_EMAIL`/`GOVEE_PASSWORD`) bakes real blobs for placeholder scenes via the authenticated `effect-strs` endpoint. `--audit [SKU…]` prints upload readiness for the named SKUs (default: all scene-capable): each scene lands in exactly one of upload / activate / blocked / placeholder, plus the profile's scene versions, with no cloud call. A re-fetch keeps a blob resolved by `--resolve` only while the public library still ships its 0xFF stub, and replaces (not merges) the public upload metadata. SKUs must be 3-8 alphanumerics; any failed fetch or write (or a corrupt existing catalog) is reported per SKU and exits non-zero; `--app-version` / `$GOVEE_APP_VERSION` overrides the AppVersion header. |
| `decrypt_btsnoop.py <log>` | Decrypt a Govee AES-RC4-PSK btsnoop capture: derives the per-connection session key from the `e7` handshake (a device `e7 01` reply counts only after a host `e7 01` request on that connection), decrypts every ATT write/notify, BCC-validates (20-byte frames, and exact-length MTU-sized / short `0xA4` and `0xA6` multi-packet frames, plus `0xA3` as an allowance; a mis-keyed exact-length frame slips through ~3/256 × 1/256), resolves device MAC per ACL handle and keeps session keys per connection. `--starts` dumps multi-packet START frames; `--dump a1,a3,a4,a6` (the default); `--all`; `--limit N`. Reads H4 captures only (btsnoop datalink 1002, e.g. Android's HCI snoop log; other datalinks such as btmon's 2001 are refused). Records with a corrupt length (>1 MiB or past end of file) abort with an error. |
| `btsnoop_mtu_scan.py <log>` | Decryption-free scan: ATT Exchange-MTU values + write-length histogram (`--protype` adds a first-byte histogram of 20-byte writes). Works on encrypted links (ATT headers are plaintext). Also holds the btsnoop record reader `decrypt_btsnoop.py` reuses (H4 / datalink 1002 only, same corrupt-record checks). |
| `analyze_frame_log.py <frames.jsonl>` | Parse a captured plaintext frame log (from `GOVEE_FRAME_LOG` / `--capture`) against the generated Kaitai reader; reports a coverage histogram and flags frames the `.ksy` doesn't model (non-zero exit on a hard issue). A missing file exits 2; malformed lines are skipped, reported as `path:lineno: reason` on stderr, and counted in the summary. |
| `h60a6_live_check.py` | Comprehensive live H60A6 command check over ONE persistent connection: power / brightness / RGB / kelvin / zones / all segments / scene dialects, each with ACK + status read-back. `--capture PATH` logs all wire frames. `GOVEE_H60A6_ADDRESS=…` or `--address`. Its `find_device` (address, else best-RSSI H60A6 scan) is shared by the two probes below. |
| `field_test/` | Guided hardware test for a tester who has the device (runs against an installed wheel, or from a checkout): `python tools/field_test`. Scans (records every Govee name heard), detects the SKU and runs that SKU's plan over one connection: a read-back and a y/n/skip question per step, restore of the original state, and a JSON report + frame capture to send back. Snapshots always redact the serial number, device id, Wi-Fi MAC and BLE MAC; the report masks Bluetooth addresses, Wi-Fi MACs and the MAC digits of advertised names (`ihoment_H6188_xxxx`) in the device and advertisement fields and in error text; the frame capture drops every `wire` field and zeroes the `aa 07` and `ac` reply payloads (a status burst's first chunk keeps its 7-byte header, so the analyzer still checks its packet count). `--sku` picks a plan, `--address`, `--no-prompt`, `--out DIR`, `--settle S`. Exit 2 = no device/plan found. Plans: H6047, H6004, H6062, H6061, H6188. See [Adding a field-test plan](#adding-a-field-test-plan). |
| `power_semantics_probe.py <ADDRESS> <SKU>` | Hardware experiment: how the global power switch (`33 01`) interacts with zone power (`33 30`/`33 36`) and whether colour/brightness wake the device. Always restores the starting power/zone state, even on failure, with the same per-profile zone frames as the experiment (`33 36` for the bar light, `33 30` per zone otherwise); prints `RESTORE FAILED` if any restore frame isn't acked. |
| `h60a6_cct_choreography.py` | Live H60A6 colour-temperature choreography over one persistent connection; prints an OBSERVE line per step. `--address`/`GOVEE_H60A6_ADDRESS`, `--capture PATH`. |
| `h60a6_segment_map_probe.py` | Live H60A6 probe that maps segment indices to physical ring/top-panel positions. `--address`/`GOVEE_H60A6_ADDRESS`, `--capture PATH`. |

## Adding a field-test plan

The runner (`field_test/runner.py`) knows nothing about any SKU. A device's plan is one module,
`field_test/plans/<sku>.py`, which is imported automatically (`plans/__init__.py` loads every module
in the directory), so a new device never edits the runner or another plan:

```python
from ..plan import PlanContext, SkuPlan, Step, register_plan

def build_steps(ctx: PlanContext) -> list[Step]:      # ctx.dev, ctx.sku, ctx.scene_codes
    dev = ctx.dev
    return [Step("power_off", lambda: dev.set_power(False), lambda st: st["is_on"] is False,
                 "Is the light OFF?")]

register_plan(SkuPlan(sku="H6xxx", title="H6xxx whatever", build_steps=build_steps,
                      intro="shown to the tester first"))
```

`Step(name, action, expect, question, free_text=False, note="")`: `action` is a zero-argument
coroutine function, `expect` checks the read-back snapshot dict (or is `None`), `question` is asked
after the step. `field_test/plan.py` has step factories for the common sequences: `power_steps`,
`brightness_steps`, `rgb_steps`, `tint_pick_step`, `scene_steps`, and `state_is(key, value)` for an
`expect`. A step built in a loop binds its loop values with `functools.partial` (or `state_is`), not
`lambda x=x:`. Optional `SkuPlan` fields: `snapshot_keys` (state attributes to record; the serial
number, device id, Wi-Fi MAC and BLE MAC are always redacted), `restore(dev, initial_snapshot)`
(default: brightness, then scene / colour temperature / RGB, then power), `extra_name_hints`
(advertised-name substrings that select the plan; the SKU is already matched from the name and the
SKU's own digits). Add a test next to `tests/test_field_test.py`; run it offline with a fake device.

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
the committed readers under `src/govee_ble_local/_generated/`; the offline suite (`pytest`) round-trips the library's frames
through those readers and validates `devices.yaml` against its schema.
