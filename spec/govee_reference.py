#!/usr/bin/env python3
"""
Govee BLE — reference codec (the "framing" layer Kaitai cannot express) + dispatch wiring.

This is the executable half of the spec. The `.ksy` files own the *byte structure*; this module owns the
three things a single-buffer grammar structurally cannot do, each grounded in the decompiled app:

    (1) DECRYPT      V1 = AES-128-ECB(16-byte blocks) + RC4(remainder)   [com/govee/encryp/ble/Safe.java]
    (2) REASSEMBLE   multi-frame transfers (0xAC status; 0xA1/0xA3 alike) [GOVEE_BLE_GATT_PROTOCOL.md 4.5/4.3]
    (3) checksum     XOR (BCC) or sum8 compute/verify, per the frame's checksum_kind   [4.1]

...plus thin `dispatch` helpers that give the spec's roots their context: the direction and a
`govee_profile` (make_profile; the library builds its profiles from `devices.yaml -> client_profile`).
A real client is then: BLE-transport + this module + the generated parser + devices.yaml. Nothing else
hand-parses bytes.

Run (self-test / round-trip harness), from spec/:
    kaitai-struct-compiler -t python --outdir OUT *.ksy   # one module per .ksy: govee_ble_frame.py, ...
    PYTHONPATH=OUT python govee_reference.py [--strict]

Without a fresh `ksc` build on sys.path the parser checks use the library's generated classes
(`../src/govee_ble_local/_generated`: run `tools/gen_kaitai.sh`, or `pip install -e .`). They are skipped
(exit 0) only when neither can be imported, and `--strict` turns that skip into a failure.

Only the crypto needs a third-party AES (pip install pycryptodome); everything else is stdlib + the Kaitai
runtime (pip install kaitaistruct). RC4 is implemented here (Safe.f/g); AES-ECB is delegated.
"""
from __future__ import annotations

import importlib
import pathlib
from collections.abc import Sequence
from io import BytesIO
from typing import Any, Final, cast


# ─────────────────────────── (3) checksum (XOR BCC, sum8) — §4.1 ───────────────────────────


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


def checksum_ok(frame: bytes, kind: str) -> bool:
    """The frame's last byte against the rule its route uses (govee_ble_frame.checksum_kind, by name):
    "xor" = XOR of every byte before it (the 20-byte BCC, and the MTU-length 0xA4 / 0xA6 packets),
    "sum8" = their 8-bit sum (0xA5 mic, 0xB0..0xBF), "none" = no checksum byte (0x05 gid info)."""
    if kind == "none":
        return True
    if len(frame) < 2:
        return False
    if kind == "sum8":
        return sum(frame[:-1]) & 0xFF == frame[-1]
    x = 0
    for b in frame[:-1]:
        x ^= b
    return x == frame[-1]


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
#                         BleUtils.H with its flag false); lastLen = endPkgLen = valid data bytes in the
#                         last packet.
# next  frames (tag = position 1..pkts-2): [AC, tag, <17 data @2..18>, CK]                         (.i)
# last  frame (tag 0xFF, position pkts-1): [AC, FF, <lastLen data @2..>, zero pad, CK]            (.h)
# pkts == 1: the first frame is the whole reply and holds lastLen (<= 12) data bytes @7 (.g).
# isSameController() matches frames by position: a 0xFF tag only at position pkts-1, which also takes
# its own position as the tag (K of the tag equals backPos); onResult() runs .h() at pkts-1 for either tag.
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
    # isSameController takes 0xFF here (backPos is allBackPkgs-1, :237-239) or the position itself
    # (K of the tag equals backPos, :248); onResult then runs .h() because backPos is allBackPkgs-1
    # (:326-331).
    if last[1] not in (_AC_LAST_TAG, count - 1):
        raise ValueError(f"0xAC last packet carries tag {last[1]:#04x}, not 0xFF or {count - 1:#04x}")
    data += last[2 : 2 + last_len]
    return bytes(data)


def chunk_ac(tlv: bytes, cmd: int = 0x03, sub: int = 0x00) -> list[bytes]:
    """Inverse of reassemble_ac (for round-trip testing / building a request-response fixture).

    ``sub`` (byte 6) must equal the reader's d() (AbsController4BleSingleSendMultiBack.f): 0 for the
    H60A6 status read (Compose4BaseInfoSingleRead builds a ControllerSingleRead with d() left at 0),
    which is what the H60A6 sends; 0x0B under ``cmd`` 0xA1 for the special-info read's segment IC record
    (ControllerReadSpecialInfoMultiBack.Companion.k, govee_ble_frame status_sub segment_ic)."""
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
# ksc writes one module per .ksy file (govee_ble_frame, govee_status_stream, govee_state, govee_profile,
# govee_common, ...). The six roots: govee_ble_frame(direction, profile) for one GATT value,
# govee_status_stream(profile, reader) for a joined 0xAC burst, govee_upload(kind, form) for a joined
# upload value or a cloud blob, govee_manufacturer_data / govee_scan_record for adverts, govee_bgc_info for
# GATT 2b12.

