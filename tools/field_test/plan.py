"""Plan model + registry for the field-test runner.

A *plan* is everything that is specific to one SKU: how to recognise it, which steps to run,
what to ask the tester. The runner (``runner.py``) knows nothing about any SKU. To add a device,
drop ``plans/<sku>.py`` next to ``plans/h6188.py`` and call :func:`register_plan` at import time;
``plans/__init__.py`` imports every module in that directory, so neither the runner nor any
existing plan needs to change.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from typing import Any

# State keys copied into every read-back snapshot unless a plan supplies its own tuple.
DEFAULT_SNAPSHOT_KEYS: tuple[str, ...] = (
    "is_on", "brightness", "rgb_color", "color_temp_kelvin", "scene_code",
    "firmware_version", "hardware_version", "serial_number", "wifi_mac",
)
# Identifying state keys: always replaced by "<redacted>" in the report, whatever the plan asks for.
REDACTED_KEYS: tuple[str, ...] = ("serial_number", "wifi_mac")


@dataclasses.dataclass
class Step:
    """One action, then a read-back check (``expect`` gets the redacted snapshot dict), then a question."""

    name: str
    action: Callable[[], Awaitable[None]]
    expect: Callable[[dict[str, Any]], bool] | None
    question: str
    free_text: bool = False
    note: str = ""


@dataclasses.dataclass(frozen=True)
class PlanContext:
    """What a plan's ``build_steps`` gets: the connected device and the SKU's bundled scene codes."""

    dev: Any                      # govee_ble_local Device, already created (connect happens in the runner)
    sku: str
    scene_codes: dict[str, int]   # {scene name: code} from the bundled catalog ({} if none)


@dataclasses.dataclass(frozen=True)
class SkuPlan:
    """The test plan for one SKU.

    ``build_steps`` is called once, after connecting, and returns the ordered steps.
    ``restore`` (optional) puts the device back from the initial snapshot; the default restore
    re-applies brightness, then scene / colour temperature / RGB, then power-off if it was off.
    ``extra_name_hints`` are substrings of the advertised name that also select this plan when the
    SKU can't be parsed from it (the SKU's own digits, e.g. ``6188``, are always tried).
    """

    sku: str
    title: str                                        # e.g. "H6188 RGB strip"
    build_steps: Callable[[PlanContext], list[Step]]
    intro: str = ""                                   # shown to the tester before the steps run
    snapshot_keys: tuple[str, ...] = DEFAULT_SNAPSHOT_KEYS
    restore: Callable[[Any, dict[str, Any]], Awaitable[None]] | None = None
    extra_name_hints: tuple[str, ...] = ()


PLANS: dict[str, SkuPlan] = {}


def register_plan(plan: SkuPlan) -> SkuPlan:
    """Add ``plan`` to :data:`PLANS` (keyed by upper-case SKU). A second plan for the same SKU is a bug."""
    sku = plan.sku.upper()
    if plan.sku != sku:
        raise ValueError(f"plan SKU must be upper-case, got {plan.sku!r}")
    if sku in PLANS:
        raise ValueError(f"a field-test plan for {sku} is already registered")
    PLANS[sku] = plan
    return plan
