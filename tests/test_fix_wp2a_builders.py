"""wire.build colour builders: typed refusals, exhaustive scheme dispatch, defaults, input checks,
and the library's frames checked byte for byte against the independent ``tests/spec_frames``."""
from __future__ import annotations

import base64
from collections.abc import Callable
from typing import get_args

import pytest
import spec_frames as sf
from fake_ble import frame

from govee_ble_local.exceptions import GoveeBleError, GoveeBleInvalidValue, GoveeBleNotSupported
from govee_ble_local.wire import build as b
from govee_ble_local.wire.cct import tint_for_kelvin

ALL_SCHEMES: tuple[b.ColorScheme, ...] = ("h60a6", "h6006", "h61a8", "h6188", "h6061")


# ── a scheme that can't express the request: GoveeBleNotSupported, not a bare ValueError ──
@pytest.mark.parametrize(("call", "match"), [
    (lambda: b.color_rgb(1, 2, 3, "h6061", 23), "color_rgb_frames"),
    (lambda: b.color_temp(4000, "h61a8", 15), "h61a8"),
    (lambda: b.segment_rgb(1, 1, 2, 3, "h6061", 23), "whole-device only"),
    (lambda: b.segment_color_temp(1, 4000, "h61a8", 15), "h61a8"),
    (lambda: b.segment_color_temp(1, 4000, "h6006"), "h6006"),
    (lambda: b.segment_color_temp(1, 4000, "h6188"), "h6188"),
    (lambda: b.segment_color_temp(1, 4000, "h6061", 23), "whole-device only"),
    (lambda: b.segment_brightness(1, 50, "h61a8", 15), "h61a8"),
    (lambda: b.segment_brightness(1, 50, "h6061", 23), "whole-device only"),
])
def test_scheme_limits_raise_not_supported(call: Callable[[], bytes], match: str) -> None:
    with pytest.raises(GoveeBleNotSupported, match=match) as info:
        call()
    assert not isinstance(info.value, ValueError)   # no longer conflated with bad input
    assert isinstance(info.value, GoveeBleError)


# ── the 0x0d family has no per-segment frame (Config.K[2] drops the position set) ──
@pytest.mark.parametrize("scheme", ["h6006", "h6188"])
def test_0d_schemes_refuse_segment_rgb_and_brightness(scheme: b.ColorScheme) -> None:
    # Used to build a 0x15 RGBIC frame (33 05 15 01 ... / 33 05 15 02 ...) for a 0x0d bulb.
    with pytest.raises(GoveeBleNotSupported, match=f"{scheme}.*whole-device only"):
        b.segment_rgb(0b11, 1, 2, 3, scheme)
    with pytest.raises(GoveeBleNotSupported, match=f"{scheme}.*whole-device only"):
        b.segment_rgb(0b11, 0xFF, 0xAE, 0x54, scheme, 13, (2000, 9000))   # a tint colour, too
    with pytest.raises(GoveeBleNotSupported, match=f"{scheme}.*per-segment brightness"):
        b.segment_brightness(0b11, 50, scheme)


def test_bad_input_is_still_invalid_value_before_the_scheme_check() -> None:
    with pytest.raises(GoveeBleInvalidValue, match="green"):
        b.segment_rgb(1, 0, 256, 0, "h6006")


def test_unknown_scheme_is_refused_not_sent_as_h60a6() -> None:
    # A string outside the ColorScheme Literal used to fall through to the 0x15 frame.
    bogus = "h9999"
    calls: list[Callable[[], object]] = [
        lambda: b.color_rgb(1, 2, 3, bogus),                     # type: ignore[arg-type]
        lambda: b.color_temp(4000, bogus),                       # type: ignore[arg-type]
        lambda: b.segment_rgb(1, 1, 2, 3, bogus),                # type: ignore[arg-type]
        lambda: b.segment_color_temp(1, 4000, bogus),            # type: ignore[arg-type]
        lambda: b.segment_brightness(1, 50, bogus),              # type: ignore[arg-type]
        lambda: b.color_rgb_frames(1, 2, 3, bogus),              # type: ignore[arg-type]
    ]
    for call in calls:
        with pytest.raises(GoveeBleNotSupported, match="unknown colour scheme"):
            call()


