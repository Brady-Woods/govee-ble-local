"""Snapshot the device's start state, put it back at the end, and check how close the result is.

The start state is held in memory only: ``Device.snapshot()`` after the first poll, which fields that poll
really read back (:func:`.readback.carried`), a plug's relay bits, the advertised on/off and, where nothing
else tells it, the tester's answer. :func:`restore_start` polls once (a write cut short by a Ctrl-C or an
error applies nothing to the library's state, though its frame may have landed), then puts back power,
brightness, colour (a scene, per-bar Kelvin, whole-device Kelvin, per-segment colours and levels, or a
whole-device RGB) and zone power, each action on its own so one error doesn't skip the rest. A scene is put
back the way the library activates one: by its catalog name (``set_scene_by_name``, with its upload where it
needs one), by code only where the catalog doesn't list it, after the segment levels the run changed.
:func:`check_restore` polls once more and compares what that poll reads back with the start (a plug: its
outlets' relay bits too; in a scene, the segments' levels but not their colours, which the scene paints).
Each unrestorable entry starts with what it is about (``colour:``, ``scene:``, ``brightness:``, ``power:``,
``relay:``): :func:`unrestorable_written` tells which the run's writes changed.
"""
from __future__ import annotations

import copy
import dataclasses
from collections.abc import Awaitable, Callable
from typing import Any

from .readback import FIELDS, FrameWatch, carried, outlet_mask, poll, relay_bits

SCENE_NOTE = ("scene: re-activated by code (the bundled catalog doesn't list it at this scene set), so a DIY "
              "or uploaded effect shows whatever the device now holds for that code")
# What each unrestorable entry is about (its prefix), as the state a write step changes (runner: a step's
# ``changes``, from its fields).
CHANGES: dict[str, str] = {
    "rgb_color": "colour", "color_temp_kelvin": "colour", "segments": "colour", "scene_code": "colour",
    "zone_color_temp_kelvin": "colour", "brightness": "brightness", "segment_brightness": "brightness",
    "is_on": "power", "zone_power": "power", "gradual": "gradual"}
_ABOUT = {"colour": "colour", "scene": "colour", "brightness": "brightness", "power": "power",
          "relay": "power"}
Ask = Callable[[str], Awaitable[str | None]]
Describe = Callable[[BaseException], str]


@dataclasses.dataclass
class StartState:
    """The device as the run found it."""

    state: Any                              # a DeviceState copy (Device.snapshot())
    read: frozenset[str]                    # the fields the first poll really read back
    kind: str = "light"
    advertised_on: bool | None = None       # the advertisement's on/off (None: it carries none)
    answered_on: bool | None = None         # the tester's answer, where nothing else tells it
    active_scene: str | None = None
    segment_count: int = 0
    relay_bits: int | None = None           # a plug's aa 01 relay byte (one bit per outlet), if read

    @property
    def is_on(self) -> bool | None:
        if "is_on" in self.read:
            return bool(self.state.is_on) if self.state.is_on is not None else None
        return self.advertised_on if self.advertised_on is not None else self.answered_on

    def summary(self) -> dict[str, Any]:
        """What the report records of the start (no identifying field is among these)."""
        st = self.state
        return {"is_on": self.is_on, "read": sorted(self.read),
                "power_source": ("read-back" if "is_on" in self.read else "advertisement"
                                 if self.advertised_on is not None else "tester"
                                 if self.answered_on is not None else None),
                "brightness": st.brightness if "brightness" in self.read else None,
                "rgb_color": st.rgb_color if "rgb_color" in self.read else None,
                "color_temp_kelvin": st.color_temp_kelvin if "color_temp_kelvin" in self.read else None,
                "scene_code": st.scene_code if "scene_code" in self.read else None,
                "active_scene": self.active_scene, "segment_count": self.segment_count,
                "zone_power": dict(st.zone_power) if "zone_power" in self.read else None,
                "gradual": st.gradual if "gradual" in self.read else None,
                "relay_bits": f"{self.relay_bits:08b}" if self.relay_bits is not None else None}


