"""Runtime parsing of inbound frames via the shipped Kaitai reader.

Single-frame ``0xAA`` read replies and ``0xEE`` notifications are decoded through
the spec-generated ``GoveeBleFrame`` reader — the spec is the single source of the
wire layout. Multi-packet ``0xAC``/``0xA1``/``0xA3``/``0xA4`` streams are reassembled
and walked in :mod:`.reassemble` (cross-frame de-chunking isn't expressible in Kaitai).

Values are returned **as the device sends them** (no rescaling): brightness is the raw
reported byte, which every curated SKU sends as a 0..100 percent (a device reporting
0..255 would set ``DeviceProfile.brightness_max`` accordingly; scaling is the Device
layer's job), and power is the raw byte so plugs can read it as a relay bitmask while
lights treat non-zero as on.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from kaitaistruct import KaitaiStream

from ..models import Segment
from .._generated.govee_ble_frame import GoveeBleFrame as _GBF  # type: ignore[attr-defined]

_LOGGER = logging.getLogger(__name__)

# The generated reader is untyped; treat it as Any so attribute chains don't need stubs.
_F: Any = _GBF
_ProType = _F.ProType
_Command = _F.Command
_NotifySub = _F.NotifySub


def _parse(frame: bytes) -> Any:
    """Parse a 20-byte frame, or return None on any malformed/unknown-enum input."""
    if len(frame) < 2:
        return None
    try:
        return _F.from_bytes(bytes(frame))
    except Exception as exc:  # noqa: BLE001 - unknown enum / truncated frame -> not for us
        # A matched reply that won't parse is worth a breadcrumb (unknown enum / truncated).
        _LOGGER.debug("frame did not parse against the spec: %s (%r)", bytes(frame).hex(), exc)
        return None


def _read_reply(frame: bytes, command: Any) -> Any:
    """Return the typed body of a ``0xAA`` read reply matching ``command``, else None."""
    f = _parse(frame)
    if f is None or f.pro_type != _ProType.read:
        return None
    try:
        if f.body.command != command:
            return None
    except Exception:  # noqa: BLE001
        return None
    return f.body.body


# ── single-command read replies ──────────────────────────────────────────────
def find_reply(frames: list[bytes], command_type: int) -> bytes | None:
    """First ``0xAA`` frame in ``frames`` whose command byte is ``command_type``, else None."""
    for reply in frames:
        if len(reply) >= 2 and reply[0] == 0xAA and reply[1] == command_type:
            return reply
    return None


def parse_power(frame: bytes) -> int | None:
    """AA 01 reply. Raw byte: lights use non-zero = on; plugs read it as a relay bitmask."""
    b = _read_reply(frame, _Command.switch)
    return None if b is None else int(b.state)


def parse_brightness(frame: bytes) -> int | None:
    """AA 04 reply. The raw reported byte, not rescaled: 0..100 percent on every curated SKU
    (see ``DeviceProfile.brightness_max``; the Device layer does any normalisation)."""
    b = _read_reply(frame, _Command.brightness)
    return None if b is None else int(b.brightness)


def parse_bar_switch(frame: bytes) -> tuple[bool, bool] | None:
    """AA 36 reply: (left, right) bar power."""
    b = _read_reply(frame, _Command.compose_light_switch)
    return None if b is None else (bool(b.left), bool(b.right))


def parse_secret(frame: bytes) -> bytes | None:
    """AA B1 reply: 8-byte account-lock secret (selector 0x01)."""
    b = _read_reply(frame, _Command.secret_read)
    if b is None or int(b.selector) != 0x01:
        return None
    return bytes(b.secret)


def parse_plug_spec(frame: bytes) -> int | None:
    """AA B3 reply: single spec-identifier byte (not the outlet count)."""
    b = _read_reply(frame, _Command.plug_spec)
    return None if b is None else int(b.spec)


def parse_gradual(frame: bytes) -> bool | None:
    """AA A3 reply (gradual_read): gradual/fade-on-handoff enabled iff byte0 == 1."""
    b = _read_reply(frame, _Command.gradual_wifi_ble)
    return None if b is None else bool(b.state)


# ── mode (0x05) read replies ─────────────────────────────────────────────────
def _mode_read(frame: bytes) -> Any:
    """Typed body of an ``AA 05`` mode read reply, else None."""
    return _read_reply(frame, _Command.mode)


def parse_active_scene(frame: bytes) -> int | None:
    """AA 05 04 reply: active scene code (u2le), or None if not in scene sub-mode."""
    m = _mode_read(frame)
    if m is None or int(m.selector_or_sub_mode) != 0x04:
        return None
    rest = bytes(m.rest)
    if len(rest) < 2:
        return None
    return rest[0] | (rest[1] << 8)


def parse_kelvin(frame: bytes) -> int | None:
    """AA 05 15 01 reply: colour temperature in Kelvin (u2be), or None."""
    m = _mode_read(frame)
    if m is None or int(m.selector_or_sub_mode) != 0x15:
        return None
    return int(m.rest.kelvin)


def parse_mode_color_0d(frame: bytes) -> tuple[int, int, int] | None:
    """AA 05 0D reply read as a bare (r, g, b) triple, or None.

    SUPERSEDED: the "mechanism C" reading (0x0D carries only RGB, H6052 only) was wrong. On
    the 0x0d-scheme bulbs the body is ``[r, g, b, kelvin u2be]``, so this drops the kelvin
    and reports white instead of the colour temperature. Use :func:`parse_mode_0d`; this is
    kept only for the spec's ``mode_color_0d_report`` view of the frame."""
    m = _mode_read(frame)
    if m is None or int(m.selector_or_sub_mode) != 0x0D:
        return None
    rb = m.rest
    return (int(rb.r), int(rb.g), int(rb.b))


