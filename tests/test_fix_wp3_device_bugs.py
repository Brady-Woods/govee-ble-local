"""Regression tests for the device-layer correctness fixes (review findings WP3-005 .. WP3-081).

Offline and deterministic: every connection method is mocked (writes recorded, reads scripted per
command byte, anything unscripted gets no reply), so nothing reaches a real transport.
"""
from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device, frames_of, status_burst
from fake_ble import frame as _frame

from govee_ble_local.devices import _readback
from govee_ble_local.devices import device as device_mod
from govee_ble_local.devices.device import Device, _local_tz, make_device
from govee_ble_local.devices.profile import DeviceProfile, profile_for
from govee_ble_local.exceptions import GoveeBleError, GoveeBleInvalidValue, GoveeBleNotSupported
from govee_ble_local.models import Capability
from govee_ble_local.scenes import load_scenes
from govee_ble_local.transport.connection import GoveeConnection
from govee_ble_local.wire import build, parse

Replies = dict[int, list[bytes] | Callable[[bytes], list[bytes]]]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _dev(sku: str, replies: Replies | None = None, **profile_overrides: Any) -> Device:
    """Device with a mocked connection. ``replies`` maps the request's command byte (frame[1]) to
    reply frames, or to a function of the request frame returning them."""
    if profile_overrides:
        # A changed profile is a new Device: the profile is read-only after construction.
        profile = profile_for(sku)
        assert profile is not None
        d: Device | None = Device(dataclasses.replace(profile, **profile_overrides),
                                  ble_device("AA:BB:CC:DD:EE:3A", sku), sku=sku)
    else:
        d = make_device(ble_device("AA:BB:CC:DD:EE:3A", sku), sku)
    assert d is not None
    sent: list[bytes] = []
    queried: list[bytes] = []
    unacked: list[bytes] = []
    table = replies or {}

    async def send(frame: bytes, **_k: object) -> None:
        sent.append(frame)

    async def send_many(frames: list[bytes]) -> None:
        sent.extend(frames)

    async def send_unacked(frames: Any, **_k: object) -> None:
        unacked.extend(frames_of(frames))

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        queried.append(frame)
        r = table.get(frame[1], [])
        return list(r(frame) if callable(r) else r)

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock(side_effect=send_unacked)  # type: ignore[method-assign]
    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    d._sent = sent  # type: ignore[attr-defined]
    d._queried = queried  # type: ignore[attr-defined]
    d._unacked = unacked  # type: ignore[attr-defined]
    return d


def _sent(d: Device) -> list[bytes]:
    return list(d._sent)  # type: ignore[attr-defined]


def _queried(d: Device) -> list[bytes]:
    return list(d._queried)  # type: ignore[attr-defined]


def _unacked(d: Device) -> list[bytes]:
    return list(d._unacked)  # type: ignore[attr-defined]


def _adv(on: int, header: int = 0x02) -> Any:
    """A bleak-style advert whose Govee manufacturer data carries on/off (header >= 2)."""
    return type("Adv", (), {"manufacturer_data": {0x8800 | header: bytes([0xEC, 0, 0, 0, on])}})()


# ── WP3-005: plug sync-time payload from the wall clock and the host timezone ──────────────
class _Tm:
    def __init__(self, off: int | None) -> None:
        self.tm_gmtoff = off


@pytest.mark.parametrize(
    ("off", "tz"),
    [(-12600, (-3, -30)), (19800, (5, 30)), (0, (0, 0)), (None, (0, 0)),
     (-3600 * 5, (-5, 0)), (3600 * 13 + 45 * 60, (13, 45))],
)
def test_local_tz_signs_hours_and_minutes(monkeypatch: pytest.MonkeyPatch, off: int | None,
                                          tz: tuple[int, int]) -> None:
    monkeypatch.setattr(device_mod.time, "localtime", lambda _ts=None: _Tm(off))
    assert _local_tz(0x6A49901A) == tz


@pytest.mark.parametrize(("off", "tz"), [(-12600, (-3, -30)), (19800, (5, 30))])
def test_relay_sync_time_payload(monkeypatch: pytest.MonkeyPatch, off: int, tz: tuple[int, int]) -> None:
    monkeypatch.setattr(device_mod.time, "time", lambda: 0x6A49901A)
    monkeypatch.setattr(device_mod.time, "localtime", lambda _ts=None: _Tm(off))
    d = _dev("H5080")
    assert d._session_frames() == [build.plug_sync_time(0x6A49901A, *tz)]
    light = _dev("H6008", light_effect_stop=True)._conn   # the provider the session runs, without ack waits
    assert light._session_frames is not None and light._session_frames() == [build.light_effect_stop()]
    assert not light._session_ack and d._conn._session_ack   # a plug waits for its time sync's ack


