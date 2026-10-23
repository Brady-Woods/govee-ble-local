"""The daytime guard: visible writes only inside a local-time window (default 07:00 to 22:00).

Every step that changes a device (``Step.kind == "write"``) is checked against the window when it is
about to run, so a run that crosses the late hour skips the rest of its writes; a restore is never
gated (it only puts the start state back). Listing, connecting and reading run at any hour.
``--force-hours`` lifts the window and is recorded in every report.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt


def now() -> _dt.datetime:
    """Local wall-clock time (a function, so a test can fake the hour)."""
    return _dt.datetime.now()


def parse_hhmm(text: str) -> _dt.time:
    """``"HH:MM"`` (24-hour) as a time; ValueError otherwise."""
    hours, sep, minutes = text.strip().partition(":")
    if not sep or not hours.isdigit() or not minutes.isdigit() or len(minutes) != 2:
        raise ValueError(f"expected HH:MM, got {text!r}")
    return _dt.time(int(hours), int(minutes))


@dataclasses.dataclass(frozen=True)
class Window:
    """Writes are allowed from ``start`` (inclusive) to ``end`` (exclusive), local time, unless forced."""

    start: _dt.time = _dt.time(7, 0)
    end: _dt.time = _dt.time(22, 0)
    forced: bool = False

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"the daytime window must end after it starts ({self.label()})")

    def label(self) -> str:
        return f"{self.start:%H:%M}-{self.end:%H:%M}"

    def allows_writes(self, at: _dt.datetime | None = None) -> bool:
        if self.forced:
            return True
        t = (at or now()).time()
        return self.start <= t < self.end

    def banner(self) -> str:
        if self.forced:
            return f"--force-hours: writes allowed at any hour (window {self.label()} lifted)"
        if self.allows_writes():
            return f"writes allowed until {self.end:%H:%M}"
        return f"outside {self.label()}: read steps only"

    def as_dict(self) -> dict[str, object]:
        return {"start": f"{self.start:%H:%M}", "end": f"{self.end:%H:%M}", "forced": self.forced}
