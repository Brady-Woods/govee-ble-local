"""Decoders that moved from hand-written byte slicing onto the spec-generated readers.

Each test pins the reader-driven decoder to the byte-level decode it replaced (kept here as a small
oracle) on seeded-random inputs, so the move is shown to change nothing on the inputs the library
can receive. Where a move deliberately changes a result, the test says so and why.
"""
from __future__ import annotations

import random
from dataclasses import astuple
from typing import Any

from govee_ble_local import crypto, identify
from govee_ble_local.const import PSK
from govee_ble_local.transport import handshake
from govee_ble_local.wire import parse, reassemble
from govee_ble_local.wire.reassemble import SIGNED_LEN_MIN, TlvCut

N = 3000


def _rng(salt: int) -> random.Random:
    return random.Random(0x5EAD ^ salt)  # noqa: S311 - deterministic test vectors


# ── 0xAC status TLV walk: shared-stream status_tlv vs the byte walk it replaced ─────────────
_Walk = tuple[list[tuple[int, int, bytes]], tuple[Any, ...] | None, list[tuple[Any, ...]]]


def _byte_walk(buf: bytes) -> _Walk:
    """The app's walker, Compose4BaseInfoSingleRead.u (:247-345), over the raw bytes: type / len from
    the bytes, each TLV's body judged by the spec's status_tlv parsed alone from its slice. Two library
    rules on top, until the spec types them: type 0 ends the walk (the padding sentinel), and a 0xA5
    whose body the spec rejects is handed on for the app's record count, as is a non-empty 0x05 value
    mode_read can't read (the app's per-sub-mode minimums aren't typed yet). Returns (type, len, value
    bytes) per TLV, the cut and the skipped list (each cut as TlvCut's fields, negative_len True for a
    len the app reads as negative)."""
    out: list[tuple[int, int, bytes]] = []
    skipped: list[tuple[Any, ...]] = []
    pos, n = 0, len(buf)
    while pos < n and buf[pos] != 0:
        typ = buf[pos]
        if pos + 2 >= n:                                   # u():257-267, :271-282: a clean stop
            return out, ("tail", typ, pos, buf[pos + 1] if pos + 1 < n else None, False), skipped
        length = buf[pos + 1]
        if length >= SIGNED_LEN_MIN:                       # new byte[len] throws
            return out, ("unparsed", typ, pos, length, True), skipped
        end = pos + 2 + length
        if end > n:                                        # System.arraycopy throws
            return out, ("truncated", typ, pos, length, False), skipped
        try:
            parse._parse_type(parse._F.StatusTlv, buf[pos:end])
        except Exception:  # noqa: BLE001
            if typ == 0xA5 or (typ == 0x05 and length > 0):
                skipped.append(("unparsed", typ, pos, length, False))
                out.append((typ, length, bytes(buf[pos + 2:end])))
            elif typ == 0x07 and length >= 1 and buf[pos + 2] not in (0x10, 0x11):
                skipped.append(("unparsed", typ, pos, length, False))
            else:
                return out, ("unparsed", typ, pos, length, False), skipped
        else:
            out.append((typ, length, bytes(buf[pos + 2:end])))
        pos = end
    return out, None, skipped


def _tlv_key(t: Any) -> tuple[int, int, bytes]:
    raw = getattr(t, "_raw_value", None)
    return int(t.type), int(t.len), bytes(raw if raw is not None else t.value)


def _random_tlv_buffer(r: random.Random) -> bytes:
    parts = []
    for _ in range(r.randrange(0, 7)):
        typ = r.choice([0x01, 0x04, 0x05, 0x07, 0x30, 0x41, 0xA5, 0x11, 0x23, r.randrange(1, 256)])
        n = (r.randrange(0x80, 0x100) if r.random() < 0.05
             else r.choice([0, 1, 2, 5, 9, 13, 17, r.randrange(40)]))
        value = bytearray(r.randrange(256) for _ in range(min(n, 50)))
        if typ == 0x07 and value and r.random() < 0.6:
            value[0] = r.choice([0x10, 0x11, 0x02, 0x03, 0x06])
        parts.append(bytes([typ, n]) + value)
    buf = b"".join(parts)
    if r.random() < 0.3:
        buf = buf[: r.randrange(len(buf) + 1)]
    elif r.random() < 0.3:
        buf += bytes(r.randrange(6))
    return buf