# ── WP3-017: register_callback's unsubscribe ──────────────────────────────────────────────
def test_register_callback_unsubscribe_is_idempotent() -> None:
    d = _dev("H6008")
    seen: list[object] = []
    other: list[object] = []
    unsub = d.register_callback(seen.append)
    d.register_callback(other.append)
    run(d.set_power(True))
    assert len(seen) == 1
    unsub()
    unsub()                                      # a second call is a no-op, not a ValueError
    run(d.set_power(False))
    assert len(seen) == 1 and len(other) == 2    # only that callback was removed


# ── WP3-019: secret type ──────────────────────────────────────────────────────────────────
def test_str_secret_is_rejected_up_front_without_echoing_it() -> None:
    d = _dev("H5083")
    with pytest.raises(GoveeBleInvalidValue, match="str") as ei:
        d.set_secret("abcdefgh")  # type: ignore[arg-type]
    assert "abcdefgh" not in str(ei.value)
    with pytest.raises(GoveeBleInvalidValue):
        Device(d.profile, ble_device("AA:BB:CC:DD:EE:3B", "H5083"),
               secret="0011223344556677")  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", [bytearray(b"12345678"), memoryview(b"12345678")])
def test_bytes_like_secret_is_normalised_to_bytes(raw: Any) -> None:
    d = _dev("H5083")
    d.set_secret(raw)
    assert d._secret == b"12345678" and type(d._secret) is bytes
    assert d._unlock_frames() == [build.secret_check(b"12345678")]


# ── WP3-021: an advertised power change keeps the zones consistent ────────────────────────
def test_h6047_advert_off_marks_both_bars_off() -> None:
    d = _dev("H6047")
    d._state.is_on, d._state.zone_power = True, {0: True, 1: True}     # last poll: both bars on
    assert d.ingest_advertisement(_adv(0)) is True
    assert d.state.zone_power == {0: False, 1: False}
    run(d.set_zone_power("left", True))
    assert _queried(d) == [] and _sent(d)[0][:4].hex() == "33360100"   # the right bar stays off


def test_h6047_advert_on_with_both_bars_off_forgets_them() -> None:
    d = _dev("H6047", {0x36: [_frame(0xAA, 0x36, 0x01, 0x01)]})
    d._state.is_on, d._state.zone_power = False, {0: False, 1: False}
    assert d.ingest_advertisement(_adv(1)) is True
    assert d.state.zone_power == {} and d.zone_is_on("left") is None
    run(d.set_zone_power("right", False))
    assert [q[:2].hex() for q in _queried(d)] == ["aa36"]              # re-read, not assumed
    assert _sent(d)[0][:4].hex() == "33360100"                         # the lit left bar stays on


def test_h60a6_advert_off_clears_both_zones() -> None:
    d = _dev("H60A6")
    d._state.is_on, d._state.zone_power = True, {0: True, 1: True}
    d.ingest_advertisement(_adv(0))
    assert d.zone_is_on("main") is False and d.zone_is_on("background") is False


def test_advert_on_keeps_known_lit_zones() -> None:
    d = _dev("H60A6")
    d._state.is_on, d._state.zone_power = False, {0: True, 1: False}   # inconsistent but known
    d.ingest_advertisement(_adv(1))
    assert d.state.zone_power == {0: True, 1: False}


def test_unzoned_advert_leaves_zone_power_alone() -> None:
    d = _dev("H6008")
    d.ingest_advertisement(_adv(1))
    assert d.state.is_on is True and d.state.zone_power == {}


# ── WP3-023: device-reported brightness is clamped to 0..100 ──────────────────────────────
def test_percent_brightness_from_the_device_is_clamped() -> None:
    d = _dev("H6008", {0x04: [_frame(0xAA, 0x04, 0x96)]})
    assert d._brightness_pct(0xFF) == 100
    run(d._read_polled())
    assert d.state.brightness == 100
    scaled = _dev("H6008", brightness_max=50)
    assert scaled._brightness_pct(0xFF) == 100 and scaled._brightness_pct(25) == 50


# ── WP3-026 / WP3-030: rgb shape checked before any I/O ───────────────────────────────────
@pytest.mark.parametrize("bad", [(1, 2), (1, 2, 3, 4), "abc", None, (1, 2, 256), (1, True, 3), (1.0, 2, 3)])
def test_bad_rgb_is_invalid_value_before_any_io(bad: Any) -> None:
    for call in (
        lambda d: d.set_rgb(bad),
        lambda d: d.turn_on(rgb=bad) if bad is not None else d.set_rgb(bad),
        lambda d: d.set_segment_rgb([0], bad),
        lambda d: d.set_zone_rgb("left", bad),
    ):
        d = _dev("H6062", zones=profile_for("H6047").zones)  # type: ignore[union-attr]
        with pytest.raises(GoveeBleInvalidValue):
            run(call(d))
        assert _sent(d) == [] and _queried(d) == []     # not even the aa 41 segment-count read


def test_bad_turn_on_kelvin_is_invalid_value_before_any_io() -> None:
    d = _dev("H6062")
    with pytest.raises(GoveeBleInvalidValue):
        run(d.turn_on(kelvin=4000.0))  # type: ignore[arg-type]
    assert _sent(d) == [] and _queried(d) == []     # not even the aa 41 segment-count read


