"""tools/field_test: nothing identifying reaches the report or the frame capture (both are meant for a
public GitHub issue), and neither is written through a planted symlink. No BLE hardware."""
from __future__ import annotations

import asyncio
import dataclasses
import importlib
import json
import os
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from govee_ble_local import DeviceState
from govee_ble_local.exceptions import GoveeBleConnectionError, GoveeBleTimeout

TOOLS = Path(__file__).resolve().parent.parent / "tools"
ADDR = "00:00:5E:00:53:D1"
WIFI = "00:00:5E:00:53:D0"    # the Wi-Fi MAC the device derives: BLE MAC minus one


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


def _identifying(text: str) -> list[str]:
    """Every spelling of ADDR / WIFI past the vendor prefix that occurs in ``text``."""
    forms: list[str] = []
    for mac in (ADDR.lower(), WIFI.lower()):
        tail = mac.split(":")[3:]
        forms += [*(sep.join(tail) for sep in (":", "-", "_")), mac.replace(":", ""), "".join(tail[1:])]
    return [f for f in forms if f in text.lower()]


# --- the scrubber -----------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    f"{ADDR}: no ack for write 0x05 after 3 attempts",
    f"{ADDR.lower()}: operation exceeded 10.0 s",
    f"connect to {ADDR.replace(':', '-')} failed",
    f"[org.bluez.Error.Failed] /org/bluez/hci0/dev_{ADDR.replace(':', '_')} not found",
    f"bare {ADDR.replace(':', '')} and {ADDR.replace(':', '').lower()}",
    f"wifi {WIFI} / {WIFI.replace(':', '')}",
])
def test_mask_text_masks_the_address_in_every_form(masking: ModuleType, text: str) -> None:
    out = masking.mask_text(text, ADDR)
    assert _identifying(out) == [], out
    assert "00005E" in re.sub(r"[:_-]", "", out.upper())   # the vendor prefix stays, for diagnosis


def test_mask_text_masks_any_mac_shaped_token_and_the_macos_uuid(masking: ModuleType) -> None:
    other = "11:22:33:44:55:66 dev_AA_BB_CC_DD_EE_FF 0A:A6:02:00:5E:00:53:56"
    assert masking.mask_text(other) == "11:22:33:xx:xx:xx dev_AA_BB_CC_xx_xx_xx 0A:A6:02:xx:xx:xx:xx:xx"
    uuid = "A1B2C3D4-1111-2222-3333-444455556666"
    assert masking.mask_text(f"{uuid.lower()}: gone", uuid) == "A1B2C3D4-…: gone"
    keep = "33 05 15 01 ff d5 a1, 2026-10-15T12:00:00, v1.00.13, 0x05"
    assert masking.mask_text(keep, ADDR) == keep
    once = masking.mask_text(f"{ADDR} {other}", ADDR)
    assert masking.mask_text(once, ADDR) == once   # idempotent


def test_describe_error(masking: ModuleType) -> None:
    err = GoveeBleTimeout(f"{ADDR}: no ack for write 0x05 after 3 attempts")
    assert masking.describe_error(err, ADDR) == (
        "GoveeBleTimeout: 00:00:5E:xx:xx:xx: no ack for write 0x05 after 3 attempts")


# --- error texts in the report ----------------------------------------------------------------


class _FailingDevice:
    encryption = "aes_rc4_psk"
    scene_names: list[str] = []   # noqa: RUF012 - test double

    def __init__(self) -> None:
        self.updates = 0

    async def connect(self) -> None: ...

    async def stop(self) -> None: ...

    async def update(self) -> SimpleNamespace:
        self.updates += 1
        if self.updates == 2:   # the read-back after the first step
            raise GoveeBleConnectionError(
                f"[org.bluez.Error.Failed] /org/bluez/hci0/dev_{ADDR.replace(':', '_')}: le-connection-abort")
        return SimpleNamespace(is_on=True)

    async def set_power(self, _on: bool) -> None:
        raise GoveeBleTimeout(f"{ADDR}: no ack for write 0x01 after 3 attempts")


