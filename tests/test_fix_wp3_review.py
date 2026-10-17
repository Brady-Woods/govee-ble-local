"""Regression tests for the second review round of the device lane (WP3-045, WP3-048, WP3-049,
WP3-069, WP3-081).

Offline and deterministic: every connection method is mocked (writes and reads recorded in one
ordered log, reads scripted per command byte, an unscripted read gets no reply), so nothing reaches
a real transport.
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device
from fake_ble import frame as _frame

from govee_ble_local.devices.device import Device
from govee_ble_local.devices.profile import DeviceProfile, profile_for
from govee_ble_local.exceptions import GoveeBleTimeout
from govee_ble_local.identify import parse_broadcast_onoff
from govee_ble_local.models import Encryption
from govee_ble_local.scenes import load_scenes
from govee_ble_local.transport.connection import GoveeConnection
from govee_ble_local.wire import build

_ADDR = "AA:BB:CC:DD:EE:3D"
Replies = dict[int, list[bytes] | Callable[[bytes], list[bytes]]]
Log = list[tuple[str, bytes]]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _profile(sku: str, **changes: Any) -> DeviceProfile:
    base = profile_for(sku)
    assert base is not None
    return dataclasses.replace(base, **changes) if changes else base


def _dev(sku: str, replies: Replies | None = None, **changes: Any) -> tuple[Device, Log]:
    """A Device on the SKU's profile (``changes`` applied) with every connection method mocked; the
    log records each write ("send"), scene-upload frame ("upload") and read ("query") in order."""
    d = Device(_profile(sku, **changes), ble_device(_ADDR, sku), sku=sku)
    log: Log = []
    table = replies or {}

    async def send(frame: bytes, **_k: object) -> None:
        log.append(("send", frame))

    async def send_many(frames: list[bytes]) -> None:
        log.extend(("send", f) for f in frames)

    async def send_unacked(frames: list[bytes], **_k: object) -> None:
        log.extend(("upload", f) for f in frames)

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        log.append(("query", frame))
        r = table.get(frame[1], [])
        return list(r(frame) if callable(r) else r)

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock(side_effect=send_unacked)  # type: ignore[method-assign]
    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    return d, log


# ── WP3-048: H61A8's aa 05 colour sub-modes follow dreamcolorlightv1 Mode.parseSubMode ─────────
def test_h61a8_mode_parse_is_kelvin_15_unsigned() -> None:
    # 0x15 only, read unsigned (SubModeColorV2.parse: BleUtil.getSignedInt masks each byte).
    assert _profile("H61A8").mode_color_parse == "kelvin_15_unsigned"


@pytest.mark.parametrize("sub_mode", [0x0D, 0x0B])
def test_h61a8_colour_report_other_than_0x15_changes_no_colour(sub_mode: int) -> None:
    # SubModeColor.parse reads only byte 0: no RGB, no Kelvin. WAS: 0x0d was decoded with the bulbs'
    # [r, g, b, kelvin] layout, giving color_temp_kelvin=4000 on a device without colour temperature.
    d, _ = _dev("H61A8", {0x05: [_frame(0xAA, 0x05, sub_mode, 0x10, 0x20, 0x30, 0x0F, 0xA0)]})
    d._state.rgb_color = (9, 9, 9)
    run(d._read_polled())
    assert d.state.rgb_color == (9, 9, 9) and d.state.color_temp_kelvin is None


def test_h61a8_0x15_report_still_carries_its_kelvin() -> None:
    # SubModeColorV2.parse: [gradual][kelvin s16be]; getColorTemKelvin returns it when > 0.
    d, _ = _dev("H61A8", {0x05: [_frame(0xAA, 0x05, 0x15, 0x01, 0x0F, 0xA0)]})
    run(d._read_polled())
    assert d.state.color_temp_kelvin == 4000 and d.state.rgb_color is None


# ── WP3-049: the new DeviceProfile fields keep the 1.3.0 positional signature ───────────────────
_FIELDS_1_3_0 = (
    "skus", "capabilities", "color_scheme", "encryption", "segments", "zones", "scene_versions",
    "scene_dialect", "min_kelvin", "max_kelvin", "requires_secret", "relay", "bar_switch",
    "global_power", "gradual", "readback", "color_readback", "color_readback_per_batch",
    "color_readback_segments", "color_readback_live_ic", "section_count_read", "brightness_max",
    "brightness_range",
)


def test_profile_fields_keep_the_1_3_0_order() -> None:
    fields = dataclasses.fields(DeviceProfile)
    assert tuple(f.name for f in fields[:len(_FIELDS_1_3_0)]) == _FIELDS_1_3_0   # fields()/astuple()
    assert all(f.kw_only for f in fields[len(_FIELDS_1_3_0):])                  # later fields: keyword
    positional = [p.name for p in inspect.signature(DeviceProfile).parameters.values()
                  if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]
    assert tuple(positional) == _FIELDS_1_3_0


def test_positional_profile_keeps_its_1_3_0_meaning() -> None:
    # WAS: relay_on_mask had been inserted after relay, so the 13th positional value (True) became
    # relay_on_mask and bar_switch silently stayed False.
    p = DeviceProfile(("HX",), frozenset(), "h60a6", Encryption.NONE, 0, (), frozenset(), "A",
                      None, None, False, False, True)
    assert p.bar_switch is True and p.relay_on_mask == 0xFF


@pytest.mark.parametrize("name", ["relay_on_mask", "advert_on_mask"])
@pytest.mark.parametrize("value", [True, False, 0, 0x100, 1.0])
def test_masks_reject_bool_and_out_of_range(name: str, value: Any) -> None:
    bad: dict[str, Any] = {name: value}
    with pytest.raises(ValueError, match=f"{name}="):
        DeviceProfile(skus=("HX",), capabilities=frozenset(), **bad)


def test_advert_mask_defaults_to_the_lights_rule() -> None:
    assert DeviceProfile(skus=("HX",), capabilities=frozenset()).advert_on_mask is None


# ── WP3-045: the device-info one-shot is judged on the current poll ──────────────────────────
def test_device_info_survives_a_silent_poll_after_a_responsive_one() -> None:
    poll = {"n": 0}

    def answers(frame: bytes) -> list[bytes]:
        n, cmd = poll["n"], frame[1]
        if n == 1 and cmd == 0x01:
            return [_frame(0xAA, 0x01, 0x01)]
        if n == 1 and cmd == 0x06:
            raise GoveeBleTimeout("aa 06 timed out")
        if n == 3 and cmd == 0x06:
            return [_frame(0xAA, 0x06, *b"1.00.01")]
        return []                                              # poll 2: silent throughout

    d, log = _dev("H6008", {cmd: answers for cmd in (0x01, 0x04, 0x05, 0x06, 0x07)})
    for n in (1, 2):
        poll["n"] = n
        run(d.update())
        assert d._device_info_read is False                    # WAS: True after the silent poll 2
    poll["n"] = 3
    start = len(log)
    run(d.update())
    assert any(kind == "query" and f[1] == 0x06 for kind, f in log[start:])
    assert d.state.firmware_version == "1.00.01" and d._device_info_read is True


def test_device_info_one_shot_is_used_up_by_a_responsive_plug_poll() -> None:
    d, log = _dev("H5083", {0x01: [_frame(0xAA, 0x01, 0x01)]})
    run(d.update())
    assert d._device_info_read is True
    start = len(log)
    run(d.update())
    assert all(f[1] not in (0x06, 0x07) for _, f in log[start:])


# ── WP3-069: plug adverts are decoded per goodsType, as the device-list models do ─────────────
def _adv(switch: int) -> object:
    # header 0x02: BleUtil.parseBleBroadOnOff reads the switch byte only for a header >= 2
    return type("Adv", (), {"manufacturer_data": {0x8802: bytes([0xEC, 0, 0, 0, switch])}})()


@pytest.mark.parametrize(("sku", "switch", "on"), [
    ("H5083", 0x11, True),     # goodsType 43, Model.A: (b & 1) == 1. WAS: off (b != 1)
    ("H5083", 0x10, False),
    ("H5083", 0x02, False),
    ("H5082", 0x02, True),     # goodsType 50/307, Model4Plug2.A: bit 0 or bit 1. WAS: off
    ("H5089", 0x03, True),
    ("H5082", 0x04, False),
    ("H5160", 0x08, True),     # goodsType 90, Model.A: (b & 0xFF) > 0. WAS: off
    ("H5161", 0x00, False),
    ("H6008", 0x11, False),    # lights keep AbsBaseBleModel.A: b == 1
    ("H6008", 0x01, True),
])
def test_plug_advert_on_off_follows_the_app(sku: str, switch: int, on: bool) -> None:
    d, log = _dev(sku)
    assert d.ingest_advertisement(_adv(switch)) is True        # type: ignore[arg-type]
    assert d.state.is_on is on and log == []


def test_parse_broadcast_onoff_mask() -> None:
    mfg = {0x8802: bytes([0xEC, 0, 0, 0, 0x11])}
    assert parse_broadcast_onoff(mfg) is False                 # default: the byte must be 1
    assert parse_broadcast_onoff(mfg, on_mask=0x01) is True
    assert parse_broadcast_onoff(mfg, on_mask=0x02) is False
    assert parse_broadcast_onoff({0x8801: bytes([0xEC, 0, 0, 0, 0x11])}, on_mask=0xFF) is None  # v1


# ── WP3-081: a secret set on the live session goes out before any operation's first frame ─────
@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(GoveeConnection, "is_connected", property(lambda _self: True))


_SECRET = bytes(range(1, 9))


def _locked(sku: str, replies: Replies | None = None) -> tuple[Device, Log]:
    """A custom account-locked profile (the table's locked SKUs are all POWER-only)."""
    d, log = _dev(sku, replies, requires_secret=True)
    d.set_secret(_SECRET)
    return d, log


@pytest.mark.parametrize("op", ["rgb", "brightness", "color_temp", "turn_on"])
def test_unlock_goes_before_any_setter(live: None, op: str) -> None:
    d, log = _locked("H6008")
    if op == "rgb":
        run(d.set_rgb((1, 2, 3)))
    elif op == "brightness":
        run(d.set_brightness(40))
    elif op == "color_temp":
        run(d.set_color_temp(3000))
    else:
        run(d.turn_on(brightness=10, power=False))
    assert log[0] == ("send", build.secret_check(_SECRET)) and len(log) == 2
    run(d.set_brightness(50))
    assert all(f[1] != 0xB2 for _, f in log[1:])              # sent once


def test_unlock_goes_before_a_scene_upload(live: None) -> None:
    d, log = _locked("H6008")
    scene = next((s for s in load_scenes("H6008").values() if d.scene_upload_frames(s)), None)
    assert scene is not None
    run(d.set_scene_by_name(scene.name))
    assert log[0] == ("send", build.secret_check(_SECRET))
    assert log[1][0] == "upload" and log[-1] == ("send", build.scene_activate(scene.code))


def test_unlock_goes_before_a_read(live: None) -> None:
    d, log = _locked("H6008", {0xB1: [_frame(0xAA, 0xB1, 0x01, *_SECRET)]})
    assert run(d.read_secret()) == _SECRET
    assert log == [("send", build.secret_check(_SECRET)), ("query", build.secret_read())]


def test_unlock_goes_before_the_segment_count_read(live: None) -> None:
    d, log = _locked("H6062", {0x41: [_frame(0xAA, 0x41, 0x00, 0x64, 0x0A)]})
    run(d.set_rgb((1, 2, 3)))
    assert log[0] == ("send", build.secret_check(_SECRET))
    assert log[1] == ("query", build.section_count_query()) and log[2][0] == "send"