def test_rgb_state_is_a_fresh_tuple() -> None:
    d = _dev("H6008")
    pick = [10, 20, 30]
    run(d.turn_on(rgb=pick))  # type: ignore[arg-type]
    assert d.state.rgb_color == (10, 20, 30) and isinstance(d.state.rgb_color, tuple)
    run(d.set_rgb([1, 2, 3]))  # type: ignore[arg-type]
    assert d.state.rgb_color == (1, 2, 3) and isinstance(d.state.rgb_color, tuple)


# ── WP3-029: brightness type ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("sku", ["H6008", "H6188"])
@pytest.mark.parametrize("bad", [50.5, True, "50"])
def test_non_int_brightness_is_invalid_value(sku: str, bad: Any) -> None:
    d = _dev(sku)
    with pytest.raises(GoveeBleInvalidValue):
        run(d.set_brightness(bad))
    with pytest.raises(GoveeBleInvalidValue):
        run(d.turn_on(brightness=bad))
    with pytest.raises(GoveeBleInvalidValue):
        run(d.set_brightness(None))  # type: ignore[arg-type]
    assert _sent(d) == [] and d.state.brightness is None


def test_non_int_segment_brightness_is_invalid_value() -> None:
    d = _dev("H60A6")
    with pytest.raises(GoveeBleInvalidValue):
        run(d.set_segment_brightness([0], 50.5))  # type: ignore[arg-type]
    assert _sent(d) == []


# ── WP3-032 / WP3-046: segment and zone colour writes leave scene mode ────────────────────
@pytest.mark.parametrize(
    "write",
    [
        lambda d: d.set_segment_rgb([0, 1], (255, 0, 0)),
        lambda d: d.set_segment_brightness([0], 40),
        lambda d: d.set_segment_color_temp([0, 1], 4000),
        lambda d: d.set_zone_rgb("background", (255, 0, 0)),
        lambda d: d.set_zone_color_temp("background", 4000),
    ],
)
def test_masked_colour_write_clears_the_active_scene(write: Any) -> None:
    d = _dev("H60A6")
    run(d.set_scene(5))
    assert d.state.scene_code == 5
    run(write(d))
    assert d.state.scene_code is None and d.active_scene is None


def test_h6641_segment_rgb_clears_scene() -> None:
    d = _dev("H6641")
    run(d.set_scene(5))
    run(d.set_segment_rgb([0], (1, 2, 3)))
    assert d.state.scene_code is None


def test_matching_bar_kelvins_drop_the_stale_rgb() -> None:
    d = _dev("H6047")
    run(d.set_rgb((255, 0, 0)))
    run(d.set_zone_color_temp("left", 4000))
    assert d.state.rgb_color == (255, 0, 0) and d.state.color_temp_kelvin is None   # bars differ
    run(d.set_zone_color_temp("right", 4000))
    assert d.state.rgb_color is None and d.state.color_temp_kelvin == 4000


def test_read_back_matching_bar_kelvins_drop_the_stale_rgb() -> None:
    d = _dev("H6047", {0x05: [_frame(0xAA, 0x05, 0x15, 0x01, 0x0F, 0xA0, 0x0F, 0xA0)]})
    run(d.set_rgb((255, 0, 0)))
    run(d._read_polled())
    assert d.state.rgb_color is None and d.state.color_temp_kelvin == 4000


# ── WP3-038: whole-device power and a bar write serialize on the bar lock ─────────────────
async def test_set_power_and_bar_write_do_not_undo_each_other() -> None:
    d = _dev("H6047")
    d._state.zone_power = {0: True, 1: True}
    entered, release = asyncio.Event(), asyncio.Event()
    record = d._conn.send_many

    async def gated_send_many(frames: list[bytes]) -> None:
        entered.set()
        await release.wait()
        await record(frames)

    d._conn.send_many = gated_send_many  # type: ignore[method-assign,assignment]
    off = asyncio.create_task(d.set_power(False))
    await entered.wait()
    left = asyncio.create_task(d.set_zone_power("left", True))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(off, left)
    # power off, then the left bar on: the bar frame is built from the settled (off) state
    assert [f[:4].hex() for f in _sent(d)] == ["33010000", "33360100"]
    assert d.state.zone_power == {0: True, 1: False} and d.state.is_on is True


async def test_scene_and_bar_write_serialize() -> None:
    d = _dev("H6047", {0x36: [_frame(0xAA, 0x36, 0x01, 0x00)]})
    d._state.zone_power = {0: False, 1: False}
    entered, release = asyncio.Event(), asyncio.Event()
    record = d._conn.send

    async def gated_send(frame: bytes, **k: Any) -> None:
        if frame[1] == 0x05:
            entered.set()
            await release.wait()
        await record(frame, **k)

    d._conn.send = gated_send  # type: ignore[method-assign,assignment]
    scene = asyncio.create_task(d.set_scene(3))
    await entered.wait()
    right = asyncio.create_task(d.set_zone_power("right", True))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(scene, right)
    # the scene lit the device and forgot the bars, so the bar write re-read them first
    assert [q[:2].hex() for q in _queried(d)] == ["aa36"]
    assert _sent(d)[-1][:4].hex() == "33360101"


