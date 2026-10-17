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
from io import BytesIO
from typing import Any, Literal

from kaitaistruct import KaitaiStream, ValidationExprError, ValidationGreaterThanError

from ..const import FRAME_LEN
from ..models import Segment
from . import parse
from .build import INFO_WIFI, _F
from .parse import _parse_type

_LOGGER = logging.getLogger(__name__)
_RECORDS_PER_GROUP = 4   # oneGroupColorSize (Support.getOneGroupColorSize): the index stride
_RECORD_LEN = 4          # color_group_record: [brightness, r, g, b] (Controller4ColorInfoByGroup.q)

# Chunk layout: the spec's multi_ac_reply_chunk (AbsController4BleSingleSendMultiBack: the first chunk's
# header and 12 data bytes, every other chunk's index and 17 data bytes; the BCC follows).
_LAST_INDEX = 0xFF          # index byte of the last packet (multi_ac_reply_chunk.is_end_tag)
MIN_CHUNK_LEN = FRAME_LEN - 1   # 19: a shorter record can't hold a whole chunk (it needs no BCC): skipped
# Compose4BaseInfoSingleRead.u reads a TLV's len byte signed (`int i10 = it2[i7]` from a byte[]):
# from this value up it is negative, `new byte[i10]` throws and the app's whole read fails. The spec's
# status_tlv rejects it (`len` valid max 0x7f), which is what the walk goes by; kept for callers.
SIGNED_LEN_MIN = 0x80

# TLV types (enum status_tlv_type).
TYPE_SWITCH = int(_F.StatusTlvType.switch)              # 0x01
TYPE_BRIGHTNESS = int(_F.StatusTlvType.brightness)      # 0x04
TYPE_MODE = int(_F.StatusTlvType.mode)                  # 0x05
TYPE_DEVICE_INFO = int(_F.StatusTlvType.device_info)    # 0x07
TYPE_SLEEP = int(_F.StatusTlvType.sleep)                # 0x11
TYPE_WAKEUP = int(_F.StatusTlvType.wakeup)              # 0x12
TYPE_TIMERS = int(_F.StatusTlvType.timers)              # 0x23
TYPE_ZONE = int(_F.StatusTlvType.zone_or_direction)     # 0x30
TYPE_BAR_SWITCH = int(_F.StatusTlvType.bar_switch)      # 0x36: H6038 / H6048 request it; not decoded here
TYPE_IC_NUM = int(_F.StatusTlvType.ic_num)              # 0x40: H6671 / H6672 request it
TYPE_SEG_INFO = int(_F.StatusTlvType.on_off_memory)     # 0x41: the seg / IC info TLV
TYPE_COLOR_GROUP = int(_F.StatusTlvType.color_group)    # 0xA5


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
    # The 0x05 TLV's value, [sub_mode, params]: the device's mode, the same body an aa 05 reply carries
    # (pact_h60a0 h60a1.Info4Detail.Q reads it; the last 0x05 TLV wins, as each overwrites there).
    mode: bytes | None = None


@dataclass(frozen=True)
class TlvCut:
    """A TLV :func:`walk_status_tlvs` could not take: where and why it stopped before the end of the
    buffer, or (in its ``skipped`` list) a TLV it stepped past."""
    # "truncated": the TLV runs past the buffer. "unparsed": the app's walker throws on it - its typed
    # body didn't parse, or its len byte is SIGNED_LEN_MIN or more (read as negative: negative_len).
    reason: Literal["truncated", "unparsed"]
    type: int                                  # the TLV type byte at the cut
    offset: int                                # its offset in the reassembled buffer
    length: int | None = None                  # its declared len (None when the len byte is missing)
    negative_len: bool = False                 # the spec's status_tlv `len` valid (max 0x7f) rejected it


