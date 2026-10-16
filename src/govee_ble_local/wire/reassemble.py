"""Reassemble multi-packet 0xAC status bursts, then parse them via the spec model.

Only the cross-frame de-chunk is hand-done (Kaitai can't join frames). It follows the app's
``AbsController4BleSingleSendMultiBack`` (``.g`` / ``.i`` / ``.h`` / ``.isSameController``): the
FIRST chunk (index 0x00) is ``[0xAC, 0x00, packets u16le, lastLen, cmd, sub, <12 data bytes>]``,
every later chunk carries 17 data bytes at offset 2, and the last packet (index 0xFF) carries only
``lastLen`` of them. A one-packet reply has no 0xFF chunk: its ``lastLen`` data bytes sit at offset
7. Chunks are de-duplicated and index-ordered (devices double-deliver). A burst is complete only when
every packet the first chunk declares arrived, as in the app; an incomplete burst is joined only up
to its first missing chunk, never past it.

The joined buffer is then parsed straight from the ksy ``status_reply`` type (generated
reader) — a ``[type, len, value]`` TLV stream (Compose4BaseInfoSingleRead.u) that types
every value (switch / brightness / zone / seg-info / colour groups / 0x07 device-info). The
offline analyzer (:mod:`.describe`) judges and walks each captured burst with the same helpers
(:func:`burst_chunks`, :func:`walk_status_tlvs`) and adds its stricter verdicts on top.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from ..const import FRAME_LEN
from ..models import Segment
from . import parse
from .build import INFO_BASIC, INFO_WIFI
from .parse import _F, _parse_type

_LOGGER = logging.getLogger(__name__)
_RECORDS_PER_GROUP = 4   # oneGroupColorSize: index stride, not the (len-derived) record count

# Chunk layout (AbsController4BleSingleSendMultiBack: k() = 7, a() = 12, i() copies 17 from offset 2).
_FIRST_DATA_OFFSET = 7
_FIRST_DATA_LEN = 12
_DATA_OFFSET = 2
_DATA_LEN = 17
_DATA_END = FRAME_LEN - 1   # 19: the BCC byte follows the data
_LAST_INDEX = 0xFF          # index byte of the last packet (isSameController: bytes[1] == -1)
MIN_CHUNK_LEN = _DATA_END   # a shorter record can't hold a whole chunk: skipped
# Compose4BaseInfoSingleRead.u reads a TLV's len byte signed (`int i10 = it2[i7]` from a byte[]):
# from this value up it is negative, `new byte[i10]` throws and the app's whole read fails.
SIGNED_LEN_MIN = 0x80

TYPE_SWITCH = 0x01
TYPE_BRIGHTNESS = 0x04
TYPE_MODE = 0x05
TYPE_DEVICE_INFO = 0x07
TYPE_SLEEP = 0x11
TYPE_WAKEUP = 0x12
TYPE_TIMERS = 0x23
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


@dataclass(frozen=True)
class TlvCut:
    """A TLV :func:`walk_status_tlvs` could not take: where and why it stopped before the end of the
    buffer, or (in its ``skipped`` list) a TLV it stepped past."""
    # "truncated": the TLV runs past the buffer. "unparsed": the app's walker throws on it - its typed
    # body didn't parse, or its len byte is SIGNED_LEN_MIN or more (read as negative).
    reason: Literal["truncated", "unparsed"]
    type: int                                  # the TLV type byte at the cut
    offset: int                                # its offset in the reassembled buffer
    length: int | None = None                  # its declared len (None when the len byte is missing)


def _header(first: bytes) -> tuple[int, int]:
    """``(packet count, lastLen)`` from the first chunk: allBackPkgs = u16le at bytes 2..3
    (``BleUtils.H(.., false)``) and endPkgLen at byte 4 (AbsController4BleSingleSendMultiBack.g)."""
    return first[2] | (first[3] << 8), first[4]


def burst_chunks(frames: Iterable[bytes]) -> tuple[list[bytes], bool, int]:
    """De-duplicate a burst's chunks by index (first copy wins), order them and judge completeness.

    Returns ``(chunks, complete, conflicts)``: ``chunks`` is the contiguous prefix 0x00, 0x01, ...
    plus the last packet when the burst is complete; ``conflicts`` counts chunk indices that arrived
    again with DIFFERENT bytes. The join is offset-by-position, so a chunk after a gap would splice in
    at the wrong offset and the TLV walk would decode garbage as real state: everything from the
    first gap on is dropped.

    Completeness is the app's rule (AbsController4BleSingleSendMultiBack.isSameController / .onResult):
    the first chunk declares the packet count, and the last packet (index 0xFF, or index count-1,
    which the app accepts too) is taken only once packets 0..count-2 are all in. So a burst that lost
    the chunks just before 0xFF, or a lone 0xFF chunk, is incomplete. A one-packet reply (count 1) is
    complete by itself (``g()`` hands it straight to ``l()``, no 0xFF chunk). A lastLen too long for
    its packet makes the app reject the whole burst before it copies a byte (``g()`` returns -1 and
    the read fails): no chunks, not complete.

    Leniency the app does not have: chunks are put back in index order. isSameController takes a
    chunk only when its index is the next one expected (``backPos``), so in the app a chunk that
    arrives early is ignored and the read then times out; here a burst whose chunks all arrived, in
    any order, is complete."""
    unique: dict[int, bytes] = {}
    conflicts = 0
    for fr in frames:
        if len(fr) < MIN_CHUNK_LEN:
            _LOGGER.debug("status burst: skipping short chunk (%d bytes)", len(fr))
            continue
        kept = unique.setdefault(fr[1], fr)
        if kept is not fr and kept != fr:
            conflicts += 1
            _LOGGER.debug("status burst: chunk 0x%02x arrived again with different bytes; "
                          "keeping the first copy", fr[1])
    first = unique.get(0)
    if first is None:
        if unique:
            _LOGGER.debug("status burst incomplete: chunks %s, no first chunk", sorted(unique))
        return [], False, conflicts
    total, last_len = _header(first)
    if last_len > (_FIRST_DATA_LEN if total == 1 else _DATA_LEN):
        _LOGGER.debug("status burst rejected: lastLen %d does not fit its packet (%d declared)",
                      last_len, total)
        return [], False, conflicts
    if total == 1:
        return [first], True, conflicts
    # Packets 0..total-2 come before the last one; a corrupt count (0) can't bound the prefix.
    stop = total - 1 if total >= 2 else _LAST_INDEX
    ordered: list[bytes] = []
    index = 0
    while index < stop and index in unique and index != _LAST_INDEX:
        ordered.append(unique[index])
        index += 1
    last = unique.get(_LAST_INDEX, unique.get(total - 1)) if total >= 2 else None
    complete = last is not None and len(ordered) == total - 1
    if complete and last is not None:
        ordered.append(last)
    else:
        _LOGGER.debug(
            "status burst incomplete: chunks %s of %d declared, using contiguous prefix of %d",
            sorted(unique), total, len(ordered),
        )
    return ordered, complete, conflicts


def _join(chunks: list[bytes], complete: bool) -> bytes:
    """Concatenate the data regions of :func:`burst_chunks`'s chunks. A complete burst ends with the
    last packet's lastLen bytes (``.h()``); an incomplete one with its last whole chunk."""
    if not chunks:
        return b""
    first = chunks[0]
    total, last_len = _header(first)
    if total == 1:   # one-packet reply (always complete): lastLen bytes at offset 7 (g(), allBackPkgs 1)
        return bytes(first[_FIRST_DATA_OFFSET:_FIRST_DATA_OFFSET + last_len])
    buf = bytearray(first[_FIRST_DATA_OFFSET:_DATA_END])
    for fr in chunks[1:-1] if complete else chunks[1:]:
        buf += fr[_DATA_OFFSET:_DATA_END]
    if complete:
        buf += chunks[-1][_DATA_OFFSET:_DATA_OFFSET + last_len]
    return bytes(buf)


