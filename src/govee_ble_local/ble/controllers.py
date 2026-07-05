"""Command builders — one function per device command.

Each returns a 20-byte plaintext frame (the transport encrypts it with the
session key before writing). Sub-command opcodes are from
``com.govee.h5080.ble.BleConstants`` and the shared light controllers.
"""
from __future__ import annotations

from .frame import PRO_READ, PRO_WRITE, build_frame

# Sub-command opcodes (BleConstants + observed protocol).
CMD_POWER = 0x01           # 33 01 <val>
CMD_BRIGHTNESS = 0x04      # 33 04 <pct>   (lights)
CMD_COLOR = 0x05           # 33 05 ...     (lights; layout varies by family)
CMD_STATUS_FIELD = 0x01    # aa 01 ...     (status/heartbeat read)
CMD_SECRET_CHECK = 0xB2    # SINGLE_CHECK_SECRET_KEY (write, pro_type 0x33)
CMD_SECRET_READ = 0xB1     # SINGLE_READ_SECRET_KEY  (read, pro_type 0xAA)
CMD_SYNC_TIME = 0xB5       # SINGLE_SYNC_TIME (plug family; 0x09 on lights)

# Power payload values.
POWER_ON = 0x01
POWER_OFF = 0x00
RELAY_ON = 0x11   # plug_relay family (H5080/H5083...): 33 01 11
RELAY_OFF = 0x10  # plug_relay family:                  33 01 10


def power(on: bool, *, relay: bool = False) -> bytes:
    """Turn the device on/off. `relay=True` for the plug family (0x10/0x11)."""
    if relay:
        val = RELAY_ON if on else RELAY_OFF
    else:
        val = POWER_ON if on else POWER_OFF
    return build_frame(PRO_WRITE, CMD_POWER, bytes([val]))


def sync_time(unix_ts: int) -> bytes:
    """Push wall-clock time: 33 b5 <4-byte big-endian ts> 01 f9.

    The plug family requires this immediately after every power command for
    the relay to actually actuate (confirmed live)."""
    ts = unix_ts & 0xFFFFFFFF
    return build_frame(
        PRO_WRITE,
        CMD_SYNC_TIME,
        bytes([(ts >> 24) & 0xFF, (ts >> 16) & 0xFF, (ts >> 8) & 0xFF, ts & 0xFF, 0x01, 0xF9]),
    )


def secret_read() -> bytes:
    """Read the device's 8-byte secret (only succeeds on an unbound device)."""
    return build_frame(PRO_READ, CMD_SECRET_READ)


def secret_check(secret: bytes) -> bytes:
    """Present the 8-byte secret to unlock command processing (33 b2 <secret>)."""
    if len(secret) != 8:
        raise ValueError(f"secret must be 8 bytes, got {len(secret)}")
    return build_frame(PRO_WRITE, CMD_SECRET_CHECK, secret)


def status_field(field: int = CMD_STATUS_FIELD) -> bytes:
    """Read a status field (aa <field>), e.g. 0x01 = online/heartbeat poll."""
    return build_frame(PRO_READ, field)
