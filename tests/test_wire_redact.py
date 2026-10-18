"""wire.redact: the account-lock secret never reaches a log line, an exception message or a capture in the
clear, even from a frame whose body doesn't parse (the mask matches the header bytes alone)."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import pytest

from govee_ble_local.const import FRAME_LEN
from govee_ble_local.crypto import checksum_ok
from govee_ble_local.wire import build, parse, redact

from fake_ble import frame

_SECRET = bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x12, 0x34, 0x56, 0x78])


def test_mask_helper_recomputes_checksum() -> None:
    plain = frame(0x33, 0xB2, *_SECRET)
    masked = redact.mask_secret(plain)
    assert len(masked) == FRAME_LEN
    assert masked[:2] == plain[:2] and masked[2:-1] == b"\xff" * (FRAME_LEN - 3)
    assert checksum_ok(masked)
    assert masked[-1] != plain[-1]  # no longer the XOR of the secret
    # short/odd lengths: header kept, rest blanked, no checksum fabricated
    assert redact.mask_secret(b"\x33\xb2\x01\x02") == b"\x33\xb2\xff\xff"


@pytest.mark.parametrize(("decode", "secret_frame", "header"), [
    # an aa b1 reply too short for secret_state: logged by parse._reply as a frame that didn't parse
    (parse.parse_secret, bytes([0xAA, 0xB1, 0x01, *_SECRET[:5]]), "aab1"),
    # a 33 b2 secret write handed to request_command: its ValueError names the frame
    (parse.request_command, build.secret_check(_SECRET), "33b2"),
])
def test_a_secret_frame_is_logged_and_raised_masked(
    decode: Callable[[bytes], Any], secret_frame: bytes, header: str, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="govee_ble_local")
    try:
        decode(secret_frame)
        raised = ""
    except ValueError as err:
        raised = str(err)
    shown = caplog.text + raised
    assert _SECRET[:4].hex() not in shown and _SECRET[1:5].hex() not in shown
    assert f"{header}ffff" in shown   # the masked frame: its header, then blanks
