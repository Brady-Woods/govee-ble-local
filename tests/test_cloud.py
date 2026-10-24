"""Tests for cloud device parsing (secret + BLE MAC extraction)."""
from __future__ import annotations

import json

from conftest import require_optional

require_optional("aiohttp")

from govee_ble_local.cloud.account import GoveeCloudAccount  # noqa: E402


def test_parse_secret_and_ble_mac_from_device_settings() -> None:
    """The BLE secret + real BLE MAC come from deviceExt.deviceSettings (a JSON
    string); the top-level `device` id embeds the Wi-Fi MAC (one off)."""
    settings = {
        "address": "00:00:5E:00:53:D1",       # BLE MAC
        "wifiMac": "00:00:5E:00:53:D0",        # embedded in `device` id
        "bleName": "ihoment_H5083_A2D1",
        "secretCode": "ASNFZ4mrze8=",          # 8-byte base64 secret
        "pactType": 2,
        "pactCode": 2,
    }
    raw = {
        "sku": "H5083",
        "device": "86:9D:00:00:5E:00:53:D0",
        "deviceName": "Plug",
        "pactType": 2,
        "pactCode": 2,
        "deviceExt": {"deviceSettings": json.dumps(settings)},
    }
    dev = GoveeCloudAccount._parse(raw)
    assert dev.sku == "H5083"
    assert dev.ble_mac == "00:00:5E:00:53:D1"          # from settings.address, not the device id
    assert dev.secret == bytes.fromhex("0123456789abcdef")


def test_parse_no_settings_falls_back_to_device_id() -> None:
    """Without deviceSettings, ble_mac falls back to the device id's last 6 octets
    and secret is None."""
    dev = GoveeCloudAccount._parse(
        {"sku": "H6008", "device": "0A:08:00:00:5E:00:53:08", "deviceName": "Bulb"}
    )
    assert dev.ble_mac == "00:00:5E:00:53:08"
    assert dev.secret is None


async def test_authed_post_relogs_in_once_on_401() -> None:
    from govee_ble_local.cloud.account import GoveeCloudAccount

    acct = GoveeCloudAccount("a@b.c", "pw", token="stale")
    calls: list[tuple[str, bool]] = []
    responses = [
        (401, {}),                                   # stale token
        (200, {"data": {"token": "fresh"}}),         # login
        (200, {"devices": [{"sku": "H5083", "device": "AA:BB:CC:DD:EE:FF:00:11"}]}),
    ]

    async def fake_post(url: str, body: dict, *, auth: bool):  # type: ignore[no-untyped-def]
        calls.append((url, auth))
        return responses.pop(0)

    acct._post = fake_post  # type: ignore[method-assign]
    devices = await acct.get_devices()
    assert acct.token == "fresh"
    assert [d.sku for d in devices] == ["H5083"]
    assert len(calls) == 3


async def test_login_failure_does_not_echo_body() -> None:
    import pytest

    from govee_ble_local.cloud.account import GoveeCloudAccount, GoveeCloudError

    acct = GoveeCloudAccount("a@b.c", "pw")

    async def fake_post(url: str, body: dict, *, auth: bool):  # type: ignore[no-untyped-def]
        return 400, {"message": "secret-ish detail"}

    acct._post = fake_post  # type: ignore[method-assign]
    with pytest.raises(GoveeCloudError) as exc:
        await acct.login()
    assert "secret-ish" not in str(exc.value)


async def test_post_wraps_transport_errors() -> None:
    import aiohttp
    import pytest

    from govee_ble_local.cloud.account import GoveeCloudAccount, GoveeCloudError

    class _Session:
        def post(self, *_a, **_k):  # type: ignore[no-untyped-def]
            raise aiohttp.ClientConnectionError("down")

        async def close(self) -> None:
            pass

    acct = GoveeCloudAccount("a@b.c", "pw", session=_Session())  # type: ignore[arg-type]
    with pytest.raises(GoveeCloudError):
        await acct.login()
    await acct.close()
