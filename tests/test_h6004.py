"""H6004 (BLE revision, goodsType 64, ``bulblightv3`` split): a profile-only addition. It reuses the
0x0d both-slot CCT frame of H6008 (``h6006`` scheme), percent brightness, polled read-back and the
type-1 RGB scene upload.

Golden frames are derived from the decompiled Govee Home 7.7.02 app (see the H6004 comment in
profile.py and the ``bulblightv3`` family in spec/devices.yaml), never from the library:

* colour: ``Config.x[64]`` -> ``Config.rv`` = 2 -> ``Config.bu``; RGB = ``Bytes.f`` = ``0d r g b 00...``,
  CCT = ``Bytes.p`` = ``0d <tint> <K u2be> <tint>``, tint = ``Constant.getTemColorByKelvin``.
* kelvin ``KelvinConfig`` map 64 -> ``V2`` = [2700, 6500] (``bulblightv3/pact/Support.getColorTemRange``).
* brightness ``Support.getBrightnessRange`` = {1, 100, 0}: a plain percent byte.
* scenes: cloud library applied with version array {0, 1} (``ScenesDelegateRegister`` ->
  ``Support4BulbLightV3.g(64)``; the IoT path ``Support.is2NewScenesMode`` uses {1}): type-1 RGB uploads
  with comm byte 0x01 (``ScenesOp.parseSceneV1``), type 0 activates only.

Tints: 2700 K ffae54, 2800 K ffb25b, 4000 K ffd5a1, 6500 K fff9fb. Not hardware-verified.
"""
from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device, frames_of

from govee_ble_local import Capability, Encryption
from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import profile_for, supported_skus
from govee_ble_local.exceptions import GoveeBleNotSupported
from govee_ble_local.identify import identify, sku_from_local_name
from govee_ble_local.scenes import load_scenes
from govee_ble_local.wire import build as b

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def _frame(*payload: int) -> bytes:
    buf = bytearray(20)
    buf[: len(payload)] = bytes(payload)
    x = 0
    for c in buf[:19]:
        x ^= c
    buf[19] = x
    return bytes(buf)


def _dev(replies: dict[int, list[bytes]] | None = None) -> Device:
    d = make_device(ble_device("AA:BB:CC:DD:EE:04", "H6004"), "H6004")
    assert d is not None
    sent: list[bytes] = []
    unacked: list[bytes] = []
    table = replies or {}

    async def send(frame: bytes, **_k: object) -> None:
        sent.append(frame)

    async def send_many(frames: list[bytes]) -> None:
        sent.extend(frames)

    async def send_unacked(frames: Any) -> None:
        unacked.extend(frames_of(frames))

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        return list(table.get(frame[1], []))

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock(side_effect=send_unacked)  # type: ignore[method-assign]
    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    d._sent = sent  # type: ignore[attr-defined]
    d._unacked = unacked  # type: ignore[attr-defined]
    return d


def _sent(d: Device) -> list[bytes]:
    return d._sent  # type: ignore[attr-defined, no-any-return]


def _unacked(d: Device) -> list[bytes]:
    return d._unacked  # type: ignore[attr-defined, no-any-return]


# ── profile ──────────────────────────────────────────────────────────────────
def test_profile() -> None:
    p = profile_for("h6004")
    assert p is not None
    assert p.capabilities == {Capability.POWER, Capability.BRIGHTNESS, Capability.RGB,
                              Capability.COLOR_TEMP, Capability.SCENES}
    assert p.color_scheme == "h6006" and p.segments == 0 and p.zones == ()
    assert (p.min_kelvin, p.max_kelvin) == (2700, 6500)         # KelvinConfig map 64 -> V2
    assert p.brightness_range is None and p.brightness_max == 100   # percent, no raw scaling
    assert p.encryption is Encryption.NONE and not p.requires_secret
    assert p.readback == "polled" and p.scene_versions == frozenset({0, 1})
    assert p.scene_dialect == "A" and p.color_readback == ""
    assert "H6004" in supported_skus()


def test_same_wire_behaviour_as_the_h6008_sibling() -> None:
    # One goodsType-64 row: the three bulbs resolve to identical facts, so they share one profile (scenes:
    # the control screen's version array {0, 1}, Support4BulbLightV3.g).
    h6004, h6008 = profile_for("H6004"), profile_for("H6008")
    assert h6004 is not None and h6004 is h6008 and h6004.skus == ("H6004", "H6006", "H6008")


