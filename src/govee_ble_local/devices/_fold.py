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

from ..models import Capability, DeviceState, Segment
from ..wire import build, parse, reassemble
from ..wire.cct import kelvin_for_tint, tint_for_kelvin
from .brightness import raw_to_percent
from ._resolve import parse_version, version_key
from .profile import DeviceProfile, wire_profile

# The mode decode named for the h60a1 screen's ``Info4Detail.Q`` (spec/devices.yaml mode_color_parse
# ``kelvin_15_rgb_kelvin_0d``: the H60A1 / H80A1, H60A4 / H80A4, H60A6, H1270, H1232 and H1252 rows), whose
# 0x0d branch leaves a Kelvin above 0 to the same read's groups (:func:`_left_to_pieces`).
_H60A1_PARSE = "kelvin_15_rgb_kelvin_0d"


def kelvin_range(profile: DeviceProfile, hardware: str | None = None,
                 pact: tuple[int, int] | None = None, soft: str | None = None) -> tuple[int, int] | None:
    """The device's inclusive Kelvin range, or None when the profile leaves either end open: the first
    :attr:`~.profile.DeviceProfile.kelvin_by_hardware` entry the ``hardware`` version is at least (or
    :attr:`~.profile.DeviceProfile.kelvin_by_software` entry the ``soft`` version is; compared as the app's
    KelvinConfig function compares it, :attr:`~.profile.DeviceProfile.kelvin_version_compare`), else the
    profile's base range, or the :attr:`~.profile.DeviceProfile.kelvin_by_pact` range
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
    compare = profile.kelvin_version_compare
    for min_version, lo, hi in gated:
        if version_key(have, compare) >= version_key(min_version, compare):
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
    would, which is what the library sent before the gates were modelled (the dreamcolorlightv1 rows:
    {0, 1, 2, 3} until the device-info read; the app, whose version parse gives 0 for one it has not read,
    fails them). So a read that brought one version back still applies the gates it can test, and
    ``Device`` reads the other again on the next poll (:func:`gated_versions`)."""
    out = profile.scene_versions
    for key, versions in profile.scene_versions_by_pact:
        if key == pact:
            out = frozenset(versions)
    for min_soft, min_hard, versions in profile.scene_versions_by_firmware:
        if _meets(soft, min_soft) and _meets(hard, min_hard):
            out = frozenset(versions)
    return out


def gated_versions(profile: DeviceProfile, *, scenes: bool = False) -> tuple[bool, bool]:
    """Whether a firmware-gated fact of ``profile`` tests the soft (``aa 06``) and the hard (``aa 07 03``)
    version, ``(soft, hard)``: a :attr:`~.profile.DeviceProfile.scene_versions_by_firmware` gate on it, or,
    unless ``scenes`` (the scene set's gates only), a :attr:`~.profile.DeviceProfile.kelvin_by_software` /
    :attr:`~.profile.DeviceProfile.kelvin_by_hardware` entry."""
    gates = profile.scene_versions_by_firmware
    soft = any(s for s, _h, _v in gates) or (not scenes and bool(profile.kelvin_by_software))
    hard = any(h for _s, h, _v in gates) or (not scenes and bool(profile.kelvin_by_hardware))
    return soft, hard


