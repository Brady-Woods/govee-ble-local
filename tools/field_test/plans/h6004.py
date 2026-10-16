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

# One activate-only scene (type 0) and two uploaded ones (type 1, version-{1} upload), if the
# bundled catalog has them; the whole 63-scene list is too long for a manual run.
_SCENES = ("Reading", "Rainbow", "Fire")


def build_steps(ctx: PlanContext) -> list[Step]:
    dev, codes = ctx.dev, ctx.scene_codes
    s = power_steps(dev, "Is the bulb OFF?", "Is the bulb ON again?")
    s += brightness_steps(dev, note="percent 1..100 on the wire (no raw scaling)")
    s += rgb_steps(dev, "Is the bulb solid {colour}?")
    for k, shown, q in ((2700, 2700, "Is it WARM (yellowish) white?"),
                        (4000, 4000, "Is it NEUTRAL white?"),
                        (6500, 6500, "Is it COOL (bluish) white?"),
                        (2750, 2800, "Is it still WARM white (2750 K is rounded to 2800 K)?")):
        s.append(Step(f"color_temp_{k}", partial(dev.set_color_temp, k),
                      state_is("color_temp_kelvin", shown), q,
                      note="both-slot 0x0d frame: tint, kelvin, tint"))
    s.append(tint_pick_step(
        dev, "Is it the same NEUTRAL white as at 4000 K (not a peach or orange colour)?",
        note="RGB pick 0xFFD5A1 = the 4000 K tint colour: "
             "the app sends CCT, 33 05 0d ff d5 a1 0f a0 ff d5 a1, not RGB (Config.bu)"))
    available = dev.scene_names
    s += scene_steps(dev, [n for n in _SCENES if n in available] or available[:1], codes)
    return s


PLAN = register_plan(SkuPlan(
    sku="H6004",
    title="H6004 BLE bulb",
    build_steps=build_steps,
    intro="H6004 (BLE revision): power, brightness, RGB, colour temperature (2700-6500 K) and a few "
          "scenes, each with a read-back. If the scan finds no H6004, it is probably the Wi-Fi-only "
          "revision (not controllable over BLE): send the report and stop.",
))
