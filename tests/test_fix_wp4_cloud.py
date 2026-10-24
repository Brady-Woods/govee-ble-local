"""Regression tests for the cloud-account review fixes (secret repr, redirects, 401 handling,
GoveeCloudAuthError, number/null hardening). aiohttp is mocked; nothing touches the network."""
from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from conftest import require_optional

require_optional("aiohttp")

import govee_ble_local as pkg  # noqa: E402
from govee_ble_local import cloud  # noqa: E402
from govee_ble_local.cloud import account as acc  # noqa: E402
from govee_ble_local.cloud.account import CloudDevice, GoveeCloudAccount  # noqa: E402
from govee_ble_local.exceptions import GoveeCloudAuthError, GoveeCloudError  # noqa: E402
from govee_ble_local.wire import build  # noqa: E402

_SECRET = bytes.fromhex("0123456789abcdef")
_DEVICES = (200, {"data": {"devices": [{"sku": "H6008", "device": "AA"}]}})


def _acct(**kw: Any) -> GoveeCloudAccount:
    return GoveeCloudAccount("a@b.c", "pw", **kw)


def _stub_post(acct: GoveeCloudAccount, responses: list[tuple[int, Any]]) -> list[str]:
    urls: list[str] = []

    async def fake_post(url: str, body: dict, *, auth: bool):  # type: ignore[no-untyped-def]
        urls.append(url)
        return responses.pop(0)

    acct._post = fake_post  # type: ignore[method-assign]
    return urls


class _Resp:
    def __init__(self, status: int, payload: Any = None) -> None:
        self.status, self._payload = status, payload

    async def __aenter__(self) -> _Resp:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def json(self, content_type: Any = None) -> Any:
        return self._payload


class _Session:
    def __init__(self, resp: _Resp) -> None:
        self.resp = resp
        self.kw: dict[str, Any] = {}

    def post(self, url: str, **kw: Any) -> _Resp:
        self.kw = kw
        return self.resp


# -- secret never in repr ------------------------------------------------------------------

def test_cloud_device_repr_omits_secret() -> None:
    dev = CloudDevice("H5083", "AA", "Plug", _SECRET, 2, 2, None)
    assert "secret" not in repr(dev) and repr(_SECRET) not in repr(dev)
    assert dev.secret == _SECRET                                    # still stored
    assert dataclasses.replace(dev, secret=None) != dev             # still part of equality
    assert [f.name for f in dataclasses.fields(CloudDevice)] == [   # constructor order unchanged
        "sku", "device", "name", "secret", "pact_type", "pact_code", "goods_type", "ble_mac",
    ]


# -- redirects are not followed ------------------------------------------------------------

async def test_post_does_not_follow_redirects() -> None:
    sess = _Session(_Resp(307))
    a = _acct(session=sess)  # type: ignore[arg-type]
    with pytest.raises(GoveeCloudError, match=r"login failed \(HTTP 307\)") as ei:
        await a.login()
    assert sess.kw["allow_redirects"] is False
    assert not isinstance(ei.value, GoveeCloudAuthError)


# -- 401 handling ----------------------------------------------------------------------------

async def test_persistent_401_relogs_in_once_then_raises() -> None:
    a = _acct(token="stale")
    urls = _stub_post(a, [(401, {}), (200, {"data": {"token": "fresh"}}), (401, {})])
    with pytest.raises(GoveeCloudError, match="HTTP 401") as ei:
        await a.get_devices()
    assert urls.count(acc.LOGIN_URL) == 1 and len(urls) == 3
    assert a.token == "fresh"
    # The fresh token was refused by the endpoint, not the user's credentials.
    assert not isinstance(ei.value, GoveeCloudAuthError)


async def test_get_devices_without_token_logs_in_first() -> None:
    a = _acct()
    urls = _stub_post(a, [(200, {"data": {"token": "t"}}), _DEVICES])
    assert [d.sku for d in await a.get_devices()] == ["H6008"]
    assert urls[0] == acc.LOGIN_URL and a.token == "t"


async def test_token_only_401_raises_auth_error_without_login() -> None:
    a = GoveeCloudAccount("", "", token="saved")       # token-only: nothing to log in with
    urls = _stub_post(a, [(401, {})])
    with pytest.raises(GoveeCloudAuthError, match="HTTP 401"):
        await a.get_devices()
    assert acc.LOGIN_URL not in urls and len(urls) == 1


async def test_relogin_rejected_raises_auth_error() -> None:
    a = _acct(token="stale")
    _stub_post(a, [(401, {}), (403, {})])
    with pytest.raises(GoveeCloudAuthError, match=r"login failed \(HTTP 403\)"):
        await a.get_devices()


# -- GoveeCloudAuthError ---------------------------------------------------------------------

