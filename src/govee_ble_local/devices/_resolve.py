"""PROCEDURAL: devices.yaml's supported rows resolved into DeviceProfile facts (standard library only).

The input is ``_generated/devices.py``, which ``tools/gen_devices.py`` projects from ``spec/devices.yaml``:
the families with a ``client_profile.by_goods_type`` row that carries ``support``, typed by the TypedDicts
below. :func:`profiles` gives one field mapping per group of (row, SKU) routes whose resolved facts are
identical, with each route's goodsType and accepted pacts, which ``devices/profile.py`` turns into the
:data:`~.profile.PROFILES` table and its routing index. The generator runs :func:`routes` over the whole
document and the same :func:`profiles` over its projection, every support level, before it writes the
module, so a missing fact, or one the library can't serve, fails the build with :class:`SpecError` and never
an import. Nothing here imports the package, so the generator can load this file on its own.

The spec's resolution (devices.schema.json ``clientRow``, ``spec/govee_reference.py`` ``ble_rows`` /
``client_row``): a row is a BLE route for its models when it has ``support``, ``pacts`` and ``ble`` in its
(else the family's) connectivity. A SKU that several rows list has one route per row; the client picks
among them by the advertised pact, else the goodsType (``profile.profile_for``), and :func:`routes` gives
the SKUs that one row routes. A row's facts are its own, else the family ``client_profile``'s, and the
layered ones (scene_versions, kelvin_range, brightness_range) fall back to the family ``specs``. The wire
facts layer the family ``wire``, ``by_model.<SKU>`` and ``by_goods_type.<gt>``. A profile is resolved with
the pact and the firmware unknown; the pact- and firmware-gated facts the library follows at run time are
named below.

Library rules over the spec's vocabulary (what the library can't serve yet, or how it serves it). A row is
admitted only when every value it holds is one the library implements, and a fact the library needs and
the row lacks fails the build: nothing defaults silently.

- Admission: ``hardware_verified`` and ``app_traced`` rows (:data:`ADMITTED`); a ``modelled`` row
  (:data:`MODELLED`) only where the caller opts in (the user's "app-traced by default"). Every row with
  support is resolved and checked, whatever its level. A row with support that is no BLE route (no models,
  pacts or ble link) raises, and so does an ``assigned_goods_type`` item in any family that names a routed
  SKU (case-insensitively): the library routes by SKU and pact, so it can't follow the app's rewrite of the
  goodsType. A SKU that a ``modelled`` row and an admitted row both list raises too: the spec's routing
  (``ble_rows``) counts both wherever both accept the pact, and the opt-in must leave the admitted routes as
  they are.
- A row's colour scheme drops the capabilities it has no frame for (:data:`SCHEME_LACKS`), but only a
  scheme the row names itself: a family default that would drop one raises. ``rgb``, ``color_temp`` or
  ``segments`` on a relay row raises (the library resolves no colour scheme or Kelvin range for a plug), and
  elsewhere they need a colour scheme. An ``untraced`` scene dialect drops ``scenes``; ``none`` is activate
  only. An ``untraced`` colour parse on a row with ``rgb`` or ``color_temp`` raises (``none`` is a traced
  value: the screen parses no colour).
- The reads the library makes: the 0xAC status read is the row's ``status_read``, in its order
  (``DeviceProfile.status_read``, ``build.status_query(types=)``). Its reply is read as the H60A6 status
  screen reads it (``reassemble.status_profile``), so a requested 0x30 / 0x41 / 0xA5 must have that read's
  wire form (:data:`_STATUS_FORMS`), a requested 0xA5 its groups of :data:`_STATUS_GROUP` records, and the
  row's status mode must be that screen's (:data:`SERVED_MODE`). A pact branch may only drop
  ``color_group``, which becomes ``status_color_groups_pact`` (one pact type, from its lowest code that
  keeps it). A polled row reads aa 04 on every poll and an ``aa a3`` exactly when it has ``gradual``, so
  ``connect_reads`` lists ``brightness`` exactly on a polled row and ``gradual`` exactly with the
  capability. Every command form the library sends or reads for the row must be the row's (wire
  ``forms``), the mode decodes are the ones it reads with (:data:`SERVED_MODE`), and a colour-group read is
  of 4-byte records; the direct ``aa a5`` read walks the row's wire ``group.num_per_group``.
- Wire branches: the library reads the wire with the pact and the firmware unknown, so a wire ``by_pact`` /
  ``by_firmware`` branch raises, but for the unread ones of :data:`_UNREAD_WIRE_BRANCHES` (H60A6 pact (1,
  1)'s one colour piece: decision D3, the library keeps the row's 13).
- Pact branches: ``status_read`` becomes the gate above, and ``scene_versions`` becomes
  ``scene_versions_by_pact``, which ``Device.scene_versions`` applies with the advertised pact before the
  firmware entries (goodsType 19 at pact code 2); ``capabilities`` take the row's own values only on the
  D3 branch, pinned exactly as the wire one is (:data:`_UNREAD_PACT_CAPABILITIES`: H60A6 pact (1, 1) keeps
  the row's capabilities), and raise elsewhere or when that branch changes; a branch on any other fact
  raises.
- Firmware branches resolve after the device-info read (D6): a ``kelvin_range`` item gated on the hardware
  version alone becomes ``kelvin_by_hardware`` (compared as a string, as ``KelvinConfig.Config.z2`` does),
  and a ``scene_versions`` item gated on the soft and / or hard version becomes
  ``scene_versions_by_firmware`` (the goodsType-19 gates, Support.newVersion4Ble). Before the read, each
  version not yet read meets its own gates, so the newest firmware's set applies, as 1.x assumed (H61A8:
  {0, 1, 2, 3}), and the Kelvin range is the one valid on every revision. Any other firmware fact or gate
  raises.
- Plug power read-back: the wire's ``relay_heartbeat_on_mask`` beats the row's ``relay_on_mask``, because
  every aa 01 poll the library makes follows the heartbeat rule (``power_state.is_on_heartbeat``). So for
  goodsTypes 50 and 307 the row's 0xFF (the connect read's) is never consumed, and the library uses their
  heartbeat 0x03, as 1.x did.
- The fallback cipher, used only when no advertisement says (decision D4): AES-RC4 on an account-locked row
  (the plugs, whose firmware encrypts) and, as the 1.x compatibility rule, on H60A6's row
  (:data:`_AES_FALLBACK_ROWS`: its firmware encrypts, and the spec records no cipher per row,
  ``wire_cipher: runtime``); none on every other row. The family ``encryption`` must be a cipher the library
  implements; it is never read as a control-time lock (D9: rows carry ``account_lock``).
"""
from __future__ import annotations

