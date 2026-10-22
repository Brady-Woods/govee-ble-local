"""Device concurrency: stale update() reads must not overwrite a setter (BUG-4), the relay
relay power write applies state before a best-effort sync-time (BUG-9/D-13), and release() lands
only between whole sequences.

Deterministic: interleavings are driven with asyncio.Event gates on a fake ``_conn`` (no sleeps,
no timing assertions). A gated read is "in flight" (issued, reply not yet applied) while the
setter runs to completion, then the read is released. The release() tests drive the real transport
over the generic fake peripheral instead, gated the same way (on a virtual clock where the heartbeat's
cadence is counted).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

import pytest
from fake_ble import FakeGovee, ble_device, frame, virtual_time

from govee_ble_local import create_device
from govee_ble_local.devices import device as device_mod
from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import PROFILES
from govee_ble_local.exceptions import GoveeBleCommandRejected, GoveeBleConnectionError, GoveeBleTimeout
from govee_ble_local.scenes import load_scenes
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.wire import build
from test_device_v3 import _STATUS_BURST, _reply


class Gate:
    """One gated query: signals ``entered`` when issued, replies only after ``release``."""

    def __init__(self, match: Callable[[bytes], bool], reply: list[bytes]) -> None:
        self.match, self.reply = match, reply
        self.entered, self.release = asyncio.Event(), asyncio.Event()


def _dev(sku: str, replies: dict[int, list[bytes]] | None = None, gates: Sequence[Gate] = ()) -> Device:
    d = make_device(ble_device("AA:BB:CC:DD:EE:70", sku), sku)
    assert d is not None
    table = replies or {}
    d._sent = []  # type: ignore[attr-defined]

    async def query(frame: bytes, **_k: Any) -> list[bytes]:
        for g in gates:
            if g.match(frame):
                g.entered.set()
                await g.release.wait()
                return list(g.reply)
        return list(table.get(frame[1], []))

    async def send(frame: bytes, **_k: Any) -> None:
        d._sent.append(frame)  # type: ignore[attr-defined]

    async def send_many(frames: list[bytes]) -> None:
        d._sent.extend(frames)  # type: ignore[attr-defined]

    d._conn.query = query  # type: ignore[method-assign,assignment]
    d._conn.send = send  # type: ignore[method-assign,assignment]
    d._conn.send_many = send_many  # type: ignore[method-assign,assignment]
    return d


def _cmd(byte: int) -> Callable[[bytes], bool]:
    return lambda f: f[0] == 0xAA and f[1] == byte


async def _poll_during(d: Device, gate: Gate, setter: Any) -> None:
    """Start update(), wait until `gate` is in flight, run `setter` to completion, release."""
    task = asyncio.create_task(d.update())
    await gate.entered.wait()
    await setter()
    gate.release.set()
    await task


# -- BUG-4: stale reads discarded -------------------------------------------------------------
async def test_polled_power_read_does_not_overwrite_set_power() -> None:
    g = Gate(_cmd(0x01), [_reply(0xAA, 0x01, 0x00)])           # device still reports OFF
    d = _dev("H6006", gates=[g])
    await _poll_during(d, g, lambda: d.set_power(True))
    assert d.state.is_on is True


async def test_polled_brightness_read_does_not_overwrite_set_brightness() -> None:
    g = Gate(_cmd(0x04), [_reply(0xAA, 0x04, 0x0A)])
    d = _dev("H6006", {0x01: [_reply(0xAA, 0x01, 0x01)]}, gates=[g])
    await _poll_during(d, g, lambda: d.set_brightness(77))
    assert d.state.brightness == 77


async def test_status_read_does_not_overwrite_set_power_and_skips_authoritative() -> None:
    g = Gate(lambda f: f[0] == 0xAC, list(_STATUS_BURST))      # burst says is_on=True, 80%
    d = _dev("H60A6", gates=[g])
    await _poll_during(d, g, lambda: d.set_power(False))
    assert d.state.is_on is False
    assert d.state.brightness is None                          # whole stale burst dropped
    assert d.state.optimistic is True                          # not marked as read-confirmed
    assert d._polled_once is False


async def test_mode_read_does_not_overwrite_set_rgb() -> None:
    # a stale mode reply (applied after the status/power reads) must not clobber the setter's colour.
    g = Gate(_cmd(0x05), [_reply(0xAA, 0x05, 0x0D, 255, 128, 64, 0, 0)])
    d = _dev("H6006", {0x01: [_reply(0xAA, 0x01, 0x01)]}, gates=[g])
    await _poll_during(d, g, lambda: d.set_rgb((1, 2, 3)))
    assert d.state.rgb_color == (1, 2, 3)


async def test_plug_read_does_not_overwrite_set_power() -> None:
    g = Gate(_cmd(0x01), [_reply(0xAA, 0x01, 0x00)])
    d = _dev("H5080", gates=[g])
    await _poll_during(d, g, lambda: d.set_power(True))
    assert d.state.is_on is True
    assert d.state.optimistic is True


async def test_gradual_read_does_not_overwrite_set_gradual() -> None:
    g = Gate(_cmd(0xA3), [_reply(0xAA, 0xA3, 0x00)])
    d = _dev("H61A8", {0x01: [_reply(0xAA, 0x01, 0x01)]}, gates=[g])
    await _poll_during(d, g, lambda: d.set_gradual(True))
    assert d.state.gradual is True


async def test_bar_switch_read_does_not_overwrite_set_zone_power() -> None:
    # H6047: poll reads bar switch (0x36); a zone toggle completes while it is in flight.
    g = Gate(_cmd(0x36), [_reply(0xAA, 0x36, 0x01, 0x01)])     # stale: both bars on
    d = _dev("H6047", gates=[g])
    d.state.zone_power = {0: True, 1: True}
    now = time.monotonic()   # both bars confirmed just now: the toggle writes without an aa 36 read first
    d.state.zone_power_confirmed = {0: now, 1: now}
    await _poll_during(d, g, lambda: d.set_zone_power("left", False))
    assert d.state.zone_power == {0: False, 1: True}


async def test_mechanism_b_segments_not_overwritten_by_stale_poll() -> None:
    g = Gate(
        lambda f: f[0] == 0xAA and f[1] == 0xA5,
        [_reply(0xAA, 0xA5, 1, 50, 7, 0, 0, 50, 7, 0, 0, 50, 7, 0, 0)],
    )
    d = _dev("H61A8", {0x01: [_reply(0xAA, 0x01, 0x01)]}, gates=[g])
    await _poll_during(d, g, lambda: d.set_segment_rgb([0], (9, 9, 9)))
    assert d.state.segments[0].rgb == (9, 9, 9)


# -- discard semantics -------------------------------------------------------------------------
async def test_discarded_poll_logs_debug_returns_current_state_and_defers_first_poll(
    caplog: pytest.LogCaptureFixture,
) -> None:
    g = Gate(_cmd(0x01), [_reply(0xAA, 0x01, 0x00)])
    d = _dev("H6006", gates=[g])
    caplog.set_level(logging.DEBUG, logger="govee_ble_local")
    task = asyncio.create_task(d.update())
    await g.entered.wait()
    await d.set_power(True)
    g.release.set()
    st = await task
    assert st is d.state and st.is_on is True
    assert d._polled_once is False                              # "first poll" not yet consumed
    assert any("discard" in r.getMessage().lower() and r.levelno == logging.DEBUG for r in caplog.records)
    # a later clean poll proceeds normally
    clean = _dev("H6006", {0x01: [_reply(0xAA, 0x01, 0x00)]})
    d._conn.query = clean._conn.query  # type: ignore[method-assign]
    await d.update()
    assert d.state.is_on is False and d._polled_once is True


async def test_read_not_discarded_without_concurrent_setter() -> None:
    d = _dev("H6006", {0x01: [_reply(0xAA, 0x01, 0x01)], 0x04: [_reply(0xAA, 0x04, 0x32)]})
    await d.update()
    assert d.state.is_on is True and d.state.brightness == 50 and d._polled_once is True


async def test_setter_not_blocked_by_in_flight_poll() -> None:
    # No device-wide lock: the setter completes while the poll is still waiting on its reply.
    g = Gate(_cmd(0x01), [_reply(0xAA, 0x01, 0x00)])
    d = _dev("H6006", gates=[g])
    task = asyncio.create_task(d.update())
    await g.entered.wait()
    await asyncio.wait_for(d.set_brightness(33), timeout=5)    # would hang if serialized
    assert not task.done() and d.state.brightness == 33
    g.release.set()
    await task


# -- relay power: the power frame alone; gt 50 / 90 then re-read and re-sync, as the app does -------
@pytest.mark.parametrize("sku", ["H5080", "H5083", "H5085", "H5089"])
async def test_relay_set_power_sends_only_the_power_frame(sku: str) -> None:
    # WAS: every plug's set_power sent an unacked 33 b5 after the power frame. The app sends it once per
    # connection (Device._session_frames); gt 43 (H5083NewDetailViewModel.y0) and gt 307
    # (BleOp4H5089.onEvent4TotalSwitch -> Y()) send nothing after a power write.
    d = _dev(sku)
    await d.set_power(True)
    assert len(d._sent) == 1 and d._sent[0][0] == 0x33           # type: ignore[attr-defined]
    assert d._sent[0][1] in (0x01, 0x02)                         # type: ignore[attr-defined]
    assert d.state.is_on is True


def _record_queries(d: Device) -> list[bytes]:
    reads: list[bytes] = []
    inner = d._conn.query

    async def query(frame: bytes, **k: Any) -> list[bytes]:
        reads.append(frame)
        return await inner(frame, **k)

    d._conn.query = query  # type: ignore[method-assign,assignment]
    return reads


def test_only_plugs_send_the_session_time_sync() -> None:
    for profile in PROFILES:
        for sku in profile.skus:
            d = Device(profile, ble_device("AA:BB:CC:DD:EE:71", sku), sku=sku)
            provider = d._conn._session_frames
            assert (provider is not None) == (profile.relay or profile.light_effect_stop), sku
            if provider is not None:
                assert [f[:2].hex() for f in provider()] == [
                    *(["33b5"] if profile.relay else ()), *(["3325"] if profile.light_effect_stop else ())]
            assert profile.power_resync == (sku in ("H5082", "H5160", "H5161")), sku


@pytest.mark.parametrize("sku", ["H5082", "H5160", "H5161"])
async def test_gt_50_90_power_write_rereads_versions_then_syncs_time(sku: str) -> None:
    # BleOpV2 / BleOpV4.onEventSwitchV2 -> G(): aa 06, aa 07 03, then 33 b5 (acked), after each power write.
    d = _dev(sku)
    reads = _record_queries(d)
    await d.set_power(False)
    assert [f[:2].hex() for f in d._sent] == ["3301", "33b5"]   # type: ignore[attr-defined]
    assert reads == [build.soft_version_query(), build.device_info_query(build.INFO_HARD)]
    await d.turn_on(brightness=None)
    assert [f[:2].hex() for f in d._sent[2:]] == ["3301", "33b5"]   # type: ignore[attr-defined]
    assert len(reads) == 4


async def test_gt_50_90_reread_follows_only_a_power_write() -> None:
    d = _dev("H5082")
    reads = _record_queries(d)
    await d.turn_on(power=False)                                 # nothing to send
    assert reads == [] and d._sent == []                         # type: ignore[attr-defined]


async def test_gt_50_90_reread_failure_does_not_raise_or_undo_state(caplog) -> None:
    # The power write succeeded, so its state is applied; a lost link in the re-read is only a WARNING.
    d = _dev("H5082")
    notified: list[Any] = []
    d.register_callback(lambda s: notified.append(s))

    async def boom(frame: bytes, **_k: Any) -> None:
        raise GoveeBleConnectionError("link down")

    d._conn.send = boom  # type: ignore[method-assign,assignment]
    caplog.set_level(logging.WARNING, logger="govee_ble_local")
    await d.set_power(True)                                      # must not raise
    assert d.state.is_on is True
    assert len(d._sent) == 1 and d._sent[0][:3].hex() == "3301ff"  # type: ignore[attr-defined]
    assert notified
    assert any(r.levelno == logging.WARNING and "link down" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("error", [GoveeBleTimeout("no ack"), GoveeBleCommandRejected("status 1")])
async def test_gt_50_90_unanswered_sync_time_is_only_debug(caplog, error: Exception) -> None:
    d = _dev("H5160")

    async def refuse(frame: bytes, **_k: Any) -> None:
        raise error

    d._conn.send = refuse  # type: ignore[method-assign,assignment]
    caplog.set_level(logging.DEBUG, logger="govee_ble_local")
    await d.set_power(True)
    assert d.state.is_on is True
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("post-power re-read not answered" in r.getMessage() for r in caplog.records)


async def test_relay_power_frame_failure_propagates_without_applying() -> None:
    # A failed power write still raises, state stays unapplied, and no sync-time is attempted.
    d = _dev("H5080")
    sync_calls: list[bytes] = []

    async def boom(frames: list[bytes]) -> None:
        raise GoveeBleConnectionError("link down")

    async def send(frame: bytes, **_k: Any) -> None:
        sync_calls.append(frame)

    d._conn.send_many = boom  # type: ignore[method-assign,assignment]
    d._conn.send = send  # type: ignore[method-assign,assignment]
    with pytest.raises(GoveeBleConnectionError):
        await d.set_power(True)
    assert d.state.is_on is None
    assert sync_calls == []


async def test_non_relay_set_power_sends_no_sync_time() -> None:
    d = _dev("H6006")
    await d.set_power(True)
    assert len(d._sent) == 1                                     # type: ignore[attr-defined]


# -- release(): the link drops between whole sequences, and the Device stays usable --------------------
@pytest.fixture
def fast_acks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.1)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_STEP2_TIMEOUT", 0.1)


def _live(monkeypatch: pytest.MonkeyPatch, fake: FakeGovee, sku: str, **kw: Any) -> Device:
    """A Device on the real transport over ``fake``, whose link stays up until it is released."""
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    dev = create_device(ble_device("AA:BB:CC:DD:EE:72", f"GV{sku}"), sku, **kw)
    dev._conn._idle_disconnect = 0
    return dev


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def _released(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "link released by caller" in r.getMessage()]


async def test_release_without_a_link_is_a_no_op(monkeypatch: pytest.MonkeyPatch, fast_acks: None,
                                                 caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="govee_ble_local")
    fake = FakeGovee()
    dev = _live(monkeypatch, fake, "H6006")
    assert await dev.release() is False
    assert fake.establish_calls == 0 and fake.disconnects == 0 and not _released(caplog)


async def test_release_then_the_next_write_reconnects(monkeypatch: pytest.MonkeyPatch, fast_acks: None,
                                                      caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="govee_ble_local")
    fake = FakeGovee()
    dev = _live(monkeypatch, fake, "H6006")
    await dev.set_power(True)
    assert await dev.release() is True
    assert not dev._conn.is_connected and fake.disconnects == 1 and dev._conn._active_until == 0.0
    assert [r.levelno for r in _released(caplog)] == [logging.DEBUG]   # its own line, once
    await dev.set_brightness(50)                                        # acked on a new link
    assert len(fake.clients) == 2 and dev._conn.is_connected
    assert fake.received[-1][:2].hex() == "3304" and dev.state.brightness == 50
    await dev.stop()


async def test_release_never_splits_a_scene_upload_from_its_activation(monkeypatch: pytest.MonkeyPatch,
                                                                       fast_acks: None) -> None:
    fake = FakeGovee(encrypted=False)
    dev = _live(monkeypatch, fake, "H6047")
    scenes = load_scenes("H6047", scene_versions=dev.scene_versions)
    name = next(n for n in sorted(scenes) if dev.scene_upload_frames(scenes[n]) is not None)
    await dev.connect()
    uploaded, go = asyncio.Event(), asyncio.Event()
    real = dev._conn.send_unacked

    async def gated(*a: Any, **k: Any) -> None:
        await real(*a, **k)
        uploaded.set()
        await go.wait()            # the activation has not gone out yet (a slow proxy)

    monkeypatch.setattr(dev._conn, "send_unacked", gated)
    with virtual_time():
        scene = asyncio.ensure_future(dev.set_scene_by_name(name))
        await uploaded.wait()
        release = asyncio.ensure_future(dev.release())
        await _settle()
        assert not release.done() and dev._conn.is_connected and fake.disconnects == 0
        go.set()
        await scene
        assert await release is True
    link = fake.clients[0]
    assert len(fake.clients) == 1 and fake.disconnects == 1   # the activation went out on the upload's link
    assert link.writes[-1] == build.scene_activate(scenes[name].code)
    assert [w for w in link.writes if w[0] == 0xA3]
    await dev.stop()


async def test_release_waits_for_a_bar_read_modify_write(monkeypatch: pytest.MonkeyPatch,
                                                         fast_acks: None) -> None:
    # H6047: the aa 36 read and the 33 36 built from it are one sequence under _bar_lock alone (_read_gate is
    # held only while the read runs). A release between them would send the bars on a new link.
    fake = FakeGovee(encrypted=False)
    fake.read_replies[0x36] = frame(0xAA, 0x36, 0x01, 0x00)   # left on, right off
    dev = _live(monkeypatch, fake, "H6047")
    await dev.connect()
    read, go = asyncio.Event(), asyncio.Event()
    real = dev._commit

    async def gated(*a: Any, **k: Any) -> None:
        read.set()                 # the bars are read; their 33 36 is next
        await go.wait()
        await real(*a, **k)

    monkeypatch.setattr(dev, "_commit", gated)
    write = asyncio.ensure_future(dev.set_zone_power("right", True))
    await read.wait()
    assert dev._bar_lock.locked() and not dev._read_gate.locked() and not dev._conn._lock.locked()
    release = asyncio.ensure_future(dev.release())
    await _settle()
    assert not release.done() and dev._conn.is_connected and fake.disconnects == 0
    assert dev._scene_lock.locked() and not dev._read_gate.locked()   # holds _scene_lock, waits for _bar_lock
    go.set()
    await write
    assert await release is True
    link = fake.clients[0]
    assert len(fake.clients) == 1 and fake.disconnects == 1
    assert build.bar_switch_query() in link.writes and link.writes[-1] == build.bar_switch(True, True)
    await dev.stop()


async def test_release_takes_the_device_locks_in_their_order(monkeypatch: pytest.MonkeyPatch,
                                                             fast_acks: None) -> None:
    # _scene_lock, _bar_lock, _read_gate (then the connection's): held by a sequence, each one stops the
    # release with every lock before it taken and none after it.
    fake = FakeGovee(encrypted=False)
    dev = _live(monkeypatch, fake, "H6047")
    order = [dev._scene_lock, dev._bar_lock, dev._read_gate]
    for i, held in enumerate(order):
        await dev.connect()
        await held.acquire()
        release = asyncio.ensure_future(dev.release())
        await _settle()
        assert not release.done() and dev._conn.is_connected
        assert [lock.locked() for lock in order] == [True] * (i + 1) + [False] * (len(order) - i - 1)
        held.release()
        assert await release is True
        assert not any(lock.locked() for lock in order) and not dev._conn.is_connected
    assert fake.disconnects == len(order)
    await dev.stop()


async def test_release_cancelled_while_it_waits_drops_nothing(monkeypatch: pytest.MonkeyPatch,
                                                              fast_acks: None) -> None:
    fake = FakeGovee(encrypted=False)
    dev = _live(monkeypatch, fake, "H6047")
    await dev.connect()
    async with dev._bar_lock:      # a bar read-modify-write runs
        release = asyncio.ensure_future(dev.release())
        await _settle()
        assert dev._scene_lock.locked()
        release.cancel()
        with pytest.raises(asyncio.CancelledError):
            await release
        assert not dev._scene_lock.locked()        # it left the queue and gave up what it held
    assert dev._conn.is_connected and fake.disconnects == 0
    assert await dev.release() is True
    await dev.stop()


def _gate_reads(monkeypatch: pytest.MonkeyPatch, dev: Device,
                command: int) -> tuple[asyncio.Event, asyncio.Event]:
    """Hold the ``aa <command>`` read in flight (``_read_gate`` taken, the connection lock not) until
    ``go`` is set; ``reading`` is set when it starts."""
    reading, go = asyncio.Event(), asyncio.Event()
    real = dev._conn.query

    async def gated(frame_plaintext: bytes, **k: Any) -> list[bytes]:
        if frame_plaintext[:2] == bytes((0xAA, command)):
            reading.set()
            await go.wait()        # a slow reply
        return await real(frame_plaintext, **k)

    monkeypatch.setattr(dev._conn, "query", gated)
    return reading, go


async def test_release_gives_way_to_a_later_write_that_reaches_the_link_first(
        monkeypatch: pytest.MonkeyPatch, fast_acks: None, caplog: pytest.LogCaptureFixture) -> None:
    # A poll's read holds _read_gate, so release() waits for it; a setter called after release() takes no
    # Device lock and goes out first, on the current link. WAS: the release then dropped that link and
    # ended the setter's 30 s window, so a command arriving behind a poll lost its hold.
    caplog.set_level(logging.DEBUG, logger="govee_ble_local")
    fake = FakeGovee(encrypted=False)
    dev = _live(monkeypatch, fake, "H6006")
    await dev.connect()
    reading, go = _gate_reads(monkeypatch, dev, 0x01)
    read = asyncio.ensure_future(dev._read_reply(build.power_query()))
    await reading.wait()
    release = asyncio.ensure_future(dev.release())
    await _settle()
    assert not release.done() and dev._bar_lock.locked()   # holds the earlier locks, waits for _read_gate
    await dev.set_brightness(50)                             # called after release(): acked on this link
    go.set()
    await read
    assert await release is False
    assert dev._conn.is_connected and dev._conn._active_until > 0 and not _released(caplog)
    assert len(fake.clients) == 1 and fake.disconnects == 0
    assert [w for w in fake.clients[0].writes if w[:2] == b"\x33\x04"]
    await dev.stop()


async def test_release_gives_way_to_a_later_write_the_device_refuses(
        monkeypatch: pytest.MonkeyPatch, fast_acks: None, caplog: pytest.LogCaptureFixture) -> None:
    # As above, but the device refuses the setter's 33 04 (acked with status 1): it still reached the device
    # on the link that is up, so that link keeps the write's 30 s window and the release drops nothing.
    caplog.set_level(logging.DEBUG, logger="govee_ble_local")
    fake = FakeGovee(encrypted=False)
    dev = _live(monkeypatch, fake, "H6006")
    await dev.connect()
    reading, go = _gate_reads(monkeypatch, dev, 0x01)
    read = asyncio.ensure_future(dev._read_reply(build.power_query()))
    await reading.wait()
    release = asyncio.ensure_future(dev.release())
    await _settle()
    assert not release.done() and dev._bar_lock.locked()   # holds the earlier locks, waits for _read_gate
    fake.ack_status = 1                                      # the device refuses the next write
    with pytest.raises(GoveeBleCommandRejected):
        await dev.set_brightness(50)                         # called after release(): refused on this link
    go.set()
    await read
    assert await release is False
    assert dev._conn.is_connected and dev._conn._active_until > 0 and not _released(caplog)
    assert len(fake.clients) == 1 and fake.disconnects == 0
    assert [w for w in fake.clients[0].writes if w[:2] == b"\x33\x04"]
    await dev.stop()


async def test_release_gives_way_to_a_bar_write_handed_the_bar_lock_before_it(
        monkeypatch: pytest.MonkeyPatch, fast_acks: None) -> None:
    # H6047: release() waits for _scene_lock behind a scene, and a set_power called after it waits for
    # _bar_lock, which the scene hands it first. WAS: its 33 01 went out on the current link, then the
    # release dropped that link and ended its window.
    fake = FakeGovee(encrypted=False)
    dev = _live(monkeypatch, fake, "H6047")
    scenes = load_scenes("H6047", scene_versions=dev.scene_versions)
    name = next(n for n in sorted(scenes) if dev.scene_upload_frames(scenes[n]) is not None)
    await dev.connect()
    uploaded, go = asyncio.Event(), asyncio.Event()
    real = dev._conn.send_unacked

    async def gated(*a: Any, **k: Any) -> None:
        await real(*a, **k)
        uploaded.set()
        await go.wait()

    monkeypatch.setattr(dev._conn, "send_unacked", gated)
    with virtual_time():
        scene = asyncio.ensure_future(dev.set_scene_by_name(name))
        await uploaded.wait()
        release = asyncio.ensure_future(dev.release())
        await _settle()
        power = asyncio.ensure_future(dev.set_power(False))   # called after release()
        await _settle()
        go.set()
        await scene
        await power
        assert await release is False
    assert len(fake.clients) == 1 and fake.clients[0].writes[-1] == build.switch(False)
    assert dev._conn.is_connected and dev._conn._active_until > 0 and fake.disconnects == 0
    await dev.stop()


@pytest.mark.parametrize("cancelled", [True, False])
async def test_release_gives_way_only_to_a_later_write_that_went_out_on_the_link(
        monkeypatch: pytest.MonkeyPatch, fast_acks: None, cancelled: bool) -> None:
    # H6006: a poll's read (a 1 s reply) holds the connection lock, so release() waits for _read_gate, and a
    # set_brightness called after it queues on the connection lock. Cancelled there (the integration's call
    # timeout, an unload), no 33 04 goes out, and the release drops the link. WAS: it gave way to that call's
    # number, returned False and left the link up with 29 s of the earlier command's window. Not cancelled,
    # the write gets the connection lock ahead of the drop and keeps the link (the check runs under the
    # connection lock, after it).
    fake = FakeGovee(encrypted=False)

    def on_write(client: Any, plain: bytes) -> None:
        if plain[0] == 0x33:
            client.push(frame(0x33, plain[1], 0x00), 0.005)
        elif plain[0] == 0xAA:
            client.push(fake.read_replies.get(plain[1], frame(0xAA, plain[1], 0x01)),
                        1.0 if plain[1] == 0x01 else 0.005)

    fake.on_write = on_write
    dev = _live(monkeypatch, fake, "H6006")
    dev._conn._idle_disconnect = 8.0
    with virtual_time():
        await dev.set_power(True)                                        # a command: its 30 s window
        poll = asyncio.ensure_future(dev._read_reply(build.power_query()))
        await _settle()
        assert dev._read_gate.locked() and dev._conn._lock.locked()
        release = asyncio.ensure_future(dev.release())
        await _settle()
        later = asyncio.ensure_future(dev.set_brightness(40))           # called after release()
        await _settle()
        assert not later.done()
        if cancelled:
            later.cancel()
            with pytest.raises(asyncio.CancelledError):
                await later
        await poll
        assert await release is cancelled                            # dropped only if it never went out
        if not cancelled:
            await later
    assert bool([w for w in fake.clients[0].writes if w[:2] == b"\x33\x04"]) is not cancelled
    assert dev._conn.is_connected is not cancelled and fake.disconnects == int(cancelled)
    assert (dev._conn._active_until == 0.0) is cancelled
    await dev.stop()


async def test_release_drops_a_link_the_later_write_never_used(monkeypatch: pytest.MonkeyPatch,
                                                               fast_acks: None) -> None:
    # H6047: a scene holds _scene_lock between its upload and its activation, so release() waits for it; a
    # set_brightness called after release() goes out on link 1, then the device ends link 1, and the
    # activation (a call made before release()) reconnects on link 2. WAS: the release still found the later
    # call's number and kept link 2, which that write never used, for the activation's 30 s window.
    fake = FakeGovee(encrypted=False)
    dev = _live(monkeypatch, fake, "H6047")
    dev._conn._idle_disconnect = 8.0
    scenes = load_scenes("H6047", scene_versions=dev.scene_versions)
    name = next(n for n in sorted(scenes) if dev.scene_upload_frames(scenes[n]) is not None)
    await dev.connect()
    uploaded, go = asyncio.Event(), asyncio.Event()
    real = dev._conn.send_unacked

    async def gated(*a: Any, **k: Any) -> None:
        await real(*a, **k)
        uploaded.set()
        await go.wait()

    monkeypatch.setattr(dev._conn, "send_unacked", gated)
    with virtual_time():
        scene = asyncio.ensure_future(dev.set_scene_by_name(name))
        await uploaded.wait()
        release = asyncio.ensure_future(dev.release())
        await _settle()
        await dev.set_brightness(40)                                     # called after release(): link 1
        assert len(fake.clients) == 1
        fake.clients[0].drop()                                           # the device ends link 1
        go.set()
        await scene                                                      # the activation: link 2
        assert await release is True
    assert [w[:2] for w in fake.clients[1].writes if w[0] == 0x33] == [b"\x33\x05"]
    assert len(fake.clients) == 2 and not dev._conn.is_connected and dev._conn._active_until == 0.0
    await dev.stop()


async def test_release_drops_a_link_a_poll_reopened_after_the_later_write_lost_its_own(
        monkeypatch: pytest.MonkeyPatch, fast_acks: None) -> None:
    # H6006: release() waits for a poll's read; a set_brightness called after it goes out on link 1, the
    # device ends link 1, and the read reconnects on link 2. Link 2 carries no write: the release drops it.
    fake = FakeGovee(encrypted=False)
    dev = _live(monkeypatch, fake, "H6006")
    await dev.connect()
    reading, go = _gate_reads(monkeypatch, dev, 0x01)
    read = asyncio.ensure_future(dev._read_reply(build.power_query()))
    await reading.wait()
    release = asyncio.ensure_future(dev.release())
    await _settle()
    await dev.set_brightness(50)                                         # called after release(): link 1
    fake.clients[0].drop()
    go.set()
    await read                                                           # reconnects: link 2
    assert await release is True
    assert len(fake.clients) == 2 and not [w for w in fake.clients[1].writes if w[0] == 0x33]
    assert not dev._conn.is_connected
    await dev.stop()


async def test_release_waits_for_a_scene_by_name_still_loading_its_catalog(
        monkeypatch: pytest.MonkeyPatch, fast_acks: None) -> None:
    # set_scene_by_name holds _scene_lock from the start, over its catalog load (a worker-thread hop, here
    # a 50 ms await, as a first load from disk takes), so a release() called after it starts drops the link
    # after the activation. WAS: release() found every lock free while the catalog loaded, dropped link 1,
    # and the scene then reconnected and held link 2 for its 30 s window.
    fake = FakeGovee(encrypted=False)
    dev = _live(monkeypatch, fake, "H6047")
    scenes = load_scenes("H6047", scene_versions=dev.scene_versions)
    name = next(n for n in sorted(scenes) if dev.scene_upload_frames(scenes[n]) is not None)

    async def catalog(sku: str, *, scene_versions: Any = None) -> Any:
        await asyncio.sleep(0.05)
        return load_scenes(sku, scene_versions=scene_versions)

    monkeypatch.setattr(device_mod, "async_load_scenes", catalog)
    await dev.set_power(True)
    scene = asyncio.ensure_future(dev.set_scene_by_name(name))
    await asyncio.sleep(0)
    release = asyncio.ensure_future(dev.release())                       # called after the scene
    await scene
    assert await release is True
    assert len(fake.clients) == 1 and not dev._conn.is_connected
    assert fake.clients[0].writes[-1] == build.scene_activate(scenes[name].code)
    await dev.stop()


@pytest.mark.parametrize("call", ["set_power", "turn_on", "set_zone_power"])
async def test_release_drops_after_a_bar_lock_waiter_called_before_it(monkeypatch: pytest.MonkeyPatch,
                                                                      fast_acks: None, call: str) -> None:
    # H6047: a bar read-modify-write holds _bar_lock; a write that waits for it is called, then release(). The
    # write was called first, so its number (taken as the call starts, not at its _commit) is the release's
    # to wait for: the link drops after it.
    fake = FakeGovee(encrypted=False)
    fake.read_replies[0x36] = frame(0xAA, 0x36, 0x01, 0x00)
    dev = _live(monkeypatch, fake, "H6047")
    await dev.connect()
    read, go = asyncio.Event(), asyncio.Event()
    real = dev._commit
    first = [True]

    async def gated(*a: Any, **k: Any) -> None:
        if first[0]:
            first[0] = False
            read.set()
            await go.wait()
        await real(*a, **k)

    monkeypatch.setattr(dev, "_commit", gated)
    holder = asyncio.ensure_future(dev.set_zone_power("right", True))
    await read.wait()
    waiter = asyncio.ensure_future({"set_power": lambda: dev.set_power(False),
                                    "turn_on": lambda: dev.turn_on(brightness=50),
                                    "set_zone_power": lambda: dev.set_zone_power("left", False)}[call]())
    await _settle()
    assert not waiter.done()                                             # waits for _bar_lock
    release = asyncio.ensure_future(dev.release())
    await _settle()
    go.set()
    await holder
    await waiter
    assert await release is True
    assert len(fake.clients) == 1 and not dev._conn.is_connected
    await dev.stop()


async def test_release_drops_after_a_set_scene_called_before_it(monkeypatch: pytest.MonkeyPatch,
                                                               fast_acks: None) -> None:
    # H6047: set_scene(code) waits for _scene_lock behind a scene by name, then release() is called: the
    # activation was called first and goes out on the link the release then drops.
    fake = FakeGovee(encrypted=False)
    dev = _live(monkeypatch, fake, "H6047")
    scenes = load_scenes("H6047", scene_versions=dev.scene_versions)
    name = next(n for n in sorted(scenes) if dev.scene_upload_frames(scenes[n]) is not None)
    await dev.connect()
    uploaded, go = asyncio.Event(), asyncio.Event()
    real = dev._conn.send_unacked

    async def gated(*a: Any, **k: Any) -> None:
        await real(*a, **k)
        uploaded.set()
        await go.wait()

    monkeypatch.setattr(dev._conn, "send_unacked", gated)
    with virtual_time():
        scene = asyncio.ensure_future(dev.set_scene_by_name(name))
        await uploaded.wait()
        code = asyncio.ensure_future(dev.set_scene(scenes[name].code))   # called before release()
        await _settle()
        release = asyncio.ensure_future(dev.release())
        await _settle()
        go.set()
        await scene
        await code
        assert await release is True
    assert len(fake.clients) == 1 and not dev._conn.is_connected
    await dev.stop()


async def test_a_write_queued_behind_release_reconnects(monkeypatch: pytest.MonkeyPatch,
                                                       fast_acks: None) -> None:
    fake = FakeGovee(encrypted=False)
    fake.on_write = lambda client, plain: client.push(frame(0x33, plain[1], 0x00), 0.05)   # a slow proxy
    dev = _live(monkeypatch, fake, "H6006")
    with virtual_time():
        await dev.set_power(True)
        running = asyncio.ensure_future(dev.set_brightness(20))
        await _settle()
        release = asyncio.ensure_future(dev.release())
        await _settle()
        assert dev._conn._lock_wanted == 2                    # the running write and the release
        later = asyncio.ensure_future(dev.set_brightness(30))
        await asyncio.gather(running, later)
        assert await release is True
    assert len(fake.clients) == 2 and fake.disconnects == 1
    assert fake.clients[0].writes[-1][:3] == bytes((0x33, 0x04, 20))
    assert fake.clients[1].writes[-1][:3] == bytes((0x33, 0x04, 30))
    await dev.stop()


def _hand_built_segment_kelvin(d: Device) -> Any:
    """set_segment_color_temp's aa 41 read, on a hand-built profile that keeps
    ``DeviceProfile.segment_color_temp``'s default: the table's H6062 refuses it before any I/O."""
    d.profile = replace(d.profile, segment_color_temp=True)
    return d.set_segment_color_temp([0, 1], 4000)


_H6062_COLOUR: dict[str, Callable[[Device], Any]] = {
    "set_rgb": lambda d: d.set_rgb((255, 0, 0)),
    "set_color_temp": lambda d: d.set_color_temp(4000),
    "set_segment_rgb": lambda d: d.set_segment_rgb([0, 1], (255, 0, 0)),
    "set_segment_brightness": lambda d: d.set_segment_brightness([0, 1], 40),
    "set_segment_color_temp(hand-built)": _hand_built_segment_kelvin,
    "turn_on(rgb)": lambda d: d.turn_on(rgb=(255, 0, 0)),
}


@pytest.mark.parametrize("call", list(_H6062_COLOUR))
async def test_release_drops_after_a_write_called_before_it_with_a_read_first(
        monkeypatch: pytest.MonkeyPatch, fast_acks: None, call: str) -> None:
    # H6062 reads its segment count (aa 41, under _read_gate) before its first colour write: a colour write
    # called before release() is still one release() waits for, though its write goes out after
    # release() started. Its number is taken when the call starts, not when its frames go out.
    fake = FakeGovee(encrypted=False)
    fake.read_replies[0x41] = frame(0xAA, 0x41, 0x00, 0x64, 0x0A)
    dev = _live(monkeypatch, fake, "H6062")
    assert dev.profile.section_count_read
    await dev.connect()
    reading, go = _gate_reads(monkeypatch, dev, 0x41)
    write = asyncio.ensure_future(_H6062_COLOUR[call](dev))
    await reading.wait()
    release = asyncio.ensure_future(dev.release())
    await _settle()
    assert not release.done()                                # waits for _read_gate
    go.set()
    await write
    assert await release is True
    assert len(fake.clients) == 1 and fake.disconnects == 1  # the colour went out on the link it dropped
    assert fake.clients[0].writes[-1][:2] == b"\x33\x05"
    await dev.stop()


async def test_release_leaves_a_pending_unlock_to_the_next_session(monkeypatch: pytest.MonkeyPatch,
                                                                   fast_acks: None) -> None:
    fake = FakeGovee()
    old, new = bytes(range(8)), bytes(range(8, 16))
    dev = _live(monkeypatch, fake, "H5080", secret=old)
    assert dev.profile.requires_secret
    await dev.connect()
    dev.set_secret(new)            # on the live session: its 33 b2 is pending
    mark = len(fake.received)      # before release(): it sends nothing itself
    assert await dev.release() is True
    await dev.set_power(True)
    await dev.set_power(False)     # a second operation on the new session: it sends no 33 b2 either
    sent = fake.received[mark:]
    unlocks = [f for f in sent if f[:2] == b"\x33\xb2"]
    assert unlocks == [build.secret_check(new)] and sent[0] == unlocks[0]   # once, by the new session's setup
    assert len(fake.clients) == 2
    await dev.stop()


async def test_release_after_stop_does_not_reopen(monkeypatch: pytest.MonkeyPatch, fast_acks: None) -> None:
    fake = FakeGovee()
    dev = _live(monkeypatch, fake, "H6006")
    await dev.connect()
    await dev.stop()
    assert await dev.release() is False
    with pytest.raises(GoveeBleConnectionError, match="connection closed"):
        await dev.set_power(True)
    assert fake.establish_calls == 1 and fake.disconnects == 1


async def test_release_stops_the_heartbeat_and_the_next_session_arms_one_chain(
        monkeypatch: pytest.MonkeyPatch, fast_acks: None) -> None:
    fake = FakeGovee()
    dev = _live(monkeypatch, fake, "H6006")
    beat = build.heartbeat(0x01)
    assert dev.profile.heartbeat == 0x01 and dev.profile.heartbeat_timing == "fixed_rate"
    interval = conn_mod.HEARTBEAT_INTERVAL
    with virtual_time():
        await dev.set_power(True)
        await asyncio.sleep(2 * interval + 0.1)
        assert fake.received.count(beat) == 2
        assert await dev.release() is True
        assert dev._conn._heartbeat_timer is None
        await asyncio.sleep(5 * interval)
        assert fake.received.count(beat) == 2                 # stopped with the link
        await dev.set_brightness(50)                          # the next session arms its own
        assert len(fake.clients) == 2 and dev._conn._heartbeat_timer is not None
        await asyncio.sleep(3 * interval + 0.1)
        assert fake.received.count(beat) == 5                 # one chain: a beat per interval
    await dev.stop()
