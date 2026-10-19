"""``DeviceState.zone_power_confirmed``: when the device last told the library each zone's power.

Stamped only by the device: the 0xAC status TLV 0x30, the ``aa 36`` bar read, and an acked write whose frame
sets the zone (``33 30`` its own zone, ``33 36`` both bars, a whole-device power write whose frames set every
zone). Never by the model (a scene activation, an advertisement), and dropped whenever the library forgets a
zone or gives it a value the device did not report, and whenever the device may hold another power than the
one held: a power write that fails after it may have reached the device (ZONE-2: every zone it targets), a
wake (H60A6 ``33 04`` / ``33 05``, H6047 ``33 04`` / ``33 05``), an H6047 ``aa 01`` the bars contradict."""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import types
from collections.abc import Callable, Coroutine
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device, frame, status_burst, unacked_via

from govee_ble_local.devices import _fold
from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import DeviceProfile, profile_for
from govee_ble_local.exceptions import GoveeBleCommandRejected, GoveeBleConnectionError, GoveeBleTimeout
from govee_ble_local.models import DeviceState
from govee_ble_local.scenes import load_scenes



class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(_fold, "time", types.SimpleNamespace(monotonic=c))
    return c


def _dev(sku: str, reply: Callable[[bytes], list[bytes]] = lambda _f: [], *,
         profile: DeviceProfile | None = None) -> tuple[Device, list[bytes]]:
    d = (make_device(ble_device("AA:BB:CC:DD:EE:30", sku), sku) if profile is None
         else Device(profile, ble_device("AA:BB:CC:DD:EE:30", sku)))
    assert d is not None
    sent: list[bytes] = []

    async def send(fr: bytes, **_k: object) -> None:
        sent.append(fr)

    async def send_many(frames: list[bytes], **_k: object) -> None:
        sent.extend(frames)

    async def query(fr: bytes, **_k: object) -> list[bytes]:
        sent.append(fr)
        return reply(fr)

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock(side_effect=unacked_via(send_many))  # type: ignore[method-assign]
    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    return d, sent


def _status(main: int, ring: int, power: int = 1) -> Callable[[bytes], list[bytes]]:
    """An H60A6 0xAC reply: TLV 0x01 [power], TLV 0x30 [main][ring]; every aa read unanswered."""
    burst = status_burst(bytes([0x01, 0x01, power, 0x30, 0x02, main, ring]))
    return lambda fr: list(burst) if fr[0] == 0xAC else []


def _bars(left: int, right: int) -> Callable[[bytes], list[bytes]]:
    """An H6047 that answers only aa 36, with [left][right]."""
    return lambda fr: [frame(0xAA, 0x36, left, right)] if fr[:2] == b"\xaa\x36" else []


def _failing(d: Device, sent: list[bytes], fail: Callable[[bytes], BaseException | None]) -> None:
    """Every ack-verified write records its frame (the transport writes it on every attempt, so it may land)
    and then raises what ``fail`` gives for it, if anything, in place of the ack."""
    async def send(fr: bytes, **_k: object) -> None:
        sent.append(fr)
        err = fail(fr)
        if err is not None:
            raise err

    async def send_many(frames: list[bytes], **_k: object) -> None:
        for fr in frames:
            await send(fr)

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]


def _advert(on: bool) -> object:
    return types.SimpleNamespace(manufacturer_data={0x8802: bytes([0xEC, 0, 0, 0, int(on)])})


def test_the_status_tlv_0x30_stamps_each_zone_it_carries(clock: _Clock) -> None:
    d, _ = _dev("H60A6", _status(1, 0))
    asyncio.run(d.update())
    assert d.state.zone_power == {0: True, 1: False}
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}


def test_an_unanswered_status_read_stamps_nothing(clock: _Clock) -> None:
    d, sent = _dev("H60A6")
    asyncio.run(d.update())
    assert sent[0][0] == 0xAC and d.state.zone_power_confirmed == {}
    d, _ = _dev("H60A6", _status(1, 1))
    asyncio.run(d.update())
    d._conn.query = AsyncMock(return_value=[])  # type: ignore[method-assign]
    clock.now = 200.0
    asyncio.run(d.update())
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}   # not refreshed


