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
from typing import NamedTuple, Protocol

from ..models import DeviceState, Segment
from ..wire import build, parse, reassemble
from . import _fold
from .profile import DeviceProfile

# The records keep the device module's logger, as before these walks moved out of it (logging
# configuration and the diagnostics docs name ``govee_ble_local.devices.device``).
_LOGGER = logging.getLogger(__name__.rpartition(".")[0] + ".device")

ReadReply = Callable[[bytes], Awaitable[bytes | None]]
"""One ``aa`` read: the ``command_type`` reply to ``frame``, or None (``Device._read_reply``)."""

QueryStatus = Callable[[], Awaitable[tuple[reassemble.StatusReply, list[bytes], list[bytes]]]]
"""One 0xAC status-burst query: ``(parsed, 0xAC frames, all frames)`` (``Device._query_status``)."""


class Walk(NamedTuple):
    """What a segment-colour walk read: ``segments``, the pieces its answered groups brought, each placed by
    the group asked for, and ``pieces``, the pieces it walked (its groups cover them). A walk that had a
    group go unanswered brought fewer. The app's connect read fails at that group: the group read is not a
    temporary or retried step, so ``AbsComposeControllerWithOpResult.onOvertime:287-312`` ends the compose
    in ``result2Fail``, and the screen syncs nothing from that read (:func:`._fold.brought_every_piece`)."""

    segments: list[Segment]
    pieces: int


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


async def walk_mechanism_b(dev: ReadTarget, read_reply: ReadReply) -> Walk | None:
    """H61A8's per-segment colour (AA A5 <seq>, the V2 records with brightness): request each batch
    and assemble positional segments over the walk's pieces (:class:`Walk`), or None if no batch came
    back. per_batch / batch-count are client constants (not frame-encoded: the screen's group size, 3
    with brightness on the bench H61A8, read back live). Each reply is placed by the batch asked for, as
    goodsType 19's screen does (see the loop); a batch that didn't answer is left out. Kept apart from
    :func:`walk_mechanism_a_direct` (H6047 / H6641, group size 4, colour only): the two share the wire
    opcode (0xA5), not the record layout the library decodes."""
    profile = dev.profile
    per_batch = profile.color_readback_per_batch   # >= 1, checked by DeviceProfile
    seg_count = profile.color_readback_segments or dev.segment_count
    batch_count = build.ceil_div(seg_count, per_batch)
    batches: list[tuple[int, list[tuple[int | None, int, int, int]]]] = []
    for seq in range(1, batch_count + 1):
        reply = await read_reply(build.bulb_group_query(seq, v2=True))
        parsed = parse.parse_bulb_group_batch(reply) if reply else None
        if parsed is None:
            continue
        # Placed by the batch asked for, as goodsType 19's screen places it (rgbiclight AcNewDetail):
        # VM4Light.N5:946-962 reads each group with Controller4ColorInfoByGroup, whose callback drops
        # the reply's own group byte (Companion.h / i, Controller4ColorInfoByGroup.java:58-75), and
        # P5(.., iD, <loop group>, ..) places it by the loop index (SubMode4Color.j:113-147), the last
        # group trimmed to the piece count. (The reply's byte placed it on the legacy screen,
        # dreamcolorlightv1 BleOpV3, which goodsType 19 never opens.)
        if parsed[0] != seq:
            _LOGGER.debug("%s (%s): batch %d reply carries group %d; placed as batch %d, as the app does",
                          dev.address, dev.sku, seq, parsed[0], seq)
        batches.append((seq, parsed[1][: max(0, seg_count - (seq - 1) * per_batch)]))
    segments = parse.bulb_groups_to_segments(batches, per_batch)
    return Walk(segments, seg_count) if segments else None


