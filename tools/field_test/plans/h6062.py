"""H6062 plan (Glide wall light, ``striplight`` split, goodsType 41).

The H6062 profile was modelled from the decompiled Govee Home app without hardware. The device
reports its own segment count with ``aa 41`` (``[IC s16be, sectionNum u8]``) and every mask and the
per-segment read-back follow it, so the first step asks the tester to count the segments and the
report's frame capture holds the raw ``aa 41`` reply to compare. Segment colours are checked against
the ``aa a5`` read-back; the whole-device colour is not restored afterwards (the app has no colour
read-back for a segment-mixed state), so the light is left in the last colour of the plan.
"""
from __future__ import annotations

from typing import Any

from ..plan import PlanContext, SkuPlan, Step, register_plan


def _seg(st: dict[str, Any], index: int) -> Any:
    return next((s for s in (st.get("segments") or []) if s.index == index), None)


def build_steps(ctx: PlanContext) -> list[Step]:
    dev = ctx.dev
    n = dev.segment_count   # the aa 41 sectionNum, read by the runner's first update()
    first, mid, last = 0, n // 2, n - 1
    s: list[Step] = [
        Step("segment_count", lambda: dev.set_rgb((255, 255, 255)),
             lambda st: len(st["segments"] or []) == n,
             f"The library read {n} segments (aa 41 sectionNum). Is the whole light now WHITE? "
             "Type how many separate segments it really has.",
             free_text=True,
             note="read-back must hold exactly sectionNum segments; compare the aa 41 reply in the "
                  "frame capture"),
        Step("power_off", lambda: dev.set_power(False), lambda st: st["is_on"] is False,
             "Is the light OFF?"),
        Step("power_on", lambda: dev.set_power(True), lambda st: st["is_on"] is True,
             "Is the light ON again?"),
    ]
    for pct, q in ((100, "Is it at FULL brightness?"), (1, "Is it VERY DIM but still lit?"),
                   (50, "Is it at roughly HALF brightness?")):
        s.append(Step(f"brightness_{pct}", lambda pct=pct: dev.set_brightness(pct),
                      lambda st, pct=pct: st["brightness"] == pct, q))
    for label, rgb in (("red", (255, 0, 0)), ("green", (0, 255, 0)), ("blue", (0, 0, 255))):
        s.append(Step(f"rgb_{label}", lambda rgb=rgb: dev.set_rgb(rgb),
                      lambda st, rgb=rgb: bool(st["segments"]) and all(
                          g.rgb == rgb for g in st["segments"]),
                      f"Is EVERY segment solid {label.upper()}?"))
    for k, q in ((2000, "Is it VERY WARM (orange-ish) white?"), (4000, "Is it NEUTRAL white?"),
                 (6500, "Is it COOL white?"), (9000, "Is it VERY COOL (blue-ish) white?")):
        s.append(Step(f"color_temp_{k}", lambda k=k: dev.set_color_temp(k),
                      lambda st, k=k: st["color_temp_kelvin"] == k, q))
    s.append(Step("segments_reset_blue", lambda: dev.set_rgb((0, 0, 255)), None,
                  "Is every segment BLUE (baseline for the per-segment steps)?"))
    for label, idx in (("first", first), ("middle", mid), ("last", last)):
        s.append(Step(f"segment_{label}_red", lambda idx=idx: dev.set_segment_rgb([idx], (255, 0, 0)),
                      lambda st, idx=idx: getattr(_seg(st, idx), "rgb", None) == (255, 0, 0),
                      f"Is ONLY the {label} segment (index {idx}) RED, all others blue?"))
    for label, idx in (("first", first), ("middle", mid), ("last", last)):
        s.append(Step(f"segment_{label}_brightness_20",
                      lambda idx=idx: dev.set_segment_brightness([idx], 20),
                      lambda st, idx=idx: getattr(_seg(st, idx), "brightness", None) == 20,
                      f"Is the {label} segment (index {idx}) clearly DIMMER than the others?"))
    s.append(Step("segment_middle_color_temp_2700", lambda: dev.set_segment_color_temp([mid], 2700),
                  None, f"Is the middle segment (index {mid}) WARM white, the others unchanged?"))
    return s


PLAN = register_plan(SkuPlan(
    sku="H6062",
    title="H6062 Glide wall light",
    build_steps=build_steps,
    intro="H6062: segment count (aa 41), power, brightness, whole-device RGB and colour "
          "temperature, per-segment colour / brightness / colour temperature (no scenes yet).",
    snapshot_keys=("is_on", "brightness", "rgb_color", "color_temp_kelvin", "segments",
                   "firmware_version", "hardware_version", "serial_number", "wifi_mac"),
))