_READER_MODULES = (
    "govee_ble_frame",                              # a fresh `ksc -t python *.ksy` on sys.path
    "govee_ble_local._generated.govee_ble_frame",   # the library's generated classes (tools/gen_kaitai.sh)
)


def _frame_module() -> Any:
    """The generated govee_ble_frame module. A fresh ksc build on sys.path wins, so the self-test checks
    the .ksy being edited; otherwise the library's generated classes are used
    (src/govee_ble_local/_generated: run tools/gen_kaitai.sh, or pip install -e .)."""
    errors: list[str] = []
    for name in _READER_MODULES:
        try:
            return importlib.import_module(name)
        except ImportError as err:
            errors.append(f"{name}: {err}")
    raise ImportError(f"no generated frame reader importable ({'; '.join(errors)})")


def _module(name: str) -> Any:
    """The generated module ``name`` (e.g. "govee_status_stream") from the same build as the frame
    reader, so one profile object serves every root."""
    package = _frame_module().__name__.rpartition(".")[0]
    return importlib.import_module(f"{package}.{name}" if package else name)


def _gbf() -> Any:
    """The generated GoveeBleFrame class (untyped, so Any)."""
    return _frame_module().GoveeBleFrame


def _stream(buf: bytes) -> Any:
    from kaitaistruct import KaitaiStream
    return KaitaiStream(BytesIO(buf))


_FORMS: Final = ("f_01", "f_02", "f_09", "f_12", "f_13", "f_14", "f_30", "f_34", "f_36", "f_40", "f_41",
                 "f_a1", "f_a2", "f_a3", "f_a5", "f_b0", "f_0b", "f_15_01", "f_15_04", "f_15_05", "f_ee_30",
                 "f_ee_40")
# govee_profile struct -> its params, each with the govee_common enum it takes (None: a plain number)
_LEAVES: Final[dict[str, dict[str, str | None]]] = {
    "CommandForms": {f: "Form" + "".join(p.capitalize() for p in f[2:].split("_")) for f in _FORMS},
    "DeviceCounts": {"num_segments": None, "num_levels": None},
    "ColorGroupForm": {"rule": "GroupCountRule", "num_per_group": None, "has_brightness": None,
                       "num_pieces": None},
    "PowerRule": {"relay_on_mask": None, "relay_heartbeat_on_mask": None, "light_nonzero": None,
                  "light_heartbeat_nonzero": None, "advert_on_mask": None},
    "ModeDecode": {"c02": "ColorDecode", "c0b": "ColorDecode", "c0d": "ColorDecode", "c14": "ColorDecode",
                   "c15": "ColorDecode", "music": "MusicDecode", "empty_fails": None},
}
_FLAGS: Final = ("has_brightness", "empty_fails", "light_nonzero",
                 "light_heartbeat_nonzero")   # the bool params: false when left out


def make_profile(**facts: Any) -> Any:
    """A govee_profile from flat keyword facts. Every fact left out is 0 (unknown / not_decoded / false),
    so make_profile() is the all-zero analyzer profile (GENERIC): overloaded bodies stay raw bytes.

    Keys: the command_forms selectors f_01 .. f_ee_40; num_segments, num_levels; rule, num_per_group,
    has_brightness, num_pieces; relay_on_mask, relay_heartbeat_on_mask, light_nonzero,
    light_heartbeat_nonzero, advert_on_mask; and the mode decode
    c02, c0b, c0d, c14, c15, music, empty_fails (one mode_decode object serves both mode and mode_status). An
    enum-typed fact may be given by its member name, e.g. make_profile(f_15_01="kelvin_tint").
    Generated parents check a profile param by identity: build one object per set of device facts and
    reuse it (the library's wire_profile() does this from devices.yaml)."""
    profile_cls = _module("govee_profile").GoveeProfile
    common = _module("govee_common").GoveeCommon
    unknown = set(facts) - {k for params in _LEAVES.values() for k in params}
    if unknown:
        raise TypeError(f"unknown profile facts: {', '.join(sorted(unknown))}")
    leaves: dict[str, Any] = {}
    for leaf, params in _LEAVES.items():
        kw: dict[str, Any] = {}
        for key, enum_name in params.items():
            value = facts.get(key, False if key in _FLAGS else 0)
            if isinstance(value, str):
                if enum_name is None:
                    raise TypeError(f"{key} takes a number, not {value!r}")
                value = getattr(common, enum_name)[value]
            kw[key] = value
        leaves[leaf] = getattr(profile_cls, leaf)(**kw, _io=None)
    mode = leaves["ModeDecode"]
    return profile_cls(forms=leaves["CommandForms"], counts=leaves["DeviceCounts"],
                       group=leaves["ColorGroupForm"], power=leaves["PowerRule"], mode=mode,
                       mode_status=mode, _io=None)


