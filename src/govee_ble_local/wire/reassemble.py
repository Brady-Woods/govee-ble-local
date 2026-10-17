"""Reassemble multi-packet 0xAC status bursts, then parse them via the spec model.

Only the cross-frame de-chunk is hand-done (Kaitai can't join frames). It is the app's
``AbsController4BleSingleSendMultiBack`` state machine (``.isSameController`` / ``.onResult`` ->
``.g`` / ``.i`` / ``.h``, as ``ControllerSingleRead`` runs it), each chunk read by the spec's
``multi_ac_reply_chunk``: the FIRST chunk (index 0x00) is ``[0xAC, 0x00, packets u16le, lastLen, cmd,
sub, <12 data bytes>]``, every later chunk carries 17 data bytes at offset 2, and the last packet
(index 0xFF) carries only ``lastLen`` of them. A one-packet reply has no 0xFF chunk: its ``lastLen``
data bytes sit at offset 7. Chunks are taken only in order (:class:`BurstAssembler`), so a duplicate,
an early or a late chunk is ignored, and only a complete burst is joined: the app decodes nothing
from an incomplete one.

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
from .build import _F
from .parse import _parse_type

_LOGGER = logging.getLogger(__name__)
_RECORDS_PER_GROUP = 4   # oneGroupColorSize (Support.getOneGroupColorSize): the index stride
_RECORD_LEN = 4          # color_group_record: [brightness, r, g, b] (Controller4ColorInfoByGroup.q)

# Chunk layout: the spec's multi_ac_reply_chunk (AbsController4BleSingleSendMultiBack: the first chunk's
# header and 12 data bytes, every other chunk's index and 17 data bytes; the BCC follows).
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
_TYPE_PADDING = int(_F.StatusTlvType.padding)           # 0x00: the trailing zero-pad sentinel


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
    # "tail": the buffer ends before the TLV's value starts (no len byte, or none after it); the app's
    # walker stops there cleanly and keeps what it read (Compose4BaseInfoSingleRead.u:257-267, :271-282).
    reason: Literal["truncated", "unparsed", "tail"]
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


@dataclass
class BurstAssembler:
    """The app's 0xAC burst assembly, frame by frame: ``AbsController4BleSingleSendMultiBack`` as
    ``ControllerSingleRead`` runs it for the status read (``Compose4BaseInfoSingleRead.java:230``).

    ``feed`` takes one received record and returns whether the app would take it. While no chunk has
    been taken (``backPos`` 0), only a first chunk (index 0) whose command and sub are the request's is
    taken (``isSameController:204-205`` -> ``f():100-103``: 3 and 0, ``ControllerSingleRead.java:82,
    89-95``); then only the next index in order, or the last packet - index 0xFF, or the index
    ``packets - 1`` - once every packet before it is in (``isSameController:207-222``). Anything else
    (a duplicate, an early or out-of-order chunk, an early 0xFF: "END_PKG_ORDER") is ignored, and the
    read goes on waiting. ``status`` becomes ``"complete"`` with the last packet (``h()``; a one-packet
    reply at once, ``g():134-141``) and ``"failed"`` when the first chunk's lastLen does not fit its
    packet (``g()`` returns -1: the read fails at once). An incomplete burst stays ``"waiting"``: the
    app times out and applies nothing (``AbsComposeControllerWithOpResult.onOvertime``).

    Spec gap: the spec's ``multi_ac_reply_chunk`` checks lastLen (its ``valid``) before it reads command
    and sub, while the app checks command and sub first, so a first chunk with both a bad lastLen and
    another command fails the read here where the app would ignore it (no capture has one)."""
    command: int = int(_F.MultiAcCommand.single_read)
    sub: int = 0
    chunks: list[bytes] = field(default_factory=list)       # the chunks taken, in order
    status: Literal["waiting", "complete", "failed"] = "waiting"
    conflicts: int = 0    # chunk indices that arrived again with DIFFERENT bytes (diagnostics)
    _back_pos: int = field(default=0, repr=False)            # backPos: the next index expected
    _total: int = field(default=-1, repr=False)              # allBackPkgs, from the first chunk
    _seen: dict[int, bytes] = field(default_factory=dict, repr=False)

    def feed(self, fr: bytes) -> bool:
        """Offer one record; True when the app's state machine takes it."""
        if len(fr) < MIN_CHUNK_LEN or fr[0] != _F.ProType.multi_reply_read:
            _LOGGER.debug("status burst: skipping a record that is not a chunk (%d bytes)", len(fr))
            return False
        chunk = read_ac_chunk(fr)
        index = 0 if chunk is None else int(chunk.index)   # only a first chunk can fail its valid
        kept = self._seen.setdefault(index, fr)
        if kept is not fr and kept != fr:
            self.conflicts += 1
            _LOGGER.debug("status burst: chunk 0x%02x arrived again with different bytes; the app takes "
                          "whichever copy comes in order", index)
        if self.status != "waiting":
            return False
        if self._back_pos == 0:
            if index != 0:
                return False
            if chunk is None:
                _LOGGER.debug("status burst rejected: the first chunk's lastLen does not fit its packet: "
                              "%s", fr.hex())
                self.status = "failed"
                return True
            if int(chunk.command) != self.command or int(chunk.sub) != self.sub:
                return False
            self._total, self._back_pos = int(chunk.packets), 1
            self.chunks.append(fr)
            if self._total == 1:
                self.status = "complete"
            return True
        if chunk is None or chunk.is_first:
            return False
        last = self._back_pos == self._total - 1
        if chunk.is_end_tag:
            if not last:
                return False
        elif index != self._back_pos:
            return False
        self.chunks.append(fr)
        if last:
            self.status = "complete"
        else:
            self._back_pos += 1
        return True