# ── WP3-043: an unknown scene name ────────────────────────────────────────────────────────
def test_unknown_scene_name_raises_typed_error_and_sends_nothing() -> None:
    d = _dev("H6008")
    with pytest.raises(GoveeBleNotSupported, match="unknown scene"):
        run(d.set_scene_by_name("No Such Scene"))
    assert _sent(d) == [] and _unacked(d) == [] and d.state.scene_code is None


@pytest.mark.parametrize(("sku", "is_on", "lead"), [
    ("H6009", None, True), ("H6010", False, True), ("H6009", True, False), ("H6006", None, False)])
def test_h6009_h6010_lead_a_scene_write_with_the_power_on(monkeypatch: pytest.MonkeyPatch, sku: str,
                                                         is_on: bool | None, lead: bool) -> None:
    """ApplyOffNeedOpenConfig (H6009 / H6010 only): the app prepends 33 01 01 to a scene apply, upload
    included, while its stored switch state is off, and reads an unknown one as off."""
    on = [build.switch(True)] if lead else []
    d = _dev(sku)
    d._state.is_on = is_on
    run(d.set_scene(7))
    assert _sent(d) == [*on, build.scene_activate(7)] and d.state.is_on is True
    # By name, with an upload (H6006's catalog: no H6009 / H6010 catalog is bundled yet).
    catalog = load_scenes("H6006")
    monkeypatch.setattr(device_mod, "async_load_scenes", lambda _sku: asyncio.sleep(0, catalog))
    d = _dev(sku)
    d._state.is_on = is_on
    name = next(n for n, s in catalog.items() if d.scene_upload_frames(s))
    order: list[str] = []
    many, unacked = d._conn.send_many.side_effect, d._conn.send_unacked.side_effect

    async def lead_first(frames: list[bytes]) -> None:
        order.append("lead")
        await many(frames)

    async def upload(frames: Any, **k: object) -> None:
        order.append("upload")
        await unacked(frames, **k)

    d._conn.send_many.side_effect = lead_first  # type: ignore[attr-defined]
    d._conn.send_unacked.side_effect = upload  # type: ignore[attr-defined]
    run(d.set_scene_by_name(name))
    assert order == (["lead", "upload"] if lead else ["upload"]) and _unacked(d)
    assert _sent(d) == [*on, build.scene_activate(catalog[name].code)]


def test_set_scene_by_name_loads_the_catalog_off_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = device_mod.async_load_scenes

    async def spy(sku: str) -> Any:
        calls.append(sku)
        return await real(sku)

    monkeypatch.setattr(device_mod, "async_load_scenes", spy)
    d = _dev("H6008")
    name = d.scene_names[0]
    run(d.set_scene_by_name(name))
    assert calls == ["H6008"] and d.state.scene_code == load_scenes("H6008")[name].code


def test_update_warms_the_scene_catalog_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def spy(sku: str) -> dict[str, Any]:
        calls.append(sku)
        return {}

    monkeypatch.setattr(device_mod, "async_load_scenes", spy)
    d = _dev("H6008")
    run(d.update())
    run(d.update())
    assert calls == ["H6008"]
    plug = _dev("H5080")
    run(plug.update())
    assert calls == ["H6008"]                     # no SCENES capability: nothing to load


def test_update_survives_a_corrupt_scene_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(_sku: str) -> dict[str, Any]:
        raise GoveeBleError("scene catalog for H6008 is malformed")

    monkeypatch.setattr(device_mod, "async_load_scenes", broken)
    d = _dev("H6008", {0x01: [_frame(0xAA, 0x01, 0x01)]})
    assert run(d.update()).is_on is True


def test_active_scene_needs_the_scenes_capability() -> None:
    d = _dev("H6008", capabilities=frozenset({Capability.POWER}))
    d._state.scene_code = next(iter(device_mod.scene_names_by_code("H6008")))
    assert d.scene_names == [] and d.active_scene is None


# ── WP3-045: the device-info one-shot and the first-poll line need an answer ──────────────
def test_silent_poll_does_not_consume_the_first_poll_line() -> None:
    d = _dev("H6008")
    run(d.update())
    assert d._polled_once is False and d.state.optimistic is True
    d._conn.query = AsyncMock(return_value=[_frame(0xAA, 0x01, 0x01)])  # type: ignore[method-assign]
    run(d.update())
    assert d._polled_once is True


def test_responsive_device_without_device_info_is_not_reread_every_poll() -> None:
    # The state reads answer, the device-info reads never do (unsupported): attempted once, so a
    # poll doesn't pay five more read timeouts every time.
    d = _dev("H6008", {0x01: [_frame(0xAA, 0x01, 0x01)]})
    run(d.update())
    assert d._device_info_read is True
    n = len(_queried(d))
    run(d.update())
    assert all(q[1] not in (0x06, 0x07) for q in _queried(d)[n:])


