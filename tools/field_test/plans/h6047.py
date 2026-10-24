"""H6047 plan (two-bar RGBIC TV light bar, ``pact_h6047`` split, goodsType 119).

The H6047 profile was modelled from the decompiled Govee Home 7.7.02 app without hardware. This plan
checks the behaviours issue #13 corrected, one connection, read-back after every step:

* whole-device power is ``33 01`` (the app's main switch); the per-bar buttons are ``33 36 <left> <right>``
* read-back covers the 10 real segments (no phantom segments 10 and 11)
* colour temperature carries the app's tint, and ``aa 05 15`` reads one kelvin per bar
* the gradual flag (``33 a3`` write, ``aa a3`` read), skipped when the first poll read none

"left" and "right" are the app's logical bar names: the bars can be swapped during setup, so the
physical bar may be the other one. Scenes are not part of this plan.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING

from ..plan import (
    DEFAULT_SNAPSHOT_KEYS,
    RGB,
    Expect,
    PlanContext,
    SkuPlan,
    Snapshot,
    Step,
    brightness_steps,
    register_plan,
    restore_scene,
    rgb_steps,
    state_is,
    tint_pick_step,
)
from ..restore import segment_runs

if TYPE_CHECKING:
    from govee_ble_local import Device

SEGMENTS = 10
LEFT, RIGHT = range(0, 5), range(5, 10)
_KEYS = (*DEFAULT_SNAPSHOT_KEYS, "zone_power", "zone_color_temp_kelvin", "gradual", "segments")


def _bars(st: Snapshot, left: bool, right: bool) -> bool:
    return bool(st.get("zone_power") == {0: left, 1: right})


def _colour(st: Snapshot, indices: range, rgb: RGB) -> bool:
    """Every segment in ``indices`` read back as ``rgb`` (a diagnostic: the device may scale it)."""
    by_index = {s.index: s for s in st.get("segments") or []}
    return all(i in by_index and by_index[i].rgb == rgb for i in indices)


def _all_segments(rgb: RGB) -> Expect:
    """An ``expect``: all 10 segments read back as ``rgb``."""
    return lambda st: _colour(st, range(SEGMENTS), rgb)


def _ten_segments(st: Snapshot) -> bool:
    return [x.index for x in st.get("segments") or []] == list(range(SEGMENTS))


def _kelvin(st: Snapshot, left: int | None, right: int | None) -> bool:
    expected = {i: k for i, k in ((0, left), (1, right)) if k}
    return bool(st.get("zone_color_temp_kelvin") == expected)


def _both_bars_kelvin(kelvin: int) -> Expect:
    """An ``expect``: both bars, and so the whole device, read back ``kelvin``."""
    return lambda st: _kelvin(st, kelvin, kelvin) and st["color_temp_kelvin"] == kelvin


def build_steps(ctx: PlanContext) -> list[Step]:
    dev = ctx.dev
    both_note = "33 36 per bar; read-back is aa 36 [left][right]"
    s: list[Step] = [
        Step("segment_readback_count", _nothing, _ten_segments,
             "Nothing was sent. Press y to continue (the read-back must list exactly segments 0..9).",
             note="the old read-back reported two phantom segments, 10 and 11", kind="read"),
        Step("power_on", partial(dev.set_power, True), state_is("is_on", True),
             "Is the light ON? (whole-device power frame is 33 01 01)",
             note="whole-device power: 33 01 01"),
        Step("zone_left_off", partial(dev.set_zone_power, "left", False), lambda st: _bars(st, False, True),
             "Is exactly ONE bar OFF (the other still on)?", note=both_note),
        Step("zone_left_on", partial(dev.set_zone_power, "left", True), lambda st: _bars(st, True, True),
             "Are BOTH bars ON again?", note=both_note),
        Step("zone_right_off", partial(dev.set_zone_power, "right", False), lambda st: _bars(st, True, False),
             "Is exactly ONE bar OFF, and is it the OTHER bar than before?", note=both_note),
        Step("whole_on_with_one_bar_off", partial(dev.set_power, True), lambda st: _bars(st, True, True),
             "After the whole-device ON (33 01 01), are BOTH bars ON?",
             note="does the main switch relight a bar that was off? (diagnostic)"),
        Step("power_off", partial(dev.set_power, False),
             lambda st: st["is_on"] is False and _bars(st, False, False),
             "Are BOTH bars OFF? (whole-device power is 33 01 00)",
             note="whole-device power: 33 01 00"),
        Step("power_on_again", partial(dev.set_power, True), state_is("is_on", True),
             "Are the bars ON again?"),
    ]
    s += brightness_steps(dev, note="percent on the wire (33 04 <pct>)")
    s += rgb_steps(dev, "Are both bars solid {colour} (every segment)?",
                   note="read-back = the 10 aa a5 segment colours", expect=_all_segments)
    s.append(Step("rgb_left_red_right_blue",
                  _then(partial(dev.set_zone_rgb, "left", (255, 0, 0)),
                        partial(dev.set_zone_rgb, "right", (0, 0, 255))),
                  lambda st: _colour(st, LEFT, (255, 0, 0)) and _colour(st, RIGHT, (0, 0, 255)),
                  "Is one bar RED and the other BLUE?", note="left = segments 0-4, right = 5-9"))
    for k, q in ((2200, "Are both bars a very WARM (orange-ish) white?"),
                 (4000, "Are both bars a NEUTRAL white?"),
                 (6500, "Are both bars a COOL (bluish) white?")):
        s.append(Step(f"color_temp_{k}", partial(dev.set_color_temp, k), _both_bars_kelvin(k), q,
                      note="15 01 ff ff ff <K> <tint> <mask 03 ff>; read-back aa 05 15 [gradual][kL][kR]"))
    s.append(Step("color_temp_left_2700_right_6500",
                  _then(partial(dev.set_zone_color_temp, "left", 2700),
                        partial(dev.set_zone_color_temp, "right", 6500)),
                  lambda st: _kelvin(st, 2700, 6500) and st["color_temp_kelvin"] is None,
                  "Is one bar WARM white and the other COOL white?",
                  note="the read-back must show a different kelvin per bar"))
    s.append(Step("color_temp_right_only_4000", partial(dev.set_zone_color_temp, "right", 4000),
                  lambda st: _kelvin(st, 2700, 4000),
                  "Did only the second bar change (to a neutral white)?"))
    s.append(Step("color_temp_whole_4000", partial(dev.set_color_temp, 4000), _both_bars_kelvin(4000),
                  "Are both bars the same NEUTRAL white?"))
    s.append(tint_pick_step(
        dev, "Are both bars the same NEUTRAL white as at 4000 K (not a peach or orange colour)?",
        note="RGB pick 0xFFD5A1 = the 4000 K tint colour: "
             "the app sends CCT, 33 05 15 01 ff ff ff 0f a0 ff d5 a1 ff 03, not RGB (Config.Yt)",
        expect=lambda st: (st["color_temp_kelvin"] == 4000 and st["rgb_color"] is None
                           and _kelvin(st, 4000, 4000))))
    # The gradual flag only from a start the first poll read: the library holds none where it read none (its
    # aa a3 reply lost), and nothing could put an unknown flag back.
    unknown = ctx.start is not None and getattr(ctx.start.state, "gradual", None) is None
    skip = "gradual not read at the start: it couldn't be restored" if unknown else ""
    s.append(Step("gradual_on", partial(dev.set_gradual, True), state_is("gradual", True),
                  "Did the light carry on normally (no flicker, no colour change)?",
                  note="33 a3 01; read-back aa a3 (no visible effect expected)", skip=skip))
    s.append(Step("gradual_off", partial(dev.set_gradual, False), state_is("gradual", False),
                  "Did the light carry on normally (no flicker, no colour change)?",
                  note="33 a3 00", skip=skip))
    return s


async def _nothing() -> None:
    """A read-back-only step: nothing is sent (so its step is ``kind="read"``: it runs at night and on a
    read-only run too)."""


def _then(*calls: Callable[[], Awaitable[None]]) -> Callable[[], Awaitable[None]]:
    """One step action that awaits each call in order."""
    async def run() -> None:
        for call in calls:
            await call()
    return run


async def restore(dev: Device, st: Snapshot) -> list[str]:
    """Put the bars back from the initial snapshot: gradual, brightness, colour (a scene by its catalog name,
    its segment colours first: :func:`.plan.restore_scene`; whole-device or per-bar kelvin; else the
    per-segment colours that were read back), then power. Returns what it can't put back."""
    notes: list[str] = []
    if st.get("gradual") is not None:
        await dev.set_gradual(bool(st["gradual"]))
    if st.get("brightness"):
        await dev.set_brightness(st["brightness"])
    kelvins = st.get("zone_color_temp_kelvin") or {}
    if st.get("scene_code") is not None:
        notes += await restore_scene(dev, st)
    elif kelvins:
        for index, name in ((0, "left"), (1, "right")):
            if index in kelvins:
                await dev.set_zone_color_temp(name, kelvins[index])
    elif st.get("segments"):
        for rgb, indices in segment_runs(st["segments"]):
            await dev.set_segment_rgb(indices, rgb)
    bars = st.get("zone_power") or {}
    if st.get("is_on") is False:
        await dev.set_power(False)
    elif st.get("is_on"):
        await dev.set_power(True)
        for index, name in ((0, "left"), (1, "right")):
            if bars.get(index) is False:
                await dev.set_zone_power(name, False)
    return notes


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
