"""Frame crypto — a faithful port of ``com.govee.encryp.ble.Safe``.

Govee's BLE payload cipher over a 20-byte frame is: **AES-ECB (no padding)
on each whole 16-byte block, then RC4 on the trailing remainder.** For a
20-byte frame that's AES-ECB(bytes[0:16]) + RC4(bytes[16:20]).

The same routine (`encrypt`/`decrypt`) is used with the PSK during the e7
handshake and with the negotiated session key for every frame afterwards.
:class:`FrameCipher` is the same routine bound to one key, with the per-key setup
done once, for the per-frame session path.
"""
from __future__ import annotations

from collections.abc import Callable

from cryptography.hazmat.primitives.ciphers import Cipher, CipherContext, algorithms, modes

from .const import FRAME_LEN
from .exceptions import GoveeBleInvalidValue


def _aes_cipher(key16: bytes) -> Cipher[modes.ECB]:
    if len(key16) != 16:
        raise GoveeBleInvalidValue(f"AES key must be exactly 16 bytes, got {len(key16)}")
    # The Govee BLE protocol mandates AES-ECB (single 16-byte blocks); not a free choice.
    return Cipher(algorithms.AES(key16), modes.ECB())  # nosec B305


def _aes_block(context: CipherContext, block16: bytes) -> bytes:
    """One 16-byte block through a fresh encryptor or decryptor context."""
    if len(block16) != 16:
        raise GoveeBleInvalidValue(f"AES block must be exactly 16 bytes, got {len(block16)}")
    return context.update(block16) + context.finalize()


def aes_ecb_block(key16: bytes, block16: bytes, *, encrypt: bool) -> bytes:
    """AES/ECB/NoPadding on a single 16-byte block (Safe.c encrypts, Safe.a decrypts).

    ``encrypt`` picks the direction; the keyword keeps its name for existing callers."""
    cipher = _aes_cipher(key16)
    return _aes_block(cipher.encryptor() if encrypt else cipher.decryptor(), block16)


def _rc4_ksa(key: bytes) -> tuple[int, ...]:
    """RC4 key schedule (Safe.f): the 256-entry state, which depends only on the key."""
    if not key:
        raise GoveeBleInvalidValue("RC4 key must not be empty")
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    return tuple(s)


def _rc4_prga(ksa: tuple[int, ...], data: bytes) -> bytes:
    """RC4 keystream XOR (Safe.g), from the start, on a fresh copy of the key schedule."""
    s = list(ksa)
    out = bytearray(len(data))
    i = j = 0
    for k, byte in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[k] = byte ^ s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


def _rc4(key: bytes, data: bytes) -> bytes:
    """Standard RC4, hand-rolled to match Safe.f (KSA) + Safe.g (PRGA)."""
    return _rc4_prga(_rc4_ksa(key), data)


def _transform(data: bytes, aes: Callable[[bytes], bytes], rc4: Callable[[bytes], bytes]) -> bytes:
    """Block-wise AES-ECB + RC4 remainder (Safe.d encrypt / Safe.b decrypt).

    ``aes`` transforms one whole 16-byte block and ``rc4`` the trailing remainder, so the
    direction is chosen by the caller rather than by a flag."""
    n_blocks, rem = divmod(len(data), 16)
    out = bytearray()
    for b in range(n_blocks):
        out += aes(data[b * 16 : b * 16 + 16])
    if rem:
        out += rc4(data[n_blocks * 16 :])
    return bytes(out)


def encrypt(plaintext: bytes, key16: bytes) -> bytes:
    """Encrypt a (already-framed) payload. Safe.d.

    Redoes the key setup on every call; use :class:`FrameCipher` for many frames under one key."""
    return _transform(
        plaintext, lambda block: aes_ecb_block(key16, block, encrypt=True), lambda rem: _rc4(key16, rem)
    )


def decrypt(ciphertext: bytes, key16: bytes) -> bytes:
    """Decrypt a received payload. Safe.b.

    Redoes the key setup on every call; use :class:`FrameCipher` for many frames under one key."""
    return _transform(
        ciphertext, lambda block: aes_ecb_block(key16, block, encrypt=False), lambda rem: _rc4(key16, rem)
    )


class FrameCipher:
    """:func:`encrypt` / :func:`decrypt` bound to one 16-byte key, with the key setup done once.

    The output is byte-for-byte that of the module functions (Safe.d / Safe.b): every call still
    gets a fresh AES context, and the RC4 keystream (Safe.g) restarts on a copy of the key
    schedule, exactly as Safe.g calls Safe.f per call. Only the key schedule and the AES key setup
    are not rebuilt per frame. Hold one per session, next to the session key, and drop it with the
    key; a module-level cache would keep session keys alive after a disconnect.

    Raises GoveeBleInvalidValue unless ``key16`` is exactly 16 bytes.
    """

    __slots__ = ("_aes", "_ksa")

    def __init__(self, key16: bytes) -> None:
        self._aes = _aes_cipher(key16)
        self._ksa = _rc4_ksa(key16)

    def _aes_encrypt(self, block16: bytes) -> bytes:
        return _aes_block(self._aes.encryptor(), block16)

    def _aes_decrypt(self, block16: bytes) -> bytes:
        return _aes_block(self._aes.decryptor(), block16)

    def _rc4(self, data: bytes) -> bytes:
        return _rc4_prga(self._ksa, data)

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt a (already-framed) payload. Safe.d."""
        return _transform(plaintext, self._aes_encrypt, self._rc4)

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt a received payload. Safe.b."""
        return _transform(ciphertext, self._aes_decrypt, self._rc4)


def checksum(body: bytes) -> int:
    """XOR of all bytes — the trailing byte of every 20-byte frame."""
    x = 0
    for b in body:
        x ^= b
    return x


def checksum_ok(frame20: bytes) -> bool:
    """True if a plaintext 20-byte frame's trailing XOR checksum is valid."""
    return len(frame20) == FRAME_LEN and checksum(frame20[:-1]) == frame20[-1]
