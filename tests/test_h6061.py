"""H6061 (Glide Hexa light panels, cubelight split), phase 1: the "h6061" colour scheme.

Whole-device RGB as ceil(n/4) group frames, whole-device CCT as `15 01 <tint> <K> <24-bit mask>`,
the polled read-back with the mode 0x0d [R, G, B, kelvin] report, and the `aa a5` group reads.

All expectations are derived from the decompiled Govee Home 7.7.02 app (see the H6061 profile entry
and the h6061 family in spec/devices.yaml for citations); none are hardware-verified."""
from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device

from govee_ble_local import Capability, Encryption
from govee_ble_local.crypto import checksum_ok
from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import profile_for
from govee_ble_local.exceptions import GoveeBleInvalidValue, GoveeBleNotSupported
from govee_ble_local.wire import build as b
from govee_ble_local.wire import parse
from govee_ble_local.wire.cct import _TINT_BY_KELVIN, tint_for_kelvin


def _frame(*payload: int) -> bytes:
    buf = bytearray(20)
    buf[: len(payload)] = bytes(payload)
    x = 0
    for c in buf[:19]:
        x ^= c
    buf[19] = x
    return bytes(buf)


def _dev(replies: dict[int, list[bytes]] | None = None,
         groups: dict[int, bytes] | None = None) -> Device:
    """H6061 with a mocked connection. ``replies``: frame[1] -> reply frames; ``groups``: the
    `aa a5 <group>` replies by group number."""
    d = make_device(ble_device("AA:BB:CC:DD:EE:61", "H6061"), "H6061")
    assert d is not None
    sent: list[bytes] = []
    queried: list[bytes] = []
    table = replies or {}
    by_group = groups or {}

    async def send(frame: bytes, **_k: object) -> None:
        sent.append(frame)

    async def send_many(frames: list[bytes]) -> None:
        sent.extend(frames)

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        queried.append(frame)
        if frame[1] == 0xA5:
            return [by_group[frame[2]]] if frame[2] in by_group else []
        return list(table.get(frame[1], []))

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    d._sent = sent  # type: ignore[attr-defined]
    d._queried = queried  # type: ignore[attr-defined]
    return d


def _sent(d: Device) -> list[bytes]:
    return d._sent  # type: ignore[attr-defined, no-any-return]


def _queried(d: Device) -> list[bytes]:
    return d._queried  # type: ignore[attr-defined, no-any-return]


def _group_reply(group: int, *records: tuple[int, int, int, int]) -> bytes:
    """`aa a5 <group> [bri, R, G, B] x4` (Controller4ColorInfoByGroup)."""
    return _frame(0xAA, 0xA5, group, *(v for rec in records for v in rec))


# ── profile ──────────────────────────────────────────────────────────────────
def test_profile() -> None:
    p = profile_for("h6061")
    assert p is not None
    assert p.capabilities == {Capability.POWER, Capability.BRIGHTNESS, Capability.RGB,
                              Capability.COLOR_TEMP}          # no SEGMENTS / SCENES in phase 1
    assert p.color_scheme == "h6061" and p.segments == 23 and p.zones == ()
    assert (p.min_kelvin, p.max_kelvin) == (2000, 9000)         # KelvinConfig has no 51 entry -> v0
    assert p.brightness_range is None and p.brightness_max == 100
    assert p.encryption is Encryption.NONE and not p.requires_secret   # 0xB2 only while pairing
    assert p.readback == "polled" and p.scene_versions == frozenset()
    assert p.color_readback == "mechanism_a_direct" and p.color_readback_per_batch == 4
    assert p.color_readback_segments is None and not p.color_readback_live_ic


def test_device_default_panel_count_is_23() -> None:
    d = _dev()
    assert d.segment_count == 23 and d.min_kelvin == 2000 and d.max_kelvin == 9000


