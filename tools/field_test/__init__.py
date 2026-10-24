"""Guided hardware field test: one runner, a hand-written plan per SKU where one helps, and a generic plan
built from the profile for every other supported SKU.

    python tools/field_test            # scan, detect the SKU, run its plan
    python tools/field_test --sku H6188 --address XX:XX:XX:XX:XX:XX
    python tools/field_test --list     # every Govee device in range, its tier, the plugs
    python tools/field_test --all      # the release-gate pass: every supported device, one at a time

See ``plan.py`` for how to add a SKU, and tools/README.md for the release-gate procedure.
"""
from __future__ import annotations

from .plan import PLANS, PlanContext, SkuPlan, Step, register_plan

# Importing the plans package registers every plans/<sku>.py (must come after .plan).
from . import plans  # noqa: F401

__all__ = ["PLANS", "PlanContext", "SkuPlan", "Step", "register_plan"]
