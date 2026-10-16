"""Frame builders — the write side, organized by the ksy command/sub-mode/op15 enums.

Kaitai generates readers only, so these encode the layouts by hand; every builder is
round-trip-proven against the generated reader (see tests/test_wire_build.py: build ->
parse -> assert). Byte layouts follow the spec and the decompiled app (each builder cites its
source). Not all of them are hardware-verified: the device profiles note which SKUs are, and the
short-value 0xA3 upload (:func:`scene_upload_a3`) is source-modeled from
``MultipleControllerCommV1.makeSendBytesV2``, not live-verified. Scene uploads take the raw
``value`` bytes (the device layer base64-decodes + strips the param).

A builder raises GoveeBleNotSupported when the colour scheme cannot express the request (no such
frame in the app for that family) and GoveeBleInvalidValue for bad caller input.
"""
from __future__ import annotations

from typing import Any, Final, Literal, Never

from .._generated.govee_ble_frame import GoveeBleFrame as _GBF  # type: ignore[attr-defined]
from ..const import FRAME_LEN, SCENE_A3_HEADER_LEN, SCENE_A3_PIECE_LEN
from ..crypto import checksum
from ..exceptions import GoveeBleInvalidValue, GoveeBleNotSupported
from .cct import kelvin_for_tint, tint_for_kelvin

# The spec-generated reader (spec/govee_ble.ksy). It is untyped, so it is Any here. This is the wire
# layer's one import of it: the protocol constants below are its enums, and :mod:`.parse`,
# :mod:`.reassemble` and :mod:`.describe` reuse this name to read frames with it.
_F: Any = _GBF


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
RELAY_ON: Final = 0x11    # plug family
RELAY_OFF: Final = 0x10

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


def _mask_le(mask: int) -> tuple[int, int]:
    """Two-byte little-endian mask (the fixed 0x0b layout, and every <=16-segment 0x15 mask)."""
    return mask & 0xFF, (mask >> 8) & 0xFF


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
    """33 01 <state>. relay=True for the plug family (0x11/0x10)."""
    val = (RELAY_ON if on else RELAY_OFF) if relay else (POWER_ON if on else POWER_OFF)
    return frame(PRO_WRITE, CMD_SWITCH, val)


def brightness(pct: int) -> bytes:
    """33 04 <level 0-100> (clamped). Raises GoveeBleInvalidValue for a non-int."""
    return frame(PRO_WRITE, CMD_BRIGHTNESS, _clamp_pct(pct))


def brightness_raw(level: int) -> bytes:
    """33 04 <raw level 0-255>, for devices whose brightness byte is not a percent
    (ControllerBrightness(i) writes the byte verbatim; e.g. H6188's 20..254 range)."""
    return frame(PRO_WRITE, CMD_BRIGHTNESS, _check_range("brightness level", level, 0, 0xFF))


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
    return [
        frame(PRO_WRITE, CMD_MODE, SUB_COLOR_15, OP15_SET_GROUP_COLOR, group,
              *(r, g, b) * min(H6061_GROUP_SIZE, n - (group - 1) * H6061_GROUP_SIZE))
        for group in range(1, ceil_div(n, H6061_GROUP_SIZE) + 1)
    ]


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
    return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_15, OP15_SET_COLOR,
                 *(tint if tint is not None else tint_for_kelvin(kelvin)), *kelvin.to_bytes(2, "big"), *mask)


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
            return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_0D, r, g, b, *kelvin.to_bytes(2, "big"),
                         *tint_for_kelvin(kelvin))
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
            return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_15, OP15_SET_COLOR, r, g, b, 0, 0, 0, 0, 0,
                         *_mask_bytes(all_segments_mask(n), n))
        case "h6006" | "h6188":
            return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_0D, r, g, b, 0, 0, 0, 0, 0)
        case "h61a8":
            mask = all_segments_mask(_segments_or_default(scheme, segments))
            return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_0B, r, g, b,
                         *_mask_le(_check_mask(mask, LEGACY_MASK_SEGMENTS)))
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
            return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_0D, *tint, *kelvin.to_bytes(2, "big"), *tint)
        case "h6188":
            # Config.GM -> Bytes.A(kelvin, RGB_WHITE): [0d, ff ff ff, kelvin u2be, tint r g b].
            return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_0D, 0xFF, 0xFF, 0xFF, *kelvin.to_bytes(2, "big"),
                         *tint_for_kelvin(kelvin))
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
            return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_15, OP15_SET_COLOR, r, g, b, 0, 0, 0, 0, 0,
                         *_mask_bytes(mask, segments))
        case "h61a8":
            return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_0B, r, g, b,
                         *_mask_le(_check_mask(mask, LEGACY_MASK_SEGMENTS)))
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
            mask_bytes = _mask_bytes(mask, segments)
            return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_15, OP15_SET_COLOR,
                         0xFF, 0xFF, 0xFF, *kelvin.to_bytes(2, "big"),
                         *(tint if tint is not None else tint_for_kelvin(kelvin)), *mask_bytes)
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
            return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_15, OP15_SET_BRIGHTNESS, _clamp_pct(pct),
                         *_mask_bytes(mask, segments))
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
    return frame(PRO_WRITE, CMD_ZONE, zone, 1 if on else 0)


