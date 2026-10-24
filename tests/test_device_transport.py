"""Device driven end-to-end over the fake peripheral (real transport, real wire layer)."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import FakeSceneFiles, gated_scene_profile, gated_scene_store

from govee_ble_local import create_device
from govee_ble_local.devices.device import Device
from govee_ble_local.devices.profile import DeviceProfile
from govee_ble_local import scenes as scenes_mod
from govee_ble_local.exceptions import GoveeBleCommandRejected, GoveeBleNotSupported, GoveeBleUnknownName
from govee_ble_local.models import Capability, Encryption
from govee_ble_local.scenes import load_scenes
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.wire import build

from fake_ble import SESSION_KEY, FakeGovee, ble_device, frame, virtual_time
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


async def test_scenes_follow_the_scene_set(monkeypatch) -> None:
    """Review G1: the scenes the Govee app lists at the device's scene set, and the effect it plays there. An
    H6168 (goodsType 136, a dreamcolorlightv1 row: {0, 1, 2} below part brightness) at soft 1.05.00 / hard
    1.00.01 has the set {0, 1, 2}, where the list drops its op-type-3 effects (devices.yaml
    scenes.list_filter): "Awaken" has none left and is not offered, and "Halloween" plays its RGB effect, code
    110. Before the device-info read the set is the newest firmware's, {0, 1, 2, 3}, with every scene; and
    set_scene_by_name reads the versions the set depends on first, on the same connection, so it never plays
    an effect the device's own set does not (review G1 item 3)."""
    store = gated_scene_store()   # (reads the bundled store first)
    fake = FakeGovee(encrypted=False)
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    dev = Device(gated_scene_profile(), ble_device("AA:BB:CC:DD:EE:01", "GVH6168"), sku="H6168")
    dev._conn._idle_disconnect = 0
    scenes_mod._store.cache_clear()
    monkeypatch.setattr(scenes_mod, "resources", SimpleNamespace(files=lambda _pkg: FakeSceneFiles(store)))
    try:
        await dev.update()   # warms the scene store; the fake answers no version read: both unread
        assert dev.scene_versions == {0, 1, 2, 3} and dev.scene_names == ["Awaken", "Halloween", "Plain"]
        union = load_scenes("H6168")["Halloween"]
        frames = dev.scene_upload_frames(union)
        assert union.code == 2497 and frames and len(frames) == 10 and frames[0][4] == 0x02   # V2 RGBIC
        reads = [build.soft_version_query(), build.device_info_query(build.INFO_HARD)]
        fake.received.clear()
        await dev.set_scene_by_name("Halloween")   # the versions read first: still unanswered, so the union's
        assert fake.received == [*reads, *frames, build.scene_activate(2497)]
        assert dev.active_scene == "Halloween"

        fake.read_replies[0x06] = frame(0xAA, 0x06, *b"1.05.00")
        fake.read_replies[0x07] = frame(0xAA, 0x07, 0x03, *b"1.00.01")
        there = load_scenes("H6168", scene_versions={0, 1, 2})["Halloween"]
        frames = scenes_mod.scene_upload_frames(there, dev.profile.scene_dialect, frozenset({0, 1, 2}))
        assert there.code == 110 and there.scene_type == 1 and frames and len(frames) == 3
        assert frames[0][:5] == bytes([0xA3, 0x00, 0x01, 3, 0x01])   # 3 packets, commByte 0x01 (V1 RGB)
        fake.received.clear()
        await dev.set_scene_by_name("Halloween")   # read first: {0, 1, 2}, so Halloween plays 110
        assert fake.received == [*reads, *frames, build.scene_activate(110)]
        assert dev.active_scene == "Halloween"
        assert (dev.state.firmware_version, dev.state.hardware_version) == ("1.05.00", "1.00.01")

        def unreadable(_pkg: str) -> Any:
            raise OSError(5, "I/O error")

        monkeypatch.setattr(scenes_mod, "resources", SimpleNamespace(files=unreadable))   # no read now
        assert dev.scene_versions == {0, 1, 2} and dev.scene_names == ["Halloween", "Plain"]
        dev._state.scene_code = 2497
        assert dev.active_scene is None   # 2497 is no scene listed at {0, 1, 2}: Halloween plays 110 there
        fake.received.clear()
        with pytest.raises(GoveeBleUnknownName, match=r"unknown scene 'Awaken' at scene set \[0, 1, 2\]"):
            await dev.set_scene_by_name("Awaken")
        assert fake.received == []   # versions read: no read, and nothing sent
    finally:
        scenes_mod._store.cache_clear()


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


