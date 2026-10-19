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
from typing import Any, Final, Literal, Never

from ..const import FRAME_LEN, SCENE_A3_HEADER_LEN, SCENE_A3_PIECE_LEN
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
    """``aa <command> [selector]``: the spec's ``read_request``, the app's ``BleUtils.Companion.p(0xAA,
    command, ext)``; the selector is typed for the reads that carry one (``mode_query``,
    ``device_info_query``, ``group_query``), and the zero fill after it is the request's ``rest``."""
    def body(f: Any) -> Any:
        rr = _spec.node(_F.ReadRequest, f, command=command, rest=_spec.echo())
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

# mode (0x05) sub-mode (enum govee_common::sub_mode)
SUB_SCENE: Final = int(_C.SubMode.scene)                 # 0x04
SUB_COLOR_15: Final = int(_C.SubMode.color_rgbic_15)     # 0x15 (H60A6/H6047/H6641)
SUB_COLOR_0D: Final = int(_C.SubMode.color_cct_0d)       # 0x0D (H6006/H6008/H6052 bulbs)
SUB_COLOR_0B: Final = int(_C.SubMode.color_rgbic_0b)     # 0x0B (the h61a8 scheme's frames)

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

# The command forms the builders write under (govee_profile command_forms): which body a shared command
# carries is a device fact, not in the frame.
_FORM_LIGHT: Final = int(_C.Form01.light)              # 33 01 [1 / 0]
_FORM_RELAYS: Final = int(_C.Form01.relays)            # 33 01 [relay byte]
_FORM_TOTAL_SWITCH: Final = int(_C.Form02.total_switch)
_FORM_ZONE_POWER: Final = int(_C.Form30.zone_power)
_FORM_BAR_PAIR: Final = int(_C.Form36.bar_pair)
_FORM_0B_RGB_MASK: Final = int(_C.Form0b.rgb_mask)


# "h60a6" = 0x15 RGBIC (segment mask); CCT is the white-slot form `15 01 ff ff ff <K> <tint> <mask>`
# (Bytes.r type 1) with the app's Kelvin->RGB tint.
# "h6006" = 0x0d bulbs (H6006/H6008/H6052); CCT is the both-slot form `0d <tint> <K> <tint>`
# (Config.bu -> Bytes.p).
# "h61a8" = 0x0b RGBIC (no CCT): the app's colour instruction 3 (Config.J[3] = cu -> Bytes.d), which
# goodsTypes 17, 18, 13, 3, 21 and 123 and the old H6163 / H6117 send. No built-in profile uses it: the
# H61A8 (goodsType 19) sends the h60a6 forms (devices.yaml client_profile by_goods_type 19); it stays for
# custom profiles.
# "h6188" = 0x0d rgblight strip: same RGB frame as h6006, but the CCT is the white-slot form
# `0d ff ff ff <K> <tint>` (Config.GM -> Bytes.A); this one is NOT the h6006 form.
# "h6061" = Glide Hexa panels, 0x15 family with its own whole-device writes: RGB is ceil(n/4)
# group frames `15 04 <group> RGB x<=4` (Bytes.m), CCT is `15 01 <tint> <K> <24-bit mask>`
# (Bytes.u). See the "Glide Hexa" section below.
# Every scheme with a colour-temperature mode also sends an RGB pick that is exactly one of the
# Kelvin tint colours (within the SKU's range) as a CCT frame instead of RGB, with the picked colour
# as the tint (rgb_as_kelvin); h61a8 has no CCT and always sends RGB.
ColorScheme = Literal["h60a6", "h6006", "h61a8", "h6188", "h6061"]


