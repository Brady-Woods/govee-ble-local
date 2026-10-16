"""The field-test runner: scan, detect the SKU, run that SKU's plan, write a masked report.

Nothing in here is specific to a device; per-SKU behaviour lives in ``plans/<sku>.py`` (see
``plan.py``). For each run it walks the plan's steps over ONE BLE connection, reads state back
after each step, asks the tester what the device is doing, restores the original state, and writes:

    <sku>-report-<time>.json   steps, read-backs, answers, advertisements, environment
    <sku>-frames-<time>.jsonl  BLE frame capture (both directions, plaintext only)

Both files are safe to attach to a GitHub issue (see ``masking.py``).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import platform
import sys
import threading
import time
from collections.abc import Collection
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias

from .masking import (
    describe_error,
    mask_address,
    mask_name,
    mask_text,
    name_without_mac_tail,
    redact_frames,
    snapshot,
    write_text_atomic,
)
from .plan import PLANS, PlanContext, SkuPlan, Snapshot, Step

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData

    from govee_ble_local import Device

_REPO_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if (_REPO_SRC / "govee_ble_local").is_dir():   # run from a checkout; else use the installed wheel
    sys.path.insert(0, str(_REPO_SRC))

TOOL_VERSION = 2
SCAN_SECONDS = 15.0
FRAME_LOG_FLUSH_SECONDS = 2.0

Names: TypeAlias = tuple[str | None, ...]   # the device's advertised names (local name, BLEDevice.name)


def match_plan_sku(name: str, identified_sku: str | None, candidates: Collection[str]) -> str | None:
    """Which of ``candidates`` (SKUs that have a plan) an advertised device is, or None.

    A SKU the library identified, else the SKU parsed from the local name, decides on its own: the
    device matches that SKU's plan or none. Only a name with no SKU in it falls back to the plans'
    name hints (the SKU's own digits, e.g. ``6188``, plus ``extra_name_hints``) as a substring of the
    name without its trailing MAC digits; ``ihoment_H6006_6188`` is an H6006, never the H6188 plan.
    """
    from govee_ble_local.identify import sku_from_local_name

    wanted = {c.upper() for c in candidates}
    known = identified_sku or sku_from_local_name(name)
    if known:
        return known.upper() if known.upper() in wanted else None
    low = name_without_mac_tail(name).lower()
    for sku in sorted(wanted):
        plan = PLANS.get(sku)
        hints = (sku[1:], *(plan.extra_name_hints if plan else ()))
        if any(h and h.lower() in low for h in hints):
            return sku
    return None


async def _readline(prompt: str) -> str:
    """``input(prompt)`` on a daemon thread. ``asyncio.to_thread`` would run it in the default
    executor, which ``asyncio.run`` joins on the way out: after a Ctrl-C at a prompt the tool would
    hang, its summary printed, until the tester pressed Enter. Nothing joins a daemon thread."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str] = loop.create_future()

    def settle(line: str | None, error: Exception | None) -> None:
        if fut.done():   # the awaiting task was cancelled meanwhile
            return
        if error is not None:
            fut.set_exception(error)
        else:
            fut.set_result(line or "")

    def worker() -> None:
        line: str | None = None
        error: Exception | None = None
        try:
            line = input(prompt)
        except Exception as err:   # EOFError: raised in the awaiting task instead
            error = err
        with contextlib.suppress(RuntimeError):   # the loop is already closed: nobody is waiting
            loop.call_soon_threadsafe(settle, line, error)

    threading.Thread(target=worker, name="field-test-prompt", daemon=True).start()
    return await fut


async def _ask(question: str, enabled: bool, free_text: bool = False) -> str | None:
    """y / n / s(kip), or a free-text answer; None when prompting is disabled."""
    if not enabled:
        return None
    hint = "describe what you see" if free_text else "y = yes, n = no, s = skip"
    while True:
        raw = (await _readline(f"    ? {question} [{hint}]: ")).strip()
        if free_text:
            return raw or "(no answer)"
        if raw.lower() in ("y", "n", "s"):
            return {"y": "yes", "n": "no", "s": "skipped"}[raw.lower()]


async def discover(
    address: str | None, sku: str | None
) -> tuple[BLEDevice | None, AdvertisementData | None, list[dict[str, Any]], str | None]:
    """Scan, and return (BLEDevice, AdvertisementData, every Govee-ish advertisement heard, SKU).

    ``sku`` (from ``--sku``) restricts matching to that plan; ``address`` selects one device. The
    returned SKU is the plan to run (``sku`` if given, else detected), or None if it can't be told.
    The advertisements are already masked (address and the MAC digits in the name).
    """
    from bleak import BleakScanner

    from govee_ble_local.identify import identify

    heard: dict[str, dict[str, Any]] = {}
    hits: dict[str, tuple[BLEDevice, AdvertisementData, str | None]] = {}
    candidates = [sku] if sku else sorted(PLANS)

    def on_adv(dev: BLEDevice, adv: AdvertisementData) -> None:
        name = adv.local_name or dev.name or ""
        ident = identify(name, adv.manufacturer_data)
        plan_sku = match_plan_sku(name, ident.sku if ident else None, candidates)
        is_target = address is not None and dev.address.upper() == address.upper()
        if plan_sku is None and ident is None and not is_target:
            return
        heard[dev.address] = {
            "address": mask_address(dev.address), "name": mask_name(name), "rssi": adv.rssi,
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


def _err(err: BaseException, address: str | None, names: Names = ()) -> str:
    """A step / restore failure as written to the report (the address and names scrubbed)."""
    return f"error: {describe_error(err, address, names)}"


async def _restore(dev: Device, st: Snapshot) -> None:
    """Put the device back the way it was found (best effort): brightness, then scene /
    colour temperature / RGB, then off if it was off."""
    if st.get("brightness"):
        await dev.set_brightness(st["brightness"])
    if st.get("scene_code") is not None:
        await dev.set_scene(st["scene_code"])
    elif st.get("color_temp_kelvin"):
        await dev.set_color_temp(st["color_temp_kelvin"])
    elif st.get("rgb_color"):
        r, g, b = st["rgb_color"]
        await dev.set_rgb((r, g, b))
    if st.get("is_on") is False:
        await dev.set_power(False)


async def _restore_with(plan: SkuPlan, dev: Device, st: Snapshot, address: str | None = None,
                        names: Names = ()) -> str:
    """Run the plan's restore (default :func:`_restore`): ``"ok"``, or the (scrubbed) error."""
    try:
        await (plan.restore or _restore)(dev, st)
        return "ok"
    except Exception as err:
        return _err(err, address, names)


def _flush_frame_log(timeout: float = FRAME_LOG_FLUSH_SECONDS) -> None:
    """Wait (bounded) until the library's frame-log writer thread has written every queued record
    (``framelog.flush``): ``Device.stop()`` doesn't drain it, and :func:`redact_frames` must see (and
    replace) the whole capture, or records queued by the restore would land unredacted after the
    rewrite. If the flush itself fails, :func:`_finish` redacts straight away."""
    from govee_ble_local.transport import framelog

    framelog.flush(timeout)


async def _run_step(step: Step, dev: Device, plan: SkuPlan, args: argparse.Namespace,
                    address: str, names: Names = ()) -> dict[str, Any]:
    """Send one step, read the state back and check it, then ask the tester: the step's record."""
    rec: dict[str, Any] = {"step": step.name, "note": step.note or None}
    try:
        await step.action()
        rec["sent"] = "ok"
    except Exception as err:
        rec["sent"] = _err(err, address, names)
        print(f"    send failed: {rec['sent']}")
    await asyncio.sleep(args.settle)
    try:
        st = snapshot(await dev.update(), plan.snapshot_keys)
        rec["readback"] = st
        rec["readback_matches"] = step.expect(st) if step.expect else None
    except Exception as err:
        rec["readback"] = _err(err, address, names)
        rec["readback_matches"] = False
    print(f"    read-back: {rec['readback']}  match={rec['readback_matches']}")
    if rec["sent"] == "ok":
        rec["observed"] = await _ask(step.question, not args.no_prompt, step.free_text)
    return rec


async def _finish(report: dict[str, Any], plan: SkuPlan, dev: Device, initial: Snapshot | None,
                  frames_path: Path, address: str, names: Names = ()) -> None:
    """Clean up, in this order: restore the initial state, read the final state, disconnect, let
    the frame log reach the disk, then redact it. The redaction runs even if disconnecting fails,
    and the flush is best effort: if it fails (say the library's frame-log internals changed), the
    capture is redacted straight away rather than left raw."""
    if initial is not None:
        report["restore"] = await _restore_with(plan, dev, initial, address, names)
    with contextlib.suppress(Exception):
        report["final_state"] = snapshot(await dev.update(), plan.snapshot_keys)
    try:
        await dev.stop()
    finally:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(_flush_frame_log)
        report["frames_redacted"] = redact_frames(frames_path, address)
        report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")


async def run(args: argparse.Namespace, out_dir: Path, stamp: str) -> int:
    import govee_ble_local as g
    from govee_ble_local import create_device
    from govee_ble_local.scenes import load_scenes

    prefix = (args.sku or "fieldtest").lower()
    report_path = out_dir / f"{prefix}-report-{stamp}.json"
    address: str | None = None   # the device's, once found: scrubbed from everything saved
    names: Names = ()            # its advertised names, ditto (some bleak-retry-connector releases
    #                              quote them in connect errors)
    report: dict[str, Any] = {
        "tool_version": TOOL_VERSION, "library_version": g.__version__,
        "python": sys.version.split()[0], "platform": platform.platform(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "steps": [],
    }

    def save() -> None:
        # Every string in the report passes the scrubber once more on the way out (a backstop: the
        # error texts were already scrubbed where they were recorded).
        text = json.dumps(report, indent=1, default=str) + "\n"
        write_text_atomic(report_path, mask_text(text, address, names))

    ble, adv, heard, sku = await discover(args.address, args.sku)
    report["advertisements"] = heard
    if ble is None:
        report["result"] = "device not found"
        save()
        looking = args.sku or ", ".join(sorted(PLANS))
        print(f"\nNo device with a test plan found (looking for: {looking}). "
              f"Report (with every Govee device heard) -> {report_path}")
        return 2
    address = ble.address
    names = (adv.local_name if adv else None, ble.name)
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
    report["device"] = {"address": mask_address(address), "name": mask_name(name)}
    initial: Snapshot | None = None
    try:
        await dev.connect()
        report["device"]["encryption"] = str(dev.encryption)
        state = snapshot(await dev.update(), plan.snapshot_keys)
        initial = dict(state)
        report["initial_state"] = state
        print(f"Connected ({dev.encryption}); current state: {state}\n")
        save()
        for i, step in enumerate(plan.build_steps(PlanContext(dev, plan.sku, codes)), 1):
            print(f"[{i:2d}] {step.name}")
            report["steps"].append(await _run_step(step, dev, plan, args, address, names))
            save()
        report["result"] = "completed"
    except Exception as err:
        report["result"] = f"aborted: {describe_error(err, address, names)}"
        print(f"\nAborted: {report['result']}")
    finally:
        await _finish(report, plan, dev, initial, frames_path, address, names)
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
