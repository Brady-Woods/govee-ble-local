"""Runtime parsing of inbound frames via the spec-generated readers.

Single-frame ``0xAA`` read replies and ``0xEE`` notifications are read as a
``govee_ble_frame(direction rx, profile)``: the reply's ``read_reply`` carries its ``govee_state``,
typed by the command and by the profile's device facts (the command forms, the colour-group rule and
the mode decode), which the frame doesn't carry. Each decoder below reads under the profile it needs:
the replies whose layout no device fact changes under :data:`_REPLY`, the mode report under the
device's own (``devices.profile.wire_profile``). Multi-packet ``0xAC``/``0xA1``/``0xA3``/``0xA4`` streams
are reassembled and walked in :mod:`.reassemble` (cross-frame de-chunking isn't expressible in
Kaitai).

Values are returned **as the device sends them** (no rescaling): brightness is the raw
reported byte, which every curated SKU sends as a 0..100 percent (a device reporting
0..255 would set ``DeviceProfile.brightness_max`` accordingly; scaling is the Device
layer's job), and power is the raw byte so plugs can read it as a relay bitmask, while
lights are on for a byte of 1, or of any non-zero value where their screen reads it so
(:func:`parse_light_power`).
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from ..const import FRAME_LEN
from ..models import Segment
from . import _spec
from ._spec import Common as _C, State as _S
from .build import INFO_BASIC, INFO_HARD, INFO_SN, INFO_WIFI, SUB_COLOR_0D, SUB_COLOR_15, SUB_SCENE
from .redact import shown_error, shown_hex

_LOGGER = logging.getLogger(__name__)

_Command = _C.Command

# One colour-group record as the group parsers return it: (brightness | None, r, g, b).
GroupRecord = tuple[int | None, int, int, int]

# The reply bodies the single-reply decoders read, as the library reads them: aa 34 is the H6057 device
# detail (form_34 device_detail), aa 36 the two bars (form_36 bar_pair: H6047), aa 41 the H6062 section
# count (form_41 section_count), aa a2 / aa a5 the mechanism-B batches (form_a2 bulb_batch_v1, form_a5
# bulb_batch_v2: H61A8). Nothing else here depends on a device fact.
_REPLY: Final = _spec.profile(
    f_34=int(_C.Form34.device_detail), f_36=int(_C.Form36.bar_pair), f_41=int(_C.Form41.section_count),
    f_a2=int(_C.FormA2.bulb_batch_v1), f_a5=int(_C.FormA5.bulb_batch_v2))


def bgc_encrypt_version(data: bytes) -> int:
    """The encrypt version a BGC-info characteristic value carries: the spec's lenient ``govee_bgc_info``
    ``effective_encrypt_version`` (BgcInfoReader.a / .d: format 1 or 2 -> byte 1, else 0; enum
    ``encrypt_version``: 0 not reported, 1 AES-RC4-PSK, 2 AES-GCM). A short format-2 value is read
    leniently, as the user chose, a documented difference from BaseBgcInfo.Companion.a:33."""
    return int(_spec.read(_spec.BgcInfo, data).effective_encrypt_version)


def parse_write_ack(frame: bytes) -> int | None:
    """The result byte of a device's ``33`` write ack (``write_ack.result``; 0 = success, ``is_ok``;
    AbsSingleController.t: value[2] == 0), else None. That is byte 2 for the app's default ``p()`` = 0;
    H6062 ControllerPos (``33 a6 01``) and H6048 CalibrationController4H6048 (``33 44``) set ``p()`` = 1,
    which moves it to byte 3 (``write_ack.uses_byte_3``): the library sends neither. A frame shorter
    than a whole ack (:data:`FRAME_LEN`) is None."""
    if len(frame) < FRAME_LEN:
        return None
    f = _spec.try_read_frame(frame, _spec.RX, _spec.PLAIN)
    if f is None or f.pro_type != _C.ProType.write:
        return None
    return int(f.body.result)


def multi_unit_comm(frame: bytes) -> int:
    """The comm byte of a built 0xA3 upload's START frame (``a3_packet`` index 0, ``a3_start.comm_byte``),
    read by the frame reader: the command a MultiController unit's ack carries
    (``AbsControllerNoEvent4Multi.isSameController``: proType 0xA3 and ``getCommandType``). ValueError for a
    frame that isn't an 0xA3 START."""
    f = _spec.try_read_frame(frame, _spec.TX, _spec.PLAIN)
    start = None if f is None or f.pro_type != _C.ProType.multi_write_v1 else getattr(f.body, "start", None)
    if start is None:
        raise ValueError(f"not an 0xA3 upload START: {shown_hex(frame)}")
    return int(start.comm_byte)


