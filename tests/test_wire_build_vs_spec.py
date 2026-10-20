"""Differential: every TX builder in ``wire.build`` and ``transport.handshake`` against its counterpart in
the spec's app-traced catalogue (``spec/govee_commands.py``) or reference codec
(``spec/govee_reference.py``), byte for byte, over seeded-random inputs.

``tests/test_wire_build.py`` proves build -> parse with the generated reader, which can't see bytes the
reader doesn't model (a raw trailer, a selector byte after a request). This compares the bytes
themselves against an independent, app-traced builder, so a library frame that differs from what the spec
says the app sends fails here.

Inputs stay in each builder's valid range: the library raises on out-of-range input where the catalogue
masks with ``& 0xFF``, and that difference is deliberate (the library validates; the catalogue doesn't).

No known divergence is left: when a library frame must differ from the catalogue pending a decision, it
goes in a ratchet that asserts the difference (so the fix has to remove it) rather than being skipped.

The e7 handshake has no catalogue builder either; it is bound to the reference's framing and cipher.
"""
from __future__ import annotations

import pathlib
import random
import sys
from collections.abc import Callable, Iterator

import pytest
from conftest import require_optional

from govee_ble_local.const import PSK
from govee_ble_local.transport import handshake
from govee_ble_local.wire import _spec
from govee_ble_local.wire import build as b
from govee_ble_local.wire.cct import _KELVIN_BY_TINT, tint_for_kelvin

_SPEC = pathlib.Path(__file__).resolve().parent.parent / "spec"
if not (_SPEC / "govee_commands.py").exists():  # pragma: no cover - sdist without spec/
    pytest.skip("spec/govee_commands.py not present", allow_module_level=True)
if str(_SPEC) not in sys.path:
    # govee_commands imports its framing primitives as a top-level `govee_reference`.
    sys.path.insert(0, str(_SPEC))

import govee_commands as C  # noqa: E402
import govee_reference as R  # noqa: E402

N = 300   # random cases per builder


def _rng(salt: int) -> random.Random:
    return random.Random(0x5EC0 ^ salt)  # noqa: S311 - deterministic test vectors


def _rgb(r: random.Random) -> tuple[int, int, int]:
    return r.randrange(256), r.randrange(256), r.randrange(256)


def _not_tint(r: random.Random) -> tuple[int, int, int]:
    """A random colour that is not one of the 178 Kelvin tint colours (an RGB pick the app sends as RGB)."""
    while True:
        c = _rgb(r)
        if (c[0] << 16 | c[1] << 8 | c[2]) not in _KELVIN_BY_TINT:
            return c


