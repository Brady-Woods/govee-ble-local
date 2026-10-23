"""v3 capability-driven Device — construction, capability gating, frame dispatch,
and scene-dialect routing (offline, mocked connection)."""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import time
from unittest.mock import AsyncMock

import pytest
from conftest import routing_pact
from fake_ble import ble_device, status_burst, unacked_via

from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import PROFILES, profile_for, wire_profile
from govee_ble_local.exceptions import GoveeBleInvalidValue, GoveeBleNotSupported
from govee_ble_local.identify import GoveeAdvertisement
from govee_ble_local.models import Capability
from govee_ble_local.wire import build, reassemble
from govee_ble_local.scenes import Scene


def _dev(sku: str, *, replies: list[bytes] | None = None) -> Device:
    d = make_device(ble_device("AA:BB:CC:DD:EE:20", sku), sku)
    assert d is not None
    sent: list[bytes] = []

    async def send(frame: bytes, **_k: object) -> None:
        sent.append(frame)

    async def send_many(frames: list[bytes]) -> None:
        sent.extend(frames)

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        sent.append(frame)
        return list(replies or [])

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock(side_effect=unacked_via(send_many))  # type: ignore[method-assign]
    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    d._sent = sent  # type: ignore[attr-defined]
    return d


def _sent(d: Device) -> list[bytes]:
    return list(d._sent)  # type: ignore[attr-defined]


def _reply(*b: int) -> bytes:
    body = bytes(b) + bytes(19 - len(b))
    x = 0
    for v in body:
        x ^= v
    return body + bytes([x])


def test_all_profiles_construct() -> None:
    for p in PROFILES:
        for sku in p.skus:
            # a SKU only a gated target row routes, or a pact route's profile, is built from its advert's pact
            pact = routing_pact(sku, profile=p)
            adv = None if pact is None else GoveeAdvertisement(sku, f"GV{sku}", False, 0, *pact)
            d = make_device(ble_device("AA:BB:CC:DD:EE:20", sku), sku, adv)
            assert d is not None and d.sku == sku.upper()
            assert d.capabilities == p.capabilities
    assert make_device(ble_device("AA:BB:CC:DD:EE:20", "H9999"), "H9999") is None


def test_power_global_switch_for_plain_lights() -> None:
    d = _dev("H6006")
    asyncio.run(d.set_power(True))
    assert [f[:3].hex() for f in _sent(d)] == ["330101"]
    assert d.state.is_on is True


def test_power_h60a6_is_one_global_frame_driving_both_zones() -> None:
    # Live-verified: H60A6's 33 01 switches the whole fixture (both zones).
    d = _dev("H60A6")
    asyncio.run(d.set_power(True))
    assert [f[:3].hex() for f in _sent(d)] == ["330101"]
    assert d.state.is_on is True and d.state.zone_power == {0: True, 1: True}
    asyncio.run(d.set_power(False))
    assert d.state.zone_power == {0: False, 1: False}


def test_power_bar_fixture_is_a_single_frame() -> None:
    # H6047's main switch is the global 33 01 (NewDetailVm.switchTrigger -> ControllerSwitch);
    # 33 36 is only the per-bar buttons.
    d = _dev("H6047")
    asyncio.run(d.set_power(False))
    assert [f[:3].hex() for f in _sent(d)] == ["330100"]
    assert d.state.zone_power == {0: False, 1: False} and d.state.is_on is False


def test_turn_on_batches_power_brightness_colour() -> None:
    d = _dev("H6008")
    asyncio.run(d.turn_on(brightness=0, rgb=(1, 2, 3)))
    assert [f[:2].hex() for f in _sent(d)] == ["3301", "3304", "3305"]
    assert _sent(d)[1][2] == 1  # brightness never sent as 0
    d._conn.send_many.assert_awaited_once()  # type: ignore[attr-defined]
    assert d.state.brightness == 1 and d.state.rgb_color == (1, 2, 3)


def test_set_brightness_clamps_to_one() -> None:
    d = _dev("H6008")
    asyncio.run(d.set_brightness(0))
    assert _sent(d)[0][2] == 1


def test_advert_onoff_ignored_right_after_command() -> None:
    d = _dev("H6008")
    adv = type("Adv", (), {"manufacturer_data": {0x8843: bytes([0xEC, 0, 0, 0, 0])}})()
    asyncio.run(d.set_power(True))
    assert d.ingest_advertisement(adv) is False and d.state.is_on is True
    d._advert_ignore_until = 0
    assert d.ingest_advertisement(adv) is True and d.state.is_on is False


def test_polled_device_reads_power_even_when_off() -> None:
    d = _dev("H6008", replies=[_reply(0xAA, 0x01, 0x01)])
    d.state.is_on = False
    asyncio.run(d._read_polled())
    assert d.state.is_on is True