# ── RGB: ceil(n/4) group frames (Bytes.m) ────────────────────────────────────
def test_rgb_23_panels_is_six_group_frames_golden() -> None:
    frames = b.color_rgb_frames(0x12, 0x34, 0x56, "h6061", 23)
    colour = "123456"
    assert [f.hex() for f in frames[:5]] == [
        _frame(0x33, 0x05, 0x15, 0x04, g, *bytes.fromhex(colour * 4)).hex() for g in range(1, 6)]
    # the last group carries the remaining 3 colours
    assert frames[5] == _frame(0x33, 0x05, 0x15, 0x04, 6, *bytes.fromhex(colour * 3))
    assert frames[0][:8].hex() == "3305150401123456"
    assert frames[5][:4 + 1 + 9].hex() == "33051504" + "06" + colour * 3
    assert frames[5][14:19] == bytes(5)
    assert all(len(f) == 20 and checksum_ok(f) for f in frames)


@pytest.mark.parametrize(("panels", "colours_per_frame"), [
    (2, [2]), (3, [3]), (4, [4]), (5, [4, 1]), (8, [4, 4]), (10, [4, 4, 2]), (12, [4, 4, 4]),
    (20, [4] * 5), (22, [4] * 5 + [2]), (23, [4] * 5 + [3]),
])
def test_rgb_group_count_and_sizes(panels: int, colours_per_frame: list[int]) -> None:
    frames = b.color_rgb_frames(9, 8, 7, "h6061", panels)
    assert len(frames) == -(-panels // 4) == len(colours_per_frame)
    for i, (f, n) in enumerate(zip(frames, colours_per_frame, strict=True), start=1):
        assert f[:5].hex() == f"33051504{i:02x}"                      # group index is 1-based
        assert f[5:5 + 3 * n] == bytes([9, 8, 7]) * n
        assert f[5 + 3 * n:19] == bytes(14 - 3 * n)                   # nothing after the colours


def test_one_panel_is_written_as_23_like_the_app() -> None:
    # ColorPieceConfig.Config.s0: a one-colour set with piece count 23 is expanded to 23 colours.
    assert b.color_rgb_frames(1, 2, 3, "h6061", 1) == b.color_rgb_frames(1, 2, 3, "h6061", 23)


@pytest.mark.parametrize("panels", [0, 24, 56, -1])
def test_panel_count_outside_1_to_23_is_refused(panels: int) -> None:
    with pytest.raises(GoveeBleInvalidValue, match="panel count"):
        b.color_rgb_frames(1, 2, 3, "h6061", panels)
    with pytest.raises(GoveeBleInvalidValue, match="panel count"):
        b.color_temp(4000, "h6061", panels)


def test_rgb_channels_validated() -> None:
    with pytest.raises(GoveeBleInvalidValue):
        b.color_rgb_frames(256, 0, 0, "h6061", 23)


def test_other_schemes_still_give_one_frame() -> None:
    for scheme in ("h60a6", "h6006", "h6188", "h61a8"):
        assert b.color_rgb_frames(1, 2, 3, scheme, 13) == [b.color_rgb(1, 2, 3, scheme, 13)]


def test_single_frame_rgb_and_segment_builders_refuse_h6061() -> None:
    with pytest.raises(ValueError, match="color_rgb_frames"):
        b.color_rgb(1, 2, 3, "h6061", 23)
    with pytest.raises(ValueError, match="whole-device only"):
        b.segment_rgb(1, 1, 2, 3, "h6061", 23)
    with pytest.raises(ValueError, match="whole-device only"):
        b.segment_color_temp(1, 4000, "h6061", 23)
    with pytest.raises(ValueError, match="whole-device only"):
        b.segment_brightness(1, 50, "h6061", 23)


# ── CCT: 15 01 <tint> <kelvin u2be> <24-bit mask> (Bytes.u) ──────────────────
def test_cct_23_panels_golden() -> None:
    f = b.color_temp(4000, "h6061", 23)
    # tint ffd5a1, kelvin 0x0fa0, mask ff ff 7f
    assert f.hex() == "33051501ffd5a10fa0ffff7f0000000000000079"
    assert f[12:19] == bytes(7) and checksum_ok(f)


def test_cct_mask_is_first_n_bits_lsb_first_in_three_bytes() -> None:
    masks = {23: "ffff7f", 24 - 1: "ffff7f", 10: "ff0300", 16: "ffff00", 17: "ffff01",
             12: "ff0f00", 4: "0f0000", 2: "030000"}
    for panels, mask in masks.items():
        f = b.color_temp(2700, "h6061", panels)
        assert f[4:7] == bytes(tint_for_kelvin(2700)) and f[7:9].hex() == "0a8c"
        assert f[9:12].hex() == mask, panels
        assert f[12:19] == bytes(7)                       # three mask bytes, never two or four


@pytest.mark.parametrize("kelvin", range(2000, 9001, 100))
def test_cct_carries_the_apps_tint_and_kelvin_for_every_step(kelvin: int) -> None:
    f = b.color_temp(kelvin, "h6061", 23)
    assert f[:4].hex() == "33051501"
    assert f[4:7] == bytes(tint_for_kelvin(kelvin)) and f[4:7] != bytes(3)
    assert f[7] << 8 | f[8] == kelvin


def test_tint_to_kelvin_round_trip_is_exact_in_the_device_range() -> None:
    # Config.Zt recovers the Kelvin from the tint (Constant.getColorTemKelvin). That only equals
    # the requested Kelvin if no two Kelvin steps share a tint colour.
    tints = [_TINT_BY_KELVIN[k] for k in range(2000, 9001, 100)]
    assert len(tints) == len(set(tints)) == 71


def test_cct_is_not_the_white_slot_form() -> None:
    # h60a6 sends `15 01 ff ff ff <K> <tint> <mask>`; the cube panels put the tint in the colour slot.
    assert b.color_temp(4000, "h6061", 23)[4:7] != bytes([0xFF, 0xFF, 0xFF])
    assert b.color_temp(4000, "h60a6", 13)[4:7] == bytes([0xFF, 0xFF, 0xFF])


# ── Device: writes ───────────────────────────────────────────────────────────
def test_set_rgb_sends_the_group_frames_and_updates_state() -> None:
    d = _dev()
    asyncio.run(d.set_rgb((1, 2, 3)))
    f = _sent(d)
    assert len(f) == 6 and [x[4] for x in f] == [1, 2, 3, 4, 5, 6]
    assert all(x[:4].hex() == "33051504" for x in f)
    assert f[5][5:14] == bytes([1, 2, 3] * 3) and f[5][14:19] == bytes(5)
    d._conn.send_many.assert_awaited_once()   # type: ignore[attr-defined]
    assert d.state.rgb_color == (1, 2, 3) and d.state.color_temp_kelvin is None


def test_set_color_temp_sends_one_frame_and_rounds_to_100k() -> None:
    d = _dev()
    asyncio.run(d.set_color_temp(2750))
    assert [x.hex() for x in _sent(d)] == [b.color_temp(2800, "h6061", 23).hex()]
    assert d.state.color_temp_kelvin == 2800 and d.state.rgb_color is None


@pytest.mark.parametrize("kelvin", [1999, 1500, 9001, 10000])
def test_set_color_temp_range_enforced(kelvin: int) -> None:
    d = _dev()
    with pytest.raises(GoveeBleInvalidValue):
        asyncio.run(d.set_color_temp(kelvin))
    assert _sent(d) == []


def test_power_and_brightness_are_plain_percent_frames() -> None:
    d = _dev()
    asyncio.run(d.set_power(True))
    asyncio.run(d.set_power(False))
    asyncio.run(d.set_brightness(100))
    asyncio.run(d.set_brightness(1))
    asyncio.run(d.set_brightness(0))                       # clamped to 1, never sent as 0
    assert [f[:3].hex() for f in _sent(d)] == [
        "330101", "330100", "330464", "330401", "330401"]
    assert d.state.brightness == 1


def test_turn_on_batches_power_brightness_then_group_frames() -> None:
    d = _dev()
    asyncio.run(d.turn_on(brightness=50, rgb=(10, 20, 30)))
    f = _sent(d)
    assert f[0][:3].hex() == "330101" and f[1][:3].hex() == "330432"
    assert [x[:5].hex() for x in f[2:]] == [f"33051504{g:02x}" for g in range(1, 7)]
    assert len(f) == 8


def test_turn_on_with_kelvin_sends_the_cct_frame() -> None:
    d = _dev()
    asyncio.run(d.turn_on(kelvin=6500))
    f = _sent(d)
    assert [x[:3].hex() for x in f] == ["330101", "330515"]
    assert f[1] == b.color_temp(6500, "h6061", 23)
    assert f[1][4:7].hex() == "fff9fb" and f[1][7:9].hex() == "1964"
    assert d.state.color_temp_kelvin == 6500


def test_segments_and_scenes_not_supported_in_phase_1() -> None:
    d = _dev()
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(d.set_segment_rgb([0], (1, 2, 3)))
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(d.set_scene(1))
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(d.set_gradual(True))
    assert _sent(d) == []


# ── panel-count override (Device.set_segment_count) ──────────────────────────
def test_panel_count_override_changes_groups_and_mask() -> None:
    d = _dev()
    d.set_segment_count(10)                                 # a layout of 10 panels
    asyncio.run(d.set_rgb((1, 2, 3)))
    assert [x[4] for x in _sent(d)] == [1, 2, 3] and _sent(d)[2][5:11] == bytes([1, 2, 3] * 2)
    assert _sent(d)[2][11:19] == bytes(8)
    asyncio.run(d.set_color_temp(4000))
    assert _sent(d)[3][9:12].hex() == "ff0300"
    d.set_segment_count(None)
    assert d.segment_count == 23


def test_panel_count_above_23_is_refused_at_write_time() -> None:
    d = _dev()
    d.set_segment_count(24)
    with pytest.raises(GoveeBleInvalidValue, match="panel count"):
        asyncio.run(d.set_rgb((1, 2, 3)))
    with pytest.raises(GoveeBleInvalidValue, match="panel count"):
        asyncio.run(d.set_color_temp(4000))
    assert _sent(d) == []


# ── read-back ────────────────────────────────────────────────────────────────
def _poll(mode_reply: bytes, groups: dict[int, bytes] | None = None) -> Device:
    d = _dev({
        0x01: [_frame(0xAA, 0x01, 0x01)],
        0x04: [_frame(0xAA, 0x04, 0x37)],
        0x05: [mode_reply],
    }, groups)
    asyncio.run(d.update())
    return d


def test_update_reads_rgb_from_the_0x0d_report() -> None:
    d = _poll(_frame(0xAA, 0x05, 0x0D, 0x12, 0x34, 0x56, 0x00, 0x00))
    s = d.state
    assert s.is_on is True and s.brightness == 55 and s.optimistic is False
    assert s.rgb_color == (0x12, 0x34, 0x56) and s.color_temp_kelvin is None and s.scene_code is None


def test_update_reads_cct_from_the_0x0d_report() -> None:
    # The app writes 0x15 but the device reports 0x0d [R, G, B, kelvin u2be]; kelvin > 0 = CCT.
    d = _poll(_frame(0xAA, 0x05, 0x0D, 0xFF, 0xD5, 0xA1, 0x0F, 0xA0))
    assert d.state.color_temp_kelvin == 4000 and d.state.rgb_color is None


def test_the_0x0d_report_is_the_bulb_layout_not_the_rgblight_one() -> None:
    cct = _frame(0xAA, 0x05, 0x0D, 0x10, 0x20, 0x30, 0x0A, 0x8C)
    assert parse.parse_mode_0d(cct) == (None, 2700)


def test_update_queries_power_brightness_mode_then_every_group() -> None:
    d = _poll(_frame(0xAA, 0x05, 0x0D, 1, 2, 3, 0, 0))
    q = [f[:3].hex() for f in _queried(d) if f[1] in (0x01, 0x04, 0x05, 0xA5)]
    assert q == ["aa0101", "aa0401", "aa0501"] + [f"aaa5{g:02x}" for g in range(1, 7)]


def test_update_reads_panel_colours_from_the_group_replies() -> None:
    groups = {g: _group_reply(g, *[(100, g, g, g)] * 4) for g in range(1, 6)}
    # group 6 holds panels 21..23: three real records, the fourth slot is not a panel
    groups[6] = _group_reply(6, (100, 6, 6, 6), (100, 6, 6, 6), (100, 6, 6, 6), (50, 9, 9, 9))
    d = _poll(_frame(0xAA, 0x05, 0x0D, 1, 2, 3, 0, 0), groups)
    segs = d.state.segments
    assert [s.index for s in segs] == list(range(23))                       # 23 panels, not 24
    assert segs[0].rgb == (1, 1, 1) and segs[0].brightness == 100
    assert segs[19].rgb == (5, 5, 5) and segs[20].rgb == (6, 6, 6) and segs[22].rgb == (6, 6, 6)


def test_group_reads_follow_the_panel_count_override() -> None:
    d = _dev({0x05: [_frame(0xAA, 0x05, 0x0D, 1, 2, 3, 0, 0)]},
             {1: _group_reply(1, *[(100, 1, 2, 3)] * 4), 2: _group_reply(2, *[(100, 4, 5, 6)] * 4),
              3: _group_reply(3, (100, 7, 8, 9), (100, 7, 8, 9), (0, 0, 0, 0), (0, 0, 0, 0))})
    d.set_segment_count(10)
    asyncio.run(d.update())
    assert [f[2] for f in _queried(d) if f[1] == 0xA5] == [1, 2, 3]
    assert len(d.state.segments) == 10 and d.state.segments[9].rgb == (7, 8, 9)


def test_update_survives_silent_group_reads() -> None:
    d = _poll(_frame(0xAA, 0x05, 0x0D, 1, 2, 3, 0, 0))
    assert d.state.segments == [] and d.state.rgb_color == (1, 2, 3)


# ── field-test plan (offline: fake discovery + device) ───────────────────────
def test_field_test_plan_is_registered_and_its_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent / "tools"))
    ft = importlib.import_module("field_test")
    plan = ft.PLANS["H6061"]
    assert plan.title == "H6061 Glide Hexa panels" and "segments" in plan.snapshot_keys
    d = _dev()
    steps = plan.build_steps(ft.PlanContext(d, "H6061", {}))
    assert [s.name for s in steps] == [
        "power_off", "power_on", "rgb_red", "rgb_green", "rgb_blue",
        "brightness_100", "brightness_1", "brightness_50",
        "color_temp_2000", "color_temp_4000", "color_temp_6500", "color_temp_9000"]
    assert all(s.question and s.expect is not None for s in steps)


