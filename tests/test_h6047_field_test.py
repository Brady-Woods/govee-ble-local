"""The H6047 field-test plan (tools/field_test/plans/h6047.py), run end to end offline.

A small simulator of what an H6047 does with the frames the app sends sits behind the real
``Device``, so the plan's steps, read-back expectations and restore are exercised against the real
frame builders and parsers. No BLE hardware."""
from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device
from test_h6047 import _dev, _frame, _sent


class _Sim:
    """What an H6047 does with the frames the app sends, per the spec: just enough to run the plan."""

    def __init__(self) -> None:
        self.on, self.bars, self.pct, self.gradual = True, [True, True], 50, False
        self.rgb: dict[int, tuple[int, int, int]] = {i: (1, 2, 3) for i in range(10)}
        self.kelvin: dict[int, int] = {}

    def write(self, f: bytes) -> None:
        if f[:2] == b"\x33\x01":
            self.on = bool(f[2])
            self.bars = [self.on, self.on]
        elif f[:2] == b"\x33\x36":
            self.bars = [bool(f[2]), bool(f[3])]
            self.on = any(self.bars)
        elif f[:2] == b"\x33\x04":
            self.pct = f[2]
        elif f[:2] == b"\x33\xa3":
            self.gradual = bool(f[2])
        elif f[:4] == b"\x33\x05\x15\x01":
            mask = f[12] | (f[13] << 8)
            kelvin = (f[7] << 8) | f[8]
            for i in range(10):
                if mask >> i & 1:
                    if kelvin:
                        self.kelvin[i], self.rgb[i] = kelvin, (f[9], f[10], f[11])
                    else:
                        self.kelvin.pop(i, None)
                        self.rgb[i] = (f[4], f[5], f[6])

    def bar_kelvin(self, bar: range) -> int:
        ks = {self.kelvin.get(i, 0) for i in bar}
        return ks.pop() if len(ks) == 1 else 0

    def read(self, f: bytes) -> list[bytes]:
        if f[1] == 0x01:
            return [_frame(0xAA, 0x01, int(self.on))]
        if f[1] == 0x04:
            return [_frame(0xAA, 0x04, self.pct)]
        if f[1] == 0x36:
            return [_frame(0xAA, 0x36, int(self.bars[0]), int(self.bars[1]), 0)]
        if f[1] == 0xA3:
            return [_frame(0xAA, 0xA3, int(self.gradual))]
        if f[1] == 0x05:
            kl, kr = self.bar_kelvin(range(0, 5)), self.bar_kelvin(range(5, 10))
            return [_frame(0xAA, 0x05, 0x15, int(self.gradual), kl >> 8, kl & 0xFF, kr >> 8, kr & 0xFF)]
        if f[1] == 0xA5:
            g = f[2]
            recs: list[int] = []
            for i in range(4):
                seg = (g - 1) * 4 + i
                recs += [100, *self.rgb[seg]] if seg < 10 else [0, 0, 0, 0]
            return [_frame(0xAA, 0xA5, g, *recs)]
        return []


def test_plan_runs_end_to_end_and_restores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent / "tools"))
    ft = importlib.import_module("field_test.runner")
    sim = _Sim()
    sim.on, sim.bars, sim.gradual = True, [True, False], True      # initial: left bar on, right off
    sim.rgb.update({i: (9, 8, 7) for i in range(5, 10)})
    d = _dev()

    async def send(frame: bytes, **_k: object) -> None:
        d._sent.append(frame)  # type: ignore[attr-defined]
        sim.write(frame)

    async def send_many(frames: list[bytes]) -> None:
        for f in frames:
            await send(f)

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        return sim.read(frame)

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    d.connect = AsyncMock()  # type: ignore[method-assign]
    d.stop = AsyncMock()  # type: ignore[method-assign]
    ble = ble_device("AA:BB:CC:DD:EE:47", "ihoment_H6047_1A2B")

    class _Adv:
        def __init__(self) -> None:
            self.local_name = "ihoment_H6047_1A2B"
            self.manufacturer_data: dict[int, bytes] = {}
            self.rssi = -60

    async def fake_discover(_address: object, _sku: object) -> tuple[Any, Any, list[dict[str, Any]], str]:
        return ble, _Adv(), [{"name": "ihoment_H6047_1A2B", "identified_sku": "H6047"}], "H6047"

    monkeypatch.setattr(ft, "discover", fake_discover)
    monkeypatch.setattr("govee_ble_local.create_device", lambda *_a, **_k: d)
    args = ft.parse_args(["--no-prompt", "--settle", "0", "--unattended"])
    assert asyncio.run(ft.run(args, tmp_path, "T")) == 0

    report_path = tmp_path / "h6047-report-T.json"
    rep = json.loads(report_path.read_text())
    assert rep["result"] == "completed" and rep["restore"] == "ok"
    assert rep["plan"] == {"sku": "H6047", "title": "H6047 two-bar light bar"}
    assert rep["initial_state"]["zone_power"] == {"0": True, "1": False}
    assert rep["initial_state"]["gradual"] is True
    assert all(r["sent"] == "ok" for r in rep["steps"])
    failed = [r["step"] for r in rep["steps"] if r["readback_matches"] is not True]
    assert failed == [], failed                    # every read-back expectation holds on a model device
    names = [r["step"] for r in rep["steps"]]
    assert names[:3] == ["segment_readback_count", "power_on", "zone_left_off"]
    assert {"zone_right_off", "power_off", "brightness_1", "rgb_blue", "rgb_left_red_right_blue",
            "color_temp_2200", "color_temp_left_2700_right_6500", "rgb_pick_equal_to_4000k_tint",
            "gradual_on", "gradual_off"} <= set(names)
    # the steps really hit the wire: whole-device power is 33 01, the per-bar buttons 33 36, gradual 33 a3
    frames = {f[:2].hex() for f in _sent(d)}
    assert {"3301", "3336", "33a3", "3304", "3305"} <= frames
    # the restore put the device back: left bar on, right bar off, original colours and gradual flag
    assert sim.on is True and sim.bars == [True, False] and sim.gradual is True and sim.pct == 50
    assert [sim.rgb[0], sim.rgb[9]] == [(1, 2, 3), (9, 8, 7)] and sim.kelvin == {}
    assert "AA:BB:CC:DD:EE:47" not in report_path.read_text()
