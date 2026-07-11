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

Not on PyPI yet — install from source:

```bash
pip install "git+https://github.com/Brady-Woods/govee-ble-local.git"
# or, from a clone (editable):
git clone https://github.com/Brady-Woods/govee-ble-local.git
cd govee-ble-local && pip install -e .
```

Requires Python 3.11+. Runtime dependencies are deliberately minimal and Home-Assistant-friendly:
`bleak`, `bleak-retry-connector`, `cryptography` (all shipped with HA), plus `kaitaistruct` (a
pure-Python, stdlib-only wire-parser runtime). Dev/test/docs extras: `.[test]`, `.[typing]`, `.[docs]`.

## Quick start

```python
import asyncio
from govee_ble_local import create_device, discover

async def main():
    # Active-scan for supported Govee devices (strongest advertisement per address).
    devices = await discover(timeout=8.0)
    found = next(d for d in devices if d.sku == "H60A6")

    dev = create_device(found.ble_device, found.sku)

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

- **`discover(timeout=10.0, *, supported_only=True)`** — active-scan → `list[DiscoveredDevice]`
  (`.ble_device`, `.advertisement`, `.sku`, `.rssi`, `.supported`). `supported(name, mfg)` and
  `match(ble_device, adv)` are the passive-matcher hooks for a HA-style scanner.
- **`create_device(ble_device, sku, advertisement_data=None, *, secret=None, frame_log=None)`**
  — construct a `Device` for the SKU (raises `GoveeBleNotSupported` if uncurated).
  `is_supported_sku(sku)` / `supported_skus()` / `device_profile_for(sku)` query the table.
- **`Device`** — one capability-gated class (behaviour comes from its `DeviceProfile`, not
  subclasses). Commands are ACK-confirmed on the wire:
  `turn_on` / `turn_off` / `set_power`, `set_brightness`, `set_rgb`, `set_color_temp`,
  `set_segment_rgb` / `set_segment_brightness` / `set_segment_color_temp`,
  `set_zone_power` / `set_zone_rgb` / `set_zone_color_temp`,
  `set_scene` / `set_scene_by_name`, and `update()` (read-back). Introspection:
  `capabilities`, `zones`, `min_kelvin` / `max_kelvin`, `scene_names`, `state`,
  `register_callback`. Methods for unsupported capabilities raise `GoveeBleNotSupported`.
- **`DeviceState`** — parsed state: `is_on`, `brightness` (0–100), `rgb_color`,
  `color_temp_kelvin`, `segments` (`list[Segment]`), `zone_power`, `scene_code`, plus
  device-info (`serial_number`, `wifi_mac`, `hardware_version`, `firmware_version`, `ble_mac`).
- **`Capability`** — `POWER`, `BRIGHTNESS`, `RGB`, `COLOR_TEMP`, `SEGMENTS`, `SCENES`.

Curated SKUs: **H60A6**, **H6047**, **H61A8**, **H6006/H6008**, **H6052**, **H6641**, and the
plug family (**H5080/H5082/H5083/H5085/H5089/H5160/H5161**). Segment-colour read-back and some
scene dialects on the non-H60A6 SKUs are source-modelled and not yet hardware-verified.

The full API reference is generated from the docstrings with [`pdoc`](https://pdoc.dev)
(`pip install '.[docs]' && bash tools/gen_docs.sh` → `docs/api/`); `govee_ble_local.__all__` is the
public contract.

## Diagnostics & session capture

The library logs under the `govee_ble_local.*` hierarchy and can capture a full protocol
session (over a local adapter or a Home Assistant Bluetooth proxy) for analysis. Enable the
flow trace, or the per-frame firehose, and decode a capture with the bundled
`govee-ble-analyze` CLI. See [`docs/DIAGNOSTICS.md`](docs/DIAGNOSTICS.md).

## Protocol

The wire protocol is specified as Kaitai Struct definitions — [`spec/govee_ble.ksy`](spec/govee_ble.ksy)
(command/reply frames) and [`spec/govee_adv.ksy`](spec/govee_adv.ksy) (advertisements) — with the
per-device table in [`spec/devices.yaml`](spec/devices.yaml). The runtime readers under
`govee_ble_local/_generated/` are generated from those (source of truth: the decompiled Govee app).

## Contributing & versioning

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development setup, branching, commit, and release
conventions. Changes are tracked in [`CHANGELOG.md`](CHANGELOG.md); the project follows
[Semantic Versioning](https://semver.org/). Live hardware tests run against the curated SKUs above.

## License

[MIT](LICENSE) © Brady Woods
