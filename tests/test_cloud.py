"""Tests for cloud device parsing (secret + BLE MAC extraction)."""
from __future__ import annotations

import json

from govee_ble_local.cloud.account import GoveeCloudAccount


def test_parse_secret_and_ble_mac_from_device_settings() -> None:
    """The BLE secret + real BLE MAC come from deviceExt.deviceSettings (a JSON
    string); the top-level `device` id embeds the Wi-Fi MAC (one off)."""
    settings = {
        "address": "98:17:3C:B1:A2:D1",       # BLE MAC
        "wifiMac": "98:17:3C:B1:A2:D0",        # embedded in `device` id
        "bleName": "ihoment_H5083_A2D1",
        "secretCode": "YVUhCQtzXFQ=",          # 8-byte base64 secret
        "pactType": 2,
        "pactCode": 2,
    }
    raw = {
        "sku": "H5083",
        "device": "86:9D:98:17:3C:B1:A2:D0",
        "deviceName": "Plug",
        "pactType": 2,
        "pactCode": 2,
        "deviceExt": {"deviceSettings": json.dumps(settings)},
    }
    dev = GoveeCloudAccount._parse(raw)
    assert dev.sku == "H5083"
    assert dev.ble_mac == "98:17:3C:B1:A2:D1"          # from settings.address, not the device id
    assert dev.secret == bytes.fromhex("615521090b735c54")


def test_parse_no_settings_falls_back_to_device_id() -> None:
    """Without deviceSettings, ble_mac falls back to the device id's last 6 octets
    and secret is None."""
    dev = GoveeCloudAccount._parse(
        {"sku": "H6008", "device": "08:73:5C:E7:53:A6:26:90", "deviceName": "Bulb"}
    )
    assert dev.ble_mac == "5C:E7:53:A6:26:90"
    assert dev.secret is None
