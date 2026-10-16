"""Describe + classify a single frame against the Kaitai spec.

One shared implementation used by BOTH the runtime logging (``transport/connection.py``
uses only the label, ``describe_frame(...)[0]``, to name frames in debug logs) and the
offline analyzer (:mod:`govee_ble_local.debug`, the ``govee-ble-analyze`` CLI, which buckets
the issues into a coverage report). It answers two questions about a plaintext frame:

  * a human LABEL (e.g. ``write/mode/color_rgbic_15``, ``ack/mode``, ``read/mode/sub=0x15``,
    ``status-chunk``, ``multi-chunk/0xa4``), and
  * a list of ISSUES ``[(severity, reason)]`` where severity (:data:`Severity`) is one of:
      ``hard``     — a valid frame whose STRUCTURE the spec doesn't represent (real gap:
                     unknown proType/command/sub-mode/notify-sub, a parse failure, or a
                     device write-ACK rejection).
      ``soft``     — a known opcode whose BODY is opaque (raw bytes): the spec models the
                     frame but not this payload.
      ``artifact`` — invalid checksum / wrong length: not a real plaintext frame
                     (ciphertext or corruption), not a spec gap.

Severities are consumed by the analyzer only; the runtime does not map them to log levels.

``0xAC`` status replies are multi-frame bursts and can't be judged one frame at a time —
use :func:`analyze_status_bursts` for those. Each chunk's framing (length, BCC) is still checked
here, and the TX status request is read with the reader.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field
from typing import Any, Literal

from ..const import FRAME_LEN
from ..crypto import checksum_ok
from . import reassemble as _reassemble
from .build import _F, _load
from .parse import _Command, _NotifySub, _ProType, _SubMode

# A frame's direction (the capture's "dir"). Only "rx" changes the reading: an RX 0x33 frame
# is the device's write-ACK echo, not a write command.
Direction = Literal["rx", "tx", "?"]
# Issue severity; see the module docstring.
Severity = Literal["hard", "soft", "artifact"]
Issue = tuple[Severity, str]

# 0xAC first-chunk header [ac, 00, packets u16le, lastLen, cmd, sub] and later-chunk header [ac, index]
# (AbsController4BleSingleSendMultiBack.g / .i): the bytes the capture masking keeps.
_AC_FIRST_HEADER = 7
_AC_CHUNK_HEADER = 2
# Variable-length multi-packet upload chunks (0xA1 / 0xA3 / 0xA4; e.g. the 19-byte 0xA4 END).
_UPLOAD_CHUNK_TYPES = (_ProType.multi_write, _ProType.multi_write_v1, _ProType.multi_write_v2)

# TLV types the reassembled 0xAC status reply is known to carry
# (Compose4BaseInfoSingleRead.u): switch/brightness/mode/device-info/... + zone/seg/colour.
KNOWN_STATUS_REPLY_TLVS: frozenset[int] = frozenset({
    0x00,                                    # trailing zero-pad sentinel
    _reassemble.TYPE_SWITCH, _reassemble.TYPE_BRIGHTNESS, _reassemble.TYPE_MODE,
    _reassemble.TYPE_DEVICE_INFO, _reassemble.TYPE_SLEEP, _reassemble.TYPE_WAKEUP,
    _reassemble.TYPE_TIMERS, _reassemble.TYPE_ZONE, _reassemble.TYPE_BAR_SWITCH,
    _reassemble.TYPE_IC_NUM, _reassemble.TYPE_SEG_INFO, _reassemble.TYPE_COLOR_GROUP,
})


def _opaque(x: object) -> bool:
    """True if the Kaitai reader left this body as raw bytes (no modelled layout)."""
    return isinstance(x, (bytes, bytearray))


def describe_frame(plain: bytes, direction: str = "?") -> tuple[str, list[Issue]]:
    """Return ``(label, issues)`` for one plaintext frame. See the module docstring.

    ``direction`` is a :data:`Direction`; only ``"rx"`` changes the reading."""
    if plain and plain[0] == _ProType.multi_reply_read:
        return _describe_status(plain, direction)
    if plain and plain[0] in _UPLOAD_CHUNK_TYPES and len(plain) != FRAME_LEN:
        return (f"multi-chunk/0x{plain[0]:02x}", [])
    if len(plain) != FRAME_LEN:
        return (f"len={len(plain)}", [("artifact", f"not {FRAME_LEN} bytes (len {len(plain)})")])
    if not checksum_ok(plain):
        return (
            "bad_checksum",
            [("artifact", "invalid BCC — likely undecrypted ciphertext / corrupt")],
        )
    try:
        f = _load(_F.from_bytes(plain))
    except Exception as exc:  # noqa: BLE001 - any reader failure on a valid frame is a spec gap
        return ("parse_error", [("hard", f"kaitai parse failed: {exc!r}")])

    pt = f.pro_type
    if not isinstance(pt, _ProType):
        return (f"proType=0x{plain[0]:02x}", [("hard", f"unknown proType 0x{plain[0]:02x}")])
    if direction == "rx" and pt == _ProType.write:
        return _describe_ack(plain, f.body)
    if pt == _ProType.write:
        return _describe_write(plain, f.body)
    if pt == _ProType.read:
        return _describe_read(plain, f.body)
    if pt == _ProType.notify:
        return _describe_notify(plain, f.body)
    return (pt.name, [])                    # multi_* / handshake: structural only.


def chunk_redacted(chunk: bytes) -> bool:
    """True for a 0xAC reply chunk whose data and BCC were zeroed by the field-test capture masking
    (``tools/field_test/masking.py``: the first chunk keeps its 7-byte header, the others
    ``ac <index>``). Such a chunk fails the checksum but is not corrupt; :func:`_header_redacted` is
    the stricter first-chunk test for a capture masked before the header was kept."""
    keep = _AC_FIRST_HEADER if len(chunk) > 1 and chunk[1] == 0x00 else _AC_CHUNK_HEADER
    return len(chunk) == FRAME_LEN and not any(chunk[keep:])


def _describe_status(plain: bytes, direction: str) -> tuple[str, list[Issue]]:
    """0xAC: framing checked as for any frame (length, BCC: the runtime drops a chunk that fails
    them). A TX frame is the status request, which the spec models (``multi_ac``) and is read with
    the reader; an RX chunk is a burst fragment, not parseable alone
    (:func:`status_burst_report` judges the bursts). A redacted chunk (:func:`chunk_redacted`) is
    labelled as a chunk without an issue."""
    if len(plain) != FRAME_LEN:
        return (f"len={len(plain)}", [("artifact", f"not {FRAME_LEN} bytes (len {len(plain)})")])
    if direction != "tx" and chunk_redacted(plain) and not checksum_ok(plain):
        return ("status-chunk", [])
    if not checksum_ok(plain):
        return ("bad_checksum", [("artifact", "invalid BCC — the runtime drops this 0xAC chunk")])
    if direction != "tx":
        return ("status-chunk", [])
    try:
        req = _load(_F.from_bytes(plain)).body
    except Exception as exc:  # noqa: BLE001 - a request the spec can't read is a gap
        return ("parse_error", [("hard", f"kaitai parse failed on the 0xAC request: {exc!r}")])
    return (f"status-request/{len(req.requested_types)}", [])


def _describe_ack(plain: bytes, body: Any) -> tuple[str, list[Issue]]:
    """RX 0x33: the device's write-ACK echo ``[0x33, command, result, zeros]``; byte2 = result
    (0 = success). The ksy models only the write COMMAND — command vs ack is direction-keyed
    (both are 0x33), so a lone frame can't tell them apart; parse it as an ack here."""
    cmd: Any = getattr(body, "command", None)
    name = cmd.name if isinstance(cmd, _Command) else f"0x{plain[1]:02x}"
    if plain[2] != 0:
        return (f"ack/{name}", [("hard", f"device REJECTED {name} write (result 0x{plain[2]:02x})")])
    return (f"ack/{name}", [])


