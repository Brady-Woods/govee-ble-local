"""Extra coverage for govee_ble_local.cloud.account (aiohttp mocked, no network)."""
from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("aiohttp")

from govee_ble_local.cloud import account as acc
from govee_ble_local.cloud.account import GoveeCloudAccount, GoveeCloudError


def _acct(**kw: Any) -> GoveeCloudAccount:
    return GoveeCloudAccount("a@b.c", "pw", **kw)


def _stub_post(acct: GoveeCloudAccount, responses: list[tuple[int, Any]]) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []

    async def fake_post(url: str, body: dict, *, auth: bool):  # type: ignore[no-untyped-def]
        calls.append((url, body))
        return responses.pop(0)

    acct._post = fake_post  # type: ignore[method-assign]
    return calls


async def test_get_scene_effect_strs_top_level_and_nested() -> None:
    a = _acct(token="t")
    calls = _stub_post(a, [
        (200, {"effectStrs": [
            {"scenceParamId": 1, "effectStr": "AAA="},
            {"scenceParamId": "2", "effectStr": "BBB="},
            {"scenceParamId": 3, "effectStr": ""},     # dropped: empty
            {"scenceParamId": None, "effectStr": "x"}, # dropped: no id
        ]}),
        (200, {"data": {"effectStrs": [{"scenceParamId": 9, "effectStr": "Z"}]}}),
        (200, {}),
    ])
    assert await a.get_scene_effect_strs([1, 2, 3]) == {1: "AAA=", 2: "BBB="}
    assert calls[0] == (acc.BASE_URL + acc.EFFECT_STRS_PATH, {"scenceParamIds": [1, 2, 3]})
    assert await a.get_scene_effect_strs([9]) == {9: "Z"}
    assert await a.get_scene_effect_strs([]) == {}


async def test_get_scene_effect_strs_non_200_raises() -> None:
    a = _acct(token="t")
    _stub_post(a, [(500, {})])
    with pytest.raises(GoveeCloudError, match="HTTP 500"):
        await a.get_scene_effect_strs([1])


async def test_aenter_logs_in_without_token_and_aexit_closes() -> None:
    a = _acct()
    _stub_post(a, [(200, {"data": {"token": "tok"}})])
    closed: list[bool] = []

    async def fake_close() -> None:
        closed.append(True)

    a.close = fake_close  # type: ignore[method-assign]
    async with a as ctx:
        assert ctx is a and a.token == "tok"
    assert closed == [True]


async def test_aenter_skips_login_when_token_present() -> None:
    a = _acct(token="have")
    calls = _stub_post(a, [])
    async with a:
        pass
    assert calls == []


class _FakeSession:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def test_close_only_closes_owned_session() -> None:
    injected = _FakeSession()
    a = _acct(session=injected)  # type: ignore[arg-type]
    await a.close()
    assert injected.closed is False and a._session is injected

    owned = _acct()
    sess = _FakeSession()
    owned._session = sess  # type: ignore[assignment]
    await owned.close()
    assert sess.closed is True and owned._session is None
    await owned.close()  # idempotent with no session


async def test_ensure_session_creates_once_and_reuses(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict] = []

    class FakeClientSession:
        def __init__(self, **kw: Any) -> None:
            created.append(kw)

    monkeypatch.setattr(acc.aiohttp, "ClientSession", FakeClientSession)
    a = _acct()
    s1 = await a._ensure_session()
    s2 = await a._ensure_session()
    assert s1 is s2 and len(created) == 1
    assert created[0]["timeout"].total == acc.REQUEST_TIMEOUT

    injected = _FakeSession()
    b = _acct(session=injected)  # type: ignore[arg-type]
    assert await b._ensure_session() is injected


class _Resp:
    def __init__(self, status: int, payload: Any = None, exc: Exception | None = None) -> None:
        self.status, self._payload, self._exc = status, payload, exc

    async def __aenter__(self) -> _Resp:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def json(self, content_type: Any = None) -> Any:
        if self._exc:
            raise self._exc
        return self._payload


class _PostSession:
    def __init__(self, resp: _Resp) -> None:
        self.resp = resp
        self.seen: dict[str, Any] = {}

    def post(self, url: str, **kw: Any) -> _Resp:
        self.seen = {"url": url, **kw}
        return self.resp


async def test_post_returns_status_and_json_and_wraps_errors() -> None:
    sess = _PostSession(_Resp(200, {"ok": 1}))
    a = _acct(token="t", session=sess)  # type: ignore[arg-type]
    assert await a._post("http://x", {"b": 1}, auth=True) == (200, {"ok": 1})
    assert sess.seen["json"] == {"b": 1}
    assert sess.seen["headers"]["Authorization"] == "Bearer t"

    bad = _acct(session=_PostSession(_Resp(200, exc=ValueError("not json"))))  # type: ignore[arg-type]
    with pytest.raises(GoveeCloudError, match="request to http://x failed"):
        await bad._post("http://x", {}, auth=False)

    timeout = _acct(session=_PostSession(_Resp(200, exc=TimeoutError())))  # type: ignore[arg-type]
    with pytest.raises(GoveeCloudError):
        await timeout._post("http://x", {}, auth=False)


async def test_login_error_paths() -> None:
    a = _acct()
    _stub_post(a, [(403, {"secret": "do-not-leak"})])
    with pytest.raises(GoveeCloudError, match=r"login failed \(HTTP 403\)") as ei:
        await a.login()
    assert "do-not-leak" not in str(ei.value)

    b = _acct()
    _stub_post(b, [(200, ["not", "a", "dict"])])
    with pytest.raises(GoveeCloudError, match="login failed"):
        await b.login()

    c = _acct()
    _stub_post(c, [(200, {"data": {}})])
    with pytest.raises(GoveeCloudError, match="no token"):
        await c.login()

    d = _acct()
    _stub_post(d, [(200, {"client": {"token": "alt"}})])
    await d.login()
    assert d.token == "alt"