def parse_mode_0d(frame: bytes) -> tuple[tuple[int, int, int] | None, int | None] | None:
    """AA 05 0D reply on the 0x0d-scheme bulbs (H6006/H6008/H6052): ``[r, g, b,
    kelvin u2be, tint r, g, b]`` - the same layout as the 0x0d write. ANY non-zero kelvin is
    colour-temperature mode -> ``(None, kelvin)``; kelvin 0 -> ``((r, g, b), None)``.

    This is the app's own rule: ``bulblightstringv1`` ``ModeParseStrategyConfig`` and
    tablelampv1 ``SubModeColor.parse`` take kelvin from bytes 3..4 and show the tint when it is
    non-zero. It must not require white (ff ff ff) in the colour slot: the app's CCT write
    (``Config.bu`` -> ``Bytes.p``) puts the tint there. Live capture, written by the earlier
    white-slot form: ``aa 05 0d ff ff ff 0a 8c`` = 2700 K."""
    if len(frame) < 8 or frame[0] != 0xAA or frame[1] != 0x05 or frame[2] != 0x0D:
        return None
    kelvin = (frame[6] << 8) | frame[7]
    if kelvin:
        return None, kelvin
    return (frame[3], frame[4], frame[5]), None


# mode_color_0d_typed `kind` for the rgblight strategy (devices.yaml mode_0d_kind: rgb_kelvin_tint).
_MODE_0D_KIND_RGB_KELVIN_TINT = 2


def parse_mode_0d_rgblight(frame: bytes) -> tuple[tuple[int, int, int] | None, int | None] | None:
    """AA 05 0D reply on the rgblight H6188, decoded by the spec's ``mode_color_0d_typed(kind=2)``:
    ``[r, g, b, kelvin u2be, tint r, g, b]`` (ModeParseStrategyConfig.a). ANY non-zero kelvin is
    colour-temperature mode -> ``(None, kelvin)`` (the app then shows the tint, not r/g/b);
    kelvin 0 -> ``((r, g, b), None)``. Same return shape as :func:`parse_mode_0d`."""
    if len(frame) < 3 or frame[0] != 0xAA or frame[1] != 0x05 or frame[2] != 0x0D:
        return None
    try:
        body = _F.ModeColor0dTyped(_MODE_0D_KIND_RGB_KELVIN_TINT, KaitaiStream(BytesIO(bytes(frame[3:]))))
    except Exception as exc:  # noqa: BLE001 - truncated body -> not a usable report
        _LOGGER.debug("0x0d report did not parse as kind 2: %s (%r)", bytes(frame).hex(), exc)
        return None
    if body.is_cct:
        return None, int(body.report_kelvin)
    return (int(body.r), int(body.g), int(body.b)), None


