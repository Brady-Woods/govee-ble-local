"""Scanning for every mode: one survey of every Govee device heard, its tier, and getting it back later.

* :func:`survey` scans once and merges the advertisements per address (the latest non-empty name, the
  latest Govee manufacturer data, the best RSSI), then identifies each device and classifies it.
* :func:`classify` tells a SKU's tier at its advertised pact: ``default`` (the library serves it),
  ``modelled`` (only with ``allow_modelled``, an opt-in row), or ``unsupported`` (listed, never tested).
* :func:`acquire` gets a device back for its turn: a recent advertisement is reused, else a fresh scan
  for that address with growing timeouts.
* :func:`device_ref` is a short, stable, paste-safe reference for a device (a keyed digest of its address).

Nothing here imports the library at module load (the runner chooses which library to test first).
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import hmac
import os
import secrets
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from .masking import mask_address, mask_name

if TYPE_CHECKING:
    from govee_ble_local import DeviceProfile

# An advertisement heard this recently is reused for a device's turn instead of scanning again.
REUSE_SECONDS = 20.0
ACQUIRE_TIMEOUTS = (10.0, 20.0, 30.0)
# The ref key: random, owner-only, created on first use. GOVEE_FIELD_TEST_REF_KEY points elsewhere.
REF_KEY_ENV = "GOVEE_FIELD_TEST_REF_KEY"
# Set (the suite sets it for every test), or under pytest (``PYTEST_CURRENT_TEST``) whatever a tree's
# conftest sets: every real scan or connect raises instead of reaching the Bluetooth stack.
NO_SCAN_ENV = "GOVEE_FIELD_TEST_NO_SCAN"
_DEFAULT_REF_KEY = Path("~/.cache/govee-ble-local/field-test-ref.key")
TIERS = ("default", "modelled", "unsupported")


@dataclasses.dataclass
class Heard:
    """One device the survey heard (the raw address and name stay in memory, never in a file)."""

    address: str
    ble: Any                        # bleak BLEDevice (the latest)
    adv: Any                        # merged advertisement: local_name, manufacturer_data, rssi
    name: str
    rssi: int | None
    sku: str | None = None
    pact: tuple[int, int] | None = None
    tier: str = "unsupported"
    reason: str | None = None
    kind: str | None = None         # light / plug / card (generic.device_kind), None when unsupported
    plan: str | None = None         # "hand" (a plans/<sku>.py), "generic", or None (not tested)
    outlets: str | None = None      # plugs only: popcount of the relay mask; "2+" / "?" when any bit counts
    connect_visible: str | None = None   # a visible write every connect sends (guard.visible_connect)
    seen_at: float = 0.0            # time.monotonic() of the latest advertisement

    @property
    def ref(self) -> str:
        return device_ref(self.address)

    def masked(self) -> dict[str, Any]:
        """The listing row as written to a file: no raw address or name."""
        return {"ref": self.ref, "sku": self.sku, "tier": self.tier,
                "pact": list(self.pact) if self.pact else None, "kind": self.kind, "rssi": self.rssi,
                "name": mask_name(self.name), "address": mask_address(self.address),
                "plan": self.plan, "outlets": self.outlets, "connect_visible": self.connect_visible,
                "reason": self.reason}


def _radio_off() -> str | None:
    """Why the real Bluetooth stack is off limits (:data:`NO_SCAN_ENV`, or a pytest test running), or None."""
    for env in (NO_SCAN_ENV, "PYTEST_CURRENT_TEST"):
        if os.environ.get(env):
            return f"{env} is set"
    return None


def _real(obj: Any) -> bool:
    return str(getattr(obj, "__module__", "")).startswith("bleak")


def _scanner() -> Any:
    """bleak's ``BleakScanner`` (looked up at each call, so a test can fake it), unless the radio is off
    (:func:`_radio_off`) and it is the real one."""
    from bleak import BleakScanner

    why = _radio_off()
    if why and _real(BleakScanner):
        raise RuntimeError(f"scanning is disabled ({why})")
    return BleakScanner


@contextlib.contextmanager
def no_radio() -> Iterator[None]:
    """For the block's length, while the radio is off (:func:`_radio_off`), every library connect that
    would reach bleak (``establish_connection`` still bleak-retry-connector's, not a test's fake) raises
    instead: connect, a write's reconnect, a restore's."""
    why = _radio_off()
    from govee_ble_local.transport import connection

    real = getattr(connection, "establish_connection", None)
    if not why or not _real(real):
        yield
        return

    async def refused(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(f"connecting is disabled ({why})")

    setattr(connection, "establish_connection", refused)  # noqa: B010 - a module attribute, by name
    try:
        yield
    finally:
        if getattr(connection, "establish_connection", None) is refused:
            setattr(connection, "establish_connection", real)  # noqa: B010


class NotPrivate(Exception):
    """A secrets file or ref key the tool won't read (:func:`read_private`). ``why``: ``"type"`` (not a
    regular file), ``"mode"`` (group or others have a permission bit), ``"acl"`` (macOS: an ACL entry) or
    ``"acl?"`` (macOS: ``ls`` could not tell); ``mode``: its permission bits. The caller words the refusal by
    the path alone, never a value."""

    def __init__(self, why: str, mode: int) -> None:
        super().__init__(why)
        self.why = why
        self.mode = stat.S_IMODE(mode)

    @property
    def acl(self) -> str:
        """The ACL refusal (``why`` ``"acl"`` or ``"acl?"``), in words without the path."""
        if self.why == "acl?":
            return "its ACL could not be checked (/bin/ls -le failed)"
        return ("it has an ACL entry (ls -le lists it), which can let others read it whatever its mode: "
                "remove it with chmod -N (and any inheritable one on its folder)")


def _acl(path: Path) -> str | None:
    """macOS only: ``"acl"`` when ``/bin/ls -led`` lists an ACL entry for ``path`` (any line after the file's
    own, such as `` 0: group:everyone inherited allow read``; ``-q`` keeps a name on one line), ``"acl?"``
    when ``ls`` can't say, else None. The mode bits don't show an ACL, and one inherited from a folder
    (``everyone allow read,file_inherit``) lets others read a 0600 file. Elsewhere None: a Linux POSIX ACL
    entry shows in the group bits (the ACL mask), which the mode check already refuses."""
    if sys.platform != "darwin":
        return None
    try:
        done = subprocess.run(["/bin/ls", "-ledq", "--", str(path)],  # noqa: S603 - fixed argv, absolute ls
                              capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return "acl?"
    lines = done.stdout.decode(errors="replace").splitlines()
    if done.returncode != 0 or not lines:
        return "acl?"
    return "acl" if any(line.strip() for line in lines[1:]) else None


def read_private(path: Path) -> bytes:
    """``path``'s bytes, when it is the tester's own: opened without following a symlink or waiting for a
    FIFO's writer (``O_NOFOLLOW | O_NONBLOCK``), then judged on that descriptor (``fstat``) before a byte is
    read: a regular file whose mode gives group and others no permission at all (``mode & 0o077`` is 0:
    0600, 0400) and, on macOS, with no ACL entry (:func:`_acl`). The descriptor is closed on every path.
    ``FileNotFoundError`` when there is none, :class:`NotPrivate` when it is refused, another ``OSError``
    when it can't be opened (a symlink: ``ELOOP``)."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode):
            raise NotPrivate("type", mode)
        if mode & 0o077:
            raise NotPrivate("mode", mode)
        acl = _acl(path)
        if acl:
            raise NotPrivate(acl, mode)
        chunks: list[bytes] = []
        while chunk := os.read(fd, 1 << 16):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _ref_key_path() -> Path:
    return Path(os.environ.get(REF_KEY_ENV) or _DEFAULT_REF_KEY).expanduser()