def _describe_write(plain: bytes, body: Any) -> tuple[str, list[Issue]]:
    """0x33 write command: label by command (and mode sub-mode); opaque payloads are soft."""
    cmd: Any = getattr(body, "command", None)
    if not isinstance(cmd, _Command):
        return (f"write/cmd=0x{plain[1]:02x}", [("hard", f"unknown command 0x{plain[1]:02x} (write)")])
    label = f"write/{cmd.name}"
    issues: list[Issue] = []
    params: Any = getattr(body, "params", None)
    if cmd == _Command.mode:
        st: Any = getattr(params, "sub_type", None)
        if isinstance(st, _SubMode):
            label += f"/{st.name}"
            if _opaque(getattr(params, "params", None)):
                issues.append(("soft", f"write mode/{st.name} payload not modelled"))
        elif st is not None:
            return (
                f"write/mode/sub=0x{plain[2]:02x}",
                [("hard", f"unknown mode sub_type 0x{plain[2]:02x}")],
            )
    elif _opaque(params):
        issues.append(("soft", f"write {cmd.name} payload not modelled"))
    return (label, issues)


def _describe_read(plain: bytes, body: Any) -> tuple[str, list[Issue]]:
    """0xAA read request / reply: label by command (and mode selector); opaque replies are soft."""
    cmd: Any = getattr(body, "command", None)
    if not isinstance(cmd, _Command):
        return (f"read/cmd=0x{plain[1]:02x}", [("hard", f"unknown command 0x{plain[1]:02x} (read)")])
    label = f"read/{cmd.name}"
    issues: list[Issue] = []
    rb: Any = getattr(body, "body", None)
    if cmd == _Command.mode:                       # mode_read: 0x15/0x13 typed; others opaque
        sel = getattr(rb, "selector_or_sub_mode", None)
        if sel is not None:
            label += f"/sub=0x{sel:02x}"
        # 0x01 = request selector (no reply body); 0x04 = scene code (read in wire.parse)
        if _opaque(getattr(rb, "rest", None)) and sel not in (0x01, 0x04):
            issues.append(("soft", f"mode read sub 0x{sel:02x} reply not modelled"))
    elif _opaque(rb):                              # e.g. 0xa2 BulbGroupColor (mechanism B)
        issues.append(("soft", f"read {cmd.name} reply not modelled (opaque)"))
    return (label, issues)


