"""Protocol constants for Govee BLE devices.

Values here were reverse-engineered from the Govee H60A6 (Ceiling Light Pro);
they are expected to hold for closely related BLE models but have only been
verified on the H60A6 so far.
"""
from __future__ import annotations

# GATT characteristics used for control (write) and responses (notify).
WRITE_CHAR_UUID = "00010203-0405-0607-0809-0a0b0c0d2b11"
NOTIFY_CHAR_UUID = "00010203-0405-0607-0809-0a0b0c0d2b10"

# Pre-shared key for the handshake's AES/RC4 layer.
PSK = b"MakingLifeSmarte"

# Zone identifiers for two-zone fixtures (upper ring / lower panel).
ZONE_LOWER = 0
ZONE_UPPER = 1

# Confirmed from real captured "max warmth" / "max cool" commands.
MIN_COLOR_TEMP_KELVIN = 2700
MAX_COLOR_TEMP_KELVIN = 6500

# Individually-addressable segments (bitmask/record indices 0-11).
SEGMENT_COUNT = 12

# --- connection / timing tunables ---
DISCONNECT_DELAY = 2  # seconds of inactivity before dropping the BLE connection
CONNECT_MAX_ATTEMPTS = 4
STATUS_CHUNK_TIMEOUT = 2  # seconds to wait for each status chunk
METADATA_FIELD_TIMEOUT = 2  # seconds to wait for each `ab` metadata field chunk

# Status-response chunk tags. The short status query returns 0x00-0x04 + 0xFF;
# the (unused) fuller query additionally returns 0x05-0x08 with per-segment
# data. REQUIRED gates a successful read; ACCEPTED is what we store.
STATUS_CHUNK_REQUIRED = (0x00, 0x01, 0x02, 0x03, 0x04, 0xFF)
STATUS_CHUNK_ACCEPTED = STATUS_CHUNK_REQUIRED
# The fuller query additionally returns per-segment chunks 0x05-0x08. It's a
# longer, drop-prone notification burst, so it's opt-in (get_status
# with_segments=True) and best-effort — segment data may be missing on a poll.
STATUS_CHUNK_ACCEPTED_FULL = STATUS_CHUNK_REQUIRED + (0x05, 0x06, 0x07, 0x08)
