"""Frame builders — the write side, written by the spec-generated read-write classes.

Every frame is a ``govee_ble_frame(direction tx, profile)`` tree of the spec's own types (spec/*.ksy,
generated with ``tools/gen_kaitai.sh``), written by Kaitai through :mod:`._spec`; the library only picks
which type and values the app sends, and the profile (the device facts: command forms, segment count)
that types them. What stays in code is what Kaitai can't express: the checksum (:func:`._spec.emit`),
packet counts and chunking of the multi-packet uploads, the app's colour and Kelvin choices (tint table,
RGB-pick-as-CCT, clamps), and the caller-input checks. ``tests/test_wire_build_vs_spec.py`` binds every
builder byte for byte to the app-traced catalogue, and ``tests/test_wire_build.py`` reads each frame
back. Byte layouts follow the spec and the decompiled app (each builder cites its source). Not all of
them are hardware-verified: the device profiles note which SKUs are, and the short-value 0xA3 upload
(:func:`scene_upload_a3`) is source-modeled from ``MultipleControllerCommV1.makeSendBytesV2``, not
live-verified. Scene uploads take the raw ``value`` bytes (the device layer base64-decodes + strips the
param).

A builder raises GoveeBleNotSupported when the colour scheme cannot express the request (no such
frame in the app for that family) and GoveeBleInvalidValue for bad caller input.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Final, Literal, Never, get_args

from ..const import FRAME_LEN, SCENE_A3_HEADER_LEN, SCENE_A3_PIECE_LEN
from ..devices._resolve import HEARTBEATS as _HEARTBEATS   # standard library only: no import cycle
from ..exceptions import GoveeBleInvalidValue, GoveeBleNotSupported
from . import _spec
from ._spec import Common as _C, Frame as _F
from .cct import kelvin_for_tint, tint_for_kelvin

_PLAIN: Final = _spec.PLAIN   # a frame whose layout no device fact changes


# ── writing with the generated classes ───────────────────────────────────────
def _frame_of(prof: Any, pro_type: Any, make_body: Callable[[Any], Any], length: int = FRAME_LEN) -> bytes:
    """A TX frame of ``pro_type`` under ``prof`` whose body ``make_body(frame)`` builds (linked to it)."""
    return _spec.build_frame(pro_type, prof, make_body, length)


def _write_frame(prof: Any, command: Any, make_params: Callable[[Any], Any] | None) -> bytes:
    """``33 <command> <params>``: a ``write_command`` whose params ``make_params(write_command)`` builds
    (None: a command with no params); the zero fill after them is its ``rest``."""
    def body(f: Any) -> Any:
        wc = _spec.node(_F.WriteCommand, f, prof, command=command, rest=_spec.echo())
        if make_params is not None:
            wc.params = make_params(wc)
        return wc
    return _frame_of(prof, _C.ProType.write, body)


def _params(cls: Any, **fields: Any) -> Callable[[Any], Any]:
    """``make_params`` for a params type ``cls`` with no ksy params of its own."""
    return lambda parent: _spec.node(cls, parent, **fields)


def _form_params(cls: Any, form: Any, body_cls: Any, **fields: Any) -> Callable[[Any], Any]:
    """``make_params`` for a form-switched params type ``cls(form)`` (``power_write``, ``cmd_30_write``,
    ...) whose ``body`` is ``body_cls`` with ``fields``."""
    def make(parent: Any) -> Any:
        p = _spec.node(cls, parent, form)
        p.body = _spec.node(body_cls, p, **fields)
        return p
    return make


def _mode_frame(prof: Any, sub_mode: Any, make_params: Callable[[Any], Any]) -> bytes:
    """``33 05 <sub_mode> <params>`` (``mode_write``)."""
    def params(wc: Any) -> Any:
        mw = _spec.node(_F.ModeWrite, wc, prof, sub_mode=sub_mode)
        mw.params = make_params(mw)
        return mw
    return _write_frame(prof, _C.Command.mode, params)


def _op15_frame(prof: Any, op: Any, make_data: Callable[[Any], Any]) -> bytes:
    """``33 05 15 <op> <data>`` (``color_15_write``)."""
    def params(mw: Any) -> Any:
        c15 = _spec.node(_F.Color15Write, mw, prof, op=op)
        c15.data = make_data(c15)
        return c15
    return _mode_frame(prof, _C.SubMode.color_rgbic_15, params)


def _request(command: Any, selector_cls: Any = None, **selector: Any) -> bytes:
    """``aa <command> [selector]``: the spec's ``read_request`` under the frame's profile (``_PLAIN``), the
    app's ``BleUtils.Companion.p(0xAA, command, ext)``; the selector is typed for the reads that carry one
    (``mode_query``, ``device_info_query``, ``group_query``), and the zero fill after it is the request's
    ``rest``. The AA 12 / 13 / B0 selectors are typed per profile form (``cmd_12_query`` ...), which this
    builder doesn't build: the library sends none of those reads."""
    def body(f: Any) -> Any:
        rr = _spec.node(_F.ReadRequest, f, f.profile, command=command, rest=_spec.echo())
        if selector_cls is not None:
            rr.selector = _spec.node(selector_cls, rr, **selector)
        return rr
    return _frame_of(_PLAIN, _C.ProType.read, body)

