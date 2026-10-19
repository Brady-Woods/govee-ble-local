"""T11.3 final re-audit fixes: debug frames-log validation (N-1), decrypt_btsnoop secret
masking (N-2), framelog O_NOFOLLOW/regular-file + writer revival (N-3/N-8), connection frame
validation (N-12), fetch_scenes atomic/validated writes (N-6)."""
from __future__ import annotations

import base64
import importlib.util
import inspect
import io
import json
import logging
import os
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from govee_ble_local import debug
from govee_ble_local.exceptions import GoveeBleInvalidValue
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.transport import framelog
from govee_ble_local.transport.connection import GoveeConnection
from govee_ble_local.wire import build

from fake_ble import FakeGovee, ble_device
from fake_ble import frame as valid_frame

TOOLS = Path(__file__).resolve().parent.parent / "tools"
posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX semantics")


def _tool(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_tool_{name}_final", TOOLS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Registered before exec (the importlib recipe): a @dataclass in a tool resolves its
    # string annotations through sys.modules[cls.__module__].
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _drain(timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while framelog._QUEUE.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.005)
    assert framelog._QUEUE.unfinished_tasks == 0


@pytest.fixture(autouse=True)
def _reset_framelog() -> None:
    framelog._failed_paths.clear()
    framelog._mode_warned.clear()


# -- N-1: debug --from-frames-log validation ----------------------------------------------

def test_frames_log_bad_hex_is_skipped_and_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = valid_frame(0x33, 0x01, 0x01).hex()  # a valid write/switch frame (checksum included)
    log = tmp_path / "f.log"
    log.write_text(
        f"AA tx cmd plain=abc wire=00 enc=none\n"
        f"AA tx cmd plain={good} wire={good} enc=none\n"
    )
    rc = debug.main(["--from-frames-log", str(log)])
    cap = capsys.readouterr()
    assert rc == 0  # a skipped malformed line is reported, never counted as a spec issue
    assert f"{log}:1:" in cap.err and "plain" in cap.err
    assert "1 malformed input line(s) skipped" in cap.out
    assert "1 plaintext frames" in cap.out


def test_frames_log_out_file_excludes_bad_records(tmp_path: Path) -> None:
    log = tmp_path / "f.log"
    log.write_text("AA rx x plain=abc wire=00 enc=none\n")
    out = tmp_path / "o.jsonl"
    debug.main(["--from-frames-log", str(log), "-o", str(out)])
    assert out.read_text() == ""


# -- N-2: decrypt_btsnoop masks secrets ---------------------------------------------------

def test_decrypt_btsnoop_masks_secret_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from govee_ble_local.crypto import checksum

    mod = _tool("decrypt_btsnoop")
    secret = bytes([0x33, 0xB2]) + bytes(range(1, 18))
    secret += bytes([checksum(secret)])
    reply = bytes([0xAA, 0xB1]) + bytes(range(1, 18))
    reply += bytes([checksum(reply)])
    normal = bytes([0x33, 0x01, 0x01]) + b"\x00" * 16
    normal += bytes([checksum(normal)])
    pdus = [(1, "AA", 0, b"\x52\x00\x00" + f) for f in (secret, reply, normal)]
    monkeypatch.setattr(mod, "_att_pdus", lambda path: iter(pdus))
    mod.main("x", set(), True, 100, False)
    out = capsys.readouterr().out
    assert secret.hex() not in out and reply.hex() not in out
    assert normal.hex() in out
    assert "33b2" in out and "aab1" in out  # headers kept
    doc = mod.__doc__ or ""
    assert "never keys/secrets" in doc and "masked" in doc


# -- N-3 / N-8: framelog ------------------------------------------------------------------

@posix_only
def test_symlink_target_refused_with_one_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep\n")
    link = tmp_path / "log.jsonl"
    link.symlink_to(victim)
    with caplog.at_level(logging.DEBUG, logger="govee_ble_local"):
        log = framelog.FrameLog(str(link), "A")
        log.record("tx", wire=b"\x01")
        _drain()
        log.record("tx", wire=b"\x02")
        _drain()
    assert victim.read_text() == "keep\n"
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1


@posix_only
def test_non_regular_file_refused(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        with caplog.at_level(logging.WARNING, logger="govee_ble_local"):
            framelog.FrameLog(str(fifo), "A").record("tx", wire=b"\x01")
            _drain()
        assert os.read(reader, 100) == b""
        assert [r for r in caplog.records if "not a regular file" in r.getMessage()]
    finally:
        os.close(reader)


@posix_only
def test_new_file_still_0600(tmp_path: Path) -> None:
    p = tmp_path / "n.jsonl"
    framelog.FrameLog(str(p), "A").record("tx", wire=b"\x01")
    _drain()
    assert (p.stat().st_mode & 0o777) == 0o600


def test_record_revives_dead_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The restart is stubbed (FakeThread): a second real writer thread would outlive this test and
    # consume the shared queue beside the first, breaking the check-before-lines order that
    # framelog._process_batch relies on in later tests. A real restart is covered in a child process
    # by test_framelog_hardening.py::test_record_revives_dead_writer_end_to_end.
    p = tmp_path / "r.jsonl"
    log = framelog.FrameLog(str(p), "A")   # the real (live) writer drains the queue below
    started: list[object] = []

    class Dead:
        def is_alive(self) -> bool:
            return False

    class FakeThread:
        def __init__(self, *, target: object, name: str, daemon: bool) -> None:
            self.target, self._alive = target, False

        def start(self) -> None:
            self._alive = True
            started.append(self.target)

        def is_alive(self) -> bool:
            return self._alive

    monkeypatch.setattr(framelog, "_writer", Dead())
    monkeypatch.setattr(framelog, "threading", SimpleNamespace(Thread=FakeThread))
    log.record("tx", wire=b"\x01")
    assert started == [framelog._write_forever]          # a dead writer was replaced, once
    assert isinstance(framelog._writer, FakeThread) and framelog._writer.is_alive()
    _drain()
    assert len(p.read_text().splitlines()) == 1


# -- N-12: connection frame validation ----------------------------------------------------

@pytest.mark.parametrize("frame", [b"", b"\x33"])
@pytest.mark.parametrize("expect_ack", [True, False])
async def test_send_short_frame_raises_invalid_value_before_io(
    monkeypatch: pytest.MonkeyPatch, frame: bytes, expect_ack: bool
) -> None:
    fake = FakeGovee()
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    c = GoveeConnection(ble_device("AA:BB:CC:DD:EE:FF", "GVH60A6X"))
    with pytest.raises(GoveeBleInvalidValue):
        await c.send(frame, expect_ack=expect_ack)
    assert c._client is None  # no connect attempted


async def test_send_many_short_frame_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGovee()
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    c = GoveeConnection(ble_device("AA:BB:CC:DD:EE:FF", "GVH60A6X"))
    with pytest.raises(GoveeBleInvalidValue):
        await c.send_many([b"\x33"])
    assert c._client is None


def test_query_defaults_use_named_constants() -> None:
    sig = inspect.signature(GoveeConnection.query)
    assert sig.parameters["opcode"].default == build.PRO_MULTI_AC
    assert sig.parameters["terminal"].default == conn_mod.QUERY_TERMINAL == 0xFF


# -- N-6 / N5: fetch_scenes ---------------------------------------------------------------

class _Resp(io.BytesIO):
    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def test_fetch_caps_response_size(monkeypatch: pytest.MonkeyPatch) -> None:
    fs = _tool("fetch_scenes")
    monkeypatch.setattr(fs, "MAX_RESPONSE_BYTES", 100)
    monkeypatch.setattr(fs.urllib.request, "urlopen", lambda *a, **k: _Resp(b"x" * 500))
    with pytest.raises(ValueError, match="exceeds"):
        fs.fetch("H60A6")


def test_fetch_accepts_normal_response(monkeypatch: pytest.MonkeyPatch) -> None:
    fs = _tool("fetch_scenes")
    body = json.dumps({"data": {"categories": []}}).encode()
    monkeypatch.setattr(fs.urllib.request, "urlopen", lambda *a, **k: _Resp(body))
    assert fs.fetch("H60A6") == {}


def test_fetch_skips_what_the_apps_base64_decoder_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    fs = _tool("fetch_scenes")
    scene = {"sceneName": "s", "lightEffects": [{"sceneCode": 7, "scenceParam": "AA\\/\\/"}]}
    body = json.dumps({"data": {"categories": [{"categoryName": "c", "scenes": [scene]}]}}).encode()
    monkeypatch.setattr(fs.urllib.request, "urlopen", lambda *a, **k: _Resp(body))
    assert fs.fetch("H6087")["s"]["param"] == "AA//"   # H6087's escaped "\/", read as "/"


def test_fetch_flags_a_placeholder_by_the_apps_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A placeholder is a big-effect scene with an empty param (BaseSceneViewMode / SceneDataVM fetch it),
    not a param whose byte 3 is 0xFF: H1401's Christmas, a well-formed 0x93 RGB scene."""
    fs = _tool("fetch_scenes")
    christmas = base64.b64encode(bytes([0x93, 0x0A, 0xFF, 0xFF, 0xFF, 0x32])).decode()
    scenes = [{"sceneName": "Christmas", "lightEffects": [{"sceneCode": 1, "scenceParam": christmas}]},
              {"sceneName": "Big", "lightEffects": [{"sceneCode": 2, "scenceParam": "", "bigEffectStr": 1}]},
              {"sceneName": "Bare", "lightEffects": [{"sceneCode": 3, "scenceParam": ""}]}]
    body = json.dumps({"data": {"categories": [{"categoryName": "c", "scenes": scenes}]}}).encode()
    monkeypatch.setattr(fs.urllib.request, "urlopen", lambda *a, **k: _Resp(body))
    got = fs.fetch("H1401")
    assert [n for n, e in got.items() if e.get("placeholder")] == ["Big"]


def test_fetch_takes_the_skus_special_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    """CategoryV1.LightEffect: a specialEffect entry whose supportSku holds the SKU overrides the base
    code, type, param, param id, cmdVersion and bigEffectStr, and its rules gate the scene (left out). Another
    SKU's entry is ignored, and an entry without a code or a param types the scene static when the base has a
    param id."""
    fs = _tool("fetch_scenes")
    base = {"sceneCode": 10, "sceneType": 1, "scenceParam": "AAAA", "scenceParamId": 5, "cmdVersion": 0}
    rules = [{"hardVersion": "1.00.00", "softVersion": "1.00.01"}]
    own = {"supportSku": ["H6641"], "sceneCode": 11, "sceneType": 2, "scenceParam": "BBBB",
           "scenceParamId": 6, "cmdVersion": 1, "bigEffectStr": 0, "rules": rules}
    other = {**own, "supportSku": ["H61A8"], "sceneCode": 12, "scenceParam": "CCCC"}
    bare = {"supportSku": ["H6641"], "sceneCode": 0, "sceneType": 1, "scenceParam": "", "scenceParamId": 0}
    scenes = [{"sceneName": "Own", "lightEffects": [{**base, "specialEffect": [other, own]}]},
              {"sceneName": "Bare", "lightEffects": [{**base, "specialEffect": [bare]}]}]
    body = json.dumps({"data": {"categories": [{"categoryName": "c", "scenes": scenes}]}}).encode()
    monkeypatch.setattr(fs.urllib.request, "urlopen", lambda *a, **k: _Resp(body))
    got = fs.fetch("H6641")
    assert fs.for_sku("H6641", scenes[0]["lightEffects"][0]) == {
        "code": 11, "type": 2, "param": "BBBB", "param_id": 6, "cmd_version": 1, "big_effect": False,
        "rules": rules}
    assert "Own" not in got   # its H6641 effect has version rules
    assert got["Bare"] == {"code": 10, "param": "AAAA", "category": "c", "param_id": 5, "scene_type": 0,
                           "cmd_version": 0}
    assert fs.fetch("H6006")["Own"] == {"code": 10, "param": "AAAA", "category": "c", "param_id": 5,
                                        "scene_type": 1, "cmd_version": 0}


def test_fetch_lists_what_the_apps_scene_list_lists(monkeypatch: pytest.MonkeyPatch,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    """devices.yaml scenes.list_filter: each lightEffect whose op type for the SKU is outside the SKU's op set
    (a base sceneType 0 passes any), a DIY one without effect codes, an expired one and an expired category
    are dropped before the first left plays; a scene whose first effect left has version rules is left out
    (the firmware picks), and one with none left is not listed. An empty op set (a null bean set) filters
    nothing but the time; where one of the SKU's sets is empty (H61A8's), a scene whose first effect in force
    is not the one the union keeps (a DIY one without codes, which the union drops) is named."""
    fs = _tool("fetch_scenes")
    rgbic1 = {"sceneCode": 1, "sceneType": 2, "cmdVersion": 1, "scenceParam": "AAAA"}   # op type 3
    rgbic0 = {**rgbic1, "sceneCode": 2, "cmdVersion": 0}                                # op type 2
    static = {"sceneCode": 3, "sceneType": 0, "scenceParam": ""}
    diy = {"sceneCode": 4, "sceneType": 5, "scenceParam": "AAAA"}
    gated = {**rgbic0, "sceneCode": 5, "rules": [{"hardVersion": "1.00.01", "softVersion": "1.05.00"}]}
    gone = {"checkEffectiveTime": 1, "effectiveTimes": [{"startTime": 0, "endTime": 1}]}
    scenes = {"Op": [rgbic1, rgbic0], "Static": [static], "NoCodes": [diy],
              "Codes": [{**diy, "diyEffectCode": [797]}], "Gated": [gated, rgbic0],
              "AfterGate": [rgbic0, gated], "Expired": [{**rgbic0, **gone}, rgbic1],
              "DiyFirst": [diy, rgbic0]}
    season = {"categoryName": "old", **gone, "scenes": [{"sceneName": "Season", "lightEffects": [static]}]}
    body = json.dumps({"data": {"categories": [
        {"categoryName": "c", "scenes": [{"sceneName": n, "lightEffects": e} for n, e in scenes.items()]},
        season]}}).encode()
    monkeypatch.setattr(fs.urllib.request, "urlopen", lambda *a, **k: _Resp(body))
    assert (fs.op_set("H61A8"), fs.op_set("H6006"), fs.op_set("H9999")) == ({0, 1, 2, 3}, {0, 1}, None)
    seen: set[tuple[str, object, int]] = set()
    listed = {"Op": 1, "Static": 3, "AfterGate": 2, "Expired": 1, "DiyFirst": 2}
    assert {n: e["code"] for n, e in fs.fetch("H6047", seen=seen).items()} == listed
    assert seen == {(n, "c", e["sceneCode"]) for n, es in scenes.items() for e in es} | {("Season", "old", 3)}
    out = capsys.readouterr().out
    assert "H6047: left out 1 scenes whose effect the firmware's version rules pick: Gated" in out
    assert "empty scene set" not in out
    assert {n: e["code"] for n, e in fs.fetch("H61A8").items()} == listed
    assert ("H61A8: at its empty scene set (no op set) the app plays another lightEffect for 1 scenes: "
            "DiyFirst") in capsys.readouterr().out
    assert set(fs.fetch("H6006")) == {"Static"}
    assert {n: e["code"] for n, e in fs.fetch("H60A6").items()} == {
        "Op": 1, "Static": 3, "Codes": 4, "AfterGate": 2, "Expired": 1, "DiyFirst": 2}
    assert {n: e["code"] for n, e in fs.fetch("H9999").items()} == {
        "Op": 1, "Static": 3, "NoCodes": 4, "Codes": 4, "Gated": 5, "AfterGate": 2, "Expired": 1,
        "DiyFirst": 4}


_EXPIRED = {"checkEffectiveTime": 1, "effectiveTimes": [{"startTime": 0, "endTime": 1}]}
_Library = tuple[tuple[Any, ...], ...]   # (category name, ((sceneName, code), ...)[, category fields])
# The app lists "Flash" in two categories (H61A8: Emotion's 2020, Zootopia 2's 14769), here twice in one.
_EMOTION = ("Emotion", (("Flash", 2020), ("Glow", 30)))
_ZOOTOPIA = ("Zootopia 2", (("Flash", 14769), ("Flash", 14770)))
_STORED = {"Flash": (14770, "Zootopia 2"), "Flash (Emotion)": (2020, "Emotion"),
           "Flash (Zootopia 2)": (14769, "Zootopia 2"), "Glow": (30, "Emotion")}
_ROUNDS: dict[str, tuple[_Library, dict[str, tuple[int, str]], tuple[str, ...]]] = {
    # change: (the library re-fetched, the stored names after it, the names printed)
    "re-fetch": ((_EMOTION, _ZOOTOPIA), _STORED, ()),
    "reorder": ((("Zootopia 2", _ZOOTOPIA[1][::-1]), ("Emotion", _EMOTION[1][::-1])), _STORED, ()),
    "add": ((_EMOTION, ("Zootopia 2", (*_ZOOTOPIA[1], ("Flash", 15000))), ("Party", (("Flash", 99),))),
            {**_STORED, "Flash (Zootopia 2, 2)": (15000, "Zootopia 2"), "Flash (Party)": (99, "Party")},
            ("Flash (Zootopia 2, 2)", "Flash (Party)")),
    "renumber": ((_EMOTION, ("Zootopia 2", (("Flash", 14769), ("Flash", 14771)))),
                 {**_STORED, "Flash": (14771, "Zootopia 2")}, ("Flash",)),
    "category rename": ((_EMOTION, ("Zootopia", _ZOOTOPIA[1])),
                        {**_STORED, "Flash": (14770, "Zootopia"), "Flash (Zootopia 2)": (14769, "Zootopia")},
                        ("Flash", "Flash (Zootopia 2)")),
    "category removed": ((_EMOTION,), _STORED, ()),   # dropped out of the library: kept
    "category expired": ((_EMOTION, (*_ZOOTOPIA, _EXPIRED)),   # answered, not listed: removed
                         {n: v for n, v in _STORED.items() if v[1] != "Zootopia 2"},
                         ("Flash", "Flash (Zootopia 2)")),
    "holder removed": ((_EMOTION, ("Zootopia 2", (("Flash", 14769),))), _STORED, ()),
    "literal": ((_EMOTION, _ZOOTOPIA, ("Specials", (("Flash (Emotion)", 500),))),   # a sceneName
                {**_STORED, "Flash (Emotion) (Specials)": (500, "Specials")},
                ("Flash (Emotion) (Specials)",)),
}


def _library(categories: _Library) -> bytes:
    def category(name: str, scenes: tuple[tuple[str, int], ...], *more: dict[str, Any]) -> dict[str, Any]:
        return {"categoryName": name, **{k: v for m in more for k, v in m.items()},
                "scenes": [{"sceneName": n, "lightEffects": [{"sceneCode": c, "scenceParam": ""}]}
                           for n, c in scenes]}
    return json.dumps({"data": {"categories": [category(*c) for c in categories]}}).encode()


@pytest.mark.parametrize("change", list(_ROUNDS))
def test_fetch_never_points_a_stored_name_at_another_scene(change: str, monkeypatch: pytest.MonkeyPatch,
                                                           tmp_path: Path,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    """A stored name is an effect name automations keep. A first fetch names the scenes the app lists under
    one name whatever their order: the highest code keeps it, each other is "<name> (<category>)" (", n"
    where that is taken), and each records its sceneName. A re-fetch matches each listed scene to a stored
    one of its sceneName (by code and category, else code, else category); only a new one gets a new name,
    never a stored one, and a stored scene no longer listed keeps its name unless the library still answers
    it. Over each change to the library, every stored name keeps its code and category or is printed."""
    fs = _tool("fetch_scenes")
    monkeypatch.setattr(fs, "OUT_DIR", tmp_path)
    library, after, printed = _ROUNDS[change]

    def fetch_from(categories: _Library) -> dict[str, tuple[object, object]]:
        body = _library(categories)
        monkeypatch.setattr(fs.urllib.request, "urlopen", lambda *a, **k: _Resp(body))
        assert fs.main(["H61A8"]) == 0
        return {n: (e["code"], e["category"]) for n, e in fs.read_catalog("H61A8", tmp_path).items()}

    assert fetch_from(_ROUNDS["reorder"][0]) == _STORED   # a first fetch: the list order does not count
    first = fs.read_catalog("H61A8", tmp_path)
    assert {n: e.get("scene_name") for n, e in first.items()} == {
        "Flash": None, "Flash (Emotion)": "Flash", "Flash (Zootopia 2)": "Flash", "Glow": None}
    capsys.readouterr()
    assert fetch_from(library) == after
    out = capsys.readouterr().out
    changes = "\n".join(line for line in out.splitlines() if "stored names" in line)
    for name, scene in _STORED.items():
        assert after.get(name) == scene or f'"{name}"' in changes, name
    assert all(f'"{name}"' in out for name in printed)
    assert bool(changes) == any(n in _STORED for n in printed)


def test_fetch_names_the_scenes_left_after_the_type5_ones(monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    """A sceneType-5 scene a legacy_scene SKU leaves out (H6006: its param leads 0x50) is left out before
    the scenes are named, so it takes no name from another scene of its sceneName."""
    fs = _tool("fetch_scenes")
    diy = {"supportSku": ["H6006"], "sceneCode": 9, "sceneType": 5,
           "scenceParam": base64.b64encode(b"\x50\x01\x02").decode()}
    body = json.dumps({"data": {"categories": [
        {"categoryName": "A", "scenes": [{"sceneName": "X", "lightEffects": [
            {"sceneCode": 8, "sceneType": 0, "scenceParam": "", "specialEffect": [diy]}]}]},
        {"categoryName": "B", "scenes": [{"sceneName": "X", "lightEffects": [
            {"sceneCode": 3, "sceneType": 1, "scenceParam": ""}]}]}]}}).encode()
    monkeypatch.setattr(fs.urllib.request, "urlopen", lambda *a, **k: _Resp(body))
    assert {n: e["code"] for n, e in fs.fetch("H6006").items()} == {"X": 3}
    assert "H6006: left out 1 sceneType-5 scenes the app uploads and the library does not" in (
        capsys.readouterr().out)


def test_fetch_validate_entry_rejects_bad_base64() -> None:
    fs = _tool("fetch_scenes")
    with pytest.raises(ValueError, match="base64"):
        fs.validate_entry("s", {"code": 1, "param": "not base64!!"})
    with pytest.raises(ValueError, match="version rules"):
        fs.validate_entry("s", {"code": 1, "param": None, "rules": True})
    fs.validate_entry("s", {"code": 1, "param": base64.b64encode(b"abc").decode()})


def test_write_catalog_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fs = _tool("fetch_scenes")
    path = tmp_path / "H60A6.json"
    path.write_text("old")
    real_replace = os.replace
    calls: list[tuple[str, str]] = []

    def spy(src: str, dst: str) -> None:
        calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(fs.os, "replace", spy)
    fs.write_catalog(path, {"a": {"code": 1}})
    assert calls and calls[0][1] == str(path)
    assert json.loads(path.read_text()) == {"a": {"code": 1}}
    assert [p.name for p in tmp_path.iterdir()] == ["H60A6.json"]  # temp file gone


def test_write_catalog_failure_leaves_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fs = _tool("fetch_scenes")
    path = tmp_path / "H60A6.json"
    path.write_text("old")

    def boom(*a: object) -> None:
        raise OSError("disk")

    monkeypatch.setattr(fs.os, "replace", boom)
    with pytest.raises(OSError):
        fs.write_catalog(path, {"a": {}})
    assert path.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["H60A6.json"]


def test_fetch_doc_reference_is_real() -> None:
    src = (TOOLS / "fetch_scenes.py").read_text()
    assert "SCENE_UPLOAD_REVIEW" not in src
    assert (TOOLS.parent / "spec" / "docs" / "SCENE_UPLOAD_ENCODING.md").exists()
    assert "SCENE_UPLOAD_ENCODING.md" in src


# -- N-9: CI flag semantics (see also test_optional_deps_gate) ----------------------------

@pytest.mark.parametrize("val", ["false", "0", "", "no", "False"])
def test_ci_falsy_values_do_not_require(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    from conftest import optional_deps_required

    monkeypatch.delenv("GOVEE_REQUIRE_OPTIONAL_TESTS", raising=False)
    monkeypatch.setenv("CI", val)
    assert optional_deps_required() is False
