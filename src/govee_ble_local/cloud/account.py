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
LOGIN_PATH = "/account/rest/account/v1/login"
DEVICE_LIST_PATH = "/device/rest/devices/v1/list"

# Version of the Govee Home APK this library was reverse-engineered against.
# Govee rejects logins with an out-of-date appVersion ("app version too low").
APP_VERSION = "7.5.20"


@dataclass(frozen=True)
class CloudDevice:
    """One device as reported by the account API."""

    sku: str
    device: str            # Govee device id (last 6 bytes = BLE MAC)
    name: str
    secret: bytes | None   # 8-byte BLE secret (decoded from secretCode), if present
    pact_type: int | None
    pact_code: int | None
    goods_type: int | None

    @property
    def ble_mac(self) -> str | None:
        """Best-effort BLE MAC from the device id (last 6 octets)."""
        parts = self.device.split(":")
        return ":".join(parts[-6:]) if len(parts) >= 6 else None


class GoveeCloudError(GoveeBleError):
    """A cloud-account API call failed."""


class GoveeCloudAccount:
    """Authenticated Govee account session for provisioning."""

    def __init__(self, email: str, password: str, *, session: "aiohttp.ClientSession | None" = None) -> None:
        self._email = email
        self._password = password
        self._client_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, email))
        self._token: str | None = None
        self._session = session
        self._owns_session = session is None

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
        """Authenticate; stores the bearer token."""
        session = await self._ensure_session()
        body = {"email": self._email, "password": self._password, "client": self._client_id}
        async with session.post(BASE_URL + LOGIN_PATH, json=body, headers=self._headers()) as resp:
            data = await resp.json(content_type=None)
        if resp.status != 200:
            raise GoveeCloudError(f"login failed (HTTP {resp.status}): {data}")
        token = (data.get("client") or {}).get("token") or (data.get("data") or {}).get("token")
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

        secret_b64 = d.get("secretCode") or d.get("secret_code")
        secret: bytes | None = None
        if isinstance(secret_b64, str) and secret_b64:
            try:
                secret = base64.b64decode(secret_b64)
            except (ValueError, TypeError):
                secret = None
        ext = d.get("deviceExt")
        ext_name = ext.get("deviceName") if isinstance(ext, dict) else None
        return CloudDevice(
            sku=str(d.get("sku", "")).upper(),
            device=str(d.get("device", "")),
            name=str(d.get("deviceName") or ext_name or d.get("sku", "")),
            secret=secret,
            pact_type=_int("pactType", "pact_type"),
            pact_code=_int("pactCode", "pact_code"),
            goods_type=_int("goodsType", "goods_type"),
        )

    async def close(self) -> None:
        if self._session is not None and self._owns_session:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> "GoveeCloudAccount":
        await self.login()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
