"""Reassemble multi-packet 0xAC status bursts, then parse them via the spec model.

Only the cross-frame de-chunk is hand-done (Kaitai can't join frames). It is the app's
``AbsController4BleSingleSendMultiBack`` state machine (``.isSameController`` / ``.onResult`` ->
``.g`` / ``.i`` / ``.h``, as ``ControllerSingleRead`` runs it), each chunk read by the spec's
``status_chunk(has_sub)``: the FIRST chunk (index 0x00) is ``[0xAC, 0x00, packets u16le, lastLen, cmd,
sub, <12 data bytes>]``, every later chunk carries 17 data bytes at offset 2, and the last packet
(index 0xFF) carries only ``lastLen`` of them. A one-packet reply has no 0xFF chunk: its ``lastLen``
data bytes sit at offset 7. Chunks are taken only in order (:class:`BurstAssembler`), so a duplicate,
an early or a late chunk is ignored, and only a complete burst is joined: the app decodes nothing
from an incomplete one.

The joined buffer is then walked TLV by TLV, each read by the spec's ``status_tlv(profile)`` — a
``[tag][len][value]`` record (Compose4BaseInfoSingleRead.u) whose value is the ``govee_state`` of its
tag on the status path (switch / brightness / mode / zone / power memory / colour groups / 0x07
device-info). The offline analyzer (:mod:`.describe`) judges and walks each captured burst with the
same helpers (:func:`burst_chunks`, :func:`walk_status_tlvs`) and adds its stricter verdicts on top.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Final, Literal

from kaitaistruct import KaitaiStream, ValidationGreaterThanError

from ..const import FRAME_LEN
from ..models import Segment
from . import _spec, parse
from ._spec import Common as _C, Frame as _F
from .redact import shown_hex

_LOGGER = logging.getLogger(__name__)
_RECORDS_PER_GROUP = 4   # oneGroupColorSize (Support.getOneGroupColorSize): the index stride

# Chunk layout: the spec's status_chunk (AbsController4BleSingleSendMultiBack: the first chunk's header
# and 12 data bytes, every other chunk's index and 17 data bytes; the BCC follows).
MIN_CHUNK_LEN = FRAME_LEN - 1   # 19: a shorter record can't hold a whole chunk (it needs no BCC): skipped
# Compose4BaseInfoSingleRead.u reads a TLV's len byte signed (`int i10 = it2[i7]` from a byte[]):
# from this value up it is negative, `new byte[i10]` throws and the app's whole read fails. The spec's
# status_tlv rejects it (`len_value` valid max 0x7f), which is what the walk goes by; kept for callers.
SIGNED_LEN_MIN = 0x80

# TLV tags (enum command) the walk and the decode look at.
TYPE_SWITCH = int(_C.Command.power)              # 0x01
TYPE_BRIGHTNESS = int(_C.Command.brightness)     # 0x04
TYPE_MODE = int(_C.Command.mode)                 # 0x05
TYPE_DEVICE_INFO = int(_C.Command.device_info)   # 0x07
TYPE_ZONE = int(_C.Command.cmd_30)               # 0x30
TYPE_SEG_INFO = int(_C.Command.cmd_41)           # 0x41: the seg / IC info (power memory) TLV
TYPE_COLOR_GROUP = int(_C.Command.color_group)   # 0xA5
_TYPE_PADDING = int(_C.Command.heartbeat)        # 0x00: the trailing zero-pad sentinel
# The tags whose empty value the walk takes as a value that doesn't read, as the library has: the switch,
# brightness, mode, device-info, zone, seg-info and colour-group values each need a byte or more (the
# app's walker reads bArr[0] of 0x01 / 0x04; the VM lambda two bytes of 0x30 / 0x41; q() the group byte
# of 0xA5). The spec reads them and flags the app's failure (``govee_state.app_read_fails``) instead.
_NEEDS_VALUE: Final = frozenset({TYPE_SWITCH, TYPE_BRIGHTNESS, TYPE_MODE, TYPE_DEVICE_INFO, TYPE_ZONE,
                                 TYPE_SEG_INFO, TYPE_COLOR_GROUP})
# The brightness a successful status read applies when its stream has no 0x04 TLV: DeviceBaseInfoFromDevice's
# default (base-classes11 .../newdetail/ble/controller/DeviceBaseInfoFromDevice.java:70-79, f = 100).
_SUCCESS_BRIGHTNESS = 100


def status_profile(pieces: int | None) -> Any:
    """The ``govee_profile`` a status stream is read under: 0x30 as the zone bits (bit 0 of each byte,
    ``VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.o``), 0x41 as the power memory and 0xA5 as the
    colour groups, four ``[brightness, r, g, b]`` records a group. With the device's colour-piece count
    the groups follow the H60A6 screen's rule (``by_pieces``: the last of the ``ceil(pieces / 4)``
    groups holds the remainder, each placed by its group byte); ``pieces`` None (no device context, e.g.
    the analyzer) counts each group's records from its TLV's len (``from_len``)."""
    rule = _C.GroupCountRule.from_len if pieces is None else _C.GroupCountRule.by_pieces
    return _spec.profile(
        f_30=int(_C.Form30.zone_power), f_41=int(_C.Form41.power_memory),
        f_a5=int(_C.FormA5.color_group), group_rule=int(rule), num_per_group=_RECORDS_PER_GROUP,
        has_brightness=True, num_pieces=pieces or 0)


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
    negative_len: bool = False                 # status_tlv's `len_value` valid (max 0x7f) rejected it


def read_ac_chunk(frame: bytes) -> Any:
    """A 0xAC reply chunk read by the spec's ``status_chunk(true)`` (the frame's status_read_rx body: the
    first chunk's index 0, ``num_packets`` u16le (``BleUtils.H(.., false)``), ``len_last``, command, sub
    and 12 data bytes; every other chunk's index and 17), read off the 18 bytes after the proType, so a
    record without its BCC still reads. None for a first chunk whose ``len_last`` doesn't fit its packet
    (``len_last_fits``: AbsController4BleSingleSendMultiBack.g returns -1 and the app rejects the whole
    burst). The caller has checked the record is at least :data:`MIN_CHUNK_LEN` bytes."""
    chunk = _spec.read(_F.StatusChunk, frame[1:MIN_CHUNK_LEN], True)
    return None if chunk.is_first and not chunk.len_last_fits else chunk


@dataclass
class BurstAssembler:
    """The app's 0xAC burst assembly, frame by frame: ``AbsController4BleSingleSendMultiBack`` as
    ``ControllerSingleRead`` runs it for the status read (``Compose4BaseInfoSingleRead.java:230``).

    ``feed`` takes one received record and returns whether the app would take it. While no chunk has
    been taken (``backPos`` 0), only a first chunk (index 0) whose command and sub are the request's is
    taken (``isSameController:204-205`` -> ``f():100-103``: 3 and 0, ``ControllerSingleRead.java:82,
    89-95``; the spec's ``is_single_read_head``); then only the next index in order, or the last packet -
    index 0xFF, or the index ``packets - 1`` - once every packet before it is in
    (``isSameController:207-222``). Anything else (a duplicate, an early or out-of-order chunk, an early
    0xFF: "END_PKG_ORDER") is ignored, and the read goes on waiting. ``status`` becomes ``"complete"``
    with the last packet (``h()``; a one-packet reply at once, ``g():134-141``) and ``"failed"`` when the
    first chunk's lastLen does not fit its packet (``g()`` returns -1: the read fails at once). An
    incomplete burst stays ``"waiting"``: the app times out and applies nothing
    (``AbsComposeControllerWithOpResult.onOvertime``).

    The library checks lastLen before command and sub, while the app checks command and sub first, so a
    first chunk with both a bad lastLen and another command fails the read here where the app would
    ignore it (approval item A7; no capture has one)."""
    command: int = int(_F.StatusCommand.status_read)
    sub: int = 0
    chunks: list[bytes] = field(default_factory=list)       # the chunks taken, in order
    status: Literal["waiting", "complete", "failed"] = "waiting"
    conflicts: int = 0    # chunk indices that arrived again with DIFFERENT bytes (diagnostics)
    _back_pos: int = field(default=0, repr=False)            # backPos: the next index expected
    _total: int = field(default=-1, repr=False)              # allBackPkgs, from the first chunk
    _seen: dict[int, bytes] = field(default_factory=dict, repr=False)

    def feed(self, fr: bytes) -> bool:
        """Offer one record; True when the app's state machine takes it."""
        if len(fr) < MIN_CHUNK_LEN or fr[0] != _C.ProType.status_read:
            _LOGGER.debug("status burst: skipping a record that is not a chunk (%d bytes)", len(fr))
            return False
        chunk = read_ac_chunk(fr)
        index = 0 if chunk is None else int(chunk.index)   # only a first chunk can fail len_last_fits
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
                              "%s", shown_hex(fr))
                self.status = "failed"
                return True
            if int(chunk.command) != self.command or int(chunk.sub) != self.sub:
                return False
            self._total, self._back_pos = int(chunk.num_packets), 1
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
    reply's lastLen bytes alone (``len_first_data``). An incomplete burst joins to nothing: the app
    decodes none of it."""
    if not chunks or not complete:
        return b""
    head = read_ac_chunk(chunks[0])   # burst_chunks only hands over a first chunk that reads
    buf = bytearray(bytes(head.data)[: int(head.len_first_data)])
    if int(head.num_packets) == 1:
        return bytes(buf)
    for fr in chunks[1:-1]:
        buf += bytes(read_ac_chunk(fr).data)
    buf += bytes(read_ac_chunk(chunks[-1]).data)[: int(head.len_last)]
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
    buf: bytes, *, pieces: int | None = None, skipped: list[TlvCut] | None = None,
) -> tuple[list[Any], TlvCut | None]:
    """Walk a reassembled TLV buffer as the app's walker steps it, each TLV read by the spec's
    ``status_tlv`` under :func:`status_profile` (``pieces``): ``(tlvs, cut)``.

    ``Compose4BaseInfoSingleRead.u`` (:247-345) reads ``[type][len][value]`` and steps
    ``i += 2 + len``. ``tlvs`` are the ``status_tlv`` objects it hands on, in stream order; ``cut`` says
    where and why the walk stopped before the end (None when it reached the end):

    - ``"tail"``: the buffer ends before a TLV's value starts (no len byte, or no byte after it,
      whatever the len). u() stops cleanly there (:257-267, :271-282) and the read succeeds with what
      it walked, so this is not a failure.
    - ``"unparsed"``: u() or a handler throws, which fails the app's whole read: a len byte of
      :data:`SIGNED_LEN_MIN` or more (read signed, negative; the spec's ``status_tlv`` ``len_value`` valid
      max 0x7f: ``negative_len``), or a typed value that doesn't read (the reader rejecting it stands in
      for the handler throwing: ``u`` reads ``bArr[0]`` for 0x01 / 0x04; ``w`` copies 16 / 12 bytes after
      a 0x10 / 0x11 selector; the VM lambda reads two 0x30 / 0x41 bytes), an empty one included
      (:data:`_NEEDS_VALUE`).
    - ``"truncated"``: a value runs past the buffer (u()'s ``System.arraycopy`` throws).

    A 0x07 block reads as the spec's ``device_info_record`` on the status path, which types only the
    basic 0x10 and Wi-Fi 0x11 selectors the app's ``w()`` decodes and leaves any other raw
    (``app_decodes``), so it is handed on like any TLV. A 0xA5 colour group is handed on whatever its
    records (an empty one is also recorded in ``skipped``): :func:`parse_status` decides by the spec's
    group rule.

    A 0x05 mode TLV whose value doesn't read is handed on raw too, as long as it holds a sub-mode byte:
    the library reads it padded as an aa 05 reply's body (:func:`.parse.mode_value`; approval items A2 /
    A3 would follow the app's per-sub-mode minimums).

    Known differences that remain (approval item A1): a type-0 byte ends the walk here (the trailing
    padding), while u() reads it as an ordinary TLV (handed to the VM's fallback lambda, which ignores it)
    and walks on; and the sleep / wake-up / timer TLVs (0x11 / 0x12 / 0x23) go to per-SKU TimerConfig /
    ControllerNewTimer parsers the library doesn't model. No capture shows either."""
    prof = status_profile(pieces)
    tlvs: list[Any] = []
    n = len(buf)
    stream = KaitaiStream(BytesIO(buf))
    pos = 0
    while pos < n:
        stream.seek(pos)
        tlv: Any = _spec.StatusStream.StatusTlv(prof, stream)
        err: Exception | None = None
        try:
            tlv._read()
        except Exception as e:  # noqa: BLE001 - judged below, in the app's order
            err = e
        typ = int(tlv.tag)
        length = getattr(tlv, "len_value", None)
        if typ == _TYPE_PADDING:   # the trailing zero-pad sentinel (see the docstring)
            break
        if pos + 2 >= n:
            return tlvs, TlvCut("tail", typ, pos, None if length is None else int(length))
        if isinstance(err, ValidationGreaterThanError):
            return tlvs, TlvCut("unparsed", typ, pos, int(tlv.len_value), negative_len=True)
        raw = getattr(tlv, "_raw_value", None)
        if err is None and typ in _NEEDS_VALUE and length == 0:
            err = ValueError("empty value")
        if err is not None:
            if length is None or raw is None:
                return tlvs, TlvCut("truncated", typ, pos, None if length is None else int(length))
            if typ == TYPE_MODE and len(raw) > 0:
                _LOGGER.debug("status reply: mode TLV %s did not read as mode_report; kept raw",
                              bytes(raw).hex())
            elif typ != TYPE_COLOR_GROUP:
                return tlvs, TlvCut("unparsed", typ, pos, int(length))
            else:   # parse_status fails the read on it (app_read_fails)
                _LOGGER.debug("status reply: empty colour group TLV")
            if skipped is not None:
                skipped.append(TlvCut("unparsed", typ, pos, int(length)))
        tlvs.append(tlv)
        pos += int(tlv.total_len)
    return tlvs, None


_GroupRead = tuple[int, list[parse.GroupRecord]] | Literal["skip", "fail"]


def _color_group(tlv: Any, pieces: int | None) -> _GroupRead:
    """One 0xA5 TLV's ``color_group`` (``Controller4ColorInfoByGroup.Companion.q:160-175``, its record
    count by the group rule, :func:`status_profile`): ``(group_index, records)`` to place, ``"skip"`` for
    a group the app copies nothing from, or ``"fail"`` where the app's read fails: an empty value, one
    short of its record count, or a group SubMode4Color.j can't place (group 0, or one starting past the
    last piece: ``app_rejects_group``; VM4LightH60A6$...$1.o / .p:126-142). With no pieces (0) the
    library has placed group 0 and dropped it, as before."""
    group = getattr(getattr(tlv.value, "body", None), "body", None)
    if group is not None and group.app_rejects_group and pieces == 0 and group.group_index == 0 \
            and not group.is_short:
        return "skip"
    if tlv.value.app_read_fails or group is None:
        return "fail"
    if not group.is_placed:
        return "skip"
    return int(group.group_index), [
        (int(rec.brightness), int(rec.r), int(rec.g), int(rec.b)) for rec in group.records]


def parse_status(frames: list[bytes], *, pieces: int | None = None) -> StatusReply:
    """Reassemble a 0xAC burst and decode it via the spec's ``status_tlv`` reader.

    Only the cross-frame de-chunk (:func:`reassemble`) is hand-done — Kaitai can't join
    frames. The reassembled buffer is then walked (:func:`walk_status_tlvs`), each TLV's value the
    ``govee_state`` of its tag on the status path — switch / brightness / zone / seg-info / colour groups
    / ``0x07`` device-info. No client-side TLV or value hand-parsing.

    Only a complete burst is decoded (:func:`burst_chunks`): the app applies nothing from one that lost
    a chunk or its last packet, or whose chunks came out of order. A walk that stops at the buffer's
    tail (``"tail"``) keeps what it read, as u() does.

    Segment global index = ``(group_index-1) * 4 + k`` (oneGroupColorSize stride). Each colour group
    is read by the spec's ``color_group`` with the app's record count for ``pieces``, the device's
    colour-piece count (the H60A6's 13, ``pact_h60a0.pact.Support.getColorPieceSize``), so extra
    records are ignored and a group short of its count fails the read, as in the app. With ``pieces``, a
    group the app can't place fails the read too: group 0, or one starting past the last piece
    (``SubMode4Color.j:113-147`` throws; that includes every group byte of 0x80 or more), while a group
    starting exactly at the end (``pieces`` a multiple of 4) is a no-op and skipped
    (``color_group.is_placed``). ``pieces`` None takes the count from the TLV len and drops group 0.
    Device-info: the last block wins, as the app's setters do (``Compose4BaseInfoSingleRead.w:370-437``):
    a basic 0x10 block sets the serial when its UID is valid and always sets the versions (None when
    zero); a Wi-Fi 0x11 block always sets the Wi-Fi MAC (None when zero). The 0x11 block's versions are
    the Wi-Fi module's, so they never fill the device's own, and other selectors are skipped, as w()
    does (``device_info_record.app_decodes``).

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
    tlvs, cut = walk_status_tlvs(buf, pieces=pieces)
    if cut is not None and cut.reason != "tail":
        _LOGGER.debug("status reply %s at TLV 0x%02x (offset %d of %d bytes): the app's read fails "
                      "here; no state used", cut.reason, cut.type, cut.offset, len(buf))
        return st

    groups: list[tuple[int, list[parse.GroupRecord]]] = []
    for tlv in tlvs:
        typ = int(tlv.tag)
        if typ == TYPE_COLOR_GROUP:
            group = _color_group(tlv, pieces)
            if group == "fail":
                _LOGGER.debug("status reply: colour group TLV (len %d) is short of the app's record count "
                              "or can't be placed among %s pieces: the app's read fails here; no state used",
                              int(tlv.len_value), pieces)
                return StatusReply()
            if group != "skip":
                groups.append(group)
            continue
        v: Any = getattr(tlv.value, "body", None)
        if typ == TYPE_SWITCH:
            # power_state.is_on on the status path: != 0, as the app's status walker reads it
            # (Compose4BaseInfoSingleRead.u), unlike a light's aa 01 reply (== 1; approval item A4).
            st.is_on = bool(v.is_on)
        elif typ == TYPE_BRIGHTNESS:
            st.brightness = int(v.level)
        elif typ == TYPE_MODE:
            st.mode = bytes(tlv._raw_value)   # [sub_mode, params]: decoded by the device's mode decode
        elif typ == TYPE_ZONE:
            # bit0 of each byte (VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.o:
            # ParseUtils.getBit2Boolean(b, 1) = (b >> 0) & 1); the other bits are not zone power.
            st.zone_power = {0: bool(v.body.zone_a_on), 1: bool(v.body.zone_b_on)}   # zone_bits: bit 0
        elif typ == TYPE_DEVICE_INFO:
            # Compose4BaseInfoSingleRead.w reads only the basic (0x10) and Wi-Fi (0x11) blocks here; it
            # logs any other selector and skips it (device_info_record.app_decodes).
            if not v.app_decodes:
                continue
            sel = int(v.selector)
            info = parse.device_info_from(sel, v.info)
            if info is None:
                continue
            if sel == _C.DeviceInfoSelector.wifi:
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
    # A read that succeeded applies the switch and brightness whether or not the stream carried them: each
    # read starts from a fresh DeviceBaseInfoFromDevice (switch false, brightness 100; its constructor),
    # which u() fills from the 0x01 / 0x04 TLVs, and the success callback hands it to h60a1 Info4Detail.n0
    # (:641-677: g(info.j()) the switch, i = info.b() the brightness;
    # VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.l).
    if st.is_on is None:
        st.is_on = False
    if st.brightness is None:
        st.brightness = _SUCCESS_BRIGHTNESS
    return st