def _ready(obj: Any) -> Any:
    """``obj`` parsed, whichever reader build produced it. A read-only build (``ksc -t python``) reads
    in ``__init__``; a read-write build (``tools/gen_kaitai.sh --read-write``, kaitaistruct 0.11
    ``ReadWriteKaitaiStruct``) reads only on ``_read()``, and a second ``_read()`` on a read-only object
    would fail, so read exactly when the object is a read-write one."""
    from kaitaistruct import ReadWriteKaitaiStruct
    if isinstance(obj, ReadWriteKaitaiStruct):
        cast(Any, obj)._read()
    return obj


def parse_frame(frame: bytes, direction: str = "rx", profile: Any = None) -> Any:
    """One plaintext frame (a GATT 2b11 write or 2b10 notification value, any length) -> GoveeBleFrame,
    read with ``direction`` ("tx" client to device, "rx" device to client) and ``profile``
    (make_profile(); None = the analyzer profile). The body is ``route``-switched (direction + proType);
    ValueError when the trailer fails the frame's own checksum_kind (checksum_ok)."""
    common = _module("govee_common").GoveeCommon
    f = _ready(_gbf()(common.Direction[direction], make_profile() if profile is None else profile,
                      _stream(frame)))
    if not checksum_ok(frame, f.checksum_kind.name):
        raise ValueError("bad checksum")
    return f


def parse_status(tlv_buffer: bytes, profile: Any = None, reader: int = 0x03) -> Any:
    """A joined 0xAC buffer -> GoveeStatusStream, read by ``reader`` (the request's status_command): for the
    status read (0x03) ``tlvs`` (status_tlv: tag, len_value, value = govee_state) while 3 or more bytes
    remain, then the 0-2 byte ``tail``, as the app's walk (tag 0 is an ordinary TLV); for the special-info
    read (0xA1) its ``segment_ic`` record. ``profile`` as for parse_frame."""
    stream_cls = _module("govee_status_stream").GoveeStatusStream
    return _ready(stream_cls(make_profile() if profile is None else profile, reader, _stream(tlv_buffer)))


# ───────────────────────── op15 segment masks (BleUtils.X / makeSelectedTwoBytes) ─────────────────────────
# Every 0x15 colour/brightness builder picks its mask the same way: BleUtils.X when posSet has more than
# 16 entries, else makeSelectedTwoBytes (color/Bytes.java h :323, r :498, G :178, b :229, d :278, l :387,
# n :417, u :553; SubModeColorV1.getWriteBytes :672 keys it on its segment count). posSet has one entry
# per device segment, so the width follows the segment count, not the selection.
OP15_LEGACY_MASK_SEGMENTS: Final = 16   # makeSelectedTwoBytes: always 2 bytes, 00 00 if empty or > 16
OP15_MAX_MASK_BYTES: Final = 7          # Bytes.h/r keep coerceAtMost(len, 17 - 10); Bytes.C truncates > 7


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


# ────────── device profiles (devices.yaml -> {cloud goodsType | goodsType-0 SKU: client_profile}) ──────────


def load_profiles(devices_yaml_path: str) -> dict[int | str, dict[str, Any]]:
    """{routing key: family client_profile}, keyed the way the app picks a device-list record's family
    (devices.yaml, colour-decode header, "device list"; SubConfig.Companion.b / c): by the record's cloud
    goodsType when it is non-zero, whatever the SKU, and by its SKU only when the goodsType is 0.

    A goodsType (int) keys the family whose goods_types holds it. A SKU (str) keys only a family a
    goodsType-0 record reaches: one with no goods_types, or one listing 0 (the models of its
    by_goods_type."0" entry where it has one, else all its models). So a SKU that several families list
    resolves per goodsType instead of to whichever family was read last (H6102: 18 -> dreamcolorlightv1,
    21 -> dreamcolorlightv2, 0 -> h6102). Families without a client_profile are not indexed. Two families
    claiming one key raise ValueError. Look a record up with profile_for(); inside the family,
    assigned_goods_type and by_goods_type / by_model still pick the branch."""
    import yaml
    with open(devices_yaml_path, encoding="utf-8") as fh:
        reg = yaml.safe_load(fh)
    out: dict[int | str, dict[str, Any]] = {}
    owner: dict[int | str, str] = {}
    for fam in reg.get("families", []):
        prof = fam.get("client_profile")
        if not prof:
            continue
        goods_types = fam.get("goods_types") or []
        keys: list[int | str] = [gt for gt in goods_types if gt]
        if not goods_types or 0 in goods_types:
            at_zero = (prof.get("by_goods_type") or {}).get("0") or {}
            keys += at_zero.get("models") or fam.get("models") or []
        for key in keys:
            if key in owner:
                raise ValueError(f"{key!r} routes to both {owner[key]} and {fam['id']}")
            owner[key] = fam["id"]
            out[key] = prof
    return out


