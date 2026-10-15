"""H6047 plan (two-bar RGBIC TV light bar, ``pact_h6047`` split, goodsType 119).

The H6047 profile was modelled from the decompiled Govee Home 7.7.02 app without hardware. This plan
checks the behaviours issue #13 corrected, one connection, read-back after every step:

* whole-device power is ``33 01`` (the app's main switch); the per-bar buttons are ``33 36 <left> <right>``
* read-back covers the 10 real segments (no phantom segments 10 and 11)
* colour temperature carries the app's tint, and ``aa 05 15`` reads one kelvin per bar
* the gradual flag (``33 a3`` write, ``aa a3`` read)

"left" and "right" are the app's logical bar names: the bars can be swapped during setup, so the
physical bar may be the other one. Scenes are not part of this plan.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ..plan import DEFAULT_SNAPSHOT_KEYS, PlanContext, SkuPlan, Step, register_plan

SEGMENTS = 10
LEFT, RIGHT = range(0, 5), range(5, 10)
_KEYS = (*DEFAULT_SNAPSHOT_KEYS, "zone_power", "zone_color_temp_kelvin", "gradual", "segments")


def _bars(st: dict[str, Any], left: bool, right: bool) -> bool:
    return bool(st.get("zone_power") == {0: left, 1: right})


def _colour(st: dict[str, Any], indices: range, rgb: tuple[int, int, int]) -> bool:
    """Every segment in ``indices`` read back as ``rgb`` (a diagnostic: the device may scale it)."""
    by_index = {s.index: s for s in st.get("segments") or []}
    return all(i in by_index and by_index[i].rgb == rgb for i in indices)


def _ten_segments(st: dict[str, Any]) -> bool:
    return [x.index for x in st.get("segments") or []] == list(range(SEGMENTS))


def _kelvin(st: dict[str, Any], left: int | None, right: int | None) -> bool:
    expected = {i: k for i, k in ((0, left), (1, right)) if k}
    return bool(st.get("zone_color_temp_kelvin") == expected)


def build_steps(ctx: PlanContext) -> list[Step]:
    dev = ctx.dev
    both_note = "33 36 per bar; read-back is aa 36 [left][right]"
    s: list[Step] = [
        Step("segment_readback_count", _nothing, _ten_segments,
             "Nothing was sent. Press y to continue (the read-back must list exactly segments 0..9).",
             note="the old read-back reported two phantom segments, 10 and 11"),
        Step("power_on", lambda: dev.set_power(True), lambda st: st["is_on"] is True,
             "Is the light ON? (whole-device power frame is 33 01 01)",
             note="whole-device power: 33 01 01"),
        Step("zone_left_off", lambda: dev.set_zone_power("left", False), lambda st: _bars(st, False, True),
             "Is exactly ONE bar OFF (the other still on)?", note=both_note),
        Step("zone_left_on", lambda: dev.set_zone_power("left", True), lambda st: _bars(st, True, True),
             "Are BOTH bars ON again?", note=both_note),
        Step("zone_right_off", lambda: dev.set_zone_power("right", False), lambda st: _bars(st, True, False),
             "Is exactly ONE bar OFF, and is it the OTHER bar than before?", note=both_note),
        Step("whole_on_with_one_bar_off", lambda: dev.set_power(True), lambda st: _bars(st, True, True),
             "After the whole-device ON (33 01 01), are BOTH bars ON?",
             note="does the main switch relight a bar that was off? (diagnostic)"),
        Step("power_off", lambda: dev.set_power(False),
             lambda st: st["is_on"] is False and _bars(st, False, False),
             "Are BOTH bars OFF? (whole-device power is 33 01 00)",
             note="whole-device power: 33 01 00"),
        Step("power_on_again", lambda: dev.set_power(True), lambda st: st["is_on"] is True,
             "Are the bars ON again?"),
    ]
    for pct, q in ((100, "Is it at FULL brightness?"), (1, "Is it VERY DIM but still lit?"),
                   (50, "Is it at roughly HALF brightness?")):
        s.append(Step(f"brightness_{pct}", lambda pct=pct: dev.set_brightness(pct),
                      lambda st, pct=pct: st["brightness"] == pct, q,
                      note="percent on the wire (33 04 <pct>)"))
    for label, rgb in (("red", (255, 0, 0)), ("green", (0, 255, 0)), ("blue", (0, 0, 255))):
        s.append(Step(f"rgb_{label}", lambda rgb=rgb: dev.set_rgb(rgb),
                      lambda st, rgb=rgb: _colour(st, range(SEGMENTS), rgb),
                      f"Are both bars solid {label.upper()} (every segment)?",
                      note="read-back = the 10 aa a5 segment colours"))
    s.append(Step("rgb_left_red_right_blue",
                  _then(lambda: dev.set_zone_rgb("left", (255, 0, 0)),
                        lambda: dev.set_zone_rgb("right", (0, 0, 255))),
                  lambda st: _colour(st, LEFT, (255, 0, 0)) and _colour(st, RIGHT, (0, 0, 255)),
                  "Is one bar RED and the other BLUE?", note="left = segments 0-4, right = 5-9"))
    for k, q in ((2200, "Are both bars a very WARM (orange-ish) white?"),
                 (4000, "Are both bars a NEUTRAL white?"),
                 (6500, "Are both bars a COOL (bluish) white?")):
        s.append(Step(f"color_temp_{k}", lambda k=k: dev.set_color_temp(k),
                      lambda st, k=k: _kelvin(st, k, k) and st["color_temp_kelvin"] == k, q,
                      note="15 01 ff ff ff <K> <tint> <mask 03 ff>; read-back aa 05 15 [gradual][kL][kR]"))
    s.append(Step("color_temp_left_2700_right_6500",
                  _then(lambda: dev.set_zone_color_temp("left", 2700),
                        lambda: dev.set_zone_color_temp("right", 6500)),
                  lambda st: _kelvin(st, 2700, 6500) and st["color_temp_kelvin"] is None,
                  "Is one bar WARM white and the other COOL white?",
                  note="the read-back must show a different kelvin per bar"))
    s.append(Step("color_temp_right_only_4000", lambda: dev.set_zone_color_temp("right", 4000),
                  lambda st: _kelvin(st, 2700, 4000),
                  "Did only the second bar change (to a neutral white)?"))
    s.append(Step("color_temp_whole_4000", lambda: dev.set_color_temp(4000),
                  lambda st: _kelvin(st, 4000, 4000) and st["color_temp_kelvin"] == 4000,
                  "Are both bars the same NEUTRAL white?"))
    s.append(Step("gradual_on", lambda: dev.set_gradual(True), lambda st: st["gradual"] is True,
                  "Did the light carry on normally (no flicker, no colour change)?",
                  note="33 a3 01; read-back aa a3 (no visible effect expected)"))
    s.append(Step("gradual_off", lambda: dev.set_gradual(False), lambda st: st["gradual"] is False,
                  "Did the light carry on normally (no flicker, no colour change)?",
                  note="33 a3 00"))
    return s


async def _nothing() -> None:
    """A read-back-only step: nothing is sent."""


def _then(*calls: Callable[[], Awaitable[None]]) -> Callable[[], Awaitable[None]]:
    """One step action that awaits each call in order."""
    async def run() -> None:
        for call in calls:
            await call()
    return run


async def restore(dev: Any, st: dict[str, Any]) -> None:
    """Put the bars back from the initial snapshot: gradual, brightness, colour (whole-device or
    per-bar kelvin, else the per-segment colours that were read back), then power."""
    if st.get("gradual") is not None:
        await dev.set_gradual(bool(st["gradual"]))
    if st.get("brightness"):
        await dev.set_brightness(st["brightness"])
    kelvins = st.get("zone_color_temp_kelvin") or {}
    if st.get("scene_code") is not None:
        await dev.set_scene(st["scene_code"])
    elif kelvins:
        for index, name in ((0, "left"), (1, "right")):
            if index in kelvins:
                await dev.set_zone_color_temp(name, kelvins[index])
    elif st.get("segments"):
        runs: dict[tuple[int, int, int], list[int]] = {}
        for seg in st["segments"]:
            if seg.rgb is not None:
                runs.setdefault(seg.rgb, []).append(seg.index)
        for rgb, indices in runs.items():
            await dev.set_segment_rgb(indices, rgb)
    bars = st.get("zone_power") or {}
    if st.get("is_on") is False:
        await dev.set_power(False)
    elif st.get("is_on"):
        await dev.set_power(True)
        for index, name in ((0, "left"), (1, "right")):
            if bars.get(index) is False:
                await dev.set_zone_power(name, False)


PLAN = register_plan(SkuPlan(
    sku="H6047",
    title="H6047 two-bar light bar",
    build_steps=build_steps,
    snapshot_keys=_KEYS,
    restore=restore,
    intro="H6047: whole-device power (33 01) and per-bar power (33 36), brightness, RGB, colour "
          "temperature (whole and per bar), gradual and the 10-segment read-back. Left/right are the "
          "Govee app's logical bar names (the bars can be swapped during setup).",
))