def read_ac_chunk(frame: bytes) -> Any:
    """A 0xAC reply chunk read by the spec's ``multi_ac_reply_chunk`` (``rx_frame``'s 0xAC body: the first
    chunk's index 0, ``packets`` u16le (``BleUtils.H(.., false)``), ``last_len``, command, sub and 12 data
    bytes; every other chunk's index and 17), read off the bytes after the proType, so a record without
    its BCC still reads. None for a first chunk whose ``last_len`` doesn't fit its packet, the spec's
    ``valid`` (AbsController4BleSingleSendMultiBack.g returns -1 and the app rejects the whole burst).
    The caller has checked the record is at least :data:`MIN_CHUNK_LEN` bytes."""
    try:
        return _parse_type(_F.MultiAcReplyChunk, frame[1:])
    except ValidationExprError:   # the first chunk's last_len valid, its only check
        return None


def burst_chunks(frames: Iterable[bytes]) -> tuple[list[bytes], bool, int]:
    """De-duplicate a burst's chunks by index (first copy wins), order them and judge completeness.

    Returns ``(chunks, complete, conflicts)``: ``chunks`` is the contiguous prefix 0x00, 0x01, ...
    plus the last packet when the burst is complete; ``conflicts`` counts chunk indices that arrived
    again with DIFFERENT bytes. The join is offset-by-position, so a chunk after a gap would splice in
    at the wrong offset and the TLV walk would decode garbage as real state: everything from the
    first gap on is dropped.

    Completeness is the app's rule (AbsController4BleSingleSendMultiBack.isSameController / .onResult):
    the first chunk declares the packet count, and the last packet (index 0xFF, or index count-1,
    which the app accepts too; the first of them to arrive once the others are in, see
    :func:`_last_chunk`) is taken only once packets 0..count-2 are all in. So a burst that lost
    the chunks just before 0xFF, or a lone 0xFF chunk, is incomplete. A one-packet reply (count 1) is
    complete by itself (``g()`` hands it straight to ``l()``, no 0xFF chunk). A lastLen too long for
    its packet makes the app reject the whole burst before it copies a byte (``g()`` returns -1 and
    the read fails): no chunks, not complete.

    Leniency the app does not have: chunks are put back in index order. isSameController takes a
    chunk only when its index is the next one expected (``backPos``), so in the app a chunk that
    arrives early is ignored and the read then times out; here a burst whose chunks all arrived, in
    any order, is complete."""
    unique: dict[int, bytes] = {}
    arrival: dict[int, int] = {}   # chunk index -> arrival position of its kept (first) copy
    conflicts = 0
    for position, fr in enumerate(frames):
        if len(fr) < MIN_CHUNK_LEN:
            _LOGGER.debug("status burst: skipping short chunk (%d bytes)", len(fr))
            continue
        chunk = read_ac_chunk(fr)
        index = 0 if chunk is None else int(chunk.index)   # only a first chunk can fail its valid
        arrival.setdefault(index, position)
        kept = unique.setdefault(index, fr)
        if kept is not fr and kept != fr:
            conflicts += 1
            _LOGGER.debug("status burst: chunk 0x%02x arrived again with different bytes; "
                          "keeping the first copy", index)
    first = unique.get(0)
    if first is None:
        if unique:
            _LOGGER.debug("status burst incomplete: chunks %s, no first chunk", sorted(unique))
        return [], False, conflicts
    head = read_ac_chunk(first)
    if head is None:
        _LOGGER.debug("status burst rejected: the first chunk's lastLen does not fit its packet: %s",
                      first.hex())
        return [], False, conflicts
    total = int(head.packets)
    if total == 1:
        return [first], True, conflicts
    # Packets 0..total-2 come before the last one; a corrupt count (0) can't bound the prefix.
    stop = total - 1 if total >= 2 else _LAST_INDEX
    ordered: list[bytes] = []
    index = 0
    while index < stop and index in unique and index != _LAST_INDEX:
        ordered.append(unique[index])
        index += 1
    last = _last_chunk(unique, arrival, total, len(ordered)) if total >= 2 else None
    complete = last is not None and len(ordered) == total - 1
    if complete and last is not None:
        ordered.append(last)
    else:
        _LOGGER.debug(
            "status burst incomplete: chunks %s of %d declared, using contiguous prefix of %d",
            sorted(unique), total, len(ordered),
        )
    return ordered, complete, conflicts