async def walk_mechanism_a_direct(dev: ReadTarget, read_reply: ReadReply,
                                  live_pieces: int | None = None, *,
                                  stop_at_miss: bool = False) -> Walk | None:
    """H6047/H6641 per-segment colour via Controller4ColorInfoByGroup's direct per-group
    requests (AA A5 <group>) over the walk's pieces (:class:`Walk`), or None when the walk was skipped or no
    group came back. A different controller from H61A8's mechanism_b (BulbGroupColorV2); they only share
    the wire opcode. Group size is the profile's `color_readback_per_batch` (the spec row's wire group
    num_per_group, 4 on every current SKU). The total piece count — and hence the group count,
    ceil(pieces / group size) — comes from either the segment count (`color_readback_segments`, else
    ``Device.segment_count``: the static `segments`, H6047's 10, or the count an aa 41 read reported, H6062;
    on the h7020 bulb strings
    ``live_pieces``, the last aa 0f read's pieces, which may pass the 112 the masks carry, as
    ``NewDetailVM.D5`` walks them all) or, when `color_readback_live_ic` is set (H6641), a live `0x40`
    IC-count read
    derived the way the app does (:func:`live_ic_pieces`; ``live_pieces``, where the caller made that read:
    ``Device`` makes it once per poll, as it sizes the writes too). When that read fails the app still walks
    the groups from the IC count it stored earlier; the library has none, so it skips the walk, but where the
    rule sizes the walk alone (:func:`walk_pieces`, ``color_readback_ic_walk_only``: h6609 goodsType 260) it
    walks the static pieces. With
    `color_readback_ic_sections` (goodsTypes 141 / 204) the same read's section sum sizes the walk, else the
    static count when it is 0, in Java's group count (:func:`_java_group_count`); a failed read skips it.

    On goodsType 184 (H608A-D / H808A) the app sizes its first walk from the server's per-SKU IC, not the
    account's: ``AcNewDetail4H608A.e3`` drops the account IC that ``Model.t`` passes, so
    ``AbsAcLightDetail.K1`` takes ``SkuIcManager.d(sku)`` (``/appsku/v1/skus``), and
    ``VM4H608A$connectBleSuc$1.l`` -> ``VM4H608A.a5`` walks that. The library sizes the walk from this poll's
    aa 40 instead, a divergence the user approved (decision 21(a), "Live count only"). The app itself settles
    on the live count: it folds this session's aa 40 count after the walk (``VM4H608A.S4``, which also
    uploads it with ``updateDeviceIc``), so only a connect's first walk differs, and only where the
    device's IC differs from the per-SKU one. An IC of 0, which ``S4`` skips, is no count
    (``color_readback_ic_skip_zero``): the library skips that poll's walk where the app walks the count it
    holds.

    A group that gets no usable reply is skipped and the walk goes on; with ``stop_at_miss`` it ends there,
    keeping the groups read before it, as a compose ends at a step that fails
    (``AbsComposeControllerWithOpResult.onOvertime``) after each earlier step's callback has applied its group
    (the keep-alive re-read, ``Compose4Off2On``).

    Each reply is placed by the group that was asked for: Controller4ColorInfoByGroup's read
    callback drops the reply's own group byte (``Controller4ColorInfoByGroup.Companion.h``/``i``
    pass only the colours on; ``VM4Light.Q5`` places them by its loop index)."""
    profile = dev.profile
    group_size = profile.color_readback_per_batch   # >= 1 (DeviceProfile); the spec's num_per_group
    if profile.color_readback_live_ic:
        live = (live_pieces if live_pieces is not None
                else walk_pieces(dev, await read_live_ic(dev, read_reply)))
        if live is None:
            return None
        total = live
        group_count = build.ceil_div(total, group_size)
    elif profile.color_readback_ic_sections:
        sections = await _section_sum(dev, read_reply)
        if sections is None:
            return None
        # VM4Light.N5:917-923: the sum when it is not 0, else getBulbStringMaxNum, the static pieces.
        wanted = sections or profile.color_readback_segments or dev.segment_count
        group_count = _java_group_count(wanted, group_size)
        # SubMode4Color.j keeps the first `wanted` pieces; a negative sum keeps none (P5's new int[N]
        # throws before it places a group).
        total = max(0, wanted)
    else:
        total = (live_pieces if live_pieces is not None
                 else profile.color_readback_segments or dev.segment_count)
        group_count = build.ceil_div(total, group_size)

    groups: list[tuple[int, list[tuple[int, int, int, int]]]] = []
    for g in range(1, group_count + 1):
        reply = await read_reply(build.bulb_group_query(g, v2=True))
        expected = max(0, min(group_size, total - (g - 1) * group_size))
        parsed = parse.parse_direct_color_group(reply, expected) if reply else None
        if parsed is None:
            if stop_at_miss:
                break
            continue
        if parsed[0] != g:
            _LOGGER.debug("%s (%s): aa a5 reply carries group %d for the group %d read; "
                          "placing it by the request, as the app does",
                          dev.address, dev.sku, parsed[0], g)
        groups.append((g, parsed[1]))
    segments = parse.direct_groups_to_segments(groups, group_size)
    return Walk(segments, total) if segments else None