def _ref_key_refused(path: Path, err: NotPrivate) -> str:
    if err.why == "type":
        return f"{path} is not a regular file"
    if err.why == "mode":
        return (f"{path}: the ref key is readable by group or others (mode {err.mode:04o}); delete it to "
                "make a new one (mode 0600)")
    return f"{path}: the ref key is not private; {err.acl}"


def _ref_key(path: Path) -> bytes:
    """The ref key at ``path``: created 0600 (its folder 0700) on first use. One :func:`read_private`
    refuses (not a regular file, group or others have a permission bit, an ACL entry on macOS: its refs are
    no longer private), a new one its folder gave an ACL entry, and one too short are an ``OSError`` that
    names the path."""
    try:
        key = read_private(path)
    except FileNotFoundError:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
        acl = _acl(path)   # one its folder passes on (inherited) is in no mode bit
        if acl:
            raise OSError(_ref_key_refused(path, NotPrivate(acl, 0o600))) from None
        return key
    except NotPrivate as err:
        raise OSError(_ref_key_refused(path, err)) from None
    if len(key) >= 16:
        return key
    raise OSError(f"{path}: the ref key is too short; delete it to make a new one")


_KEYS: dict[Path, bytes] = {}


def ref_key() -> bytes:
    """The ref key (:data:`REF_KEY_ENV`, else the default path), read or made once; :func:`_ref_key`'s
    ``OSError`` where it is refused (``runner.main`` checks it before any scan, so that is a refusal)."""
    path = _ref_key_path()
    if path not in _KEYS:
        _KEYS[path] = _ref_key(path)
    return _KEYS[path]


