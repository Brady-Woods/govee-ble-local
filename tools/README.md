# tools

Developer/analysis utilities. Run them from the repo root. From a checkout, run `bash tools/gen_kaitai.sh`
(or `pip install -e .`) first: most tools put `../src` first on `sys.path`, and its `_generated/` classes are
generated, not committed. Re-run it after a checkout or pull that changes `spec/*.ksy`.

| Tool | What it does |
| --- | --- |
| `gen_kaitai.sh` | Compile `spec/*.ksy` → `src/govee_ble_local/_generated/*.py`: Kaitai 0.11 **read-write** classes, which the library uses both to parse frames (explicit `_read()`) and to build every frame it sends (`_check()` + `_write()`). The generated classes are a **build product** (gitignored, never committed): the in-tree build backend (`_build_backend/`) runs this script before every sdist, wheel and editable build, so `pip install -e .[test]` generates them; the sdist and wheel ship them. Run it after editing a `.ksy`, or after a checkout or pull that changes one (`pytest` stops and says so when the classes are older than the spec); `scripts/check.sh`, and so every CI and release job, also runs it first. `--out DIR` writes elsewhere. The readwrite build clears `_generated/` first, so it holds exactly the compiler's output plus `__init__.py` and the device table (`gen_devices.py`, run with `$PYTHON`, which needs PyYAML). Needs the JVM `kaitai-struct-compiler` 0.11.x on PATH (`brew install kaitai-struct-compiler`, or `bash scripts/install_ksc.sh` on Debian/Ubuntu; `KSC=` overrides). `--read-only --out DIR` writes the older auto-reading readers to a scratch directory, and only that mode can fall back to the pure-JS compiler (userland Node under `.toolchain/`, gitignored). `VERBOSE=1` shows the compiler's style warnings. |
| `gen_devices.py` | Write `src/govee_ble_local/_generated/devices.py`, the supported-device table, from `spec/devices.yaml`: the mode_color_parse table and the families with a supported `client_profile` row, projected through an allow-list checked against `devices.schema.json` and resolved with the library's own `devices/_resolve.py` before it is written, so a missing fact fails the build, and the SKUs of the scene store (`store.json`'s `skus`), the only ones a profile offers scenes to. A build product like the Kaitai classes (`gen_kaitai.sh` runs it); `--out DIR` writes elsewhere. Needs PyYAML. |
| `gen_docs.sh` | Generate the public API reference (`docs/api/`, gitignored) from the package docstrings with pdoc; needs `pip install '.[docs]'`. |
| `kaitai_gen.js` | Node helper invoked by `gen_kaitai.sh` (not run directly) for its pure-JS fallback: compiles `spec/*.ksy` to read-only Python readers for `--read-only` builds. The npm compiler's `compile()` API has no read-write switch, so the library's read-write classes always use the JVM compiler. |
| `fetch_scenes.py [SKU…]` | Fetch Govee's built-in scene library per SKU (public endpoint) into `src/govee_ble_local/scenes/store.json`, each scene stored once: its `pool` lists every bundled scene once, sorted by content (its name, then its JSON with sorted keys), and `skus` maps each SKU to the increasing indexes of its scenes. The app's scene list filter runs at the scene set in force, so the tool runs it at each of the SKU's scene sets: a SKU's catalog is each scene's pick at the union of its sets, and `by_set` maps the SKU and each set where a result differs (its versions sorted and comma-joined, `""` for the empty set) to those scenes: `null` where the app lists no such scene there (or plays an effect there the library can't store: version rules, no code, a sceneType-5 upload, a placeholder, an entry the shared check refuses, such as a code past 0xFFFF; printed per set, never a failed fetch, though a value that is not an int, 1e999 among them, fails the fetch, as below), else the pool index of the entry it plays there. Naming ignores the per-set results (a change to a scene's other effects never renames it; alike twins, one sceneName and one entry at the union, take the order of their whole lightEffect content, so a reordered response never swaps which plays which effect), each scene whose result at a set is not the one the store held is printed by name, per set (hidden there, plays an effect there, changes from one effect to another, listed there again), the tombstone keeps a name's entry at the union only, and a scene only an empty set lists is left out and printed. A kept scene the fetch no longer lists keeps its per-set results at the SKU's current sets only (one at a set it no longer has is dropped, printed), and at a set added since, it is hidden where its entry's op type is outside the set. Each write lays the whole store out again from every SKU's catalog, so the same catalogs give the same bytes and a scene no SKU holds leaves the pool, in one atomic replace. A SKU the library has no scenes for is not stored, and one whose every stored scene is removed (below) leaves the store. For a `legacy_scene` SKU, the sceneType-5 scenes the app uploads and the library does not (a param that leads 0x50, the new-DIY path; any, where the scene versions hold a sceneType-5 branch of `parseSceneV1`, 7 to 13) are left out and listed. Each scene takes the values the app reads for the SKU: a `specialEffect` entry whose `supportSku` holds it overrides the base code, type, param, param id, cmdVersion and big-effect flag (`CategoryV1.LightEffect`), and a scene whose lightEffect the firmware's version rules pick is left out and named on stdout. A value of a lightEffect or a `specialEffect` entry that it reads as an int (`sceneCode`, `scenceParamId`, `sceneType`, `cmdVersion`, `bigEffectStr`) and that is not an int or null, or is outside a Java int's range, and a `supportSku` that is not a list of strings, fail the SKU's fetch, as the app's Gson fails the whole response (a scene type `1.9` or `"1"` read as one the SKU refuses retired the stored name, and every name of a SKU whose every scene type was so). An int in range is data: a scene type with no op type (-1: sceneType 9, say) is one a SKU's op set refuses for good, so its stored name is removed and retired. Characters the app's Base64 decoder skips (anything outside the alphabet and `=`) are dropped from a blob, as the app does. `--resolve` (with `GOVEE_EMAIL`/`GOVEE_PASSWORD`) bakes real blobs for placeholder scenes via the authenticated `effect-strs` endpoint. `--audit [SKU…]` prints upload readiness for the named SKUs (default: every SKU whose admitted row offers scenes, catalog or not; a profile offers scenes only to a SKU with a catalog): one row per scene set (the device's before the device-info read, then each smaller one), each scene of the catalog offered at that set, the effects played there included, planned at that set, landing in exactly one of upload / activate / blocked / placeholder, plus the set's versions, with no cloud call. A placeholder is the app's own signal, a big-effect scene (`bigEffectStr`) with an empty param. A re-fetch keeps a blob resolved by `--resolve` only while the public library still ships it as a placeholder of the same param id (the scene that keeps its stored name, through a renumber or a category rename too, else one of the same code and category, else the resolved entries of that param id where they hold one blob, one scene under more names; a bare entry of it has no blob to keep, so the scene stays a placeholder, as a new name does where the sceneType-5 cut would drop that blob), and takes the fresh code, category and upload metadata (replaced, not merged). A stored name is a Home Assistant effect name, so a re-fetch never points one at another scene, and a name the store gave up never comes back on another; every change to a stored name is printed. Each listed scene keeps the name of the stored scene of its sceneName that holds its code and category (ties by the whole entry, a resolved one's as its placeholder, then by blob and param id; a placeholder has no blob, so it ties by its param id); else, where exactly one stored and one listed scene fit each other, by one of its own lightEffects' codes or by category and blob (a printed code or category move), or by category and param id or category alone (printed as a guess). A guess passes over a stored scene whose code the library still answers under any sceneName, or whose param id another listed scene carries (its scene, listed under another name), and a blob match over one it still answers at its own sceneName and category; both pass over one a fetch found gone from the library, or found its scene (its param id) listed under another name or left out only for its time or rules at another code, since it was last listed, but a blob match with its param id too (its scene back at a new code: H6006's Motunui, dropped with Disney's Moana and back at 1176 from 4476). Where more than one fits, the scene takes a new name, printed as ambiguous. Only an unmatched scene gets a new name, never one the store or the tombstone holds: its sceneName, or `<name> (<category>)` / `<name> (<category>, n)` where that is taken, with the sceneName kept in `scene_name` (of the new scenes of one sceneName the highest code keeps it, whatever the list order, so a first fetch names a response alike whatever the store holds: no other SKU's names are taken). A stored scene no longer listed keeps its name: one the library dropped, one left out only for its time (an expired category or effect) or the firmware's version rules, one listed under another name or left out so at another code (its param id). A sceneName the app renames keeps its old name too, so two names hold one code (`scene_names_by_code` gives the first). Only one whose scene the SKU's scene list refuses for good (its op set), or a sceneType-5 scene the library does not upload, is removed (where two stored scenes the fetch does not list could be that scene, neither is), printed and retired in **`tools/scene_tombstone.json`**, the tombstone, which is committed with the store and is not in the package (neither the sdist nor the wheel has `tools/`): per SKU, `retired` maps each name the store lost (whatever removed it) to the entry it held (one the fetch matched, then cut: the entry it was to hold), and only that scene's exact return (sceneName, code, category and blob; a placeholder's with its param id; a blob `--resolve` baked in returns as its placeholder of the same param id, and comes back with the name) takes the name back, printed where the sceneType-5 cut keeps it; `dropped` lists the stored names whose scene a fetch found gone from the library (its sceneName listed, or left out only for its time or rules, at its code under no category but where another stored or retired name sits), or found (its param id) listed under another name or left out only for its time or rules at another code, since they were last listed: only a match by code and category, or by category, blob and param id (its scene back at a new code), keeps one, and a scene of another blob refused at its place later is not its own. It also holds `store_sha256`, the sha256 of the `store.json` it was written with (every write of the store, `--resolve`'s too, writes the tombstone first where that changes it), so `tests/test_fetch_scenes_store.py` fails a `store.json` committed without its tombstone, and a run (`--resolve` too) over a tombstone that is missing or not the store's fetches and writes nothing: restore `tools/scene_tombstone.json` from git, or pass `--reset-tombstone` for a deliberate reset (the run goes on over the tombstone as it is, none where it is missing, and its store write pairs them again; the names a lost tombstone held are free). The sha256 can't see a change of the tombstone alone (a name dropped leaves `store.json`'s bytes as they were), so a run that changes the tombstone ends by printing `tools/scene_tombstone.json changed (N records): commit it with this run`: always commit the tombstone diff with its run, even when `store.json` didn't change. A corrupt tombstone fails every SKU and is left as it is. `TOMBSTONE` (like `OUT_DIR`) points the tool elsewhere; point both for a scratch run: with only `OUT_DIR` moved, the committed tombstone still pairs with the bundled store, so its `store_sha256` is left as it is (its records are still written). SKUs must be 3-8 alphanumerics; any failed fetch or write (or a corrupt existing store, which fails every SKU and is left as it is) is reported per SKU and exits non-zero; `--app-version` / `$GOVEE_APP_VERSION` overrides the AppVersion header (default 7.7.02, the traced app). |
| `decrypt_btsnoop.py <log>` | Decrypt a Govee AES-RC4-PSK btsnoop capture: derives the per-connection session key from the `e7` handshake (a device `e7 01` reply counts only after a host `e7 01` request on that connection), decrypts every ATT write/notify, BCC-validates (20-byte frames, and exact-length MTU-sized / short `0xA4` and `0xA6` multi-packet frames, plus `0xA3` as an allowance; a mis-keyed exact-length frame slips through ~3/256 × 1/256), resolves device MAC per ACL handle and keeps session keys per connection. `--starts` dumps multi-packet START frames; `--dump a1,a3,a4,a6` (the default); `--all`; `--limit N`. Reads H4 captures only (btsnoop datalink 1002, e.g. Android's HCI snoop log; other datalinks such as btmon's 2001 are refused). Records with a corrupt length (>1 MiB or past end of file) abort with an error. |
| `btsnoop_mtu_scan.py <log>` | Decryption-free scan: ATT Exchange-MTU values + write-length histogram (`--protype` adds a first-byte histogram of 20-byte writes). Works on encrypted links (ATT headers are plaintext). Also holds the btsnoop record reader `decrypt_btsnoop.py` reuses (H4 / datalink 1002 only, same corrupt-record checks). |
| `analyze_frame_log.py <frames.jsonl>` | Parse a captured plaintext frame log (from `GOVEE_FRAME_LOG` / `--capture`) against the generated Kaitai reader; reports a coverage histogram and flags frames the `.ksy` doesn't model (non-zero exit on a hard issue). A missing file exits 2; malformed lines are skipped, reported as `path:lineno: reason` on stderr, and counted in the summary. |
| `h60a6_live_check.py` | Comprehensive live H60A6 command check over ONE persistent connection: power / brightness / RGB / kelvin / zones / all segments / scene dialects, each with ACK + status read-back. `--capture PATH` logs all wire frames. `GOVEE_H60A6_ADDRESS=…` or `--address`. Its `find_device` (address, else best-RSSI H60A6 scan) is shared by the two probes below. |
| `field_test/` | Guided hardware test for a tester who has the device, and the release-gate pass over every device in range (runs from a checkout once its `_generated/` classes exist, else against the installed wheel; `--library checkout` or `--library installed` picks one, `--expect-version X.Y.Z` stops on a mismatch): `python tools/field_test`. One device: scans (records every Govee name heard), detects the SKU and runs its plan over one connection: the SKU's hand-written plan if it has one, else the generic plan built from its profile (`--generic` forces it). Each step is one command, then a read-back, judged pass / fail / unverifiable from the fields the row really reads back, then an optional y/n/skip question (`--ask all`, `unverifiable` or `none`; `--no-prompt`); the start state is restored at the end, also after an error or Ctrl-C, and checked with one more poll. `--list` scans only (every Govee device heard with its ref, SKU, tier, pact, kind and plan, then the plugs and their outlets; no connection, any hour); `--all` tests every supported device one at a time (`--allow-modelled` adds the opt-in rows, `--only` / `--skip` narrow it). Plugs get read-only steps unless allow-listed with `--toggle-plugs REF[,REF]` and confirmed at run time (`--secrets-file` or a hidden prompt for the secret). Visible writes run only from 07:00 to 22:00 local time (`--day-start` / `--day-end`, `--force-hours`; `--read-only` for read steps only), and a device whose every connect writes visibly (a bulb's `33 25` effect stop, read from its profile's connect writes) is not connected to outside the window or with `--read-only`. Writes a masked summary, and per device a JSON report and a frame capture: snapshots always redact the serial number, device id, Wi-Fi MAC and BLE MAC; the files mask Bluetooth addresses, Wi-Fi MACs, the MAC digits of advertised names (`ihoment_H6188_xxxx`) and plug secrets; a device is named by a keyed 6-digit ref; the frame capture drops every `wire` field and zeroes the `aa 07` and `ac` reply payloads (a status burst's first chunk keeps its 7-byte header, so the analyzer still checks its packet count). `--sku`, `--address`, `--out DIR`, `--settle S`, `--scan-seconds S`, `--retries N` (connect and discovery attempts: 3 with `--all`, 1 for one device), `--unattended` (with no terminal on stdin, one device or `--all`, visible writes are allowed; recorded). Exit codes: see [the table](#exit-codes). Hand-written plans: H6047, H6004, H6062, H6061, H6188. See [Adding a field-test plan](#adding-a-field-test-plan) and [Release-gate pass](#release-gate-pass-all-devices). |
| `power_semantics_probe.py <ADDRESS> <SKU>` | Hardware experiment: how the global power switch (`33 01`) interacts with zone power (`33 30`/`33 36`) and whether colour/brightness wake the device. Always restores the starting power/zone state, even on failure, with the same per-profile zone frames as the experiment (`33 36` for the bar light, `33 30` per zone otherwise); prints `RESTORE FAILED` if any restore frame isn't acked. |
| `h60a6_cct_choreography.py` | Live H60A6 colour-temperature choreography over one persistent connection; prints an OBSERVE line per step. `--address`/`GOVEE_H60A6_ADDRESS`, `--capture PATH`. |
| `h60a6_segment_map_probe.py` | Live H60A6 probe that maps segment indices to physical ring/top-panel positions. `--address`/`GOVEE_H60A6_ADDRESS`, `--capture PATH`. |

## Adding a field-test plan

The runner (`field_test/runner.py`) knows nothing about any SKU. Every supported SKU already gets the
generic plan (`field_test/generic.py`), built from its profile and Device API: power, brightness, RGB,
colour temperature at the range's ends and middle, an RGB pick of a Kelvin tint, segments, zones, a scene,
the gradual flag, and the refusals the library must make before any I/O. A hand-written plan is for what the
generic one can't know (a segment count to compare by eye, per-bar choreography, a device's quirks); it wins
for its SKU. It is one module, `field_test/plans/<sku>.py`, imported automatically (`plans/__init__.py` loads
every module in the directory), so a new device never edits the runner or another plan:

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
after the step. The keyword fields serve the release-gate verdicts and default to a hand plan's old
behaviour: `kind` (`"write"`, the default, is gated by the daytime window and `--read-only`; `"read"`
runs at any hour, so a step that sends nothing, such as the H6047's `segment_readback_count`, is `"read"`),
`fields` (the state fields judged against what the library intended, only where the
row reads them back and the step's poll set them: `readback.carried`; a field intended as it already was
proves nothing by matching), `refusal` (the exception the action must raise with nothing
sent), `touched` (a segment step's indices), `skip` and `check`. A hand-written step's verdict comes from
its `expect` (`True` pass, `False` fail, `None` unverifiable) and the tester's answer ("no" fails).
`field_test/plan.py` has step factories for the common sequences: `power_steps`, `brightness_steps`,
`rgb_steps`, `tint_pick_step`, `scene_steps`, and `state_is(key, value)` for an `expect`. A step built in
a loop binds its loop values with `functools.partial` (or `state_is`), not `lambda x=x:`. Optional
`SkuPlan` fields: `snapshot_keys` (state attributes to record; the serial number, device id, Wi-Fi MAC
and BLE MAC are always redacted), `restore(dev, initial_snapshot)` (default: brightness, then scene /
colour temperature / RGB, then power; it runs only once a write step was sent, under `--all` the generic
restore from the captured start follows it, and the generic restore check polls after them), `extra_name_hints` (advertised-name substrings that select the plan; the SKU is
already matched from the name and the SKU's own digits). Test it offline next to
`tests/test_field_test.py` with a fake device, within the suite's test budget. Under pytest
(`PYTEST_CURRENT_TEST`) or with `GOVEE_FIELD_TEST_NO_SCAN` set, the tool refuses every real Bluetooth scan
and connect (bleak's own `BleakScanner`, and every library connect while `establish_connection` is
still bleak-retry-connector's), so a test reaches only a fake, whatever its tree's conftest sets. Outside
pytest, a run with `GOVEE_FIELD_TEST_NO_SCAN` exported is refused before it starts (exit 3: unset it for a
real run).

## Release-gate pass (all devices)

Before every release, the user's gate is a full live pass over every Govee device in range of any of
their systems, on the built wheel, by day, every device restored to its start state, started by the user.
This tool is the Mac route; the bazzite bench and Home Assistant's proxies are covered by the Home
Assistant route (through the integration on the lab instance: bazzite's bt-proxy is one of its scanners;
host-side bleak on bazzite is off-limits, the adapter wedges). Run the two routes one after the other,
never at the same time.

1. Build and install the release candidate in a clean venv: `python -m build`, then
   `python3.13 -m venv /tmp/gate && /tmp/gate/bin/pip install dist/govee_ble_local-X.Y.Z-py3-none-any.whl`.
2. From the Desktop app's Terminal panel (CLI bleak is killed with SIGABRT on this Mac outside it), after
   07:00: `/tmp/gate/bin/python tools/field_test --list --library installed`. Note the plugs' refs and
   what each powers.
3. `/tmp/gate/bin/python tools/field_test --all --library installed --expect-version X.Y.Z
   --toggle-plugs <ref>[,<ref>] --out gate-X.Y.Z` (add `--allow-modelled` to include the opt-in rows).
   The tool lists every device, shows each allow-listed plug by its ref, its advertised name as the Govee
   app and the plug's label show it (on the console only, never in a file; its MAC digits only when stdout
   is a terminal, masked into a pipe or a `| tee` log, and never beside the address's vendor prefix) and its
   signal, asks what it powers (the answer is never stored) and has you type that plug's ref to let it
   toggle (anything else keeps it read-only), asks for its secret unless the secrets file (below) has it
   (every other device whose row needs a secret, every plug among them,
   takes it from that file only, and without one it can't pass, unless its connect sends the app's empty
   `33 b2` check and that check is acked: H6071 / H6075, H6058 on 1.9.0), then asks once before the first
   visible change. That answer (or
   `--yes`) is the whole run's: a run started before the window opens without `--yes` stays read-only to
   its end, even once the window opens. Each device then runs over its own connection, one at a time, and is restored before the next
   starts. With `--ask unverifiable` (the `--all` default) the tester answers only where nothing was read
   back.
   The secrets file is `~/.config/govee-ble-local/field-test-secrets.json` (`--secrets-file PATH` or
   `$GOVEE_FIELD_TEST_SECRETS` point elsewhere; never put it in a checkout: `field-test-secrets*.json` is
   git-ignored for that case): JSON `{"<ref or address>": "<16 hex digits>"}`, on each host that runs the
   tool, owner-only. Make it with
   `install -d -m 700 ~/.config/govee-ble-local && install -m 600 /dev/null ~/.config/govee-ble-local/field-test-secrets.json`,
   then edit it. What is checked, on the open file itself (never through a symlink, and a FIFO is never
   waited on): it is a regular file, its mode gives group and others no permission at all (`mode & 077` is
   0: 0600 or 0400; 0640, 0604 or 0610 are refused: `chmod 600` it), and on macOS it has no ACL entry
   (`ls -le` lists none; an ACL, such as an `everyone allow read` its folder passes on, is in no mode bit:
   `chmod -N` removes it). On Linux a POSIX ACL entry shows in the group bits, so the mode check covers it.
   Its owner and its folder's permissions are not checked. Anything else (a symlink, a folder, a FIFO, a
   file that isn't JSON) is refused (exit 3; the message gives the path, and the mode or the ACL, never a
   value), and no value is ever printed or written to a report. No file is no failure: a device that needs
   a secret and is given none stays read-only and the gate is `incomplete`. The device refs are keyed by
   `~/.cache/govee-ble-local/field-test-ref.key` (`$GOVEE_FIELD_TEST_REF_KEY` elsewhere), made 0600 in a
   0700 folder on first use; the same checks refuse it before any scan (exit 3, the path and why; a new
   one too, when its folder passes an ACL entry on): delete it to make a new one (every ref changes, so run
   `--list` again).
4. Attach `gate-X.Y.Z/fieldtest-summary-<time>.json` (and any failing device's report and frame
   capture) to the release PR. Its `gate` is `pass`, `fail` (a step failed, a device aborted, a restore
   was partial or failed, or unverified after a write it can never put back) or `incomplete` (a close
   restore of such a write, a device not reached, a device skipped because its connect
   writes visibly, a write skipped by the window, a write step nothing verified: no read-back and no
   answer, as with `--no-prompt` or an `s`; a device whose row needs a secret tested without one: every
   plug, allow-listed or not, since its `33 b2` unlock is then never sent; an interrupted run). Only
   `pass` clears the gate; the exit code says which ([Exit codes](#exit-codes)).

Notes:

- Ctrl-C once restores the current device, marks the rest "not run (interrupted)" and saves the summary
  (exit 130), wherever it lands: in a step (even between a command's frame and its ack), during the
  restore (which carries on) or at its question (left unanswered). A second Ctrl-C abandons that restore
  (`restore: abandoned`), and the device is printed `NOT RESTORED` with what the run changed. SIGHUP (the
  Terminal panel or tab closed, the app quit, an SSH session dropped) and SIGTERM do what the first Ctrl-C
  does; after either, nothing more is asked, and after SIGHUP nothing more is printed. Once a first
  interrupt is under way (a Ctrl-C, or one of them), either signal cancels nothing: the restore carries on,
  and only a second Ctrl-C abandons it. A console that stops taking output (a closed tty, a broken pipe
  through `| tee`) is silenced, never in the way of a restore, and the end of the input on a terminal (the
  tab closed under a prompt, Ctrl-D) is no answer: at the start question or at "go on with the next
  device?" it stops the run, the rest `not run (interrupted)` (exit 130).
- A device's frame capture is written under a hidden name (`.<sku>-NN-frames-<time>.jsonl.raw`) and only
  its redaction writes `<sku>-NN-frames-<time>.jsonl`, so a run killed outright (SIGKILL, a crash) leaves no
  raw capture under the name you attach; the next start (any mode, same `--out`) redacts what it left.
  Until then it is git-ignored (`*.jsonl.raw`), as is the pass's `--out gate-X.Y.Z` folder (`gate-*/`),
  so a `git add -A` in the checkout stages neither.
- A run with no terminal on stdin (cron, a detached session, stdin redirected), one device or `--all`,
  sends no visible write unless `--unattended` is given; the summary and every report record it
  (`options.unattended`, `options.writes_off`).
- A 2- or 3-outlet plug (H5082, H5089, H5160, H5161) switches every outlet with one frame, so it toggles
  only when the first read finds its outlets all on or all off (all off where the row doesn't say which
  relay bits are outlets: `--list` shows `2+ outlets`); from a mixed start its toggle is skipped. Its
  restore is checked outlet by outlet.
- Expect "not reached: connect failed" on devices Home Assistant (or the Govee app) is holding: a Govee
  light takes one central. The Home Assistant route covers those.
- Read steps still make the library's connect writes that change nothing visible (the handshake, a plug's
  unlock and time sync). A row whose every connect also makes a visible write, read from its profile's
  connect writes (the `33 25` effect stop of the goodsType 342 bulbs H14A1, R14A1, H14C2 and R14C2, which
  stops a running effect), is not connected to outside the window, in a run started there without `--yes`,
  or with `--read-only`. `--list` and the summary mark it `skipped: its connect sends 33 25 (effect stop), a
  visible change, outside the window` (or `on a read-only run`), and the gate is `incomplete`. Inside the
  window it runs as any other device. That is checked at its turn and again right before every connect
  attempt, since the re-scans and back-offs before one can carry the run past the window's end; once the
  window closes on it while connected, the tool polls it no more (a link it dropped would be re-opened,
  connect writes and all), so its remaining read steps are skipped, but its restore still runs. Nothing
  else is sent outside the window.
- A card row (one session per command, no read-back: H70B2) is read back from its next advertisement for
  power only; its brightness and its restore rest on the tester's answers.
- A step passes on a read-back only where the step's poll set the field in the library's state: a reply
  the library dropped (a bad checksum) or rejected (a status stream the app's read fails on) proves
  nothing, whatever frames arrived. A write step's field intended as it already was proves nothing by
  matching, and an RGB read after a colour-temperature write proves nothing on a report that can't show
  one (`rgb_kelvin_0d_nocct`, `rgb`): such a step is `unverifiable`, and the tester is asked. Segments are
  judged (and a restore checked) only where the poll set them: a status burst's colour groups are merged
  over the segments held, so one whose group no reply carried still holds a write's intent. A hand-written
  plan's check reads the same state: a check that reads a field the poll didn't read back leaves the step
  `unverifiable` (the report's `readback_unread`). A segment brightness step expects the whole groups the
  row's strip dims (`segment_brightness_groups`), and the gradual toggle (the H6047 plan's too) is skipped
  when the first poll read no start for it.
- A restore is `exact`, `close` (every field read back matches, but something can't be restored exactly: a
  scene the catalog doesn't list is re-activated by code, or the start colour, brightness or gradual flag
  was never read back),
  `partial` (fields differ), `failed`, `unverified` (nothing to compare), or `not_needed`. A scene start is
  put back as the library activates one: by its catalog name, with its upload where it needs one, after
  the segment levels the run changed; in a scene the check compares the segments' levels, not their
  colours (the scene paints them). When the run wrote something it can never put back (a start colour,
  brightness or power never read: the summary's `restore.unrestorable_written`), a `close` restore leaves
  the gate `incomplete` and an `unverified` one fails it.
- Under `--all` a `partial` or `failed` restore is tried twice more (2 s, then 5 s, each after a fresh scan
  and connect). A device still not back is printed as `NOT RESTORED: <ref> <sku> (<kind>): <what>`, plugs
  first, again at the end, and the run asks whether to go on (with `--no-prompt` it stops: the rest are
  `not run (stopped: <ref> was not restored)`). Under `--all` a hand-written plan's restore is followed by
  the generic one from the captured start, which puts back what the plan's own doesn't (the H6062's
  segment levels, the H6061's mixed colours).

### Exit codes

| Code | One device | `--all` |
|---|---|---|
| 0 | The plan completed (its verdicts are in the report and the summary). | `gate` `pass`. |
| 1 | It could not complete: not reached, aborted, or skipped (its connect writes visibly and writes aren't allowed). | `gate` `fail`: a failed step, an aborted device, a partial or failed restore, or an unverified restore after a write it can never put back. |
| 2 | No device found, or no plan for it. | Nothing to test. |
| 3 | Refused: the wrong library or version, a bad `--secrets-file` or ref key (not a regular file, any group or others bit in its mode, an ACL entry on macOS; a secrets file that isn't JSON), `GOVEE_FIELD_TEST_NO_SCAN` set outside the test suite. | Refused: the same, a declined start, or a run with visible writes and neither prompts nor `--yes`. |
| 4 | Not used. | `gate` `incomplete`: a device not reached or skipped, a write skipped by the window or not verified, a device whose row needs a secret (every plug) tested without one, a close restore of a write it can never put back. |
| 130 | Interrupted (Ctrl-C, SIGHUP, SIGTERM). | Interrupted (Ctrl-C, SIGHUP, SIGTERM), whatever the gate. |

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

`spec/` is the machine-readable protocol reference: the nine `govee_*.ksy` roots (frame, state, status
stream, upload value, profile, advertisement, scan record, BGC info and the shared `govee_common`; Kaitai),
`devices.yaml` (+ `devices.schema.json`, the device/capability table) and `checks/run.sh` (the spec's
structural lints, which `scripts/check.sh` runs). `gen_kaitai.sh` compiles every `.ksy` in one call to the
read-write classes under `src/govee_ble_local/_generated/` (generated by every build, never committed),
which the library builds and parses frames with; the offline suite (`pytest`) round-trips the library's
frames through them and validates `devices.yaml` against its schema.