def test_a_write_to_the_main_zone_stamps_only_the_main_zone(clock: _Clock) -> None:
    # 33 30 sets its own zone only: the ring's cached power is carried along, not confirmed.
    d, sent = _dev("H60A6")
    asyncio.run(d.set_zone_power("main", True))
    assert sent[-1][:4].hex() == "33300001"
    assert d.state.zone_power_confirmed == {0: 100.0}
    d, _ = _dev("H60A6", _status(1, 1))
    asyncio.run(d.update())
    clock.now = 200.0
    asyncio.run(d.set_zone_power("main", False))
    assert d.state.zone_power == {0: False, 1: True}
    assert d.state.zone_power_confirmed == {0: 200.0, 1: 100.0}


def test_a_scene_activation_stamps_nothing(clock: _Clock) -> None:
    d, sent = _dev("H60A6")
    asyncio.run(d.set_scene(1))
    assert sent[-1][:3].hex() == "330504"
    assert d.state.zone_power == {0: True, 1: True} and d.state.zone_power_confirmed == {}
    d, _ = _dev("H60A6", _status(1, 1))
    asyncio.run(d.update())
    clock.now = 200.0
    asyncio.run(d.set_scene(1))
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}   # the same power: kept, not refreshed
    d, _ = _dev("H60A6", _status(1, 0))
    asyncio.run(d.update())
    clock.now = 300.0
    asyncio.run(d.set_scene(1))   # assumes the ring lit: its "off" confirmation goes
    assert d.state.zone_power == {0: True, 1: True} and d.state.zone_power_confirmed == {0: 200.0}


def test_the_aa_36_reply_stamps_both_bars(clock: _Clock) -> None:
    d, sent = _dev("H6047", _bars(1, 0))
    asyncio.run(d.update())
    assert any(f[:2].hex() == "aa36" for f in sent)
    assert d.state.zone_power == {0: True, 1: False}
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}


def test_the_33_36_bar_write_stamps_both_bars(clock: _Clock) -> None:
    d, sent = _dev("H6047", _bars(1, 1))
    asyncio.run(d.update())
    clock.now = 200.0
    asyncio.run(d.set_zone_power("left", False))
    assert sent[-1][:4].hex() == "33360001"
    assert d.state.zone_power_confirmed == {0: 200.0, 1: 200.0}


def test_the_h60a6_master_switch_stamps_both_zones(clock: _Clock) -> None:
    # FIRMWARE-SOURCED: H60A6 33 01 sets both zone flags (FUN_9b0278dc @0x9b02791c-0x9b027930).
    d, sent = _dev("H60A6")
    asyncio.run(d.set_power(False))
    assert sent == [bytes.fromhex("3301" + "00" * 17 + "32")]
    assert d.state.zone_power == {0: False, 1: False}
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    clock.now = 200.0
    asyncio.run(d.set_power(True))
    assert d.state.zone_power == {0: True, 1: True}
    assert d.state.zone_power_confirmed == {0: 200.0, 1: 200.0}


def test_the_h6047_switch_stamps_both_bars_off_and_forgets_them_on(clock: _Clock) -> None:
    # FIRMWARE-SOURCED: H6047 33 01 sets both bar flags (FUN_01004f32 -> FUN_01002720); after an
    # "on" the library forgets the bars (the app's rule), so their stamps go with them.
    d, _ = _dev("H6047")
    asyncio.run(d.set_power(False))
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    asyncio.run(d.set_power(True))
    assert d.state.zone_power == {} and d.state.zone_power_confirmed == {}


def test_an_h6042_switch_off_leaves_no_bar_stamp(clock: _Clock) -> None:
    # H6042: aa 36 stamps its bars, but it has no profile zones and no firmware in hand says what its 33 01 00
    # leaves the bars at, so the off keeps their power (as before) and drops their stamps.
    d, _ = _dev("H6042", _bars(1, 1))
    asyncio.run(d.update())
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    clock.now = 130.0
    asyncio.run(d.set_power(False))
    assert d.state.is_on is False and d.state.zone_power == {0: True, 1: True}
    assert d.state.zone_power_confirmed == {}


