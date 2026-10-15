"""Guided hardware field test: one runner, one test plan per SKU.

    python tools/field_test            # scan, detect the SKU, run its plan
    python tools/field_test --sku H6188 --address XX:XX:XX:XX:XX:XX

See ``plan.py`` for how to add a SKU.
"""
from __future__ import annotations

from .plan import PLANS, PlanContext, SkuPlan, Step, register_plan

# Importing the plans package registers every plans/<sku>.py (must come after .plan).
from . import plans  # noqa: F401

__all__ = ["PLANS", "PlanContext", "SkuPlan", "Step", "register_plan"]