def _joined(frames: list[bytes]) -> tuple[bytes, bool]:
    """``(buffer, complete)`` for a burst: :func:`burst_chunks` then :func:`_join`."""
    chunks, complete, _conflicts = burst_chunks(frames)
    return _join(chunks, complete), complete


def reassemble(frames: list[bytes]) -> bytes:
    """Join a 0xAC burst: first chunk's data @ offset 7, the rest @ offset 2.

    The burst is keyed by the chunk index at byte 1 (``ac 00, ac 01, … ac FF``).
    Duplicates collapse to one per index; only the contiguous prefix is joined
    (see :func:`burst_chunks`)."""
    return _joined(frames)[0]


def _app_skips(typ: int, value: bytes) -> bool:
    """True for a TLV the app's walker steps past without reading its body: a 0x07 block whose
    selector (value byte 0) is neither basic (0x10) nor Wi-Fi (0x11). Compose4BaseInfoSingleRead.w
    reads ``bArr[0]``, logs any other selector ("parse07Info() 其他信息") and returns, so ``u()``
    walks on. An empty 0x07 value has no selector: ``bArr[0]`` throws there, so it is not skipped."""
    return typ == TYPE_DEVICE_INFO and len(value) >= 1 and value[0] not in (INFO_BASIC, INFO_WIFI)


