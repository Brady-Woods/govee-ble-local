"""Reassemble multi-packet 0xAC status bursts, then parse them via the spec model.

Only the cross-frame de-chunk is hand-done (Kaitai can't join frames): the FIRST frame
contributes its data at offset 7 (12 bytes), subsequent frames at offset 2 (17 bytes),
through the 0xFF terminator (chunks de-duplicated + index-ordered — devices double-deliver).
The joined buffer is then parsed straight from the ksy ``status_reply`` type (generated
reader) — a ``[type, len, value]`` TLV stream (Compose4BaseInfoSingleRead.u) that types
every value (switch / brightness / zone / seg-info / colour groups / 0x07 device-info) and
terminates on the trailing zero pad. The offline analyzer (:mod:`.describe`) parses the same
buffer with the same generated reader and flags any TLV type outside the modelled set.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from io import BytesIO as _BytesIO
from typing import Any

from kaitaistruct import KaitaiStream as _KaitaiStream

from ..models import Segment
from .._generated.govee_ble_frame import GoveeBleFrame as _GBF  # type: ignore[attr-defined]
from . import parse

_LOGGER = logging.getLogger(__name__)
_F: Any = _GBF   # generated reader (untyped -> Any so attribute chains don't need stubs)
_RECORDS_PER_GROUP = 4   # oneGroupColorSize: index stride, not the (len-derived) record count

TYPE_SWITCH = 0x01
TYPE_BRIGHTNESS = 0x04
TYPE_DEVICE_INFO = 0x07
TYPE_ZONE = 0x30
TYPE_SEG_INFO = 0x41
TYPE_COLOR_GROUP = 0xA5


@dataclass
class StatusReply:
    """Device state decoded from a reassembled 0xAC status reply."""
    is_on: bool | None = None
    brightness: int | None = None            # raw reported value: 0..100 on curated SKUs (brightness_max)
    zone_power: dict[int, bool] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)
    # Device-info from the 0x07 TLV (BLE-only devices report it ONLY here — was the MAC-anchor).
    serial_number: str | None = None
    wifi_mac: str | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None


def _ordered_chunks(frames: list[bytes]) -> tuple[list[bytes], bool]:
    """Dedupe chunks by index (first wins), order them, and keep only the
    CONTIGUOUS prefix 0x00, 0x01, ... (plus the 0xFF terminator when nothing is
    missing). Returns (chunks, complete). The join is offset-by-position, so a
    chunk after a gap would splice in at the wrong offset and the TLV walk would
    decode garbage as real state - everything from the first gap on is dropped."""
    unique: dict[int, bytes] = {}
    for fr in frames:
        if len(fr) < 19:
            _LOGGER.debug("status burst: skipping short chunk (%d bytes)", len(fr))
            continue
        unique.setdefault(fr[1], fr)
    ordered: list[bytes] = []
    index = 0
    while index in unique and index != 0xFF:
        ordered.append(unique[index])
        index += 1
    complete = 0xFF in unique and len(ordered) == len(unique) - 1
    if complete:
        ordered.append(unique[0xFF])
    elif unique:
        _LOGGER.debug(
            "status burst incomplete: chunks %s, using contiguous prefix of %d",
            sorted(unique), len(ordered),
        )
    return ordered, complete


def reassemble(frames: list[bytes]) -> bytes:
    """Join a 0xAC burst: first chunk's data @ offset 7, the rest @ offset 2.

    The burst is keyed by the chunk index at byte 1 (``ac 00, ac 01, … ac FF``).
    Duplicates collapse to one per index; only the contiguous prefix is joined
    (see :func:`_ordered_chunks`)."""
    ordered, _complete = _ordered_chunks(frames)
    buf = bytearray()
    for i, fr in enumerate(ordered):
        buf += fr[7:19] if i == 0 else fr[2:19]
    return bytes(buf)


def _complete_tlvs(buf: bytes) -> bytes:
    """Truncate a (possibly cut-short) TLV stream at the last whole TLV and
    terminate it, so the spec reader can type everything that did arrive."""
    pos = 0
    while pos + 2 <= len(buf):
        typ, length = buf[pos], buf[pos + 1]
        if typ == 0:
            break
        if pos + 2 + length > len(buf):
            break
        pos += 2 + length
    return buf[:pos] + b"\x00\x00"


def parse_status(frames: list[bytes]) -> StatusReply:
    """Reassemble a 0xAC burst and decode it via the generated ``status_reply`` reader.

    Only the cross-frame de-chunk (:func:`reassemble`) is hand-done — Kaitai can't join
    frames. The reassembled buffer is then parsed straight from the spec model: the ksy
    ``status_reply`` walks the TLV stream (``repeat: until type == 0`` handles the trailing
    zero pad) and types every value — switch / brightness / zone / seg-info / colour groups /
    ``0x07`` device-info (== ``device_info_read``). No client-side TLV or value hand-parsing.

    Segment global index = ``(group_index-1) * 4 + k`` (oneGroupColorSize stride; the per-group
    record COUNT comes from the TLV len). Device-info: first non-None wins (basic 0x10 precedes
    wifi 0x11), giving wifi_mac / hw / sw / serial for BLE-only devices (H60A6) from the spec."""
    st = StatusReply()
    buf = reassemble(frames)
    try:
        reply = _F.StatusReply(_KaitaiStream(_BytesIO(buf)))
    except Exception:  # noqa: BLE001 - truncated burst (lost tail chunk / terminator)
        trimmed = _complete_tlvs(buf)
        try:
            reply = _F.StatusReply(_KaitaiStream(_BytesIO(trimmed)))
        except Exception as exc:  # noqa: BLE001 - malformed -> empty state
            _LOGGER.debug("status reply did not parse (%d bytes): %r", len(buf), exc)
            return st
        _LOGGER.debug("status reply truncated; decoded %d of %d bytes", len(trimmed) - 2, len(buf))

    segs: list[Segment] = []
    for tlv in reply.tlvs:
        typ = int(tlv.type)
        if typ == 0:                       # trailing zero-pad sentinel
            continue
        v = tlv.value
        if typ == TYPE_SWITCH:
            st.is_on = bool(v.state)
        elif typ == TYPE_BRIGHTNESS:
            st.brightness = int(v.brightness)
        elif typ == TYPE_ZONE:
            st.zone_power = {0: bool(v.zone_a), 1: bool(v.zone_b)}
        elif typ == TYPE_COLOR_GROUP:
            base = parse.group_base_index(int(v.group_index), _RECORDS_PER_GROUP)
            if base is None:
                continue
            for k, rec in enumerate(v.records):
                segs.append(Segment(
                    index=base + k, rgb=(int(rec.r), int(rec.g), int(rec.b)),
                    brightness=int(rec.brightness),
                ))
        elif typ == TYPE_DEVICE_INFO:
            info = parse.device_info_from(int(v.selector), v.info)
            if info is not None:
                if info.serial is not None and st.serial_number is None:
                    st.serial_number = info.serial
                if info.wifi_mac is not None and st.wifi_mac is None:
                    st.wifi_mac = info.wifi_mac
                if info.sw_version is not None and st.firmware_version is None:
                    st.firmware_version = info.sw_version
                if info.hw_version is not None and st.hardware_version is None:
                    st.hardware_version = info.hw_version
    st.segments = parse.merge_segments(segs)
    return st