def device_ref(address: str) -> str:
    """6 hex digits of HMAC-SHA256 over the upper-cased address, keyed by a random per-user key: stable
    between runs and safe to paste (an unkeyed digest of a MAC whose vendor prefix is known is not: the
    other 3 bytes can be brute-forced)."""
    return hmac.new(ref_key(), address.upper().encode(), hashlib.sha256).hexdigest()[:6]


def outlets(profile: DeviceProfile | None) -> str | None:
    """A plug's outlet count from its read-back relay mask; where any bit counts, ``2+`` for a row whose
    power write sets every outlet (``relay_power`` ``all_outlets`` / ``total_switch``), else ``?``. None for
    a light."""
    if profile is None or not getattr(profile, "relay", False):
        return None
    mask = int(getattr(profile, "relay_on_mask", 0xFF))
    if mask != 0xFF:
        return str(bin(mask).count("1"))
    return "?" if getattr(profile, "relay_power", "outlet0") == "outlet0" else "2+"


def classify(sku: str | None, pact: tuple[int, int] | None) -> tuple[str, str | None, Any]:
    """``(tier, reason, profile)`` for an advertised SKU at its pact; the profile is None when unsupported."""
    from govee_ble_local import profile_for, supported_skus

    if not sku:
        return "unsupported", "SKU not identified", None
    profile = profile_for(sku, pact=pact)
    if profile is not None:
        return "default", None, profile
    profile = profile_for(sku, pact=pact, allow_modelled=True)
    if profile is not None:
        return "modelled", None, profile
    if sku.upper() not in supported_skus(allow_modelled=True):
        return "unsupported", "no BLE row", None
    if pact is None:
        return "unsupported", "pact required, none advertised", None
    return "unsupported", f"not served at pact ({pact[0]},{pact[1]}) / rows need goodsType", None


