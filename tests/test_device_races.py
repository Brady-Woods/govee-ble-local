"""Device concurrency: stale update() reads must not overwrite a setter (BUG-4) and the relay
relay power write applies state before a best-effort sync-time (BUG-9/D-13).

Deterministic: interleavings are driven with asyncio.Event gates on a fake ``_conn`` (no sleeps,
no timing assertions). A gated read is "in flight" (issued, reply not yet applied) while the
setter runs to completion, then the read is released.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from fake_ble import ble_device

from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import PROFILES
from govee_ble_local.exceptions import GoveeBleCommandRejected, GoveeBleConnectionError, GoveeBleTimeout
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
            d = make_device(ble_device("AA:BB:CC:DD:EE:71", sku), sku)
            assert d is not None
            provider = d._conn._session_frames
            assert (provider is not None) == profile.relay, sku
            if provider is not None:
                assert [f[:2].hex() for f in provider()] == ["33b5"]
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
