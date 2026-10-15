"""H6188 plan (legacy RGB strip, ``rgblight`` split).

The H6188 profile was modelled from the decompiled Govee Home app without hardware. The brightness
steps are diagnostics, not pass/fail: the app writes a raw 20..254 byte, not a percent, so check the
frame capture for the byte on the wire.
"""
from __future__ import annotations

from ..plan import PlanContext, SkuPlan, Step, register_plan


def build_steps(ctx: PlanContext) -> list[Step]:
    dev, codes = ctx.dev, ctx.scene_codes
    s: list[Step] = [
        Step("power_off", lambda: dev.set_power(False), lambda st: st["is_on"] is False,
             "Is the strip OFF?"),
        Step("power_on", lambda: dev.set_power(True), lambda st: st["is_on"] is True,
             "Is the strip ON again?"),
    ]
    for label, rgb in (("red", (255, 0, 0)), ("green", (0, 255, 0)), ("blue", (0, 0, 255))):
        s.append(Step(f"rgb_{label}", lambda rgb=rgb: dev.set_rgb(rgb),
                      lambda st, rgb=rgb: st["rgb_color"] == rgb, f"Is the strip solid {label.upper()}?"))
    for pct, q in ((100, "Is it at FULL brightness?"), (1, "Is it VERY DIM but still lit?"),
                   (50, "Is it at roughly HALF brightness?")):
        s.append(Step(f"brightness_{pct}", lambda pct=pct: dev.set_brightness(pct),
                      lambda st, pct=pct: st["brightness"] == pct, q,
                      note="raw byte on the wire: 20..254 (check the frame capture)"))
    for k, q in ((2700, "Is it WARM (yellowish) white?"), (6500, "Is it COOL (bluish) white?"),
                 (9000, "Is it VERY COOL (blue-ish) white?")):
        s.append(Step(f"color_temp_{k}", lambda k=k: dev.set_color_temp(k),
                      lambda st, k=k: st["color_temp_kelvin"] == k, q))
    s.append(Step("rgb_pick_equal_to_4000k_tint", lambda: dev.set_rgb((255, 213, 161)),
                  lambda st: st["color_temp_kelvin"] == 4000 and st["rgb_color"] is None,
                  "Is it the same NEUTRAL white as at 4000 K (not a peach or orange colour)?",
                  note="RGB pick 0xFFD5A1 = the 4000 K tint colour: "
                       "the app sends CCT, 33 05 0d ff d5 a1 0f a0 ff d5 a1, not RGB (Config.bu)"))
    for name in dev.scene_names:
        code = codes.get(name)
        s.append(Step(f"scene_{name}", lambda name=name: dev.set_scene_by_name(name),
                      lambda st, code=code: st["scene_code"] == code,
                      f"Scene '{name}': is an effect/scene running (not a plain colour)?"))
    return s


PLAN = register_plan(SkuPlan(
    sku="H6188",
    title="H6188 RGB strip",
    build_steps=build_steps,
    intro="H6188: power, RGB, brightness (raw 20..254 on the wire), colour temperature and all "
          "9 scenes.",
))
