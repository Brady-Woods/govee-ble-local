"""0xAC status bursts assembled and walked as the app does it.

Burst assembly is ``AbsController4BleSingleSendMultiBack`` as ``ControllerSingleRead`` runs it
(``isSameController`` / ``onResult``): a first chunk with the request's command 3 and sub 0, then each
index in order, then the last packet once every packet before it is in. The TLV walk is
``Compose4BaseInfoSingleRead.u``: a clean stop at the buffer's tail, a 0xA5 read with the app's record
count and placed only where ``SubMode4Color.j`` can place it, and the last 0x07 block winning. No
hardware."""
from __future__ import annotations

import random

import pytest
from fake_ble import FakeGovee, ble_device, frame

from govee_ble_local.devices.device import make_device
from govee_ble_local.devices.profile import profile_for, wire_profile
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.transport.connection import GoveeConnection
from govee_ble_local.wire import _spec, build, describe, parse
from govee_ble_local.wire import reassemble as r

# Real captured H60A6 0xAC status burst (13 segments, both zones on, brightness 80).
# Device ids and MACs in it are made-up examples (checksums recomputed).
_BURST = [bytes.fromhex(h) for h in (
    "ac000a000c0300010101040150050415010000e8",
    "ac010707065753005e00020711105653005e00ae",
    "ac0202a60a0100290104030000070d1102005e69",
    "ac030053560100290104031104001e0f0f12079a",
    "ac04ff640000800f00231000000080000000808f",
    "ac0500000080000000804102020130020101a57e",
    "ac06110132ff00006400ff00640000ff64ff00ec",
    "ac0700a511026400ff00640000ff64ff000064e2",
    "ac0800ff00a51103640000ff64ff00006400ff77",
    "acff00640000ffa5050464ff00000000000000f7",
)]
_FULL = r.parse_status(_BURST, pieces=13)


def _chunk(data: bytes) -> list[bytes]:
    """A TLV stream as the device chunks it (AbsController4BleSingleSendMultiBack.g / .i / .h)."""
    if len(data) <= 12:
        return [frame(0xAC, 0x00, 1, 0, len(data), 0x03, 0x00, *data)]
    rest = data[12:]
    pieces = [rest[i:i + 17] for i in range(0, len(rest), 17)]
    out = [frame(0xAC, 0x00, 1 + len(pieces), 0, len(pieces[-1]), 0x03, 0x00, *data[:12])]
    out += [frame(0xAC, i, *piece) for i, piece in enumerate(pieces[:-1], start=1)]
    out.append(frame(0xAC, 0xFF, *pieces[-1]))
    return out


def _with(chunk: bytes, offset: int, *values: int) -> bytes:
    """``chunk`` with bytes from ``offset`` replaced and its BCC recomputed."""
    buf = bytearray(chunk)
    buf[offset:offset + len(values)] = bytes(values)
    return frame(*buf[:19])


def test_the_real_burst_decodes_whole() -> None:
    assert _FULL.is_on is True and _FULL.brightness == 0x50 and len(_FULL.segments) == 13


# ── A. burst assembly ────────────────────────────────────────────────────────────────────────
def test_a_copy_that_arrives_before_the_first_chunk_is_ignored() -> None:
    # isSameController:204-205: with nothing taken, only a first chunk is; the in-order copy is used.
    early = _with(_BURST[1], 2, 0x07, 0x01, 0x10)     # a different copy of chunk 1
    frames = [early, _BURST[0], *_BURST[1:]]
    chunks, complete, conflicts = r.burst_chunks(frames)
    assert complete and chunks == _BURST and conflicts == 1
    assert r.parse_status(frames, pieces=13) == _FULL


def test_a_first_chunk_of_another_command_is_ignored() -> None:
    # f():100-103: the first chunk's bytes 5-6 must be the request's command 3 and sub 0.
    other = _with(_BURST[0], 5, 0x99, 0x07, 0x01, 0x01, 0x01, 0x04, 0x01, 0x64)
    st = r.parse_status([other, *_BURST], pieces=13)
    assert st == _FULL and st.brightness == 0x50