def _unsupported_scheme(scheme: Never) -> Never:
    """Exhaustiveness guard: mypy proves every builder handles each ``ColorScheme``; at runtime (a
    string outside the Literal) refuse rather than send another family's frame."""
    raise GoveeBleNotSupported(f"unknown colour scheme {scheme!r}")


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
# MAX_MASK_SEGMENTS is the most segments any body the library writes can address.
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
# Source (Govee Home 7.7.02, split pact_h6061 + base.apk): the panel count n is the size of the
# user's layout, else the goodsType's piece count ColorPieceConfig.j, ColorPieceConfig.Companion.f(gt)
# (devices.yaml h6061 wire counts.num_segments, the profile's segments; Device passes it as ``pieces``):
# Support.getBulbStringMaxNum(51) = 23 on the H6061, the default.
H6061_MAX_PANELS: Final = 23
H6061_GROUP_SIZE: Final = 4          # Bytes.m / BleUtils.w0(colors, 4): four colours per group frame
H6061_MASK_BYTES: Final = 3          # ColorPieceConfig.k: the H6061's 23 pieces round up to 24 positions
OP15_SET_GROUP_COLOR: Final = int(_C.Op15.set_color_group)   # 0x04; Bytes.m: [0x15, 0x04, group, RGB x<=4]
H6061_KELVIN_RANGE: Final = (2000, 9000)   # KelvinConfig: no entry for 51 -> version 0
_FORM_RGB_GROUP: Final = int(_C.Form1504.rgb_group)
# H6061's frames: op 0x04 is the RGB group form, op 0x01 the colour-version-5 form over its posSet.
_PROFILE_H6061_GROUP: Final = _spec.profile(f_15_04=_FORM_RGB_GROUP)

# Segment count a whole-device builder assumes when the caller passes none (``Device`` always passes
# the profile's count): ``h60a6`` and ``h61a8`` take their namesake SKU's count (H60A6 13 segments, H61A8
# 15, the 0x0b frames' strip), and ``h6061`` the device's piece count (:func:`_h6061_panels`). The 0x0d
# schemes carry no mask.
_DEFAULT_SEGMENTS: Final[dict[ColorScheme, int]] = {"h60a6": 13, "h61a8": 15}


def _segments_or_default(scheme: ColorScheme, segments: int | None) -> int:
    """``segments``, or the scheme's default count when it is None (an explicit 0 stays 0)."""
    return segments if segments is not None else _DEFAULT_SEGMENTS.get(scheme, 0)


def _h6061_panels(panels: int | None, pieces: int | None) -> tuple[int, int]:
    """``(panels written, mask positions)`` for a layout of ``panels`` panels (None: the whole device) on a
    device of ``pieces`` (``ColorPieceConfig.j``; None: the H6061's 23).

    More than ``j`` is refused: ``ColorPieceConfig.Config.s0`` returns nothing for a colour set longer
    than ``j``, so the app sends no colour at all. A single colour set with ``j`` > 1 is expanded to ``j``
    colours in ``s0``, so a one-panel layout is written as ``j`` panels. The mask spans
    ``ColorPieceConfig.k``, ``j`` rounded up to whole bytes (23 pieces: 24 positions)."""
    j = H6061_MAX_PANELS if pieces is None else pieces
    n = j if panels is None else panels
    _check_range("panel count", n, 1, j)
    return (j if n == 1 else n), 8 * ceil_div(j, 8)

def _h6061_group_frames(r: int, g: int, b: int, panels: int | None, pieces: int | None) -> list[bytes]:
    """Whole-device RGB: ``33 05 15 04 <group from 1> R G B x<=4``, one frame per group of four
    panels, the last carrying only the remaining panels (23 panels: 6 frames, the last with 3
    colours). ``ColorModeControllerConfig.k`` -> ``w`` -> ``l`` (colour-brightness-group mode 1,
    ``Config.A[51]``) -> ``Bytes.m``."""
    n, _positions = _h6061_panels(panels, pieces)

    def group_frame(group: int) -> bytes:
        count = min(H6061_GROUP_SIZE, n - (group - 1) * H6061_GROUP_SIZE)
        # op15_rgb_group carries four colours: the ones past the group's count are black (the zero fill).
        colors = [_spec.rgb(r, g, b) if k < count else _spec.rgb(0, 0, 0) for k in range(H6061_GROUP_SIZE)]

        def data(c15: Any) -> Any:
            grp = _spec.node(_F.Op15Group, c15, _FORM_RGB_GROUP)
            grp.body = _spec.node(_F.Op15RgbGroup, grp, group=group, colors=colors)
            return grp
        return _op15_frame(_PROFILE_H6061_GROUP, _C.Op15.set_color_group, data)

    return [group_frame(group) for group in range(1, ceil_div(n, H6061_GROUP_SIZE) + 1)]