async def capture_start(dev: Any, adv: Any, frames: list[Any], *, kind: str, ask: Ask | None = None,
                        polled: Any = None, assigned: frozenset[str] | None = None) -> StartState:
    """The start state after the first poll (``frames``: that poll's window of the FrameWatch; ``polled``:
    the state it returned, kept where the device has no ``snapshot()``; ``assigned``: the fields that poll
    set, :func:`.readback.poll`)."""
    read = frozenset(f for f, reason in carried(dev, frames, assigned=assigned).items() if reason is None)
    advertised: bool | None = None
    if "is_on" not in read and adv is not None:
        try:
            advertised = dev.advertised_on(adv)
        except Exception:
            advertised = None
    answered: bool | None = None
    if "is_on" not in read and advertised is None and ask is not None:
        reply = await ask("Nothing reads its power back: is the device ON right now?")
        answered = {"yes": True, "no": False}.get(reply or "")
    try:
        scene = dev.active_scene
    except Exception:
        scene = None
    take = getattr(dev, "snapshot", None)
    state = take() if callable(take) else copy.copy(polled)
    count = int(getattr(dev, "segment_count", 0) or 0)
    relay = relay_bits(frames) if kind == "plug" else None
    return StartState(state, read, kind, advertised, answered, scene, count, relay)


@dataclasses.dataclass
class Outcome:
    actions: list[str] = dataclasses.field(default_factory=list)
    errors: list[str] = dataclasses.field(default_factory=list)
    unrestorable: list[str] = dataclasses.field(default_factory=list)


def _caps(dev: Any) -> set[str]:
    return {str(c) for c in getattr(dev, "capabilities", ())}


async def restore_start(dev: Any, start: StartState, describe: Describe) -> Outcome:
    """Put the start state back as far as the API allows; never raises an error (each action on its own).

    It polls first: a write that was cut short (a Ctrl-C, a timeout) applied nothing to ``dev.state``
    though its frame may have reached the device, so the comparisons below (relay, gradual, zones, segment
    levels) are made against what the device reads back. Where that poll fails, or doesn't set the field
    (:func:`.readback.poll`), those are sent anyway."""
    out = Outcome()
    caps, p, st, read = _caps(dev), dev.profile, start.state, start.read
    fresh = getattr(p, "readback", "none") != "none" and start.kind != "card"
    polled: frozenset[str] = frozenset()
    if fresh:
        try:
            polled = (await poll(dev))[1] or frozenset()
        except Exception:
            fresh = False

    def known(field: str) -> bool:
        """The restore's poll set ``field``: ``dev.state`` holds what the device reports, not an intent."""
        return fresh and field in polled

    async def do(label: str, fn: Callable[..., Awaitable[None]], *args: Any) -> None:
        try:
            await fn(*args)
            out.actions.append(label)
        except Exception as err:
            out.errors.append(f"{label}: {describe(err)}")

    started_on = start.is_on
    if start.kind == "plug":
        if started_on is None:
            out.unrestorable.append("relay: start state unknown")
        elif not known("is_on") or dev.state.is_on is not started_on:
            await do(f"relay {'on' if started_on else 'off'}", dev.set_power, started_on)
        return out
    colour = _colour_actions(dev, start, out, known("segments"))
    restore_brightness = "brightness" in caps and "brightness" in read and bool(st.brightness)
    if "brightness" in caps and not restore_brightness:
        out.unrestorable.append("brightness: unknown at the start (not read back)")
    if getattr(p, "gradual", False) and "gradual" in read and st.gradual is not None \
            and (not known("gradual") or dev.state.gradual != st.gradual):
        await do("gradual", dev.set_gradual, bool(st.gradual))
    if started_on is None:
        out.unrestorable.append("power: unknown at the start")
    if started_on or colour or restore_brightness:
        await do("power on", dev.set_power, True)
    if restore_brightness:
        await do(f"brightness {st.brightness}", dev.set_brightness, int(st.brightness))
    for label, fn, args in colour:
        await do(label, fn, *args)
    if started_on and dev.zones and "zone_power" in read:
        for zone in dev.zones:
            want = st.zone_power.get(zone.power_index)
            held = dev.state.zone_power.get(zone.power_index)
            if want is not None and (not known("zone_power") or held != want):
                await do(f"zone {zone.name} {'on' if want else 'off'}", dev.set_zone_power, zone.name, want)
    if started_on is False:
        await do("power off", dev.set_power, False)
    return out