async def _section_sum(dev: ReadTarget, read_reply: ReadReply) -> int | None:
    """The ``aa 40`` read of :attr:`~.profile.DeviceProfile.color_readback_ic_sections`: its section sum
    (:attr:`parse.IcCount.section_sum`), or None, and the walk skips, when the read failed or the app's
    reader (``ControllerIcNum.d``) would throw on it: the screen's connect then stops at aa 40 and never
    walks."""
    reply = await read_reply(build.ic_count_query())
    ic = parse.parse_ic_count(reply) if reply else None
    if ic is None or ic.section_sum is None:
        _LOGGER.debug("%s (%s): no section count from the aa 40 read; skipping segment colour read-back",
                      dev.address, dev.sku)
        return None
    return ic.section_sum


def _java_group_count(pieces: int, group_size: int) -> int:
    """``pieces / group_size + (pieces % group_size != 0)`` in Java's truncating integer division, the aa a5
    reads ``VM4Light.N5`` makes (:938-946; none when it is below 1): ``ceil(pieces / group_size)`` for a
    count of 0 or more, and 1 for a negative count above ``-group_size``."""
    whole, part = divmod(abs(pieces), group_size)
    return max(0, (whole if pieces >= 0 else -whole) + (part != 0))


def walk_pieces(dev: ReadTarget, live: int | None) -> int | None:
    """The pieces a live-IC walk covers, from its ``aa 40`` read's pieces ``live`` (:func:`read_live_ic`):
    those, or where the read gave no count, none (the walk is skipped: the app walks the count it stored,
    which the library doesn't have), but the static pieces (``color_readback_segments``, else the segment
    count) on a :attr:`~.profile.DeviceProfile.color_readback_ic_walk_only` profile (the devices.yaml
    ``readback_only`` table). That screen walks the IC it stored at pairing (h6609 ``DetailViewModel.T5``
    over ``H70c4Support.b(SKU, q())``) and writes the static pieces; the static count is its walk at every IC
    but the table's. Logs which, where the read gave no count."""
    if live is not None:
        return live
    if not dev.profile.color_readback_ic_walk_only:
        _LOGGER.debug("%s (%s): no live IC count; skipping segment colour read-back", dev.address, dev.sku)
        return None
    pieces = dev.profile.color_readback_segments or dev.segment_count
    _LOGGER.debug("%s (%s): no live IC count; walking the static %d pieces", dev.address, dev.sku, pieces)
    return pieces


async def read_live_ic(dev: ReadTarget, read_reply: ReadReply) -> int | None:
    """The ``aa 40`` read of :attr:`~.profile.DeviceProfile.color_readback_live_ic`: the pieces
    :func:`live_ic_pieces` derives from its reply, or None (logged) when the read failed or the app's reader
    would store no count (what the walk then does is :func:`walk_pieces`'s)."""
    reply = await read_reply(build.ic_count_query())
    ic = parse.parse_ic_count(reply) if reply else None
    if ic is None:
        _LOGGER.debug("%s (%s): live IC-count read failed: no count", dev.address, dev.sku)
        return None
    pieces = live_ic_pieces(dev, ic)
    if pieces is None:
        _LOGGER.debug("%s (%s): implausible live IC count 0x%04x: no count", dev.address, dev.sku,
                      ic.ic_count)
    return pieces