def bar_switch(left: bool, right: bool) -> bytes:
    """33 36 <left> <right> (H6047 both bars in one frame)."""
    return frame(PRO_WRITE, CMD_BAR_SWITCH, 1 if left else 0, 1 if right else 0)


# ── scenes ───────────────────────────────────────────────────────────────────
def scene_activate(code: int) -> bytes:
    """33 05 04 <code u2le>."""
    _check_range("scene code", code, 0, 0xFFFF)
    return frame(PRO_WRITE, CMD_MODE, SUB_SCENE, *code.to_bytes(2, "little"))


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
    stream = bytes([0x01, pieces, comm_byte & 0xFF]) + value
    out: list[bytes] = []
    for i in range(pieces):
        piece = stream[i * SCENE_A3_PIECE_LEN : (i + 1) * SCENE_A3_PIECE_LEN]
        seq = 0xFF if i == pieces - 1 else i
        out.append(frame(PRO_MULTI_A3, seq, *piece))
    return out


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

    def _with_bcc(body: bytes) -> bytes:
        return body + bytes([checksum(body)])

    chunks = [rest[i : i + mid_cap] for i in range(0, len(rest), mid_cap)] or [b""]
    out = [_with_bcc(bytes([PRO_MULTI_A4, 0x00, 0x00, 0x01, *total.to_bytes(2, "little"), comm_byte & 0xFF])
                     + head)]
    for seq, chunk in enumerate(chunks, start=1):
        marker = b"\xff\xff" if seq == len(chunks) else seq.to_bytes(2, "little")
        out.append(_with_bcc(bytes([PRO_MULTI_A4]) + marker + chunk))
    return out


# ── plug / secret ────────────────────────────────────────────────────────────
def plug_sync_time(unix_ts: int, tz_hour: int, tz_min: int) -> bytes:
    """33 B5 <u4be epoch> 01 <s1 tz_hour> <s1 tz_min>."""
    ts = unix_ts & 0xFFFFFFFF
    return frame(PRO_WRITE, CMD_PLUG_SYNC_TIME, *ts.to_bytes(4, "big"),
                 0x01, tz_hour & 0xFF, tz_min & 0xFF)


SECRET_LEN: Final = 8


def secret_check(secret: bytes) -> bytes:
    """33 B2 <8-byte account-lock secret>."""
    if len(secret) != SECRET_LEN:
        raise GoveeBleInvalidValue(f"secret must be exactly {SECRET_LEN} bytes, got {len(secret)}")
    return frame(PRO_WRITE, CMD_SECRET_WRITE, *secret)


# ── read requests (0xAA / 0xAC) ──────────────────────────────────────────────
def power_query() -> bytes:
    """aa 01 01: read the power / relay state (reply: parse_power)."""
    return frame(PRO_READ, CMD_SWITCH, 0x01)


def brightness_query() -> bytes:
    """aa 04 01: read the brightness (reply: parse_brightness)."""
    return frame(PRO_READ, CMD_BRIGHTNESS, 0x01)


def mode_query() -> bytes:
    """aa 05 01: read the mode report (reply: parse_active_scene / parse_kelvin / parse_mode_0d)."""
    return frame(PRO_READ, CMD_MODE, 0x01)


def bar_switch_query() -> bytes:
    """aa 36: read H6047's two bars' power (reply: parse_bar_switch)."""
    return frame(PRO_READ, CMD_BAR_SWITCH)