def walk_status_tlvs(
    buf: bytes, *, skipped: list[TlvCut] | None = None,
) -> tuple[list[Any], TlvCut | None]:
    """Parse a reassembled TLV buffer with the generated reader: ``(tlvs, cut)``.

    ``tlvs`` are the parsed ``status_tlv`` objects in stream order, without the type-0 padding
    sentinel. The whole buffer is read as one ``status_reply`` first. If that fails, the TLVs are
    walked one at a time the way the app's walker steps (Compose4BaseInfoSingleRead.u:
    ``i += 2 + len``), each still typed by the generated ``status_tlv`` reader, and the walk stops at
    the first TLV that runs past the buffer (a lost tail chunk, or in a complete burst a len past
    the reply's end, where u()'s ``System.arraycopy`` throws) or whose typed body doesn't parse.
    The TLVs before it are returned, and ``cut`` says where and why the walk stopped (None when it
    reached the end).

    The reader rejecting a body stands in for the app's handler throwing on it (``u`` reads
    ``bArr[0]`` for 0x01 / 0x04; ``w`` copies 16 / 12 bytes after a 0x10 / 0x11 selector; the VM
    lambda reads two 0x30 / 0x41 bytes). The exception is a 0x07 block with another selector, which
    w() returns from without reading (:func:`_app_skips`): the walk steps past it and, when
    ``skipped`` is given, records it there. A len byte of :data:`SIGNED_LEN_MIN` or more also stops
    the walk ("unparsed"): u() reads it signed, and the negative length throws.

    Known differences that remain: for 0xA5 the app reads its per-SKU record count
    (VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.o -> i3 ->
    Controller4ColorInfoByGroup.Companion.q), so it ignores extra records and throws on missing
    ones, while the reader takes the count from len and rejects a len that isn't 1 + 4k; the
    sleep / wake-up / timer TLVs (0x11 / 0x12 / 0x23), which the spec leaves raw, go to per-SKU
    TimerConfig / ControllerNewTimer parsers the library doesn't model; a type-0 byte ends the walk
    here (the spec's ``status_reply`` padding sentinel), while u() reads it as an ordinary TLV
    (handed to the VM's fallback lambda, which ignores it) and walks on; and a last TLV whose value
    would start at the buffer's end (len 0) takes u()'s failure branch (``i + 2 >= length``) but is
    read here. No capture shows any of them."""
    try:
        reply = _parse_type(_F.StatusReply, buf)
    except Exception:  # noqa: BLE001 - a TLV the stream read can't take: walk them one at a time
        reply = None
    if reply is not None:
        whole = [tlv for tlv in reply.tlvs if int(tlv.type) != 0]
        if all(int(tlv.len) < SIGNED_LEN_MIN for tlv in whole):
            return whole, None
        # A len the app reads as negative: walk one TLV at a time, which stops there.
    tlvs: list[Any] = []
    pos = 0
    while pos < len(buf) and buf[pos] != 0:   # type 0 = trailing zero pad (status_reply's sentinel)
        typ = buf[pos]
        if pos + 2 > len(buf):
            return tlvs, TlvCut("truncated", typ, pos)
        length = buf[pos + 1]
        if length >= SIGNED_LEN_MIN:
            return tlvs, TlvCut("unparsed", typ, pos, length)
        end = pos + 2 + length
        if end > len(buf):
            return tlvs, TlvCut("truncated", typ, pos, length)
        try:
            tlvs.append(_parse_type(_F.StatusTlv, buf[pos:end]))
        except Exception:  # noqa: BLE001 - a typed body the spec can't read (short, bad UTF-8, ...)
            if not _app_skips(typ, buf[pos + 2:end]):
                return tlvs, TlvCut("unparsed", typ, pos, length)
            _LOGGER.debug("status reply: 0x07 block with selector 0x%02x (len %d) did not parse; "
                          "skipped, as the app's w() skips it", buf[pos + 2], length)
            if skipped is not None:
                skipped.append(TlvCut("unparsed", typ, pos, length))
        pos = end
    return tlvs, None


