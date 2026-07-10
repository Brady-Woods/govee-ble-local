"""Reassemble multi-packet 0xAC status bursts and walk the reply's TLV stream.

Cross-frame de-chunking isn't expressible in Kaitai, so the client joins the burst
first — the FIRST frame contributes its data at offset 7 (12 bytes), subsequent frames
at offset 2 (17 bytes), through the 0xFF terminator — then walks the buffer as a
``[type, len, value]`` TLV stream (Compose4BaseInfoSingleRead.u). Known reply types:
0x01 switch, 0x04 brightness, 0x30 zone (2 on/off bits), 0x41 seg/IC info, and 0xA5
colour groups (``[group_index, records×4B [brightness,R,G,B]]``, spec color_group_read).
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from ..models import Segment

TYPE_SWITCH = 0x01
TYPE_BRIGHTNESS = 0x04
TYPE_ZONE = 0x30
TYPE_SEG_INFO = 0x41
TYPE_COLOR_GROUP = 0xA5


@dataclass
class StatusReply:
    """Device state decoded from a reassembled 0xAC status reply."""
    is_on: bool | None = None
    brightness: int | None = None            # raw 0-255 (percent mapping is UI-layer)
    zone_power: dict[int, bool] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)


def reassemble(frames: list[bytes]) -> bytes:
    """Join a 0xAC burst: first chunk's data @ offset 7, the rest @ offset 2.

    The burst is keyed by the chunk index at byte 1 (``ac 00, ac 01, … ac FF``).
    Devices double-deliver each notification (duplicate GATT callbacks), so chunks
    are first collapsed to one per index (first wins) and ordered by index — the
    join is offset-by-*position*, so an un-deduped duplicate would splice in at the
    wrong offset and drift the TLV walk. 0xFF (terminator) sorts last naturally."""
    unique: dict[int, bytes] = {}
    for fr in frames:
        if len(fr) < 19:
            continue
        unique.setdefault(fr[1], fr)
    ordered = [unique[k] for k in sorted(unique)]
    buf = bytearray()
    for i, fr in enumerate(ordered):
        buf += fr[7:19] if i == 0 else fr[2:19]
    return bytes(buf)


def walk_tlvs(buf: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield (type, value) for each ``[type, len, value]`` TLV; stop at zero padding."""
    i, n = 0, len(buf)
    while i + 2 <= n:
        t, ln = buf[i], buf[i + 1]
        if t == 0 and ln == 0:            # trailing zero pad
            break
        val = buf[i + 2 : i + 2 + ln]
        if len(val) < ln:                 # truncated tail
            break
        yield t, val
        i += 2 + ln


def _add_color_group(val: bytes, segs: dict[int, Segment]) -> None:
    """0xA5 group: value[0] = 1-based group_index, then 4-byte [brightness,R,G,B] records."""
    if not val:
        return
    gi = val[0]
    recs = val[1:]
    for k in range(len(recs) // 4):
        brightness, r, g, b = recs[k * 4 : k * 4 + 4]
        idx = 4 * (gi - 1) + k
        segs[idx] = Segment(index=idx, rgb=(r, g, b), brightness=brightness)


def parse_status(frames: list[bytes]) -> StatusReply:
    """Reassemble a 0xAC burst and decode switch / brightness / zone / segment colours."""
    st = StatusReply()
    segs: dict[int, Segment] = {}
    for t, val in walk_tlvs(reassemble(frames)):
        if t == TYPE_SWITCH and val:
            st.is_on = bool(val[0])
        elif t == TYPE_BRIGHTNESS and val:
            st.brightness = val[0]
        elif t == TYPE_ZONE and len(val) >= 2:
            st.zone_power = {0: bool(val[0]), 1: bool(val[1])}
        elif t == TYPE_COLOR_GROUP:
            _add_color_group(val, segs)
    st.segments = [segs[k] for k in sorted(segs)]
    return st
