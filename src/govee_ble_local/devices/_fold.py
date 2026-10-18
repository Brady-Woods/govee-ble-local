"""How a write or a read-back changes a device's :class:`~..models.DeviceState`.

Pure functions over a :class:`~.profile.DeviceProfile` and the state they change in place: no I/O,
no locks and no stale-read check. :class:`~.device.Device` calls them once a write has gone out
(``Device._commit``) or a reply has passed the stale-read check (``Device._read_fresh``), so they can
be exercised with a bare ``DeviceState``.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..models import DeviceState, Segment
from ..wire import build, parse, reassemble
from .brightness import raw_to_percent
from .profile import DeviceProfile, wire_profile


def kelvin_range(profile: DeviceProfile) -> tuple[int, int] | None:
    """The profile's inclusive Kelvin range, or None when it leaves either end open."""
    lo, hi = profile.min_kelvin, profile.max_kelvin
    return (lo, hi) if lo is not None and hi is not None else None


def brightness_pct(profile: DeviceProfile, raw: int) -> int:
    """The device-reported brightness byte as the 0..100 percent ``state`` documents: clamped
    on every path, so a misbehaving device can't push e.g. 255 % into state."""
    if profile.brightness_range is not None:
        return raw_to_percent(raw, *profile.brightness_range)
    full = profile.brightness_max
    pct = raw if full == 100 else round(raw * 100 / full)
    return max(0, min(100, pct))


# -- writes -----------------------------------------------------------------
def apply_power(profile: DeviceProfile, state: DeviceState, on: bool) -> None:
    """Whole-device power, and the zones it drives."""
    state.is_on = on
    if profile.bar_switch and on:
        # 33 01 on: the app does not say which bars light (it learns that from the next aa 36
        # heartbeat), so don't guess; the next zone write re-reads them rather than assuming.
        state.zone_power = {}
    elif profile.zones:
        state.zone_power = {z.power_index: on for z in profile.zones}


def apply_zone_power(profile: DeviceProfile, state: DeviceState, zone_power: dict[int, bool]) -> None:
    """A zone power write: the zones' power (a new dict, as every writer of ``zone_power`` assigns)
    and, once every zone is known, the whole-device on/off (on while any zone is)."""
    state.zone_power = zone_power
    if profile.zones and len(zone_power) == len(profile.zones):
        state.is_on = any(zone_power.values())


def _remember_kelvin(state: DeviceState) -> None:
    """Keep ``last_color_temp_kelvin`` as the app keeps its Kelvin: it follows ``color_temp_kelvin``
    whenever that is a colour temperature (> 0), and nothing that clears the mode clears it."""
    if state.color_temp_kelvin is not None and state.color_temp_kelvin > 0:
        state.last_color_temp_kelvin = state.color_temp_kelvin


def apply_rgb(profile: DeviceProfile, state: DeviceState, rgb: tuple[int, int, int]) -> None:
    """A whole-device RGB write: the colour, or - when the app sends it as a colour temperature
    (``build.rgb_as_kelvin``) - that Kelvin, which is what the device reports (and what the app
    syncs to its Kelvin slider; both bars of a bar-switch device)."""
    kelvin = build.rgb_as_kelvin(*rgb, profile.color_scheme, kelvin_range(profile))
    if kelvin is None:
        state.rgb_color, state.color_temp_kelvin = rgb, None
    else:
        state.rgb_color, state.color_temp_kelvin = None, kelvin
    state.scene_code = None
    apply_zone_kelvin(profile, state, {0: kelvin, 1: kelvin})
    _remember_kelvin(state)


def apply_kelvin(profile: DeviceProfile, state: DeviceState, kelvin: int) -> None:
    """A whole-device colour-temperature write: that Kelvin, no RGB colour and no active scene
    (both bars of a bar-switch device)."""
    state.color_temp_kelvin, state.rgb_color = kelvin, None
    state.scene_code = None
    apply_zone_kelvin(profile, state, {0: kelvin, 1: kelvin})
    _remember_kelvin(state)


def apply_scene(profile: DeviceProfile, state: DeviceState, code: int) -> None:
    """A ``33 05 04`` scene activation: the device is on, in that scene, with no solid colour."""
    state.scene_code = code
    apply_power(profile, state, True)
    state.rgb_color = None
    state.color_temp_kelvin = None
    apply_zone_kelvin(profile, state, {0: None, 1: None})


