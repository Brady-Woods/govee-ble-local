"""The e7 encryption handshake — port of ``Controller4Aes``.

Two-step challenge with the PSK:
  1. host -> ``e7 01 <16 random bytes>`` (PSK-encrypted); device replies
     ``e7 01 <16-byte session key>`` (PSK-encrypted). We keep bytes[2:18]
     as the session key.
  2. host -> ``e7 02 <16 random bytes>`` (PSK-encrypted); device echoes an
     ``e7 02`` ack.

The random payload bytes are ignored by the device (confirmed) — the session
key is entirely device-chosen and returned in step 1. Every application frame
after the handshake is encrypted with that session key.
"""
from __future__ import annotations

import os

from ..const import FRAME_LEN, PSK
from ..crypto import checksum, decrypt, encrypt

HANDSHAKE_OPCODE = 0xE7


def _build_plain(step: int, payload: bytes | None) -> bytes:
    """Plaintext handshake frame: [E7, step, <payload>, <random fill>, bcc]."""
    body = bytearray(FRAME_LEN)
    body[0] = HANDSHAKE_OPCODE
    body[1] = step
    n = 0
    if payload:
        body[2 : 2 + len(payload)] = payload
        n = len(payload)
    # Controller4Aes.a fills the gap up to index 19 with random bytes.
    fill = os.urandom(19 - (2 + n))
    body[2 + n : 19] = fill
    body[19] = checksum(bytes(body[:19]))
    return bytes(body)


def build_step1(psk: bytes = PSK, payload: bytes | None = None) -> bytes:
    """PSK-encrypted ``e7 01`` request (Controller4Aes.e)."""
    return encrypt(_build_plain(0x01, payload), psk)


def build_step2(psk: bytes = PSK, payload: bytes | None = None) -> bytes:
    """PSK-encrypted ``e7 02`` confirm (Controller4Aes.f)."""
    return encrypt(_build_plain(0x02, payload), psk)


def parse_session_key(reply_ciphertext: bytes, psk: bytes = PSK) -> bytes | None:
    """Decrypt an ``e7 01`` reply and extract the 16-byte session key
    (Controller4Aes.g). Returns None if it isn't a valid step-1 reply."""
    pt = decrypt(reply_ciphertext, psk)
    if len(pt) >= 18 and pt[0] == HANDSHAKE_OPCODE and pt[1] == 0x01:
        return pt[2:18]
    return None


def is_step2_ack(reply_ciphertext: bytes, psk: bytes = PSK) -> bool:
    """True if the ciphertext decrypts to a valid ``e7 02`` ack (Controller4Aes.h)."""
    pt = decrypt(reply_ciphertext, psk)
    return len(pt) >= 2 and pt[0] == HANDSHAKE_OPCODE and pt[1] == 0x02