def test_tlv_walk_matches_the_byte_walk() -> None:
    r = _rng(1)
    for _ in range(N):
        buf = _random_tlv_buffer(r)
        skipped: list[TlvCut] = []
        tlvs, cut = reassemble.walk_status_tlvs(buf, skipped=skipped)
        want_tlvs, want_cut, want_skipped = _byte_walk(buf)
        assert [_tlv_key(t) for t in tlvs] == want_tlvs, buf.hex()
        assert (astuple(cut) if cut else None) == want_cut, buf.hex()
        assert [astuple(s) for s in skipped] == want_skipped, buf.hex()


# ── 0xAC colour groups: color_group_read with the app's record count ─────────────────────────
def _burst(tlvs: bytes) -> list[bytes]:
    """A one- or multi-packet 0xAC burst carrying ``tlvs`` (AbsController4BleSingleSendMultiBack)."""
    first_cap, mid = 12, 17
    if len(tlvs) <= first_cap:
        body = bytes([0xAC, 0x00, 0x01, 0x00, len(tlvs), 0x03, 0x00]) + tlvs
        return [_frame(body)]
    rest = tlvs[first_cap:]
    chunks = [rest[i:i + mid] for i in range(0, len(rest), mid)]
    total = 1 + len(chunks)
    header = bytes([0xAC, 0x00, total & 0xFF, total >> 8, len(chunks[-1]), 0x03, 0x00])
    frames = [_frame(header + tlvs[:first_cap])]
    for i, chunk in enumerate(chunks, start=1):
        frames.append(_frame(bytes([0xAC, 0xFF if i == len(chunks) else i]) + chunk))
    return frames


def _frame(body: bytes) -> bytes:
    """A zero-padded 20-byte frame with its BCC."""
    buf = bytearray(20)
    buf[: len(body)] = body
    buf[19] = crypto.checksum(bytes(buf[:19]))
    return bytes(buf)


def _group(index: int, records: int, base: int = 0) -> bytes:
    return bytes([0xA5, 1 + 4 * records, index]) + b"".join(
        bytes([50, base + k, base + k, base + k]) for k in range(records))


def test_h60a6_groups_take_the_apps_record_count() -> None:
    # 13 pieces: groups 1..3 hold 4, the last (4) holds 1
    # (VM4LightH60A6$afterConnectedSingleReadDeviceInfo$1.o).
    tlvs = b"\x01\x01\x01" + _group(1, 4) + _group(2, 4, 10) + _group(3, 4, 20) + _group(4, 4, 30)
    st = reassemble.parse_status(_burst(tlvs), pieces=13)
    # Group 4 carried 4 records, the app reads 1 (segment 12) and ignores the rest.
    assert [s.index for s in st.segments] == list(range(13))
    assert st.segments[12].rgb == (30, 30, 30)
    # With no piece count the TLV len decides, as before: segments 12..15 from group 4.
    assert [s.index for s in reassemble.parse_status(_burst(tlvs)).segments] == list(range(16))


def test_a_group_short_of_its_count_fails_the_read() -> None:
    # Group 2 with 3 records where the app reads 4: Controller4ColorInfoByGroup.q throws, no state.
    tlvs = b"\x01\x01\x01" + _group(1, 4) + _group(2, 3)
    assert reassemble.parse_status(_burst(tlvs), pieces=13) == reassemble.StatusReply()
    assert reassemble.parse_status(_burst(tlvs)).is_on is True   # len-derived: read as before


def test_group_record_count() -> None:
    assert [reassemble.group_record_count(g, 17, 13) for g in (1, 2, 3, 4, 5)] == [4, 4, 4, 1, 4]
    assert [reassemble.group_record_count(g, 17, 12) for g in (1, 2, 3)] == [4, 4, 4]
    assert reassemble.group_record_count(1, 17, None) == 4
    assert reassemble.group_record_count(1, 21, None) == 5
    assert reassemble.group_record_count(1, 0, None) == 0