def burst_chunks(frames: Iterable[bytes]) -> tuple[list[bytes], bool, int]:
    """Run a burst's records through :class:`BurstAssembler`: ``(chunks, complete, conflicts)``.

    ``chunks`` are the chunks the app takes, in order (all of them, last packet included, when
    ``complete``); ``conflicts`` counts chunk indices that arrived again with DIFFERENT bytes. Only a
    complete burst is decoded (:func:`parse_status`), as in the app."""
    asm = BurstAssembler()
    for fr in frames:
        asm.feed(fr)
    if asm.status == "failed":
        return [], False, asm.conflicts
    if asm.status != "complete" and asm.chunks:
        _LOGGER.debug("status burst incomplete: %d of %d declared chunks taken", len(asm.chunks),
                      asm._total)
    return asm.chunks, asm.status == "complete", asm.conflicts


def _join(chunks: list[bytes], complete: bool) -> bytes:
    """Concatenate the data regions of a complete burst's chunks (:func:`burst_chunks`): the first
    chunk's 12 bytes, 17 from each middle chunk, and the last packet's lastLen (``.h()``); a one-packet
    reply's lastLen bytes alone. An incomplete burst joins to nothing: the app decodes none of it."""
    if not chunks or not complete:
        return b""
    head = read_ac_chunk(chunks[0])   # burst_chunks only hands over a first chunk that reads
    if int(head.packets) == 1:   # one-packet reply: lastLen bytes (first_data_len)
        return bytes(head.data)[: int(head.first_data_len)]
    buf = bytearray(head.data)
    for fr in chunks[1:-1]:
        buf += bytes(read_ac_chunk(fr).data)
    buf += bytes(read_ac_chunk(chunks[-1]).data)[: int(head.last_len)]
    return bytes(buf)


def _joined(frames: list[bytes]) -> tuple[bytes, bool]:
    """``(buffer, complete)`` for a burst: :func:`burst_chunks` then :func:`_join`."""
    chunks, complete, _conflicts = burst_chunks(frames)
    return _join(chunks, complete), complete