# ── WP3-047: mode reports are authoritative for the colour state ──────────────────────────
@pytest.mark.parametrize("sku", ["H6641", "H6062"])
def test_kelvin_zero_report_clears_the_colour_temperature(sku: str) -> None:
    reports = iter([_frame(0xAA, 0x05, 0x15, 0x01, 0x0F, 0xA0), _frame(0xAA, 0x05, 0x15, 0x01, 0x00, 0x00)])
    d = _dev(sku, {0x05: lambda _f: [next(reports)]})
    d.set_segment_count(10)
    run(d._read_polled())
    assert d.state.color_temp_kelvin == 4000
    run(d._read_polled())
    assert d.state.color_temp_kelvin is None


def _mode_burst(*mode: int) -> list[bytes]:
    """An H60A6 0xAC status reply whose only TLV is the 0x05 mode [sub_mode, params]."""
    return status_burst(bytes([0x05, len(mode), *mode]))


def test_h60a6_kelvin_zero_status_mode_report_clears_the_colour_temperature() -> None:
    d = _dev("H60A6", {0x03: _mode_burst(0x15, 0x01, 0x00, 0x00)})
    run(d.set_color_temp(4000))
    run(d._read_status())
    assert d.state.color_temp_kelvin is None


@pytest.mark.parametrize("sku", ["H60A6", "H6047", "H6006", "H6188", "H6641"])
def test_scene_report_clears_the_colours(sku: str) -> None:
    # The H60A6 reads its mode from the status burst's 0x05 TLV; the others from aa 05.
    replies = ({0x03: _mode_burst(0x04, 0x10, 0x00)} if sku == "H60A6"
               else {0x05: [_frame(0xAA, 0x05, 0x04, 0x10, 0x00)]})
    d = _dev(sku, replies)
    run(d.set_color_temp(4000))
    run(d._read_status() if d.profile.readback == "status" else d._read_polled())
    assert d.state.scene_code == 16
    assert d.state.color_temp_kelvin is None and d.state.rgb_color is None
    assert d.state.zone_color_temp_kelvin == {}


# ── WP3-048: colour sub-mode decoding per the SKU's mode parse ────────────────────────────
@pytest.mark.parametrize("sub", [0x0D, 0x0B, 0x02, 0x15])
def test_h6062_colour_submodes_use_the_submode4color_parse(sub: int) -> None:
    # striplight OpInfo4Detail.parseModeValidBytes: 0x15/0x0b/0x02/0x0d -> [gradual][kelvin s16be]
    d = _dev("H6062", {0x05: [_frame(0xAA, 0x05, sub, 0x01, 0x0F, 0xA0)]})
    d.set_segment_count(19)
    run(d.set_rgb((9, 9, 9)))
    run(d._read_polled())
    assert d.state.color_temp_kelvin == 4000 and d.state.rgb_color is None


def test_h6062_0d_report_is_not_read_as_bulb_rgb() -> None:
    d = _dev("H6062", {0x05: [_frame(0xAA, 0x05, 0x0D, 0x01, 0x00, 0x00)]})
    d.set_segment_count(19)
    run(d._read_polled())
    assert d.state.rgb_color is None and d.state.color_temp_kelvin is None


@pytest.mark.parametrize("sku", ["H6641", "H6047"])
def test_0d_report_is_ignored_where_the_app_does_not_parse_it(sku: str) -> None:
    d = _dev(sku, {0x05: [_frame(0xAA, 0x05, 0x0D, 0x01, 0x0F, 0xA0)]})
    run(d.set_rgb((9, 8, 7)))
    run(d._read_polled())
    assert d.state.rgb_color == (9, 8, 7) and d.state.color_temp_kelvin is None


def test_h60a6_0d_report_keeps_the_rgb_kelvin_layout() -> None:
    # h60a1 Info4Detail.Q (used by VM4LightH60A6): 0x0d = [r, g, b, kelvin s16be]
    d = _dev("H60A6", {0x03: _mode_burst(0x0D, 0x10, 0x20, 0x30, 0x00, 0x00)})
    run(d._read_status())
    assert d.state.rgb_color == (0x10, 0x20, 0x30) and d.state.color_temp_kelvin is None
    # The burst is the whole poll: no aa 05 read follows it (the app's connect read).
    assert _queried(d) and all(q[0] == 0xAC for q in _queried(d))


def test_mode_color_parse_is_validated() -> None:
    with pytest.raises(ValueError, match="mode_color_parse"):
        dataclasses.replace(profile_for("H6008"), mode_color_parse="bogus")  # type: ignore[arg-type]


