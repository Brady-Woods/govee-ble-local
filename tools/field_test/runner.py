"""The field-test runner: scan, detect the SKU, run that SKU's plan, write a masked report.

Nothing in here is specific to a device; per-SKU behaviour lives in ``plans/<sku>.py`` (see
``plan.py``). For each run it walks the plan's steps over ONE BLE connection, reads state back
after each step, asks the tester what the device is doing, restores the original state, and writes:

    <sku>-report-<time>.json   steps, read-backs, answers, advertisements, environment
    <sku>-frames-<time>.jsonl  raw BLE frame capture (both directions)

Both files are safe to attach to a GitHub issue (see ``masking.py``).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import platform
import sys
import time
from collections.abc import Collection
from pathlib import Path
from typing import Any

from .masking import mask_address, redact_frames, snapshot
from .plan import PLANS, PlanContext, SkuPlan

_REPO_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if (_REPO_SRC / "govee_ble_local").is_dir():   # run from a checkout; else use the installed wheel
    sys.path.insert(0, str(_REPO_SRC))

TOOL_VERSION = 2
SCAN_SECONDS = 15.0


def match_plan_sku(name: str, identified_sku: str | None, candidates: Collection[str]) -> str | None:
    """Which of ``candidates`` (SKUs that have a plan) an advertised device is, or None.

    Tries the SKU the library identified, then the SKU parsed from the local name, then the plan's
    name hints (its own digits, e.g. ``6188``, plus ``extra_name_hints``) as a substring of the name.
    """
    from govee_ble_local.identify import sku_from_local_name

    wanted = {c.upper() for c in candidates}
    for parsed in (identified_sku, sku_from_local_name(name)):
        if parsed and parsed.upper() in wanted:
            return parsed.upper()
    low = name.lower()
    for sku in sorted(wanted):
        plan = PLANS.get(sku)
        hints = (sku[1:], *(plan.extra_name_hints if plan else ()))
        if any(h and h.lower() in low for h in hints):
            return sku
    return None


async def _ask(question: str, enabled: bool, free_text: bool = False) -> str | None:
    """y / n / s(kip), or a free-text answer; None when prompting is disabled."""
    if not enabled:
        return None
    hint = "describe what you see" if free_text else "y = yes, n = no, s = skip"
    while True:
        raw = (await asyncio.to_thread(input, f"    ? {question} [{hint}]: ")).strip()
        if free_text:
            return raw or "(no answer)"
        if raw.lower() in ("y", "n", "s"):
            return {"y": "yes", "n": "no", "s": "skipped"}[raw.lower()]


async def discover(
    address: str | None, sku: str | None
) -> tuple[Any, Any, list[dict[str, Any]], str | None]:
    """Scan, and return (BLEDevice, AdvertisementData, every Govee-ish advertisement heard, SKU).

    ``sku`` (from ``--sku``) restricts matching to that plan; ``address`` selects one device. The
    returned SKU is the plan to run (``sku`` if given, else detected), or None if it can't be told.
    """
    from bleak import BleakScanner

    from govee_ble_local.identify import identify

    heard: dict[str, dict[str, Any]] = {}
    hits: dict[str, tuple[Any, Any, str | None]] = {}
    candidates = [sku] if sku else sorted(PLANS)

    def on_adv(dev: Any, adv: Any) -> None:
        name = adv.local_name or dev.name or ""
        ident = identify(name, adv.manufacturer_data)
        plan_sku = match_plan_sku(name, ident.sku if ident else None, candidates)
        is_target = bool(address) and dev.address.upper() == address.upper()
        if plan_sku is None and ident is None and not is_target:
            return
        heard[dev.address] = {
            "address": mask_address(dev.address), "name": name, "rssi": adv.rssi,
            "identified_sku": ident.sku if ident else None,
            "encrypted_flag": ident.encrypted if ident else None,
            "manufacturer_data": {str(k): v.hex() for k, v in adv.manufacturer_data.items()},
        }
        if is_target:
            hits[dev.address] = (dev, adv, sku or plan_sku or (ident.sku if ident else None))
        elif not address and plan_sku:
            hits[dev.address] = (dev, adv, plan_sku)

    print(f"Scanning for {SCAN_SECONDS:.0f} s ...")
    async with BleakScanner(on_adv):
        await asyncio.sleep(SCAN_SECONDS)
    seen = sorted(heard.values(), key=lambda e: -(e["rssi"] or -999))
    for e in seen:
        print(f"  heard {e['name']!r:28s} sku={e['identified_sku']} rssi={e['rssi']} {e['address']}")
    if not hits:
        return None, None, seen, None
    if len(hits) > 1:
        kinds = sorted({h[2] or "?" for h in hits.values()})
        print(f"  more than one candidate ({', '.join(kinds)}); using the strongest "
              "(pass --address or --sku to choose)")
    dev, adv, found = max(hits.values(), key=lambda t: t[1].rssi)
    return dev, adv, seen, found


async def _restore(dev: Any, st: dict[str, Any]) -> str:
    """Put the device back the way it was found (best effort): brightness, then scene /
    colour temperature / RGB, then off if it was off."""
    try:
        if st.get("brightness"):
            await dev.set_brightness(st["brightness"])
        if st.get("scene_code") is not None:
            await dev.set_scene(st["scene_code"])
        elif st.get("color_temp_kelvin"):
            await dev.set_color_temp(st["color_temp_kelvin"])
        elif st.get("rgb_color"):
            await dev.set_rgb(tuple(st["rgb_color"]))
        if st.get("is_on") is False:
            await dev.set_power(False)
        return "ok"
    except Exception as err:
        return f"error: {type(err).__name__}: {err}"


async def _restore_with(plan: SkuPlan, dev: Any, st: dict[str, Any]) -> str:
    if plan.restore is None:
        return await _restore(dev, st)
    try:
        await plan.restore(dev, st)
        return "ok"
    except Exception as err:
        return f"error: {type(err).__name__}: {err}"


async def run(args: argparse.Namespace, out_dir: Path, stamp: str) -> int:
    import govee_ble_local as g
    from govee_ble_local import create_device
    from govee_ble_local.scenes import load_scenes

    prefix = (args.sku or "fieldtest").lower()
    report_path = out_dir / f"{prefix}-report-{stamp}.json"
    report: dict[str, Any] = {
        "tool_version": TOOL_VERSION, "library_version": g.__version__,
        "python": sys.version.split()[0], "platform": platform.platform(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "steps": [],
    }

    def save() -> None:
        report_path.write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")

    ble, adv, heard, sku = await discover(args.address, args.sku)
    report["advertisements"] = heard
    if ble is None:
        report["result"] = "device not found"
        save()
        looking = args.sku or ", ".join(sorted(PLANS))
        print(f"\nNo device with a test plan found (looking for: {looking}). "
              f"Report (with every Govee device heard) -> {report_path}")
        return 2
    plan = PLANS.get(sku or "")
    if plan is None:
        report["result"] = f"no test plan for SKU {sku or 'unknown'}"
        save()
        print(f"\nFound {ble.address} but no test plan matches it (SKU {sku or 'unknown'}). "
              f"Pass --sku (one of: {', '.join(sorted(PLANS))}). Report -> {report_path}")
        return 2
    report_path = out_dir / f"{plan.sku.lower()}-report-{stamp}.json"
    frames_path = out_dir / f"{plan.sku.lower()}-frames-{stamp}.jsonl"
    name = (adv.local_name if adv else None) or ble.name
    print(f"\n{plan.title}: using {name!r} at {ble.address}")
    if plan.intro:
        print(f"  {plan.intro}")
    dev = create_device(ble, plan.sku, adv, idle_disconnect=0, frame_log=str(frames_path))
    codes = {n: sc.code for n, sc in load_scenes(plan.sku).items()}
    report["plan"] = {"sku": plan.sku, "title": plan.title}
    report["device"] = {"address": mask_address(ble.address), "name": name}
    restore: dict[str, Any] | None = None
    try:
        await dev.connect()
        report["device"]["encryption"] = str(dev.encryption)
        initial = snapshot(await dev.update(), plan.snapshot_keys)
        restore = dict(initial)
        report["initial_state"] = initial
        print(f"Connected ({dev.encryption}); current state: {initial}\n")
        save()
        for i, step in enumerate(plan.build_steps(PlanContext(dev, plan.sku, codes)), 1):
            print(f"[{i:2d}] {step.name}")
            rec: dict[str, Any] = {"step": step.name, "note": step.note or None}
            try:
                await step.action()
                rec["sent"] = "ok"
            except Exception as err:
                rec["sent"] = f"error: {type(err).__name__}: {err}"
                print(f"    send failed: {rec['sent']}")
            await asyncio.sleep(args.settle)
            try:
                st = snapshot(await dev.update(), plan.snapshot_keys)
                rec["readback"] = st
                rec["readback_matches"] = step.expect(st) if step.expect else None
            except Exception as err:
                rec["readback"] = f"error: {type(err).__name__}: {err}"
                rec["readback_matches"] = False
            print(f"    read-back: {rec['readback']}  match={rec['readback_matches']}")
            if rec["sent"] == "ok":
                rec["observed"] = await _ask(step.question, not args.no_prompt, step.free_text)
            report["steps"].append(rec)
            save()
        report["result"] = "completed"
    except Exception as err:
        report["result"] = f"aborted: {type(err).__name__}: {err}"
        print(f"\nAborted: {report['result']}")
    finally:
        if restore is not None:
            report["restore"] = await _restore_with(plan, dev, restore)
        with contextlib.suppress(Exception):
            report["final_state"] = snapshot(await dev.update(), plan.snapshot_keys)
        await dev.stop()
        report["frames_redacted"] = redact_frames(frames_path, ble.address)
        report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        save()
    _summary(report)
    print("\nPlease attach BOTH files to the GitHub issue (addresses, serial and Wi-Fi MAC are"
          f" masked):\n  {report_path}\n  {frames_path}")
    return 0 if report.get("result") == "completed" else 1


def _summary(report: dict[str, Any]) -> None:
    print("\nSummary")
    for rec in report["steps"]:
        print(f"  {rec['step']:28s} sent={rec.get('sent')!s:6.6s} "
              f"readback={rec.get('readback_matches')!s:5s} observed={rec.get('observed')}")
    print(f"  restore: {report.get('restore')}  result: {report.get('result')}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    skus = ", ".join(sorted(PLANS))
    ap = argparse.ArgumentParser(
        description="Guided field test (run by someone with the device): scans, detects the SKU "
                    f"and runs its test plan. Plans available: {skus}.")
    ap.add_argument("--sku", type=str.upper, choices=sorted(PLANS), metavar="SKU",
                    help="run this SKU's plan (default: detect it from the scanned device)")
    ap.add_argument("--address", help="Bluetooth address (or macOS UUID) of the device; default: scan")
    ap.add_argument("--out", default=".", help="directory for the report + frame capture (default: .)")
    ap.add_argument("--no-prompt", action="store_true", help="don't ask questions; read-backs only")
    ap.add_argument("--settle", type=float, default=1.0, help="seconds to wait after each command")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    return asyncio.run(run(args, out, stamp))
