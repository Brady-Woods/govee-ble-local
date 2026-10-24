"""H6188 (rgblight legacy RGB strip): its colour tab's 0x0d frames (scheme "h6006"; the white-slot
"h6188" scheme, which H8015 keeps, is checked at the builder), the Kelvin->tint table, the raw 20..254
brightness range, the rgblight 0x0d read-back rule, and the fixed scene list.

All expectations are derived from the decompiled Govee Home 7.7.02 app (see the H6188 entry in
spec/devices.yaml for citations); none are hardware-verified."""
from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import spec_read
from conftest import require_optional
from fake_ble import ble_device, unacked_via

from govee_ble_local import Capability, Encryption
from govee_ble_local.devices.brightness import percent_to_raw, raw_to_percent
from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import DeviceProfile, profile_for
from govee_ble_local.exceptions import GoveeBleInvalidValue, GoveeBleNotSupported
from govee_ble_local.scenes import load_scenes
from govee_ble_local.wire import _spec
from govee_ble_local.wire import build as b
from govee_ble_local.wire.cct import tint_for_kelvin


def _frame(*payload: int) -> bytes:
    buf = bytearray(20)
    buf[: len(payload)] = bytes(payload)
    x = 0
    for c in buf[:19]:
        x ^= c
    buf[19] = x
    return bytes(buf)


def _dev(replies: dict[int, list[bytes]] | None = None) -> Device:
    d = make_device(ble_device("AA:BB:CC:DD:EE:88", "H6188"), "H6188")
    assert d is not None
    sent: list[bytes] = []
    table = replies or {}

    async def send(frame: bytes, **_k: object) -> None:
        sent.append(frame)

    async def send_many(frames: list[bytes]) -> None:
        sent.extend(frames)

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        return list(table.get(frame[1], []))

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock(side_effect=unacked_via(send_many))  # type: ignore[method-assign]
    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    d._sent = sent  # type: ignore[attr-defined]
    return d


def _sent(d: Device) -> list[bytes]:
    return d._sent  # type: ignore[attr-defined, no-any-return]


# ── profile ──────────────────────────────────────────────────────────────────
def test_profile() -> None:
    p = profile_for("h6188")
    assert p is not None
    assert p.capabilities == {Capability.POWER, Capability.BRIGHTNESS, Capability.RGB,
                              Capability.COLOR_TEMP, Capability.SCENES}
    assert p.color_scheme == "h6006" and p.segments == 0 and p.zones == ()   # the colour tab (Config.bu)
    assert (p.min_kelvin, p.max_kelvin) == (2000, 9000)         # KelvinConfig version 0
    assert p.brightness_range == (20, 254)                       # BrightnessConfig version 0
    assert p.encryption is Encryption.NONE and not p.requires_secret
    assert p.readback == "polled" and p.scene_versions == frozenset()


@pytest.mark.parametrize("sku", ["H6085", "H6086", "H6089"])
def test_wifi_only_siblings_not_supported(sku: str) -> None:
    assert profile_for(sku) is None   # soft-AP Wi-Fi bulbs: no BLE control path in the app


@pytest.mark.parametrize("rng", [(0, 0), (254, 20), (-1, 100), (20, 256)])
def test_brightness_range_validated(rng: tuple[int, int]) -> None:
    p = profile_for("H6188")
    assert p is not None
    with pytest.raises(ValueError, match="brightness_range"):
        dataclasses.replace(p, brightness_range=rng)


def test_brightness_range_none_is_default() -> None:
    assert DeviceProfile(skus=("X",), capabilities=frozenset()).brightness_range is None


# ── tint table (Constant.getTemColorByKelvin) ────────────────────────────────
def test_tint_table_known_entries() -> None:
    assert tint_for_kelvin(2000) == (0xFF, 0x8D, 0x0B)
    assert tint_for_kelvin(2700) == (0xFF, 0xAE, 0x54)
    # LinkedHashMap re-put: #fff9fd is first put at 6500 then re-put at 6600 (keeps its slot,
    # value updated), so 6500's first match is #fff9fb and 6600's is #fff9fd.
    assert tint_for_kelvin(6500) == (0xFF, 0xF9, 0xFB)
    assert tint_for_kelvin(6600) == (0xFF, 0xF9, 0xFD)
    assert tint_for_kelvin(9000) == (0xD9, 0xE1, 0xFF)


