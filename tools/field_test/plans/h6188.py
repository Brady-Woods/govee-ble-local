"""H6188 plan (legacy RGB strip, ``rgblight`` split).

The H6188 profile was modelled from the decompiled Govee Home app without hardware. Two steps are
diagnostics, not pass/fail: colour temperature 2750 K (the app sends no RGB "tint" bytes for a
Kelvin that is not on a 100 K step, which shows whether the firmware renders the Kelvin value or the
tint), and the brightness read-backs (the app writes a raw 20..254 byte, not a percent).
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
    s.append(Step("color_temp_2750_no_tint", lambda: dev.set_color_temp(2750),
                  lambda st: st["color_temp_kelvin"] == 2750,
                  "DIAGNOSTIC 2750 K: is it warm white like 2700 K, OFF/black, or something else?",
                  free_text=True, note="the app sends no tint bytes for a Kelvin not on a 100 K step"))
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
    intro="H6188: power, RGB, brightness (raw 20..254 on the wire), colour temperature (incl. the "
          "2750 K no-tint diagnostic) and all 9 scenes.",
))
