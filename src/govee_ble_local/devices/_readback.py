"""Read-back walks: the multi-read sequences behind :meth:`~.device.Device.update`.

Each walk issues its reads through the one-read primitive it is given (``read_reply``, i.e.
``Device._read_reply``: one ``aa`` read under the device's read gate, its reply or None) and returns
what it read. Nothing here touches the connection, the locks or the stale-read protocol: ``Device``
applies a walk's result through ``Device._read_fresh``, so a walk that raced a setter is discarded
whole. A walk can therefore be driven by a scripted ``read_reply``. The frames, their order and the
decoding are the ones the Device methods used before they moved here.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from ..models import DeviceState, Segment
from ..wire import build, parse, reassemble
from . import _fold
from .profile import DeviceProfile

# The records keep the device module's logger, as before these walks moved out of it (logging
# configuration and the diagnostics docs name ``govee_ble_local.devices.device``).
_LOGGER = logging.getLogger(__name__.rpartition(".")[0] + ".device")

ReadReply = Callable[[bytes, int], Awaitable[bytes | None]]
"""One ``aa`` read: the ``command_type`` reply to ``frame``, or None (``Device._read_reply``)."""

QueryStatus = Callable[[], Awaitable[tuple[reassemble.StatusReply, list[bytes], list[bytes]]]]
"""One 0xAC status-burst query: ``(parsed, 0xAC frames, all frames)`` (``Device._query_status``)."""


class ReadTarget(Protocol):
    """The device a walk reads for (a :class:`~.device.Device`): its public identity (for the log
    lines), profile, segment count and live state."""

    @property
    def address(self) -> str: ...

    @property
    def sku(self) -> str: ...

    @property
    def profile(self) -> DeviceProfile: ...

    @property
    def segment_count(self) -> int: ...

    @property
    def state(self) -> DeviceState: ...


def chunk_indices(ac_frames: list[bytes]) -> str:
    """Format the chunk-index byte (frame[1]) of each received 0xAC frame, for diagnosing a
    dropped/truncated status burst (e.g. ``0x00,0x01,0x02,0x04,0xff`` — index 3 missing)."""
    if not ac_frames:
        return "<none>"
    idx = sorted({f[1] for f in ac_frames if len(f) >= 2})
    return ",".join(f"0x{i:02x}" for i in idx)


async def status_burst(dev: ReadTarget, query_status: QueryStatus) -> reassemble.StatusReply:
    """One 0xAC status-burst read, retried once when no usable TLVs came back."""
    st, ac, frames = await query_status()
    if st.is_on is None:
        # The device answered (or didn't) but no usable TLVs came back — most often a
        # dropped/truncated 0xAC burst (a BLE notification lost mid-sequence, so the
        # reassembled buffer ends mid-TLV or is empty). Retry once with a fresh query —
        # a new burst is independent, so this self-heals most single-poll losses — before
        # leaving state stale. This is also the signal for "mechanism-A doesn't work here"
        # for a device that reliably returns nothing (e.g. H6047).
        _LOGGER.debug(
            "%s (%s): status read empty on first attempt (%d 0xAC of %d frames, "
            "chunks %s); retrying once",
            dev.address, dev.sku, len(ac), len(frames), chunk_indices(ac),
        )
        st, ac, frames = await query_status()
    if st.is_on is None:
        _LOGGER.warning(
            "%s (%s): status read returned no usable data after retry "
            "(%d 0xAC of %d frames, chunks %s); state left stale",
            dev.address, dev.sku, len(ac), len(frames), chunk_indices(ac),
        )
    return st


async def walk_mechanism_b(dev: ReadTarget, read_reply: ReadReply) -> list[Segment] | None:
    """H61A8's per-segment colour via BulbGroupColorV2 (AA A5 <seq>, V2): request each
    batch and assemble positional segments, or None if no batch came back. per_batch/batch-count
    are client constants (not frame-encoded, the controller's own fixed group size),
    source-modeled and not yet live-verified. NOT shared with H6047/H6641 — they use a different
    controller (Controller4ColorInfoByGroup, a different group size) via
    :func:`walk_mechanism_a_direct`; the two only happen to share the wire opcode (0xA5), not the
    reply layout."""
    profile = dev.profile
    per_batch = profile.color_readback_per_batch   # >= 1, checked by DeviceProfile
    seg_count = profile.color_readback_segments or dev.segment_count
    batch_count = build.ceil_div(seg_count, per_batch)
    batches: list[tuple[int, list[tuple[int | None, int, int, int]]]] = []
    for seq in range(1, batch_count + 1):
        reply = await read_reply(build.bulb_group_query(seq, v2=True), build.CMD_BULB_COLOR_V2)
        parsed = parse.parse_bulb_group_batch(reply) if reply else None
        # The app places a batch by its own group byte into a fixed bulbStringMaxNum array
        # (dreamcolorlightv1 BleOpV3.onEventBulbStringColorV2), so a group outside the walk
        # can't land anywhere; dropping it keeps untrusted data from adding segments.
        if parsed is not None and 1 <= parsed[0] <= batch_count:
            batches.append(parsed)
    return parse.bulb_groups_to_segments(batches, per_batch) or None


async def walk_mechanism_a_direct(dev: ReadTarget, read_reply: ReadReply) -> list[Segment] | None:
    """H6047/H6641 per-segment colour via Controller4ColorInfoByGroup's direct per-group
    requests (AA A5 <group>), or None when the walk was skipped or no group came back. A different
    controller from H61A8's mechanism_b (BulbGroupColorV2); they only share the wire opcode. Group
    size is a fixed client constant (`color_readback_per_batch`, source-confirmed 4 for both
    curated SKUs). The total piece count — and hence the group count, ceil(pieces / group size) —
    comes from either a static per-SKU value (`color_readback_segments`/`segments`; H6047 uses its
    10 `segments`) or, when `color_readback_live_ic` is set (H6641), a live `0x40` IC-count read
    derived the way the app does (:func:`live_ic_pieces`). When that read fails the app still walks
    the groups from the IC count it stored earlier; the library has none, so it skips the walk.

    Each reply is placed by the group that was asked for: Controller4ColorInfoByGroup's read
    callback drops the reply's own group byte (``Controller4ColorInfoByGroup.Companion.h``/``i``
    pass only the colours on; ``VM4Light.Q5`` places them by its loop index)."""
    profile = dev.profile
    group_size = profile.color_readback_per_batch   # 4 on every table profile (profile._incoherent)
    if profile.color_readback_live_ic:
        ic_reply = await read_reply(build.ic_count_query(), build.CMD_IC_NUM)
        ic = parse.parse_ic_count(ic_reply) if ic_reply else None
        if ic is None:
            _LOGGER.debug(
                "%s (%s): live IC-count read failed; skipping segment colour read-back",
                dev.address, dev.sku,
            )
            return None
        pieces = live_ic_pieces(dev, ic.ic_count)
        if pieces is None:
            _LOGGER.debug(
                "%s (%s): implausible live IC count 0x%04x; skipping segment colour read-back",
                dev.address, dev.sku, ic.ic_count,
            )
            return None
        total = pieces
    else:
        total = profile.color_readback_segments or dev.segment_count
    group_count = build.ceil_div(total, group_size)

    groups: list[tuple[int, list[tuple[int, int, int, int]]]] = []
    for g in range(1, group_count + 1):
        reply = await read_reply(build.bulb_group_query(g, v2=True), build.CMD_BULB_COLOR_V2)
        expected = max(0, min(group_size, total - (g - 1) * group_size))
        parsed = parse.parse_direct_color_group(reply, expected) if reply else None
        if parsed is None:
            continue
        if parsed[0] != g:
            _LOGGER.debug("%s (%s): aa a5 reply carries group %d for the group %d read; "
                          "placing it by the request, as the app does",
                          dev.address, dev.sku, parsed[0], g)
        groups.append((g, parsed[1]))
    return parse.direct_groups_to_segments(groups, group_size) or None


def live_ic_pieces(dev: ReadTarget, ic: int) -> int | None:
    """Colour pieces for a live ``aa 40`` IC count (``ic``: read signed, :func:`parse.parse_ic_count`), as
    the app derives them for H6641 (goodsType 247; h61d3, Govee Home 7.5.12 names):
    ``ReadIcController.Companion.d`` reads bytes 0..1 as a signed short (``BleUtil.getSignedShort``, the
    spec's ``ic_count_signed``) and clamps it to ``H61D3Support.q(247)`` = 41..70
    (``color_readback_ic_range``); ``H61D3Support.e(247, ic)`` = ceil(ic / 5) pieces
    (``color_readback_ic_per_piece``), the ``bulbStringMaxNum`` of ``VM4Light.P5``. The reply's
    third byte is never used.

    None for a count of 0x1000 or more: ``ReadIcController.Companion.a`` then takes its
    byte-swap branch, which indexes past the 2-byte array it was given, so the app's read
    callback never stores the count (``VM4Light.D5`` does not run) - the same as a failed read."""
    ic_range = dev.profile.color_readback_ic_range
    if ic >= 0x1000:
        return None
    # Every table profile has its clamp (profile._incoherent); a custom profile without one uses the
    # reported count as it is, never below 0.
    clamped = max(0, ic) if ic_range is None else max(ic_range[0], min(ic_range[1], ic))
    return build.ceil_div(clamped, dev.profile.color_readback_ic_per_piece)


async def read_device_info(dev: ReadTarget, read_reply: ReadReply) -> bool:
    """Populate the static device-info fields of ``dev.state``; returns whether the device answered any of
    the reads.

    ``aa 06`` firmware, ``aa 07 03`` hardware version and ``aa 07 02`` device id (SN), read in that order.
    Each is handled by the firmware of every image analysed (H6006, H6008, H6047, H60A1, H60A6, H5083:
    ``ble_msg_get_version`` / ``ble_msg_get_dev_info`` 02 and 03 on the Realtek images, the H6047 command
    table) and answered live by H60A6, H6047, H61A8, H6052, H6006, H6008 and H5083. Where the app sends
    them: ``aa 06`` and ``aa 07 03`` are control-screen reads (``ControllerSoftVersion`` /
    ``ControllerHardVersion``) on the lights except the H60A6, whose versions come from the 0xAC 0x07 TLV;
    the H60A6 and gt 43 plug screens send neither, and ``aa 07 02`` (``SnController``) the app sends only
    while pairing. Those reads are kept anyway as firmware-sourced extensions (CLAUDE.md rule 6; the plugs
    have no other BLE source for them); the app does not use them there.

    Not sent: ``aa 07 10`` (basic) and ``aa 07 11`` (Wi-Fi). No firmware image has a handler for either
    (most drop the request, which cost a timeout per poll; H60A1 / H60A6 echo it back as zeros) and the app
    never sends them (``BasicInfoController`` / ``BasicWifiInfoController`` have no caller); their record
    layouts exist only as the 0x10 / 0x11 records of the 0xAC 0x07 TLV. Unanswered reads leave fields None.
    ble_mac stays None unless a reply carries a MAC distinct from the connectable address (none confirmed
    yet: that address is otherwise the BLE MAC)."""
    state = dev.state
    # aa 06 / aa 07 03: the app's control-screen reads on the lights except the H60A6; FIRMWARE-SOURCED
    # (CLAUDE.md rule 6) on the H60A6 and the gt 43 plugs, whose screens don't send them: Realtek
    # ble_msg_get_version and ble_msg_get_dev_info selector 03 (get_hw_version), e.g. H60A6 sw 1.00.41
    # @0x9b04a4ac / @0x9b04a5bc, H5083 sw 1.00.30 @0x9b0318dc / @0x9b031984.
    soft = await read_reply(build.soft_version_query(), build.CMD_SOFT_VERSION)
    answered = soft is not None
    _fold.merge_device_info(state, firmware=parse.parse_soft_version(soft or b""))
    # aa 07 02, FIRMWARE-SOURCED on every SKU (the app sends it only while pairing, SnController):
    # ble_msg_get_dev_info selector 02 (get_sn) on the Realtek images (e.g. H6006 @0x9b03c87c, H60A6
    # @0x9b04a528, H5083 @0x9b031924) and the H6047's aa 07 handler (@0x01005370); answered live by all
    # seven bench SKUs.
    for selector in (build.INFO_HARD, build.INFO_SN):
        got, info = await read_info_reply(dev, read_reply, selector)
        answered |= got
        if info is not None:
            _fold.merge_device_info(state, serial=info.serial, hardware=info.hw_version)
    return answered


async def read_info_reply(
    dev: ReadTarget, read_reply: ReadReply, selector: int
) -> tuple[bool, parse.DeviceInfo | None]:
    """One ``aa 07 <selector>`` read: ``(answered, parsed)``. A reply whose selector byte is not
    the one asked for (e.g. a late reply to the previous read landing in this one) is ignored."""
    reply = await read_reply(build.device_info_query(selector), build.CMD_DEVICE_INFO)
    if reply is None:
        return False, None
    got = parse.parse_device_info_selector(reply)
    if got != selector:
        _LOGGER.debug("%s (%s): ignoring aa 07 reply for selector %s to the 0x%02x read: %s",
                      dev.address, dev.sku, None if got is None else f"0x{got:02x}", selector, reply.hex())
        return True, None
    return True, parse.parse_device_info(reply)