def test_one_33_30_per_zone_stamps_every_zone(clock: _Clock) -> None:
    p = profile_for("H60A6")
    assert p is not None
    d = Device(dataclasses.replace(p, global_power=False), ble_device("AA:BB:CC:DD:EE:31", "H60A6"))
    send_many = AsyncMock()
    d._conn.send_many = send_many  # type: ignore[method-assign]
    asyncio.run(d.set_power(True))
    assert send_many.await_args is not None
    assert [f[:4].hex() for f in send_many.await_args.args[0]] == ["33300001", "33300101"]
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}


def test_turn_on_stamps_only_through_its_power_frame(clock: _Clock) -> None:
    d, sent = _dev("H60A6")
    asyncio.run(d.turn_on(brightness=50))
    assert [f[:3].hex() for f in sent] == ["330101", "330432"]
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    clock.now = 200.0
    asyncio.run(d.turn_on(power=False, brightness=20))
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}


def test_a_write_that_fails_stamps_nothing(clock: _Clock) -> None:
    d, _ = _dev("H60A6")
    d._conn.send = AsyncMock(side_effect=GoveeBleTimeout("no ack"))  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=GoveeBleTimeout("no ack"))  # type: ignore[method-assign]
    with pytest.raises(GoveeBleTimeout):
        asyncio.run(d.set_zone_power("main", True))
    with pytest.raises(GoveeBleTimeout):
        asyncio.run(d.set_power(True))
    assert d.state.zone_power == {} and d.state.zone_power_confirmed == {}


def _lit(sku: str) -> tuple[Device, list[bytes]]:
    """A device read at t=100 with both zones lit and stamped, then the clock at 130."""
    d, sent = _dev(sku, _status(1, 1) if sku == "H60A6" else _bars(1, 1))
    asyncio.run(d.update())
    assert d.state.zone_power == {0: True, 1: True} and d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    return d, sent


def _scene_with_upload(sku: str) -> str:
    """A bundled scene of ``sku`` that set_scene_by_name uploads before its activation."""
    probe, _ = _dev(sku)
    return next(n for n, s in load_scenes(sku).items() if probe.scene_upload_frames(s))


_ZONE2: list[tuple[str, Callable[[Device], Coroutine[Any, Any, None]], str, dict[int, float]]] = [
    # (sku, power write, the frame it raised on, the stamps left) from both zones lit and stamped at 100.
    # ZONE-2: every zone the write targets loses its stamp, whatever power its frame gives that zone.
    ("H60A6", lambda d: d.set_zone_power("background", False), "33300100", {0: 100.0}),   # 33 30: its zone
    ("H60A6", lambda d: d.set_zone_power("background", True), "33300101", {0: 100.0}),    # (the power held)
    ("H6047", lambda d: d.set_zone_power("right", False), "33360100", {}),   # 33 36: both bars
    ("H6047", lambda d: d.set_zone_power("right", True), "33360101", {}),
    ("H60A6", lambda d: d.set_power(False), "33010000", {}),   # whole-fixture power: every zone
    ("H60A6", lambda d: d.set_power(True), "33010100", {}),
    ("H60A6", lambda d: d.turn_on(brightness=50), "33010100", {}),
    ("H6047", lambda d: d.set_power(False), "33010000", {}),
    ("H6047", lambda d: d.set_power(True), "33010100", {}),
    ("H60A6", lambda d: d.set_scene(1), "330504", {}),   # scene activation: every zone
    ("H6047", lambda d: d.set_scene(1), "330504", {}),
    ("H60A6", lambda d: d.set_scene_by_name(_scene_with_upload("H60A6")), "330504", {}),
]


@pytest.mark.parametrize("error", [GoveeBleTimeout("no ack"), asyncio.CancelledError(),
                                   GoveeBleConnectionError("link lost"), GoveeBleCommandRejected("refused")],
                         ids=["timeout", "cancelled", "link-lost", "refused"])