def parse_multi_ack(frame: bytes) -> bool | None:
    """Whether a MultiController unit's ``a3 <comm>`` ack accepts the unit (``upload_ack.is_multi_ok``: a
    whole 20-byte frame whose byte 3 is 0, ``AbsControllerNoEvent4Multi.onResult`` at ``MultiController.b()``
    = 3 for ``Companion.a`` / ``c``), or None for a frame that doesn't read as an 0xA3 reply."""
    f = _spec.try_read_frame(frame, _spec.RX, _spec.PLAIN)
    if f is None or f.pro_type != _C.ProType.multi_write_v1:
        return None
    return bool(f.body.is_multi_ok)


def _reply(frame: bytes, prof: Any = _REPLY) -> Any:
    """The ``read_reply`` body of a ``0xAA`` frame read under ``prof``, else None (another proType, or a
    reply that doesn't read)."""
    try:
        f = _spec.read_frame(frame, _spec.RX, prof)
    except Exception as exc:  # noqa: BLE001 - unknown enum / truncated frame -> not for us
        # A matched reply that won't parse is worth a breadcrumb (unknown enum / truncated).
        _LOGGER.debug("frame did not parse against the spec: %s (%s)", shown_hex(frame),
                      shown_error(frame, exc))
        return None
    return None if f.pro_type != _C.ProType.read else f.body


def _reply_state(frame: bytes, command: Any, prof: Any = _REPLY) -> Any:
    """The ``govee_state`` body of a ``0xAA`` read reply to ``command`` read under ``prof``, else None
    (another frame, a reply that doesn't read, or a state with no body)."""
    r = _reply(frame, prof)
    if r is None or r.command != command:
        return None
    return getattr(r.state, "body", None)


# ── single-command read replies ──────────────────────────────────────────────
def _head_command(frame: bytes, direction: int) -> tuple[Any, Any]:
    """The frame reader's ``pro_type`` and its body's ``command`` for ``frame``, each None when the frame
    is too short for it. Read even when the body after them doesn't parse: the reader sets each field as
    it goes."""
    f = _spec.frame_head(frame, direction, _spec.PLAIN)
    return getattr(f, "pro_type", None), getattr(getattr(f, "body", None), "command", None)


def request_command(frame: bytes) -> int:
    """The command of a built ``aa`` read request, read by the frame reader (``read_request.command``):
    the command its reply carries, which is all the app matches a reply on besides the proType
    (``AbsControllerNoEvent4Single.isSameController:176-179``). ValueError for a frame that isn't a read."""
    pro_type, command = _head_command(frame, _spec.TX)
    if pro_type != _C.ProType.read or command is None:
        raise ValueError(f"not an aa read request: {shown_hex(frame)}")
    return int(command)


def write_command(frame: bytes) -> int:
    """The command of a built ``33`` write, read by the frame reader (``write_command.command``): the
    command its ack carries, which the app matches the ack on besides the proType
    (``AbsControllerNoEvent4Single.isSameController:176-179``). ValueError for a frame that isn't a ``33``
    write with a command (a 2-byte frame is proType and checksum, with no body)."""
    pro_type, command = _head_command(frame, _spec.TX)
    if pro_type != _C.ProType.write or command is None:
        got = "no proType" if pro_type is None else f"proType 0x{int(pro_type):02X}"
        raise ValueError(f"not a 33 write with a command ({got}): {shown_hex(frame)}")
    return int(command)


def find_reply(frames: list[bytes], command_type: int) -> bytes | None:
    """First ``0xAA`` frame in ``frames`` whose command is ``command_type`` (the frame reader's
    ``pro_type`` and ``read_reply.command``), else None. A reply whose body doesn't parse is still
    that reply (its decoder then returns None)."""
    for reply in frames:
        pro_type, command = _head_command(reply, _spec.RX)
        if pro_type == _C.ProType.read and command is not None and command == command_type:
            return reply
    return None


def parse_power(frame: bytes) -> int | None:
    """AA 01 reply: the raw byte (``power_state.state``). Plugs read it as a relay bitmask; for a light
    use :func:`parse_light_power`."""
    b = _reply_state(frame, _Command.power)
    return None if b is None else int(b.state)