# ── every builder x every scheme: its own family's frame, or GoveeBleNotSupported ──
# The colour sub-mode each scheme's frames use (33 05 <sub> ...); nothing may fall through to another's.
_FAMILY: dict[b.ColorScheme, int] = {
    "h60a6": b.SUB_COLOR_15, "h6061": b.SUB_COLOR_15, "h61a8": b.SUB_COLOR_0B,
    "h6006": b.SUB_COLOR_0D, "h6188": b.SUB_COLOR_0D,
}
_TINT_2700 = (0xFF, 0xAE, 0x54)   # a Kelvin tint colour: the CCT-pick path on every scheme with CCT
# builder -> (call, the schemes that can express it)
_BUILDERS: dict[str, tuple[Callable[[b.ColorScheme], bytes | list[bytes]], frozenset[str]]] = {
    "color_rgb_frames": (lambda s: b.color_rgb_frames(1, 2, 3, s), frozenset(ALL_SCHEMES)),
    "color_rgb_frames tint pick": (lambda s: b.color_rgb_frames(*_TINT_2700, s, None, (2000, 9000)),
                                   frozenset(ALL_SCHEMES)),
    "color_rgb": (lambda s: b.color_rgb(1, 2, 3, s), frozenset(ALL_SCHEMES) - {"h6061"}),
    "color_temp": (lambda s: b.color_temp(4000, s), frozenset(ALL_SCHEMES) - {"h61a8"}),
    "segment_rgb": (lambda s: b.segment_rgb(1, 1, 2, 3, s), frozenset({"h60a6", "h61a8"})),
    "segment_rgb tint pick": (lambda s: b.segment_rgb(1, *_TINT_2700, s, 13, (2000, 9000)),
                              frozenset({"h60a6", "h61a8"})),
    "segment_color_temp": (lambda s: b.segment_color_temp(1, 4000, s), frozenset({"h60a6"})),
    "segment_brightness": (lambda s: b.segment_brightness(1, 50, s), frozenset({"h60a6"})),
}


def test_all_schemes_is_the_color_scheme_literal() -> None:
    # A scheme added to the Literal must be added to the matrix below, too.
    assert set(ALL_SCHEMES) == set(get_args(b.ColorScheme)) == set(_FAMILY)


@pytest.mark.parametrize("scheme", ALL_SCHEMES)
@pytest.mark.parametrize("builder", sorted(_BUILDERS))
def test_every_builder_sends_its_own_family_or_refuses(builder: str, scheme: b.ColorScheme) -> None:
    call, supported = _BUILDERS[builder]
    if scheme not in supported:
        with pytest.raises(GoveeBleNotSupported, match=scheme) as info:
            call(scheme)
        assert not isinstance(info.value, ValueError)   # never a bare ValueError the Device must relabel
        return
    out = call(scheme)
    frames = out if isinstance(out, list) else [out]
    assert frames
    for f in frames:
        assert f[:3] == bytes([b.PRO_WRITE, b.CMD_MODE, _FAMILY[scheme]]), (builder, scheme, f.hex())


# ── brightness: a non-int is GoveeBleInvalidValue, not a TypeError from bytes() ──
@pytest.mark.parametrize("bad", [50.5, "50", None, True])
def test_brightness_type_checked(bad: object) -> None:
    with pytest.raises(GoveeBleInvalidValue, match="brightness"):
        b.brightness(bad)  # type: ignore[arg-type]
    with pytest.raises(GoveeBleInvalidValue, match="brightness"):
        b.segment_brightness(1, bad, "h60a6", 13)  # type: ignore[arg-type]


def test_brightness_ints_still_clamp() -> None:
    assert b.brightness(-5)[2] == 0 and b.brightness(55)[2] == 55 and b.brightness(500)[2] == 100
    assert b.segment_brightness(1, 500, "h60a6", 13)[4] == 100


# ── segment-count defaults: per scheme, never another SKU's count ────────────
def test_h6061_default_is_the_apps_23_panels() -> None:
    # Used to be H60A6's 13: a 13-panel mask ff 1f 00 the app never sends.
    assert b.color_temp(4000, "h6061") == b.color_temp(4000, "h6061", 23)
    assert b.color_temp(4000, "h6061")[9:12] == bytes([0xFF, 0xFF, 0x7F])
    assert b.color_rgb_frames(1, 2, 3, "h6061") == b.color_rgb_frames(1, 2, 3, "h6061", 23)
    assert len(b.color_rgb_frames(1, 2, 3, "h6061")) == 6


def test_h61a8_default_is_its_15_segments() -> None:
    assert b.color_rgb(1, 2, 3, "h61a8") == b.color_rgb(1, 2, 3, "h61a8", 15)
    assert b.color_rgb(1, 2, 3, "h61a8")[6:8] == bytes([0xFF, 0x7F])


def test_h60a6_default_unchanged() -> None:
    assert b.color_rgb(1, 2, 3, "h60a6") == b.color_rgb(1, 2, 3, "h60a6", 13)
    assert b.color_temp(3000, "h60a6") == b.color_temp(3000, "h60a6", 13)


def test_h6061_explicit_zero_panels_rejected_on_every_path() -> None:
    # `segments or 23` used to turn an explicit 0 into 23 panels, but only for a tint-colour pick.
    for rgb in ((1, 2, 3), (0xFF, 0xAE, 0x54)):                  # plain RGB, then the 2700 K tint
        with pytest.raises(GoveeBleInvalidValue, match="panel count"):
            b.color_rgb_frames(*rgb, "h6061", 0)
    with pytest.raises(GoveeBleInvalidValue, match="panel count"):
        b.color_temp(3000, "h6061", 0)