_BAD_SHAPES: list[tuple[str, Any, Any]] = [
    # (method, body, arg)
    ("get_devices", ["list"], None),
    ("get_devices", None, None),
    ("get_devices", "str", None),
    ("get_devices", {"devices": ["not-a-dict"]}, None),
    ("get_devices", {"devices": "nope"}, None),
    ("get_devices", {"data": "nope"}, None),
    ("get_devices", {"data": {"devices": {"a": 1}}}, None),
    ("get_scene_effect_strs", None, [1]),
    ("get_scene_effect_strs", ["x"], [1]),
    ("get_scene_effect_strs", {"effectStrs": [None]}, [1]),
    ("get_scene_effect_strs", {"effectStrs": "nope"}, [1]),
    ("get_scene_effect_strs", {"data": 5}, [1]),
    ("get_scene_effect_strs", {"effectStrs": [{"scenceParamId": "abc", "effectStr": "x"}]}, [1]),
    ("get_scene_effect_strs", {"effectStrs": [{"scenceParamId": [1], "effectStr": "x"}]}, [1]),
    ("get_scene_effect_strs", {"effectStrs": [{"scenceParamId": 1, "effectStr": ["x"]}]}, [1]),
]


@pytest.mark.parametrize(("method", "body", "arg"), _BAD_SHAPES)
async def test_malformed_json_bodies_raise_cloud_error(method: str, body: Any, arg: Any) -> None:
    # WAS (SEC-5): AttributeError/TypeError/ValueError escaped.
    # FIXED: every wrong-shaped 200 body raises GoveeCloudError, never echoing the body.
    a = _acct(token="t")
    _stub_post(a, [(200, body)])
    with pytest.raises(GoveeCloudError) as ei:
        await (getattr(a, method)() if arg is None else getattr(a, method)(arg))
    for leak in ("not-a-dict", "nope", "abc"):
        assert leak not in str(ei.value)


async def test_login_wrong_nested_shape_raises_cloud_error() -> None:
    for body in ({"data": "str"}, {"data": ["x"]}, {"client": 5}):
        a = _acct()
        _stub_post(a, [(200, body)])
        with pytest.raises(GoveeCloudError):
            await a.login()


async def test_post_non_json_body_raises_cloud_error_without_leak() -> None:
    import json

    exc = json.JSONDecodeError("Expecting value: SECRET-BODY", "SECRET-BODY", 0)
    bad = _acct(session=_PostSession(_Resp(200, exc=exc)))  # type: ignore[arg-type]
    with pytest.raises(GoveeCloudError) as ei:
        await bad._post("http://x", {}, auth=False)
    assert "SECRET-BODY" not in str(ei.value)


def test_parse_malformed_secret_raises() -> None:
    import base64
    import json

    # WAS: bad base64 silently became None / wrong-length bytes passed through.
    for bad in ("!!!notbase64", base64.b64encode(b"short").decode(), base64.b64encode(b"x" * 9).decode()):
        with pytest.raises(GoveeCloudError, match="device secret for H6008 is malformed") as ei:
            GoveeCloudAccount._parse({"sku": "h6008", "device": "AA", "secretCode": bad})
        assert bad not in str(ei.value)
    settings = json.dumps({"address": "98:17:3C:B1:A2:D1", "secretCode": "AAAA"})
    with pytest.raises(GoveeCloudError, match="98:17:3C:B1:A2:D1"):
        GoveeCloudAccount._parse({"device": "AA", "deviceExt": {"deviceSettings": settings}})
    ok = GoveeCloudAccount._parse({"sku": "H1", "device": "AA", "secretCode": "YVUhCQtzXFQ="})
    assert ok.secret == bytes.fromhex("615521090b735c54")
    assert GoveeCloudAccount._parse({"sku": "H1", "device": "AA"}).secret is None


def test_parse_non_dict_settings_json_is_ignored() -> None:
    dev = GoveeCloudAccount._parse({"sku": "H1", "device": "AA", "deviceExt": {"deviceSettings": "[1]"}})
    assert dev.secret is None


async def test_aenter_login_failure_closes_owned_session() -> None:
    a = _acct()
    sess = _FakeSession()
    a._session = sess  # type: ignore[assignment]
    _stub_post(a, [(403, {})])
    with pytest.raises(GoveeCloudError):
        async with a:
            pytest.fail("should not enter")
    assert sess.closed is True and a._session is None

    inj = _FakeSession()
    b = _acct(session=inj)  # type: ignore[arg-type]
    _stub_post(b, [(403, {})])
    with pytest.raises(GoveeCloudError):
        await b.__aenter__()
    assert inj.closed is False


async def test_app_version_keyword_used_in_headers() -> None:
    a = _acct(app_version="9.9.9")
    h = a._headers()
    assert h["appVersion"] == "9.9.9" and "GoveeHome/9.9.9" in h["User-Agent"]
    assert _acct()._headers()["appVersion"] == acc.APP_VERSION


def test_parse_edge_branches() -> None:
    dev = GoveeCloudAccount._parse({
        "sku": "h6008", "device": "AA", "pactType": "x", "pact_code": "5",
        "deviceExt": {"deviceSettings": "{not json", "deviceName": "Ext"},
    })
    assert dev.sku == "H6008" and dev.name == "Ext"
    assert dev.pact_type is None and dev.pact_code == 5
    assert dev.ble_mac is None
