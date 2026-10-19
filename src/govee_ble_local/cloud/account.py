"""Govee-account (undocumented app API) client for BLE secrets and protocol metadata.

One login provisions every device on the account. Flow (the community govee2mqtt
project's; see ``LOGIN_URL`` for why the login host differs from the app's):
  1. POST https://community-api.govee.com/os/v1/login  {email, password, client}
     -> bearer token
  2. POST https://app2.govee.com/device/rest/devices/v1/list  (Authorization: Bearer <token>)
     -> per-device: sku, device, pactType, pactCode, goodsType, secretCode

`secretCode` is base64 of the 8 BLE secret bytes (same encoding the app's
SecretKeyController uses). This is an undocumented API and Govee enforces a
current `appVersion` header — we send the version of the APK this was built
against. It can break if Govee changes the backend; the offline paths
(Device.read_secret / btsnoop) don't depend on it.

Errors: every failure raises GoveeCloudError; rejected credentials, or a saved token
that is rejected with no credentials to log in again, raise its GoveeCloudAuthError
subclass.
"""
from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Final

try:
    import aiohttp
except ModuleNotFoundError as err:  # pragma: no cover
    raise ImportError("cloud provisioning needs aiohttp: pip install 'govee-ble-local[cloud]'") from err

from ..exceptions import GoveeCloudAuthError, GoveeCloudError
from ..wire.build import SECRET_LEN  # BLE secret length in bytes (one definition, wire layer)

_LOGGER = logging.getLogger(__name__)

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

# Status codes that unambiguously mean "credentials/token rejected". The community login's
# failure body is undocumented, so only these (as the HTTP status, or the body's embedded
# `status` field that Govee's APIs carry) map to GoveeCloudAuthError; anything else stays a
# plain GoveeCloudError rather than guessing.
_AUTH_REJECTED: Final = frozenset({401, 403})

# What decoding a malformed response body raises: ValueError for invalid JSON or bad UTF-8,
# RecursionError (a RuntimeError) for a body nested too deeply for the decoder. Both become
# GoveeCloudError, so callers only ever need to catch that.
_JSON_ERRORS: Final = (ValueError, RecursionError)


@dataclass(frozen=True)
class CloudDevice:
    """One device as reported by the account API.

    The repr leaves out ``secret``, so logging a device (or the list from
    :meth:`GoveeCloudAccount.get_devices`) never writes the account-lock key to a log.
    """

    sku: str
    """Upper-case model number, e.g. ``"H6008"`` (empty if the API sent none)."""
    device: str
    """Govee device id (embeds the Wi-Fi MAC in its last 6 octets)."""
    name: str
    """The device name from the Govee app (falls back to the SKU)."""
    secret: bytes | None = field(repr=False)
    """8-byte BLE secret (decoded from ``secretCode``), or None if absent or malformed."""
    pact_type: int | None
    """Protocol type (``pactType``)."""
    pact_code: int | None
    """Protocol code (``pactCode``)."""
    goods_type: int | None
    """Govee goodsType (``goodsType``)."""
    ble_mac: str | None = None
    """The device's BLE MAC, best effort.

    From ``deviceExt.deviceSettings.address`` when the API sends it. Otherwise it is the last
    6 octets of :attr:`device`, which for a Wi-Fi SKU is the Wi-Fi MAC (one off the BLE MAC),
    not the address it advertises over BLE.
    """


def _dict_field(data: object, key: str, what: str) -> dict[str, Any]:
    """``data[key]`` as a dict ({} if absent or null).

    ``data`` itself must be a dict. Raises GoveeCloudError on the wrong shape and never
    echoes the response content.
    """
    if not isinstance(data, dict):
        raise GoveeCloudError(f"{what} response was not a JSON object")
    v = data.get(key)
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise GoveeCloudError(f"{what} response had a malformed {key!r}")
    return v


def _list_field(data: object, key: str, what: str) -> list[Any]:
    """``data[key]`` as a list ([] if absent or null); GoveeCloudError on the wrong shape."""
    if not isinstance(data, dict):
        raise GoveeCloudError(f"{what} response was not a JSON object")
    v = data.get(key)
    if v is None:
        return []
    if not isinstance(v, list):
        raise GoveeCloudError(f"{what} response had a malformed {key!r}")
    return v