def test_rgb_pick_as_kelvin_masks_unchanged() -> None:
    # The pick helper now builds the whole-device mask itself; the bytes are the same frames.
    k = b.rgb_as_kelvin(0xFF, 0xAE, 0x54, "h60a6", (2000, 9000))
    assert k == 2700
    assert b.color_rgb_frames(0xFF, 0xAE, 0x54, "h60a6", 13, (2000, 9000)) == [
        b.segment_color_temp((1 << 13) - 1, 2700, "h60a6", 13, tint=(0xFF, 0xAE, 0x54))]
    assert b.segment_rgb(0x5, 0xFF, 0xAE, 0x54, "h60a6", 13, (2000, 9000)) == b.segment_color_temp(
        0x5, 2700, "h60a6", 13, tint=(0xFF, 0xAE, 0x54))
    for scheme in ("h6006", "h6188"):
        f = b.color_rgb_frames(0xFF, 0xA9, 0x57, scheme, None, (2000, 9000))
        assert f == [frame(0x33, 0x05, 0x0D, 0xFF, 0xA9, 0x57, 0x0A, 0x8C, *tint_for_kelvin(2700))]


def test_status_query_full_is_keyword_only() -> None:
    assert b.status_query(full=True) == sf.status_query(True)
    with pytest.raises(TypeError):
        b.status_query(True)  # type: ignore[misc]


# ── the library matches the independent spec builders byte for byte ──────────
_PARAM = (
    "UCABAQEBAAGyAAOlAAYACgAA/zoACwwNDjc4OTo7HgBW/kEBCg8QERITMjM0NTY8PT4/"
    "QFlaW1xdXl9gYXR1dncPAKz+SGJzeHl6e3x9fn+AgYKDhCYA3/tFAgMICRQVFhcYGSwt"
    "Li8wMUFCQ0RFRlNUVVZXWGNkZWZnbm9wcXIeAO/2NwQHGhscHR4nKCkqK0dISUpLTE1O"
    "T1BRUmhpamtsbQoA//IpBQYfICEiIyQlJgIAZAEQJwAAAAA="
)


@pytest.mark.parametrize(("spec", "lib"), [
    (lambda: sf.power(True), lambda: b.switch(True)),
    (lambda: sf.power(False), lambda: b.switch(False)),
    (lambda: sf.brightness(0), lambda: b.brightness(0)),
    (lambda: sf.brightness(75), lambda: b.brightness(75)),
    (lambda: sf.brightness(999), lambda: b.brightness(999)),
    (lambda: sf.zone_power(1, True), lambda: b.zone_power(1, True)),
    (lambda: sf.zone_power(0, False), lambda: b.zone_power(0, False)),
    (lambda: sf.plug_sync_time(0x6A49901A, -7, 0), lambda: b.plug_sync_time(0x6A49901A, -7, 0)),
    (lambda: sf.plug_sync_time(0x6A49901A, -2, -30), lambda: b.plug_sync_time(0x6A49901A, -2, -30)),
    (lambda: sf.scene_activate(0x4AD6), lambda: b.scene_activate(0x4AD6)),
    (lambda: sf.color_rgb_15(0x11, 0x22, 0x33, sf.all_segments_mask(13)),
     lambda: b.color_rgb(0x11, 0x22, 0x33, "h60a6", 13)),
    (lambda: sf.color_rgb_15(1, 2, 3, 0x0004), lambda: b.segment_rgb(0x0004, 1, 2, 3, "h60a6")),
    (lambda: sf.color_temp_15(4000, tint_for_kelvin(4000), sf.all_segments_mask(13)),
     lambda: b.color_temp(4000, "h60a6", 13)),
    (lambda: sf.color_temp_15(2700, tint_for_kelvin(2700), 0x0007),
     lambda: b.segment_color_temp(0x0007, 2700, "h60a6")),
    (lambda: sf.segment_brightness_15(75, 0x0004), lambda: b.segment_brightness(0x0004, 75, "h60a6")),
    (lambda: sf.status_query(False), lambda: b.status_query()),
    (lambda: sf.status_query(True), lambda: b.status_query(full=True)),
])
def test_library_matches_spec_builders(spec: Callable[[], bytes], lib: Callable[[], bytes]) -> None:
    assert lib() == spec()


def test_library_a3_upload_matches_spec_builder() -> None:
    value = base64.b64decode(_PARAM)[1:]
    assert b.scene_upload_a3(value, b.COMM_H60A6) == sf.scene_upload(_PARAM, comm_byte=sf.COMM_H60A6)
