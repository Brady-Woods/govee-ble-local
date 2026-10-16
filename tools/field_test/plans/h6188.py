"""H6188 plan (legacy RGB strip, ``rgblight`` split).

The H6188 profile was modelled from the decompiled Govee Home app without hardware. The brightness
steps are diagnostics, not pass/fail: the app writes a raw 20..254 byte, not a percent, so check the
frame capture for the byte on the wire.
"""
from __future__ import annotations

from functools import partial

from ..plan import (
    PlanContext,
    SkuPlan,
    Step,
    brightness_steps,
    power_steps,
    register_plan,
    rgb_steps,
    scene_steps,
    state_is,
    tint_pick_step,
)


def build_steps(ctx: PlanContext) -> list[Step]:
    dev, codes = ctx.dev, ctx.scene_codes
    s = power_steps(dev, "Is the strip OFF?", "Is the strip ON again?")
    s += rgb_steps(dev, "Is the strip solid {colour}?")
    s += brightness_steps(dev, note="raw byte on the wire: 20..254 (check the frame capture)")
    for k, q in ((2700, "Is it WARM (yellowish) white?"), (6500, "Is it COOL (bluish) white?"),
                 (9000, "Is it VERY COOL (blue-ish) white?")):
        s.append(Step(f"color_temp_{k}", partial(dev.set_color_temp, k), state_is("color_temp_kelvin", k), q))
    s.append(tint_pick_step(
        dev, "Is it the same NEUTRAL white as at 4000 K (not a peach or orange colour)?",
        note="RGB pick 0xFFD5A1 = the 4000 K tint colour: "
             "the app sends CCT, 33 05 0d ff d5 a1 0f a0 ff d5 a1, not RGB (Config.bu)"))
    s += scene_steps(dev, dev.scene_names, codes)
    return s


PLAN = register_plan(SkuPlan(
    sku="H6188",
    title="H6188 RGB strip",
    build_steps=build_steps,
    intro="H6188: power, RGB, brightness (raw 20..254 on the wire), colour temperature and all "
          "9 scenes.",
))
