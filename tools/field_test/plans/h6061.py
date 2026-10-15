"""H6061 plan (Glide Hexa light panels, ``cubelight`` split), phase 1: whole-device control.

The H6061 profile was modelled from the decompiled Govee Home app without hardware. The panel count
is the library default of 23, which is what the app uses when no layout is stored; a smaller
installation may differ from the app's layout-sized writes (``Device.set_segment_count`` sets the
size). The ``segments`` entry in each read-back is the per-group colour (``aa a5`` group replies);
the raw ``aa 05`` and ``aa a5`` frames are in the frame capture.
"""
from __future__ import annotations

from ..plan import DEFAULT_SNAPSHOT_KEYS, PlanContext, SkuPlan, Step, register_plan


def build_steps(ctx: PlanContext) -> list[Step]:
    dev = ctx.dev
    s: list[Step] = [
        Step("power_off", lambda: dev.set_power(False), lambda st: st["is_on"] is False,
             "Are ALL panels OFF?"),
        Step("power_on", lambda: dev.set_power(True), lambda st: st["is_on"] is True,
             "Are all panels ON again?"),
    ]
    for label, rgb in (("red", (255, 0, 0)), ("green", (0, 255, 0)), ("blue", (0, 0, 255))):
        s.append(Step(f"rgb_{label}", lambda rgb=rgb: dev.set_rgb(rgb),
                      lambda st, rgb=rgb: st["rgb_color"] == rgb,
                      f"Is EVERY panel solid {label.upper()} (none left in the old colour)?",
                      note="6 frames: 33 05 15 04 <group 1..6> ... (the last carries 3 colours)"))
    for pct, q in ((100, "Is it at FULL brightness?"), (1, "Is it VERY DIM but still lit?"),
                   (50, "Is it at roughly HALF brightness?")):
        s.append(Step(f"brightness_{pct}", lambda pct=pct: dev.set_brightness(pct),
                      lambda st, pct=pct: st["brightness"] == pct, q))
    for k, q in ((2000, "Is EVERY panel a very WARM (orange) white?"),
                 (4000, "Is every panel NEUTRAL white?"),
                 (6500, "Is every panel COOL (bluish) white?"),
                 (9000, "Is every panel VERY COOL (blue-ish) white?")):
        s.append(Step(f"color_temp_{k}", lambda k=k: dev.set_color_temp(k),
                      lambda st, k=k: st["color_temp_kelvin"] == k, q,
                      note="33 05 15 01 <tint> <kelvin> ff ff 7f"))
    s.append(Step("rgb_pick_equal_to_4000k_tint", lambda: dev.set_rgb((255, 213, 161)),
                  lambda st: st["color_temp_kelvin"] == 4000 and st["rgb_color"] is None,
                  "Is every panel the same NEUTRAL white as at 4000 K (not a peach or orange colour)?",
                  note="RGB pick 0xFFD5A1 = the 4000 K tint colour: "
                       "the app sends CCT, 33 05 15 01 ff d5 a1 0f a0 ff ff 7f (one frame), "
                       "not the 6 group frames (Config.Zt)"))
    return s


PLAN = register_plan(SkuPlan(
    sku="H6061",
    title="H6061 Glide Hexa panels",
    build_steps=build_steps,
    snapshot_keys=(*DEFAULT_SNAPSHOT_KEYS, "segments"),
    intro="H6061: power, brightness, whole-device RGB (the 6 group frames: every panel must show the "
          "colour) and colour temperature at 2000 / 4000 / 6500 / 9000 K. Panel count 23 (the app's "
          "default). The original colour is restored as one colour on all panels.",
))
