"""Smoke + pure-helper tests for the developer tools under tools/ (no BLE hardware)."""
from __future__ import annotations

import asyncio
import importlib.util
import py_compile
import re
import struct
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
ARGPARSE_TOOLS = [
    "fetch_scenes.py", "decrypt_btsnoop.py", "btsnoop_mtu_scan.py", "power_semantics_probe.py",
    "analyze_frame_log.py", "h60a6_live_check.py", "h60a6_cct_choreography.py",
    "h60a6_segment_map_probe.py",
]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_tool_{name}", TOOLS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("path", sorted(TOOLS.glob("*.py")), ids=lambda p: p.name)
def test_tool_compiles(path: Path, tmp_path: Path) -> None:
    py_compile.compile(str(path), cfile=str(tmp_path / "x.pyc"), doraise=True)


@pytest.mark.parametrize("name", ARGPARSE_TOOLS)
def test_tool_help_exits_zero(name: str) -> None:
    r = subprocess.run(  # noqa: S603 - fixed argv, sys.executable
        [sys.executable, str(TOOLS / name), "--help"], capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stderr
    assert "usage" in r.stdout.lower()


def test_no_real_macs_in_tools() -> None:
    mac = re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b")
    placeholder = {"AA:BB:CC:DD:EE:FF", "00:00:00:00:00"}
    for path in TOOLS.iterdir():
        if path.is_file():
            for m in mac.findall(path.read_text(errors="ignore")):
                assert m.upper() in placeholder, f"{path.name}: {m}"


# --- fetch_scenes -----------------------------------------------------------------


@pytest.mark.parametrize("sku", ["H60A6", "h6052", "ABC"])
def test_fetch_validate_sku_ok(sku: str) -> None:
    assert _load("fetch_scenes").validate_sku(sku) == sku.upper()


@pytest.mark.parametrize("sku", ["", "AB", "TOOLONGSKU", "../etc", "H60A6/../x", "H6 0", "H60A6\n"])
def test_fetch_validate_sku_rejects(sku: str) -> None:
    with pytest.raises(ValueError):
        _load("fetch_scenes").validate_sku(sku)


def test_fetch_catalog_path_inside_out_dir(tmp_path: Path) -> None:
    fs = _load("fetch_scenes")
    assert fs.catalog_path("h60a6", tmp_path) == tmp_path.resolve() / "H60A6.json"
    with pytest.raises(ValueError):
        fs.catalog_path("../../evil", tmp_path)


def test_fetch_validate_entry() -> None:
    fs = _load("fetch_scenes")
    fs.validate_entry("ok", {"code": 5, "param": None, "category": "c"})
    for bad in ({}, {"code": "5"}, {"code": True}, {"code": 1, "param": 3}, {"code": 1, "scene_type": "x"}):
        with pytest.raises(ValueError):
            fs.validate_entry("bad", bad)
    with pytest.raises(ValueError):
        fs.validate_entry(None, {"code": 1})


def test_fetch_main_exits_nonzero_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    fs = _load("fetch_scenes")
    monkeypatch.setattr(fs, "OUT_DIR", tmp_path)

    def boom(sku: str, app_version: str = "") -> dict:
        raise OSError("network down")

    monkeypatch.setattr(fs, "fetch", boom)
    assert fs.main(["H60A6", "H61A8"]) == 2
    assert "FETCH FAILED" in capsys.readouterr().err
    monkeypatch.setattr(
        fs, "fetch", lambda sku, app_version="": {"x": {"code": 1, "param": None, "category": None}}
    )
    assert fs.main(["H60A6"]) == 0
    assert (tmp_path / "H60A6.json").exists()


def test_fetch_app_version_flag_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    fs = _load("fetch_scenes")
    monkeypatch.delenv("GOVEE_APP_VERSION", raising=False)
    assert fs.parse_args(["--app-version", "9.9.9", "H60A6"]).app_version == "9.9.9"
    monkeypatch.setenv("GOVEE_APP_VERSION", "1.2.3")
    assert _load("fetch_scenes").parse_args([]).app_version == "1.2.3"


def test_fetch_parse_rejects_bad_sku() -> None:
    with pytest.raises(SystemExit) as exc:
        _load("fetch_scenes").parse_args(["../x"])
    assert exc.value.code == 2


# --- btsnoop tools ----------------------------------------------------------------


def _btsnoop(tmp_path: Path, incl: int, payload: bytes) -> Path:
    p = tmp_path / "cap.log"
    p.write_bytes(b"btsnoop\x00" + struct.pack(">II", 1, 1002)
                  + struct.pack(">IIIIq", incl, incl, 0, 0, 0) + payload)
    return p


@pytest.mark.parametrize("tool", ["decrypt_btsnoop", "btsnoop_mtu_scan"])
def test_btsnoop_rejects_oversized_record(tool: str, tmp_path: Path) -> None:
    mod = _load(tool)
    with pytest.raises(SystemExit, match="corrupt btsnoop record"):
        list(mod._records(str(_btsnoop(tmp_path, 2 << 20, b"\x00" * 8))))
    with pytest.raises(SystemExit, match="corrupt btsnoop record"):
        list(mod._records(str(_btsnoop(tmp_path, 64, b"\x00" * 8))))  # longer than the file


@pytest.mark.parametrize("tool", ["decrypt_btsnoop", "btsnoop_mtu_scan"])
def test_btsnoop_reads_valid_record(tool: str, tmp_path: Path) -> None:
    out = list(_load(tool)._records(str(_btsnoop(tmp_path, 4, b"\x02\x01\x02\x03"))))
    assert len(out) == 1 and out[0][1] == b"\x02\x01\x02\x03"


def test_decrypt_argparse_preserves_documented_flags() -> None:
    a = _load("decrypt_btsnoop").parse_args(["x.log", "--dump", "a1,a4", "--all", "--limit", "5", "--starts"])
    assert a.paths == ["x.log"] and a.dump == {0xA1, 0xA4} and a.all and a.starts and a.limit == 5


def test_mtu_scan_argparse_protype() -> None:
    a = _load("btsnoop_mtu_scan").parse_args(["a.log", "--protype", "b.log"])
    assert a.paths == ["a.log", "b.log"] and a.protype


# --- h60a6_live_check.read_kelvin -------------------------------------------------


def _mode_frame(sub: int, kelvin: int) -> bytes:
    body = bytes([0xAA, 0x05, sub, 0x01, kelvin >> 8, kelvin & 0xFF]) + bytes(13)
    c = 0
    for b in body:
        c ^= b
    return body + bytes([c])


class _FakeConn:
    def __init__(self, frames: list[bytes]) -> None:
        self.frames = frames

    async def query(self, *_a: object, **_k: object) -> list[bytes]:
        return self.frames


class _FakeDev:
    def __init__(self, frames: list[bytes]) -> None:
        self._conn = _FakeConn(frames)


def test_read_kelvin_validates_with_kaitai_and_returns_value() -> None:
    lc = _load("h60a6_live_check")
    assert asyncio.run(lc.read_kelvin(_FakeDev([_mode_frame(0x15, 2700)]))) == 2700


def test_read_kelvin_skips_other_submodes_and_does_not_swallow_attribute_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lc = _load("h60a6_live_check")
    assert asyncio.run(lc.read_kelvin(_FakeDev([_mode_frame(0x04, 2700)]))) is None
    from govee_ble_local._generated.govee_ble_frame import GoveeBleFrame

    class Broken:
        body = object()  # no .body -> must raise, not be swallowed

    monkeypatch.setattr(GoveeBleFrame, "from_bytes", classmethod(lambda cls, b: Broken()))
    with pytest.raises(AttributeError):
        asyncio.run(lc.read_kelvin(_FakeDev([_mode_frame(0x15, 2700)])))
