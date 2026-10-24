"""H6061 (Glide Hexa light panels, cubelight split), phase 1: the "h6061" colour scheme.

Whole-device RGB as one `15 01 R G B 00 00 <mask>` frame (Bytes.l), whole-device CCT as
`15 01 <tint> <K> <mask>` (Bytes.u), the polled read-back with the mode 0x0d [R, G, B, kelvin] report, and
the `aa a5` group reads.

All expectations are derived from the decompiled Govee Home 7.7.02 app (see the H6061 profile entry
and the h6061 family in spec/devices.yaml for citations); none are hardware-verified."""
from __future__ import annotations

import asyncio
import dataclasses
import importlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device, unacked_via

from govee_ble_local import Capability, DeviceState, Encryption
from govee_ble_local.crypto import checksum_ok
from govee_ble_local.devices import _fold
from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import DeviceProfile, profile_for
from govee_ble_local.exceptions import GoveeBleInvalidValue, GoveeBleNotSupported
from govee_ble_local.wire import build as b
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
    d._conn.send_unacked = AsyncMock(side_effect=unacked_via(send_many))  # type: ignore[method-assign]
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


# ── RGB: one 15 01 R G B 00 00 <mask> frame (Bytes.l) ───────────────────────
@pytest.mark.parametrize("panels", [0, 24, 56, -1])
def test_panel_count_outside_1_to_23_is_refused(panels: int) -> None:
    with pytest.raises(GoveeBleInvalidValue, match="panel count"):
        b.color_rgb_frames(1, 2, 3, "h6061", panels)
    with pytest.raises(GoveeBleInvalidValue, match="panel count"):
        b.color_temp(4000, "h6061", panels)


def test_rgb_channels_validated() -> None:
    with pytest.raises(GoveeBleInvalidValue):
        b.color_rgb_frames(256, 0, 0, "h6061", 23)


def test_every_scheme_gives_one_rgb_frame() -> None:
    for scheme in ("h60a6", "h6006", "h6188", "h61a8", "h6061"):
        assert b.color_rgb_frames(1, 2, 3, scheme, 13) == [b.color_rgb(1, 2, 3, scheme, 13)]


# ── CCT: 15 01 <tint> <kelvin u2be> <24-bit mask> (Bytes.u) ──────────────────
def test_cct_23_panels_golden() -> None:
    f = b.color_temp(4000, "h6061", 23)
    # tint ffd5a1, kelvin 0x0fa0, mask ff ff 7f
    assert f.hex() == "33051501ffd5a10fa0ffff7f0000000000000079"
    assert f[12:19] == bytes(7) and checksum_ok(f)