def test_segment_write_updates_state() -> None:
    d = _dev("H60A6")
    asyncio.run(d.set_segment_rgb([2, 3], (9, 8, 7)))
    asyncio.run(d.set_segment_brightness([3], 40))
    segs = {s.index: s for s in d.state.segments}
    assert segs[2].rgb == (9, 8, 7) and segs[3].rgb == (9, 8, 7) and segs[3].brightness == 40


def test_capability_gating() -> None:
    bulb = _dev("H6006")             # no SEGMENTS
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(bulb.set_segment_rgb([0], (255, 0, 0)))
    plug = _dev("H5080")             # power only
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(plug.set_brightness(50))


def test_rgb_scheme_per_device() -> None:
    for sku, prefix in (("H60A6", "33051501"), ("H6006", "33050d"), ("H61A8", "33051501")):
        d = _dev(sku)
        asyncio.run(d.set_rgb((0xFF, 0, 0)))
        assert _sent(d)[0].hex().startswith(prefix), sku


def test_plug_relay_power_frame_alone() -> None:
    d = _dev("H5080")
    asyncio.run(d.set_power(True))
    s = _sent(d)
    assert len(s) == 1 and s[0][:2].hex() == "3301" and s[0][2] == 0x11   # relay ON
    # WAS: a best-effort, unacked 33 b5 followed every plug power write. It is now sent once per
    # connection, after the unlock (Device._session_frames), as every plug screen's connect read does.
    d._conn.send.assert_not_awaited()                          # type: ignore[attr-defined]
    assert d._conn._session_frames is not None                 # type: ignore[attr-defined]


@pytest.mark.parametrize(("pct", "sent"), [(0, 1), (-3, 1), (1, 1), (100, 100), (101, 100)])
def test_segment_brightness_is_clamped_to_the_sliders_1_to_100(pct: int, sent: int) -> None:
    # ViewColorMode.t0:232-235 / DelegateColorV2Part.t0:459-462: setProgressRange({1, 100}); before,
    # the library sent 0 for 0 or below.
    d = _dev("H60A6")
    asyncio.run(d.set_segment_brightness([12], pct))
    frame = _sent(d)[-1]
    assert frame[:4].hex() == "33051502" and frame[4] == sent
    assert {s.index: s.brightness for s in d.state.segments} == {12: sent}


@pytest.mark.parametrize(("indices", "mask", "folded"), [
    ([0], 0x0007, [0, 1, 2]), ([3], 0x0078, [3, 4, 5, 6]), ([2, 10, 2], 0x3C07, [0, 1, 2, 10, 11, 12, 13])])
def test_segment_brightness_widens_to_the_strips_groups(indices: list[int], mask: int,
                                                        folded: list[int]) -> None:
    # ColorMode14Rect4ReverseSelect.canClickBrightness:379-421 sets every piece of each group {0-2} / {3-6} /
    # {7-9} / {10-13} holding a selected one before the 15 02 write (DelegateColorV2Part.U:217-230 ->
    # ColorViewModel.m0, which folds that set): the profile's segment_brightness_groups, here on the
    # pact_h6099 screen's 203 row (H6098, released at 1.6.1; the user's M2, "Match the app").
    d = _dev("H6098")
    asyncio.run(d.set_segment_brightness(indices, 40))
    assert _sent(d)[-1][:7] == bytes([0x33, 0x05, 0x15, 0x02, 40]) + mask.to_bytes(2, "little")
    assert {s.index: s.brightness for s in d.state.segments} == dict.fromkeys(folded, 40)
    with pytest.raises(GoveeBleInvalidValue, match="segment_brightness_groups"):
        dataclasses.replace(d.profile, segment_brightness_groups=((0, 1), (1,)))


def test_zone_bar_vs_direct() -> None:
    h60a6 = _dev("H60A6")            # per-zone 33 30
    asyncio.run(h60a6.set_zone_power("background", False))
    assert _sent(h60a6)[0][:2].hex() == "3330"
    h6047 = _dev("H6047")            # combined 33 36
    h6047.state.zone_power = {0: True, 1: True}
    now = time.monotonic()   # both bars confirmed just now: no aa 36 read first (ZONE_POWER_FRESH_SECONDS)
    h6047.state.zone_power_confirmed = {0: now, 1: now}
    asyncio.run(h6047.set_zone_power("left", False))
    assert _sent(h6047)[0][:4].hex() == "33360001"   # right bar kept ON


