"""``DeviceState.zone_power_confirmed``: when the device last told the library each zone's power.

Stamped only by the device: the 0xAC status TLV 0x30, the ``aa 36`` bar read, and an acked write whose frame
sets the zone (``33 30`` its own zone, ``33 36`` both bars, a whole-device power write whose frames set every
zone). Never by the model (a scene activation, an advertisement), and dropped whenever the library forgets a
zone or gives it a value the device did not report, and whenever the device may hold another power than the
one held: a power write that fails after it may have reached the device (ZONE-2: every zone it targets), a
wake (H60A6 ``33 04`` / ``33 05``, H6047 ``33 04`` / ``33 05``), an H6047 ``aa 01`` the bars contradict.

The follow-up: a zone-off that may leave no zone lit switches the H60A6 main switch off (ZPC-1); TLV 0x30
vouches for a lit zone only while TLV 0x01 says on (MISSED-2); a status reply that may predate an H60A6 power
write the firmware acked but has not applied vouches for no zone it contradicts (ZPC-2); a poll clears the
power doubt only from a read issued after it arose (ZPC-4); a scene activation vouches for no zone (F7); an
H6047 bar write reads the bars first unless the other bar is freshly confirmed (F2); and a batch's frames are
never split by another write (MISSED-4)."""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import types
from collections.abc import Callable, Coroutine
from typing import Any
from unittest.mock import AsyncMock

import pytest
from bleak.exc import BleakError
from fake_ble import FakeGovee, ble_device, frame, status_burst, unacked_via

from govee_ble_local.const import SESSION_ATTEMPTS, ZONE_POWER_FRESH_SECONDS
from govee_ble_local.devices import _fold
from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import DeviceProfile, profile_for
from govee_ble_local.exceptions import GoveeBleCommandRejected, GoveeBleConnectionError, GoveeBleTimeout
from govee_ble_local.models import DeviceState, Encryption
from govee_ble_local.scenes import load_scenes
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.transport.connection import GoveeConnection



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
    d._conn.connect = AsyncMock()  # type: ignore[method-assign]
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


@pytest.mark.parametrize(("main", "ring"), [(1, 1), (1, 0)])
def test_a_scene_activation_stamps_nothing_and_drops_every_stamp(clock: _Clock, main: int, ring: int) -> None:
    # F7: what a scene activation does to the zone flags is not traced (H60A6 FUN_9b025124, H6047 0x01005510),
    # so an acked one vouches for no zone, as a failed one does: a zone it leaves at the power it held loses
    # its stamp too, the main panel confirmed lit shortly before included.
    d, sent = _dev("H60A6")
    asyncio.run(d.set_scene(1))
    assert sent[-1][:3].hex() == "330504"
    assert d.state.zone_power == {0: True, 1: True} and d.state.zone_power_confirmed == {}
    d, _ = _dev("H60A6", _status(main, ring))
    asyncio.run(d.update())
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    clock.now = 101.0
    asyncio.run(d.set_scene(1))
    assert d.state.zone_power == {0: True, 1: True} and d.state.zone_power_confirmed == {}


def test_an_h6047_scene_activation_drops_the_bar_stamps(clock: _Clock) -> None:
    d, _ = _dev("H6047", _bars(1, 1))
    asyncio.run(d.update())
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    asyncio.run(d.set_scene(1))   # bar_switch: the activation forgets the bars, as a 33 01 on does
    assert d.state.zone_power == {} and d.state.zone_power_confirmed == {}


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
    # A 33 30 off clears its zone's flag and, when no zone is left lit, the main switch (FUN_9b0278dc zone
    # mode @0x9b027972-0x9b027986; a zone-on sets it). The main switch is already off here, so it stays off,
    # though apply_zone_power derives is_on from the zones. The 33 05 after it wakes the fixture (dispatcher
    # case 5), ring and main zone included. (MISSED-2: the read, TLV 0x01 off, vouches for neither zone's
    # "on".)
    d, _ = _dev("H60A6", _status(1, 1, power=0))
    asyncio.run(d.update())
    assert d.state.is_on is False and d.state.zone_power == {0: True, 1: True}
    assert d.state.zone_power_confirmed == {}
    clock.now = 130.0
    asyncio.run(d.set_zone_power("main", False))
    assert d.state.is_on is True and d.state.zone_power_confirmed == {0: 130.0}
    assert d._power_unsure is True
    clock.now = 160.0
    asyncio.run(d.set_zone_rgb("background", (0, 0, 255)))
    assert d.state.zone_power == {0: False, 1: True} and d.state.zone_power_confirmed == {}


