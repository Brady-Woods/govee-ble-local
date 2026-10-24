"""The field-test runner: scan, pick each device's plan, run it over one connection, restore, report.

Nothing in here is specific to a device; per-SKU behaviour lives in ``plans/<sku>.py`` (see ``plan.py``),
and every other supported SKU gets the generic plan built from its profile (``generic.py``). Modes:

    python tools/field_test                 one device: detect it (or --sku / --address) and run its plan
    python tools/field_test --list          scan only: every Govee device heard, its tier, the plugs
    python tools/field_test --all           the release-gate pass: every supported device, one at a time

Each device run walks its plan's steps over ONE BLE connection, reads state back after each step, judges
the step (pass / fail / unverifiable, ``readback.py``), may ask the tester, restores the start state
(also on an error or Ctrl-C) and writes, next to the run's summary:

    <sku>[-NN]-report-<time>.json   steps, read-backs, verdicts, restore, environment
    <sku>[-NN]-frames-<time>.jsonl  BLE frame capture (both directions, plaintext only)
    fieldtest-summary-<time>.json   every device heard, and per device tested its verdicts

All are safe to attach to a GitHub issue (see ``masking.py``). Visible writes run only inside the
daytime window (``guard.py``), and a device whose every connect makes a visible write (its profile's connect
writes: the bulbs' ``33 25`` effect stop) is not connected to outside it or on a read-only run; relay
toggles only on allow-listed, confirmed plugs.

No module of this package imports the library when it loads: :func:`main` chooses it first
(``--library``: this checkout's ``src`` once its classes are generated, or the installed wheel).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import dataclasses
import getpass
import json
import os
import platform
import stat
import sys
import threading
import time
from collections.abc import Callable, Collection, Coroutine
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar

from . import survey as _survey
from .generic import device_kind, generic_plan, generic_steps
from .guard import Window, connect_skip, parse_hhmm, visible_connect
from .masking import (
    describe_error,
    json_default,
    mask_address,
    mask_name,
    mask_text,
    mask_text_all,
    name_without_mac_tail,
    redact_frames,
    snapshot,
    write_text_atomic,
)
from .plan import PLANS, PlanContext, SkuPlan, Snapshot, Step
from .readback import FrameWatch, Judgement, carried, judge, judge_fields, outlet_mask
from .restore import Outcome, RestoreCheck, StartState, capture_start, check_restore, restore_start
from .summary import Summary, console_table, counts, gate_verdict

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData

    from govee_ble_local import Device

_REPO = Path(__file__).resolve().parent.parent.parent
_REPO_SRC = _REPO / "src"

TOOL_VERSION = 3
SCAN_SECONDS = 15.0
FRAME_LOG_FLUSH_SECONDS = 2.0
CONNECT_BACKOFF = (2.0, 5.0, 10.0)
CARD_RESCAN_SECONDS = 4.0
KIND_ORDER = {"light": 0, "card": 1, "plug": 2}
EXIT_OK, EXIT_FAIL, EXIT_NOTHING, EXIT_REFUSED, EXIT_INCOMPLETE, EXIT_INTERRUPTED = 0, 1, 2, 3, 4, 130
SURVEY_SECONDS = 20.0   # --list / --all
# What single-device discovery scans with, set by run() for its own call only (--scan-seconds, --generic).
DISCOVER_OPTIONS: dict[str, Any] = {}

Names: TypeAlias = tuple[str | None, ...]   # the device's advertised names (local name, BLEDevice.name)
T = TypeVar("T")


class Refused(Exception):
    """The run is refused before it touches a device (exit 3)."""


# -- the library under test ---------------------------------------------------------------------


def _select_library(mode: str) -> None:
    """Put the library to test on ``sys.path``: ``checkout`` (this checkout's ``src``; its Kaitai classes
    must be generated), ``installed`` (nothing inserted: the installed wheel), ``auto`` (the checkout once
    generated, else the installed wheel)."""
    generated = (_REPO_SRC / "govee_ble_local" / "_generated" / "govee_ble_frame.py").is_file()
    if mode == "checkout" and not generated:
        raise Refused("--library checkout: generate the Kaitai classes first (bash tools/gen_kaitai.sh)")
    if (mode == "checkout" or (mode == "auto" and generated)) and str(_REPO_SRC) not in sys.path:
        sys.path.insert(0, str(_REPO_SRC))


def _direct_url() -> dict[str, Any]:
    from importlib import metadata

    try:
        text = metadata.distribution("govee-ble-local").read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return {}
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _tree_version(root: Path) -> str | None:
    """The ``[project] version`` of the source tree at ``root``, or None."""
    import tomllib

    try:
        with (root / "pyproject.toml").open("rb") as fh:
            return str(tomllib.load(fh)["project"]["version"])
    except (OSError, KeyError, ValueError):
        return None


def library_info(mode: str = "auto") -> dict[str, Any]:
    """Which library this run tests: ``{"version", "source", "wheel"}`` (no path). A package imported from a
    source tree (``<tree>/src/govee_ble_local``: this checkout, another one on ``sys.path``, or an editable
    install) takes that tree's version, since the installed metadata may be another's. ``installed``
    refuses a source tree, which would pass for the wheel."""
    try:
        import govee_ble_local as g
    except ImportError as err:
        raise Refused(f"govee_ble_local is not importable ({err}): generate this checkout's classes "
                      "(bash tools/gen_kaitai.sh) or install the wheel") from None
    where = Path(g.__file__).resolve().parent
    tree = where.parent.parent if where.parent.name == "src" else None
    tree_version = _tree_version(tree) if tree is not None else None
    direct = _direct_url()
    editable = bool((direct.get("dir_info") or {}).get("editable"))
    if mode == "installed" and (tree_version is not None or editable):
        raise Refused("--library installed, but govee_ble_local comes from a source tree (this checkout, "
                      "another one on sys.path, or an editable install): install the wheel in a clean venv")
    if tree_version is not None:
        source = "checkout" if tree == _REPO.resolve() else "source tree"
        return {"version": tree_version, "source": source, "wheel": None}
    url = str(direct.get("url") or "")
    wheel = url.rsplit("/", 1)[-1] if url.endswith(".whl") else None
    return {"version": g.__version__, "source": "editable" if editable else "installed", "wheel": wheel}


# -- prompts ------------------------------------------------------------------------------------


async def _readline(prompt: str, reader: Callable[[str], str] | None = None) -> str:
    """``reader(prompt)`` (``input``, or ``getpass`` for a secret) on a daemon thread. ``asyncio.to_thread``
    would run it in the default executor, which ``asyncio.run`` joins on the way out: after a Ctrl-C at a
    prompt the tool would hang, its summary printed, until the tester pressed Enter. Nothing joins a
    daemon thread."""
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
            line = (reader or input)(prompt)
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


async def _ask_yes_no(question: str) -> str | None:
    return await _ask(question, True)


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


# -- discovery ----------------------------------------------------------------------------------


def _heard_row(h: _survey.Heard) -> dict[str, Any]:
    return {**h.masked(), "identified_sku": h.sku,
            "manufacturer_data": {str(k): v.hex() for k, v in (h.adv.manufacturer_data or {}).items()}}


def _masks(heard: Collection[_survey.Heard]) -> list[tuple[str | None, tuple[str | None, ...]]]:
    return [(h.address, (h.name, getattr(h.ble, "name", None))) for h in heard]


async def discover(
    address: str | None, sku: str | None
) -> tuple[BLEDevice | None, AdvertisementData | None, list[dict[str, Any]], str | None]:
    """Scan, and return (BLEDevice, advertisement, every Govee advertisement heard, SKU) for one device.

    ``address`` selects one device; ``sku`` (from ``--sku``) restricts matching to that SKU. Otherwise the
    strongest device with a hand-written plan is chosen, else the strongest supported one (the generic
    plan). The returned
    SKU is the one to test (``sku`` if given, else detected), or None if it can't be told. The
    advertisements are already masked (address and the MAC digits in the name)."""
    seconds = float(DISCOVER_OPTIONS.get("seconds") or SCAN_SECONDS)
    print(f"Scanning for {seconds:.0f} s ...")
    heard = await _survey.survey(seconds, target=address, generic=bool(DISCOVER_OPTIONS.get("generic")))
    seen = [_heard_row(h) for h in heard]
    for h in heard:
        print(f"  heard {mask_name(h.name)!r:28s} sku={h.sku} tier={h.tier} rssi={h.rssi} "
              f"{mask_address(h.address)}")
    hits: list[tuple[_survey.Heard, str | None]] = []
    for h in heard:
        if address is not None:
            if h.address.upper() == address.upper():
                hits.append((h, sku or h.sku))
            continue
        if sku:
            found = match_plan_sku(h.name, h.sku, [sku])
            if found:
                hits.append((h, found))
            continue
        planned = match_plan_sku(h.name, h.sku, sorted(PLANS))
        if planned:
            hits.append((h, planned))
    if not hits and address is None and not sku:   # no hand-written plan heard: a supported SKU
        hits = [(h, h.sku) for h in heard if h.tier == "default"]
    if not hits:
        return None, None, seen, None
    if len(hits) > 1:
        kinds = sorted({s or "?" for _, s in hits})
        print(f"  more than one candidate ({', '.join(kinds)}); using the strongest "
              "(pass --address or --sku to choose)")
    best, found = max(hits, key=lambda t: t[0].rssi if t[0].rssi is not None else -999)
    return best.ble, best.adv, seen, found


def _target(ble: Any, adv: Any, sku: str, generic: bool) -> _survey.Heard:
    """A survey entry for the device :func:`discover` returned (or a test's fake), tested as ``sku``."""
    name = (getattr(adv, "local_name", None) if adv is not None else None) or getattr(ble, "name", None) or ""
    merged = SimpleNamespace(local_name=name or None,
                             manufacturer_data=dict(getattr(adv, "manufacturer_data", None) or {}),
                             rssi=getattr(adv, "rssi", None))
    entry = _survey.Heard(ble.address, ble, merged, name, merged.rssi, seen_at=time.monotonic())
    _survey.describe(entry, generic=generic)
    entry.sku = sku.upper()
    entry.tier, entry.reason, profile = _survey.classify(entry.sku, entry.pact)
    entry.kind = device_kind(profile) if profile is not None else entry.kind
    entry.outlets = _survey.outlets(profile)
    entry.connect_visible = visible_connect(profile)
    entry.plan = "hand" if (not generic and entry.sku in PLANS) else ("generic" if profile else None)
    return entry


# -- policy -------------------------------------------------------------------------------------


@dataclasses.dataclass
class Policy:
    """What a run may do: the daytime window, prompts, plugs, secrets, retries."""

    window: Window
    ask: str = "all"                                    # all / unverifiable / none
    read_only: bool = False
    generic: bool = False
    toggle: set[str] = dataclasses.field(default_factory=set)            # addresses (upper-cased)
    secrets: dict[str, bytes] = dataclasses.field(default_factory=dict)  # address (upper) -> secret
    retries: int = 3
    settle: float = 1.0
    route: str = "mac"
    gate: bool = False        # --all: a device that can't be reached is "not reached", not a failure
    writes_off: str | None = None   # --all started outside the window without --yes: no write all run

    @property
    def prompts(self) -> bool:
        return self.ask != "none"

    def connect_skip_reason(self, h: _survey.Heard) -> str | None:
        """Why ``h`` is not connected to now: every connect of its row makes a visible write
        (``h.connect_visible``) and writes are not allowed (outside the window, a run started there without
        ``--yes``, a read-only run). None: it connects as any other device."""
        return connect_skip(h.connect_visible, read_only=self.read_only,
                            writes_allowed=not self.writes_off and self.window.allows_writes())


def _window(args: argparse.Namespace) -> Window:
    return Window(args.day_start, args.day_end, bool(args.force_hours))


def _policy(args: argparse.Namespace, window: Window, *, gate: bool) -> Policy:
    """One device asks every question and connects once, as the tool always did; ``--all`` asks only where
    nothing was read back and retries discovery and the connect (``--retries``, default 3)."""
    ask = "none" if args.no_prompt else (args.ask or ("unverifiable" if gate else "all"))
    retries = args.retries if args.retries is not None else (3 if gate else 1)
    return Policy(window=window, ask=ask, read_only=bool(args.read_only), generic=bool(args.generic),
                  retries=max(1, int(retries)), settle=float(args.settle), route=args.route, gate=gate)


def _read_secrets(path: str | None) -> dict[str, bytes]:
    """``{ref or address (upper): secret}`` from a 0600 JSON file of hex secrets (read once, never logged)."""
    if not path:
        return {}
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as err:
        raise Refused(f"--secrets-file: cannot open it ({err.strerror})") from None
    with os.fdopen(fd, encoding="utf-8") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077:
            raise Refused("--secrets-file must be a regular file with mode 0600")
        try:
            data = json.load(fh)
        except ValueError:
            raise Refused("--secrets-file is not JSON") from None
    if not isinstance(data, dict):
        raise Refused("--secrets-file must hold {ref or address: hex secret}")
    out: dict[str, bytes] = {}
    for n, (key, value) in enumerate(data.items(), 1):
        try:
            secret = bytes.fromhex(str(value))
        except ValueError:
            raise Refused(f"--secrets-file: entry {n} is not hex") from None
        if len(secret) != 8:
            raise Refused(f"--secrets-file: entry {n} is not 8 bytes")
        out[str(key).upper()] = secret
    return out


def _keys(text: str | None) -> set[str]:
    return {k.strip().upper() for k in (text or "").split(",") if k.strip()}


def _wanted(h: _survey.Heard, keys: Collection[str]) -> bool:
    return h.ref.upper() in keys or h.address.upper() in keys


async def _confirm_plugs(targets: list[_survey.Heard], args: argparse.Namespace, policy: Policy,
                         file_secrets: dict[str, bytes]) -> None:
    """Fill ``policy.toggle`` and ``policy.secrets``: an allow-listed plug toggles only once the tester has
    said what it powers (free text, never stored) and confirmed; its secret comes from the secrets file or
    a hidden prompt (Enter: none, so its toggle steps are skipped)."""
    allow = _keys(args.toggle_plugs)
    for h in targets:
        for key in (h.ref.upper(), h.address.upper()):
            if key in file_secrets:
                policy.secrets[h.address.upper()] = file_secrets[key]
    unknown = [k for k in allow if not any(_wanted(h, {k}) for h in targets)]
    if unknown:
        print(f"  --toggle-plugs: {len(unknown)} entr{'y is' if len(unknown) == 1 else 'ies are'} not among "
              "the devices to test (ignored)")
    for h in targets:
        if h.kind != "plug" or not _wanted(h, allow):
            continue
        line = f"  {h.ref} {h.sku} {mask_name(h.name)} {mask_address(h.address)}"
        if outlet_mask(_survey.classify(h.sku, h.pact)[2]) != 0x01:
            line += (f" ({h.outlets} outlets, switched together: it toggles only if they all start on, or "
                     "all off)")
        if not policy.prompts:
            print(f"{line}: allow-listed, but a relay toggle needs the run-time confirmation: read-only")
            continue
        print(f"{line}: allow-listed for a relay toggle")
        await _ask("What is plugged into it? (not saved anywhere)", True, free_text=True)
        if await _ask("Toggle it off and back on?", True) != "yes":
            print("    read-only")
            continue
        policy.toggle.add(h.address.upper())
        if h.address.upper() not in policy.secrets:
            raw = (await _readline("    its secret (16 hex digits; Enter = none): ", getpass.getpass)).strip()
            try:
                secret = bytes.fromhex(raw) if raw else b""
            except ValueError:
                secret = b""
            if len(secret) == 8:
                policy.secrets[h.address.upper()] = secret
            else:
                print("    no usable secret: its toggle steps are skipped (no secret)")


# -- one device ---------------------------------------------------------------------------------


def _err(err: BaseException, address: str | None, names: Names = (), secrets: Collection[bytes] = ()) -> str:
    """A step / restore failure as written to the report (the address, names and secrets scrubbed)."""
    return f"error: {mask_text(describe_error(err, address, names), None, (), secrets)}"


async def _restore(dev: Device, st: Snapshot) -> None:
    """A hand-written plan's default restore (best effort): brightness, then scene / colour temperature /
    RGB, then off if it was off. The generic plan restores with ``restore.restore_start``."""
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


def _state_copy(dev: Any, fallback: Any = None) -> Any:
    """``dev.snapshot()`` (a deep copy of its state), or a copy of ``fallback`` for a device without one."""
    take = getattr(dev, "snapshot", None)
    if callable(take):
        with contextlib.suppress(Exception):
            return take()
    return copy.copy(fallback)


@dataclasses.dataclass
class _Run:
    """One device run's moving parts, shared by its steps and its finish."""

    dev: Any
    plan: SkuPlan
    generic: bool
    kind: str
    policy: Policy
    watch: FrameWatch
    address: str
    names: Names
    secrets: tuple[bytes, ...]
    extra: Callable[[], dict[str, Any]]
    pre: Any = None
    wrote: bool = False
    interrupted: bool = False   # a first Ctrl-C reached this device's run (it then restores and stops)
    abandon: bool = False       # a second one: the restore is abandoned
    warnings: list[str] = dataclasses.field(default_factory=list)


def _skip_reason(step: Step, run: _Run) -> str | None:
    if step.skip:
        return step.skip
    if step.kind == "write" and run.policy.read_only:
        return "read-only run"
    if step.kind == "write" and run.policy.writes_off:
        return run.policy.writes_off
    if step.kind == "write" and not run.policy.window.allows_writes():
        return "outside the daytime window"
    return None


async def _card_readback(run: _Run) -> tuple[Any, dict[str, str | None]]:
    """A card row's read-back: its next advertisement's on/off (no link is held between commands)."""
    adv = await _survey.rescan_advert(run.address, CARD_RESCAN_SECONDS)
    on = run.dev.advertised_on(adv) if adv is not None else None
    reason = None if on is not None else "no on/off in the next advertisement"
    return SimpleNamespace(is_on=on), {"is_on": reason}


async def _run_step(step: Step, run: _Run) -> dict[str, Any]:
    """Send one step, read the state back, judge it, maybe ask the tester: the step's record."""
    dev, policy = run.dev, run.policy
    rec: dict[str, Any] = {"step": step.name, "kind": step.kind, "note": step.note or None}
    skip = _skip_reason(step, run)
    if skip is not None:
        rec.update(sent=None, writes=0, fields={}, verdict="skipped", basis=None, reason=skip)
        print(f"    skipped: {skip}")
        return rec
    mark = run.watch.mark()
    raised: BaseException | None = None
    sent_error: str | None = None
    # Before the send: a Ctrl-C (CancelledError) can land after its frame went out and before its ack.
    run.wrote = run.wrote or (step.kind == "write" and step.refusal is None)
    try:
        await step.action()
        rec["sent"] = "ok"
    except Exception as err:
        raised = err
        if step.refusal is not None and isinstance(err, step.refusal):
            rec["sent"] = f"refused: {type(err).__name__}"
        else:
            sent_error = _err(err, run.address, run.names, run.secrets)
            rec["sent"] = sent_error
            print(f"    send failed: {sent_error}")
    finally:
        run.wrote = run.wrote or bool(run.watch.writes(mark))   # a refusal that sent something after all
    writes = len(run.watch.writes(mark))
    intent = _state_copy(dev) if step.fields else None
    post: Any = None
    carried_map: dict[str, str | None] = {}
    if step.refusal is None:
        await asyncio.sleep(policy.settle)
        rmark = run.watch.mark()
        if run.kind == "card":
            if "is_on" in step.fields and sent_error is None:
                post, carried_map = await _card_readback(run)
            rec["readback"] = None if post is None else {"is_on": post.is_on}
            rec["readback_matches"] = None
        else:
            try:
                st = await dev.update()
                rec["readback"] = snapshot(st, run.plan.snapshot_keys, run.extra())
                rec["readback_matches"] = step.expect(rec["readback"]) if step.expect else None
                post = st
                carried_map = carried(dev, run.watch.window(rmark), run.warnings)
            except Exception as err:
                rec["readback"] = _err(err, run.address, run.names, run.secrets)
                rec["readback_matches"] = False
        print(f"    read-back: {rec['readback']}  match={rec['readback_matches']}")
    field_results: dict[str, str] = {}
    fails: list[str] = []
    if step.fields and post is not None and intent is not None:
        field_results, fails = judge_fields(step, dev, run.pre, intent, post, carried_map)
    check: Any = None
    if step.check is not None:
        verdict, why = step.check()
        check = (verdict, why) if verdict in ("pass", "fail", "unverifiable") else None
    expect_match = rec.get("readback_matches") if step.expect is not None else None

    def verdict_with(observed: str | None) -> Judgement:
        return judge(step, sent_error=sent_error, field_results=field_results, fails=fails, observed=observed,
                     writes=writes, raised=raised, expect_match=expect_match, check=check)

    final = verdict_with(None)
    observed = None
    ask = policy.ask == "all" or (policy.ask == "unverifiable" and final.verdict == "unverifiable")
    if rec["sent"] == "ok" and step.question and ask:
        observed = await _ask(step.question, policy.prompts, step.free_text)
        if observed is not None:
            final = verdict_with(observed)
    rec.update(observed=observed, writes=writes, fields=final.fields, verdict=final.verdict,
               basis=final.basis, reason=final.reason and mask_text(final.reason, None, (), run.secrets))
    if post is not None and run.kind != "card":
        run.pre = _state_copy(dev, post)
    print(f"    {final.verdict}" + (f" ({final.basis})" if final.basis else "")
          + (f": {rec['reason']}" if rec["reason"] else ""))
    return rec


async def _connect(dev: Any, target: _survey.Heard, retries: int) -> None:
    """``dev.connect()`` with retries: between attempts the device is scanned for again (its fresh
    BLEDevice and advertisement handed to the Device), backing off 2, 5 then 10 s. An auth error is final."""
    from govee_ble_local import GoveeBleAuthError

    last: BaseException | None = None
    for attempt in range(retries):
        try:
            await dev.connect()
            return
        except GoveeBleAuthError:
            raise
        except Exception as err:
            last = err
            if attempt + 1 >= retries:
                break
            await asyncio.sleep(CONNECT_BACKOFF[min(attempt, len(CONNECT_BACKOFF) - 1)])
            target.seen_at = 0.0   # a fresh scan for its BLEDevice and advertisement
            with contextlib.suppress(Exception):
                if await _survey.acquire(target, 1):
                    dev.update_ble_device(target.ble)
                    dev.ingest_advertisement(target.adv)
    if last is not None:
        raise last


def _absorb(run: _Run) -> bool:
    """A CancelledError reached a device's run. The first Ctrl-C (``asyncio.run`` cancels the main task) is
    absorbed: noted on ``run`` and the task uncancelled, so the device is restored and the run stops after
    it (True). A later one is the abandonment (False, and ``run.abandon`` set): the second Ctrl-C makes
    ``asyncio.run`` raise KeyboardInterrupt and cancel every task, here or in the same breath as the first
    (a task with two cancellations requested)."""
    task = asyncio.current_task()
    if run.interrupted or (task is not None and task.cancelling() > 1):
        run.interrupted = run.abandon = True
        return False
    run.interrupted = True
    if task is not None:
        task.uncancel()
    return True


async def _carry_on(aw: Coroutine[Any, Any, T], run: _Run) -> T:
    """Await ``aw`` to its end in a task of its own, so that a first Ctrl-C meanwhile doesn't cut it short (it
    only marks the run interrupted, :func:`_absorb`); a cancellation after that one cancels it and passes
    through (the second Ctrl-C abandons it)."""
    task = asyncio.ensure_future(aw)
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled() or not _absorb(run):
                run.abandon = True
                task.cancel()
                raise
            print("\nInterrupted: this device's restore and clean-up carry on, then the run stops (a second "
                  "Ctrl-C abandons them) ...")


async def _plan_restore(run: _Run, initial: Snapshot) -> Outcome:
    """A hand-written plan's restore as an :class:`Outcome`."""
    result = await _restore_with(run.plan, run.dev, initial, run.address, run.names)
    return Outcome(["the plan's restore"], [] if result == "ok" else [result])


def _abandoned(report: dict[str, Any]) -> None:
    report["restore"] = report.get("restore") or "abandoned"
    if "restore_check" not in report:
        report["restore_check"] = RestoreCheck(
            "abandoned", errors=["a second Ctrl-C abandoned the restore"]).as_dict()
    print("  restore: abandoned (second Ctrl-C)")


async def _finish(report: dict[str, Any], run: _Run, start: StartState | None, initial: Snapshot | None,
                  frames_path: Path) -> None:
    """Clean up, in this order: restore the start state (also after an error or Ctrl-C), check the
    restore with one more poll, disconnect, let the frame log reach the disk, then redact it. The
    redaction runs even if disconnecting fails, and the flush is best effort: if it fails (say the
    library's frame-log internals changed), the capture is redacted straight away rather than left raw.

    One Ctrl-C at any point here (or before, in a step) still restores the device: the restore, the
    disconnect and the flush run through :func:`_carry_on`, and one at the check's poll or its question
    leaves that check unverified. A second Ctrl-C abandons the restore (``restore: abandoned``)."""
    dev = run.dev
    outcome: Outcome | None = None

    async def ask(question: str) -> str | None:
        try:
            return await _ask_yes_no(question)
        except asyncio.CancelledError:
            if not _absorb(run):
                raise
            print("\nInterrupted: the question is left unanswered; the run stops after this device.")
            return None

    try:
        restore: Coroutine[Any, Any, Outcome] | None = None
        if run.abandon:
            pass
        elif run.generic and start is not None and run.wrote:
            restore = restore_start(dev, start, lambda e: _err(e, run.address, run.names, run.secrets))
        elif not run.generic and initial is not None and run.wrote:
            restore = _plan_restore(run, initial)
        if restore is not None:
            print("  restoring the start state ...")
            outcome = await _carry_on(restore, run)
            joined = "; ".join(outcome.errors)
            report["restore"] = ("ok" if not outcome.errors
                                 else f"error: {joined}" if run.generic else joined)
        if run.abandon:
            _abandoned(report)
        else:
            check = RestoreCheck("not_needed")
            if outcome is not None and start is not None:
                try:
                    check = await check_restore(dev, start, outcome, run.watch,
                                                ask=ask if run.policy.prompts else None)
                except asyncio.CancelledError:
                    if not _absorb(run):
                        raise
                    cut = "not checked: a Ctrl-C cut the check's poll short"
                    check = RestoreCheck("unverified", [*outcome.unrestorable, cut],
                                         errors=list(outcome.errors), actions=list(outcome.actions))
                except Exception as err:
                    check = RestoreCheck("failed", list(outcome.unrestorable),
                                         errors=[*outcome.errors, _err(err, run.address, run.names)])
            elif outcome is not None:
                check = RestoreCheck("failed" if outcome.errors else "unverified",
                                     errors=list(outcome.errors))
            report["restore_check"] = check.as_dict()
            notes = f" ({'; '.join(check.unrestorable)})" if check.unrestorable else ""
            print(f"  restore: {check.result}{notes}")
            if run.kind != "card":
                try:
                    with contextlib.suppress(Exception):
                        report["final_state"] = snapshot(await dev.update(), run.plan.snapshot_keys)
                except asyncio.CancelledError:
                    if not _absorb(run):
                        raise
    except asyncio.CancelledError:
        _abandoned(report)
        raise
    finally:
        try:
            await _carry_on(dev.stop(), run)
        finally:
            try:
                with contextlib.suppress(Exception):
                    await _carry_on(asyncio.to_thread(_flush_frame_log), run)
            finally:
                run.watch.detach()
                report["frames_redacted"] = redact_frames(frames_path, run.address)
                report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")


async def run_device(target: _survey.Heard, n: int | None, args: argparse.Namespace, out_dir: Path,
                     stamp: str, policy: Policy, lib: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test one device: acquire it, connect (with retries), capture its start state, run its plan's steps,
    and in every case restore and check it, stop, flush and redact. Returns its summary record; a Ctrl-C
    marks it ``interrupted`` (``result: "interrupted"`` when it landed before the plan's end) after the
    restore, and a second one abandons the restore (``restore: abandoned``)."""
    from govee_ble_local import GoveeBleAuthError, create_device
    from govee_ble_local.scenes import load_scenes

    sku = (target.sku or "fieldtest").upper()
    prefix = sku.lower() if n is None else f"{sku.lower()}-{n:02d}"
    report_path = out_dir / f"{prefix}-report-{stamp}.json"
    frames_path = out_dir / f"{prefix}-frames-{stamp}.jsonl"
    address = target.address
    names: Names = (getattr(target.adv, "local_name", None), getattr(target.ble, "name", None), target.name)
    secret = policy.secrets.get(address.upper())
    secrets: tuple[bytes, ...] = (secret,) if secret else ()
    report: dict[str, Any] = {
        "tool_version": TOOL_VERSION, "library_version": (lib or {}).get("version"), "library": lib,
        "python": sys.version.split()[0], "platform": platform.platform(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "session": {"route": policy.route, "ask": policy.ask, "read_only": policy.read_only,
                    "writes_off": policy.writes_off},
        "window": policy.window.as_dict(), "tier": target.tier, "kind": target.kind, "steps": [],
    }
    rec: dict[str, Any] = {"ref": target.ref, "sku": sku, "tier": target.tier, "kind": target.kind,
                           "plan": target.plan, "result": None, "counts": counts([]), "steps": [],
                           "restore": None, "report": report_path.name, "frames": frames_path.name,
                           "no_secret": False, "errors": []}

    def save() -> None:
        # Every string in the report passes the scrubber once more on the way out (a backstop: the
        # error texts were already scrubbed where they were recorded).
        text = json.dumps(report, indent=1, default=json_default) + "\n"
        write_text_atomic(report_path, mask_text(text, address, names, secrets))

    def done(result: str, interrupted: bool = False) -> dict[str, Any]:
        report["result"] = result
        report["interrupted"] = interrupted
        save()
        rec["result"] = mask_text(result, address, names, secrets)
        rec["interrupted"] = interrupted
        rec["steps"] = [{"step": s["step"], "kind": s.get("kind"), "verdict": s.get("verdict"),
                         "basis": s.get("basis"), "reason": s.get("reason")} for s in report["steps"]]
        rec["counts"] = counts(report["steps"])
        rc = report.get("restore_check") or {}
        rec["restore"] = {k: rc.get(k) for k in ("result", "unrestorable", "diffs", "errors")} if rc else None
        if not result.startswith(("completed", "interrupted", "skipped")):
            rec["errors"].append(rec["result"])
        return rec

    print(f"\n== {target.ref} {sku} ({target.tier}, {target.kind or '?'}, plan {target.plan}) "
          f"{mask_name(target.name)!r} {mask_address(address)}")
    # Its connect itself would change the device visibly (the bulbs' 33 25 stops a running effect): checked
    # at its turn, so a run that crosses the window's edge connects to it only inside the window.
    skip = policy.connect_skip_reason(target)
    if skip is not None:
        print(f"  {skip}")
        return done(skip)
    try:
        if not await _survey.acquire(target, policy.retries):
            return done("not reached: not heard again")
    except asyncio.CancelledError:   # the first Ctrl-C (a device's run is where the run is interruptible)
        task = asyncio.current_task()
        if task is not None:
            task.uncancel()
        return done("interrupted", interrupted=True)
    kwargs: dict[str, Any] = {"idle_disconnect": 0, "frame_log": str(frames_path)}
    if target.tier == "modelled":
        kwargs["allow_modelled"] = True
    if secret:
        kwargs["secret"] = secret
    try:
        dev = create_device(target.ble, sku, target.adv, **kwargs)
    except Exception as err:
        return done(f"not created: {describe_error(err, address, names)}")
    plan = (None if policy.generic else PLANS.get(sku)) or generic_plan(dev)
    is_generic = plan.build_steps is generic_steps
    profile = getattr(dev, "profile", None)
    kind = device_kind(profile) if profile is not None else "light"
    rec["kind"], rec["plan"] = kind, "generic" if is_generic else "hand"
    # A row that needs a secret (every plug) tested without one: its 33 b2 unlock is never sent, so the
    # device can't pass the gate (summary.device_passed), whatever its read steps say.
    rec["no_secret"] = bool(getattr(profile, "requires_secret", False)) and not secret
    report.update(kind=kind, plan={"sku": plan.sku, "title": plan.title}, plan_kind=rec["plan"],
                  ref=target.ref, device={"address": mask_address(address), "name": mask_name(target.name)})
    codes: dict[str, int] = {}
    with contextlib.suppress(Exception):
        codes = {name: sc.code for name, sc in load_scenes(plan.sku).items()}

    def extra() -> dict[str, Any]:
        if not is_generic:
            return {}
        out: dict[str, Any] = {"segment_count": getattr(dev, "segment_count", None), "active_scene": None}
        with contextlib.suppress(Exception):
            out["active_scene"] = dev.active_scene
        return out

    watch = FrameWatch(address).attach()
    run = _Run(dev, plan, is_generic, kind, policy, watch, address, names, secrets, extra)
    print(plan.title)
    if plan.intro:
        print(f"  {plan.intro}")
    start: StartState | None = None
    initial: Snapshot | None = None
    result = "aborted: not started"
    connected = False
    try:
        await _connect(dev, target, policy.retries)
        connected = True
        report["device"]["encryption"] = str(getattr(dev, "encryption", None))
        mark = watch.mark()
        st = await dev.update()
        start = await capture_start(dev, target.adv, watch.window(mark), kind=kind, polled=st,
                                    ask=_ask_yes_no if policy.prompts else None)
        initial = snapshot(st, plan.snapshot_keys, extra())
        report["initial_state"] = initial
        report["start"] = start.summary()
        run.pre = _state_copy(dev, st)
        print(f"Connected ({getattr(dev, 'encryption', '?')}); current state: {initial}\n")
        save()
        ctx = PlanContext(dev, plan.sku, codes, kind=kind, tier=target.tier, adv=target.adv, start=start,
                          toggle_ok=address.upper() in policy.toggle, has_secret=bool(secret),
                          prompts=policy.prompts, watch=watch)
        for i, step in enumerate(plan.build_steps(ctx), 1):
            print(f"[{i:2d}] {step.name}" + (f"  ({step.note})" if step.note else ""))
            report["steps"].append(await _run_step(step, run))
            save()
        result = "completed"
    except asyncio.CancelledError:
        result = "interrupted"
        if _absorb(run):
            print("\nInterrupted: restoring this device (a second Ctrl-C abandons the restore) ...")
    except GoveeBleAuthError as err:
        result = f"auth failed: {describe_error(err, address, names)}"
    except Exception as err:
        unreached = policy.gate and not connected   # --all: a device Home Assistant holds, say
        result = (f"not reached: connect failed: {describe_error(err, address, names)}" if unreached
                  else f"aborted: {describe_error(err, address, names)}")
        print(f"\n{result}")
    finally:
        if run.warnings:
            report["warnings"] = sorted(set(run.warnings))
        try:
            with contextlib.suppress(Exception):
                await _finish(report, run, start, initial, frames_path)
        except asyncio.CancelledError:   # the second Ctrl-C: _finish recorded the restore as abandoned
            run.abandon = run.interrupted = True
        watch.detach()
    report["result"] = result
    _print_steps(report)
    return done(result, interrupted=run.interrupted)


def _print_steps(report: dict[str, Any]) -> None:
    print("\nSteps")
    for rec in report["steps"]:
        print(f"  {rec['step']:30s} {rec.get('verdict') or '?':12s} sent={rec.get('sent')!s:6.6s} "
              f"readback={rec.get('readback_matches')!s:5s} observed={rec.get('observed')}")
    rc = report.get("restore_check") or {}
    print(f"  restore: {report.get('restore')} ({rc.get('result')})  result: {report.get('result')}")


# -- modes --------------------------------------------------------------------------------------


def _base_summary(args: argparse.Namespace, policy: Policy, lib: dict[str, Any]) -> dict[str, Any]:
    return {"tool_version": TOOL_VERSION, "route": policy.route, "library": lib,
            "python": sys.version.split()[0], "platform": platform.platform(),
            "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "window": policy.window.as_dict(),
            "options": {"allow_modelled": bool(args.allow_modelled), "toggle_plugs": len(policy.toggle),
                        "ask": policy.ask, "generic": policy.generic, "read_only": policy.read_only}}


def _exit_code(data: dict[str, Any], interrupted: bool) -> int:
    """``--all``: the summary's gate as the exit code: 0 ``pass``, 1 ``fail`` (a failed step, an aborted
    device, a partial or failed restore), 4 ``incomplete`` (a device not reached or skipped, a write skipped
    by the window or not verified, a device whose row needs a secret, every plug, tested without one); 130
    when interrupted, 2 when nothing was tested."""
    if interrupted:
        return EXIT_INTERRUPTED
    if not data.get("devices"):
        return EXIT_NOTHING
    return {"pass": EXIT_OK, "fail": EXIT_FAIL}.get(gate_verdict(data), EXIT_INCOMPLETE)


def _print_listing(heard: list[_survey.Heard],
                   skip: Callable[[_survey.Heard], str | None] | None = None) -> None:
    """The listing; ``skip`` marks a device that would not be connected to now
    (``Policy.connect_skip_reason``)."""
    print(f"\nGovee devices heard: {len(heard)}")
    print(f"  {'ref':6s} {'sku':6s} {'tier':11s} {'pact':6s} {'kind':5s} {'plan':7s} {'rssi':>4s}  "
          "name / address")
    for h in heard:
        pact = f"{h.pact[0]},{h.pact[1]}" if h.pact else "-"
        rssi = str(h.rssi) if h.rssi is not None else "?"
        why = h.reason or (skip(h) if skip is not None and h.plan else None)
        print(f"  {h.ref:6s} {h.sku or '?':6s} {h.tier:11s} {pact:6s} {h.kind or '-':5s} {h.plan or '-':7s} "
              f"{rssi:>4s}  {mask_name(h.name)} {mask_address(h.address)}" + (f"  ({why})" if why else ""))
    plugs = [h for h in heard if h.kind == "plug"]
    if plugs:
        print("\nPlugs (a relay toggles only with --toggle-plugs REF and the run-time confirmation):")
        for h in plugs:
            word = "outlet" if h.outlets == "1" else "outlets"
            print(f"  {h.ref} {h.sku} {mask_name(h.name)} {mask_address(h.address)} {h.outlets} {word}")


async def list_devices(args: argparse.Namespace, out_dir: Path, stamp: str) -> int:
    """``--list``: scan, print every Govee device heard and the plugs, write the masked listing. No
    connection, at any hour; a device that ``--all`` would not connect to now (its connect writes visibly,
    and writes are not allowed: the window, ``--read-only``) is marked ``skipped``."""
    seconds = args.scan_seconds or SURVEY_SECONDS
    policy = Policy(window=_window(args), read_only=bool(args.read_only))
    print(f"Scanning for {seconds:.0f} s ...")
    heard = await _survey.survey(seconds, generic=bool(args.generic))
    _print_listing(heard, policy.connect_skip_reason)
    path = out_dir / f"fieldtest-list-{stamp}.json"
    rows = [{**h.masked(), "skip": policy.connect_skip_reason(h) if h.plan else None} for h in heard]
    text = json.dumps({"tool_version": TOOL_VERSION, "listed": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                       "window": policy.window.as_dict(), "heard": rows}, indent=1) + "\n"
    write_text_atomic(path, mask_text_all(text, _masks(heard)))
    print(f"\nListing -> {path}")
    return EXIT_OK if heard else EXIT_NOTHING


def _not_run(target: _survey.Heard) -> dict[str, Any]:
    return {"ref": target.ref, "sku": target.sku, "tier": target.tier, "kind": target.kind,
            "plan": target.plan, "result": "not run (interrupted)", "counts": counts([]), "steps": [],
            "restore": None, "errors": []}


async def run_all(args: argparse.Namespace, out_dir: Path, stamp: str, window: Window,
                  lib: dict[str, Any]) -> int:
    """``--all``: list every Govee device heard, then test each supported one (and with
    ``--allow-modelled`` each opt-in one), one at a time, each over its own connection."""
    policy = _policy(args, window, gate=True)
    file_secrets = _read_secrets(args.secrets_file)
    print(f"Release-gate pass (route {policy.route}): {window.banner()}")
    print("Ctrl-C once restores the current device and stops; a second Ctrl-C abandons that restore.")
    heard: list[_survey.Heard] = []
    seconds = args.scan_seconds or SURVEY_SECONDS
    for attempt in range(policy.retries):
        print(f"Scanning for {seconds:.0f} s ...")
        heard = await _survey.survey(seconds, generic=policy.generic)
        if heard or attempt + 1 >= policy.retries:
            break
        print("  nothing heard; scanning again")
    summary = Summary(out_dir / f"fieldtest-summary-{stamp}.json", _base_summary(args, policy, lib))
    summary.mask = _masks(heard)
    summary.data["heard"] = [h.masked() for h in heard]
    _print_listing(heard, policy.connect_skip_reason)
    only, skip = _keys(args.only), _keys(args.skip)
    tiers = {"default", "modelled"} if args.allow_modelled else {"default"}
    chosen = [h for h in heard if h.tier in tiers and (not only or _wanted(h, only)) and not _wanted(h, skip)]
    if not chosen:
        summary.save()
        print(f"\nNothing to test. Summary -> {summary.path}")
        return EXIT_NOTHING
    await _confirm_plugs(chosen, args, policy, file_secrets)
    summary.secrets = list(policy.secrets.values())
    summary.data["options"]["toggle_plugs"] = len(policy.toggle)
    order = sorted(chosen, key=lambda h: (KIND_ORDER.get(h.kind or "light", 0),
                                          -(h.rssi if h.rssi is not None else -999)))
    visible = [h for h in order if h.kind != "plug" or h.address.upper() in policy.toggle]
    if visible and not policy.read_only and not args.yes:
        if not window.allows_writes():
            # The start confirmation is the run's: a run started before the window opens without --yes
            # writes nothing visible all the way through, even once the window opens.
            policy.writes_off = f"started outside {window.label()} without --yes: read steps only this run"
            print(f"{len(visible)} device(s) would change visibly, but the run started outside "
                  f"{window.label()} without --yes: read steps only, for the whole run.")
        elif not policy.prompts:
            summary.save()
            print(f"{len(visible)} device(s) would change visibly: a run without prompts needs --yes.")
            return EXIT_REFUSED
        else:
            reply = await _ask(f"{len(visible)} device(s) will change visibly (each is restored after); "
                               "start?", True)
            if reply != "yes":
                summary.save()
                print("Not started.")
                return EXIT_REFUSED
    summary.data["options"]["writes_off"] = policy.writes_off
    summary.save()
    interrupted = False
    for n, target in enumerate(order, 1):
        if interrupted:
            summary.add_device(_not_run(target))
            continue
        if not window.allows_writes() and not policy.read_only and not policy.writes_off:
            print(f"\n({window.banner()})")
        rec = await run_device(target, n, args, out_dir, stamp, policy, lib)
        summary.add_device(rec)
        interrupted = bool(rec.get("interrupted"))
    summary.save()
    print("\nSummary")
    print(console_table(summary.data, summary.path))
    print("\nAttach the summary (and any device's report and frame capture): addresses, names, serials, "
          "Wi-Fi MACs and secrets are masked.")
    return _exit_code(summary.data, interrupted)


async def run(args: argparse.Namespace, out_dir: Path, stamp: str, *, window: Window | None = None,
              lib: dict[str, Any] | None = None) -> int:
    """One device: detect it (or ``--sku`` / ``--address``) and run its plan (hand-written, else generic)."""
    window = window or _window(args)
    if lib is None:
        with contextlib.suppress(Exception):
            lib = library_info("auto")
    lib = lib or {}
    policy = _policy(args, window, gate=False)
    file_secrets = _read_secrets(args.secrets_file)
    prefix = (args.sku or "fieldtest").lower()
    report_path = out_dir / f"{prefix}-report-{stamp}.json"
    report: dict[str, Any] = {
        "tool_version": TOOL_VERSION, "library_version": lib.get("version"), "library": lib,
        "python": sys.version.split()[0], "platform": platform.platform(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "steps": [],
    }

    def save(address: str | None = None, names: Names = ()) -> None:
        text = json.dumps(report, indent=1, default=json_default) + "\n"
        write_text_atomic(report_path, mask_text(text, address, names))

    DISCOVER_OPTIONS.update(seconds=args.scan_seconds, generic=policy.generic)
    try:
        ble, adv, heard, sku = await discover(args.address, args.sku)
    finally:
        DISCOVER_OPTIONS.clear()
    report["advertisements"] = heard
    if ble is None:
        report["result"] = "device not found"
        save()
        looking = args.sku or "a device with a plan or a supported SKU"
        print(f"\nNo device found (looking for: {looking}). "
              f"Report (with every Govee device heard) -> {report_path}")
        return EXIT_NOTHING
    names: Names = (getattr(adv, "local_name", None), getattr(ble, "name", None))
    target = _target(ble, adv, sku, policy.generic) if sku else None
    why = None
    if target is None or target.plan is None:
        why = f"no test plan for SKU {sku or 'unknown'}"
    elif target.tier == "modelled" and not args.allow_modelled:
        why = f"{target.sku} is a modelled (opt-in) row: pass --allow-modelled"
    if target is None or why is not None:
        report["result"] = why
        save(ble.address, names)
        print(f"\nFound {mask_address(ble.address)} but {why}. Pass --sku (hand-written plans: "
              f"{', '.join(sorted(PLANS))}; any supported SKU uses the generic plan). "
              f"Report -> {report_path}")
        return EXIT_NOTHING
    print(window.banner())
    await _confirm_plugs([target], args, policy, file_secrets)
    summary = Summary(out_dir / f"fieldtest-summary-{stamp}.json", _base_summary(args, policy, lib))
    summary.mask = _masks([target])
    summary.secrets = list(policy.secrets.values())
    summary.data["heard"] = [target.masked()]
    rec = await run_device(target, None, args, out_dir, stamp, policy, lib)
    summary.add_device(rec)
    print("\nSummary")
    print(console_table(summary.data, summary.path))
    print("\nPlease attach the report and the frame capture (and the summary) to the GitHub issue "
          f"(addresses, serial and Wi-Fi MAC are masked):\n  {out_dir / str(rec['report'])}\n"
          f"  {out_dir / str(rec['frames'])}")
    if rec.get("interrupted"):
        return EXIT_INTERRUPTED
    # One device, as the tool always did: 0 once the plan completed (its verdicts are in the report and the
    # summary), 1 when it could not (``--all`` fails on a failed step too).
    return EXIT_OK if rec["result"] == "completed" else EXIT_FAIL


# -- arguments ----------------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    skus = ", ".join(sorted(PLANS))
    ap = argparse.ArgumentParser(
        description="Guided field test (run by someone with the device): scans, detects the SKU and runs "
                    f"its test plan. Hand-written plans: {skus}; every other supported SKU uses the generic "
                    "plan built from its profile. --list surveys every Govee device in range; --all is the "
                    "release-gate pass over all of them, one at a time.")
    ap.add_argument("--sku", type=str.upper, metavar="SKU",
                    help=f"test this SKU (hand-written plans: {skus}; any other supported SKU uses the "
                         "generic plan; default: detect it from the scanned device)")
    ap.add_argument("--address", help="Bluetooth address (or macOS UUID) of the device; default: scan")
    ap.add_argument("--out", default=".", help="directory for the reports and frame captures (default: .)")
    ap.add_argument("--no-prompt", action="store_true", help="don't ask questions (the same as --ask none)")
    ap.add_argument("--settle", type=float, default=1.0, help="seconds to wait after each command")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true",
                      help="scan only: list every Govee device heard (ref, SKU, tier, pact, kind, plan) "
                           "and the plugs with their outlets, write a masked listing and exit; no "
                           "connection, any hour")
    mode.add_argument("--all", action="store_true",
                      help="release-gate pass: list, then test every supported device one at a time (one "
                           "connection each), restoring each; unsupported devices are listed only")
    ap.add_argument("--allow-modelled", action="store_true",
                    help="also test modelled (opt-in) rows (create_device(..., allow_modelled=True))")
    ap.add_argument("--toggle-plugs", metavar="REF[,REF]",
                    help="plugs (by ref or address) whose relay may toggle; each still needs the run-time "
                         "confirmation, and every other plug gets read-only steps")
    ap.add_argument("--secrets-file", metavar="PATH",
                    help="JSON {ref or address: hex secret}, a regular file with mode 0600 (else asked, "
                         "hidden)")
    ap.add_argument("--force-hours", action="store_true",
                    help="allow visible writes outside the daytime window (recorded in every report)")
    ap.add_argument("--day-start", type=parse_hhmm, default=parse_hhmm("07:00"), metavar="HH:MM",
                    help="visible writes allowed from (default 07:00, local time)")
    ap.add_argument("--day-end", type=parse_hhmm, default=parse_hhmm("22:00"), metavar="HH:MM",
                    help="visible writes allowed until (default 22:00, local time)")
    ap.add_argument("--read-only", action="store_true", help="only read steps (connects, polls), at any hour")
    ap.add_argument("--generic", action="store_true",
                    help="use the generic plan even where a hand-written one exists")
    ap.add_argument("--only", metavar="REF,...", help="--all: test only these devices (ref or address)")
    ap.add_argument("--skip", metavar="REF,...", help="--all: skip these devices (ref or address)")
    ap.add_argument("--scan-seconds", type=float, default=None,
                    help=f"scan length (default {SURVEY_SECONDS:.0f} s for --list / --all, "
                         f"{SCAN_SECONDS:.0f} s for one device)")
    ap.add_argument("--retries", type=int, default=None,
                    help="discovery and connect attempts (default: 3 with --all, 1 for one device)")
    ap.add_argument("--ask", choices=("all", "unverifiable", "none"),
                    help="which steps ask the tester: all (the default for one device), unverifiable (the "
                         "default for --all: only where nothing was read back), none")
    ap.add_argument("--yes", action="store_true",
                    help="skip the 'N devices will change visibly; start?' question")
    ap.add_argument("--library", choices=("auto", "checkout", "installed"), default="auto",
                    help="the library to test: auto (this checkout once its classes are generated, else the "
                         "installed one), checkout, or installed (the release-candidate wheel; refuses a "
                         "source tree)")
    ap.add_argument("--expect-version", metavar="X.Y.Z",
                    help="stop unless the library under test is this version")
    ap.add_argument("--route", default="mac", help="route label recorded in the summary (default: mac)")
    args = ap.parse_args(argv)
    try:
        Window(args.day_start, args.day_end, args.force_hours)
    except ValueError as err:
        ap.error(str(err))
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    try:
        _select_library(args.library)
        lib = library_info(args.library)
        if args.expect_version and lib.get("version") != args.expect_version:
            raise Refused(f"the library under test is {lib.get('version')} ({lib.get('source')}), "
                          f"not {args.expect_version}")
    except Refused as err:
        print(f"Refused: {err}")
        return EXIT_REFUSED
    window = _window(args)
    print(f"Library under test: govee-ble-local {lib.get('version')} ({lib.get('source')}"
          + (f", {lib['wheel']}" if lib.get("wheel") else "") + ")")
    try:
        if args.list:
            return asyncio.run(list_devices(args, out, stamp))
        if args.all:
            return asyncio.run(run_all(args, out, stamp, window, lib))
        return asyncio.run(run(args, out, stamp, window=window, lib=lib))
    except Refused as err:
        print(f"Refused: {err}")
        return EXIT_REFUSED
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return EXIT_INTERRUPTED