def _tints() -> Iterator[tuple[tuple[int, int, int], int]]:
    """Every tint colour with its Kelvin, in the table's order."""
    for rgb, kelvin in _KELVIN_BY_TINT.items():
        yield ((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF), kelvin


def _width(segments: int) -> int:
    """Segment-mask bytes the spec documents (``build_op15_brightness`` / ``build_color_rgbic_0b``:
    u16le up to 16 positions, ceil(n/8) bytes above; BleUtils.X)."""
    return 2 if segments <= 16 else -(-segments // 8)


def _mask(r: random.Random, segments: int) -> int:
    return r.randrange(1 << segments)


# ── power / brightness ───────────────────────────────────────────────────────
@pytest.mark.parametrize("on", [True, False])
def test_switch(on: bool) -> None:
    assert b.switch(on) == C.build_switch(on)


@pytest.mark.parametrize("on", [True, False])
def test_switch_relay_is_outlet_zero(on: bool) -> None:
    # Byte-equal to the catalogue's outlet-0 relay frame. Whether the plug's main switch should send this
    # or the all-outlets / total-switch frame is a device-level choice (KNOWN_DIVERGENCES: plug power, #1).
    assert b.switch(on, relay=True) == C.build_plug_relay(0, on)


@pytest.mark.parametrize("on", [True, False])
def test_plug_main_switch_frames(on: bool) -> None:
    # gt 50 / 90: SwitchControllerV2(on, 15), every outlet; gt 307: Controller4TotalSwitch.
    assert b.plug_all_outlets(on) == C.build_plug_relay(15, on)
    assert b.plug_total_switch(on) == C.build_plug_total_switch(on)


def test_brightness_percent_and_raw() -> None:
    for pct in range(101):
        assert b.brightness(pct) == C.build_brightness(pct)
    for level in range(256):
        assert b.brightness_raw(level) == C.build_brightness(level)
    # The library clamps a percent; the catalogue writes the byte it is given.
    assert b.brightness(-5) == C.build_brightness(0)
    assert b.brightness(250) == C.build_brightness(100)


# ── colour: whole device ─────────────────────────────────────────────────────
@pytest.mark.parametrize("scheme", ["h6006", "h6188"])
def test_color_rgb_0d(scheme: b.ColorScheme) -> None:
    r = _rng(2)
    for _ in range(N):
        rgb = _rgb(r)
        assert b.color_rgb(*rgb, scheme) == C.build_color_cct_0d(*rgb)


def test_color_rgb_0b_h61a8() -> None:
    r = _rng(3)
    for segments in range(1, 17):
        for _ in range(20):
            rgb = _rgb(r)
            assert b.color_rgb(*rgb, "h61a8", segments) == \
                C.build_color_rgbic_0b(*rgb, (1 << segments) - 1)


def test_color_rgb_15_h60a6_up_to_16_segments() -> None:
    r = _rng(4)
    for segments in range(1, 17):
        for _ in range(20):
            rgb = _rgb(r)
            assert b.color_rgb(*rgb, "h60a6", segments) == \
                C.build_op15_color_bytes_h(*rgb, [True] * segments)


def test_color_temp_0d() -> None:
    for kelvin in range(1000, 10001, 100):
        t = tint_for_kelvin(kelvin)
        # h6006: Config.bu -> Bytes.p, the tint in both colour slots.
        assert b.color_temp(kelvin, "h6006") == C.build_color_cct_0d(*t, kelvin, t)
        # h6188: Config.GM -> Bytes.A, white in the colour slot.
        assert b.color_temp(kelvin, "h6188") == C.build_color_cct_0d(255, 255, 255, kelvin, t)


def test_color_temp_15_h60a6_up_to_16_segments() -> None:
    for segments in (1, 8, 13, 15, 16):
        for kelvin in range(2000, 9001, 100):
            assert b.color_temp(kelvin, "h60a6", segments) == C.build_op15_color_temp_bytes_r(
                tint_for_kelvin(kelvin), kelvin, [True] * segments)


def test_h6061_color_temp() -> None:
    for panels in range(1, b.H6061_MAX_PANELS + 1):
        n = b.H6061_MAX_PANELS if panels == 1 else panels   # a one-panel layout is written as 23
        for kelvin in range(2000, 9001, 500):
            assert b.color_temp(kelvin, "h6061", panels) == C.build_op15_color_v5(
                *tint_for_kelvin(kelvin), (1 << n) - 1, kelvin=kelvin, mask_bytes=b.H6061_MASK_BYTES)


def test_h6061_rgb_group_frames() -> None:
    r = _rng(5)
    for panels in range(1, b.H6061_MAX_PANELS + 1):
        n = b.H6061_MAX_PANELS if panels == 1 else panels
        rgb = _not_tint(r)
        assert b.color_rgb_frames(*rgb, "h6061", panels) == [
            C.build_op15_color_group(g, [rgb] * min(4, n - (g - 1) * 4)) for g in range(1, -(-n // 4) + 1)]


# ── colour: an RGB pick that is a Kelvin tint colour goes out as CCT ──────────
@pytest.mark.parametrize("kelvin_range", [(2000, 9000), (2700, 6500)])
def test_rgb_pick_as_kelvin_every_tint(kelvin_range: tuple[int, int]) -> None:
    lo, hi = kelvin_range
    for rgb, kelvin in _tints():
        inside = lo <= kelvin <= hi
        # 0x0d (Config.bu -> Bytes.p): the picked colour in the colour slot, the Kelvin's first tint after.
        expect_0d = (C.build_color_cct_0d(*rgb, kelvin, tint_for_kelvin(kelvin)) if inside
                     else C.build_color_cct_0d(*rgb))
        assert b.color_rgb_frames(*rgb, "h6006", kelvin_range=kelvin_range) == [expect_0d]
        assert b.color_rgb_frames(*rgb, "h6188", kelvin_range=kelvin_range) == [expect_0d]
        # 0x15 (Config.Yt -> Bytes.r): white in the colour slot, the picked colour as the tint.
        expect_15 = (C.build_op15_color_temp_bytes_r(rgb, kelvin, [True] * 13) if inside
                     else C.build_op15_color_bytes_h(*rgb, [True] * 13))
        assert b.color_rgb_frames(*rgb, "h60a6", 13, kelvin_range=kelvin_range) == [expect_15]
        # h61a8 has no CCT: always RGB.
        assert b.color_rgb_frames(*rgb, "h61a8", 15, kelvin_range=kelvin_range) == [
            C.build_color_rgbic_0b(*rgb, 0x7FFF)]


def test_rgb_pick_as_kelvin_h6061_default_range() -> None:
    for rgb, kelvin in _tints():
        frames = b.color_rgb_frames(*rgb, "h6061", 23)
        if 2000 <= kelvin <= 9000:   # KelvinConfig: no entry for goodsType 51 -> version 0
            assert frames == [C.build_op15_color_v5(*rgb, (1 << 23) - 1, kelvin=kelvin, mask_bytes=3)]
        else:
            assert frames == [C.build_op15_color_group(g, [rgb] * min(4, 23 - (g - 1) * 4))
                              for g in range(1, 7)]


# ── colour: segments ─────────────────────────────────────────────────────────
def test_segment_rgb() -> None:
    r = _rng(6)
    for _ in range(N):
        segments = r.randrange(1, 17)
        mask, rgb = _mask(r, segments), _not_tint(r)
        assert b.segment_rgb(mask, *rgb, "h60a6", segments) == \
            C.build_op15_color_bytes_h(*rgb, _pos_set(mask, segments))
        assert b.segment_rgb(mask, *rgb, "h61a8", segments) == C.build_color_rgbic_0b(*rgb, mask)


def test_segment_color_temp() -> None:
    r = _rng(7)
    for _ in range(N):
        segments = r.randrange(1, 17)
        mask, kelvin = _mask(r, segments), r.randrange(20, 91) * 100
        assert b.segment_color_temp(mask, kelvin, "h60a6", segments) == C.build_op15_color_temp_bytes_r(
            tint_for_kelvin(kelvin), kelvin, _pos_set(mask, segments))


def test_segment_brightness_every_mask_width() -> None:
    r = _rng(8)
    for segments in range(1, b.MAX_MASK_SEGMENTS + 1):
        for _ in range(10):
            mask, pct = _mask(r, segments), r.randrange(101)
            # The reference takes the raw byte; the library clamps to the app's slider range, 1..100.
            assert b.segment_brightness(mask, pct, "h60a6", segments) == \
                C.build_op15_brightness(max(1, pct), mask, mask_bytes=_width(segments))


# ── zones / bars / scenes / misc writes ──────────────────────────────────────
def test_zone_bar_gradual() -> None:
    for zone in range(256):
        for on in (True, False):
            assert b.zone_power(zone, on) == C.build_zone_power(zone, on)
    for left in (True, False):
        for right in (True, False):
            assert b.bar_switch(left, right) == C.build_bar_switch_pair(left, right)
    for on in (True, False):
        assert b.gradual(on) == C.build_gradual(on)


def test_scene_activate_every_code() -> None:
    for code in range(0x10000):
        assert b.scene_activate(code) == C.build_scene(code)


def test_scene_upload_a3() -> None:
    r = _rng(9)
    lengths = [0, 1, 13, 14, 15, 30, 31, 32, 48, 49, 4332] + [r.randrange(0, 4333) for _ in range(N)]
    for n in lengths:
        value, comm = bytes(r.randrange(256) for _ in range(n)), r.randrange(256)
        assert b.scene_upload_a3(value, comm) == C.build_multi_a3_v1(comm, value)


def test_scene_upload_a4_mtu() -> None:
    r = _rng(10)
    for _ in range(N):
        mtu = r.choice([20, 23, 185, 244, 247, 512, r.randrange(20, 513)])
        n = r.choice([0, 1, mtu - 8, mtu - 7, mtu - 8 + (mtu - 4), r.randrange(0, 3000)])
        value, comm = bytes(r.randrange(256) for _ in range(n)), r.randrange(256)
        assert b.scene_upload_a4_mtu(value, comm, mtu=mtu) == C.build_mtu_a4_frames(comm, value, mtu)


def test_plug_and_secret_writes() -> None:
    r = _rng(11)
    for _ in range(N):
        ts, hh, mm = r.randrange(1 << 32), r.randrange(-12, 15), r.choice([0, 30, 45, -30])
        assert b.plug_sync_time(ts, hh, mm) == C.build_plug_sync_time(ts, hh, mm)
        secret = bytes(r.randrange(256) for _ in range(b.SECRET_LEN))
        assert b.secret_check(secret) == C.build_secret_write(secret)
    assert b.light_effect_stop() == C.build_set_light_end()


# ── read requests ────────────────────────────────────────────────────────────
_READS: list[tuple[str, Callable[[], bytes], Callable[[], bytes]]] = [
    # No selector byte, as SwitchController / ControllerSwitch and BrightnessController send them (the
    # library sent aa 01 01 / aa 04 01 until the user chose to match the app; no firmware reads byte 2).
    ("power_query", b.power_query, C.read_switch),
    ("brightness_query", b.brightness_query, C.read_brightness),
    ("mode_query", b.mode_query, C.read_mode),
    ("bar_switch_query", b.bar_switch_query, C.read_bar_switch),
    ("gradual_query", b.gradual_query, C.read_gradual),
    ("secret_read", b.secret_read, C.read_secret),
    ("plug_spec_query", b.plug_spec_query, C.read_plug_spec),
    ("soft_version_query", b.soft_version_query, C.read_soft_version),
    ("ic_count_query", b.ic_count_query, C.read_ic_segment),
    # The catalogue's 0x41 read cites the on_off_memory controller; the frame is the same bytes.
    ("section_count_query", b.section_count_query, C.read_on_off_memory_or_section_count),
    ("status_query(full)", lambda: b.status_query(full=True),
     lambda: C.build_status_request([0x41, 0x30, 0xA5])),
    ("status_query()", b.status_query, lambda: C.build_status_request([0x41, 0x30])),
]


@pytest.mark.parametrize(("name", "lib", "spec"), _READS, ids=[x[0] for x in _READS])
def test_read_request(name: str, lib: Callable[[], bytes], spec: Callable[[], bytes]) -> None:
    assert lib() == spec(), name


def test_parametrised_read_requests() -> None:
    for seq in range(256):
        assert b.bulb_group_query(seq) == C.read_color_group(seq)
        assert b.bulb_group_query(seq, v2=False) == C.read_bulb_group_color(seq)
    for sel in range(256):
        assert b.device_info_query(sel) == C.read_device_info(sel)


# ── handshake (e7) ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(("build", "step"), [(handshake.build_step1, 0x01), (handshake.build_step2, 0x02)])
def test_handshake_frames(monkeypatch: pytest.MonkeyPatch, build: Callable[..., bytes], step: int) -> None:
    """``Controller4Aes.e`` / ``.f``: ``[E7, step, 17 random bytes, BCC]``, PSK-encrypted with the V1 cipher.
    The catalogue has no e7 builder, so this binds to the reference's framing (``append_bcc``) and cipher
    (``crypt_v1``, an independent AES)."""
    require_optional("Crypto.Cipher.AES", reason="pycryptodome (reference AES) not installed")
    r = _rng(12 + step)
    for _ in range(50):
        fill = bytes(r.randrange(256) for _ in range(17))
        monkeypatch.setattr(handshake.os, "urandom", lambda n, fill=fill: fill[:n])
        plain = R.append_bcc(bytes([0xE7, step]) + fill)
        assert build() == R.crypt_v1(plain, PSK, encrypt=True)


# ── wide masks ───────────────────────────────────────────────────────────────
def _pos_set(mask: int, segments: int) -> list[bool]:
    """The app's position set for ``mask`` on a ``segments``-segment device."""
    return [bool(mask >> i & 1) for i in range(segments)]


def test_op15_colour_every_mask_width() -> None:
    """0x15 colour and CCT at every segment count up to 112, against the catalogue's Bytes.h / Bytes.r
    builders (the app's 17-byte ext array, BleUtils.X above 16 positions, coerceAtMost 7 mask bytes).
    Above 16 segments the mask is ceil(n/8) bytes, cut to 7; this was the spec gap the audit found (#7),
    closed in faa2cb7."""
    r = _rng(13)
    for segments in range(1, b.MAX_MASK_SEGMENTS + 1):
        if segments > 56:
            mask = _mask(r, segments)
            rgb = _not_tint(r)
            assert b.color_rgb(*rgb, "h60a6", segments) == C.build_op15_color_bytes_h(*rgb, [True] * segments)
            assert b.segment_rgb(mask, *rgb, "h60a6", segments) == \
                C.build_op15_color_bytes_h(*rgb, _pos_set(mask, segments))
            continue
        whole = [True] * segments
        for _ in range(4):
            rgb, kelvin = _not_tint(r), r.randrange(27, 66) * 100
            mask = _mask(r, segments)
            tint = tint_for_kelvin(kelvin)
            assert b.color_rgb(*rgb, "h60a6", segments) == C.build_op15_color_bytes_h(*rgb, whole)
            assert b.segment_rgb(mask, *rgb, "h60a6", segments) == \
                C.build_op15_color_bytes_h(*rgb, _pos_set(mask, segments))
            assert b.color_temp(kelvin, "h60a6", segments) == \
                C.build_op15_color_temp_bytes_r(tint, kelvin, whole)
            assert b.segment_color_temp(mask, kelvin, "h60a6", segments) == \
                C.build_op15_color_temp_bytes_r(tint, kelvin, _pos_set(mask, segments))


# ── protocol constants come from the generated enums ─────────────────────────
_ENUM_CONSTANTS: dict[str, tuple[int, str]] = {
    "PRO_WRITE": (0x33, "ProType.write"), "PRO_READ": (0xAA, "ProType.read"),
    "PRO_MULTI_A1": (0xA1, "ProType.multi_write"), "PRO_MULTI_A3": (0xA3, "ProType.multi_write_v1"),
    "PRO_MULTI_A4": (0xA4, "ProType.multi_write_v2"), "PRO_MULTI_AC": (0xAC, "ProType.status_read"),
    "PRO_NOTIFY": (0xEE, "ProType.notify"),
    "CMD_SWITCH": (0x01, "Command.power"), "CMD_TOTAL_SWITCH": (0x02, "Command.cmd_02"),
    "CMD_BRIGHTNESS": (0x04, "Command.brightness"),
    "CMD_MODE": (0x05, "Command.mode"), "CMD_SOFT_VERSION": (0x06, "Command.soft_version"),
    "CMD_DEVICE_INFO": (0x07, "Command.device_info"), "CMD_ZONE": (0x30, "Command.cmd_30"),
    "CMD_BAR_SWITCH": (0x36, "Command.cmd_36"),
    "CMD_GRADUAL_WIFI_BLE": (0xA3, "Command.gradual"),
    "CMD_SECRET_READ": (0xB1, "Command.secret_read"), "CMD_SECRET_WRITE": (0xB2, "Command.secret_write"),
    "CMD_PLUG_SPEC": (0xB3, "Command.plug_spec"), "CMD_PLUG_SYNC_TIME": (0xB5, "Command.plug_sync_time"),
    "CMD_BULB_COLOR_V1": (0xA2, "Command.color_group_rgb"),
    "CMD_BULB_COLOR_V2": (0xA5, "Command.color_group"), "CMD_IC_NUM": (0x40, "Command.ic_num"),
    "CMD_SECTION_COUNT": (0x41, "Command.cmd_41"),
    "SUB_SCENE": (0x04, "SubMode.scene"), "SUB_COLOR_15": (0x15, "SubMode.color_rgbic_15"),
    "SUB_COLOR_0D": (0x0D, "SubMode.color_cct_0d"), "SUB_COLOR_0B": (0x0B, "SubMode.color_rgbic_0b"),
    "OP15_SET_COLOR": (0x01, "Op15.set_color"), "OP15_SET_BRIGHTNESS": (0x02, "Op15.set_brightness"),
    "OP15_SET_GROUP_COLOR": (0x04, "Op15.set_color_group"),
}


@pytest.mark.parametrize("name", sorted(_ENUM_CONSTANTS))
def test_protocol_constant_is_the_spec_enum(name: str) -> None:
    value, enum_path = _ENUM_CONSTANTS[name]
    enum_name, member = enum_path.split(".")
    got = getattr(b, name)
    assert type(got) is int and got == value == int(getattr(getattr(_spec.Common, enum_name), member))


def test_handshake_constants_are_the_spec_enums() -> None:
    assert handshake.HANDSHAKE_OPCODE == 0xE7 == int(_spec.Common.ProType.handshake)
    assert handshake._STEP_SESSION_KEY == 0x01 == int(_spec.Common.HandshakeOp.v1_request_session_key)
    assert handshake._STEP_CONFIRM == 0x02 == int(_spec.Common.HandshakeOp.v1_confirm)


# ── the builders write with the generated read-write classes ─────────────────
def test_builders_write_with_the_spec_classes() -> None:
    # A frame is a GoveeBleFrame tree serialized by Kaitai: reading one back gives the same tree, and a
    # value the spec type can't carry (s1 time-zone hour) is refused as bad input, not masked.
    from govee_ble_local.exceptions import GoveeBleInvalidValue

    f = _spec.read_frame(b.plug_sync_time(1_760_000_000, -5, -30), _spec.TX, _spec.PLAIN)
    p = f.body.params
    assert (p.unix_seconds, p.marker, p.tz_hour, p.tz_min) == (1_760_000_000, 1, -5, -30)
    with pytest.raises(GoveeBleInvalidValue, match=r"doesn.t fit the spec"):
        b.plug_sync_time(0, 200, 0)
