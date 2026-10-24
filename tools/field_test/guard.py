"""The daytime guard: visible writes only inside a local-time window (default 07:00 to 22:00).

Every step that changes a device (``Step.kind == "write"``) is checked against the window when it is
about to run, so a run that crosses the late hour skips the rest of its writes; a restore is never
gated (it only puts the start state back). Listing, connecting and reading run at any hour, with one
exception: a row whose every connect itself makes a visible write (:func:`visible_connect`, read from its
profile's connect writes) is not connected to outside the window or on a read-only run
(:func:`connect_skip`, checked before every connect attempt), and once the window closes on it while it is
connected it is polled no more but by its restore (a dropped link would reconnect). ``--force-hours`` lifts
the window and is recorded in every report.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import os
from typing import Any

# The writes every connect of a row sends after the handshake and any unlock (its devices.yaml
# ``connect_writes``), by the DeviceProfile field that carries each: a plug's time sync, and the bulb
# screens' effect stop (bulblight ``VM4Light.i5``).
CONNECT_WRITES: tuple[tuple[str, str], ...] = (
    ("relay", "plug_sync_time"), ("light_effect_stop", "light_effect_stop"))
# Of those, the ones a person would see, as the listing and the summary name them. ``33 25 01`` stops a
# running ``33 24`` effect; a plug's time sync changes nothing visible.
VISIBLE_CONNECT_WRITES: dict[str, str] = {"light_effect_stop": "33 25 (effect stop)"}


def now() -> _dt.datetime:
    """Local wall-clock time (a function, so a test can fake the hour). ``GOVEE_FIELD_TEST_NOW`` (an ISO 8601
    time, which the test suite sets) pins it, so the suite's runs don't depend on the hour they run at."""
    pinned = os.environ.get("GOVEE_FIELD_TEST_NOW")
    return _dt.datetime.fromisoformat(pinned) if pinned else _dt.datetime.now()


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


def connect_writes(profile: Any) -> tuple[str, ...]:
    """The writes every connect to ``profile``'s row sends (the spec's command names, in the library's
    order). Read with ``getattr``: an older wheel's profile without a field sends none of that write."""
    return tuple(name for attr, name in CONNECT_WRITES if getattr(profile, attr, False))


def visible_connect(profile: Any) -> str | None:
    """What every connect to ``profile``'s row sends that a person would see (``"33 25 (effect stop)"``), or
    None (also for no profile)."""
    seen = [VISIBLE_CONNECT_WRITES[w] for w in connect_writes(profile) if w in VISIBLE_CONNECT_WRITES]
    return ", ".join(seen) or None


def connect_skip(visible: str | None, *, read_only: bool, writes_allowed: bool) -> str | None:
    """Why a device whose connect sends ``visible`` (:func:`visible_connect`) is not connected to: writes are
    not allowed now (outside the window, or a run started there without ``--yes``), or the run is read-only.
    None: it connects as any other device."""
    if not visible:
        return None
    if not writes_allowed:
        where = "outside the window"
    elif read_only:
        where = "on a read-only run"
    else:
        return None
    return f"skipped: its connect sends {visible}, a visible change, {where}"