def _body_status(data: object) -> int | None:
    """The integer ``status`` field a Govee JSON body may embed, or None."""
    status = data.get("status") if isinstance(data, dict) else None
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _decode_secret(value: object) -> bytes | None:
    """The BLE secret from a base64 ``secretCode``, or None if it is malformed."""
    if not isinstance(value, str):
        return None
    try:
        secret = base64.b64decode(value, validate=True)
    except ValueError:  # binascii.Error, or a non-ASCII str
        return None
    return secret if len(secret) == SECRET_LEN else None


class GoveeCloudAccount:
    """Authenticated Govee account session for provisioning.

    Use it as ``async with GoveeCloudAccount(email, password) as acct:`` (which logs in on
    enter unless ``token`` was given, and closes on exit), or call :meth:`login` and
    :meth:`close` yourself. Requests made before a login log in first.
    """

    def __init__(
        self,
        email: str,
        password: str,
        *,
        token: str | None = None,
        session: aiohttp.ClientSession | None = None,
        app_version: str = APP_VERSION,
    ) -> None:
        """Create an account client; nothing is sent until the first request or login.

        ``token``: a previously saved bearer token (see :attr:`token`) to skip the login.
        ``session``: an aiohttp session to reuse (e.g. Home Assistant's shared one); it is
        never closed by :meth:`close`. Without one the client creates and owns its own.
        ``app_version``: the Govee Home appVersion sent in request headers (default: the
        APK version this library was built against). Override it if Govee starts rejecting
        the default as too old.
        """
        self._app_version = app_version
        self._email = email
        self._password = password
        self._client_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, email))
        self._token = token
        self._session = session
        self._owns_session = session is None

    @property
    def token(self) -> str | None:
        """The current bearer token.

        Persist this (NOT the password) and pass it back via the constructor to skip the
        login. On expiry (HTTP 401) the client logs in again once when it has an email and
        password; a token-only client raises GoveeCloudAuthError instead.
        """
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

    async def _post(self, url: str, body: dict[str, Any], *, auth: bool) -> tuple[int, object]:
        session = await self._ensure_session()
        try:
            # allow_redirects=False: neither endpoint redirects, and aiohttp re-sends the body
            # (the password, for the login) to any 307/308 Location, even cross-origin or to
            # plain http. A 3xx is reported as "HTTP 3xx" through the non-200 path instead.
            async with session.post(
                url, json=body, headers=self._headers(auth=auth),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                allow_redirects=False,
            ) as resp:
                status = resp.status
                if status != 200:
                    # Check the status first: a 401/5xx body is often HTML, and callers
                    # (re-login on 401, "HTTP n" errors) need the status, not a JSON error.
                    try:
                        return status, await resp.json(content_type=None)
                    except _JSON_ERRORS:
                        return status, None
                return status, await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise GoveeCloudError(f"request to {url} failed: {err}") from err
        except _JSON_ERRORS as err:
            # Invalid JSON: the decoder message can quote the body, so omit it.
            raise GoveeCloudError(f"request to {url} failed: response was not valid JSON") from err

    async def _authed_post(self, path: str, body: dict[str, Any]) -> object:
        """POST with the bearer token; on 401 log in again once (token expiry) if possible."""
        if self._token is None:
            await self.login()
        status, data = await self._post(BASE_URL + path, body, auth=True)
        if status == 401:
            if not (self._email and self._password):
                # Token-only client: nothing to log in with, so don't send empty credentials.
                raise GoveeCloudAuthError(f"{path} failed (HTTP 401): token rejected, log in again")
            await self.login()  # GoveeCloudAuthError if the credentials are rejected now
            status, data = await self._post(BASE_URL + path, body, auth=True)
        if status != 200:
            # A 401 right after a successful re-login is the endpoint refusing a fresh token,
            # not the user's credentials, so it stays a plain GoveeCloudError.
            raise GoveeCloudError(f"{path} failed (HTTP {status})")
        return data

    async def login(self) -> None:
        """Authenticate and store the bearer token (community login host).

        Raises GoveeCloudAuthError when the account rejects the credentials, and
        GoveeCloudError for any other failure.
        """
        body = {"email": self._email, "password": self._password, "client": self._client_id}
        status, data = await self._post(LOGIN_URL, body, auth=False)
        # Don't echo the response body in either error: it can carry account details.
        if status in _AUTH_REJECTED or _body_status(data) in _AUTH_REJECTED:
            raise GoveeCloudAuthError(f"login failed (HTTP {status}): credentials rejected")
        if status != 200 or not isinstance(data, dict):
            raise GoveeCloudError(f"login failed (HTTP {status})")
        token = _dict_field(data, "data", "login response").get("token") or _dict_field(
            data, "client", "login response"
        ).get("token")
        if not (isinstance(token, str) and token):
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
                v = d.get(k)
                # A non-integral float (7.9, inf, nan) is not an id: skip it, don't truncate it.
                if v is None or (isinstance(v, float) and not v.is_integer()):
                    continue
                try:
                    return int(v)
                except (TypeError, ValueError, OverflowError):
                    pass
            return None

        # The BLE secret and the real BLE MAC live inside deviceExt.deviceSettings,
        # which is itself a JSON string (not top-level). e.g.
        #   deviceSettings = '{"address":"AA:BB:CC:DD:EE:FF","secretCode":"..base64..",
        #                      "bleName":"ihoment_H5083_EEFF","wifiMac":"AA:BB:CC:DD:EE:FE",...}'
        # (the top-level `device` id embeds the Wi-Fi MAC, one off the BLE MAC.)
        ext = d.get("deviceExt")
        settings: dict[str, Any] = {}
        if isinstance(ext, dict):
            raw_settings = ext.get("deviceSettings")
            if isinstance(raw_settings, str) and raw_settings:
                try:
                    decoded = json.loads(raw_settings)
                except (ValueError, TypeError, RecursionError):
                    decoded = None
                if isinstance(decoded, dict):
                    settings = decoded

        secret_b64 = d.get("secretCode") or d.get("secret_code") or settings.get("secretCode")
        ble_mac = settings.get("address")
        if not (isinstance(ble_mac, str) and ble_mac):
            # Best effort (see CloudDevice.ble_mac): for a Wi-Fi SKU this is the Wi-Fi MAC.
            parts = str(d.get("device") or "").split(":")
            ble_mac = ":".join(parts[-6:]) if len(parts) >= 6 else None

        secret: bytes | None = None
        if secret_b64:
            # Never pass a malformed/odd-length secret on; never echo its value.
            secret = _decode_secret(secret_b64)
            if secret is None:
                who = (
                    str(d.get("sku") or "").upper()
                    or (ble_mac if isinstance(ble_mac, str) else "")
                    or "unknown device"
                )
                _LOGGER.warning("device secret for %s is malformed; ignoring it", who)

        ext_name = ext.get("deviceName") if isinstance(ext, dict) else None
        # `or ""`, not a .get() default: a key present with JSON null must not become "None".
        return CloudDevice(
            sku=str(d.get("sku") or "").upper(),
            device=str(d.get("device") or ""),
            name=str(d.get("deviceName") or ext_name or d.get("sku") or ""),
            secret=secret,
            pact_type=_int("pactType", "pact_type"),
            pact_code=_int("pactCode", "pact_code"),
            goods_type=_int("goodsType", "goods_type"),
            ble_mac=ble_mac if isinstance(ble_mac, str) else None,
        )

    async def get_scene_effect_strs(self, param_ids: list[int]) -> dict[int, str]:
        """Fetch the real effect blobs for placeholder scenes, keyed by scenceParamId.

        The public scene library ships these without their blob (a big-effect scene with an empty
        param, the app's signal to fetch it: BaseSceneViewMode / SceneDataVM). Upload the
        returned base64 blobs via the a3-chunk burst, then activate: this is how the app
        renders 'big'/DIY-backed built-in scenes like Aurora.
        """
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
                # int() would truncate 7.9 to scene 7 (and overflow on inf): reject it instead.
                if isinstance(pid, float) and not pid.is_integer():
                    raise GoveeCloudError("effect-strs response had a malformed scene id")
                try:
                    scene_id = int(pid)
                except (TypeError, ValueError, OverflowError) as err:
                    raise GoveeCloudError("effect-strs response had a malformed scene id") from err
                try:
                    base64.b64decode(s, validate=True)
                except ValueError as err:  # binascii.Error
                    raise GoveeCloudError(
                        f"effect-strs response had a malformed effect for scene {scene_id}"
                    ) from err
                out[scene_id] = s
        return out

    async def close(self) -> None:
        """Close the HTTP session if this client created it (a passed-in one stays open)."""
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