@pytest.mark.parametrize("encrypted", [False, True])
async def test_a_per_command_profile_sends_each_write_on_its_own_session(
        monkeypatch, encrypted: bool) -> None:
    # The device-list card's session (DeviceProfile.session "per_command"): one frame per link, the reply read
    # only for a refusal, the link torn down after each, each one 1.1 s after the last release; no read.
    fake = FakeGovee(encrypted=encrypted)
    starts: list[tuple[float, float | None]] = []   # (loop time, the last release) at each connect

    async def establish(*a: Any, **k: Any) -> Any:
        starts.append((asyncio.get_running_loop().time(), dev._conn._released_at))
        return await fake.establish(*a, **k)

    monkeypatch.setattr(conn_mod, "establish_connection", establish)
    profile = DeviceProfile(skus=("HX1",), capabilities=frozenset({Capability.POWER, Capability.BRIGHTNESS}),
                            encryption=Encryption.NONE, session="per_command")
    dev = Device(profile, ble_device("AA:BB:CC:DD:EE:01", "GVHX1"), sku="HX1")
    key = SESSION_KEY if encrypted else None

    def refuse(cmd: int) -> Callable[[Any, bytes], None]:
        def ack(client: Any, plain: bytes) -> None:
            client.push(fake._wire(frame(0x33, plain[1], int(plain[1] == cmd)), key), 0.005)
        return ack

    with virtual_time():
        st = await dev.update()
        await dev.connect()
        with pytest.raises(GoveeBleNotSupported):
            await dev.read_secret()
        for colour in ({"rgb": (1, 2, 3)}, {"kelvin": 4000}):   # the card sends no colour: refused first
            with pytest.raises(GoveeBleNotSupported):
                await dev.turn_on(**colour)  # type: ignore[arg-type]
        assert starts == [] and fake.received == [] and st.optimistic and st.is_on is None
        # A brightness on a light not known to be on: the card's switch, then its slider.
        await dev.set_brightness(50)
        assert [f.hex() for f in fake.received] == [build.switch(True).hex(), build.brightness(50).hex()]
        assert len(fake.clients) == 2 and not any(c.is_connected for c in fake.clients)
        assert dev.state.is_on is True and dev.state.brightness == 50 and dev.state.optimistic
        # The tap is fresher than an advert still carrying the old switch byte (ADVERT_SUPPRESS_SECONDS).
        off = SimpleNamespace(manufacturer_data={0x8802: bytes([0xEC, 0, 0, 0, 0])})
        assert dev.ingest_advertisement(off) is False and dev.state.is_on is True  # type: ignore[arg-type]
        assert dev._conn._idle_timer is None and dev._conn._heartbeat_timer is None
        await dev.turn_on(power=False, brightness=60)   # known on: the slider alone
        fake.no_ack_cmds = {0x01}   # no reply: the write is the success, sent once
        await dev.turn_off()
        assert fake.received[2:] == [build.brightness(60), build.switch(False)]
        fake.no_ack_cmds, fake.on_write = set(), refuse(0x04)
        with pytest.raises(GoveeBleCommandRejected):   # the power-on lands, the refused brightness doesn't
            await dev.turn_on(brightness=40)
        assert fake.received[4:] == [build.switch(True), build.brightness(40)]
        assert dev.state.is_on is True and dev.state.brightness == 60
        fake.on_write = refuse(0x01)
        with pytest.raises(GoveeBleCommandRejected):
            await dev.set_power(False)
        assert dev.state.is_on is True and fake.received[6:] == [build.switch(False)]
        # The link lost while the reply is awaited: the write went out, which is the card's success.
        fake.on_write = lambda client, _plain: asyncio.get_running_loop().call_later(0.005, client.drop)
        await dev.set_power(False)
        # _tap_lock: a turn_off called while a brightness's two taps run lands after both, not between.
        fake.on_write, sent = None, len(fake.received)
        await asyncio.gather(dev.set_brightness(30), dev.turn_off())
        assert fake.received[sent:] == [build.switch(True), build.brightness(30), build.switch(False)]
    assert dev.state.is_on is False and len(fake.clients) == len(starts) == 11
    assert all(c.writes and not c.is_connected for c in fake.clients)
    assert all(start >= released + conn_mod.PER_COMMAND_SESSION_GAP
               for start, released in starts[1:] if released is not None)
    assert all(released is not None for _start, released in starts[1:])


async def test_stop_closes(monkeypatch) -> None:
    fake = FakeGovee(encrypted=False)
    dev = _device(monkeypatch, fake, "H6006")
    await dev.set_power(True)
    await dev.stop()
    assert fake.clients[0].is_connected is False
