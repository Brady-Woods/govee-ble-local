"""How a write or a read-back changes a device's :class:`~..models.DeviceState`.

Pure functions over a :class:`~.profile.DeviceProfile` and the state they change in place: no I/O,
no locks and no stale-read check (the one clock they read is ``time.monotonic()``, for the zones'
``zone_power_confirmed`` stamps). :class:`~.device.Device` calls them once a write has gone out
(``Device._commit``) or a reply has passed the stale-read check (``Device._read_fresh``), so they can
be exercised with a bare ``DeviceState``.
"""
from __future__ import annotations

import time
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any

from ..models import DeviceState, Segment
from ..wire import build, parse, reassemble
from .brightness import raw_to_percent
from ._resolve import version
from .profile import DeviceProfile, wire_profile


def kelvin_range(profile: DeviceProfile, hardware: str | None = None,
                 pact: tuple[int, int] | None = None, soft: str | None = None) -> tuple[int, int] | None:
    """The device's inclusive Kelvin range, or None when the profile leaves either end open: the first
    :attr:`~.profile.DeviceProfile.kelvin_by_hardware` entry the ``hardware`` version is at least (or
    :attr:`~.profile.DeviceProfile.kelvin_by_software` entry the ``soft`` version is; a string compare, as
    the app's), else the profile's base range, or the :attr:`~.profile.DeviceProfile.kelvin_by_pact` range
    of the advertised ``pact`` (a profile has one kind of entry only). With that version or the pact not yet
    known (None), the range that holds on every revision, or on every pact: the base range cut to each
    entry's."""
    lo_, hi_ = profile.min_kelvin, profile.max_kelvin
    if lo_ is None or hi_ is None:
        return None
    for key, lo, hi in profile.kelvin_by_pact:
        if pact is None:
            lo_, hi_ = max(lo_, lo), min(hi_, hi)
        elif key == pact:
            lo_, hi_ = lo, hi
    gated, have = ((profile.kelvin_by_software, soft) if profile.kelvin_by_software
                   else (profile.kelvin_by_hardware, hardware))
    if have is None:
        for _min_version, lo, hi in gated:
            lo_, hi_ = max(lo_, lo), min(hi_, hi)
        return lo_, hi_
    for min_version, lo, hi in gated:
        if have >= min_version:
            return lo, hi
    return lo_, hi_


def scene_versions(profile: DeviceProfile, soft: str | None = None, hard: str | None = None,
                   pact: tuple[int, int] | None = None) -> frozenset[int]:
    """The device's scene versionArray, as the spec resolves a row (``govee_reference.client_row``):
    :attr:`~.profile.DeviceProfile.scene_versions`, replaced by the
    :attr:`~.profile.DeviceProfile.scene_versions_by_pact` set of the advertised ``pact``, then by each
    :attr:`~.profile.DeviceProfile.scene_versions_by_firmware` entry whose gates the ``soft`` / ``hard``
    versions meet, in order.

    The pre-read rule is per version: one not yet read (None) meets every gate on it, as the newest firmware
    would, which is what the library sent before the gates were modelled (H61A8: {0, 1, 2, 3} until the
    device-info read). So a read that brought one version back still applies the gates it can test."""
    out = profile.scene_versions
    for key, versions in profile.scene_versions_by_pact:
        if key == pact:
            out = frozenset(versions)
    for min_soft, min_hard, versions in profile.scene_versions_by_firmware:
        if _meets(soft, min_soft) and _meets(hard, min_hard):
            out = frozenset(versions)
    return out


def _meets(text: str | None, gate: str) -> bool:
    """Whether a device version meets a ``min_*`` gate: ``""`` is no gate, an unread version (None) meets
    it, and a read one compares field by field (one that does not parse meets none)."""
    need = version(gate)
    if need is None or text is None:
        return True
    have = version(text)
    return have is not None and have >= need


def brightness_range(profile: DeviceProfile, pact: tuple[int, int] | None = None) -> tuple[int, int] | None:
    """The device's raw brightness range in force, None for percent: the profile's
    :attr:`~.profile.DeviceProfile.brightness_range`, or the
    :attr:`~.profile.DeviceProfile.brightness_by_pact` range of the advertised ``pact``."""
    span = profile.brightness_range
    for key, by_pact in profile.brightness_by_pact:
        if key == pact:
            span = by_pact
    return span


def brightness_pending(profile: DeviceProfile, pact: tuple[int, int] | None) -> bool:
    """Whether the brightness byte's range waits for the advertised pact: a profile with
    :attr:`~.profile.DeviceProfile.brightness_by_pact` and no pact heard. The app always knows the pact, so
    the library neither writes nor reads that byte until it does (``Device`` refuses the write)."""
    return bool(profile.brightness_by_pact) and pact is None


def brightness_pct(profile: DeviceProfile, raw: int, pact: tuple[int, int] | None = None) -> int | None:
    """The device-reported brightness byte as the 0..100 percent ``state`` documents, read with the range
    in force for ``pact`` (:func:`brightness_range`): clamped on every path, so a misbehaving device can't
    push e.g. 255 % into state. None (not known) while the range waits for the pact
    (:func:`brightness_pending`)."""
    if brightness_pending(profile, pact):
        return None
    span = brightness_range(profile, pact)
    if span is not None:
        return raw_to_percent(raw, *span)
    full = profile.brightness_max
    pct = raw if full == 100 else round(raw * 100 / full)
    return max(0, min(100, pct))