def parse_light_power(frame: bytes, prof: Any = _REPLY, *, heartbeat: bool = False) -> bool | None:
    """AA 01 reply on a light: the spec's ``power_state.is_on`` with no relay mask, by ``prof``'s power rule
    (``devices.profile.wire_profile``): on only when the byte is 1 (ControllerSwitch.Companion.c,
    ControllerSwitch.java:32, and the aa 01 heartbeat ShareVM.N3), the default, or at any non-zero byte
    under ``light_nonzero`` (the legacy SwitchController.parseValidBytes, the H70BX connect read
    AfterConnectSuccessReadDeviceInfo.w, and where the row's heartbeat rule says so, H1168's
    HeartControllerWithBattery: ``DeviceProfile.light_nonzero`` is the rule the poll follows); None if this
    is not an aa 01 reply. ``heartbeat``: the reply to the aa 01 keep-alive, read by the heartbeat's rule
    (``power_state.is_on_heartbeat``: a status row's, ``DeviceProfile.heartbeat_power``). Not for plugs,
    whose byte is a relay mask (:func:`parse_power`)."""
    b = _reply_state(frame, _Command.power, prof)
    return None if b is None else bool(b.is_on_heartbeat if heartbeat else b.is_on)


def parse_detail_power(frame: bytes) -> bool | None:
    """AA 34 reply on the H6057 (the device-detail read, form_34 ``device_detail``): on while the header's
    main switch shows on, the light open or white noise playing (the spec's ``device_detail.main_switch_on``;
    ``Controllers.e``, ``H6057DelegateHeader.E0``), or None if this is not an aa 34 reply that reads."""
    b = getattr(_reply_state(frame, _Command.cmd_34), "body", None)
    return None if b is None else bool(b.main_switch_on)


def parse_relay_on(frame: bytes, prof: Any) -> bool | None:
    """AA 01 reply on a plug: on when a bit of the profile's relay mask is set in the relay byte (the
    spec's ``power_state.is_on_heartbeat``: the aa 01 heartbeat rule, HeartControllerV2 ->
    BleOpV2.onEventHeartV2:216-231 / BleOp4H5089.onEventHeartV2:331-341; bit 0 on the single-outlet
    screens), or None if this is not an aa 01 reply. ``prof`` carries the mask
    (``devices.profile.wire_profile``)."""
    b = _reply_state(frame, _Command.power, prof)
    return None if b is None else bool(b.is_on_heartbeat)


def parse_brightness(frame: bytes) -> int | None:
    """AA 04 reply. The raw reported byte, not rescaled: 0..100 percent on every curated SKU
    (see ``DeviceProfile.brightness_max``; the Device layer does any normalisation)."""
    b = _reply_state(frame, _Command.brightness)
    return None if b is None else int(b.level)


def parse_bar_switch(frame: bytes) -> tuple[bool, bool] | None:
    """AA 36 reply: (left, right) bar power (``bar_state``). A bar is on only when its byte is 1, as the
    app reads it (h6047 ``ComposeLightController.parseValidBytes`` / ``ComposeLightHeartController``:
    ``bArr[0] == 1``, ``bArr[1] == 1``)."""
    b = getattr(_reply_state(frame, _Command.cmd_36), "body", None)
    return None if b is None else (bool(b.left_on), bool(b.right_on))


def parse_secret(frame: bytes) -> bytes | None:
    """AA B1 reply: 8-byte account-lock secret (``secret_state.is_valid``: selector 0x01)."""
    b = _reply_state(frame, _Command.secret_read)
    if b is None or not b.is_valid:
        return None
    return bytes(b.secret)


def parse_plug_spec(frame: bytes) -> int | None:
    """AA B3 reply: single spec-identifier byte (not the outlet count).

    Spec surface for tools and tests: Device does not read it."""
    b = _reply_state(frame, _Command.plug_spec)
    return None if b is None else int(b.spec)


def parse_gradual(frame: bytes) -> bool | None:
    """AA A3 reply (``gradual_state``): gradual/fade-on-handoff enabled iff byte0 == 1
    (``ControllerGradual4BleWifi.h``: ``bArr[0] == 1``; any other value is off)."""
    b = _reply_state(frame, _Command.gradual)
    return None if b is None else bool(b.is_on)


# ── mode (0x05) reports ──────────────────────────────────────────────────────
# Every mode decoder reads the spec's ``mode_report(decode)`` ([sub_mode][params]): an aa 05 reply's
# (:func:`mode_reply`) or a 0xAC TLV 0x05 value's, read in the status stream's root read
# (:func:`.reassemble.parse_status`), which carries the same body. The colour layout of each sub-mode is
# the device's mode decode (the profile's ``mode``: the SKU's Info4Detail parse,
# ``devices.profile.wire_profile``); :func:`mode_color` reads the colour off it.
_SUB_MUSIC: Final = int(_C.SubMode.music)   # 0x13