def _meets(text: str | None, gate: str) -> bool:
    """Whether a device version meets a ``min_*`` gate: ``""`` is no gate, an unread version (None) meets
    it, and a read one compares as the app's (:func:`._resolve.parse_version`: one int of the digits, so
    ``1.6.0`` is below ``1.06.00``; one that does not parse is 0, which meets none)."""
    if not gate or text is None:
        return True
    return parse_version(text) >= parse_version(gate)


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
    frame was built with (:func:`kelvin_range`), so the state follows what was sent.

    A profile without COLOR_TEMP keeps the colour, though its frame may be the colour-temperature form
    (``legacy_02``'s ``Bytes.o`` on H7310 / H7318 / H6165): the app keeps the picked colours
    (``ColorViewModel.u0``: ``SubMode4Color.p``) and its ``syncKelvin`` has no Kelvin control to drive on
    those screens (``VM4Light.syncKelvin``; ``Config.EO`` gives them no colour temperature).

    Every segment the state holds takes the picked colour, whichever frame carried it
    (:func:`fill_segments`)."""
    kelvin = (build.rgb_as_kelvin(*rgb, profile.color_scheme, span)
              if Capability.COLOR_TEMP in profile.capabilities else None)
    if kelvin is None:
        state.rgb_color, state.color_temp_kelvin = rgb, None
    else:
        state.rgb_color, state.color_temp_kelvin = None, kelvin
    state.scene_code = None
    apply_zone_kelvin(profile, state, {0: kelvin, 1: kelvin})
    _remember_kelvin(state)
    fill_segments(state, rgb)


def apply_kelvin(profile: DeviceProfile, state: DeviceState, kelvin: int) -> None:
    """A whole-device colour-temperature write: that Kelvin, no RGB colour and no active scene
    (both bars of a bar-switch device), and every segment the state holds at the app's tint for it
    (:func:`fill_segments`)."""
    state.color_temp_kelvin, state.rgb_color = kelvin, None
    state.scene_code = None
    apply_zone_kelvin(profile, state, {0: kelvin, 1: kelvin})
    _remember_kelvin(state)
    fill_segments(state, tint_for_kelvin(kelvin))


def fill_segments(state: DeviceState, rgb: tuple[int, int, int]) -> None:
    """A whole-device colour or colour-temperature write sets every piece: each segment ``state.segments``
    holds takes ``rgb``, its brightness kept, and a segment it does not hold stays unknown. The newdetail
    colour page (``DelegateCurMode.createView:185-203``) applies a whole-strip pick over every piece:
    ``DelegateColorV2.F0:220-235`` (a colour) and ``G0:241-253`` (a Kelvin, as its tint
    ``Constant.getTemColorByKelvin(K)[2]``: :func:`~..wire.cct.tint_for_kelvin`) select every position at that
    colour (``ColorModeUtil.e:140-146``). So the state does not keep the segment colours from before the write
    beside the whole-device colour it now holds (a status row that follows the keep-alive reads no status
    again on its link, :attr:`~.profile.DeviceProfile.heartbeat_power`); a later read-back of the segments
    replaces them, as before."""
    if state.segments:
        state.segments = [Segment(seg.index, rgb, seg.brightness) for seg in state.segments]


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
    if st.color is not None:
        # The one-record 0xA5 (H703A / H703B): the one piece's colour, folded as the whole-device colour
        # before the same burst's mode, which then decides: a Kelvin above 0 reads as colour temperature, 0
        # keeps the colour, a scene clears it. A library choice: the app holds the Kelvin (Info4Detail.W) and
        # the piece colour (SubMode4Color.l) side by side, and state keeps one of the two. Not yet read as the
        # screen's stock colour page shows it (DelegateColorV2.W1: a tint in range as its Kelvin; _as_shown):
        # the row has no color_page, as the keep-alive re-read's aa 05 (a 0x15 Kelvin of 0) would clear a
        # folded Kelvin while its aa a5 group is not applied; the H703A / H703B step (decision 27) does both.
        state.rgb_color, state.color_temp_kelvin = st.color, None
        applied = True
    if st.mode is not None:
        # The 0x05 TLV is the mode, as the app reads it on connect (no separate aa 05 read): the same
        # [sub_mode, params] body an aa 05 reply carries, read in the stream with the same per-SKU decode.
        # Its colour is shown over the pieces of this burst's TLV 0xA5 groups (_as_shown): the app applies
        # the TLVs in reply order (Compose4BaseInfoSingleRead.u:247-345), the reply carries 0x05 before
        # 0xA5 (the real H60A6 bursts in tests/test_wire_reassemble.py), and the groups then replace the
        # report's one colour in SubMode4Color.d (SubMode4Color.j:124-126), which DelegateColorV2.W1 tests
        # and, where they all show one colour, shows (d[0]). Those bursts' own report is the 0x15 sub-mode
        # (15 01 00 00), which carries no colour and leaves d to the groups, so W1 shows their one colour
        # there too, as after the h60a1 screen's 0x0d Kelvin above 0, whose tint they replace
        # (_shown_over_pieces).
        apply_mode(profile, state, st.mode, pieces=st.segments)
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


def apply_mode(profile: DeviceProfile, state: DeviceState, m: Any,
               pieces: Sequence[Segment] = ()) -> None:
    """:func:`_apply_mode`, then :func:`_remember_kelvin`: a report's Kelvin (> 0) is remembered. ``pieces``:
    the segments the same read brought, which a colour-mode report is shown over (:func:`_as_shown`,
    :func:`_shown_over_pieces`)."""
    _apply_mode(profile, state, m, pieces)
    _remember_kelvin(state)


def apply_mode_over_pieces(profile: DeviceProfile, state: DeviceState, m: Any, pieces: Sequence[Segment],
                           remembered: int | None) -> None:
    """Show a polled ``aa 05`` colour-mode report again once the same poll's ``aa a5`` walk has read
    ``pieces``: as :func:`apply_mode` would have folded it had the pieces been in when it came, ``remembered``
    being the ``last_color_temp_kelvin`` from before it. The app shows the colour page once its connect read
    is done, and the walk is part of that read, after the ``aa 05`` reply: the mode callback's group reads
    join the same compose (``Compose4InfoBleIot$iControllerStrategy$1.v:125-132``, ``:243-250``; h60a1
    ``VM4Light.h5:327-340`` -> ``Y4:153-179`` -> ``Z4:181-185``, then ``i5:342-358``; cubelight
    ``CubeVM4Light$connectBleSuc$2.l:47-60`` -> ``CubeVM4Light.l5:386-421`` -> ``m5:424-438``). So
    ``DelegateColorV2.W1`` tests the walk's pieces, which replace the report's one colour in
    ``SubMode4Color.d`` (:func:`_as_shown`), or fill it after a 0x15 report, which carries none, and on the
    h60a1 screen after a 0x0d Kelvin above 0, whose tint they replace (:func:`_shown_over_pieces`): their one
    colour, where they all show the same, whatever the report's was. The Kelvin is remembered only where that
    colour reads as one, as the app syncs a Kelvin only when every piece is one tint in range
    (``initColor4Device:1211-1222``: ``KelvinConfig.f:144-157`` -> ``syncKelvin``), else ``remembered``
    stays, or the Kelvin above 0 such a report's parse holds (:func:`_held_kelvin`). Any other report already
    left state as the page shows it."""
    if parse.scene_of(m) is not None:
        return
    color = parse.mode_color(m, unsigned_kelvin=profile.mode_color_parse == "rgb_kelvin")
    over = _shown_over_pieces(profile, state, m, color, pieces)
    if over is not None:
        shown, remembered = over, _held_kelvin(color) or remembered
    elif isinstance(color, tuple):
        shown = _as_shown(profile, state, color, pieces)
    else:
        return
    state.rgb_color, state.color_temp_kelvin = shown
    state.last_color_temp_kelvin = remembered
    _remember_kelvin(state)


def _apply_mode(profile: DeviceProfile, state: DeviceState, m: Any, pieces: Sequence[Segment] = ()) -> None:
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
    layout sets the colour or the colour temperature, as the screen shows it over the same read's ``pieces``
    (:func:`_as_shown`). A sub-mode the SKU's parse ignores leaves the colour as it was. A 0x15 report, which
    carries no colour, and on the h60a1 screen a 0x0d Kelvin above 0, whose tint the pieces replace, are shown
    as the same read's ``pieces`` where they all show one colour, as the stock page shows it, the Kelvin above
    0 held (:func:`_shown_over_pieces`, :func:`_held_kelvin`); with no pieces, or pieces that differ, they
    fold as above.

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
    over = _shown_over_pieces(profile, state, m, color, pieces)
    if over is not None:
        held = _held_kelvin(color)
        if held is not None:
            state.last_color_temp_kelvin = held
        state.rgb_color, state.color_temp_kelvin = over
    elif isinstance(color, parse.BarKelvin):
        apply_zone_kelvin(profile, state, {0: color.left, 1: color.right})
    elif isinstance(color, parse.KelvinReport):
        if color.kelvin > 0 or profile.mode_reports_kelvin:   # else the report can't say: keep what was sent
            apply_kelvin_report(state, color.kelvin)
    elif isinstance(color, parse.HeadPair):
        _apply_head_pair(profile, state, color)
    elif color is not None:
        state.rgb_color, state.color_temp_kelvin = _as_shown(profile, state, color, pieces)


def _as_shown(profile: DeviceProfile, state: DeviceState, color: parse.ColorReport,
              pieces: Sequence[Segment] = ()) -> parse.ColorReport:
    """A colour report ``(rgb, kelvin)`` as the screen shows it over ``pieces``, the segments the same read
    brought (a status burst's TLV 0xA5 groups, a poll's ``aa a5`` walk: :func:`apply_mode_over_pieces`). On
    the stock colour page (:attr:`~.profile.DeviceProfile.color_page`) the page shows one colour: the
    report's when the read brought no pieces, else the pieces' when every piece shows the same one. That
    colour, when it is one of the app's tint colours (:func:`~..wire.cct.kelvin_for_tint`) whose Kelvin
    lies in the device's inclusive range (:func:`kelvin_range`), is that colour temperature, with no RGB
    colour, whatever the decode; else it is the RGB colour. ``DelegateColorV2.W1:1105-1123``
    (``DelegateCurMode.createView:185-203`` -> ``ColorViewModel.initColor4Device:1182-1224`` ->
    ``v1:625-630`` -> ``w1:666-734``) tests ``SubMode4Color.d``: when its pieces all equal the first
    (``ColorUtils.q:335-349``; one piece always does) it shows ``d[0]``, as the Kelvin slider at
    ``KelvinConfig.c:119-122`` of it when ``KelvinConfig.i:193-200`` holds (``ColorUtils.i:261-276``,
    inclusive), else as that colour (``P1`` / ``G1``); when they differ it sets neither, and ``w1:732`` ->
    ``S1:1084-1090`` shows the pieces. ``d`` is the report's one colour (h60a1 ``Info4Detail.Q:416-420``,
    cubelight ``CubeNewInfo4Detail:146``) until the same read's groups replace it, sized to the piece count
    (``SubMode4Color.j:113-148``), so with pieces ``d[0]`` is the first piece's, whatever the report's was.
    Pieces that differ keep the report's RGB, a library choice: the page shows no one colour, and state
    holds the pieces as the segments. The report's own Kelvin bytes are not read here: a decode that takes a
    Kelvin above 0 as colour temperature has already reported no colour, and the page never tests the Kelvin
    of one that doesn't (``rgb_kelvin_0d_nocct``); such a report stays as decoded here (on the h60a1 screen
    the same read's pieces replace its tint: :func:`_shown_over_pieces`). The same rule as a
    write's (:func:`apply_rgb`), so a tint the library sent reads back as the Kelvin it sent. Any other
    report, another page and a profile without COLOR_TEMP stay as decoded."""
    rgb, _kelvin = color
    if rgb is None or not _stock_page(profile):
        return color
    if pieces:
        shown = _one_colour(pieces)
        if shown is None:
            return color
        rgb = shown   # W1 shows d[0]: the pieces' one colour, which replaced the report's in SubMode4Color.d
    return _page_colour(profile, state, rgb)


def _shown_over_pieces(profile: DeviceProfile, state: DeviceState, m: Any, color: parse.ModeColor | None,
                       pieces: Sequence[Segment]) -> parse.ColorReport | None:
    """A report whose parse leaves ``SubMode4Color.d`` to the same read's groups (:func:`_left_to_pieces`) as
    the stock colour page shows it over ``pieces``, the segments that read brought (a status burst's TLV 0xA5
    groups, a poll's ``aa a5`` walk: :func:`apply_mode_over_pieces`), or None where the page shows no one
    colour of theirs. The screen shows such a report on the colour page (mode 9: h60a1
    ``Info4Detail.K:215-224`` for 0x15 and 0x0d, cubelight ``CubeNewInfo4Detail.M:297-306`` for 0x15). The
    same read's groups then fill ``d`` at the piece count (``SubMode4Color.j:113-148``; ``:124-126`` sizes it
    to the pieces, so a one-colour ``d`` the parse set is gone): the burst's (h60a1
    ``VM4Light$afterConnectedSingleReadDeviceInfo$1.n:65-95`` -> ``o:97-106``,
    ``VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.o:94-124`` -> ``p:126-142``,
    ``VM4LightH1270$afterConnectedSingleReadDeviceInfo$1.n:70-97`` -> ``o:99-108``) or the walk's (h60a1
    ``VM4Light.Z4:181-185``, cubelight ``CubeVM4Light.m5:424-438``, which keeps ``d`` only while
    ``SubMode4Color.b`` holds a Kelvin above 0, which only the screen's 0x0d parse sets,
    ``CubeNewInfo4Detail:143-153``). So ``DelegateColorV2.W1:1105-1123`` tests the pieces whatever the
    report's Kelvin: where every piece shows one colour (:func:`_one_colour`) it shows that colour, read as
    :func:`_page_colour` reads it; where they differ it sets neither, and the report folds as decoded (None
    here), a library choice, as :func:`_as_shown` keeps a colour report's RGB over pieces that differ. None
    too with no pieces, on a report whose parse sets ``d`` itself, on another page and on a profile without
    COLOR_TEMP."""
    if not pieces or not _stock_page(profile) or not _left_to_pieces(profile, m, color):
        return None
    rgb = _one_colour(pieces)
    return None if rgb is None else _page_colour(profile, state, rgb)


def _left_to_pieces(profile: DeviceProfile, m: Any, color: parse.ModeColor | None) -> bool:
    """Whether the screen's parse of the report leaves ``SubMode4Color.d`` to the same read's groups:

    - a 0x15 report, which carries the gradual flag and a Kelvin and no colour (a :class:`parse.KelvinReport`;
      the Glide panels' decode reads nothing from it). h60a1 ``Info4Detail.Q:399-407`` (the ``Info4Detail``
      of the H60A1 / H60A4's ``VM4Light:104``, the H60A6 / H1232 / H1252's ``VM4LightH60A6:150`` and the
      H1270's ``VM4LightH1270:145``) takes only the flag and the Kelvin (the ``SubMode4Color`` default parse,
      ``:42-49``), and cubelight ``CubeNewInfo4Detail.U:488-537`` takes nothing, so ``d`` stays as it was;
    - on the h60a1 screen (:data:`_H60A1_PARSE`), a 0x0d report of a Kelvin above 0, which the decode reads
      as that colour temperature with no colour: ``Info4Detail.Q:416-426`` holds the Kelvin and sets ``d`` to
      its one tint (``Constant.getTemColorByKelvin``), which the groups replace, as its walk and burst
      handlers above call ``SubMode4Color.j`` with no test of ``SubMode4Color.b``. The Glide panels' walk
      keeps that ``d`` instead (``CubeVM4Light.m5``), so their decoded Kelvin is what their page shows.

    Not a 0x15 decode that reads a colour, the bars' pair or the heads, nor any other report."""
    sub = parse.sub_mode(m)
    if sub == build.SUB_COLOR_15:
        return color is None or isinstance(color, parse.KelvinReport)
    return (sub == build.SUB_COLOR_0D and profile.mode_color_parse == _H60A1_PARSE
            and isinstance(color, tuple) and color[0] is None and (color[1] or 0) > 0)


