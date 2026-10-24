"""Privacy masking for everything the field test writes (reports and frame captures).

Both output files are meant to be attached to a public GitHub issue, so:

* Bluetooth addresses are masked to their vendor prefix (:func:`mask_address`), and the MAC digits
  a Govee name ends in (``ihoment_H6188_1A2B``) are masked too (:func:`mask_name`): with the vendor
  prefix they would give back all but one byte of the address.
* Free text, such as an exception message, goes through :func:`mask_text` (via
  :func:`describe_error`) before it reaches the report, and the whole report once more as it is
  saved: the device's address in any case and form (also the Wi-Fi MAC derived from it) and every
  other MAC-shaped token (``:``, ``-`` or ``_`` separated, which covers BlueZ's ``dev_XX_XX_..``
  D-Bus path) are masked like an address, and the device's advertised names and every other
  Govee-name-shaped token lose their MAC digits like :func:`mask_name`. That is defence in depth:
  bleak-retry-connector 4.6.1 builds its connect errors as ``<address> - <address>: ...`` (the
  library passes the address as the name), but other releases within the dependency floor may name
  the device (``<address> - <name> -> <adapter>``).
* The serial number, device id and MACs are removed from state snapshots (:func:`snapshot`).
* A plug's secret given to the run is scrubbed as hex in either case wherever it appears
  (:func:`mask_text`'s ``secrets``); the run's summary, which names many devices, is scrubbed for every
  one of them (:func:`mask_text_all`).
* In the frame capture (:func:`redact_frames`) every ``wire`` field is dropped: it holds the
  ciphertext and the ``e7`` handshake, from which the session key can be derived (README, Security
  notes), and the analyzer only reads ``plain``. Device-info (``aa 07``) and status-burst (``ac``)
  reply payloads are zeroed, since they can carry the serial number and Wi-Fi MAC. A status burst's
  first chunk keeps its 7-byte header (no PII: index, packet count, lastLen, cmd, sub), so the
  analyzer can still judge the burst by the app's packet-count rule. The library writes the capture under
  a hidden name (:func:`raw_capture`), redacted into the capture's own name at the device's end, so that
  name never holds a raw capture; one that a killed run left is redacted at the next start
  (:func:`redact_leftovers`).
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .plan import REDACTED_KEYS, Snapshot

if TYPE_CHECKING:
    from govee_ble_local import DeviceState

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
# A MAC-shaped token inside free text: 6 or more hex pairs joined by ONE separator (``:``, ``-``, or
# ``_`` as in BlueZ's ``/org/bluez/hci0/dev_98_17_3C_B1_A2_D1``); 8 pairs is a Govee device id.
_MAC_TOKEN_RE = re.compile(
    r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{2}([:_-])[0-9A-Fa-f]{2}(?:\1[0-9A-Fa-f]{2}){4,}(?![0-9A-Fa-f])")
_HEX4_TAIL_RE = re.compile(r"[0-9A-Fa-f]{4}$")
# A Govee advertised name inside free text, in the shapes ``identify.sku_from_local_name`` parses: a
# brand or ``GBK_`` name (``ihoment_H6188_A2D1``), a ``GVH``/``GVR`` name with an ``_``
# (``GVH6188_1A2B``) or a bare ``GV`` name (``GVH60A67457``, ``GV61881A2B``). Group 1 is the name up
# to the 4 MAC digits it ends in. Only these shapes, so ``color_temp_2700`` is never taken for one.
_NAME_TOKEN_RE = re.compile(
    r"((?:ihoment|Govee|Minger|GBK)_[0-9A-Za-z]+_|(?<![0-9A-Za-z])GV[HR][0-9A-Za-z]*_"
    r"|(?<![0-9A-Za-z])GV[HR]?[0-9A-Za-z]{4})[0-9A-Fa-f]{4}(?![0-9A-Za-z])")
_REDACTED = "<redacted>"


def mask_address(address: str | None) -> str | None:
    """Keep only the vendor prefix (OUI) of a MAC; a macOS CoreBluetooth UUID keeps 8 chars."""
    if not address:
        return address
    if _MAC_RE.match(address):
        sep = address[2]
        return address[:8].upper() + f"{sep}xx" * 3
    return address[:8] + "-…"


def _mac_tail_start(name: str) -> int | None:
    """Where the MAC digits a Govee name ends in start, or None: the last 4 hex digits after an
    ``_`` (``ihoment_H5083_A2D1``, ``GVH6188_1A2B``) or, in a bare ``GV`` name, after the SKU
    (``GV61881A2B``, ``GVH60A67457``; see ``identify.sku_from_local_name``)."""
    m = _HEX4_TAIL_RE.search(name)
    if m is None:
        return None
    start = m.start()
    if name[start - 1:start] == "_" or (name.startswith("GV") and "_" not in name and start >= 6):
        return start
    return None


def mask_name(name: str | None) -> str | None:
    """Mask the MAC digits a Govee advertised name ends in: ``ihoment_H6188_1A2B`` ->
    ``ihoment_H6188_xxxx``; a name without them is returned as-is."""
    if not name:
        return name
    start = _mac_tail_start(name)
    return name if start is None else name[:start] + "xxxx"


def name_without_mac_tail(name: str) -> str:
    """``name`` without the MAC digits it ends in (for substring matching on the rest)."""
    start = _mac_tail_start(name)
    return name if start is None else name[:start]


def _mask_mac_token(m: re.Match[str]) -> str:
    sep = m.group(1)
    pairs = m.group(0).split(sep)
    return sep.join([*(p.upper() for p in pairs[:3]), *("xx" for _ in pairs[3:])])


def _known_forms(address: str | None, names: Iterable[str | None] = ()) -> list[tuple[str, str]]:
    """(exact text, its mask) for the device: ``address`` itself (a macOS UUID too) and, for a MAC,
    its bare-hex form plus the Wi-Fi MAC the device derives from it (the BLE MAC minus one, see
    ``cloud/account.py``) in both forms; then each advertised name in ``names`` that ends in MAC
    digits (:func:`mask_name`), whatever its shape. The separated MAC forms and the usual name shapes
    are also caught by the token passes."""
    forms: list[tuple[str, str]] = []
    if address:
        forms.append((address, mask_address(address) or ""))
    if address and _MAC_RE.match(address):
        sep = address[2]
        value = int(address.replace(sep, ""), 16)
        for mac in (value, value - 1) if value else (value,):
            bare = f"{mac:012X}"
            forms.append((bare, bare[:6] + "x" * 6))
            spelled = sep.join(bare[i:i + 2] for i in range(0, 12, 2))
            forms.append((spelled, mask_address(spelled) or ""))
    for name in names:
        masked = mask_name(name)
        if name and masked and masked != name:
            forms.append((name, masked))
    return forms


def mask_text(text: str, address: str | None = None, names: Iterable[str | None] = (),
              secrets: Iterable[bytes] = ()) -> str:
    """Scrub free text for the report: ``address`` (any case, any form, and its Wi-Fi MAC) and the
    device's advertised ``names``, then every MAC-shaped token, masked to its vendor prefix like
    :func:`mask_address`, and every Govee-name-shaped token, its MAC digits masked like
    :func:`mask_name`; each of ``secrets`` (a plug's account-lock key) as hex, in either case. Idempotent."""
    for secret in secrets:
        if secret:
            text = re.sub(re.escape(secret.hex()), _REDACTED, text, flags=re.IGNORECASE)
    for exact, masked in _known_forms(address, names):   # a name is literal text: escape any "\"
        text = re.sub(re.escape(exact), masked.replace("\\", r"\\"), text, flags=re.IGNORECASE)
    text = _MAC_TOKEN_RE.sub(_mask_mac_token, text)
    return _NAME_TOKEN_RE.sub(r"\1xxxx", text)


def mask_text_all(text: str, devices: Iterable[tuple[str | None, Iterable[str | None]]],
                  secrets: Iterable[bytes] = ()) -> str:
    """:func:`mask_text` for every ``(address, names)`` of ``devices`` (a run's summary names many)."""
    secrets = tuple(secrets)
    for address, names in devices:
        text = mask_text(text, address, names, secrets)
    return mask_text(text, None, (), secrets)


def describe_error(err: BaseException, address: str | None = None,
                   names: Iterable[str | None] = ()) -> str:
    """``<ExceptionType>: <message>`` for the report, scrubbed with :func:`mask_text` (most transport
    errors start with the device's address; connect errors from some bleak-retry-connector releases
    also carry its advertised name)."""
    return mask_text(f"{type(err).__name__}: {err}", address, names)


def snapshot(state: DeviceState, keys: tuple[str, ...], extra: dict[str, Any] | None = None) -> Snapshot:
    """Copy ``keys`` off a device state, plus ``extra`` (computed values such as the device's
    ``active_scene`` and ``segment_count``); identifying keys are never written to the report."""
    snap = {k: getattr(state, k, None) for k in keys}
    snap.update(extra or {})
    for k in REDACTED_KEYS:   # identifying: never written to the report
        if snap.get(k) is not None:
            snap[k] = _REDACTED
    return snap


def json_default(obj: object) -> object:
    """``json.dumps`` default for a report: a ``Segment`` as ``[index, rgb, brightness]``, another dataclass
    as its fields, a set sorted, else its ``str``."""
    if all(hasattr(obj, k) for k in ("index", "rgb", "brightness")):   # a Segment, compactly
        rgb = getattr(obj, "rgb", None)
        return [obj.index, list(rgb) if rgb is not None else None, obj.brightness]  # type: ignore[attr-defined]
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=str)
    return str(obj)


