"""The heartbeat held across a Device's multi-operation sequences (GoveeConnection.hold_heartbeat): a poll's
reads, a scene's upload and activation, a bar read-modify-write, a plug's power write with its re-read and an
H6062's segment-count read with the colour write it sizes go out with no beat between them, however long the
gaps (here 1-2.5 s of virtual time after each step, as a scene catalog load or a slow proxy makes), while the
unheld control run shows a beat landing in such a gap. Over the generic fake peripheral, real transport and
wire layer."""
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
from govee_ble_local.wire.cct import tint_for_kelvin

from fake_ble import FakeGovee, ble_device, frame, status_burst, virtual_time
from test_connection import H60A6_BURST
from test_device_races import _H6062_COLOUR


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


@pytest.mark.parametrize(("sku", "follows", "reread"), [
    ("H703A", True, ["aa05", "aaa501"]),                                       # h61d3 263: one piece
    ("H617G", True, ["aa05", "aaa501", "aaa502", "aaa503", "aaa504"]),         # rgbiclight 351: 15 pieces
    ("H7025", True, []),                                                       # h7020 296: the switch alone
    ("H60A6", False, []),
])
async def test_a_status_row_on_the_aa_01_keep_alive_reads_once_a_link_and_follows_the_reply(
        monkeypatch: pytest.MonkeyPatch, sku: str, follows: bool, reread: list[str]) -> None:
    # heartbeat_power (the user's decision 17): the screen reads the 0xAC status once per connect, then
    # ShareVM.N3 -> J3 takes the power from each aa 01 keep-alive reply, a read that stamps power_read_at with
    # the drop count of its beat; where J3 re-reads on a change (Compose4Off2On: aa 05, then the screen's aa
    # a5 groups; DeviceProfile.heartbeat_reread_pieces) the library re-reads at once on that link, and where
    # it sets the switch alone (h7020 NewDetailVM.J3) it reads nothing. H60A6's aa 00 reply carries no switch
    # (BleComm.G): it reads the status on every poll, as before. A new link reads it again.
    fake = FakeGovee()
    fake.status_burst = status_burst(bytes([0x01, 0x01, 0x01]))   # on
    fake.read_replies[0x01] = frame(0xAA, 0x01, 0x00)              # the keep-alive reply: off
    dev = _device(monkeypatch, fake, sku, held=True, allow_modelled=True)
    assert dev.profile.heartbeat_power is follows
    beat_frame = build.heartbeat(dev.profile.heartbeat)
    with virtual_time():
        await dev.update()
        assert dev.state.is_on is True
        with _span(fake) as beat:   # two beats: the first reply changes the power, the second doesn't
            await asyncio.sleep(2 * conn_mod.HEARTBEAT_INTERVAL + 0.5)
        # one re-read in all: J3 re-reads only on a change (zSwitchCur != z)
        assert beat.count(beat_frame) == 2 and [f[:3].hex() if f[1] == 0xA5 else f[:2].hex() for f in beat
                                                if f[:2] in (b"\xaa\x05", b"\xaa\xa5")] == reread
        # stamped with is_on now off: the reply's stamp where it follows (the status read's gave on)
        assert dev.power_read_at is not None and dev.state.is_on is not follows
        with _span(fake) as polled:
            await dev.update()
        assert dev.state.is_on is not follows
        if follows:   # a drop after the beat (an advert, a write): the reply to that beat stamps nothing,
            dev._drop_power_read()   # and the reply to the next beat, sent after the drop, stamps again
            dev._on_beat_reply(frame(0xAA, 0x01, 0x00))
            assert dev.power_read_at is None and dev.state.is_on is False
            await asyncio.sleep(conn_mod.HEARTBEAT_INTERVAL)
            assert dev.power_read_at is not None
            # The link reads no status again, so a whole-device write sets every held piece, as the app's
            # whole-strip pick does (DelegateColorV2.F0 / G0 -> ColorModeUtil.e): H617G's 15 from the re-read.
            held = len(dev.state.segments)
            await dev.set_color_temp(3000)
            assert [s.rgb for s in dev.state.segments] == [tint_for_kelvin(3000)] * held
            await dev.set_rgb((1, 2, 3))
            assert [s.rgb for s in dev.state.segments] == [(1, 2, 3)] * held
        await dev.release()
        with _span(fake) as fresh:
            await dev.update()
    assert any(f[0] == 0xAC for f in polled) is not follows
    assert any(f[0] == 0xAC for f in fresh) and dev.state.is_on is True
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


@pytest.mark.parametrize("call", list(_H6062_COLOUR))
@pytest.mark.parametrize("held", [True, False])
async def test_a_segment_count_read_and_the_write_it_sizes_hold_the_beat(monkeypatch: pytest.MonkeyPatch,
                                                                         held: bool, call: str) -> None:
    # H6062 reads its segment count (aa 41) before its first colour write, as the app does in its connect
    # read, during which it sends no heartbeat: none between the read and the write, as in a poll.
    fake = FakeGovee(encrypted=False)
    fake.read_replies[0x41] = frame(0xAA, 0x41, 0x00, 0x64, 0x0A)
    dev = _device(monkeypatch, fake, "H6062", held=held)
    assert dev.profile.section_count_read and dev.profile.heartbeat == 0x01
    beat = build.heartbeat(0x01)
    await dev.connect()
    _slow(monkeypatch, dev, "query", 2.5)
    with virtual_time(), _span(fake) as sent:
        await _H6062_COLOUR[call](dev)
    heads = [f[:2].hex() for f in sent if f != beat]
    assert heads[0] == "aa41" and heads[-1] == "3305"
    assert (beat in sent) is not held
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