def _set_zone_power(state: DeviceState, zone_power: dict[int, bool], confirmed: Iterable[int] = ()) -> None:
    """Assign ``state.zone_power`` (``zone_power`` itself: every writer assigns a new dict) and keep
    ``state.zone_power_confirmed`` with it. Each zone of ``confirmed`` that ``zone_power`` holds (its power
    learned from the device by this change: a read of it, or an acked frame that sets it) is stamped now
    (``time.monotonic()``). Every other zone keeps its stamp only while it keeps its value, so a zone this
    change forgets, or gives a value the device did not report, loses it. Every writer of ``zone_power``
    goes through here."""
    old = state.zone_power
    stamps = {i: t for i, t in state.zone_power_confirmed.items()
              if i in zone_power and zone_power[i] == old.get(i)}
    now = time.monotonic()
    stamps.update((i, now) for i in confirmed if i in zone_power)
    state.zone_power = zone_power
    state.zone_power_confirmed = stamps


def unconfirm_zones(state: DeviceState, zones: Iterable[int] | None = None) -> None:
    """The device may hold another power for these zones (None: every zone) than the one held, and the
    library has not learned which: a power write that targets them raised once it may have reached the
    device (``Device._commit``). Each loses its ``zone_power_confirmed`` stamp, whatever its held power;
    ``zone_power`` itself is left as it is."""
    stamps = state.zone_power_confirmed
    gone = set(stamps) if zones is None else stamps.keys() & set(zones)
    if gone:
        state.zone_power_confirmed = {i: t for i, t in stamps.items() if i not in gone}


def unconfirm_contradicted(state: DeviceState, power: Mapping[int, bool]) -> None:
    """The device may now hold ``power`` for these zones without the library folding it in: a write that
    wakes them (which only lights, ``Device._wake_power``), or a reply that rules any other power out. Each
    zone that ``state.zone_power`` holds at another power loses its ``zone_power_confirmed`` stamp (one held
    at that power holds it either way); ``zone_power`` itself is left as it is."""
    unconfirm_zones(state, [i for i, on in power.items() if state.zone_power.get(i) != on])


def confirmed_within(state: DeviceState, zone: int, seconds: float) -> bool:
    """Whether ``zone``'s power was learned from the device at most ``seconds`` ago: it has a
    ``zone_power_confirmed`` stamp, read against the same ``time.monotonic()`` clock that stamps it."""
    stamp = state.zone_power_confirmed.get(zone)
    return stamp is not None and time.monotonic() - stamp <= seconds


# -- writes -----------------------------------------------------------------
def apply_power(profile: DeviceProfile, state: DeviceState, on: bool, *,
                confirmed: Iterable[int] = ()) -> None:
    """Whole-device power, and the zones it drives. ``confirmed``: the zones the acked power write's
    frames set (``Device._power_zones``), stamped as learned from the device. Empty (the default) for the
    power a scene activation implies (:func:`apply_scene`, which then drops every zone's stamp)."""
    state.is_on = on
    if profile.bar_switch and on:
        # 33 01 on: the app does not say which bars light (it learns that from the next aa 36
        # heartbeat), so don't guess; the next zone write re-reads them rather than assuming.
        _set_zone_power(state, {})
    elif profile.zones:
        _set_zone_power(state, {z.power_index: on for z in profile.zones}, confirmed)
    elif profile.bar_switch:
        # 33 01 off on a bar-switch device without profile zones (H6042): the bars its aa 36 read keep
        # their power, but no firmware in hand says what the off leaves them at, so they are not vouched for.
        unconfirm_zones(state)


def apply_zone_power(profile: DeviceProfile, state: DeviceState, zone_power: dict[int, bool], *,
                     confirmed: Iterable[int] = ()) -> None:
    """A zone power write: the zones' power (a new dict, as every writer of ``zone_power`` assigns), the
    zones the acked frame set (``confirmed``: a ``33 30`` its own zone, a ``33 36`` both bars) stamped as
    learned from the device, and, once every zone is known, the whole-device on/off (on while any zone
    is)."""
    _set_zone_power(state, zone_power, confirmed)
    if profile.zones and len(zone_power) == len(profile.zones):
        state.is_on = any(zone_power.values())


def _remember_kelvin(state: DeviceState) -> None:
    """Keep ``last_color_temp_kelvin`` as the app keeps its Kelvin: it follows ``color_temp_kelvin``
    whenever that is a colour temperature (> 0), and nothing that clears the mode clears it."""
    if state.color_temp_kelvin is not None and state.color_temp_kelvin > 0:
        state.last_color_temp_kelvin = state.color_temp_kelvin


