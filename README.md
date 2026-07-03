# govee-ble-local

**Local Bluetooth LE control of Govee lights — no cloud, no LAN API.**

A standalone Python library implementing Govee's encrypted BLE control
protocol (AES + RC4 handshake, packet framing, command construction, and
status parsing), reverse-engineered from packet captures. It's the protocol
engine behind the
[`hass-govee-ble-local`](https://github.com/bradywoods/hass-govee-ble-local)
Home Assistant integration, but has no Home Assistant dependency and can be
used on its own.

> ⚠️ **Unofficial.** Not affiliated with or endorsed by Govee. Reverse-engineered;
> behavior may change with device firmware. Verified on the **H60A6** (Ceiling
> Light Pro); other BLE models may work but are untested.

## Install

```bash
pip install govee-ble-local        # once published
# or, from source:
pip install -e .
```

Requires Python 3.11+. Depends on `bleak`, `bleak-retry-connector`, and `cryptography`.

## Quick start

```python
import asyncio
from bleak import BleakScanner
from govee_ble_local import GoveeBleClient, ZONE_UPPER, ZONE_LOWER

async def main():
    device = await BleakScanner.find_device_by_address("D4:13:68:21:D0:75")
    client = GoveeBleClient(device)

    await client.set_brightness_pct(60)
    await client.set_rgb_color(255, 0, 0)
    await client.set_zone(ZONE_UPPER, True)
    await client.set_zone(ZONE_LOWER, False)

    status = await client.get_status()
    print(status)   # zones, brightness, scene, MACs, hardware version

    await client.disconnect()

asyncio.run(main())
```

## API

- **`GoveeBleClient(ble_device)`** — on-demand encrypted session with idle
  auto-disconnect. Methods: `set_zone`, `set_brightness_pct`, `set_rgb_color`,
  `set_color_temp_kelvin`, `set_segment_color`, `set_segment_brightness`,
  `set_scene`, `set_scene_full`, `get_status`, `get_serial_number`,
  `update_ble_device`, `disconnect`.
- **`GoveeBleStatus`** / **`GoveeBleSegment`** — parsed state dataclasses.
- **`govee_ble_local.protocol`** — the pure, Bluetooth-free protocol functions
  (crypto, framing, `cmd_*` command builders, `parse_status`,
  `build_scene_chunks`, `kelvin_to_rgb`, …). Importable and testable without
  `bleak` installed.

## Protocol

The wire protocol — opcodes, the encryption scheme, status chunk layout, and
the reverse-engineering history — is documented in the Home Assistant
integration repo's
[`PROTOCOL.md`](https://github.com/bradywoods/hass-govee-ble-local/blob/master/custom_components/govee_h60a6/PROTOCOL.md).

## License

[MIT](LICENSE) © Brady Woods
