"""Device driven end-to-end over the fake peripheral (real transport, real wire layer)."""
from __future__ import annotations

import pytest

from govee_ble_local import create_device
from govee_ble_local.exceptions import GoveeBleAuthError
from govee_ble_local.transport import connection as conn_mod

from fake_ble import FakeGovee, ble_device, frame
from test_connection import H60A6_BURST, IDLE_GAP, spy_rx_waits


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.1)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_STEP2_TIMEOUT", 0.1)
    monkeypatch.setattr(conn_mod, "BURST_IDLE_GAP", 0.1)


def _device(monkeypatch: pytest.MonkeyPatch, fake: FakeGovee, sku: str, **kw: object):
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    dev = create_device(ble_device("AA:BB:CC:DD:EE:01", f"GV{sku}"), sku, **kw)  # type: ignore[arg-type]
    dev._conn._idle_disconnect = 0
    return dev


async def test_h60a6_poll_lost_terminator_is_fast_and_complete_enough(monkeypatch) -> None:
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
    assert st.is_on is False and st.brightness == 100 and st.zone_power == {0: False, 1: False}
    assert st.color_temp_kelvin == 2700
    assert st.firmware_version == "1.00.41"
    assert sum(1 for f in fake.received if f[0] == 0xAC) == 1  # no retry query needed


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


async def test_plug_wrong_secret_is_auth_error(monkeypatch) -> None:
    fake = FakeGovee()
    fake.ack_status = 1
    dev = _device(monkeypatch, fake, "H5083", secret=bytes(8))
    with pytest.raises(GoveeBleAuthError):
        await dev.set_power(True)


async def test_scene_upload_is_paced_then_activated(monkeypatch) -> None:
    fake = FakeGovee(encrypted=False)
    dev = _device(monkeypatch, fake, "H6006")
    name = next(n for n in dev.scene_names)
    await dev.set_scene_by_name(name)
    assert fake.received[-1][:3].hex() == "330504"
    assert dev.active_scene == name and dev.state.is_on


async def test_stop_closes(monkeypatch) -> None:
    fake = FakeGovee(encrypted=False)
    dev = _device(monkeypatch, fake, "H6006")
    await dev.set_power(True)
    await dev.stop()
    assert fake.clients[0].is_connected is False