def write_text_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through a fresh temp file in the same directory and ``os.replace``:
    ``mkstemp`` creates its file exclusively (owner-only) and the rename replaces whatever is at
    ``path`` without following it, so a symlink planted in a shared ``--out`` is never written through."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _read_capture(path: Path) -> str:
    """The capture's text; '' unless ``path`` is a regular file (never read through a symlink)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return ""
    with os.fdopen(fd, encoding="utf-8") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            return ""
        return fh.read()


def _blank(plain: str, keep: int) -> str:
    """``plain`` with everything after its first ``keep`` hex digits zeroed."""
    return plain[:keep] + "0" * (len(plain) - keep)


# A status burst's first chunk header, in hex digits: ac 00 <packet count u16le> <lastLen> <cmd> <sub>
# (AbsController4BleSingleSendMultiBack.g). Framing only, so it is kept; every later chunk keeps ac <index>.
_AC_FIRST_HEADER_HEX = 14
_AC_CHUNK_HEADER_HEX = 4


_MASKED_MAC_RE = re.compile(r"^[0-9A-F]{2}([:-])[0-9A-F]{2}\1[0-9A-F]{2}(\1xx){3}$")
# The library writes a device's capture here while it runs (:func:`raw_capture`): a hidden name, so that a
# run killed before its redaction (a SIGKILL, a crash) leaves nothing under the name a tester attaches. The
# next start redacts it into that name (:func:`redact_leftovers`).
RAW_SUFFIX = ".raw"
_LEFTOVER_GLOB = f".*-frames-*.jsonl{RAW_SUFFIX}"


def _mask_addr_field(value: Any, masked: str | None) -> str | None:
    """A record's ``addr`` as written: ``masked`` (the run's device), else the record's own address masked
    (a leftover capture, whose device the next start doesn't know); an address already masked is kept."""
    if masked is not None:
        return masked
    if not isinstance(value, str) or not value:
        return None
    return value if _MASKED_MAC_RE.match(value) or value.endswith("-…") else mask_address(value)


def _redact_line(line: str, masked: str | None) -> str | None:
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    rec: dict[str, Any] = obj
    rec["addr"] = _mask_addr_field(rec.get("addr"), masked)
    rec["wire"] = None   # ciphertext / handshake: the analyzer only reads `plain`
    plain = rec.get("plain") or ""
    if rec.get("dir") == "rx" and plain.lower().startswith("aa07"):
        rec["plain"] = _blank(plain, 6)   # keep header (aa 07 selector)
        rec["redacted"] = "device-info payload"
    elif rec.get("dir") == "rx" and plain.lower().startswith("ac"):
        # A status burst's 0x07 TLV (the serial and Wi-Fi MAC of status-readback devices such as
        # the H60A6) can span chunks, so every data byte is zeroed. The headers stay: ac <index>,
        # plus the first chunk's packet count, lastLen, cmd and sub (its data starts at byte 7).
        first = plain[2:4] == "00"
        rec["plain"] = _blank(plain, _AC_FIRST_HEADER_HEX if first else _AC_CHUNK_HEADER_HEX)
        rec["redacted"] = "status-burst payload"
    return json.dumps(rec)


def redact_frames(path: Path, address: str | None, *, dest: Path | None = None) -> int:
    """Rewrite the frame capture with the address masked, every ``wire`` field dropped, and the
    device-info (aa 07) and status-burst (ac) reply payloads zeroed (they can carry the serial
    number and MACs). A burst's first chunk keeps its 7-byte header (index, packet count, lastLen,
    cmd, sub), the others their ``ac <index>``. ``dest`` (default ``path``): where the redacted capture is
    written; records already there (redacted before) are kept ahead of ``path``'s. ``address`` None (a
    leftover's): each record's own address is masked. Idempotent. Returns the number of records."""
    target = dest or path
    if not os.path.lexists(path):
        return 0
    masked = mask_address(address) if address else None
    lines = _read_capture(path).splitlines()
    if target != path and os.path.lexists(target):
        lines = [*_read_capture(target).splitlines(), *lines]
    out = [r for r in (_redact_line(line, masked) for line in lines) if r is not None]
    write_text_atomic(target, "\n".join(out) + ("\n" if out else ""))
    return len(out)


def raw_capture(path: Path) -> Path:
    """Where the library writes the capture that ends up, redacted, at ``path``."""
    return path.with_name(f".{path.name}{RAW_SUFFIX}")


def redact_capture(path: Path, address: str | None) -> int:
    """Redact the device's raw capture (:func:`raw_capture`) into ``path`` and remove it, so the attachable
    name only ever holds a redacted capture; a capture found at ``path`` itself is redacted in place."""
    raw = raw_capture(path)
    if not os.path.lexists(raw):
        return redact_frames(path, address)
    count = redact_frames(raw, address, dest=path)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(raw)
    return count


def redact_leftovers(out_dir: Path) -> list[Path]:
    """Redact every raw capture a stopped run left in ``out_dir`` (killed before its redaction) into its own
    name, and remove it. Returns the captures written."""
    done: list[Path] = []
    for raw in sorted(out_dir.glob(_LEFTOVER_GLOB)):
        path = raw.with_name(raw.name[1:-len(RAW_SUFFIX)])
        try:
            redact_capture(path, None)
        except OSError:
            continue
        done.append(path)
    return done