def update_segments(
    state: DeviceState,
    indices: Sequence[int],
    *,
    rgb: tuple[int, int, int] | None = None,
    brightness: int | None = None,
) -> None:
    """Fold a segment write into ``state.segments``."""
    by_index = {seg.index: seg for seg in state.segments}
    updated: list[Segment] = []
    for i in indices:
        cur = by_index.get(i, Segment(index=i))
        seg = Segment(
            index=i,
            rgb=rgb if rgb is not None else cur.rgb,
            brightness=brightness if brightness is not None else cur.brightness,
        )
        by_index[i] = seg   # a repeated index folds onto the previous write
        updated.append(seg)
    state.segments = parse.merge_segments(state.segments, updated)


def apply_zone_kelvin(
    profile: DeviceProfile, state: DeviceState, kelvin_by_index: dict[int, int | None]
) -> None:
    """Fold a per-bar kelvin change into state (bar-switch devices only; others are a no-op).

    ``None``, 0 or a negative Kelvin (a signed short of 0x8000 or more) means that bar is not in
    colour-temperature mode. Like the app
    (``Info4Detail`` subMode4Color parse), the whole-device ``color_temp_kelvin`` is set only
    while both bars carry the same kelvin, and unknown otherwise. Once it is known the device is
    in colour-temperature mode, so a stale whole-device ``rgb_color`` is dropped (the two are
    mutually exclusive on every path)."""
    if not profile.bar_switch:
        return
    zk = dict(state.zone_color_temp_kelvin)
    for index, k in kelvin_by_index.items():
        if k is not None and k > 0:
            zk[index] = k
        else:
            zk.pop(index, None)
    state.zone_color_temp_kelvin = zk
    left = zk.get(0)
    whole = left if left is not None and left == zk.get(1) else None
    state.color_temp_kelvin = whole
    if whole is not None:
        state.rgb_color = None
    _remember_kelvin(state)   # the app takes the bars' Kelvin only when they match


# -- read-backs -------------------------------------------------------------
def apply_advert_power(profile: DeviceProfile, state: DeviceState, on: bool) -> None:
    """The on/off an advertisement carries, with the zones kept consistent as the power setters keep
    them: off -> every zone is off (as :func:`apply_power`); on -> which zones lit is not known, so
    zones that all read off are forgotten and the next zone write / poll re-reads them (as after a
    bar-switch 33 01 on) instead of trusting the stale "off"."""
    state.is_on = on
    if profile.zones:
        if not on:
            state.zone_power = {z.power_index: False for z in profile.zones}
        elif not any(state.zone_power.values()):
            state.zone_power = {}


def apply_bar_switch(state: DeviceState, bars: tuple[bool, bool]) -> None:
    """An ``aa 36`` bar-switch reply: both bars, and the device on while either is (the app's
    NewDetailVm heart parse)."""
    state.zone_power = {0: bars[0], 1: bars[1]}
    state.is_on = bars[0] or bars[1]


def apply_status(
    profile: DeviceProfile, state: DeviceState, st: reassemble.StatusReply
) -> tuple[bool, bool]:
    """Fold a 0xAC status burst into state. Returns ``(applied, power_read)``: whether a live field
    came back (state is then no longer optimistic) and whether on/off did (the advertised on/off is
    then suppressed for a while; ``Device._finish_read``). The static device-info fields are merged
    but count as neither, and an empty burst sets neither."""
    applied = power_read = False
    if st.is_on is not None:
        state.is_on = st.is_on
        applied = power_read = True
    if st.brightness is not None:
        state.brightness = brightness_pct(profile, st.brightness)
        applied = True
    if st.zone_power:
        state.zone_power = st.zone_power
        applied = True
    if st.segments:
        # A burst whose tail was lost carries only a prefix of the segments;
        # merge so the rest keep their last known values.
        state.segments = parse.merge_segments(state.segments, st.segments)
        applied = True
    if st.mode is not None:
        # The 0x05 TLV is the mode, as the app reads it on connect (no separate aa 05 read): the same
        # [sub_mode, params] body an aa 05 reply carries, decoded by the same per-SKU parse.
        apply_mode(profile, state, parse.mode_value(st.mode, wire_profile(profile)))
        applied = True
    # Device-info (serial / wifi_mac / fw / hw) comes from the 0x07 TLV in the 0xAC
    # stream — the ONLY source for BLE-only devices (H60A6); aa 07 returns zeros there.
    merge_device_info(
        state, serial=st.serial_number, wifi_mac=st.wifi_mac,
        firmware=st.firmware_version, hardware=st.hardware_version)
    return applied, power_read