@pytest.mark.parametrize("sku", ["H6003", "H6085"])
def test_wifi_only_siblings_not_supported(sku: str) -> None:
    assert profile_for(sku) is None   # goodsType 9 / soft-AP Wi-Fi bulbs: no BLE control path


# ── BLE name -> SKU ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", [
    "Govee_H6004_1A2B", "ihoment_H6004_1A2B", "Minger_H6004_1A2B", "GBK_H6004_1A2B",
    "GVH6004_1A2B", "GV60041A2B",
])
def test_advertised_names_resolve_to_h6004(name: str) -> None:
    assert sku_from_local_name(name) == "H6004"
    adv = identify(name, {})
    assert adv is not None and adv.sku == "H6004"
    assert profile_for(adv.sku) is not None


def test_device_is_made_for_an_advertised_h6004() -> None:
    adv = identify("Govee_H6004_1A2B", {})
    assert adv is not None
    d = make_device(ble_device("AA:BB:CC:DD:EE:04", "Govee_H6004_1A2B"), adv.sku)
    assert d is not None and d.sku == "H6004" and "H6004" in d.profile.skus


# ── builders: golden frames ──────────────────────────────────────────────────
def test_rgb_frame() -> None:
    assert b.color_rgb(0x12, 0x34, 0x56, "h6006")[:11].hex() == "33050d123456" + "00" * 5


@pytest.mark.parametrize(("kelvin", "tint", "k_hex"), [
    (2700, "ffae54", "0a8c"),
    (4000, "ffd5a1", "0fa0"),
    (6500, "fff9fb", "1964"),
])
def test_cct_frame_is_the_both_slot_form(kelvin: int, tint: str, k_hex: str) -> None:
    f = b.color_temp(kelvin, "h6006")
    assert f[:11].hex() == f"33050d{tint}{k_hex}{tint}"
    assert f[11:19] == bytes(8)


def test_brightness_is_a_percent_byte() -> None:
    assert [b.brightness(p)[:3].hex() for p in (1, 50, 100)] == ["330401", "330432", "330464"]


# ── device ───────────────────────────────────────────────────────────────────
def test_set_color_temp_sends_the_app_frame_and_rounds_to_100k() -> None:
    d = _dev()
    asyncio.run(d.set_color_temp(2700))
    asyncio.run(d.set_color_temp(2750))   # not on the app's 100 K grid -> 2800 K
    assert [f[:11].hex() for f in _sent(d)] == [
        "33050dffae540a8cffae54",
        "33050dffb25b0af0ffb25b",
    ]
    assert d.state.color_temp_kelvin == 2800


@pytest.mark.parametrize(("kelvin", "edge"), [(2000, 2700), (2699, 2700), (6501, 6500), (9000, 6500)])
def test_set_color_temp_clamped_to_the_range(kelvin: int, edge: int) -> None:
    # like the app (KelvinConfig.calibrationKelvin): out of range is clamped, not rejected, and the
    # frame is exactly the one for the range edge
    d = _dev()
    asyncio.run(d.set_color_temp(kelvin))
    ref = _dev()
    asyncio.run(ref.set_color_temp(edge))
    assert _sent(d) == _sent(ref) != [] and d.state.color_temp_kelvin == edge


def test_set_brightness_writes_percent_and_reports_it() -> None:
    d = _dev()
    asyncio.run(d.set_brightness(100))
    asyncio.run(d.set_brightness(1))
    assert [f[:3].hex() for f in _sent(d)] == ["330464", "330401"]
    assert d.state.brightness == 1


def test_set_rgb_sends_the_0d_frame() -> None:
    d = _dev()
    asyncio.run(d.set_rgb((1, 2, 3)))
    assert _sent(d)[0][:11].hex() == "33050d010203" + "00" * 5
    assert d.state.rgb_color == (1, 2, 3)


def test_segments_not_supported() -> None:
    d = _dev()
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(d.set_segment_rgb([0], (1, 2, 3)))


