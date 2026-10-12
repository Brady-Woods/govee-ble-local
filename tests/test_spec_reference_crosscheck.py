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
    # shipped reassembly pads the last chunk to a whole 17/12-byte region; the payload prefix must match
    joined = ra.reassemble(frames)
    assert joined[: len(tlv)] == tlv
    assert not any(joined[len(tlv):])  # remainder is just zero padding


def test_reassemble_random_agree() -> None:
    r = _rng()
    for _ in range(N_RANDOM):
        tlv = _rand_bytes(r, r.randrange(1, 120))
        frames = ref.chunk_ac(tlv)
        assert ref.reassemble_ac(frames) == tlv
        assert ra.reassemble(frames)[: len(tlv)] == tlv
        # the shipped one also tolerates double delivery; the reference is only a single pass
        assert ra.reassemble(frames + frames[1:3])[: len(tlv)] == tlv


def test_aes_rc4_framing_agrees_when_pycryptodome_present() -> None:
    require_optional("Crypto.Cipher.AES", reason="pycryptodome (declared in the test extra)")
    r = _rng()
    for _ in range(N_RANDOM):
        key = _rand_bytes(r, 16)
        data = _rand_bytes(r, r.choice((16, 20, 32, 20, 36, 5)))
        enc = ref.crypt_v1(data, key, encrypt=True)
        assert enc == crypto.encrypt(data, key)
        assert ref.crypt_v1(enc, key, encrypt=False) == crypto.decrypt(enc, key) == data
