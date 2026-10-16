"""tools/field_test runner: the tester prompt and SKU detection. No BLE hardware."""
from __future__ import annotations

import asyncio
import importlib
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.syspath_prepend(str(TOOLS))
    yield importlib.import_module("field_test.runner")


def test_ctrl_c_at_a_prompt_does_not_wait_for_the_blocked_input_thread(
    runner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled prompt (Ctrl-C cancels the main task) must not leave asyncio.run joining a thread
    still blocked in input(): the tool would hang after its summary until the tester pressed Enter."""
    release = threading.Event()

    def blocked_input(_prompt: str = "") -> str:
        release.wait(5)
        return "y"

    monkeypatch.setattr("builtins.input", blocked_input)

    async def prompt_then_cancel() -> None:
        task = asyncio.create_task(runner._ask("Is it OFF?", True))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    start = time.monotonic()
    try:
        asyncio.run(prompt_then_cancel())
        elapsed = time.monotonic() - start
    finally:
        release.set()
    assert elapsed < 2.0


def test_ask_maps_answers_and_reprompts_on_anything_else(
    runner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter(["maybe", " Y ", "n", "s", "", "  six segments "])
    prompts: list[str] = []

    def fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)

    async def ask_all() -> list[str | None]:
        return [await runner._ask("Off?", True), await runner._ask("Off?", True),
                await runner._ask("Off?", True), await runner._ask("Count?", True, free_text=True),
                await runner._ask("Count?", True, free_text=True), await runner._ask("x", False)]

    assert asyncio.run(ask_all()) == ["yes", "no", "skipped", "(no answer)", "six segments", None]
    assert prompts[0] == "    ? Off? [y = yes, n = no, s = skip]: " and len(prompts) == 6


def test_end_of_input_at_a_prompt_raises_in_the_caller(runner: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def closed(_prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", closed)
    with pytest.raises(EOFError):
        asyncio.run(runner._ask("Off?", True))


@pytest.mark.parametrize(("name", "identified"), [
    ("ihoment_H6006_6188", "H6006"),   # names end in the last two MAC bytes, which can be all digits
    ("Govee_H6008_6047", "H6008"),
    ("ihoment_H6006_6188", None),      # the SKU parsed from the name decides just the same
    ("GV60066188", None),
])
def test_a_device_known_to_be_another_sku_never_matches_a_plan_by_its_digits(
    runner: Any, name: str, identified: str | None
) -> None:
    assert runner.match_plan_sku(name, identified, ["H6004", "H6047", "H6061", "H6062", "H6188"]) is None


def test_the_digit_hint_ignores_the_mac_digits_of_a_name_without_a_sku(runner: Any) -> None:
    assert runner.match_plan_sku("Lamp_6188", None, ["H6188"]) is None
    assert runner.match_plan_sku("Lamp 6188 bar", None, ["H6188"]) == "H6188"