@pytest.mark.parametrize("is_on", [False, None])
def test_a_zone_off_on_a_device_not_known_to_be_on_trusts_no_zone_left_lit(
        clock: _Clock, is_on: bool | None) -> None:
    # The zone-off rule's first half (ZPC-1 keeps it): on a device not known to be on, is_on is not trusted
    # after an acked 33 30 off even when the other zone is held lit with a stamp; the 33 05 after it is
    # treated as a wake, so the main zone's "off" stamp goes.
    d, _ = _dev("H60A6")
    d._state.is_on = is_on
    d._state.zone_power = {0: True, 1: True}
    d._state.zone_power_confirmed = {0: 40.0, 1: 40.0}
    asyncio.run(d.set_zone_power("main", False))
    assert d._power_unsure is True and d.state.zone_power_confirmed == {0: 100.0, 1: 40.0}
    clock.now = 130.0
    asyncio.run(d.set_rgb((255, 0, 0)))
    assert d.state.zone_power_confirmed == {1: 40.0}


def _flags_h60a6(d: Device, dev: dict[Any, int], *, lag: bool = False) -> list[bytes]:
    """Drive ``d`` against an H60A6 flag model (UserConfig +8 main, +9 zone 0, +0xA zone 1), written from the
    firmware: 33 01 -> MSG_SET_TURN, FUN_9b0278dc mode 0 (main and both zones); 33 30 -> MSG_SET_TURN_MULT,
    mode 1 (a zone-on sets the main switch @0x9b02795e; the zone's flag; no zone lit -> main off
    @0x9b027972-0x9b027986); 33 04 with the main switch off -> MSG_SET_TURN(1) (@0x9b026c5a-0x9b026c5e); 33 05
    with it off sets all three (case 5). The 0xAC reply is TLV 0x01 [main], TLV 0x30 [z0][z1]. With ``lag``
    the posted messages are applied only after the next 0xAC reply went out (the dispatcher acks once it has
    posted them; the message thread applies them later)."""
    sent: list[bytes] = []
    posted: list[tuple[int, int, int]] = []   # (kind, zone, state): kind 0 = MSG_SET_TURN, 1 = _MULT

    def run_posted() -> None:
        while posted:
            kind, z, s = posted.pop(0)
            if kind == 0:
                dev.update({"main": int(s != 0), 0: int(s != 0), 1: int(s != 0)})
                continue
            if s == 1:
                dev["main"] = 1
            if z in (0, 1):
                dev[z] = int(s != 0)
            if not dev[0] and not dev[1]:
                dev["main"] = 0

    async def send(fr: bytes, **_k: object) -> None:
        sent.append(fr)
        if fr[1] == 0x01:
            posted.append((0, 0, fr[2]))
        elif fr[1] == 0x30:
            posted.append((1, fr[2], fr[3]))
        elif fr[1] == 0x04 and not dev["main"]:
            posted.append((0, 0, 1))
        elif fr[1] == 0x05 and not dev["main"]:
            dev.update({"main": 1, 0: 1, 1: 1})
        if not lag:
            run_posted()

    async def send_many(frames: list[bytes], **_k: object) -> None:
        for fr in frames:
            await send(fr)

    async def query(fr: bytes, **_k: object) -> list[bytes]:
        sent.append(fr)
        if fr[0] != 0xAC:
            return []
        burst = status_burst(bytes([0x01, 0x01, dev["main"], 0x30, 0x02, dev[0], dev[1]]))
        run_posted()   # the message thread runs after the reply went out
        return burst

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock()  # type: ignore[method-assign]
    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    return sent