def test_an_early_last_packet_is_ignored() -> None:
    # isSameController:209-218: a 0xFF before the packets ahead of it are in is END_PKG_ORDER.
    frames = [_BURST[0], _BURST[-1], *_BURST[1:-1]]
    assert r.burst_chunks(frames)[1] is False and r.parse_status(frames) == r.StatusReply()
    # A later 0xFF completes it; the app takes that copy's tail.
    late = _with(_BURST[-1], 2, *bytes.fromhex("00640000ffa5050464ff0000"))
    assert r.burst_chunks([*frames, late])[0][-1] == late
    assert r.parse_status([*frames, late], pieces=13) == r.parse_status([*_BURST[:-1], late], pieces=13)


def test_chunks_out_of_order_give_no_state() -> None:
    frames = [_BURST[0], _BURST[2], _BURST[1], *_BURST[3:]]
    assert r.parse_status(frames) == r.StatusReply()


def test_the_position_of_the_last_packet_also_ends_the_burst() -> None:
    # isSameController:220 / onResult:302: index packets-1 in place of 0xFF is taken as the last packet.
    last = _with(_BURST[-1], 1, 0x09)
    assert r.parse_status([*_BURST[:-1], last], pieces=13) == _FULL


@pytest.mark.parametrize("count", [0, 0xFFFF])
def test_a_packet_count_of_0_or_0xffff_never_completes(count: int) -> None:
    first = _with(_BURST[0], 2, count & 0xFF, count >> 8)
    assert r.burst_chunks([first, *_BURST[1:]])[1] is False


def test_a_bad_last_len_fails_the_burst() -> None:
    first = _with(_BURST[0], 4, 18)                    # > 17 for a multi-packet burst: g() returns -1
    asm = r.BurstAssembler()
    assert asm.feed(first) is True and asm.status == "failed"
    assert asm.feed(_BURST[1]) is False
    assert r.burst_chunks([first, *_BURST[1:]]) == ([], False, 0)


def _app_model(frames: list[bytes]) -> bytes | None:
    """AbsController4BleSingleSendMultiBack.isSameController / onResult over raw bytes: the joined
    buffer when the burst completes, None otherwise."""
    d, e, f, g = 0, -1, -1, bytearray()
    for b in frames:
        if len(b) < 19 or b[0] != 0xAC:
            continue
        if d == 0:
            if not (b[1] == 0 and b[5] == 3 and b[6] == 0):
                continue
            e, f, d = b[2] | b[3] << 8, b[4], 1
            if e != 1:
                if f > 17:
                    return None
                g += b[7:19]
                continue
            return bytes(b[7:7 + f]) if f <= 12 else None
        if b[1] == 0xFF:
            if d != e - 1:
                continue
        elif b[1] != d:
            continue
        if d == e - 1:
            return bytes(g + b[2:2 + f])
        g += b[2:19]
        d += 1
    return None


def test_burst_assembly_matches_the_app_on_perturbed_bursts() -> None:
    rnd = random.Random(20261017)  # noqa: S311 - deterministic test vectors
    for _ in range(3000):
        frames = list(_BURST)
        for _op in range(rnd.randrange(4)):
            i = rnd.randrange(len(frames))
            op = rnd.randrange(5)
            if op == 0:
                frames.insert(i, frames[rnd.randrange(len(frames))])                 # duplicate
            elif op == 1 and len(frames) > 1:
                del frames[i]                                                         # drop
            elif op == 2:
                j = rnd.randrange(len(frames))
                frames[i], frames[j] = frames[j], frames[i]                           # swap
            elif op == 3:
                frames.insert(i, _with(_BURST[-1], 2, rnd.randrange(256)))            # stray 0xFF
            else:
                frames.insert(i, _with(_BURST[0], 5, rnd.randrange(256), rnd.randrange(256)))
        want = _app_model(frames)
        assert r.reassemble(frames) == (want or b""), [f[1] for f in frames]


# ── A. the query ends where the app's read does ────────────────────────────────────────────
@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.1)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_STEP2_TIMEOUT", 0.1)


def _conn(monkeypatch: pytest.MonkeyPatch, fake: FakeGovee) -> GoveeConnection:
    monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
    c = GoveeConnection(ble_device("AA:BB:CC:DD:EE:A5", "GVH60A6"), encryption=conn_mod.Encryption.NONE)
    c._idle_disconnect = 0
    return c


