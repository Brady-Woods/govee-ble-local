"""wire.redact: the account-lock secret never reaches a log line, an exception message or a capture in the
clear, even from a frame whose body doesn't parse (the mask matches the header bytes alone)."""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

import pytest
from kaitaistruct import ValidationNotEqualError

from govee_ble_local.const import FRAME_LEN
from govee_ble_local.crypto import checksum_ok
from govee_ble_local.wire import _spec, build, describe, parse, redact

from fake_ble import frame

_SECRET = bytes([0xDE, 0xAD, 0xBE, 0xEF, 0xC0, 0xFE, 0xE1, 0x9D])   # high bytes: a repr shows them as \x..


def test_mask_helper_recomputes_checksum() -> None:
    plain = frame(0x33, 0xB2, *_SECRET)
    masked = redact.mask_secret(plain)
    assert len(masked) == FRAME_LEN
    assert masked[:2] == plain[:2] and masked[2:-1] == b"\xff" * (FRAME_LEN - 3)
    assert checksum_ok(masked)
    assert masked[-1] != plain[-1]  # no longer the XOR of the secret
    # short/odd lengths: header kept, rest blanked, no checksum fabricated
    assert redact.mask_secret(b"\x33\xb2\x01\x02") == b"\x33\xb2\xff\xff"
    # a reader exception: its type only for a secret frame, its whole repr for any other
    err = ValueError("got b'\\x01'")
    assert redact.shown_error(plain, err) == "ValueError"
    assert redact.shown_error(frame(0xAA, 0x01, 0x01), err) == repr(err)


_FULL_B1 = frame(0xAA, 0xB1, 0x01, *_SECRET)


def _describe_rx(f: bytes) -> object:
    return describe.describe_frame(f, "rx")


@pytest.mark.parametrize(("decode", "secret_frame", "masked", "reader_raises"), [
    # an aa b1 reply too short for secret_state: logged by parse._reply as a frame that didn't parse
    (parse.parse_secret, bytes([0xAA, 0xB1, 0x01, *_SECRET[:5]]), "aab1" + "ff" * 6, False),
    # a 33 b2 too short for a write ack (2-3 bytes): logged by parse._reply the same way
    (parse.parse_secret, bytes([0x33, 0xB2, _SECRET[0]]), "33b2ff", False),
    # a 33 b2 secret write handed to request_command: its ValueError names the frame
    (parse.request_command, build.secret_check(_SECRET), "33b2" + "ff" * 17 + "7e", False),
    # a reader exception that quotes the field it read (a `valid` failure): only its type is shown
    (parse.parse_secret, _FULL_B1, "aab1" + "ff" * 17 + "e4", True),
    (_describe_rx, _FULL_B1, None, True),
])
def test_a_secret_frame_is_logged_and_raised_masked(
    decode: Callable[[bytes], Any], secret_frame: bytes, masked: str | None, reader_raises: bool,
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def leaky_read(self: Any) -> None:
        raise ValidationNotEqualError(bytes(8), _SECRET, None, "/types/secret_state/seq/1")

    if reader_raises:
        monkeypatch.setattr(_spec.State.SecretState, "_read", leaky_read)
    caplog.set_level(logging.DEBUG, logger="govee_ble_local")
    try:
        shown = repr(decode(secret_frame))
    except ValueError as err:
        shown = str(err)
    shown += caplog.text
    # exactly the masked frame, wherever a secret frame's hex appears; no 2 secret bytes, as hex or repr
    assert set(re.findall(r"\b(?:aab1|33b2)[0-9a-f]*", shown)) == ({masked} if masked else set())
    pairs = [_SECRET[i:i + 2] for i in range(len(_SECRET) - 1)]
    as_repr = [rf"\\+x{p[0]:02x}\\+x{p[1]:02x}" for p in pairs]   # \xde\xad, escaped once or more
    assert not any(p.hex() in shown for p in pairs) and not any(re.search(r, shown) for r in as_repr), shown
