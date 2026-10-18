"""Device driven end-to-end over the fake peripheral (real transport, real wire layer)."""
from __future__ import annotations

from typing import Any

import pytest

from govee_ble_local import create_device
from govee_ble_local.scenes import load_scenes
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.wire import build

from fake_ble import FakeGovee, ble_device, frame
from test_connection import H60A6_BURST, IDLE_GAP, spy_rx_waits


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    # (BURST_IDLE_GAP is not patched: query() binds it as a default at import, so the real gap,
    # IDLE_GAP, applies and the burst test below asserts against it.)
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.1)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_STEP2_TIMEOUT", 0.1)


def _device(monkeypatch: pytest.MonkeyPatch, fake: FakeGovee, sku: str, **kw: object):
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    dev = create_device(ble_device("AA:BB:CC:DD:EE:01", f"GV{sku}"), sku, **kw)  # type: ignore[arg-type]
    dev._conn._idle_disconnect = 0
    return dev


async def test_h60a6_poll_lost_terminator_is_fast_and_uses_no_state(monkeypatch) -> None:
    fake = FakeGovee()
    fake.status_burst = H60A6_BURST
    fake.drop_terminal = True
    fake.read_replies[0x05] = frame(0xAA, 0x05, 0x15, 0x01, 0x0A, 0x8C)  # CCT 2700 K
    dev = _device(monkeypatch, fake, "H60A6")
    waits = spy_rx_waits(monkeypatch, dev._conn)
    status_waits: list[float] = []  # waits made inside the 0xAC status query only
    real_query = dev._conn.query

    async def spy_query(*a: object, **k: object):
        start = len(waits)
        try:
            return await real_query(*a, **k)
        finally:
            if not status_waits:
                status_waits.extend(waits[start:])

    monkeypatch.setattr(dev._conn, "query", spy_query)
    st = await dev.update()
    # The status burst ended on the idle gap (last wait bounded by it), not the 5 s query timeout.
    assert status_waits and status_waits[-1] <= IDLE_GAP
    # WAS: the prefix's power, brightness, zones and mode. The app never completes such a read, so it
    # applies nothing: the library uses no state from it, retries once, then leaves the state stale.
    assert st.is_on is None and st.brightness is None and st.zone_power == {}
    assert st.color_temp_kelvin is None
    assert sum(1 for f in fake.received if f[0] == 0xAC) == 2  # the one retry


async def test_h60a6_turn_on_batch(monkeypatch) -> None:
    fake = FakeGovee()
    dev = _device(monkeypatch, fake, "H60A6")
    await dev.turn_on(brightness=40, kelvin=4000)
    # power, brightness, one all-segments CCT frame
    assert [f[:2].hex() for f in fake.received] == ["3301", "3304", "3305"]
    assert len(fake.clients) == 1
    assert dev.state.is_on and dev.state.zone_power == {0: True, 1: True}
    assert dev.state.color_temp_kelvin == 4000 and dev.state.brightness == 40


async def test_polled_bulb_update(monkeypatch) -> None:
    fake = FakeGovee(encrypted=False)
    fake.read_replies[0x01] = frame(0xAA, 0x01, 0x01)
    fake.read_replies[0x04] = frame(0xAA, 0x04, 0x32)
    fake.read_replies[0x05] = frame(0xAA, 0x05, 0x04, 0x82, 0x4A)
    dev = _device(monkeypatch, fake, "H6006")
    st = await dev.update()
    assert st.is_on is True and st.brightness == 50 and st.scene_code == 0x4A82
    assert st.optimistic is False


async def test_h6047_update_reads_bars(monkeypatch) -> None:
    fake = FakeGovee(encrypted=False)
    fake.read_replies[0x36] = frame(0xAA, 0x36, 0x01, 0x00)
    dev = _device(monkeypatch, fake, "H6047")
    st = await dev.update()
    assert st.zone_power == {0: True, 1: False}


async def test_scene_by_name_uploads_then_activates(monkeypatch) -> None:
    # The upload frames, then the activation, over the real transport (the pacing between them
    # is checked in test_scene_upload_e2e.py).
    fake = FakeGovee(encrypted=False)
    dev = _device(monkeypatch, fake, "H6006")
    name = next(n for n in dev.scene_names if dev.scene_upload_frames(load_scenes("H6006")[n]))
    scene = load_scenes("H6006")[name]
    frames = dev.scene_upload_frames(scene)
    await dev.set_scene_by_name(name)
    assert frames and fake.received == [*frames, build.scene_activate(scene.code)]
    assert dev.active_scene == name and dev.state.is_on


async def test_update_ble_device_reaches_next_connect(monkeypatch) -> None:
    # Home Assistant calls update_ble_device() on every advertisement so the next connect uses the
    # best proxy/adapter path: both the first attempt and the retry callback must see it.
    fake = FakeGovee(encrypted=False)
    dev = _device(monkeypatch, fake, "H6006")
    seen: list[Any] = []
    establish = fake.establish

    async def recording(cls: Any, device: Any, name: str, **kwargs: Any) -> Any:
        seen.append((device, kwargs["ble_device_callback"]()))
        return await establish(cls, device, name, **kwargs)

    monkeypatch.setattr(conn_mod, "establish_connection", recording)
    newer = ble_device("AA:BB:CC:DD:EE:01", "via-proxy")
    dev.update_ble_device(newer)
    await dev.connect()
    assert seen == [(newer, newer)]
    assert dev.name == "via-proxy"
    await dev.stop()


async def test_stop_closes(monkeypatch) -> None:
    fake = FakeGovee(encrypted=False)
    dev = _device(monkeypatch, fake, "H6006")
    await dev.set_power(True)
    await dev.stop()
    assert fake.clients[0].is_connected is False
