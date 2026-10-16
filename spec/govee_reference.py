#!/usr/bin/env python3
"""
Govee BLE — reference codec (the "framing" layer Kaitai cannot express) + dispatch wiring.

This is the executable half of the spec. `govee_ble.ksy` owns the *byte structure*; this module owns the
three things a single-buffer grammar structurally cannot do, each grounded in the decompiled app:

    (1) DECRYPT      V1 = AES-128-ECB(16-byte blocks) + RC4(remainder)   [com/govee/encryp/ble/Safe.java]
    (2) REASSEMBLE   multi-frame transfers (0xAC status; 0xA1/0xA3 alike) [GOVEE_BLE_GATT_PROTOCOL.md 4.5/4.3]
    (3) XOR checksum (BCC) compute/verify                                [4.1]

...plus thin `dispatch` helpers that feed the parametric .ksy types the one discriminator they need
(`devices.yaml -> client_profile`). A real client is then: BLE-transport + this module + the generated
parser + devices.yaml. Nothing else hand-parses bytes.

Run (self-test / round-trip harness):
    kaitai-struct-compiler -t python govee_ble.ksy      # -> govee_ble_frame.py (put on sys.path)
    python govee_reference.py [--strict]

Without a fresh `ksc` build on sys.path the parser checks use the reader committed in the library
(`../src/govee_ble_local/_generated`); they are skipped (exit 0) only when neither can be imported, and
`--strict` turns that skip into a failure.

Only the crypto needs a third-party AES (pip install pycryptodome); everything else is stdlib + the Kaitai
runtime (pip install kaitaistruct). RC4 is implemented here (Safe.f/g); AES-ECB is delegated.
"""
from __future__ import annotations

import importlib
from collections.abc import Sequence
from io import BytesIO
from typing import Any, Final


# ─────────────────────────── (3) XOR checksum (BCC) — §4.1 ───────────────────────────


def bcc(frame: bytes | bytearray) -> int:
    """XOR of bytes 0..18 (BleUtils.o :1016). == frame[19] for a valid 20-byte frame."""
    x = 0
    for b in frame[:19]:
        x ^= b
    return x


FRAME_LEN: Final = 20             # every frame is 20 bytes: 19 of proType/cmd/payload + the BCC
FRAME_BODY_MAX: Final = FRAME_LEN - 1


def append_bcc(payload: bytes) -> bytes:
    """[proType, cmd, payload...] (<=19 bytes) -> a zero-padded 20-byte frame with the checksum at byte 19.

    ValueError for a longer payload: the app cannot send one either (BleUtils.p :1030-1041 copies the
    ext bytes into a 20-byte array at offset 2, so more than 18 throw and exactly 18 lose their last
    byte to the BCC; AbsController.q :47-60 truncates ext bytes to 17 before that). Without this check
    a 20-byte payload had byte 19 overwritten by the BCC and a longer one returned a >20-byte frame."""
    if len(payload) > FRAME_BODY_MAX:
        raise ValueError(
            f"payload too long: {len(payload)} bytes (max {FRAME_BODY_MAX} so the frame fits in {FRAME_LEN})")
    b = bytearray(FRAME_LEN)
    b[: len(payload)] = payload
    b[FRAME_BODY_MAX] = bcc(b)
    return bytes(b)


def verify_bcc(frame: bytes) -> bool:
    return len(frame) == 20 and frame[19] == bcc(frame)


# ─────────────────────── (1) V1 wire cipher (AES-ECB + RC4) — Safe.java ───────────────────────
# Safe.b()/d(): for each full 16-byte block do AES-128-ECB/NoPadding; the trailing len%16 bytes go through
# RC4 (Safe.f = KSA, Safe.g = PRGA-XOR). Same key for both. A 20-byte frame = 1 AES block + 4 RC4 bytes.
# RC4 is re-keyed from the key ALONE every call (no IV) -> the remainder keystream is deterministic per key.


