"""tools/field_test: the generic runner (masking, SKU detection, plan registry) with a fake device.

The H6188 plan's end-to-end run lives in test_h6188.py; the per-SKU plans of later devices test
their own steps. No BLE hardware."""
from __future__ import annotations

import asyncio
import importlib
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"


@pytest.fixture
def ft(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.syspath_prepend(str(TOOLS))
    yield importlib.import_module("field_test")


@pytest.fixture
def runner(ft: ModuleType) -> ModuleType:
    return importlib.import_module("field_test.runner")


@pytest.fixture
def masking(ft: ModuleType) -> ModuleType:
    return importlib.import_module("field_test.masking")


# --- masking (preserved from the H6188 field test) --------------------------------------------


def test_masks_address_serial_and_wifi_mac(masking: ModuleType, tmp_path: Path) -> None:
    frames = tmp_path / "f.jsonl"
    info = "aa0710" + "5ce753f47457" + "1122334455" + "00" * 4   # device-info reply: MAC + serial
    rows = [
        {"ts": 1, "addr": "5C:E7:53:F4:74:57", "dir": "tx", "plain": "aa0710" + "00" * 17,
         "wire": "aa0710" + "00" * 17, "enc": "none"},
        {"ts": 2, "addr": "5C:E7:53:F4:74:57", "dir": "rx", "plain": info, "wire": info, "enc": "none"},
        {"ts": 3, "addr": "5C:E7:53:F4:74:57", "dir": "rx", "plain": "aa04fe" + "00" * 17,
         "wire": "aa04fe" + "00" * 17, "enc": "none"},
    ]
    frames.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert masking.redact_frames(frames, "5C:E7:53:F4:74:57") == 3
    out = [json.loads(line) for line in frames.read_text().splitlines()]
    assert {r["addr"] for r in out} == {"5C:E7:53:xx:xx:xx"}
    assert out[1]["plain"] == "aa0710" + "0" * (len(info) - 6) and out[1]["wire"] is None
    assert out[0]["plain"] == rows[0]["plain"] and out[2]["plain"] == rows[2]["plain"]  # untouched
    assert all(r["wire"] is None for r in out)   # wire bytes (ciphertext, handshake) are never kept
    raw = frames.read_text().lower()
    assert "5ce753f47457" not in raw and "f4:74:57" not in raw and "1122334455" not in raw
    assert masking.redact_frames(tmp_path / "missing.jsonl", "x") == 0
    assert masking.mask_address("5c-e7-53-f4-74-57") == "5C-E7-53-xx-xx-xx"
    assert masking.mask_address("A1B2C3D4-1111-2222-3333-444455556666") == "A1B2C3D4-…"
    assert masking.mask_address(None) is None


def test_snapshot_redacts_identifiers_even_if_a_plan_asks_for_them(masking: ModuleType) -> None:
    st = SimpleNamespace(is_on=True, serial_number="SER123", wifi_mac="AA:BB:CC:DD:EE:FF", extra=7,
                         device_id="SER123", ble_mac="AA:BB:CC:DD:EE:00")
    keys = ("is_on", "serial_number", "wifi_mac", "extra", "missing", "device_id", "ble_mac")
    snap = masking.snapshot(st, keys)
    assert snap == {"is_on": True, "serial_number": "<redacted>", "wifi_mac": "<redacted>",
                    "extra": 7, "missing": None, "device_id": "<redacted>", "ble_mac": "<redacted>"}


# --- registry + discovery of plans ------------------------------------------------------------


def test_h6188_plan_is_registered_by_package_import(ft: Any) -> None:
    assert "H6188" in ft.PLANS and ft.PLANS["H6188"].sku == "H6188"
    assert {"PLANS", "SkuPlan", "Step", "PlanContext", "register_plan"} <= set(ft.__all__)


def test_register_plan_rejects_duplicates_and_lowercase(ft: Any) -> None:
    plan = ft.SkuPlan(sku="H9998", title="t", build_steps=lambda _ctx: [])
    try:
        assert ft.register_plan(plan) is plan
        with pytest.raises(ValueError, match="already registered"):
            ft.register_plan(plan)
    finally:
        ft.PLANS.pop("H9998", None)
    with pytest.raises(ValueError, match="upper-case"):
        ft.register_plan(ft.SkuPlan(sku="h9998", title="t", build_steps=lambda _ctx: []))
    assert "H9998" not in ft.PLANS


def test_plans_package_imports_every_module_in_the_directory(ft: Any) -> None:
    on_disk = {p.stem for p in (TOOLS / "field_test" / "plans").glob("*.py") if p.stem != "__init__"}
    registered = {sku.lower() for sku in ft.PLANS}
    assert on_disk <= registered   # each plans/<sku>.py registered its plan


# --- SKU detection ----------------------------------------------------------------------------


def test_match_plan_sku(runner: Any) -> None:
    m = runner.match_plan_sku
    assert m("ihoment_H6188_1A2B", None, ["H6188"]) == "H6188"
    assert m("whatever", "h6188", ["H6188"]) == "H6188"          # library-identified SKU wins
    assert m("GV61881A2B", None, ["H6188"]) == "H6188"           # bare-GV name, parsed like the app
    assert m("Weird 6188 name", None, ["H6188"]) == "H6188"      # digits-in-name fallback
    assert m("ihoment_H6006_6188", "H6006", ["H6188"]) is None   # an H6006 whose MAC ends in 61:88
    assert m("Govee_H6008_6047", None, ["H6047"]) is None        # the parsed SKU decides, not the digits
    assert m("ihoment_H6047_1A2B", None, ["H6188"]) is None
    assert m("", None, ["H6188"]) is None


def test_name_hints_select_a_plan(ft: Any, runner: Any) -> None:
    plan = ft.SkuPlan(sku="H9997", title="t", build_steps=lambda _ctx: [], extra_name_hints=("Glide",))
    ft.register_plan(plan)
    try:
        assert runner.match_plan_sku("Govee Glide wall", None, ["H9997"]) == "H9997"
        assert runner.match_plan_sku("Govee Strip", None, ["H9997"]) is None
    finally:
        ft.PLANS.pop("H9997", None)


# --- running a (fake) plan --------------------------------------------------------------------


class _FakeDevice:
    scene_names: tuple[str, ...] = ()
    encryption = "none"

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.on = True

    async def connect(self) -> None: ...

    async def stop(self) -> None: ...

    async def update(self) -> SimpleNamespace:
        return SimpleNamespace(is_on=self.on, tint=5, serial_number="SER1", wifi_mac="MAC1")

    async def set_power(self, on: bool) -> None:
        self.calls.append(("power", on))
        self.on = on


def _run_fake_plan(ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                   argv: list[str], restore_log: list[Any]) -> tuple[int, _FakeDevice]:
    dev = _FakeDevice()

    async def restore(d: Any, st: dict[str, Any]) -> None:
        restore_log.append((d, st))

    plan = ft.SkuPlan(
        sku="H9996", title="H9996 test lamp", intro="hello",
        snapshot_keys=("is_on", "tint", "serial_number", "wifi_mac"),
        restore=restore,
        build_steps=lambda ctx: [
            ft.Step("off", lambda: ctx.dev.set_power(False), lambda st: st["is_on"] is False, "Off?"),
            ft.Step("bad", lambda: ctx.dev.set_power(True), lambda st: st["tint"] == 6, "On?"),
        ],
    )
    ft.register_plan(plan)
    ble = SimpleNamespace(address="AA:BB:CC:DD:EE:11", name="ihoment_H9996_1A2B")
    adv = SimpleNamespace(local_name="ihoment_H9996_1A2B", manufacturer_data={}, rssi=-50)
    seen: list[Any] = []

    async def fake_discover(address: Any, sku: Any) -> tuple[Any, Any, list[Any], str]:
        seen.append((address, sku))
        return ble, adv, [], sku or "H9996"

    monkeypatch.setattr(runner, "discover", fake_discover)
    monkeypatch.setattr("govee_ble_local.create_device", lambda *_a, **_k: dev)
    try:
        args = runner.parse_args([*argv, "--no-prompt", "--settle", "0"])
        return asyncio.run(runner.run(args, tmp_path, "T")), dev
    finally:
        ft.PLANS.pop("H9996", None)


def test_runner_runs_a_registered_plan_with_its_own_snapshot_keys_and_restore(
    ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log: list[Any] = []
    rc, dev = _run_fake_plan(ft, runner, monkeypatch, tmp_path, [], log)
    assert rc == 0 and dev.calls == [("power", False), ("power", True)]
    rep = json.loads((tmp_path / "h9996-report-T.json").read_text())
    assert rep["plan"]["sku"] == "H9996" and rep["restore"] == "ok" and len(log) == 1
    assert rep["initial_state"] == {"is_on": True, "tint": 5, "serial_number": "<redacted>",
                                    "wifi_mac": "<redacted>"}
    assert [r["readback_matches"] for r in rep["steps"]] == [True, False]
    assert "AA:BB:CC:DD:EE:11" not in (tmp_path / "h9996-report-T.json").read_text()


def test_sku_flag_is_passed_to_discovery_case_insensitively(
    ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rc, _ = _run_fake_plan(ft, runner, monkeypatch, tmp_path, ["--sku", "h9996"], [])
    assert rc == 0 and (tmp_path / "h9996-report-T.json").exists()


def test_runner_reports_not_found_and_unplanned_sku(
    ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def nothing(_a: Any, _s: Any) -> tuple[Any, Any, list[Any], None]:
        return None, None, [{"name": "x"}], None

    monkeypatch.setattr(runner, "discover", nothing)
    assert asyncio.run(runner.run(runner.parse_args(["--no-prompt"]), tmp_path, "T")) == 2
    assert json.loads((tmp_path / "fieldtest-report-T.json").read_text())["result"] == "device not found"

    ble = SimpleNamespace(address="AA:BB:CC:DD:EE:11", name="n")

    async def unknown(_a: Any, _s: Any) -> tuple[Any, Any, list[Any], None]:
        return ble, None, [], None

    monkeypatch.setattr(runner, "discover", unknown)
    assert asyncio.run(runner.run(runner.parse_args(["--address", "x"]), tmp_path, "U")) == 2
    assert "no test plan" in json.loads((tmp_path / "fieldtest-report-U.json").read_text())["result"]


def test_help_runs_as_a_directory_and_lists_plans() -> None:
    r = subprocess.run(  # noqa: S603 - fixed argv, sys.executable
        [sys.executable, str(TOOLS / "field_test"), "--help"], capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stderr
    assert "H6188" in r.stdout and "--sku" in r.stdout