from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from typing import Any, Final, NamedTuple, NoReturn, Required, TypedDict


# ── the projected devices.yaml (devices.schema.json definitions) ────────────────────────────────────
class ZoneRow(TypedDict):
    """``clientRow.zones`` item."""
    id: str
    power_index: int
    segments: tuple[int, ...]


class IcPieces(TypedDict):
    """``clientRow.ic_pieces``."""
    min_ic: int
    max_ic: int
    ic_per_piece: int


class Branch(TypedDict, total=False):
    """``clientBranch``: a pact branch of a row."""
    mode_color_parse: str
    mode_color_parse_status: str
    status_read: tuple[str, ...]
    capabilities: tuple[str, ...]
    connect_reads: tuple[str, ...]
    scene_versions: tuple[int, ...]
    kelvin_range: tuple[int, ...]
    brightness_range: tuple[int, ...]


class FirmwareBranch(TypedDict, total=False):
    """``clientFirmware``: a firmware branch of a row."""
    min_soft_version: str
    min_hard_version: str
    min_wifi_soft_version: str
    mode_color_parse: str
    scene_versions: tuple[int, ...]
    kelvin_range: tuple[int, ...]
    brightness_range: tuple[int, ...]


class Row(TypedDict, total=False):
    """``clientRow``: one goodsType of ``client_profile.by_goods_type``."""
    models: tuple[str, ...]
    connectivity: tuple[str, ...]
    pacts: tuple[str, ...] | str
    support: str
    power_read: str
    power_live: tuple[str, ...]
    mode_color_parse: str
    mode_color_parse_status: str
    relay_power: str
    relay_on_mask: int
    advert_on_mask: int
    color_scheme: str
    color_functions: tuple[str, ...]
    capabilities: tuple[str, ...]
    zones: tuple[ZoneRow, ...]
    account_lock: bool
    scene_dialect: str
    scene_versions: tuple[int, ...]
    kelvin_range: tuple[int, ...]
    brightness_range: tuple[int, ...]
    mode_reports_kelvin: bool
    status_read: tuple[str, ...]
    connect_reads: tuple[str, ...]
    connect_writes: tuple[str, ...]
    power_resync: bool
    ic_pieces: IcPieces
    by_pact: dict[str, Branch]
    by_firmware: tuple[FirmwareBranch, ...]


class ClientModel(TypedDict, total=False):
    """``clientModel``: a per-SKU colour-report override."""
    mode_color_parse: str
    mode_color_parse_status: str
    by_pact: dict[str, Branch]
    by_firmware: tuple[FirmwareBranch, ...]


class WireCounts(TypedDict, total=False):
    """``wireCounts``."""
    num_segments: int
    num_levels: int


class WireGroup(TypedDict, total=False):
    """``wireGroup``."""
    rule: str
    num_per_group: int
    num_pieces: int


class WirePower(TypedDict, total=False):
    """``wirePower``."""
    relay_heartbeat_on_mask: int


class WireMode(TypedDict, total=False):
    """``wireMode``."""
    music: str
    empty_fails: bool


class ModeDecode(TypedDict):
    """A ``wireMode`` with both leaves set: a decode the library reads mode reports with."""
    music: str
    empty_fails: bool


class WireBranch(TypedDict, total=False):
    """``wireBranch``: one override of the govee_profile context."""
    forms: dict[str, str]
    counts: WireCounts
    group: WireGroup
    power: WirePower
    mode: WireMode
    mode_status: WireMode


class WireFirmware(WireBranch, total=False):
    """``wireFirmware``."""
    min_soft_version: str
    min_hard_version: str
    min_wifi_soft_version: str


class WireLayer(WireBranch, total=False):
    """``wireLayer``."""
    by_pact: dict[str, WireBranch]
    by_firmware: tuple[WireFirmware, ...]


class Wire(WireLayer, total=False):
    """``wire``: the family block and its per-SKU and per-goodsType layers."""
    by_goods_type: dict[str, WireLayer]
    by_model: dict[str, WireLayer]