def _held_kelvin(color: parse.ModeColor | None) -> int | None:
    """The ``last_color_temp_kelvin`` a report shown over pieces (:func:`_shown_over_pieces`) holds: its
    Kelvin above 0, which h60a1 ``Info4Detail.Q`` stores as the screen's held Kelvin ``v`` (``:401-404`` for
    0x15, ``:423`` for 0x0d; ``Info4Detail.q:728-729``, the Kelvin of ``illuminationCur``), else None, ``v``
    kept. A tint in range that the pieces all show then replaces it, as ``initColor4Device:1211-1222`` syncs
    that one (``KelvinConfig.f:144-157`` -> ``syncKelvin`` -> ``Info4Detail.f:564-570``). The Glide panels'
    decode reads no Kelvin from 0x15, as cubelight ``CubeNewInfo4Detail.U:488-537`` takes none."""
    if isinstance(color, parse.KelvinReport):
        return color.kelvin if color.kelvin > 0 else None
    if isinstance(color, tuple) and color[0] is None and (color[1] or 0) > 0:
        return color[1]
    return None


def _stock_page(profile: DeviceProfile) -> bool:
    """Whether the screen shows a reported colour on the stock colour page with a Kelvin control."""
    return profile.color_page == "stock" and Capability.COLOR_TEMP in profile.capabilities


