#!/usr/bin/env python3
"""
Govee BLE — reference codec (the "framing" layer Kaitai cannot express) + dispatch wiring.

This is the executable half of the spec. `govee_ble.ksy` owns the *byte structure*; this module owns the
three things a single-buffer grammar structurally cannot do, each grounded in the decompiled app:

    (1) DECRYPT      V1 = AES-128-ECB(16-byte blocks) + RC4(remainder)   [com/govee/encryp/ble/Safe.java]
    (2) REASSEMBLE   multi-frame transfers (0xAC status; 0xA1/0xA3 similar)   [GOVEE_BLE_GATT_PROTOCOL.md 4.5/4.3]
    (3) XOR checksum (BCC) compute/verify                                [4.1]

...plus thin `dispatch` helpers that feed the parametric .ksy types the one discriminator they need
(`devices.yaml -> client_profile`). A real client is then: BLE-transport + this module + the generated
parser + devices.yaml. Nothing else hand-parses bytes.

Run (self-test / round-trip harness):
    kaitai-struct-compiler -t python govee_ble.ksy      # -> govee_ble_frame.py (put on sys.path)
    python govee_reference.py

Only the crypto needs a third-party AES (pip install pycryptodome); everything else is stdlib + the Kaitai
runtime (pip install kaitaistruct). RC4 is implemented here (Safe.f/g); AES-ECB is delegated.
"""
from __future__ import annotations
import functools
from io import BytesIO

# ─────────────────────────── (3) XOR checksum (BCC) — §4.1 ───────────────────────────

def bcc(frame: bytes) -> int:
    """XOR of bytes 0..18 (BleUtils.o :1016). == frame[19] for a valid 20-byte frame."""
    x = 0
    for b in frame[:19]:
        x ^= b
    return x

def append_bcc(payload: bytes) -> bytes:
    """[proType, cmd, payload...] (<=19 bytes) -> a zero-padded 20-byte frame with the checksum at byte 19."""
    b = bytearray(20)
    b[: len(payload)] = payload
    b[19] = bcc(b)
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
    from Crypto.Cipher import AES  # pip install pycryptodome
    aes = AES.new(key, AES.MODE_ECB)
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
# first frame (tag 0x00): [AC, 00, totalLo, totalHi, lastLen, cmd, sub, <12 data @7..18>, CK]
# next  frames (tag 01..): [AC, tag, <17 data @2..18>, CK]
# terminator  (tag 0xFF): signals completion. Reassembled buffer = concat(data regions)[:total] (the TLV stream).

def reassemble_ac(frames: list[bytes]) -> bytes:
    f0 = frames[0]
    total = f0[2] | (f0[3] << 8)
    data = bytearray(f0[7:19])
    for f in frames[1:]:
        if f[1] == 0xFF:
            break
        data += f[2:19]
    return bytes(data[:total])

def chunk_ac(tlv: bytes, cmd: int = 0x03, sub: int = 0x02) -> list[bytes]:
    """Inverse of reassemble_ac (for round-trip testing / building a request-response fixture)."""
    total = len(tlv)
    first, rest = tlv[:12], tlv[12:]
    last_len = (len(rest) % 17) or (17 if rest else len(first))
    frames = [append_bcc(bytes([0xAC, 0x00, total & 0xFF, (total >> 8) & 0xFF, last_len, cmd, sub]) + first)]
    tag, i = 1, 0
    while i < len(rest):
        frames.append(append_bcc(bytes([0xAC, tag]) + rest[i : i + 17]))
        i += 17
        tag += 1
    frames.append(append_bcc(bytes([0xAC, 0xFF])))  # terminator
    return frames

# ─────────────────────────── dispatch (wire the generated .ksy) ───────────────────────────
# Import the ksc-generated parser. Kept lazy so the framing helpers above work even before you compile it.

def _gbf():
    import govee_ble_frame  # generated: `ksc -t python govee_ble.ksy`
    return govee_ble_frame.GoveeBleFrame

def _stream(buf: bytes):
    from kaitaistruct import KaitaiStream
    return KaitaiStream(BytesIO(buf))

def parse_frame(frame20: bytes):
    """Verify BCC, then parse one plaintext, single 20-byte frame (proType-switched)."""
    if not verify_bcc(frame20):
        raise ValueError("bad BCC")
    return _gbf().from_bytes(frame20)

def parse_status(tlv_buffer: bytes):
    """A reassembled 0xAC buffer -> StatusReply (terminates on the 0x00 padding sentinel)."""
    return _gbf().StatusReply(_stream(tlv_buffer))

def parse_color_group(group_bytes: bytes, *, has_brightness: bool, record_count: int):
    """One 0xA5 colour group; `has_brightness` (from client_profile) selects 4-byte vs 3-byte records."""
    return _gbf().ColorGroupRead(record_count, 1 if has_brightness else 0, _stream(group_bytes))

def build_op15_color(r: int, g: int, b: int, seg_mask: int, *, variant: int, kelvin: int = 0, tint=(0, 0, 0)):
    """Build a 0x33 05 15 01 colour-write frame using the parametric op15_color_typed layout.
    variant: 1 basic, 12 H60A1/H60A6 RGB, 11 CCT (from client_profile.color_scheme + the op)."""
    body = bytearray([0x05, 0x15, 0x01, r, g, b])
    if variant == 12:
        body += bytes(5)
    elif variant == 11:
        body += bytes([(kelvin >> 8) & 0xFF, kelvin & 0xFF, *tint])
    body += bytes([seg_mask & 0xFF, (seg_mask >> 8) & 0xFF])
    return append_bcc(bytes([0x33]) + body)

# ─────────────────────────── device profiles (devices.yaml -> {sku: client_profile}) ───────────────────────────

def load_profiles(devices_yaml_path: str) -> dict[str, dict]:
    import yaml
    reg = yaml.safe_load(open(devices_yaml_path))
    out: dict[str, dict] = {}
    for fam in reg.get("families", []):
        prof = fam.get("client_profile")
        if not prof:
            continue
        for sku in fam.get("models", []) or []:
            out[sku] = prof
    return out

# ─────────────────────────────────────── self-test / harness ───────────────────────────────────────

def _selftest() -> int:
    ok = True

    def check(name, cond):
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

    try:
        _gbf()
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  govee_ble_frame not importable ({e}); run ksc first. Skipping parser tests.")
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

    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