def _last_chunk(unique: dict[int, bytes], arrival: dict[int, int], total: int, prefix: int) -> bytes | None:
    """The chunk taken as the last packet of a ``total``-packet burst whose first ``prefix`` packets are
    in hand: as the app takes it, the first to arrive of index 0xFF and index ``total - 1``
    (AbsController4BleSingleSendMultiBack.isSameController accepts either once ``backPos`` is
    ``total - 1``, and the first one ends the read), among those that arrived after the prefix was
    complete; a candidate that came earlier the app had refused ("END_PKG_ORDER"). When none came
    after it, the earliest candidate is taken anyway: the same out-of-order leniency as the prefix."""
    candidates = [i for i in {_LAST_INDEX, total - 1} if i in unique]
    if not candidates:
        return None
    done = max((arrival[i] for i in range(prefix)), default=-1)
    late = [i for i in candidates if arrival[i] > done]
    return unique[min(late or candidates, key=arrival.__getitem__)]


def _join(chunks: list[bytes], complete: bool) -> bytes:
    """Concatenate the data regions of :func:`burst_chunks`'s chunks. A complete burst ends with the
    last packet's lastLen bytes (``.h()``); an incomplete one with its last whole chunk."""
    if not chunks:
        return b""
    head = read_ac_chunk(chunks[0])   # burst_chunks only hands over a first chunk that reads
    if int(head.packets) == 1:   # one-packet reply (always complete): lastLen bytes (first_data_len)
        return bytes(head.data)[: int(head.first_data_len)]
    buf = bytearray(head.data)
    for fr in chunks[1:-1] if complete else chunks[1:]:
        buf += bytes(read_ac_chunk(fr).data)
    if complete:
        buf += bytes(read_ac_chunk(chunks[-1]).data)[: int(head.last_len)]
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


def _status_device_info(value: bytes) -> Any:
    """A 0x07 TLV value read by the spec's ``status_device_info`` (the app's status parser: only the
    basic 0x10 and Wi-Fi 0x11 selectors have a body; every other selector's stays raw bytes), or None
    when even its selector can't be read (an empty value)."""
    try:
        return _parse_type(_F.StatusDeviceInfo, value)
    except Exception:  # noqa: BLE001 - empty value / a 0x10 / 0x11 body too short
        return None


def _app_skips(typ: int, value: bytes) -> bool:
    """True for a TLV the app's walker steps past without reading its body: a 0x07 block whose
    selector is neither basic (0x10) nor Wi-Fi (0x11), which ``status_device_info`` leaves as raw bytes.
    Compose4BaseInfoSingleRead.w reads ``bArr[0]``, logs any other selector ("parse07Info() 其他信息") and
    returns, so ``u()`` walks on. An empty 0x07 value has no selector: ``bArr[0]`` throws there, so it is
    not skipped."""
    if typ != TYPE_DEVICE_INFO:
        return False
    info = _status_device_info(value)
    return info is not None and isinstance(info.info, (bytes, bytearray))


