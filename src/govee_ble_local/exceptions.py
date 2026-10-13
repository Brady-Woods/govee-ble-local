"""Exception hierarchy. Every library-raised error subclasses GoveeBleError,
and timeouts are normalized to GoveeBleTimeout (never a bare asyncio/builtin
TimeoutError leaking to callers)."""
from __future__ import annotations


class GoveeBleError(Exception):
    """Base class for every error raised by this library."""


class GoveeBleConnectionError(GoveeBleError):
    """Failed to establish or maintain the BLE transport connection."""


class GoveeBleTimeout(GoveeBleError):
    """An operation (connect, handshake, command ack) timed out."""


class GoveeBleHandshakeError(GoveeBleError):
    """The e7 encryption handshake did not complete successfully."""


class GoveeBleAuthError(GoveeBleError):
    """The device rejected authentication (e.g. wrong/missing secret key)."""


class GoveeBleNotSupported(GoveeBleError):
    """The device/SKU or requested capability is not supported."""


class GoveeBleInvalidValue(GoveeBleError, ValueError):
    """The caller passed an out-of-range or otherwise invalid argument (colour
    channel, kelvin, segment index, secret length, ...). Not a device capability
    problem, so deliberately NOT a GoveeBleNotSupported."""