Rgb = tuple[int, int, int]
ColorReport = tuple[Rgb | None, int | None]   # (rgb, None) or (None, kelvin)


@dataclass(frozen=True)
class KelvinReport:
    """A colour report that carries only a Kelvin (SubMode4Color's ``[gradual][kelvin s16be]``): > 0 is
    colour-temperature mode, 0 or below is not (and carries no colour). A decode with no CCT test (the
    ``_nocct`` ones: the layout's ``is_cct`` is false) is never colour-temperature mode: 0."""
    kelvin: int


@dataclass(frozen=True)
class BarKelvin:
    """H6047's 0x15 report ``[gradual][kelvin left s16be][kelvin right s16be]``: one Kelvin per bar, 0 =
    that bar is not in colour-temperature mode (a missing right bar is 0)."""
    left: int
    right: int


@dataclass(frozen=True)
class HeadPair:
    """H7061's 0x15 report (goodsType 98, the spec's ``bri_rgb_pair``): ``(brightness, rgb)`` for each lamp
    head, the brightness a signed byte, as ``H70606162Info4Detail.I`` stores them (SubMode4Color's colours
    and relative brightness). It carries no colour temperature."""
    heads: tuple[tuple[int, Rgb], tuple[int, Rgb]]


ModeColor = KelvinReport | BarKelvin | HeadPair | ColorReport


def mode_reply(frame: bytes, prof: Any) -> Any:
    """The spec's ``mode_report`` of an ``AA 05`` reply read under ``prof`` (the device's mode decode),
    else None. A reply shorter than a whole frame is None where it is too short for the layout the
    library has read its sub-mode with (:func:`_too_short`)."""
    m = _reply_state(frame, _Command.mode, prof)
    return None if m is None or _too_short(m.sub_mode, bytes(frame[3:-1])) else m


def _too_short(sub: int, params: bytes) -> bool:
    """Whether a reply's mode ``params`` are too short for the layout the library has read sub-mode ``sub``
    with, and so drops the reply: 0x15 and 0x0d need three bytes, 0x13 four (seven with its colour flag
    set). Only a reply shorter than a whole frame can be; the spec reads every short form (``is_short``),
    so this is the library's rule, kept until the approval list decides it."""
    if sub in (SUB_COLOR_15, SUB_COLOR_0D):
        return len(params) < 3
    if sub == _SUB_MUSIC:
        return len(params) < 4 or (params[3] != 0 and len(params) < 7)
    return False


def sub_mode(m: Any) -> int:
    """A ``mode_report``'s sub-mode byte."""
    return int(m.sub_mode)


def scene_of(m: Any) -> int | None:
    """The active scene code of a ``mode_report`` (``scene_report.code``, u2le after sub-mode 0x04), None
    for another sub-mode."""
    if sub_mode(m) != SUB_SCENE:
        return None
    code = getattr(m.params, "code", None)
    return None if code is None else int(code)


def _rgb(color: Any) -> Rgb:
    return int(color.r), int(color.g), int(color.b)