def _describe_notify(plain: bytes, body: Any) -> tuple[str, list[Issue]]:
    """0xEE device push: label by sub-type; an opaque payload is soft."""
    st: Any = getattr(body, "sub_type", None)
    if not isinstance(st, _NotifySub):
        return (f"notify/sub=0x{plain[1]:02x}", [("hard", f"unknown notify sub_type 0x{plain[1]:02x}")])
    issues: list[Issue] = []
    if _opaque(getattr(body, "data", None)):
        issues.append(("soft", f"notify {st.name} payload not modelled"))
    return (f"notify/{st.name}", issues)


@dataclass
class StatusBurstReport:
    """What :func:`status_burst_report` found in a capture's RX ``0xAC`` chunks."""
    tlv_types: collections.Counter[int] = field(default_factory=collections.Counter)
    gaps: list[str] = field(default_factory=list)
    incomplete: int = 0    # not walked: a chunk / the last packet the first chunk declares is missing
    conflicting: int = 0   # not walked: a chunk index arrived twice with different bytes
    truncated: int = 0     # walked up to the cut: a complete burst whose TLV stream overruns it
    #                        (the runtime, like the app, uses NO state from such a burst)
    redacted: int = 0      # never walked, framing judged by index only: payload zeroed by the masking

    @property
    def malformed(self) -> int:
        """Bursts that were NOT walked (dropped / merged chunks): ``incomplete + conflicting``.
        ``truncated`` bursts were walked (their TLV types are counted), so they are not in it, and
        neither is a ``redacted`` burst whose chunk indices are all there."""
        return self.incomplete + self.conflicting


