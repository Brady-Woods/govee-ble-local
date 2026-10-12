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
(Device.read_secret / btsnoop) don't depend on it.
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

# Every request is bounded; the undocumented API occasionally hangs.
REQUEST_TIMEOUT = 20.0


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


SECRET_LEN = 8  # BLE secret length in bytes


def _dict_field(data: Any, key: str, what: str) -> dict[str, Any]:
    """`data[key]` as a dict ({} if absent/null); GoveeCloudError on wrong shape.
    `data` itself must be a dict. Never echoes the response content."""
    if not isinstance(data, dict):
        raise GoveeCloudError(f"{what} response was not a JSON object")
    v = data.get(key)
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise GoveeCloudError(f"{what} response had a malformed {key!r}")
    return v


def _list_field(data: Any, key: str, what: str) -> list[Any]:
    """`data[key]` as a list ([] if absent/null); GoveeCloudError on wrong shape."""
    if not isinstance(data, dict):
        raise GoveeCloudError(f"{what} response was not a JSON object")
    v = data.get(key)
    if v is None:
        return []
    if not isinstance(v, list):
        raise GoveeCloudError(f"{what} response had a malformed {key!r}")
    return v


class GoveeCloudAccount:
    """Authenticated Govee account session for provisioning."""

    def __init__(
        self,
        email: str,
        password: str,
        *,
        token: str | None = None,
        session: aiohttp.ClientSession | None = None,
        app_version: str = APP_VERSION,
    ) -> None:
        """`app_version` is the Govee Home appVersion sent in request headers
        (default: the APK version this library was built against). Override it
        if Govee starts rejecting the default as too old."""
        self._app_version = app_version
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
            "appVersion": self._app_version,
            "clientId": self._client_id,
            "clientType": "1",
            "iotVersion": "0",
            "timestamp": str(int(time.time() * 1000)),
            "User-Agent": f"GoveeHome/{self._app_version} (com.govee.home; Android)",
            "Content-Type": "application/json",
        }
        if auth and self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            )
        return self._session

    async def _post(self, url: str, body: dict[str, Any], *, auth: bool) -> tuple[int, Any]:
        session = await self._ensure_session()
        try:
            async with session.post(
                url, json=body, headers=self._headers(auth=auth),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                return resp.status, await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise GoveeCloudError(f"request to {url} failed: {err}") from err
        except ValueError as err:
            # Invalid JSON: the decoder message can quote the body, so omit it.
            raise GoveeCloudError(f"request to {url} failed: response was not valid JSON") from err

    async def _authed_post(self, path: str, body: dict[str, Any]) -> Any:
        """POST with the bearer token; on 401 log in again once (token expiry)."""
        if self._token is None:
            await self.login()
        status, data = await self._post(BASE_URL + path, body, auth=True)
        if status == 401:
            await self.login()
            status, data = await self._post(BASE_URL + path, body, auth=True)
        if status != 200:
            raise GoveeCloudError(f"{path} failed (HTTP {status})")
        return data

    async def login(self) -> None:
        """Authenticate; stores the bearer token (community login host)."""
        body = {"email": self._email, "password": self._password, "client": self._client_id}
        status, data = await self._post(LOGIN_URL, body, auth=False)
        if status != 200 or not isinstance(data, dict):
            # Don't echo the response body: it can carry account details.
            raise GoveeCloudError(f"login failed (HTTP {status})")
        token = _dict_field(data, "data", "login response").get("token") or _dict_field(
            data, "client", "login response"
        ).get("token")
        if not token:
            raise GoveeCloudError("login response had no token")
        self._token = token

    async def get_devices(self) -> list[CloudDevice]:
        """Fetch all devices on the account with secrets + protocol metadata."""
        data = await self._authed_post(DEVICE_LIST_PATH, {})
        raw = _list_field(data, "devices", "device list") or _list_field(
            _dict_field(data, "data", "device list"), "devices", "device list"
        )
        parsed: list[CloudDevice] = []
        for d in raw:
            if not isinstance(d, dict):
                raise GoveeCloudError("device list had a malformed entry")
            parsed.append(self._parse(d))
        return parsed

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
                if not isinstance(settings, dict):
                    settings = {}

        secret_b64 = d.get("secretCode") or d.get("secret_code") or settings.get("secretCode")
        ble_mac = settings.get("address")
        if not (isinstance(ble_mac, str) and ble_mac):
            parts = str(d.get("device", "")).split(":")
            ble_mac = ":".join(parts[-6:]) if len(parts) >= 6 else None

        secret: bytes | None = None
        if secret_b64:
            # Never pass a malformed/odd-length secret on; never echo its value.
            try:
                if not isinstance(secret_b64, str):
                    raise ValueError("not a string")
                secret = base64.b64decode(secret_b64, validate=True)
                if len(secret) != SECRET_LEN:
                    raise ValueError("wrong length")
            except ValueError as err:  # binascii.Error is a ValueError
                who = (
                    str(d.get("sku") or "").upper()
                    or (ble_mac if isinstance(ble_mac, str) else "")
                    or "unknown device"
                )
                raise GoveeCloudError(f"device secret for {who} is malformed") from err

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
        data = await self._authed_post(EFFECT_STRS_PATH, {"scenceParamIds": param_ids})
        strs = _list_field(data, "effectStrs", "effect-strs") or _list_field(
            _dict_field(data, "data", "effect-strs"), "effectStrs", "effect-strs"
        )
        out: dict[int, str] = {}
        for e in strs:
            if not isinstance(e, dict):
                raise GoveeCloudError("effect-strs response had a malformed entry")
            pid, s = e.get("scenceParamId"), e.get("effectStr")
            if pid is not None and s:
                if not isinstance(s, str) or isinstance(pid, (bool, list, dict)):
                    raise GoveeCloudError("effect-strs response had a malformed entry")
                try:
                    out[int(pid)] = s
                except (TypeError, ValueError) as err:
                    raise GoveeCloudError("effect-strs response had a malformed scene id") from err
        return out

    async def close(self) -> None:
        if self._session is not None and self._owns_session:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> GoveeCloudAccount:
        try:
            if self._token is None:
                await self.login()
        except BaseException:
            await self.close()  # don't leak an owned session on a failed login
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