# ── read-back: polled aa 01 / aa 04 / aa 05 ─────────────────────────────────
def _poll(mode_reply: bytes, brightness: int = 60) -> Device:
    d = _dev({
        0x01: [_frame(0xAA, 0x01, 0x01)],
        0x04: [_frame(0xAA, 0x04, brightness)],
        0x05: [mode_reply],
    })
    asyncio.run(d.update())
    return d


def test_update_reads_percent_brightness_and_cct() -> None:
    # the app's own CCT write puts the tint in the colour slot: [tint, kelvin u2be, tint]
    s = _poll(_frame(0xAA, 0x05, 0x0D, 0xFF, 0xAE, 0x54, 0x0A, 0x8C, 0xFF, 0xAE, 0x54)).state
    assert s.is_on is True and s.brightness == 60
    assert s.color_temp_kelvin == 2700 and s.rgb_color is None and s.scene_code is None


def test_update_reads_rgb() -> None:
    s = _poll(_frame(0xAA, 0x05, 0x0D, 0x12, 0x34, 0x56)).state
    assert s.rgb_color == (0x12, 0x34, 0x56) and s.color_temp_kelvin is None


def test_update_reads_scene() -> None:
    assert _poll(_frame(0xAA, 0x05, 0x04, 0x97, 0x04)).state.scene_code == 1175


# ── scenes (cloud library, version array {0, 1}) ──────────────────────────────────────
def test_scene_catalog_shipped_and_types_fit_version_1() -> None:
    cat = load_scenes("H6004")
    assert len(cat) == 63
    # only static (0) and rgb (1) scenes: every uploadable one is covered by the version array
    assert {s.scene_type for s in cat.values()} == {0, 1}


def test_type_1_scene_uploads_with_comm_byte_1_then_activates() -> None:
    d = _dev()
    asyncio.run(d.set_scene_by_name("Rainbow"))
    up = _unacked(d)
    # a3, seq 0, [01 marker, packet count, comm byte 01 (type-1 RGB, parseSceneV1 version 1)]
    assert up and up[0][:5].hex() == "a300010201"
    assert [f[:5].hex() for f in _sent(d)] == ["3305049704"]
    assert d.state.scene_code == 1175


def test_static_scene_is_activate_only() -> None:
    d = _dev()
    asyncio.run(d.set_scene_by_name("Reading"))
    assert _unacked(d) == []
    assert [f[:5].hex() for f in _sent(d)] == ["3305040d00"]
    assert d.state.scene_code == 13


# ── field-test plan ──────────────────────────────────────────────────────────
def _plans(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(TOOLS))
    return importlib.import_module("field_test")


def test_field_test_plan_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    ft = _plans(monkeypatch)
    plan = ft.PLANS["H6004"]
    d = _dev()
    codes = {n: s.code for n, s in load_scenes("H6004").items()}
    steps = plan.build_steps(ft.PlanContext(d, "H6004", codes))
    assert [s.name for s in steps] == [
        "power_off", "power_on", "brightness_100", "brightness_1", "brightness_50",
        "rgb_red", "rgb_green", "rgb_blue",
        "color_temp_2700", "color_temp_4000", "color_temp_6500", "color_temp_2750",
        "rgb_pick_equal_to_4000k_tint",
        "scene_Reading", "scene_Rainbow", "scene_Fire",
    ]

    # drive every action, then check the expectation against the state the library now reports
    async def run_all() -> None:
        for step in steps:
            await step.action()
            snap = {"is_on": d.state.is_on, "brightness": d.state.brightness,
                    "rgb_color": d.state.rgb_color, "color_temp_kelvin": d.state.color_temp_kelvin,
                    "scene_code": d.state.scene_code}
            assert step.expect is not None and step.expect(snap), step.name

    asyncio.run(run_all())
    assert "33050dffb25b0af0ffb25b" in [f[:11].hex() for f in _sent(d)]   # 2750 K went out as 2800 K


def test_runner_picks_the_h6004_plan_from_its_advertised_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _plans(monkeypatch)
    runner = importlib.import_module("field_test.runner")
    for name in ("Govee_H6004_1A2B", "ihoment_H6004_1A2B", "GV60041A2B"):
        assert runner.match_plan_sku(name, None, ["H6004", "H6188"]) == "H6004"
    assert runner.match_plan_sku("whatever", "h6004", ["H6004"]) == "H6004"