@pytest.mark.parametrize(("sku", "write", "last", "left"), _ZONE2,
                         ids=[f"{c[0]}-{i}-{c[2]}" for i, c in enumerate(_ZONE2)])
def test_a_power_write_that_fails_after_sending_clears_the_stamps_of_the_zones_it_targets(
        clock: _Clock, sku: str, write: Callable[[Device], Coroutine[Any, Any, None]], last: str,
        left: dict[int, float], error: BaseException) -> None:
    # The transport writes the frame on every attempt and once more on a fresh link, so a write that raised
    # (even refused, after an attempt that landed unacked) may have reached the device. Nothing else changes.
    d, sent = _lit(sku)
    clock.now = 130.0
    _failing(d, sent, lambda _f: error)
    with pytest.raises(type(error)):
        asyncio.run(write(d))
    assert sent[-1].hex().startswith(last)
    assert d.state.zone_power == {0: True, 1: True} and d.state.zone_power_confirmed == left


@pytest.mark.parametrize("error", [GoveeBleTimeout("no ack"), GoveeBleCommandRejected("refused")],
                         ids=["timeout", "refused"])
def test_a_batch_that_fails_after_an_acked_power_frame_drops_its_stamps(
        clock: _Clock, error: BaseException) -> None:
    # turn_on's 33 01 01 is acked (H60A6 FUN_9b0278dc mode 0 lights both zones), its 33 04 is not: the
    # zones confirmed off by the 33 01 00 before it lose their stamps, though the state still holds them off.
    d, sent = _dev("H60A6")
    asyncio.run(d.set_power(False))
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    clock.now = 130.0
    _failing(d, sent, lambda f: error if f[1] == 0x04 else None)
    with pytest.raises(type(error)):
        asyncio.run(d.turn_on(brightness=50))
    assert [f[:3].hex() for f in sent[-2:]] == ["330101", "330432"]
    assert d.state.zone_power == {0: False, 1: False} and d.state.zone_power_confirmed == {}


_WAKES: list[tuple[str, Callable[[Device], Coroutine[Any, Any, None]], bool]] = [
    # (sku, write, whether it wakes the device while off)
    ("H60A6", lambda d: d.set_brightness(50), True),
    ("H60A6", lambda d: d.set_rgb((255, 0, 0)), True),
    ("H60A6", lambda d: d.set_zone_color_temp("background", 4000), True),
    ("H60A6", lambda d: d.turn_on(power=False, brightness=20), True),
    ("H6047", lambda d: d.set_brightness(50), True),
    ("H6047", lambda d: d.set_rgb((255, 0, 0)), True),   # 0x05 handler 0x01005510 @0x010057fc-0x01005802
]


@pytest.mark.parametrize("acked", [True, False], ids=["acked", "no-ack"])
@pytest.mark.parametrize(("sku", "write", "wakes"), _WAKES,
                         ids=["H60A6-33-04", "H60A6-33-05-rgb", "H60A6-33-05-zone-cct",
                              "H60A6-turn_on-no-power", "H6047-33-04", "H6047-33-05-rgb"])
def test_a_write_that_wakes_the_device_while_off_drops_the_off_stamps(
        clock: _Clock, sku: str, write: Callable[[Device], Coroutine[Any, Any, None]], wakes: bool,
        acked: bool) -> None:
    # FIRMWARE-SOURCED, a wake the state does not fold in. H60A6, main switch off: a 33 04 (dispatcher case 4
    # -> MSG_SET_TURN(1)) or a 33 05 (case 5) lights the main switch and both zones. H6047, light off: a 33 04
    # or a 33 05 (handlers 0x01005454, 0x01005510) runs the power-on sequence FUN_01002750, whose
    # FUN_01002720(1) lights both bars.
    d, sent = _dev(sku)
    asyncio.run(d.set_power(False))
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    clock.now = 130.0
    if not acked:
        _failing(d, sent, lambda _f: GoveeBleTimeout("no ack"))
    with pytest.raises(GoveeBleTimeout) if not acked else contextlib.nullcontext():
        asyncio.run(write(d))
    assert sent[-1][:2].hex() in ("3304", "3305")
    assert d.state.is_on is False and d.state.zone_power == {0: False, 1: False}
    assert d.state.zone_power_confirmed == ({} if wakes else {0: 100.0, 1: 100.0})


