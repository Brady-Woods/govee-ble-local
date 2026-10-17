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
from .profile import DeviceProfile


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


def apply_kelvin(profile: DeviceProfile, state: DeviceState, kelvin: int) -> None:
    """A whole-device colour-temperature write: that Kelvin, no RGB colour and no active scene
    (both bars of a bar-switch device)."""
    state.color_temp_kelvin, state.rgb_color = kelvin, None
    state.scene_code = None
    apply_zone_kelvin(profile, state, {0: kelvin, 1: kelvin})


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
        apply_mode(profile, state, parse.mode_value(st.mode))
        applied = True
    # Device-info (serial / wifi_mac / fw / hw) comes from the 0x07 TLV in the 0xAC
    # stream — the ONLY source for BLE-only devices (H60A6); aa 07 returns zeros there.
    merge_device_info(
        state, serial=st.serial_number, wifi_mac=st.wifi_mac,
        firmware=st.firmware_version, hardware=st.hardware_version)
    return applied, power_read


def apply_mode_reply(profile: DeviceProfile, state: DeviceState, reply: bytes) -> None:
    """Fold an aa 05 mode reply into state (:func:`apply_mode` of its ``mode_read``). A frame that is
    not one changes nothing (the connection only delivers whole 20-byte frames, which always parse)."""
    m = parse.mode_reply(reply)
    if m is not None:
        apply_mode(profile, state, m)


def apply_mode(profile: DeviceProfile, state: DeviceState, m: Any) -> None:
    """Fold a device's mode into state: active scene, or the colour / colour temperature of the
    solid-colour sub-mode. ``m`` is the spec's ``mode_read``, of an aa 05 reply
    (:func:`parse.mode_reply`) or of the 0xAC 0x05 TLV (:func:`parse.mode_value`).

    A scene report clears the colours, as a scene activation does. A colour-temperature report
    is authoritative for the Kelvin: 0 means "not in colour-temperature mode" (as on the bar
    path and in the 0x0d report), so it clears ``color_temp_kelvin``. The colour sub-modes are
    decoded per the profile's ``mode_color_parse`` (the SKU's Info4Detail mode parse); one that
    parse ignores leaves the colour as it was."""
    state.scene_code = parse.scene_of(m)
    if state.scene_code is not None:
        state.rgb_color = state.color_temp_kelvin = None
        apply_zone_kelvin(profile, state, {0: None, 1: None})
        return
    bars = parse.bar_kelvin_of(m) if profile.bar_switch else None
    if bars is not None:
        # H6047 aa 05 15: [gradual][kelvin left u2be][kelvin right u2be]
        apply_zone_kelvin(profile, state, {0: bars[0], 1: bars[1]})
        return
    sub = parse.sub_mode(m)
    if profile.mode_color_parse == "kelvin":
        # H6062: OpInfo4Detail.parseModeValidBytes sends 0x15/0x0b/0x02/0x0d alike to the
        # SubMode4Color parse and shows the Kelvin only when it is > 0.
        kelvin = parse.gradual_kelvin_of(m) if sub in parse.SUBMODE4COLOR_SUB_MODES else None
        if kelvin is not None:
            apply_kelvin_report(state, kelvin)
        return
    if profile.mode_color_parse == "rgb_kelvin_tint":
        # Bulbs / H6188: OpInfo4Detail.parseModeValidBytes sends 0x15/0x0b/0x02/0x0d alike to
        # ModeParseStrategyConfig.a ([r, g, b][kelvin s16be][tint]; CCT only for a Kelvin > 0).
        decoded = parse.rgb_kelvin_tint_of(m)
        if decoded is not None:
            state.rgb_color, state.color_temp_kelvin = decoded
        return
    if profile.mode_color_parse == "rgb_0d":
        # H6052: H6052InfoDetail.G decodes 0x0d as [r, g, b] and never reads its Kelvin bytes; 0x15
        # and the other colour sub-modes are "not support" and change nothing.
        rgb = parse.rgb_0d_of(m)
        if rgb is not None:
            state.rgb_color, state.color_temp_kelvin = rgb, None
        return
    if profile.mode_color_parse == "kelvin_15_rgb_kelvin_0d":
        # H60A6: pact_h60a0 h60a1.Info4Detail.Q (:399-430) reads 0x15 with SubMode4Color's default
        # [gradual][kelvin s16be] and 0x0d as [r, g, b][kelvin s16be] (CCT for a Kelvin > 0, else that
        # RGB); 0x04 is the scene (above) and every other sub-mode is "not support!". A 0x15 Kelvin of 0
        # or below clears the Kelvin here (apply_kelvin_report); the app keeps its last one.
        if sub == build.SUB_COLOR_15:
            kelvin = parse.gradual_kelvin_of(m)
            if kelvin is not None:
                apply_kelvin_report(state, kelvin)
        elif sub == build.SUB_COLOR_0D:
            decoded = parse.rgb_kelvin_0d_of(m)
            if decoded is not None:
                state.rgb_color, state.color_temp_kelvin = decoded
        return
    if profile.mode_color_parse == "rgb_kelvin_0d":
        # H6061: CubeNewInfo4Detail.U decodes 0x0d alone among the colour sub-modes; 0x15 (which
        # the H6061 itself writes) and the rest are "not support!" and change nothing.
        cube = parse.rgb_kelvin_0d_of(m)
        if cube is not None:
            state.rgb_color, state.color_temp_kelvin = cube
        return
    # 0x15 alone: H6641 reads its Kelvin signed (a plain SubMode4Color), H61A8 unsigned (SubModeColorV2.parse,
    # BleUtil.getSignedInt masks each byte); the default rgb_kelvin parse keeps the raw u2be.
    signed = profile.mode_color_parse == "kelvin_15"
    kelvin = parse.kelvin_signed_of(m) if signed else parse.kelvin_of(m)
    if kelvin is not None:
        apply_kelvin_report(state, kelvin)
        return
    if profile.mode_color_parse in ("kelvin_15", "kelvin_15_unsigned"):
        return   # the app parses no other colour sub-mode for this SKU (0x0d "is not support")
    # The default "rgb_kelvin" parse: parse_mode_0d_for's 0x0d decoder, kind 2 on every colour scheme.
    mode_0d = parse.cct_0d_of(m)
    if mode_0d is not None:
        rgb, kelvin = mode_0d
        state.rgb_color, state.color_temp_kelvin = rgb, kelvin


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
