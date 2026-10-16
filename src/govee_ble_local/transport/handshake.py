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
from typing import Final

from ..const import FRAME_LEN, PSK
from ..crypto import checksum, decrypt, encrypt
from ..exceptions import GoveeBleInvalidValue

HANDSHAKE_OPCODE = 0xE7

_STEP_SESSION_KEY: Final = 0x01    # e7 01: request / reply carrying the session key
_STEP_CONFIRM: Final = 0x02        # e7 02: confirm / ack
_KEY_SLICE: Final = slice(2, 18)   # the 16-byte session key in an e7 01 reply (Controller4Aes.g)
_MAX_PAYLOAD: Final = FRAME_LEN - 3  # opcode, step and checksum take the other three bytes


def _build_plain(step: int, payload: bytes | None = None) -> bytes:
    """Plaintext handshake frame: [E7, step, <payload>, <random fill>, bcc].

    Controller4Aes.e / .f (the only callers in the app) pass no payload; ``payload`` is kept
    for tests and raises GoveeBleInvalidValue if it doesn't fit in front of the checksum."""
    if payload is not None and len(payload) > _MAX_PAYLOAD:
        raise GoveeBleInvalidValue(
            f"handshake payload must be at most {_MAX_PAYLOAD} bytes, got {len(payload)}")
    body = bytearray(FRAME_LEN)
    body[0] = HANDSHAKE_OPCODE
    body[1] = step
    n = 0
    if payload:
        body[2 : 2 + len(payload)] = payload
        n = len(payload)
    # Controller4Aes.a fills the gap up to index 19 with random bytes.
    fill = os.urandom(FRAME_LEN - 1 - (2 + n))
    body[2 + n : FRAME_LEN - 1] = fill
    body[FRAME_LEN - 1] = checksum(bytes(body[: FRAME_LEN - 1]))
    return bytes(body)


def build_step1(psk: bytes = PSK) -> bytes:
    """PSK-encrypted ``e7 01`` request (Controller4Aes.e)."""
    return encrypt(_build_plain(_STEP_SESSION_KEY), psk)


def build_step2(psk: bytes = PSK) -> bytes:
    """PSK-encrypted ``e7 02`` confirm (Controller4Aes.f)."""
    return encrypt(_build_plain(_STEP_CONFIRM), psk)


def parse_session_key(reply_ciphertext: bytes, psk: bytes = PSK) -> bytes | None:
    """Decrypt an ``e7 01`` reply and extract the 16-byte session key
    (Controller4Aes.g). Returns None if it isn't a valid step-1 reply."""
    pt = decrypt(reply_ciphertext, psk)
    if len(pt) >= _KEY_SLICE.stop and pt[0] == HANDSHAKE_OPCODE and pt[1] == _STEP_SESSION_KEY:
        return pt[_KEY_SLICE]
    return None


def is_step2_ack(reply_ciphertext: bytes, psk: bytes = PSK) -> bool:
    """True if the ciphertext decrypts to a valid ``e7 02`` ack (Controller4Aes.h)."""
    pt = decrypt(reply_ciphertext, psk)
    return len(pt) >= 2 and pt[0] == HANDSHAKE_OPCODE and pt[1] == _STEP_CONFIRM
