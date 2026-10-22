"""Constants shared across the library.

Values are ported from the decompiled Govee Home app (v7.5.20). See
spec/docs/GOVEE_BLE_GATT_PROTOCOL.md for the full protocol write-up.
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
# The app actually matches ihoment_/Govee_/Minger_ ANYWHERE in the name; identify()
# follows it exactly (identify.sku_from_local_name). This tuple is the prefix
# form for passive matchers (e.g. a Home Assistant manifest).
# BaseBleProcessor.parse: brand markers matched ANYWHERE in the name (String.contains) ...
BRAND_MARKERS: Final = ("ihoment_", "Govee_", "Minger_")
# ... then these prefixes (startsWith), tested in this order after the markers.
GBK_PREFIX: Final = "GBK_"
GV_LONG_PREFIXES: Final = ("GVH", "GVR")
GV_PREFIX: Final = "GV"
LOCAL_NAME_PREFIXES: Final = (*BRAND_MARKERS, *GV_LONG_PREFIXES, GV_PREFIX, GBK_PREFIX)

# Govee's BLE manufacturer (company) IDs seen in advertisements. Used as an
# additional passive-scan filter / Home Assistant manifest matcher. The id is
# 0x88 << 8 | header: the header's low nibble is the broadcast version and bit 0x40
# the encrypt flag, and the app accepts every version >= 1 with or without it
# (BleUtil.parseBleBroadcastPact). Listed: plaintext v1..v3 and the encrypted
# counterparts of the same versions.
MANUFACTURER_IDS: Final = (0x8801, 0x8802, 0x8803, 0x8841, 0x8842, 0x8843)

# --- Timeouts (seconds) ----------------------------------------------------
HANDSHAKE_TIMEOUT: Final = 10.0
# The device answers the e7 02 confirm within ~0.1 s (and retransmits it ~1.1 s
# later). Waiting for the first copy before sending anything else matters: a
# query fired while the confirm is still in flight loses the tail of its reply.
HANDSHAKE_STEP2_TIMEOUT: Final = 1.0
# Late copies of the handshake frames keep arriving for ~1.2 s after it; frames
# matching them are dropped instead of being mistaken for replies.
HANDSHAKE_ECHO_WINDOW: Final = 2.0
IDLE_DISCONNECT_DELAY: Final = 8.0
# After a user command (a 0x33 write) keep the connection warm this long so rapid
# sequential changes are responsive (no reconnect handshake each time). Routine
# polls/reads use the shorter base delay above, so idle devices still release the
# adapter's scarce slots quickly.
ACTIVE_IDLE_DELAY: Final = 30.0
# While the link is up the app's control screen sends a keep-alive read every 2000 ms (AbsBle.F; the
# ctlchannel plug screen's BleClientProxy.Builder f; devices.yaml timing.heartbeat.interval_ms), and the
# firmware drops a link that has had no write for 10-11 s. GoveeConnection sends the profile's heartbeat at
# this interval; it never extends the link past the idle delays above.
HEARTBEAT_INTERVAL: Final = 2.0
# Bound on one heartbeat's write, so a wedged proxy write can't hold the device lock (a heartbeat is not an
# operation: OPERATION_TIMEOUT doesn't cover it). The app waits for no ack or reply either.
HEARTBEAT_WRITE_TIMEOUT: Final = 1.0
# The device-list card's session (a ``session: per_command`` row, GoveeConnection.send_once): the next session to
# a device starts at least this long after the last one's release (CardCommManagerProxy.s = 1100 ms; devices.yaml
# timing.per_command.gap_ms).
PER_COMMAND_SESSION_GAP: Final = 1.1
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
# Upper bound on client.disconnect(): a wedged proxy must not hold the device lock forever.
DISCONNECT_TIMEOUT: Final = 5.0
# A multi-frame read (0xAC status burst) is considered complete once the device has
# gone quiet this long after its last frame, even if the 0xFF terminator was lost.
BURST_IDLE_GAP: Final = 0.6
# After a command or an authoritative read, Device ignores the advertised on/off for
# this long: Govee refreshes the broadcast on/off byte lazily, so a stale advertisement
# arriving right after a toggle would otherwise flip the state straight back.
ADVERT_SUPPRESS_SECONDS: Final = 15.0
# How long a DeviceState.zone_power_confirmed stamp counts as fresh where the library itself relies on one: a
# bar-switch device's 33 36 carries both bars, so Device._set_bar_power reads them (aa 36) before it unless
# the other bar was confirmed at most this long ago. The app builds its 33 36 from bars its aa 36 heartbeat
# keeps current (h6047 NewDetailVm.D5 / A3). 120 s is the bound the Home Assistant integration uses for its
# own "zone confirmed lit" check.
ZONE_POWER_FRESH_SECONDS: Final = 120.0

# --- Read/query timing and framing ----------------------------------------
# Overall timeout for the 0xAC status-burst read (Device._query_status).
STATUS_READ_TIMEOUT: Final = 5.0
# Timeout for a single-reply read (Device._read_reply).
INFO_READ_TIMEOUT: Final = 2.0
# Default overall timeout of GoveeConnection.query().
QUERY_DEFAULT_TIMEOUT: Final = 3.0
# GoveeConnection.query()'s default terminal: byte 1 of the final frame of a 0xAC burst (the app's
# "last packet" marker, AbsController4BleSingleSendMultiBack.isSameController: bytes[1] == -1).
QUERY_TERMINAL: Final = 0xFF
# Default inter-frame gap of GoveeConnection.send_unacked().
UNACKED_SEND_GAP: Final = 0.03
# BGC info characteristic: first-byte values accepted as an encryptVersion.
BGC_VERSION_VALUES: Final = (1, 2)
# 0xA3 scene upload framing: each frame carries 17 stream bytes after the seq byte,
# and the stream starts with a 3-byte header [marker, packet_count, comm_byte].
SCENE_A3_PIECE_LEN: Final = 17
SCENE_A3_HEADER_LEN: Final = 3
