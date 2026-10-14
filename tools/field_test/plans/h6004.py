"""H6004 plan (BLE bulb, ``bulblightv3`` split, goodsType 64).

The H6004 profile was modelled from the decompiled Govee Home app without hardware. H6004 has two
hardware revisions and only the goodsType-64 one speaks BLE (the goodsType-10 one is Wi-Fi/IoT-only),
so the scan is the first check: a BLE-capable unit advertises ``Govee_H6004_*`` / ``ihoment_H6004_*``
with Govee manufacturer data. If this tool finds no device, the bulb is probably the Wi-Fi-only
revision: send the JSON report (it lists every Govee device heard) and skip the rest.

The CCT steps send the app's both-slot 0x0d frame ``0d <tint> <K> <tint>``; 2750 K is not on the app's
100 K step grid, so the library rounds it to 2800 K (check the read-back, and the frame capture for
``ffb25b 0af0 ffb25b``, the 2800 K tint in both colour slots).
"""
from __future__ import annotations

from ..plan import PlanContext, SkuPlan, Step, register_plan

# One activate-only scene (type 0) and two uploaded ones (type 1, version-{1} upload), if the
# bundled catalog has them; the whole 63-scene list is too long for a manual run.
_SCENES = ("Reading", "Rainbow", "Fire")


def build_steps(ctx: PlanContext) -> list[Step]:
    dev, codes = ctx.dev, ctx.scene_codes
    s: list[Step] = [
        Step("power_off", lambda: dev.set_power(False), lambda st: st["is_on"] is False,
             "Is the bulb OFF?"),
        Step("power_on", lambda: dev.set_power(True), lambda st: st["is_on"] is True,
             "Is the bulb ON again?"),
    ]
    for pct, q in ((100, "Is it at FULL brightness?"), (1, "Is it VERY DIM but still lit?"),
                   (50, "Is it at roughly HALF brightness?")):
        s.append(Step(f"brightness_{pct}", lambda pct=pct: dev.set_brightness(pct),
                      lambda st, pct=pct: st["brightness"] == pct, q,
                      note="percent 1..100 on the wire (no raw scaling)"))
    for label, rgb in (("red", (255, 0, 0)), ("green", (0, 255, 0)), ("blue", (0, 0, 255))):
        s.append(Step(f"rgb_{label}", lambda rgb=rgb: dev.set_rgb(rgb),
                      lambda st, rgb=rgb: st["rgb_color"] == rgb, f"Is the bulb solid {label.upper()}?"))
    for k, shown, q in ((2700, 2700, "Is it WARM (yellowish) white?"),
                        (4000, 4000, "Is it NEUTRAL white?"),
                        (6500, 6500, "Is it COOL (bluish) white?"),
                        (2750, 2800, "Is it still WARM white (2750 K is rounded to 2800 K)?")):
        s.append(Step(f"color_temp_{k}", lambda k=k: dev.set_color_temp(k),
                      lambda st, shown=shown: st["color_temp_kelvin"] == shown, q,
                      note="both-slot 0x0d frame: tint, kelvin, tint"))
    names = [n for n in _SCENES if n in dev.scene_names] or list(dev.scene_names)[:1]
    for name in names:
        code = codes.get(name)
        s.append(Step(f"scene_{name}", lambda name=name: dev.set_scene_by_name(name),
                      lambda st, code=code: st["scene_code"] == code,
                      f"Scene '{name}': is an effect/scene running (not a plain colour)?"))
    return s


PLAN = register_plan(SkuPlan(
    sku="H6004",
    title="H6004 BLE bulb",
    build_steps=build_steps,
    intro="H6004 (BLE revision): power, brightness, RGB, colour temperature (2700-6500 K) and a few "
          "scenes, each with a read-back. If the scan finds no H6004, it is probably the Wi-Fi-only "
          "revision (not controllable over BLE): send the report and stop.",
))