def _h6061_color_temp(kelvin: int, panels: int | None, tint: tuple[int, int, int] | None = None,
                      pieces: int | None = None) -> bytes:
    """Whole-device CCT: ``33 05 15 01 <tint RGB> <kelvin u2be> <mask>``, the first ``panels`` mask bits
    set, LSB first (23 panels: ``ff ff 7f``). ``Config.x[51]`` = 5 -> ``Config.J[5]`` = ``Config.Zt`` ->
    ``Bytes.u(kelvin, positions, tint)``; the mask is ``BleUtils.X`` over the ``ColorPieceConfig.k``
    position set (24 on the H6061, :func:`_h6061_panels`). The tint sits in the colour slot (not
    ``ff ff ff``). ``tint`` defaults to the app's tint for ``kelvin`` (the slider path); an RGB pick that
    is itself a tint colour passes that exact colour (``Zt`` writes the picked colour, whichever of the
    Kelvin step's two colours it is)."""
    n, positions = _h6061_panels(panels, pieces)
    return _op15_color_frame(_FORM_KELVIN, tint if tint is not None else tint_for_kelvin(kelvin),
                             all_segments_mask(n), positions, kelvin)


# Schemes whose colour screen turns an RGB pick that is exactly a Kelvin tint colour into a CCT write
# (every scheme with CCT; h61a8 has none). Config.Yt (0x15 family, h60a6) / Config.bu (0x0d family,
# h6006 and h6188) / ColorModeControllerConfig.k + Config.Zt (h6061), all keyed on KelvinConfig.i.
_RGB_PICK_AS_KELVIN_SCHEMES: Final[frozenset[ColorScheme]] = frozenset({"h60a6", "h6006", "h6188", "h6061"})