def mode_color(m: Any, *, unsigned_kelvin: bool = False) -> ModeColor | None:
    """The colour a ``mode_report`` carries in the layout its profile's mode decode picked for its
    sub-mode, or None (another sub-mode, a sub-mode the decode leaves undecoded, a short layout):

    * ``gradual_kelvin`` (SubMode4Color's default parse): :class:`KelvinReport`, its Kelvin above 0 only
      where the layout's ``is_cct`` holds (never on the ``_nocct`` decodes);
    * ``kelvin_pair`` (H6047's bars): :class:`BarKelvin`;
    * ``rgb_kelvin_tint`` (``ModeParseStrategyConfig.a``): ``(None, kelvin)`` for a Kelvin > 0 (``is_cct``),
      the tint as the colour for a negative one (``shows_tint``), else the colour;
    * ``rgb_kelvin`` (``[r, g, b][kelvin s16be]``): ``(None, kelvin)`` for a Kelvin > 0, else the colour;
    * ``rgb_only`` (H6052InfoDetail.G): the colour;
    * ``bri_rgb_pair`` (H7061's two heads, ``H70606162Info4Detail.I``): :class:`HeadPair`.

    ``unsigned_kelvin`` is the library's default ``"rgb_kelvin"`` parse, which no app screen uses: the
    Kelvin read unsigned (``s2 & 0xFFFF``), and on the 0x0d tint layout ANY non-zero Kelvin is CCT
    (approval item A5 would read it signed)."""
    params = getattr(m, "params", None)
    layout = getattr(params, "layout", None) if isinstance(params, _S.ColorReport) else None
    if layout is None:
        return None
    # A field a short layout doesn't reach is an absent attribute (read with getattr).
    if isinstance(layout, _S.GradualKelvin):
        k = getattr(layout, "kelvin", None)
        if k is None:
            return None
        if unsigned_kelvin:
            return KelvinReport(int(k) & 0xFFFF)
        # is_cct: the decode's CCT test; a Kelvin it rejects is not CCT, so never above 0 (a nocct decode).
        return KelvinReport(int(k) if layout.is_cct else min(int(k), 0))
    if isinstance(layout, _S.KelvinPair):
        left, right = getattr(layout, "kelvin", None), getattr(layout, "kelvin_b", None)
        return None if left is None else BarKelvin(int(left), int(right or 0))
    if layout.is_short:
        return None
    if isinstance(layout, _S.RgbKelvinTint):
        k = int(layout.kelvin)
        if unsigned_kelvin:
            return (None, k & 0xFFFF) if k else (_rgb(layout.color), None)
        if layout.is_cct:
            return None, k
        return (_rgb(layout.tint) if layout.shows_tint else _rgb(layout.color)), None
    if isinstance(layout, _S.RgbKelvin):
        return (None, int(layout.kelvin)) if layout.is_cct else (_rgb(layout.color), None)
    if isinstance(layout, _S.RgbOnly):
        return _rgb(layout.color), None
    if isinstance(layout, _S.BriRgbPair):
        return HeadPair(((int(layout.bri), _rgb(layout.color)), (int(layout.bri_b), _rgb(layout.color_b))))
    return None   # a layout no profile in the library selects


# ── mechanism-B per-group colour read-back (H61A8; 0xAA 0xA2 V1 / 0xA5 V2) ──────
def parse_bulb_group_batch(frame: bytes) -> tuple[int, list[GroupRecord]] | None:
    """One mechanism-B batch reply (spec Change 7): ``bulb_batch_v2`` (0xA5, ``[brightness, r, g, b]``
    x 3) or ``bulb_batch_v1`` (0xA2, ``[r, g, b]`` x 4). Returns ``(batch_seq, groups)`` where each group
    is ``(brightness|None, r, g, b)`` — brightness is None for the V1 colour-only form. The segment index
    is POSITIONAL (assembled by the caller via :func:`bulb_groups_to_segments`), never carried in the
    frame."""
    r = _reply(frame)
    batch = getattr(getattr(getattr(r, "state", None), "body", None), "body", None)
    if batch is None:
        return None
    if r.command == _Command.color_group:        # V2 (0xA5): [brightness, r, g, b]
        return (int(batch.batch_seq),
                [(int(g.brightness), int(g.r), int(g.g), int(g.b)) for g in batch.records])
    if r.command == _Command.color_group_rgb:    # V1 (0xA2): [r, g, b]
        return int(batch.batch_seq), [(None, int(g.r), int(g.g), int(g.b)) for g in batch.records]
    return None


def group_base_index(group_index: int, group_size: int) -> int | None:
    """First segment index of a 1-based group: ``(group_index - 1) * group_size``.

    Returns None (and logs at DEBUG) for ``group_index < 1`` - device data is untrusted and
    a 0/garbage index would otherwise yield negative segment indices."""
    if group_index < 1:
        _LOGGER.debug("skipping colour group with invalid index %d", group_index)
        return None
    return (group_index - 1) * group_size


def _groups_to_segments(
    groups: Iterable[tuple[int, Sequence[GroupRecord]]], group_size: int
) -> list[Segment]:
    """Positional segments from 1-based groups: ``index = (group - 1) * group_size + k``."""
    segs: list[Segment] = []
    for group, records in groups:
        base = group_base_index(group, group_size)
        if base is None:
            continue
        for k, (brightness, r, g, b) in enumerate(records):
            segs.append(Segment(index=base + k, rgb=(r, g, b), brightness=brightness))
    return merge_segments(segs)


def bulb_groups_to_segments(
    batches: Iterable[tuple[int, Sequence[GroupRecord]]], per_batch: int
) -> list[Segment]:
    """Assemble mechanism-B batches into positional segments:
    ``index = (batch_seq - 1) * per_batch + i`` (per_batch is a client constant:
    V1 = 4, V2 = 3 — not frame-encoded)."""
    return _groups_to_segments(batches, per_batch)