@pytest.mark.parametrize("wake", ["33 05", "33 04"])
def test_a_zone_off_that_may_leave_no_zone_lit_does_not_trust_is_on(clock: _Clock, wake: str) -> None:
    # ZPC-1: a 33 30 off that leaves no zone lit also switches the main switch off (FUN_9b0278dc zone mode
    # @0x9b027972-0x9b027986: both zone flags 0 -> FUN_9b02b3fc(0), then FUN_9b033e28). An advertisement's
    # "on" left the zones unknown, so is_on rests on nothing the zone-off kept lit: the colour / brightness
    # write after it wakes the fixture, and the main zone's fresh "off" must not outlive it.
    d, _ = _dev("H60A6")
    dev = {"main": 1, 0: 1, 1: 0}   # main panel lit, ring off
    _flags_h60a6(d, dev)
    d.ingest_advertisement(_advert(True))  # type: ignore[arg-type]
    assert d.state.is_on is True and d.state.zone_power == {}
    asyncio.run(d.set_zone_power("main", False))
    assert dev == {"main": 0, 0: 0, 1: 0} and d.state.is_on is True
    assert d._power_unsure is True and d.state.zone_power_confirmed == {0: 100.0}
    clock.now = 105.0
    asyncio.run(d.set_rgb((255, 0, 0)) if wake == "33 05" else d.set_brightness(50))
    assert dev == {"main": 1, 0: 1, 1: 1}
    assert d.state.zone_power == {0: False} and d.state.zone_power_confirmed == {}


def test_a_zone_off_after_a_scene_that_assumed_the_ring_lit_does_not_trust_is_on(clock: _Clock) -> None:
    # The scene assumes the ring lit (no stamp), the main-zone off then darkens the fixture, and the 33 04
    # that wakes it must not leave the main zone a fresh "off".
    d, _ = _dev("H60A6")
    dev = {"main": 1, 0: 1, 1: 0}
    _flags_h60a6(d, dev)
    asyncio.run(d.update())
    assert d.state.zone_power == {0: True, 1: False} and d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    clock.now = 110.0
    asyncio.run(d.set_scene(1))
    assert d.state.zone_power == {0: True, 1: True} and d.state.zone_power_confirmed == {}
    clock.now = 120.0
    asyncio.run(d.set_zone_power("main", False))
    assert dev["main"] == 0 and d.state.is_on is True and d._power_unsure is True
    clock.now = 130.0
    asyncio.run(d.set_brightness(50))
    assert dev == {"main": 1, 0: 1, 1: 1} and 0 not in d.state.zone_power_confirmed


def test_a_zone_off_with_the_other_zone_confirmed_lit_keeps_trusting_is_on(clock: _Clock) -> None:
    # The other zone is held lit with a stamp on a device known to be on: the zone-off leaves it lit, the main
    # switch stays on (FUN_9b0278dc zone mode @0x9b027972-0x9b027980), and a colour write wakes nothing.
    d, _ = _dev("H60A6")
    dev = {"main": 1, 0: 1, 1: 1}
    _flags_h60a6(d, dev)
    asyncio.run(d.update())
    clock.now = 130.0
    asyncio.run(d.set_zone_power("main", False))
    assert dev == {"main": 1, 0: 0, 1: 1} and d._power_unsure is False
    clock.now = 160.0
    asyncio.run(d.set_rgb((255, 0, 0)))
    assert d.state.zone_power == {0: False, 1: True} and d.state.zone_power_confirmed == {0: 130.0, 1: 100.0}


