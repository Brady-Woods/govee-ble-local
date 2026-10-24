"""The run's summary: one masked JSON per run, rewritten after each device, and the console table.

``fieldtest-summary-<stamp>.json`` lists every Govee device heard (masked), and per device tested its
steps' verdicts, its restore result and its errors, with the names of its own report and frame capture.
It is passed through :func:`.masking.mask_text_all` for every device the run heard, and plug secrets are
scrubbed, so it is safe to attach.
"""
from __future__ import annotations

import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .masking import json_default, mask_text_all, write_text_atomic

def counts(steps: Iterable[dict[str, Any]]) -> dict[str, int]:
    out = {"pass": 0, "fail": 0, "unverifiable": 0, "skipped": 0}
    for rec in steps:
        verdict = rec.get("verdict")
        if verdict in out:
            out[verdict] += 1
    return out


def left_changed(dev: dict[str, Any]) -> list[str]:
    """What the run wrote that its restore can never put back (``restore.unrestorable_written``: a start
    colour, brightness or power never read)."""
    return list((dev.get("restore") or {}).get("unrestorable_written") or [])


def device_failed(dev: dict[str, Any]) -> bool:
    """A failed step, an aborted run, a partial or failed restore, or a restore nothing could verify after
    the run wrote what it can never put back."""
    restore = (dev.get("restore") or {}).get("result")
    result = str(dev.get("result") or "")
    failed_run = result.startswith(("aborted", "auth failed", "not created"))
    unverifiable_loss = restore == "unverified" and bool(left_changed(dev))
    return (dev.get("counts", {}).get("fail", 0) > 0 or failed_run or restore in ("partial", "failed")
            or unverifiable_loss)


def device_completed(dev: dict[str, Any]) -> bool:
    return dev.get("result") == "completed"


def unverified_writes(dev: dict[str, Any]) -> int:
    """The write steps nothing verified: no read-back carried the field, and no tester's answer
    (``--no-prompt``, or ``s``)."""
    return sum(1 for s in dev.get("steps", [])
               if s.get("kind") == "write" and s.get("verdict") == "unverifiable")


def device_passed(dev: dict[str, Any]) -> bool:
    """Completed, nothing failed, every write step ran and was verified (by a read-back or the tester), its
    row's unlock was sent where the row needs a secret (``no_secret``: every plug, tested without its
    secret, is not a pass), its restore put back everything the run wrote (a ``close`` restore that left
    a colour, brightness or power changed for good is not a pass) and was verified (by a read-back or the
    tester's yes: an ``unverified`` restore, a card row's nobody answered for, is not a pass), and no Ctrl-C
    reached it (one during its restore lets the restore finish, but the tester stopped the run)."""
    restore = (dev.get("restore") or {}).get("result")
    return (device_completed(dev) and not device_failed(dev) and not dev.get("counts", {}).get("skipped", 0)
            and not unverified_writes(dev) and not dev.get("no_secret") and not dev.get("interrupted")
            and not left_changed(dev) and restore != "unverified")


def device_skipped(dev: dict[str, Any]) -> bool:
    """Not connected to: every connect of its row makes a visible write, and writes were not allowed
    (``guard.connect_skip``)."""
    return str(dev.get("result") or "").startswith("skipped")


def totals(data: dict[str, Any]) -> dict[str, int]:
    devices = data.get("devices", [])
    not_run = [d for d in devices if str(d.get("result", "")).startswith("not run")]
    skipped = [d for d in devices if device_skipped(d)]
    tested = [d for d in devices if d not in not_run and d not in skipped]
    failed = [d for d in tested if device_failed(d)]
    reached = [d for d in tested if device_completed(d)
               or str(d.get("result", "")).startswith(("aborted", "interrupted"))]
    passed = [d for d in tested if device_passed(d)]
    tested_refs = {d.get("ref") for d in devices}
    return {"heard": len(data.get("heard", [])), "tested": len(tested), "passed": len(passed),
            "failed": len(failed), "unverified": len([d for d in tested if unverified_writes(d)]),
            "no_secret": len([d for d in tested if d.get("no_secret")]),
            "left_changed": len([d for d in tested if left_changed(d)]),
            "not_restored": len([d for d in tested if d.get("not_restored")]),
            "not_reached": len(tested) - len(reached), "skipped": len(skipped), "not_run": len(not_run),
            "listed_only": len([h for h in data.get("heard", []) if h.get("ref") not in tested_refs])}


def gate_verdict(data: dict[str, Any]) -> str:
    """``fail`` (a step failed, a device aborted, a restore partial or failed, or unverified after the run
    wrote what it can never put back), ``incomplete`` (a device not reached, not run or skipped, a write
    skipped or unverified, a restore nothing verified, a device whose row needs a secret tested without one,
    a close restore that left a write in place for good, a Ctrl-C, nothing tested), else ``pass``."""
    devices = data.get("devices", [])
    if any(device_failed(d) for d in devices):
        return "fail"
    if not devices or not all(device_passed(d) for d in devices):
        return "incomplete"
    return "pass"


class Summary:
    """The summary file of one run. ``mask`` lists every ``(address, names)`` the run heard."""

    def __init__(self, path: Path, base: dict[str, Any]) -> None:
        self.path = path
        self.data: dict[str, Any] = {**base, "heard": [], "devices": []}
        self.mask: list[tuple[str | None, tuple[str | None, ...]]] = []
        self.secrets: list[bytes] = []

    def add_device(self, rec: dict[str, Any]) -> None:
        self.data["devices"].append(rec)
        self.save()

    def save(self) -> None:
        self.data["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.data["totals"] = totals(self.data)
        self.data["gate"] = gate_verdict(self.data)
        text = json.dumps(self.data, indent=1, default=json_default) + "\n"
        write_text_atomic(self.path, mask_text_all(text, self.mask, self.secrets))


def console_table(data: dict[str, Any], path: Path | None = None) -> str:
    rows = [f"  {'ref':6s} {'sku':6s} {'tier':8s} {'kind':5s} {'plan':7s} {'pass':>4s} {'fail':>4s} "
            f"{'unv':>4s} {'skip':>4s}  {'restore':10s} result"]
    for d in data.get("devices", []):
        c = d.get("counts", {})
        rows.append(f"  {d.get('ref', '?'):6s} {d.get('sku') or '?':6s} {d.get('tier') or '?':8s} "
                    f"{d.get('kind') or '?':5s} {d.get('plan') or '?':7s} {c.get('pass', 0):4d} "
                    f"{c.get('fail', 0):4d} {c.get('unverifiable', 0):4d} {c.get('skipped', 0):4d}  "
                    f"{(d.get('restore') or {}).get('result') or '-':10s} {d.get('result')}")
    t = data.get("totals") or totals(data)
    rows.append("  totals: " + ", ".join(f"{k} {v}" for k, v in t.items()))
    rows.append(f"  gate: {data.get('gate') or gate_verdict(data)}")
    if path is not None:
        rows.append(f"  summary: {path}")
    return "\n".join(rows)