# ── aa a5 direct colour group: color_group_read vs the byte slicing ──────────────────────────
def test_direct_color_group_matches_the_byte_slice() -> None:
    r = _rng(2)
    for _ in range(N):
        frame = bytearray(r.randrange(256) for _ in range(20))
        frame[0], frame[1] = 0xAA, 0xA5
        expected = r.randrange(-1, 7)
        n = max(0, min(expected, 16 // 4))
        want = (frame[2], [tuple(frame[3 + 4 * i:7 + 4 * i]) for i in range(n)])
        assert parse.parse_direct_color_group(bytes(frame), expected) == want


# ── e7 handshake: the spec's handshake type vs the byte slicing ──────────────────────────────
def test_handshake_parse_matches_the_byte_slice() -> None:
    r = _rng(3)
    for _ in range(N):
        pt = bytearray(r.randrange(256) for _ in range(r.choice([2, 17, 18, 19, 20, 20, 20])))
        pt[0] = 0xE7 if r.random() < 0.9 else r.randrange(256)
        pt[1] = r.choice([0x01, 0x02, 0x11, r.randrange(256)])
        ct = crypto.encrypt(bytes(pt), PSK)
        # The frame reader's handshake.data is bytes 2..len-2 (it leaves the BCC byte out), so a key
        # needs 19 bytes; the connection only delivers 20-byte frames.
        key = bytes(pt[2:18]) if len(pt) >= 19 and pt[0] == 0xE7 and pt[1] == 0x01 else None
        assert handshake.parse_session_key(ct) == key
        assert handshake.is_step2_ack(ct) == (len(pt) >= 3 and pt[0] == 0xE7 and pt[1] == 0x02)


# ── advertisements: the spec's manufacturer_data type vs the byte logic ──────────────────────
def _byte_adv(mfg: dict[int, bytes], on_mask: int | None) -> tuple[Any, Any]:
    pact = onoff = None
    for cid, v in mfg.items():
        hdr, hi = cid & 0xFF, (cid >> 8) & 0xFF
        if pact is None and len(v) >= 4 and hi == 0x88 and v[0] == 0xEC and hdr & 0x0F:
            pact = (bool(hdr & 0x40), hdr & 0x0F, (v[1] << 8) | v[2], v[3])
        if onoff is None and hi == 0x88 and len(v) >= 5 and v[0] == 0xEC and 2 <= hdr <= 0x7F:
            onoff = v[4] == 1 if on_mask is None else bool(v[4] & on_mask)
    return pact, onoff


def test_advertisement_parse_matches_the_byte_logic() -> None:
    r = _rng(4)
    for _ in range(N):
        mfg: dict[int, bytes] = {}
        for _ in range(r.randrange(0, 3)):
            cid = r.choice([r.randrange(0x10000), 0x8800 | r.choice([0, 1, 2, 0x41, 0x7F, 0x80, 0xC1])])
            value = bytearray(r.randrange(256) for _ in range(r.choice([0, 3, 4, 5, 6, 9])))
            if value and r.random() < 0.8:
                value[0] = 0xEC
            mfg[cid] = bytes(value)
        on_mask = r.choice([None, 0x01, 0xFF])
        assert (identify.parse_manufacturer_data(mfg),
                identify.parse_broadcast_onoff(mfg, on_mask=on_mask)) == _byte_adv(mfg, on_mask)


def test_advertisement_company_id_outside_16_bits_is_skipped() -> None:
    # bleak's company ids are 16-bit; a larger key can't be the AD payload's two bytes (it used to be
    # read through its low 16 bits).
    value = bytes([0xEC, 0x12, 0x34, 0x01, 0x01])
    assert identify.parse_manufacturer_data({0x1_8841: value}) is None
    assert identify.parse_manufacturer_data({0x8841: value}) == (True, 1, 0x1234, 0x01)
    assert identify.parse_broadcast_onoff({-1: value}) is None
