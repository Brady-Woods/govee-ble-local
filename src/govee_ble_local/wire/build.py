"""Frame builders — the write side, written by the spec-generated read-write classes.

Every frame is a ``GoveeBleFrame`` tree of the spec's own types (spec/govee_ble.ksy, generated with
``tools/gen_kaitai.sh``), serialized by Kaitai; the library only picks which type and values the app
sends. What stays in code is what Kaitai can't express: the XOR checksum (the frame's ``checksum``
field, "computed externally"), packet counts and chunking of the multi-packet uploads, the app's
colour and Kelvin choices (tint table, RGB-pick-as-CCT, clamps), and the caller-input checks.
Where the frame's read dispatch has no case for a write body, or types it as the reply, the spec's
standalone write type is used (``ZonePowerWrite``, ``Op15ColorWrite``, ``ReadRequest``, ...).
``tests/test_wire_build_vs_spec.py`` binds every builder byte for byte to the app-traced catalogue,
and ``tests/test_wire_build.py`` reads each frame back. Byte layouts follow the spec and the
decompiled app (each builder cites its source). Not all of them are hardware-verified: the device
profiles note which SKUs are, and the short-value 0xA3 upload (:func:`scene_upload_a3`) is source-modeled from
``MultipleControllerCommV1.makeSendBytesV2``, not live-verified. Scene uploads take the raw
``value`` bytes (the device layer base64-decodes + strips the param).

A builder raises GoveeBleNotSupported when the colour scheme cannot express the request (no such
frame in the app for that family) and GoveeBleInvalidValue for bad caller input.
"""
from __future__ import annotations

import struct
from collections.abc import Callable
from io import BytesIO
from typing import Any, Final, Literal, Never

from kaitaistruct import KaitaiStream, KaitaiStructError, ReadWriteKaitaiStruct

from .._generated.govee_ble_frame import GoveeBleFrame as _GBF  # type: ignore[attr-defined]
from ..const import FRAME_LEN, SCENE_A3_HEADER_LEN, SCENE_A3_PIECE_LEN
from ..crypto import checksum
from ..exceptions import GoveeBleInvalidValue, GoveeBleNotSupported
from .cct import kelvin_for_tint, tint_for_kelvin

# The spec-generated read-write classes (spec/govee_ble.ksy). They are untyped, so Any here. This is
# the wire layer's one import of them: the protocol constants below are their enums, the builders
# below write every frame with them, and :mod:`.parse`, :mod:`.reassemble` and :mod:`.describe` read
# frames with them.
_F: Any = _GBF


def _load(obj: Any) -> Any:
    """Read a generated object and return it: a read-write class reads only when asked
    (``from_bytes`` / ``Cls(..., stream)`` just bind the stream). Raises what the reader raises."""
    obj._read()
    return obj


# ── writing with the generated classes ───────────────────────────────────────
def _node(cls: Any, parent: Any, *params: Any, **fields: Any) -> Any:
    """A generated object to write: ``params`` are the type's ksy params, ``fields`` its values. It is
    linked under ``parent`` (``_check`` verifies the links); a standalone body has no parent."""
    obj = cls(*params, _io=None, _parent=parent, _root=parent._root if parent is not None else None)
    if parent is None:
        obj._root = obj   # a standalone body is its own root, so its children link to it
    for name, value in fields.items():
        setattr(obj, name, value)
    return obj


def _check_tree(obj: Any) -> None:
    """``_check()`` every generated object under ``obj``, then ``obj`` itself (Kaitai checks one object
    at a time and refuses to write one that changed since its check)."""
    for name, value in vars(obj).items():
        if name in ("_io", "_parent", "_root"):
            continue
        children = value if isinstance(value, list) else (value,)
        for child in children:
            if isinstance(child, ReadWriteKaitaiStruct):
                _check_tree(child)
    obj._check()


def _serialize(obj: Any, length: int) -> bytes:
    """``obj`` written into a zeroed ``length``-byte stream: the bytes past its last field stay zero, the
    frame padding. A value the spec type can't carry (a ``valid`` it fails, a number too wide for its
    field) is GoveeBleInvalidValue."""
    try:
        _check_tree(obj)
        io = KaitaiStream(BytesIO(bytearray(length)))
        obj._write(io)
        return bytes(io.to_byte_array())
    except (KaitaiStructError, struct.error) as err:
        raise GoveeBleInvalidValue(f"value doesn't fit the spec's {type(obj).__name__}: {err}") from err


def _body(obj: Any, length: int) -> bytes:
    """A standalone spec body as the ``length`` raw bytes of the frame field it fills (zero-padded):
    for a field the frame's read dispatch leaves untyped (e.g. the params of 33 30 / 33 36 / 33 a3)."""
    return _serialize(obj, length)


def _emit(frame_obj: Any, length: int = FRAME_LEN) -> bytes:
    """A whole frame. Its ``checksum`` field is the one value computed here (the XOR of every byte before
    it; the ksy can't compute it): written once with 0, then with the XOR."""
    frame_obj.checksum = 0
    first = _serialize(frame_obj, length)
    frame_obj.checksum = checksum(first[:-1])
    return _serialize(frame_obj, length)


