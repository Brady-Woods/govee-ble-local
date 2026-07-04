#!/usr/bin/env python3
"""Split a btsnoop_hci.log into per-device Govee capture files.

Identifies every device in the capture whose advertised local name matches
``--name-pattern`` (default: Govee/ihoment devices), then for each one writes
two files under ``--out-dir``:

- ``<label>_raw.log``       - every ATT packet to/from that device, hex only,
                               one per line, otherwise untouched.
- ``<label>_annotated.log`` - the same packets in two aligned columns: raw
                               hex on the left, this library's best-effort
                               decode on the right (real values, padding
                               noted by length, unknown fields called out
                               explicitly - see decode_btsnoop.py).

Both files collapse a repeating, byte-for-byte-identical heartbeat exchange
to its first occurrence plus a one-line elision note, by default - pass
``--keep-heartbeat`` to disable that and keep every repeat.

Usage:
    python3 tools/extract_govee_session.py btsnoop_hci.log.last --out-dir out/
    python3 tools/extract_govee_session.py btsnoop_hci.log.last --keep-heartbeat
    python3 tools/extract_govee_session.py btsnoop_hci.log.last --name-pattern govee --all
"""
from __future__ import annotations

import argparse
import collections
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from decode_btsnoop import (
    PREFIX_WIDTH,
    BleSessionMap,
    ChunkReassembler,
    DecodedMessage,
    decode_frame,
    decrypt_all,
    format_annotated_line,
    format_left,
    iter_att_events,
    iter_hci_records,
    note as annotate,
)
from decode_btsnoop import PlainEvent  # noqa: E402
from govee_ble_local import messages, profile  # noqa: E402
from govee_ble_local.const import MAX_COLOR_TEMP_KELVIN, MIN_COLOR_TEMP_KELVIN  # noqa: E402

DEFAULT_NAME_PATTERN = r"^gv|govee|ihoment"  # "^gv" catches the "GVH60A6..." advertised-name scheme newer devices use

# Where packaged device profiles live; the default target for --generate-config.
PACKAGED_DEVICES_DIR = Path(__file__).resolve().parent.parent / "src" / "govee_ble_local" / "devices"


@dataclass
class DeviceSession:
    addr: str
    name: str | None
    events: list  # PlainEvent, in chronological order

    @property
    def label(self) -> str:
        addr_tag = self.addr.replace(":", "").lower()[-6:]
        if self.name:
            safe_name = re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_")
            return f"{safe_name}_{addr_tag}"
        return f"unidentified_{addr_tag}"


def _build_session_map(path: str) -> BleSessionMap:
    session = BleSessionMap()
    for rec in iter_hci_records(path):
        if rec.pkt_type == 0x04:
            session.feed_event(rec.body)
    return session


def _group_by_device(path: str, session: BleSessionMap) -> dict[str, DeviceSession]:
    events = list(iter_att_events(path))
    decoded = list(decrypt_all(events))

    by_addr: dict[str, DeviceSession] = {}
    for ev in decoded:
        addr = session.addr_for_handle.get(ev.chandle)
        if addr is None:
            addr = f"handle-{ev.chandle}"  # connection established before capture start
        name = session.name_for_addr.get(addr)
        if addr not in by_addr:
            by_addr[addr] = DeviceSession(addr=addr, name=name, events=[])
        elif name and not by_addr[addr].name:
            by_addr[addr].name = name
        by_addr[addr].events.append(ev)
    return by_addr


def _select_devices(
    by_addr: dict[str, DeviceSession], name_pattern: str, include_all: bool
) -> tuple[list[DeviceSession], list[DeviceSession]]:
    pattern = re.compile(name_pattern, re.IGNORECASE)
    included, excluded = [], []
    for dev in by_addr.values():
        matches = bool(dev.name and pattern.search(dev.name))
        unidentified = dev.name is None
        if include_all or matches or unidentified:
            included.append(dev)
        else:
            excluded.append(dev)
    return included, excluded


# --------------------------------------------------------------------------
# Heartbeat collapsing: an exact-repeat WRITE+NOTIFY pair, back to back,
# byte-for-byte identical to the immediately preceding pair, is elided after
# its first occurrence.
# --------------------------------------------------------------------------


def _elision_note(t_rel: float, count: int, span: float) -> str:
    return f"t+{t_rel:8.3f}s [...]   -- {count} repeat(s) of the heartbeat above elided (spanning {span:.1f}s); pass --keep-heartbeat to see them all --"


