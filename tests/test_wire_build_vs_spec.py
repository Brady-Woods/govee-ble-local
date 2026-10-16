"""Differential: every TX builder in ``wire.build`` and ``transport.handshake`` against its counterpart in
the spec's app-traced catalogue (``spec/govee_commands.py``) or reference codec
(``spec/govee_reference.py``), byte for byte, over seeded-random inputs.

``tests/test_wire_build.py`` proves build -> parse with the generated reader, which can't see bytes the
reader doesn't model (a raw trailer, a selector byte after a request). This compares the bytes
themselves against an independent, app-traced builder, so a library frame that differs from what the spec
says the app sends fails here.

Inputs stay in each builder's valid range: the library raises on out-of-range input where the catalogue
masks with ``& 0xFF``, and that difference is deliberate (the library validates; the catalogue doesn't).

Two ratchets keep this honest instead of skipping:

* ``KNOWN_DIVERGENCES``: frames the library sends today that differ from the catalogue, each waiting on a
  decision (SPEC_CLIENT_AUDIT_ba9e6bd.md section 2). The test asserts they still differ in exactly the
  recorded way, so a fix must also remove the entry.
* ``test_spec_gap_*``: library frames the spec has no builder for yet. The test asserts the gap is still
  there, so when the spec adds the builder the ratchet has to be replaced with a real binding.

The e7 handshake has no catalogue builder either; it is bound to the reference's framing and cipher.
"""
from __future__ import annotations

import pathlib
import random
import sys
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from conftest import require_optional

from govee_ble_local.const import PSK
from govee_ble_local.transport import handshake
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


# ── framing primitive ────────────────────────────────────────────────────────
def test_frame_matches_append_bcc() -> None:
    r = _rng(1)
    for _ in range(N):
        pro = r.randrange(256)
        payload = bytes(r.randrange(256) for _ in range(r.randrange(0, 19)))
        assert b.frame(pro, *payload) == R.append_bcc(bytes([pro]) + payload)


# ── power / brightness ───────────────────────────────────────────────────────
@pytest.mark.parametrize("on", [True, False])
def test_switch(on: bool) -> None:
    assert b.switch(on) == C.build_switch(on)


@pytest.mark.parametrize("on", [True, False])
def test_switch_relay_is_outlet_zero(on: bool) -> None:
    # Byte-equal to the catalogue's outlet-0 relay frame. Whether the plug's main switch should send this
    # or the all-outlets / total-switch frame is a device-level choice (KNOWN_DIVERGENCES: plug power, #1).
    assert b.switch(on, relay=True) == C.build_plug_relay(0, on)


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
                R.build_op15_color(*rgb, (1 << segments) - 1, variant=12)


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
            assert b.color_temp(kelvin, "h60a6", segments) == R.build_op15_color(
                255, 255, 255, (1 << segments) - 1, variant=11, kelvin=kelvin, tint=tint_for_kelvin(kelvin))


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
        expect_15 = (R.build_op15_color(255, 255, 255, 0x1FFF, variant=11, kelvin=kelvin, tint=rgb) if inside
                     else R.build_op15_color(*rgb, 0x1FFF, variant=12))
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
        assert b.segment_rgb(mask, *rgb, "h60a6", segments) == R.build_op15_color(*rgb, mask, variant=12)
        assert b.segment_rgb(mask, *rgb, "h61a8", segments) == C.build_color_rgbic_0b(*rgb, mask)


def test_segment_color_temp() -> None:
    r = _rng(7)
    for _ in range(N):
        segments = r.randrange(1, 17)
        mask, kelvin = _mask(r, segments), r.randrange(20, 91) * 100
        assert b.segment_color_temp(mask, kelvin, "h60a6", segments) == R.build_op15_color(
            255, 255, 255, mask, variant=11, kelvin=kelvin, tint=tint_for_kelvin(kelvin))


def test_segment_brightness_every_mask_width() -> None:
    r = _rng(8)
    for segments in range(1, b.MAX_MASK_SEGMENTS + 1):
        for _ in range(10):
            mask, pct = _mask(r, segments), r.randrange(101)
            assert b.segment_brightness(mask, pct, "h60a6", segments) == \
                C.build_op15_brightness(pct, mask, mask_bytes=_width(segments))


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