class ClientProfile(TypedDict, total=False):
    """``family.client_profile``."""
    has_brightness: bool
    color_scheme: str
    power_read: str
    power_live: tuple[str, ...]
    mode_color_parse: str
    mode_color_parse_status: str
    relay_power: str
    relay_on_mask: int
    advert_on_mask: int
    by_model: dict[str, ClientModel]
    by_goods_type: dict[str, Row]
    wire: Wire


class Specs(TypedDict, total=False):
    """``family.specs``: the family-wide layered facts."""
    kelvin_range: tuple[int, ...]
    brightness_range: tuple[int, ...]
    scene_versions: tuple[int, ...]


class AssignedGoodsType(TypedDict, total=False):
    """``assignedGoodsType``: SKUs of the family whose goodsType the app rewrites itself."""
    models: tuple[str, ...]
    goods_type: int


class Family(TypedDict, total=False):
    """``family``."""
    id: Required[str]
    connectivity: tuple[str, ...]
    specs: Specs
    encryption: str
    assigned_goods_type: tuple[AssignedGoodsType, ...]
    client_profile: ClientProfile


class ModeColorStrategy(TypedDict):
    """``modeColorStrategy``: colour sub-mode byte (``"0x15"``) -> mode_color_decode kind."""
    sub_modes: dict[str, str]


# ── library rules ────────────────────────────────────────────────────────────────────────────────
class SpecError(ValueError):
    """A supported devices.yaml row lacks a fact the library needs, or holds one it can't serve."""


Fail = Callable[[str], NoReturn]

ADMITTED: Final = frozenset({"hardware_verified", "app_traced"})
MODELLED: Final = frozenset({"modelled"})   # admitted only on the caller's opt-in
LEVELS: Final = ADMITTED | MODELLED
CAPABILITIES: Final = ("power", "brightness", "rgb", "color_temp", "segments", "scenes")   # models.Capability
COLOR_SCHEMES: Final = ("h60a6", "h6006", "h6188", "h61a8", "h6061")   # wire.build.ColorScheme
RELAY_POWERS: Final = ("outlet0", "all_outlets", "total_switch")        # devices.profile.RelayPower
# The capabilities a colour scheme has no frame for (wire.build refuses them), which a row's resolution drops
# (only from a scheme the row names itself):
#  - h61a8 (the 0x0b frames): no colour temperature (build.color_temp / segment_color_temp);
#  - h6006 / h6188 (0x0d): no segment mask in any colour frame (Bytes.f / p / A);
#  - h6061: whole-device only; per-panel colour needs the user's layout, which is not modelled.
SCHEME_LACKS: Final[Mapping[str, frozenset[str]]] = {
    "h61a8": frozenset({"color_temp"}),
    "h6006": frozenset({"segments"}),
    "h6188": frozenset({"segments"}),
    "h6061": frozenset({"segments"}),
}
# Wire branches the library leaves unread on purpose: (family, layer, branch) -> the branch exactly.
_UNREAD_WIRE_BRANCHES: Final[Mapping[tuple[str, str, str], Mapping[str, Any]]] = {
    ("h60ax", "by_goods_type 303", "by_pact 1,1"): {"group": {"num_pieces": 1}},
}
# The mode decodes (wireMode) the library reads a row's mode reports with: the aa 05 reply's is the spec's
# default, every leaf unset (profile.wire_profile passes neither), and the 0xAC TLV 0x05's the H60A6 status
# screen's (wire.reassemble._STATUS_MODE is built from this entry).
_MODE_UNSET: Final[ModeDecode] = {"music": "not_decoded", "empty_fails": False}
SERVED_MODE: Final[Mapping[str, ModeDecode]] = {
    "aa05": _MODE_UNSET, "status": {"music": "v2_default", "empty_fails": True}}
# The family encryption tags the library implements: plaintext, the AES-RC4 PSK channel, and the account
# lock, whose cipher is resolved at connect (aes_gcm_v2 is detected at connect but not implemented).
_CIPHERS: Final = frozenset({"none", "aes_v1", "secret_key_pairing"})
# scenes.upload.dialects -> DeviceProfile.scene_dialect; "none" = activate only.
_DIALECTS: Final = {"legacy_scene": "A", "diy_graffiti": "B_h60a6", "tablelamp_h6052_type5_diy": "B_h6052",
                    "none": "A"}
_READBACK: Final = {"aa01": "polled", "ac_status": "status"}   # power_read -> DeviceProfile.readback
# The 0xAC reply's wire facts as reassemble.status_profile reads them (the H60A6 status screen's): the form of
# each TLV it decodes, and the 0xA5 group size (wire.reassemble._RECORDS_PER_GROUP); its TLV 0x05 decode is
# SERVED_MODE's "status".
_STATUS_FORMS: Final[Mapping[str, tuple[str, str]]] = {
    "cmd_30": ("f_30", "zone_power"), "cmd_41": ("f_41", "power_memory"),
    "color_group": ("f_a5", "color_group")}
_STATUS_GROUP: Final = 4
_READS: Final = frozenset({"brightness", "gradual", "cmd_36", "color_group", "ic_num", "cmd_41"})
_PACT_FACTS: Final = frozenset({"status_read", "scene_versions"})
# Pact branches whose capabilities the library leaves unread: (family, goodsType, pact key) -> the branch's
# capabilities exactly. D3: H60A6 at pact (1, 1) keeps the row's capabilities.
_UNREAD_PACT_CAPABILITIES: Final[Mapping[tuple[str, str, str], tuple[str, ...]]] = {
    ("h60ax", "303", "1,1"): ("power", "brightness", "rgb", "color_temp", "scenes")}
