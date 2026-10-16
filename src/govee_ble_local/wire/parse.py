"""Runtime parsing of inbound frames via the shipped Kaitai reader.

Single-frame ``0xAA`` read replies and ``0xEE`` notifications are decoded through the
spec-generated ``GoveeBleFrame`` reader: a whole frame through the frame type, or, where the
right type depends on context the frame doesn't carry (the SKU's parse strategy, the caller's
record count), by handing the body to the parametric type the spec defines for it
(``mode_color_0d_typed``, ``color_group_read``). Two replies are still hand-decoded, each
because the spec has no type for it yet: the ``aa 05 04`` scene code (``mode_read`` has no
0x04 case) and the H6062 ``aa 41`` section count (0x41 is ``on_off_memory`` in the shared
command enum). Multi-packet ``0xAC``/``0xA1``/``0xA3``/``0xA4`` streams are reassembled and walked
in :mod:`.reassemble` (cross-frame de-chunking isn't expressible in Kaitai).

Values are returned **as the device sends them** (no rescaling): brightness is the raw
reported byte, which every curated SKU sends as a 0..100 percent (a device reporting
0..255 would set ``DeviceProfile.brightness_max`` accordingly; scaling is the Device
layer's job), and power is the raw byte so plugs can read it as a relay bitmask, while
lights are on only for a byte of 1 (:func:`parse_light_power`).
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Final

from kaitaistruct import KaitaiStream

from ..const import FRAME_LEN
from ..models import Segment
from .build import (
    CMD_MODE,
    CMD_SECTION_COUNT,
    INFO_BASIC,
    INFO_HARD,
    INFO_SN,
    INFO_WIFI,
    PRO_READ,
    SUB_COLOR_0B,
    SUB_COLOR_0D,
    SUB_COLOR_15,
    SUB_SCENE,
    ColorScheme,
    _F,
    _load,
)

_LOGGER = logging.getLogger(__name__)

# The generated reader (imported once, in :mod:`.build`) is untyped, so attribute chains are Any and
# need no stubs. :mod:`.reassemble` and :mod:`.describe` reuse these names.
_ProType = _F.ProType
_Command = _F.Command
_SubMode = _F.SubMode
_NotifySub = _F.NotifySub

# One colour-group record as the group parsers return it: (brightness | None, r, g, b).
GroupRecord = tuple[int | None, int, int, int]


def _parse_type(cls: Any, data: bytes, *params: Any) -> Any:
    """Read ``data`` as the generated type ``cls`` (``params`` = its ksy params, if any).
    Raises whatever the generated reader raises on malformed input."""
    return _load(cls(*params, KaitaiStream(BytesIO(bytes(data)))))


def _parse(frame: bytes) -> Any:
    """Parse a 20-byte frame, or return None on any malformed/unknown-enum input."""
    if len(frame) < 2:
        return None
    try:
        return _load(_F.from_bytes(bytes(frame)))
    except Exception as exc:  # noqa: BLE001 - unknown enum / truncated frame -> not for us
        # A matched reply that won't parse is worth a breadcrumb (unknown enum / truncated).
        _LOGGER.debug("frame did not parse against the spec: %s (%r)", bytes(frame).hex(), exc)
        return None


def _read_reply(frame: bytes, command: Any) -> Any:
    """Return the typed body of a ``0xAA`` read reply matching ``command``, else None."""
    f = _parse(frame)
    # A read frame always parses to ReadCommand, which sets ``command`` (raw int when unknown).
    if f is None or f.pro_type != _ProType.read or f.body.command != command:
        return None
    return f.body.body


# ── single-command read replies ──────────────────────────────────────────────
def find_reply(frames: list[bytes], command_type: int) -> bytes | None:
    """First ``0xAA`` frame in ``frames`` whose command byte is ``command_type``, else None."""
    for reply in frames:
        if len(reply) >= 2 and reply[0] == PRO_READ and reply[1] == command_type:
            return reply
    return None


def parse_power(frame: bytes) -> int | None:
    """AA 01 reply: the raw byte. Plugs read it as a relay bitmask; for a light use
    :func:`parse_light_power`."""
    b = _read_reply(frame, _Command.switch)
    return None if b is None else int(b.state)


def parse_light_power(frame: bytes) -> bool | None:
    """AA 01 reply on a light: on only when the byte is 1, the spec's ``switch_read_reply.light_is_on``
    (ControllerSwitch.Companion.c, ControllerSwitch.java:32, and the aa 01 heartbeat ShareVM.N3), or None
    if this is not an aa 01 reply. Not for plugs, whose byte is a relay mask (:func:`parse_power`)."""
    b = _read_reply(frame, _Command.switch)
    return None if b is None else bool(b.light_is_on)


def parse_brightness(frame: bytes) -> int | None:
    """AA 04 reply. The raw reported byte, not rescaled: 0..100 percent on every curated SKU
    (see ``DeviceProfile.brightness_max``; the Device layer does any normalisation)."""
    b = _read_reply(frame, _Command.brightness)
    return None if b is None else int(b.brightness)


def parse_bar_switch(frame: bytes) -> tuple[bool, bool] | None:
    """AA 36 reply: (left, right) bar power. A bar is on only when its byte is 1, as the app reads
    it (h6047 ``ComposeLightController.parseValidBytes`` / ``ComposeLightHeartController``:
    ``bArr[0] == 1``, ``bArr[1] == 1``)."""
    b = _read_reply(frame, _Command.compose_light_switch)
    return None if b is None else (int(b.left) == 1, int(b.right) == 1)


def parse_secret(frame: bytes) -> bytes | None:
    """AA B1 reply: 8-byte account-lock secret (selector 0x01)."""
    b = _read_reply(frame, _Command.secret_read)
    if b is None or int(b.selector) != 0x01:
        return None
    return bytes(b.secret)


def parse_plug_spec(frame: bytes) -> int | None:
    """AA B3 reply: single spec-identifier byte (not the outlet count).

    Spec surface for tools and tests: Device does not read it."""
    b = _read_reply(frame, _Command.plug_spec)
    return None if b is None else int(b.spec)


def parse_gradual(frame: bytes) -> bool | None:
    """AA A3 reply (gradual_read): gradual/fade-on-handoff enabled iff byte0 == 1
    (``ControllerGradual4BleWifi.h``: ``bArr[0] == 1``; any other value is off)."""
    b = _read_reply(frame, _Command.gradual_wifi_ble)
    return None if b is None else int(b.state) == 1


# ── mode (0x05) read replies ─────────────────────────────────────────────────
def _mode_read(frame: bytes) -> Any:
    """Typed body of an ``AA 05`` mode read reply, else None."""
    return _read_reply(frame, _Command.mode)


def parse_active_scene(frame: bytes) -> int | None:
    """AA 05 04 reply: active scene code (u2le), or None if not in scene sub-mode."""
    m = _mode_read(frame)
    if m is None or int(m.selector_or_sub_mode) != SUB_SCENE:
        return None
    rest = bytes(m.rest)
    if len(rest) < 2:
        return None
    return rest[0] | (rest[1] << 8)


def parse_kelvin(frame: bytes) -> int | None:
    """AA 05 15 01 reply: colour temperature in Kelvin (u2be), or None."""
    m = _mode_read(frame)
    if m is None or int(m.selector_or_sub_mode) != SUB_COLOR_15:
        return None
    return int(m.rest.kelvin)


def parse_bar_kelvin(frame: bytes) -> tuple[int, int] | None:
    """H6047 ``AA 05 15`` reply: ``(left kelvin, right kelvin)``, 0 = that bar is not in
    colour-temperature mode, or None if this is not a 0x15 mode reply.

    The H6047 layout is ``[gradual][kelvin left u2be][kelvin right u2be]``
    (``SubModeColor.parse``; ``Info4Detail`` ``subMode4Color`` parse strategy). The app shows a
    whole-device kelvin only when both bars match; that rule lives in the Device layer."""
    m = _mode_read(frame)
    if m is None or int(m.selector_or_sub_mode) != SUB_COLOR_15:
        return None
    right = getattr(m.rest, "kelvin_right", None)
    return int(m.rest.kelvin), int(right or 0)


def _mode_0d_kind2(frame: bytes) -> tuple[tuple[int, int, int] | None, int | None] | None:
    """An ``AA 05 0D`` reply read by the spec's ``mode_color_0d_typed(kind=2)`` over the body between
    the sub-mode byte and the BCC: ``[r, g, b, kelvin u2be, tint r, g, b]``. ANY non-zero kelvin
    (``is_cct``) -> ``(None, kelvin)``; kelvin 0 -> ``((r, g, b), None)``. None when this is not a 0x0d
    mode reply or its body is too short for the type (a reply is a 20-byte frame)."""
    if len(frame) < 3 or frame[0] != PRO_READ or frame[1] != CMD_MODE or frame[2] != SUB_COLOR_0D:
        return None
    try:
        body = _parse_type(_F.ModeColor0dTyped, frame[3:FRAME_LEN - 1], _MODE_0D_KIND_RGB_KELVIN_TINT)
    except Exception as exc:  # noqa: BLE001 - truncated body -> not a usable report
        _LOGGER.debug("0x0d report did not parse as kind 2: %s (%r)", bytes(frame).hex(), exc)
        return None
    if body.is_cct:
        return None, int(body.report_kelvin)
    return (int(body.r), int(body.g), int(body.b)), None


def parse_mode_0d(frame: bytes) -> tuple[tuple[int, int, int] | None, int | None] | None:
    """AA 05 0D reply read as ``[r, g, b, kelvin u2be, tint r, g, b]`` - the same layout as the 0x0d
    write. ANY non-zero kelvin is colour-temperature mode -> ``(None, kelvin)``; kelvin 0 ->
    ``((r, g, b), None)``.

    This is the 0x0d decoder of the default ``"rgb_kelvin"`` mode parse
    (``DeviceProfile.mode_color_parse``), which no 0x0d-scheme SKU in the profile table uses: the
    bulbs (H6006/H6008/H6004) and H6188 decode their report as the app does with
    :func:`parse_mode_rgb_kelvin_tint`, and H6052 with :func:`parse_mode_0d_rgb`. It does not require
    white (ff ff ff) in the colour slot: the app's CCT write (``Config.bu`` -> ``Bytes.p``) puts the
    tint there.

    Read by the spec's ``mode_color_0d_typed`` kind 2, the same decode as
    :func:`parse_mode_0d_rgblight`, so the body must hold its 8 bytes (a reply is a 20-byte frame).
    Which kind the default strategy should use is open in the spec (``devices.yaml`` gives H60A6 no
    ``mode_0d_kind``; kind 0 is ``[gradual][kelvin]``)."""
    return _mode_0d_kind2(frame)


# mode_color_0d_typed `kind`s (devices.yaml mode_0d_kind): 1 = rgb (H6052), 2 = rgb_kelvin_tint.
_MODE_0D_KIND_RGB = 1
_MODE_0D_KIND_RGB_KELVIN_TINT = 2
# The aa 05 colour sub-modes OpInfo4Detail.parseModeValidBytes hands to the SKU's SubMode4Color parse
# strategy (``b6 == 21 || b6 == 11 || b6 == 2 || b6 == 13``); it sends every other sub-mode elsewhere.
SUBMODE4COLOR_SUB_MODES: Final = frozenset(
    {SUB_COLOR_15, SUB_COLOR_0B, int(_SubMode.color_legacy), SUB_COLOR_0D})
# BleUtil.getSignedShort: a big-endian Kelvin of 0x8000 or more reads as negative.
_SIGNED_SHORT_MIN: Final = 0x8000


def parse_mode_rgb_kelvin_tint(frame: bytes) -> tuple[tuple[int, int, int] | None, int | None] | None:
    """AA 05 colour report on the ``ModeParseStrategyConfig.a`` SKUs (the 0x0d bulbs H6006 / H6008 /
    H6004 and the rgblight H6188), decoded as the app does: ``((r, g, b) | None, kelvin | None)``,
    or None when this is not such a report.

    App path (Govee Home 7.7.02): bulblight ``AcNewDetail`` -> ``VM4Light`` (``$connectBleSuc$1.l``
    -> ``m5`` -> ``j5`` installs ``ModeParseStrategyConfig.a``, the non-H6001 branch) and rgblight
    ``VM4Light`` (``$connectBleSuc$1.l`` -> ``A5`` -> ``v5`` installs ``ModeParseStrategyConfig.a.a()``
    for H6188), both then handing the reply to ``OpInfo4Detail.parseModeValidBytes`` (``$default`` ->
    ``OpInfo4Detail.x``). That sends sub-modes 0x15, 0x0b, 0x02 and 0x0d alike
    (:data:`SUBMODE4COLOR_SUB_MODES`) to the strategy, which reads the body after the sub-mode byte
    as ``[r, g, b][kelvin s16be][tint r, g, b]`` (the spec's ``mode_color_0d_typed`` kind 2):
    ``s(getSignedShort(vb[3], vb[4]))``, and the colour is the tint ``vb[5..7]`` when that kelvin is
    non-zero, else ``vb[0..2]``. parseModeValidBytes then takes colour-temperature mode only for a
    kelvin > 0. So: kelvin > 0 -> ``(None, kelvin)``; kelvin 0 -> ``((r, g, b), None)``; a negative
    kelvin (0x8000 or more, which the bulbs' firmware never stores) -> ``((tint r, g, b), None)``.

    Read through the generated kind-2 reader, so the body must hold its 8 bytes (a reply is a
    20-byte frame)."""
    if len(frame) < 3 or frame[0] != PRO_READ or frame[1] != CMD_MODE:
        return None
    if frame[2] not in SUBMODE4COLOR_SUB_MODES:
        return None
    try:
        body = _parse_type(_F.ModeColor0dTyped, frame[3:FRAME_LEN - 1], _MODE_0D_KIND_RGB_KELVIN_TINT)
    except Exception as exc:  # noqa: BLE001 - truncated body -> not a usable report
        _LOGGER.debug("colour report did not parse as kind 2: %s (%r)", bytes(frame).hex(), exc)
        return None
    kelvin = int(body.report_kelvin)
    if kelvin >= _SIGNED_SHORT_MIN:
        return (int(body.tint_r), int(body.tint_g), int(body.tint_b)), None
    if kelvin:
        return None, kelvin
    return (int(body.r), int(body.g), int(body.b)), None


def parse_mode_0d_rgb(frame: bytes) -> tuple[int, int, int] | None:
    """AA 05 0D reply on H6052: its ``(r, g, b)``, or None when this is not a 0x0d report.

    App path (tablelampv1, goodsType 22): ``Model.u`` -> ``H6052NewDetailActivity`` ->
    ``H6052NewDetailVM`` (``$connectBleSuc$1`` and the heartbeat both call ``H6052InfoDetail.G``).
    G decodes a colour only for sub-mode 0x0d, with the strategy the H6052InfoDetail constructor
    installs: ``ColorUtils.G(vb[0], vb[1], vb[2])`` and nothing more (the spec's
    ``mode_color_0d_typed`` kind 1). The Kelvin bytes that follow are never read. (G then shows a
    Kelvin only when the app's own last write left one in its in-memory SubMode4Color; that is not
    a decode of the report and is not modelled.) Every other colour sub-mode, 0x15 included, is
    "not support" there."""
    if len(frame) < 3 or frame[0] != PRO_READ or frame[1] != CMD_MODE or frame[2] != SUB_COLOR_0D:
        return None
    try:
        body = _parse_type(_F.ModeColor0dTyped, frame[3:FRAME_LEN - 1], _MODE_0D_KIND_RGB)
    except Exception as exc:  # noqa: BLE001 - truncated body -> not a usable report
        _LOGGER.debug("0x0d report did not parse as kind 1: %s (%r)", bytes(frame).hex(), exc)
        return None
    return int(body.r), int(body.g), int(body.b)


def parse_mode_0d_rgblight(frame: bytes) -> tuple[tuple[int, int, int] | None, int | None] | None:
    """AA 05 0D reply decoded by the spec's ``mode_color_0d_typed(kind=2)``: ``[r, g, b, kelvin u2be,
    tint r, g, b]``. ANY non-zero kelvin (``is_cct``) -> ``(None, kelvin)``; kelvin 0 ->
    ``((r, g, b), None)``. Same return shape as :func:`parse_mode_0d`, and its 0x0d decoder on scheme
    ``"h6188"`` under the default ``"rgb_kelvin"`` mode parse (:func:`parse_mode_0d_for`). H6188 itself
    decodes its report as the app does, with :func:`parse_mode_rgb_kelvin_tint`."""
    return _mode_0d_kind2(frame)


def parse_mode_0d_for(
    frame: bytes, scheme: ColorScheme,
) -> tuple[tuple[int, int, int] | None, int | None] | None:
    """The AA 05 0D colour report under the default ``"rgb_kelvin"`` mode parse
    (``DeviceProfile.mode_color_parse``): :func:`parse_mode_0d_rgblight` on ``"h6188"``,
    :func:`parse_mode_0d` on every other scheme. The one place that picks that 0x0d decoder by colour
    scheme (the Device layer calls this). The SKUs whose app strategy is known choose their own mode
    parse instead (H6006 / H6008 / H6004 / H6188 :func:`parse_mode_rgb_kelvin_tint`, H6052
    :func:`parse_mode_0d_rgb`)."""
    return parse_mode_0d_rgblight(frame) if scheme == "h6188" else parse_mode_0d(frame)


# ── mechanism-B per-group colour read-back (H61A8; 0xAA 0xA2 V1 / 0xA5 V2) ──────
def parse_bulb_group_batch(frame: bytes) -> tuple[int, list[GroupRecord]] | None:
    """One mechanism-B batch reply (spec Change 7). Returns ``(batch_seq, groups)`` where
    each group is ``(brightness|None, r, g, b)`` — brightness is None for the V1 (0xA2)
    colour-only form. The segment index is POSITIONAL (assembled by the caller via
    :func:`bulb_groups_to_segments`), never carried in the frame."""
    f = _parse(frame)
    if f is None or f.pro_type != _ProType.read:
        return None
    cmd = getattr(f.body, "command", None)
    body = getattr(f.body, "body", None)
    if body is None:
        return None
    if cmd == _Command.local_color_read:            # V2 (0xA5): [brightness, r, g, b]
        return (int(body.batch_seq),
                [(int(g.brightness), int(g.r), int(g.g), int(g.b)) for g in body.groups])
    if cmd == _Command.bulb_string_color_read:      # V1 (0xA2): [r, g, b]
        return (int(body.batch_seq),
                [(None, int(g.r), int(g.g), int(g.b)) for g in body.groups])
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
# H60A6/H6047/H6641 share this PARSER (group size 4, confirmed independently for H6047 —
# "count=getColorPieceSize (=12), 4/group" — and H6641 — "4 records/group", devices.yaml).
# It is a DIFFERENT, unrelated controller from H61A8's BulbGroupColorV2/mechanism-B (group
# size 3) — they only share the wire opcode (0xAA read, command 0xA5), not the reply layout.
# The frame is a fixed 20-byte zero-padded Govee frame like every other, with NO explicit
# length field (unlike the embedded-0xAC-TLV variant of this same parser, which derives its
# record count from the TLV's own `len` byte) — so a bare direct reply's record count can't be
# length-derived; the real app already knows the total count in advance (getColorPieceSize, or
# a live 0x40 IC read) and simply reads the fixed group size except a shorter final group. This
# mirrors that: the CALLER supplies `expected_records`, computed from its own already-known
# total. The frame path's generated type for an aa a5 reply is H61A8's bulb_group_color_read_v2,
# which would misdecode this format, so the group is read by the spec's parametric
# color_group_read instead, with the record count the caller passes (the same "shared opcode,
# per-SKU body" case as the 0x0d mode read, parse_mode_0d).
_RECORD_LEN = 4   # color_group_record: [brightness, r, g, b]


def parse_direct_color_group(
    frame: bytes, expected_records: int
) -> tuple[int, list[tuple[int, int, int, int]]] | None:
    """One ``AA A5 <group>`` direct reply: ``(group_index, records)`` where each record is
    ``(brightness, r, g, b)``. Returns at most ``expected_records`` (and at most what actually
    fits in the frame), never more — the caller determines how many are real for this group.

    Read by the spec's ``color_group_read(record_count, has_brightness=1)`` over the bytes after
    ``[proType, command]`` and before the BCC byte."""
    f = _parse(frame)
    if f is None or f.pro_type != _ProType.read:
        return None
    if getattr(f.body, "command", None) != _Command.local_color_read:
        return None
    # [group_index, records...]; never empty: the frame reader above needs more than the group byte.
    group = bytes(frame)[2:FRAME_LEN - 1]
    n = max(0, min(expected_records, (len(group) - 1) // _RECORD_LEN))
    body = _parse_type(_F.ColorGroupRead, group, n, 1)
    return int(body.group_index), [
        (int(rec.brightness), int(rec.r), int(rec.g), int(rec.b)) for rec in body.records]


@dataclass(frozen=True)
class IcCount:
    """AA 40 reply (ic_segment_read / ControllerOnlyReadIcSegmentNum): the device's live IC
    (lamp-bead) count, and the reply's third byte as received.

    ``segment_count`` is that third byte, kept for compatibility: the app does not use it for H6641
    (h61d3 ``ReadIcController`` reads bytes 0..1 only). The piece and group counts are derived
    client-side from the IC count, as the app does (:func:`..devices._readback.live_ic_pieces`:
    ``ceil(clamp(IC, 41, 70) / 5)`` pieces, ``ceil(pieces / 4)`` groups; ``H61D3Support.e``)."""
    ic_count: int
    segment_count: int


def parse_ic_count(frame: bytes) -> IcCount | None:
    """AA 40 reply: the live IC count and the reply's third byte (see :class:`IcCount`)."""
    b = _read_reply(frame, _Command.ic_num)
    if b is None:
        return None
    return IcCount(ic_count=int(b.ic_count), segment_count=int(b.segment))


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
    """AA 41 reply: ``[IC s16be, sectionNum u8]``, i.e. frame bytes 2..4 (``BleUtil.getSignedShort``
    of the first two, then the third; ``ControllerIcSegmentNum.makeController4Read``).

    Hand-decoded, not via the generated reader: opcode 0x41 is ``on_off_memory`` in the shared
    command enum and only means this on the ``striplight`` split (the same "shared opcode,
    per-SKU body" case as :func:`parse_mode_0d`)."""
    if len(frame) < 5 or frame[0] != PRO_READ or frame[1] != CMD_SECTION_COUNT:
        return None
    return SectionCount(ic_count=int.from_bytes(frame[2:4], "big", signed=True), section_num=frame[4])


# ── device info (AA 07 10/11/02) ─────────────────────────────────────────────
def _version(v: Any) -> str | None:
    """Govee 3-byte version -> "X.YY.ZZ", or None for the 0.00.00 zero sentinel (a
    device that doesn't populate this field — e.g. H60A6's aa 07 wifi reply)."""
    major, minor, patch = int(v.major), int(v.minor), int(v.patch)
    if major == 0 and minor == 0 and patch == 0:
        return None
    return f"{major}.{minor:02d}.{patch:02d}"


_VERSION_TRIM = "".join(map(chr, range(0x21)))   # BleUtils.K trims every char <= 0x20


def _ascii_version(text: str) -> str | None:
    """ASCII version string (already cut at the first NUL by the reader), trimmed of control and
    space characters at both ends as the app does (BleUtils.K); None when nothing is left."""
    return text.strip(_VERSION_TRIM) or None


def _mac_or_none(raw: bytes) -> str | None:
    return ":".join(f"{b:02X}" for b in raw) if any(raw) else None


def _uid_serial(uid: bytes) -> str | None:
    """8-byte UID -> MAC-style colon hex (reversed) = the device id, as SnController renders it:
    an all-zero UID is invalid (NumberUtil.isInvalidUuid -> None) and one leading ``00:00:`` is
    dropped (SnController.parseValidBytes)."""
    if not any(uid):
        return None
    s = ":".join(f"{x:02X}" for x in reversed(uid))
    return s.removeprefix("00:00:") or None


@dataclass(frozen=True)
class DeviceInfo:
    """Device info from one ``aa 07`` reply or one ``0x07`` TLV of the 0xAC status reply (same
    ``device_info_read`` layout). Only the fields its selector carries are set: basic 0x10
    (serial, sw / hw, dsp), Wi-Fi 0x11 (Wi-Fi MAC and the Wi-Fi MODULE's sw / hw), SN 0x02
    (serial), hard version 0x03 (hw)."""
    serial: str | None = None
    wifi_mac: str | None = None
    sw_version: str | None = None
    hw_version: str | None = None
    dsp_version: int | None = None


def device_info_from(sel: int, info: Any) -> DeviceInfo | None:
    """Shared selector -> DeviceInfo mapping for a parsed device_info_read body (used by the
    single-frame aa 07 reply AND the 0xAC status 0x07 TLV — identical layout)."""
    if sel == INFO_BASIC:
        return DeviceInfo(
            serial=_uid_serial(bytes(info.uid)),
            sw_version=_version(info.sw_version),
            hw_version=_version(info.hw_version),
            dsp_version=int(info.dsp_version),
        )
    if sel == INFO_WIFI:
        return DeviceInfo(
            wifi_mac=_mac_or_none(bytes(info.wifi_mac)),
            sw_version=_version(info.wifi_sw_version),
            hw_version=_version(info.wifi_hw_version),
        )
    if sel == INFO_SN:
        return DeviceInfo(serial=_uid_serial(bytes(info.uid)))
    if sel == INFO_HARD:
        return DeviceInfo(hw_version=_ascii_version(info.version))
    return None   # 0x06 etc. — logged & ignored by the device


def parse_device_info(frame: bytes) -> DeviceInfo | None:
    """AA 07 reply: basic (0x10), wifi (0x11), SN (0x02) or hard-version (0x03) device info."""
    b = _read_reply(frame, _Command.device_info)
    if b is None:
        return None
    return device_info_from(int(b.selector), b.info)


def parse_soft_version(frame: bytes) -> str | None:
    """AA 06 reply: the ASCII firmware (soft) version, e.g. ``1.00.41`` (None when empty)."""
    b = _read_reply(frame, _Command.soft_version)
    return None if b is None else _ascii_version(b.version)


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
    """0xEE push. Types the three sub-payloads the curated SKUs emit (0x20/0x11/0x30);
    other (cross-category) sub-types return None.

    Spec surface for tools and tests: Device does not consume 0xEE pushes (it polls), so nothing
    in the library calls this yet."""
    f = _parse(frame)
    if f is None or f.pro_type != _ProType.notify:
        return None
    sub = f.body.sub_type
    d = f.body.data
    if sub == _NotifySub.brightness:
        return Notify(sub_type=int(sub), level=int(d.level))
    if sub == _NotifySub.wifi_connect:
        return Notify(sub_type=int(sub), wifi_connected=(int(d.status) == 0))
    if sub == _NotifySub.device_info_or_zone:
        return Notify(sub_type=int(sub), zone_flags=(int(d.flags_a), int(d.flags_b), int(d.flags_c)))
    return None
