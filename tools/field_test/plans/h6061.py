"""H6061 plan (Glide Hexa light panels, ``cubelight`` split), phase 1: whole-device control.

The H6061 profile was modelled from the decompiled Govee Home app without hardware. The panel count
is the library default of 23: a full layout's count, and the app's read-back default when no layout
is stored (``Support.getBulbStringMaxNum``). The app sizes its writes from the stored layout, and
with none stored writes one panel (mask ``01 00``), so a smaller installation may differ
(``Device.set_segment_count`` sets the size). The ``segments`` entry in each read-back is the
per-group colour (``aa a5`` group replies); the raw ``aa 05`` and ``aa a5`` frames are in the frame
capture.
"""
from __future__ import annotations

from functools import partial

from ..plan import (
    DEFAULT_SNAPSHOT_KEYS,
    PlanContext,
    SkuPlan,
    Step,
    brightness_steps,
    power_steps,
    register_plan,
    rgb_steps,
    state_is,
    tint_pick_step,
)


def build_steps(ctx: PlanContext) -> list[Step]:
    dev = ctx.dev
    s = power_steps(dev, "Are ALL panels OFF?", "Are all panels ON again?")
    s += rgb_steps(dev, "Is EVERY panel solid {colour} (none left in the old colour)?",
                   note="one frame: 33 05 15 01 R G B 00 00 ff ff 7f (Bytes.l)")
    s += brightness_steps(dev)
    for k, q in ((2000, "Is EVERY panel a very WARM (orange) white?"),
                 (4000, "Is every panel NEUTRAL white?"),
                 (6500, "Is every panel COOL (bluish) white?"),
                 (9000, "Is every panel VERY COOL (blue-ish) white?")):
        s.append(Step(f"color_temp_{k}", partial(dev.set_color_temp, k), state_is("color_temp_kelvin", k), q,
                      note="33 05 15 01 <tint> <kelvin> ff ff 7f"))
    s.append(tint_pick_step(
        dev, "Is every panel the same NEUTRAL white as at 4000 K (not a peach or orange colour)?",
        note="RGB pick 0xFFD5A1 = the 4000 K tint colour: "
             "the app sends CCT, 33 05 15 01 ff d5 a1 0f a0 ff ff 7f (one frame), "
             "not the RGB frame with Kelvin 00 00 (Config.Zt)"))
    return s


PLAN = register_plan(SkuPlan(
    sku="H6061",
    title="H6061 Glide Hexa panels",
    build_steps=build_steps,
    snapshot_keys=(*DEFAULT_SNAPSHOT_KEYS, "segments"),
    intro="H6061: power, brightness, whole-device RGB (one frame over all 23 panels: every panel must show "
          "the colour) and colour temperature at 2000 / 4000 / 6500 / 9000 K. Panel count 23 (a full "
          "layout's). The original colour is restored as one colour on all panels.",
))
