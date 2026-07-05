"""20-byte frame construction — port of ``BleUtils.generate20Bytes`` + BCC.

A Govee command frame is: ``[pro_type, cmd, *payload, <zero pad>, checksum]``
where checksum is the XOR of bytes[0:19] (``BleUtils.v``/getBCC).

``pro_type`` distinguishes the operation class (from BleConstants):
  0x33 SINGLE_WRITE   0xAA SINGLE_READ   0xA1/0xA2 MULTIPLE write/read   0xEE NOTIFY
``cmd`` is the sub-command within that class.
"""
from __future__ import annotations

from ..const import FRAME_LEN
from ..crypto import checksum

# Protocol (pro_type) opcodes — BleConstants.
PRO_WRITE = 0x33       # SINGLE_WRITE
PRO_READ = 0xAA        # SINGLE_READ
PRO_MULTI_WRITE = 0xA1  # MULTIPLE_WRITE
PRO_MULTI_READ = 0xA2   # MULTIPLE_READ
PRO_NOTIFY = 0xEE       # NOTIFY


def build_frame(pro_type: int, cmd: int, payload: bytes = b"") -> bytes:
    """Assemble one 20-byte plaintext frame with trailing XOR checksum."""
    if len(payload) > FRAME_LEN - 3:  # 2 header bytes + 1 checksum
        raise ValueError(f"payload too long: {len(payload)} > {FRAME_LEN - 3}")
    body = bytearray(FRAME_LEN)
    body[0] = pro_type
    body[1] = cmd
    body[2 : 2 + len(payload)] = payload
    body[19] = checksum(bytes(body[:19]))
    return bytes(body)


def split_frame(frame20: bytes) -> tuple[int, int, bytes]:
    """Inverse of build_frame: return (pro_type, cmd, payload[2:19])."""
    return frame20[0], frame20[1], frame20[2:19]