def _one_colour(pieces: Sequence[Segment]) -> tuple[int, int, int] | None:
    """The one colour every piece shows (``ColorUtils.q:335-349``; one piece always does), None when they
    differ or one is unknown."""
    shown = pieces[0].rgb
    return None if shown is None or any(seg.rgb != shown for seg in pieces) else shown


def _page_colour(profile: DeviceProfile, state: DeviceState, rgb: tuple[int, int, int]) -> parse.ColorReport:
    """One colour as ``DelegateColorV2.W1`` shows it: one of the app's tint colours
    (:func:`~..wire.cct.kelvin_for_tint`) whose Kelvin lies in the device's inclusive range
    (:func:`kelvin_range`) is that colour temperature, with no RGB colour (``KelvinConfig.c:119-122`` when
    ``KelvinConfig.i:193-200`` holds), else it is the RGB colour."""
    span = kelvin_range(profile, state.hardware_version, soft=state.firmware_version)
    tint = kelvin_for_tint(rgb)
    if span is None or tint is None or not span[0] <= tint <= span[1]:
        return rgb, None
    return None, tint


def _apply_head_pair(profile: DeviceProfile, state: DeviceState, pair: parse.HeadPair) -> None:
    """H7061's two-head report (``H70606162Info4Detail.I``: SubMode4Color's colours and relative
    brightness, the two head swatches and their "N%" of ``H7061ColorMode.updateColor`` /
    ``updateBrightness``): head 1 is segment 0 and head 2 segment 1 (the colour page's posSet over its 2
    pieces), each brightness clamped to the 0..100 state holds. The whole device has a colour only while
    both heads show the same one, which the stock colour page shows as :func:`_as_shown` reads it: an
    in-range Kelvin tint as that colour temperature (``DelegateColorV2.W1``, every piece the same colour),
    as :func:`apply_rgb` folds the same pick. The report carries no Kelvin field (its default parse's
    ``s16be(r1, g1)`` is head 1's red and green), so none is read from it."""
    (bri_a, rgb_a), (bri_b, rgb_b) = pair.heads
    state.segments = parse.merge_segments(state.segments, [
        Segment(0, rgb_a, max(0, min(100, bri_a))), Segment(1, rgb_b, max(0, min(100, bri_b)))])
    shared = rgb_a if rgb_a == rgb_b else None
    state.rgb_color, state.color_temp_kelvin = (
        (None, None) if shared is None else _as_shown(profile, state, (shared, None)))


