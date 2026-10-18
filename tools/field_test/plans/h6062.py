"""H6062 plan (Glide wall light, ``striplight`` split, goodsType 41).

The H6062 profile was modelled from the decompiled Govee Home app without hardware. The device
reports its own segment count with ``aa 41`` (``[IC s16be, sectionNum u8]``) and every mask and the
per-segment read-back follow it, so the first step asks the tester to count the segments and the
report's frame capture holds the raw ``aa 41`` reply to compare. Segment colours are checked against
the ``aa a5`` read-back; the whole-device colour is not restored afterwards (the app has no colour
read-back for a segment-mixed state), so the light is left in the last colour of the plan.
"""
from __future__ import annotations

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
    power_steps,
    register_plan,
    rgb_steps,
    scene_steps,
    state_is,
    tint_pick_step,
)

if TYPE_CHECKING:
    from govee_ble_local import Segment


def _seg(st: Snapshot, index: int) -> Segment | None:
    return next((s for s in (st.get("segments") or []) if s.index == index), None)


def _every_segment(rgb: RGB) -> Expect:
    """An ``expect``: there are segments, and every one read back as ``rgb``."""
    return lambda st: bool(st["segments"]) and all(g.rgb == rgb for g in st["segments"])


def _segment_rgb(index: int, rgb: RGB) -> Expect:
    """An ``expect``: segment ``index`` read back as ``rgb``."""
    return lambda st: (seg := _seg(st, index)) is not None and seg.rgb == rgb


def _segment_brightness(index: int, pct: int) -> Expect:
    """An ``expect``: segment ``index`` read back at ``pct`` brightness."""
    return lambda st: (seg := _seg(st, index)) is not None and seg.brightness == pct


def build_steps(ctx: PlanContext) -> list[Step]:
    dev, codes = ctx.dev, ctx.scene_codes
    n = dev.segment_count   # the aa 41 sectionNum, read by the runner's first update()
    first, mid, last = 0, n // 2, n - 1
    s: list[Step] = [
        Step("segment_count", partial(dev.set_rgb, (255, 255, 255)),
             lambda st: len(st["segments"] or []) == n,
             f"The library read {n} segments (aa 41 sectionNum). Is the whole light now WHITE? "
             "Type how many separate segments it really has.",
             free_text=True,
             note="read-back must hold exactly sectionNum segments; compare the aa 41 reply in the "
                  "frame capture"),
        *power_steps(dev, "Is the light OFF?", "Is the light ON again?"),
    ]
    s += brightness_steps(dev)
    s += rgb_steps(dev, "Is EVERY segment solid {colour}?", expect=_every_segment)
    for k, q in ((2000, "Is it VERY WARM (orange-ish) white?"), (4000, "Is it NEUTRAL white?"),
                 (6500, "Is it COOL white?"), (9000, "Is it VERY COOL (blue-ish) white?")):
        s.append(Step(f"color_temp_{k}", partial(dev.set_color_temp, k), state_is("color_temp_kelvin", k), q))
    s.append(tint_pick_step(
        dev, "Is every segment the same NEUTRAL white as at 4000 K (not a peach or orange colour)?",
        note="RGB pick 0xFFD5A1 = the 4000 K tint colour: "
             "the app sends CCT, 33 05 15 01 ff ff ff 0f a0 ff d5 a1 <mask>, not RGB (Config.Yt)"))
    s.append(Step("segments_reset_blue", partial(dev.set_rgb, (0, 0, 255)), None,
                  "Is every segment BLUE (baseline for the per-segment steps)?"))
    for label, idx in (("first", first), ("middle", mid), ("last", last)):
        s.append(Step(f"segment_{label}_red", partial(dev.set_segment_rgb, [idx], (255, 0, 0)),
                      _segment_rgb(idx, (255, 0, 0)),
                      f"Is ONLY the {label} segment (index {idx}) RED, all others blue?"))
    for label, idx in (("first", first), ("middle", mid), ("last", last)):
        s.append(Step(f"segment_{label}_brightness_20", partial(dev.set_segment_brightness, [idx], 20),
                      _segment_brightness(idx, 20),
                      f"Is the {label} segment (index {idx}) clearly DIMMER than the others?"))
    s.append(Step("segment_middle_color_temp_2700", partial(dev.set_segment_color_temp, [mid], 2700),
                  None, f"Is the middle segment (index {mid}) WARM white, the others unchanged?"))
    s += scene_steps(dev, dev.scene_names[:1], codes)
    return s


PLAN = register_plan(SkuPlan(
    sku="H6062",
    title="H6062 Glide wall light",
    build_steps=build_steps,
    intro="H6062: segment count (aa 41), power, brightness, whole-device RGB and colour "
          "temperature, per-segment colour / brightness / colour temperature, one scene.",
    snapshot_keys=(*DEFAULT_SNAPSHOT_KEYS, "segments"),
))