def _run(ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, plan: Any,
         dev: Any, name: str = "ihoment_H9995_53D1") -> tuple[int, str]:
    ft.register_plan(plan)
    ble = SimpleNamespace(address=ADDR, name=name)
    adv = SimpleNamespace(local_name=name, manufacturer_data={}, rssi=-50)

    async def fake_discover(_a: Any, _s: Any) -> tuple[Any, Any, list[Any], str]:
        return ble, adv, [], plan.sku

    monkeypatch.setattr(runner, "discover", fake_discover)
    monkeypatch.setattr("govee_ble_local.create_device", lambda *_a, **_k: dev)
    try:
        rc = asyncio.run(runner.run(runner.parse_args(["--no-prompt", "--settle", "0", "--unattended"]),
                                    tmp_path, "T"))
    finally:
        ft.PLANS.pop(plan.sku, None)
    return rc, (tmp_path / f"{plan.sku.lower()}-report-T.json").read_text()


def test_send_readback_and_restore_errors_are_scrubbed(
    ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def restore(_d: Any, _st: Any) -> None:
        raise GoveeBleTimeout(f"{ADDR}: restore failed (Wi-Fi {WIFI})")

    dev = _FailingDevice()
    plan = ft.SkuPlan(sku="H9995", title="t", snapshot_keys=("is_on",), restore=restore,
                      build_steps=lambda ctx: [
                          ft.Step("off", lambda: ctx.dev.set_power(False), None, "Off?"),
                          ft.Step("on", lambda: ctx.dev.set_power(True), None, "On?")])
    rc, text = _run(ft, runner, monkeypatch, tmp_path, plan, dev)
    rep = json.loads(text)
    assert rc == 0 and rep["result"] == "completed"
    assert rep["steps"][0]["sent"] == (
        "error: GoveeBleTimeout: 00:00:5E:xx:xx:xx: no ack for write 0x01 after 3 attempts")
    assert rep["steps"][0]["readback"].startswith("error: GoveeBleConnectionError:")
    assert "dev_00_00_5E_xx_xx_xx" in rep["steps"][0]["readback"]
    assert rep["restore"].startswith("error: GoveeBleTimeout: 00:00:5E:xx:xx:xx: restore failed")
    assert _identifying(text) == [] and "53D1" not in text.upper()
    assert rep["device"] == {"address": "00:00:5E:xx:xx:xx", "name": "ihoment_H9995_xxxx",
                             "encryption": "aes_rc4_psk"}


def test_an_aborted_run_is_scrubbed(
    ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class _NoLink(_FailingDevice):
        async def connect(self) -> None:
            raise GoveeBleConnectionError(f"connect to {ADDR.lower()} failed: le-connection-abort")

    plan = ft.SkuPlan(sku="H9994", title="t", build_steps=lambda _ctx: [])
    rc, text = _run(ft, runner, monkeypatch, tmp_path, plan, _NoLink())
    assert rc == 1 and json.loads(text)["result"].startswith("aborted: GoveeBleConnectionError: connect to")
    assert _identifying(text) == []


# --- advertised names -------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "masked"), [
    ("ihoment_H6188_5383", "ihoment_H6188_xxxx"),
    ("Govee_H6008_6047", "Govee_H6008_xxxx"),
    ("GVH6188_1A2B", "GVH6188_xxxx"),
    ("GVH60A65357", "GVH60A6xxxx"),
    ("GV61881A2B", "GV6188xxxx"),
    ("Govee Glide wall", "Govee Glide wall"),
    ("Weird 6188 name", "Weird 6188 name"),
    ("", ""),
])
def test_mask_name_hides_the_mac_digits_a_govee_name_ends_in(
    masking: ModuleType, name: str, masked: str
) -> None:
    assert masking.mask_name(name) == masked
    assert masking.mask_name(None) is None


