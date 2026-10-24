"""Cross-check the independent reference codec in ``spec/govee_reference.py`` (BCC, RC4, 0xAC
reassembly, AES+RC4 framing) against the shipped implementations in ``crypto.py`` and
``wire/reassemble.py``, on fixed vectors plus seeded-random inputs. A divergence means one
of the two independent implementations is wrong."""
from __future__ import annotations

import importlib.util
import pathlib
import random

import pytest
from conftest import require_optional

from govee_ble_local import crypto
from govee_ble_local.wire import reassemble as ra

_REF_PATH = pathlib.Path(__file__).resolve().parent.parent / "spec" / "govee_reference.py"
if not _REF_PATH.exists():  # pragma: no cover
    pytest.skip("spec/govee_reference.py not present", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("govee_reference_under_test", _REF_PATH)
assert _spec and _spec.loader
ref = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ref)

N_RANDOM = 500


def _rng() -> random.Random:
    return random.Random(0x60EE)  # noqa: S311 - deterministic test vectors


def _rand_bytes(r: random.Random, n: int) -> bytes:
    return bytes(r.randrange(256) for _ in range(n))


def test_rc4_known_vector_both() -> None:
    assert ref._rc4(b"Plaintext", b"Key").hex() == "bbf316e8d940af0ad3"
    assert crypto._rc4(b"Key", b"Plaintext").hex() == "bbf316e8d940af0ad3"


def test_rc4_random_agree() -> None:
    r = _rng()
    for _ in range(N_RANDOM):
        key = _rand_bytes(r, r.randrange(1, 33))
        data = _rand_bytes(r, r.randrange(0, 64))
        assert ref._rc4(data, key) == crypto._rc4(key, data)


def test_bcc_fixed_and_random_agree() -> None:
    for payload in (b"\xaa\x04\x64", b"\x33\x05\x04\x4a\x82", b"", b"\xff" * 19):
        fr = ref.append_bcc(payload)
        assert len(fr) == 20 and ref.verify_bcc(fr) and crypto.checksum_ok(fr)
        assert ref.bcc(fr) == crypto.checksum(fr[:19]) == fr[19]
    r = _rng()
    for _ in range(N_RANDOM):
        fr = _rand_bytes(r, 20)
        assert ref.bcc(fr) == crypto.checksum(fr[:19])
        assert ref.verify_bcc(fr) == crypto.checksum_ok(fr)
        fixed = ref.append_bcc(fr[:19])
        assert crypto.checksum_ok(fixed)


def _status_tlv_cases() -> list[bytes]:
    r = _rng()
    fixed = [bytes([0x01, 0x01, 0x01, 0x04, 0x01, 0x64, 0xA5, 0x0D, 0x01, 100, 255, 0, 0, 80, 0, 255,
                    0, 60, 0, 0, 255])]
    # lengths covering: < 12, exactly 12, 12+17k boundaries, one past
    for n in (1, 11, 12, 13, 28, 29, 30, 46, 47, 48, 100):
        fixed.append(_rand_bytes(r, n))
    return fixed


@pytest.mark.parametrize("tlv", _status_tlv_cases())
def test_reassemble_fixed_vectors_agree(tlv: bytes) -> None:
    frames = ref.chunk_ac(tlv)
    assert ref.reassemble_ac(frames) == tlv
    # both trim the last chunk to lastLen (AbsController4BleSingleSendMultiBack.h), so no zero padding
    assert ra.reassemble(frames) == tlv


def test_reassemble_random_agree() -> None:
    r = _rng()
    for _ in range(N_RANDOM):
        tlv = _rand_bytes(r, r.randrange(1, 120))
        frames = ref.chunk_ac(tlv)
        assert ref.reassemble_ac(frames) == tlv
        assert ra.reassemble(frames) == tlv
        # the shipped one also tolerates double delivery; the reference is only a single pass
        assert ra.reassemble(frames + frames[1:3]) == tlv


# Real H60A6 0xAC bursts (same captures as tests/test_wire_reassemble.py _BURST / _LIVE): bytes 2-3 of
# the first chunk = 0x000a = 10 packets (ac00..ac08 + acff), byte 4 = 0x0c = 12 data bytes in the
# data-bearing 0xFF chunk (AbsController4BleSingleSendMultiBack.g/.h).
# Device ids and MACs in them are made-up examples (checksums recomputed).
_REAL_BURSTS = {
    "burst": [
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
    ],
    "live": [
        "ac000a000c0300010100040164050415010a8c5b",
        "ac010707065753005e00020711105653005e00ae",
        "ac0202a60a0100290104030000070d1102005e69",
        "ac030053560100290104031104001e0f0f12079a",
        "ac04ff640000800f00231000000080000000808f",
        "ac0500000080000000804102020130020000a57e",
        "ac06110164ffae5464ffae5464ffae5464ffaeee",
        "ac0754a5110264ffae5464ffae5464ffae54644c",
        "ac08ffae54a5110364ffae5464ffae5464ffae23",
        "acff5464ffae54a5050464ffae540000000000a3",
    ],
}


def _tlv_end(buf: bytes) -> int:
    pos = 0
    while pos + 2 <= len(buf) and buf[pos] != 0:
        pos += 2 + buf[pos + 1]
    return pos


