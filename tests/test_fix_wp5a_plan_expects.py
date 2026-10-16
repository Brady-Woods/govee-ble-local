"""The field-test plans' read-back expectations, run against states where they must hold.

Each plan runs end to end through the real runner on the real ``Device`` (mocked connection, no
prompts). The connection answers no status read, so after every step the library's own state for
what it just sent is what the read-back shows; the H6062 also answers its ``aa 41`` section count
and the ``aa a5`` segment walk from a small model of the segments it was sent. A wrong key or value
in a plan's ``expect`` then fails here instead of showing up as a false read-back mismatch in a
hardware field report. The H6047 has its own simulator (test_h6047_field_test.py). No BLE hardware."""
from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device

from govee_ble_local.devices.device import Device, make_device

TOOLS = Path(__file__).resolve().parent.parent / "tools"
H6062_SEGMENTS = 19


def _frame(*payload: int) -> bytes:
    buf = bytearray(20)
    buf[: len(payload)] = bytes(payload)
    x = 0
    for c in buf[:19]:
        x ^= c
    buf[19] = x
    return bytes(buf)


class _H6062Segments:
    """The H6062's per-segment state as its ``aa a5`` walk reports it, from the 0x15 frames it was sent
    (``33 05 15 01 <rgb> <K u2be> <tint> <mask @12>``, ``33 05 15 02 <pct> <mask @5>``, LE mask bits)."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.rgb = {i: (1, 2, 3) for i in range(n)}
        self.pct = {i: 100 for i in range(n)}

    def _indices(self, mask: bytes) -> list[int]:
        bits = int.from_bytes(mask, "little")
        return [i for i in range(self.n) if bits >> i & 1]

    def write(self, f: bytes) -> None:
        if f[:4] == b"\x33\x05\x15\x01":
            kelvin = f[7] << 8 | f[8]
            colour = (f[9], f[10], f[11]) if kelvin else (f[4], f[5], f[6])
            for i in self._indices(f[12:19]):
                self.rgb[i] = colour
        elif f[:4] == b"\x33\x05\x15\x02":
            for i in self._indices(f[5:19]):
                self.pct[i] = f[4]

    def read(self, f: bytes) -> list[bytes]:
        if f[1] == 0x41:
            return [_frame(0xAA, 0x41, 0x00, 0x38, self.n)]
        if f[1] == 0xA5:
            g = f[2]
            recs = [v for i in range((g - 1) * 4, min(g * 4, self.n)) for v in (self.pct[i], *self.rgb[i])]
            return [_frame(0xAA, 0xA5, g, *recs)]
        return []


def _device(sku: str, model: _H6062Segments | None = None) -> Device:
    d = make_device(ble_device("AA:BB:CC:DD:EE:01", f"ihoment_{sku}_1A2B"), sku)
    assert d is not None

    async def send(frame: bytes, **_k: object) -> None:
        if model is not None:
            model.write(frame)

    async def send_many(frames: list[bytes], **_k: object) -> None:
        for f in frames:
            await send(f)

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        return model.read(frame) if model is not None else []

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    d.connect = AsyncMock()  # type: ignore[method-assign]
    d.stop = AsyncMock()  # type: ignore[method-assign]
    return d


def _run_plan(sku: str, d: Device, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.syspath_prepend(str(TOOLS))
    runner = importlib.import_module("field_test.runner")
    name = f"ihoment_{sku}_1A2B"

    class _Adv:
        local_name = name
        manufacturer_data: dict[int, bytes] = {}   # noqa: RUF012 - test double
        rssi = -60

    async def fake_discover(_a: object, _s: object) -> tuple[Any, Any, list[dict[str, Any]], str]:
        return ble_device("AA:BB:CC:DD:EE:01", name), _Adv(), [], sku

    monkeypatch.setattr(runner, "discover", fake_discover)
    monkeypatch.setattr("govee_ble_local.create_device", lambda *_a, **_k: d)
    args = runner.parse_args(["--no-prompt", "--settle", "0"])
    assert asyncio.run(runner.run(args, tmp_path, "T")) == 0
    rep: dict[str, Any] = json.loads((tmp_path / f"{sku.lower()}-report-T.json").read_text())
    return rep


@pytest.mark.parametrize("sku", ["H6004", "H6061", "H6188", "H6062"])
def test_every_read_back_expectation_holds_on_a_model_device(
    sku: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = _H6062Segments(H6062_SEGMENTS) if sku == "H6062" else None
    rep = _run_plan(sku, _device(sku, model), tmp_path, monkeypatch)
    assert rep["result"] == "completed" and rep["restore"] == "ok"
    assert [r["step"] for r in rep["steps"] if r["sent"] != "ok"] == []
    failed = [(r["step"], r["readback"]) for r in rep["steps"] if r["readback_matches"] is False]
    assert failed == [], failed
    checked = [r["step"] for r in rep["steps"] if r["readback_matches"] is True]
    assert len(checked) >= 10   # most steps carry a check, and every one of them held


def test_h6062_whole_device_colour_checks_read_every_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rep = _run_plan("H6062", _device("H6062", _H6062Segments(H6062_SEGMENTS)), tmp_path, monkeypatch)
    by_name = {r["step"]: r for r in rep["steps"]}
    for step in ("segment_count", "rgb_red", "rgb_green", "rgb_blue", "segment_middle_red",
                 "segment_last_brightness_20", "color_temp_9000", "rgb_pick_equal_to_4000k_tint"):
        assert by_name[step]["readback_matches"] is True, step
    assert len(by_name["rgb_blue"]["readback"]["segments"]) == H6062_SEGMENTS


@pytest.mark.parametrize("sku", ["H6004", "H6047", "H6061", "H6062", "H6188"])
def test_no_expectation_holds_on_an_unknown_state(sku: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A check that also passes when nothing is known proves nothing: every one must fail here."""
    monkeypatch.syspath_prepend(str(TOOLS))
    ft = importlib.import_module("field_test")
    from govee_ble_local.scenes import load_scenes

    plan = ft.PLANS[sku]
    d = _device(sku)
    if sku == "H6062":
        d.set_segment_count(H6062_SEGMENTS)
    codes = {n: s.code for n, s in load_scenes(sku).items()}
    unknown = dict.fromkeys(plan.snapshot_keys)
    vacuous = [s.name for s in plan.build_steps(ft.PlanContext(d, sku, codes))
               if s.expect is not None and s.expect(unknown)]
    assert vacuous == []


def test_h6062_snapshot_keys_extend_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(TOOLS))
    ft = importlib.import_module("field_test")
    from field_test.plan import DEFAULT_SNAPSHOT_KEYS

    assert ft.PLANS["H6062"].snapshot_keys == (*DEFAULT_SNAPSHOT_KEYS, "segments")