_BODY_LEN: Final = FRAME_LEN - 2         # the frame's body: everything between proType and checksum
_PARAMS_LEN: Final = _BODY_LEN - 1       # a command's params: the body after the command byte


def _frame_of(pro_type: Any, make_body: Callable[[Any], Any], length: int = FRAME_LEN) -> bytes:
    """A ``GoveeBleFrame`` of ``pro_type`` whose body ``make_body(frame)`` builds (linked to it)."""
    f = _node(_F, None, pro_type=pro_type)
    f.body = make_body(f)
    return _emit(f, length)


def _write_frame(command: Any, make_params: Callable[[Any], Any]) -> bytes:
    """``33 <command> <params>``: a ``single_command`` whose params ``make_params(single_command)``
    builds: a typed body, or the raw bytes of an untyped one."""
    def body(f: Any) -> Any:
        sc = _node(_F.SingleCommand, f, command=command)
        sc.params = make_params(sc)
        return sc
    return _frame_of(_F.ProType.write, body)


def _mode_frame(sub_mode: Any, make_params: Callable[[Any], Any]) -> bytes:
    """``33 05 <sub_mode> <params>`` (``mode_payload``)."""
    def params(sc: Any) -> Any:
        mp = _node(_F.ModePayload, sc, sub_type=sub_mode)
        mp.params = make_params(mp)
        return mp
    return _write_frame(_F.Command.mode, params)


def _op15_frame(op: Any, make_data: Callable[[Any], Any]) -> bytes:
    """``33 05 15 <op> <data>`` (``color_15``)."""
    def params(mp: Any) -> Any:
        c15 = _node(_F.Color15, mp, op_type=op)
        c15.data = make_data(c15)
        return c15
    return _mode_frame(_F.SubMode.color_rgbic_15, params)


def _request(command: Any, *selector: int) -> bytes:
    """``aa <command> <selector...>``: the spec's ``read_request``, in the body the read dispatch types
    as the reply (``read_command``); the selector field runs to the end of the body, zero-padded."""
    return _frame_of(_F.ProType.read, lambda f: _node(
        _F.ReadRequest, f, command=command, selector=bytes(selector).ljust(_PARAMS_LEN, b"\0")))


