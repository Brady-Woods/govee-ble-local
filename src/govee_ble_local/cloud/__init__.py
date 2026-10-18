"""Optional Govee-account cloud provisioning.

The library never *requires* the cloud. This module is one of three ways to
obtain a device's BLE secret + protocol metadata:
  1. cloud (here): one account login returns secret_code + sku/pact for every
     device (GoveeCloudAccount);
  2. on-device read: Device.read_secret() (`aa b1`, offline, unbound only);
  3. btsnoop capture of the official app.

Requires the optional `aiohttp` dependency (install `govee-ble-local[cloud]`). The
exceptions (GoveeCloudError and its GoveeCloudAuthError subclass for rejected
credentials or tokens) are defined in `govee_ble_local.exceptions` and exported from
`govee_ble_local` too, so callers can name them without the extra installed.
"""
from ..exceptions import GoveeCloudAuthError, GoveeCloudError
from .account import CloudDevice, GoveeCloudAccount

__all__ = ["CloudDevice", "GoveeCloudAccount", "GoveeCloudAuthError", "GoveeCloudError"]