def _collapse_heartbeat(events: list, t0: float, keep_heartbeat: bool):
    """Yield (event_or_None, elision_note_or_None) - an elision entry replaces
    a run of exact-repeat pairs with a single synthetic marker line."""
    if keep_heartbeat:
        for ev in events:
            yield ev, None
        return

    i = 0
    n = len(events)
    while i < n:
        ev = events[i]
        # A "pair" is this WRITE immediately followed by a NOTIFY from the same device.
        is_pair_start = ev.direction == "WRITE" and i + 1 < n and events[i + 1].direction == "NOTIFY"
        if not is_pair_start:
            yield ev, None
            i += 1
            continue

        pair = (ev.data, events[i + 1].data)
        yield ev, None
        yield events[i + 1], None
        i += 2

        # Count subsequent exact repeats of this same pair.
        repeat_start_t = None
        repeat_count = 0
        while (
            i + 1 < n
            and events[i].direction == "WRITE"
            and events[i + 1].direction == "NOTIFY"
            and (events[i].data, events[i + 1].data) == pair
        ):
            if repeat_start_t is None:
                repeat_start_t = events[i].t
            repeat_count += 1
            last_t = events[i + 1].t
            i += 2
        if repeat_count:
            yield None, _elision_note(repeat_start_t - t0, repeat_count, last_t - repeat_start_t)


def _resolve_entry(ev: PlainEvent) -> DecodedMessage:
    """Map one PlainEvent to a DecodedMessage (Govee frames via the codec;
    capture-layer housekeeping via annotate())."""
    if ev.status in ("OK", "HANDSHAKE"):
        # Both are decrypted 20-byte Govee frames; the codec identifies them
        # (a HANDSHAKE frame decodes to the understood "handshake" message).
        return decode_frame(ev.data, ev.direction)
    if ev.status == "OTHER":
        # A non-20-byte ATT PDU: standard BLE GATT (service/characteristic
        # discovery, MTU exchange, etc), not a Govee command frame. The
        # opcode itself is known (from the ATT spec) even though we don't
        # decode its payload - that's out of this tool's scope, not an
        # unknown finding.
        known = not ev.opcode_name.startswith("Opcode0x")
        confidence = "confirmed" if known else "unknown"
        return annotate(f"{ev.opcode_name} (standard BLE GATT, not Govee-specific - not decoded): 0x{ev.data.hex()}", confidence)
    return annotate(f"FAILED TO DECRYPT ({ev.opcode_name}): 0x{ev.data.hex()}", "unknown")  # FAIL