def test_an_h60a6_write_while_on_wakes_nothing(clock: _Clock) -> None:
    d, _ = _dev("H60A6", _status(1, 0))
    asyncio.run(d.update())
    clock.now = 130.0
    asyncio.run(d.set_brightness(50))
    asyncio.run(d.set_rgb((255, 0, 0)))
    assert d.state.zone_power == {0: True, 1: False}
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}


def _serialized(d: Device, sent: list[bytes], fail: Callable[[bytes], bool] = lambda _f: False) -> None:
    """Writes that hold one lock across their frames and yield before each, as the transport's connection
    lock does, so concurrent setters go out in turn. A frame ``fail`` picks is written, then not acked."""
    lock = asyncio.Lock()

    async def write(fr: bytes) -> None:
        await asyncio.sleep(0)
        sent.append(fr)
        if fail(fr):
            raise GoveeBleTimeout("no ack (the frame was written)")

    async def send(fr: bytes, **_k: object) -> None:
        async with lock:
            await write(fr)

    async def send_many(frames: list[bytes], **_k: object) -> None:
        async with lock:
            for fr in frames:
                await write(fr)

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]


async def _together(*calls: Coroutine[Any, Any, None]) -> list[BaseException | None]:
    return await asyncio.gather(*calls, return_exceptions=True)


@pytest.mark.parametrize("acked", [True, False], ids=["acked", "no-ack"])
@pytest.mark.parametrize(("sku", "wake", "frames"), [
    # W1: H60A6 33 05 with the main switch off (dispatcher case 5) lights the main switch and both zones.
    ("H60A6", lambda d: d.set_rgb((255, 0, 0)), ["33010000", "33051501"]),
    # W2: H6047 33 04 with the light off runs the power-on sequence (FUN_01002750 -> FUN_01002720(1)).
    ("H6047", lambda d: d.set_brightness(50), ["33010000", "33043200"]),
], ids=["W1-H60A6", "W2-H6047"])
def test_a_wake_queued_behind_a_power_off_drops_the_off_stamps_the_off_left(
        clock: _Clock, sku: str, wake: Callable[[Device], Coroutine[Any, Any, None]], frames: list[str],
        acked: bool) -> None:
    # The power-off is acked. The wake was planned while the device read on; the power-off applied while it
    # waited for the link, so it landed on a device that was off. It is checked again once its frame went
    # out, acked or not.
    d, sent = _lit(sku)
    clock.now = 130.0
    sent.clear()
    _serialized(d, sent, lambda f: not acked and f[1] != 0x01)
    off, woke = asyncio.run(_together(d.set_power(False), wake(d)))
    assert off is None and (woke is None if acked else isinstance(woke, GoveeBleTimeout))
    assert [f[:4].hex() for f in sent] == frames
    assert d.state.is_on is False and d.state.zone_power == {0: False, 1: False}
    assert d.state.zone_power_confirmed == {}


@pytest.mark.parametrize(("sku", "read", "wake"), [
    ("H6047", _bars(1, 0), lambda d: d.set_brightness(50)),        # W3
    ("H60A6", _status(1, 0), lambda d: d.set_rgb((0, 255, 0))),   # W4
], ids=["W3-H6047", "W4-H60A6"])
def test_a_wake_after_a_power_off_of_unknown_outcome_leaves_no_off_stamp(
        clock: _Clock, sku: str, read: Callable[[bytes], list[bytes]],
        wake: Callable[[Device], Coroutine[Any, Any, None]]) -> None:
    # The off is written but not acked (it landed: every zone is off), so is_on still reads on. The wake
    # that follows, acked, lights every zone.
    d, sent = _dev(sku, read)
    asyncio.run(d.update())
    assert d.state.zone_power == {0: True, 1: False} and d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    clock.now = 130.0
    _serialized(d, sent, lambda f: f[1] == 0x01)
    with pytest.raises(GoveeBleTimeout):
        asyncio.run(d.set_power(False))
    assert d.state.is_on is True and d.state.zone_power_confirmed == {}   # ZONE-2: every zone
    clock.now = 160.0
    asyncio.run(wake(d))
    assert d.state.zone_power == {0: True, 1: False} and d.state.zone_power_confirmed == {}


