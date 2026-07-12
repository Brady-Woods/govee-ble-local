"""Frame builders — the write side, organized by the ksy command/sub-mode/op15 enums.

Kaitai generates readers only, so these encode the layouts by hand; every builder is
round-trip-proven against the generated reader (see tests/test_wire_build.py: build ->
parse -> assert). Byte layouts are the hardware-verified ones from the spec. Scene
uploads take the raw ``value`` bytes (the device layer base64-decodes + strips the param).
"""
from __future__ import annotations

from typing import Literal

from ..crypto import checksum

FRAME_LEN = 20

# proType (enum pro_type)
PRO_WRITE = 0x33
PRO_READ = 0xAA
PRO_MULTI_A3 = 0xA3
PRO_MULTI_A4 = 0xA4
PRO_MULTI_AC = 0xAC

# command (enum command)
CMD_SWITCH = 0x01
CMD_BRIGHTNESS = 0x04
CMD_MODE = 0x05
CMD_DEVICE_INFO = 0x07
CMD_ZONE = 0x30            # light_direction_or_zone
CMD_BAR_SWITCH = 0x36      # compose_light_switch
CMD_GRADUAL_WIFI_BLE = 0xA3   # gradual/fade on BLE<->wifi handoff (W/R flag)
CMD_SECRET_READ = 0xB1
CMD_SECRET_WRITE = 0xB2
CMD_PLUG_SPEC = 0xB3
CMD_PLUG_SYNC_TIME = 0xB5

# mode (0x05) sub-mode (enum sub_mode)
SUB_SCENE = 0x04
SUB_COLOR_15 = 0x15        # color_rgbic_15 (H60A6/H6047/H6641)
SUB_COLOR_0D = 0x0D        # color_cct_0d   (H6006/H6008/H6052 bulbs)
SUB_COLOR_0B = 0x0B        # color_rgbic_0b (H61A8)

# op15 (enum op15) — sub-op after 0x15
OP15_SET_COLOR = 0x01
OP15_SET_BRIGHTNESS = 0x02

# power payload values
POWER_ON, POWER_OFF = 0x01, 0x00
RELAY_ON, RELAY_OFF = 0x11, 0x10   # plug family

# H60A6 dialect-B DIY/graffiti protocol code; H6052 type-5 professional graffiti.
COMM_H60A6 = 0x58
COMM_H6052_GRAFFITI = 0x09

ColorScheme = Literal["h60a6", "h6006", "h61a8"]


def frame(pro_type: int, *payload: int) -> bytes:
    """A 20-byte frame: [pro_type, *payload, zero-pad, XOR-checksum@19]."""
    if len(payload) > FRAME_LEN - 2:
        raise ValueError(f"payload too long: {len(payload)}")
    buf = bytearray(FRAME_LEN)
    buf[0] = pro_type
    buf[1 : 1 + len(payload)] = bytes(payload)
    buf[19] = checksum(bytes(buf[:19]))
    return bytes(buf)


def _mask_le(mask: int) -> tuple[int, int]:
    return mask & 0xFF, (mask >> 8) & 0xFF


def all_segments_mask(segments: int) -> int:
    """Whole-device mask = every segment bit set (BleUtil select-all-positions)."""
    return (1 << max(0, min(16, segments))) - 1


# ── power / brightness ───────────────────────────────────────────────────────
def switch(on: bool, *, relay: bool = False) -> bytes:
    """33 01 <state>. relay=True for the plug family (0x11/0x10)."""
    val = (RELAY_ON if on else RELAY_OFF) if relay else (POWER_ON if on else POWER_OFF)
    return frame(PRO_WRITE, CMD_SWITCH, val)


def brightness(pct: int) -> bytes:
    """33 04 <level 0-100>."""
    return frame(PRO_WRITE, CMD_BRIGHTNESS, max(0, min(100, pct)))


# ── colour ───────────────────────────────────────────────────────────────────
def color_rgb(r: int, g: int, b: int, scheme: ColorScheme, segments: int = 13) -> bytes:
    """Solid RGB, per-family SubModeColor.getWriteBytes (whole-device = all-segments mask)."""
    lo, hi = _mask_le(all_segments_mask(segments))
    if scheme == "h6006":
        return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_0D, r, g, b, 0, 0, 0, 0, 0)
    if scheme == "h61a8":
        return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_0B, r, g, b, lo, hi)
    return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_15, OP15_SET_COLOR, r, g, b, 0, 0, 0, 0, 0, lo, hi)