@pytest.mark.parametrize("name", sorted(_REAL_BURSTS))
def test_reassemble_real_h60a6_burst_agrees(name: str) -> None:
    frames = [bytes.fromhex(h) for h in _REAL_BURSTS[name]]
    buf = ref.reassemble_ac(frames)
    assert len(buf) == 12 + 17 * 8 + 12 == 160   # packet count 10, lastLen 12 from the 0xFF chunk
    assert _tlv_end(buf) == len(buf)             # exactly the TLV stream, nothing past it
    assert buf[-12:] == frames[-1][2:14]         # ...ending in the 0xFF chunk's lastLen data bytes
    assert [t.tag for t in ref.parse_status(buf).tlvs][-4:] == [0xA5] * 4   # incl. group 4 (segment 12)
    assert ra.reassemble(frames) == buf          # shipped trims the 0xFF chunk to lastLen too
    assert ref.chunk_ac(buf) == frames           # the inverse reproduces the capture byte for byte


def test_chunk_ac_header_is_packet_count_and_last_len() -> None:
    tlv = bytes(range(1, 47))                    # 12 + 17 + 17: three packets
    frames = ref.chunk_ac(tlv)
    assert [f[1] for f in frames] == [0x00, 0x01, 0xFF]
    assert (frames[0][2] | frames[0][3] << 8, frames[0][4]) == (3, 17)
    assert frames[-1][2:19] == tlv[29:]          # the 0xFF chunk carries data
    one = ref.chunk_ac(tlv[:5])                  # <= 12 bytes: one packet, lastLen bytes @7
    assert len(one) == 1 and (one[0][2], one[0][3], one[0][4]) == (1, 0, 5)
    assert ref.reassemble_ac(one) == tlv[:5]


def test_reassemble_ac_rejects_what_the_app_rejects() -> None:
    frames = ref.chunk_ac(bytes(range(1, 47)))
    with pytest.raises(ValueError, match="incomplete"):
        ref.reassemble_ac(frames[:2])
    with pytest.raises(ValueError, match="tag"):
        ref.reassemble_ac([frames[0], frames[2], frames[1]])
    bad = bytearray(ref.chunk_ac(b"\x01\x02")[0])
    bad[4] = 13                                  # single packet holds at most 12 bytes
    with pytest.raises(ValueError, match="lastLen"):
        ref.reassemble_ac([bytes(bad)])


def test_selftest_falls_back_to_committed_reader(capsys: pytest.CaptureFixture[str],
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    from govee_ble_local._generated.govee_ble_frame import GoveeBleFrame

    # The self-test imports the builder catalogue, spec/govee_commands.py, as a top-level `govee_commands`
    # (which imports `govee_reference` so): spec/ is on the path here, whatever ran before.
    monkeypatch.syspath_prepend(str(_REF_PATH.parent))
    assert ref._gbf() is GoveeBleFrame
    assert ref._selftest(strict=True) == 0       # every parser check ran and passed
    assert "Skipping" not in capsys.readouterr().out


def test_selftest_skip_is_a_failure_only_when_strict(monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(ref, "_READER_MODULES", ("_no_such_reader_module",))
    assert ref._selftest() == 0
    assert ref._selftest(strict=True) == 1
    assert "Skipping parser tests" in capsys.readouterr().out


def test_aes_rc4_framing_agrees_when_pycryptodome_present() -> None:
    require_optional("Crypto.Cipher.AES", reason="pycryptodome (declared in the test extra)")
    r = _rng()
    for _ in range(N_RANDOM):
        key = _rand_bytes(r, 16)
        data = _rand_bytes(r, r.choice((16, 20, 32, 20, 36, 5)))
        enc = ref.crypt_v1(data, key, encrypt=True)
        assert enc == crypto.encrypt(data, key)
        assert ref.crypt_v1(enc, key, encrypt=False) == crypto.decrypt(enc, key) == data


def _retag(frame: bytes, tag: int) -> bytes:
    """``frame`` with its index byte replaced by ``tag`` (and the BCC fixed)."""
    return ref.append_bcc(bytes([frame[0], tag]) + frame[2:19])


def test_reassemble_ac_accepts_the_last_packet_tagged_with_its_position() -> None:
    # isSameController takes the last packet's own position (K(tag) == backPos) as well as 0xFF, and
    # onResult runs h() there either way (AbsController4BleSingleSendMultiBack:248, :326-331)
    tlv = bytes(range(1, 47))                    # three packets: positions 0, 1, 2
    frames = ref.chunk_ac(tlv)
    frames[-1] = _retag(frames[-1], 2)
    assert ref.reassemble_ac(frames) == tlv == ra.reassemble(frames)
    with pytest.raises(ValueError, match="last packet carries tag 0x03"):
        ref.reassemble_ac([*frames[:-1], _retag(frames[-1], 3)])   # neither 0xFF nor its position
    real = [bytes.fromhex(h) for h in _REAL_BURSTS["burst"]]
    assert ref.reassemble_ac([*real[:-1], _retag(real[-1], 9)]) == ref.reassemble_ac(real)


def test_reassemble_ac_refuses_0xff_mid_burst() -> None:
    # 257 packets: position 255 is a middle packet, and its tag byte can only be 0xFF, which
    # isSameController reads as END_PKG out of place (:237-247), so the app never completes it
    count = 257
    head = bytes([0xAC, 0x00, count & 0xFF, count >> 8, 17, 0x03, 0x00]) + bytes(12)
    frames = [ref.append_bcc(head)] + [ref.append_bcc(bytes([0xAC, pos & 0xFF]) + bytes(17))
                                       for pos in range(1, count - 1)]
    frames.append(ref.append_bcc(bytes([0xAC, 0xFF]) + bytes(17)))
    with pytest.raises(ValueError, match="packet 255 carries tag 0xff"):
        ref.reassemble_ac(frames)