def _unsure_h60a6(clock: _Clock) -> tuple[Device, list[bytes]]:
    """An H60A6 read with both zones lit at t=100, then a set_power(False) at t=130 that is written but not
    acked: is_on still reads on, and the device may be off."""
    d, sent = _lit("H60A6")
    clock.now = 130.0
    _serialized(d, sent, lambda f: f[:3] == bytes.fromhex("330100"))
    with pytest.raises(GoveeBleTimeout):
        asyncio.run(d.set_power(False))
    assert d.state.is_on is True and d.state.zone_power_confirmed == {}
    clock.now = 160.0
    return d, sent


def test_after_an_off_of_unknown_outcome_a_wake_is_not_ruled_out_by_is_on(clock: _Clock) -> None:
    # The ring's acked 33 30 off stamps it (and leaves the main switch as it was, off if the 33 01 00
    # landed). is_on still reads on, but the 33 05 that follows may wake the fixture, ring included.
    d, _ = _unsure_h60a6(clock)
    asyncio.run(d.set_zone_power("background", False))
    assert d.state.is_on is True and d.state.zone_power_confirmed == {1: 160.0}
    clock.now = 190.0
    asyncio.run(d.set_rgb((255, 0, 0)))
    assert d.state.zone_power == {0: True, 1: False} and d.state.zone_power_confirmed == {}


def test_a_zone_off_does_not_make_a_device_read_off_known_to_be_on(clock: _Clock) -> None:
    # A 33 30 sets only its zone's flag (FUN_9b0278dc zone mode; the main switch too only on a zone-on), so a
    # zone-off leaves the main switch off, though apply_zone_power derives is_on from the zones. The 33 05
    # after it wakes the fixture (dispatcher case 5), ring and main zone included.
    d, _ = _dev("H60A6", _status(1, 1, power=0))
    asyncio.run(d.update())
    assert d.state.is_on is False and d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    clock.now = 130.0
    asyncio.run(d.set_zone_power("main", False))
    assert d.state.is_on is True and d.state.zone_power_confirmed == {0: 130.0, 1: 100.0}
    clock.now = 160.0
    asyncio.run(d.set_zone_rgb("background", (0, 0, 255)))
    assert d.state.zone_power == {0: False, 1: True} and d.state.zone_power_confirmed == {1: 100.0}


@pytest.mark.parametrize("learn", ["power read", "acked power write"])
def test_the_on_off_learned_again_trusts_is_on_for_a_wake_again(clock: _Clock, learn: str) -> None:
    d, _ = _unsure_h60a6(clock)
    if learn == "power read":
        asyncio.run(d.update())   # TLV 0x01 [on], TLV 0x30 [1][1]
    else:
        asyncio.run(d.set_power(True))   # 33 01 01 sets the main switch and both zones
    asyncio.run(d.set_zone_power("background", False))
    assert d.state.is_on is True and d.state.zone_power_confirmed == {0: 160.0, 1: 160.0}
    clock.now = 190.0
    asyncio.run(d.set_rgb((255, 0, 0)))   # the main switch is on: no wake
    assert d.state.zone_power == {0: True, 1: False}
    assert d.state.zone_power_confirmed == {0: 160.0, 1: 160.0}