def apply_piece_kelvin(state: DeviceState, pieces: int, color_mode: bool | None) -> None:
    """The colour-temperature state of a :attr:`~.profile.DeviceProfile.kelvin_from_pieces` device, read from
    the ``pieces`` colour pieces of ``state.segments`` that the poll's walk just set (goodsType 180:
    ``VM4Light.g5`` after the connect read's walk). ``ColorUtils.r`` takes the pieces' one colour when every
    piece shows the same one (``q``) and, when it is one of the app's tint colours (``C``: the whole
    1000-10000 K table, :func:`..wire.cct.kelvin_for_tint`), its Kelvin, else 0. g5 checks no mode: it stores
    that value as the colour mode's Kelvin (``SubMode4Color.s``, over the ``aa 05`` report's), and a non-zero
    one as the screen's held Kelvin too (``syncKelvin`` -> ``Info4Detail.f``), whatever the mode.

    ``color_mode`` is what the same poll's ``aa 05`` report showed: True for the colour sub-mode, the colour
    page (``Info4Detail.V`` / ``O``: 0x15 is mode 9), whose Kelvin :func:`apply_kelvin_report` then folds
    (``color_temp_kelvin`` is set only in that mode); False for another mode (a scene, music, DIY), which
    leaves ``color_temp_kelvin`` as the report left it and updates only ``last_color_temp_kelvin``. Nothing
    changes when the report did not answer (None) or a piece is missing: the app applies nothing from a
    connect read that lost a read (``Compose4InfoBleIot`` fails whole). The report's own Kelvin, which
    :func:`apply_mode` remembered, stays the held one when the pieces give 0, as ``Info4Detail.V`` stored it
    and g5 syncs only a non-zero Kelvin."""
    by_index = {s.index: s.rgb for s in state.segments}
    shown = [by_index.get(i) for i in range(pieces)]
    if color_mode is None or not shown or None in shown:
        return
    first = shown[0]
    kelvin = kelvin_for_tint(first) if first is not None and shown.count(first) == pieces else None
    if color_mode:
        apply_kelvin_report(state, kelvin or 0)
    if kelvin:
        state.last_color_temp_kelvin = kelvin


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