def test_field_test_runs_end_to_end_and_restores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent / "tools"))
    ft = importlib.import_module("field_test.runner")

    # initial: on, brightness 50 %, RGB (1, 2, 3); the group replies are not scripted
    d = _dev({0x01: [_frame(0xAA, 0x01, 0x01)], 0x04: [_frame(0xAA, 0x04, 50)],
              0x05: [_frame(0xAA, 0x05, 0x0D, 1, 2, 3, 0, 0)]})
    d.connect = AsyncMock()  # type: ignore[method-assign]
    d.stop = AsyncMock()  # type: ignore[method-assign]
    ble = ble_device("AA:BB:CC:DD:EE:61", "ihoment_H6061_1A2B")

    class _Adv:
        local_name = "ihoment_H6061_1A2B"
        manufacturer_data: dict[int, bytes] = {}   # noqa: RUF012 - test double
        rssi = -60

    async def fake_discover(_address: object, _sku: object) -> tuple[Any, Any, list[dict[str, Any]], str]:
        return ble, _Adv(), [{"name": "ihoment_H6061_1A2B", "identified_sku": "H6061"}], "H6061"

    monkeypatch.setattr(ft, "discover", fake_discover)
    monkeypatch.setattr("govee_ble_local.create_device", lambda *_a, **_k: d)
    args = ft.parse_args(["--no-prompt", "--settle", "0"])
    assert asyncio.run(ft.run(args, tmp_path, "T")) == 0
    report_path = tmp_path / "h6061-report-T.json"
    rep = json.loads(report_path.read_text())
    assert rep["result"] == "completed" and rep["restore"] == "ok"
    assert rep["plan"] == {"sku": "H6061", "title": "H6061 Glide Hexa panels"}
    assert rep["initial_state"]["brightness"] == 50 and rep["initial_state"]["rgb_color"] == [1, 2, 3]
    assert len(rep["steps"]) == 12 and rep["steps"][0]["step"] == "power_off"
    assert all(r["sent"] == "ok" and r["observed"] is None for r in rep["steps"])
    # the restore re-applied brightness, then the colour as the six group frames
    sent = _sent(d)
    assert sent[-7][:3].hex() == "330432"
    assert [f[:5].hex() for f in sent[-6:]] == [f"33051504{g:02x}" for g in range(1, 7)]
    assert "AA:BB:CC:DD:EE:61" not in report_path.read_text()
    assert rep["device"]["address"] == "AA:BB:CC:xx:xx:xx"