@pytest.mark.parametrize(("bars", "power", "left"), [
    ((1, 1), 0, {}),                      # off: both bars held lit are ruled out
    ((1, 0), 0, {1: 100.0}),             # off: the left bar held lit goes; the right, held off, agrees
    ((0, 0), 1, {}),                      # on: a bar is lit, which one is not known
    ((1, 0), 1, {0: 100.0, 1: 100.0}),   # on: agrees
])
def test_an_h6047_power_reply_the_bars_contradict_drops_their_stamps(
        clock: _Clock, bars: tuple[int, int], power: int, left: dict[int, float]) -> None:
    # H6047 answers aa 01 with bar 0 | bar 1 (FUN_01004f32): when the aa 36 read after it is lost, a reply
    # the held bars contradict still rules their stamps out; their power stays as it was.
    answers = types.SimpleNamespace(power=int(any(bars)), bars=bars)

    def reply(fr: bytes) -> list[bytes]:
        if fr[:2] == b"\xaa\x01":
            return [frame(0xAA, 0x01, answers.power)]
        if fr[:2] == b"\xaa\x36" and answers.bars is not None:
            return [frame(0xAA, 0x36, *answers.bars)]
        return []

    d, _ = _dev("H6047", reply)
    asyncio.run(d.update())
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    clock.now = 130.0
    answers.power, answers.bars = power, None   # the aa 36 read is lost
    asyncio.run(d.update())
    assert d.state.is_on is bool(power)
    assert d.state.zone_power == {0: bool(bars[0]), 1: bool(bars[1])}
    assert d.state.zone_power_confirmed == left


def _scene_power_on() -> DeviceProfile:
    """A zoned scene_power_on profile (none today): one 33 30 per zone leads a scene on a device not known to
    be on, and lights every zone."""
    p = profile_for("H60A6")
    assert p is not None
    return dataclasses.replace(p, global_power=False, scene_power_on=True)   # no wake either


def test_a_scene_power_on_lead_drops_the_off_stamps_before_it_goes_out(clock: _Clock) -> None:
    # set_scene_by_name sends the lead on its own and folds it in only with the activation: a zone held off
    # loses its stamp before the lead goes out, and one held lit (lit either way) keeps it.
    zoned = _scene_power_on()
    probe, _ = _dev("H60A6", profile=zoned)
    name = next(n for n, s in load_scenes("H60A6").items() if probe.scene_upload_frames(s))
    d, sent = _dev("H60A6", profile=zoned)
    asyncio.run(d.set_zone_power("main", True))
    asyncio.run(d.set_zone_power("background", False))
    d._state.is_on = None   # not known to be on: the scene is led by the switch
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    during: list[dict[int, float]] = []

    async def upload(_frames: object, **_k: object) -> None:
        during.append(dict(d.state.zone_power_confirmed))

    d._conn.send_unacked = AsyncMock(side_effect=upload)  # type: ignore[method-assign]
    clock.now = 130.0
    asyncio.run(d.set_scene_by_name(name))
    assert [f[:4].hex() for f in sent[2:4]] == ["33300001", "33300101"]   # the lead
    assert during == [{0: 100.0}]
    assert d.state.zone_power == {0: True, 1: True} and d.state.zone_power_confirmed == {0: 100.0}


@pytest.mark.parametrize(("by_name", "fails"), [
    (False, "lead"), (False, "activation"), (True, "lead"), (True, "upload"), (True, "activation")],
    ids=lambda v: {False: "set_scene", True: "set_scene_by_name"}.get(v, v))
def test_a_scene_power_on_lead_or_scene_that_fails_clears_every_stamp(
        clock: _Clock, by_name: bool, fails: str) -> None:
    # ZONE-2: a whole-device power write and a scene activation target every zone, so when either raises,
    # every stamp goes, the lit zone's too. set_scene sends the lead with the activation in one batch;
    # set_scene_by_name sends it on its own, then the upload, then the activation.
    zoned = _scene_power_on()
    probe, _ = _dev("H60A6", profile=zoned)
    name = next(n for n, s in load_scenes("H60A6").items() if probe.scene_upload_frames(s))
    d, sent = _dev("H60A6", profile=zoned)
    asyncio.run(d.set_zone_power("main", True))
    assert d.state.is_on is None and d.state.zone_power_confirmed == {0: 100.0}
    clock.now = 130.0
    _failing(d, sent, lambda f: GoveeBleTimeout("no ack") if f[1] == {"lead": 0x30, "activation": 0x05}.get(
        fails) else None)
    if fails == "upload":
        d._conn.send_unacked = AsyncMock(side_effect=GoveeBleTimeout("lost"))  # type: ignore[method-assign]
    with pytest.raises(GoveeBleTimeout):
        asyncio.run(d.set_scene_by_name(name) if by_name else d.set_scene(1))
    assert sent[1][:4].hex() == "33300001"   # the lead went out
    assert d.state.zone_power == {0: True} and d.state.zone_power_confirmed == {}