# ── WP3-049: plug relay bits per the app's detail screen ──────────────────────────────────
@pytest.mark.parametrize(
    ("sku", "byte", "on"),
    [
        ("H5080", 0x01, True), ("H5080", 0x10, False), ("H5080", 0x11, True), ("H5080", 0x02, False),
        ("H5085", 0xF0, False),   # H5083: test_h5083_firmware (its relay read against the firmware)
        # gt 50 / 307: relays 0-1, the aa 01 heartbeat rule (onEventHeartV2). WAS: any bit.
        ("H5082", 0x10, False), ("H5082", 0x02, True), ("H5089", 0x80, False), ("H5089", 0x01, True),
        ("H5089", 0x00, False),
        ("H5160", 0x04, True), ("H5160", 0x10, False), ("H5161", 0x08, False), ("H5161", 0x01, True),
    ],
)
def test_plug_relay_bits_follow_the_detail_screen(sku: str, byte: int, on: bool) -> None:
    d = _dev(sku, {0x01: [_frame(0xAA, 0x01, byte)]})
    run(d._read_plug())
    assert d.state.is_on is on and d.state.optimistic is False


def test_plug_profiles_are_split_by_goods_type() -> None:
    masks = {sku: profile_for(sku).relay_on_mask  # type: ignore[union-attr]
             for sku in ("H5080", "H5082", "H5083", "H5085", "H5089", "H5160", "H5161")}
    assert masks == {"H5080": 0x01, "H5083": 0x01, "H5085": 0x01, "H5082": 0x03, "H5089": 0x03,
                     "H5160": 0x07, "H5161": 0x07}
    for sku in masks:
        p = profile_for(sku)
        assert p is not None and p.capabilities == frozenset({Capability.POWER})
        assert p.requires_secret and p.relay and p.readback == "plug"
    with pytest.raises(ValueError, match="relay_on_mask"):
        dataclasses.replace(profile_for("H5080"), relay_on_mask=0)  # type: ignore[arg-type]


# ── WP3-050: a device-info reply for another selector is ignored ──────────────────────────
def test_stray_wifi_reply_never_becomes_the_device_versions() -> None:
    # A stray aa 07 11 reply landing in the aa 07 03 / 02 reads (the library no longer sends 07 11):
    # its selector is not the one asked for, so neither its MAC nor the Wi-Fi module's versions are used.
    wifi = _frame(0xAA, 0x07, 0x11, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 4, 5, 6, 7, 8, 9)
    d = _dev("H6008", {0x07: lambda f: [wifi]})
    run(d._read_device_info())
    assert d.state.wifi_mac is None
    assert d.state.firmware_version is None and d.state.hardware_version is None
    assert d._device_info_read is True                       # the device did answer


def test_mismatched_selector_reply_is_not_parsed() -> None:
    hard = _frame(0xAA, 0x07, 0x03, *b"1.02.00")
    d = _dev("H6008", {0x07: lambda f: [hard]})               # every aa 07 read gets the 0x03 reply
    run(d._read_device_info())
    assert d.state.hardware_version == "1.02.00" and d.state.serial_number is None


# ── WP3-053: mechanism-B batches are placed by the batch asked for ──────────────────────────
@pytest.mark.parametrize("claimed", [200, 0, 1])
def test_mechanism_b_places_each_reply_by_its_request(claimed: int) -> None:
    # WAS: placed by the reply's own byte, and a reply claiming batch 200 dropped. goodsType 19's screen
    # drops that byte (Controller4ColorInfoByGroup.Companion.i) and places by its loop (VM4Light.N5 ->
    # P5), so a reply claiming batch 200, 0 or a duplicate 1 still lands as the batch asked for.
    def batch(frame: bytes) -> list[bytes]:
        seq = claimed if frame[2] == 5 else frame[2]
        return [_frame(0xAA, 0xA5, seq, *([50, frame[2], 2, 3] * 3))]

    d = _dev("H61A8", {0xA5: batch})
    run(d._read_mechanism_b())
    assert [s.index for s in d.state.segments] == list(range(15))
    assert [s.rgb for s in d.state.segments[12:]] == [(5, 2, 3)] * 3


# ── WP3-054 .. WP3-057 / WP3-059: what makes a read-back authoritative ────────────────────
def test_polled_mode_only_reply_reads_back_but_does_not_suppress_adverts() -> None:
    d = _dev("H6008", {0x05: [_frame(0xAA, 0x05, 0x04, 0x01, 0x00)]})
    run(d._read_polled())
    assert d.state.optimistic is False and d._advert_ignore_until == 0.0
    assert d.ingest_advertisement(_adv(1)) is True and d.state.is_on is True


def test_polled_power_reply_suppresses_adverts() -> None:
    d = _dev("H6008", {0x01: [_frame(0xAA, 0x01, 0x00)]})
    run(d._read_polled())
    assert d._advert_ignore_until > time.monotonic()
    assert d.ingest_advertisement(_adv(1)) is False and d.state.is_on is False


def test_h6047_poll_answered_only_by_the_bars_is_authoritative() -> None:
    d = _dev("H6047", {0x36: [_frame(0xAA, 0x36, 0x01, 0x00)]})
    run(d._read_polled())
    assert d.state.is_on is True and d.state.zone_power == {0: True, 1: False}
    assert d.state.optimistic is False and d._advert_ignore_until > time.monotonic()
    assert d.ingest_advertisement(_adv(0)) is False and d.state.is_on is True


