"""Dialect-B (H60A6) scene upload: 0xA4-MTU builder round-trips through the Kaitai
`multi_a4` reader, and set_scene_by_name routes graffiti->0xA4 / DIY->0xA3.

The 0xA4 layout is source-derived (MultipleControllerCommV1.makeSendBytesMtu) and
here proven self-consistent: build -> parse with the generated reader -> reassemble
-> must equal the input value byte-for-byte.
"""
from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock

import pytest
from bleak.backends.device import BLEDevice

pytest.importorskip("kaitaistruct", reason="Kaitai runtime not installed")
gb = pytest.importorskip(
    "spec_gen.govee_ble_frame", reason="run tools/gen_kaitai.sh to generate the reader"
)
GBF = gb.GoveeBleFrame

from govee_ble_local.ble import controllers  # noqa: E402
from govee_ble_local.crypto import checksum  # noqa: E402
from govee_ble_local.registry import create_device  # noqa: E402
from govee_ble_local.scenes import Scene, load_scenes  # noqa: E402


def _bcc_ok(frame: bytes) -> bool:
    return len(frame) >= 2 and frame[-1] == checksum(frame[:-1])


def _reassemble(frames: list[bytes]) -> bytes:
    """De-chunk an 0xA4-MTU burst back to the value: START ++ MIDDLEs(asc) ++ END."""
    parsed = [GBF.from_bytes(f) for f in frames]
    start = [p for p in parsed if p.body.seq_marker == 0]
    end = [p for p in parsed if p.body.seq_marker == 0xFFFF]
    mids = sorted(
        (p for p in parsed if p.body.seq_marker not in (0, 0xFFFF)),
        key=lambda p: p.body.seq_marker,
    )
    return b"".join(p.body.value for p in (start + mids + end))


def test_a4_mtu_roundtrips_through_ksy() -> None:
    # A graffiti-shaped value (gate u16le@0 + 2 != len): 187 bytes, like Aurora.
    value = bytes([0x20, 0x00]) + bytes(range(256)) [:185]
    assert len(value) == 187 and (value[0] | value[1] << 8) + 2 != len(value)

    frames = controllers.scene_upload_a4_mtu(value, controllers.COMM_H60A6)

    assert len(frames) == 12                       # START + 10 MIDDLE + END
    for fr in frames[:-1]:
        assert len(fr) == 20 and _bcc_ok(fr)       # full frames
    assert len(frames[-1]) == 19 and _bcc_ok(frames[-1])  # END is short (15 value bytes)

    start = GBF.from_bytes(frames[0])
    assert start.pro_type == GBF.ProType.multi_write_v2
    assert start.body.seq_marker == 0
    assert start.body.start.packet_count == 12     # cnt = total frame count
    assert start.body.start.comm_byte == controllers.COMM_H60A6
    assert GBF.from_bytes(frames[-1]).body.seq_marker == 0xFFFF

    assert _reassemble(frames) == value            # loop closed


def test_real_aurora_builds_and_reassembles() -> None:
    """The bundled Aurora param routes to 0xA4-MTU and round-trips."""
    aurora = load_scenes("H60A6").get("Aurora")
    assert aurora is not None and aurora.param
    value = base64.b64decode(aurora.param)[1:]     # dialect-B value = decode(param)[1:]
    frames = controllers.scene_upload_a4_mtu(value, controllers.COMM_H60A6)
    assert len(frames) == 12
    assert _reassemble(frames) == value


def _h60a6(monkeypatch: object, scenes: dict[str, Scene]) -> object:
    from govee_ble_local.devices import base as basemod
    monkeypatch.setattr(basemod, "load_scenes", lambda sku: scenes)  # type: ignore[attr-defined]
    dev = create_device(BLEDevice("AA:BB:CC:DD:EE:05", "GVH60A6", details={}), "H60A6")
    dev._connection.send = AsyncMock()  # type: ignore[attr-defined]
    return dev


def test_set_scene_by_name_routes_graffiti_diy_static(monkeypatch: object) -> None:
    graffiti = base64.b64encode(bytes([0x50, 0x20, 0x00]) + bytes(185)).decode()  # gate!=len
    diy_val = bytes([18, 0]) + bytes(18)                                          # gate 18 == len-2
    diy = base64.b64encode(bytes([0x50]) + diy_val).decode()
    scenes = {
        "Aurora": Scene("Aurora", 0x4A82, graffiti, scene_type=5),   # graffiti -> 0xA4
        "Xmas": Scene("Xmas", 0x4A95, diy, scene_type=5),            # DIY      -> 0xA3
        "Static": Scene("Static", 0x4A83, None, scene_type=0),       # no param -> activate-only
    }

    def sent_frames(dev: object) -> list[bytes]:
        return [c.args[0] for c in dev._connection.send.call_args_list]  # type: ignore[attr-defined]

    dev = _h60a6(monkeypatch, scenes)
    asyncio.run(dev.set_scene_by_name("Aurora"))  # type: ignore[attr-defined]
    frames = sent_frames(dev)
    assert any(f[0] == 0xA4 for f in frames), "Aurora uploads 0xA4-MTU"
    assert not any(f[0] == 0xA3 for f in frames)
    assert frames[0][6] == controllers.COMM_H60A6  # commByte @ byte6
    assert frames[-1][:3].hex() == "330504", "activation follows upload"

    dev._connection.send = AsyncMock()  # type: ignore[attr-defined]
    asyncio.run(dev.set_scene_by_name("Xmas"))  # type: ignore[attr-defined]
    frames = sent_frames(dev)
    assert any(f[0] == 0xA3 for f in frames) and not any(f[0] == 0xA4 for f in frames)
    assert frames[0][4] == controllers.COMM_H60A6  # 0xA3 commByte @ byte4
    assert frames[-1][:3].hex() == "330504"

    dev._connection.send = AsyncMock()  # type: ignore[attr-defined]
    asyncio.run(dev.set_scene_by_name("Static"))  # type: ignore[attr-defined]
    frames = sent_frames(dev)
    assert len(frames) == 1 and frames[0][:3].hex() == "330504", "static = activate-only"
