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
from ..wire import _spec
from ..wire._spec import Common as _C, Frame as _F

HANDSHAKE_OPCODE: Final = int(_C.ProType.handshake)   # 0xE7

_STEP_SESSION_KEY: Final = int(_C.HandshakeOp.v1_request_session_key)   # e7 01: request / reply with the key
_STEP_CONFIRM: Final = int(_C.HandshakeOp.v1_confirm)                   # e7 02: confirm / ack
_MAX_PAYLOAD: Final = FRAME_LEN - 3  # opcode, step and checksum take the other three bytes


def _build_plain(step: int, payload: bytes | None = None) -> bytes:
    """Plaintext handshake frame: [E7, step, <payload>, <random fill>, bcc], the spec's
    ``handshake_request`` (``op``, ``fill``) written by the generated classes.

    Controller4Aes.e / .f (the only callers in the app) pass no payload; ``payload`` is kept
    for tests and raises GoveeBleInvalidValue if it doesn't fit in front of the checksum."""
    if payload is not None and len(payload) > _MAX_PAYLOAD:
        raise GoveeBleInvalidValue(
            f"handshake payload must be at most {_MAX_PAYLOAD} bytes, got {len(payload)}")
    head = payload or b""
    # Controller4Aes.a fills the rest of the data field, up to the checksum, with random bytes.
    data = head + os.urandom(_MAX_PAYLOAD - len(head))
    return _spec.build_frame(_C.ProType.handshake, _spec.PLAIN,
                             lambda f: _spec.node(_F.HandshakeRequest, f, op=step, fill=data), FRAME_LEN)


def build_step1(psk: bytes = PSK) -> bytes:
    """PSK-encrypted ``e7 01`` request (Controller4Aes.e)."""
    return encrypt(_build_plain(_STEP_SESSION_KEY), psk)


def build_step2(psk: bytes = PSK) -> bytes:
    """PSK-encrypted ``e7 02`` confirm (Controller4Aes.f)."""
    return encrypt(_build_plain(_STEP_CONFIRM), psk)


def _handshake_body(pt: bytes) -> Any:
    """The spec's ``handshake_reply`` (``op``, the ``key`` its op carries, ``rest``) of a decrypted ``e7``
    frame, read by the frame reader, or None when ``pt`` is not one (a mis-keyed decrypt, or a session-key
    reply too short for its key). The reader leaves the BCC byte out, and checks it no more than the app
    does: Controller4Aes.g / .h read only the op and the key."""
    f = _spec.try_read_frame(pt, _spec.RX, _spec.PLAIN)
    return f.body if f is not None and f.pro_type == _C.ProType.handshake else None


def parse_session_key(reply_ciphertext: bytes, psk: bytes = PSK) -> bytes | None:
    """Decrypt an ``e7 01`` reply and extract the 16-byte session key (Controller4Aes.g): the spec's
    ``handshake_reply.key``, a ``session_key`` (frame bytes 2..17). Returns None if it isn't a valid
    step-1 reply."""
    body = _handshake_body(decrypt(reply_ciphertext, psk))
    if body is None or body.op != _STEP_SESSION_KEY:
        return None
    return bytes(body.key.key)


def is_step2_ack(reply_ciphertext: bytes, psk: bytes = PSK) -> bool:
    """True if the ciphertext decrypts to a valid ``e7 02`` ack (Controller4Aes.h)."""
    body = _handshake_body(decrypt(reply_ciphertext, psk))
    return body is not None and body.op == _STEP_CONFIRM