def parse_status(frames: list[bytes]) -> StatusReply:
    """Reassemble a 0xAC burst and decode it via the generated ``status_reply`` reader.

    Only the cross-frame de-chunk (:func:`reassemble`) is hand-done — Kaitai can't join
    frames. The reassembled buffer is then parsed straight from the spec model
    (:func:`walk_status_tlvs`): the ksy ``status_reply`` walks the TLV stream and types every
    value — switch / brightness / zone / seg-info / colour groups / ``0x07`` device-info
    (== ``device_info_read``). No client-side TLV or value hand-parsing.

    Segment global index = ``(group_index-1) * 4 + k`` (oneGroupColorSize stride; the per-group
    record COUNT comes from the TLV len). Device-info: first non-None wins (basic 0x10 precedes
    wifi 0x11), giving wifi_mac / hw / sw / serial for BLE-only devices (H60A6) from the spec.
    The 0x11 block's versions are the Wi-Fi module's, so they never fill the device's own, and
    other selectors are skipped, as the app's status walker does (Compose4BaseInfoSingleRead.w),
    whether or not the spec can read their body (:func:`walk_status_tlvs`).

    A TLV the app's walker throws on (a typed body that doesn't parse, a len byte of 0x80 or more,
    or in a complete burst a len past the reply's end) fails the app's whole read, so it gives NO
    state here. Nothing
    catches the throw (ComposeBleComm.K), the read times out into result2Fail
    (AbsComposeControllerWithOpResult.onOvertime), and the VM's failure callback
    (VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.m) reports it failed with P(1). Its success
    callback l(), which applies switch / brightness / device-info and syncs the mode to the
    screen, never runs; the zone / colour values its lambda (.o) had already stored stay in the
    Info4Detail model (plain setters) without that sync. Device then retries once and leaves
    state stale. A burst cut short by LOST chunks is kept whole up to the cut (the library's own
    leniency: the app would not decode it at all), and so is one whose chunks arrived out of order
    (:func:`burst_chunks`). The other known differences from u() are listed under
    :func:`walk_status_tlvs`."""
    st = StatusReply()
    buf, complete = _joined(frames)
    tlvs, cut = walk_status_tlvs(buf)
    if cut is not None:
        if cut.reason == "unparsed" or complete:
            _LOGGER.debug("status reply %s at TLV 0x%02x (offset %d of %d bytes): the app's read fails "
                          "here; no state used", cut.reason, cut.type, cut.offset, len(buf))
            return st
        _LOGGER.debug("status reply %s at TLV 0x%02x (offset %d of %d bytes, chunks lost); using the "
                      "%d TLVs before it", cut.reason, cut.type, cut.offset, len(buf), len(tlvs))

    groups: list[tuple[int, list[parse.GroupRecord]]] = []
    for tlv in tlvs:
        typ = int(tlv.type)
        v = tlv.value
        if typ == TYPE_SWITCH:
            st.is_on = bool(v.state)
        elif typ == TYPE_BRIGHTNESS:
            st.brightness = int(v.brightness)
        elif typ == TYPE_ZONE:
            # bit0 of each byte (VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.o:
            # ParseUtils.getBit2Boolean(b, 1) = (b >> 0) & 1); the other bits are not zone power.
            st.zone_power = {0: bool(int(v.zone_a) & 0x01), 1: bool(int(v.zone_b) & 0x01)}
        elif typ == TYPE_COLOR_GROUP:
            groups.append((int(v.group_index), [
                (int(rec.brightness), int(rec.r), int(rec.g), int(rec.b)) for rec in v.records]))
        elif typ == TYPE_DEVICE_INFO:
            sel = int(v.selector)
            # Compose4BaseInfoSingleRead.w reads only the basic (0x10) and Wi-Fi (0x11) blocks
            # here; it logs any other selector and skips it.
            info = parse.device_info_from(sel, v.info) if sel in (INFO_BASIC, INFO_WIFI) else None
            if info is not None:
                if info.serial is not None and st.serial_number is None:
                    st.serial_number = info.serial
                if info.wifi_mac is not None and st.wifi_mac is None:
                    st.wifi_mac = info.wifi_mac
                # 0x11 sw/hw are the Wi-Fi module's (Compose4BaseInfoSingleRead.w stores them as
                # wifiSoftVersion / wifiHardVersion, apart from the 0x10 softVersion / hardVersion).
                if sel != INFO_WIFI:
                    if info.sw_version is not None and st.firmware_version is None:
                        st.firmware_version = info.sw_version
                    if info.hw_version is not None and st.hardware_version is None:
                        st.hardware_version = info.hw_version
    # Same positional assembly as the direct group replies (oneGroupColorSize stride).
    st.segments = parse.direct_groups_to_segments(groups, _RECORDS_PER_GROUP)
    return st
