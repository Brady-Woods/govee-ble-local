"""Constants shared across the library.

Values are ported from the decompiled Govee Home app (v7.5.20). See
PROTOCOL.md for the full protocol write-up.
"""
from __future__ import annotations

from typing import Final

# --- GATT ------------------------------------------------------------------
# Govee's control service + characteristics. Write commands to WRITE_CHAR,
# receive notifications on NOTIFY_CHAR. (com.govee.h5080.ble.BleComm etc.)
SERVICE_UUID: Final = "00010203-0405-0607-0809-0a0b0c0d1910"
WRITE_CHAR_UUID: Final = "00010203-0405-0607-0809-0a0b0c0d2b11"
NOTIFY_CHAR_UUID: Final = "00010203-0405-0607-0809-0a0b0c0d2b10"
# "BGC info" characteristic (Constants.b()): read at connect to learn the
# device's encryptVersion (0=plaintext, 1=AES-RC4-PSK, 2=AES-GCM).
BGC_INFO_CHAR_UUID: Final = "00010203-0405-0607-0809-0a0b0c0d2b12"

# --- Encryption ------------------------------------------------------------
# The pre-shared key used for the e7 handshake (AES-ECB + RC4). In the app
# this is LibTools.c() -> parseHexStr2Byte(<decoded resource>), which resolves
# to these 16 ASCII bytes. Confirmed live: decrypting real handshakes with
# this key yields valid e7 frames, and using it end-to-end controls devices.
PSK: Final = b"MakingLifeSmarte"

FRAME_LEN: Final = 20  # every Govee BLE frame is exactly 20 bytes

# --- Advertisement identification ------------------------------------------
# A BLE device is a candidate Govee device if its advertised local name starts
# with one of these prefixes (com.govee.base2home.main.choose.BaseBleProcessor).
LOCAL_NAME_PREFIXES: Final = (
    "ihoment_",
    "Govee_",
    "Minger_",
    "GVH",
    "GVR",
    "GV",
    "GBK_",
)

# Govee's BLE manufacturer (company) IDs seen in advertisements. Used as an
# additional passive-scan filter / Home Assistant manifest matcher.
MANUFACTURER_IDS: Final = (0x8801, 0x8802, 0x8803, 0x8843)

# --- Timeouts (seconds) ----------------------------------------------------
CONNECT_TIMEOUT: Final = 20.0
HANDSHAKE_TIMEOUT: Final = 10.0
COMMAND_ACK_TIMEOUT: Final = 3.0
IDLE_DISCONNECT_DELAY: Final = 8.0
# After a user command (a 0x33 write) keep the connection warm this long so rapid
# sequential changes are responsive (no reconnect handshake each time). Routine
# polls/reads use the shorter base delay above, so idle devices still release the
# adapter's scarce slots quickly. Dial down if slot contention appears.
ACTIVE_IDLE_DELAY: Final = 30.0
CONNECT_MAX_ATTEMPTS: Final = 4
# Write-ACK (mirrors the app's ControllerComm/AbsSingleController): a command's
# reply must match [0x33, commandType] and succeed with byte[2] == 0. The write
# is retried within a ~6 s budget (the app's write timeout).
WRITE_ACK_TIMEOUT: Final = 2.0   # per attempt
WRITE_ATTEMPTS: Final = 3