async def test_bar_switch_read_reports_what_it_did() -> None:
    d = _dev("H6047")
    assert await d._read_bar_switch() == "none"
    d._conn.query = AsyncMock(return_value=[_frame(0xAA, 0x36, 0x00, 0x01)])  # type: ignore[method-assign]
    assert await d._read_bar_switch() == "applied"
    seq_bump = d._conn.query

    async def racing(frame: bytes, **k: Any) -> list[bytes]:
        d._mutated()
        return list(await seq_bump(frame, **k))

    d._conn.query = racing  # type: ignore[method-assign,assignment]
    assert await d._read_bar_switch() == "stale"


# ── WP3-066: a raw brightness range must map 100 % onto a byte ────────────────────────────
@pytest.mark.parametrize("rng", [(100, 255), (200, 254), (0, 0), (30, 20), (-1, 100)])
def test_brightness_range_rejected_when_100_percent_overflows(rng: tuple[int, int]) -> None:
    with pytest.raises(ValueError, match="brightness_range"):
        DeviceProfile(skus=("HX",), capabilities=frozenset(), brightness_range=rng)


def test_brightness_range_of_the_table_is_accepted() -> None:
    p = DeviceProfile(skus=("HX",), capabilities=frozenset(), brightness_range=(20, 254))
    assert p.brightness_range == (20, 254)


def test_live_ic_profile_needs_its_ic_derivation() -> None:
    # color_readback_live_ic is a 1.3.0 field: a custom profile setting it alone is still accepted
    # (the reported count is used unclamped); the table's build needs the app's clamp (ic_pieces).
    DeviceProfile(skus=("HX",), capabilities=frozenset(), color_readback_live_ic=True)
    with pytest.raises(ValueError, match="color_readback_ic_per_piece"):
        DeviceProfile(skus=("HX",), capabilities=frozenset(), color_readback_live_ic=True,
                      color_readback_ic_range=(1, 2), color_readback_ic_per_piece=0)
    with pytest.raises(ValueError, match="color_readback_ic_range"):
        DeviceProfile(skus=("HX",), capabilities=frozenset(), color_readback_ic_range=(5, 2))


def test_live_ic_without_a_clamp_uses_the_reported_count() -> None:
    profile = DeviceProfile(skus=("HX",), capabilities=frozenset(), color_readback_live_ic=True,
                            color_readback_ic_per_piece=5)
    d = Device(profile, ble_device("AA:BB:CC:DD:EE:3C", "HX"))
    assert _readback.live_ic_pieces(d, 23) == 5          # ceil(23 / 5), no clamp
    assert _readback.live_ic_pieces(d, -1) == 0          # a negative count (aa 40 ff ff) is no pieces
    # The app never stores a count of 0x1000 or more: the spec's ic_count_checked is None there.
    ic = parse.parse_ic_count(_frame(0xAA, 0x40, 0x10, 0x00, 0x05))
    assert ic is not None and ic.ic_count == 0x1000 and ic.ic_count_checked is None
    h6641 = make_device(ble_device("AA:BB:CC:DD:EE:3D", "H6641"), "H6641")
    assert h6641 is not None
    assert _readback.live_ic_pieces(h6641, 23) == 9      # clamped to 41, ceil(41 / 5)


# ── WP3-081: a secret set on a live session is sent before the next command ───────────────
def _connected(monkeypatch: pytest.MonkeyPatch, up: bool = True) -> None:
    monkeypatch.setattr(GoveeConnection, "is_connected", property(lambda _self: up))


def test_secret_set_on_a_live_session_is_sent_first(monkeypatch: pytest.MonkeyPatch) -> None:
    _connected(monkeypatch)
    d = _dev("H5083")
    secret = bytes(range(1, 9))
    d.set_secret(secret)
    run(d.set_power(True))
    assert [f[:2].hex() for f in _sent(d)] == ["33b2", "3301"]
    assert _sent(d)[0] == build.secret_check(secret)
    run(d.set_power(False))
    assert [f[:2].hex() for f in _sent(d)[2:]] == ["3301"]               # sent once


def test_secret_set_without_a_session_is_left_to_session_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    _connected(monkeypatch, up=False)
    d = _dev("H5083")
    d.set_secret(bytes(range(1, 9)))
    run(d.update())
    assert all(f[1] != 0xB2 for f in _sent(d))
    _connected(monkeypatch, up=True)
    run(d.set_power(True))
    assert all(f[1] != 0xB2 for f in _sent(d))                         # the new session sent it


def test_connect_after_set_secret_sends_the_unlock_once(monkeypatch: pytest.MonkeyPatch) -> None:
    up = {"v": False}
    monkeypatch.setattr(GoveeConnection, "is_connected", property(lambda _self: up["v"]))
    d = _dev("H5083")
    d.set_secret(bytes(range(1, 9)))

    async def connect() -> None:      # the session setup sends the (new) secret itself
        up["v"] = True

    d._conn.connect = AsyncMock(side_effect=connect)  # type: ignore[method-assign]
    run(d.connect())
    assert _sent(d) == []                                            # not a second 33 b2
    run(d.set_power(True))
    assert [f[:2].hex() for f in _sent(d)] == ["3301"]