def test_cct_mask_is_first_n_bits_lsb_first_in_three_bytes() -> None:
    # a one-panel layout is written as 23 panels (_h6061_panels: ColorPieceConfig.Config.s0)
    masks = {23: "ffff7f", 22: "ffff3f", 1: "ffff7f", 10: "ff0300", 16: "ffff00", 17: "ffff01",
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
def test_set_rgb_sends_one_frame_and_updates_state() -> None:
    d = _dev()
    asyncio.run(d.set_rgb((1, 2, 3)))
    assert [x[:12].hex() for x in _sent(d)] == ["33051501010203" + "0000" + "ffff7f"]
    d._conn.send_many.assert_awaited_once()   # type: ignore[attr-defined]
    assert d.state.rgb_color == (1, 2, 3) and d.state.color_temp_kelvin is None


def test_set_color_temp_sends_one_frame_and_rounds_to_100k() -> None:
    d = _dev()
    asyncio.run(d.set_color_temp(2750))
    assert [x.hex() for x in _sent(d)] == [b.color_temp(2800, "h6061", 23).hex()]
    assert d.state.color_temp_kelvin == 2800 and d.state.rgb_color is None


@pytest.mark.parametrize(("kelvin", "edge"), [(1999, 2000), (1500, 2000), (9001, 9000), (10000, 9000)])
def test_set_color_temp_clamped_to_the_range(kelvin: int, edge: int) -> None:
    # like the app (KelvinConfig.calibrationKelvin): out of range is clamped, not rejected, and the
    # frame is exactly the one for the range edge
    d = _dev()
    asyncio.run(d.set_color_temp(kelvin))
    ref = _dev()
    asyncio.run(ref.set_color_temp(edge))
    assert _sent(d) == _sent(ref) != [] and d.state.color_temp_kelvin == edge


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


def test_turn_on_batches_power_brightness_then_the_colour() -> None:
    d = _dev()
    asyncio.run(d.turn_on(brightness=50, rgb=(10, 20, 30)))
    assert [x[:3].hex() for x in _sent(d)] == ["330101", "330432", "330515"]
    assert _sent(d)[2] == b.color_rgb(10, 20, 30, "h6061", 23)


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
def test_panel_count_override_changes_the_mask() -> None:
    d = _dev()
    d.set_segment_count(10)                                 # a layout of 10 panels
    asyncio.run(d.set_rgb((1, 2, 3)))
    asyncio.run(d.set_color_temp(4000))
    assert [f[9:12].hex() for f in _sent(d)] == ["ff0300", "ff0300"]
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


def test_the_panel_count_is_the_profiles() -> None:
    # The device's piece count is its row's wire counts.num_segments (the profile's segments; 25 on
    # goodsType 96): the RGB and CCT masks over 25 panels, four bytes.
    d = _dev()
    d.profile = dataclasses.replace(d.profile, segments=25)
    asyncio.run(d.set_rgb((1, 2, 3)))
    asyncio.run(d.set_color_temp(4000))
    assert [f[9:14].hex() for f in _sent(d)] == ["ffffff0100", "ffffff0100"]


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
    import spec_read

    cct = _frame(0xAA, 0x05, 0x0D, 0x10, 0x20, 0x30, 0x0A, 0x8C)
    assert spec_read.mode_color(cct, "rgb_kelvin_0d") == (None, 2700)


# CubeNewInfo4Detail.U (7.7.02 split_h6061) decodes mode bytes 13 (colour), 4, 10 and 19 only and
# logs anything else as "is not support!", so a 0x15 report leaves the app's colour as it was, even
# though the H6061 itself writes 0x15 frames (33 05 15 01 RGB and CCT).
def _h6061() -> DeviceProfile:
    p = profile_for("H6061")
    assert p is not None
    return p


def test_profile_decodes_only_the_0x0d_colour_report() -> None:
    assert _h6061().mode_color_parse == "rgb_kelvin_0d"


@pytest.mark.parametrize("report", [
    _frame(0xAA, 0x05, 0x15, 0x04, 0x01, 0xFF, 0x00, 0x00),        # a colour-stripe group's shape
    _frame(0xAA, 0x05, 0x15, 0x01, 0xFF, 0xAE, 0x54, 0x0A, 0x8C),  # the CCT write's shape
    _frame(0xAA, 0x05, 0x15, 0x01, 0x00, 0x0A, 0x8C),              # the bulbs' 0x15 kelvin layout
    _frame(0xAA, 0x05, 0x0B, 0x00, 0x0A, 0x8C),                    # another colour sub-mode
], ids=["15-04-rgb", "15-01-cct", "15-kelvin", "0b"])
@pytest.mark.parametrize("before", [
    DeviceState(rgb_color=(1, 2, 3)), DeviceState(color_temp_kelvin=2700, last_color_temp_kelvin=2700),
    DeviceState(),
], ids=["rgb", "kelvin", "unknown"])
def test_a_non_0x0d_colour_report_leaves_the_colour_unchanged(
        report: bytes, before: DeviceState) -> None:
    state = dataclasses.replace(before)
    _fold.apply_mode_reply(_h6061(), state, report)
    assert state == before


def test_update_keeps_the_written_colour_through_a_0x15_report() -> None:
    d = _dev({0x01: [_frame(0xAA, 0x01, 0x01)], 0x04: [_frame(0xAA, 0x04, 0x37)],
              0x05: [_frame(0xAA, 0x05, 0x15, 0x04, 0x01, 0xFF, 0x00, 0x00)]})

    async def run() -> None:
        await d.set_rgb((0x12, 0x34, 0x56))
        await d.update()

    asyncio.run(run())
    assert d.state.rgb_color == (0x12, 0x34, 0x56) and d.state.color_temp_kelvin is None
    assert d.state.is_on is True and d.state.brightness == 55   # the rest of the poll still lands


def test_a_0x0d_report_after_an_ignored_0x15_is_still_decoded() -> None:
    state = DeviceState(rgb_color=(1, 2, 3))
    _fold.apply_mode_reply(_h6061(), state, _frame(0xAA, 0x05, 0x15, 0x01, 0xFF, 0xAE, 0x54, 0x0A, 0x8C))
    _fold.apply_mode_reply(_h6061(), state, _frame(0xAA, 0x05, 0x0D, 0xFF, 0xD5, 0xA1, 0x0F, 0xA0))
    assert state.color_temp_kelvin == 4000 and state.rgb_color is None
    _fold.apply_mode_reply(_h6061(), state, _frame(0xAA, 0x05, 0x0D, 0x12, 0x34, 0x56, 0x00, 0x00))
    assert state.rgb_color == (0x12, 0x34, 0x56) and state.color_temp_kelvin is None


def test_a_negative_signed_kelvin_in_the_0x0d_report_is_an_rgb_colour() -> None:
    # The ctor's SubMode4Color strategy reads BleUtil.getSignedShort(bArr[3], bArr[4]) and shows a
    # Kelvin only when it is > 0, so 0x8000.. (negative) keeps the reported RGB colour.
    d = _poll(_frame(0xAA, 0x05, 0x0D, 0x12, 0x34, 0x56, 0x80, 0x00))
    assert d.state.rgb_color == (0x12, 0x34, 0x56) and d.state.color_temp_kelvin is None


def test_update_queries_power_brightness_mode_then_every_group() -> None:
    d = _poll(_frame(0xAA, 0x05, 0x0D, 1, 2, 3, 0, 0))
    q = [f[:3].hex() for f in _queried(d) if f[1] in (0x01, 0x04, 0x05, 0xA5)]
    # aa 01 / aa 04 without a selector byte, as the app sends them
    assert q == ["aa0100", "aa0400", "aa0501"] + [f"aaa5{g:02x}" for g in range(1, 7)]


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
        "color_temp_2000", "color_temp_4000", "color_temp_6500", "color_temp_9000",
        "rgb_pick_equal_to_4000k_tint"]
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
    args = ft.parse_args(["--no-prompt", "--settle", "0", "--unattended"])
    assert asyncio.run(ft.run(args, tmp_path, "T")) == 0
    report_path = tmp_path / "h6061-report-T.json"
    rep = json.loads(report_path.read_text())
    assert rep["result"] == "completed" and rep["restore"] == "ok"
    assert rep["plan"] == {"sku": "H6061", "title": "H6061 Glide Hexa panels"}
    assert rep["initial_state"]["brightness"] == 50 and rep["initial_state"]["rgb_color"] == [1, 2, 3]
    assert len(rep["steps"]) == 13 and rep["steps"][0]["step"] == "power_off"
    assert all(r["sent"] == "ok" and r["observed"] is None for r in rep["steps"])
    # the restore re-applied brightness, then the colour as one frame
    sent = _sent(d)
    assert sent[-2][:3].hex() == "330432" and sent[-1] == b.color_rgb(1, 2, 3, "h6061", 23)
    assert "AA:BB:CC:DD:EE:61" not in report_path.read_text()
    assert rep["device"]["address"] == "AA:BB:CC:xx:xx:xx"