@pytest.mark.parametrize(
    ("status", "body"),
    [(401, None), (403, {"message": "do-not-leak"}), (200, {"status": 401, "message": "do-not-leak"})],
)
async def test_login_rejection_is_auth_error(status: int, body: Any) -> None:
    a = _acct()
    _stub_post(a, [(status, body)])
    with pytest.raises(GoveeCloudAuthError, match="credentials rejected") as ei:
        await a.login()
    assert "do-not-leak" not in str(ei.value) and a.token is None


@pytest.mark.parametrize(
    ("status", "body"),
    [(400, {}), (500, None), (200, {"status": 400}), (200, {"status": True}), (200, {"data": {}})],
)
async def test_other_login_failures_are_not_auth_errors(status: int, body: Any) -> None:
    a = _acct()
    _stub_post(a, [(status, body)])
    with pytest.raises(GoveeCloudError) as ei:
        await a.login()
    assert not isinstance(ei.value, GoveeCloudAuthError)


def test_auth_error_is_a_cloud_error_exported_everywhere() -> None:
    assert issubclass(GoveeCloudAuthError, GoveeCloudError)
    assert cloud.GoveeCloudAuthError is pkg.GoveeCloudAuthError is GoveeCloudAuthError
    assert acc.GoveeCloudError is pkg.GoveeCloudError is GoveeCloudError
    assert {"GoveeCloudAuthError", "GoveeCloudError"} <= set(pkg.__all__)


# -- parsing hardening -----------------------------------------------------------------------

@pytest.mark.parametrize("bad", [1e400, float("nan"), 7.9])
def test_parse_non_integral_numbers_are_dropped_not_crashing(bad: float) -> None:
    dev = GoveeCloudAccount._parse({"sku": "H1", "device": "AA", "pactType": bad, "pact_type": 4,
                                    "pactCode": bad, "goodsType": 7.0})
    assert dev.pact_type == 4 and dev.pact_code is None and dev.goods_type == 7


@pytest.mark.parametrize("pid", [1e400, float("-inf"), 7.9])
async def test_effect_strs_bad_float_id_is_cloud_error(pid: float) -> None:
    a = _acct(token="t")
    _stub_post(a, [(200, {"effectStrs": [{"scenceParamId": pid, "effectStr": "AAAA"}]})])
    with pytest.raises(GoveeCloudError, match="malformed scene id"):
        await a.get_scene_effect_strs([1])


async def test_effect_strs_integral_float_id_accepted() -> None:
    a = _acct(token="t")
    _stub_post(a, [(200, {"effectStrs": [{"scenceParamId": 7.0, "effectStr": "AAAA"}]})])
    assert await a.get_scene_effect_strs([7]) == {7: "AAAA"}


def test_parse_null_fields_are_empty_not_none_text() -> None:
    dev = GoveeCloudAccount._parse({"sku": None, "device": None, "deviceName": None})
    assert (dev.sku, dev.device, dev.name, dev.ble_mac) == ("", "", "", None)


def test_parse_deeply_nested_settings_json_is_ignored() -> None:
    deep = "[" * 200_000 + "]" * 200_000
    dev = GoveeCloudAccount._parse({"sku": "H1", "device": "AA", "deviceExt": {"deviceSettings": deep}})
    assert dev.secret is None and dev.sku == "H1"


class _DeepResp(_Resp):
    """A response body nested too deeply for the decoder: aiohttp's ``resp.json`` decodes
    with ``json.loads``, which raises RecursionError (a RuntimeError, not a ValueError)."""

    async def json(self, content_type: Any = None) -> Any:
        return json.loads('{"data": ' + "[" * 200_000 + "]" * 200_000 + "}")


async def test_deeply_nested_response_body_is_cloud_error() -> None:
    a = _acct(token="t", session=_Session(_DeepResp(200)))  # type: ignore[arg-type]
    with pytest.raises(GoveeCloudError, match="not valid JSON") as ei:
        await a.get_devices()
    assert isinstance(ei.value.__cause__, RecursionError)


async def test_deeply_nested_error_body_keeps_the_http_status() -> None:
    a = _acct(session=_Session(_DeepResp(500)))  # type: ignore[arg-type]
    with pytest.raises(GoveeCloudError, match=r"login failed \(HTTP 500\)"):
        await a.login()


def test_parse_non_ascii_secret_is_ignored() -> None:
    dev = GoveeCloudAccount._parse({"sku": "H1", "device": "AA", "secretCode": "ASNFZ4mrze8é"})
    assert dev.secret is None


def test_secret_len_is_the_wire_constant() -> None:
    assert acc.SECRET_LEN is build.SECRET_LEN
    settings = json.dumps({"secretCode": "ASNFZ4mrze8="})
    dev = GoveeCloudAccount._parse({"sku": "H1", "device": "AA", "deviceExt": {"deviceSettings": settings}})
    assert dev.secret is not None and len(dev.secret) == build.SECRET_LEN
