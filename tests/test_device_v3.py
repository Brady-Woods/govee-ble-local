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


def test_readback_status_maps_to_state() -> None:
    # feed the real captured H60A6 0xAC burst through Device._read_status via a mock query
    burst = [bytes.fromhex(h) for h in (
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
    d = _dev("H60A6")
    d._conn.query = AsyncMock(return_value=burst)  # type: ignore[attr-defined]
    # scene mode-read returns nothing usable -> scene_code stays None
    asyncio.run(d._read_status())
    assert d.state.is_on is True and d.state.brightness == 0x50
    assert d.state.zone_power == {0: True, 1: True}
    assert len(d.state.segments) == 13