def rgb_as_kelvin(r: int, g: int, b: int, scheme: ColorScheme,
                  kelvin_range: tuple[int, int] | None = None) -> int | None:
    """The Kelvin the app sends for an RGB pick, or None when it sends the plain RGB frame.

    The colour screens of every scheme with a colour-temperature mode test the picked colour with
    ``KelvinConfig.i(colour)`` (full ARGB equality with one of the 178 Kelvin tint colours,
    :func:`wire.cct.kelvin_for_tint`, AND that Kelvin inside the SKU's inclusive ``[lo, hi]`` range)
    and write ``KelvinConfig.c(colour)`` as a CCT frame instead of RGB: ``Config.Yt`` (0x15 family,
    ``h60a6``), ``Config.bu`` (0x0d family, ``h6006`` and ``h6188``) and, for a whole-device colour
    set with one colour, ``ColorModeControllerConfig.k`` -> ``Config.Zt`` (``h6061``).
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
                         segments: int | None, mask: int | None = None, pieces: int | None = None) -> bytes:
    """The CCT frame the app writes for an RGB pick that :func:`rgb_as_kelvin` turned into
    ``kelvin``; the PICKED colour is the tint, not the table's first colour for ``kelvin`` (a Kelvin
    step has two colours). ``mask`` None is the whole device:

    * ``h60a6`` (``Config.Yt`` -> ``Bytes.r``): ``15 01 ff ff ff <K> <picked RGB> <mask>``, one frame
      with the mask the RGB frame would have used, all ``segments`` when ``mask`` is None (the slider
      path, :func:`color_temp`, is the same frame over the all-segments mask with the Kelvin's first
      tint);
    * ``h6006`` / ``h6188`` (``Config.bu`` -> ``Bytes.p``): ``0d <picked RGB> <K> <first tint of K>``,
      whole device (no mask), the same frame for both schemes (the white-slot form of the h6188
      slider is not used here);
    * ``h6061`` (``Config.Zt`` -> ``Bytes.u``): ``15 01 <picked RGB> <K> <mask>`` over the ``segments``
      panels of a device of ``pieces`` (whole device only, :func:`_h6061_panels`)."""
    match scheme:
        case "h60a6":
            if mask is None:
                segments = _segments_or_default(scheme, segments)
                mask = all_segments_mask(segments)
            return segment_color_temp(mask, kelvin, scheme, segments, tint=(r, g, b))
        case "h6006" | "h6188":
            return _cct0d_frame((r, g, b), kelvin, tint_for_kelvin(kelvin))
        case "h6061":
            return _h6061_color_temp(kelvin, segments, (r, g, b), pieces)
        case "h61a8":   # rgb_as_kelvin never picks a Kelvin here
            raise GoveeBleNotSupported("h61a8 has no colour-temperature capability")
        case _:
            _unsupported_scheme(scheme)


# ── colour ───────────────────────────────────────────────────────────────────
def color_rgb_frames(r: int, g: int, b: int, scheme: ColorScheme, segments: int | None = None,
                     kelvin_range: tuple[int, int] | None = None, *,
                     pieces: int | None = None) -> list[bytes]:
    """Whole-device RGB pick as the frames the app sends: one for every scheme except ``h6061``, whose
    panels are written in groups of four (see :func:`color_rgb`, which is the plain single-frame form).
    An RGB that is a Kelvin tint colour within ``kelvin_range`` (the SKU's range) is sent as one CCT
    frame instead, exactly as the app does, on every scheme with a colour-temperature mode (see
    :func:`rgb_as_kelvin` and :func:`_rgb_pick_color_temp`); ``kelvin_range`` None sends plain RGB
    (``h6061`` defaults to its own range). ``segments`` None is the scheme's default count
    (:data:`_DEFAULT_SEGMENTS`; on ``h6061`` every panel, the app's fallback). ``pieces`` is an
    ``h6061`` device's panel count (the profile's ``segments``; None: the H6061's 23), which bounds
    ``segments`` and sizes the mask (:func:`_h6061_panels`); other schemes ignore it."""
    _check_rgb(r, g, b)
    kelvin = rgb_as_kelvin(r, g, b, scheme, kelvin_range)
    if kelvin is not None:
        return [_rgb_pick_color_temp(r, g, b, kelvin, scheme, segments, pieces=pieces)]
    if scheme == "h6061":
        return _h6061_group_frames(r, g, b, segments, pieces)
    return [color_rgb(r, g, b, scheme, segments)]


def color_rgb(r: int, g: int, b: int, scheme: ColorScheme, segments: int | None = None) -> bytes:
    """Solid RGB, per-family SubModeColor.getWriteBytes (whole-device = all-segments mask;
    ``segments`` None is the scheme's default count, :data:`_DEFAULT_SEGMENTS`). ``h6061`` has no
    single-frame form (it needs :func:`color_rgb_frames`): GoveeBleNotSupported."""
    _check_rgb(r, g, b)
    match scheme:
        case "h60a6":
            n = _segments_or_default(scheme, segments)
            mask = all_segments_mask(n)
            positions = _mask_positions(mask, n, COLOR_MASK_CAP)
            return _op15_color_frame(_FORM_KELVIN_TINT, (r, g, b), mask, positions)
        case "h6006" | "h6188":
            return _cct0d_frame((r, g, b))
        case "h61a8":
            return _rgbic0b_frame((r, g, b), all_segments_mask(_segments_or_default(scheme, segments)))
        case "h6061":
            raise GoveeBleNotSupported("h6061 whole-device RGB is several frames; use color_rgb_frames")
        case _:
            _unsupported_scheme(scheme)


def color_temp(kelvin: int, scheme: ColorScheme, segments: int | None = None, *,
               pieces: int | None = None) -> bytes:
    """Whole-device colour temperature, always with the app's Kelvin->RGB tint
    (``wire.cct.tint_for_kelvin``): ``h6006`` = ``0d <tint> <K> <tint>``, ``h6188`` =
    ``0d ff ff ff <K> <tint>``, ``h60a6`` = ``15 01 ff ff ff <K> <tint> <mask>`` (this is just
    segment_color_temp with the all-segments mask), ``h6061`` = ``15 01 <tint> <K> <mask>`` over
    ``segments`` panels of a device of ``pieces`` (as in :func:`color_rgb_frames`; ``segments`` None is
    the scheme's default count, :data:`_DEFAULT_SEGMENTS`). ``h61a8`` has no colour temperature:
    GoveeBleNotSupported. The builder sends ``kelvin`` as given; the ``Device`` layer applies
    :func:`normalize_kelvin` first."""
    if scheme == "h61a8":
        raise GoveeBleNotSupported("h61a8 has no colour-temperature capability")
    _check_kelvin(kelvin)
    match scheme:
        case "h60a6":
            n = _segments_or_default(scheme, segments)
            return segment_color_temp(all_segments_mask(n), kelvin, scheme, n)
        case "h6006":
            # Config.bu -> Bytes.p(tint, K): [0d, tint r g b, kelvin u2be, tint r g b] - the tint
            # sits in BOTH colour slots (H6008/H6006/H6052; also H6004 via the same builder).
            tint = tint_for_kelvin(kelvin)
            return _cct0d_frame(tint, kelvin, tint)
        case "h6188":
            # Config.GM -> Bytes.A(kelvin, RGB_WHITE): [0d, ff ff ff, kelvin u2be, tint r g b].
            return _cct0d_frame((0xFF, 0xFF, 0xFF), kelvin, tint_for_kelvin(kelvin))
        case "h6061":
            return _h6061_color_temp(kelvin, segments, pieces=pieces)
        case _:
            _unsupported_scheme(scheme)


# The 0x0d family (h6006 / h6188) has no per-segment frame in the app: its colour writer, colour
# instruction version 2 = Config.K[2] (Config.at in the current decompile, cited as Config.bu
# above), takes the position set as an unused argument, and its builders Bytes.f / Bytes.p /
# Bytes.A carry no mask. The segment builders below refuse it.
def segment_rgb(mask: int, r: int, g: int, b: int, scheme: ColorScheme,
                segments: int | None = None,
                kelvin_range: tuple[int, int] | None = None) -> bytes:
    """Per-segment RGB pick (mask selects segments); same layout as color_rgb. ``segments`` is the
    device's segment count, which sets the 0x15 mask width (see :func:`mask_width`); omitted,
    the mask is the legacy two bytes. On ``h60a6`` a colour that is a Kelvin tint colour within
    ``kelvin_range`` (the SKU's range) is sent as the CCT frame ``15 01 ff ff ff <K> <picked RGB>
    <mask>`` instead (``Config.Yt``, same mask), see :func:`rgb_as_kelvin`; ``kelvin_range`` None
    sends plain RGB. GoveeBleNotSupported on the whole-device-only schemes (0x0d, ``h6061``)."""
    _check_rgb(r, g, b)
    match scheme:
        case "h60a6":
            kelvin = rgb_as_kelvin(r, g, b, scheme, kelvin_range)
            if kelvin is not None:
                return _rgb_pick_color_temp(r, g, b, kelvin, scheme, segments, mask)
            return _op15_color_frame(_FORM_KELVIN_TINT, (r, g, b), mask,
                                     _mask_positions(mask, segments, COLOR_MASK_CAP))
        case "h61a8":
            return _rgbic0b_frame((r, g, b), mask)
        case "h6006" | "h6188":
            raise GoveeBleNotSupported(f"{scheme} colour is whole-device only (no segment mask)")
        case "h6061":
            raise GoveeBleNotSupported("h6061 is whole-device only (per-panel colour is not modelled)")
        case _:
            _unsupported_scheme(scheme)


def segment_color_temp(mask: int, kelvin: int, scheme: ColorScheme,
                       segments: int | None = None,
                       tint: tuple[int, int, int] | None = None) -> bytes:
    """Colour temperature on selected segments (mask), ``15 01 ff ff ff <K u2be> <tint> <mask>``
    (``Bytes.r`` type 1; the tint is ``Constant.getTemColorByKelvin``). ``segments`` is the
    device's segment count, which sets the mask width (see :func:`mask_width`); omitted, the
    mask is the legacy two bytes. Only the 0x15 family
    embeds a segment mask in the CCT frame — the 0x0d frame has no mask (whole-device only) and
    h61a8 has no CCT at all — so masked CCT is 0x15-only (GoveeBleNotSupported elsewhere). ``tint``
    defaults to the app's tint for ``kelvin`` (the slider path); an RGB pick that is itself a tint
    colour passes that exact colour (``Config.Yt`` writes the picked colour, whichever of the Kelvin
    step's two colours it is)."""
    match scheme:
        case "h60a6":
            _check_kelvin(kelvin)
            positions = _mask_positions(mask, segments, COLOR_MASK_CAP)
            return _op15_color_frame(_FORM_KELVIN_TINT, (0xFF, 0xFF, 0xFF), mask, positions, kelvin,
                                     tint if tint is not None else tint_for_kelvin(kelvin))
        case "h61a8":
            raise GoveeBleNotSupported("h61a8 has no colour-temperature capability")
        case "h6006" | "h6188":
            raise GoveeBleNotSupported(f"{scheme} colour-temperature is whole-device only (no segment mask)")
        case "h6061":
            raise GoveeBleNotSupported(
                "h6061 is whole-device only (per-panel colour temperature is not modelled)")
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
        case "h6061":
            raise GoveeBleNotSupported("h6061 is whole-device only (per-panel brightness is not modelled)")
        case _:
            _unsupported_scheme(scheme)



# ── zones / bars ─────────────────────────────────────────────────────────────
def zone_power(zone: int, on: bool) -> bytes:
    """33 30 <zoneIndex> <state> (H60A6 per-zone): ``cmd_30_write`` under form_30 ``zone_power``."""
    _check_range("zone", zone, 0, 0xFF)
    return _write_frame(_spec.profile(f_30=_FORM_ZONE_POWER), _C.Command.cmd_30, _form_params(
        _F.Cmd30Write, _FORM_ZONE_POWER, _F.ZonePowerWrite, zone=zone, state=POWER_ON if on else POWER_OFF))


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
    if pieces > 0xFF:
        # The count is one byte and seq 0xFF is the end marker.
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


# ── read requests (0xAA / 0xAC) ──────────────────────────────────────────────
def power_query() -> bytes:
    """aa 01: read the power / relay state (reply: parse_light_power on lights, parse_power's relay byte on
    plugs). No selector byte, as the app sends it (ControllerSwitch.Companion.b, ControllerSwitch.java:38-47;
    SwitchController / HeartGMessage on the plugs); no firmware image reads byte 2 of the request."""
    return _request(_C.Command.power)


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
    ``types`` requested, in order (command codes, the device's ``DeviceProfile.status_read``); None = the
    H60A6's, 0x41 and 0x30, and with ``full`` the 0xA5 colour groups.

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