def gradual(on: bool) -> bytes:
    """33 A3 <0/1>: gradual/fade on the BLE<->wifi control handoff (GRADUAL_CHANGE_WIFI_BLE)."""
    return frame(PRO_WRITE, CMD_GRADUAL_WIFI_BLE, 1 if on else 0)


def gradual_query() -> bytes:
    """AA A3: read the gradual flag (reply = gradual_read, state @ byte0)."""
    return frame(PRO_READ, CMD_GRADUAL_WIFI_BLE)


def secret_read() -> bytes:
    """aa b1: read the 8-byte account-lock secret (reply: parse_secret)."""
    return frame(PRO_READ, CMD_SECRET_READ)


def plug_spec_query() -> bytes:
    """aa b3: read the plug's spec byte (reply: parse_plug_spec). Spec surface: Device does not send it."""
    return frame(PRO_READ, CMD_PLUG_SPEC)


CMD_BULB_COLOR_V1: Final = int(_F.Command.bulb_string_color_read)   # 0xA2 (mechanism-B V1: colour only)
CMD_BULB_COLOR_V2: Final = int(_F.Command.local_color_read)         # 0xA5 (mechanism-B V2: + brightness)


def bulb_group_query(batch_seq: int, *, v2: bool = True) -> bytes:
    """Mechanism-B per-group colour read request: ``AA <A5|A2> <batch_seq>`` (1-based
    batch number, AbsSingleController.p). V2 (0xA5) carries per-segment brightness.

    Device always sends V2; ``v2=False`` (the V1 ``aa a2`` read, BulbStringColorController) is
    spec surface for tools and tests."""
    return frame(PRO_READ, CMD_BULB_COLOR_V2 if v2 else CMD_BULB_COLOR_V1, batch_seq & 0xFF)


def device_info_query(selector: int) -> bytes:
    """aa 07 <selector> (0x02 sn / device id, 0x03 hard version, 0x10 basic, 0x11 wifi)."""
    return frame(PRO_READ, CMD_DEVICE_INFO, selector & 0xFF)


def soft_version_query() -> bytes:
    """aa 06: read the firmware (soft) version (SoftVersionController; the reply is ASCII)."""
    return frame(PRO_READ, CMD_SOFT_VERSION)


CMD_IC_NUM: Final = int(_F.Command.ic_num)   # 0x40 (ic_segment_read reply): live IC/segment capability read


def ic_count_query() -> bytes:
    """aa 40: read the live IC (lamp-bead) count (ControllerOnlyReadIcSegmentNum; reply:
    parse_ic_count). The app does not use the reply's third byte for H6641: the piece and group
    counts are derived client-side from the IC count, as the app does
    (devices/_readback.live_ic_pieces: ceil(clamp(IC, 41, 70) / 5) pieces, ceil(pieces / 4) groups)."""
    return frame(PRO_READ, CMD_IC_NUM)


# 0x41: striplight SINGLE_IC_SEGMENT_NUM (H6062); the shared enum names it on_off_memory, its meaning on
# the other SKUs.
CMD_SECTION_COUNT: Final = int(_F.Command.on_off_memory)


def section_count_query() -> bytes:
    """aa 41: read the H6062's ``[IC count s16be, sectionNum u8]`` (``striplight``
    ``ControllerIcSegmentNum``, ``BleProtocol.SINGLE_IC_SEGMENT_NUM`` = 65). ``sectionNum`` is
    the segment count the app uses for every mask and the ``aa a5`` group walk. Not the
    ``aa 40`` read of :func:`ic_count_query` (``SINGLE_IC_NUM_AND_PIECE_NUM`` = 64), from whose IC
    count the app derives the pieces client-side instead."""
    return frame(PRO_READ, CMD_SECTION_COUNT)


def status_query(*, full: bool = False) -> bytes:
    """0xAC status request: [AC, 03, N, cmd…]. full = dual-zone (adds 0xA5).

    Device always sends ``full=True``; the single-zone ``ac 03 02 41 30`` (``full=False``) is spec
    surface for tools and tests."""
    if full:
        return frame(PRO_MULTI_AC, 0x03, 0x03, 0x41, 0x30, 0xA5)
    return frame(PRO_MULTI_AC, 0x03, 0x02, 0x41, 0x30)
