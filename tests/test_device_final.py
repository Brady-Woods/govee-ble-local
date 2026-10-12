"""T11.1 final re-audit device fixes: bar-switch lock (N-1), device-info link errors in
update() (N-6), public scene_upload_frames only (N3), stale-read message (N-4)."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from test_device_races import Gate, _cmd, _dev
from test_device_v3 import _reply

from govee_ble_local.devices.device import Device
from govee_ble_local.exceptions import GoveeBleConnectionError, GoveeBleError
from govee_ble_local.wire import build


async def test_concurrent_set_zone_power_keeps_both_changes() -> None:
    g = Gate(_cmd(0x36), [_reply(0xAA, 0x36, 0x00, 0x00)])    # both bars off
    d = _dev("H6047", gates=[g])
    inner_send = d._conn.send

    async def send(frame: bytes, **k: Any) -> None:
        await asyncio.sleep(0)                                # a real send yields to the loop
        await inner_send(frame, **k)

    d._conn.send = send  # type: ignore[method-assign,assignment]
    left = asyncio.create_task(d.set_zone_power("left", True))
    await g.entered.wait()
    right = asyncio.create_task(d.set_zone_power("right", True))
    await asyncio.sleep(0)                                    # let `right` reach the lock
    g.release.set()
    await asyncio.gather(left, right)
    assert d.state.zone_power == {0: True, 1: True}
    assert d._sent[-1] == build.bar_switch(True, True)  # type: ignore[attr-defined]


async def test_stale_bar_read_raises_retry_message_before_sending() -> None:
    g = Gate(_cmd(0x36), [_reply(0xAA, 0x36, 0x01, 0x01)])
    d = _dev("H6047", gates=[g])
    task = asyncio.create_task(d.set_zone_power("left", False))
    await g.entered.wait()
    d._mutated()                                              # a setter applied state meanwhile
    g.release.set()
    with pytest.raises(GoveeBleError, match="state changed during read; retry"):
        await task
    assert d._sent == []  # type: ignore[attr-defined]


async def test_failed_bar_read_keeps_unknown_message() -> None:
    d = _dev("H6047")
    with pytest.raises(GoveeBleError, match="unknown"):
        await d.set_zone_power("left", True)


async def test_update_survives_device_info_link_error() -> None:
    # WAS: a link error in the device-info step propagated out of update() on every poll.
    d = _dev("H6006", {0x01: [_reply(0xAA, 0x01, 0x01)]})
    inner = d._conn.query
    calls: list[int] = []

    async def query(frame: bytes, **k: Any) -> list[bytes]:
        if frame[1] == 0x07:
            calls.append(1)
            raise GoveeBleConnectionError("link dropped")
        return await inner(frame, **k)

    d._conn.query = query  # type: ignore[method-assign,assignment]
    seen: list[object] = []
    d.register_callback(seen.append)
    st = await d.update()
    assert st.is_on is True and d._device_info_read is False and len(seen) == 1
    await d.update()
    assert len(calls) == 2                                    # retried next poll


def test_alias_removed() -> None:
    assert not hasattr(Device, "_scene_upload_frames")
    assert hasattr(Device, "scene_upload_frames")