def _rc4(data: bytes, key: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    out = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out.append(byte ^ s[(s[i] + s[j]) & 0xFF])
    return bytes(out)


def crypt_v1(buf: bytes, key: bytes, *, encrypt: bool) -> bytes:
    """V1 codec (structure is symmetric; `encrypt` picks AES direction). Needs a 16-byte AES key
    (handshake = fixed PSK; payloads = session key). Raises if pycryptodome is absent."""
    # pycryptodome is optional (test extra only), so it is imported by name: the reference stays
    # independent of the library's `cryptography` backend, and type checkers don't need it installed.
    aes_mod: Any = importlib.import_module("Crypto.Cipher.AES")
    aes = aes_mod.new(key, aes_mod.MODE_ECB)
    out = bytearray()
    nblocks = len(buf) // 16
    for i in range(nblocks):
        blk = buf[i * 16 : i * 16 + 16]
        out += aes.encrypt(blk) if encrypt else aes.decrypt(blk)
    rem = len(buf) % 16
    if rem:
        out += _rc4(buf[nblocks * 16 :], key)
    return bytes(out)


# ─────────────────────── (2) reassembly: 0xAC status burst -> TLV buffer — §4.5 ───────────────────────
# AbsController4BleSingleSendMultiBack (base2light/ble/controller), per received 20-byte frame:
# first frame (tag 0x00): [AC, 00, pktsLo, pktsHi, lastLen, cmd, sub, <12 data @7..18>, CK]      (.f/.g)
#                         pkts = allBackPkgs = TOTAL packet count incl. this one and the last (u16 LE,
#                         BleUtils.H(.., false)); lastLen = endPkgLen = valid data bytes in the last packet.
# next  frames (tag = position 1..pkts-2): [AC, tag, <17 data @2..18>, CK]                         (.i)
# last  frame (tag 0xFF, position pkts-1): [AC, FF, <lastLen data @2..>, zero pad, CK]            (.h)
# pkts == 1: the first frame is the whole reply and holds lastLen (<= 12) data bytes @7 (.g).
# isSameController() matches frames by position: a 0xFF tag only at position pkts-1, which also takes
# its own position as the tag (K(tag) == backPos); onResult() runs .h() at pkts-1 for either tag.
# Reassembled = the concatenated data, exactly 12 + 17*(pkts-2) + lastLen bytes (the TLV stream).
_AC_FIRST_LEN = 12   # AbsController4BleSingleSendMultiBack.a()
_AC_DATA_LEN = 17    # ... f57173o (middle packets, .i)
_AC_LAST_TAG = 0xFF  # ... f57169k (END_PKG)


def reassemble_ac(frames: list[bytes]) -> bytes:
    """An in-order 0xAC burst -> the TLV buffer (AbsController4BleSingleSendMultiBack.g/.i/.h).
    ValueError for a burst the app would reject (short, out of order, or lastLen out of range).
    The last packet may carry tag 0xFF (what the H60A6 sends) or its position pkts-1: the app
    accepts both (isSameController/onResult)."""
    f0 = frames[0]
    if f0[0] != 0xAC or f0[1] != 0x00:
        raise ValueError("0xAC burst must start with the tag-0x00 frame")
    count = f0[2] | (f0[3] << 8)  # allBackPkgs
    last_len = f0[4]              # endPkgLen
    if count == 1:
        if last_len > _AC_FIRST_LEN:
            raise ValueError(f"single-packet lastLen {last_len} > {_AC_FIRST_LEN}")
        return bytes(f0[7 : 7 + last_len])
    if count < 1 or last_len > _AC_DATA_LEN:
        raise ValueError(f"bad 0xAC header: packets={count}, lastLen={last_len}")
    if len(frames) < count:
        raise ValueError(f"0xAC burst incomplete: {len(frames)} of {count} packets")
    data = bytearray(f0[7 : 7 + _AC_FIRST_LEN])
    for pos, f in enumerate(frames[1 : count - 1], start=1):
        # isSameController checks END_PKG (0xFF) before the position, so it's refused mid-burst even
        # at position 255 (:237-247)
        if f[1] != pos or f[1] == _AC_LAST_TAG:
            raise ValueError(f"0xAC packet {pos} carries tag {f[1]:#04x}")
        data += f[2 : 2 + _AC_DATA_LEN]
    last = frames[count - 1]
    # isSameController takes 0xFF here (backPos == allBackPkgs-1, :237-239) or the position itself
    # (K(tag) == backPos, :248); onResult then runs .h() because backPos == allBackPkgs-1 (:326-331).
    if last[1] not in (_AC_LAST_TAG, count - 1):
        raise ValueError(f"0xAC last packet carries tag {last[1]:#04x}, not 0xFF or {count - 1:#04x}")
    data += last[2 : 2 + last_len]
    return bytes(data)


def chunk_ac(tlv: bytes, cmd: int = 0x03, sub: int = 0x00) -> list[bytes]:
    """Inverse of reassemble_ac (for round-trip testing / building a request-response fixture).

    ``sub`` (byte 6) must equal the reader's d() (AbsController4BleSingleSendMultiBack.f): 0 for the
    H60A6 status read (Compose4BaseInfoSingleRead builds a ControllerSingleRead with d() left at 0),
    which is what the H60A6 sends."""
    if len(tlv) <= _AC_FIRST_LEN:  # single packet: lastLen bytes @7, no further frames
        return [append_bcc(bytes([0xAC, 0x00, 1, 0, len(tlv), cmd, sub]) + tlv)]
    rest = tlv[_AC_FIRST_LEN:]
    chunks = [rest[i : i + _AC_DATA_LEN] for i in range(0, len(rest), _AC_DATA_LEN)]
    count = 1 + len(chunks)
    if count > _AC_LAST_TAG:  # the tag byte is the position; 0xFF is reserved for the last packet
        raise ValueError(f"{len(tlv)}-byte TLV needs {count} packets; at most {_AC_LAST_TAG} fit")
    head = bytes([0xAC, 0x00, count & 0xFF, (count >> 8) & 0xFF, len(chunks[-1]), cmd, sub])
    frames = [append_bcc(head + tlv[:_AC_FIRST_LEN])]
    for tag, chunk in enumerate(chunks[:-1], start=1):
        frames.append(append_bcc(bytes([0xAC, tag]) + chunk))
    frames.append(append_bcc(bytes([0xAC, _AC_LAST_TAG]) + chunks[-1]))  # data-bearing last packet
    return frames


# ─────────────────────────── dispatch (wire the generated .ksy) ───────────────────────────
# Import the ksc-generated parser. Kept lazy so the framing helpers above work even before you compile it.

_READER_MODULES = (
    "govee_ble_frame",                              # a fresh `ksc -t python govee_ble.ksy` on sys.path
    "govee_ble_local._generated.govee_ble_frame",   # the reader committed in the library (../src)
)


def _gbf() -> Any:
    """The generated GoveeBleFrame class (untyped, so Any). A fresh ksc build on sys.path wins, so the
    self-test checks the .ksy being edited; otherwise the library's committed reader is used."""
    errors: list[str] = []
    for name in _READER_MODULES:
        try:
            return importlib.import_module(name).GoveeBleFrame
        except ImportError as err:
            errors.append(f"{name}: {err}")
    raise ImportError(f"no generated frame reader importable ({'; '.join(errors)})")


def _stream(buf: bytes) -> Any:
    from kaitaistruct import KaitaiStream
    return KaitaiStream(BytesIO(buf))


def parse_frame(frame20: bytes) -> Any:
    """Verify BCC, then parse one plaintext, single 20-byte frame (proType-switched)."""
    if not verify_bcc(frame20):
        raise ValueError("bad BCC")
    return _gbf().from_bytes(frame20)


def parse_status(tlv_buffer: bytes) -> Any:
    """A reassembled 0xAC buffer -> StatusReply (terminates on the 0x00 padding sentinel or EOF)."""
    return _gbf().StatusReply(_stream(tlv_buffer))


def parse_color_group(group_bytes: bytes, *, has_brightness: bool, record_count: int) -> Any:
    """One 0xA5 colour group; `has_brightness` (from client_profile) selects 4-byte vs 3-byte records."""
    return _gbf().ColorGroupRead(record_count, 1 if has_brightness else 0, _stream(group_bytes))


# ───────────────────────── op15 segment masks (BleUtils.X / makeSelectedTwoBytes) ─────────────────────────
# Every 0x15 colour/brightness builder picks its mask by `posSet.length > 16 ? BleUtils.X(posSet, true)
# : makeSelectedTwoBytes(posSet, true)` (color/Bytes.java h :323, r :498, G :178, b :229, d :278, l :387,
# n :417, u :553; SubModeColorV1.getWriteBytes :672 keys it on its segment count). posSet has one entry
# per device segment, so the width follows the segment count, not the selection.
OP15_LEGACY_MASK_SEGMENTS: Final = 16   # makeSelectedTwoBytes: always 2 bytes, 00 00 if empty or > 16
OP15_MAX_MASK_BYTES: Final = 7          # Bytes.h/r keep coerceAtMost(len, 17 - 10); Bytes.C truncates > 7
OP15_MAX_MASK_SEGMENTS: Final = OP15_MAX_MASK_BYTES * 8


def bleutils_x(pos_set: Sequence[bool]) -> bytes:
    """BleUtils.X(posSet, true) (base-classes10/com/govee/base2kt/utils/BleUtils.java:406-438):
    ceil(n/8) bytes, no cap; byte j holds positions 8j..8j+7 with position 8j+k at bit k (each 8-entry
    chunk is written as a '1'/'0' string, reversed because the flag is true, then parsed base 2)."""
    out = bytearray((len(pos_set) + 7) // 8)
    for i, on in enumerate(pos_set):
        if on:
            out[i // 8] |= 1 << (i % 8)
    return bytes(out)


def make_selected_two_bytes(pos_set: Sequence[bool]) -> bytes:
    """BleUtils.Y(posSet, true, false) = makeSelectedTwoBytes (BleUtils.java:444-494; BleUtil.java:412-443):
    exactly two bytes in the X bit order, and 00 00 for an empty or a more-than-16-entry posSet."""
    if not pos_set or len(pos_set) > OP15_LEGACY_MASK_SEGMENTS:
        return bytes(2)
    return bleutils_x([*pos_set, *([False] * (OP15_LEGACY_MASK_SEGMENTS - len(pos_set)))])


def op15_mask(pos_set: Sequence[bool]) -> bytes:
    """The mask the 0x15 builders write for posSet: 2 bytes up to 16 entries, else ceil(n/8) (uncapped;
    each builder applies its own cap)."""
    if len(pos_set) > OP15_LEGACY_MASK_SEGMENTS:
        return bleutils_x(pos_set)
    return make_selected_two_bytes(pos_set)


def op15_mask_width(segments: int) -> int:
    """Mask bytes for a device with ``segments`` segments: 2 for 0..16, else ceil(n/8). ValueError above
    56 (7 bytes, the most Bytes.h/r/C put on the wire); the library's wire.build.mask_width."""
    if isinstance(segments, bool) or not 0 <= segments <= OP15_MAX_MASK_SEGMENTS:
        raise ValueError(f"segment count must be an integer in 0..{OP15_MAX_MASK_SEGMENTS}, got {segments!r}")
    return 2 if segments <= OP15_LEGACY_MASK_SEGMENTS else (segments + 7) // 8


def op15_mask_from_int(seg_mask: int, segments: int) -> bytes:
    """``seg_mask`` (bit i = segment i) as the op15_mask_width(segments)-byte LSB-first mask; ValueError
    if it does not fit that width (the library's wire.build._mask_bytes)."""
    width = op15_mask_width(segments)
    if not 0 <= seg_mask < 1 << (8 * width):
        raise ValueError(f"segment mask {seg_mask:#x} does not fit {width} bytes ({segments} segments)")
    return seg_mask.to_bytes(width, "little")


def build_op15_color(r: int, g: int, b: int, seg_mask: int, *, variant: int, kelvin: int = 0,
                     tint: tuple[int, int, int] = (0, 0, 0), segments: int | None = None) -> bytes:
    """Build a 0x33 05 15 01 colour-write frame using the parametric op15_color_typed layout.
    variant: 1 basic, 12 H60A1/H60A6 RGB, 11 CCT (from client_profile.color_scheme + the op).

    ``segments`` is the device's segment count, which sets the mask width (op15_mask_width): None, the
    default, keeps the legacy two-byte mask (seg_mask & 0xFFFF) exactly as before; 0..16 is also two
    bytes; 17..56 is ceil(n/8) bytes, LSB first (BleUtils.X). ValueError above 56 segments or for a
    seg_mask wider than its width."""
    body = bytearray([0x05, 0x15, 0x01, r, g, b])
    if variant == 12:
        body += bytes(5)
    elif variant == 11:
        body += bytes([(kelvin >> 8) & 0xFF, kelvin & 0xFF, *tint])
    if segments is None:
        body += bytes([seg_mask & 0xFF, (seg_mask >> 8) & 0xFF])
    else:
        body += op15_mask_from_int(seg_mask, segments)
    return append_bcc(bytes([0x33]) + body)


# ─────────────────────── device profiles (devices.yaml -> {sku: client_profile}) ───────────────────────


def load_profiles(devices_yaml_path: str) -> dict[str, dict[str, Any]]:
    import yaml
    with open(devices_yaml_path, encoding="utf-8") as fh:
        reg = yaml.safe_load(fh)
    out: dict[str, dict[str, Any]] = {}
    for fam in reg.get("families", []):
        prof = fam.get("client_profile")
        if not prof:
            continue
        for sku in fam.get("models", []) or []:
            out[sku] = prof
    return out


# ─────────────────────────────────────── self-test / harness ───────────────────────────────────────


def _selftest(*, strict: bool = False) -> int:
    ok = True

    def check(name: str, cond: object) -> None:
        nonlocal ok
        print(f"  {'✅' if cond else '❌'} {name}")
        ok = ok and bool(cond)

    print("framing:")
    fr = append_bcc(bytes([0xAA, 0x04, 0x64]))
    check("bcc round-trips (append/verify)", verify_bcc(fr) and len(fr) == 20)
    check("rc4 vector (Key/Plaintext)", _rc4(b"Plaintext", b"Key").hex() == "bbf316e8d940af0ad3")

    tlv = bytes([0x01, 0x01, 0x01, 0x04, 0x01, 0x64,
                 0xA5, 0x0D, 0x01, 100, 255, 0, 0, 80, 0, 255, 0, 60, 0, 0, 255])
    check("0xAC reassemble(chunk(x)) == x", reassemble_ac(chunk_ac(tlv)) == tlv)
    check("0xAC single-packet reassemble(chunk(x)) == x", reassemble_ac(chunk_ac(tlv[:6])) == tlv[:6])
    try:
        append_bcc(bytes(20))
        check("append_bcc refuses a 20-byte payload", False)
    except ValueError:
        check("append_bcc refuses a 20-byte payload", True)

    print("op15 masks:")
    check("BleUtils.X: 33 positions -> ff ff ff ff 01", bleutils_x([True] * 33).hex() == "ffffffff01")
    check("BleUtils.X: bit k of byte j = position 8j+k",
          bleutils_x([True] + [False] * 7 + [True]) == b"\x01\x01")
    check("makeSelectedTwoBytes: 3 -> 07 00, 17 -> 00 00",
          make_selected_two_bytes([True] * 3) == b"\x07\x00"
          and make_selected_two_bytes([True] * 17) == bytes(2))
    check("op15_mask_width 16/17/33/48/49/56 -> 2/3/5/6/7/7",
          [op15_mask_width(n) for n in (16, 17, 33, 48, 49, 56)] == [2, 3, 5, 6, 7, 7])
    check("build_op15_color default mask unchanged (2 bytes)",
          build_op15_color(1, 2, 3, 0x1FFF, variant=12).hex() == "330515010102030000000000ff1f0000000000c2")
    check("build_op15_color H6062 33 segments -> 5-byte mask",
          build_op15_color(0xFF, 0, 0, (1 << 33) - 1, variant=12, segments=33).hex()
          == "33051501ff00000000000000ffffffff010000dc")

    try:
        _gbf()
    except ImportError as e:
        print(f"  ⚠️  {e}. Skipping parser tests.")
        if strict:
            print("  ❌ --strict: parser tests skipped")
            return 1
        return 0 if ok else 1

    print("parse dispatch:")
    f = parse_frame(fr)
    check("read reply AA 04 -> brightness 100", f.body.body.brightness == 100)
    st = parse_status(tlv)
    reals = [t for t in st.tlvs if t.type != 0]
    check("0xAC status: 3 TLVs (switch/brightness/colour-group)", len(reals) == 3)
    check("0xAC status: colour group 3x4B", reals[2].type == 0xA5 and len(reals[2].value.records) == 3)
    check("0xAC status tolerates trailing padding",
          len([t for t in parse_status(tlv + bytes(7)).tlvs if t.type != 0]) == 3)

    g4 = parse_color_group(bytes([3, 100, 255, 0, 0, 80, 0, 255, 0]), has_brightness=True, record_count=2)
    check("color_group has_brightness=1 -> 4B", g4.records[0].brightness == 100 and g4.records[1].b == 0)
    g3 = parse_color_group(bytes([3, 255, 0, 0, 0, 255, 0]), has_brightness=False, record_count=2)
    check("color_group has_brightness=0 -> 3B", g3.records[0].r == 255 and g3.records[1].g == 255)

    wr = parse_frame(build_op15_color(0x11, 0x22, 0x33, 0x0003, variant=11, kelvin=2700, tint=(100, 50, 16)))
    ext = wr.body.params.params.data  # single_command -> mode_payload -> color_15 -> op15_color (frame path)
    check("build_op15_color(CCT) round-trips r/g/b", ext.r == 0x11 and ext.g == 0x22 and ext.b == 0x33)

    # govee_commands.py — the full command-builder catalogue (0x33 writes / 0xAA reads). Each builder
    # carries a self-check (byte-exact vs the app layout it was traced from); run them all here so the
    # repo's self-test gate covers the whole catalogue. See docs/OPCODE_COVERAGE.md.
    try:
        import govee_commands  # noqa: F401
        from govee_commands import _SELF_CHECKS  # list[(name, bool)]
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  govee_commands not importable ({e}); skipping command-catalogue checks.")
    else:
        bad = [n for n, good in _SELF_CHECKS if not good]
        check(f"govee_commands: {len(_SELF_CHECKS)} builder self-checks pass", not bad)
        for n in bad[:10]:
            print(f"      ✗ {n}")

    return 0 if ok else 1


if __name__ == "__main__":
    import argparse
    import pathlib
    import sys

    # From a checkout, fall back to the library's committed reader (a fresh ksc build on PYTHONPATH,
    # or an installed govee_ble_local, still comes first / works as before).
    sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent / "src"))
    _ap = argparse.ArgumentParser(description="Reference-codec self-test.")
    _ap.add_argument("--strict", action="store_true", help="fail (exit 1) if the parser tests are skipped")
    sys.exit(_selftest(strict=_ap.parse_args().strict))