def test_direct_zone_power_updates_zone_and_derived_power() -> None:
    # H60A6 33 30 <zone> <on>: the zone's own state, and whole-device power derived once every zone
    # is known (on while any zone is lit); unchanged while the other zone is unknown.
    d = _dev("H60A6")
    asyncio.run(d.set_zone_power("background", False))
    assert _sent(d)[0][:4].hex() == "33300100"
    assert d.state.zone_power == {1: False} and d.state.is_on is None
    assert d.zone_is_on("background") is False and d.zone_is_on("main") is None
    asyncio.run(d.set_zone_power("main", True))
    assert _sent(d)[1][:4].hex() == "33300001"
    assert d.state.is_on is True and d.zone_is_on("main") is True
    asyncio.run(d.set_zone_power("main", False))
    assert d.state.zone_power == {0: False, 1: False} and d.state.is_on is False
    with pytest.raises(GoveeBleNotSupported):
        d.zone_is_on("left")


def test_bar_switch_reads_unknown_state_instead_of_assuming() -> None:
    # Unknown state used to default to ON: turning one bar off turned the other on.
    d = _dev("H6047", replies=[_reply(0xAA, 0x36, 0x01, 0x00)])  # left on, right off
    asyncio.run(d.set_zone_power("left", False))
    frames = _sent(d)
    assert frames[0][:2].hex() == "aa36"                 # read first
    assert frames[1][:4].hex() == "33360000"             # right stays OFF


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

    d2 = _dev("H6098")   # no Kelvin to an H60A6 piece selection (decision 24); the H6098 sends the selection
    asyncio.run(d2.set_segment_color_temp([0, 1, 2], 3000))
    assert _sent(d2)[0][12:14] == bytes([0x07, 0x00])        # mask {0,1,2}

    # capability gating: no COLOR_TEMP (a plug: every colour-temperature path) / no SEGMENTS (bulb) -> not
    # supported, and nothing sent
    plug = _dev("H5080")
    for call in (lambda d: d.set_color_temp(3000), lambda d: d.set_segment_color_temp([0], 3000),
                 lambda d: d.set_zone_color_temp("main", 3000), lambda d: d.turn_on(kelvin=3000)):
        with pytest.raises(GoveeBleNotSupported):
            asyncio.run(call(plug))
    assert _sent(plug) == []
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(_dev("H6006").set_segment_color_temp([0], 3000))


# A scheme with no per-segment brightness: test_fix_wp3_device_design
# .test_a_scheme_without_segment_brightness_is_not_supported_with_the_sku.
def test_h61a8_kelvin_range_follows_its_hardware_and_it_sends_15_02_segment_brightness() -> None:
    # goodsType 19: KelvinConfig.Config.z2 gives [2700, 6500] from hardware "3.08.01", a string compare, else
    # [2000, 9000]; until aa 07 03 gives the version, the range valid on both. color_functions
    # segment_brightness sends Bytes.G (33 05 15 02).
    d = _dev("H61A8")
    assert (d.min_kelvin, d.max_kelvin) == (2700, 6500)          # hardware not read yet
    for hardware, span in (("3.01.10", (2000, 9000)), ("3.08.01", (2700, 6500)), ("3.10.00", (2700, 6500))):
        d._state.hardware_version = hardware
        assert (d.min_kelvin, d.max_kelvin) == span, hardware
    for hardware, sent in (("3.01.10", 2000), ("3.08.01", 2700)):   # 2000 K: kept, or clamped up
        d._state.hardware_version = hardware
        asyncio.run(d.set_color_temp(2000))
        assert int.from_bytes(_sent(d)[-1][7:9], "big") == sent and d.state.color_temp_kelvin == sent
    asyncio.run(d.set_segment_brightness([0], 50))
    assert _sent(d)[-1][:7].hex() == "33051502320100"             # [pct 50][mask: segment 0, 2 bytes]


def test_gradual_setting() -> None:
    d = _dev("H61A8")                       # only curated SKU with the 0xA3 gradual flag
    asyncio.run(d.set_gradual(True))
    assert _sent(d)[0][:3].hex() == "33a301" and d.state.gradual is True
    with pytest.raises(GoveeBleNotSupported):
        asyncio.run(_dev("H60A6").set_gradual(True))   # profile.gradual = False


def test_scene_dialect_routing() -> None:
    def upload(sku: str, scene: Scene) -> list[bytes] | None:
        return _dev(sku).scene_upload_frames(scene)

    def param(*head: int) -> str:
        return base64.b64encode(bytes(list(head) + [0] * 30)).decode()

    def exact(*blob: int) -> str:
        return base64.b64encode(bytes(blob)).decode()

    # H6006 bulb: type-1 rgb -> comByte 1 @ a3 byte4 (83 00 00: mode 0, no effects, no colours, which
    # ScenesRgb.isValidProtocolBytes accepts; a zero-padded blob it rejects is only activated)
    f = upload("H6006", Scene("x", 1, exact(0x83, 0, 0), scene_type=1))
    assert f and f[0][0] == 0xA3 and f[0][4] == 0x01
    assert upload("H6006", Scene("x", 1, param(0x83), scene_type=1)) is None
    # H6047 rgbic: type-2 -> comByte 2 (00: no effects, which ScenesRgbIC.isValidProtocolBytes accepts)
    f = upload("H6047", Scene("x", 1, exact(0x00), scene_type=2))
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