@pytest.mark.parametrize("learn", ["power read", "acked power write"])
def test_the_on_off_learned_again_trusts_is_on_for_a_wake_again(clock: _Clock, learn: str) -> None:
    d, _ = _unsure_h60a6(clock)
    if learn == "power read":
        # TLV 0x01 [on], TLV 0x30 [1][1]. The first reply contradicts the 33 01 00 that may have landed, so it
        # may predate the device applying it (ZPC-2): it vouches for neither zone and leaves is_on untrusted.
        asyncio.run(d.update())
        assert d._power_unsure is True and d.state.zone_power_confirmed == {}
        asyncio.run(d.update())
    else:
        asyncio.run(d.set_power(True))   # 33 01 01 sets the main switch and both zones
        assert d._power_unsure is False
        # The firmware acks it before its message thread applies it (ZPC-2), so a status reply carrying it
        # comes first: until then a 33 04 / 33 05 may still find the main switch off.
        assert d._zone_power_posted == {0: {True}, 1: {True}}
        asyncio.run(d.update())
        assert d._zone_power_posted == {}
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
    # Then the activation vouches for no zone (F7), the lit one included.
    assert d.state.zone_power == {0: True, 1: True} and d.state.zone_power_confirmed == {}


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


def _replying(reply: Callable[[bytes], list[bytes]]) -> AsyncMock:
    """A ``query`` fake that answers with ``reply`` (the transport's keywords ignored)."""
    async def query(fr: bytes, **_k: object) -> list[bytes]:
        return reply(fr)

    return AsyncMock(side_effect=query)


def test_tlv_0x30_vouches_for_a_lit_zone_only_while_tlv_0x01_says_on(clock: _Clock) -> None:
    # MISSED-2: with the main switch off (TLV 0x01 [0]) a zone flag of 1 does not say the zone is lit, so its
    # "on" is not stamped (the "off" of the other still is), and a stamp of "on" from an earlier read goes.
    d, _ = _dev("H60A6", _status(1, 0, power=0))
    asyncio.run(d.update())
    assert d.state.is_on is False and d.state.zone_power == {0: True, 1: False}
    assert d.state.zone_power_confirmed == {1: 100.0}
    d, _ = _dev("H60A6", _status(1, 1))
    asyncio.run(d.update())
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    d._conn.query = _replying(_status(1, 1, power=0))  # type: ignore[method-assign]
    clock.now = 200.0
    asyncio.run(d.update())
    assert d.state.is_on is False and d.state.zone_power == {0: True, 1: True}
    assert d.state.zone_power_confirmed == {}


@pytest.mark.parametrize(("write", "start", "gap", "after"), [
    # (the acked write, the device's flags before it, what the gap reply vouches for, the flags it leaves)
    (lambda d: d.set_power(False), {"main": 1, 0: 1, 1: 1}, {}, {"main": 0, 0: 0, 1: 0}),
    (lambda d: d.set_power(True), {"main": 0, 0: 0, 1: 0}, {}, {"main": 1, 0: 1, 1: 1}),
    (lambda d: d.set_zone_power("background", False), {"main": 1, 0: 1, 1: 1}, {0: 110.5},
     {"main": 1, 0: 1, 1: 0}),
    (lambda d: d.set_zone_power("main", True), {"main": 0, 0: 0, 1: 0}, {1: 110.5}, {"main": 1, 0: 1, 1: 0}),
    (lambda d: d.set_brightness(50), {"main": 0, 0: 0, 1: 0}, {}, {"main": 1, 0: 1, 1: 1}),   # the 33 04 wake
], ids=["33-01-off", "33-01-on", "33-30-ring-off", "33-30-main-on", "33-04-wake"])
def test_a_status_reply_that_predates_an_acked_power_write_vouches_for_no_zone_it_contradicts(
        clock: _Clock, write: Callable[[Device], Coroutine[Any, Any, None]], start: dict[Any, int],
        gap: dict[int, float], after: dict[Any, int]) -> None:
    # ZPC-2, FIRMWARE-SOURCED: the H60A6 dispatcher acks a 33 01 / 33 30 (and a 33 04 wake) once it has posted
    # it (MSG_SET_TURN 0xB2 / MSG_SET_TURN_MULT 0xB7); its message thread applies it later (FUN_9b0278dc),
    # and the 0xAC reply is built from the flags as they are. A reply answered in that gap reports the old
    # power: it keeps the zone's value as before but no stamp, and its on/off does not rule a wake out. The
    # read after it vouches again.
    d, _ = _dev("H60A6")
    dev = dict(start)
    _flags_h60a6(d, dev, lag=True)
    asyncio.run(d.update())
    old = {0: bool(start[0]), 1: bool(start[1])}
    assert d.state.zone_power == old and d._power_unsure is False
    clock.now = 110.0
    asyncio.run(write(d))
    clock.now = 110.5
    asyncio.run(d.update())   # answered before the message thread ran
    assert dev == after
    assert d.state.zone_power == old and d.state.is_on is bool(start["main"])
    assert d.state.zone_power_confirmed == gap and d._power_unsure is True
    clock.now = 140.0
    asyncio.run(d.update())
    assert d.state.zone_power == {0: bool(after[0]), 1: bool(after[1])}
    assert d.state.zone_power_confirmed == {0: 140.0, 1: 140.0} and d._power_unsure is False