# ── mechanism-B per-group colour read-back (H61A8; 0xAA 0xA2 V1 / 0xA5 V2) ──────
def parse_bulb_group_batch(
    frame: bytes,
) -> tuple[int, list[tuple[int | None, int, int, int]]] | None:
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


def bulb_groups_to_segments(
    batches: list[tuple[int, list[tuple[int | None, int, int, int]]]], per_batch: int
) -> list[Segment]:
    """Assemble mechanism-B batches into positional segments:
    ``index = (batch_seq - 1) * per_batch + i`` (per_batch is a client constant:
    V1 = 4, V2 = 3 — not frame-encoded)."""
    segs: list[Segment] = []
    for batch_seq, groups in batches:
        base = group_base_index(batch_seq, per_batch)
        if base is None:
            continue
        for i, (brightness, r, g, b) in enumerate(groups):
            segs.append(Segment(index=base + i, rgb=(r, g, b), brightness=brightness))
    return merge_segments(segs)


def merge_segments(*groups: Iterable[Segment]) -> list[Segment]:
    """Merge segment records by ``index`` (later groups/records win) -> list sorted by index.
    Used both to assemble read-back batches and to fold a partial burst over known state."""
    merged: dict[int, Segment] = {}
    for group in groups:
        for seg in group:
            merged[seg.index] = seg
    return [merged[k] for k in sorted(merged)]


