"""v3 capability-driven Device — construction, capability gating, frame dispatch,
and scene-dialect routing (offline, mocked connection)."""
from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock

import pytest
from bleak.backends.device import BLEDevice

from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import PROFILES, profile_for
from govee_ble_local.exceptions import GoveeBleNotSupported
from govee_ble_local.models import Capability
from govee_ble_local.scenes import Scene


def _dev(sku: str) -> Device:
    d = make_device(BLEDevice("AA:BB:CC:DD:EE:20", sku, details={}), sku)
    assert d is not None
    d._conn.send = AsyncMock()  # type: ignore[attr-defined]
    return d


def _sent(d: Device) -> list[bytes]:
    return [c.args[0] for c in d._conn.send.call_args_list]  # type: ignore[attr-defined]


def test_all_profiles_construct() -> None:
    for p in PROFILES:
        for sku in p.skus:
            d = make_device(BLEDevice("AA:BB:CC:DD:EE:20", sku, details={}), sku)
            assert d is not None and d.sku == sku.upper()
            assert d.capabilities == p.capabilities
    assert make_device(BLEDevice("AA:BB:CC:DD:EE:20", "H9999", details={}), "H9999") is None


def test_power_state_and_caps() -> None:
    d = _dev("H60A6")
    asyncio.run(d.set_power(True))
    assert _sent(d)[0][:2].hex() == "3301" and _sent(d)[0][2] == 0x01
    assert d.state.is_on is True


def test_capability_gating() -> None:
    h61a8 = _dev("H61A8")            # no COLOR_TEMP
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(h61a8.set_color_temp(4000))
    bulb = _dev("H6006")             # no SEGMENTS
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(bulb.set_segment_rgb([0], (255, 0, 0)))
    plug = _dev("H5080")             # power only
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(plug.set_brightness(50))


def test_rgb_scheme_per_device() -> None:
    for sku, prefix in (("H60A6", "33051501"), ("H6006", "33050d"), ("H61A8", "33050b")):
        d = _dev(sku)
        asyncio.run(d.set_rgb((0xFF, 0, 0)))
        assert _sent(d)[0].hex().startswith(prefix), sku


def test_plug_relay_and_sync_time() -> None:
    d = _dev("H5080")
    asyncio.run(d.set_power(True))
    s = _sent(d)
    assert s[0][:2].hex() == "3301" and s[0][2] == 0x11        # relay ON
    assert s[1][:2].hex() == "33b5"                            # sync-time follow-up


def test_zone_bar_vs_direct() -> None:
    h60a6 = _dev("H60A6")            # per-zone 33 30
    asyncio.run(h60a6.set_zone_power("background", False))
    assert _sent(h60a6)[0][:2].hex() == "3330"
    h6047 = _dev("H6047")            # combined 33 36
    asyncio.run(h6047.set_zone_power("left", False))
    assert _sent(h6047)[0][:2].hex() == "3336"


def test_zone_and_segment_color_temp() -> None:
    # per-zone / masked CCT: the 0x15 CCT frame carries a segment mask (H60A6)
    d = _dev("H60A6")
    asyncio.run(d.set_zone_color_temp("background", 3000))   # ring 0..11 -> mask 0x0FFF
    f = _sent(d)[0]
    assert f.hex().startswith("33051501")                    # mode/0x15/set-color
    assert (f[4], f[5], f[6]) == (0xFF, 0xFF, 0xFF)          # WHITE slot
    assert (f[7], f[8]) == (0x0B, 0xB8)                       # kelvin 3000, u2be
    assert (f[12], f[13]) == (0xFF, 0x0F)                    # mask 0x0FFF, little-endian

    # the MAIN PANEL is the highest segment (index 12) — independently addressable
    dm = _dev("H60A6")
    asyncio.run(dm.set_zone_color_temp("main", 5000))
    assert _sent(dm)[0][12:14] == bytes([0x00, 0x10])        # mask 0x1000 (bit 12 only)

    d2 = _dev("H60A6")
    asyncio.run(d2.set_segment_color_temp([0, 1, 2], 3000))
    assert _sent(d2)[0][12:14] == bytes([0x07, 0x00])        # mask {0,1,2}

    # capability gating: no COLOR_TEMP (H61A8) / no SEGMENTS (bulb) -> not supported
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(_dev("H61A8").set_zone_color_temp("segment", 3000))
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(_dev("H6006").set_segment_color_temp([0], 3000))


