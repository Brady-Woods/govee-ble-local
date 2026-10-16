"""Scene uploads against independent oracles: the app's own writers, ported line for line.

``scene_upload_a3`` must be byte-for-byte ``MultipleControllerCommV1.makeSendBytesV2`` (via
``makeSendBytesV1``, a one-byte ``commBytes``) and ``scene_upload_a4_mtu`` must be
``makeSendBytesMtu``; the ports live in ``tests/spec_frames.py`` and share no code with the library.
Before the fix a value of 14 bytes or fewer went out as ONE frame (seq 0xFF, packet_count 1, no
START); the app always sends START (seq 0, packet_count 2) plus a separate, empty 0xFF END."""
from __future__ import annotations

import pytest
import spec_frames as sf
from fake_ble import ble_device

from govee_ble_local.devices.device import make_device
from govee_ble_local.exceptions import GoveeBleInvalidValue
from govee_ble_local.scenes import load_scenes
from govee_ble_local.wire import build as b


def _value(n: int) -> bytes:
    return bytes((i * 7 + 1) & 0xFF for i in range(n))


# ── 0xA3: makeSendBytesV2 ────────────────────────────────────────────────────
@pytest.mark.parametrize("comm", [0x01, 0x02, 0x07, 0x0C, 0x03, b.COMM_H60A6, b.COMM_H6052_GRAFFITI])
def test_a3_matches_make_send_bytes_v2_for_every_length(comm: int) -> None:
    for n in range(0, 300):
        value = _value(n)
        assert b.scene_upload_a3(value, comm) == sf.make_send_bytes_v2(0xA3, bytes([comm]), value), n


@pytest.mark.parametrize("n", [0, 1, 13, 14])
def test_a3_short_value_is_start_plus_empty_end(n: int) -> None:
    value = _value(n)
    frames = b.scene_upload_a3(value, 0x01)
    assert len(frames) == 2
    start, end = frames
    assert start[:5] == bytes([0xA3, 0x00, 0x01, 0x02, 0x01])   # seq 0, marker, packet_count 2, comm
    assert start[5 : 5 + n] == value and not any(start[5 + n : 19])
    assert end[:2] == b"\xa3\xff" and not any(end[2:19])
    for f in frames:
        assert len(f) == 20 and f[19] == sf._bcc(f, 19)


@pytest.mark.parametrize("n", [15, 31, 32, 48, 49, 200])
def test_a3_packet_count_counts_every_frame(n: int) -> None:
    frames = b.scene_upload_a3(_value(n), 0x01)
    assert len(frames) == 1 + -(-(n - 14) // 17)
    assert frames[0][1] == 0x00 and frames[0][3] == len(frames)
    assert [f[1] for f in frames[1:-1]] == list(range(1, len(frames) - 1)) and frames[-1][1] == 0xFF


def _reassemble_a3(frames: list[bytes]) -> bytes:
    stream = b"".join(f[2:19] for f in frames)
    assert stream[0] == 0x01 and stream[1] == len(frames)
    return stream[3:]


@pytest.mark.parametrize("n", [0, 1, 14, 15, 31, 32, 200])
def test_a3_roundtrips(n: int) -> None:
    value = _value(n)
    body = _reassemble_a3(b.scene_upload_a3(value, b.COMM_H60A6))
    assert body[:n] == value and not any(body[n:])


def test_a3_start_golden_h61a8_capture() -> None:
    # spec/docs/SCENE_UPLOAD_ENCODING.md "Worked example": the H61A8 DIY-editor START (commByte 3,
    # packetCount 16). Only the START was captured; a 259-byte value with that 14-byte head gives it.
    head = bytes.fromhex("09 00 64 00 00 00 1c 05 00 ff 00 00 01 02")
    frames = b.scene_upload_a3(head + bytes(259 - len(head)), 0x03)
    assert len(frames) == 16
    assert frames[0] == bytes.fromhex("a3 00 01 10 03 09 00 64 00 00 00 1c 05 00 ff 00 00 01 02 39")


def test_a3_h6006_asleep_golden() -> None:
    # A real 14-byte catalogue scene on a bulb: START a3 00 01 02 01 <value> + empty END a3 ff 00.. 5c
    # (it used to go out as the single frame a3 ff 01 01 01 <value>).
    d = make_device(ble_device("AA:BB:CC:DD:EE:06", "H6006"), "H6006")
    assert d is not None
    frames = d.scene_upload_frames(load_scenes("H6006")["Asleep"])
    assert frames is not None
    assert [f.hex() for f in frames] == [
        "a3000102018301ffe1963200021432fa6432faeb",
        "a3ff" + "00" * 17 + "5c",
    ]


def test_a3_oversize_still_refused() -> None:
    with pytest.raises(GoveeBleInvalidValue, match="too large for 0xA3"):
        b.scene_upload_a3(bytes(14 + 17 * 254 + 1), 1)          # 256 frames: the u8 count can't hold it
    assert len(b.scene_upload_a3(bytes(14 + 17 * 254), 1)) == 255


# ── 0xA4: makeSendBytesMtu ───────────────────────────────────────────────────
@pytest.mark.parametrize("mtu", [20, 23, 32, 100, 512])
def test_a4_matches_make_send_bytes_mtu_for_every_length(mtu: int) -> None:
    for n in range(0, 400):
        value = _value(n)
        assert b.scene_upload_a4_mtu(value, b.COMM_H60A6, mtu=mtu) == sf.make_send_bytes_mtu(
            0xA4, b.COMM_H60A6, value, mtu), (n, mtu)


@pytest.mark.parametrize("mtu", [-1, 0, 4, 6, 8, 9, 19, 513, True, 20.0])
def test_a4_mtu_outside_the_apps_range_is_refused(mtu: object) -> None:
    # makeSendBytesMtu is @IntRange(from = 20, to = 512); mtu 4 used to die in range() with a bare
    # ValueError and mtu 6 built a START frame longer than the MTU.
    with pytest.raises(GoveeBleInvalidValue, match="mtu"):
        b.scene_upload_a4_mtu(bytes(30), b.COMM_H60A6, mtu=mtu)  # type: ignore[arg-type]


def test_a4_count_overflow_is_refused() -> None:
    # 1 START + 0xFFFF chunks would need a 0x10000 count (and a middle seq 0xFFFF == END marker).
    biggest_ok = 12 + 16 * 0xFFFE                                # 1 + 0xFFFE = 0xFFFF frames
    assert len(b.scene_upload_a4_mtu(bytes(biggest_ok), b.COMM_H60A6)) == 0xFFFF
    with pytest.raises(GoveeBleInvalidValue, match="too large for 0xA4"):
        b.scene_upload_a4_mtu(bytes(biggest_ok + 1), b.COMM_H60A6)