def test_tint_table_coverage_and_misses() -> None:
    for k in range(1000, 10001, 100):
        assert tint_for_kelvin(k) != (0, 0, 0), k
    for k in (999, 10001, 2750, 4001, 0):
        assert tint_for_kelvin(k) == (0, 0, 0), k


# ── builders ─────────────────────────────────────────────────────────────────
def test_color_rgb_matches_h6006_layout() -> None:
    assert b.color_rgb(1, 2, 3, "h6188") == b.color_rgb(1, 2, 3, "h6006")
    assert b.color_rgb(1, 2, 3, "h6188")[:7].hex() == "33050d010203" + "00"


def test_color_temp_carries_tint() -> None:
    f = b.color_temp(2700, "h6188")
    assert f[:11].hex() == "33050d" + "ffffff" + "0a8c" + "ffae54"
    assert f[11:19] == bytes(8)
    # off-table Kelvin: the builder leaves the tint zero (the Device layer rounds to 100 K first)
    assert b.color_temp(2750, "h6188")[8:11] == b"\x00\x00\x00"
    # the h6006 both-slot form is a different layout and stays distinct from h6188's
    assert b.color_temp(2700, "h6006")[:11].hex() == "33050d" + "ffae54" + "0a8c" + "ffae54"
    assert b.color_temp(2700, "h6006") != b.color_temp(2700, "h6188")


def test_color_temp_round_trips_kaitai() -> None:
    require_optional("kaitaistruct", reason="Kaitai runtime not installed")
    m = spec_read.tx(b.color_temp(4000, "h6188")).body.params
    assert m.sub_mode == _spec.Common.SubMode.color_cct_0d
    c, t = m.params.color, m.params.tint
    assert (c.r, c.g, c.b, m.params.kelvin) == (0xFF, 0xFF, 0xFF, 4000)
    assert (t.r, t.g, t.b) == (0xFF, 0xD5, 0xA1)


def test_segment_color_temp_rejected() -> None:
    with pytest.raises(GoveeBleNotSupported, match="h6188"):
        b.segment_color_temp(1, 3000, "h6188")


def test_brightness_raw_builder() -> None:
    assert b.brightness_raw(254)[:3].hex() == "3304fe"
    for bad in (-1, 256):
        with pytest.raises(GoveeBleInvalidValue, match="brightness level"):
            b.brightness_raw(bad)


# ── brightness conversion (NumberUtil, Java float semantics) ─────────────────
def test_percent_to_raw_known_values() -> None:
    # (int)(20 + (p-1) * 2.37f)
    assert [percent_to_raw(p, 20, 254) for p in (1, 2, 3, 4, 50, 99, 100)] == [
        20, 22, 24, 27, 136, 252, 254]
    assert percent_to_raw(0, 20, 254) == 20 and percent_to_raw(150, 20, 254) == 254   # clamped


def test_raw_to_percent_known_values_and_clamp() -> None:
    # ceil((raw-20)/2.37f) + 1 of the byte clamped to [20, 254] (ViewBrightness.b): 1..100. WAS: 0 below 20.
    assert [raw_to_percent(r, 20, 254) for r in (20, 21, 136, 254, 255)] == [1, 2, 50, 100, 100]
    assert {raw_to_percent(r, 20, 254) for r in range(0, 18)} == {1}
    assert raw_to_percent(0, 6, 254) == raw_to_percent(0, 40, 254) == 1
    # The percent range [1, 100] shows the byte itself (setDrawTextIn100 false). WAS: calculatePercent.
    assert [raw_to_percent(r, 1, 100) for r in (0, 1, 49, 100, 120)] == [0, 1, 49, 100, 100]


def test_brightness_round_trip_every_percent() -> None:
    raws = [percent_to_raw(p, 20, 254) for p in range(1, 101)]
    assert raws == sorted(set(raws)) and len(raws) == 100
    assert all(raw_to_percent(r, 20, 254) == p for p, r in zip(range(1, 101), raws, strict=True))


# ── read-back parse ──────────────────────────────────────────────────────────
def test_parse_mode_0d_rgblight_kelvin_marks_cct() -> None:
    cct = _frame(0xAA, 0x05, 0x0D, 0x10, 0x20, 0x30, 0x0A, 0x8C, 0xFF, 0xAE, 0x54)
    assert spec_read.mode_color(cct) == (None, 2700)                       # the default: kelvin != 0 => CCT
    assert spec_read.mode_color(cct, "rgb_kelvin_tint") == (None, 2700)    # H6188's own (the app's) decode
    rgb = _frame(0xAA, 0x05, 0x0D, 0x10, 0x20, 0x30)
    assert spec_read.mode_color(rgb) == ((0x10, 0x20, 0x30), None)