def test_a_forget_clears_the_stamps(clock: _Clock) -> None:
    d, _ = _dev("H60A6", _status(0, 0, power=0))
    asyncio.run(d.update())
    assert d.state.is_on is False and d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    d._advert_ignore_until = 0.0   # past the read's advert suppression
    assert d.ingest_advertisement(_advert(True)) is True  # type: ignore[arg-type]  # "on" over all-off zones
    assert d.state.zone_power == {} and d.state.zone_power_confirmed == {}


def test_an_advert_confirms_nothing_and_drops_a_zone_it_changes() -> None:
    p = profile_for("H60A6")
    assert p is not None
    st = DeviceState(zone_power={0: True, 1: False}, zone_power_confirmed={0: 1.0, 1: 2.0})
    _fold.apply_advert_power(p, st, False)
    assert st.zone_power == {0: False, 1: False} and st.zone_power_confirmed == {1: 2.0}


def test_snapshots_carry_the_stamps_and_equality_ignores_them(clock: _Clock) -> None:
    d, _ = _dev("H60A6", _status(1, 1))
    asyncio.run(d.update())
    before = d.snapshot()
    assert before.zone_power_confirmed == {0: 100.0, 1: 100.0}
    assert before.zone_power_confirmed is not d.state.zone_power_confirmed
    clock.now = 200.0
    asyncio.run(d.update())   # re-confirms the same power
    assert d.state.zone_power_confirmed == {0: 200.0, 1: 200.0} and before.zone_power_confirmed[0] == 100.0
    assert d.state == before


_ZONES = {"H60A6": ("main", "background"), "H6047": ("left", "right")}
_STARTS = {"both on": {0: True, 1: True}, "both off": {0: False, 1: False}, "first on": {0: True, 1: False}}
_OPS: list[tuple[str, int, bool]] = [   # (call, zone index for set_zone_power, on / scene code)
    ("set_power", 0, True), ("set_power", 0, False), ("set_scene", 0, True),
    ("advert", 0, True), ("advert", 0, False),
    *[("set_zone_power", i, on) for i in (0, 1) for on in (True, False)]]


@pytest.mark.parametrize("op", _OPS, ids=lambda o: f"{o[0]}-{o[1]}-{o[2]}")
@pytest.mark.parametrize("start", list(_STARTS))
@pytest.mark.parametrize("sku", list(_ZONES))
def test_a_confirmation_never_vouches_for_a_change_it_didnt_see(
        clock: _Clock, sku: str, start: str, op: tuple[str, int, bool]) -> None:
    """From each start with 60 s-old stamps on both zones: every zone whose value the call changed (or
    forgot) ends with no stamp, or with one taken during the call (the Home Assistant contract)."""
    d, _ = _dev(sku)
    before = dict(_STARTS[start])
    d._state.is_on = any(before.values())
    d._state.zone_power = dict(before)
    d._state.zone_power_confirmed = {0: 40.0, 1: 40.0}
    d._advert_ignore_until = 0.0
    name, zone, on = op
    if name == "advert":
        d.ingest_advertisement(_advert(on))  # type: ignore[arg-type]
    elif name == "set_zone_power":
        asyncio.run(d.set_zone_power(_ZONES[sku][zone], on))
    elif name == "set_scene":
        asyncio.run(d.set_scene(1))
    else:
        asyncio.run(d.set_power(on))
    after, stamps = d.state.zone_power, d.state.zone_power_confirmed
    assert set(stamps) <= set(after)
    for i in (0, 1):
        if after.get(i) != before[i]:
            assert stamps.get(i, clock.now) == clock.now, (i, before, after, stamps)