def test_scene_dialect_routing() -> None:
    def upload(sku: str, scene: Scene) -> list[bytes] | None:
        return _dev(sku)._scene_upload_frames(scene)

    def param(*head: int) -> str:
        return base64.b64encode(bytes(list(head) + [0] * 30)).decode()

    # H6006 bulb: type-1 rgb -> comByte 1 @ a3 byte4
    f = upload("H6006", Scene("x", 1, param(0x83), scene_type=1))
    assert f and f[0][0] == 0xA3 and f[0][4] == 0x01
    # H6047 rgbic: type-2 -> comByte 2
    f = upload("H6047", Scene("x", 1, param(0x54, 0, 0, 2), scene_type=2))
    assert f and f[0][4] == 0x02
    # H6052 type-5 (byte0 0x13) -> comByte 9 ; type-3 -> activate-only
    f = upload("H6052", Scene("x", 1, param(0x13, 1, 2, 3), scene_type=5))
    assert f and f[0][4] == 0x09
    assert upload("H6052", Scene("x", 1, param(0x01, 0, 0), scene_type=3)) is None
    # H60A6 graffiti (gate fails) -> 0xA4 ; DIY (gate holds) -> 0xA3
    graffiti = base64.b64encode(bytes([0x50, 0x20, 0x00]) + bytes(185)).decode()
    f = upload("H60A6", Scene("x", 1, graffiti, scene_type=5))
    assert f and any(fr[0] == 0xA4 for fr in f)
    diy = base64.b64encode(bytes([0x50]) + bytes([18, 0]) + bytes(18)).decode()
    f = upload("H60A6", Scene("x", 1, diy, scene_type=5))
    assert f and all(fr[0] == 0xA3 for fr in f)


def test_h6641_segment_control_wired() -> None:
    # H6641 mechanism-A: per-segment control + status read-back now exposed.
    d = _dev("H6641")
    assert Capability.SEGMENTS in d.capabilities and d.profile.readback == "status"
    asyncio.run(d.set_segment_rgb([0, 1, 2], (0, 0, 255)))
    f = _sent(d)[0]
    assert f.hex().startswith("33051501")   # 0x15 set-color (RGBIC per-segment)
    assert f[12] == 0x07                     # mask lo: segments {0,1,2} = 0b111


def _frame(*payload: int) -> bytes:
    b = bytearray(20)
    b[: len(payload)] = bytes(payload)
    x = 0
    for c in b[:19]:
        x ^= c
    b[19] = x
    return bytes(b)


# Real captured H60A6 0xAC status burst (13 segments, both zones on, brightness 0x50).
# Device-agnostic parse — reused to exercise mechanism-A status read-back for H6047 too.
_STATUS_BURST = [bytes.fromhex(h) for h in (
    "ac000a000c0300010101040150050415010000e8",
    "ac010707065774f453e75c0711105674f453e7f0",
    "ac025cdb2d0100290104030000070d115ce753d9",
    "ac03f474560100290104031104001e0f0f120749",
    "ac04ff640000800f00231000000080000000808f",
    "ac0500000080000000804102020130020101a57e",
    "ac06110132ff00006400ff00640000ff64ff00ec",
    "ac0700a511026400ff00640000ff64ff000064e2",
    "ac0800ff00a51103640000ff64ff00006400ff77",
    "acff00640000ffa5050464ff00000000000000f7",
)]


def test_mechanism_c_reads_single_rgb() -> None:
    # H6052: 0x05 sub-mode 0x0D report body [R,G,B] -> state.rgb_color
    d = _dev("H6052")
    d._conn.query = AsyncMock(return_value=[_frame(0xAA, 0x05, 0x0D, 255, 128, 64)])  # type: ignore[attr-defined]
    asyncio.run(d._read_mechanism_c())
    assert d.state.rgb_color == (255, 128, 64)


def test_mechanism_b_assembles_positional_segments() -> None:
    # H61A8: 15 segments / 3-per-batch = 5 batches, requested AA A5 <seq>; each V2 batch
    # = [brightness,r,g,b]*3. Segments assemble positionally across the batches.
    d = _dev("H61A8")

    def _batch(frame: bytes, **_kw: object) -> list[bytes]:
        seq = frame[2]                       # AA A5 <batch_seq>
        groups = []
        for i in range(3):
            seg = (seq - 1) * 3 + i
            groups += [50 + seg, seg, 0, 0]  # brightness encodes segment index, R=index
        return [_frame(0xAA, 0xA5, seq, *groups)]

    d._conn.query = AsyncMock(side_effect=_batch)  # type: ignore[attr-defined]
    asyncio.run(d._read_mechanism_b())
    segs = d.state.segments
    assert [s.index for s in segs] == list(range(15))
    assert segs[7].brightness == 57 and segs[7].rgb == (7, 0, 0)