def merge_segments(*groups: Iterable[Segment]) -> list[Segment]:
    """Merge segment records by ``index`` (later groups/records win) -> list sorted by index.
    Used both to assemble read-back batches and to fold a partial burst over known state."""
    merged: dict[int, Segment] = {}
    for group in groups:
        for seg in group:
            merged[seg.index] = seg
    return [merged[k] for k in sorted(merged)]


def direct_groups_to_segments(
    groups: Iterable[tuple[int, Sequence[GroupRecord]]], group_size: int
) -> list[Segment]:
    """Assemble mechanism-A-direct group replies (:func:`parse_direct_color_group` results)
    into positional segments: ``index = (group_index - 1) * group_size + k``."""
    return _groups_to_segments(groups, group_size)


# ── mechanism A-direct: per-group colour via Controller4ColorInfoByGroup ────────
# H60A6/H6047/H6641 share this reply (group size 4, confirmed independently for H6047 —
# "count=getColorPieceSize (=12), 4/group" — and H6641 — "4 records/group", devices.yaml).
# It is a DIFFERENT, unrelated controller from H61A8's BulbGroupColorV2/mechanism-B (group
# size 3) — they only share the wire opcode (0xAA read, command 0xA5), not the reply layout:
# the spec's form_a5 ``color_group`` vs ``bulb_batch_v2``. The frame is a fixed 20-byte zero-padded
# Govee frame like every other, with NO explicit length field (unlike the 0xAC TLV form of this
# same reply, whose record count can come from the TLV's own `len` byte), so a bare direct reply's
# record count can't be length-derived; the real app already knows the total count in advance
# (getColorPieceSize, or a live 0x40 IC read) and simply reads the fixed group size except a shorter
# final group. This mirrors that: the CALLER supplies `expected_records`, the ``fixed`` group rule's
# ``num_per_group``. The direct path places by request, so the group byte is not checked
# (Controller4ColorInfoByGroup.Companion.i).
_BATCH_REPLY_LEN: Final = FRAME_LEN - 4   # the shortest aa a5 frame a bulb_batch_v2 reply fits in


def parse_direct_color_group(
    frame: bytes, expected_records: int
) -> tuple[int, list[tuple[int, int, int, int]]] | None:
    """One ``AA A5 <group>`` direct reply: ``(group_index, records)`` where each record is
    ``(brightness, r, g, b)``. Returns at most ``expected_records`` (and at most what actually
    fits in the frame), never more — the caller determines how many are real for this group.

    Read by the spec's ``color_group`` (``Controller4ColorInfoByGroup.Companion.q`` over frame bytes
    2..18) under the ``fixed`` rule of ``expected_records`` records, capped at what fits in those bytes
    (``color_group.num_records``), where the app's read would throw. A frame too short for a whole batch
    reply is None, as before."""
    if len(frame) < _BATCH_REPLY_LEN:
        return None
    prof = _spec.profile(f_a5=int(_C.FormA5.color_group), group_rule=int(_C.GroupCountRule.fixed),
                         num_per_group=max(0, expected_records), has_brightness=True)
    group = getattr(_reply_state(frame, _Command.color_group, prof), "body", None)
    if group is None:
        return None
    return int(group.group_index), [
        (int(rec.brightness), int(rec.r), int(rec.g), int(rec.b)) for rec in group.records]


@dataclass(frozen=True)
class IcCount:
    """AA 40 reply (``ic_count`` / ControllerOnlyReadIcSegmentNum): the device's live IC (lamp-bead)
    count, read signed as the app reads it (``BleUtil.getSignedShort``, the spec's ``ic_count`` s2:
    negative for a reply of 0x8000 or more), and the reply's third byte as received.

    ``segment_count`` is that third byte, kept for compatibility: the app does not use it for H6641
    (h61d3 ``ReadIcController`` reads bytes 0..1 only). The piece and group counts are derived
    client-side from the IC count, as the app does (:func:`..devices._readback.live_ic_pieces`:
    ``ceil(clamp(IC, 41, 70) / 5)`` pieces, ``ceil(pieces / 4)`` groups; ``H61D3Support.e``)."""
    ic_count: int
    segment_count: int
    # ``ic_count`` as the app's checking readers return it (the spec's ``ic_count_checked``): None when
    # their byte-order check fires (``ic_count_byte_order_suspect``, 0x1000 or more), where the app's read
    # throws and stores no count.
    ic_count_checked: int | None = None
    # The two section bytes (2 and 5) summed as Java bytes, as the sectioned IC-pair reader passes them on
    # (the spec's ``section_sum``, ``ControllerIcNum.d``): None where that reader throws (``app_pair_fails``)
    # or the reply is under 6 bytes.
    section_sum: int | None = None
    # The IC counts the IC-pair reader returns (``ControllerIcNum.e``): ``(ic_count,)``, with ``ic_count_2``
    # after it where the reply carries a second pair (the spec's ``has_second_pair``), or None where that
    # reader throws (``app_pair_fails``).
    ic_pair: tuple[int, ...] | None = None