def profile_for(profiles: dict[int | str, dict[str, Any]], sku: str,
                goods_type: int) -> dict[str, Any] | None:
    """The family client_profile for one device-list record (its SKU and cloud goodsType), or None where no
    family takes it (the app shows its unsupported card, DefNoSupportLib)."""
    return profiles.get(goods_type if goods_type else sku)


def ble_rows(reg: dict[str, Any], sku: str, pact: tuple[int, int] | None = None) -> list[tuple[str, int]]:
    """PROCEDURAL (BLE routing, devices.schema.json clientRow): the (family id, goodsType) rows a BLE client
    that knows only the SKU and the advertised pact may route `sku` to. A row qualifies when it has support,
    its models list the SKU, its connectivity (else the family's) holds ble, and its pacts are 'any' or hold
    the pact (any listed pact when no pact is advertised, but on the goodsType a pact-conditioned
    assigned_goods_type item rewrites the SKU to: the app rewrites only a record whose pact meets the gate;
    and on no row of a SKU a bind item names: its write's pact runs other facts than the row's, so a client
    without the pact can't tell them apart; and on a pact_required row, whose goodsType the app also runs at
    other pacts on a path the row doesn't serve); a row without pacts is never a route. Exactly one row is a
    route; none or several leave the SKU unrouted unless the caller knows the goodsType."""
    gated = {(model, item.get("goods_type")) for fam in reg.get("families", [])
             for item in fam.get("assigned_goods_type") or []
             if set(item.get("condition") or {}) == {"min_pact_type", "min_pact_code"}
             for model in item.get("models") or []}
    paired = {model for fam in reg.get("families", []) for item in fam.get("assigned_goods_type") or []
              if item.get("context") == "bind" for model in item.get("models") or []}
    out: list[tuple[str, int]] = []
    for fam in reg.get("families", []):
        for gt, row in ((fam.get("client_profile") or {}).get("by_goods_type") or {}).items():
            pacts = row.get("pacts")
            if ("support" not in row or sku not in (row.get("models") or []) or pacts is None
                    or "ble" not in (row.get("connectivity") or fam.get("connectivity") or [])):
                continue
            if pact is None and ((sku, int(gt)) in gated or sku in paired or row.get("pact_required")):
                continue
            if pacts == "any" or pact is None or f"{pact[0]},{pact[1]}" in pacts:
                out.append((fam["id"], int(gt)))
    return out


def _version(text: str | None) -> tuple[int, ...] | None:
    """modeColorVersion 'a.bb.cc' as a tuple compared field by field; None for an empty or unparsable one."""
    try:
        return tuple(int(f) for f in str(text).split(".")) if text else None
    except ValueError:
        return None


