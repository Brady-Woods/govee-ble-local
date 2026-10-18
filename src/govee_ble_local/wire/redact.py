"""Masking of the account-lock secret in everything the library logs or captures.

A frame carries the secret when it is a ``33 b2`` check write or an ``aa b1`` read reply. It is matched on its
two header bytes on purpose, not through the spec's frame reader: masking must fail closed, so a secret frame
whose body doesn't parse (short, malformed, a capture fragment) is still masked before it reaches a log, an
exception message or a frame capture. The frame capture (``transport.connection``), the wire layer's log lines
and exception text, and the capture tools all go through here.
"""
from __future__ import annotations

from ..const import FRAME_LEN
from ..crypto import checksum
from .build import CMD_SECRET_READ, CMD_SECRET_WRITE, PRO_READ, PRO_WRITE


def carries_secret(plain: bytes) -> bool:
    """True for a frame that carries the account-lock secret: a ``33 b2`` check write or an ``aa b1`` read
    reply, matched on the header bytes alone (see the module docstring)."""
    return len(plain) >= 2 and (
        (plain[0] == PRO_WRITE and plain[1] == CMD_SECRET_WRITE)  # secret check write
        or (plain[0] == PRO_READ and plain[1] == CMD_SECRET_READ)  # secret read reply
    )


def mask_secret(frame: bytes) -> bytes:
    """Blank the payload of a secret-carrying frame, keeping its 2-byte header.

    The single masking helper (used for plain, wire and log output). A full-length frame keeps a VALID
    trailing checksum - recomputed over the masked bytes, so it never leaks the XOR of the secret; any other
    length is just header + blanks."""
    if len(frame) != FRAME_LEN:
        return frame[:2] + b"\xff" * (len(frame) - 2)
    body = frame[:2] + b"\xff" * (FRAME_LEN - 3)
    return body + bytes([checksum(body)])


def shown_hex(frame: bytes) -> str:
    """``frame`` as hex for a log line or an exception message: masked (:func:`mask_secret`) when it carries
    the secret, else as it is."""
    data = bytes(frame)
    return (mask_secret(data) if carries_secret(data) else data).hex()