def test_discovery_masks_names_and_skips_a_device_identified_as_another_sku(
    runner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def adv(name: str, rssi: int) -> SimpleNamespace:
        return SimpleNamespace(local_name=name, manufacturer_data={}, rssi=rssi)

    seen = [  # an H6006 whose name happens to end in 6188 is the strongest signal around
        (SimpleNamespace(address="00:00:5E:00:53:88", name=None), adv("ihoment_H6006_6188", -40)),
        (SimpleNamespace(address=ADDR, name=None), adv("ihoment_H6188_53D1", -70)),
    ]

    class _Scanner:
        def __init__(self, cb: Any) -> None:
            self.cb = cb

        async def __aenter__(self) -> _Scanner:
            for dev, data in seen:
                self.cb(dev, data)
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr("bleak.BleakScanner", _Scanner)
    monkeypatch.setattr(runner, "SCAN_SECONDS", 0)
    dev, _adv, heard, sku = asyncio.run(runner.discover(None, None))
    assert dev.address == ADDR and sku == "H6188"
    assert sorted(e["name"] for e in heard) == ["ihoment_H6006_xxxx", "ihoment_H6188_xxxx"]
    assert {e["address"] for e in heard} == {"00:00:5E:xx:xx:xx"}


# --- snapshots --------------------------------------------------------------------------------


def test_snapshot_redacts_every_identifying_device_state_field(masking: ModuleType, ft: Any) -> None:
    from field_test.plan import REDACTED_KEYS

    names = [f.name for f in dataclasses.fields(DeviceState)]
    names += [n for n, v in vars(DeviceState).items() if isinstance(v, property)]
    identifying = {n for n in names if "mac" in n or "serial" in n or n.endswith("_id")}
    assert {"serial_number", "device_id", "wifi_mac", "ble_mac"} <= identifying <= set(REDACTED_KEYS)
    st = DeviceState(is_on=True, serial_number="0A:A6:02:00:5E:00:53:56", wifi_mac=WIFI, ble_mac=ADDR)
    snap = masking.snapshot(st, ("is_on", *sorted(identifying)))
    assert snap == {"is_on": True, "ble_mac": "<redacted>", "device_id": "<redacted>",
                    "serial_number": "<redacted>", "wifi_mac": "<redacted>"}


# --- the frame capture ------------------------------------------------------------------------


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_redaction_drops_every_wire_field_and_status_burst_payloads(
    masking: ModuleType, tmp_path: Path
) -> None:
    frames = tmp_path / "f.jsonl"
    # First chunk: header ac 00 <count 02 00> <lastLen 06> <cmd 03> <sub 02>, then a 0x07 TLV at byte 7.
    burst0 = ("ac00" + "0200060302" + "0707" + "1122334455667788990011").ljust(40, "0")
    rows = [
        {"ts": 1, "addr": ADDR, "dir": "tx", "plain": None, "wire": "e701" + "ab" * 18, "enc": "e7"},
        {"ts": 2, "addr": ADDR, "dir": "rx", "plain": None, "wire": "e701" + "cd" * 18, "enc": ""},
        {"ts": 3, "addr": ADDR, "dir": "tx", "plain": "aa0101" + "00" * 17, "wire": "5a" * 20, "enc": "aes"},
        {"ts": 4, "addr": ADDR, "dir": "tx", "plain": "ac" + "00" * 19, "wire": "77" * 20, "enc": "aes"},
        {"ts": 5, "addr": ADDR, "dir": "rx", "plain": burst0, "wire": "66" * 20, "enc": "aes"},
        {"ts": 6, "addr": ADDR, "dir": "rx", "plain": "acff" + "00005e0053d0" + "00" * 12, "wire": "55" * 20},
    ]
    _write_jsonl(frames, rows)
    assert masking.redact_frames(frames, ADDR) == 6
    out = [json.loads(line) for line in frames.read_text().splitlines()]
    assert all(r["wire"] is None for r in out)                     # no ciphertext, no handshake
    assert all(r["addr"] == "00:00:5E:xx:xx:xx" for r in out)
    assert out[2]["plain"] == rows[2]["plain"] and out[3]["plain"] == rows[3]["plain"]   # tx kept
    assert out[4]["plain"] == "ac000200060302" + "0" * 26 and out[4]["redacted"] == "status-burst payload"
    assert out[5]["plain"] == "acff" + "0" * 36
    raw = frames.read_text().lower()
    assert "abab" not in raw and "cdcd" not in raw and "1122334455" not in raw and "53d0" not in raw


def test_redaction_never_writes_through_a_planted_temp_file_symlink(
    masking: ModuleType, tmp_path: Path
) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("precious\n")
    frames = tmp_path / "h6188-frames-T.jsonl"
    _write_jsonl(frames, [{"ts": 1, "addr": ADDR, "dir": "tx", "plain": "aa01", "wire": "aa01"}])
    os.symlink(victim, frames.with_suffix(".tmp"))         # the old, predictable temp name
    assert masking.redact_frames(frames, ADDR) == 1
    assert victim.read_text() == "precious\n"
    assert json.loads(frames.read_text())["addr"] == "00:00:5E:xx:xx:xx"


def test_redaction_does_not_read_a_capture_that_is_a_symlink(
    masking: ModuleType, tmp_path: Path
) -> None:
    victim = tmp_path / "victim.jsonl"
    secret = json.dumps({"ts": 1, "addr": ADDR, "dir": "tx", "plain": "aa01", "wire": "aa01"}) + "\n"
    victim.write_text(secret)
    frames = tmp_path / "h6188-frames-T.jsonl"
    os.symlink(victim, frames)
    assert masking.redact_frames(frames, ADDR) == 0
    assert victim.read_text() == secret and not frames.is_symlink() and frames.read_text() == ""


def test_the_report_is_never_written_through_a_planted_symlink(
    runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("precious\n")
    os.symlink(victim, tmp_path / "fieldtest-report-T.json")

    async def nothing(_a: Any, _s: Any) -> tuple[Any, Any, list[Any], None]:
        return None, None, [], None

    monkeypatch.setattr(runner, "discover", nothing)
    assert asyncio.run(runner.run(runner.parse_args(["--no-prompt"]), tmp_path, "T")) == 2
    assert victim.read_text() == "precious\n"
    report = tmp_path / "fieldtest-report-T.json"
    assert not report.is_symlink() and json.loads(report.read_text())["result"] == "device not found"


def test_the_capture_is_redacted_only_after_the_frame_log_reached_the_disk(
    ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Device.stop() does not drain the frame log's writer thread; the redaction must wait for it, or
    records still queued land, unredacted, after the rewrite."""
    from govee_ble_local.transport import framelog

    real_write = framelog._write_batch
    writing = threading.Event()

    def slow_write(path: str, lines: list[str]) -> None:
        writing.set()
        time.sleep(0.3)
        real_write(path, lines)

    monkeypatch.setattr(framelog, "_write_batch", slow_write)
    log: list[Any] = []

    class _LoggingDevice:
        encryption = "none"
        scene_names: list[str] = []   # noqa: RUF012 - test double

        async def connect(self) -> None: ...

        async def stop(self) -> None: ...

        async def update(self) -> SimpleNamespace:
            frame = bytes.fromhex("aa01" + "00" * 18)
            log[0].record("tx", wire=frame, plain=frame)
            return SimpleNamespace(is_on=True)

        async def set_power(self, _on: bool) -> None: ...

    def create(*_a: Any, frame_log: str, **_k: Any) -> _LoggingDevice:
        log.append(framelog.FrameLog(frame_log, ADDR))
        return _LoggingDevice()

    monkeypatch.setattr("govee_ble_local.create_device", create)
    plan = ft.SkuPlan(sku="H9993", title="t", snapshot_keys=("is_on",),
                      build_steps=lambda ctx: [ft.Step("on", lambda: ctx.dev.set_power(True), None, "On?")])
    ft.register_plan(plan)
    ble = SimpleNamespace(address=ADDR, name="n")

    async def fake_discover(_a: Any, _s: Any) -> tuple[Any, Any, list[Any], str]:
        return ble, None, [], "H9993"

    monkeypatch.setattr(runner, "discover", fake_discover)
    try:
        args = runner.parse_args(["--no-prompt", "--settle", "0", "--unattended"])
        assert asyncio.run(runner.run(args, tmp_path, "T")) == 0
    finally:
        ft.PLANS.pop("H9993", None)
    assert writing.is_set()
    rep = json.loads((tmp_path / "h9993-report-T.json").read_text())
    text = (tmp_path / "h9993-frames-T.jsonl").read_text()
    records = [json.loads(line) for line in text.splitlines()]
    assert rep["frames_redacted"] == len(records) == 3    # initial, step and final read-backs
    assert ADDR not in text and all(r["addr"] == "00:00:5E:xx:xx:xx" for r in records)