def ceil_div(a: int, b: int) -> int:
    """Ceiling integer division (``ceil(a / b)`` for positive ``b``)."""
    return -(-a // b)


# Protocol constants, taken from the generated classes' enums so the spec is their one source (int()
# keeps them plain ints, as the frame bytes and the ``==`` checks against raw bytes need).
# proType (enum govee_common::pro_type)
PRO_WRITE: Final = int(_C.ProType.write)                 # 0x33
PRO_READ: Final = int(_C.ProType.read)                   # 0xAA
PRO_MULTI_A1: Final = int(_C.ProType.multi_write)        # 0xA1: MultiPackageManager stream (RX only)
PRO_MULTI_A2: Final = int(_C.ProType.multi_read)         # 0xA2: multi_read, device -> app (RX only)
PRO_MULTI_A3: Final = int(_C.ProType.multi_write_v1)     # 0xA3
PRO_MULTI_A4: Final = int(_C.ProType.multi_write_v2)     # 0xA4
PRO_MULTI_AC: Final = int(_C.ProType.status_read)        # 0xAC
PRO_NOTIFY: Final = int(_C.ProType.notify)               # 0xEE: device pushes (parse.parse_notify)

# command (enum govee_common::command)
CMD_SWITCH: Final = int(_C.Command.power)                        # 0x01
CMD_TOTAL_SWITCH: Final = int(_C.Command.cmd_02)                 # 0x02: H5089's total switch (form_02)
CMD_BRIGHTNESS: Final = int(_C.Command.brightness)               # 0x04
CMD_MODE: Final = int(_C.Command.mode)                           # 0x05
CMD_SOFT_VERSION: Final = int(_C.Command.soft_version)           # 0x06
CMD_DEVICE_INFO: Final = int(_C.Command.device_info)             # 0x07
CMD_ZONE: Final = int(_C.Command.cmd_30)                         # 0x30 (form_30: zone power on H60A6)
CMD_BAR_SWITCH: Final = int(_C.Command.cmd_36)                   # 0x36 (form_36: H6047's two bars)
CMD_GRADUAL_WIFI_BLE: Final = int(_C.Command.gradual)            # 0xA3: gradual/fade on BLE<->wifi handoff
CMD_SECRET_READ: Final = int(_C.Command.secret_read)             # 0xB1
CMD_SECRET_WRITE: Final = int(_C.Command.secret_write)           # 0xB2
CMD_PLUG_SPEC: Final = int(_C.Command.plug_spec)                 # 0xB3
CMD_PLUG_SYNC_TIME: Final = int(_C.Command.plug_sync_time)       # 0xB5
# The keep-alive reads' command codes (heartbeat): aa 01, aa 00, aa 33 and aa 36, the resolver's HEARTBEATS.
_HEARTBEAT_CODES: Final = frozenset(int(_C.Command[name]) for name in _HEARTBEATS)

# mode (0x05) sub-mode (enum govee_common::sub_mode)
SUB_SCENE: Final = int(_C.SubMode.scene)                 # 0x04
SUB_COLOR_15: Final = int(_C.SubMode.color_rgbic_15)     # 0x15 (H60A6/H6047/H6641)
SUB_COLOR_0D: Final = int(_C.SubMode.color_cct_0d)       # 0x0D (H6006/H6008/H6052 bulbs)
SUB_COLOR_0B: Final = int(_C.SubMode.color_rgbic_0b)     # 0x0B (the h61a8 scheme's frames)
SUB_COLOR_02: Final = int(_C.SubMode.color_legacy)       # 0x02 (the legacy_02 scheme's frames)

# op15 (enum govee_common::op15) — sub-op after 0x15
OP15_SET_COLOR: Final = int(_C.Op15.set_color)            # 0x01
OP15_SET_BRIGHTNESS: Final = int(_C.Op15.set_brightness)  # 0x02

# Device-info (0x07) selectors (enum device_info_selector). SN (device id) and HARD (hardware version)
# are the reads the app makes (SnController.p, HardVersionController.p); BASIC / WIFI are the binary forms.
INFO_BASIC: Final = int(_C.DeviceInfoSelector.basic)          # 0x10
INFO_WIFI: Final = int(_C.DeviceInfoSelector.wifi)            # 0x11
INFO_SN: Final = int(_C.DeviceInfoSelector.sn)                # 0x02
INFO_HARD: Final = int(_C.DeviceInfoSelector.hard_version)    # 0x03

# power payload values (enums light_power and relay_write)
POWER_ON: Final = int(_F.LightPower.light_on)        # 0x01
POWER_OFF: Final = int(_F.LightPower.light_off)      # 0x00
# Relays: outlet 0 0x11 / 0x10, every outlet 0xFF / 0xF0 (SwitchControllerV2.q, outlet 0 and 15: 0xF0 | 0x0F).
RELAY_ON: Final = int(_F.RelayWrite.relay0_on)
RELAY_OFF: Final = int(_F.RelayWrite.relay0_off)
RELAY_ALL_ON: Final = int(_F.RelayWrite.all_on)
RELAY_ALL_OFF: Final = int(_F.RelayWrite.all_off)

# Upload comm bytes (enum comm_byte, named after the app's MULTI_* constants): the H60A6 dialect-B
# DIY / graffiti upload sends 0x58, which the app's table names cube_in_diy; the H6052 type-5
# professional graffiti sends v1_graffiti_multi_layer (0x09).
COMM_H60A6: Final = int(_F.CommByte.cube_in_diy)
COMM_H6052_GRAFFITI: Final = int(_F.CommByte.v1_graffiti_multi_layer)
# goodsType 411's colour write (the h3010 scheme): its 0xA3 value goes out on sub_mode_color_multi (0x6e).
COMM_COLOR_MULTI: Final = int(_F.CommByte.sub_mode_color_multi)

# The command forms the builders write under (govee_profile command_forms): which body a shared command
# carries is a device fact, not in the frame.
_FORM_LIGHT: Final = int(_C.Form01.light)              # 33 01 [1 / 0]
_FORM_RELAYS: Final = int(_C.Form01.relays)            # 33 01 [relay byte]
_FORM_TOTAL_SWITCH: Final = int(_C.Form02.total_switch)
_FORM_ZONE_POWER: Final = int(_C.Form30.zone_power)
_FORM_LIGHT_SWITCH: Final = int(_C.Form30.light_switch)
_FORM_BAR_PAIR: Final = int(_C.Form36.bar_pair)
_FORM_0B_RGB_MASK: Final = int(_C.Form0b.rgb_mask)


# "h60a6" = 0x15 RGBIC (segment mask); CCT is the white-slot form `15 01 ff ff ff <K> <tint> <mask>`
# (Bytes.r type 1) with the app's Kelvin->RGB tint.
# "h6006" = 0x0d bulbs (H6006/H6008/H6052); CCT is the both-slot form `0d <tint> <K> <tint>`
# (Config.bu -> Bytes.p).
# "h61a8" = 0x0b RGBIC (no CCT): the app's colour instruction 3 (Config.J[3] = cu -> Bytes.d). By goodsType
# (Config.x) goodsType 3 sends it, and goodsTypes 13, 17, 18 and 21 below their gates; by SKU (Config.w,
# which the app reads first) H7022 / H7024, and the old H6102, H6107, H6116, H6117, H6125, H6126, H6127,
# H6161 and H6163 by their firmware. No built-in profile uses it: the H61A8 (goodsType 19) sends the h60a6
# forms, as do 123 and the other dreamcolorlightv1 goodsTypes from 19 up (devices.yaml dreamcolorlightv1
# client_profile); it stays for custom profiles.
# "h6188" = 0x0d with the white-slot CCT: same RGB frame as h6006, but the CCT is `0d ff ff ff <K> <tint>`
# (Config.N[2] = Config.GM -> Bytes.A), NOT the h6006 form. It is the illumination tab's Kelvin write
# (H8015, via KelvinConfig.b) and the rgblight group / automation frame; it is no longer H6188's, whose
# colour tab sends the h6006 form (devices.yaml h6188 row). The name is kept: it is the public ColorScheme
# literal.
# "h6061" = Glide Hexa panels, 0x15 colour version 5 (Config.Zt), whole device only: RGB is
# `15 01 R G B 00 00 <mask>` (Bytes.l), CCT `15 01 <tint> <K> <mask>` (Bytes.u), one frame each. See the
# "Glide Hexa" section below.
# "legacy_02" = colour instruction version 4 (Config.J[4] = Config.fu), the legacy 0x02 sub-mode, whole
# device only (fu drops the position set): RGB `02 R G B` (Bytes.a); a Kelvin goes out as its tint colour,
# `02 ff ff ff 01 <tint>` (Bytes.o), with no Kelvin on the wire. Config.w / Config.x give it to goodsType-0
# SKUs such as H6105 / H6107 / H6129 / H6185 and the rgblight H7310 / H7312 / H7317 / H7318 / H6165 / H6166.
# "kelvin_v9" = colour instruction version 9, a Kelvin and nothing else: `15 04 <K u16be>` (the spec's
# op15_group form kelvin_v9, Config.N[9] = Config.JM -> Bytes.D, and the colour page's Config.J[9] = hu, which
# maps every pick to a Kelvin first), with no RGB frame, no segment mask and no tint. Config.x gives it to
# goodsTypes 57 and 67 (H6071 / H6075), whose illumination tab sends it with 33 04 (devices.yaml h6071).
# "h3010" = colour instruction version 8 (Config.J[8] = Config.gu), goodsType 411 (H3010-H3013), whole device
# only: Config.E[411] sends the colour page's single colour and colour temperature through
# ColorModeControllerConfig.j as one MultiController unit, the 0xA3 packets of the value
# `[01] <colour> <K u2be> <tint> <mask>` on comm byte 0x6e (Bytes.j for RGB: K and tint 0; Bytes.t for CCT:
# the white slot, K, the tint), its mask every piece's bit with no cap (BleUtils.X), then `33 05 15 00`. See
# the "0xA3 colour" section below.
# Every scheme with a colour-temperature mode but kelvin_v9 also sends an RGB pick that is exactly one of the
# Kelvin tint colours (within the SKU's range) as a CCT frame instead of RGB, with the picked colour
# as the tint (rgb_as_kelvin); h61a8 has no CCT and always sends RGB. The writers make that test on every
# pick without asking whether the screen offers colour temperature (Config.Yt / bu / Zt / fu), so it runs
# on the legacy_02 SKUs with no colour-temperature control (H7310 / H7318 / H6165) too.
ColorScheme = Literal["h60a6", "h6006", "h61a8", "h6188", "h6061", "legacy_02", "kelvin_v9", "h3010"]

# The h60a6 colour-temperature frame's slot (op15_color kelvin_tint), the white type the colour page hands
# Bytes.r: the app's ColorTemperatureType (LightColorCapabilityCenter.d1 -> Config.H: Config.O by SKU, else
# Config.P by goodsType, else 1), named as the app names it. Bytes.r (Bytes.java:474-501):
#   "rgb_white"          (1, and 2 RGB_SELF)  slot ff ff ff, then the Kelvin and the tint;
#   "rgb_black"          (0)                  slot 00 00 00, then the Kelvin and the tint;
#   "rgb_self_tem_color" (3)                  over a whole posSet as rgb_black; over a partial one (Bytes.x:
#                                             a position unset) the tint in the slot, Kelvin and tint zero,
#                                             the bytes of the plain RGB frame of the tint (Bytes.h).
# Only the h60a6 scheme carries it (devices.yaml clientRow white_type; DeviceProfile.white_type).
WhiteType = Literal["rgb_black", "rgb_white", "rgb_self_tem_color"]
DEFAULT_WHITE_TYPE: Final[WhiteType] = "rgb_white"


def _unsupported_scheme(scheme: Never) -> Never:
    """Exhaustiveness guard: mypy proves every builder handles each ``ColorScheme``; at runtime (a
    string outside the Literal) refuse rather than send another family's frame."""
    raise GoveeBleNotSupported(f"unknown colour scheme {scheme!r}")


def _check_white_type(scheme: ColorScheme, white_type: WhiteType) -> None:
    """GoveeBleNotSupported for a white type other than the default on a scheme other than ``h60a6``: only
    the 0x15 colour-temperature frame has the slot (:data:`WhiteType`)."""
    if white_type not in get_args(WhiteType):
        raise GoveeBleInvalidValue(f"unknown white type {white_type!r}")
    if white_type != DEFAULT_WHITE_TYPE and scheme != "h60a6":
        raise GoveeBleNotSupported(f"{scheme} has no white slot (white type {white_type!r} is the h60a6 "
                                   "scheme's)")


def _check_range(name: str, value: int, lo: int, hi: int) -> int:
    """Return ``value`` if it is an int in [lo, hi]; else raise GoveeBleInvalidValue."""
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise GoveeBleInvalidValue(f"{name} must be an integer in {lo}..{hi}, got {value!r}")
    return value


def _clamp_pct(pct: int) -> int:
    """``pct`` clamped to 0..100; GoveeBleInvalidValue for a non-int (``bool`` included), the same
    type check as :func:`_check_range` (a float would otherwise fail inside ``bytes()``)."""
    if isinstance(pct, bool) or not isinstance(pct, int):
        raise GoveeBleInvalidValue(f"brightness must be an integer, got {pct!r}")
    return max(0, min(100, pct))


def _check_rgb(r: int, g: int, b: int) -> None:
    _check_range("red", r, 0, 255)
    _check_range("green", g, 0, 255)
    _check_range("blue", b, 0, 255)


def _check_kelvin(kelvin: int) -> int:
    return _check_range("kelvin", kelvin, 0, 0xFFFF)


KELVIN_STEP: Final = 100   # the app only ever sends whole 100 K steps (Constant colour->Kelvin table)


def normalize_kelvin(kelvin: int, lo: int | None = None, hi: int | None = None) -> int:
    """The Kelvin value the app would put on the wire for ``kelvin``.

    Order of operations, as in the app: clamp to the device's range, then land on the 100 K grid.
    The app clamps any integer to the per-SKU range and never rejects it
    (``KelvinConfig.Companion.a`` = ``calibrationKelvin``, ``Constant.checkKelvinRange``); its
    slider only produces 100 K steps, and ``Constant.getTemColorByKelvin`` accepts only values of
    the colour->Kelvin table, which is on a 100 K grid (an off-grid value is a failed lookup there,
    never sent). This library rounds an off-grid value to the nearest 100 K (halves up) instead,
    then clamps again so the rounding can never leave a range whose edges are not 100 K multiples.

    ``lo``/``hi`` omitted (a profile with no known range) means no clamp on that side; the result
    must still fit the wire's u16 Kelvin field. Raises GoveeBleInvalidValue only for a non-int
    (including ``bool``) or, once clamped, a value outside the u16 field; an out-of-range int is clamped."""
    if isinstance(kelvin, bool) or not isinstance(kelvin, int):
        raise GoveeBleInvalidValue(f"kelvin must be an integer, got {kelvin!r}")
    out = kelvin
    if lo is not None:
        out = max(out, lo)
    if hi is not None:
        out = min(out, hi)
    out = (_check_kelvin(out) + KELVIN_STEP // 2) // KELVIN_STEP * KELVIN_STEP
    if lo is not None:
        out = max(out, lo)
    if hi is not None:
        out = min(out, hi)
    return out

# Segment masks: the spec's seg_mask(num_segments, cap). Up to 16 segments the mask is two bytes
# (BleUtils.makeSelectedTwoBytes); above that ceil(n/8) bytes, LSB first (BleUtils.X). Each body that carries
# a mask names its byte cap as its `len_cap` instance, and the app's writers drop the positions past it
# (coerceAtMost). The library reads the cap off a built node: the 0x15 RGB and CCT frames 7 bytes
# (op15_color form kelvin_tint), per-segment brightness 14 (op15_brightness), the 0x0b frame 13
# (color_0b_rgb_mask, read where it is built). MAX_MASK_BYTES is the widest cap of the 0x15 bodies, so
# MAX_MASK_SEGMENTS is the most segments any single-frame body the library writes can address (an h3010 0xA3
# colour value's mask has no cap: piece_cap).
LEGACY_MASK_SEGMENTS: Final = 16
_FORM_KELVIN_TINT: Final = int(_C.Form1501.kelvin_tint)   # [slot rgb][kelvin][tint][mask]: H60A6 family
_FORM_KELVIN: Final = int(_C.Form1501.kelvin)             # [rgb][kelvin][mask]: colour version 5, H6061
COLOR_MASK_CAP: Final = int(_spec.node(_F.Op15Color, None, _FORM_KELVIN_TINT, 0).len_cap)
BRIGHTNESS_MASK_CAP: Final = int(_spec.node(_F.Op15Brightness, None, 0).len_cap)
MAX_MASK_BYTES: Final = max(COLOR_MASK_CAP, BRIGHTNESS_MASK_CAP)
MAX_MASK_SEGMENTS: Final = MAX_MASK_BYTES * 8


def mask_width(segments: int, mask_cap: int = MAX_MASK_BYTES) -> int:
    """Bytes of segment mask the app writes for a device with ``segments`` segments into a body that carries
    at most ``mask_cap`` bytes: 2 for 0..16 (makeSelectedTwoBytes), else ceil(n/8) (BleUtils.X), cut to
    ``mask_cap`` (the spec's ``seg_mask.len_mask``). Raises GoveeBleInvalidValue above
    :data:`MAX_MASK_SEGMENTS` (112)."""
    _check_range("segment count", segments, 0, MAX_MASK_SEGMENTS)
    return int(_spec.node(_C.SegMask, None, segments, mask_cap).len_mask)


def mask_positions(segments: int, mask_cap: int) -> int:
    """How many of a device's ``segments`` positions a body of ``mask_cap`` mask bytes carries: all of them,
    or the first ``mask_cap * 8`` where the app's writer cuts the mask (``seg_mask.is_truncated``)."""
    return min(segments, 8 * mask_width(segments, mask_cap))


def _check_mask(mask: int, bits: int = LEGACY_MASK_SEGMENTS) -> int:
    return _check_range("segment mask", mask, 0, (1 << bits) - 1)


def _mask_positions(mask: int, segments: int | None, mask_cap: int) -> int:
    """The posSet length a body of ``mask_cap`` mask bytes is written with for a device with ``segments``
    segments (``None`` = the legacy two-byte mask), after checking ``mask`` fits the device's full mask:
    the device's mask width rounded up to whole bytes, cut to the cap (the spec's seg_mask doc allows the
    rounded length: the same width and bytes). Any bit inside those bytes is written, as before; the app
    never sets one at or past the piece count, and Device doesn't either. Positions past the cap are
    dropped (:func:`_mask_bits`), as the app's coerceAtMost writers drop them."""
    count = LEGACY_MASK_SEGMENTS if segments is None else segments
    full = mask_width(count, MAX_MASK_BYTES)
    _check_range("segment mask", mask, 0, (1 << (8 * full)) - 1)
    return 8 * mask_width(count, mask_cap)


def _mask_bits(mask: int, positions: int) -> int:
    """``mask`` cut to its first ``positions`` bits."""
    return mask & ((1 << positions) - 1)

def all_segments_mask(segments: int) -> int:
    """Whole-device mask = the first ``segments`` bits set (BleUtil select-all-positions).
    Raises GoveeBleInvalidValue above :data:`MAX_MASK_SEGMENTS` (112)."""
    _check_range("segment count", segments, 0, MAX_MASK_SEGMENTS)
    return (1 << segments) - 1


# ── power / brightness ───────────────────────────────────────────────────────
def switch(on: bool, *, relay: bool = False) -> bytes:
    """33 01 <state>. relay=True: the outlet-0 relay encoding 0x11/0x10, goodsType 43's main switch
    (H5080 / H5083 / H5085); the multi-outlet plugs use :func:`plug_all_outlets` /
    :func:`plug_total_switch`. The spec's ``power_write`` under form_01 ``relays`` / ``light``."""
    if relay:
        return _power_frame(_FORM_RELAYS, _F.RelayPowerWrite, RELAY_ON if on else RELAY_OFF)
    return _power_frame(_FORM_LIGHT, _F.LightPowerWrite, POWER_ON if on else POWER_OFF)


def _power_frame(form: int, body_cls: Any, state: int) -> bytes:
    """``33 01 <state>``: ``power_write(form)`` with its ``body_cls`` body."""
    return _write_frame(_spec.profile(f_01=form), _C.Command.power,
                        _form_params(_F.PowerWrite, form, body_cls, state=state))


def plug_all_outlets(on: bool) -> bytes:
    """33 01 FF / F0: every outlet of a multi-outlet plug on / off, the main switch of goodsType 50
    (H5082, UiV2.onOffChange) and 90 (H5160 / H5161, UiV4.onOffChange): ``SwitchControllerV2(on, 15)``,
    whose ``q()`` writes ``0xF0 | (0x0F if on else 0)`` (h5080 SwitchControllerV2.java:53-71)."""
    return _power_frame(_FORM_RELAYS, _F.RelayPowerWrite, RELAY_ALL_ON if on else RELAY_ALL_OFF)


def plug_total_switch(on: bool) -> bytes:
    """33 02 01 / 00: H5089's main switch (goodsType 307, Ui4H5089.onOffChange:929-946):
    ``Controller4TotalSwitch(on)``, command 0x02, one 1/0 byte (Controller4TotalSwitch.q), the spec's
    ``cmd_02_write`` under form_02 ``total_switch``."""
    return _write_frame(_spec.profile(f_02=_FORM_TOTAL_SWITCH), _C.Command.cmd_02, _form_params(
        _F.Cmd02Write, _FORM_TOTAL_SWITCH, _F.TotalSwitchWrite, state=POWER_ON if on else POWER_OFF))


def brightness(pct: int) -> bytes:
    """33 04 <level 0-100> (clamped). Raises GoveeBleInvalidValue for a non-int."""
    level = _clamp_pct(pct)
    return _write_frame(_PLAIN, _C.Command.brightness, _params(_F.LevelWrite, level=level))


def brightness_raw(level: int) -> bytes:
    """33 04 <raw level 0-255>, for devices whose brightness byte is not a percent
    (ControllerBrightness(i) writes the byte verbatim; e.g. H6188's 20..254 range)."""
    raw = _check_range("brightness level", level, 0, 0xFF)
    return _write_frame(_PLAIN, _C.Command.brightness, _params(_F.LevelWrite, level=raw))


# ── colour frames (the spec's write types) ───────────────────────────────────
def _cct0d_frame(rgb: tuple[int, int, int], kelvin: int = 0,
                 tint: tuple[int, int, int] = (0, 0, 0)) -> bytes:
    """``33 05 0d [r, g, b][kelvin u2be][tint r, g, b]`` (``color_0d_write``): RGB with kelvin 0 and no
    tint (Bytes.f), CCT with the tint (Bytes.p / Bytes.A)."""
    return _mode_frame(_PLAIN, _C.SubMode.color_cct_0d, _params(
        _F.Color0dWrite, color=_spec.rgb(*rgb), kelvin=kelvin, tint=_spec.rgb(*tint)))


_WHITE: Final = (0xFF, 0xFF, 0xFF)   # ColorUtils.R(): the colour slot of Bytes.o, and Bytes.r's rgb_white


def _color02_frame(rgb: tuple[int, int, int], tint: tuple[int, int, int] | None = None) -> bytes:
    """``33 05 02 [r, g, b][white_flag][tint r, g, b]`` (``color_02_write``), the frames ``Config.fu``
    sends: with no ``tint``, ``rgb`` under flag ``rgb`` and a zero tint (``Bytes.a``); with one, flag
    ``white_tint`` and the tint after ``rgb`` (``Bytes.o``, which ``fu`` calls with ``ColorUtils.R()``, white,
    as ``rgb``)."""
    flag = _F.Color02Flag.rgb if tint is None else _F.Color02Flag.white_tint
    return _mode_frame(_PLAIN, _C.SubMode.color_legacy, _params(
        _F.Color02Write, color=_spec.rgb(*rgb), white_flag=flag, tint=_spec.rgb(*(tint or (0, 0, 0)))))


# The 0x0b frame's posSet: the two-byte mask of up to 16 segments the library writes.
_PROFILE_0B: Final = _spec.profile(f_0b=_FORM_0B_RGB_MASK, num_segments=LEGACY_MASK_SEGMENTS)


def _rgbic0b_frame(rgb: tuple[int, int, int], mask: int) -> bytes:
    """``33 05 0b [r, g, b][seg_mask]`` (``color_0b_write`` form ``rgb_mask``: the two-byte mask of up to
    16 segments)."""
    bits = _check_mask(mask, LEGACY_MASK_SEGMENTS)

    def params(mw: Any) -> Any:
        c = _spec.node(_F.Color0bWrite, mw, _FORM_0B_RGB_MASK, LEGACY_MASK_SEGMENTS)
        c.body = _spec.node(_F.Color0bRgbMask, c, LEGACY_MASK_SEGMENTS, color=_spec.rgb(*rgb))
        c.body.mask = _spec.seg_mask(c.body, LEGACY_MASK_SEGMENTS, int(c.body.len_cap), bits)
        return c
    return _mode_frame(_PROFILE_0B, _C.SubMode.color_rgbic_0b, params)


# op15_color forms (enum form_15_01): the colour form the app's writer picks. kelvin_tint carries RGB as
# kelvin 0 and tint 0, byte-identical to SubModeColorV1 opType 12 / Bytes.h [rgb][00 x5][mask].
#   kelvin_tint [slot r, g, b][kelvin][tint][mask]   Bytes.h / Bytes.r (H60A6 family RGB and CCT)
#   kelvin      [r, g, b][kelvin][mask]              Bytes.l / Bytes.u (colour version 5, H6061)
def _op15_color_frame(form: int, rgb: tuple[int, int, int], mask: int, positions: int, kelvin: int = 0,
                      tint: tuple[int, int, int] = (0, 0, 0)) -> bytes:
    """``33 05 15 01`` with the spec's ``op15_color(form, num_segments)``, its mask over ``positions``
    (a whole number of bytes)."""
    prof = _spec.profile(f_15_01=form, num_segments=positions)

    def data(c15: Any) -> Any:
        w = _spec.node(_F.Op15Color, c15, form, positions, color=_spec.rgb(*rgb), kelvin=kelvin)
        if form == _FORM_KELVIN_TINT:
            w.tint = _spec.rgb(*tint)
        w.mask = _spec.seg_mask(w, positions, int(w.len_cap), _mask_bits(mask, positions))
        return w
    return _op15_frame(prof, _C.Op15.set_color, data)

# ── Glide Hexa (H6061) whole-device writes ───────────────────────────────────
# Source (Govee Home 7.7.02, split pact_h6061 + base.apk): the colour screen's all tab (DelegateColorV2.F0
# for a colour, G0 for the slider) writes every panel of the user's stored layout, a posSet of
# CubeColorModeV2.realMyColorPiece = max(layout size, 1) (ColorModeUtil.e), through
# ColorModeControllerConfig.c -> Config.J[5] = Config.Zt: one 15 01 frame, Bytes.l for an RGB pick and
# Bytes.u for a Kelvin tint (the spec's op15_color, form kelvin). The device never reports the layout; the
# goodsType's panel count stands in for it (devices.yaml h6061 wire counts.num_segments, the profile's
# segments; Device passes it as ``pieces``): a full layout, 20 on the H6061, the default: the layout
# editor's cap for its hexagons (Constants.o(0), BasicShapeBusiness.x / x0). Support.getBulbStringMaxNum(51)
# = 23 is only the read-back default with no layout stored. The 15 04 group frames (Bytes.m) are the
# colour-stripe path (ColorViewModel.O -> ColorModeControllerConfig.k), which the library doesn't send.
H6061_MAX_PANELS: Final = 20
H6061_KELVIN_RANGE: Final = (2000, 9000)   # KelvinConfig: no entry for 51 -> version 0

# Segment count a whole-device builder assumes when the caller passes none (``Device`` always passes
# the profile's count): ``h60a6``, ``h61a8`` and ``h3010`` take their namesake SKU's count (H60A6 13 segments,
# H61A8 15, the 0x0b frames' strip, H3010 the 15 pieces before its first IC read), and ``h6061`` the device's
# piece count (:func:`_h6061_panels`). The 0x0d schemes carry no mask.
_DEFAULT_SEGMENTS: Final[dict[ColorScheme, int]] = {"h60a6": 13, "h61a8": 15, "h3010": 15}


def _segments_or_default(scheme: ColorScheme, segments: int | None) -> int:
    """``segments``, or the scheme's default count when it is None (an explicit 0 stays 0)."""
    return segments if segments is not None else _DEFAULT_SEGMENTS.get(scheme, 0)


def _h6061_panels(panels: int | None, pieces: int | None, *, expand_one: bool) -> tuple[int, int]:
    """``(panels written, mask positions)`` for a layout of ``panels`` panels (None: the whole device) on a
    device of ``pieces`` (None: the H6061's 20).

    More than ``pieces`` is refused (the profile's count is a full layout). The panels written are the app's
    posSet, ``max(L, 1)`` (``CubeColorModeV2.realMyColorPiece``), so one panel is mask ``01 00``.
    ``expand_one`` writes a one-panel layout as ``pieces`` panels instead, the colour-stripe path's
    single-colour expansion (``ColorPieceConfig.Config.s0``): the CCT frames keep it as admitted at 1.6.1
    (decision 9 approves the RGB frame only), a known divergence from ``Bytes.u`` that only an explicit
    one-panel count reaches. The mask spans ``pieces`` rounded up to whole bytes (20 pieces: 24 positions);
    past the panels written it is zero, as the app's shorter ``BleUtils.X`` / two-byte mask is followed by
    the zero fill."""
    j = H6061_MAX_PANELS if pieces is None else pieces
    n = j if panels is None else panels
    _check_range("panel count", n, 1, j)
    return (j if expand_one and n == 1 else n), 8 * ceil_div(j, 8)


def _h6061_color(rgb: tuple[int, int, int], kelvin: int, panels: int | None, pieces: int | None, *,
                 expand_one: bool = False) -> bytes:
    """Whole-device ``33 05 15 01 <rgb> <kelvin u2be> <mask>`` (``Config.Zt``), the first ``panels`` mask
    bits set, LSB first (20 panels: ``ff ff 0f``): ``Bytes.l`` (an RGB pick, Kelvin 0) or ``Bytes.u`` (CCT,
    the tint in the colour slot, not ``ff ff ff``), over the posSet of :func:`_h6061_panels`."""
    n, positions = _h6061_panels(panels, pieces, expand_one=expand_one)
    return _op15_color_frame(_FORM_KELVIN, rgb, all_segments_mask(n), positions, kelvin)


def _h6061_color_temp(kelvin: int, panels: int | None, tint: tuple[int, int, int] | None = None,
                      pieces: int | None = None) -> bytes:
    """Whole-device CCT: ``Config.Zt`` -> ``Bytes.u(kelvin, posSet, tint)`` (:func:`_h6061_color`), a
    one-panel layout written as every panel (:func:`_h6061_panels` ``expand_one``). ``tint`` defaults to
    the app's tint for ``kelvin`` (the slider path); an RGB pick that is itself a tint colour passes that
    exact colour (``Zt`` writes the picked colour, whichever of the Kelvin step's two colours it is)."""
    return _h6061_color(tint if tint is not None else tint_for_kelvin(kelvin), kelvin, panels, pieces,
                        expand_one=True)


_FORM_KELVIN_V9: Final = int(_C.Form1504.kelvin_v9)
_KELVIN_V9_NO_RGB: Final = ("kelvin_v9 has no RGB frame (colour instruction 9 writes a Kelvin only: "
                            "Config.J[9] = hu maps a pick to its Kelvin, Bytes.D)")
_PROFILE_KELVIN_V9: Final = _spec.profile(f_15_04=_FORM_KELVIN_V9)


def _kelvin_v9_frame(kelvin: int) -> bytes:
    """``33 05 15 04 <kelvin u2be>``: colour instruction 9 (``Config.N[9]`` = ``Config.JM`` -> ``Bytes.D``),
    the spec's ``op15_group`` under form_15_04 ``kelvin_v9``."""
    return _op15_frame(_PROFILE_KELVIN_V9, _C.Op15.set_color_group, _form_params(
        _F.Op15Group, _FORM_KELVIN_V9, _F.Op15KelvinV9, kelvin=kelvin))
# ── 0xA3 colour (H3010-H3013, goodsType 411) ─────────────────────────────────
# ColorModeControllerConfig.j (:689-727): MultiController.c(Config.gu(colour, posSet, white type), 0x6e,
# ControllerMode.e([0x15])), the unit LightColorCapabilityCenter.b2 picks for both colour writes
# (ColorViewModel.p0 :1412-1413, U :883-884). The value is the spec's govee_upload color_multi_value over the
# whole-device posSet (ColorPieceConfig.c = max(15, IC); the device passes its live count), packetised as
# every 0xA3 upload is (MultipleControllerCommV1.makeSendBytesV1 = :func:`scene_upload_a3`), and the unit ends
# with the op15 set_mode frame. The app's unit is done on an A3 6E ack whose byte 3 is 0
# (AbsControllerNoEvent4Multi.onResult at MultiController.e); the transport's ``send_many`` sends the run and
# the write as that unit.
def _color_multi_value(pieces: int, **fields: Any) -> Any:
    """The spec's ``color_multi_value`` over ``pieces``, its ``fields`` set (the mask is the caller's)."""
    return _spec.node(_spec.Upload.ColorMultiValue, None, pieces, **fields)


# The value bytes ahead of its mask (the spec's len_head), and so the most pieces its uncapped mask can span
# within one 0xA3 upload (SCENE_A3_MAX_VALUE).
_COLOR_MULTI_HEAD_LEN: Final = int(_color_multi_value(0).len_head)


def piece_cap(scheme: ColorScheme) -> int:
    """The most pieces a whole-device write of ``scheme`` carries, and so the most a live count or
    ``Device.set_segment_count`` sizes it by: on ``h3010``, whose mask has no cap (``BleUtils.X``), every
    piece the value's mask can span in one 0xA3 upload (:data:`SCENE_A3_MAX_VALUE` after the value's head);
    else :data:`MAX_MASK_SEGMENTS`, the most any 0x15 body carries, so every larger count writes the same
    bytes. A row's own IC rule bounds its live count first (devices.yaml ``ic_pieces``)."""
    if scheme == "h3010":
        return 8 * (SCENE_A3_MAX_VALUE - _COLOR_MULTI_HEAD_LEN)
    return MAX_MASK_SEGMENTS


def color_mode_switch() -> bytes:
    """``33 05 15 00`` (op15 ``set_mode``, ``ControllerMode.e([0x15])``): the colour-mode switch that ends a
    goodsType 411 colour unit (:func:`_color_multi_frames`)."""
    return _op15_frame(_PLAIN, _C.Op15.set_mode, lambda _c15: None)


def _color_multi_frames(color: tuple[int, int, int], pieces: int | None, kelvin: int = 0,
                        tint: tuple[int, int, int] = (0, 0, 0)) -> list[bytes]:
    """The h3010 colour unit over every one of ``pieces`` (None: the scheme's default,
    :data:`_DEFAULT_SEGMENTS`): the 0xA3 packets of the spec's ``color_multi_value`` ``[01] <color> <kelvin
    u2be> <tint> <mask>`` (``Config.gu``: ``Bytes.j`` / ``Bytes.t``), the mask ``BleUtils.X`` of the
    whole-device posSet with no cap, then ``33 05 15 00``. GoveeBleInvalidValue for a count outside
    1..:func:`piece_cap`."""
    n = _segments_or_default("h3010", pieces)
    _check_range("piece count", n, 1, piece_cap("h3010"))
    value = _color_multi_value(n, op=_C.Op15.set_color, color=_spec.rgb(*color), kelvin=kelvin,
                               tint=_spec.rgb(*tint))
    value.mask = _spec.seg_mask(value, n, int(value.len_cap), (1 << n) - 1)
    body = _spec.serialize(value, int(value.len_value))
    return [*scene_upload_a3(body, COMM_COLOR_MULTI), color_mode_switch()]


def _color_multi_rgb(r: int, g: int, b: int, pieces: int | None,
                     kelvin_range: tuple[int, int] | None) -> list[bytes]:
    """``Config.gu`` for an RGB pick: ``Bytes.t`` when the pick is a Kelvin tint colour in the range
    (``KelvinConfig.i``; the white slot ``ff ff ff``, white type 1 = Config.P[411], the Kelvin and the
    picked colour), else ``Bytes.j``."""
    kelvin = rgb_as_kelvin(r, g, b, "h3010", kelvin_range)
    if kelvin is not None:
        return _color_multi_frames(_WHITE, pieces, kelvin, (r, g, b))
    return _color_multi_frames((r, g, b), pieces)


# Schemes whose colour screen turns an RGB pick that is exactly a Kelvin tint colour into a CCT write
# (every scheme with CCT; h61a8 has none). Config.Yt (0x15 family, h60a6) / Config.bu (0x0d family,
# h6006 and h6188) / Config.Zt (h6061) / Config.fu (legacy_02) / Config.gu (h3010), all keyed on
# KelvinConfig.i.
_RGB_PICK_AS_KELVIN_SCHEMES: Final[frozenset[ColorScheme]] = frozenset(
    {"h60a6", "h6006", "h6188", "h6061", "legacy_02", "h3010"})


def rgb_as_kelvin(r: int, g: int, b: int, scheme: ColorScheme,
                  kelvin_range: tuple[int, int] | None = None) -> int | None:
    """The Kelvin the app sends for an RGB pick, or None when it sends the plain RGB frame.

    The colour screens of every scheme with a colour-temperature mode test the picked colour with
    ``KelvinConfig.i(colour)`` (full ARGB equality with one of the 178 Kelvin tint colours,
    :func:`wire.cct.kelvin_for_tint`, AND that Kelvin inside the SKU's inclusive ``[lo, hi]`` range)
    and write ``KelvinConfig.c(colour)`` as a CCT frame instead of RGB: ``Config.Yt`` (0x15 family,
    ``h60a6``), ``Config.bu`` (0x0d family, ``h6006`` and ``h6188``), ``Config.Zt`` (``h6061``) and
    ``Config.fu`` (``legacy_02``: the picked colour as the tint of its white form). None of them asks whether
    the screen offers colour temperature, so the test runs on a SKU without one too (the legacy_02 H7310 /
    H7318 / H6165, whose state keeps the colour: ``devices._fold.apply_rgb``).
    ``kelvin_range`` is the SKU's range (the profile's min/max Kelvin); ``h6061`` defaults to its
    own [2000, 9000], any other scheme without a range cannot tell and returns None, as does
    ``h61a8`` (no colour temperature)."""
    if scheme not in _RGB_PICK_AS_KELVIN_SCHEMES:
        return None
    if kelvin_range is None and scheme == "h6061":
        kelvin_range = H6061_KELVIN_RANGE
    if kelvin_range is None:
        return None
    kelvin = kelvin_for_tint((r, g, b))
    lo, hi = kelvin_range
    return kelvin if kelvin is not None and lo <= kelvin <= hi else None


def _rgb_pick_color_temp(r: int, g: int, b: int, kelvin: int, scheme: ColorScheme,
                         segments: int | None, mask: int | None = None, pieces: int | None = None,
                         white_type: WhiteType = DEFAULT_WHITE_TYPE) -> bytes:
    """The CCT frame the app writes for an RGB pick that :func:`rgb_as_kelvin` turned into
    ``kelvin``; the PICKED colour is the tint, not the table's first colour for ``kelvin`` (a Kelvin
    step has two colours). ``mask`` None is the whole device:

    * ``h60a6`` (``Config.Yt`` -> ``Bytes.r``): ``15 01 <slot> <K> <picked RGB> <mask>``, one frame
      with the mask the RGB frame would have used, all ``segments`` when ``mask`` is None (the slider
      path, :func:`color_temp`, is the same frame over the all-segments mask with the Kelvin's first
      tint); the slot is the device's ``white_type`` (:data:`WhiteType`: ``ff ff ff`` by default);
    * ``h6006`` / ``h6188`` (``Config.bu`` -> ``Bytes.p``): ``0d <picked RGB> <K> <first tint of K>``,
      whole device (no mask), the same frame for both schemes (the white-slot form of the h6188
      slider is not used here);
    * ``h6061`` (``Config.Zt`` -> ``Bytes.u``): ``15 01 <picked RGB> <K> <mask>`` over the ``segments``
      panels of a device of ``pieces`` (whole device only, :func:`_h6061_panels`);
    * ``legacy_02`` (``Config.fu`` -> ``Bytes.o``): ``02 ff ff ff 01 <picked RGB>``, whole device, with
      no Kelvin on the wire."""
    match scheme:
        case "h60a6":
            if mask is None:
                segments = _segments_or_default(scheme, segments)
                mask = all_segments_mask(segments)
            return segment_color_temp(mask, kelvin, scheme, segments, tint=(r, g, b), white_type=white_type)
        case "h6006" | "h6188":
            return _cct0d_frame((r, g, b), kelvin, tint_for_kelvin(kelvin))
        case "h6061":
            return _h6061_color_temp(kelvin, segments, (r, g, b), pieces)
        case "legacy_02":
            return _color02_frame(_WHITE, (r, g, b))
        case "h61a8":   # rgb_as_kelvin never picks a Kelvin here
            raise GoveeBleNotSupported("h61a8 has no colour-temperature capability")
        case "kelvin_v9":   # nor here: it has no RGB pick
            raise GoveeBleNotSupported(_KELVIN_V9_NO_RGB)
        case "h3010":
            raise GoveeBleNotSupported("h3010 sends a pick as a multi-packet unit (color_rgb_frames)")
        case _:
            _unsupported_scheme(scheme)


# ── colour ───────────────────────────────────────────────────────────────────
def color_rgb_frames(r: int, g: int, b: int, scheme: ColorScheme, segments: int | None = None,
                     kelvin_range: tuple[int, int] | None = None, *,
                     pieces: int | None = None, white_type: WhiteType = DEFAULT_WHITE_TYPE) -> list[bytes]:
    """Whole-device RGB pick as the frames the app sends: one on every scheme, :func:`color_rgb`, except
    that an RGB that is a Kelvin tint colour within ``kelvin_range`` (the SKU's range) is sent as one CCT
    frame instead, exactly as the app does, on every scheme with a colour-temperature mode (see
    :func:`rgb_as_kelvin` and :func:`_rgb_pick_color_temp`; on ``h60a6`` with the device's
    ``white_type`` slot); ``kelvin_range`` None sends plain RGB
    (``h6061`` defaults to its own range). ``segments`` None is the scheme's default count
    (:data:`_DEFAULT_SEGMENTS`; on ``h6061`` every panel). ``pieces`` is an ``h6061`` device's panel count
    (the profile's ``segments``; None: the H6061's 20), which bounds ``segments`` and sizes the mask
    (:func:`_h6061_panels`); other schemes ignore it. ``h3010`` sends one 0xA3 unit of several frames over
    ``segments`` pieces (:func:`_color_multi_frames`)."""
    _check_rgb(r, g, b)
    _check_white_type(scheme, white_type)
    if scheme == "h3010":   # Config.gu: one 0xA3 unit (:func:`_color_multi_rgb`)
        return _color_multi_rgb(r, g, b, segments, kelvin_range)
    kelvin = rgb_as_kelvin(r, g, b, scheme, kelvin_range)
    if kelvin is not None:
        return [_rgb_pick_color_temp(r, g, b, kelvin, scheme, segments, pieces=pieces, white_type=white_type)]
    return [color_rgb(r, g, b, scheme, segments, pieces=pieces)]


def color_rgb(r: int, g: int, b: int, scheme: ColorScheme, segments: int | None = None, *,
              pieces: int | None = None) -> bytes:
    """Solid RGB, per-family SubModeColor.getWriteBytes (whole-device = all-segments mask;
    ``segments`` None is the scheme's default count, :data:`_DEFAULT_SEGMENTS`). ``h6061`` =
    ``15 01 R G B 00 00 <mask>`` over ``segments`` panels of a device of ``pieces`` (``Config.Zt`` ->
    ``Bytes.l``, :func:`_h6061_color`); other schemes ignore ``pieces``. The plain frame only: an RGB pick
    that is a Kelvin tint colour goes out as CCT, which :func:`color_rgb_frames` picks."""
    _check_rgb(r, g, b)
    match scheme:
        case "h60a6":
            n = _segments_or_default(scheme, segments)
            mask = all_segments_mask(n)
            positions = _mask_positions(mask, n, COLOR_MASK_CAP)
            return _op15_color_frame(_FORM_KELVIN_TINT, (r, g, b), mask, positions)
        case "h6006" | "h6188":
            return _cct0d_frame((r, g, b))
        case "legacy_02":   # Config.fu -> Bytes.a
            return _color02_frame((r, g, b))
        case "h61a8":
            return _rgbic0b_frame((r, g, b), all_segments_mask(_segments_or_default(scheme, segments)))
        case "h6061":
            return _h6061_color((r, g, b), 0, segments, pieces)
        case "kelvin_v9":
            raise GoveeBleNotSupported(_KELVIN_V9_NO_RGB)
        case "h3010":
            raise GoveeBleNotSupported("h3010 colour is a multi-packet unit (color_rgb_frames)")
        case _:
            _unsupported_scheme(scheme)


def color_temp_frames(kelvin: int, scheme: ColorScheme, segments: int | None = None, *,
                      pieces: int | None = None, white_type: WhiteType = DEFAULT_WHITE_TYPE) -> list[bytes]:
    """Whole-device colour temperature as the frames the app sends: ``[color_temp(...)]`` on every scheme
    but ``h3010``, whose ``Config.gu`` sends one 0xA3 unit (``Bytes.t``: the white slot ``ff ff ff``, the
    Kelvin, the app's tint for it, the mask over ``segments`` pieces; :func:`_color_multi_frames`)."""
    if scheme == "h3010":
        _check_kelvin(kelvin)
        _check_white_type(scheme, white_type)
        return _color_multi_frames(_WHITE, segments, kelvin, tint_for_kelvin(kelvin))
    return [color_temp(kelvin, scheme, segments, pieces=pieces, white_type=white_type)]


def color_temp(kelvin: int, scheme: ColorScheme, segments: int | None = None, *,
               pieces: int | None = None, white_type: WhiteType = DEFAULT_WHITE_TYPE) -> bytes:
    """Whole-device colour temperature, with the app's Kelvin->RGB tint but on ``kelvin_v9``
    (``wire.cct.tint_for_kelvin``): ``h6006`` = ``0d <tint> <K> <tint>`` (the colour tab's
    ``Config.bu -> Bytes.p``), ``h6188`` = ``0d ff ff ff <K> <tint>`` (the illumination tab's
    ``Config.GM -> Bytes.A``), ``legacy_02`` = ``02 ff ff ff 01 <tint>`` (``Config.fu -> Bytes.o``, no
    Kelvin on the wire), ``h60a6`` = ``15 01 <slot> <K> <tint> <mask>`` (this is just
    segment_color_temp with the all-segments mask; the slot is the ``white_type``'s, ``ff ff ff`` by
    default), ``h6061`` = ``15 01 <tint> <K> <mask>`` over
    ``segments`` panels of a device of ``pieces`` (as in :func:`color_rgb`; ``segments`` None is
    the scheme's default count, :data:`_DEFAULT_SEGMENTS`), ``kelvin_v9`` = ``15 04 <K>`` (``Config.JM`` ->
    ``Bytes.D``, no tint and no mask). ``h61a8`` has no colour temperature: GoveeBleNotSupported. The builder
    sends ``kelvin`` as given; the ``Device`` layer applies
    :func:`normalize_kelvin` first."""
    if scheme == "h61a8":
        raise GoveeBleNotSupported("h61a8 has no colour-temperature capability")
    _check_kelvin(kelvin)
    _check_white_type(scheme, white_type)
    match scheme:
        case "h60a6":
            n = _segments_or_default(scheme, segments)
            return segment_color_temp(all_segments_mask(n), kelvin, scheme, n, white_type=white_type)
        case "h6006":
            # Config.bu -> Bytes.p(tint, K): [0d, tint r g b, kelvin u2be, tint r g b] - the tint
            # sits in BOTH colour slots (H6008/H6006/H6052/H6188; also H6004 via the same builder).
            tint = tint_for_kelvin(kelvin)
            return _cct0d_frame(tint, kelvin, tint)
        case "h6188":
            # Config.GM -> Bytes.A(kelvin, RGB_WHITE): [0d, ff ff ff, kelvin u2be, tint r g b] (the
            # illumination tab via KelvinConfig.b, H8015; group control and automations elsewhere).
            return _cct0d_frame((0xFF, 0xFF, 0xFF), kelvin, tint_for_kelvin(kelvin))
        case "legacy_02":
            # ColorViewModel.U sends Constant.getTemColorByKelvin(K)[2] through ColorModeControllerConfig.c
            # to Config.fu, which writes Bytes.o(white, tint): the Device clamps the Kelvin into the SKU's
            # range first, so the tint is always one KelvinConfig.i finds in it.
            return _color02_frame(_WHITE, tint_for_kelvin(kelvin))
        case "h6061":
            return _h6061_color_temp(kelvin, segments, pieces=pieces)
        case "kelvin_v9":
            # Config.N[9] = Config.JM -> Bytes.D (the illumination tab's builder; the colour page's
            # Config.J[9] = hu writes the same): the Kelvin alone, no tint.
            return _kelvin_v9_frame(kelvin)
        case "h3010":
            raise GoveeBleNotSupported("h3010 colour temperature is a multi-packet unit (color_temp_frames)")
        case _:
            _unsupported_scheme(scheme)


# The 0x0d family (h6006 / h6188) has no per-segment frame in the app: its colour writer, colour
# instruction version 2 = Config.K[2] (Config.at in the current decompile, cited as Config.bu
# above), takes the position set as an unused argument, and its builders Bytes.f / Bytes.p /
# Bytes.A carry no mask. The segment builders below refuse it, and legacy_02 too: Config.fu ignores the
# position set, and Bytes.a / Bytes.o carry no mask.
def segment_rgb(mask: int, r: int, g: int, b: int, scheme: ColorScheme,
                segments: int | None = None,
                kelvin_range: tuple[int, int] | None = None, *,
                white_type: WhiteType = DEFAULT_WHITE_TYPE) -> bytes:
    """Per-segment RGB pick (mask selects segments); same layout as color_rgb. ``segments`` is the
    device's segment count, which sets the 0x15 mask width (see :func:`mask_width`); omitted,
    the mask is the legacy two bytes. On ``h60a6`` a colour that is a Kelvin tint colour within
    ``kelvin_range`` (the SKU's range) is sent as the CCT frame ``15 01 <slot> <K> <picked RGB>
    <mask>`` instead (``Config.Yt``, same mask; the ``white_type``'s slot, :func:`segment_color_temp`),
    see :func:`rgb_as_kelvin`; ``kelvin_range`` None
    sends plain RGB. GoveeBleNotSupported on the whole-device-only schemes (0x0d, ``h6061``)."""
    _check_rgb(r, g, b)
    _check_white_type(scheme, white_type)
    match scheme:
        case "h60a6":
            kelvin = rgb_as_kelvin(r, g, b, scheme, kelvin_range)
            if kelvin is not None:
                return _rgb_pick_color_temp(r, g, b, kelvin, scheme, segments, mask, white_type=white_type)
            return _op15_color_frame(_FORM_KELVIN_TINT, (r, g, b), mask,
                                     _mask_positions(mask, segments, COLOR_MASK_CAP))
        case "h61a8":
            return _rgbic0b_frame((r, g, b), mask)
        case "h6006" | "h6188" | "legacy_02":
            raise GoveeBleNotSupported(f"{scheme} colour is whole-device only (no segment mask)")
        case "h6061":
            raise GoveeBleNotSupported("h6061 is whole-device only (per-panel colour is not modelled)")
        case "kelvin_v9":
            raise GoveeBleNotSupported(_KELVIN_V9_NO_RGB)
        case "h3010":
            raise GoveeBleNotSupported("h3010 is whole-device only (its Part tab is not modelled)")
        case _:
            _unsupported_scheme(scheme)


_BLACK: Final = (0x00, 0x00, 0x00)   # ColorUtils.F()


def _white_slot(white_type: WhiteType, mask: int, segments: int | None, kelvin: int,
                tint: tuple[int, int, int]) -> tuple[tuple[int, int, int], int, tuple[int, int, int]]:
    """``(slot, kelvin, tint)`` of an h60a6 colour-temperature frame over ``mask`` on a device of
    ``segments`` segments, as ``Bytes.r`` fills them for the device's white type (:data:`WhiteType`;
    Bytes.java:474-501). ``rgb_self_tem_color`` tests the posSet: whole when the mask sets all ``segments``
    positions (``Bytes.x`` false), so it needs the count (GoveeBleInvalidValue without it)."""
    match white_type:
        case "rgb_white":
            return _WHITE, kelvin, tint
        case "rgb_black":
            return _BLACK, kelvin, tint
        case "rgb_self_tem_color":
            if segments is None:
                raise GoveeBleInvalidValue("white type 'rgb_self_tem_color' needs the device's segment count")
            whole = all_segments_mask(segments)
            return (_BLACK, kelvin, tint) if mask & whole == whole else (tint, 0, _BLACK)
        case _:
            _unknown_white_type(white_type)


def _unknown_white_type(white_type: Never) -> Never:
    """Exhaustiveness guard for :data:`WhiteType`, as :func:`_unsupported_scheme` is for the schemes."""
    raise GoveeBleInvalidValue(f"unknown white type {white_type!r}")


def segment_color_temp(mask: int, kelvin: int, scheme: ColorScheme,
                       segments: int | None = None,
                       tint: tuple[int, int, int] | None = None, *,
                       white_type: WhiteType = DEFAULT_WHITE_TYPE) -> bytes:
    """Colour temperature on selected segments (mask), ``15 01 <slot> <K u2be> <tint> <mask>``
    (``Bytes.r``; the tint is ``Constant.getTemColorByKelvin``). The slot is the device's ``white_type``'s
    (:data:`WhiteType`, :func:`_white_slot`): ``ff ff ff`` by default (Bytes.r type 1), ``00 00 00`` for
    ``rgb_black``, and for ``rgb_self_tem_color`` ``00 00 00`` over every segment or, over some, the tint
    with Kelvin and tint zero (which needs ``segments``). ``segments`` is the
    device's segment count, which sets the mask width (see :func:`mask_width`); omitted, the
    mask is the legacy two bytes. Only the 0x15 family
    embeds a segment mask in the CCT frame — the 0x0d frame has no mask (whole-device only) and
    h61a8 has no CCT at all — so masked CCT is 0x15-only (GoveeBleNotSupported elsewhere). ``tint``
    defaults to the app's tint for ``kelvin`` (the slider path); an RGB pick that is itself a tint
    colour passes that exact colour (``Config.Yt`` writes the picked colour, whichever of the Kelvin
    step's two colours it is)."""
    _check_white_type(scheme, white_type)
    match scheme:
        case "h60a6":
            _check_kelvin(kelvin)
            positions = _mask_positions(mask, segments, COLOR_MASK_CAP)
            slot, sent, shade = _white_slot(white_type, mask, segments, kelvin,
                                            tint if tint is not None else tint_for_kelvin(kelvin))
            return _op15_color_frame(_FORM_KELVIN_TINT, slot, mask, positions, sent, shade)
        case "h61a8":
            raise GoveeBleNotSupported("h61a8 has no colour-temperature capability")
        case "h6006" | "h6188" | "legacy_02":
            raise GoveeBleNotSupported(f"{scheme} colour-temperature is whole-device only (no segment mask)")
        case "h6061":
            raise GoveeBleNotSupported(
                "h6061 is whole-device only (per-panel colour temperature is not modelled)")
        case "kelvin_v9":
            raise GoveeBleNotSupported("kelvin_v9 colour temperature is whole-device only (no segment mask)")
        case "h3010":
            raise GoveeBleNotSupported("h3010 is whole-device only (its Part tab is not modelled)")
        case _:
            _unsupported_scheme(scheme)


def segment_brightness(mask: int, pct: int, scheme: ColorScheme,
                       segments: int | None = None) -> bytes:
    """Per-segment brightness ``15 02 <pct> <mask>`` (``Bytes.G``; ``pct`` clamped to 1..100, the app's
    segment slider range, ``ViewColorMode.t0:232-235``); only the 0x15 family has it
    (GoveeBleNotSupported elsewhere)."""
    match scheme:
        case "h60a6":
            level = max(1, _clamp_pct(pct))
            positions = _mask_positions(mask, segments, BRIGHTNESS_MASK_CAP)

            def data(c15: Any) -> Any:
                w = _spec.node(_F.Op15Brightness, c15, positions, pct=level)
                w.mask = _spec.seg_mask(w, positions, int(w.len_cap), _mask_bits(mask, positions))
                return w
            return _op15_frame(_spec.profile(num_segments=positions), _C.Op15.set_brightness, data)
        case "h61a8":
            raise GoveeBleNotSupported("h61a8 has no per-segment brightness")
        case "h6006" | "h6188":
            raise GoveeBleNotSupported(f"{scheme} has no per-segment brightness (0x0d is whole-device only)")
        case "legacy_02":
            raise GoveeBleNotSupported("legacy_02 has no per-segment brightness (0x02 is whole-device only)")
        case "h6061":
            raise GoveeBleNotSupported("h6061 is whole-device only (per-panel brightness is not modelled)")
        case "kelvin_v9":
            raise GoveeBleNotSupported("kelvin_v9 has no per-segment brightness (no segment mask)")
        case "h3010":
            raise GoveeBleNotSupported("h3010 has no per-segment brightness (its Part tab is not modelled)")
        case _:
            _unsupported_scheme(scheme)



# ── zones / bars ─────────────────────────────────────────────────────────────
def zone_power(zone: int, on: bool) -> bytes:
    """33 30 <zoneIndex> <state> (H60A6 per-zone): ``cmd_30_write`` under form_30 ``zone_power``."""
    _check_range("zone", zone, 0, 0xFF)
    return _write_frame(_spec.profile(f_30=_FORM_ZONE_POWER), _C.Command.cmd_30, _form_params(
        _F.Cmd30Write, _FORM_ZONE_POWER, _F.ZonePowerWrite, zone=zone, state=POWER_ON if on else POWER_OFF))


def light_switch(on: bool) -> bytes:
    """33 30 <1 / 0>: the H6057 header's light switch, the light alone (``H6057DetailVM.h5`` ->
    ``ControllerSingle.c(BleProtocol.command_light_switch, ...)``): ``cmd_30_write`` under form_30
    ``light_switch``, a one-byte body, not :func:`zone_power`'s ``<zone> <state>``."""
    return _write_frame(_spec.profile(f_30=_FORM_LIGHT_SWITCH), _C.Command.cmd_30, _form_params(
        _F.Cmd30Write, _FORM_LIGHT_SWITCH, _F.LightSwitchWrite, state=POWER_ON if on else POWER_OFF))


def bar_switch(left: bool, right: bool) -> bytes:
    """33 36 <left> <right> (H6047 both bars in one frame): ``cmd_36_write`` under form_36 ``bar_pair``."""
    return _write_frame(_spec.profile(f_36=_FORM_BAR_PAIR), _C.Command.cmd_36, _form_params(
        _F.Cmd36Write, _FORM_BAR_PAIR, _F.BarPairWrite,
        left=POWER_ON if left else POWER_OFF, right=POWER_ON if right else POWER_OFF))


# ── scenes ───────────────────────────────────────────────────────────────────
def scene_activate(code: int) -> bytes:
    """33 05 04 <code u2le> (``scene_select``; the music code after it is the zero fill)."""
    _check_range("scene code", code, 0, 0xFFFF)
    return _mode_frame(_PLAIN, _C.SubMode.scene, _params(_F.SceneSelect, effect=code, music_code=0))


# The most frames one 0xA3 upload has (its packet count is one byte, and seq 0xFF marks the END), and so the
# most value bytes it carries: every frame's chunk but the START's head.
SCENE_A3_MAX_PACKETS: Final = 0xFF
SCENE_A3_MAX_VALUE: Final = SCENE_A3_MAX_PACKETS * SCENE_A3_PIECE_LEN - SCENE_A3_HEADER_LEN


def scene_upload_a3(value: bytes, comm_byte: int) -> list[bytes]:
    """0xA3 scene/DIY upload, ``MultipleControllerCommV1.makeSendBytesV2`` with a one-byte
    ``commBytes`` (``makeSendBytesV1``): the stream ``[0x01 marker, packet_count, comm_byte, value…]``
    cut into 17-byte pieces, one per frame, seq 0, 1, …, the last 0xFF. START (seq 0) and END (0xFF)
    are always two frames: a value of 14 bytes or fewer rides whole in START, followed by an empty END
    (packet_count 2); a longer value's final chunk rides in END. ``packet_count`` counts every frame.
    Each frame is the spec's ``a3_packet`` (START: its ``a3_start`` head, marker v1)."""
    # makeSendBytesV2 always appends the separate END bArr2 and starts packetCount at 2, so even a
    # value that fits in START is two frames. That short-value START + empty END form is
    # source-modeled; not live-verified until checked on the H6006/H6008 bench.
    pieces = max(2, ceil_div(SCENE_A3_HEADER_LEN + len(value), SCENE_A3_PIECE_LEN))
    if pieces > SCENE_A3_MAX_PACKETS:
        raise GoveeBleInvalidValue(f"scene payload too large for 0xA3 upload ({pieces} packets)")
    inline = SCENE_A3_PIECE_LEN - SCENE_A3_HEADER_LEN   # value bytes in START

    def chunk(i: int) -> bytes:
        def body(f: Any) -> Any:
            p = _spec.node(_F.A3Packet, f, index=0xFF if i == pieces - 1 else i)
            if i == 0:
                p.start = _spec.node(_F.A3Start, p, marker=_F.A3Marker.v1, num_packets=pieces,
                                     comm_byte=comm_byte & 0xFF)
                p.chunk = value[:inline].ljust(inline, b"\0")
            else:
                start = inline + (i - 1) * SCENE_A3_PIECE_LEN
                p.chunk = value[start:start + SCENE_A3_PIECE_LEN].ljust(SCENE_A3_PIECE_LEN, b"\0")
            return p
        return _frame_of(_PLAIN, _C.ProType.multi_write_v1, body)

    return [chunk(i) for i in range(pieces)]


# makeSendBytesMtu's mtuSize is @IntRange(from = 20, to = PlaybackStateCompat.X = 512).
_A4_MTU_MIN: Final = 20
_A4_MTU_MAX: Final = 512


def scene_upload_a4_mtu(value: bytes, comm_byte: int, *, mtu: int = 20) -> list[bytes]:
    """0xA4-MTU multi-packet upload (``MultipleControllerCommV1.makeSendBytesMtu``). START [A4 00 00 01
    cntLo cntHi comm value x (mtu-8)]; MIDDLE [A4 seqLo seqHi value x (mtu-4)] (1-based); END [A4 FF FF
    value] carries the final chunk, or nothing when the whole value fits in START. ``cnt`` counts every
    frame. Exact-length frames (BCC appended, not zero-padded), each the spec's ``a4_packet``.
    GoveeBleInvalidValue for an ``mtu`` outside the app's 20..512 or a value too long for the u16 count
    (seq 0xFFFF is the END marker)."""
    _check_range("mtu", mtu, _A4_MTU_MIN, _A4_MTU_MAX)
    start_cap, mid_cap = mtu - 8, mtu - 4
    head, rest = value[:start_cap], value[start_cap:]
    total = 1 + max(1, ceil_div(len(rest), mid_cap))
    if total > 0xFFFF:
        raise GoveeBleInvalidValue(f"scene payload too large for 0xA4 upload ({total} packets)")

    chunks = [rest[i : i + mid_cap] for i in range(0, len(rest), mid_cap)] or [b""]

    def packet(index: int, chunk: bytes) -> bytes:
        def body(f: Any) -> Any:
            p = _spec.node(_F.A4Packet, f, index=index, chunk=chunk)
            if index == 0:   # contents [1]: Kaitai 0.11 writes a contents field only as given
                p.start = _spec.node(_F.A4Start, p, marker=b"\x01", num_packets=total,
                                     comm_byte=comm_byte & 0xFF)
            return p
        # exact-length frame: proType, index u2le, START's 4-byte head, the chunk, the checksum
        length = 1 + 2 + (4 if index == 0 else 0) + len(chunk) + 1
        return _frame_of(_PLAIN, _C.ProType.multi_write_v2, body, length)

    return [packet(0, head)] + [packet(0xFFFF if seq == len(chunks) else seq, chunk)
                                for seq, chunk in enumerate(chunks, start=1)]


# ── connect writes ───────────────────────────────────────────────────────────
_EFFECT_STOP: Final = 0x01   # light_effect_stop.value: 1 stops the 0x24 effect (other values are no-ops)


def light_effect_stop() -> bytes:
    """33 25 01: stop the ``33 24`` light effect (``light_effect_stop``), the write the bulb screens send on
    every connect (bulblight ``VM4Light.i5``, ``getCommandType`` 37)."""
    return _write_frame(_PLAIN, _C.Command.light_effect_stop, _params(_F.LightEffectStop, value=_EFFECT_STOP))


# ── plug / secret ────────────────────────────────────────────────────────────
_SYNC_MARKER: Final = 0x01   # epoch_time.marker_is_valid: the app always sends 1


def plug_sync_time(unix_ts: int, tz_hour: int, tz_min: int) -> bytes:
    """33 B5 <u4be epoch> 01 <s1 tz_hour> <s1 tz_min> (``epoch_time``)."""
    ts = unix_ts & 0xFFFFFFFF
    return _write_frame(_PLAIN, _C.Command.plug_sync_time, _params(
        _F.EpochTime, unix_seconds=ts, marker=_SYNC_MARKER, tz_hour=tz_hour, tz_min=tz_min))


SECRET_LEN: Final = 8


def secret_check(secret: bytes) -> bytes:
    """33 B2 <8-byte account-lock secret> (``secret_write``)."""
    if len(secret) != SECRET_LEN:
        raise GoveeBleInvalidValue(f"secret must be exactly {SECRET_LEN} bytes, got {len(secret)}")
    return _write_frame(_PLAIN, _C.Command.secret_write, _params(_F.SecretWrite, secret=bytes(secret)))


def empty_secret_check() -> bytes:
    """33 B2 with no secret: the check a screen that sends it on every connect sends while the account holds
    no secret (``secret_write``'s empty check: ``Controller4Secret.Companion.a("")`` decodes to no bytes and
    ``BleUtils.p`` zero-fills, so the 8 secret bytes are zero). See ``DeviceProfile.empty_secret_check``."""
    return _write_frame(_PLAIN, _C.Command.secret_write, _params(_F.SecretWrite, secret=bytes(SECRET_LEN)))


# ── read requests (0xAA / 0xAC) ──────────────────────────────────────────────
def power_query() -> bytes:
    """aa 01: read the power / relay state (reply: parse_light_power on lights, parse_power's relay byte on
    plugs). No selector byte, as the app sends it (ControllerSwitch.Companion.b, ControllerSwitch.java:38-47;
    SwitchController / HeartGMessage on the plugs); no firmware image reads byte 2 of the request."""
    return _request(_C.Command.power)


def device_detail_query() -> bytes:
    """aa 34: the H6057 device-detail read (reply: parse_detail_power), no selector, as the screen's
    connect list sends it (``Controllers.f``: a Controller4ExtBytes read of 0x34)."""
    return _request(_C.Command.cmd_34)


def heartbeat(command: int) -> bytes:
    """``aa <command>`` with the zero fill: the keep-alive read a control screen sends while the link is up
    (spec devices.yaml timing.heartbeat; the row's ``heartbeat``), the spec's read_request with no selector,
    as the app's heart controllers build it (``Controller4HeartCommon`` for ``ConnectHeartbeat.c``'s byte, an
    ``IHeart``'s controller, ``HeartGMessage.getSendBytes``). ``command`` is a govee_common command code:
    aa 01 (``power``, the same bytes as :func:`power_query`), aa 00, aa 33 or aa 36 (the same bytes as
    :func:`bar_switch_query`): the four codes ``DeviceProfile.heartbeat`` accepts. Raises GoveeBleInvalidValue
    for any other ``command``, a bool or non-int included."""
    if isinstance(command, bool) or not isinstance(command, int) or command not in _HEARTBEAT_CODES:
        raise GoveeBleInvalidValue(
            f"heartbeat must be a keep-alive read (0x00, 0x01, 0x33 or 0x36), got {command!r}")
    return _request(_C.Command(command))


def brightness_query() -> bytes:
    """aa 04: read the brightness (reply: parse_brightness). No selector byte, as the app sends it
    (ControllerBrightness.Companion.b, ControllerBrightness.java:40-48)."""
    return _request(_C.Command.brightness)


_MODE_READ_SELECTOR: Final = 0x01   # mode_query: 1 = the mode read (AbsModeController.p, default ext {1})


def mode_query() -> bytes:
    """aa 05 01: read the mode report (reply: the ``mode_report`` :mod:`.parse` decodes)."""
    return _request(_C.Command.mode, _F.ModeQuery, selector=_MODE_READ_SELECTOR)


def bar_switch_query() -> bytes:
    """aa 36: read H6047's two bars' power (reply: parse_bar_switch)."""
    return _request(_C.Command.cmd_36)


def gradual(on: bool) -> bytes:
    """33 A3 <0/1>: gradual/fade on the BLE<->wifi control handoff (GRADUAL_CHANGE_WIFI_BLE)."""
    return _write_frame(_PLAIN, _C.Command.gradual, _params(   # state: light_power
        _F.GradualWrite, state=POWER_ON if on else POWER_OFF))


def gradual_query() -> bytes:
    """AA A3: read the gradual flag (reply = gradual_state, state @ byte0)."""
    return _request(_C.Command.gradual)


def secret_read() -> bytes:
    """aa b1: read the 8-byte account-lock secret (reply: parse_secret)."""
    return _request(_C.Command.secret_read)


def plug_spec_query() -> bytes:
    """aa b3: read the plug's spec byte (reply: parse_plug_spec). Spec surface: Device does not send it."""
    return _request(_C.Command.plug_spec)


CMD_BULB_COLOR_V1: Final = int(_C.Command.color_group_rgb)   # 0xA2 (mechanism-B V1: colour only)
CMD_BULB_COLOR_V2: Final = int(_C.Command.color_group)       # 0xA5 (mechanism-B V2: + brightness)


def bulb_group_query(batch_seq: int, *, v2: bool = True) -> bytes:
    """Mechanism-B per-group colour read request: ``AA <A5|A2> <batch_seq>`` (1-based
    batch number, AbsSingleController.p; the spec's ``group_query``). V2 (0xA5) carries per-segment
    brightness.

    Device always sends V2; ``v2=False`` (the V1 ``aa a2`` read, BulbStringColorController) is
    spec surface for tools and tests."""
    command = _C.Command.color_group if v2 else _C.Command.color_group_rgb
    return _request(command, _F.GroupQuery, group=batch_seq & 0xFF)


def device_info_query(selector: int) -> bytes:
    """aa 07 <selector> (0x02 sn / device id, 0x03 hard version, 0x10 basic, 0x11 wifi)."""
    return _request(_C.Command.device_info, _F.DeviceInfoQuery, selector=selector & 0xFF)


def soft_version_query() -> bytes:
    """aa 06: read the firmware (soft) version (SoftVersionController; the reply is ASCII)."""
    return _request(_C.Command.soft_version)


CMD_IC_NUM: Final = int(_C.Command.ic_num)   # 0x40 (ic_count reply): live IC/segment capability read


def ic_count_query() -> bytes:
    """aa 40: read the live IC (lamp-bead) count (ControllerOnlyReadIcSegmentNum; reply:
    parse_ic_count). The app does not use the reply's third byte for H6641: the piece and group
    counts are derived client-side from the IC count, as the app does
    (devices/_readback.live_ic_pieces: ceil(clamp(IC, 41, 70) / 5) pieces, ceil(pieces / 4) groups)."""
    return _request(_C.Command.ic_num)


def light_num_query() -> bytes:
    """aa 0f: read the bulb-string count (h7020 ``Controller4BulbNum``, no selector; reply: parse_light_num),
    which the bulb-string screens size their colour pieces from (``NewDetailVM.u5``)."""
    return _request(_C.Command.light_num)


# 0x41: striplight SINGLE_IC_SEGMENT_NUM (H6062), the form_41 ``section_count`` reply; power memory on the
# other SKUs.
CMD_SECTION_COUNT: Final = int(_C.Command.cmd_41)


def section_count_query() -> bytes:
    """aa 41: read the H6062's ``[IC count s16be, sectionNum u8]`` (``striplight``
    ``ControllerIcSegmentNum``, ``BleProtocol.SINGLE_IC_SEGMENT_NUM`` = 65). ``sectionNum`` is
    the segment count the app uses for every mask and the ``aa a5`` group walk. Not the
    ``aa 40`` read of :func:`ic_count_query` (``SINGLE_IC_NUM_AND_PIECE_NUM`` = 64), from whose IC
    count the app derives the pieces client-side instead."""
    return _request(_C.Command.cmd_41)


def status_query(*, full: bool = False, types: Sequence[int] | None = None) -> bytes:
    """0xAC status request: [AC, 03, N, cmd…] (``status_request`` with ``status_read_args``): the TLV
    ``types`` requested, in order (command codes, the device's ``DeviceProfile.status_read``; empty = the bare
    read ``ac 03 00``); None = the H60A6's, 0x41 and 0x30, and with ``full`` the 0xA5 colour groups.

    Device sends its profile's types, without 0xA5 where the advertised pact rules the colour groups out
    (``DeviceProfile.status_color_groups_pact``; H60A6 pact (1, 1): ``ac 03 02 41 30``)."""
    if types is None:   # 0x41 seg info, 0x30 zones, 0xA5 colour groups
        zone_types = [_C.Command.cmd_41, _C.Command.cmd_30]
        types = [*zone_types, _C.Command.color_group] if full else zone_types
    requested = list(types)

    def body(f: Any) -> Any:
        req = _spec.node(_F.StatusRequest, f, command=_F.StatusCommand.status_read, rest=_spec.echo())
        req.args = _spec.node(_F.StatusReadArgs, req, num_types=len(requested), types=requested)
        return req
    return _frame_of(_PLAIN, _C.ProType.status_read, body)


def special_info_query() -> bytes:
    """0xAC special-info read ``ac a1`` (``status_request`` ``special_info``, no args): the goodsType 180
    screen's connect read of the device's segment IC record
    (``ControllerReadSpecialInfoMultiBack.Companion.k``, sent by ``VM4Light.x5``). The reply is a
    multi-packet burst under sub ``segment_ic`` (:func:`.reassemble.parse_segment_ic`)."""
    return _frame_of(_PLAIN, _C.ProType.status_read, lambda f: _spec.node(
        _F.StatusRequest, f, command=_F.StatusCommand.special_info, rest=_spec.echo()))