def client_row(reg: dict[str, Any], family_id: str, goods_type: int, *, sku: str | None = None,
               pact: tuple[int, int] | None = None, soft: str | None = None, hard: str | None = None,
               wifi_soft: str | None = None) -> dict[str, Any]:
    """PROCEDURAL (devices.schema.json clientRow / clientModel / clientBranch / clientFirmware): one row's
    facts for a device. The row's own values, then its by_pact branch for the pact (none when the pact is
    unknown), then every by_firmware item whose min_* all hold, in order (an unknown version fails its
    test); a layered fact the row lacks (scene_versions, kelvin_range, brightness_range) comes from the
    family specs. With the device's ``sku``, a range the row lacks (kelvin_range, brightness_range) comes
    from the SKU's client_profile.by_model entry before the family specs, and that entry's by_firmware
    items carry it too, ahead of the row's items. The row's own range wins over the model's and its
    branches (the library refuses a by_model range under it). The SKU's connect_reads, connect_writes,
    ic_pieces and support replace the row's (the screen's split by SKU within one goodsType). Only those:
    the by_model colour report is not resolved here. Versions compare field by field, but a kelvin_range
    item's gate is a string
    compare, as the app's (KelvinConfig.Config.z2: versionHard.compareTo("3.08.01") >= 0).

    Known difference: where several kelvin_range items hold, the last in list order wins here, while the
    library takes the one with the highest version (``_fold.kelvin_range``); the two agree when the items
    are listed by ascending version."""
    fam = next(f for f in reg["families"] if f["id"] == family_id)
    profile = fam.get("client_profile") or {}
    row = profile.get("by_goods_type", {})[str(goods_type)]
    model = ((profile.get("by_model") or {}).get(sku) if sku else None) or {}
    ranges = {k for k in ("kelvin_range", "brightness_range") if k not in row}   # the model's to give
    out = {k: v for k, v in row.items() if k not in ("by_pact", "by_firmware", "doc", "doc-ref")}
    out.update({k: model[k] for k in ranges if k in model})
    out.update({k: model[k] for k in ("connect_reads", "connect_writes", "ic_pieces", "support")
                if k in model})
    for k in ("scene_versions", "kelvin_range", "brightness_range"):
        if k not in out and k in (fam.get("specs") or {}):
            out[k] = fam["specs"][k]
    if pact is not None:
        out.update({k: v for k, v in ((row.get("by_pact") or {}).get(f"{pact[0]},{pact[1]}") or {}).items()
                    if k != "doc"})
    have = {"min_soft_version": soft, "min_hard_version": hard, "min_wifi_soft_version": wifi_soft}

    def holds(gate: str, dev: str | None, as_string: bool) -> bool:
        if as_string:
            return bool(dev) and str(dev) >= gate
        need, got = _version(gate), _version(dev)
        return need is not None and got is not None and got >= need

    items = [*({k: v for k, v in item.items() if k.startswith("min_") or k in ranges}
               for item in model.get("by_firmware") or []), *(row.get("by_firmware") or [])]
    for item in items:
        if all(holds(v, have[k], "kelvin_range" in item) for k, v in item.items() if k.startswith("min_")):
            out.update({k: v for k, v in item.items() if not k.startswith("min_") and k != "doc"})
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

    print("device profiles:")
    try:
        import yaml
    except ImportError:
        print("  ⚠️  PyYAML not importable; the devices.yaml routing checks did not run.")
    else:
        path = pathlib.Path(__file__).with_name("devices.yaml")
        fams = {f["id"]: f.get("client_profile")
                for f in yaml.safe_load(path.read_text(encoding="utf-8"))["families"]}
        profs = load_profiles(str(path))
        check("H6102 routes by goodsType: 18 -> dreamcolorlightv1, 21 -> dreamcolorlightv2, 0 -> h6102",
              len({str(fams[i]) for i in ("dreamcolorlightv1", "dreamcolorlightv2", "h6102")}) == 3
              and profile_for(profs, "H6102", 18) == fams["dreamcolorlightv1"]
              and profile_for(profs, "H6102", 21) == fams["dreamcolorlightv2"]
              and profile_for(profs, "H6102", 0) == fams["h6102"])
        check("a SKU is a key only at goodsType 0 (H6001 -> bulblightv3; H6006 is goodsType-keyed)",
              profile_for(profs, "H6001", 0) == fams["bulblightv3"] and "H6006" not in profs
              and profile_for(profs, "H6006", 64) == fams["bulblightv3"])
        reg = yaml.safe_load(path.read_text(encoding="utf-8"))
        check("BLE routes: H6004 -> bulblightv3 64 (10 is IoT-only); H60A6 at (1,3) -> 303, at (2,1) none; "
              "H6114 -> h6114 39 at (10,1), none without a pact (the BK rewrite's gate); H615E -> "
              "stringlightv2 108 at (2,1), none at its pairing write's (65535,255) or without a pact; "
              "H6076 -> straightfloorlamp 69 at (2,1), none without a pact (pact_required)",
              ble_rows(reg, "H6004") == [("bulblightv3", 64)]
              and ble_rows(reg, "H60A6", (1, 3)) == [("h60ax", 303)] and ble_rows(reg, "H60A6", (2, 1)) == []
              and ble_rows(reg, "H6114", (10, 1)) == [("h6114", 39)] and ble_rows(reg, "H6114") == []
              and ble_rows(reg, "H615E", (2, 1)) == [("stringlightv2", 108)]
              and ble_rows(reg, "H615E", (65535, 255)) == [] and ble_rows(reg, "H615E") == []
              and ble_rows(reg, "H6076", (2, 1)) == [("straightfloorlamp", 69)]
              and ble_rows(reg, "H6076") == [])
        check("device-list card rows: H70B2 -> h70b2 185 at (1,1) only; H6631 -> h6630 286 at (2,4), where "
              "H6630 has no row; each a per_command session with no keep-alive",
              ble_rows(reg, "H70B2", (1, 1)) == [("h70b2", 185)] and ble_rows(reg, "H70B2", (1, 2)) == []
              and ble_rows(reg, "H6631", (2, 4)) == [("h6630", 286)] and ble_rows(reg, "H6630", (2, 4)) == []
              and all((r["session"], r["power_read"], r["heartbeat"]) == ("per_command", "advert", "none")
                      for r in (client_row(reg, "h70b2", 185), client_row(reg, "h6630", 285),
                                client_row(reg, "h6630", 286))))
        h61a8 = [client_row(reg, "dreamcolorlightv1", 19, pact=pact, soft=s, hard=h)
                 for pact, s, h in (((1, 1), "3.03.18", "3.01.10"), ((1, 1), "1.03.00", "3.08.01"),
                                    ((1, 2), "1.05.00", "1.00.01"))]
        h6168 = [client_row(reg, "dreamcolorlightv1", 136, soft=s, hard=h)
                 for s, h in (("1.06.00", "1.00.02"), ("1.06.00", "1.00.01"), ("1.05.00", "3.01.10"))]
        check("firmware branches: H61A8 {0,1,2,3} at every pact and firmware, HW 3.08.01 -> [2700, 6500], "
              "HW 10.00.00 below it (a string compare); H6168 {0,1,2,3} from soft 1.06.00 and hard 1.00.02 "
              "(part brightness), else {0,1,2}",
              [(r["scene_versions"], r["kelvin_range"]) for r in h61a8]
              == [([0, 1, 2, 3], [2000, 9000]), ([0, 1, 2, 3], [2700, 6500]), ([0, 1, 2, 3], [2000, 9000])]
              and client_row(reg, "dreamcolorlightv1", 19, hard="10.00.00")["kelvin_range"] == [2000, 9000]
              and [r["scene_versions"] for r in h6168] == [[0, 1, 2, 3], [0, 1, 2], [0, 1, 2]])
        check("H60A6 pact (1,1): no 0xA5 in the status read; H7070 adds 0x42 at (1,2) / (2,1); with the pact "
              "unknown the row's own applies",
              client_row(reg, "h60ax", 303, pact=(1, 1))["status_read"] == ["cmd_41", "cmd_30"]
              and client_row(reg, "h60ax", 303)["status_read"] == ["cmd_41", "cmd_30", "color_group"]
              and [client_row(reg, "h6092", 283, pact=p)["status_read"]
                   for p in (None, (1, 1), (1, 2), (2, 1))]
              == [["cmd_41", "cmd_0c"]] * 2 + [["cmd_41", "cmd_0c", "write_check_ic"]] * 2)

    try:
        _gbf()
        for sibling in ("govee_common", "govee_profile", "govee_status_stream"):
            _module(sibling)
    except ImportError as e:
        print(f"  ⚠️  {e}. Skipping parser tests (generate the classes with tools/gen_kaitai.sh, or "
              "pip install -e .).")
        if strict:
            print("  ❌ --strict: parser tests skipped")
            return 1
        return 0 if ok else 1

    print("parse dispatch:")
    command = _module("govee_common").GoveeCommon.Command
    f = parse_frame(fr, "rx")  # read_reply -> govee_state -> level
    check("read reply AA 04 (rx) -> brightness 100", f.body.state.body.level == 100)
    check("read request AA 04 (tx) -> read_request brightness",
          parse_frame(fr, "tx").body.command == command.brightness)

    def light_on(state: int, beat: bool = False, **power: Any) -> bool:
        reply = parse_frame(append_bcc(bytes([0xAA, 0x01, state])), "rx", make_profile(**power))
        return bool(reply.body.state.body.is_on_heartbeat if beat else reply.body.state.body.is_on)

    check("AA 01 reply: a light is on at 1 only, at any non-zero byte under light_nonzero (and in the "
          "heartbeat under light_heartbeat_nonzero too); a plug by mask",
          [light_on(s) for s in (0, 1, 2)] == [False, True, False]
          and [light_on(s, light_nonzero=True) for s in (0, 1, 2)] == [False, True, True]
          and [light_on(s, beat, light_heartbeat_nonzero=True) for beat in (False, True)
               for s in (0, 1, 2)] == [False, True, False, False, True, True]
          and [light_on(s, relay_on_mask=0x01) for s in (0x10, 0x11)] == [False, True])
    try:
        parse_frame(fr[:19] + bytes([fr[19] ^ 1]))
        check("parse_frame refuses a bad checksum", False)
    except ValueError:
        check("parse_frame refuses a bad checksum", True)
    check("checksum_kind sum8 (A5 mic) and none (05 gid info)",
          checksum_ok(bytes([0xA5, 0x02, 0x10, 0x01, 0xB8]), "sum8") and checksum_ok(bytes(20), "none"))

    status_profile = make_profile(f_a5="color_group", rule="from_len", has_brightness=True)
    st = parse_status(tlv, status_profile)
    reals = [t for t in st.tlvs if t.tag != 0]
    check("0xAC status: 3 TLVs (power/brightness/colour-group)", len(reals) == 3)
    check("0xAC status: colour group 3x4B",  # status_tlv.value = govee_state -> cmd_a5_state -> color_group
          reals[2].tag == command.color_group and len(reals[2].value.body.body.records) == 3)
    single = make_profile(f_a5="single_record", num_pieces=1)
    one, short = (parse_status(bytes([0xA5, len(v), *v]), single).tlvs[0].value.body.body
                  for v in ((0xFF, 0x40, 0x11, 0x22, 0x33), (0x00, 0x40, 0x11, 0x22)))
    two = parse_status(bytes([0xA5, 5, 0xFF, 0x40, 0x11, 0x22, 0x33]),
                       make_profile(f_a5="single_record", num_pieces=2)).tlvs[0].value.body.body
    check("0xAC status: single_record TLV 0xA5 (H703A) is one record at bytes 1-4 from a 5-byte value, "
          "byte 0 unread, applied at one piece only; a shorter one reads none",
          one.app_applies and (one.record.brightness, one.record.r, one.record.b) == (0x40, 0x11, 0x33)
          and two.is_read and not two.app_applies
          and not short.is_read and getattr(short, "record", None) is None)
    padded = parse_status(tlv + bytes(7), status_profile)
    check("0xAC status: trailing zero padding reads as type-0 TLVs and a 1-byte tail",
          len([t for t in padded.tlvs if t.tag != 0]) == 3 and padded.tail == b"\x00")
    segs = parse_status(bytes([0xA1, 0, 0xA1, 4, 3, 54, 54, 36]), make_profile(f_a1="segment_info")).tlvs
    check("0xAC status: TLV 0xA1 segment_info [count][IC per segment]; an empty one fails the app's read",
          segs[0].app_read_fails and not segs[1].app_read_fails
          and segs[1].value.body.body.segment_count == 3
          and list(segs[1].value.body.body.segment_ics) == [54, 54, 36])

    def group_reply(has_brightness: bool, records: Sequence[int]) -> Any:
        prof = make_profile(f_a5="color_group", rule="fixed", num_per_group=2, has_brightness=has_brightness)
        return parse_frame(append_bcc(bytes([0xAA, 0xA5, 3, *records])), "rx", prof).body.state.body.body

    g4 = group_reply(True, [100, 255, 0, 0, 80, 0, 255, 0])
    check("aa a5 reply, has_brightness -> 4B records",
          g4.group_index == 3 and g4.records[0].brightness == 100 and g4.records[1].b == 0)
    g3 = group_reply(False, [255, 0, 0, 0, 255, 0])
    check("aa a5 reply, no brightness -> 3B records", g3.records[0].r == 255 and g3.records[1].g == 255)

    def ic_reply(*data: int) -> Any:
        return parse_frame(append_bcc(bytes([0xAA, 0x40, *data])), "rx").body.state.body

    check("aa 40 reply: section_sum = bytes 2 + 5 as Java bytes, absent where ControllerIcNum.d throws",
          ic_reply(0x00, 0x1C, 0x0A, 0x00, 0x2A, 0x08).section_sum == 18
          and ic_reply(0x00, 0x1C, 0xFF, 0x00, 0x00, 0xFE).section_sum == -3
          and ic_reply(0x10, 0x00, 0x05).section_sum is None)
    ic40 = parse_status(bytes([0x40, 3, 0x9C, 0x40, 0x00]), make_profile(f_40="one_count_u16")).tlvs[0]
    check("TLV 0x40: bytes 0-1 signed in ic_count (getSignedShort), unsigned in ic_count_u16 "
          "(SegmentSettingController.b)",
          (ic40.value.body.ic_count, ic40.value.body.ic_count_u16) == (0x9C40 - 0x10000, 0x9C40)
          and not ic40.app_read_fails)
    upload = _module("govee_upload").GoveeUpload
    multi = _ready(upload(upload.UploadKind.color_multi, upload.UploadForm.wire,
                          _stream(bytes.fromhex("01ffffff0fa0ffd5a1ff7f")), None, None))
    acks = [parse_frame(raw, "rx").body for raw in (
        append_bcc(bytes([0xA3, 0x6E, 0x00, 0x01]) + bytes(15)), append_bcc(bytes([0xA3, 0x6E, 0x00, 0x00])),
        bytes([0xA3, 0x6E, 0x00, 0x00, 0xA3 ^ 0x6E]))]   # a refused byte 3, a whole ack, a short frame
    check("0xA3 colour value (Config.gu: Bytes.t over 15 pieces) and the unit ack: byte 3 of a whole 20-byte "
          "frame (MultiController, AbsControllerNoEvent4Multi.onResult)",
          (multi.value.kelvin, multi.value.tint.g, multi.value.mask.bits[14:17], multi.value.len_value)
          == (4000, 0xD5, [True, False], 11)
          and multi.value.is_cct and acks[0].multi_result == 1 and acks[0].is_ok
          and [a.is_multi_ok for a in acks] == [False, True, False])
    whole = make_profile(f_a5="color_group", rule="whole_groups", num_per_group=4, has_brightness=True,
                         num_pieces=10)
    placed = [parse_status(bytes([0xA5, 17, g, *([1] * 16)]), whole).tlvs[0].value.body.body
              for g in (2, 3, 4)]
    check("whole_groups (VM4LightH351011): only the groups whole within num_pieces are placed, the rest "
          "dropped without failing the read",
          [g.is_placed for g in placed] == [True, False, False] and not any(g.app_read_fails for g in placed))

    seg = parse_status(reassemble_ac(chunk_ac(bytes([12] + [5] * 12), 0xA1, 0x0B)), reader=0xA1).segment_ic
    check("ac a1 special-info reply (sub 0x0B): two packets join to [12][IC x 12] (the stream's segment_ic, "
          "no TLVs); an empty payload fails the app's read",
          seg.num_segments == 12 and seg.ic_per_segment == [5] * 12 and not seg.app_read_fails
          and parse_status(b"", reader=0xA1).segment_ic.app_read_fails)

    check("aa 0f reply: the string count is byte 0 read as a Java byte (Controller4BulbNum.Companion.d)",
          [parse_frame(append_bcc(bytes([0xAA, 0x0F, n])), "rx").body.state.body.count for n in (2, 0x80)]
          == [2, -128])

    def detail_reply(*data: int) -> Any:
        prof = make_profile(f_34="device_detail")
        return parse_frame(append_bcc(bytes([0xAA, 0x34, *data])), "rx", prof).body.state.body.body

    check("aa 34 reply (H6057 device detail): main switch on = light (byte 0 bit 7) or white noise playing "
          "(byte 2 bits 4 and 7)",
          [detail_reply(*b).main_switch_on for b in ((0x80,), (0x7F,), (0x00, 0, 0x90), (0x00, 0, 0x80))]
          == [True, False, True, False] and detail_reply(0x80).light_open
          and not detail_reply(0x00, 0, 0x90).light_open)

    from govee_commands import (  # the catalogue imports this module
        build_op15_color_temp_bytes_r,
        build_op15_color_v5,
    )

    wr = parse_frame(build_op15_color_temp_bytes_r((100, 50, 16), 2700, [True, True] + [False] * 14),
                     "tx", make_profile(f_15_01="kelvin_tint", num_segments=16))
    ext = wr.body.params.params.data  # write_command -> mode_write -> color_15_write -> op15_color
    check("build_op15_color_temp_bytes_r round-trips slot, kelvin and tint",
          (ext.color.r, ext.color.g, ext.color.b, ext.kelvin) == (0xFF, 0xFF, 0xFF, 2700)
          and (ext.tint.r, ext.tint.g, ext.tint.b) == (100, 50, 16) and ext.is_cct)
    v5 = parse_frame(build_op15_color_v5(0x12, 0x34, 0x56, (1 << 23) - 1, mask_bytes=3), "tx",
                     make_profile(f_15_01="kelvin", num_segments=23)).body.params.params.data
    check("build_op15_color_v5 RGB pick (Bytes.l, H6061) reads as RGB, kelvin 0, over 23 panels",
          (v5.color.r, v5.color.g, v5.color.b, v5.kelvin) == (0x12, 0x34, 0x56, 0) and not v5.is_cct
          and v5.mask.bits[:24] == [True] * 23 + [False])

    from govee_commands import build_color_legacy

    flag = _gbf().Color02Flag
    rgb02, tint02 = (parse_frame(build_color_legacy(*args), "tx").body.params.params
                     for args in ((0x11, 0x22, 0x33), (0xFF, 0xFF, 0xFF, 1, 0xFF, 0xAE, 0x54)))
    check("build_color_legacy round-trips both Config.fu forms (color_02_write: Bytes.a, Bytes.o)",
          (rgb02.color.r, rgb02.color.g, rgb02.color.b, rgb02.white_flag) == (0x11, 0x22, 0x33, flag.rgb)
          and (tint02.color.r, tint02.white_flag) == (0xFF, flag.white_tint)
          and (tint02.tint.r, tint02.tint.g, tint02.tint.b) == (0xFF, 0xAE, 0x54))
    # The white types (devices.yaml white_type): rgb_black's slot 00 00 00 is still CCT; rgb_self_tem_color
    # over a partial posSet is the tint as plain RGB (kelvin 0).
    prof16 = make_profile(f_15_01="kelvin_tint", num_segments=16)
    black, partial = (parse_frame(build_op15_color_temp_bytes_r((100, 50, 16), 2700, pos, kind), "tx",
                                  prof16).body.params.params.data
                      for pos, kind in (([True] * 16, 0), ([True, False] * 8, 3)))
    check("Bytes.r white types: rgb_black leads 00 00 00 at 2700 K; partial rgb_self_tem_color is RGB",
          (black.color.r, black.color.g, black.color.b, black.kelvin, black.is_cct) == (0, 0, 0, 2700, True)
          and (partial.color.r, partial.color.g, partial.color.b, partial.kelvin, partial.is_cct)
          == (100, 50, 16, 0, False))

    k05 = append_bcc(bytes([0x33, 0x05, 0x15, 0x05, 0x0A, 0x8C]))
    kw = parse_frame(k05, "tx", make_profile(f_15_05="kelvin"))
    check("33 05 15 05 0a 8c (op15_kelvin, H60A0) reads 2700 K, big-endian",
          kw.body.params.params.data.body.kelvin == 2700)

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

    # From a checkout, fall back to the library's generated classes (src/govee_ble_local/_generated: run
    # tools/gen_kaitai.sh, or pip install -e .). A fresh ksc build on PYTHONPATH, or an installed
    # govee_ble_local, still comes first / works as before.
    sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent / "src"))
    _ap = argparse.ArgumentParser(description="Reference-codec self-test.")
    _ap.add_argument("--strict", action="store_true", help="fail (exit 1) if the parser tests are skipped")
    sys.exit(_selftest(strict=_ap.parse_args().strict))
