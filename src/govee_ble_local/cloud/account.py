"""Govee-account (undocumented app API) client — provisions BLE secrets +
protocol metadata for every device on the account in one login.

Flow (matches the official app and the community govee2mqtt project):
  1. POST /account/rest/account/v1/login  {email, password, client}
     -> bearer token
  2. POST /device/rest/devices/v1/list     (Authorization: Bearer <token>)
     -> per-device: sku, device, pactType, pactCode, goodsType, secretCode

`secretCode` is base64 of the 8 BLE secret bytes (same encoding the app's
SecretKeyController uses). This is an undocumented API and Govee enforces a
current `appVersion` header — we send the version of the APK this was built
against. It can break if Govee changes the backend; the offline paths
(GoveeDevice.read_secret / btsnoop) don't depend on it.
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

try:
    import aiohttp
except ModuleNotFoundError as err:  # pragma: no cover
    raise ImportError("cloud provisioning needs aiohttp: pip install 'govee-ble-local[cloud]'") from err

from ..exceptions import GoveeBleError

BASE_URL = "https://app2.govee.com"
# Login: the app's own /bff-app/v2/account/login is @EncryptDecrypt (RSA-2048 +
# AES-GCM request envelope) as of v7.5.20, so we use the community login host,
# which returns a plain bearer token that IS accepted by the app2 bff-app
# endpoints (verified against /bff-app/v2/devices/scenes/effect-strs). This is
# the same flow the govee2mqtt project uses.
LOGIN_URL = "https://community-api.govee.com/os/v1/login"
DEVICE_LIST_PATH = "/device/rest/devices/v1/list"
# Real effect blobs for placeholder/"big" scenes, keyed by scenceParamId
# (ScenesApi.getSceneEffectStrV2). Fixes scenes the public library stubs out.
EFFECT_STRS_PATH = "/bff-app/v2/devices/scenes/effect-strs"

# Version of the Govee Home APK this library was reverse-engineered against.
# Govee rejects logins with an out-of-date appVersion ("app version too low").
APP_VERSION = "7.5.20"


@dataclass(frozen=True)
class CloudDevice:
    """One device as reported by the account API."""

    sku: str
    device: str            # Govee device id (embeds the Wi-Fi MAC in its last 6 octets)
    name: str
    secret: bytes | None   # 8-byte BLE secret (decoded from secretCode), if present
    pact_type: int | None
    pact_code: int | None
    goods_type: int | None
    ble_mac: str | None = None   # the device's BLE MAC (from deviceSettings.address)


class GoveeCloudError(GoveeBleError):
    """A cloud-account API call failed."""


class GoveeCloudAccount:
    """Authenticated Govee account session for provisioning."""

    def __init__(
        self,
        email: str,
        password: str,
        *,
        token: str | None = None,
        session: "aiohttp.ClientSession | None" = None,
    ) -> None:
        self._email = email
        self._password = password
        self._client_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, email))
        # Pass a previously-saved token to skip login (see `token` property).
        self._token = token
        self._session = session
        self._owns_session = session is None

    @property
    def token(self) -> str | None:
        """The current bearer token. Persist this (NOT the password) and pass
        it back via the constructor to avoid re-logging in. Re-login happens
        automatically on expiry (401) when credentials are available."""
        return self._token

    def _headers(self, *, auth: bool = False) -> dict[str, str]:
        h = {
            "appVersion": APP_VERSION,
            "clientId": self._client_id,
            "clientType": "1",
            "iotVersion": "0",
            "timestamp": str(int(time.time() * 1000)),
            "User-Agent": f"GoveeHome/{APP_VERSION} (com.govee.home; Android)",
            "Content-Type": "application/json",
        }
        if auth and self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def _ensure_session(self) -> "aiohttp.ClientSession":
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def login(self) -> None:
        """Authenticate; stores the bearer token (community login host)."""
        session = await self._ensure_session()
        body = {"email": self._email, "password": self._password, "client": self._client_id}
        async with session.post(LOGIN_URL, json=body, headers=self._headers()) as resp:
            data = await resp.json(content_type=None)
        if resp.status != 200:
            raise GoveeCloudError(f"login failed (HTTP {resp.status}): {data}")
        token = (data.get("data") or {}).get("token") or (data.get("client") or {}).get("token")
        if not token:
            raise GoveeCloudError(f"login response had no token: {data}")
        self._token = token

    async def get_devices(self) -> list[CloudDevice]:
        """Fetch all devices on the account with secrets + protocol metadata."""
        if self._token is None:
            await self.login()
        session = await self._ensure_session()
        async with session.post(BASE_URL + DEVICE_LIST_PATH, json={}, headers=self._headers(auth=True)) as resp:
            data = await resp.json(content_type=None)
        if resp.status != 200:
            raise GoveeCloudError(f"device list failed (HTTP {resp.status}): {data}")
        raw = data.get("devices") or (data.get("data") or {}).get("devices") or []
        return [self._parse(d) for d in raw]

    @staticmethod
    def _parse(d: dict[str, Any]) -> CloudDevice:
        def _int(*keys: str) -> int | None:
            for k in keys:
                if d.get(k) is not None:
                    try:
                        return int(d[k])
                    except (TypeError, ValueError):
                        pass
            return None

        # The BLE secret and the real BLE MAC live inside deviceExt.deviceSettings,
        # which is itself a JSON string (not top-level). e.g.
        #   deviceSettings = '{"address":"98:17:3C:B1:A2:D1","secretCode":"..base64..",
        #                      "bleName":"ihoment_H5083_A2D1","wifiMac":"98:17:3C:B1:A2:D0",...}'
        # (the top-level `device` id embeds the Wi-Fi MAC, one off the BLE MAC.)
        ext = d.get("deviceExt")
        settings: dict[str, Any] = {}
        if isinstance(ext, dict):
            raw_settings = ext.get("deviceSettings")
            if isinstance(raw_settings, str) and raw_settings:
                try:
                    settings = json.loads(raw_settings)
                except (ValueError, TypeError):
                    settings = {}

        secret_b64 = d.get("secretCode") or d.get("secret_code") or settings.get("secretCode")
        secret: bytes | None = None
        if isinstance(secret_b64, str) and secret_b64:
            try:
                secret = base64.b64decode(secret_b64)
            except (ValueError, TypeError):
                secret = None

        ble_mac = settings.get("address")
        if not (isinstance(ble_mac, str) and ble_mac):
            parts = str(d.get("device", "")).split(":")
            ble_mac = ":".join(parts[-6:]) if len(parts) >= 6 else None

        ext_name = ext.get("deviceName") if isinstance(ext, dict) else None
        return CloudDevice(
            sku=str(d.get("sku", "")).upper(),
            device=str(d.get("device", "")),
            name=str(d.get("deviceName") or ext_name or d.get("sku", "")),
            secret=secret,
            pact_type=_int("pactType", "pact_type"),
            pact_code=_int("pactCode", "pact_code"),
            goods_type=_int("goodsType", "goods_type"),
            ble_mac=ble_mac if isinstance(ble_mac, str) else None,
        )

    async def get_scene_effect_strs(self, param_ids: list[int]) -> dict[int, str]:
        """Fetch the REAL effect blobs for scenes the public library stubs out
        (placeholder 0xff header), keyed by scenceParamId. Upload the returned
        base64 blobs via the a3-chunk burst, then activate — this is how the app
        renders 'big'/DIY-backed built-in scenes like Aurora."""
        if self._token is None:
            await self.login()
        session = await self._ensure_session()
        body = {"scenceParamIds": param_ids}
        async with session.post(BASE_URL + EFFECT_STRS_PATH, json=body, headers=self._headers(auth=True)) as resp:
            data = await resp.json(content_type=None)
        if resp.status != 200:
            raise GoveeCloudError(f"effect-strs failed (HTTP {resp.status}): {data}")
        strs = data.get("effectStrs") or (data.get("data") or {}).get("effectStrs") or []
        out: dict[int, str] = {}
        for e in strs:
            pid, s = e.get("scenceParamId"), e.get("effectStr")
            if pid is not None and s:
                out[int(pid)] = s
        return out

    async def close(self) -> None:
        if self._session is not None and self._owns_session:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> "GoveeCloudAccount":
        await self.login()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