def test_plug_reads_relay_state() -> None:
    # Item 1: plug read-back polls aa 01 (raw relay bitmask) -> state.is_on
    d = _dev("H5080")
    assert d.profile.readback == "plug"
    d._conn.query = AsyncMock(return_value=[_frame(0xAA, 0x01, 0x01)])  # type: ignore[attr-defined]
    asyncio.run(d._read_plug())
    assert d.state.is_on is True and d.state.optimistic is False
    d._conn.query = AsyncMock(return_value=[_frame(0xAA, 0x01, 0x00)])  # type: ignore[attr-defined]
    asyncio.run(d._read_plug())
    assert d.state.is_on is False


def test_h6047_status_readback_populates_segments() -> None:
    # Item 2: H6047 now uses mechanism-A status read-back (like H60A6/H6641)
    assert profile_for("H6047").readback == "status"
    d = _dev("H6047")
    d._conn.query = AsyncMock(return_value=_STATUS_BURST)  # type: ignore[attr-defined]
    asyncio.run(d._read_status())
    assert len(d.state.segments) == 13   # parser takes N from the reply, not a fixed count
    assert d.state.zone_power == {0: True, 1: True}


def test_device_info_populates_state_once() -> None:
    # Item 3: update() reads aa 07 basic/wifi/sn once and fills the static fields
    d = _dev("H60A6")

    def _info(frame: bytes, **_kw: object) -> list[bytes]:
        sel = frame[2]
        if sel == 0x10:
            return [_frame(0xAA, 0x07, 0x10, 0x5C, 0xE7, 0x53, 0xF4, 0x74, 0x56, 0, 0, 1, 2, 3, 1, 0, 5)]
        if sel == 0x11:
            return [_frame(0xAA, 0x07, 0x11, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 4, 5, 6, 7, 8, 9)]
        if sel == 0x02:
            return [_frame(0xAA, 0x07, 0x02, 0x5C, 0xE7, 0x53, 0xF4, 0x74, 0x56, 0, 0)]
        return []

    d._conn.query = AsyncMock(side_effect=_info)  # type: ignore[attr-defined]
    asyncio.run(d._read_device_info())
    assert d.state.serial_number == "56:74:F4:53:E7:5C"
    assert d.state.wifi_mac == "AA:BB:CC:DD:EE:FF"
    assert d.state.firmware_version == "1.02.03" and d.state.hardware_version == "1.00.05"
    assert d.state.ble_mac is None            # no reply carries a distinct BLE MAC
    assert d._device_info_read is True        # gated to once


def test_read_secret_bootstrap() -> None:
    # aa b1 reply: selector 0x01 + 8-byte secret; readable on an unbound plug (no secret set)
    d = _dev("H5080")
    secret = bytes(range(1, 9))
    d._conn.query = AsyncMock(  # type: ignore[attr-defined]
        return_value=[_frame(0xAA, 0xB1, 0x01, *secret)]
    )
    assert asyncio.run(d.read_secret()) == secret


def test_ingest_advertisement_onoff() -> None:
    d = _dev("H60A6")

    class _Adv:
        def __init__(self, on: int) -> None:
            self.manufacturer_data = {0x8801: bytes([0xEC, 0, 0, 0, on])}

    assert d.ingest_advertisement(_Adv(1)) is True and d.state.is_on is True
    assert d.ingest_advertisement(_Adv(1)) is False          # unchanged -> no event
    assert d.ingest_advertisement(_Adv(0)) is True and d.state.is_on is False
    assert d.ingest_advertisement(object()) is False          # no manufacturer_data


def test_readback_status_maps_to_state() -> None:
    # feed the real captured H60A6 0xAC burst through Device._read_status via a mock query
    d = _dev("H60A6")
    d._conn.query = AsyncMock(return_value=_STATUS_BURST)  # type: ignore[attr-defined]
    # scene mode-read returns nothing usable -> scene_code stays None
    asyncio.run(d._read_status())
    assert d.state.is_on is True and d.state.brightness == 0x50
    assert d.state.zone_power == {0: True, 1: True}
    assert len(d.state.segments) == 13


def test_read_status_anchors_device_info() -> None:
    # BLE-only H60A6 reports wifi_mac + hardware_version ONLY via the 0xAC anchor (on its
    # own BLE MAC). A device whose address matches the burst gets them populated.
    d = make_device(BLEDevice("5C:E7:53:F4:74:57", "H60A6", details={}), "H60A6")
    assert d is not None
    d._conn.query = AsyncMock(return_value=_STATUS_BURST)  # type: ignore[attr-defined]
    asyncio.run(d._read_status())
    assert d.state.wifi_mac == "5C:E7:53:F4:74:56"
    assert d.state.hardware_version == "1.04.03"
