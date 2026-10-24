"""tools/field_test: the generic runner (masking, SKU detection, plan registry) with a fake device, the
generic plan over every profile, and the release-gate modes (--list, --all) on the fake BLE.

The H6188 plan's end-to-end run lives in test_h6188.py; the per-SKU plans of later devices test
their own steps. No BLE hardware."""
from __future__ import annotations

import asyncio
import datetime
import errno
import importlib
import io
import json
import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"


@pytest.fixture
def ft(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.syspath_prepend(str(TOOLS))
    yield importlib.import_module("field_test")


@pytest.fixture
def runner(ft: ModuleType) -> ModuleType:
    return importlib.import_module("field_test.runner")


@pytest.fixture
def masking(ft: ModuleType) -> ModuleType:
    return importlib.import_module("field_test.masking")


# --- masking (preserved from the H6188 field test) --------------------------------------------


def test_masks_address_serial_and_wifi_mac(masking: ModuleType, tmp_path: Path) -> None:
    frames = tmp_path / "f.jsonl"
    info = "aa0710" + "02005e005357" + "1122334455" + "00" * 4   # device-info reply: MAC + serial
    rows = [
        {"ts": 1, "addr": "02:00:5E:00:53:57", "dir": "tx", "plain": "aa0710" + "00" * 17,
         "wire": "aa0710" + "00" * 17, "enc": "none"},
        {"ts": 2, "addr": "02:00:5E:00:53:57", "dir": "rx", "plain": info, "wire": info, "enc": "none"},
        {"ts": 3, "addr": "02:00:5E:00:53:57", "dir": "rx", "plain": "aa04fe" + "00" * 17,
         "wire": "aa04fe" + "00" * 17, "enc": "none"},
    ]
    frames.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert masking.redact_frames(frames, "02:00:5E:00:53:57") == 3
    out = [json.loads(line) for line in frames.read_text().splitlines()]
    assert {r["addr"] for r in out} == {"02:00:5E:xx:xx:xx"}
    assert out[1]["plain"] == "aa0710" + "0" * (len(info) - 6) and out[1]["wire"] is None
    assert out[0]["plain"] == rows[0]["plain"] and out[2]["plain"] == rows[2]["plain"]  # untouched
    assert all(r["wire"] is None for r in out)   # wire bytes (ciphertext, handshake) are never kept
    raw = frames.read_text().lower()
    assert "02005e005357" not in raw and "00:53:57" not in raw and "1122334455" not in raw
    assert masking.redact_frames(tmp_path / "missing.jsonl", "x") == 0
    assert masking.mask_address("02-00-5e-00-53-57") == "02-00-5E-xx-xx-xx"
    assert masking.mask_address("A1B2C3D4-1111-2222-3333-444455556666") == "A1B2C3D4-…"
    assert masking.mask_address(None) is None
    # a plug's secret given to the run is scrubbed as hex, in either case, from every report text
    secret = bytes.fromhex("0011aabbccddeeff")
    text = masking.mask_text("key 0011AABBCCDDEEFF / 0011aabbccddeeff", secrets=[secret])
    assert text == "key <redacted> / <redacted>"
    assert "aabbcc" not in masking.mask_text_all(f"{secret.hex()} 02:00:5E:00:53:57",
                                                 [("02:00:5E:00:53:57", ())], [secret]).lower()


def test_snapshot_redacts_identifiers_even_if_a_plan_asks_for_them(masking: ModuleType) -> None:
    st = SimpleNamespace(is_on=True, serial_number="SER123", wifi_mac="AA:BB:CC:DD:EE:FF", extra=7,
                         device_id="SER123", ble_mac="AA:BB:CC:DD:EE:00")
    keys = ("is_on", "serial_number", "wifi_mac", "extra", "missing", "device_id", "ble_mac")
    snap = masking.snapshot(st, keys)
    assert snap == {"is_on": True, "serial_number": "<redacted>", "wifi_mac": "<redacted>",
                    "extra": 7, "missing": None, "device_id": "<redacted>", "ble_mac": "<redacted>"}


# --- registry + discovery of plans ------------------------------------------------------------


def test_h6188_plan_is_registered_by_package_import(ft: Any) -> None:
    assert "H6188" in ft.PLANS and ft.PLANS["H6188"].sku == "H6188"
    assert {"PLANS", "SkuPlan", "Step", "PlanContext", "register_plan"} <= set(ft.__all__)


def test_register_plan_rejects_duplicates_and_lowercase(ft: Any) -> None:
    plan = ft.SkuPlan(sku="H9998", title="t", build_steps=lambda _ctx: [])
    try:
        assert ft.register_plan(plan) is plan
        with pytest.raises(ValueError, match="already registered"):
            ft.register_plan(plan)
    finally:
        ft.PLANS.pop("H9998", None)
    with pytest.raises(ValueError, match="upper-case"):
        ft.register_plan(ft.SkuPlan(sku="h9998", title="t", build_steps=lambda _ctx: []))
    assert "H9998" not in ft.PLANS


def test_plans_package_imports_every_module_in_the_directory(ft: Any) -> None:
    on_disk = {p.stem for p in (TOOLS / "field_test" / "plans").glob("*.py") if p.stem != "__init__"}
    registered = {sku.lower() for sku in ft.PLANS}
    assert on_disk <= registered   # each plans/<sku>.py registered its plan


# --- SKU detection ----------------------------------------------------------------------------


def test_match_plan_sku(runner: Any) -> None:
    m = runner.match_plan_sku
    assert m("ihoment_H6188_1A2B", None, ["H6188"]) == "H6188"
    assert m("whatever", "h6188", ["H6188"]) == "H6188"          # library-identified SKU wins
    assert m("GV61881A2B", None, ["H6188"]) == "H6188"           # bare-GV name, parsed like the app
    assert m("Weird 6188 name", None, ["H6188"]) == "H6188"      # digits-in-name fallback
    assert m("ihoment_H6006_6188", "H6006", ["H6188"]) is None   # an H6006 whose MAC ends in 61:88
    assert m("Govee_H6008_6047", None, ["H6047"]) is None        # the parsed SKU decides, not the digits
    assert m("ihoment_H6047_1A2B", None, ["H6188"]) is None
    assert m("", None, ["H6188"]) is None


def test_name_hints_select_a_plan(ft: Any, runner: Any) -> None:
    plan = ft.SkuPlan(sku="H9997", title="t", build_steps=lambda _ctx: [], extra_name_hints=("Glide",))
    ft.register_plan(plan)
    try:
        assert runner.match_plan_sku("Govee Glide wall", None, ["H9997"]) == "H9997"
        assert runner.match_plan_sku("Govee Strip", None, ["H9997"]) is None
    finally:
        ft.PLANS.pop("H9997", None)


# --- running a (fake) plan --------------------------------------------------------------------


class _FakeDevice:
    scene_names: tuple[str, ...] = ()
    encryption = "none"

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.on = True

    async def connect(self) -> None: ...

    async def stop(self) -> None: ...

    async def update(self) -> SimpleNamespace:
        return SimpleNamespace(is_on=self.on, tint=5, serial_number="SER1", wifi_mac="MAC1")

    async def set_power(self, on: bool) -> None:
        self.calls.append(("power", on))
        self.on = on


def _run_fake_plan(ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                   argv: list[str], restore_log: list[Any]) -> tuple[int, _FakeDevice]:
    dev = _FakeDevice()

    async def restore(d: Any, st: dict[str, Any]) -> None:
        restore_log.append((d, st))

    plan = ft.SkuPlan(
        sku="H9996", title="H9996 test lamp", intro="hello",
        snapshot_keys=("is_on", "tint", "serial_number", "wifi_mac"),
        restore=restore,
        build_steps=lambda ctx: [
            ft.Step("off", lambda: ctx.dev.set_power(False), lambda st: st["is_on"] is False, "Off?"),
            ft.Step("bad", lambda: ctx.dev.set_power(True), lambda st: st["tint"] == 6, "On?"),
        ],
    )
    ft.register_plan(plan)
    ble = SimpleNamespace(address="AA:BB:CC:DD:EE:11", name="ihoment_H9996_1A2B")
    adv = SimpleNamespace(local_name="ihoment_H9996_1A2B", manufacturer_data={}, rssi=-50)
    seen: list[Any] = []

    async def fake_discover(address: Any, sku: Any) -> tuple[Any, Any, list[Any], str]:
        seen.append((address, sku))
        return ble, adv, [], sku or "H9996"

    monkeypatch.setattr(runner, "discover", fake_discover)
    monkeypatch.setattr("govee_ble_local.create_device", lambda *_a, **_k: dev)
    try:
        args = runner.parse_args([*argv, "--no-prompt", "--settle", "0"])
        return asyncio.run(runner.run(args, tmp_path, "T")), dev
    finally:
        ft.PLANS.pop("H9996", None)


def test_runner_runs_a_registered_plan_with_its_own_snapshot_keys_and_restore(
    ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log: list[Any] = []
    rc, dev = _run_fake_plan(ft, runner, monkeypatch, tmp_path, ["--unattended"], log)
    assert rc == 0 and dev.calls == [("power", False), ("power", True)]
    rep = json.loads((tmp_path / "h9996-report-T.json").read_text())
    assert rep["plan"]["sku"] == "H9996" and rep["restore"] == "ok" and len(log) == 1
    assert rep["initial_state"] == {"is_on": True, "tint": 5, "serial_number": "<redacted>",
                                    "wifi_mac": "<redacted>"}
    assert [r["readback_matches"] for r in rep["steps"]] == [True, False]
    # the first check reads is_on, which no reply of this profile-less fake carried: it holds, and proves
    # nothing; the second fails on a key that is no state field the library could have intended
    assert [(r["verdict"], r["readback_unread"]) for r in rep["steps"]] == [
        ("unverifiable", ["is_on"]), ("fail", None)]
    assert "AA:BB:CC:DD:EE:11" not in (tmp_path / "h9996-report-T.json").read_text()
    # with no terminal on stdin, one device as --all: no visible write unless --unattended, recorded
    alone = tmp_path / "alone"
    alone.mkdir()
    rc, dev = _run_fake_plan(ft, runner, monkeypatch, alone, [], log)
    summary = json.loads((alone / "fieldtest-summary-T.json").read_text())
    assert rc == 0 and dev.calls == [] and summary["options"]["unattended"] is False
    assert summary["options"]["writes_off"] == runner.NO_TERMINAL
    assert {s["verdict"] for s in summary["devices"][0]["steps"]} == {"skipped"}


def test_sku_flag_is_passed_to_discovery_case_insensitively(
    ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rc, _ = _run_fake_plan(ft, runner, monkeypatch, tmp_path, ["--sku", "h9996"], [])
    assert rc == 0 and (tmp_path / "h9996-report-T.json").exists()


def test_runner_reports_not_found_and_unplanned_sku(
    ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def nothing(_a: Any, _s: Any) -> tuple[Any, Any, list[Any], None]:
        return None, None, [{"name": "x"}], None

    monkeypatch.setattr(runner, "discover", nothing)
    assert asyncio.run(runner.run(runner.parse_args(["--no-prompt"]), tmp_path, "T")) == 2
    assert json.loads((tmp_path / "fieldtest-report-T.json").read_text())["result"] == "device not found"

    ble = SimpleNamespace(address="AA:BB:CC:DD:EE:11", name="n")

    async def unknown(_a: Any, _s: Any) -> tuple[Any, Any, list[Any], None]:
        return ble, None, [], None

    monkeypatch.setattr(runner, "discover", unknown)
    assert asyncio.run(runner.run(runner.parse_args(["--address", "x"]), tmp_path, "U")) == 2
    assert "no test plan" in json.loads((tmp_path / "fieldtest-report-U.json").read_text())["result"]

    # the secrets file: none there (the session's) is no failure; one group or others can read is refused,
    # saying why and printing no value; the ref key is made 0600 and refused once wider
    from fake_ble import ble_device

    from govee_ble_local import GoveeBleError, create_device
    from govee_ble_local.transport import connection as conn_mod
    survey = importlib.import_module("field_test.survey")
    assert runner._read_secrets(None) == {}
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"AA:BB:CC:DD:EE:11": "0011aabbccddeeff"}))
    secrets.chmod(0o644)
    with pytest.raises(runner.Refused, match=r"group or others can read it \(mode 0644\); chmod 600"):
        runner._read_secrets(str(secrets))
    secrets.chmod(0o600)
    assert runner._read_secrets(str(secrets)) == {"AA:BB:CC:DD:EE:11": bytes.fromhex("0011aabbccddeeff")}
    assert "aabbcc" not in capsys.readouterr().out.lower()
    key = tmp_path / "keys" / "ref.key"
    assert len(survey._ref_key(key)) == 32 and key.stat().st_mode & 0o777 == 0o600
    key.chmod(0o640)
    with pytest.raises(OSError, match=r"readable by group or others \(mode 0640\)"):
        survey._ref_key(key)
    with monkeypatch.context() as m:   # the tool refuses it (exit 3) before any scan, not with a traceback
        m.setenv(survey.REF_KEY_ENV, str(key))
        assert runner.main(["--list", "--out", str(tmp_path / "wide")]) == runner.EXIT_REFUSED
        shown = capsys.readouterr().out
        assert f"Refused: {key}: the ref key is readable by group or others (mode 0640)" in shown
        assert key not in survey._KEYS
    # under pytest no real scan or connect, even in a tree whose conftest sets no GOVEE_FIELD_TEST_NO_SCAN;
    # the connect no_radio stops is a tripwire, bleak's in name only: a broken guard fails here, off the radio
    monkeypatch.delenv("GOVEE_FIELD_TEST_NO_SCAN", raising=False)
    with pytest.raises(RuntimeError, match=r"scanning is disabled \(PYTEST_CURRENT_TEST"):
        survey._scanner()

    async def tripwire(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("no_radio let a connect through")

    tripwire.__module__ = "bleak_retry_connector"
    assert survey._real(conn_mod.establish_connection)
    monkeypatch.setattr(conn_mod, "establish_connection", tripwire)
    dev = create_device(ble_device("AA:BB:CC:DD:EE:47", "ihoment_H6047_1A2B"), "H6047")
    with survey.no_radio(), pytest.raises(GoveeBleError, match=r"connecting is disabled \(PYTEST_CUR"):
        asyncio.run(dev.connect())
    assert conn_mod.establish_connection is tripwire   # put back


def test_help_runs_as_a_directory_and_lists_plans() -> None:
    r = subprocess.run(  # noqa: S603 - fixed argv, sys.executable
        [sys.executable, str(TOOLS / "field_test"), "--help"], capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stderr
    assert "H6188" in r.stdout and "--sku" in r.stdout
    for flag in ("--list", "--all", "--toggle-plugs", "--allow-modelled", "--force-hours", "--library",
                 "--secrets-file", "--read-only", "--generic", "--ask"):
        assert flag in r.stdout, flag


# --- the release-gate pass: the generic plan and the --list / --all modes ----------------------


async def test_the_generic_plan_follows_every_profile(ft: Any) -> None:
    """Every profile the library routes (each row of each SKU, the modelled ones too) gets a generic plan
    whose step names are unique, whose plugs get no write step without the allow-list (and with it, toggle
    from a mixed start of their outlets only where the power write sets outlet 0 alone), whose card rows read
    nothing back, whose refusal steps raise GoveeBleNotSupported before any I/O, and whose other steps the
    library accepts (a fake connection: writes acked, reads answered with nothing). A segment-brightness step
    judges no segment the library dimmed as unchanged (the strip's groups widen the write), and the gradual
    toggle waits for a start the first poll read. A scene start is restored by name, after the segment levels.
    A poll that brings every reply but sets nothing (the library
    dropped or rejected them) reads nothing back, and a poll answered by nobody sets nothing. The visible
    write the tool reads from each profile's connect writes is what the library's every connect sends but a
    plug's time sync. One property over the table, not a test per SKU; then what a read-back proves: an RGB
    read after a Kelvin write on a report with no Kelvin nothing, a field intended as it already was nothing
    by matching, a segment only where the poll set it; what a restore puts back and checks; the H6047 plan's
    gradual toggle from a read start only; when a Ctrl-C abandons a restore."""
    from unittest.mock import AsyncMock

    from fake_ble import ble_device

    from govee_ble_local import GoveeBleError, GoveeBleInvalidValue, GoveeBleNotSupported, profile_for
    from govee_ble_local.devices import profile as table
    from govee_ble_local.devices.device import Device
    from govee_ble_local.models import Segment

    generic = importlib.import_module("field_test.generic")
    guard = importlib.import_module("field_test.guard")
    readback = importlib.import_module("field_test.readback")
    restore = importlib.import_module("field_test.restore")
    every_reply = [readback.Frame(0.0, "rx", op, 0x15, frozenset(range(256)))
                   for op in ("aa01", "aa04", "aa05", "aa34", "aa36", "aaa3", "aaa5", "ac")]
    routes = {(sku, id(r.profile)): (sku, r.profile, r.pacts)
              for rows in (table._ADMITTED[1], table._MODELLED[1]) for sku, rs in rows.items() for r in rs}
    bad: list[str] = []
    for sku, profile, pacts in routes.values():
        dev = Device(profile, ble_device("AA:BB:CC:DD:EE:01", f"ihoment_{sku}_1A2B"), None, sku=sku)
        if pacts:
            dev._learn_pact(min(pacts))
        sent: list[bytes] = []

        async def one(frame: bytes, *_a: object, sent: list[bytes] = sent, **_k: object) -> None:
            sent.append(frame)

        async def many(frames: Any, *_a: object, sent: list[bytes] = sent, **_k: object) -> None:
            sent.extend(frames(20) if callable(frames) else frames)

        async def nothing(*_a: object, **_k: object) -> list[bytes]:
            return []

        for name, fake in (("send", one), ("send_once", one), ("send_many", many), ("send_unacked", many),
                           ("query", nothing), ("connect", nothing)):
            if hasattr(dev._conn, name):
                setattr(dev._conn, name, AsyncMock(side_effect=fake))
        kind = generic.device_kind(profile)
        visible = {f[:2].hex(" ") for f in dev._session_frames() if f[:2] != b"\x33\xb5"}   # but a time sync
        label = guard.visible_connect(profile) or ""
        if bool(visible) != bool(label) or not all(op in label for op in visible):
            bad.append(f"{sku}: its connect sends {sorted(visible)}, the tool reads {label!r}")
        start = restore.StartState(dev.snapshot(), frozenset(), kind)
        steps = generic.generic_steps(ft.PlanContext(dev, sku, {}, kind=kind, start=start))
        names = [s.name for s in steps]
        if len(set(names)) != len(names):
            bad.append(f"{sku}: repeated step names {names}")
        if kind == "plug" and any(s.kind == "write" for s in steps):
            bad.append(f"{sku}: a plug write step without the allow-list")
        for bits in (0b000, 0b001) if kind == "plug" else ():   # all off; outlet 0 alone on
            state = dev.snapshot()
            state.is_on = bool(bits)
            lit = restore.StartState(state, frozenset({"is_on"}), kind, relay_bits=bits)
            ctx = ft.PlanContext(dev, sku, {}, kind=kind, start=lit, toggle_ok=True, has_secret=True)
            skip = {s.name: s.skip for s in generic.generic_steps(ctx)}["relay_toggle"]
            if bool(skip) != (bits == 0b001 and readback.outlet_mask(profile) != 0x01):
                bad.append(f"{sku}: relay bits {bits:03b} at the start, toggle skip {skip!r}")
        if (kind == "card" or profile.readback == "none") and any(
                r is None for r in readback.carried(dev, every_reply).values()):
            bad.append(f"{sku}: a card row judged on a read-back")
        if any(r is None for set_ in (None, frozenset())
               for r in readback.carried(dev, every_reply, assigned=set_).values()):
            bad.append(f"{sku}: a field read back from a poll that set nothing")
        gradual = [s for s in steps if s.name.startswith("gradual_")]
        if getattr(profile, "gradual", False) and not all(s.skip for s in gradual):
            bad.append(f"{sku}: a gradual toggle from a start nobody read")
        try:
            listed = list(dev.scene_names)
        except GoveeBleError:
            listed = []
        if listed:   # a scene start is put back by name (its upload with it), after the segment levels
            state = dev.snapshot()
            state.scene_code, state.segments = 1, [Segment(0, None, 100)]
            scened = restore.StartState(state, frozenset({"scene_code", "segments"}), kind,
                                        active_scene=listed[0])
            acts = restore._colour_actions(dev, scened, restore.Outcome(), None)
            levels = "segments" in {str(c) for c in dev.capabilities} and getattr(
                dev, "supports_segment_brightness", True)
            if acts[-1][1:] != (dev.set_scene_by_name, (listed[0],)) or (
                    levels and acts[0][1] != dev.set_segment_brightness):
                bad.append(f"{sku}: a scene start restored by {[a[0] for a in acts]}")
        if kind != "card" and profile.readback != "none":
            try:
                seen = (await readback.poll(dev)).assigned
            except GoveeBleError:
                seen = frozenset()
            if seen is None or seen & set(readback.FIELDS):
                bad.append(f"{sku}: an unanswered poll set {seen}")
        for step in steps:
            before, raised = len(sent), None
            try:
                await step.action()
            except (GoveeBleNotSupported, GoveeBleInvalidValue) as err:
                raised = err
            except GoveeBleError:
                pass   # a read the row needs went unanswered here: the library refuses to guess
            if step.refusal is not None and (not isinstance(raised, step.refusal) or len(sent) != before):
                bad.append(f"{sku} {step.name}: {raised!r} after {len(sent) - before} frame(s)")
            elif step.refusal is None and raised is not None:
                bad.append(f"{sku} {step.name}: {raised!r}")
            if step.name.startswith("segment_brightness_") and step.refusal is None and raised is None:
                dimmed = {s.index for s in dev.state.segments if s.brightness == generic.SEGMENT_LEVEL}
                if not dimmed <= set(step.touched):
                    bad.append(f"{sku} {step.name}: dims {sorted(dimmed)}, judges {step.touched} alone")
    assert len(routes) > 100 and not bad, bad[:20]
    kelvin = ft.Step("color_temp_5500", generic._nothing, None, "", fields=("color_temp_kelvin", "rgb_color"))
    was, want = (SimpleNamespace(rgb_color=None, color_temp_kelvin=k) for k in (2000, 5500))
    blind = {"rgb_color": None, "color_temp_kelvin": "the mode reply carries no Kelvin"}   # a _nocct decode
    tint = SimpleNamespace(rgb_color=(255, 238, 222), color_temp_kelvin=None)
    assert readback.judge_fields(kelvin, None, was, want, tint, blind) == (
        {"color_temp_kelvin": "not read: the mode reply carries no Kelvin",
         "rgb_color": f"not read: {readback.CCT_BLIND}"}, [])
    both = dict.fromkeys(("rgb_color", "color_temp_kelvin"))   # the library read the Kelvin (a newer fold)
    assert readback.judge_fields(kelvin, None, was, want, want, both) == (
        {"color_temp_kelvin": "pass", "rgb_color": f"not read: {readback.UNCHANGED}"}, [])
    # a colour the decode table says a 0x15 mode report can't carry counts where the poll set it to a value
    # (a newer library's fold), and so does the "no RGB" a Kelvin above 0 brings; not a None it set
    polled = SimpleNamespace(readback="polled", mode_color_parse="rgb_kelvin", bar_switch=False,
                             gradual=False, color_readback="mechanism_a_direct", brightness_by_pact=(),
                             detail_power_read=False)
    mode_15 = [readback.Frame(0.0, "rx", "aa05", 0x15), readback.Frame(0.0, "rx", "aaa5")]
    for rgb, kelvin_read, counted in (((1, 2, 3), None, True), (None, 5500, True), (None, None, False)):
        fake = SimpleNamespace(profile=polled, pact=None,
                               state=SimpleNamespace(rgb_color=rgb, color_temp_kelvin=kelvin_read))
        got = readback.carried(fake, mode_15, assigned={"rgb_color", "color_temp_kelvin"})
        assert (got["rgb_color"] is None) is counted, (rgb, kelvin_read)
    # and segments assigned by a poll that set none of them (a burst merged over what was held) read nothing
    assert readback.carried(fake, mode_15, assigned={"segments"}, segments=frozenset())["segments"] == (
        readback.NOT_SET)
    # segments: a touched one already as intended proves nothing, nor does a whole-device colour every segment
    # already showed; only the segments the poll set are judged (a burst that lost a colour group keeps the
    # others as they were, a write's intent among them)
    red, blue = (255, 0, 0), (0, 0, 255)
    segs = SimpleNamespace(segments=[Segment(0, red, 100), Segment(1, blue, 100)], rgb_color=blue)
    seg_step = ft.Step("segment_rgb_0", generic._nothing, None, "", fields=("segments",), touched=(0,))
    rgb_step = ft.Step("rgb_blue", generic._nothing, None, "", fields=("rgb_color",))
    carries = {"segments": None, "rgb_color": "the mode reply carries no RGB"}

    def unset(i: int) -> str:
        return f"not read: segments [{i}] not set by this poll's replies (no reply carried their group)"

    for step, field, read, verdict in ((seg_step, "segments", None, f"not read: {readback.UNCHANGED}"),
                                       (seg_step, "segments", {1}, unset(0)),
                                       (rgb_step, "rgb_color", None, "fail"),
                                       (rgb_step, "rgb_color", {1}, unset(0))):
        assert readback.judge_fields(step, None, segs, segs, segs, carries, read)[0] == {field: verdict}
    all_blue = SimpleNamespace(segments=[Segment(0, blue, 100), Segment(1, blue, 100)], rgb_color=blue)
    assert readback.judge_fields(rgb_step, None, all_blue, all_blue, all_blue, carries)[0] == {
        "rgb_color": f"not read: {readback.UNCHANGED}"}
    # in a scene its segments' colours are the scene's: a restore check compares their levels only, and only
    # the segments its poll set; a plug's relay is put back unless the restore's own poll read it as it
    # started, a segment level unless that poll set it as it started; a gradual flag nobody read is noted
    from govee_ble_local.models import DeviceState

    runner = importlib.import_module("field_test.runner")
    watch = readback.FrameWatch("AA:BB:CC:DD:EE:01")
    relay: list[bool] = []

    async def answered_by_nobody() -> None:   # a poll answered by nobody: it sets nothing
        return None

    async def power(*args: Any) -> None:
        relay.append(args[-1])
    painted = SimpleNamespace(profile=polled, pact=None, state=DeviceState(scene_code=5))

    async def scene_poll() -> Any:   # sets segment 0 alone, the others kept as they were held
        watch.frames.extend((readback.Frame(0.0, "rx", "aa05", readback.SCENE_SUB), mode_15[1]))
        held = [g for g in painted.state.segments if g.index != 0]
        painted.state.scene_code, painted.state.segments = 5, [Segment(0, (9, 9, 9), 100), *held]
        return painted.state

    painted.update = scene_poll
    in_scene = restore.StartState(DeviceState(scene_code=5, segments=[Segment(0, red, 100)]),
                                  frozenset({"scene_code", "segments"}))
    assert (await restore.check_restore(painted, in_scene, restore.Outcome(), watch)).result == "exact"
    painted.state = DeviceState(segments=[Segment(1, (0, 255, 0), 100)])   # segment 1: a write's intent
    two = restore.StartState(DeviceState(segments=[Segment(0, (9, 9, 9), 100), Segment(1, blue, 100)]),
                             frozenset({"segments"}))
    assert (await restore.check_restore(painted, two, restore.Outcome(), watch)).result == "exact"
    painted.capabilities, painted.supports_segment_brightness = ("segments",), True
    painted.set_segment_brightness = power
    levels = restore.StartState(DeviceState(segments=[Segment(1, blue, 60)]), frozenset({"segments"}))
    painted.state = DeviceState(segments=[Segment(1, blue, 60)])
    assert [a[2] for a in restore._segment_levels(painted, levels, frozenset())] == [([1], 60)]
    assert restore._segment_levels(painted, levels, frozenset({1})) == []
    flag = SimpleNamespace(profile=SimpleNamespace(readback="polled", gradual=True), capabilities=(),
                           zones=(), state=DeviceState(), update=answered_by_nobody)
    unread = restore.StartState(DeviceState(), frozenset())
    noted = (await restore.restore_start(flag, unread, str)).unrestorable
    gradual = "gradual: unknown at the start (not read back)"
    assert gradual in noted and restore.unrestorable_written(noted, {"gradual"}) == [gradual]
    lamp = SimpleNamespace(profile=SimpleNamespace(readback="plug"), capabilities=(), set_power=power,
                           state=DeviceState(is_on=True), update=answered_by_nobody)
    was_on = restore.StartState(DeviceState(is_on=True), frozenset({"is_on"}), "plug")
    assert (await restore.restore_start(lamp, was_on, str)).actions == ["relay on"] and relay == [True]
    # a row with the app's empty unlock check needs no secret once that check was sent and acked; a step with
    # no field (a hand-written plan's) may change any colour or brightness
    acked = readback.FrameWatch("AA:BB:CC:DD:EE:01")
    acked.frames += [readback.Frame(0.0, "tx", "33b2"), readback.Frame(0.0, "rx", "33b2", 0)]
    empty, needs = (SimpleNamespace(requires_secret=True, empty_secret_check=e) for e in (True, False))
    assert [runner._no_secret(p, s, w) for p, s, w in (
        (empty, None, acked), (empty, None, watch), (needs, None, acked), (needs, b"\x01" * 8, watch))] == [
        False, True, True, False]
    assert runner._changes(ft.Step("hand", generic._nothing, None, "")) == ["brightness", "colour"]
    # only the second counted Ctrl-C abandons a restore; after a SIGHUP or SIGTERM no uncounted cancellation
    # does either (a write's ack timeout firing with it); with neither, any cancellation after the first does
    first = SimpleNamespace(interrupted=True)
    try:
        for console, abandons in ((runner._Console(stopped_by="SIGTERM"), False),
                                  (runner._Console(sigints=1), False), (runner._Console(sigints=2), True),
                                  (runner._Console(), True)):
            runner._CONSOLE = console
            assert runner._second_ctrl_c(first, None) is abandons, console
    finally:
        runner._CONSOLE = runner._Console()
    # a poll whose burst merged one colour group over the segments held set only that group's segments
    kept = Segment(0, red, 100)
    merged = SimpleNamespace(state=DeviceState(segments=[kept]))

    async def merge() -> Any:
        merged.state.segments = [kept, Segment(1, blue, 100)]
        return merged.state

    merged.update = merge
    assert (await readback.poll(merged)).segments == {1}
    # the H6047's own plan toggles the gradual flag only from a start the first poll read
    h6047 = Device(profile_for("H6047"), ble_device("AA:BB:CC:DD:EE:47", "ihoment_H6047_1A2B"), None,
                   sku="H6047")
    for flag, skipped in ((None, True), (False, False)):
        st = h6047.snapshot()
        st.gradual = flag
        ctx = ft.PlanContext(h6047, "H6047", {}, start=restore.StartState(st, frozenset()))
        skips = {s.name: bool(s.skip) for s in ft.PLANS["H6047"].build_steps(ctx)}
        assert (skips["gradual_on"], skips["gradual_off"]) == (skipped, skipped)


def test_all_and_list_modes_guard_writes_and_plugs_and_restore_after_an_interrupt(
    ft: Any, runner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """--list connects nothing and lists the plug by its ref; --all started at 05:00 runs read steps only
    (the H6047 plan's segment read-back too), even once the window opens during the run, writes no relay of a
    plug that is not allow-listed, and connects to no H14A1, whose every connect sends 33 25 (an effect stop):
    --list and the summary mark it skipped, as does a read-only run by day, and the run exits 4 (incomplete).
    Inside the window it connects as normal; when the window closes during its turn, it gets no connect after
    (in its re-scan) or no poll after (once connected). A plug tested without its secret (its unlock never
    sent) keeps the gate incomplete (exit 4) on its own; an allow-listed one toggles only once its ref is
    typed. --force-hours lifts the window. A Ctrl-C (the main
    task cancelled, as asyncio.run does) as the H6047's restore begins, with the terminal gone (every write to
    it fails), lets that restore finish and marks the devices after it not run; a SIGHUP after the first
    write went out, before its ack, still restores the H6047 and leaves no raw capture. With no terminal on
    stdin, --all writes nothing visible unless --unattended. Modelled and unsupported devices are listed only.
    A write step nothing verified keeps the gate incomplete (exit 4). A capture a killed run left raw is
    redacted at the next start; GOVEE_FIELD_TEST_NOW pins the clock under pytest only. A Ctrl-C and then a
    SIGHUP as the restore is checked abandon nothing; a tab closed at the start question, or at "go on with
    the next device?" after a NOT RESTORED, stops the run with the rest not run (interrupted). Every file the
    runs write is masked."""
    from fake_ble import FakeGovee, FirmwareGovee, ble_device, frame, virtual_time
    from test_h6047_field_test import _Sim

    from govee_ble_local import profile_for, supported_skus
    from govee_ble_local.transport import connection as conn_mod

    survey = importlib.import_module("field_test.survey")
    guard = importlib.import_module("field_test.guard")
    monkeypatch.setattr(runner, "_CONSOLE", runner._Console())
    real_now = guard.now
    sim = _Sim()
    sim.bars, sim.gradual = [True, False], True                 # start: left bar on, right bar off
    sim.rgb.update({i: (9, 8, 7) for i in range(5, 10)})
    start = (sim.on, list(sim.bars), sim.pct, sim.gradual, dict(sim.rgb), dict(sim.kelvin))
    light, plug, bulb = FakeGovee(encrypted=False), FirmwareGovee(), FakeGovee(encrypted=False)

    def answer(client: Any, plain: bytes) -> None:   # the H6047 simulator behind a real connection
        if plain[0] == 0x33:
            sim.write(plain)
            client.push(frame(0x33, plain[1], 0), 0.005)
        elif plain[0] == 0xAA:
            for reply in sim.read(plain) or [frame(0xAA, plain[1], 0x01)]:
                client.push(reply, 0.005)

    light.on_write = answer
    peers: dict[str, Any] = {"AA:BB:CC:DD:EE:47": light, "AA:BB:CC:DD:EE:83": plug, "AA:BB:CC:DD:EE:14": bulb}

    async def establish(cls: Any, device: Any, name: str, **kw: Any) -> Any:
        return await peers[device.address].establish(cls, device, name, **kw)

    monkeypatch.setattr(conn_mod, "establish_connection", establish)
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.1)   # the unpaired plug never acks its time sync
    modelled = next(s for s in supported_skus(allow_modelled=True)
                    if profile_for(s) is None and profile_for(s, allow_modelled=True) is not None)

    def heard(address: str, adv: Any) -> Any:
        entry = survey.Heard(address, ble_device(address, adv.local_name), adv, adv.local_name, adv.rssi,
                             seen_at=float("inf"))   # just heard: never scanned for again
        return survey.describe(entry)

    entries = [
        heard("AA:BB:CC:DD:EE:47", SimpleNamespace(local_name="ihoment_H6047_1A2B", rssi=-50,
                                                   manufacturer_data={0x8802: bytes([0xEC, 0, 1, 1, 1])})),
        heard("AA:BB:CC:DD:EE:83", plug.advertisement()),
        heard("AA:BB:CC:DD:EE:01", SimpleNamespace(local_name=f"ihoment_{modelled}_1A2B",
                                                   manufacturer_data={}, rssi=-70)),
        heard("AA:BB:CC:DD:EE:02", SimpleNamespace(local_name="ihoment_H9999_1A2B", manufacturer_data={},
                                                   rssi=-80)),
        heard("AA:BB:CC:DD:EE:14", SimpleNamespace(local_name="ihoment_H14A1_1A2B", manufacturer_data={},
                                                   rssi=-60)),
    ]
    assert [e.tier for e in entries] == ["default", "default", "modelled", "unsupported", "default"]
    assert [e.connect_visible for e in entries] == [None, None, None, None, "33 25 (effect stop)"]
    no_effect_stop = "skipped: its connect sends 33 25 (effect stop), a visible change, outside the window"

    async def fake_survey(_seconds: float, **_k: Any) -> list[Any]:
        return list(entries)

    monkeypatch.setattr(survey, "survey", fake_survey)

    main: dict[str, Any] = {}

    async def on_virtual_time(args: Any, out: Path) -> int:
        main["task"] = asyncio.current_task()   # what asyncio.run cancels on a Ctrl-C
        if args.list:
            return int(await runner.list_devices(args, out, "T"))
        with virtual_time():
            return int(await runner.run_all(args, out, "T", runner._window(args), {"version": "test"}))

    def run_all(*argv: str, signals: bool = False) -> tuple[int, dict[str, Any], Path]:
        out = tmp_path / str(len(list(tmp_path.iterdir())))
        out.mkdir()
        args = runner.parse_args([*argv, "--settle", "0", "--out", str(out)])
        run = on_virtual_time(args, out)
        rc = runner._main_with_signals(run) if signals else asyncio.run(run)
        summary = out / "fieldtest-summary-T.json"
        return rc, json.loads(summary.read_text()) if summary.exists() else {}, out

    # --list at 05:00: every device, its tier, and the plug by its ref and outlets; no connection, any hour;
    # the H14A1 marked as --all would leave it now
    clock = {"now": datetime.datetime(2026, 10, 23, 5, 0)}
    monkeypatch.setattr(guard, "now", lambda: clock["now"])
    with monkeypatch.context() as m:   # outside the suite an exported pin is ignored
        m.delenv("PYTEST_CURRENT_TEST")
        assert guard.pinned_clock_ignored() and real_now() != datetime.datetime(2026, 10, 23, 12, 0)
    listed = tmp_path / "listed"
    listed.mkdir()
    left = listed / ".h6047-01-frames-X.jsonl.raw"   # what a killed run leaves: redacted at the next start
    left.write_text(json.dumps({"ts": 1, "addr": "AA:BB:CC:DD:EE:47", "dir": "tx", "plain": "3301",
                                "wire": "e701ab", "enc": "none"}) + "\n")
    rc = runner.main(["--list", "--out", str(listed)])
    shown = capsys.readouterr().out
    assert rc == 0 and light.establish_calls == plug.establish_calls == bulb.establish_calls == 0
    assert f"{entries[1].ref} H5083 ihoment_H5083_xxxx AA:BB:CC:xx:xx:xx 1 outlet" in shown
    assert f"ihoment_H14A1_xxxx AA:BB:CC:xx:xx:xx  ({no_effect_stop})" in shown
    rows = json.loads(next(listed.glob("fieldtest-list-*.json")).read_text())["heard"]
    assert [(h["tier"], h["skip"]) for h in rows] == [
        ("default", None), ("default", None), ("modelled", None), ("unsupported", None),
        ("default", no_effect_stop)]
    assert not left.exists() and json.loads((listed / "h6047-01-frames-X.jsonl").read_text())["addr"] == (
        "AA:BB:CC:xx:xx:xx")

    # 05:00: read steps only, though the window opens before the first device's turn (no --yes); nothing
    # visible is written, the plug's relay least of all, and the H14A1 is not connected to
    real_device = runner.run_device

    async def device_at_0730(*a: Any, **k: Any) -> Any:
        clock["now"] = datetime.datetime(2026, 10, 23, 7, 30)
        return await real_device(*a, **k)

    monkeypatch.setattr(runner, "run_device", device_at_0730)
    rc, night, _ = run_all("--all", "--no-prompt")
    monkeypatch.setattr(runner, "run_device", real_device)
    assert rc == 4 and night["gate"] == "incomplete"
    assert [d["sku"] for d in night["devices"]] == ["H6047", "H14A1", "H5083"]   # the others: listed only
    steps = [s for d in night["devices"] for s in d["steps"]]
    assert all(s["kind"] == "read" or s["verdict"] == "skipped" for s in steps)
    assert [steps[0][k] for k in ("step", "kind", "verdict")] == ["segment_readback_count", "read", "pass"]
    assert night["devices"][1]["result"] == no_effect_stop and not night["devices"][1]["steps"]
    assert bulb.establish_calls == 0 and night["totals"]["skipped"] == 1
    assert {s["step"]: s["verdict"] for s in night["devices"][2]["steps"]} == {
        "session_setup": "unverifiable", "relay_read": "pass"}            # no secret: no unlock sent
    assert night["devices"][2]["no_secret"] and night["totals"]["no_secret"] == 1
    assert (sim.on, sim.bars, sim.pct, sim.gradual) == start[:4]
    assert "write_tx power" not in plug.sim.requests()
    assert night["devices"][0]["restore"]["result"] == "not_needed"

    # 07:30: a read-only run still leaves the H14A1 alone; one that may write connects to it as normal
    rc, ro, _ = run_all("--all", "--read-only", "--no-prompt", "--only", entries[4].ref)
    assert rc == 4 and ro["devices"][0]["result"] == no_effect_stop.replace("outside the window",
                                                                            "on a read-only run")
    assert bulb.establish_calls == 0
    with monkeypatch.context() as m:   # no terminal on stdin: nothing visible without --unattended
        m.setattr(runner, "_on_a_terminal", lambda: False)
        rc, alone, _ = run_all("--all", "--no-prompt", "--yes", "--only", entries[4].ref)
        assert rc == 4 and alone["devices"][0]["result"] == no_effect_stop.replace("outside the window",
                                                                                   "with no terminal")
        assert bulb.establish_calls == 0 and alone["options"]["unattended"] is False
        rc, lit, lit_out = run_all("--all", "--no-prompt", "--yes", "--unattended", "--only", entries[4].ref)
    assert lit["devices"][0]["result"] == "completed" and bulb.establish_calls >= 1
    assert lit["options"]["unattended"] is True
    assert any(f[:3] == b"\x33\x25\x01" for f in bulb.received)
    rc, lone, _ = run_all("--all", "--no-prompt", "--only", entries[1].ref)   # the plug alone, no secret
    assert rc == 4 and lone["gate"] == "incomplete" and lone["devices"][0]["result"] == "completed"

    # an allow-listed plug is shown by its name as its label shows it, and its signal (on the console only),
    # and toggles only once its own ref is typed: another answer keeps it read-only
    async def tester(prompt: str, _reader: Any = None) -> str:
        return "a desk lamp" if "plugged" in prompt else "nope" if "Type its ref" in prompt else "s"

    capsys.readouterr()
    with monkeypatch.context() as m:
        m.setattr(runner, "_readline", tester)
        rc, typed, _ = run_all("--all", "--only", entries[1].ref, "--toggle-plugs", entries[1].ref)
    shown = capsys.readouterr().out
    assert f"{entries[1].ref} H5083 {entries[1].name} (rssi {entries[1].rssi} dBm)" in shown
    assert f"{entries[1].ref}: read-only" in shown and typed["options"]["toggle_plugs"] == 0
    assert "write_tx power" not in plug.sim.requests()

    # the tab closes at the start question: on a terminal its prompt sees EOF, so nobody can answer; nothing
    # is started, and the summary lists every device as not run (interrupted), exit 130
    def hung_up(_prompt: str = "") -> str:
        raise EOFError

    calls = light.establish_calls
    with monkeypatch.context() as m:
        m.setattr(runner, "_on_a_terminal", lambda: True)
        m.setattr("builtins.input", hung_up)
        rc, closed, _ = run_all("--all", "--only", entries[0].ref)
    assert rc == 130 and runner._CONSOLE.deaf and light.establish_calls == calls
    assert [d["result"] for d in closed["devices"]] == ["not run (interrupted)"]
    monkeypatch.setattr(runner, "_CONSOLE", runner._Console())

    # the window closes during the H14A1's turn: in its re-scan (21:59:50 to 22:00:30), so its connect is
    # not made; or once it is connected, so nothing polls it after (a dropped link would reconnect)
    def at_2200(real: Any) -> Any:
        async def late(*a: Any, **k: Any) -> Any:
            clock["now"] = datetime.datetime(2026, 10, 23, 22, 0, 30)
            return await real(*a, **k)
        return late

    def effect_stops() -> int:
        return sum(f[:3] == b"\x33\x25\x01" for f in bulb.received)

    for module, name, connects, result in ((survey, "acquire", 0, no_effect_stop),
                                           (runner, "capture_start", 1, "completed")):
        clock["now"] = datetime.datetime(2026, 10, 23, 21, 59, 50)
        calls, stops, real = bulb.establish_calls, effect_stops(), getattr(module, name)
        monkeypatch.setattr(module, name, at_2200(real))
        rc, edge, edge_out = run_all("--all", "--no-prompt", "--yes", "--unattended", "--only",
                                     entries[4].ref)
        monkeypatch.setattr(module, name, real)
        report = json.loads((edge_out / edge["devices"][0]["report"]).read_text())
        assert rc == 4 and edge["devices"][0]["result"] == result and "final_state" not in report
        assert (bulb.establish_calls - calls, effect_stops() - stops) == (connects, connects)
    assert edge["devices"][0]["steps"][0] == {
        "step": "readback_baseline", "kind": "read", "verdict": "skipped", "basis": None,
        "reason": no_effect_stop.removeprefix("skipped: ")}

    # --force-hours lifts the window; a Ctrl-C as the H6047's restore begins, the terminal gone with it (every
    # write to it fails, the "restoring" line first): it is restored all the same, and the run stops after it
    real_restore = runner.restore_start
    gone = {"tty": False}

    class Tty(io.StringIO):
        def write(self, s: str) -> int:
            if gone["tty"]:
                raise OSError(errno.EIO, "Input/output error")
            return len(s)

    def ctrl_c_then_restore(*a: Any, **k: Any) -> Any:
        main["task"].cancel()
        gone["tty"] = True
        return real_restore(*a, **k)

    monkeypatch.setattr(runner, "restore_start", ctrl_c_then_restore)
    with monkeypatch.context() as m:
        m.setattr(sys, "stdout", Tty())
        rc, day, out = run_all("--all", "--generic", "--force-hours", "--no-prompt", "--yes", "--unattended")
    monkeypatch.setattr(runner, "restore_start", real_restore)
    assert runner._CONSOLE.dead
    monkeypatch.setattr(runner, "_CONSOLE", runner._Console())
    assert rc == 130 and day["gate"] == "fail" and day["window"]["forced"] is True
    bars, *rest = day["devices"]
    assert bars["result"] == "completed" and bars["interrupted"]
    assert [r["result"] for r in rest] == ["not run (interrupted)"] * 2
    verdicts = {s["step"]: s["verdict"] for s in bars["steps"]}
    assert verdicts.pop("segment_brightness_0_9") == "fail"   # the simulator ignores 33 05 15 02
    assert [verdicts.pop(k) for k in verdicts.copy() if k.startswith("scene_")] == ["fail"]   # and scenes
    assert verdicts.pop("segment_color_temp_refused") == "pass" and set(verdicts.values()) == {"pass"}
    assert {"power_off", "color_temp_4400", "segment_rgb_0_9", "zone_right_off",
            "zone_left_color_temp_2200", "gradual_back"} <= set(verdicts)
    assert bars["restore"]["result"] == "exact"
    assert (sim.on, sim.bars, sim.pct, sim.gradual, sim.rgb, sim.kelvin) == start
    assert set(day) >= {"tool_version", "route", "library", "window", "options", "heard", "devices", "totals"}

    # the terminal closes (SIGHUP) once the first write (power off) reached the light, before its ack: the
    # H6047 is restored as on a Ctrl-C, and its capture is redacted into its own name (no raw file left); a
    # Ctrl-C there instead, and the tab closed as its restore is checked: the hang-up cuts nothing short
    def hang_up_before_the_ack(client: Any, plain: bytes) -> None:
        if plain[:3] != b"\x33\x01\x00" or main.get("cut"):
            return answer(client, plain)
        main["cut"] = True
        sim.write(plain)
        if not main["ctrl_c"]:
            return os.kill(os.getpid(), signal.SIGHUP)
        ctrl_c = signal.getsignal(signal.SIGINT)   # what the terminal's Ctrl-C runs: asyncio.run's, counted
        assert callable(ctrl_c)
        ctrl_c(signal.SIGINT, None)

    real_check = runner.check_restore

    def hang_up_in_the_check(*a: Any, **k: Any) -> Any:
        if main["ctrl_c"]:
            os.kill(os.getpid(), signal.SIGHUP)
        return real_check(*a, **k)

    light.on_write = hang_up_before_the_ack
    monkeypatch.setattr(runner, "check_restore", hang_up_in_the_check)
    hup = signal.signal(signal.SIGHUP, signal.SIG_IGN)   # never the suite's death, if no handler took it
    for ctrl_c in (False, True):
        main.update(cut=False, ctrl_c=ctrl_c)
        try:
            rc, cut, cut_out = run_all("--all", "--generic", "--force-hours", "--no-prompt", "--yes",
                                       "--unattended", "--only", entries[0].ref, signals=True)
        finally:
            signal.signal(signal.SIGHUP, hup if ctrl_c else signal.SIG_IGN)
        assert rc == 130 and main["cut"] and cut["devices"][0]["result"] == "interrupted"
        assert (runner._CONSOLE.stopped_by, runner._CONSOLE.sigints) == ("SIGHUP", int(ctrl_c))
        assert cut["devices"][0]["restore"]["result"] == "exact" and not cut["devices"][0]["not_restored"]
        assert (sim.on, sim.bars, sim.pct, sim.gradual, sim.rgb, sim.kelvin) == start
        assert sorted(p.name for p in cut_out.iterdir() if "frames" in p.name) == ["h6047-01-frames-T.jsonl"]
        monkeypatch.setattr(runner, "_CONSOLE", runner._Console())
    monkeypatch.setattr(runner, "check_restore", real_check)

    # a restore the light ignores at first (a dropout) is tried again and ends exact; one it ignores for good
    # is NOT RESTORED after its retries, and the run (no prompts) stops there; under the H6047's own plan the
    # generic restore follows the plan's
    def ignore_the_restored_brightness(client: Any, plain: bytes) -> None:
        if main["restoring"] and plain[:2] == b"\x33\x04" and main["ignore"]:
            main["ignore"] -= 1
            return client.push(frame(0x33, 0x04, 0), 0.005)   # acked, not applied
        return answer(client, plain)

    def restoring(*a: Any, **k: Any) -> Any:
        main["restoring"] = True
        return real_restore(*a, **k)

    light.on_write = ignore_the_restored_brightness
    monkeypatch.setattr(runner, "restore_start", restoring)
    capsys.readouterr()
    for plan, ignore, retries, left in (("--generic", 1, 1, []),
                                        ("--generic", 3, 2, ["brightness: start 50, now 100"]),
                                        ("--force-hours", 0, 0, [])):
        main.update(restoring=False, ignore=ignore)
        rc, again, again_out = run_all("--all", plan, "--force-hours", "--no-prompt", "--yes", "--unattended",
                                       "--only", f"{entries[0].ref},{entries[4].ref}")
        bars = again["devices"][0]
        assert (bars["restore"]["retries"], bars["not_restored"]) == (retries, left), (plan, ignore)
        stopped = f"not run (stopped: {entries[0].ref} was not restored)"
        assert (again["devices"][1]["result"] == stopped) is bool(left)
        sim.pct = start[2]
    shown = capsys.readouterr().out
    assert f"NOT RESTORED: {entries[0].ref} H6047 (light): brightness: start 50, now 100" in shown
    actions = json.loads((again_out / bars["report"]).read_text())["restore_check"]["actions"]
    assert actions[0] == "the plan's restore" and "power on" in actions
    assert bars["plan"] == "hand" and bars["counts"]["fail"] == 0 and bars["restore"]["result"] == "exact"
    assert (sim.on, sim.bars, sim.pct, sim.gradual, sim.rgb, sim.kelvin) == start
    # with prompts, the tab closed at "go on with the next device?" after one: its prompt sees EOF on the
    # terminal, so the rest are not run (interrupted), exit 130
    main.update(restoring=False, ignore=3)
    with monkeypatch.context() as m:
        m.setattr(runner, "_on_a_terminal", lambda: True)
        m.setattr("builtins.input", hung_up)
        rc, lost, _ = run_all("--all", "--generic", "--force-hours", "--yes", "--only",
                              f"{entries[0].ref},{entries[4].ref}")
    assert rc == 130 and lost["devices"][0]["not_restored"]
    assert lost["devices"][1]["result"] == "not run (interrupted)"
    sim.pct = start[2]
    monkeypatch.setattr(runner, "_CONSOLE", runner._Console())
    monkeypatch.setattr(runner, "restore_start", real_restore)

    # the gate: a write step nothing verified (no read-back carried it, no answer) leaves it incomplete, as
    # does a close restore of something the run wrote and could never put back; an unverified one fails
    summary = importlib.import_module("field_test.summary")
    restore = importlib.import_module("field_test.restore")
    ok = {"result": "completed", "counts": {"pass": 1}, "steps": [{"kind": "write", "verdict": "pass"}]}
    unverified = {**ok, "steps": [{"kind": "write", "verdict": "unverifiable"}]}
    failed = {**ok, "counts": {"pass": 0, "fail": 1}}
    lost = restore.unrestorable_written(["colour: not read", "brightness: unknown", "relay: unknown"],
                                        {"colour", "power"})
    assert lost == ["colour: not read", "relay: unknown"]
    close, blind = ({**ok, "restore": {"result": r, "unrestorable_written": lost}}
                    for r in ("close", "unverified"))
    kept = {**ok, "restore": {"result": "close", "unrestorable_written": []}}
    cases = (ok, unverified, failed, close, blind, kept)
    assert [summary.gate_verdict({"devices": [d]}) for d in cases] == [
        "pass", "incomplete", "fail", "incomplete", "fail", "pass"]
    assert [runner._exit_code({"devices": [d]}, False) for d in (ok, unverified, failed)] == [0, 4, 1]
    # a restore a second Ctrl-C abandoned after the run wrote is NOT RESTORED, with what the writes changed
    abandoned = restore.RestoreCheck("abandoned")
    assert runner.not_restored(abandoned, {"power", "brightness"}, True) == [
        "restore abandoned (a second Ctrl-C): what the run changed (brightness, power) is left as the run "
        "made it"]
    assert runner.not_restored(abandoned, (), False) == []
    for path in (*out.iterdir(), *listed.iterdir(), *lit_out.iterdir(), *cut_out.iterdir()):
        text = path.read_text()
        assert "DD:EE" not in text and "1A2B" not in text, path.name
        assert '"wire": "' not in text, path.name
