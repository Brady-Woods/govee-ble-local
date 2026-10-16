"""crypto.FrameCipher (per-session key setup done once) and handshake frame building."""
from __future__ import annotations

import random
from collections.abc import Callable

import pytest

from govee_ble_local import crypto
from govee_ble_local.const import FRAME_LEN, PSK
from govee_ble_local.crypto import FrameCipher, aes_ecb_block, checksum_ok, decrypt, encrypt
from govee_ble_local.exceptions import GoveeBleInvalidValue
from govee_ble_local.transport import handshake


def _rng() -> random.Random:
    return random.Random(0x1B)  # noqa: S311 - deterministic test vectors


# -- FrameCipher -----------------------------------------------------------------------------

def test_frame_cipher_matches_module_functions() -> None:
    r = _rng()
    for _ in range(200):
        key = r.randbytes(16)
        data = r.randbytes(r.choice((0, 4, 15, 16, 17, 20, 20, 32, 36)))
        fc = FrameCipher(key)
        assert fc.encrypt(data) == encrypt(data, key)
        assert fc.decrypt(data) == decrypt(data, key)
        assert fc.decrypt(fc.encrypt(data)) == data


def test_frame_cipher_restarts_rc4_each_call() -> None:
    # Safe.g runs the keystream from the key schedule on every call: no state carries over.
    fc = FrameCipher(PSK)
    frame = bytes(range(20))
    assert fc.encrypt(frame) == fc.encrypt(frame) == encrypt(frame, PSK)
    assert fc.decrypt(fc.encrypt(frame)) == frame


def test_frame_cipher_builds_key_schedule_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bytes] = []
    real = crypto._rc4_ksa

    def counting(key: bytes) -> tuple[int, ...]:
        calls.append(key)
        return real(key)

    monkeypatch.setattr(crypto, "_rc4_ksa", counting)
    fc = FrameCipher(PSK)
    for i in range(5):
        fc.decrypt(fc.encrypt(bytes([i]) * 20))
    assert calls == [PSK]


@pytest.mark.parametrize("n", [0, 8, 15, 17, 32])
def test_frame_cipher_key_length_validated(n: int) -> None:
    with pytest.raises(GoveeBleInvalidValue, match="16 bytes"):
        FrameCipher(bytes(n))


def test_module_functions_keep_lazy_validation() -> None:
    # Like Safe.d, a frame shorter than one AES block only runs RC4, whatever the key length.
    assert encrypt(b"abcd", b"Key") == crypto._rc4(b"Key", b"abcd")
    with pytest.raises(GoveeBleInvalidValue, match="16 bytes"):
        encrypt(bytes(16), b"Key")


def test_aes_ecb_block_keyword_still_accepted() -> None:
    block = bytes(range(16))
    enc = aes_ecb_block(PSK, block, encrypt=True)
    assert enc == encrypt(block, PSK)
    assert aes_ecb_block(PSK, enc, encrypt=False) == block


# -- handshake ---------------------------------------------------------------------------------

@pytest.mark.parametrize("n", [18, 19, 25])
def test_build_plain_rejects_oversized_payload(n: int) -> None:
    with pytest.raises(GoveeBleInvalidValue, match="at most 17 bytes"):
        handshake._build_plain(1, bytes(n))


def test_build_plain_largest_payload_fits() -> None:
    pt = handshake._build_plain(2, bytes(range(1, 18)))
    assert len(pt) == FRAME_LEN and pt[:2] == b"\xe7\x02" and pt[2:19] == bytes(range(1, 18))
    assert checksum_ok(pt)


@pytest.mark.parametrize(("build", "step"), [(handshake.build_step1, 1), (handshake.build_step2, 2)])
def test_steps_are_psk_encrypted_e7_frames(build: Callable[[], bytes], step: int) -> None:
    # Controller4Aes.e / .f: [e7, step, 17 random bytes, bcc], Safe.d with the PSK.
    wires = {build() for _ in range(3)}
    assert len(wires) == 3  # random fill
    for wire in wires:
        pt = decrypt(wire, PSK)
        assert len(wire) == FRAME_LEN and pt[0] == handshake.HANDSHAKE_OPCODE and pt[1] == step
        assert checksum_ok(pt)


def test_parse_session_key_and_ack() -> None:
    key = bytes(range(0x40, 0x50))
    reply = bytearray(FRAME_LEN)
    reply[:2] = b"\xe7\x01"
    reply[2:18] = key
    assert handshake.parse_session_key(encrypt(bytes(reply), PSK)) == key
    assert handshake.parse_session_key(encrypt(bytes(reply[:17]), PSK)) is None  # too short
    reply[1] = 0x02
    assert handshake.parse_session_key(encrypt(bytes(reply), PSK)) is None
    assert handshake.is_step2_ack(encrypt(bytes(reply), PSK))
    assert not handshake.is_step2_ack(encrypt(b"\xe7\x01" + bytes(18), PSK))