def reassemble(frames: list[bytes]) -> bytes:
    """Join a 0xAC burst: first chunk's data @ offset 7, the rest @ offset 2.

    The chunks are the ones the app takes, in order (:func:`burst_chunks`); a burst that is not
    complete joins to ``b""``."""
    return _joined(frames)[0]


def walk_status_tlvs(
    buf: bytes, *, skipped: list[TlvCut] | None = None,
) -> tuple[list[Any], TlvCut | None]:
    """Walk a reassembled TLV buffer as the app's walker steps it, each TLV read by the generated
    ``status_tlv``: ``(tlvs, cut)``.

    ``Compose4BaseInfoSingleRead.u`` (:247-345) reads ``[type][len][value]`` and steps
    ``i += 2 + len``. ``tlvs`` are the ``status_tlv`` objects it hands on, in stream order; ``cut`` says
    where and why the walk stopped before the end (None when it reached the end):

    - ``"tail"``: the buffer ends before a TLV's value starts (no len byte, or no byte after it,
      whatever the len). u() stops cleanly there (:257-267, :271-282) and the read succeeds with what
      it walked, so this is not a failure.
    - ``"unparsed"``: u() or a handler throws, which fails the app's whole read: a len byte of
      :data:`SIGNED_LEN_MIN` or more (read signed, negative; the spec's ``status_tlv`` ``len`` valid
      max 0x7f: ``negative_len``), or a typed body that doesn't parse (the reader rejecting it stands
      in for the handler throwing: ``u`` reads ``bArr[0]`` for 0x01 / 0x04; ``w`` copies 16 / 12 bytes
      after a 0x10 / 0x11 selector; the VM lambda reads two 0x30 / 0x41 bytes).
    - ``"truncated"``: a value runs past the buffer (u()'s ``System.arraycopy`` throws).

    A 0x07 block reads as the spec's ``status_device_info``, which types only the basic 0x10 and Wi-Fi
    0x11 selectors the app's ``w()`` decodes and leaves any other raw (``app_decodes``), so it is handed
    on like any TLV. A 0xA5 colour group whose typed value the spec can't read is not a cut either: the
    app reads it with its own record count (:func:`parse_status` decides), so it is handed on and, when
    ``skipped`` is given, recorded there.

    A 0x05 mode TLV whose value ``mode_read`` can't read is handed on raw too, as long as it holds a
    sub-mode byte (see the walk): the app's per-sub-mode minimums aren't typed yet.

    Known differences that remain, waiting for spec types: a type-0 byte ends the walk here (the spec's
    ``status_tlv`` reads no len or value for it, the ``status_reply`` padding sentinel), while u() reads
    it as an ordinary TLV (handed to the VM's fallback lambda, which ignores it) and walks on; and the
    sleep / wake-up / timer TLVs (0x11 / 0x12 / 0x23), which the spec leaves raw, go to per-SKU
    TimerConfig / ControllerNewTimer parsers the library doesn't model. No capture shows either."""
    tlvs: list[Any] = []
    n = len(buf)
    stream = KaitaiStream(BytesIO(buf))
    pos = 0
    while pos < n:
        stream.seek(pos)
        tlv: Any = _F.StatusTlv(stream)
        err: Exception | None = None
        try:
            tlv._read()
        except Exception as e:  # noqa: BLE001 - judged below, in the app's order
            err = e
        typ = int(tlv.type)
        length = getattr(tlv, "len", None)
        if typ == _TYPE_PADDING:   # the trailing zero-pad sentinel (see the docstring)
            break
        if pos + 2 >= n:
            return tlvs, TlvCut("tail", typ, pos, None if length is None else int(length))
        if isinstance(err, ValidationGreaterThanError):
            return tlvs, TlvCut("unparsed", typ, pos, int(tlv.len), negative_len=True)
        if err is not None:
            raw = getattr(tlv, "_raw_value", None)
            if length is None or raw is None:
                return tlvs, TlvCut("truncated", typ, pos, None if length is None else int(length))
            if typ == TYPE_MODE and len(raw) > 0:
                # status_tlv reads 0x05 as mode_read over the exact value, which is stricter than the app for
                # some short bodies (e.g. 13 31 00, which Info4Detail.Q accepts) and looser for others: the
                # app's per-sub-mode minimums (h60a1 Info4Detail.Q:380-433) are not typed yet. Until they
                # are, a non-empty value is handed on as before; parse_status keeps it raw.
                _LOGGER.debug("status reply: mode TLV %s did not read as mode_read; kept raw",
                              bytes(raw).hex())
            elif typ != TYPE_COLOR_GROUP:
                return tlvs, TlvCut("unparsed", typ, pos, int(length))
            else:
                _LOGGER.debug("status reply: colour group TLV of len %d did not read as color_group_status; "
                              "read with the app's record count", length)
            if skipped is not None:
                skipped.append(TlvCut("unparsed", typ, pos, int(length)))
            tlvs.append(tlv)
            pos += int(tlv.total_len)
            continue
        tlvs.append(tlv)
        pos += int(tlv.total_len)
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
    raw value, with :func:`group_record_count` records: the app reads the group byte, then exactly its
    count (``VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.o:105-121`` ->
    ``Controller4ColorInfoByGroup.Companion.q:160-175``). Raises what the reader raises on an empty
    value or one too short for that count, where the app throws."""
    raw = bytes(tlv._raw_value)   # the TLV's value bytes, as the generated status_tlv keeps them
    if pieces is not None:   # the H60A6 screen's count: the spec's color_group_status_counted
        group = _parse_type(_F.ColorGroupStatusCounted, raw, pieces, _RECORDS_PER_GROUP, 1)
    else:
        group = _parse_type(_F.ColorGroupRead, raw, group_record_count(0, len(raw), None), 1)
    group_index = int(group.group_index)
    return group_index, [
        (int(rec.brightness), int(rec.r), int(rec.g), int(rec.b)) for rec in group.records]


def _group_placement(group_index: int, pieces: int | None) -> Literal["place", "skip", "fail"]:
    """What the app does with a 0xA5 group of ``pieces`` (VM4LightH60A6$...$1.o / .p:126-142): the
    last of the ``ceil(pieces / 4)`` groups is placed by ``.p``; any other goes to
    ``SubMode4Color.j:113-147``, which throws for group 0 and for a group starting past the last piece,
    and copies nothing for one starting exactly at the end. ``pieces`` None (no device context): place
    it (``parse.group_base_index`` drops group 0)."""
    if pieces is None:
        return "place"
    groups = -(-pieces // _RECORDS_PER_GROUP)
    if group_index == groups:
        return "place"
    start = (group_index - 1) * _RECORDS_PER_GROUP
    if group_index == 0 or start > pieces:
        return "fail"
    return "skip" if start == pieces else "place"


def parse_status(frames: list[bytes], *, pieces: int | None = None) -> StatusReply:
    """Reassemble a 0xAC burst and decode it via the generated ``status_reply`` reader.

    Only the cross-frame de-chunk (:func:`reassemble`) is hand-done — Kaitai can't join
    frames. The reassembled buffer is then parsed straight from the spec model
    (:func:`walk_status_tlvs`): the ksy ``status_reply`` walks the TLV stream and types every
    value — switch / brightness / zone / seg-info / colour groups / ``0x07`` device-info
    (== ``device_info_read``). No client-side TLV or value hand-parsing.

    Only a complete burst is decoded (:func:`burst_chunks`): the app applies nothing from one that lost
    a chunk or its last packet, or whose chunks came out of order. A walk that stops at the buffer's
    tail (``"tail"``) keeps what it read, as u() does.

    Segment global index = ``(group_index-1) * 4 + k`` (oneGroupColorSize stride). Each colour group
    is read by the spec's ``color_group_read`` with the app's record count for ``pieces``, the
    device's colour-piece count (:func:`group_record_count`; the H60A6's 13,
    ``pact_h60a0.pact.Support.getColorPieceSize``), so extra records are ignored and a group short of
    its count fails the read, as in the app. With ``pieces``, a group the app can't place fails the read
    too: group 0, or one starting past the last piece (``SubMode4Color.j:113-147`` throws; that includes
    every group byte of 0x80 or more), while a group starting exactly at the end (``pieces`` a multiple
    of 4) is a no-op and skipped. ``pieces`` None takes the count from the TLV len and drops group 0.
    Device-info: the last block wins, as the app's setters do (``Compose4BaseInfoSingleRead.w:370-437``):
    a basic 0x10 block sets the serial when its UID is valid and always sets the versions (None when
    zero); a Wi-Fi 0x11 block always sets the Wi-Fi MAC (None when zero). The 0x11 block's versions are
    the Wi-Fi module's, so they never fill the device's own, and other selectors are skipped, as w()
    does (``status_device_info.app_decodes``).

    A TLV the app's walker throws on (a typed body that doesn't parse, a len byte of 0x80 or more,
    or a len past the reply's end) fails the app's whole read, so it gives NO
    state here. Nothing
    catches the throw (ComposeBleComm.K), the read times out into result2Fail
    (AbsComposeControllerWithOpResult.onOvertime), and the VM's failure callback
    (VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.m) reports it failed with P(1). Its success
    callback l(), which applies switch / brightness / device-info and syncs the mode to the
    screen, never runs; the zone / colour values its lambda (.o) had already stored stay in the
    Info4Detail model (plain setters) without that sync. Device then retries once and leaves
    state stale. The known differences from u() that remain are listed under
    :func:`walk_status_tlvs`."""
    st = StatusReply()
    buf, complete = _joined(frames)
    if not complete:
        _LOGGER.debug("status reply incomplete: the app decodes nothing from it; no state used")
        return st
    tlvs, cut = walk_status_tlvs(buf)
    if cut is not None and cut.reason != "tail":
        _LOGGER.debug("status reply %s at TLV 0x%02x (offset %d of %d bytes): the app's read fails "
                      "here; no state used", cut.reason, cut.type, cut.offset, len(buf))
        return st

    groups: list[tuple[int, list[parse.GroupRecord]]] = []
    for tlv in tlvs:
        typ = int(tlv.type)
        if typ == TYPE_COLOR_GROUP:   # its typed value may not have parsed (walk_status_tlvs)
            try:
                group = _color_group(tlv, pieces)
            except Exception:  # noqa: BLE001 - Controller4ColorInfoByGroup.q throws: the read fails
                _LOGGER.debug("status reply: colour group TLV (len %d) is short of the app's record count: "
                              "the app's read fails here; no state used", int(tlv.len))
                return StatusReply()
            placed = _group_placement(group[0], pieces)
            if placed == "fail":
                _LOGGER.debug("status reply: colour group %d can't be placed among %s pieces: the app's "
                              "read fails here; no state used", group[0], pieces)
                return StatusReply()
            if placed == "place":
                groups.append(group)
            continue
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
        elif typ == TYPE_DEVICE_INFO:
            # Compose4BaseInfoSingleRead.w reads only the basic (0x10) and Wi-Fi (0x11) blocks here; it
            # logs any other selector and skips it (status_device_info.app_decodes).
            if not v.app_decodes:
                continue
            sel = int(v.selector)
            info = parse.device_info_from(sel, v.info)
            if info is None:
                continue
            if sel == _F.DeviceInfoSelector.wifi:
                # w():384-386: the Wi-Fi MAC is always set ("" when all zero). Its sw/hw versions are
                # the Wi-Fi module's (wifiSoftVersion / wifiHardVersion), apart from the device's own.
                st.wifi_mac = info.wifi_mac
            else:
                # w():405-427: the serial only from a valid UID; the versions always ("" when zero).
                if info.serial is not None:
                    st.serial_number = info.serial
                st.firmware_version, st.hardware_version = info.sw_version, info.hw_version
    # Same positional assembly as the direct group replies (oneGroupColorSize stride).
    st.segments = parse.direct_groups_to_segments(groups, _RECORDS_PER_GROUP)
    return st