# ── RGB picks that are Kelvin tint colours are sent as CCT, like the app ─────
# ColorModeControllerConfig.k: all panels one colour + KelvinConfig.i(colour, info) (the colour is in
# the Constant.k2 / ColorUtils.e table within [2000, 9000]) + Config.I[51][4] -> Config.Zt -> Bytes.u
# with the PICKED colour as the tint and Constant.getColorTemKelvin(colour) as the Kelvin.
def test_tint_table_has_the_apps_178_colours_two_per_kelvin_step() -> None:
    from govee_ble_local.wire.cct import _KELVIN_BY_TINT, kelvin_for_tint

    assert len(_KELVIN_BY_TINT) == 178
    per_kelvin: dict[int, list[int]] = {}
    for colour, kelvin in _KELVIN_BY_TINT.items():
        per_kelvin.setdefault(kelvin, []).append(colour)
    assert sorted(per_kelvin) == list(range(1000, 10001, 100))
    assert sorted(len(v) for v in per_kelvin.values()).count(2) == 87     # 4 steps have one colour
    for kelvin, colours in per_kelvin.items():
        first = tint_for_kelvin(kelvin)                                     # the slider path's tint
        assert first == ((colours[0] >> 16) & 255, (colours[0] >> 8) & 255, colours[0] & 255)
        assert kelvin_for_tint(first) == kelvin
    assert kelvin_for_tint((255, 255, 255)) is None and kelvin_for_tint((0, 0, 0)) is None