def walk_status_tlvs(
    buf: bytes, *, skipped: list[TlvCut] | None = None,
) -> tuple[list[Any], TlvCut | None]:
    """Parse a reassembled TLV buffer with the generated reader: ``(tlvs, cut)``.

    ``tlvs`` are the parsed ``status_tlv`` objects in stream order, without the type-0 padding
    sentinel. The whole buffer is read as one ``status_reply`` first. If that fails, the TLVs are
    walked one at a time the way the app's walker steps (Compose4BaseInfoSingleRead.u:
    ``i += 2 + len``), each read by the generated ``status_tlv`` off one shared stream, and the walk stops at
    the first TLV that runs past the buffer (a lost tail chunk, or in a complete burst a len past
    the reply's end, where u()'s ``System.arraycopy`` throws) or whose typed body doesn't parse.
    The TLVs before it are returned, and ``cut`` says where and why the walk stopped (None when it
    reached the end).

    The reader rejecting a body stands in for the app's handler throwing on it (``u`` reads
    ``bArr[0]`` for 0x01 / 0x04; ``w`` copies 16 / 12 bytes after a 0x10 / 0x11 selector; the VM
    lambda reads two 0x30 / 0x41 bytes). The exception is a 0x07 block with another selector, which
    w() returns from without reading (:func:`_app_skips`): the walk steps past it and, when
    ``skipped`` is given, records it there. A len byte the spec's ``status_tlv`` rejects (``valid``
    max 0x7f; :data:`SIGNED_LEN_MIN` or more) also stops the walk ("unparsed", ``negative_len``): u()
    reads it signed, and the negative length throws.

    Known differences that remain: for 0xA5 the app reads its per-SKU record count
    (VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.o -> i3 ->
    Controller4ColorInfoByGroup.Companion.q), and :func:`parse_status` does too, but this walk's
    ``color_group_status`` takes the count from len and so rejects a len that isn't 1 + 4k, which the
    app reads when the value holds at least its count; the
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
    if reply is not None:   # every len passed status_tlv's valid (max 0x7f), so the app reads them all
        return [tlv for tlv in reply.tlvs if int(tlv.type) != 0], None
    # The generated status_tlv reads each TLV off one shared stream, so the walk steps exactly where
    # the spec's own TLV boundaries are. When the reader can't take one, how far it got says why, in
    # the app's order: no len byte (``len`` unset), a negative len (``len``'s valid), a value past the
    # buffer (``len`` set, no raw value), a body that doesn't parse (the raw value is there).
    tlvs: list[Any] = []
    stream = KaitaiStream(BytesIO(buf))
    while not stream.is_eof():
        pos = stream.pos()
        tlv: Any = _F.StatusTlv(stream)
        try:
            tlv._read()
        except ValidationGreaterThanError:
            return tlvs, TlvCut("unparsed", int(tlv.type), pos, int(tlv.len), negative_len=True)
        except Exception:  # noqa: BLE001 - truncated, or a typed body the spec can't read (short, bad UTF-8)
            typ = int(tlv.type)
            length = getattr(tlv, "len", None)
            raw = getattr(tlv, "_raw_value", None)
            if length is None:
                return tlvs, TlvCut("truncated", typ, pos)
            if raw is None:
                return tlvs, TlvCut("truncated", typ, pos, int(length))
            if not _app_skips(typ, bytes(raw)):
                return tlvs, TlvCut("unparsed", typ, pos, int(length))
            _LOGGER.debug("status reply: 0x07 block with selector 0x%02x (len %d) did not parse; "
                          "skipped, as the app's w() skips it", raw[0], length)
            if skipped is not None:
                skipped.append(TlvCut("unparsed", typ, pos, int(length)))
            stream.seek(pos + 2 + int(length))
            continue
        if int(tlv.type) == 0:   # type 0 = trailing zero pad (status_reply's sentinel)
            break
        tlvs.append(tlv)
    return tlvs, None


def group_record_count(group_index: int, value_len: int, pieces: int | None) -> int:
    """Records to read from one 0xA5 colour-group TLV of ``value_len`` bytes.

    With the device's colour-piece count, the app's rule
    (``VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.o``): ``oneGroupColorSize`` (4) records,
    except the last of the ``ceil(pieces / 4)`` groups, which holds the remainder;
    ``Controller4ColorInfoByGroup.Companion.q`` then reads exactly that many, ignoring any extra and
    throwing on a short value. ``pieces`` None (no device context, e.g. the analyzer) keeps the TLV's
    own len, as the spec's ``color_group_status`` reads it: ``(len - 1) / 4``."""
    if pieces is None:
        return max(0, (value_len - 1) // _RECORD_LEN)
    groups = -(-pieces // _RECORDS_PER_GROUP)
    if group_index != groups:
        return _RECORDS_PER_GROUP
    return pieces - (groups - 1) * _RECORDS_PER_GROUP


def _color_group(tlv: Any, pieces: int | None) -> tuple[int, list[parse.GroupRecord]]:
    """One 0xA5 TLV read by the spec's ``color_group_read(record_count, has_brightness=1)`` over its
    raw value, with :func:`group_record_count` records. Raises what the reader raises on a value too
    short for that count."""
    raw = bytes(tlv._raw_value)   # the TLV's value bytes, as the generated status_tlv keeps them
    # Without the device's piece count, the spec's own color_group_status records (repeat eos).
    count = (len(tlv.value.records) if pieces is None
             else group_record_count(int(tlv.value.group_index), len(raw), pieces))
    group = _parse_type(_F.ColorGroupRead, raw, count, 1)
    return int(group.group_index), [
        (int(rec.brightness), int(rec.r), int(rec.g), int(rec.b)) for rec in group.records]


def parse_status(frames: list[bytes], *, pieces: int | None = None) -> StatusReply:
    """Reassemble a 0xAC burst and decode it via the generated ``status_reply`` reader.

    Only the cross-frame de-chunk (:func:`reassemble`) is hand-done — Kaitai can't join
    frames. The reassembled buffer is then parsed straight from the spec model
    (:func:`walk_status_tlvs`): the ksy ``status_reply`` walks the TLV stream and types every
    value — switch / brightness / zone / seg-info / colour groups / ``0x07`` device-info
    (== ``device_info_read``). No client-side TLV or value hand-parsing.

    Segment global index = ``(group_index-1) * 4 + k`` (oneGroupColorSize stride). Each colour group
    is read by the spec's ``color_group_read`` with the app's record count for ``pieces``, the
    device's colour-piece count (:func:`group_record_count`; the H60A6's 13,
    ``pact_h60a0.pact.Support.getColorPieceSize``), so extra records are ignored and a group short of
    its count fails the read, as in the app. ``pieces`` None takes the count from the TLV len.
    Device-info: first non-None wins (basic 0x10 precedes
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
            # status_switch.is_on: != 0, as the app's status walker reads it (Compose4BaseInfoSingleRead.u),
            # unlike a light's aa 01 reply (== 1).
            st.is_on = bool(v.is_on)
        elif typ == TYPE_BRIGHTNESS:
            st.brightness = int(v.brightness)
        elif typ == TYPE_MODE:
            st.mode = bytes(tlv._raw_value)   # mode_status: [sub_mode, params]
        elif typ == TYPE_ZONE:
            # bit0 of each byte (VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.o:
            # ParseUtils.getBit2Boolean(b, 1) = (b >> 0) & 1); the other bits are not zone power.
            st.zone_power = {0: bool(v.zone_a_on), 1: bool(v.zone_b_on)}   # status_zone: bit 0
        elif typ == TYPE_COLOR_GROUP:
            try:
                groups.append(_color_group(tlv, pieces))
            except Exception:  # noqa: BLE001 - Controller4ColorInfoByGroup.q throws: the read fails
                _LOGGER.debug("status reply: colour group %d is short of its %d records (len %d): the "
                              "app's read fails here; no state used", int(v.group_index),
                              group_record_count(int(v.group_index), int(tlv.len), pieces), int(tlv.len))
                return StatusReply()
        elif typ == TYPE_DEVICE_INFO:
            # Compose4BaseInfoSingleRead.w reads only the basic (0x10) and Wi-Fi (0x11) blocks here; it
            # logs any other selector and skips it: the spec's status_device_info types only those two.
            sdi = _status_device_info(bytes(tlv._raw_value))
            if sdi is None or isinstance(sdi.info, (bytes, bytearray)):
                continue
            sel = int(sdi.selector)
            info = parse.device_info_from(sel, sdi.info)
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