def parse_ic_count(frame: bytes) -> IcCount | None:
    """AA 40 reply: the live IC count, the reply's third byte, the section sum and the IC-pair reader's counts
    (see :class:`IcCount`)."""
    b = _reply_state(frame, _Command.ic_num)
    if b is None or getattr(b, "segment", None) is None:   # shorter than [ic_count s2][segment]
        return None
    checked, sections = b.ic_count_checked, b.section_sum
    second = (int(b.ic_count_2),) if b.has_second_pair else ()
    pair = None if b.app_pair_fails else (int(b.ic_count), *second)
    return IcCount(ic_count=int(b.ic_count), segment_count=int(b.segment),
                   ic_count_checked=None if checked is None else int(checked),
                   section_sum=None if sections is None else int(sections), ic_pair=pair)


@dataclass(frozen=True)
class SectionCount:
    """AA 41 reply on the H6062 (``striplight`` ``ControllerIcSegmentNum``): the device's IC
    (lamp-bead) count, read signed as the app reads it (negative for a reply of 0x8000 or more), and
    its ``sectionNum``. Only ``section_num`` drives control: the app
    stores it as the device's segment count (``ControllerStrategyConfig.X`` ->
    ``Info4BleIot.T``; ``VM4Light$connectBleSuc$1`` -> ``Info4BleIotDevice.P0``)."""
    ic_count: int
    section_num: int


def parse_section_count(frame: bytes) -> SectionCount | None:
    """AA 41 reply: ``[IC s16be, sectionNum s8]``, frame bytes 2..4, the spec's ``section_count`` under
    form_41 ``section_count`` (``ControllerIcSegmentNum.makeController4Read``: ``BleUtil.getSignedShort``
    of the first two, then the third as a Java byte, so 0x80 or more reads negative). 0x41 means this
    only on the ``striplight`` split; elsewhere it is the power memory."""
    sc = getattr(_reply_state(frame, _Command.cmd_41), "body", None)
    if sc is None:
        return None
    return SectionCount(ic_count=int(sc.ic_count), section_num=int(sc.section_num))


# ── device info (AA 07 10/11/02) ─────────────────────────────────────────────
def _version(v: Any) -> str | None:
    """Govee 3-byte version as the spec renders it (``version3.text``: "X.YY.ZZ" with each byte read
    signed, as Compose4BaseInfoSingleRead.v:363-368 renders it), or None for the 0.00.00 zero sentinel
    (``is_zero``: a device that doesn't populate this field, e.g. H60A6's aa 07 wifi reply)."""
    return None if v.is_zero else str(v.text)


_VERSION_TRIM = "".join(map(chr, range(0x21)))   # BleUtils.K trims every char <= 0x20


def _text(v: Any) -> str | None:
    """A ``version_text``'s ``text`` (cut at the first NUL by the reader, decoded as UTF-8), or None when
    its bytes are not UTF-8: the reply is then not one the library reads, as before."""
    try:
        return str(v.text)
    except UnicodeDecodeError as exc:
        _LOGGER.debug("frame did not parse against the spec: version text %s (%r)", bytes(v.raw).hex(), exc)
        return None


def _ascii_version(text: str) -> str | None:
    """A version string trimmed of control and space characters at both ends as the app does
    (BleUtils.K); None when nothing is left."""
    return text.strip(_VERSION_TRIM) or None


def _wifi_mac(info: Any) -> str | None:
    """A ``wifi_info``'s MAC as colon hex, or None when it is all zeros (``wifi_mac_is_invalid``)."""
    return None if info.wifi_mac_is_invalid else ":".join(f"{b:02X}" for b in bytes(info.wifi_mac))


