"""Privacy masking for everything the field test writes (reports and frame captures).

Both output files are meant to be attached to a public GitHub issue, so the Bluetooth address is
masked to its vendor prefix, and the serial number / Wi-Fi MAC are removed from state snapshots and
from device-info (``aa 07``) reply frames. Advertised names are kept as-is.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .plan import REDACTED_KEYS

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
_REDACTED = "<redacted>"


def mask_address(address: str | None) -> str | None:
    """Keep only the vendor prefix (OUI) of a MAC; a macOS CoreBluetooth UUID keeps 8 chars."""
    if not address:
        return address
    if _MAC_RE.match(address):
        sep = address[2]
        return address[:8].upper() + f"{sep}xx" * 3
    return address[:8] + "-…"


def snapshot(state: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    """Copy ``keys`` off a device state; identifying keys are never written to the report."""
    snap = {k: getattr(state, k, None) for k in keys}
    for k in REDACTED_KEYS:   # identifying: never written to the report
        if snap.get(k) is not None:
            snap[k] = _REDACTED
    return snap


def redact_frames(path: Path, address: str) -> int:
    """Rewrite the frame capture with the address masked and device-info (aa 07) reply payloads
    zeroed (they can carry the serial number and MACs). Returns the number of records."""
    if not path.exists():
        return 0
    masked = mask_address(address)
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        rec["addr"] = masked
        plain = rec.get("plain") or ""
        if rec.get("dir") == "rx" and plain.lower().startswith("aa07"):
            rec["plain"] = plain[:6] + "0" * (len(plain) - 6)   # keep header (aa 07 selector)
            rec["wire"] = None
            rec["redacted"] = "device-info payload"
        out.append(json.dumps(rec))
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    os.replace(tmp, path)
    return len(out)
