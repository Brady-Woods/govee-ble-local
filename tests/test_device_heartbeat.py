"""The heartbeat held across a Device's multi-operation sequences (GoveeConnection.hold_heartbeat): a poll's
reads, a scene's upload and activation, a bar read-modify-write and a plug's power write with its re-read go
out with no beat between them, however long the gaps (here 1-2.5 s of virtual time after each step, as a scene
catalog load or a slow proxy makes), while the unheld control run shows a beat landing in such a gap. Over the
generic fake peripheral, real transport and wire layer."""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from typing import Any

import pytest

from govee_ble_local import create_device
from govee_ble_local.devices.device import Device
from govee_ble_local.scenes import load_scenes
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.wire import build

from fake_ble import FakeGovee, ble_device, frame, virtual_time
from test_connection import H60A6_BURST


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.1)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_STEP2_TIMEOUT", 0.1)


def _device(monkeypatch: pytest.MonkeyPatch, fake: FakeGovee, sku: str, *, held: bool,
            **kw: object) -> Device:
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    dev = create_device(ble_device("AA:BB:CC:DD:EE:01", f"GV{sku}"), sku, **kw)  # type: ignore[arg-type]
    dev._conn._idle_disconnect = 0
    if not held:   # the control run: the Device's holds do nothing
        monkeypatch.setattr(dev._conn, "hold_heartbeat", contextlib.nullcontext)
    return dev


def _slow(monkeypatch: pytest.MonkeyPatch, dev: Device, method: str, gap: float) -> None:
    """``gap`` seconds pass after each call of the connection's ``method``, outside its lock."""
    real = getattr(dev._conn, method)

    async def slow(*a: Any, **k: Any) -> Any:
        try:
            return await real(*a, **k)
        finally:
            await asyncio.sleep(gap)

    monkeypatch.setattr(dev._conn, method, slow)


@contextlib.contextmanager
def _span(fake: FakeGovee) -> Iterator[list[bytes]]:
    """The frames the device received inside the block."""
    out: list[bytes] = []
    mark = len(fake.received)
    try:
        yield out
    finally:
        out.extend(fake.received[mark:])


@pytest.mark.parametrize("held", [True, False])
async def test_a_poll_sends_no_beat_between_its_reads(monkeypatch: pytest.MonkeyPatch, held: bool) -> None:
    fake = FakeGovee()
    fake.status_burst = H60A6_BURST
    dev = _device(monkeypatch, fake, "H60A6", held=held)
    beat = build.heartbeat(0x00)
    assert dev.profile.heartbeat == 0x00
    _slow(monkeypatch, dev, "query", 1.0)
    loop = asyncio.get_running_loop()
    with virtual_time():
        start = loop.time()
        with _span(fake) as polled:
            await dev.update()
        assert loop.time() - start > 2 * conn_mod.HEARTBEAT_INTERVAL   # four reads, a second after each
        reads = [f for f in polled if f != beat]
        assert [f[:2].hex() for f in reads] == ["ac03", "aa06", "aa07", "aa07"]
        if held:
            assert beat not in polled
            await asyncio.sleep(conn_mod.HEARTBEAT_INTERVAL)
            assert fake.received[-1] == beat   # the chain kept running: the next tick beats
        else:
            assert beat in polled   # the gaps leave room for a beat: the hold is what keeps it out
    await dev.stop()


@pytest.mark.parametrize("held", [True, False])
async def test_a_scene_upload_and_its_activation_hold_the_beat(monkeypatch: pytest.MonkeyPatch,
                                                               held: bool) -> None:
    fake = FakeGovee(encrypted=False)
    dev = _device(monkeypatch, fake, "H6047", held=held)
    beat = build.heartbeat(0x36)
    scenes = load_scenes("H6047", scene_versions=dev.scene_versions)
    name = next(n for n in sorted(scenes) if dev.scene_upload_frames(scenes[n]) is not None)
    await dev.connect()
    _slow(monkeypatch, dev, "send_unacked", 2.5)
    with virtual_time(), _span(fake) as sent:
        await dev.set_scene_by_name(name)
    activate = build.scene_activate(scenes[name].code)
    upload = [f for f in sent if f[0] == 0xA3]
    assert upload and sent[-1] == activate
    if held:
        assert sent == [*upload, activate]   # back to back, as the app's one composed controller
    else:
        assert beat in sent
    await dev.stop()


@pytest.mark.parametrize("held", [True, False])
async def test_a_bar_read_modify_write_holds_the_beat(monkeypatch: pytest.MonkeyPatch, held: bool) -> None:
    fake = FakeGovee(encrypted=False)
    fake.read_replies[0x36] = frame(0xAA, 0x36, 0x01, 0x00)   # left on, right off
    dev = _device(monkeypatch, fake, "H6047", held=held)
    await dev.connect()
    _slow(monkeypatch, dev, "query", 2.5)
    with virtual_time(), _span(fake) as sent:
        await dev.set_zone_power("right", True)
    read, write = build.bar_switch_query(), build.bar_switch(True, True)
    assert sent[-1] == write
    # aa 36 is both the bar read and this SKU's heartbeat: held, the one aa 36 is the read.
    assert sent.count(read) == (1 if held else 2)
    await dev.stop()


@pytest.mark.parametrize("held", [True, False])
async def test_a_plug_power_write_and_its_re_read_hold_the_beat(monkeypatch: pytest.MonkeyPatch,
                                                               held: bool) -> None:
    fake = FakeGovee()
    dev = _device(monkeypatch, fake, "H5082", held=held, secret=bytes(range(8)))
    assert dev.profile.power_resync and dev.profile.heartbeat == 0x01
    await dev.connect()
    _slow(monkeypatch, dev, "send", 1.5)
    _slow(monkeypatch, dev, "query", 1.5)
    with virtual_time(), _span(fake) as sent:
        await dev.set_power(False)
    heads = [f[:2].hex() for f in sent]
    assert [h for h in heads if h != "aa01"] == ["3301", "aa06", "aa07", "33b5"]
    assert ("aa01" in heads) is not held   # aa 01 is only ever the heartbeat here
    await dev.stop()
