"""Scene upload driven end-to-end: ``Device.set_scene_by_name`` over the fake peripheral.

For a DIY (0xA3) and a graffiti (0xA4) scene shipped in the H60A6 catalog, assert the exact
frame sequence the peripheral receives (upload frames, then the acked activation), that the
chunking/seq numbering is well-formed, that upload frames are unacked and paced, and that the
device ends with ``active_scene`` set."""
from __future__ import annotations

import asyncio
import base64
import itertools
from typing import Any

import pytest
import spec_frames as sf

from govee_ble_local import create_device
from govee_ble_local.const import UNACKED_SEND_GAP
from govee_ble_local.crypto import checksum
from govee_ble_local.scenes import load_scenes
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.wire import build

from fake_ble import FakeClient, FakeGovee, ble_device


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.2)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_STEP2_TIMEOUT", 0.1)


def _scene_names(dev: Any, prefix: int) -> list[str]:
    cat = load_scenes("H60A6")
    return [n for n, s in cat.items() if (f := dev.scene_upload_frames(s)) and f[0][0] == prefix]


def _make(monkeypatch: pytest.MonkeyPatch, fake: FakeGovee) -> Any:
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    dev = create_device(ble_device("AA:BB:CC:DD:EE:01", "GVH60A6"), "H60A6")
    dev._conn._idle_disconnect = 0
    return dev


def _check_a3(frames: list[bytes]) -> None:
    n = len(frames)
    assert n >= 2  # makeSendBytesV2 always sends START (seq 0) and a separate 0xFF END
    for i, f in enumerate(frames):
        assert len(f) == 20 and f[0] == 0xA3
        assert f[19] == checksum(f[:19])
        assert f[1] == (0xFF if i == n - 1 else i)  # seq 0.. , last 0xFF
    assert frames[0][2] == 0x01 and frames[0][3] == n  # marker + packet count
    assert frames[0][4] == build.COMM_H60A6


def _check_a4(frames: list[bytes]) -> None:
    n = len(frames)
    assert n >= 2
    for f in frames:
        assert 4 <= len(f) <= 20 and f[0] == 0xA4
        assert f[-1] == checksum(f[:-1])
    start, *mid, end = frames
    assert start[1:4] == bytes([0, 0, 1])
    assert start[6] == build.COMM_H60A6
    for k, f in enumerate(mid, start=1):  # middle packets are 1-based, little-endian seq
        assert f[1] | (f[2] << 8) == k
    assert end[1:3] == b"\xff\xff"
    assert (start[4] | (start[5] << 8)) == n  # START's count covers start + all following packets


@pytest.mark.parametrize(
    ("prefix", "checker"), [(0xA3, _check_a3), (0xA4, _check_a4)], ids=["diy_a3", "graffiti_a4"]
)
async def test_scene_upload_end_to_end(
    monkeypatch: pytest.MonkeyPatch, prefix: int, checker: Any
) -> None:
    fake = FakeGovee()
    dev = _make(monkeypatch, fake)
    name = _scene_names(dev, prefix)[0]
    scene = load_scenes("H60A6")[name]
    expected = dev.scene_upload_frames(scene)
    assert expected
    # ...and those are the app's frames: the ported makeSendBytesV2 / makeSendBytesMtu over the
    # scene's value (B_h60a6 DIY/graffiti: the param's leading byte is consumed).
    value = base64.b64decode(scene.param or "")[1:]
    oracle = (sf.make_send_bytes_v2(0xA3, bytes([build.COMM_H60A6]), value) if prefix == 0xA3
              else sf.make_send_bytes_mtu(0xA4, build.COMM_H60A6, value, 20))
    assert expected == oracle

    loop = asyncio.get_running_loop()
    stamps: list[float] = []
    acks_per_frame: list[int] = []
    pushes = 0
    orig_push = FakeClient.push
    orig_handle = fake.handle_write

    def counting_push(self: FakeClient, raw: bytes, delay: float = 0.0) -> None:
        nonlocal pushes
        pushes += 1
        orig_push(self, raw, delay)

    def handle(client: FakeClient, data: bytes) -> None:
        seen, before = len(fake.received), pushes
        orig_handle(client, data)
        if len(fake.received) > seen:  # an application frame (not the e7 handshake)
            stamps.append(loop.time())
            acks_per_frame.append(pushes - before)

    monkeypatch.setattr(FakeClient, "push", counting_push)
    monkeypatch.setattr(fake, "handle_write", handle)

    await dev.set_scene_by_name(name)

    n = len(expected)
    assert fake.received == [*expected, build.scene_activate(scene.code)]
    checker(fake.received[:n])
    # upload frames are unacked (the fake pushed nothing), the activation is acked
    assert acks_per_frame == [0] * n + [1]
    # ...and paced: each upload frame is followed by a UNACKED_SEND_GAP sleep
    gaps = [b - a for a, b in itertools.pairwise(stamps)]
    assert all(g >= UNACKED_SEND_GAP * 0.5 for g in gaps), gaps
    assert dev.active_scene == name and dev.state.scene_code == scene.code and dev.state.is_on


async def test_graffiti_upload_uses_the_links_mtu(monkeypatch: pytest.MonkeyPatch) -> None:
    # On a link with an ATT MTU of 247 the 0xA4 frames are 244 bytes long, encrypted whole
    # (EncryptionManager.c -> AESEncryptionStrategy), then the acked 33 05 04 activation.
    fake = FakeGovee()
    fake.write_size = 244
    dev = _make(monkeypatch, fake)
    name = _scene_names(dev, 0xA4)[0]
    scene = load_scenes("H60A6")[name]
    value = base64.b64decode(scene.param or "")[1:]
    expected = sf.make_send_bytes_mtu(0xA4, build.COMM_H60A6, value, 244)
    assert expected != sf.make_send_bytes_mtu(0xA4, build.COMM_H60A6, value, 20)
    await dev.set_scene_by_name(name)
    assert fake.received == [*expected, build.scene_activate(scene.code)]
    assert dev.state.scene_code == scene.code