def apply_mode_reply(profile: DeviceProfile, state: DeviceState, reply: bytes) -> None:
    """Fold an aa 05 mode reply into state (:func:`apply_mode` of its ``mode_report``, read under the
    device's mode decode). A frame that is not one changes nothing (the connection only delivers whole
    20-byte frames, which always parse)."""
    m = parse.mode_reply(reply, wire_profile(profile))
    if m is not None:
        apply_mode(profile, state, m)


def apply_mode(profile: DeviceProfile, state: DeviceState, m: Any) -> None:
    """:func:`_apply_mode`, then :func:`_remember_kelvin`: a report's Kelvin (> 0) is remembered."""
    _apply_mode(profile, state, m)
    _remember_kelvin(state)


def _apply_mode(profile: DeviceProfile, state: DeviceState, m: Any) -> None:
    """Fold a device's mode into state: active scene, or the colour / colour temperature of the
    solid-colour sub-mode. ``m`` is the spec's ``mode_report``, of an aa 05 reply
    (:func:`parse.mode_reply`) or of the 0xAC 0x05 TLV (:func:`parse.mode_value`), read under the
    device's mode decode (:func:`.profile.wire_profile`), which picks each colour sub-mode's layout.

    A scene report clears the colours, as a scene activation does. A colour report folds by what its
    layout carries (:func:`parse.mode_color`): a Kelvin alone is authoritative for the Kelvin
    (:func:`apply_kelvin_report`: 0 means "not in colour-temperature mode", as on the bar path and in
    the 0x0d report, so it clears ``color_temp_kelvin``); the bars' pair sets the per-bar Kelvin; a colour
    layout sets the colour or the colour temperature. A sub-mode the SKU's parse ignores leaves the
    colour as it was.

    Sub-mode 0xA1 is not a scene here, matching what the screen shows. Base
    ``OpInfo4Detail.x:786`` (bulbs, H6188, H61A8) hands an 0xA1 report to ``SubMode4Scene`` like 0x04,
    but the screen's mode map has no entry for it (``OpInfo4Detail.l:280-321``, "is not config, choose
    colorMode!" -> ``VM4Light.W3`` returns 9, colour mode): the app shows colour mode with the colour
    untouched and only keeps the code for its scene list. The library shows the same: no scene, the
    colour as it was."""
    state.scene_code = parse.scene_of(m)
    if state.scene_code is not None:
        state.rgb_color = state.color_temp_kelvin = None
        apply_zone_kelvin(profile, state, {0: None, 1: None})
        return
    color = parse.mode_color(m, unsigned_kelvin=profile.mode_color_parse == "rgb_kelvin")
    if isinstance(color, parse.BarKelvin):
        apply_zone_kelvin(profile, state, {0: color.left, 1: color.right})
    elif isinstance(color, parse.KelvinReport):
        apply_kelvin_report(state, color.kelvin)
    elif color is not None:
        state.rgb_color, state.color_temp_kelvin = color


def apply_kelvin_report(state: DeviceState, kelvin: int) -> None:
    """A whole-device colour-temperature report: > 0 is that Kelvin (and no RGB colour); 0 (or a
    negative signed short) is "not in colour-temperature mode", which the report carries no RGB
    for, so only the Kelvin is cleared."""
    if kelvin > 0:
        state.color_temp_kelvin, state.rgb_color = kelvin, None
    else:
        state.color_temp_kelvin = None


def merge_device_info(
    state: DeviceState,
    *,
    serial: str | None = None,
    wifi_mac: str | None = None,
    firmware: str | None = None,
    hardware: str | None = None,
) -> None:
    """Copy each non-None device-info field onto state; None leaves the field as is."""
    if serial is not None:
        state.serial_number = serial
    if wifi_mac is not None:
        state.wifi_mac = wifi_mac
    if firmware is not None:
        state.firmware_version = firmware
    if hardware is not None:
        state.hardware_version = hardware


def summary(state: DeviceState) -> str:
    """One-line state summary for the poll log lines."""
    s = state   # keeps the format string short
    return (
        f"on={s.is_on} bri={s.brightness} rgb={s.rgb_color} kelvin={s.color_temp_kelvin} "
        f"segs={len(s.segments)} zones={s.zone_power} scene={s.scene_code}"
    )