def test_same_secret_again_sends_nothing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    _connected(monkeypatch)
    secret = bytes(range(1, 9))
    d = make_device(ble_device("AA:BB:CC:DD:EE:3C", "H5083"), "H5083", secret=secret)
    assert d is not None
    d._conn.send = AsyncMock()  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock()  # type: ignore[method-assign]
    d.set_secret(secret)
    run(d.set_power(True))
    assert all(c.args[0][1] != 0xB2 for c in d._conn.send.await_args_list)  # type: ignore[attr-defined]


def test_secret_on_a_light_without_the_lock_never_sends_an_unlock(monkeypatch: pytest.MonkeyPatch) -> None:
    _connected(monkeypatch)
    d = _dev("H6008")
    d.set_secret(bytes(8))
    run(d.set_power(True))
    assert [f[:2].hex() for f in _sent(d)] == ["3301"]


# ── builder errors carry the SKU ──────────────────────────────────────────────────────────
def test_builder_not_supported_is_prefixed_once_with_the_sku() -> None:
    d = _dev("H60A6")

    def unsupported(*_a: object) -> bytes:
        raise GoveeBleNotSupported("h61a8 has no per-segment brightness")

    with pytest.raises(GoveeBleNotSupported, match=r"^H60A6: h61a8 has no per-segment brightness$"):
        d._build_or_unsupported(unsupported)

    def prefixed(*_a: object) -> bytes:
        raise GoveeBleNotSupported("H60A6: already")

    with pytest.raises(GoveeBleNotSupported, match=r"^H60A6: already$"):
        d._build_or_unsupported(prefixed)


# A scheme with no per-segment brightness names the SKU: test_fix_wp3_device_design
# .test_a_scheme_without_segment_brightness_is_not_supported_with_the_sku.


def test_whole_device_colour_builder_error_names_the_sku(monkeypatch: pytest.MonkeyPatch) -> None:
    def unsupported(*_a: object, **_k: object) -> list[bytes]:
        raise GoveeBleNotSupported("unknown colour scheme 'x'")

    monkeypatch.setattr(device_mod.build, "color_rgb_frames", unsupported)
    d = _dev("H6008")
    for call in (d.set_rgb((1, 2, 3)), d.turn_on(rgb=(1, 2, 3))):
        with pytest.raises(GoveeBleNotSupported, match=r"^H6008: unknown colour scheme"):
            run(call)
    assert _sent(d) == []


# ── WP3-052: an IC count the app can't parse is a failed IC read ──────────────────────────
@pytest.mark.parametrize("ic", [0x1000, 0x7FFF])
def test_h6641_ic_count_from_the_swap_branch_skips_the_walk(ic: int) -> None:
    # ReadIcController.Companion.a: a signed count >= 0x1000 takes the byte-swap branch, which reads
    # past its 2-byte array, so the app never stores it (VM4Light.D5 does not run).
    d = _dev("H6641", {0x40: [_frame(0xAA, 0x40, ic >> 8, ic & 0xFF, 3)],
                       0xA5: [_frame(0xAA, 0xA5, 1, *([50, 1, 2, 3] * 4))]})
    run(d._read_mechanism_a_direct())
    assert [q[1] for q in _queried(d)] == [0x40] and d.state.segments == []


# ── scene upload + activation are not split by a concurrent scene write ───────────────────
async def test_concurrent_scene_by_name_calls_do_not_interleave() -> None:
    d = _dev("H6006")
    scenes = load_scenes("H6006")
    names = [n for n in sorted(scenes) if d.scene_upload_frames(scenes[n])][:2]
    assert len(names) == 2
    entered, release = asyncio.Event(), asyncio.Event()
    order: list[str] = []
    record = d._conn.send_unacked

    async def gated_upload(frames: list[bytes], **k: Any) -> None:
        order.append("upload")
        if not entered.is_set():
            entered.set()
            await release.wait()
        await record(frames, **k)

    async def activate(frame: bytes, **_k: Any) -> None:
        order.append(f"activate {frame[3] | frame[4] << 8}")

    d._conn.send_unacked = gated_upload  # type: ignore[method-assign,assignment]
    d._conn.send = AsyncMock(side_effect=activate)  # type: ignore[method-assign]
    first = asyncio.create_task(d.set_scene_by_name(names[0]))
    await entered.wait()
    second = asyncio.create_task(d.set_scene_by_name(names[1]))
    plain = asyncio.create_task(d.set_scene(7))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second, plain)
    a, b = (scenes[n].code for n in names)
    # each upload is followed directly by its own activation; nothing lands in between
    ups = [i for i, step in enumerate(order) if step == "upload"]
    assert [order[i + 1] for i in ups] == [f"activate {a}", f"activate {b}"]
    assert sorted(order) == sorted(["upload", "upload", f"activate {a}", f"activate {b}", "activate 7"])
