#!/usr/bin/env python3
"""Guided H6188 field test — for someone who has the device, run once, send back the report.

The H6188 profile was modelled from the decompiled Govee Home app without hardware. This
walks every capability over ONE BLE connection, reads state back after each step, asks the
tester what the strip is doing, restores the original state, and writes:

    h6188-report-<time>.json   steps, read-backs, answers, advertisement, environment
    h6188-frames-<time>.jsonl  raw BLE frame capture (both directions)

Send BOTH files back privately (they contain the device's Bluetooth address and raw frames;
don't post them publicly).

    pip install govee_ble_local-<version>-py3-none-any.whl
    python h6188_field_test.py                 # scan, pick the H6188, interactive
    python h6188_field_test.py --address XX:XX:XX:XX:XX:XX
    python h6188_field_test.py --no-prompt     # no questions (read-backs only)

Two steps are diagnostics, not pass/fail: colour temperature 2750 K (the app sends no RGB
"tint" bytes for it, which shows whether the firmware renders the Kelvin value or the tint),
and the brightness read-backs (the app writes a raw 20..254 byte, not a percent).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import json
import platform
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

_REPO_SRC = Path(__file__).resolve().parent.parent / "src"
if (_REPO_SRC / "govee_ble_local").is_dir():   # run from a checkout; else use the installed wheel
    sys.path.insert(0, str(_REPO_SRC))

TOOL_VERSION = 1
SKU = "H6188"
SCAN_SECONDS = 15.0


def _snapshot(state: Any) -> dict[str, Any]:
    keys = ("is_on", "brightness", "rgb_color", "color_temp_kelvin", "scene_code",
            "firmware_version", "hardware_version", "serial_number", "wifi_mac")
    return {k: getattr(state, k, None) for k in keys}


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


async def discover(address: str | None) -> tuple[Any, Any, list[dict[str, Any]]]:
    """Return (BLEDevice, AdvertisementData, every Govee-ish advertisement heard)."""
    from bleak import BleakScanner

    from govee_ble_local.identify import identify

    heard: dict[str, dict[str, Any]] = {}
    hits: dict[str, tuple[Any, Any]] = {}

    def on_adv(dev: Any, adv: Any) -> None:
        name = adv.local_name or dev.name or ""
        ident = identify(name, adv.manufacturer_data)
        if "6188" not in name and ident is None and dev.address != address:
            return
        heard[dev.address] = {
            "address": dev.address, "name": name, "rssi": adv.rssi,
            "identified_sku": ident.sku if ident else None,
            "encrypted_flag": ident.encrypted if ident else None,
            "manufacturer_data": {str(k): v.hex() for k, v in adv.manufacturer_data.items()},
        }
        if (address and dev.address.upper() == address.upper()) or (
                not address and ("6188" in name or (ident and ident.sku == SKU))):
            hits[dev.address] = (dev, adv)

    print(f"Scanning for {SCAN_SECONDS:.0f} s ...")
    async with BleakScanner(on_adv):
        await asyncio.sleep(SCAN_SECONDS)
    seen = sorted(heard.values(), key=lambda e: -(e["rssi"] or -999))
    for e in seen:
        print(f"  heard {e['name']!r:28s} sku={e['identified_sku']} rssi={e['rssi']} {e['address']}")
    if not hits:
        return None, None, seen
    if len(hits) > 1:
        print("  more than one candidate; using the strongest (pass --address to choose)")
    dev, adv = max(hits.values(), key=lambda t: t[1].rssi)
    return dev, adv, seen


@dataclasses.dataclass
class Step:
    name: str
    action: Callable[[], Awaitable[None]]
    expect: Callable[[dict[str, Any]], bool] | None
    question: str
    free_text: bool = False
    note: str = ""


def build_steps(dev: Any, codes: dict[str, int]) -> list[Step]:
    s: list[Step] = [
        Step("power_off", lambda: dev.set_power(False), lambda st: st["is_on"] is False,
             "Is the strip OFF?"),
        Step("power_on", lambda: dev.set_power(True), lambda st: st["is_on"] is True,
             "Is the strip ON again?"),
    ]
    for label, rgb in (("red", (255, 0, 0)), ("green", (0, 255, 0)), ("blue", (0, 0, 255))):
        s.append(Step(f"rgb_{label}", lambda rgb=rgb: dev.set_rgb(rgb),
                      lambda st, rgb=rgb: st["rgb_color"] == rgb, f"Is the strip solid {label.upper()}?"))
    for pct, q in ((100, "Is it at FULL brightness?"), (1, "Is it VERY DIM but still lit?"),
                   (50, "Is it at roughly HALF brightness?")):
        s.append(Step(f"brightness_{pct}", lambda pct=pct: dev.set_brightness(pct),
                      lambda st, pct=pct: st["brightness"] == pct, q,
                      note="raw byte on the wire: 20..254 (check the frame capture)"))
    for k, q in ((2700, "Is it WARM (yellowish) white?"), (6500, "Is it COOL (bluish) white?"),
                 (9000, "Is it VERY COOL (blue-ish) white?")):
        s.append(Step(f"color_temp_{k}", lambda k=k: dev.set_color_temp(k),
                      lambda st, k=k: st["color_temp_kelvin"] == k, q))
    s.append(Step("color_temp_2750_no_tint", lambda: dev.set_color_temp(2750),
                  lambda st: st["color_temp_kelvin"] == 2750,
                  "DIAGNOSTIC 2750 K: is it warm white like 2700 K, OFF/black, or something else?",
                  free_text=True, note="the app sends no tint bytes for a Kelvin not on a 100 K step"))
    for name in dev.scene_names:
        code = codes.get(name)
        s.append(Step(f"scene_{name}", lambda name=name: dev.set_scene_by_name(name),
                      lambda st, code=code: st["scene_code"] == code,
                      f"Scene '{name}': is an effect/scene running (not a plain colour)?"))
    return s


async def run(args: argparse.Namespace, report_path: Path, frames_path: Path) -> int:
    import govee_ble_local as g
    from govee_ble_local import create_device
    from govee_ble_local.scenes import load_scenes

    report: dict[str, Any] = {
        "tool_version": TOOL_VERSION, "library_version": g.__version__,
        "python": sys.version.split()[0], "platform": platform.platform(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "steps": [],
    }

    def save() -> None:
        report_path.write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")

    ble, adv, heard = await discover(args.address)
    report["advertisements"] = heard
    if ble is None:
        report["result"] = "device not found"
        save()
        print(f"\nNo H6188 found. Report (with every Govee device heard) -> {report_path}")
        return 2
    name = (adv.local_name if adv else None) or ble.name
    print(f"\nUsing {name!r} at {ble.address}")
    dev = create_device(ble, SKU, adv, idle_disconnect=0, frame_log=str(frames_path))
    codes = {n: sc.code for n, sc in load_scenes(SKU).items()}
    report["device"] = {"address": ble.address, "name": name}
    restore: dict[str, Any] | None = None
    try:
        await dev.connect()
        report["device"]["encryption"] = str(dev.encryption)
        initial = _snapshot(await dev.update())
        restore = dict(initial)
        report["initial_state"] = initial
        print(f"Connected ({dev.encryption}); current state: {initial}\n")
        save()
        for i, step in enumerate(build_steps(dev, codes), 1):
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
                st = _snapshot(await dev.update())
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
            report["restore"] = await _restore(dev, restore)
        with contextlib.suppress(Exception):
            report["final_state"] = _snapshot(await dev.update())
        await dev.stop()
        report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        save()
    _summary(report)
    print(f"\nPlease send back BOTH files (privately):\n  {report_path}\n  {frames_path}")
    return 0 if report.get("result") == "completed" else 1


async def _restore(dev: Any, st: dict[str, Any]) -> str:
    """Put the strip back the way it was found (best effort)."""
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


def _summary(report: dict[str, Any]) -> None:
    print("\nSummary")
    for rec in report["steps"]:
        print(f"  {rec['step']:28s} sent={rec.get('sent')!s:6.6s} "
              f"readback={rec.get('readback_matches')!s:5s} observed={rec.get('observed')}")
    print(f"  restore: {report.get('restore')}  result: {report.get('result')}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Guided H6188 field test (run by someone with the device).")
    ap.add_argument("--address", help="Bluetooth address (or macOS UUID) of the H6188; default: scan")
    ap.add_argument("--out", default=".", help="directory for the report + frame capture (default: .)")
    ap.add_argument("--no-prompt", action="store_true", help="don't ask questions; read-backs only")
    ap.add_argument("--settle", type=float, default=1.0, help="seconds to wait after each command")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    return asyncio.run(run(args, out / f"h6188-report-{stamp}.json", out / f"h6188-frames-{stamp}.jsonl"))


if __name__ == "__main__":
    sys.exit(main())