def _write_outputs(dev: DeviceSession, out_dir: Path, keep_heartbeat: bool) -> None:
    if not dev.events:
        return
    t0 = dev.events[0].t

    # Pass 1: resolve every entry and measure the widest hex-data column, so
    # the comment column lines up across the whole file - packet length
    # varies (Govee frames are a fixed 20 bytes, but the same capture also
    # carries ordinary variable-length BLE/GATT traffic).
    #
    # Entries are one of: a real packet (t_rel, direction, data, Decoded); a
    # bare str (an elision note - goes in both raw and annotated, since it's
    # about what's *missing* from the raw wire log too); or a 1-tuple (an
    # aggregate reassembly summary - annotated-only, since it's an
    # interpretation of several packets, not a real one on the wire).
    entries: list[tuple[float, str, bytes, DecodedMessage] | str | tuple[str]] = []
    left_width = 0
    reassembler = ChunkReassembler(dev.addr)
    for ev, note in _collapse_heartbeat(dev.events, t0, keep_heartbeat):
        if note is not None:
            entries.append(note)
            continue
        t_rel = ev.t - t0
        decoded = _resolve_entry(ev)
        entries.append((t_rel, ev.direction, ev.data, decoded))
        left_width = max(left_width, len(format_left(t_rel, ev.direction, ev.data)))
        if ev.status == "OK":
            aggregate = reassembler.feed(ev.direction, ev.data)
            if aggregate is not None:
                entries.append((aggregate.summary if aggregate.confidence == "confirmed" else f"[{aggregate.confidence.upper()}] {aggregate.summary}",))
    for aggregate in reassembler.flush():
        marker = "" if aggregate.confidence == "confirmed" else f"[{aggregate.confidence.upper()}] "
        entries.append((f"{marker}{aggregate.summary}",))

    # Pass 2: render both files from the same resolved entries.
    raw_lines, annotated_lines = [], []
    for entry in entries:
        if isinstance(entry, tuple) and len(entry) == 1:
            annotated_lines.append(f"{' ' * PREFIX_WIDTH}^-- {entry[0]}")
            continue
        if isinstance(entry, str):
            raw_lines.append(entry)
            annotated_lines.append(entry)
            continue
        t_rel, direction, data, decoded = entry
        raw_lines.append(format_left(t_rel, direction, data))
        annotated_lines.append(format_annotated_line(t_rel, direction, data, decoded, left_width))

    out_dir.mkdir(parents=True, exist_ok=True)
    header = [
        f"# Device: {dev.name or '(name not resolved in this capture)'}  addr={dev.addr}",
        f"# {len(dev.events)} packets" + ("" if keep_heartbeat else " (repeating heartbeat collapsed - see elision notes; --keep-heartbeat to disable)"),
        "",
    ]
    (out_dir / f"{dev.label}_raw.log").write_text("\n".join(header + raw_lines) + "\n", encoding="utf-8")
    (out_dir / f"{dev.label}_annotated.log").write_text("\n".join(header + annotated_lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Device-config generation from a capture (--generate-config)
# --------------------------------------------------------------------------


def _derive_sku_and_prefix(name: str) -> tuple[str, str] | None:
    """From an advertised local name derive (SKU, local_name_prefix).

    Govee SKUs are ``H`` + 4 alphanumerics; the advertised name embeds it,
    e.g. ``GVH60A67457`` -> (``H60A6``, ``GVH60A6``) and
    ``ihoment_H6006_0EEB`` -> (``H6006``, ``ihoment_H6006``). The prefix is the
    name up to and including the SKU, which is what profile matching keys on.
    """
    match = re.search(r"H[0-9A-Za-z]{4}", name)
    if not match:
        return None
    return match.group().upper(), name[: match.end()]


def _infer_from_capture(dev: DeviceSession) -> tuple[set[str], "collections.Counter[tuple[str, bool]]"]:
    """Walk a device's decoded Govee frames; return (capabilities implied,
    inventory of (message-name, understood) counts)."""
    caps_seen: set[str] = set()
    inventory: collections.Counter[tuple[str, bool]] = collections.Counter()
    for ev in dev.events:
        if ev.status != "OK":
            continue
        msg = messages.deserialize(ev.data, ev.direction)
        inventory[(msg.name, msg.understood)] += 1
        cap = messages.CAPABILITY_BY_MESSAGE.get(msg.name)
        if cap:
            caps_seen.add(cap)
    return caps_seen, inventory


def _render_device_yaml(sku: str, prefix: str, caps_seen: set[str]) -> str:
    lines = [
        f"sku: {sku}",
        f"name: Govee {sku}",
        "",
        "# Auto-generated from a BLE capture by tools/extract_govee_session.py.",
        "# Capabilities reflect the command types actually observed in that",
        "# capture - review before trusting (absence of a capability may just",
        "# mean it wasn't exercised). Scene catalog is fetched separately:",
        f"#   python3 tools/fetch_scene_catalog.py --sku {sku}",
        "",
        "match:",
        f'  local_name_prefixes: ["{prefix}"]',
        "",
        "capabilities:",
        "  brightness: true",
        f"  rgb: {'true' if 'rgb' in caps_seen else 'false'}",
    ]
    if "color_temp" in caps_seen:
        lines.append(f"  color_temp: {{ min_kelvin: {MIN_COLOR_TEMP_KELVIN}, max_kelvin: {MAX_COLOR_TEMP_KELVIN} }}")
    if "zones" in caps_seen:
        lines.append("  zones: [upper, lower]")
    lines.append(f"  segments: {messages.DEFAULT_SEGMENT_COUNT if 'segments' in caps_seen else 0}")
    lines.append(f"  scenes: {'true' if 'scenes' in caps_seen else 'false'}")
    lines += ["", "notes: NOTES.md"]
    return "\n".join(lines) + "\n"


def _render_notes(sku: str, dev: DeviceSession, inventory: "collections.Counter[tuple[str, bool]]") -> str:
    lines = [
        f"# {sku} — device notes (auto-generated)",
        "",
        f"Generated from a BLE capture of `{dev.name}` (`{dev.addr}`) by",
        "`tools/extract_govee_session.py --generate-config`. Review and expand.",
        "",
        "## Observed Govee frame types",
        "",
        "| Message | Understood | Count |",
        "| --- | --- | --- |",
    ]
    for (msg_name, understood), count in sorted(inventory.items(), key=lambda kv: (-kv[1], kv[0][0])):
        lines.append(f"| `{msg_name}` | {'yes' if understood else 'no (stub/unknown)'} | {count} |")
    lines += [
        "",
        "Frame types marked *not understood* are recognized but not acted on;",
        "see `PROTOCOL.md` and `src/govee_ble_local/messages.py`.",
    ]
    return "\n".join(lines) + "\n"


def _report_capability_diff(sku: str, target: Path, caps_seen: set[str]) -> None:
    """Non-destructive: print capture-implied vs declared capabilities for an
    existing device profile."""
    prof = profile.load_profile(target)
    cap = prof.capabilities
    declared = {
        "rgb": cap.rgb,
        "color_temp": cap.color_temp is not None,
        "zones": bool(cap.zones),
        "segments": cap.segments > 0,
        "scenes": cap.scenes,
    }
    print(f"{sku}: profile already exists at {target} - not overwriting. Capability diff:")
    for key, is_declared in declared.items():
        seen = key in caps_seen
        if seen and is_declared:
            mark = "ok"
        elif seen and not is_declared:
            mark = "GAP - capture shows this but device.yaml does not declare it"
        elif not seen and is_declared:
            mark = "(declared; not exercised in this capture)"
        else:
            mark = "(absent both)"
        print(f"  {key:11s} capture={str(seen):5s} declared={str(is_declared):5s}  {mark}")


def generate_config(dev: DeviceSession, config_dir: Path, keep_heartbeat: bool) -> None:
    if not dev.name:
        print(f"{dev.addr}: no advertised name in capture - cannot derive an SKU; skipping.", file=sys.stderr)
        return
    derived = _derive_sku_and_prefix(dev.name)
    if derived is None:
        print(f"{dev.addr}: could not derive an SKU from name {dev.name!r}; skipping.", file=sys.stderr)
        return
    sku, prefix = derived
    caps_seen, inventory = _infer_from_capture(dev)
    target = config_dir / sku.lower()

    if (target / "device.yaml").is_file():
        _report_capability_diff(sku, target, caps_seen)
        return

    (target / "captures").mkdir(parents=True, exist_ok=True)
    (target / "device.yaml").write_text(_render_device_yaml(sku, prefix, caps_seen), encoding="utf-8")
    (target / "NOTES.md").write_text(_render_notes(sku, dev, inventory), encoding="utf-8")
    _write_outputs(dev, target / "captures", keep_heartbeat)
    caps_list = ", ".join(sorted(caps_seen)) or "none observed"
    print(f"{sku}: generated {target}/ (device.yaml, NOTES.md, captures/)")
    print(f"       capabilities observed: {caps_list}")
    print(f"       next: python3 tools/fetch_scene_catalog.py --sku {sku}   # to populate scenes.yaml")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("btsnoop_log", help="path to a btsnoop_hci.log(.last) file")
    ap.add_argument("--out-dir", default="govee_capture_out", help="output directory (default: %(default)s)")
    ap.add_argument("--name-pattern", default=DEFAULT_NAME_PATTERN, help="regex to match advertised device names (default: %(default)r)")
    ap.add_argument("--all", action="store_true", help="include every device seen, ignoring --name-pattern")
    ap.add_argument("--keep-heartbeat", action="store_true", help="do not collapse repeating heartbeat exchanges")
    ap.add_argument(
        "--generate-config",
        nargs="?",
        const=str(PACKAGED_DEVICES_DIR),
        default=None,
        metavar="DEVICES_DIR",
        help="instead of writing capture logs, generate a device profile (device.yaml/NOTES.md/captures) "
        "for each device under DEVICES_DIR (default: the packaged devices/ dir). If a profile already "
        "exists, print a capability diff instead of overwriting.",
    )
    args = ap.parse_args()

    session = _build_session_map(args.btsnoop_log)
    by_addr = _group_by_device(args.btsnoop_log, session)
    included, excluded = _select_devices(by_addr, args.name_pattern, args.all)

    if not included:
        print("No matching devices found.", file=sys.stderr)
        if by_addr:
            print("Devices seen in this capture:", file=sys.stderr)
            for dev in by_addr.values():
                print(f"  {dev.addr}  name={dev.name!r}  packets={len(dev.events)}", file=sys.stderr)
        return 1

    if args.generate_config is not None:
        config_dir = Path(args.generate_config)
        for dev in included:
            generate_config(dev, config_dir, args.keep_heartbeat)
        return 0

    out_dir = Path(args.out_dir)
    for dev in included:
        _write_outputs(dev, out_dir, args.keep_heartbeat)
        print(f"{dev.label}: {len(dev.events)} packets -> {out_dir / (dev.label + '_raw.log')}, {out_dir / (dev.label + '_annotated.log')}")

    if excluded:
        print("\nExcluded (name didn't match --name-pattern):", file=sys.stderr)
        for dev in excluded:
            print(f"  {dev.addr}  name={dev.name!r}  packets={len(dev.events)}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
