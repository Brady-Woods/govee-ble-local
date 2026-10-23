"""Plan model + registry for the field-test runner.

A *plan* is everything that is specific to one SKU: how to recognise it, which steps to run,
what to ask the tester. The runner (``runner.py``) knows nothing about any SKU. To add a device,
drop ``plans/<sku>.py`` next to ``plans/h6188.py`` and call :func:`register_plan` at import time;
``plans/__init__.py`` imports every module in that directory, so neither the runner nor any
existing plan needs to change.

The step factories below (:func:`power_steps`, :func:`brightness_steps`, :func:`rgb_steps`,
:func:`tint_pick_step`, :func:`scene_steps`) build the steps most plans share; bind a step's
arguments with :func:`functools.partial` and :func:`state_is` rather than a ``lambda x=x:`` default,
which a type checker can't follow.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable, Iterable
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

if TYPE_CHECKING:   # typing only: the runner chooses the library to test after parsing its arguments
    from govee_ble_local import Device

# A read-back snapshot (masking.snapshot): state key -> value, identifying keys already redacted.
Snapshot: TypeAlias = dict[str, Any]
Action: TypeAlias = Callable[[], Awaitable[None]]
Expect: TypeAlias = Callable[[Snapshot], bool]
RGB: TypeAlias = tuple[int, int, int]

# State keys copied into every read-back snapshot unless a plan supplies its own tuple.
DEFAULT_SNAPSHOT_KEYS: tuple[str, ...] = (
    "is_on", "brightness", "rgb_color", "color_temp_kelvin", "scene_code",
    "firmware_version", "hardware_version", "serial_number", "wifi_mac",
)
# Identifying state keys: always replaced by "<redacted>" in the report, whatever the plan asks for.
# ``device_id`` is DeviceState's alias of ``serial_number``; ``ble_mac`` a device-reported MAC.
REDACTED_KEYS: tuple[str, ...] = ("serial_number", "device_id", "wifi_mac", "ble_mac")


StepKind: TypeAlias = Literal["read", "write"]
# What a device is to the runner (generic.device_kind): a held-link light, a plug, or a device-list card
# row (one session per command, no read-back).
DeviceKind: TypeAlias = Literal["light", "plug", "card"]


@dataclasses.dataclass
class Step:
    """One action, then a read-back check (``expect`` gets the redacted snapshot dict), then a question.

    The keyword fields serve the release-gate pass (``readback.judge``); a hand-written plan can leave
    them at their defaults:

    * ``kind``: ``"write"`` (the default) changes the device, so the daytime window and ``--read-only``
      gate it; ``"read"`` only reads (a connect, a poll, an advertisement) and runs at any hour.
    * ``fields``: the state fields the step is judged on, compared with what the library intended
      (``Device.snapshot()`` right after the action) wherever the row reads that field back. Empty: the
      step is judged on ``expect`` (hand-written plans) or the tester's answer.
    * ``refusal``: the exception the action must raise before any I/O (``GoveeBleNotSupported``); the
      step passes when it does and the device was sent nothing.
    * ``touched``: the segment indices a segment step writes (the others are judged unchanged).
    * ``skip``: a reason to record the step as skipped without running it (e.g. ``"no secret"``).
    * ``check``: called after the read-back, returns ``(verdict, reason)``: ``"pass"`` / ``"fail"`` decide
      the step, ``"unverifiable"`` leaves it to the fields and the tester (e.g. the session-setup acks).
    """

    name: str
    action: Action
    expect: Expect | None
    question: str
    free_text: bool = False
    note: str = ""
    kind: StepKind = "write"
    fields: tuple[str, ...] = ()
    refusal: type[Exception] | None = None
    touched: tuple[int, ...] = ()
    skip: str = ""
    check: Callable[[], tuple[str, str | None]] | None = None


@dataclasses.dataclass(frozen=True)
class PlanContext:
    """What a plan's ``build_steps`` gets: the connected device and the SKU's bundled scene codes.

    The fields after ``scene_codes`` are the release-gate pass's (the generic plan reads them); a
    hand-written plan may ignore them."""

    dev: Device                   # already created (connect happens in the runner)
    sku: str
    scene_codes: dict[str, int]   # {scene name: code} from the bundled catalog ({} if none)
    kind: DeviceKind = "light"
    tier: str = "default"         # "default" or "modelled" (survey.classify)
    adv: Any = None               # the advertisement the device was created from
    start: Any = None             # restore.StartState, captured after the first update()
    toggle_ok: bool = False       # a plug whose relay the tester allowed to toggle (--toggle-plugs)
    has_secret: bool = False      # a secret was given for this device (--secrets-file / prompt)
    prompts: bool = True          # the tester can be asked (--ask is not "none")
    watch: Any = None             # readback.FrameWatch on the device's frames (generic session checks)


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
    restore: Callable[[Device, Snapshot], Awaitable[None]] | None = None
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


# --- step factories ---------------------------------------------------------------------------

PRIMARIES: tuple[tuple[str, RGB], ...] = (("red", (255, 0, 0)), ("green", (0, 255, 0)), ("blue", (0, 0, 255)))
BRIGHTNESS_LEVELS: tuple[tuple[int, str], ...] = (
    (100, "Is it at FULL brightness?"), (1, "Is it VERY DIM but still lit?"),
    (50, "Is it at roughly HALF brightness?"),
)
# The app's 4000 K tint colour (0xFFD5A1): an RGB pick of it is sent as colour temperature.
TINT_4000K: RGB = (255, 213, 161)


def state_is(key: str, value: object) -> Expect:
    """An ``expect``: the read-back ``key`` is ``value`` (identity for True / False / None, else ==)."""
    if value is None or isinstance(value, bool):
        return lambda st: st[key] is value
    return lambda st: bool(st[key] == value)


def power_steps(dev: Device, off_question: str, on_question: str) -> list[Step]:
    """``power_off`` then ``power_on``, each checked against the read-back ``is_on``."""
    return [
        Step("power_off", partial(dev.set_power, False), state_is("is_on", False), off_question),
        Step("power_on", partial(dev.set_power, True), state_is("is_on", True), on_question),
    ]


def brightness_steps(dev: Device, note: str = "") -> list[Step]:
    """``brightness_100`` / ``_1`` / ``_50`` (percent), each checked against the read-back."""
    return [Step(f"brightness_{pct}", partial(dev.set_brightness, pct), state_is("brightness", pct), q,
                 note=note) for pct, q in BRIGHTNESS_LEVELS]


def rgb_steps(dev: Device, question: str, note: str = "",
              expect: Callable[[RGB], Expect] | None = None) -> list[Step]:
    """``rgb_red`` / ``rgb_green`` / ``rgb_blue``: whole-device RGB. ``question`` is formatted with
    ``colour`` (``RED`` ...); ``expect(rgb)`` builds the check (default: the read-back ``rgb_color``)."""
    steps: list[Step] = []
    for label, rgb in PRIMARIES:
        check = expect(rgb) if expect is not None else state_is("rgb_color", rgb)
        steps.append(Step(f"rgb_{label}", partial(dev.set_rgb, rgb), check,
                          question.format(colour=label.upper()), note=note))
    return steps


def is_4000k_cct(st: Snapshot) -> bool:
    """The read-back is the whole-device 4000 K colour temperature (no RGB)."""
    return bool(st["color_temp_kelvin"] == 4000 and st["rgb_color"] is None)


def tint_pick_step(dev: Device, question: str, note: str, expect: Expect = is_4000k_cct) -> Step:
    """``rgb_pick_equal_to_4000k_tint``: pick the 4000 K tint colour as RGB; the app sends it as CCT."""
    return Step("rgb_pick_equal_to_4000k_tint", partial(dev.set_rgb, TINT_4000K), expect, question,
                note=note)


def scene_steps(dev: Device, names: Iterable[str], codes: dict[str, int]) -> list[Step]:
    """One ``scene_<name>`` step per name: activate it by name, check the read-back scene code."""
    return [Step(f"scene_{name}", partial(dev.set_scene_by_name, name),
                 state_is("scene_code", codes.get(name)),
                 f"Scene '{name}': is an effect/scene running (not a plain colour)?") for name in names]