_AES_FALLBACK_ROWS: Final = frozenset({("h60ax", "303")})   # D4: 1.x's fallback, kept for the unlocked H60A6
_LAYERED: Final = ("scene_versions", "kelvin_range", "brightness_range")
_PERCENT: Final = (1, 100)
_BLOCKS: Final = ("forms", "counts", "group", "power", "mode", "mode_status")


def version(text: str | None) -> tuple[int, ...] | None:
    """A firmware version (``1.06.00``) as a tuple compared field by field (``govee_reference._version``);
    None for an unread or unparsable one."""
    try:
        return tuple(int(f) for f in text.split(".")) if text else None
    except ValueError:
        return None


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class Route(NamedTuple):
    """One BLE row's route for one SKU it lists."""
    sku: str
    goods_type: int
    pacts: frozenset[tuple[int, int]] | None
    """The ``(pactType, pactCode)`` pairs the row accepts; None = any (``pacts: any``)."""


def _layer(*layers: Mapping[str, Any]) -> dict[str, Any]:
    """The wire blocks ``layers`` merged in order, a later layer replacing single leaves."""
    out: dict[str, dict[str, Any]] = {}
    for layer in layers:
        for block in _BLOCKS:
            out.setdefault(block, {}).update(layer.get(block) or {})
    return out


def routes(families: Sequence[Family]) -> dict[str, tuple[Family, str, str]]:
    """``{SKU upper-cased: (family, goodsType, SKU)}`` for each SKU that exactly one BLE row with support
    lists, when that row is admitted: the SKUs routed with neither the pact nor the goodsType. SpecError for
    a row with support (any level) that is no BLE route, for a SKU that a ``modelled`` row and an admitted
    row both list (the spec's ``ble_rows`` counts both wherever both accept the pact, so the opt-in would no
    longer be additive), and for a routed SKU that an ``assigned_goods_type`` item in any family names
    (matched as the routes are, case-insensitively). The generator runs it over the whole document."""
    listed: dict[str, list[tuple[Family, str, str, bool]]] = {}
    for fam in families:
        rows = (fam.get("client_profile") or {}).get("by_goods_type") or {}
        for gt, row in rows.items():
            if "support" not in row:
                continue
            if any(not isinstance(m, str) or not m for m in row.get("models") or ()):
                raise SpecError(f"{fam['id']} goodsType {gt}: models must be non-empty SKU strings")
            if not _is_route(fam, row):
                raise SpecError(f"{fam['id']} goodsType {gt}: a row with support needs models, pacts and a "
                                "ble link")
            admitted = row["support"] in ADMITTED
            for sku in row.get("models") or ():
                hits = listed.setdefault(sku.upper(), [])
                other = next((h for h in hits if h[3] != admitted), None)
                if other is not None:
                    raise SpecError(f"{fam['id']} goodsType {gt} ({sku}): also listed by {other[0]['id']} "
                                    f"{other[1]}, a row of the other admission (the modelled opt-in must "
                                    "leave the admitted routes as they are)")
                hits.append((fam, gt, sku, admitted))
    for fam in families:
        for item in fam.get("assigned_goods_type") or ():
            for sku in item.get("models") or ():
                if isinstance(sku, str) and sku.upper() in listed:
                    owner, gt, routed, _admitted = listed[sku.upper()][0]
                    raise SpecError(f"{owner['id']} goodsType {gt} ({routed}): {fam['id']} "
                                    f"assigned_goods_type reassigns it to goodsType {item.get('goods_type')} "
                                    "(the library routes by SKU and pact)")
    return {key: hits[0][:3] for key, hits in listed.items() if len(hits) == 1 and hits[0][3]}


def _is_route(fam: Family, row: Row) -> bool:
    """A row is a BLE route: it lists models and pacts, and ble is in its (else its family's) connectivity."""
    return bool(row.get("models")) and "pacts" in row and "ble" in (
        row.get("connectivity") or fam.get("connectivity") or ())


def _rows(families: Sequence[Family], admitted: Collection[str]) -> Iterator[tuple[Family, str, Row]]:
    """Each BLE row whose support level is in ``admitted``, in devices.yaml order."""
    for fam in families:
        for gt, row in ((fam.get("client_profile") or {}).get("by_goods_type") or {}).items():
            if row.get("support") in admitted and _is_route(fam, row):
                yield fam, gt, row


def profiles(families: Sequence[Family], parse_names: Collection[str], admitted: Collection[str] = ADMITTED
             ) -> list[tuple[tuple[str, ...], dict[str, Any], tuple[Route, ...]]]:
    """``[(skus, fields, routes)]``: DeviceProfile field values for each group of (row, SKU) routes of the
    ``admitted`` rows whose resolved facts are identical, in devices.yaml order (row models order within a
    row), with those routes. ``parse_names`` are the mode_color_parse table's names. SpecError on a fact
    missing or not served."""
    groups: dict[str, tuple[list[str], dict[str, Any], list[Route]]] = {}
    for fam, gt, row in _rows(families, admitted):
        pacts = row.get("pacts", "any")   # every route has pacts (_rows); 'any' tests none
        accepted = None if isinstance(pacts, str) else frozenset(_pact(p) for p in pacts)
        for sku in row.get("models") or ():
            fields = _fields(fam, gt, sku, parse_names)
            skus, _same, members = groups.setdefault(repr(sorted(fields.items())), ([], fields, []))
            if sku not in skus:
                skus.append(sku)
            members.append(Route(sku, int(gt), accepted))
    return [(tuple(skus), fields, tuple(members)) for skus, fields, members in groups.values()]


