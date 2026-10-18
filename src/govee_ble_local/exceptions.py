"""Exception hierarchy: every library-raised error subclasses GoveeBleError.

BLE operation timeouts are normalized to GoveeBleTimeout (never a bare asyncio/builtin
TimeoutError leaking to callers). Cloud-account failures, timeouts included, raise
GoveeCloudError. The class names are part of the semver-stable API and keep their
historical spelling (e.g. ``GoveeBleTimeout``, not ``GoveeBleTimeoutError``).
"""
from __future__ import annotations


class GoveeBleError(Exception):
    """Base class for every error raised by this library."""


class GoveeBleConnectionError(GoveeBleError):
    """Failed to establish or maintain the BLE transport connection."""


class GoveeBleTimeout(GoveeBleError):
    """An operation (connect, handshake, command ack) timed out."""


class GoveeBleHandshakeError(GoveeBleError):
    """The e7 encryption handshake did not complete successfully."""


class GoveeBleHandshakeTimeout(GoveeBleHandshakeError, GoveeBleTimeout):
    """No valid e7 handshake reply arrived in time.

    Also a GoveeBleTimeout, so existing ``except GoveeBleTimeout`` handlers keep matching.
    """


class GoveeBleHandshakeConnectionError(GoveeBleHandshakeError, GoveeBleConnectionError):
    """The link dropped while the e7 handshake was in progress.

    Also a GoveeBleConnectionError, so existing handlers for it keep matching.
    """


class GoveeBleAuthError(GoveeBleError):
    """The device rejected authentication (e.g. wrong/missing secret key)."""


class GoveeBleCommandRejected(GoveeBleError):
    """The device acknowledged a write with a non-zero status (it refused the command)."""


class GoveeBleNotSupported(GoveeBleError):
    """The device/SKU or requested capability is not supported."""


class GoveeBleInvalidValue(GoveeBleError, ValueError):
    """The caller passed an invalid argument.

    Out-of-range or malformed input (colour channel, kelvin, segment index, secret
    length, ...). Not a device capability problem, so deliberately NOT a
    GoveeBleNotSupported. The one exception is an unknown zone or scene name
    (:class:`GoveeBleUnknownName`), which is both.
    """


class GoveeBleUnknownName(GoveeBleNotSupported, GoveeBleInvalidValue):
    """The caller named a zone or scene this device does not have.

    That is invalid caller input, so it is a GoveeBleInvalidValue, like a bad segment index. It is
    also a GoveeBleNotSupported, the class these errors had before, so existing
    ``except GoveeBleNotSupported`` handlers keep matching (that base goes at the next major
    release).
    """


class GoveeCloudError(GoveeBleError):
    """A cloud-account API call failed (transport, timeout, HTTP or payload error)."""


class GoveeCloudAuthError(GoveeCloudError):
    """The cloud account rejected the credentials or the token (log in again)."""