def test_parse_mode_0d_rgblight_rejects_other_frames() -> None:
    assert spec_read.mode_color(_frame(0xAA, 0x05, 0x04, 0x09)) is None
    assert spec_read.mode_color(_frame(0xAA, 0x04, 0x0D)) is None
    assert spec_read.mode_color(b"\xaa\x05\x0d\x01\x02") is None   # truncated body


def test_ksy_colour_layouts() -> None:
    require_optional("kaitaistruct", reason="Kaitai runtime not installed")
    st = _spec.State

    def layout(kind: str, body: bytes) -> Any:
        decode = int(getattr(_spec.Common.ColorDecode, kind))
        return _spec.read(st.ColorReport, body, decode, len(body)).layout

    t = layout("rgb_kelvin_tint", bytes([1, 2, 3, 0x0F, 0xA0, 0xFF, 0xD5, 0xA1]) + bytes(8))
    assert (t.color.r, t.color.g, t.color.b, t.kelvin) == (1, 2, 3, 4000)
    assert (t.tint.r, t.tint.g, t.tint.b) == (0xFF, 0xD5, 0xA1) and t.is_cct
    assert not layout("rgb_kelvin_tint", bytes([1, 2, 3, 0, 0, 0, 0, 0])).is_cct
    k0 = layout("gradual_kelvin_s16be", bytes([1, 0x0A, 0x8C]))
    assert (k0.gradual, k0.kelvin) == (1, 2700) and k0.is_cct
    k1 = layout("rgb", bytes([7, 8, 9]))
    assert (k1.color.r, k1.color.g, k1.color.b) == (7, 8, 9) and not k1.is_cct


# ── device ───────────────────────────────────────────────────────────────────
def test_set_brightness_writes_raw_and_reports_percent() -> None:
    d = _dev()
    asyncio.run(d.set_brightness(100))
    asyncio.run(d.set_brightness(1))
    asyncio.run(d.set_brightness(50))
    assert [f[:3].hex() for f in _sent(d)] == ["3304fe", "330414", "330488"]
    assert d.state.brightness == 50


def test_turn_on_batches_raw_brightness_and_cct() -> None:
    d = _dev()
    asyncio.run(d.turn_on(brightness=100, kelvin=6500))
    f = _sent(d)
    assert f[0][:3].hex() == "330101"
    assert f[1][:3].hex() == "3304fe"
    assert f[2][:11].hex() == "33050dfff9fb1964fff9fb"
    assert d.state.color_temp_kelvin == 6500 and d.state.brightness == 100


def test_set_color_temp_range_clamped() -> None:
    d = _dev()                       # 2000..9000, clamped like the app (KelvinConfig.calibrationKelvin)
    asyncio.run(d.set_color_temp(1500))
    assert _sent(d)[0][6:8].hex() == "07d0" and d.state.color_temp_kelvin == 2000
    asyncio.run(d.set_color_temp(9000))
    assert _sent(d)[1][3:11].hex() == "d9e1ff2328d9e1ff"


def test_segments_not_supported() -> None:
    d = _dev()
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(d.set_segment_rgb([0], (1, 2, 3)))


def _poll(mode_reply: bytes) -> Device:
    d = _dev({
        0x01: [_frame(0xAA, 0x01, 0x01)],
        0x04: [_frame(0xAA, 0x04, 0xFE)],
        0x05: [mode_reply],
    })
    asyncio.run(d.update())
    return d


def test_update_reads_raw_brightness_and_cct() -> None:
    d = _poll(_frame(0xAA, 0x05, 0x0D, 0xFF, 0xFF, 0xFF, 0x0F, 0xA0, 0xFF, 0xD5, 0xA1))
    s = d.state
    assert s.is_on is True and s.brightness == 100
    assert s.color_temp_kelvin == 4000 and s.rgb_color is None and s.scene_code is None


def test_update_reads_rgb() -> None:
    s = _poll(_frame(0xAA, 0x05, 0x0D, 0x12, 0x34, 0x56)).state
    assert s.rgb_color == (0x12, 0x34, 0x56) and s.color_temp_kelvin is None


def test_update_reads_scene() -> None:
    assert _poll(_frame(0xAA, 0x05, 0x04, 0x18, 0x00)).state.scene_code == 24