def _fields(fam: Family, gt: str, sku: str, parse_names: Collection[str]) -> dict[str, Any]:
    """One routed SKU's DeviceProfile field values (see the module doc for the rules)."""
    cp = fam.get("client_profile") or {}
    row = (cp.get("by_goods_type") or {})[gt]
    model = (cp.get("by_model") or {}).get(sku) or {}
    where = f"{fam['id']} goodsType {gt} ({sku})"

    def fail(problem: str) -> NoReturn:
        raise SpecError(f"{where}: {problem}")

    def need(value: Any, name: str) -> Any:
        if value is None:
            fail(f"no {name}")
        return value

    w = _wire(fam, gt, sku, fail)
    forms, counts, group, power = w["forms"], w["counts"], w["group"], w["power"]
    relay = forms.get("f_01") == "relays"
    if model.get("by_pact") or model.get("by_firmware"):
        fail("by_model pact / firmware branches are not served")
    if fam.get("encryption") not in _CIPHERS:
        fail(f"encryption {fam.get('encryption')!r} is not a cipher the library implements")

    # Layered facts: the row's own (its pact and firmware branches resolve apart), else the family specs.
    own: dict[str, object] = dict(row)
    specs: dict[str, object] = dict(fam.get("specs") or {})
    layered: dict[str, Any] = {k: own[k] if k in own else specs[k]
                               for k in _LAYERED if k in own or k in specs}
    kelvin_by_hardware, scenes_by_firmware = _firmware(row, fail)

    status_read = row.get("status_read")
    by_pact = row.get("by_pact") or {}
    for key, branch in by_pact.items():
        pinned = _UNREAD_PACT_CAPABILITIES.get((fam["id"], gt, key))
        unread = ({"capabilities"} if pinned is not None and tuple(branch.get("capabilities") or ()) == pinned
                  else set())
        facts = set(branch) - unread
        if facts - _PACT_FACTS:
            fail(f"pact branch {key} on {sorted(facts - _PACT_FACTS)} is not served")
    scenes_by_pact = tuple(sorted((_pact(key), _scene_set(branch["scene_versions"], fail))
                                  for key, branch in by_pact.items() if "scene_versions" in branch))
    gate = None
    if status_read is not None:
        if not status_read or len(set(status_read)) != len(status_read):
            fail(f"status_read {list(status_read)} (distinct TLV types, at least one)")
        gate = _status_gate(row, tuple(status_read), fail)

    row_caps = frozenset(need(row.get("capabilities"), "capabilities"))
    if not row_caps <= {*CAPABILITIES, "gradual"}:
        fail(f"capabilities {sorted(row_caps - {*CAPABILITIES, 'gradual'})}")
    caps = set(row_caps)
    scheme = row.get("color_scheme") or cp.get("color_scheme")
    if row_caps & {"rgb", "color_temp", "segments"}:
        if relay:
            fail(f"capabilities {sorted(row_caps & {'rgb', 'color_temp', 'segments'})} on a relay row (the "
                 "library resolves no colour scheme or Kelvin range for a plug)")
        need(scheme, "color_scheme")
    if scheme is not None and scheme not in COLOR_SCHEMES:
        fail(f"color_scheme {scheme!r} is not one the library builds")
    lacks = caps & SCHEME_LACKS.get(scheme or "", frozenset())
    if lacks and "color_scheme" not in row:
        fail(f"the family color_scheme {scheme} would drop {sorted(lacks)}: the row must name it")
    caps -= lacks
    cf = row.get("color_functions")
    if cf is not None and (("color_temperature" in cf) != ("color_temp" in row_caps)
                           or ("segment_brightness" in cf) != ("segments" in row_caps)):
        fail(f"color_functions {list(cf)} disagree with capabilities {sorted(row_caps)}")
    dialect = row.get("scene_dialect")
    if "scenes" in caps and dialect not in (*_DIALECTS, "untraced"):
        fail(f"scene_dialect {dialect!r}")
    if dialect == "untraced":
        caps.discard("scenes")
    uploads = "scenes" in caps and dialect != "none"
    scenes = _scene_set(need(layered.get("scene_versions"), "scene_versions"), fail) if uploads else ()
    if "color_temp" in caps:
        need(layered.get("kelvin_range"), "kelvin_range")
    kelvin = (_kelvin(layered["kelvin_range"], "kelvin_range", fail)
              if layered.get("kelvin_range") is not None and not relay else None)
    _check_kelvin_by_hardware(kelvin, kelvin_by_hardware, fail)
    bright = need(layered.get("brightness_range"), "brightness_range") if "brightness" in caps else None
    # NumberUtil.calculateProgress(100) = min + 99 * (max - min + 3) / 100 must be a byte too.
    if bright is not None and not (len(bright) == 2 and all(map(_is_int, bright))
                                   and 0 <= bright[0] < bright[1] <= 0xFF
                                   and bright[0] + 99 * bright[1] + 297 < 0x100 * 100):
        fail(f"brightness_range {list(bright)} is not a raw [min, max] that 100 % maps into a byte")

    readback = "plug"
    if not relay:
        power_read = need(row.get("power_read", cp.get("power_read")), "power_read")
        if power_read not in _READBACK:
            fail(f"power_read {power_read!r}")
        readback = _READBACK[power_read]
        if (readback == "status") != (status_read is not None):
            fail(f"power_read {power_read!r} with status_read {status_read!r}")
    reads = set(need(row.get("connect_reads"), "connect_reads"))
    if not reads <= _READS:
        fail(f"connect_reads {sorted(reads - _READS)} are not served")
    if ("brightness" in reads) != (readback == "polled"):
        fail("connect_reads lists brightness exactly on a polled row (the library reads aa 04 every poll)")
    if ("gradual" in reads) != ("gradual" in caps):
        fail("connect_reads lists gradual exactly with the capability (the library reads aa a3 then)")
    color_readback = ""
    if "color_group" in reads:
        color_readback = {"color_group": "mechanism_a_direct", "bulb_batch_v2": "mechanism_b"}.get(
            forms.get("f_a5", ""), "")
        if not color_readback:
            fail(f"colour read-back with f_a5 {forms.get('f_a5')!r}")
    status_groups = "color_group" in (status_read or ())
    if (color_readback or status_groups) and cp.get("has_brightness") is not True:
        fail("a colour-group read without has_brightness true (the library reads [brightness, r, g, b])")
    # The direct read walks the row's group size (Controller4ColorInfoByGroup takes it from its caller); the
    # status reply's groups are the H60A6 screen's.
    per_group = group.get("num_per_group")
    if color_readback == "mechanism_a_direct" and not (_is_int(per_group) and per_group >= 1):
        fail(f"wire group num_per_group {per_group!r} (the direct aa a5 read walks groups of it)")
    if status_groups and per_group != _STATUS_GROUP:
        fail(f"status_read color_group with wire group num_per_group {per_group!r} (the status reply reads "
             f"groups of {_STATUS_GROUP})")
    live_ic = "ic_num" in reads
    ic = row.get("ic_pieces")
    if live_ic and (color_readback != "mechanism_a_direct" or ic is None):
        fail("an ic_num read needs the direct colour read-back and ic_pieces")
    if ic is not None and not (0 <= ic["min_ic"] <= ic["max_ic"] and ic["ic_per_piece"] >= 1):
        fail(f"ic_pieces {dict(ic)} (min_ic <= max_ic, ic_per_piece >= 1)")
    section_count = "cmd_41" in reads
    if section_count and forms.get("f_41") != "section_count":
        fail("an aa 41 read without f_41 section_count")
    bar = forms.get("f_36") == "bar_pair"
    if ("cmd_36" in reads) != bar:
        fail("the aa 36 read and f_36 bar_pair go together")
    if tuple(need(row.get("connect_writes"), "connect_writes")) != (("plug_sync_time",) if relay else ()):
        fail(f"connect_writes {list(row.get('connect_writes') or ())} (the library sends the plug time "
             "sync, on every relay row only)")
    resync = row.get("power_resync")
    if relay:
        need(resync, "power_resync")
    elif resync:
        fail("power_resync on a light (the library re-syncs a plug's session only)")
    live_served = {"heartbeat_aa01": readback == "polled", "heartbeat_aa36": bar,
                   "push_30": forms.get("f_ee_30", "unknown") != "unknown"}
    for live in row.get("power_live", cp.get("power_live")) or ():
        if not live_served.get(live, False):
            fail(f"power_live {live} is not served (the library polls aa 01, the aa 36 bars or the status "
                 "read, and an ee 30 push needs its wire f_ee_30)")
    _check_mode(w, readback, fail)

    zones = tuple((z["id"], z["power_index"], tuple(z["segments"])) for z in need(row.get("zones"), "zones"))
    lock = bool(need(row.get("account_lock"), "account_lock"))
    out: dict[str, Any] = {
        "capabilities": tuple(c for c in CAPABILITIES if c in caps),
        "aes_fallback": lock or (fam["id"], gt) in _AES_FALLBACK_ROWS,
        "segments": int(counts.get("num_segments", 0)),
        "zones": zones,
        "scene_versions": scenes,
        "scene_versions_by_firmware": tuple(scenes_by_firmware) if uploads else (),
        "scene_versions_by_pact": scenes_by_pact if uploads else (),
        "scene_dialect": _DIALECTS.get(dialect or "none", "A"),
        "min_kelvin": kelvin[0] if kelvin else None,
        "max_kelvin": kelvin[1] if kelvin else None,
        "requires_secret": lock,
        "relay": relay,
        "bar_switch": bar,
        "global_power": bool(zones) and not bar and forms.get("f_01") == "light",
        "gradual": "gradual" in caps,
        "color_readback": color_readback,
        "color_readback_per_batch": int(per_group) if color_readback == "mechanism_a_direct" else 3,
        "color_readback_live_ic": live_ic,
        "section_count_read": section_count,
        "brightness_range": tuple(bright) if bright is not None and tuple(bright) != _PERCENT else None,
        "status_color_groups_pact": gate,
        "power_resync": bool(resync),
        "kelvin_by_hardware": tuple(sorted(kelvin_by_hardware, reverse=True)),
        "mode_reports_kelvin": bool(row.get("mode_reports_kelvin", True)),
    }
    if ("segments" in row_caps or color_readback) and not out["segments"]:   # before SCHEME_LACKS (h6061)
        fail("segments or a colour read-back without wire counts num_segments")
    for name, _index, segs in zones:
        if not all(0 <= s < out["segments"] for s in segs):
            fail(f"zone {name} segments {list(segs)} are not all below the {out['segments']} segments")
    if scheme is not None:
        out["color_scheme"] = scheme
    if status_read is not None:
        out["status_read"] = tuple(status_read)   # command names; devices/profile.py takes their codes
    if ic is not None:
        out["color_readback_ic_range"] = (ic["min_ic"], ic["max_ic"])
        out["color_readback_ic_per_piece"] = ic["ic_per_piece"]
    if relay:
        relay_power = need(row.get("relay_power", cp.get("relay_power")), "relay_power")
        if relay_power not in RELAY_POWERS:
            fail(f"relay_power {relay_power!r} is not a frame the library builds")
        on_mask = power.get("relay_heartbeat_on_mask") or row.get("relay_on_mask", cp.get("relay_on_mask"))
        advert = row.get("advert_on_mask", cp.get("advert_on_mask"))
        masks = {"relay_on_mask": need(on_mask, "relay_on_mask"),
                 "advert_on_mask": need(advert, "advert_on_mask")}
        for name, mask in masks.items():
            if not (_is_int(mask) and 1 <= mask <= 0xFF):
                fail(f"{name} {mask!r} is not a mask within a byte")
        out.update(readback="plug", relay_power=relay_power, **masks)
    else:
        out["readback"] = readback
        out["mode_color_parse"] = need(
            row.get("mode_color_parse", model.get("mode_color_parse", cp.get("mode_color_parse"))),
            "mode_color_parse")
        status = row.get("mode_color_parse_status",
                         model.get("mode_color_parse_status", cp.get("mode_color_parse_status")))
        if status is not None:
            out["mode_color_parse_status"] = status
        if "untraced" in {out["mode_color_parse"], status} and caps & {"rgb", "color_temp"}:
            fail("mode_color_parse untraced")
        unknown = {out["mode_color_parse"], status or out["mode_color_parse"]} - set(parse_names)
        if unknown:
            fail(f"mode_color_parse {sorted(unknown)} not in the mode_color_parse table")
    _check_forms(forms, _forms_used(out, caps, tuple(status_read or ()), relay), fail)
    return out


