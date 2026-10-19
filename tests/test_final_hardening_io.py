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


def test_fetch_validate_entry_rejects_bad_base64() -> None:
    fs = _tool("fetch_scenes")
    with pytest.raises(ValueError, match="base64"):
        fs.validate_entry("s", {"code": 1, "param": "not base64!!"})
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