def ceil_div(a: int, b: int) -> int:
    """Ceiling integer division (``ceil(a / b)`` for positive ``b``)."""
    return -(-a // b)


# Protocol constants, taken from the generated reader's enums so the spec is their one source
# (int() keeps them plain ints, as the frame bytes and the ``==`` checks against raw bytes need).
# proType (enum pro_type)
PRO_WRITE: Final = int(_F.ProType.write)                 # 0x33
PRO_READ: Final = int(_F.ProType.read)                   # 0xAA
PRO_MULTI_A1: Final = int(_F.ProType.multi_write)        # 0xA1: MultiPackageManager stream (RX only)
PRO_MULTI_A2: Final = int(_F.ProType.multi_read)         # 0xA2: multi_read, device -> app (RX only)
PRO_MULTI_A3: Final = int(_F.ProType.multi_write_v1)     # 0xA3
PRO_MULTI_A4: Final = int(_F.ProType.multi_write_v2)     # 0xA4
PRO_MULTI_AC: Final = int(_F.ProType.multi_reply_read)   # 0xAC
PRO_NOTIFY: Final = int(_F.ProType.notify)               # 0xEE: device pushes (parse.parse_notify)

# command (enum command)
CMD_SWITCH: Final = int(_F.Command.switch)                       # 0x01
CMD_BRIGHTNESS: Final = int(_F.Command.brightness)               # 0x04
CMD_MODE: Final = int(_F.Command.mode)                           # 0x05
CMD_SOFT_VERSION: Final = int(_F.Command.soft_version)           # 0x06
CMD_DEVICE_INFO: Final = int(_F.Command.device_info)             # 0x07
CMD_ZONE: Final = int(_F.Command.light_direction_or_zone)        # 0x30
CMD_BAR_SWITCH: Final = int(_F.Command.compose_light_switch)     # 0x36
CMD_GRADUAL_WIFI_BLE: Final = int(_F.Command.gradual_wifi_ble)   # 0xA3: gradual/fade on BLE<->wifi handoff
CMD_SECRET_READ: Final = int(_F.Command.secret_read)             # 0xB1
CMD_SECRET_WRITE: Final = int(_F.Command.secret_write)           # 0xB2
CMD_PLUG_SPEC: Final = int(_F.Command.plug_spec)                 # 0xB3
CMD_PLUG_SYNC_TIME: Final = int(_F.Command.plug_sync_time)       # 0xB5

# mode (0x05) sub-mode (enum sub_mode)
SUB_SCENE: Final = int(_F.SubMode.scene)                 # 0x04
SUB_COLOR_15: Final = int(_F.SubMode.color_rgbic_15)     # 0x15 (H60A6/H6047/H6641)
SUB_COLOR_0D: Final = int(_F.SubMode.color_cct_0d)       # 0x0D (H6006/H6008/H6052 bulbs)
SUB_COLOR_0B: Final = int(_F.SubMode.color_rgbic_0b)     # 0x0B (H61A8)

# op15 (enum op15) — sub-op after 0x15
OP15_SET_COLOR: Final = int(_F.Op15.set_color)            # 0x01
OP15_SET_BRIGHTNESS: Final = int(_F.Op15.set_brightness)  # 0x02

# Device-info (0x07) selectors (no ksy enum yet). SN (device id) and HARD (hardware version) are the
# reads the app makes (SnController.p, HardVersionController.p); BASIC / WIFI are the binary forms.
INFO_BASIC: Final = 0x10
INFO_WIFI: Final = 0x11
INFO_SN: Final = 0x02
INFO_HARD: Final = 0x03

# power payload values (no ksy enum yet)
POWER_ON: Final = 0x01
POWER_OFF: Final = 0x00
RELAY_ON: Final = 0x11    # plug family, outlet 0 (SwitchControllerV2.q, outlet 0)
RELAY_OFF: Final = 0x10
RELAY_ALL_ON: Final = 0xFF    # every outlet (SwitchControllerV2.q, outlet 15: 0xF0 | 0x0F)
RELAY_ALL_OFF: Final = 0xF0
# 33 02: H5089's total switch (Controller4TotalSwitch.getCommandType); no ksy enum entry yet.
CMD_TOTAL_SWITCH: Final = 0x02

# H60A6 dialect-B DIY/graffiti protocol code; H6052 type-5 professional graffiti.
COMM_H60A6: Final = 0x58
COMM_H6052_GRAFFITI: Final = 0x09

# "h60a6" = 0x15 RGBIC (segment mask); CCT is the white-slot form `15 01 ff ff ff <K> <tint> <mask>`
# (Bytes.r type 1) with the app's Kelvin->RGB tint.
# "h6006" = 0x0d bulbs (H6006/H6008/H6052); CCT is the both-slot form `0d <tint> <K> <tint>`
# (Config.bu -> Bytes.p).
# "h61a8" = 0x0b RGBIC (no CCT).
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


# Segment-mask width, from the app's encoder. Up to 16 segments the mask is always two bytes
# (BleUtils.makeSelectedTwoBytes); above that it is ceil(n/8) bytes, LSB first
# (BleUtils.X, the `length > 16` branch in color/Bytes.G/b/d/C). Bytes.C caps the width at 7
# bytes (it truncates a longer mask and logs an analytics event); we refuse instead.
LEGACY_MASK_SEGMENTS: Final = 16
MAX_MASK_BYTES: Final = 7
MAX_MASK_SEGMENTS: Final = MAX_MASK_BYTES * 8


def mask_width(segments: int) -> int:
    """Bytes of segment mask the app writes for a device with ``segments`` segments:
    2 for 0..16 (makeSelectedTwoBytes), else ceil(n/8) (BleUtils.X). Raises
    GoveeBleInvalidValue above 56 segments (7 bytes, the most Bytes.C will encode)."""
    _check_range("segment count", segments, 0, MAX_MASK_SEGMENTS)
    if segments <= LEGACY_MASK_SEGMENTS:
        return 2
    return ceil_div(segments, 8)


def _check_mask(mask: int, bits: int = LEGACY_MASK_SEGMENTS) -> int:
    return _check_range("segment mask", mask, 0, (1 << bits) - 1)


def frame(pro_type: int, *payload: int) -> bytes:
    """A 20-byte frame: [pro_type, *payload, zero-pad, XOR-checksum@19]."""
    if len(payload) > FRAME_LEN - 2:
        raise ValueError(
            f"payload too long: {len(payload)} bytes (max {FRAME_LEN - 2} so the frame fits in {FRAME_LEN})")
    buf = bytearray(FRAME_LEN)
    buf[0] = pro_type
    buf[1 : 1 + len(payload)] = bytes(payload)
    buf[FRAME_LEN - 1] = checksum(bytes(buf[: FRAME_LEN - 1]))
    return bytes(buf)


def _mask_bytes(mask: int, segments: int | None = None) -> bytes:
    """Segment mask as the app encodes it, LSB first: :func:`mask_width` bytes for a device
    with ``segments`` segments (the width depends on the device's segment count, not on
    which segments are selected). ``segments=None`` is the legacy two-byte mask."""
    width = mask_width(LEGACY_MASK_SEGMENTS if segments is None else segments)
    _check_range("segment mask", mask, 0, (1 << (8 * width)) - 1)
    return mask.to_bytes(width, "little")


def all_segments_mask(segments: int) -> int:
    """Whole-device mask = the first ``segments`` bits set (BleUtil select-all-positions).
    Raises GoveeBleInvalidValue above :data:`MAX_MASK_SEGMENTS` (56)."""
    _check_range("segment count", segments, 0, MAX_MASK_SEGMENTS)
    return (1 << segments) - 1


# ── power / brightness ───────────────────────────────────────────────────────
def switch(on: bool, *, relay: bool = False) -> bytes:
    """33 01 <state>. relay=True: the outlet-0 relay encoding 0x11/0x10, goodsType 43's main switch
    (H5080 / H5083 / H5085); the multi-outlet plugs use :func:`plug_all_outlets` /
    :func:`plug_total_switch`."""
    val = (RELAY_ON if on else RELAY_OFF) if relay else (POWER_ON if on else POWER_OFF)
    return _write_frame(_F.Command.switch, lambda sc: _node(_F.SwitchPayload, sc, state=val))


def plug_all_outlets(on: bool) -> bytes:
    """33 01 FF / F0: every outlet of a multi-outlet plug on / off, the main switch of goodsType 50
    (H5082, UiV2.onOffChange) and 90 (H5160 / H5161, UiV4.onOffChange): ``SwitchControllerV2(on, 15)``,
    whose ``q()`` writes ``0xF0 | (0x0F if on else 0)`` (h5080 SwitchControllerV2.java:53-71)."""
    val = RELAY_ALL_ON if on else RELAY_ALL_OFF
    return _write_frame(_F.Command.switch, lambda sc: _node(_F.SwitchPayload, sc, state=val))


def plug_total_switch(on: bool) -> bytes:
    """33 02 01 / 00: H5089's main switch (goodsType 307, Ui4H5089.onOffChange:929-946):
    ``Controller4TotalSwitch(on)``, command 0x02, one 1/0 byte (Controller4TotalSwitch.q). The spec has no
    0x02 command or body type yet, so its one params byte is written raw."""
    state = POWER_ON if on else POWER_OFF
    return _write_frame(CMD_TOTAL_SWITCH, lambda sc: bytes([state]).ljust(_PARAMS_LEN, b"\0"))


def brightness(pct: int) -> bytes:
    """33 04 <level 0-100> (clamped). Raises GoveeBleInvalidValue for a non-int."""
    level = _clamp_pct(pct)
    return _write_frame(_F.Command.brightness, lambda sc: _node(_F.BrightnessPayload, sc, level=level))


def brightness_raw(level: int) -> bytes:
    """33 04 <raw level 0-255>, for devices whose brightness byte is not a percent
    (ControllerBrightness(i) writes the byte verbatim; e.g. H6188's 20..254 range)."""
    raw = _check_range("brightness level", level, 0, 0xFF)
    return _write_frame(_F.Command.brightness, lambda sc: _node(_F.BrightnessPayload, sc, level=raw))


# ── colour frames (the spec's write types) ───────────────────────────────────
def _cct0d_frame(rgb: tuple[int, int, int], kelvin: int = 0,
                 tint: tuple[int, int, int] = (0, 0, 0)) -> bytes:
    """``33 05 0d [r, g, b][kelvin u2be][tint r, g, b]`` (``color_cct_0d``): RGB with kelvin 0 and no tint
    (Bytes.f), CCT with the tint (Bytes.p / Bytes.A)."""
    return _mode_frame(_F.SubMode.color_cct_0d, lambda mp: _node(
        _F.ColorCct0d, mp, r=rgb[0], g=rgb[1], b=rgb[2], kelvin=kelvin,
        tint_r=tint[0], tint_g=tint[1], tint_b=tint[2]))


def _rgbic0b_frame(rgb: tuple[int, int, int], mask: int) -> bytes:
    """``33 05 0b [r, g, b][seg_mask]`` (``color_rgbic_0b_wide``, the two-byte mask of up to 16 segments)."""
    seg_mask = _check_mask(mask, LEGACY_MASK_SEGMENTS).to_bytes(2, "little")
    return _mode_frame(_F.SubMode.color_rgbic_0b, lambda mp: _node(
        _F.ColorRgbic0bWide, mp, len(seg_mask), r=rgb[0], g=rgb[1], b=rgb[2], seg_mask=seg_mask))


# op15_color_write variants (the colour form the app's writer picks; not in the frame).
_OP15_RGB: Final = 12    # [r, g, b][00 x5][mask]                 Bytes.h (H60A6 family RGB)
_OP15_CCT: Final = 11    # [slot r, g, b][kelvin][tint][mask]     Bytes.r (H60A6 family CCT)
_OP15_V5: Final = 5      # [r, g, b][kelvin][mask]                Bytes.l / Bytes.u (colour version 5, H6061)


def _op15_color_frame(variant: int, rgb: tuple[int, int, int], seg_mask: bytes, kelvin: int = 0,
                      tint: tuple[int, int, int] = (0, 0, 0)) -> bytes:
    """``33 05 15 01`` with the spec's ``op15_color_write(variant, mask_width)``."""
    def data(c15: Any) -> Any:
        w = _node(_F.Op15ColorWrite, c15, variant, len(seg_mask), r=rgb[0], g=rgb[1], b=rgb[2])
        if variant == _OP15_RGB:
            w.reserved = bytes(5)   # contents [0] x5: Kaitai 0.11 writes a contents field only as given
        if variant in (_OP15_CCT, _OP15_V5):
            w.kelvin = kelvin
        if variant == _OP15_CCT:
            w.tint_r, w.tint_g, w.tint_b = tint
        w.seg_mask = seg_mask
        return w
    return _op15_frame(_F.Op15.set_color, data)


# ── Glide Hexa (H6061) whole-device writes ───────────────────────────────────
# Source (Govee Home 7.7.02, split pact_h6061 + base.apk): the panel count n is the size of the
# user's layout, else ColorPieceConfig.Companion.f(51) = Support.getBulbStringMaxNum(51) = 23.
H6061_MAX_PANELS: Final = 23
H6061_GROUP_SIZE: Final = 4          # Bytes.m / BleUtils.w0(colors, 4): four colours per group frame
H6061_MASK_BYTES: Final = 3          # ColorPieceConfig.k: piece count 23 rounds up to a 24-bit position set
OP15_SET_GROUP_COLOR: Final = int(_F.Op15.set_color_group)   # 0x04; Bytes.m: [0x15, 0x04, group, RGB x<=4]
H6061_KELVIN_RANGE: Final = (2000, 9000)   # KelvinConfig: no entry for 51 -> version 0

# Segment count a whole-device builder assumes when the caller passes none (``Device`` always passes
# the profile's count). ``h6061`` takes the app's own fallback above, the 23-panel layout; ``h60a6`` and
# ``h61a8`` take their namesake SKU's count (H60A6 13 segments, H61A8 15). The 0x0d schemes carry no mask.
_DEFAULT_SEGMENTS: Final[dict[ColorScheme, int]] = {"h60a6": 13, "h61a8": 15, "h6061": H6061_MAX_PANELS}


def _segments_or_default(scheme: ColorScheme, segments: int | None) -> int:
    """``segments``, or the scheme's default count when it is None (an explicit 0 stays 0)."""
    return segments if segments is not None else _DEFAULT_SEGMENTS.get(scheme, 0)


def _h6061_panels(panels: int) -> int:
    """The panel count the app writes for ``panels`` panels: 1..23.

    More than 23 is refused: ``ColorPieceConfig.Config.s0`` returns nothing for a colour set longer
    than ``ColorPieceConfig.j`` (23), so the app sends no colour at all. A single colour set with
    ``j`` > 1 is expanded to ``j`` colours in ``s0``, so a one-panel layout is written as 23 panels."""
    _check_range("panel count", panels, 1, H6061_MAX_PANELS)
    return H6061_MAX_PANELS if panels == 1 else panels


def _h6061_group_frames(r: int, g: int, b: int, panels: int) -> list[bytes]:
    """Whole-device RGB: ``33 05 15 04 <group from 1> R G B x<=4``, one frame per group of four
    panels, the last carrying only the remaining panels (23 panels: 6 frames, the last with 3
    colours). ``ColorModeControllerConfig.k`` -> ``w`` -> ``l`` (colour-brightness-group mode 1,
    ``Config.A[51]``) -> ``Bytes.m``."""
    n = _h6061_panels(panels)

    def group_frame(group: int) -> bytes:
        count = min(H6061_GROUP_SIZE, n - (group - 1) * H6061_GROUP_SIZE)
        g_obj = _node(_F.Op15ColorGroupWrite, None, count, group=group)
        g_obj.colors = [_node(_F.ScRgb, g_obj, r=r, g=g, b=b) for _ in range(count)]
        # op 0x04 has no case in the read dispatch (color_15.data is raw there): its spec body's bytes.
        return _op15_frame(_F.Op15.set_color_group, lambda c15: _body(g_obj, _PARAMS_LEN - 2))

    return [group_frame(group) for group in range(1, ceil_div(n, H6061_GROUP_SIZE) + 1)]


def _h6061_color_temp(kelvin: int, panels: int, tint: tuple[int, int, int] | None = None) -> bytes:
    """Whole-device CCT: ``33 05 15 01 <tint RGB> <kelvin u2be> <24-bit mask>``, the first ``panels``
    mask bits set, LSB first (23 panels: ``ff ff 7f``). ``Config.x[51]`` = 5 -> ``Config.J[5]`` =
    ``Config.Zt`` -> ``Bytes.u(kelvin, positions, tint)``; the mask is ``BleUtils.X`` over the
    ``ColorPieceConfig.k`` = 24-position set. The tint sits in the colour slot (not ``ff ff ff``).
    ``tint`` defaults to the app's tint for ``kelvin`` (the slider path); an RGB pick that is itself a
    tint colour passes that exact colour (``Zt`` writes the picked colour, whichever of the Kelvin
    step's two colours it is)."""
    n = _h6061_panels(panels)
    mask = all_segments_mask(n).to_bytes(H6061_MASK_BYTES, "little")
    return _op15_color_frame(_OP15_V5, tint if tint is not None else tint_for_kelvin(kelvin), mask, kelvin)


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
                         segments: int | None, mask: int | None = None) -> bytes:
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
    * ``h6061`` (``Config.Zt`` -> ``Bytes.u``): ``15 01 <picked RGB> <K> <24-bit mask>`` over the
      ``segments`` panels (whole device only)."""
    match scheme:
        case "h60a6":
            if mask is None:
                segments = _segments_or_default(scheme, segments)
                mask = all_segments_mask(segments)
            return segment_color_temp(mask, kelvin, scheme, segments, tint=(r, g, b))
        case "h6006" | "h6188":
            return _cct0d_frame((r, g, b), kelvin, tint_for_kelvin(kelvin))
        case "h6061":
            return _h6061_color_temp(kelvin, _segments_or_default(scheme, segments), (r, g, b))
        case "h61a8":   # rgb_as_kelvin never picks a Kelvin here
            raise GoveeBleNotSupported("h61a8 has no colour-temperature capability")
        case _:
            _unsupported_scheme(scheme)


# ── colour ───────────────────────────────────────────────────────────────────
def color_rgb_frames(r: int, g: int, b: int, scheme: ColorScheme, segments: int | None = None,
                     kelvin_range: tuple[int, int] | None = None) -> list[bytes]:
    """Whole-device RGB pick as the frames the app sends: one for every scheme except ``h6061``, whose
    panels are written in groups of four (see :func:`color_rgb`, which is the plain single-frame form).
    An RGB that is a Kelvin tint colour within ``kelvin_range`` (the SKU's range) is sent as one CCT
    frame instead, exactly as the app does, on every scheme with a colour-temperature mode (see
    :func:`rgb_as_kelvin` and :func:`_rgb_pick_color_temp`); ``kelvin_range`` None sends plain RGB
    (``h6061`` defaults to its own range). ``segments`` None is the scheme's default count
    (:data:`_DEFAULT_SEGMENTS`; 23 panels on ``h6061``, the app's fallback)."""
    _check_rgb(r, g, b)
    n = _segments_or_default(scheme, segments)
    kelvin = rgb_as_kelvin(r, g, b, scheme, kelvin_range)
    if kelvin is not None:
        return [_rgb_pick_color_temp(r, g, b, kelvin, scheme, n)]
    if scheme == "h6061":
        return _h6061_group_frames(r, g, b, n)
    return [color_rgb(r, g, b, scheme, n)]


def color_rgb(r: int, g: int, b: int, scheme: ColorScheme, segments: int | None = None) -> bytes:
    """Solid RGB, per-family SubModeColor.getWriteBytes (whole-device = all-segments mask;
    ``segments`` None is the scheme's default count, :data:`_DEFAULT_SEGMENTS`). ``h6061`` has no
    single-frame form (it needs :func:`color_rgb_frames`): GoveeBleNotSupported."""
    _check_rgb(r, g, b)
    match scheme:
        case "h60a6":
            n = _segments_or_default(scheme, segments)
            return _op15_color_frame(_OP15_RGB, (r, g, b), _mask_bytes(all_segments_mask(n), n))
        case "h6006" | "h6188":
            return _cct0d_frame((r, g, b))
        case "h61a8":
            return _rgbic0b_frame((r, g, b), all_segments_mask(_segments_or_default(scheme, segments)))
        case "h6061":
            raise GoveeBleNotSupported("h6061 whole-device RGB is several frames; use color_rgb_frames")
        case _:
            _unsupported_scheme(scheme)


def color_temp(kelvin: int, scheme: ColorScheme, segments: int | None = None) -> bytes:
    """Whole-device colour temperature, always with the app's Kelvin->RGB tint
    (``wire.cct.tint_for_kelvin``): ``h6006`` = ``0d <tint> <K> <tint>``, ``h6188`` =
    ``0d ff ff ff <K> <tint>``, ``h60a6`` = ``15 01 ff ff ff <K> <tint> <mask>`` (this is just
    segment_color_temp with the all-segments mask), ``h6061`` = ``15 01 <tint> <K> <24-bit mask>``
    over ``segments`` panels (``segments`` None is the scheme's default count,
    :data:`_DEFAULT_SEGMENTS`). ``h61a8`` has no colour temperature: GoveeBleNotSupported. The
    builder sends ``kelvin`` as given; the ``Device`` layer applies :func:`normalize_kelvin` first."""
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
            return _h6061_color_temp(kelvin, _segments_or_default(scheme, segments))
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
            return _op15_color_frame(_OP15_RGB, (r, g, b), _mask_bytes(mask, segments))
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
            return _op15_color_frame(_OP15_CCT, (0xFF, 0xFF, 0xFF), _mask_bytes(mask, segments), kelvin,
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
    """Per-segment brightness ``15 02 <pct> <mask>`` (``Bytes.G``; ``pct`` clamped to 0..100); only
    the 0x15 family has it (GoveeBleNotSupported elsewhere)."""
    match scheme:
        case "h60a6":
            level, seg_mask = _clamp_pct(pct), _mask_bytes(mask, segments)
            return _op15_frame(_F.Op15.set_brightness, lambda c15: _node(
                _F.Op15BrightnessWide, c15, len(seg_mask), pct=level, seg_mask=seg_mask))
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
    """33 30 <zoneIndex> <state> (H60A6 per-zone)."""
    _check_range("zone", zone, 0, 0xFF)
    body = _node(_F.ZonePowerWrite, None, zone=zone, state=1 if on else 0)
    return _write_frame(_F.Command.light_direction_or_zone, lambda sc: _body(body, _PARAMS_LEN))


def bar_switch(left: bool, right: bool) -> bytes:
    """33 36 <left> <right> (H6047 both bars in one frame)."""
    body = _node(_F.BarSwitchWrite, None, left=1 if left else 0, right=1 if right else 0)
    return _write_frame(_F.Command.compose_light_switch, lambda sc: _body(body, _PARAMS_LEN))


# ── scenes ───────────────────────────────────────────────────────────────────
def scene_activate(code: int) -> bytes:
    """33 05 04 <code u2le>."""
    _check_range("scene code", code, 0, 0xFFFF)
    return _mode_frame(_F.SubMode.scene, lambda mp: _node(_F.ScenePayload, mp, effect=code))


def scene_upload_a3(value: bytes, comm_byte: int) -> list[bytes]:
    """0xA3 scene/DIY upload, ``MultipleControllerCommV1.makeSendBytesV2`` with a one-byte
    ``commBytes`` (``makeSendBytesV1``): the stream ``[0x01 marker, packet_count, comm_byte, value…]``
    cut into 17-byte pieces, one per frame, seq 0, 1, …, the last 0xFF. START (seq 0) and END (0xFF)
    are always two frames: a value of 14 bytes or fewer rides whole in START, followed by an empty END
    (packet_count 2); a longer value's final chunk rides in END. ``packet_count`` counts every frame."""
    # makeSendBytesV2 always appends the separate END bArr2 and starts packetCount at 2, so even a
    # value that fits in START is two frames. That short-value START + empty END form is
    # source-modeled; not live-verified until checked on the H6006/H6008 bench.
    pieces = max(2, ceil_div(SCENE_A3_HEADER_LEN + len(value), SCENE_A3_PIECE_LEN))
    if pieces > 0xFF:
        # The count is one byte and seq 0xFF is the end marker.
        raise GoveeBleInvalidValue(f"scene payload too large for 0xA3 upload ({pieces} packets)")
    inline = SCENE_A3_PIECE_LEN - SCENE_A3_HEADER_LEN   # value bytes in START (a3_start.inline_value)

    def chunk(i: int) -> bytes:
        def body(f: Any) -> Any:
            m = _node(_F.MultiA3, f, seq_no=0xFF if i == pieces - 1 else i)
            if i == 0:
                m.frame = _node(_F.A3Start, m, marker=0x01, packet_count=pieces, comm_byte=comm_byte & 0xFF,
                                inline_value=value[:inline].ljust(inline, b"\0"))
            else:
                start = inline + (i - 1) * SCENE_A3_PIECE_LEN
                m.frame = value[start:start + SCENE_A3_PIECE_LEN].ljust(SCENE_A3_PIECE_LEN, b"\0")
            return m
        return _frame_of(_F.ProType.multi_write_v1, body)

    return [chunk(i) for i in range(pieces)]


# makeSendBytesMtu's mtuSize is @IntRange(from = 20, to = PlaybackStateCompat.X = 512).
_A4_MTU_MIN: Final = 20
_A4_MTU_MAX: Final = 512


def scene_upload_a4_mtu(value: bytes, comm_byte: int, *, mtu: int = 20) -> list[bytes]:
    """0xA4-MTU multi-packet upload (``MultipleControllerCommV1.makeSendBytesMtu``). START [A4 00 00 01
    cntLo cntHi comm value x (mtu-8)]; MIDDLE [A4 seqLo seqHi value x (mtu-4)] (1-based); END [A4 FF FF
    value] carries the final chunk, or nothing when the whole value fits in START. ``cnt`` counts every
    frame. Exact-length frames (BCC appended, not zero-padded). GoveeBleInvalidValue for an ``mtu``
    outside the app's 20..512 or a value too long for the u16 count (seq 0xFFFF is the END marker)."""
    _check_range("mtu", mtu, _A4_MTU_MIN, _A4_MTU_MAX)
    start_cap, mid_cap = mtu - 8, mtu - 4
    head, rest = value[:start_cap], value[start_cap:]
    total = 1 + max(1, ceil_div(len(rest), mid_cap))
    if total > 0xFFFF:
        raise GoveeBleInvalidValue(f"scene payload too large for 0xA4 upload ({total} packets)")

    chunks = [rest[i : i + mid_cap] for i in range(0, len(rest), mid_cap)] or [b""]

    def packet(seq_marker: int, chunk: bytes) -> bytes:
        def body(f: Any) -> Any:
            m = _node(_F.MultiA4, f, seq_marker=seq_marker)
            if seq_marker == 0:
                m.start = _node(_F.A4StartHead, m, marker=b"\x01", packet_count=total,
                                comm_byte=comm_byte & 0xFF)
            m.value = chunk
            return m
        # exact-length frame: proType, seq marker u2le, START's 4-byte head, the chunk, the checksum
        length = 1 + 2 + (4 if seq_marker == 0 else 0) + len(chunk) + 1
        return _frame_of(_F.ProType.multi_write_v2, body, length)

    return [packet(0, head)] + [packet(0xFFFF if seq == len(chunks) else seq, chunk)
                                for seq, chunk in enumerate(chunks, start=1)]


# ── plug / secret ────────────────────────────────────────────────────────────
def plug_sync_time(unix_ts: int, tz_hour: int, tz_min: int) -> bytes:
    """33 B5 <u4be epoch> 01 <s1 tz_hour> <s1 tz_min>."""
    ts = unix_ts & 0xFFFFFFFF
    return _write_frame(_F.Command.plug_sync_time, lambda sc: _node(
        _F.PlugSyncTimePayload, sc, unix_seconds=ts, marker=0x01, tz_hour=tz_hour, tz_min=tz_min))


SECRET_LEN: Final = 8


def secret_check(secret: bytes) -> bytes:
    """33 B2 <8-byte account-lock secret>."""
    if len(secret) != SECRET_LEN:
        raise GoveeBleInvalidValue(f"secret must be exactly {SECRET_LEN} bytes, got {len(secret)}")
    body = _node(_F.SecretKeyWrite, None, secret=bytes(secret))
    return _write_frame(_F.Command.secret_write, lambda sc: _body(body, _PARAMS_LEN))


# ── read requests (0xAA / 0xAC) ──────────────────────────────────────────────
def power_query() -> bytes:
    """aa 01: read the power / relay state (reply: parse_light_power on lights, parse_power's relay byte on
    plugs). No selector byte, as the app sends it (ControllerSwitch.Companion.b, ControllerSwitch.java:38-47;
    SwitchController / HeartGMessage on the plugs); no firmware image reads byte 2 of the request."""
    return _request(_F.Command.switch)


def brightness_query() -> bytes:
    """aa 04: read the brightness (reply: parse_brightness). No selector byte, as the app sends it
    (ControllerBrightness.Companion.b, ControllerBrightness.java:40-48)."""
    return _request(_F.Command.brightness)


def mode_query() -> bytes:
    """aa 05 01: read the mode report (reply: parse_active_scene / parse_kelvin / parse_mode_0d)."""
    return _request(_F.Command.mode, 0x01)


def bar_switch_query() -> bytes:
    """aa 36: read H6047's two bars' power (reply: parse_bar_switch)."""
    return _request(_F.Command.compose_light_switch)


def gradual(on: bool) -> bytes:
    """33 A3 <0/1>: gradual/fade on the BLE<->wifi control handoff (GRADUAL_CHANGE_WIFI_BLE)."""
    body = _node(_F.GradualWrite, None, state=1 if on else 0)
    return _write_frame(_F.Command.gradual_wifi_ble, lambda sc: _body(body, _PARAMS_LEN))


def gradual_query() -> bytes:
    """AA A3: read the gradual flag (reply = gradual_read, state @ byte0)."""
    return _request(_F.Command.gradual_wifi_ble)


def secret_read() -> bytes:
    """aa b1: read the 8-byte account-lock secret (reply: parse_secret)."""
    return _request(_F.Command.secret_read)


def plug_spec_query() -> bytes:
    """aa b3: read the plug's spec byte (reply: parse_plug_spec). Spec surface: Device does not send it."""
    return _request(_F.Command.plug_spec)


CMD_BULB_COLOR_V1: Final = int(_F.Command.bulb_string_color_read)   # 0xA2 (mechanism-B V1: colour only)
CMD_BULB_COLOR_V2: Final = int(_F.Command.local_color_read)         # 0xA5 (mechanism-B V2: + brightness)


def bulb_group_query(batch_seq: int, *, v2: bool = True) -> bytes:
    """Mechanism-B per-group colour read request: ``AA <A5|A2> <batch_seq>`` (1-based
    batch number, AbsSingleController.p). V2 (0xA5) carries per-segment brightness.

    Device always sends V2; ``v2=False`` (the V1 ``aa a2`` read, BulbStringColorController) is
    spec surface for tools and tests."""
    command = _F.Command.local_color_read if v2 else _F.Command.bulb_string_color_read
    return _request(command, batch_seq & 0xFF)


def device_info_query(selector: int) -> bytes:
    """aa 07 <selector> (0x02 sn / device id, 0x03 hard version, 0x10 basic, 0x11 wifi)."""
    return _request(_F.Command.device_info, selector & 0xFF)


def soft_version_query() -> bytes:
    """aa 06: read the firmware (soft) version (SoftVersionController; the reply is ASCII)."""
    return _request(_F.Command.soft_version)


CMD_IC_NUM: Final = int(_F.Command.ic_num)   # 0x40 (ic_segment_read reply): live IC/segment capability read


def ic_count_query() -> bytes:
    """aa 40: read the live IC (lamp-bead) count (ControllerOnlyReadIcSegmentNum; reply:
    parse_ic_count). The app does not use the reply's third byte for H6641: the piece and group
    counts are derived client-side from the IC count, as the app does
    (devices/_readback.live_ic_pieces: ceil(clamp(IC, 41, 70) / 5) pieces, ceil(pieces / 4) groups)."""
    return _request(_F.Command.ic_num)


# 0x41: striplight SINGLE_IC_SEGMENT_NUM (H6062); the shared enum names it on_off_memory, its meaning on
# the other SKUs.
CMD_SECTION_COUNT: Final = int(_F.Command.on_off_memory)


def section_count_query() -> bytes:
    """aa 41: read the H6062's ``[IC count s16be, sectionNum u8]`` (``striplight``
    ``ControllerIcSegmentNum``, ``BleProtocol.SINGLE_IC_SEGMENT_NUM`` = 65). ``sectionNum`` is
    the segment count the app uses for every mask and the ``aa a5`` group walk. Not the
    ``aa 40`` read of :func:`ic_count_query` (``SINGLE_IC_NUM_AND_PIECE_NUM`` = 64), from whose IC
    count the app derives the pieces client-side instead."""
    return _request(_F.Command.on_off_memory)


def status_query(*, full: bool = False) -> bytes:
    """0xAC status request: [AC, 03, N, cmd…]. full = dual-zone (adds 0xA5).

    Device always sends ``full=True``; the single-zone ``ac 03 02 41 30`` (``full=False``) is spec
    surface for tools and tests."""
    types = [0x41, 0x30, 0xA5] if full else [0x41, 0x30]
    return _frame_of(_F.ProType.multi_reply_read, lambda f: _node(
        _F.MultiAc, f, command=0x03, count=len(types), requested_types=types,
        pad=bytes(_BODY_LEN - 2 - len(types))))
