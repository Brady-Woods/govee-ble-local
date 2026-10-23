"""The generic plan: steps built from a device's profile and Device API, for any SKU without a hand-written
plan (``plans/<sku>.py`` still wins for its SKU; ``--generic`` forces this one).

The steps are built after the connect and the first poll, so the segment count, the Kelvin range, the scene
list and the pact are known. Each step is one command, then a read-back where the row reads it
(``Device.update()``; a card row's advertisement), judged by :mod:`.readback`:

1. read: ``readback_baseline`` (held-link lights), ``advert_state`` (card rows), ``session_setup`` (rows
   with an unlock or a time sync) and ``relay_read`` (plugs);
2. power off / on; brightness 25 % then 100 %; whole-device RGB red; colour temperature at the range's
   min, mid and max; an RGB pick of the mid Kelvin's tint (the app sends it as colour temperature);
3. segments: a blue baseline, then RGB and brightness on the first and last segment, and colour
   temperature on them where the row allows it;
4. per zone: off, on, its own colour, a colour temperature;
5. the first catalog scene; the gradual flag there and back;
6. expected refusals, each with its group, where the profile has the capability but a narrower fact
   refuses it (``segment_color_temp`` False, no segment brightness, ``gradual_read_only``, a brightness range
   that waits for the pact): pass when ``GoveeBleNotSupported`` is raised and nothing was sent.

A plug gets only read steps unless the tester allowed its relay to toggle (``--toggle-plugs`` and the
run-time confirmation), and then one toggle and back. Fields added to ``DeviceProfile`` after 1.6 are read
with ``getattr(..., default)``, so the tool loads against an older wheel.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from .plan import DEFAULT_SNAPSHOT_KEYS, RGB, DeviceKind, PlanContext, SkuPlan, Step

if TYPE_CHECKING:
    from govee_ble_local import Device

GENERIC_KEYS: tuple[str, ...] = (
    *DEFAULT_SNAPSHOT_KEYS, "segments", "zone_power", "zone_color_temp_kelvin", "gradual")
RED: RGB = (255, 0, 0)
BLUE: RGB = (0, 0, 255)
ZONE_COLOURS: tuple[tuple[str, RGB], ...] = (
    ("GREEN", (0, 255, 0)), ("MAGENTA", (255, 0, 255)), ("CYAN", (0, 255, 255)), ("YELLOW", (255, 255, 0)))
SEGMENT_LEVEL = 20


def device_kind(profile: Any) -> DeviceKind:
    """``plug`` for a relay row, ``card`` for a device-list card row (one session per command), else
    ``light``."""
    if getattr(profile, "relay", False):
        return "plug"
    if getattr(profile, "session", "held") == "per_command":
        return "card"
    return "light"


def generic_plan(dev: Device) -> SkuPlan:
    """The generic plan for ``dev`` (not registered in ``PLANS``)."""
    kind = device_kind(dev.profile)
    return SkuPlan(sku=dev.sku, title=f"{dev.sku} generic plan ({kind})", build_steps=generic_steps,
                   snapshot_keys=GENERIC_KEYS,
                   intro="Generic plan from the device's profile: each step is one command, then a read-back "
                         "where the row reads it; the start state is restored at the end.")


async def _nothing() -> None:
    """A read step: nothing is sent; the poll after it is the step."""


def _slug(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", name.lower()).strip("_") or "zone"


def _caps(dev: Any) -> set[str]:
    return {str(c) for c in dev.capabilities}


def generic_steps(ctx: PlanContext) -> list[Step]:
    kind = ctx.kind
    if kind == "plug":
        return _plug_steps(ctx)
    if kind == "card":
        return _card_steps(ctx)
    steps: list[Step] = [*_session(ctx), _baseline(ctx)]
    for build in (_power, _brightness, _whole_rgb, _cct, _tint_pick, _segments, _zones, _scene, _gradual):
        steps += build(ctx)
    return steps


# -- read steps ---------------------------------------------------------------------------------


def _baseline(ctx: PlanContext) -> Step:
    read = tuple(sorted(ctx.start.read)) if ctx.start is not None else ()
    return Step("readback_baseline", _nothing, None, "", kind="read", fields=read,
                note="a second poll, nothing sent: every field the first poll read must read the same")


def _session_check(ctx: PlanContext) -> Callable[[], tuple[str, str | None]]:
    """The connect's unlock (``33 b2``: the secret, or the empty check) sent and acked on a connect that held
    (the library masks the ack's status in its capture; a refusal fails the connect), and a plug's time sync
    (``33 b5``) sent and acked with status 0."""
    profile = ctx.dev.profile

    def check() -> tuple[str, str | None]:
        watch = ctx.watch
        if watch is None:
            return "unverifiable", "no frame capture"
        verdict, notes = "pass", []
        if profile.requires_secret:
            empty = getattr(profile, "empty_secret_check", False) and not ctx.has_secret
            label = "empty 33 b2 check" if empty else "33 b2 unlock"
            if not watch.sent("33b2"):
                verdict = "unverifiable"
                notes.append("no secret: no 33 b2 unlock sent")
            elif watch.acked("33b2") is None:
                verdict = "unverifiable"
                notes.append(f"{label} sent, no ack")
            else:
                # The library masks the whole 33 b2 frame in its capture, the ack's status too; a refusal
                # fails the connect with GoveeBleAuthError, so an ack on a connect that held is a pass.
                notes.append(f"{label} acked (the connect held)")
        if profile.relay:
            if not watch.sent("33b5"):
                return "fail", "no 33 b5 time sync sent"
            status = watch.acked("33b5")
            if status is None:
                verdict = "unverifiable"
                notes.append("33 b5 time sync sent, no ack")
            elif status != 0:
                return "fail", f"33 b5 time sync refused (status {status})"
            else:
                notes.append("33 b5 time sync acked")
        return verdict, "; ".join(notes) or None

    return check


def _session(ctx: PlanContext) -> list[Step]:
    p = ctx.dev.profile
    if not (p.requires_secret or p.relay):
        return []
    return [Step("session_setup", _nothing, None, "", kind="read", check=_session_check(ctx),
                 note="the connect's unlock / time sync, sent and acked")]


# -- plugs and card rows ------------------------------------------------------------------------


def _plug_steps(ctx: PlanContext) -> list[Step]:
    dev = ctx.dev
    steps = [*_session(ctx),
             Step("relay_read", _nothing, None, "", kind="read", fields=("is_on",),
                  note="aa 01 relay read")]
    start_on = ctx.start.is_on if ctx.start is not None else None
    if not ctx.toggle_ok:
        return steps
    skip = "" if ctx.has_secret else "no secret"
    if start_on is None and not skip:
        skip = "relay start state unknown: it couldn't be restored"
    first = not start_on if start_on is not None else False
    steps += [
        Step("relay_toggle", partial(dev.set_power, first), None,
             f"Did the plug switch {'ON' if first else 'OFF'} (and what it powers with it)?",
             fields=("is_on",), skip=skip),
        Step("relay_back", partial(dev.set_power, not first), None,
             f"Is the plug back {'ON' if not first else 'OFF'}?", fields=("is_on",), skip=skip),
    ]
    return steps


def _card_steps(ctx: PlanContext) -> list[Step]:
    dev, caps = ctx.dev, _caps(ctx.dev)
    start_on = ctx.start.is_on if ctx.start is not None else None
    skip = "" if start_on is not None else "start power unknown: it couldn't be restored"
    def advert_check() -> tuple[str, str | None]:
        if ctx.start is not None and ctx.start.advertised_on is not None:
            return "pass", "start power from the advertisement"
        if ctx.start is not None and ctx.start.answered_on is not None:
            return "pass", "start power from the tester's answer"
        return "unverifiable", "the advertisement carries no on/off"

    steps = [Step("advert_state", _nothing, None, "", kind="read", check=advert_check,
                  note="the start power, from the advertisement (or the tester)")]
    note = "one session per command (the app's device-list card); read back from the next advertisement"
    if "power" in caps:
        steps += [Step("power_off", partial(dev.set_power, False), None, "Is it OFF?",
                       fields=("is_on",), note=note, skip=skip),
                  Step("power_on", partial(dev.set_power, True), None, "Is it ON again?",
                       fields=("is_on",), note=note, skip=skip)]
    if "brightness" in caps:
        steps += [Step("brightness_25", partial(dev.set_brightness, 25), None, "Is it at about a QUARTER?",
                       note="no read-back on a card row: the tester's answer decides", skip=skip),
                  Step("brightness_100", partial(dev.set_brightness, 100), None, "Is it at FULL brightness?",
                       note="no read-back on a card row: the tester's answer decides", skip=skip)]
    return steps


# -- light write steps --------------------------------------------------------------------------


def _power(ctx: PlanContext) -> list[Step]:
    dev, p = ctx.dev, ctx.dev.profile
    if "power" not in _caps(dev):
        return []
    fields: tuple[str, ...] = ("is_on", "zone_power") if dev.zones else ("is_on",)
    note = ""
    if getattr(p, "light_switch_power", False):
        note = "33 30 light switch, not the 33 01 main switch: white noise keeps playing"
    off_q = "Is the LIGHT off" + (" (white noise may keep playing)?" if note else "?")
    return [Step("power_off", partial(dev.set_power, False), None, off_q, fields=fields, note=note),
            Step("power_on", partial(dev.set_power, True), None, "Is the light ON again?", fields=fields,
                 note=note)]


def _brightness_waits(dev: Any) -> bool:
    return bool(getattr(dev.profile, "brightness_by_pact", ())) and dev.pact is None


def _brightness(ctx: PlanContext) -> list[Step]:
    dev = ctx.dev
    if "brightness" not in _caps(dev) or _brightness_waits(dev):
        return _refusals(ctx, "brightness")
    raw = getattr(dev.profile, "brightness_range", None)
    note = f"raw {raw[0]}..{raw[1]} on the wire" if raw else ""
    return [Step("brightness_25", partial(dev.set_brightness, 25), None, "Is it at about a QUARTER?",
                 fields=("brightness",), note=note),
            Step("brightness_100", partial(dev.set_brightness, 100), None, "Is it at FULL brightness?",
                 fields=("brightness",), note=note)]


def _whole_rgb(ctx: PlanContext) -> list[Step]:
    dev = ctx.dev
    if "rgb" not in _caps(dev):
        return []
    return [Step("rgb_red", partial(dev.set_rgb, RED), None, "Is it solid RED (every part)?",
                 fields=("rgb_color", "color_temp_kelvin"))]


def _kelvin_range(dev: Any) -> tuple[int, int, int] | None:
    """``(min, mid, max)`` Kelvin, the mid on the app's 100 K grid; None without a known range."""
    lo, hi = dev.min_kelvin, dev.max_kelvin
    if lo is None or hi is None:
        return None
    from govee_ble_local.wire import build

    return lo, build.normalize_kelvin((lo + hi) // 2, lo, hi), hi


def _cct(ctx: PlanContext) -> list[Step]:
    dev = ctx.dev
    span = _kelvin_range(dev)
    if "color_temp" not in _caps(dev) or span is None:
        return []
    words = ("WARM", "NEUTRAL", "COOL")
    return [Step(f"color_temp_{k}", partial(dev.set_color_temp, k), None, f"Is it a {w} white ({k} K)?",
                 fields=("color_temp_kelvin", "rgb_color"))
            for k, w in zip(span, words, strict=True)]


def _tint_pick(ctx: PlanContext) -> list[Step]:
    dev = ctx.dev
    span = _kelvin_range(dev)
    if "rgb" not in _caps(dev) or span is None:
        return []
    from govee_ble_local.wire import cct

    mid = span[1]
    tint = cct.tint_for_kelvin(mid)
    if tint == (0, 0, 0):
        return []
    return [Step(f"rgb_pick_tint_{mid}", partial(dev.set_rgb, tint), None,
                 f"Is it the same white as at {mid} K (not a peach or orange colour)?",
                 fields=("color_temp_kelvin", "rgb_color"),
                 note=f"RGB pick {bytes(tint).hex()} = the {mid} K tint: the app sends colour temperature")]


def _ends(dev: Any) -> tuple[int, ...]:
    n = int(dev.segment_count or 0)
    return tuple(sorted({0, n - 1})) if n >= 1 else ()


def _segment_brightness_ok(dev: Any) -> bool:
    return bool(getattr(dev, "supports_segment_brightness", getattr(dev.profile, "segment_brightness", True)))


def _segment_cct_ok(dev: Any) -> bool:
    p = dev.profile
    return bool(getattr(p, "segment_color_temp", True)) and not getattr(p, "whole_device_kelvin", False)


def _segments(ctx: PlanContext) -> list[Step]:
    dev = ctx.dev
    caps = _caps(dev)
    ends = _ends(dev)
    if not {"segments", "rgb"} <= caps or not ends:
        return _refusals(ctx, "segments")
    tag = "_".join(map(str, (ends[0], ends[-1])))
    idx = list(ends)
    which = f"segment{'s' if len(ends) > 1 else ''} {' and '.join(map(str, ends))}"
    steps = [
        Step("segments_baseline_blue", partial(dev.set_rgb, BLUE), None,
             "Is every segment BLUE (the baseline for the segment steps)?", fields=("rgb_color",)),
        Step(f"segment_rgb_{tag}", partial(dev.set_segment_rgb, idx, RED), None,
             f"Are ONLY {which} RED, the others still blue?", fields=("segments",), touched=ends),
    ]
    if _segment_brightness_ok(dev):
        dim = partial(dev.set_segment_brightness, idx, SEGMENT_LEVEL)
        steps.append(Step(f"segment_brightness_{tag}", dim, None,
                          f"Are {which} clearly DIMMER than the others?",
                          fields=("segment_brightness",), touched=ends))
    span = _kelvin_range(dev)
    if "color_temp" in caps and span is not None and _segment_cct_ok(dev):
        steps.append(Step(f"segment_color_temp_{tag}", partial(dev.set_segment_color_temp, idx, span[1]),
                          None, f"Are {which} a NEUTRAL white ({span[1]} K), the others unchanged?",
                          touched=ends, note="no per-segment Kelvin read-back: the tester's answer decides"))
    return steps + _refusals(ctx, "segments")


def _zones(ctx: PlanContext) -> list[Step]:
    dev = ctx.dev
    caps = _caps(dev)
    steps: list[Step] = []
    span = _kelvin_range(dev)
    whole_kelvin = getattr(dev.profile, "whole_device_kelvin", False)
    for i, zone in enumerate(dev.zones):
        z = _slug(zone.name)
        steps += [
            Step(f"zone_{z}_off", partial(dev.set_zone_power, zone.name, False), None,
                 f"Is ONLY the {zone.name} zone off?", fields=("zone_power", "is_on")),
            Step(f"zone_{z}_on", partial(dev.set_zone_power, zone.name, True), None,
                 f"Is the {zone.name} zone ON again?", fields=("zone_power", "is_on")),
        ]
        if {"rgb", "segments"} <= caps and zone.segments:
            word, rgb = ZONE_COLOURS[i % len(ZONE_COLOURS)]
            steps.append(Step(f"zone_{z}_rgb", partial(dev.set_zone_rgb, zone.name, rgb), None,
                              f"Is ONLY the {zone.name} zone {word}?", fields=("segments",),
                              touched=tuple(zone.segments)))
        if "color_temp" in caps and zone.segments and span is not None and not whole_kelvin:
            k = span[0] if i % 2 == 0 else span[2]
            kelvin = partial(dev.set_zone_color_temp, zone.name, k)
            steps.append(Step(f"zone_{z}_color_temp_{k}", kelvin, None,
                              f"Is ONLY the {zone.name} zone a {'WARM' if k == span[0] else 'COOL'} white?",
                              fields=("zone_color_temp_kelvin",)))
    return steps


def _scene(ctx: PlanContext) -> list[Step]:
    dev = ctx.dev
    if "scenes" not in _caps(dev):
        return []
    try:
        names = list(dev.scene_names)
    except Exception:
        return []
    if not names:
        return []
    name = names[0]
    note = "activate only"
    try:
        from govee_ble_local.scenes import load_scenes

        scene = load_scenes(dev.sku).get(name)
        frames = dev.scene_upload_frames(scene) if scene is not None else None
        if frames:
            note = f"uploads {len(frames)} frame(s), then activates"
    except Exception:
        note = "upload unknown"
    return [Step(f"scene_{name}", partial(dev.set_scene_by_name, name), None,
                 f"Scene '{name}': is an effect running (not a plain colour)?", fields=("scene_code",),
                 note=note)]


def _gradual(ctx: PlanContext) -> list[Step]:
    dev = ctx.dev
    if not getattr(dev.profile, "gradual", False):
        return _refusals(ctx, "gradual")
    start = bool(ctx.start.state.gradual) if ctx.start is not None and "gradual" in ctx.start.read else False
    question = "Did the light carry on normally (no flicker, no colour change)?"
    return [Step("gradual_toggle", partial(dev.set_gradual, not start), None, question, fields=("gradual",),
                 note=f"33 a3 {int(not start):02x}; no visible effect expected"),
            Step("gradual_back", partial(dev.set_gradual, start), None, question, fields=("gradual",),
                 note=f"33 a3 {int(start):02x}")]


def _refusal(name: str, action: Any, why: str) -> Step:
    """A step the library must refuse before any I/O (the capability is there, a narrower fact refuses
    it): it passes when ``GoveeBleNotSupported`` is raised and nothing was sent."""
    from govee_ble_local import GoveeBleNotSupported

    return Step(name, action, None, "", refusal=GoveeBleNotSupported, note=why)


def _refusals(ctx: PlanContext, group: str) -> list[Step]:
    """The refusal steps of one step group (``segments``, ``gradual``, ``brightness``), placed with it."""
    dev, p = ctx.dev, ctx.dev.profile
    caps = _caps(dev)
    ends = list(_ends(dev))
    span = _kelvin_range(dev)
    out: list[Step] = []
    if group == "segments" and {"segments", "rgb"} <= caps and ends and not _segment_brightness_ok(dev):
        out.append(_refusal("segment_brightness_refused",
                            partial(dev.set_segment_brightness, ends, SEGMENT_LEVEL),
                            "the row's page has no segment brightness"))
    if group == "segments" and {"segments", "color_temp"} <= caps and ends and span is not None \
            and not _segment_cct_ok(dev):
        out.append(_refusal("segment_color_temp_refused", partial(dev.set_segment_color_temp, ends, span[1]),
                            "segment_color_temp is False (the app sends no Kelvin to part of the device)"))
    if group == "gradual" and getattr(p, "gradual_read_only", False) and not getattr(p, "gradual", False):
        out.append(_refusal("gradual_refused", partial(dev.set_gradual, True),
                            "the row reads the gradual flag, never writes it"))
    if group == "brightness" and "brightness" in caps and _brightness_waits(dev):
        out.append(_refusal("brightness_refused_no_pact", partial(dev.set_brightness, 50),
                            "the brightness range waits for the advertised pact"))
    return out