def color_temp(kelvin: int, scheme: ColorScheme, segments: int = 13) -> bytes:
    """Whole-device colour temperature (WHITE slot + raw u2be Kelvin). On the 0x15 family
    this is just segment_color_temp with the all-segments mask."""
    if scheme == "h61a8":
        raise ValueError("h61a8 has no colour-temperature capability")
    if scheme == "h6006":
        khi, klo = (kelvin >> 8) & 0xFF, kelvin & 0xFF
        return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_0D, 0xFF, 0xFF, 0xFF, khi, klo, 0, 0, 0)
    return segment_color_temp(all_segments_mask(segments), kelvin, scheme)


def segment_rgb(mask: int, r: int, g: int, b: int, scheme: ColorScheme) -> bytes:
    """Per-segment RGB (mask selects segments); same layout as color_rgb."""
    lo, hi = _mask_le(mask)
    if scheme == "h61a8":
        return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_0B, r, g, b, lo, hi)
    return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_15, OP15_SET_COLOR, r, g, b, 0, 0, 0, 0, 0, lo, hi)


def segment_color_temp(mask: int, kelvin: int, scheme: ColorScheme) -> bytes:
    """Colour temperature on selected segments (mask). Only the 0x15 family embeds a
    segment mask in the CCT frame — h6006's 0x0d has no mask (whole-device only) and
    h61a8 has no CCT at all — so masked CCT is 0x15-only."""
    if scheme == "h61a8":
        raise ValueError("h61a8 has no colour-temperature capability")
    if scheme == "h6006":
        raise ValueError("h6006 colour-temperature is whole-device only (no segment mask)")
    khi, klo = (kelvin >> 8) & 0xFF, kelvin & 0xFF
    lo, hi = _mask_le(mask)
    return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_15, OP15_SET_COLOR,
                 0xFF, 0xFF, 0xFF, khi, klo, 0, 0, 0, lo, hi)


def segment_brightness(mask: int, pct: int, scheme: ColorScheme) -> bytes:
    """Per-segment brightness (0x15 op 0x02); only the 0x15 family supports it."""
    if scheme == "h61a8":
        raise ValueError("h61a8 has no per-segment brightness")
    lo, hi = _mask_le(mask)
    return frame(PRO_WRITE, CMD_MODE, SUB_COLOR_15, OP15_SET_BRIGHTNESS, max(0, min(100, pct)), lo, hi)


# ── zones / bars ─────────────────────────────────────────────────────────────
def zone_power(zone: int, on: bool) -> bytes:
    """33 30 <zoneIndex> <state> (H60A6 per-zone)."""
    return frame(PRO_WRITE, CMD_ZONE, zone & 0xFF, 1 if on else 0)


def bar_switch(left: bool, right: bool) -> bytes:
    """33 36 <left> <right> (H6047 both bars in one frame)."""
    return frame(PRO_WRITE, CMD_BAR_SWITCH, 1 if left else 0, 1 if right else 0)


# ── scenes ───────────────────────────────────────────────────────────────────
def scene_activate(code: int) -> bytes:
    """33 05 04 <code u2le>."""
    return frame(PRO_WRITE, CMD_MODE, SUB_SCENE, code & 0xFF, (code >> 8) & 0xFF)


