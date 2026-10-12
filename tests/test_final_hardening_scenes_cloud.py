"""T11.2 — final re-audit fixes: scene param validation (N-4/N-5), cloud hardening (N-5/N-7/N-8)."""
from __future__ import annotations

import base64
import logging
from typing import Any

import pytest

from conftest import require_optional

from govee_ble_local import scenes
from govee_ble_local.const import FRAME_LEN
from govee_ble_local.exceptions import GoveeBleError, GoveeBleInvalidValue
from govee_ble_local.scenes import Scene, load_scenes, scene_upload_frames
from govee_ble_local.wire import build

_BAD_B64 = "!!not*base64!!"


def _scene(param: str, st: int = 1) -> Scene:
    return Scene("Sunset", 77, param, scene_type=st)


@pytest.mark.parametrize(
    ("dialect", "st"), [(None, 1), ("B_h60a6", 5), ("B_h6052", 5)]
)
def test_scene_upload_frames_bad_base64_is_ble_error(dialect: str | None, st: int) -> None:
    # WAS: bare binascii.Error (a ValueError, not GoveeBleError) escaped.
    with pytest.raises(GoveeBleInvalidValue, match="77") as ei:
        scene_upload_frames(_scene(_BAD_B64, st), dialect, frozenset({1, 2, 3, 6}))
    assert _BAD_B64 not in str(ei.value)
    assert isinstance(ei.value, GoveeBleError)


def test_scene_upload_frames_oversized_payload_is_ble_error() -> None:
    # WAS: bare ValueError('scene payload too large ...') from build.scene_upload_a3.
    big = base64.b64encode(bytes(20 * 300)).decode()
    with pytest.raises(GoveeBleInvalidValue, match="77") as ei:
        scene_upload_frames(_scene(big), None, frozenset({1}))
    assert big[:20] not in str(ei.value)


def test_build_scene_upload_a3_too_large_is_invalid_value() -> None:
    with pytest.raises(GoveeBleInvalidValue):
        build.scene_upload_a3(bytes(20 * 300), 1)


def test_catalog_with_bad_param_is_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Res:
        def __truediv__(self, _n: str) -> _Res:
            return self

        def read_text(self, encoding: str | None = None) -> str:
            return '{"a": {"code": 3, "param": "!!bad!!"}}'

    scenes._catalog.cache_clear()
    monkeypatch.setattr(scenes.resources, "files", lambda _pkg: _Res())
    try:
        with pytest.raises(GoveeBleError, match="scene catalog for H1234 is malformed") as ei:
            load_scenes("H1234")
        assert "!!bad!!" not in str(ei.value)
    finally:
        scenes._catalog.cache_clear()


def test_frame_len_literals_unchanged() -> None:
    assert len(build.frame(0x33, 1)) == FRAME_LEN
    from govee_ble_local.transport import handshake

    assert len(handshake._build_plain(1, b"x" * 16)) == FRAME_LEN


# ── cloud ────────────────────────────────────────────────────────────────────
require_optional("aiohttp")

from govee_ble_local.cloud.account import GoveeCloudAccount, GoveeCloudError  # noqa: E402


def _acct(**kw: Any) -> GoveeCloudAccount:
    return GoveeCloudAccount("a@b.c", "pw", **kw)


def _stub_post(a: GoveeCloudAccount, responses: list[tuple[int, Any]]) -> None:
    async def fake(url: str, body: dict, *, auth: bool):  # type: ignore[no-untyped-def]
        return responses.pop(0)

    a._post = fake  # type: ignore[method-assign]


@pytest.mark.parametrize("token", [123, ["t"], {"a": 1}, True])
async def test_login_non_string_token_rejected(token: Any) -> None:
    # WAS: any truthy token (int/list/dict) was stored and sent as "Bearer ...".
    a = _acct()
    _stub_post(a, [(200, {"data": {"token": token}})])
    with pytest.raises(GoveeCloudError, match="no token"):
        await a.login()
    assert a.token is None


async def test_effect_strs_bad_base64_rejected() -> None:
    a = _acct(token="t")
    _stub_post(a, [(200, {"effectStrs": [{"scenceParamId": 5, "effectStr": _BAD_B64}]})])
    with pytest.raises(GoveeCloudError, match="5") as ei:
        await a.get_scene_effect_strs([5])
    assert _BAD_B64 not in str(ei.value)


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


class _Sess:
    def __init__(self, *resps: _Resp) -> None:
        self.resps = list(resps)

    def post(self, url: str, **kw: Any) -> _Resp:
        return self.resps.pop(0)


async def test_post_checks_status_before_json_5xx() -> None:
    # WAS: 'response was not valid JSON' for a 502 HTML page; status never reported.
    a = _acct(token="t", session=_Sess(_Resp(502, exc=ValueError("<html>SECRET</html>"))))  # type: ignore[arg-type]
    with pytest.raises(GoveeCloudError, match="HTTP 502") as ei:
        await a._authed_post("/p", {})
    assert "SECRET" not in str(ei.value) and "JSON" not in str(ei.value)


async def test_post_401_html_body_still_relogs_in() -> None:
    # WAS: the non-JSON 401 body raised before the one-time re-login path.
    sess = _Sess(
        _Resp(401, exc=ValueError("<html>")),
        _Resp(200, {"data": {"token": "new"}}),   # login
        _Resp(200, {"ok": 1}),
    )
    a = _acct(token="old", session=sess)  # type: ignore[arg-type]
    assert await a._authed_post("/p", {}) == {"ok": 1}
    assert a.token == "new"


async def test_post_200_non_json_still_errors() -> None:
    a = _acct(token="t", session=_Sess(_Resp(200, exc=ValueError("SECRET"))))  # type: ignore[arg-type]
    with pytest.raises(GoveeCloudError, match="not valid JSON") as ei:
        await a._post("http://x", {}, auth=True)
    assert "SECRET" not in str(ei.value)


async def test_get_devices_bad_secret_yields_none_and_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # WAS (N-7): one bad secretCode made get_devices() raise for the whole account.
    good = {"sku": "H6008", "device": "AA", "secretCode": "YVUhCQtzXFQ="}
    bad = {"sku": "H5083", "device": "BB", "secretCode": "!!notbase64"}
    a = _acct(token="t")
    _stub_post(a, [(200, {"devices": [good, bad]})])
    with caplog.at_level(logging.WARNING, logger="govee_ble_local"):
        devs = await a.get_devices()
    assert [d.sku for d in devs] == ["H6008", "H5083"]
    assert devs[0].secret == bytes.fromhex("615521090b735c54") and devs[1].secret is None
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1
    assert "H5083" in warns[0].getMessage() and "notbase64" not in warns[0].getMessage()
