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
from typing import Any, Final


from ..const import FRAME_LEN, PSK
from ..crypto import decrypt, encrypt
from ..exceptions import GoveeBleInvalidValue
from ..wire.build import _F, _frame_of, _load, _node

HANDSHAKE_OPCODE: Final = int(_F.ProType.handshake)   # 0xE7

_STEP_SESSION_KEY: Final = int(_F.HandshakeOp.v1_request_session_key)   # e7 01: request / reply with the key
_STEP_CONFIRM: Final = int(_F.HandshakeOp.v1_confirm)                   # e7 02: confirm / ack
_KEY_LEN: Final = 16   # the session key: e7 01 reply bytes 2..17, i.e. handshake.data[:16] (Controller4Aes.g)
_MAX_PAYLOAD: Final = FRAME_LEN - 3  # opcode, step and checksum take the other three bytes


def _build_plain(step: int, payload: bytes | None = None) -> bytes:
    """Plaintext handshake frame: [E7, step, <payload>, <random fill>, bcc], the spec's ``handshake``
    body (``op``, ``data``) written by the generated classes.

    Controller4Aes.e / .f (the only callers in the app) pass no payload; ``payload`` is kept
    for tests and raises GoveeBleInvalidValue if it doesn't fit in front of the checksum."""
    if payload is not None and len(payload) > _MAX_PAYLOAD:
        raise GoveeBleInvalidValue(
            f"handshake payload must be at most {_MAX_PAYLOAD} bytes, got {len(payload)}")
    head = payload or b""
    # Controller4Aes.a fills the rest of the data field, up to the checksum, with random bytes.
    data = head + os.urandom(_MAX_PAYLOAD - len(head))
    return _frame_of(_F.ProType.handshake, lambda f: _node(_F.Handshake, f, op=step, data=data))


def build_step1(psk: bytes = PSK) -> bytes:
    """PSK-encrypted ``e7 01`` request (Controller4Aes.e)."""
    return encrypt(_build_plain(_STEP_SESSION_KEY), psk)


def build_step2(psk: bytes = PSK) -> bytes:
    """PSK-encrypted ``e7 02`` confirm (Controller4Aes.f)."""
    return encrypt(_build_plain(_STEP_CONFIRM), psk)


def _handshake_body(pt: bytes) -> Any:
    """The spec's ``handshake`` body (``op``, ``data``) of a decrypted ``e7`` frame, read by the frame
    reader, or None when ``pt`` is not one. Its ``data`` is bytes 2..18 (the reader leaves the BCC byte
    out, and checks it no more than the app does: Controller4Aes.g / .h read only the op and the key)."""
    try:
        f = _load(_F.from_bytes(bytes(pt)))
    except Exception:  # noqa: BLE001 - a mis-keyed decrypt is noise: not an e7 frame
        return None
    return f.body if f.pro_type == _F.ProType.handshake else None


def parse_session_key(reply_ciphertext: bytes, psk: bytes = PSK) -> bytes | None:
    """Decrypt an ``e7 01`` reply and extract the 16-byte session key
    (Controller4Aes.g): the first 16 bytes of the spec's ``handshake.data``. Returns None if it isn't
    a valid step-1 reply."""
    body = _handshake_body(decrypt(reply_ciphertext, psk))
    if body is None or body.op != _STEP_SESSION_KEY or len(body.data) < _KEY_LEN:
        return None
    return bytes(body.data[:_KEY_LEN])


def is_step2_ack(reply_ciphertext: bytes, psk: bytes = PSK) -> bool:
    """True if the ciphertext decrypts to a valid ``e7 02`` ack (Controller4Aes.h)."""
    body = _handshake_body(decrypt(reply_ciphertext, psk))
    return body is not None and body.op == _STEP_CONFIRM