# ── read requests ────────────────────────────────────────────────────────────
_READS: list[tuple[str, Callable[[], bytes], Callable[[], bytes]]] = [
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


# ── known divergences and spec gaps (ratchets) ───────────────────────────────
# name -> (library frame, catalogue frame, the decision it waits on)
KNOWN_DIVERGENCES: dict[str, tuple[Callable[[], bytes], Callable[[], bytes], str]] = {
    "power_query": (b.power_query, C.read_switch,
                    "#2/G16: library sends aa 01 01; SwitchController / ControllerSwitch send aa 01"),
    "brightness_query": (b.brightness_query, C.read_brightness,
                         "#2/G16: library sends aa 04 01; BrightnessController sends aa 04"),
}


@pytest.mark.parametrize("name", sorted(KNOWN_DIVERGENCES))
def test_known_divergence_still_open(name: str) -> None:
    lib, spec, why = KNOWN_DIVERGENCES[name]
    got, want = lib(), spec()
    assert got != want, f"{name} now matches the catalogue: remove it from KNOWN_DIVERGENCES ({why})"
    # The only difference is the trailing selector byte 0x01 (and the BCC it changes).
    assert got[:2] == want[:2] and got[2] == 0x01 and want[2] == 0x00 and got[3:19] == want[3:19], why


def _wide(segments: int) -> dict[str, Any]:
    mask = (1 << segments) - 1
    return {
        "color_rgb": (b.color_rgb(1, 2, 3, "h60a6", segments), R.build_op15_color(1, 2, 3, mask, variant=12)),
        "color_temp": (b.color_temp(3000, "h60a6", segments),
                       R.build_op15_color(255, 255, 255, mask, variant=11, kelvin=3000,
                                          tint=tint_for_kelvin(3000))),
    }


@pytest.mark.parametrize("segments", [17, 24, 33, 56])
def test_spec_gap_op15_colour_mask_above_16_segments(segments: int) -> None:
    """SPEC_GAPS #7: above 16 segments the app's 0x15 colour mask is ceil(n/8) bytes (BleUtils.X, the
    ``length > 16`` branch of Bytes.r / Bytes.h), as the library writes; the spec's
    ``build_op15_color`` (and the ksy ``op15_color_typed``) still hard-code a 2-byte mask, and the catalogue
    has no Bytes.h / Bytes.r builder. When the spec gains one, bind it above and drop this ratchet."""
    width = _width(segments)
    for name, (lib, spec) in _wide(segments).items():
        assert lib != spec, f"{name}: the spec now writes a wide 0x15 mask; bind it and remove this gap"
        # [33 05 15 01][colour x3][00 x5, or kelvin u2be + tint x3][mask]: everything before the mask
        # agrees, and the library's mask is the full-width LSB-first one.
        assert lib[:12] == spec[:12], name
        assert lib[12:12 + width] == ((1 << segments) - 1).to_bytes(width, "little"), name
        assert not any(lib[12 + width:19]), name


# ── protocol constants come from the generated enums ─────────────────────────
_ENUM_CONSTANTS: dict[str, tuple[int, str]] = {
    "PRO_WRITE": (0x33, "ProType.write"), "PRO_READ": (0xAA, "ProType.read"),
    "PRO_MULTI_A1": (0xA1, "ProType.multi_write"), "PRO_MULTI_A3": (0xA3, "ProType.multi_write_v1"),
    "PRO_MULTI_A4": (0xA4, "ProType.multi_write_v2"), "PRO_MULTI_AC": (0xAC, "ProType.multi_reply_read"),
    "PRO_NOTIFY": (0xEE, "ProType.notify"),
    "CMD_SWITCH": (0x01, "Command.switch"), "CMD_BRIGHTNESS": (0x04, "Command.brightness"),
    "CMD_MODE": (0x05, "Command.mode"), "CMD_SOFT_VERSION": (0x06, "Command.soft_version"),
    "CMD_DEVICE_INFO": (0x07, "Command.device_info"), "CMD_ZONE": (0x30, "Command.light_direction_or_zone"),
    "CMD_BAR_SWITCH": (0x36, "Command.compose_light_switch"),
    "CMD_GRADUAL_WIFI_BLE": (0xA3, "Command.gradual_wifi_ble"),
    "CMD_SECRET_READ": (0xB1, "Command.secret_read"), "CMD_SECRET_WRITE": (0xB2, "Command.secret_write"),
    "CMD_PLUG_SPEC": (0xB3, "Command.plug_spec"), "CMD_PLUG_SYNC_TIME": (0xB5, "Command.plug_sync_time"),
    "CMD_BULB_COLOR_V1": (0xA2, "Command.bulb_string_color_read"),
    "CMD_BULB_COLOR_V2": (0xA5, "Command.local_color_read"), "CMD_IC_NUM": (0x40, "Command.ic_num"),
    "CMD_SECTION_COUNT": (0x41, "Command.on_off_memory"),
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
    assert type(got) is int and got == value == int(getattr(getattr(b._F, enum_name), member))


def test_handshake_constants_are_the_spec_enums() -> None:
    assert handshake.HANDSHAKE_OPCODE == 0xE7 == int(b._F.ProType.handshake)
    assert handshake._STEP_SESSION_KEY == 0x01 == int(b._F.HandshakeOp.v1_request_session_key)
    assert handshake._STEP_CONFIRM == 0x02 == int(b._F.HandshakeOp.v1_confirm)
