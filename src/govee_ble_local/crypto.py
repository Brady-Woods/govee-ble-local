"""Frame crypto — a faithful port of ``com.govee.encryp.ble.Safe``.

Govee's BLE payload cipher over a 20-byte frame is: **AES-ECB (no padding)
on each whole 16-byte block, then RC4 on the trailing remainder.** For a
20-byte frame that's AES-ECB(bytes[0:16]) + RC4(bytes[16:20]).

The same routine (`encrypt`/`decrypt`) is used with the PSK during the e7
handshake and with the negotiated session key for every frame afterwards.
"""
from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def aes_ecb_block(key16: bytes, block16: bytes, *, encrypt: bool) -> bytes:
    """AES/ECB/NoPadding on a single 16-byte block (Safe.a / Safe.c)."""
    cipher = Cipher(algorithms.AES(key16), modes.ECB())
    op = cipher.encryptor() if encrypt else cipher.decryptor()
    return op.update(block16) + op.finalize()


def _rc4(key: bytes, data: bytes) -> bytes:
    """Standard RC4, hand-rolled to match Safe.f (KSA) + Safe.g (PRGA)."""
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    out = bytearray(len(data))
    i = j = 0
    for k, byte in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[k] = byte ^ s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


def _transform(data: bytes, key16: bytes, *, encrypt: bool) -> bytes:
    """Block-wise AES-ECB + RC4 remainder (Safe.d encrypt / Safe.b decrypt)."""
    n_blocks, rem = divmod(len(data), 16)
    out = bytearray()
    for b in range(n_blocks):
        out += aes_ecb_block(key16, data[b * 16 : b * 16 + 16], encrypt=encrypt)
    if rem:
        out += _rc4(key16, data[n_blocks * 16 :])
    return bytes(out)


def encrypt(plaintext: bytes, key16: bytes) -> bytes:
    """Encrypt a (already-framed) payload. Safe.d."""
    return _transform(plaintext, key16, encrypt=True)


def decrypt(ciphertext: bytes, key16: bytes) -> bytes:
    """Decrypt a received payload. Safe.b."""
    return _transform(ciphertext, key16, encrypt=False)


def checksum(body: bytes) -> int:
    """XOR of all bytes — the trailing byte of every 20-byte frame."""
    x = 0
    for b in body:
        x ^= b
    return x


def checksum_ok(frame20: bytes) -> bool:
    """True if a plaintext 20-byte frame's trailing XOR checksum is valid."""
    return len(frame20) == 20 and checksum(frame20[:19]) == frame20[19]
