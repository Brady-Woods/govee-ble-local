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

RESTORE_OK = ("exact", "close", "not_needed", "unverified")


def counts(steps: Iterable[dict[str, Any]]) -> dict[str, int]:
    out = {"pass": 0, "fail": 0, "unverifiable": 0, "skipped": 0}
    for rec in steps:
        verdict = rec.get("verdict")
        if verdict in out:
            out[verdict] += 1
    return out


def device_failed(dev: dict[str, Any]) -> bool:
    restore = (dev.get("restore") or {}).get("result")
    result = str(dev.get("result") or "")
    failed_run = result.startswith(("aborted", "auth failed", "not created"))
    return dev.get("counts", {}).get("fail", 0) > 0 or failed_run or restore in ("partial", "failed")


def device_completed(dev: dict[str, Any]) -> bool:
    return dev.get("result") == "completed"


def totals(data: dict[str, Any]) -> dict[str, int]:
    devices = data.get("devices", [])
    not_run = [d for d in devices if str(d.get("result", "")).startswith("not run")]
    tested = [d for d in devices if d not in not_run]
    failed = [d for d in tested if device_failed(d)]
    reached = [d for d in tested if device_completed(d)
               or str(d.get("result", "")).startswith(("aborted", "interrupted"))]
    passed = [d for d in tested if device_completed(d) and not device_failed(d)]
    tested_refs = {d.get("ref") for d in devices}
    return {"heard": len(data.get("heard", [])), "tested": len(tested), "passed": len(passed),
            "failed": len(failed), "not_reached": len(tested) - len(reached), "not_run": len(not_run),
            "listed_only": len([h for h in data.get("heard", []) if h.get("ref") not in tested_refs])}


def gate_verdict(data: dict[str, Any]) -> str:
    """``fail`` (a step failed, a device aborted, a restore partial or failed), ``incomplete`` (a device not
    reached or not run, a write skipped, nothing tested), else ``pass``."""
    devices = data.get("devices", [])
    if any(device_failed(d) for d in devices):
        return "fail"
    if not devices or any(not device_completed(d) for d in devices):
        return "incomplete"
    if any(d.get("counts", {}).get("skipped", 0) for d in devices):
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