def scene_upload_a3(value: bytes, comm_byte: int) -> list[bytes]:
    """0xA3 scene/DIY upload (MultipleControllerCommV1): frames of
    [0x01(marker), packet_count, comm_byte, value…], seq 0,1,…, last=0xFF (data-bearing)."""
    pieces = -(-(3 + len(value)) // 17)  # ceil over [marker, count, comm] + value
    stream = bytes([0x01, pieces & 0xFF, comm_byte & 0xFF]) + value
    out: list[bytes] = []
    for i in range(pieces):
        piece = stream[i * 17 : (i + 1) * 17]
        seq = 0xFF if i == pieces - 1 else i
        out.append(frame(PRO_MULTI_A3, seq, *piece))
    return out


def scene_upload_a4_mtu(value: bytes, comm_byte: int, *, mtu: int = 20) -> list[bytes]:
    """0xA4-MTU multi-packet upload (makeSendBytesMtu). START [A4 00 00 01 cntLo cntHi comm
    value×(mtu-8)]; MIDDLE [A4 seqLo seqHi value×(mtu-4)] (1-based); END [A4 FF FF value]
    (data-bearing). Exact-length frames (BCC appended, not zero-padded)."""
    def f(body: bytes) -> bytes:
        return body + bytes([checksum(body)])

    start_cap, mid_cap = mtu - 8, mtu - 4
    head, rest = value[:start_cap], value[start_cap:]
    chunks = [rest[i : i + mid_cap] for i in range(0, len(rest), mid_cap)]
    total = 1 + (len(chunks) if chunks else 1)
    out = [f(bytes([PRO_MULTI_A4, 0x00, 0x00, 0x01, total & 0xFF, (total >> 8) & 0xFF,
                    comm_byte & 0xFF]) + head)]
    if not chunks:
        out.append(f(bytes([PRO_MULTI_A4, 0xFF, 0xFF])))
        return out
    last = len(chunks) - 1
    for idx, chunk in enumerate(chunks):
        if idx == last:
            out.append(f(bytes([PRO_MULTI_A4, 0xFF, 0xFF]) + chunk))
        else:
            seq = idx + 1
            out.append(f(bytes([PRO_MULTI_A4, seq & 0xFF, (seq >> 8) & 0xFF]) + chunk))
    return out


# ── plug / secret ────────────────────────────────────────────────────────────
def plug_sync_time(unix_ts: int, tz_hour: int, tz_min: int) -> bytes:
    """33 B5 <u4be epoch> 01 <s1 tz_hour> <s1 tz_min>."""
    ts = unix_ts & 0xFFFFFFFF
    return frame(PRO_WRITE, CMD_PLUG_SYNC_TIME,
                 (ts >> 24) & 0xFF, (ts >> 16) & 0xFF, (ts >> 8) & 0xFF, ts & 0xFF,
                 0x01, tz_hour & 0xFF, tz_min & 0xFF)


def secret_check(secret: bytes) -> bytes:
    """33 B2 <8-byte account-lock secret>."""
    return frame(PRO_WRITE, CMD_SECRET_WRITE, *secret[:8])


# ── read requests (0xAA / 0xAC) ──────────────────────────────────────────────
def power_query() -> bytes:
    return frame(PRO_READ, CMD_SWITCH, 0x01)


def brightness_query() -> bytes:
    return frame(PRO_READ, CMD_BRIGHTNESS, 0x01)


def mode_query() -> bytes:
    return frame(PRO_READ, CMD_MODE, 0x01)


def bar_switch_query() -> bytes:
    return frame(PRO_READ, CMD_BAR_SWITCH)


def gradual(on: bool) -> bytes:
    """33 A3 <0/1>: gradual/fade on the BLE<->wifi control handoff (GRADUAL_CHANGE_WIFI_BLE)."""
    return frame(PRO_WRITE, CMD_GRADUAL_WIFI_BLE, 1 if on else 0)


def gradual_query() -> bytes:
    """AA A3: read the gradual flag (reply = gradual_read, state @ byte0)."""
    return frame(PRO_READ, CMD_GRADUAL_WIFI_BLE)


def secret_read() -> bytes:
    return frame(PRO_READ, CMD_SECRET_READ)


def plug_spec_query() -> bytes:
    return frame(PRO_READ, CMD_PLUG_SPEC)


CMD_BULB_COLOR_V1 = 0xA2   # bulb_string_color_read (mechanism-B V1: colour only)
CMD_BULB_COLOR_V2 = 0xA5   # local_color_read       (mechanism-B V2: + brightness)


def bulb_group_query(batch_seq: int, *, v2: bool = True) -> bytes:
    """Mechanism-B per-group colour read request: ``AA <A5|A2> <batch_seq>`` (1-based
    batch number, AbsSingleController.p). V2 (0xA5) carries per-segment brightness."""
    return frame(PRO_READ, CMD_BULB_COLOR_V2 if v2 else CMD_BULB_COLOR_V1, batch_seq & 0xFF)


def device_info_query(selector: int) -> bytes:
    """aa 07 <selector> (0x10 basic, 0x11 wifi, 0x02 sn)."""
    return frame(PRO_READ, CMD_DEVICE_INFO, selector & 0xFF)


CMD_IC_NUM = 0x40   # ic_num (ic_segment_read reply): live IC/segment capability read


def ic_count_query() -> bytes:
    """aa 40: read the live IC (lamp-bead) count + device-computed group/segment count
    (ControllerOnlyReadIcSegmentNum). The ONLY live-BLE capability read — lets a client
    discover true segmentation (e.g. H6641's mechanism A-direct group count) instead of
    relying on the static per-SKU table."""
    return frame(PRO_READ, CMD_IC_NUM)


def status_query(full: bool = False) -> bytes:
    """0xAC status request: [AC, 03, N, cmd…]. full = dual-zone (adds 0xA5)."""
    if full:
        return frame(PRO_MULTI_AC, 0x03, 0x03, 0x41, 0x30, 0xA5)
    return frame(PRO_MULTI_AC, 0x03, 0x02, 0x41, 0x30)