def _uid_serial(info: Any) -> str | None:
    """A ``basic_info`` / ``uid_record`` 8-byte UID -> MAC-style colon hex (reversed) = the device id, as
    SnController renders it: an all-zero UID is invalid (``uid_is_invalid``; NumberUtil.isInvalidUuid ->
    None) and one leading ``00:00:`` is dropped (``uid_has_leading_pair``, SnController.parseValidBytes)."""
    if info.uid_is_invalid:
        return None
    parts = [f"{x:02X}" for x in reversed(bytes(info.uid))]
    return ":".join(parts[2:] if info.uid_has_leading_pair else parts)


@dataclass(frozen=True)
class DeviceInfo:
    """Device info from one ``aa 07`` reply or one ``0x07`` TLV of the 0xAC status reply (same
    ``device_info_record`` layout). Only the fields its selector carries are set: basic 0x10
    (serial, sw / hw, dsp), Wi-Fi 0x11 (Wi-Fi MAC and the Wi-Fi MODULE's sw / hw), SN 0x02
    (serial), hard version 0x03 (hw)."""
    serial: str | None = None
    wifi_mac: str | None = None
    sw_version: str | None = None
    hw_version: str | None = None
    dsp_version: int | None = None


def device_info_from(sel: int, info: Any) -> DeviceInfo | None:
    """Shared selector -> DeviceInfo mapping for a ``device_info_record``'s info (used by the single-frame
    aa 07 reply AND the 0xAC status 0x07 TLV — the same type)."""
    if sel == INFO_BASIC:
        return DeviceInfo(
            serial=_uid_serial(info),
            sw_version=_version(info.sw_version),
            hw_version=_version(info.hw_version),
            dsp_version=int(info.dsp_version),
        )
    if sel == INFO_WIFI:
        return DeviceInfo(
            wifi_mac=_wifi_mac(info),
            sw_version=_version(info.wifi_sw_version),
            hw_version=_version(info.wifi_hw_version),
        )
    if sel == INFO_SN:
        return DeviceInfo(serial=_uid_serial(info))
    if sel == INFO_HARD:
        text = _text(info)
        return None if text is None else DeviceInfo(hw_version=_ascii_version(text))
    return None   # 0x06 etc. — logged & ignored by the device


def parse_device_info_selector(frame: bytes) -> int | None:
    """The selector byte of an ``AA 07`` reply (``device_info_record.selector``), else None."""
    b = _reply_state(frame, _Command.device_info)
    return None if b is None else int(b.selector)


def parse_device_info(frame: bytes) -> DeviceInfo | None:
    """AA 07 reply: basic (0x10), wifi (0x11), SN (0x02) or hard-version (0x03) device info."""
    b = _reply_state(frame, _Command.device_info)
    info = None if b is None else getattr(b, "info", None)
    if b is None or info is None:
        return None
    return device_info_from(int(b.selector), info)


def parse_soft_version(frame: bytes) -> str | None:
    """AA 06 reply: the ASCII firmware (soft) version, e.g. ``1.00.41`` (None when empty)."""
    b = _reply_state(frame, _Command.soft_version)
    text = None if b is None else _text(b)
    return None if text is None else _ascii_version(text)


# ── notifications (0xEE) ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class Notify:
    """One decoded ``0xEE`` device push (:func:`parse_notify`). ``sub_type`` is the push sub-type
    byte; only the field that sub-type carries is set."""
    sub_type: int
    level: int | None = None                      # 0x20 brightness push (raw reported value)
    wifi_connected: bool | None = None            # 0x11 (status byte == 0)
    zone_flags: tuple[int, int, int] | None = None  # 0x30 flag bytes (family-interpreted)


def parse_notify(frame: bytes) -> Notify | None:
    """0xEE push (``notify``). Types the three sub-payloads the curated SKUs emit (0x20/0x11/0x30);
    other (cross-category) sub-types return None.

    Spec surface for tools and tests: Device does not consume 0xEE pushes (it polls), so nothing
    in the library calls this yet."""
    f = _spec.try_read_frame(frame, _spec.RX, _spec.PLAIN)
    if f is None or f.pro_type != _C.ProType.notify:
        return None
    sub = f.body.sub
    d: Any = getattr(f.body, "data", None)
    if sub == _C.NotifySub.brightness:
        return Notify(sub_type=int(sub), level=int(d.level))
    if sub == _C.NotifySub.wifi_connect:
        return Notify(sub_type=int(sub), wifi_connected=bool(d.is_connected))   # wifi_status: status == 0
    if sub == _C.NotifySub.switch_zone:
        return Notify(sub_type=int(sub), zone_flags=(int(d.flags_a), int(d.flags_b), int(d.flags_c)))
    return None