def direct_groups_to_segments(
    groups: Iterable[tuple[int, list[tuple[int, int, int, int]]]], group_size: int
) -> list[Segment]:
    """Assemble mechanism-A-direct group replies (:func:`parse_direct_color_group` results)
    into positional segments: ``index = (group_index - 1) * group_size + k``."""
    segs: list[Segment] = []
    for group_index, records in groups:
        base = group_base_index(group_index, group_size)
        if base is None:
            continue
        for k, (brightness, r, g, b) in enumerate(records):
            segs.append(Segment(index=base + k, rgb=(r, g, b), brightness=brightness))
    return merge_segments(segs)


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
# total. Hand-decoded (bypassing the generated bulb_group_color_read_v2 type, which is
# H61A8-specific and would misdecode this format) — the same "shared opcode, per-SKU body"
# precedent as the 0x0d mode read (parse_mode_0d).
def parse_direct_color_group(
    frame: bytes, expected_records: int
) -> tuple[int, list[tuple[int, int, int, int]]] | None:
    """One ``AA A5 <group>`` direct reply: ``(group_index, records)`` where each record is
    ``(brightness, r, g, b)``. Returns at most ``expected_records`` (and at most what actually
    fits in the frame), never more — the caller determines how many are real for this group."""
    f = _parse(frame)
    if f is None or f.pro_type != _ProType.read:
        return None
    if getattr(f.body, "command", None) != _Command.local_color_read:
        return None
    group_index = frame[2]
    body = bytes(frame)[3:19]   # after [proType, command, group_index]; before the BCC byte
    n = max(0, min(expected_records, len(body) // 4))
    records = [
        (body[i * 4], body[i * 4 + 1], body[i * 4 + 2], body[i * 4 + 3]) for i in range(n)
    ]
    return (group_index, records)


@dataclass(frozen=True)
class IcCount:
    """AA 40 reply (ic_segment_read / ControllerOnlyReadIcSegmentNum): the device's OWN
    live IC (lamp-bead) count and its precomputed group/segment count — lets a client
    discover the true segmentation instead of relying on a static per-SKU table, and without
    needing to re-derive the group-count divisor client-side."""
    ic_count: int
    segment_count: int


def parse_ic_count(frame: bytes) -> IcCount | None:
    """AA 40 reply: live IC count + device-reported group/segment count."""
    b = _read_reply(frame, _Command.ic_num)
    if b is None:
        return None
    return IcCount(ic_count=int(b.ic_count), segment_count=int(b.segment))


@dataclass(frozen=True)
class SectionCount:
    """AA 41 reply on the H6062 (``striplight`` ``ControllerIcSegmentNum``): the device's IC
    (lamp-bead) count and its ``sectionNum``. Only ``section_num`` drives control: the app
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
    if len(frame) < 5 or frame[0] != 0xAA or frame[1] != 0x41:
        return None
    return SectionCount(ic_count=(frame[2] << 8) | frame[3], section_num=frame[4])


# ── device info (AA 07 10/11/02) ─────────────────────────────────────────────
def _version(v: Any) -> str | None:
    """Govee 3-byte version -> "X.YY.ZZ", or None for the 0.00.00 zero sentinel (a
    device that doesn't populate this field — e.g. H60A6's aa 07 wifi reply)."""
    major, minor, patch = int(v.major), int(v.minor), int(v.patch)
    if major == 0 and minor == 0 and patch == 0:
        return None
    return f"{major}.{minor:02d}.{patch:02d}"


def _mac_or_none(raw: bytes) -> str | None:
    return ":".join(f"{b:02X}" for b in raw) if any(raw) else None


def _uid_serial(uid: bytes) -> str | None:
    """8-byte UID -> MAC-style colon hex (reversed), leading 00: pairs stripped."""
    s = ":".join(f"{x:02X}" for x in reversed(uid))
    while s.startswith("00:"):
        s = s[3:]
    return s or None


@dataclass(frozen=True)
class DeviceInfo:
    serial: str | None = None
    wifi_mac: str | None = None
    sw_version: str | None = None
    hw_version: str | None = None
    dsp_version: int | None = None


def device_info_from(sel: int, info: Any) -> DeviceInfo | None:
    """Shared selector -> DeviceInfo mapping for a parsed device_info_read body (used by the
    single-frame aa 07 reply AND the 0xAC status 0x07 TLV — identical layout)."""
    if sel == 0x10:
        return DeviceInfo(
            serial=_uid_serial(bytes(info.uid)),
            sw_version=_version(info.sw_version),
            hw_version=_version(info.hw_version),
            dsp_version=int(info.dsp_version),
        )
    if sel == 0x11:
        return DeviceInfo(
            wifi_mac=_mac_or_none(bytes(info.wifi_mac)),
            sw_version=_version(info.wifi_sw_version),
            hw_version=_version(info.wifi_hw_version),
        )
    if sel == 0x02:
        return DeviceInfo(serial=_uid_serial(bytes(info.uid)))
    return None   # 0x06 etc. — logged & ignored by the device


def parse_device_info(frame: bytes) -> DeviceInfo | None:
    """AA 07 reply: basic (0x10), wifi (0x11), or SN (0x02) device info."""
    b = _read_reply(frame, _Command.device_info)
    if b is None:
        return None
    return device_info_from(int(b.selector), b.info)


# ── notifications (0xEE) ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class Notify:
    sub_type: int
    level: int | None = None                      # 0x20 brightness push (raw reported value)
    wifi_connected: bool | None = None            # 0x11 (status byte == 0)
    zone_flags: tuple[int, int, int] | None = None  # 0x30 flag bytes (family-interpreted)


def parse_notify(frame: bytes) -> Notify | None:
    """0xEE push. Types the three sub-payloads the curated SKUs emit (0x20/0x11/0x30);
    other (cross-category) sub-types return None."""
    f = _parse(frame)
    if f is None or f.pro_type != _ProType.notify:
        return None
    sub = f.body.sub_type
    d = f.body.data
    if sub == _NotifySub.brightness:
        return Notify(sub_type=0x20, level=int(d.level))
    if sub == _NotifySub.wifi_connect:
        return Notify(sub_type=0x11, wifi_connected=(int(d.status) == 0))
    if sub == _NotifySub.device_info_or_zone:
        return Notify(sub_type=0x30, zone_flags=(int(d.flags_a), int(d.flags_b), int(d.flags_c)))
    return None