def _wire(fam: Family, gt: str, sku: str, fail: Fail) -> dict[str, Any]:
    """The SKU's wire facts: the family ``wire``, its ``by_model.<SKU>`` and ``by_goods_type.<gt>`` merged.
    A pact or firmware branch in any of them fails (the library reads the wire with both unknown), but for
    an unread one of :data:`_UNREAD_WIRE_BRANCHES`."""
    wire = (fam.get("client_profile") or {}).get("wire") or {}
    layers: dict[str, WireLayer] = {
        "": wire, f"by_model {sku}": (wire.get("by_model") or {}).get(sku) or {},
        f"by_goods_type {gt}": (wire.get("by_goods_type") or {}).get(gt) or {}}
    for name, layer in layers.items():
        branches: list[tuple[str, Mapping[str, Any]]] = [
            *((f"by_pact {k}", b) for k, b in (layer.get("by_pact") or {}).items()),
            *((f"by_firmware {i}", b) for i, b in enumerate(layer.get("by_firmware") or ()))]
        for key, branch in branches:
            if _UNREAD_WIRE_BRANCHES.get((fam["id"], name, key)) != branch:
                label = " ".join(filter(None, ("wire", name, key)))
                fail(f"{label} is not served (the library reads the wire with the pact and firmware unknown)")
    return _layer(*layers.values())