Action = tuple[str, Callable[..., Awaitable[None]], tuple[Any, ...]]


def _colour_actions(dev: Any, start: StartState, out: Outcome, fresh: bool) -> list[Action]:
    """The colour to put back, by precedence: scene, per-bar Kelvin, whole Kelvin, mixed segments, RGB."""
    caps, p, st, read = _caps(dev), dev.profile, start.state, start.read
    if not caps & {"rgb", "color_temp", "scenes"}:
        return []
    if "scene_code" in read and st.scene_code is not None and "scenes" in caps:
        # The segment levels the run changed first, then the scene, as the library activates it: by name
        # (with its upload) where the catalog lists the start's scene, else by code.
        levels = _segment_levels(dev, start, fresh) if "segments" in read else []
        name = start.active_scene
        if name and name in _listed_scenes(dev):
            return [*levels, (f"scene {name!r} by name (uploaded where it needs it)", dev.set_scene_by_name,
                              (name,))]
        out.unrestorable.append(SCENE_NOTE)
        return [*levels, (f"scene {st.scene_code} by code", dev.set_scene, (int(st.scene_code),))]
    actions: list[Action] = []
    segs = [s for s in (st.segments or []) if s.rgb is not None]
    if getattr(p, "bar_switch", False) and "zone_color_temp_kelvin" in read and st.zone_color_temp_kelvin:
        actions = [(f"zone {z.name} {st.zone_color_temp_kelvin[z.power_index]} K", dev.set_zone_color_temp,
                    (z.name, int(st.zone_color_temp_kelvin[z.power_index])))
                   for z in dev.zones if z.power_index in st.zone_color_temp_kelvin]
    elif "color_temp_kelvin" in read and st.color_temp_kelvin:
        actions = [(f"{st.color_temp_kelvin} K", dev.set_color_temp, (int(st.color_temp_kelvin),))]
    elif "segments" in read and segs and "segments" in caps:
        runs: dict[tuple[int, int, int], list[int]] = {}
        for seg in segs:
            runs.setdefault(tuple(seg.rgb), []).append(seg.index)
        if len(runs) == 1 and "rgb" in caps:
            rgb = next(iter(runs))
            actions = [(f"rgb {rgb}", dev.set_rgb, (rgb,))]
        else:
            actions = [(f"segments {idx[0]}..{idx[-1]} rgb {rgb}", dev.set_segment_rgb, (idx, rgb))
                       for rgb, idx in runs.items()]
    elif "rgb_color" in read and st.rgb_color:
        actions = [(f"rgb {tuple(st.rgb_color)}", dev.set_rgb, (tuple(st.rgb_color),))]
    else:
        out.unrestorable.append("colour: not read back at the start (or a mode the library doesn't know: "
                                "music, DIY), so it is left as the plan's last colour")
        return []
    if "segments" in read:
        actions += _segment_levels(dev, start, fresh)
    return actions


def _listed_scenes(dev: Any) -> list[str]:
    """The scene names the device lists now (``Device.scene_names``), or none where it can't say."""
    try:
        return list(dev.scene_names)
    except Exception:
        return []


def _segment_levels(dev: Any, start: StartState, fresh: bool) -> list[Action]:
    """Per-segment brightness back where it was read and the run changed it (every level, when the
    restore's first poll failed)."""
    if "segments" not in _caps(dev) or not getattr(dev, "supports_segment_brightness", True):
        return []
    now = {s.index: s.brightness for s in (dev.state.segments or [])} if fresh else {}
    levels: dict[int, list[int]] = {}
    for seg in start.state.segments or []:
        if seg.brightness is not None and now.get(seg.index) != seg.brightness:
            levels.setdefault(int(seg.brightness), []).append(seg.index)
    return [(f"segments {idx[0]}..{idx[-1]} at {lvl}%", dev.set_segment_brightness, (idx, lvl))
            for lvl, idx in sorted(levels.items()) if lvl > 0]


