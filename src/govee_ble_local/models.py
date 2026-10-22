"""Public data types: capabilities and device state."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Capability(StrEnum):
    """A feature a device supports. Drives Home Assistant entity creation."""

    POWER = "power"
    BRIGHTNESS = "brightness"
    RGB = "rgb"
    COLOR_TEMP = "color_temp"
    SEGMENTS = "segments"
    SCENES = "scenes"


class Encryption(StrEnum):
    """How a device secures its command channel.

    Per the app (BleUtil advertisement `encrypt` flag + BgcInfo `encryptVersion`)
    this is binary at heart — encrypted or not — with two encrypted variants:

    - NONE: encrypt flag clear -> no handshake, plaintext frames (H6006, H6052,
      H61A8, ...).
    - AES_RC4_PSK: encrypt flag set, BgcInfo version 1 (or no BgcInfo) -> e7 01
      handshake, session key, AES-ECB+RC4 per frame (H60A6, H5083, ...).
    - AES_GCM: encrypt flag set, BgcInfo version 2 -> e7 1a handshake, AES-GCM
      (Controller4AesGcm). Newer devices.

    (There is no "handshake-only" mode — a device either uses the encrypted
    channel or sends plaintext.)
    """

    NONE = "none"
    AES_RC4_PSK = "aes_rc4_psk"
    AES_GCM = "aes_gcm"


@dataclass(frozen=True)
class Segment:
    """One addressable segment of a multi-segment device."""

    index: int
    rgb: tuple[int, int, int] | None = None
    brightness: int | None = None  # 0..100


@dataclass(frozen=True)
class Zone:
    """A named physical zone of a device (e.g. H60A6's ring / panel).

    `power_index` addresses the zone-on/off command (33 30 <index>);
    `segments` lists the segment bit-indices that make up this zone (for
    zone-level color via the segment mask).
    """

    name: str
    power_index: int
    segments: tuple[int, ...] = ()


@dataclass
class DeviceState:
    """Best-known device state. Fields are None when unknown/unsupported.

    Devices with no status read-back (e.g. the plug family) track state
    optimistically: it reflects the last command we sent, not a device read.
    """

    is_on: bool | None = None
    brightness: int | None = None            # 0..100
    rgb_color: tuple[int, int, int] | None = None
    color_temp_kelvin: int | None = None
    segments: list[Segment] = field(default_factory=list)
    scene_code: int | None = None            # active scene code, if in scene mode
    gradual: bool | None = None              # 0xA3 gradual/fade-on-handoff flag (gradual-capable SKUs)
    # Per-zone power, keyed by the zone's power_index (e.g. H60A6 0=main, 1=background).
    zone_power: dict[int, bool] = field(default_factory=dict)
    # Per-zone colour temperature in Kelvin, keyed like ``zone_power``; a zone is absent when it is
    # not (known to be) in colour-temperature mode. Only devices that report it (H6047, whose
    # ``aa 05 15`` reply carries one kelvin per bar) fill it. ``color_temp_kelvin`` is the
    # whole-device value and is set only while every zone agrees.
    zone_color_temp_kelvin: dict[int, int] = field(default_factory=dict)
    # A Device's state starts with optimistic True; the first read that brings a live field back clears it for
    # good, and a later poll that reads nothing leaves it False. Device.power_read_at says when a read last
    # returned the on/off.
    optimistic: bool = False
    # Device-info (static-ish), populated where the device supports read-back.
    wifi_mac: str | None = None
    hardware_version: str | None = None
    firmware_version: str | None = None
    serial_number: str | None = None         # the device id (aa 07 02), same value as `device_id`
    # A device-reported BLE MAC, IFF a device-info reply carries one distinct from the
    # connectable address (relevant only for rotating/random advertised addresses). No
    # curated SKU is confirmed to report this yet — stays None; the connectable address
    # is otherwise the Bluetooth MAC.
    ble_mac: str | None = None
    # The colour temperature the device last reported or was set to (> 0), kept while it is in
    # another mode, as the app keeps its Kelvin: SubMode4Color / Info4Detail take a report's Kelvin
    # only when it is > 0 (base OpInfo4Detail.parseModeValidBytes:766-778, pact_h60a0 h60a1
    # Info4Detail.Q:399-406 / :416-427), and H6047 its bars' only when they match (h6047 detail
    # Info4Detail ctor strategy :173-185). ``color_temp_kelvin`` is set only while the device is in
    # colour-temperature mode (where the report carries no Kelvin, the H61A8's
    # ``DeviceProfile.mode_reports_kelvin``, the Kelvin last sent stays until an RGB or scene write or a
    # scene report); this one is never cleared by a report.
    last_color_temp_kelvin: int | None = None
    # Last, so the positional order of the fields before it stays as it was.
    zone_power_confirmed: dict[int, float] = field(default_factory=dict, compare=False)
    """When the device last told the library each zone's power: ``time.monotonic()`` seconds, keyed like
    ``zone_power``. An entry vouches that ``zone_power`` still holds the value the device gave at that
    time; a zone without one holds a value the library assumed, or none.

    Stamped only by the device: the 0xAC status read's TLV 0x30 (each zone it carries, with two
    exceptions below), a bar-switch device's ``aa 36`` read (both bars), and an acked write whose frame
    sets the zone: ``33 30`` its own zone only (the other zone's power is carried along: it keeps the
    stamp it had and gets no new one), ``33 36`` both bars, and a whole-device power write whose frames set
    every zone (one ``33 30`` per zone; the ``33 01`` of a ``DeviceProfile.global_power`` fixture; an
    H6047's ``33 01`` off; see ``Device._power_zones``). Never by the model: a scene activation, the zones
    a power write assumes, and an advertisement stamp nothing.

    TLV 0x30 does not stamp, and the zone loses the stamp it had while taking the reported power:

    - a zone it reports lit in a burst whose TLV 0x01 does not say on (an "off" still stamps): the device
      says it is off (H60A6: TLV 0x01 is the main switch flag), so the zone's flag does not say it is lit;
    - on an H60A6, a zone a ``33 01`` / ``33 30`` (or a ``33 04`` wake) set since the last reply that
      carried it, unless the reply reports the one power those writes can leave it at (the last acked
      ``33 01`` / ``33 30``'s, and that of each later write that raised, or may not have posted: a ``33 04``
      wake). FIRMWARE-SOURCED: the dispatcher acks those once it has posted them to its message thread,
      which applies them later, in order, and the 0xAC reply is built from the flags as they are, so the
      reply may predate them (see ``Device._read_status``). The next reply stamps the zone.

    Any other change to a zone's value drops its stamp, and so does forgetting the zone (its key leaving
    ``zone_power``): an advertisement's "off" over a zone confirmed on, and an advertisement's "on" over
    zones that all read off forgets them, as an H6047 ``33 01`` on forgets the bars. A zone whose value such
    a change leaves as it was keeps its stamp. A scene activation, acked or not, drops every zone's stamp:
    what it does to the zone flags is not traced (H60A6 ``FUN_9b025124``, H6047 handler ``0x01005510``).

    A stamp also goes, with ``zone_power`` left as it was, when the device may hold another power for the
    zone than the one held:

    - a power write that raises, since it may have reached the device (a timeout, a cancellation, a lost
      link, even a refusal: the transport writes a frame on every attempt). Every zone it targets loses its
      stamp, whatever power it is held at: a ``33 30`` its own zone, a ``33 36`` both bars, and a
      whole-device power write or a scene activation every zone;
    - a write that wakes the device while it is not known to be on, which lights every zone (an H60A6
      ``33 04`` / ``33 05``, an H6047 ``33 04`` / ``33 05``; see ``Device._wake_power``): a zone held off
      loses its stamp. ``is_on`` is not trusted to rule a wake out after a write that may have switched the
      device off raised; after an acked zone-off over some zones (an H60A6 ``33 30``), unless the device
      was known to be on and every other zone is held lit with a stamp, since a zone-off that leaves no
      zone lit also switches the main switch off (``FUN_9b0278dc`` @0x9b027972-0x9b027986); and after a
      status reply that came before an H60A6 power write applied (above). A read issued after the last of
      these, or an acked write over every zone, gives the on/off again. On an H60A6 a write still counts as
      a wake while a ``33 01`` on or ``33 30`` zone-on is posted and no reply has carried it: the
      dispatcher checks the main switch as the message thread has left it;
    - an H6047 ``aa 01`` reply (bar 0 | bar 1) that the bars' held power contradicts;
    - an acked ``33 01`` off on a bar-switch device without profile zones (H6042): no firmware in hand says
      what it leaves the bars its ``aa 36`` read at.

    The library reads the stamps in two places: an H6047 bar write (``33 36`` carries both bars) reads the
    bars first unless the other bar was confirmed at most ``const.ZONE_POWER_FRESH_SECONDS`` ago (the only
    place a stamp changes a frame), and an acked H60A6 zone-off keeps ``is_on`` trusted to rule a wake out
    only while every other zone is held lit with a stamp (``Device._commit``, ``_others_confirmed_lit``). So
    a stamp a consumer sets or keeps changes both.

    ``Device.snapshot()`` copies it. It is left out of equality, so a read that re-confirms the same power
    leaves the state equal to a snapshot taken before it. ``dataclasses.asdict()`` and ``repr()`` still
    include it, and a read that re-confirms a zone gives it a new time, so a consumer that diffs those to
    detect a change should drop it. ``dataclasses.replace(state, zone_power=...)`` carries the same
    ``zone_power_confirmed`` dict (the same object) into the copy, still vouching for the old values: a
    consumer that replaces ``zone_power`` should drop the stamps too (``zone_power_confirmed={}``)."""

    @property
    def device_id(self) -> str | None:
        """The device id the Govee app calls ``device``; an alias of ``serial_number``.

        E.g. ``2D:DB:5C:E7:53:F4:74:56``, read with ``aa 07 02``."""
        return self.serial_number