def _check_mode(w: Mapping[str, Any], readback: str, fail: Fail) -> None:
    """The row's wire ``mode`` / ``mode_status`` must be the decodes the library reads its mode reports with
    (:data:`SERVED_MODE`): the 0xAC TLV 0x05's on a status row (mode_status over mode), the aa 05 reply's on a
    polled row (no mode_status: it has no status read), none on a plug."""
    mode = {**_MODE_UNSET, **w["mode"]}
    if readback == "status":
        served = {**mode, **w["mode_status"]} == SERVED_MODE["status"]
    elif readback == "polled":
        served = mode == SERVED_MODE["aa05"] and not w["mode_status"]
    else:
        served = not w["mode"] and not w["mode_status"]
    if not served:
        fail(f"wire mode {w['mode']} / mode_status {w['mode_status']} is not a decode the library reads with")


def _forms_used(out: Mapping[str, Any], caps: Collection[str], status_read: Sequence[str],
                relay: bool) -> list[tuple[str, str]]:
    """``[(command form, the member the library sends or reads)]`` for one resolved row: the power frame, a
    plug's total switch, the colour scheme's frames, the zone switch and the TLVs the status read requests
    (:data:`_STATUS_FORMS`; the colour read-back, aa 41 and aa 36 forms are checked where they are
    derived)."""
    used = [("f_01", "relays" if relay else "light")]
    if relay and out.get("relay_power") == "total_switch":
        used.append(("f_02", "total_switch"))
    scheme = out.get("color_scheme")
    if scheme == "h60a6" and set(caps) & {"rgb", "color_temp", "segments"}:
        used.append(("f_15_01", "kelvin_tint"))
    if scheme == "h6061":
        used += [*([("f_15_04", "rgb_group")] if "rgb" in caps else ()),
                 *([("f_15_01", "kelvin")] if "color_temp" in caps else ())]
    if scheme == "h61a8" and set(caps) & {"rgb", "segments"}:
        used.append(("f_0b", "rgb_mask"))
    if out["zones"] and not out["bar_switch"]:
        used.append(("f_30", "zone_power"))
    used += [_STATUS_FORMS[tlv] for tlv in status_read if tlv in _STATUS_FORMS]
    return used