async def test_a_bad_last_len_ends_the_query_at_once(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    fake.status_burst = [_with(_BURST[0], 4, 18), *_BURST[1:]]
    c = _conn(monkeypatch, fake)
    frames = await c.query(build.status_query(full=True), timeout=3.0, idle_gap=2.0)
    assert [f[1] for f in frames if f[0] == 0xAC] == [0x00]


async def test_a_stray_last_packet_before_the_burst_does_not_end_the_query(monkeypatch, fast) -> None:
    fake = FakeGovee(encrypted=False)
    fake.status_burst = list(_BURST)
    c = _conn(monkeypatch, fake)
    await c.connect()
    stray = _with(_BURST[-1], 2, 0x11)

    def push_stray_then_burst(client, plain) -> None:
        if plain[0] == 0xAC:
            client.push(stray, 0.001)
            for i, f in enumerate(_BURST):
                client.push(f, 0.05 + 0.002 * i)

    fake.on_write = push_stray_then_burst
    frames = await c.query(build.status_query(full=True), timeout=3.0)
    assert r.parse_status([f for f in frames if f[0] == 0xAC], pieces=13) == _FULL


async def test_an_ignored_frame_does_not_arm_the_idle_gap(monkeypatch, fast) -> None:
    # The H60A6's first chunk comes ~1.1 s after the request: a stray frame before it must not start
    # the 0.6 s idle gap and end the wait before the burst begins.
    fake = FakeGovee(encrypted=False)
    c = _conn(monkeypatch, fake)
    await c.connect()

    def late_burst(client, plain) -> None:
        if plain[0] == 0xAC:
            client.push(_with(_BURST[3], 2, 0x55), 0.001)         # a late chunk of some earlier burst
            for i, f in enumerate(_BURST):
                client.push(f, 0.3 + 0.002 * i)

    fake.on_write = late_burst
    frames = await c.query(build.status_query(full=True), timeout=3.0, idle_gap=0.1)
    assert r.parse_status([f for f in frames if f[0] == 0xAC], pieces=13) == _FULL


# ── B. the TLV walk ──────────────────────────────────────────────────────────────────────────
def _status(tlvs: bytes, pieces: int | None = 13) -> r.StatusReply:
    return r.parse_status(_chunk(tlvs), pieces=pieces)


@pytest.mark.parametrize("tail", [b"\x04", b"\x30\x00", b"\x99\x80"])
def test_a_tlv_cut_by_the_buffers_end_stops_cleanly(tail: bytes) -> None:
    # u():257-267 (no len byte) and :271-282 (no byte after it, whatever the len): success.
    st = _status(bytes([0x01, 0x01, 0x01]) + tail)
    assert st.is_on is True


@pytest.mark.parametrize("stream", [
    bytes([0x01, 0x01, 0x01, 0x99, 0x80, 0x00]),          # a negative len with a value byte: throws
    bytes([0x30, 0x00, 0x01, 0x01, 0x01]),                # 0x30 with no value bytes: the VM reads two
    bytes([0x04, 0x05, 0xAA]),                            # a value past the end: arraycopy throws
])
def test_what_u_throws_on_gives_no_state(stream: bytes) -> None:
    assert _status(stream) == r.StatusReply()


# The values the H60A6 status screen's read fails on (govee_state app_read_fails, under
# reassemble.status_profile with the H60A6's wire profile), each mid-stream: a TLV follows, so none is the
# stream's 2-byte tail. Compose4BaseInfoSingleRead.u hands each value to
# VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.o (0x30 / 0x41 / 0xA5) and h60a1 Info4Detail.Q (0x05);
# a throw fails the whole read. The empty 0x05 is the H60A6's own and is read past
# (test_mode_read_decoders.test_h60a6s_empty_mode_value_is_read_past_and_every_other_failure_still_fails).
_SCREEN_FAILS = [
    [0x01, 0x00], [0x04, 0x00], [0x07, 0x00], [0x23, 0x00], [0x30, 0x00], [0x41, 0x00], [0xA5, 0x00],  # empty
    [0x23, 0x01, 0x00],                      # a timer list short of its record
    [0x05, 0x03, 0x15, 0x01, 0x0A],          # a mode value short of its sub-mode's layout
    [0x05, 0x01, 0x04],
    [0x05, 0x03, 0x13, 0x01, 0x50],          # music short of the H60A6's V2 layout's colour flag
    [0xA5, 0x05, 0x01, 0x32, 1, 2, 3],       # a colour group short of its records
]
# ... and values it reads whole, so the records after them apply.
_SCREEN_READS = [
    [0x40, 0x00], [0x40, 0x01, 0x05], [0xA3, 0x00],   # its lambda reads neither: forms f_40 / f_a3 unknown
    [0x00, 0x00], [0x00, 0x01, 0x07],                # a tag 0 is walked on (u() hands it to the fallback)
    [0x05, 0x04, 0x13, 0x01, 0x50, 0x00],            # music with the V2 layout's colour flag
]


@pytest.mark.parametrize(("value", "reads"), [(bytes(v), False) for v in _SCREEN_FAILS]
                         + [(bytes(v), True) for v in _SCREEN_READS],
                         ids=[bytes(v).hex() for v in _SCREEN_FAILS + _SCREEN_READS])
def test_the_h60a6_status_read_fails_where_its_screen_does(value: bytes, reads: bool) -> None:
    h60a6 = profile_for("H60A6")
    assert h60a6 is not None
    st = r.parse_status(_chunk(bytes([0x01, 0x01, 0x01]) + value + bytes([0x04, 0x01, 0x32])), pieces=13,
                        decode=wire_profile(h60a6))
    assert ((st.is_on, st.brightness) == (True, 0x32)) if reads else st == r.StatusReply()


def test_a_value_the_spec_leaves_unflagged_still_fails_the_read(monkeypatch: pytest.MonkeyPatch) -> None:
    # parse_status reads fields of the switch, brightness, zone and device-info values. Were the spec to
    # stop flagging an empty one (app_read_fails), the read still fails, rather than raising out of the poll.
    monkeypatch.setattr(_spec.StatusStream.StatusTlv, "app_read_fails", property(lambda self: False))
    for empty in ([0x01, 0x00], [0x04, 0x00], [0x07, 0x00], [0x30, 0x00]):
        assert _status(bytes([0x01, 0x01, 0x01, *empty, 0x04, 0x01, 0x32])) == r.StatusReply(), empty


def test_without_a_decode_the_status_read_takes_the_h60a6s() -> None:
    # parse_status(decode=None), 1.3.0's call, and the analyzer read the 0x05 value with the H60A6's colour
    # decode (the one device whose readback is this read): a 0x15 value short of its layout fails both.
    h60a6 = profile_for("H60A6")
    assert h60a6 is not None
    report = _chunk(bytes([0x01, 0x01, 0x01, 0x05, 0x04, 0x15, 0x01, 0x0A, 0x8C, 0x04, 0x01, 0x32]))
    assert parse.mode_color(r.parse_status(report).mode) == parse.mode_color(
        r.parse_status(report, decode=wire_profile(h60a6)).mode) == parse.KelvinReport(kelvin=2700)
    short = _chunk(bytes([0x01, 0x01, 0x01, 0x05, 0x03, 0x15, 0x01, 0x0A, 0x04, 0x01, 0x32]))
    assert r.parse_status(short) == r.StatusReply()
    assert describe.status_burst_report(short).gaps == [
        "0xAC reply TLV type 0x05 (len 3) fails the app's read"]


def _group(index: int, records: int, extra: bytes = b"") -> bytes:
    value = bytes([index]) + bytes([0x64, 0xFF, 0x00, 0x00]) * records + extra
    return bytes([0xA5, len(value)]) + value


def test_a_colour_group_is_read_with_the_apps_count_whatever_its_len() -> None:
    # VM .o:112-116 -> Controller4ColorInfoByGroup.Companion.q reads exactly the count: extra bytes
    # are ignored (WAS: a len that isn't 1 + 4k stopped the walk, so no state).
    st = _status(bytes([0x01, 0x01, 0x01]) + _group(4, 1, b"\xee"))
    assert st.is_on is True and [(s.index, s.brightness, s.rgb) for s in st.segments] == [
        (12, 100, (255, 0, 0))]
    st = _status(_group(1, 4, b"\x01\x02"))
    assert [s.index for s in st.segments] == [0, 1, 2, 3]
    assert _status(_group(1, 3)) == r.StatusReply()      # short of its 4: q() throws


@pytest.mark.parametrize("index", [0, 5, 0x80])
def test_a_group_the_app_cant_place_fails_the_read(index: int) -> None:
    # SubMode4Color.j:113-147 throws for group 0 and for one starting past the last piece.
    assert _status(bytes([0x01, 0x01, 0x01]) + _group(1, 4) + _group(index, 4)) == r.StatusReply()


def test_a_group_starting_exactly_at_the_end_is_a_no_op() -> None:
    # pieces 12: 3 groups; group 4 starts at piece 12, so j() copies nothing. Read (q) all the same.
    st = _status(_group(1, 4) + _group(4, 4), pieces=12)
    assert [s.index for s in st.segments] == [0, 1, 2, 3]
    assert _status(_group(1, 4) + _group(4, 3), pieces=12) == r.StatusReply()


def test_without_the_piece_count_group_0_is_dropped_as_before() -> None:
    st = _status(_group(0, 1) + _group(1, 1), pieces=None)
    assert [s.index for s in st.segments] == [0]


_BASIC = bytes([0x07, 0x11, 0x10, 0x56, 0x53, 0x00, 0x5E, 0x00, 0x02, 0xA6, 0x0A,
                1, 0, 41, 1, 4, 3, 0, 0])
_BASIC_2 = bytes([0x07, 0x11, 0x10, 0x09, 0x09, 0x09, 0x09, 0x09, 0x09, 0x09, 0x09,
                  2, 0, 5, 3, 0, 7, 0, 0])
_BASIC_ZERO = bytes([0x07, 0x11, 0x10, *bytes(16)])
_WIFI = bytes([0x07, 0x0D, 0x11, 0x02, 0x00, 0x5E, 0x00, 0x53, 0x56, 2, 0, 9, 3, 0, 1])
_WIFI_ZERO = bytes([0x07, 0x0D, 0x11, *bytes(12)])


def test_the_last_device_info_block_wins() -> None:
    # Compose4BaseInfoSingleRead.w:370-437 sets plain fields (WAS: the first block won).
    st = _status(_BASIC + _BASIC_2)
    assert (st.serial_number, st.firmware_version, st.hardware_version) == (
        "09:09:09:09:09:09:09:09", "2.00.05", "3.00.07")
    # A zero UID leaves the serial; zero versions are none (the app sets "").
    st = _status(_BASIC + _BASIC_ZERO)
    assert (st.serial_number, st.firmware_version, st.hardware_version) == (
        "0A:A6:02:00:5E:00:53:56", None, None)
    assert _status(_WIFI + _WIFI_ZERO).wifi_mac is None
    assert _status(_WIFI_ZERO + _WIFI).wifi_mac == "02:00:5E:00:53:56"


async def test_an_incomplete_burst_on_a_device_retries_then_stays_stale() -> None:
    # The device's status read: one retry, then the state is left as it was (_readback.status_burst).
    d = make_device(ble_device("AA:BB:CC:DD:EE:A6", "H60A6"), "H60A6")
    assert d is not None
    calls: list[bytes] = []

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        calls.append(frame)
        return list(_BURST[:-1]) if frame[0] == 0xAC else []

    d._conn.query = query  # type: ignore[method-assign,assignment]
    d.state.brightness = 42
    await d._read_status()
    assert sum(1 for f in calls if f[0] == 0xAC) == 2 and d.state.brightness == 42


def test_a_successful_read_defaults_a_missing_switch_and_brightness() -> None:
    # A fresh DeviceBaseInfoFromDevice (switch false, brightness 100) is what h60a1 Info4Detail.n0 applies.
    st = _status(bytes([0x30, 0x02, 0x01, 0x00]))
    assert (st.is_on, st.brightness, st.zone_power) == (False, 100, {0: True, 1: False})
    st = _status(bytes([0x01, 0x01, 0x01, 0x04]))          # a clean tail stop is a success too
    assert (st.is_on, st.brightness) == (True, 100)
    assert _status(bytes([0x04, 0x05, 0xAA])) == r.StatusReply()   # a failed read applies nothing