def _split_bursts(rx_ac: list[bytes]) -> list[list[bytes]]:
    """Group a capture's 0xAC chunks into bursts: a chunk with index 0x00 opens one, the 0xFF
    chunk closes it. A byte-identical repeat of the first chunk is the device's double delivery
    (``ac00 ac00 ac01 ac01 ...``) only while the open burst holds nothing but copies of it; once a
    later chunk has arrived, an 0x00 chunk opens a new burst. Two polls of an unchanged light share
    their first chunk byte for byte, so a poll that lost its terminator is not folded into the
    next. A record too short to be a chunk is skipped, as the runtime reassembler skips it
    (:func:`.reassemble.burst_chunks`).

    Residual case: a first chunk double-delivered AFTER interior chunks (``00 01 02 00 03 .. FF``)
    opens a new burst here, so one read counts as two incomplete bursts, although the runtime (one
    query) and the app (isSameController ignores the repeated index 0) decode it. Without the TX
    request boundaries in this list, a late repeat can't be told from the next poll's first chunk."""
    bursts: list[list[bytes]] = []
    cur: list[bytes] = []
    for fr in rx_ac:
        if len(fr) < _reassemble.MIN_CHUNK_LEN:
            continue
        idx = fr[1]
        if idx == 0x00:                 # start of a burst
            if cur and all(c == fr for c in cur):   # double-delivered first chunk
                cur.append(fr)
                continue
            if cur:                     # previous burst never terminated: judged on its own
                bursts.append(cur)
            cur = [fr]
        elif idx == 0xFF:               # terminator closes the burst
            if cur:
                cur.append(fr)
                bursts.append(cur)
                cur = []
            # stray terminator with no open burst (or its double delivery) -> ignore
        elif cur:                       # interior chunk of an open burst
            cur.append(fr)
    if cur:                             # trailing chunks with no terminator
        bursts.append(cur)
    return bursts


def _header_redacted(first: bytes) -> bool:
    """True for a first chunk whose bytes after ``ac 00`` are all zero, as the field-test capture
    masking wrote every RX 0xAC chunk before it kept the first chunk's header
    (``tools/field_test/masking.py``: ``redact_frames`` kept only ``ac <index>``; it now keeps the
    7-byte header, so such a capture is judged by the packet count again). A device never sends one:
    a packet count of 0 never completes in the app (AbsController4BleSingleSendMultiBack.isSameController
    takes the 0xFF chunk only at index count-1), and the chunk's BCC would be 0xAC, not 0. A real first
    chunk with a corrupt count but any other byte set is not matched, so it is still judged by the app's
    rule (incomplete)."""
    return not any(first[2:])


def _redacted_framing(burst: list[bytes]) -> tuple[bool, int]:
    """``(complete, conflicts)`` for a burst whose header was redacted (:func:`_header_redacted`),
    by chunk index alone: indices 0x00..k with no gap, then 0xFF, and no other index. Offline
    analysis only (the runtime never sees a redacted capture): without it every status burst in a
    field-test report would read as dropped. An interior gap or a lost terminator still shows; a
    lost chunk just before 0xFF can't, since only the redacted packet count would reveal it."""
    unique: dict[int, bytes] = {}
    conflicts = 0
    for fr in burst:
        if unique.setdefault(fr[1], fr) != fr:
            conflicts += 1
    run = 0
    while run in unique and run != 0xFF:
        run += 1
    return 0xFF in unique and len(unique) == run + 1, conflicts


