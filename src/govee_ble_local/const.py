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
HANDSHAKE_TIMEOUT: Final = 10.0
# The device answers the e7 02 confirm within ~0.1 s (and retransmits it ~1.1 s
# later). Waiting for the first copy before sending anything else matters: a
# query fired while the confirm is still in flight loses the tail of its reply.
HANDSHAKE_STEP2_TIMEOUT: Final = 1.0
# Late copies of the handshake frames keep arriving for ~1.2 s after it; frames
# matching them are dropped instead of being mistaken for replies.
HANDSHAKE_ECHO_WINDOW: Final = 2.0
COMMAND_ACK_TIMEOUT: Final = 3.0
IDLE_DISCONNECT_DELAY: Final = 8.0
# After a user command (a 0x33 write) keep the connection warm this long so rapid
# sequential changes are responsive (no reconnect handshake each time). Routine
# polls/reads use the shorter base delay above, so idle devices still release the
# adapter's scarce slots quickly.
ACTIVE_IDLE_DELAY: Final = 30.0
CONNECT_MAX_ATTEMPTS: Final = 3
# A fresh link whose session setup (BGC read / handshake / unlock) fails is torn
# down and re-established this many times in total before giving up.
SESSION_ATTEMPTS: Final = 2
# Write-ACK (mirrors the app's ControllerComm/AbsSingleController): a command's
# reply must match [0x33, commandType] and succeed with byte[2] == 0. The write is
# retried on the same link, then once more on a freshly re-established link (a
# link that stops acking is usually half-dead: bleak still says "connected").
WRITE_ACK_TIMEOUT: Final = 1.5   # per attempt
WRITE_ATTEMPTS: Final = 2        # per link
# Hard ceiling on one public operation (connect + handshake + I/O + retries), so a
# wedged adapter/proxy can't hold the device lock (and the caller) for minutes.
OPERATION_TIMEOUT: Final = 45.0
# A multi-frame read (0xAC status burst) is considered complete once the device has
# gone quiet this long after its last frame, even if the 0xFF terminator was lost.
BURST_IDLE_GAP: Final = 0.6

# --- Named magic numbers (adopted by device/connection in later tasks) ------
# Device.update(): 0xAC status-burst read overall timeout (device.py query(..., timeout=5.0)).
STATUS_READ_TIMEOUT: Final = 5.0
# Device single-reply read (_read_reply -> query(..., timeout=2.0)).
INFO_READ_TIMEOUT: Final = 2.0
# Connection.query() default overall timeout (connection.py `timeout: float = 3.0`).
QUERY_DEFAULT_TIMEOUT: Final = 3.0
# Connection.send_unacked() default inter-frame gap (connection.py `gap: float = 0.03`).
UNACKED_SEND_GAP: Final = 0.03
# BGC info characteristic: first byte values accepted as an encryptVersion
# (connection.py `data[0] in (1, 2)`).
BGC_VERSION_VALUES: Final = (1, 2)
# 0xA3 scene upload framing: each frame carries 17 stream bytes after the seq byte,
# and the stream starts with a 3-byte header [marker, packet_count, comm_byte].
SCENE_A3_PIECE_LEN: Final = 17
SCENE_A3_HEADER_LEN: Final = 3
