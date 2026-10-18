"""Test helpers: read a frame with the spec's generated classes, and decode an aa 05 mode report the way a
profile's ``mode_color_parse`` does (``devices.profile.wire_profile`` + ``wire.parse.mode_color``)."""
from __future__ import annotations

from typing import Any

from govee_ble_local.devices.profile import DeviceProfile, wire_profile
from govee_ble_local.wire import _spec, parse


def tx(frame: bytes, **facts: Any) -> Any:
    """``frame`` read on its TX route under a profile of ``facts`` (``_spec.profile`` keywords)."""
    return _spec.read_frame(frame, _spec.TX, _spec.profile(**facts))


def rx(frame: bytes, **facts: Any) -> Any:
    """``frame`` read on its RX route under a profile of ``facts`` (``_spec.profile`` keywords)."""
    return _spec.read_frame(frame, _spec.RX, _spec.profile(**facts))


def mode_profile(color_parse: str = "rgb_kelvin", *, bar_switch: bool = False) -> Any:
    """The wire profile of a device on ``color_parse`` (only the mode decode matters to these reads)."""
    return wire_profile(DeviceProfile(skus=("TEST",), capabilities=frozenset(), bar_switch=bar_switch,
                                      mode_color_parse=color_parse))  # type: ignore[arg-type]


def mode_color(frame: bytes, color_parse: str = "rgb_kelvin", *, bar_switch: bool = False) -> Any:
    """The colour an aa 05 reply carries under ``color_parse`` (:func:`parse.mode_color`), or None."""
    m = parse.mode_reply(frame, mode_profile(color_parse, bar_switch=bar_switch))
    return None if m is None else parse.mode_color(m, unsigned_kelvin=color_parse == "rgb_kelvin")