def _check_forms(forms: Mapping[str, str], used: Sequence[tuple[str, str]], fail: Fail) -> None:
    """Every command form the library sends or reads for the row is the row's (wire ``forms``)."""
    for form, member in used:
        if forms.get(form) != member:
            fail(f"wire forms {form} {forms.get(form)!r}, where the library uses {member}")


def _firmware(row: Row, fail: Fail) -> tuple[list[tuple[str, int, int]],
                                             list[tuple[str, str, tuple[int, ...]]]]:
    """The row's ``by_firmware`` items as ``kelvin_by_hardware`` and ``scene_versions_by_firmware``
    entries."""
    kelvin: list[tuple[str, int, int]] = []
    scenes: list[tuple[str, str, tuple[int, ...]]] = []
    for item in row.get("by_firmware") or ():
        gates: dict[str, Any] = {k: v for k, v in item.items() if k.startswith("min_")}
        facts: dict[str, Any] = {k: v for k, v in item.items() if not k.startswith("min_")}
        if not all(version(v) for v in gates.values()):
            fail(f"firmware gates {gates} are not all a.bb.cc versions")
        if "kelvin_range" in facts:
            if set(gates) != {"min_hard_version"} or len(facts) != 1:
                fail("a Kelvin firmware branch must gate on min_hard_version alone")
            kelvin.append((gates["min_hard_version"],
                           *_kelvin(facts["kelvin_range"], "firmware kelvin_range", fail)))
        elif set(facts) == {"scene_versions"}:
            if not gates or not set(gates) <= {"min_soft_version", "min_hard_version"}:
                fail("a scene firmware branch must gate on the soft / hard version")
            scenes.append((gates.get("min_soft_version", ""), gates.get("min_hard_version", ""),
                           _scene_set(facts["scene_versions"], fail)))
        else:
            fail(f"firmware branch on {sorted(facts)} is not served")
    return kelvin, scenes


def _kelvin(value: Any, name: str, fail: Fail) -> tuple[int, int]:
    """A ``[min, max]`` Kelvin range as a tuple; ``fail`` unless it is two ints, 0 < min <= max."""
    if not (len(value) == 2 and all(map(_is_int, value)) and 0 < value[0] <= value[1]):
        fail(f"{name} {list(value)} is not a [min, max] Kelvin range")
    return int(value[0]), int(value[1])


def _check_kelvin_by_hardware(base: tuple[int, int] | None, gated: Sequence[tuple[str, int, int]],
                              fail: Fail) -> None:
    """``kelvin_by_hardware``'s rules (``DeviceProfile._check_kelvin_by_hardware``): a base range, one entry
    per version, each overlapping the base, and a range valid on every revision."""
    if not gated:
        return
    lo, hi = base if base is not None else (0, -1)
    if (base is None or len({v for v, _lo, _hi in gated}) != len(gated)
            or any(k_lo > hi or k_hi < lo for _v, k_lo, k_hi in gated)
            or max(lo, *(e[1] for e in gated)) > min(hi, *(e[2] for e in gated))):
        fail(f"Kelvin firmware branches {list(gated)} need the row's kelvin_range {base}, one per version, "
             "each overlapping it and a range valid on every revision")


def _scene_set(value: Any, fail: Fail) -> tuple[int, ...]:
    """A scene versionArray, sorted; ``fail`` unless every version is a non-negative int."""
    if not all(_is_int(v) and v >= 0 for v in value):
        fail(f"scene_versions {list(value)} are not all non-negative ints")
    return tuple(sorted(value))


def _pact(key: str) -> tuple[int, int]:
    """A ``"pactType,pactCode"`` key (devices.schema.json pattern) as ints."""
    pact_type, code = (int(v) for v in key.split(","))
    return pact_type, code


def _status_gate(row: Row, status_read: tuple[str, ...], fail: Fail) -> tuple[int, int] | None:
    """``status_color_groups_pact`` from the row's pact branches: None when every pact's status read asks for
    the 0xA5 colour groups, else the ``(pact type, lowest code)`` of the pacts that do. ``fail`` unless each
    branch's read is the row's ``status_read``, or it without ``color_group``, and the pacts that keep the
    groups are one pact type from that code up (the gate the library implements), each within a byte."""
    short = tuple(t for t in status_read if t != "color_group")
    branches = {key: tuple(branch["status_read"])
                for key, branch in (row.get("by_pact") or {}).items() if "status_read" in branch}
    if any(read not in (status_read, short) for read in branches.values()):
        fail("a pact's status_read is the row's with more than color_group dropped")
    without = {k for k, read in branches.items() if read != status_read}
    if not without:
        return None
    pacts = row.get("pacts")
    if not isinstance(pacts, tuple):
        fail("a status_read pact branch needs listed pacts")
    keep = sorted(_pact(p) for p in pacts if p not in without)
    drop = [_pact(p) for p in without]
    if not keep or len({t for t, _ in keep}) != 1:
        fail("the pacts with colour groups are not one pact type")
    pact_type, code = keep[0]
    if any(t == pact_type and c >= code for t, c in drop):
        fail("the pacts with colour groups are not a code range")
    if not (0 <= pact_type <= 0xFF and 0 <= code <= 0xFF):
        fail(f"the colour-group pact ({pact_type}, {code}) is not within a byte")
    return pact_type, code