def test_a_status_reply_that_agrees_with_an_acked_power_write_vouches_for_it(clock: _Clock) -> None:
    # The message thread ran before the reply (the usual case): it carries the posted power and stamps it.
    d, _ = _dev("H60A6")
    dev = {"main": 1, 0: 1, 1: 1}
    _flags_h60a6(d, dev)
    asyncio.run(d.update())
    clock.now = 110.0
    asyncio.run(d.set_power(False))
    clock.now = 110.5
    asyncio.run(d.update())
    assert d.state.zone_power == {0: False, 1: False} and d.state.zone_power_confirmed == {0: 110.5, 1: 110.5}
    assert d._power_unsure is False and d._zone_power_posted == {}


def _h6047_poll_raced_by_a_failed_off(gated: bytes) -> Device:
    """ZPC-4's race: an H6047 poll (aa 01, aa 04, aa 36, aa 05; both bars lit) waits before its ``gated`` read
    while a set_power(False) whose 33 01 00 lands unacked runs, then finishes. Later polls are not gated."""
    d, _ = _dev("H6047")
    bars = [1, 1]
    lock = asyncio.Lock()
    gate: dict[str, asyncio.Event] = {}

    async def send(fr: bytes, **_k: object) -> None:
        async with lock:
            await asyncio.sleep(0)
            if fr[1] == 0x01:
                bars[:] = [fr[2], fr[2]]
                raise GoveeBleTimeout("landed, no ack")

    async def send_many(frames: list[bytes], **_k: object) -> None:
        for fr in frames:
            await send(fr)

    async def query(fr: bytes, **_k: object) -> list[bytes]:
        if fr[:2] == gated and gate:
            gate["past"].set()
            await gate["release"].wait()
        async with lock:
            await asyncio.sleep(0)
            if fr[:2] == b"\xaa\x01":
                return [frame(0xAA, 0x01, int(any(bars)))]
            if fr[:2] == b"\xaa\x36":
                return [frame(0xAA, 0x36, *bars)]
            return []

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]

    async def race() -> None:
        gate["past"], gate["release"] = asyncio.Event(), asyncio.Event()
        poll = asyncio.ensure_future(d.update())
        await gate["past"].wait()   # aa 01 answered (lit); the poll waits on its gated read
        with pytest.raises(GoveeBleTimeout):
            await d.set_power(False)
        assert d._power_unsure is True and d.state.is_on is True
        gate["release"].set()
        await poll

    asyncio.run(race())
    gate.clear()
    assert bars == [0, 0]
    return d


def test_a_poll_raced_by_a_failed_power_off_after_its_power_read_leaves_is_on_untrusted(
        clock: _Clock) -> None:
    # ZPC-4: an H6047 poll reads aa 01 and aa 36, and a set_power(False) whose 33 01 00 lands unacked runs
    # before its aa 05: the poll's power reads predate the off, so its end does not make is_on trusted again.
    # A poll issued after the off does.
    d = _h6047_poll_raced_by_a_failed_off(b"\xaa\x05")   # aa 01 and aa 36 answered before the off
    assert d.state.is_on is True and d._power_unsure is True
    asyncio.run(d.update())
    assert d.state.is_on is False and d._power_unsure is False


