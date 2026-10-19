"""Device.update()'s connect budget (``connect_attempts`` / ``connect_timeout``): a background poll at a
device that doesn't answer gives the device lock up sooner, while every other call keeps the defaults."""
from __future__ import annotations

import asyncio
import dataclasses
from typing import Any
from unittest.mock import AsyncMock

import pytest
from bleak.exc import BleakError

from govee_ble_local import create_device
from govee_ble_local.const import CONNECT_MAX_ATTEMPTS, OPERATION_TIMEOUT, SESSION_ATTEMPTS
from govee_ble_local.devices import profile as profile_mod
from govee_ble_local.devices.device import Device
from govee_ble_local.exceptions import GoveeBleConnectionError, GoveeBleInvalidValue, GoveeBleTimeout
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.transport.connection import ConnectBudget, GoveeConnection
from govee_ble_local.wire import build

from fake_ble import FakeGovee, ble_device

ADDR = "AA:BB:CC:DD:EE:01"


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.1)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_STEP2_TIMEOUT", 0.1)


def _bulb(monkeypatch: pytest.MonkeyPatch, fake: FakeGovee) -> Device:
    """An H6006 (polled bulb) over ``fake``. The fake is encrypted, so each connect runs the e7 handshake,
    which a dead link (``fake.dead_links``) leaves waiting for its HANDSHAKE_TIMEOUT (10 s)."""
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    dev = create_device(ble_device(ADDR, "GVH6006"), "H6006")
    dev._conn._idle_disconnect = 0
    return dev


def _spy_timeouts(monkeypatch: pytest.MonkeyPatch) -> list[float | None]:
    """The delay of every asyncio.timeout opened from now on: the connection's operation deadlines and
    connect limits."""
    seen: list[float | None] = []
    real = asyncio.timeout

    def spy(delay: float | None) -> asyncio.Timeout:
        seen.append(delay)
        return real(delay)

    monkeypatch.setattr(conn_mod.asyncio, "timeout", spy)
    return seen


async def test_the_defaults_keep_three_attempts_and_the_45s_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGovee()
    dev = _bulb(monkeypatch, fake)
    timeouts = _spy_timeouts(monkeypatch)
    assert (await dev.update()).is_on is True
    assert fake.connect_kwargs["max_attempts"] == CONNECT_MAX_ATTEMPTS == 3
    assert timeouts and set(timeouts) == {OPERATION_TIMEOUT} and OPERATION_TIMEOUT == 45.0  # no connect limit
    # A link that never comes up is still tried again by the session retry.
    await dev._conn.disconnect()
    fake.establish_error = BleakError("device not found")
    with pytest.raises(GoveeBleConnectionError):
        await dev.update()
    assert fake.establish_calls == 1 + SESSION_ATTEMPTS


async def test_connect_attempts_one_tries_the_link_once(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGovee()
    fake.establish_error = BleakError("device not found")
    dev = _bulb(monkeypatch, fake)
    with pytest.raises(GoveeBleConnectionError):
        await dev.update(connect_attempts=1)
    assert fake.establish_calls == 1 and fake.connect_kwargs["max_attempts"] == 1
    assert not dev._conn._lock.locked()


async def test_connect_attempts_keeps_the_session_retry_for_a_link_that_came_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conn_mod, "HANDSHAKE_TIMEOUT", 0.1)
    fake = FakeGovee()
    fake.dead_links = 1   # the first link never answers its handshake
    dev = _bulb(monkeypatch, fake)
    assert (await dev.update(connect_attempts=1)).is_on is True
    assert fake.establish_calls == 2 and fake.connect_kwargs["max_attempts"] == 1


@pytest.mark.parametrize("stall", ["establish", "handshake"])
async def test_connect_timeout_frees_the_device_and_leaves_the_connection_reusable(
    monkeypatch: pytest.MonkeyPatch, stall: str,
) -> None:
    fake = FakeGovee()
    dev = _bulb(monkeypatch, fake)
    conn = dev._conn
    cancelled: list[bool] = []
    if stall == "establish":   # bleak's connect never returns
        async def hanging(*_a: object, **_k: object) -> Any:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        monkeypatch.setattr(conn_mod, "establish_connection", hanging)
    else:   # the link is up, the device never answers the handshake
        fake.dead_links = 1
    with pytest.raises(GoveeBleTimeout, match=r"connect exceeded 0\.2s"):
        await dev.update(connect_timeout=0.2)
    assert not conn._lock.locked() and not dev._read_gate.locked()
    assert conn._client is None and not conn.is_connected and conn._session_key is None
    if stall == "establish":
        assert cancelled   # bleak's connect was cancelled, not left running
    else:
        assert not fake.clients[0].is_connected and fake.disconnects == 1   # the half-made link torn down
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    assert (await dev.update()).is_on is True   # the next operation connects afresh
    assert conn.is_connected