# ── scenes ───────────────────────────────────────────────────────────────────
def test_fixed_scene_catalog() -> None:
    cat = load_scenes("H6188")
    assert {n: s.code for n, s in cat.items()} == {
        "Movie": 4, "Dating": 5, "Reading": 13, "Bright": 23, "Aurora": 24,
        "Candlelight": 9, "Energetic": 16, "Romantic": 7, "Gradient": 17,
    }
    assert all(s.param is None for s in cat.values())


def test_set_scene_by_name_activates_without_upload() -> None:
    d = _dev()
    asyncio.run(d.set_scene_by_name("Candlelight"))
    assert [f[:5].hex() for f in _sent(d)] == ["3305040900"]
    d._conn.send_unacked.assert_not_called()  # type: ignore[attr-defined]
    assert d.state.scene_code == 9


def test_fetch_scenes_never_overwrites_app_defined_catalog(capsys: Any) -> None:
    path = Path(__file__).resolve().parent.parent / "tools" / "fetch_scenes.py"
    spec = importlib.util.spec_from_file_location("_tool_fetch_scenes_h6188", path)
    assert spec and spec.loader
    fs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fs)
    assert "H6188" not in fs.scene_capable_skus()

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("fetch must not be called for H6188")

    fs.fetch = boom
    assert fs.main(["H6188"]) == 0
    assert "skipped" in capsys.readouterr().out


# ── field-test runner, H6188 plan (offline: mocked discovery + device) ───────
def _field_test(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent / "tools"))
    return importlib.import_module("field_test.runner")


def test_field_test_runs_end_to_end_and_restores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ft = _field_test(monkeypatch)

    # initial: on, brightness raw 136 (50 %), RGB (1, 2, 3)
    d = _dev({0x01: [_frame(0xAA, 0x01, 0x01)], 0x04: [_frame(0xAA, 0x04, 136)],
              0x05: [_frame(0xAA, 0x05, 0x0D, 1, 2, 3)]})
    d.connect = AsyncMock()  # type: ignore[method-assign]
    d.stop = AsyncMock()  # type: ignore[method-assign]
    ble = ble_device("AA:BB:CC:DD:EE:88", "ihoment_H6188_1A2B")

    class _Adv:
        def __init__(self) -> None:
            self.local_name = "ihoment_H6188_1A2B"
            self.manufacturer_data: dict[int, bytes] = {}
            self.rssi = -60

    async def fake_discover(_address: object, _sku: object) -> tuple[Any, Any, list[dict[str, Any]], str]:
        return ble, _Adv(), [{"name": "ihoment_H6188_1A2B", "identified_sku": "H6188"}], "H6188"

    monkeypatch.setattr(ft, "discover", fake_discover)
    monkeypatch.setattr("govee_ble_local.create_device", lambda *_a, **_k: d)
    args = ft.parse_args(["--no-prompt", "--settle", "0", "--unattended"])
    assert asyncio.run(ft.run(args, tmp_path, "T")) == 0
    report_path = tmp_path / "h6188-report-T.json"

    import json
    rep = json.loads(report_path.read_text())
    assert rep["result"] == "completed" and rep["restore"] == "ok"
    assert rep["plan"] == {"sku": "H6188", "title": "H6188 RGB strip"}
    assert rep["initial_state"]["brightness"] == 50 and rep["initial_state"]["rgb_color"] == [1, 2, 3]
    names = [r["step"] for r in rep["steps"]]
    assert len(names) == 2 + 3 + 3 + 3 + 1 + 9 and names[0] == "power_off"
    assert all(r["sent"] == "ok" and r["observed"] is None for r in rep["steps"])
    # the steps really hit the wire, and the restore re-applied brightness then colour
    assert _sent(d)[-2][:3].hex() == "330488" and _sent(d)[-1][:6].hex() == "33050d010203"
    # nothing identifying is written: the address is masked to its vendor prefix
    text = report_path.read_text()
    assert "AA:BB:CC:DD:EE:88" not in text and rep["device"]["address"] == "AA:BB:CC:xx:xx:xx"


@pytest.mark.parametrize(("raw", "pct"), [(0, 1), (17, 1), (20, 1), (136, 50), (0xFF, 100)])
def test_a_reported_raw_brightness_reads_as_the_screen_shows_it(raw: int, pct: int) -> None:
    # ViewBrightness.b: calculatePercent of the byte clamped to [20, 254], so never 0 (WAS: 0 below 18).
    d = _dev({0x04: [_frame(0xAA, 0x04, raw)]})
    asyncio.run(d._read_polled())
    assert d.state.brightness == pct
