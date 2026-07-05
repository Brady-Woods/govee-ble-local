"""Optional Govee-account cloud provisioning.

The library never *requires* the cloud. This module is one of three ways to
obtain a device's BLE secret + protocol metadata:
  1. cloud (here): one account login returns secret_code + sku/pact for every
     device (GoveeCloudAccount);
  2. on-device read: GoveeDevice.read_secret() (`aa b1`, offline, unbound only);
  3. btsnoop capture of the official app.

Requires the optional `aiohttp` dependency (install `govee-ble-local[cloud]`).
"""
from .account import CloudDevice, GoveeCloudAccount

__all__ = ["CloudDevice", "GoveeCloudAccount"]