def live_ic_pieces(dev: ReadTarget, ic: parse.IcCount) -> int | None:
    """Colour pieces (0 or more) for a live ``aa 40`` reply, by the profile's rule (the spec's ``ic_pieces``
    object forms), or None where the app's reader stores no count, the same as a failed read.

    The one-count rules take bytes 0..1 as a signed short (``BleUtil.getSignedShort``, the spec's
    ``ic_count_checked``). A count of 0x1000 or more has none: ``ReadIcController.Companion.a`` then takes
    its byte-swap branch, which indexes past the 2-byte array it was given, so the app's read callback never
    stores it (``VM4Light.D5`` does not run). The reply's third byte is never used. Where the screen reads
    the reply with the IC-pair reader (``color_readback_ic_pair_reader``: the h705a screens'
    ``ControllerIcNum.b`` and H1A45's ``ControllerIcNum.e``), that reader also throws on a second IC field
    of 0x1000 or more (:attr:`parse.IcCount.ic_pair` None, the spec's ``app_pair_fails``), so there is no
    count there either, whatever the rule. Under ``color_readback_ic_skip_zero`` an IC of 0 is none too:
    h705a ``VM4H608A.S4`` (:516-538) skips it and keeps the count the screen holds (goodsType 184).

    - The clamp and divisor, as the app derives them for H6641 (goodsType 247; h61d3, Govee Home 7.5.12
      names): ``ReadIcController.Companion.d`` clamps the count to ``H61D3Support.q(247)`` = 41..70
      (``color_readback_ic_range``); ``H61D3Support.e(247, ic)`` = ceil(ic / 5) pieces
      (``color_readback_ic_per_piece``), the ``bulbStringMaxNum`` of ``VM4Light.P5``. h705a ``Support.b``
      (:317-336) divides 125 / 240's count by 4 in Java's integer division
      (``color_readback_ic_rounding`` floor).
    - The table (``color_readback_ic_table``): h6609 ``Support.getColorPieceSize(173, IC)`` (:146-149), 18
      pieces at IC 36, else 14 (``color_readback_ic_otherwise``); ``H70c4Support.b(SKU, IC)`` (:101-132) for
      H70C4 / H70C5 / H70C7 / H6870 / H80C4 at goodsType 260, 20 at IC 1000, else 10, the walk's alone.
    - The pair (``color_readback_ic_pair_cap``): the counts ``ControllerIcNum.e`` returns
      (:attr:`parse.IcCount.ic_pair`). ``VM4Light.j5`` (:551-575) keeps the first and the second's IC,
      ``VM4Light.h5`` (:1182-1199) stores the two only where the first is not 0, and ``H61D3Support.e``
      (:1142-1153) gives ``min(IC 1, 15) + min(IC 2, 15)`` for two whose second is above 0, else 30
      (goodsType 375)."""
    profile = dev.profile
    if profile.color_readback_ic_pair_reader and ic.ic_pair is None:
        return None   # ControllerIcNum.e throws: no count is stored
    otherwise = profile.color_readback_ic_otherwise or 0   # set with either rule (DeviceProfile)
    cap = profile.color_readback_ic_pair_cap
    if cap is not None:   # with color_readback_ic_pair_reader (DeviceProfile), so a pair is here
        pair = ic.ic_pair or ()
        if len(pair) == 2 and pair[0] != 0 and pair[1] > 0:
            return max(0, min(pair[0], cap) + min(pair[1], cap))
        return otherwise
    return ic_rule_pieces(profile, ic.ic_count_checked)


def ic_rule_pieces(profile: DeviceProfile, count: int | None) -> int | None:
    """Colour pieces (0 or more) for an IC ``count`` by the profile's one-count rule (:func:`live_ic_pieces`'s
    table or clamp and divisor), or None for no count: ``count`` None, or 0 under
    ``color_readback_ic_skip_zero``. A status profile's TLV 0x40 count comes here as the screen's reader
    returns it (``reassemble.StatusReply.ic_count``)."""
    if count is None or (count == 0 and profile.color_readback_ic_skip_zero):
        return None
    otherwise = profile.color_readback_ic_otherwise or 0   # set with the table (DeviceProfile)
    if profile.color_readback_ic_table:
        return dict(profile.color_readback_ic_table).get(count, otherwise)
    ic_range = profile.color_readback_ic_range
    # Every table profile has its clamp (devices/_resolve.py); a custom profile without one uses the
    # reported count as it is, never below 0.
    clamped = max(0, count) if ic_range is None else max(ic_range[0], min(ic_range[1], count))
    per = profile.color_readback_ic_per_piece
    return clamped // per if profile.color_readback_ic_rounding == "floor" else build.ceil_div(clamped, per)