def _merge(entry: Heard | None, ble: Any, adv: Any) -> Heard:
    """Fold one advertisement into the address's entry: the latest non-empty name, the latest Govee
    manufacturer data, the best RSSI."""
    from govee_ble_local.identify import parse_manufacturer_data

    name = getattr(adv, "local_name", None) or getattr(ble, "name", None) or ""
    mfg = dict(getattr(adv, "manufacturer_data", None) or {})
    rssi = getattr(adv, "rssi", None)
    if entry is None:
        entry = Heard(ble.address, ble, SimpleNamespace(local_name=None, manufacturer_data={}, rssi=None),
                      "", None)
    entry.ble = ble
    entry.seen_at = time.monotonic()
    if name:
        entry.name = name
    if mfg and (parse_manufacturer_data(mfg) is not None or not entry.adv.manufacturer_data):
        entry.adv.manufacturer_data = mfg
    if rssi is not None and (entry.rssi is None or rssi > entry.rssi):
        entry.rssi = rssi
    entry.adv.local_name = entry.name or None
    entry.adv.rssi = entry.rssi
    return entry


def describe(entry: Heard, *, generic: bool = False) -> Heard:
    """Identify and classify a merged entry (sku, pact, tier, reason, kind, plan, outlets, connect_visible).
    ``generic``: the generic plan is used where a hand-written one exists (``--generic``)."""
    from govee_ble_local.identify import identify

    from .generic import device_kind
    from .guard import visible_connect
    from .plan import PLANS

    ident = identify(entry.name, entry.adv.manufacturer_data)
    entry.sku = ident.sku.upper() if ident else entry.sku
    entry.pact = ident.pact if ident else None
    entry.tier, entry.reason, profile = classify(entry.sku, entry.pact)
    entry.kind = device_kind(profile) if profile is not None else None
    entry.outlets = outlets(profile)
    entry.connect_visible = visible_connect(profile)
    hand = not generic and entry.sku in PLANS
    entry.plan = None if profile is None else ("hand" if hand else "generic")
    return entry


async def survey(seconds: float, *, target: str | None = None, generic: bool = False) -> list[Heard]:
    """Scan for ``seconds`` and return every Govee device heard (plus ``target``, an address, if heard
    whatever its name), strongest first, identified and classified."""
    from govee_ble_local.identify import identify

    scanner = _scanner()
    merged: dict[str, Heard] = {}

    def on_adv(ble: Any, adv: Any) -> None:
        merged[ble.address] = _merge(merged.get(ble.address), ble, adv)

    async with scanner(on_adv):
        await asyncio.sleep(seconds)
    out: list[Heard] = []
    for entry in merged.values():
        is_target = target is not None and entry.address.upper() == target.upper()
        if identify(entry.name, entry.adv.manufacturer_data) is None and not is_target:
            continue
        out.append(describe(entry, generic=generic))
    return sorted(out, key=lambda e: -(e.rssi if e.rssi is not None else -999))


async def acquire(entry: Heard, retries: int) -> bool:
    """Get ``entry`` back for its turn: reuse an advertisement younger than :data:`REUSE_SECONDS`, else
    scan for its address with growing timeouts (10, 20, 30 s; ``retries`` of them). Updates the entry's
    BLEDevice and advertisement; False when it was not heard."""
    if time.monotonic() - entry.seen_at <= REUSE_SECONDS:
        return True
    scanner = _scanner()
    want = entry.address.upper()
    for timeout in ACQUIRE_TIMEOUTS[:max(1, retries)]:
        heard: list[tuple[Any, Any]] = []

        def match(ble: Any, adv: Any, heard: list[tuple[Any, Any]] = heard) -> bool:
            if ble.address.upper() != want:
                return False
            heard.append((ble, adv))
            return True

        found = await scanner.find_device_by_filter(match, timeout=timeout)
        if found is not None and heard:
            _merge(entry, *heard[-1])
            return True
    return False


async def rescan_advert(address: str, seconds: float = 4.0) -> Any:
    """The device's next advertisement within ``seconds`` (a card row's read-back), or None."""
    scanner = _scanner()
    want = address.upper()
    heard: list[Any] = []

    def match(ble: Any, adv: Any) -> bool:
        if ble.address.upper() == want:
            heard.append(adv)
            return True
        return False

    await scanner.find_device_by_filter(match, timeout=seconds)
    return heard[-1] if heard else None