def apply_rgb(profile: DeviceProfile, state: DeviceState, rgb: tuple[int, int, int],
              span: tuple[int, int] | None) -> None:
    """A whole-device RGB write: the colour, or - when the app sends it as a colour temperature
    (``build.rgb_as_kelvin``) - that Kelvin, which is what the device reports (and what the app
    syncs to its Kelvin slider; both bars of a bar-switch device). ``span`` is the Kelvin range the
    frame was built with (:func:`kelvin_range`), so the state follows what was sent."""
    kelvin = build.rgb_as_kelvin(*rgb, profile.color_scheme, span)
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
    """A ``33 05 04`` scene activation: the device is on, in that scene, with no solid colour. Its zones'
    power is assumed (:func:`apply_power` with no zone confirmed), never learned, and every zone loses its
    stamp, one whose held power stays as it was too: what the activation does to the zone flags is not
    traced (H60A6: the dispatcher's case 5 hands it to FUN_9b025124 whatever the main switch
    (@0x9b026b80), then lights the main switch and both zones if it was off (@0x9b026b92-0x9b026ba6); what
    FUN_9b025124 does to the zone flags with the switch on is not traced; H6047: the 0x05 handler
    0x01005510 is traced only as writing scene and segment data), so it vouches for none, as a failed one
    does (``Device._commit``)."""
    state.scene_code = code
    apply_power(profile, state, True)
    unconfirm_zones(state)
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
    bar-switch 33 01 on) instead of trusting the stale "off". An advertisement confirms no zone (it
    carries the whole-device on/off only): a zone its "off" turns off loses its stamp, and so does a
    zone its "on" forgets."""
    state.is_on = on
    if profile.zones:
        if not on:
            _set_zone_power(state, {z.power_index: False for z in profile.zones})
        elif not any(state.zone_power.values()):
            _set_zone_power(state, {})


def apply_bar_switch(state: DeviceState, bars: tuple[bool, bool]) -> None:
    """An ``aa 36`` bar-switch reply: both bars, learned from the device, and the device on while either
    is (the app's NewDetailVm heart parse)."""
    _set_zone_power(state, {0: bars[0], 1: bars[1]}, (0, 1))
    state.is_on = bars[0] or bars[1]


def apply_status(
    profile: DeviceProfile, state: DeviceState, st: reassemble.StatusReply,
    pact: tuple[int, int] | None = None, *, unsettled: Collection[int] = (),
) -> tuple[bool, bool]:
    """Fold a 0xAC status burst into state (its brightness read with the range in force for the advertised
    ``pact``). Returns ``(applied, power_read)``: whether a live field
    came back (state is then no longer optimistic) and whether on/off did (the advertised on/off is
    then suppressed for a while; ``Device._finish_read``). The static device-info fields are merged
    but count as neither, and an empty burst sets neither.

    TLV 0x30 stamps each zone it carries (``zone_power_confirmed``), except:

    - a zone it reports lit while the same burst's TLV 0x01 does not say on: the device says it is off
      (H60A6: TLV 0x01 is the main switch flag), so the zone's flag does not vouch that it is lit (an "off"
      still stamps);
    - a zone of ``unsettled``: the reply may predate a power write the device had not applied yet
      (``Device._read_status``).

    Such a zone takes the reported power, as before, and loses the stamp it had."""
    applied = power_read = False
    if st.is_on is not None:
        state.is_on = st.is_on
        applied = power_read = True
    if st.brightness is not None:
        state.brightness = brightness_pct(profile, st.brightness, pact)
        applied = True
    if st.zone_power:
        vouched = [i for i, on in st.zone_power.items()
                   if (st.is_on is True or not on) and i not in unsettled]
        _set_zone_power(state, st.zone_power, vouched)   # TLV 0x30: each zone it carries and vouches for
        unconfirm_zones(state, [i for i in st.zone_power if i not in vouched])
        applied = True
    if st.segments:
        # A burst whose tail was lost carries only a prefix of the segments;
        # merge so the rest keep their last known values.
        state.segments = parse.merge_segments(state.segments, st.segments)
        applied = True
    if st.mode is not None:
        # The 0x05 TLV is the mode, as the app reads it on connect (no separate aa 05 read): the same
        # [sub_mode, params] body an aa 05 reply carries, read in the stream with the same per-SKU decode.
        apply_mode(profile, state, st.mode)
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
    (:func:`parse.mode_reply`) or of the 0xAC 0x05 TLV (read in the status stream,
    :func:`.reassemble.parse_status`), read under the device's mode decode
    (:func:`.profile.wire_profile`), which picks each colour sub-mode's layout.

    A scene report clears the colours, as a scene activation does. A colour report folds by what its
    layout carries (:func:`parse.mode_color`): a Kelvin alone is authoritative for the Kelvin
    (:func:`apply_kelvin_report`: 0 means "not in colour-temperature mode", as on the bar path and in
    the 0x0d report, so it clears ``color_temp_kelvin``, except where the report carries no Kelvin,
    ``DeviceProfile.mode_reports_kelvin`` False: the H61A8, whose 0 leaves the Kelvin the library sent);
    the bars' pair sets the per-bar Kelvin; a colour
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
        if color.kelvin > 0 or profile.mode_reports_kelvin:   # else the report can't say: keep what was sent
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