def _h6641_ic_query(ic: int, third: int, asked: list[int]) -> AsyncMock:
    """aa 40 -> [IC u2be][third byte]; aa a5 <g> -> four [brightness, r, g, b] records (the frame
    is always full; the caller decides how many are real). Records the requested groups."""
    def _q(frame: bytes, **_kw: object) -> list[bytes]:
        if frame[1] == 0x40:                    # ic_count_query
            return [_frame(0xAA, 0x40, ic >> 8, ic & 0xFF, third)]
        g = frame[2]                             # AA A5 <group>
        asked.append(g)
        recs: list[int] = []
        for i in range(4):
            seg = (g - 1) * 4 + i
            recs += [50 + seg, seg, 0, 0]
        return [_frame(0xAA, 0xA5, g, *recs)]
    return AsyncMock(side_effect=_q)


@pytest.mark.parametrize(
    ("ic", "pieces"),
    [
        (50, 10),        # ceil(50 / 5) = 10 pieces -> groups of 4, 4, 2
        (30, 9),         # below the range: clamped to 41 -> 9 pieces in 3 groups
        (41, 9), (70, 14), (69, 14),
        (200, 14),       # above the range: clamped to 70 -> 14 pieces in 4 groups
        (0x9000, 9),     # a signed short below 0 (BleUtil.getSignedShort) clamps to 41
    ],
)
def test_h6641_mechanism_a_direct_readback_via_live_ic(ic: int, pieces: int) -> None:
    # H6641 (goodsType 247): H61D3Support.f0(247)=false, so it never dispatches the 0xAC status
    # burst — per-segment colour is Controller4ColorInfoByGroup's DIRECT `aa a5 <group>` path
    # (group size 4, source-confirmed), sized from a LIVE 0x40 IC-count read the way the app does
    # it: ReadIcController.Companion.d clamps the IC to H61D3Support.q(247) = 41..70, and
    # VM4Light.P5 walks ceil(N / 4) groups for N = H61D3Support.e(247, IC) = ceil(IC / 5) pieces.
    # The reply's third byte (7 here, inconsistent on purpose) is never used.
    p = profile_for("H6641")
    assert p is not None
    assert p.readback == "polled" and p.color_readback == "mechanism_a_direct"
    assert p.color_readback_live_ic is True and p.color_readback_per_batch == 4
    assert p.color_readback_ic_range == (41, 70) and p.color_readback_ic_per_piece == 5

    d = _dev("H6641")
    asked: list[int] = []
    d._conn.query = _h6641_ic_query(ic, 7, asked)  # type: ignore[method-assign]
    asyncio.run(d._read_mechanism_a_direct())
    assert asked == list(range(1, -(-pieces // 4) + 1))           # exactly the app's aa a5 reads
    segs = d.state.segments
    assert [s.index for s in segs] == list(range(pieces))          # no phantom padding segments
    last = pieces - 1
    assert segs[last].rgb == (last, 0, 0) and segs[last].brightness == 50 + last


def test_h6641_group_reply_is_placed_by_the_requested_group() -> None:
    # Controller4ColorInfoByGroup.Companion.h/i drop the reply's own group byte; VM4Light.Q5 places
    # the records by the group it asked for. A reply naming another group can't inject segments.
    d = _dev("H6641")

    def _q(frame: bytes, **_kw: object) -> list[bytes]:
        if frame[1] == 0x40:
            return [_frame(0xAA, 0x40, 0x00, 41, 0xFF)]           # 9 pieces, 3 groups
        return [_frame(0xAA, 0xA5, 0xFF, *([7, 1, 2, 3] * 4))]   # every reply claims group 255

    d._conn.query = AsyncMock(side_effect=_q)  # type: ignore[method-assign]
    asyncio.run(d._read_mechanism_a_direct())
    assert [s.index for s in d.state.segments] == list(range(9))
    assert d._conn.query.await_count == 4                          # aa 40 + 3 aa a5, not 255


def test_h6641_segment_control_wired() -> None:
    # H6641: per-segment control (write) + mechanism A-direct colour read-back (Controller4-
    # ColorInfoByGroup, `aa a5 <group>` — the 0xAC status burst is source-confirmed zero for gt 247).
    d = _dev("H6641")
    assert Capability.SEGMENTS in d.capabilities
    assert d.profile.readback == "polled" and d.profile.color_readback == "mechanism_a_direct"
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
# H60A6-specific — H6047/H6641 do NOT answer the 0xAC status burst at all (source-confirmed;
# see their profile.py comments), so this is not reused for those SKUs.
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


def _status_query_mock(bursts: list[list[bytes]]) -> AsyncMock:
    """A ``d._conn.query`` mock that routes ONLY 0xAC status-query triggers through `bursts`
    (one entry consumed per status-query call; recorded on `.status_calls`), and returns an
    empty reply for anything else — e.g. the scene mode-query (0xAA) H60A6 also issues
    alongside status read-back, which would otherwise be miscounted as a retry."""
    it = iter(bursts)
    mock = AsyncMock()
    mock.status_calls = 0

    async def _query(frame: bytes, **_kw: object) -> list[bytes]:
        if frame and frame[0] == 0xAC:
            mock.status_calls += 1
            return next(it)
        return []

    mock.side_effect = _query
    return mock


def test_mode_0d_report_reads_colour_or_cct() -> None:
    # 0x0d bulbs (H6006/H6008): aa 05 0d [r g b kelvin-s16be tint]; a Kelvin > 0 = CCT mode
    # (live capture: aa 05 0d ff ff ff 0a 8c = 2700 K); a Kelvin of 0 is that RGB colour.
    d = _dev("H6008")
    d._apply_mode_reply(_frame(0xAA, 0x05, 0x0D, 0xFF, 0xFF, 0xFF, 0x0A, 0x8C))
    assert d.state.color_temp_kelvin == 2700 and d.state.rgb_color is None
    d._apply_mode_reply(_frame(0xAA, 0x05, 0x0D, 255, 128, 64, 0, 0))
    assert d.state.rgb_color == (255, 128, 64) and d.state.color_temp_kelvin is None
    # H6052's control screen (H6052InfoDetail.G) reads [r g b] only: the same live frame is RGB
    # white with no Kelvin.
    lamp = _dev("H6052")
    lamp._apply_mode_reply(_frame(0xAA, 0x05, 0x0D, 0xFF, 0xFF, 0xFF, 0x0A, 0x8C))
    assert lamp.state.rgb_color == (255, 255, 255) and lamp.state.color_temp_kelvin is None


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


def test_h6047_mechanism_a_direct_readback() -> None:
    # H6047 (goodsType 119) does NOT answer the 0xAC status burst — Support.isGoodsTypeH6047:186
    # routes it to Compose4InfoBleIot instead: per-group DIRECT `aa a5 <group>` reads via
    # Controller4ColorInfoByGroup (group size 4, source-confirmed — NOT H61A8's unrelated
    # mechanism_b/BulbGroupColorV2, group size 3). The old detail screen walks
    # Support.getBulbStringMaxNum(119) = 10 pieces (NewDetailVm.n5), NOT getColorPieceSize = 12
    # (newdetail screens only): ceil(10/4) = 3 groups, the last trimmed to 2 records.
    p = profile_for("H6047")
    assert p.readback == "polled" and p.color_readback == "mechanism_a_direct"
    assert p.color_readback_segments is None and p.color_readback_per_batch == 4

    d = _dev("H6047")
    asked: list[int] = []

    def _group(frame: bytes, **_kw: object) -> list[bytes]:
        g = frame[2]                         # AA A5 <group>
        asked.append(g)
        recs: list[int] = []
        for i in range(4):                   # the device may pad the last group with a 4th record
            seg = (g - 1) * 4 + i
            recs += [50 + seg, seg, 0, 0]    # brightness encodes segment index, R=index
        return [_frame(0xAA, 0xA5, g, *recs)]

    d._conn.query = AsyncMock(side_effect=_group)  # type: ignore[attr-defined]
    asyncio.run(d._read_mechanism_a_direct())
    segs = d.state.segments
    assert asked == [1, 2, 3]
    assert [s.index for s in segs] == list(range(10))   # the 10 writable segments: no phantom 10, 11


def test_device_info_populates_state_once() -> None:
    # Item 3: update() reads the device info once and fills the static fields
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
    # aa 07 10 / 11 are not sent (no firmware handler, no app sender): no Wi-Fi MAC or basic versions.
    assert d.state.wifi_mac is None
    assert d.state.firmware_version is None and d.state.hardware_version is None
    assert {call.args[0][1:3].hex() for call in d._conn.query.call_args_list} == {"0600", "0703", "0702"}
    assert d.state.ble_mac is None            # no reply carries a distinct BLE MAC
    assert d._device_info_read is True        # gated to once


def test_ingest_advertisement_onoff() -> None:
    d = _dev("H60A6")

    class _Adv:
        def __init__(self, on: int) -> None:
            # header 0x02: BleUtil.parseBleBroadOnOff reads on/off only for a header >= 2
            self.manufacturer_data = {0x8802: bytes([0xEC, 0, 0, 0, on])}

    assert d.ingest_advertisement(_Adv(1)) is True and d.state.is_on is True
    assert d.ingest_advertisement(_Adv(1)) is False          # unchanged -> no event
    assert d.ingest_advertisement(_Adv(0)) is True and d.state.is_on is False
    assert d.ingest_advertisement(object()) is False          # no manufacturer_data
    v1 = type("Adv", (), {"manufacturer_data": {0x8801: bytes([0xEC, 0, 0, 0, 1])}})()
    assert d.ingest_advertisement(v1) is False and d.state.is_on is False  # v1 header: no on/off


def test_readback_status_maps_to_state() -> None:
    # feed the real captured H60A6 0xAC burst through Device._read_status via a mock query
    d = _dev("H60A6")
    d._conn.query = AsyncMock(return_value=_STATUS_BURST)  # type: ignore[attr-defined]
    # scene mode-read returns nothing usable -> scene_code stays None
    asyncio.run(d._read_status())
    assert d.state.is_on is True and d.state.brightness == 0x50
    assert d.state.zone_power == {0: True, 1: True}
    assert len(d.state.segments) == 13


def test_read_status_populates_device_info() -> None:
    # BLE-only H60A6 reports device-info ONLY via the 0xAC 0x07 TLV — now parsed from the
    # spec model (no MAC-anchor), so it's address-INDEPENDENT (the mocked device's address
    # need not match the burst).
    d = _dev("H60A6")
    d._conn.query = AsyncMock(return_value=_STATUS_BURST)  # type: ignore[attr-defined]
    asyncio.run(d._read_status())
    assert d.state.wifi_mac == "5C:E7:53:F4:74:56"
    assert d.state.hardware_version == "1.04.03"
    assert d.state.firmware_version == "1.00.41"
    assert d.state.serial_number == "2D:DB:5C:E7:53:F4:74:56"


def _status_frames(d: Device) -> list[bytes]:
    """Every 0xAC request a status read sends (no reply, so the read retries once)."""
    asked: list[bytes] = []

    async def query(frame: bytes, **_kw: object) -> list[bytes]:
        asked.append(frame)
        return []

    d._conn.query = query  # type: ignore[method-assign,assignment]
    asyncio.run(d._read_status())
    return [f for f in asked if f[0] == 0xAC]


@pytest.mark.parametrize(("pact", "full"), [
    (None, True), ((1, 1), False), ((1, 0), False), ((2, 9), False), ((1, 2), True), ((1, 3), True),
    ((1, 9), True),
])
def test_h60a6_asks_for_colour_groups_only_on_the_apps_pacts(
    pact: tuple[int, int] | None, full: bool,
) -> None:
    # VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1:158-161: [0x41, 0x30] + 0xA5 if J5(), which is
    # goodsType 303 with pact type 1 and code >= 2 (Support.supportPartColor4H60A6:434-436). An unknown
    # pact keeps the colour groups (the library may not have heard an advertisement yet).
    d = _dev("H60A6")
    d._pact = pact
    frames = _status_frames(d)
    assert len(frames) == 2 and frames == [build.status_query(full=full)] * 2   # the retry asks the same
    assert frames[0][:6].hex() == ("ac0303" "4130a5" if full else "ac0302" "413000")


def test_a_pact_heard_later_changes_the_next_status_request() -> None:
    d = _dev("H60A6")
    assert _status_frames(d)[0] == build.status_query(full=True)
    d._learn_pact((1, 1))
    assert d.pact == (1, 1) and _status_frames(d)[0] == build.status_query(full=False)


def test_only_the_h60a6_gates_its_colour_groups_on_the_pact() -> None:
    gated = {sku for p in PROFILES if p.status_color_groups_pact is not None for sku in p.skus}
    h60a6 = profile_for("H60A6")
    assert gated == {"H60A6"} and h60a6 is not None and h60a6.status_color_groups_pact == (1, 2)


def test_the_status_request_is_the_profiles_status_read() -> None:
    # The request is the profile's status_read (a devices.yaml row's) in its order, here goodsType 263's
    # [0x41, 0xA5, 0x40, 0x16]; a pact outside status_color_groups_pact drops only the 0xA5 colour groups.
    h60a6 = profile_for("H60A6")
    assert h60a6 is not None and h60a6.status_read == (0x41, 0x30, 0xA5)
    d = Device(dataclasses.replace(h60a6, status_read=(0x41, 0xA5, 0x40, 0x16)),
               ble_device("AA:BB:CC:DD:EE:21", "GVH60A6"))
    assert _status_frames(d)[0][:7].hex() == "ac0304" "41a54016"
    d._pact = (1, 1)
    assert _status_frames(d)[0][:6].hex() == "ac0303" "414016"
    # An empty status_read is the bare read (the h70dx screen's, VM4H70DX.e5), and the TLV 0xA5 its reply
    # carries unrequested is read as colour groups, as that screen parses each one (VM4H70DX.g5).
    bare = next(p for p in PROFILES if p.status_read == ())
    assert _status_frames(Device(bare, ble_device("AA:BB:CC:DD:EE:22", "GVH70D1")))[0][:4].hex() == "ac030000"
    group = bytes([0xA5, 17, 0x01, *(90, 1, 2, 3) * 4])
    st = reassemble.parse_status(status_burst(bytes([0x01, 0x01, 0x01]) + group), pieces=bare.segments,
                                 decode=wire_profile(bare))
    assert st.is_on is True and [(s.index, s.rgb) for s in st.segments] == [(i, (1, 2, 3)) for i in range(4)]


def test_the_status_reply_is_read_with_the_profiles_status_decode() -> None:
    # The 0xAC reply is read with the profile's status_decode (a devices.yaml row's wire facts), not always
    # the H60A6 screen's: here 3-record groups by the fixed rule, placed 3 apart, where the H60A6's by_pieces
    # count of 4 fails the read; counted from the len, they set no segment past the piece count; with no
    # colour-group form the 0xA5 TLV is skipped, and with no zone_power form the 0x30 TLV is.
    h60a6 = profile_for("H60A6")
    assert h60a6 is not None
    records = [(90, 1, 2, 3), (80, 4, 5, 6), (70, 7, 8, 9)]
    burst = status_burst(bytes([0x01, 0x01, 0x01, 0x05, 0x00, 0xA5, 13, 0x02, *sum(records, ())]))
    assert reassemble.parse_status(burst, pieces=13, decode=wire_profile(h60a6)) == reassemble.StatusReply()
    groups = dataclasses.replace(h60a6, status_decode=(
        ("f_a5", "color_group"), ("group_rule", "fixed"), ("num_per_group", 3), ("has_brightness", True)))
    st = reassemble.parse_status(burst, pieces=13, decode=wire_profile(groups))
    assert st.is_on is True and [(s.index, s.rgb) for s in st.segments] == [
        (3, (1, 2, 3)), (4, (4, 5, 6)), (5, (7, 8, 9))]
    by_len = dataclasses.replace(h60a6, status_decode=(
        ("f_a5", "color_group"), ("group_rule", "from_len"), ("num_per_group", 3), ("has_brightness", True)))
    by_len_st = reassemble.parse_status(burst, pieces=4, decode=wire_profile(by_len))
    assert [s.index for s in by_len_st.segments] == [3]
    bare = dataclasses.replace(h60a6, status_decode=(("music", "v2_default"),))
    assert reassemble.parse_status(burst, pieces=13, decode=wire_profile(bare)).segments == []
    no_zones = reassemble.parse_status(status_burst(bytes([0x01, 0x01, 0x01, 0x30, 0x02, 0x01, 0x00])),
                                       pieces=13, decode=wire_profile(bare))
    assert no_zones.is_on is True and no_zones.zone_power == {}
    for bad in ((("f_a5", "color_group"),), (("f_99", "x"),), (("num_per_group", 0),)):
        with pytest.raises(GoveeBleInvalidValue, match="status_decode"):
            dataclasses.replace(h60a6, status_decode=bad)


@pytest.mark.parametrize("sku", ["H1630", "R1630"])
def test_h1630_status_read_fails_on_a_short_power_memory_value(sku: str) -> None:
    # h1630 364 reads TLV 0x41 as the power-off memory (f_41 power_memory): the screen's status lambda stores
    # the value's byte 1 unguarded (VM4LightH1630$afterConnectedSingleReadDeviceInfo$1.o), so a 0x41 value of
    # 0 or 1 byte fails the app's whole read, and the library's, wherever it sits; one of 2 bytes reads as
    # before. An empty 0x41 as the stream's last two bytes is no TLV to the walker, so it reads as before.
    p = profile_for(sku)
    assert p is not None and ("f_41", "power_memory") in p.status_decode
    power, brightness = bytes([0x01, 0x01, 0x01]), bytes([0x04, 0x01, 0x32])
    for value, fails in ((b"", True), (b"\x02", True), (b"\x02\x01", False)):
        tlv = bytes([0x41, len(value), *value])
        for tlvs in (tlv + power + brightness, power + tlv + brightness):
            st = reassemble.parse_status(status_burst(tlvs), pieces=1, decode=wire_profile(p))
            assert (st.is_on, st.brightness) == ((None, None) if fails else (True, 50))
    tail = reassemble.parse_status(status_burst(power + brightness + bytes([0x41, 0x00])), pieces=1,
                                   decode=wire_profile(p))
    assert (tail.is_on, tail.brightness) == (True, 50)


def test_read_status_retries_once_then_recovers(caplog: pytest.LogCaptureFixture) -> None:
    # A dropped/truncated 0xAC burst (e.g. a lost BLE notification) yields an empty parse on
    # the first attempt; a fresh query is independent and usually succeeds — retry once before
    # giving up. Recovery -> no WARNING, but the retry-attempt DEBUG line is emitted.
    d = _dev("H60A6")
    mock = _status_query_mock([[], _STATUS_BURST])
    d._conn.query = mock  # type: ignore[attr-defined]
    with caplog.at_level("DEBUG", logger="govee_ble_local.devices.device"):
        asyncio.run(d._read_status())
    assert mock.status_calls == 2
    assert d.state.is_on is True and d.state.brightness == 0x50
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("retrying once" in r.message for r in caplog.records)


def test_read_status_warns_and_preserves_state_after_two_empty_reads(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Both attempts come back empty (0xAC never arrived, or arrived truncated both times):
    # WARNING logged once, and existing good state is NOT erased (fields are applied
    # conditionally — an empty StatusReply carries no fields to overwrite with).
    d = _dev("H60A6")
    d._conn.query = _status_query_mock([_STATUS_BURST])  # type: ignore[attr-defined]
    asyncio.run(d._read_status())
    assert d.state.is_on is True  # seed known-good state

    d._conn.query = _status_query_mock([[], []])  # type: ignore[attr-defined]
    with caplog.at_level("DEBUG", logger="govee_ble_local.devices.device"):
        asyncio.run(d._read_status())
    assert d._conn.query.status_calls == 2  # type: ignore[attr-defined]
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "state left stale" in warnings[0].message
    assert "<none>" in warnings[0].message  # chunk-index diagnostic: nothing received
    assert d.state.is_on is True  # unchanged — not erased by the empty reply


def test_read_status_with_no_usable_data_is_not_a_read_back() -> None:
    # Two empty 0xAC bursts and an unanswered aa 05 read nothing: state stays optimistic, the
    # advertised on/off (the only other on/off source) is not suppressed, and the one-time
    # "first poll" step is not consumed.
    d = _dev("H60A6")
    d._conn.query = _status_query_mock([[], []])  # type: ignore[method-assign]
    asyncio.run(d.update())
    assert d._conn.query.status_calls == 2  # type: ignore[attr-defined]
    assert d.state.optimistic is True and d.state.is_on is None
    assert d._advert_ignore_until == 0.0
    assert d._polled_once is False


def test_read_status_seeded_device_does_not_rearm_advert_suppression_on_empty_read() -> None:
    d = _dev("H60A6")
    d._conn.query = _status_query_mock([_STATUS_BURST])  # type: ignore[method-assign]
    asyncio.run(d._read_status())
    assert d.state.optimistic is False and d._advert_ignore_until > 0
    d._advert_ignore_until = 0.0
    d._conn.query = _status_query_mock([[], []])  # type: ignore[method-assign]
    asyncio.run(d._read_status())
    assert d._advert_ignore_until == 0.0                     # nothing read: adverts still honoured


def test_a_status_read_without_switch_or_brightness_applies_the_apps_defaults() -> None:
    # Only the burst's 0x05 mode TLV came back. The app applies a fresh DeviceBaseInfoFromDevice's switch
    # (false) and brightness (100) on any successful read (h60a1 Info4Detail.n0:641-677), so the read
    # reports power off, and like every power read it holds off the lagging advertised on/off. WAS: no
    # on/off and no brightness, adverts not suppressed.
    d = _dev("H60A6")
    d.state.brightness = 42

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        return status_burst(bytes([0x05, 0x03, 0x04, 0x01, 0x00])) if frame[0] == 0xAC else []

    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    asyncio.run(d._read_status())
    assert d.state.optimistic is False and d.state.scene_code == 1
    assert (d.state.is_on, d.state.brightness) == (False, 100)
    assert d._advert_ignore_until > 0.0


def test_chunk_indices_lists_each_received_chunk_once() -> None:
    from govee_ble_local.devices._readback import chunk_indices   # the status-burst walk's helper

    assert chunk_indices([b"\xac\x00", b"\xac\xff", b"\xac\x00"]) == "0x00,0xff"
    assert chunk_indices([b"\xac"]) == ""                   # too short to carry an index
    assert chunk_indices([]) == "<none>"


def test_read_status_no_retry_when_first_attempt_succeeds() -> None:
    # The common path: don't pay the retry cost when the first query already succeeds.
    d = _dev("H60A6")
    mock = _status_query_mock([_STATUS_BURST])
    d._conn.query = mock  # type: ignore[attr-defined]
    asyncio.run(d._read_status())
    assert mock.status_calls == 1
