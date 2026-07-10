"""Runtime parsing of inbound frames via the shipped Kaitai reader.

Single-frame ``0xAA`` read replies and ``0xEE`` notifications are decoded through
the spec-generated ``GoveeBleFrame`` reader — the spec is the single source of the
wire layout. Multi-packet ``0xAC``/``0xA1``/``0xA3``/``0xA4`` streams are reassembled
and walked in :mod:`.reassemble` (cross-frame de-chunking isn't expressible in Kaitai).

Values are returned **as the device sends them** (spec-aligned): brightness is the
raw 0-255 byte (percent mapping is a UI concern), and power is the raw byte so plugs
can read it as a relay bitmask while lights treat non-zero as on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .._generated.govee_ble_frame import GoveeBleFrame as _GBF  # type: ignore[attr-defined]

# The generated reader is untyped; treat it as Any so attribute chains don't need stubs.
_F: Any = _GBF
_ProType = _F.ProType
_Command = _F.Command
_NotifySub = _F.NotifySub


def _parse(frame: bytes) -> Any:
    """Parse a 20-byte frame, or return None on any malformed/unknown-enum input."""
    if len(frame) < 2:
        return None
    try:
        return _F.from_bytes(bytes(frame))
    except Exception:  # noqa: BLE001 - unknown enum / truncated frame -> not for us
        return None


def _read_reply(frame: bytes, command: Any) -> Any:
    """Return the typed body of a ``0xAA`` read reply matching ``command``, else None."""
    f = _parse(frame)
    if f is None or f.pro_type != _ProType.read:
        return None
    try:
        if f.body.command != command:
            return None
    except Exception:  # noqa: BLE001
        return None
    return f.body.body


# ── single-command read replies ──────────────────────────────────────────────
def parse_power(frame: bytes) -> int | None:
    """AA 01 reply. Raw byte: lights use non-zero = on; plugs read it as a relay bitmask."""
    b = _read_reply(frame, _Command.switch)
    return None if b is None else int(b.state)


def parse_brightness(frame: bytes) -> int | None:
    """AA 04 reply. Raw 0-255 (no codec rescale; percent mapping is UI-layer)."""
    b = _read_reply(frame, _Command.brightness)
    return None if b is None else int(b.brightness)


def parse_bar_switch(frame: bytes) -> tuple[bool, bool] | None:
    """AA 36 reply: (left, right) bar power."""
    b = _read_reply(frame, _Command.compose_light_switch)
    return None if b is None else (bool(b.left), bool(b.right))


def parse_secret(frame: bytes) -> bytes | None:
    """AA B1 reply: 8-byte account-lock secret (selector 0x01)."""
    b = _read_reply(frame, _Command.secret_read)
    if b is None or int(b.selector) != 0x01:
        return None
    return bytes(b.secret)


def parse_plug_spec(frame: bytes) -> int | None:
    """AA B3 reply: single spec-identifier byte (not the outlet count)."""
    b = _read_reply(frame, _Command.plug_spec)
    return None if b is None else int(b.spec)


# ── mode (0x05) read replies ─────────────────────────────────────────────────
def _mode_read(frame: bytes) -> Any:
    f = _parse(frame)
    if f is None or f.pro_type != _ProType.read:
        return None
    try:
        if f.body.command != _Command.mode:
            return None
    except Exception:  # noqa: BLE001
        return None
    return f.body.body


def parse_active_scene(frame: bytes) -> int | None:
    """AA 05 04 reply: active scene code (u2le), or None if not in scene sub-mode."""
    m = _mode_read(frame)
    if m is None or int(m.selector_or_sub_mode) != 0x04:
        return None
    rest = bytes(m.rest)
    if len(rest) < 2:
        return None
    return rest[0] | (rest[1] << 8)


def parse_kelvin(frame: bytes) -> int | None:
    """AA 05 15 01 reply: colour temperature in Kelvin (u2be), or None."""
    m = _mode_read(frame)
    if m is None or int(m.selector_or_sub_mode) != 0x15:
        return None
    return int(m.rest.kelvin)


# ── device info (AA 07 10/11/02) ─────────────────────────────────────────────
def _version(v: Any) -> str:
    return f"{int(v.major)}.{int(v.minor):02d}.{int(v.patch):02d}"


def _uid_serial(uid: bytes) -> str | None:
    """8-byte UID -> MAC-style colon hex (reversed), leading 00: pairs stripped."""
    s = ":".join(f"{x:02X}" for x in reversed(uid))
    while s.startswith("00:"):
        s = s[3:]
    return s or None


@dataclass(frozen=True)
class DeviceInfo:
    serial: str | None = None
    wifi_mac: str | None = None
    sw_version: str | None = None
    hw_version: str | None = None
    dsp_version: int | None = None


def parse_device_info(frame: bytes) -> DeviceInfo | None:
    """AA 07 reply: basic (0x10), wifi (0x11), or SN (0x02) device info."""
    b = _read_reply(frame, _Command.device_info)
    if b is None:
        return None
    sel = int(b.selector)
    info = b.info
    if sel == 0x10:
        return DeviceInfo(
            serial=_uid_serial(bytes(info.uid)),
            sw_version=_version(info.sw_version),
            hw_version=_version(info.hw_version),
            dsp_version=int(info.dsp_version),
        )
    if sel == 0x11:
        return DeviceInfo(
            wifi_mac=":".join(f"{x:02X}" for x in bytes(info.wifi_mac)),
            sw_version=_version(info.wifi_sw_version),
            hw_version=_version(info.wifi_hw_version),
        )
    if sel == 0x02:
        return DeviceInfo(serial=_uid_serial(bytes(info.uid)))
    return None


# ── notifications (0xEE) ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class Notify:
    sub_type: int
    level: int | None = None                      # 0x20 brightness push (raw 0-255)
    wifi_connected: bool | None = None            # 0x11 (status byte == 0)
    zone_flags: tuple[int, int, int] | None = None  # 0x30 flag bytes (family-interpreted)


def parse_notify(frame: bytes) -> Notify | None:
    """0xEE push. Types the three sub-payloads the curated SKUs emit (0x20/0x11/0x30);
    other (cross-category) sub-types return None."""
    f = _parse(frame)
    if f is None or f.pro_type != _ProType.notify:
        return None
    sub = f.body.sub_type
    d = f.body.data
    if sub == _NotifySub.brightness:
        return Notify(sub_type=0x20, level=int(d.level))
    if sub == _NotifySub.wifi_connect:
        return Notify(sub_type=0x11, wifi_connected=(int(d.status) == 0))
    if sub == _NotifySub.device_info_or_zone:
        return Notify(sub_type=0x30, zone_flags=(int(d.flags_a), int(d.flags_b), int(d.flags_c)))
    return None