def status_burst_report(rx_ac: list[bytes]) -> StatusBurstReport:
    """Group RX ``0xAC`` reply chunks into bursts and walk each complete reassembled TLV stream.

    A status burst is the index sequence ``ac 00, ac 01, … ac FF`` (byte1 = chunk index;
    0xFF = terminator). Grouping keys on that index — a new ``index==0x00`` starts a fresh
    burst, ``0xFF`` closes it — so a dropped terminator (which would otherwise concatenate
    two reads and drift the walk into phantom TLVs) does not merge two reads. The exceptions
    (see :func:`_split_bursts`): a read that lost everything after its first chunk (the next read's
    identical first chunk can't be told from a double delivery), and a first chunk repeated after
    interior chunks, which splits one read into two incomplete bursts. Each burst is then judged
    by the runtime's own rule (:func:`.reassemble.burst_chunks`: the first chunk's packet count,
    chunks de-duplicated) and decoded by the runtime's own walker
    (:func:`.reassemble.walk_status_tlvs`). Stricter than the runtime, which decodes the
    contiguous prefix of any burst: only complete bursts without conflicting duplicate chunks
    are walked. In those, a TLV type outside :data:`KNOWN_STATUS_REPLY_TLVS`, or a whole TLV its
    spec type can't read (including a 0x07 block the walk steps past, as the app does), is a real
    spec gap; so is a TLV whose len byte the app reads as negative (0x80 or more). A complete burst
    whose TLV stream overruns it is walked up to the cut and counted in
    :attr:`StatusBurstReport.truncated`: the runtime uses no state from it.

    A burst from a redacted field-test capture (first chunk zeroed past ``ac 00``,
    :func:`_header_redacted`) has no packet count and no payload: it is counted in
    :attr:`StatusBurstReport.redacted`, judged by its chunk indices (:func:`_redacted_framing`)
    and never walked. A chunk that fails the checksum is left out, as the runtime drops it
    (:func:`describe_frame` reports it as an artifact)."""
    report = StatusBurstReport()
    # The runtime drops a chunk that fails the checksum before reassembly (connection._handle_notify),
    # so the bursts are judged without them; a redacted chunk's zeroed BCC is not a failure.
    rx_ac = [fr for fr in rx_ac if checksum_ok(fr) or chunk_redacted(fr)]
    for burst in _split_bursts(rx_ac):
        redacted = _header_redacted(burst[0])   # burst[0] is always its index-0x00 chunk
        if redacted:
            report.redacted += 1
            complete, conflicts = _redacted_framing(burst)
        else:
            _chunks, complete, conflicts = _reassemble.burst_chunks(burst)
        if conflicts:
            report.conflicting += 1
            continue
        if not complete:
            report.incomplete += 1
            continue
        if redacted:                            # payload zeroed: no TLVs to walk
            continue
        skipped: list[_reassemble.TlvCut] = []
        tlvs, cut = _reassemble.walk_status_tlvs(_reassemble.reassemble(burst), skipped=skipped)
        for tlv in tlvs:
            t = int(tlv.type)
            report.tlv_types[t] += 1
            if t not in KNOWN_STATUS_REPLY_TLVS:  # unknown type -> value falls to raw bytes
                report.gaps.append(f"0xAC reply TLV type 0x{t:02x} not modelled")
        unread = [*skipped, cut] if cut is not None and cut.reason == "unparsed" else skipped
        for bad in unread:
            report.tlv_types[bad.type] += 1
            if bad.length is not None and bad.length >= _reassemble.SIGNED_LEN_MIN:
                report.gaps.append(
                    f"0xAC reply TLV type 0x{bad.type:02x} has len 0x{bad.length:02x}: the app reads a "
                    "len of 0x80 or more as negative and fails the read")
            else:
                report.gaps.append(
                    f"0xAC reply TLV type 0x{bad.type:02x} (len {bad.length}) did not parse against the spec")
        if cut is not None and cut.reason == "truncated":
            report.truncated += 1
    return report


def analyze_status_bursts(
    rx_ac: list[bytes],
) -> tuple[collections.Counter[int], list[str], int]:
    """:func:`status_burst_report` as ``(tlv_type_counts, gaps, malformed_burst_count)``.

    ``malformed_burst_count`` is :attr:`StatusBurstReport.malformed`: the bursts that were not
    walked (missing chunks, conflicting duplicates). A complete burst whose TLV stream overruns
    it is walked up to the cut and counted only in :attr:`StatusBurstReport.truncated`; a redacted
    burst whose chunk indices are all there only in :attr:`StatusBurstReport.redacted`."""
    report = status_burst_report(rx_ac)
    return report.tlv_types, report.gaps, report.malformed