@dataclasses.dataclass
class RestoreCheck:
    result: str                 # exact / close / partial / failed / unverified / not_needed
    unrestorable: list[str] = dataclasses.field(default_factory=list)
    diffs: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)
    errors: list[str] = dataclasses.field(default_factory=list)
    actions: list[str] = dataclasses.field(default_factory=list)
    basis: str | None = None    # "readback" / "observed"

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _differs(field: str, want: Any, got: Any, raw: bool, *, colours: bool = True) -> bool:
    """``colours``: compare the segments' colours too (not in a scene, which paints them)."""
    if field == "brightness" and raw and isinstance(want, int) and isinstance(got, int):
        return abs(want - got) > 1
    if field == "segments":
        have = {s.index: s for s in got or []}
        for seg in want or []:
            now = have.get(seg.index)
            if now is None:
                continue
            if colours and seg.rgb is not None and (now.rgb is None or tuple(now.rgb) != tuple(seg.rgb)):
                return True
            if seg.brightness is not None and now.brightness is not None and now.brightness != seg.brightness:
                return True
        return False
    if isinstance(want, (tuple, list)) and isinstance(got, (tuple, list)):
        return tuple(want) != tuple(got)
    return bool(want != got)


def _show(field: str, value: Any) -> Any:
    if field == "segments":
        return [[s.index, list(s.rgb) if s.rgb else None, s.brightness] for s in value or []]
    return value


async def check_restore(dev: Any, start: StartState, outcome: Outcome | None, watch: FrameWatch | None,
                        *, ask: Ask | None = None) -> RestoreCheck:
    """Poll once more and compare what it reads back with the start."""
    if outcome is None:
        return RestoreCheck("not_needed")
    check = RestoreCheck("exact", list(outcome.unrestorable), {}, list(outcome.errors), list(outcome.actions))
    profile = getattr(dev, "profile", None)
    if profile is None or start.kind == "card" or getattr(profile, "readback", "none") == "none":
        check.result = "failed" if check.errors else "unverified"
        if not check.errors and ask is not None:
            reply = await ask("Restore done: is the device back as it was at the start?")
            if reply in ("yes", "no"):
                check.result, check.basis = ("close" if reply == "yes" else "partial"), "observed"
        return check
    mark = watch.mark() if watch is not None else 0
    try:
        post, assigned = await poll(dev)
    except Exception as err:
        check.errors.append(f"final read-back: {type(err).__name__}")
        check.result = "failed"
        return check
    now = carried(dev, watch.window(mark) if watch is not None else [], assigned=assigned)
    raw = bool(getattr(profile, "brightness_range", None) or getattr(profile, "brightness_by_pact", ()))
    in_scene = "scene_code" in start.read and start.state.scene_code is not None
    compared = 0
    for field in FIELDS:
        if field not in start.read or now.get(field) is not None:
            continue
        compared += 1
        want, got = getattr(start.state, field, None), getattr(post, field, None)
        if _differs(field, want, got, raw, colours=not in_scene):
            check.diffs[field] = {"start": _show(field, want), "end": _show(field, got)}
    end_bits = relay_bits(watch.window(mark)) if watch is not None else None
    if start.kind == "plug" and start.relay_bits is not None and end_bits is not None:
        # is_on is "any outlet on": a 2- or 3-outlet plug's outlets are compared bit by bit
        compared += 1
        mask = outlet_mask(profile) or 0xFF
        if (start.relay_bits ^ end_bits) & mask:
            check.diffs["relay_bits"] = {"start": f"{start.relay_bits & mask:08b}",
                                         "end": f"{end_bits & mask:08b}"}
    check.basis = "readback" if compared else None
    if check.errors:
        check.result = "failed"
    elif check.diffs:
        check.result = "partial"
    elif not compared:
        check.result = "unverified"
        if ask is not None:
            reply = await ask("Restore done (nothing it read at the start was read back): is the device "
                              "back as it was?")
            if reply in ("yes", "no"):
                check.result, check.basis = ("close" if reply == "yes" else "partial"), "observed"
    elif check.unrestorable:
        check.result = "close"
    return check


def unrestorable_written(unrestorable: list[str], written: set[str]) -> list[str]:
    """The entries of ``unrestorable`` about state the run's writes changed (``written``: :data:`CHANGES`
    values): what the run left changed for good, which keeps a restore that is only ``close`` (or
    ``unverified``) from clearing the gate."""
    return [u for u in unrestorable if _ABOUT.get(u.split(":", 1)[0]) in written]