def test_a_poll_whose_bar_read_follows_a_failed_power_off_trusts_is_on_again(clock: _Clock) -> None:
    # ZPC-4, the aa 36 half: the off lands between the poll's aa 01 and its aa 36 (the poll waits on aa 04).
    # The aa 36 is issued after the off and gives the on/off (both bars off), so the poll's end makes is_on
    # trusted again.
    d = _h6047_poll_raced_by_a_failed_off(b"\xaa\x04")
    assert d.state.is_on is False and d._power_unsure is False


@pytest.mark.parametrize(("now", "stamps", "reads"), [
    (300.0, {0: 100.0, 1: 100.0}, True),    # the other bar confirmed 200 s ago: stale
    (110.0, {0: 100.0}, True),              # the other bar held but not vouched for
    (220.0, {0: 100.0, 1: 100.0}, False),   # confirmed ZONE_POWER_FRESH_SECONDS ago: fresh
    (150.0, {1: 100.0}, False),             # only the other bar's stamp matters
], ids=["stale", "unstamped", "at-the-bound", "fresh"])
def test_a_bar_write_reads_the_bars_unless_the_other_bar_is_freshly_confirmed(
        clock: _Clock, now: float, stamps: dict[int, float], reads: bool) -> None:
    # F2: one 33 36 carries both bars, and the app builds it from bars its aa 36 heartbeat keeps current
    # (h6047 NewDetailVm.D5 / A3). The bars read {left on, right off}; then the right bar is switched on in
    # the app. A left-bar write reads the bars first unless the right bar was confirmed within the bound, and
    # sends what it read: the right bar stays lit.
    assert ZONE_POWER_FRESH_SECONDS == 120.0
    bars = [1, 0]
    d, sent = _dev("H6047", lambda fr: [frame(0xAA, 0x36, *bars)] if fr[:2] == b"\xaa\x36" else [])
    asyncio.run(d.update())
    d._state.zone_power_confirmed = dict(stamps)
    bars[1] = 1
    clock.now = now
    sent.clear()
    asyncio.run(d.set_zone_power("left", True))
    if reads:
        assert [f[:4].hex() for f in sent] == ["aa360000", "33360101"]
        assert d.state.zone_power == {0: True, 1: True}
    else:
        assert [f[:4].hex() for f in sent] == ["33360100"]
    assert d.state.zone_power_confirmed == {0: now, 1: now}


def test_a_bar_write_whose_bar_read_goes_unanswered_sends_the_held_bar(clock: _Clock) -> None:
    # The app sends the bars of its last heartbeat when one is missed; so does the library with a held bar
    # (an unknown one still raises).
    d, sent = _dev("H6047", _bars(1, 0))
    asyncio.run(d.update())
    d._conn.query = _replying(lambda _f: [])  # type: ignore[method-assign]
    clock.now = 300.0
    sent.clear()
    asyncio.run(d.set_zone_power("left", False))
    assert [f[:4].hex() for f in sent] == ["33360000"]
    assert d._conn.query.await_args is not None and d._conn.query.await_args.args[0][:2] == b"\xaa\x36"
    assert d.state.zone_power == {0: False, 1: False} and d.state.zone_power_confirmed == {0: 300.0, 1: 300.0}


@pytest.mark.parametrize("err", [GoveeBleConnectionError("link lost mid-operation"), GoveeBleTimeout("x")],
                         ids=["link-lost", "timeout"])
def test_a_bar_write_whose_stale_bar_re_read_fails_sends_the_held_bar(clock: _Clock, err: Exception) -> None:
    # F2 must not turn a write d47d22a sent into an error: with both bars held, the re-read run only because
    # the other bar's stamp is stale falls back to the held bar when it raises, as when it goes unanswered.
    d, sent = _dev("H6047", _bars(1, 0))
    asyncio.run(d.update())

    async def query(fr: bytes, **_k: object) -> list[bytes]:
        sent.append(fr)
        raise err

    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    clock.now = 400.0
    sent.clear()
    asyncio.run(d.set_zone_power("left", False))
    assert [f[:4].hex() for f in sent] == ["aa360000", "33360000"]
    assert d.state.zone_power == {0: False, 1: False} and d.state.zone_power_confirmed == {0: 400.0, 1: 400.0}