def test_rgb_pick_equal_to_a_tint_colour_is_sent_as_cct_golden() -> None:
    # 0xFFAE54 = the first colour of 2700 K; 0xFFA957 = its second colour (also 2700 K, in the table)
    first = b.color_rgb_frames(0xFF, 0xAE, 0x54, "h6061", 23)
    assert [f.hex() for f in first] == ["33051501ffae540a8cffff7f00000000000000de"]
    assert first == [b.color_temp(2700, "h6061", 23)]
    alt = b.color_rgb_frames(0xFF, 0xA9, 0x57, "h6061", 23)
    assert len(alt) == 1 and alt[0][:12].hex() == "33051501ffa9570a8cffff7f"     # picked colour kept
    assert alt[0] != first[0] and checksum_ok(alt[0])


def test_kelvin_range_is_2000_to_9000_inclusive() -> None:
    def kelvin(rgb: tuple[int, int, int], span: tuple[int, int] | None = None) -> str:
        frames = b.color_rgb_frames(*rgb, "h6061", 23, span)
        assert len(frames) == 1
        return frames[0][7:9].hex()                                        # 0000: the RGB frame

    assert kelvin((0xFF, 0x87, 0x00)) == "0000"    # 1900 K: below the range
    assert kelvin((0xFF, 0x8D, 0x0B)) == "07d0"    # 2000 K
    assert kelvin((0xD9, 0xE1, 0xFF)) == "2328"    # 9000 K
    assert kelvin((0xD8, 0xE0, 0xFF)) == "0000"    # 9100 K: above the range
    assert kelvin((0xFF, 0x38, 0x00)) == "0000"    # 1000 K (second colour)
    # an explicit (narrower) range is honoured
    assert kelvin((0xFF, 0xAE, 0x54), (3000, 9000)) == "0000"
    assert b.rgb_as_kelvin(0xFF, 0xAE, 0x54, "h6061", (2000, 2700)) == 2700


def test_near_misses_and_other_colours_stay_rgb() -> None:
    for rgb in ((0xFF, 0xAE, 0x55), (0xFE, 0xAE, 0x54), (255, 255, 255), (255, 0, 0), (0, 0, 0)):
        assert b.color_rgb_frames(*rgb, "h6061", 23) == [b.color_rgb(*rgb, "h6061", 23)], rgb


def test_tint_pick_uses_the_panel_count_mask() -> None:
    d = _dev()
    d.set_segment_count(10)
    asyncio.run(d.set_rgb((0xFF, 0xAE, 0x54)))
    assert len(_sent(d)) == 1 and _sent(d)[0][9:12].hex() == "ff0300"
    assert _sent(d)[0][:9].hex() == "33051501ffae540a8c"


def test_set_rgb_tint_pick_reports_kelvin_state() -> None:
    d = _dev()
    asyncio.run(d.set_rgb((0xFF, 0xAE, 0x54)))
    # One write of one frame (set_rgb always hands its frames to send_many, which acks each).
    d._conn.send_many.assert_awaited_once()     # type: ignore[attr-defined]
    assert len(d._conn.send_many.await_args.args[0]) == 1  # type: ignore[attr-defined]
    assert d.state.color_temp_kelvin == 2700 and d.state.rgb_color is None
    asyncio.run(d.set_rgb((1, 2, 3)))
    assert d.state.rgb_color == (1, 2, 3) and d.state.color_temp_kelvin is None


def test_turn_on_with_a_tint_rgb_sends_the_cct_frame() -> None:
    d = _dev()
    asyncio.run(d.turn_on(rgb=(0xD9, 0xE1, 0xFF)))
    assert [f[:3].hex() for f in _sent(d)] == ["330101", "330515"]
    assert _sent(d)[1][:9].hex() == "33051501d9e1ff232" + "8"
    assert d.state.color_temp_kelvin == 9000 and d.state.rgb_color is None


def test_h6061_default_range_is_its_own_and_other_schemes_need_a_range() -> None:
    # the other schemes take the SKU's range from the profile (tests/test_rgb_pick_as_cct.py); only
    # h6061 has a built-in default, and it is unchanged: [2000, 9000]
    assert b.H6061_KELVIN_RANGE == (2000, 9000)
    assert b.rgb_as_kelvin(0xFF, 0xAE, 0x54, "h6061") == 2700
    assert b.rgb_as_kelvin(0xFF, 0xAE, 0x54, "h60a6") is None
    assert b.color_rgb_frames(0xFF, 0xAE, 0x54, "h6006", 13) == [b.color_rgb(0xFF, 0xAE, 0x54, "h6006", 13)]
