# govee-ble-local

**Local Bluetooth LE control of Govee devices — no cloud, no LAN API.**

A standalone Python library implementing Govee's BLE control protocol (AES-ECB+RC4
handshake, 20-byte command framing, multi-packet scene upload, and status read-back),
derived from the decompiled Govee Home app and verified against real hardware. It's the
protocol engine behind the
[`hass-govee-ble-local`](https://github.com/Brady-Woods/hass-govee-ble-local)
Home Assistant integration, but has no Home Assistant dependency and can be used on its own.

> ⚠️ **Unofficial.** Not affiliated with or endorsed by Govee. Reverse-engineered from the
> app + hardware; behavior may change with device firmware. Live-verified on the **H60A6**
> (Ceiling Light Pro); the other curated SKUs are modelled from the app and are not all
> hardware-verified (see the profile notes).

## Install

```bash
pip install govee-ble-local
# optional Govee cloud-account helpers (pulls in aiohttp):
pip install 'govee-ble-local[cloud]'
# or, from a clone (editable, for development; needs the JVM kaitai-struct-compiler 0.11:
#   brew install kaitai-struct-compiler, or bash scripts/install_ksc.sh on Debian/Ubuntu;
#   see CONTRIBUTING.md):
git clone https://github.com/Brady-Woods/govee-ble-local.git
cd govee-ble-local && pip install -e '.[dev]'
```

Requires Python 3.11+. Installing from PyPI needs no compiler: the wheel ships the generated Kaitai classes. Runtime dependencies are deliberately minimal and Home-Assistant-friendly:
`bleak`, `bleak-retry-connector`, `cryptography` (all shipped with HA), plus `kaitaistruct` (a
pure-Python, stdlib-only wire-parser runtime). Dev extras: `.[dev]` (everything a contributor needs), or individually `.[test]`, `.[typing]`,
`.[lint]`, `.[audit]`, `.[docs]`.

## Quick start

```python
import asyncio
from govee_ble_local import create_device, discover

async def main():
    # Active-scan for supported Govee devices (strongest advertisement per address).
    devices = await discover(timeout=8.0)
    found = next(d for d in devices if d.sku == "H60A6")

    dev = create_device(found.ble_device, found.sku, found.advertisement)

    await dev.turn_on()
    await dev.set_brightness(60)             # percent
    await dev.set_rgb((255, 0, 0))
    await dev.set_zone_power("background", False)

    state = await dev.update()               # read-back into dev.state
    print(state.is_on, state.brightness, state.rgb_color, state.segments)

    await dev.stop()

asyncio.run(main())
```

## API

The library follows Home-Assistant BLE-library conventions: identify a device from its
advertisement, build a capability-driven `Device`, then drive it.

- **`discover(timeout=10.0, *, supported_only=True, allow_modelled=False)`** — active-scan →
  `list[DiscoveredDevice]` (`.ble_device`, `.advertisement`, `.sku`, `.rssi`, `.supported`); raises
  `GoveeBleConnectionError` when the scan itself fails (no adapter, adapter off, no BlueZ/D-Bus);
  that error is also the `BleakError` / `OSError` / `EOFError` 1.3.0 raised, so either handler matches.
  `supported(name, mfg)` and `match(ble_device, adv)` are the passive-matcher hooks for a
  HA-style scanner.
- **`create_device(ble_device, sku, advertisement_data=None, *, secret=None, frame_log=None,
  idle_disconnect=None, goods_type=None, allow_modelled=False)`** — construct a `Device` for the SKU
  (raises `GoveeBleNotSupported` if unsupported). Pass the advertisement (`DiscoveredDevice.advertisement`
  or bleak's own): its encrypt flag selects the command channel, which some firmwares set differently
  from the SKU's default (H6008), and its pact the SKU's devices.yaml row. Both are read whatever the
  advertised name; `bound_advertisement(advertisement_data, sku, *, fallback_name=None)` is that read on
  its own, for a caller with no `Device` yet: the `GoveeAdvertisement` (`.pact`, `.encrypted`,
  `.encryption_known`) `create_device` routes by, e.g. the pact it refused. `is_supported_sku(sku)` /
  `supported_skus()` / `profile_for(sku)` query the table; `profile_for` and `is_supported_sku` take
  `pact=` / `goods_type=` to pick between rows. A SKU served only on a pact-gated row (the BK-firmware
  rows, H6076 / H8076 / R16D0, H70B6, and behind `allow_modelled` the h70b3 rows' H70B3 / H70B4 / H70B5 /
  H6810 / H6811 / H8811) needs `pact=`: `supported_skus()` lists it, but without the advertisement's pact
  (`GoveeAdvertisement.pact`) `is_supported_sku` and `profile_for` refuse it (`create_device` with the
  advertisement, `supported`, `match` and `discover` pass it already). `allow_modelled=True` (also on
  `supported_skus`, `create_device`, `supported`, `match` and `discover`) admits the rows the spec marks
  `modelled` (inferred from the app, not traced), which are off by default (`device_profile_for(sku)` is a
  deprecated alias of `profile_for`, removed in 2.0).
- **`Device`** — one capability-gated class (behaviour comes from its `DeviceProfile`, not
  subclasses). Commands are ACK-confirmed on the wire:
  `turn_on` / `turn_off` / `set_power`, `set_brightness`, `set_rgb`, `set_color_temp`,
  `set_segment_rgb` / `set_segment_brightness` / `set_segment_color_temp`,
  `set_zone_power` / `set_zone_rgb` / `set_zone_color_temp`,
  `set_scene` / `set_scene_by_name`, and `update()` (read-back). Introspection:
  `capabilities`, `zones`, `min_kelvin` / `max_kelvin`, `scene_names` (the scenes the Govee app lists at
  the device's scene set, `scene_versions`), `state`, `register_callback`, `power_read_at` (when a read
  last returned the on/off, while `state.is_on` still holds it). `advertised_on(advertisement_data)`
  decodes an advertisement's on/off as the device does, without changing state; `ingest_advertisement`
  folds it in. Methods for unsupported capabilities raise `GoveeBleNotSupported`, and so does
  `set_segment_color_temp` where `DeviceProfile.segment_color_temp` is false: where the SKU's colour page
  has no Part-tab Kelvin item, and where that item widens every pick to whole zones (the field's docstring
  lists those SKUs). SEGMENTS and COLOR_TEMP together don't promise it; `set_zone_color_temp` doesn't
  read the field. The link
  comes up with the first operation (or `connect()`) and drops `idle_disconnect` seconds after the last one
  (8 s by default), or 30 s after a command (`const.ACTIVE_IDLE_DELAY`, so quick follow-ups skip the
  reconnect). While it is up the `Device` sends the keep-alive read the SKU's control screen sends every 2 s
  (`DeviceProfile.heartbeat`), which never keeps the link longer; to send none, build the `Device` from
  `dataclasses.replace(profile, heartbeat=None)` (`create_device`'s docstring has the recipe). `release()`
  drops it now and ends that window, for a caller that knows no follow-up is coming (a voice command, an
  automation, a burst across many devices): it waits for each link operation already running or queued (FIFO)
  and never splits a scene's upload from its activation or an H6047 bar read from its write, but it can land
  between a poll's reads or a plug's power write and its re-read (their later steps reconnect; a plug's
  re-read ends with a `33 b5` write, which holds the new link a fresh 30 s); a command called after it that
  writes on the link first keeps the link and its window. It returns whether it dropped a link and never
  raises a `GoveeBleError`; the next operation reconnects. `stop()` closes the device for good.
- **`DeviceState`** — parsed state: `is_on`, `brightness` (0–100), `rgb_color`,
  `color_temp_kelvin`, `segments` (`list[Segment]`), `zone_power`, `zone_power_confirmed` (when each
  zone's power was last learned from the device), `zone_color_temp_kelvin` (H6047
  reads one Kelvin per bar), `gradual`, `scene_code`, plus
  device-info (`device_id` / `serial_number`, `wifi_mac`, `hardware_version`, `firmware_version`,
  `ble_mac`). It comes from the `aa 06`, `aa 07 03` and `aa 07 02` reads (the Govee app's own frames)
  and, on a status-read SKU, from the status reply's TLV 0x07. A status row's `device_info_reads` in
  `spec/devices.yaml` says whether the library sends the three reads there too: at `none` it sends none
  and takes these fields from TLV 0x07 alone, as the app's screen does. Every status row is at `none` but
  the H60A6's and the H6076's (`firmware`: their firmware answers the reads).
- **`Capability`** — `POWER`, `BRIGHTNESS`, `RGB`, `COLOR_TEMP`, `SEGMENTS`, `SCENES`.

Curated SKUs: **H60A6**, **H6047**, **H61A8**, **H6004** (the BLE revision; the Wi-Fi-only one can't be controlled over BLE), **H6006/H6008**, **H6052**, **H6641**, **H6188**, **H6062**, **H6061** (Glide Hexa: whole-device power, brightness, colour and colour temperature), and the
plug family (**H5080/H5082/H5083/H5085/H5089/H5160/H5161**). Govee's public catalogue (a snapshot in
`spec/govee_catalog.json`) also routes H6009, H600A-H600D, H6010, H6011, H6013 and H800B to the
H6004/H6006/H6008 profile by their goodsType (64), not bench-checked and without named scenes (scene
codes only). Segment-colour read-back and some
scene dialects on the non-H60A6 SKUs are source-modelled and not yet hardware-verified.

Colour temperature matches the Govee app's frames: like the app, an out-of-range Kelvin is clamped to
the device range, not rejected (`set_color_temp(1500)` on a 2700-6500 K device sends 2700 K), and the
result is rounded to a whole 100 K step (`set_color_temp(2750)` sends and reports 2800 K). `state`
holds the Kelvin actually sent. Only a non-integer raises `GoveeBleInvalidValue`. Every CCT frame
carries the app's Kelvin tint, and whole-device CCT is one frame (on the H60A6 the all-segments mask,
as the app's Kelvin sliders send it).

Like the app, a picked RGB colour that is exactly one of its Kelvin tint colours (178 of them, two per
100 K step) and whose Kelvin is inside the SKU's range is sent as a colour-temperature frame instead of
an RGB frame, and `state` then reports `color_temp_kelvin` with `rgb_color` unset: `set_rgb`, `turn_on(rgb=)`,
`set_zone_rgb` and `set_segment_rgb` on the 0x15 devices (H60A6, H61A8, H6641, H6062, H6047, with the same mask
the RGB frame would use), the 0x0d bulbs and strip (H6004, H6006, H6008, H6052, H6188) and H6061. Colours outside
the range or one channel off the table stay RGB. Not modelled: colour-set / mixed-colour picks and the real-time
drag frames (RGB only). The H61A8's Kelvin range is [2000, 9000], or [2700, 6500] from hardware 3.08.01; until its
hardware version is read (once per device, on the first poll) the range valid on both, [2700, 6500], applies
(`Device.min_kelvin` / `max_kelvin` give the range in force). Its mode report carries no Kelvin, so after a
colour-temperature write `state` keeps the Kelvin that was sent until an RGB or scene write or a scene
report. Because it reports the same `15 01 00 00` after an RGB write too, a Kelvin the library sent stays in
`state` across a colour change made in the Govee app.

Read-back decodes the `aa 05` mode report the way the app's control screen does for each SKU. The
H6052's screen reads its colour report as RGB only, so after a poll an H6052 in colour-temperature
mode reports the report's colour as `rgb_color` (for a CCT the app or this library set, the Kelvin's
tint) and no `color_temp_kelvin`.

The H6047 follows the app's frames too: whole-device power is `33 01` (`33 36` is only the per-bar
buttons, `set_zone_power`), read-back covers its 10 segments, `Device.zone_color_temp_kelvin(name)`
gives each bar's Kelvin, and `set_gradual` toggles the fade flag.

The full API reference is generated from the docstrings with [`pdoc`](https://pdoc.dev)
(`pip install '.[docs]' && bash tools/gen_docs.sh` → `docs/api/`); `govee_ble_local.__all__` is the
public contract.

## Security notes

The Govee BLE protocol itself has well-known limits that this library cannot fix:

- Its encryption uses a public pre-shared key, AES-ECB for the handshake and an RC4 stream for
  traffic, and integrity is only an XOR checksum. Anyone within radio range can drive an
  encrypted device.
- Captures made with `GOVEE_FRAME_LOG` contain wire frames from which the session key can be
  derived. Treat them as sensitive and do not post them publicly. Frame-log files are created
  with owner-only permissions.

## Diagnostics & session capture

The library logs under the `govee_ble_local.*` hierarchy and can capture a full protocol
session (over a local adapter or a Home Assistant Bluetooth proxy) for analysis. Enable the
flow trace, or the per-frame firehose, and decode a capture with the bundled
`govee-ble-analyze` CLI. See [`docs/DIAGNOSTICS.md`](docs/DIAGNOSTICS.md).

## Protocol

The wire protocol is specified as Kaitai Struct definitions in [`spec/`](spec/): one root per message
class — [`govee_ble_frame.ksy`](spec/govee_ble_frame.ksy) (every command/reply frame, typed by its
direction and the device's [`govee_profile`](spec/govee_profile.ksy)), the decoded state
([`govee_state.ksy`](spec/govee_state.ksy), [`govee_status_stream.ksy`](spec/govee_status_stream.ksy)),
the upload values ([`govee_upload.ksy`](spec/govee_upload.ksy)), the advertisement
([`govee_manufacturer_data.ksy`](spec/govee_manufacturer_data.ksy),
[`govee_scan_record.ksy`](spec/govee_scan_record.ksy)) and the BGC-info value
([`govee_bgc_info.ksy`](spec/govee_bgc_info.ksy)), sharing [`govee_common.ksy`](spec/govee_common.ksy)
— with the per-device table in [`spec/devices.yaml`](spec/devices.yaml). The Kaitai read-write classes
under `govee_ble_local/_generated/` are generated from those, and the library parses and builds every
frame with them; its supported-device table is generated from `devices.yaml` too (source of truth: the
decompiled Govee app).

## Contributing & versioning

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development setup, branching, commit, and release
conventions. Changes are tracked in [`CHANGELOG.md`](CHANGELOG.md); the project follows
[Semantic Versioning](https://semver.org/). Live hardware tests run against the curated SKUs above.

## License

[MIT](LICENSE) © Brady Woods