def test_a_bar_write_whose_bar_read_fails_while_a_bar_is_unknown_raises(clock: _Clock) -> None:
    # The unknown-bar read keeps d47d22a's behaviour: its error propagates and no 33 36 goes out.
    d, sent = _dev("H6047")

    async def query(fr: bytes, **_k: object) -> list[bytes]:
        sent.append(fr)
        raise GoveeBleConnectionError("link lost mid-operation")

    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    d._state.zone_power = {0: True}
    with pytest.raises(GoveeBleConnectionError):
        asyncio.run(d.set_zone_power("left", False))
    assert [f[:2].hex() for f in sent] == ["aa36"]


def test_a_cancelled_stale_bar_re_read_cancels_the_bar_write(clock: _Clock) -> None:
    # Only a GoveeBleError falls back to the held bar: a cancellation during the re-read propagates.
    d, sent = _dev("H6047", _bars(1, 0))
    asyncio.run(d.update())

    async def query(fr: bytes, **_k: object) -> list[bytes]:
        sent.append(fr)
        raise asyncio.CancelledError

    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    clock.now = 400.0
    sent.clear()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(d.set_zone_power("left", False))
    assert [f[:2].hex() for f in sent] == ["aa36"]


async def test_a_stale_bar_write_to_an_unreachable_device_connects_once(
        monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> None:
    # The re-read falls back to the held bar only when it fails on a live link: the link comes up before it,
    # so a device that cannot be reached raises after one connect's attempts, as the write alone did at
    # d47d22a, and the write does not connect (and wait out an operation deadline) a second time.
    fake = FakeGovee()
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    fake.establish_error = BleakError("device not found")
    d = make_device(ble_device("AA:BB:CC:DD:EE:30", "GVH6047"), "H6047")
    assert d is not None
    d._state.zone_power = {0: False, 1: True}
    d._state.zone_power_confirmed = {0: 100.0, 1: 100.0}
    clock.now = 100.0 + ZONE_POWER_FRESH_SECONDS + 180.0
    with pytest.raises(GoveeBleConnectionError):
        await d.set_zone_power("left", True)
    assert SESSION_ATTEMPTS == 2 and fake.establish_calls == SESSION_ATTEMPTS
    assert fake.received == []
    await d.stop()


async def test_send_many_holds_the_connection_lock_across_its_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    # MISSED-4: a write queued while a batch is mid-way goes out after the whole batch, never between its
    # frames (GoveeConnection.send_many runs every frame in one _run hold).
    fake = FakeGovee()
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    c = GoveeConnection(ble_device("AA:BB:CC:DD:EE:30", "GVH60A6"), encryption=Encryption.AES_RC4_PSK)
    c._idle_disconnect = 0
    batch = asyncio.ensure_future(c.send_many([frame(0x33, 0x01, 0x01), frame(0x33, 0x04, 50)]))
    await asyncio.sleep(0)   # the batch holds the lock
    await asyncio.gather(batch, c.send(frame(0x33, 0x30, 0x00, 0x00)))
    assert [f[:2].hex() for f in fake.received] == ["3301", "3304", "3330"]
    await c.close()


async def test_a_zone_off_never_slips_between_turn_on_frames(
        monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> None:
    # MISSED-4: turn_on stamps every zone lit once its batch (33 01 01, then 33 04) is acked. That holds only
    # because no other write lands between the two frames: a 33 30 off slipped in there would be applied
    # before the batch's fixture-wide "on", leaving the main zone stamped lit while it is off.
    fake = FakeGovee()
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    d = make_device(ble_device("AA:BB:CC:DD:EE:30", "GVH60A6"), "H60A6")
    assert d is not None
    d._conn._idle_disconnect = 0
    on = asyncio.ensure_future(d.turn_on(brightness=50))
    await asyncio.sleep(0)
    await asyncio.gather(on, d.set_zone_power("main", False))
    assert [f[:4].hex() for f in fake.received] == ["33010100", "33043200", "33300000"]
    assert d.state.zone_power == {0: False, 1: True}
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}
    await d.stop()


@pytest.mark.parametrize("wake", ["33 05", "33 04"])
def test_a_wake_before_a_posted_zone_on_applied_drops_the_off_stamps(clock: _Clock, wake: str) -> None:
    # ZPC-2's other side, FIRMWARE-SOURCED: the dispatcher's wake check reads the main switch as the message
    # thread has left it. The fixture read off; an acked 33 30 ring-on makes is_on read on, but until the
    # thread applies it the main switch is still off, so the colour / brightness write after it wakes every
    # zone (case 5, or case 4's MSG_SET_TURN(1)): the main zone's "off" must not outlive it.
    d, _ = _dev("H60A6")
    dev = {"main": 0, 0: 0, 1: 0}
    _flags_h60a6(d, dev, lag=True)
    asyncio.run(d.update())
    assert d.state.zone_power_confirmed == {0: 100.0, 1: 100.0}   # the zones' "off" (MISSED-2)
    clock.now = 110.0
    asyncio.run(d.set_zone_power("background", True))
    assert d.state.is_on is True and d._power_unsure is False and dev["main"] == 0
    clock.now = 120.0
    asyncio.run(d.set_rgb((255, 0, 0)) if wake == "33 05" else d.set_brightness(50))
    assert d.state.zone_power == {0: False, 1: True} and 0 not in d.state.zone_power_confirmed
    asyncio.run(d.update())   # the posted writes apply after this reply
    asyncio.run(d.update())
    assert dev == {"main": 1, 0: 1, 1: 1}
    assert d.state.zone_power == {0: True, 1: True} and d.state.zone_power_confirmed == {0: 120.0, 1: 120.0}


def test_a_colour_write_after_a_settled_zone_on_wakes_nothing(clock: _Clock) -> None:
    # Once a status reply carries the posted "on" the device is known to be on again: no wake, stamps kept.
    d, _ = _dev("H60A6")
    dev = {"main": 1, 0: 0, 1: 1}
    _flags_h60a6(d, dev)
    asyncio.run(d.update())
    clock.now = 110.0
    asyncio.run(d.set_zone_power("background", True))
    asyncio.run(d.update())
    clock.now = 120.0
    asyncio.run(d.set_rgb((255, 0, 0)))
    assert d.state.zone_power == {0: False, 1: True} and d.state.zone_power_confirmed == {0: 110.0, 1: 110.0}


def test_a_possible_wake_does_not_vouch_for_a_posted_zone_off(clock: _Clock) -> None:
    # A 33 04 counted as a possible wake (a posted ring-on no reply has carried yet) posts MSG_SET_TURN(1)
    # only if the main switch is off when it is dispatched. Here it is on, so the main zone's posted "off"
    # still applies: a reply answered before the thread ran (main zone lit) must not vouch for "on" on the
    # strength of the wake.
    d, _ = _dev("H60A6")
    dev = {"main": 1, 0: 1, 1: 1}
    _flags_h60a6(d, dev, lag=True)
    asyncio.run(d.update())
    clock.now = 110.0
    asyncio.run(d.set_zone_power("main", False))
    asyncio.run(d.set_zone_power("background", True))
    asyncio.run(d.set_brightness(50))
    assert d._zone_power_posted == {0: {False, True}, 1: {True}}
    clock.now = 120.0
    asyncio.run(d.update())   # answered before the posted writes applied
    assert dev == {"main": 1, 0: 0, 1: 1}
    assert d.state.zone_power == {0: True, 1: True} and 0 not in d.state.zone_power_confirmed
    asyncio.run(d.update())
    assert d.state.zone_power == {0: False, 1: True} and d.state.zone_power_confirmed == {0: 120.0, 1: 120.0}