async def test_a_command_queued_behind_a_timed_out_poll_gets_the_lock_and_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGovee()
    fake.dead_links = 1   # the poll's link never answers its handshake
    linked = asyncio.Event()
    fake.link_up = lambda _client: linked.set()   # type: ignore[method-assign]
    dev = _bulb(monkeypatch, fake)
    poll = asyncio.create_task(dev.update(connect_attempts=1, connect_timeout=0.2))
    await linked.wait()   # the poll holds the device lock, in its handshake
    command = asyncio.create_task(dev.set_power(True))
    await asyncio.sleep(0.05)
    assert dev._conn._lock.locked() and not command.done()   # queued behind the poll
    with pytest.raises(GoveeBleTimeout):
        await poll
    await command
    assert dev.state.is_on is True and bytes.fromhex("330101") in [f[:3] for f in fake.received]
    # The command's own connect, at the defaults.
    assert len(fake.clients) == 2 and fake.connect_kwargs["max_attempts"] == CONNECT_MAX_ATTEMPTS


async def test_a_teardown_cut_short_by_the_connect_limit_is_still_abandoned_at_its_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conn_mod, "DISCONNECT_TIMEOUT", 0.2)
    fake = FakeGovee()
    fake.start_notify_error = BleakError("notify failed")   # the link comes up, its setup fails...
    fake.disconnect_hangs = True   # ...and its teardown wedges: the limit runs out inside that wait
    dev = _bulb(monkeypatch, fake)
    with pytest.raises(GoveeBleTimeout, match=r"connect exceeded 0\.05s"):
        await dev.update(connect_attempts=1, connect_timeout=0.05)
    teardowns = set(dev._conn._background)
    assert fake.disconnect_started == 1 and len(teardowns) == 1 and not dev._conn._lock.locked()
    await asyncio.sleep(0.3)   # past DISCONNECT_TIMEOUT from the teardown's start
    assert all(t.cancelled() for t in teardowns) and not dev._conn._background


async def test_a_connect_limit_running_out_on_the_pending_unlocks_reconnect_ends_the_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A secret set on a live plug session, and a device that never acks 33 b2: the unlock write's retry
    # reconnects under the poll's limit, and that session's own unlock outlasts it.
    fake = FakeGovee()
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    dev = create_device(ble_device(ADDR, "GVH5080"), "H5080", secret=b"\x01" * 8)
    dev._conn._idle_disconnect = 0
    await dev.connect()
    fake.no_ack_cmds = {0xB2}
    dev.set_secret(b"\x02" * 8)
    with pytest.raises(GoveeBleTimeout, match=r"connect exceeded 0\.15s"):
        await dev.update(connect_timeout=0.15)
    assert fake.establish_calls == 2   # the first link and one budgeted reconnect: no second connect
    assert not dev._conn._lock.locked() and not dev._conn.is_connected
    sent = len(fake.received)
    fake.no_ack_cmds = set()
    await dev.update()   # the next session setup sends the unlock
    assert build.secret_check(b"\x02" * 8) in fake.received[sent:]


@pytest.mark.parametrize("kw", [
    {"connect_attempts": 0}, {"connect_attempts": True}, {"connect_attempts": 1.5},
    {"connect_timeout": 0}, {"connect_timeout": float("nan")}, {"connect_timeout": float("inf")},
    {"connect_timeout": "5"}, {"connect_timeout": 10**400}, {"connect_timeout": 10**5000},
])
async def test_a_bad_budget_raises_before_any_io(monkeypatch: pytest.MonkeyPatch, kw: dict[str, Any]) -> None:
    fake = FakeGovee()
    dev = _bulb(monkeypatch, fake)
    with pytest.raises(GoveeBleInvalidValue):
        await dev.update(**kw)
    assert fake.establish_calls == 0


async def test_every_connection_call_of_a_poll_carries_its_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    # A live session with a secret set on it, so the poll also sends the pending 33 b2 unlock first.
    monkeypatch.setattr(GoveeConnection, "is_connected", property(lambda _self: True))
    budget = ConnectBudget(1, 5.0)
    # Every table profile, and the aa 40 connect read that only a custom profile makes today.
    custom = dataclasses.replace(profile_mod.PROFILES[0], ic_num_read_only=True)
    for profile in (*profile_mod.PROFILES, *profile_mod._MODELLED[0], custom):
        for kw, expected in (({}, None), ({"connect_attempts": 1, "connect_timeout": 5.0}, budget)):
            dev = Device(profile, ble_device(ADDR, None), secret=b"\x01" * 8)
            dev._unlock_pending = profile.requires_secret
            dev._conn.query = AsyncMock(return_value=[])   # type: ignore[method-assign]
            dev._conn.send = AsyncMock(return_value=None)   # type: ignore[method-assign]
            await dev.update(**kw)
            calls = [*dev._conn.query.call_args_list, *dev._conn.send.call_args_list]
            assert dev._conn.query.call_args_list or profile.readback == "none", profile.skus
            if expected is None:   # the transport's call shape, unchanged: no connect_budget keyword
                assert all("connect_budget" not in c.kwargs for c in calls), profile.skus
            else:
                assert all(c.kwargs["connect_budget"] == expected for c in calls), profile.skus