async def read_device_info(dev: ReadTarget, read_reply: ReadReply) -> bool:
    """Populate the static device-info fields of ``dev.state``; returns whether the device answered any of
    the reads.

    ``aa 06`` firmware, ``aa 07 03`` hardware version and ``aa 07 02`` device id (SN), read in that order.
    Each is handled by the firmware of every image analysed (H6006, H6008, H6047, H60A1, H60A6, H5083:
    ``ble_msg_get_version`` / ``ble_msg_get_dev_info`` 02 and 03 on the Realtek images, the H6047 command
    table) and answered live by H60A6, H6047, H61A8, H6052, H6006, H6008, H5083 and, a hardware observation
    (bench 2026-10-21, soft 1.04.05), H6076.

    Where the app sends them goes by the screen's connect path, not by SKU. ``aa 06`` and ``aa 07 03`` are
    connect reads where the screen queues ``ControllerSoftVersion`` / ``ControllerHardVersion`` (the aa-read
    strategies, e.g. rgbiclight ``ControllerStrategyConfig.E1``, and the gt 50 / 90 plug screens). A screen
    that connects with the 0xAC status read sends neither: ``Compose4BaseInfoSingleRead`` queues only the
    ``TimerConfig.f`` time sync and the ``ControllerSingleRead``, and the versions and device id come from the
    0xAC 0x07 TLV (``Compose4BaseInfoSingleRead.w``). That is every ``readback == "status"`` profile. The gt
    43 plug screens don't send them either, and ``aa 07 02`` (``SnController``) the app sends only while
    pairing.

    The library sends all three on the first poll of every profile with a read-back, but a status profile
    without ``DeviceProfile.device_info_reads`` (a status row's ``device_info_reads: none``: every status row
    but the H60A6's and the H6076's), which takes them from TLV 0x07 alone, as its screen does, and never
    calls this. Where the app doesn't send them, they are FIRMWARE-SOURCED extensions (CLAUDE.md rule 6),
    sent only where a firmware image or the bench shows them answered: on the H60A6 and H6076 status screens
    (``device_info_reads: firmware``) and on the gt 43 plugs, which have no other BLE source for them.

    Not sent: ``aa 07 10`` (basic) and ``aa 07 11`` (Wi-Fi). No firmware image has a handler for either
    (most drop the request, which cost a timeout per poll; H60A1 / H60A6 echo it back as zeros) and the app
    never sends them (``BasicInfoController`` / ``BasicWifiInfoController`` have no caller); their record
    layouts exist only as the 0x10 / 0x11 records of the 0xAC 0x07 TLV. Unanswered reads leave fields None.
    ble_mac stays None unless a reply carries a MAC distinct from the connectable address (none confirmed
    yet: that address is otherwise the BLE MAC)."""
    # aa 06 / aa 07 03: the app's connect reads where the screen queues ControllerSoftVersion /
    # ControllerHardVersion (the aa-read strategies, the gt 50 / 90 plugs). No 0xAC status-read screen
    # (Compose4BaseInfoSingleRead) and no gt 43 plug screen sends them. There they are FIRMWARE-SOURCED
    # (CLAUDE.md rule 6) only on the H60A6 and the gt 43 plugs (Realtek ble_msg_get_version and
    # ble_msg_get_dev_info selector 03, get_hw_version: H60A6 sw 1.00.41 @0x9b04a4ac / @0x9b04a5bc, H5083
    # sw 1.00.30 @0x9b0318dc / @0x9b031984) and on the H6076 (answered on the bench, a hardware
    # observation). No other status row makes them (device_info_reads: none).
    answered = await read_versions(dev, read_reply)
    # aa 07 02, FIRMWARE-SOURCED (the app sends it only while pairing, SnController): ble_msg_get_dev_info
    # selector 02 (get_sn) on the Realtek images (e.g. H6006 @0x9b03c87c, H60A6 @0x9b04a528, H5083
    # @0x9b031924) and the H6047's aa 07 handler (@0x01005370); answered live by all seven bench SKUs and by
    # the H6076 (a hardware observation, 2026-10-21). No other status row sends it, as above.
    got, info = await read_info_reply(dev, read_reply, build.INFO_SN)
    if info is not None:
        _fold.merge_device_info(dev.state, serial=info.serial, hardware=info.hw_version)
    return answered or got


async def read_versions(dev: ReadTarget, read_reply: ReadReply, *, soft: bool = True,
                        hard: bool = True) -> bool:
    """``aa 06`` (firmware) then ``aa 07 03`` (hardware version), folded into ``dev.state``; returns
    whether the device answered either. The first two reads of :func:`read_device_info`, the gt 50 / 90
    plugs' re-read after a power write (``Device._resync_after_power``), and, one of them alone where only
    ``soft`` or ``hard``, the re-read of a version a firmware-gated fact still waits for
    (``Device._read_missing_versions``)."""
    got_soft = got_hard = False
    if soft:
        reply = await read_reply(build.soft_version_query())
        _fold.merge_device_info(dev.state, firmware=parse.parse_soft_version(reply or b""))
        got_soft = reply is not None
    if hard:
        got_hard, info = await read_info_reply(dev, read_reply, build.INFO_HARD)
        if info is not None:
            _fold.merge_device_info(dev.state, serial=info.serial, hardware=info.hw_version)
    return got_soft or got_hard


async def read_info_reply(
    dev: ReadTarget, read_reply: ReadReply, selector: int
) -> tuple[bool, parse.DeviceInfo | None]:
    """One ``aa 07 <selector>`` read: ``(answered, parsed)``. A reply whose selector byte is not
    the one asked for (e.g. a late reply to the previous read landing in this one) is ignored."""
    reply = await read_reply(build.device_info_query(selector))
    if reply is None:
        return False, None
    got = parse.parse_device_info_selector(reply)
    if got != selector:
        _LOGGER.debug("%s (%s): ignoring aa 07 reply for selector %s to the 0x%02x read: %s",
                      dev.address, dev.sku, None if got is None else f"0x{got:02x}", selector, reply.hex())
        return True, None
    return True, parse.parse_device_info(reply)
