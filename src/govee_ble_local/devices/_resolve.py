"""PROCEDURAL: devices.yaml's supported rows resolved into DeviceProfile facts (standard library only).

The input is ``_generated/devices.py``, which ``tools/gen_devices.py`` projects from ``spec/devices.yaml``:
the families with a ``client_profile.by_goods_type`` row that carries ``support``, typed by the TypedDicts
below. :func:`profiles` gives one field mapping per group of (row, SKU) routes whose resolved facts are
identical, with each route's goodsType and accepted pacts, which ``devices/profile.py`` turns into the
:data:`~.profile.PROFILES` table and its routing index. The generator runs :func:`routes` over the whole
document and the same :func:`profiles` over its projection, every support level, before it writes the
module, so a missing fact, or one the library can't serve, fails the build with :class:`SpecError` and never
an import. Nothing here imports the package, so the generator can load this file on its own. The generator
also runs :func:`catalogue` over the whole document with the committed catalogue snapshot
(``spec/govee_catalog.json``), and the rows' ``models`` in the projection carry the SKUs it routes.

The spec's resolution (devices.schema.json ``clientRow``, ``spec/govee_reference.py`` ``ble_rows`` /
``client_row``): a row is a BLE route for its models when it has ``support``, ``pacts`` and ``ble`` in its
(else the family's) connectivity. A SKU that several rows list has one route per row; the client picks
among them by the advertised pact, else the goodsType (``profile.profile_for``), and :func:`routes` gives
the SKUs that one row routes. A row's facts are its own, else the family ``client_profile``'s, and the
layered ones (scene_versions, kelvin_range, brightness_range) fall back to the family ``specs``, with a
SKU's ``by_model`` Kelvin and brightness ranges between the two (the schema's by_model, which the row
wins over; ``client_row``'s ``sku``). The wire facts layer the family
``wire``, ``by_model.<SKU>`` and ``by_goods_type.<gt>``. A profile is resolved with
the pact and the firmware unknown; the pact- and firmware-gated facts the library follows at run time are
named below.

Library rules over the spec's vocabulary (what the library can't serve yet, or how it serves it). A row is
admitted only when every value it holds is one the library implements, and a fact the library needs and
the row lacks fails the build: nothing defaults silently.

- Admission: ``hardware_verified`` and ``app_traced`` rows (:data:`ADMITTED`); a ``modelled`` row
  (:data:`MODELLED`) only where the caller opts in (the user's "app-traced by default"). Every row with
  support is resolved and checked, whatever its level. A row with support that is no BLE route (no models,
  pacts or ble link) raises, and so does an ``assigned_goods_type`` item in any family that names a routed
  SKU (case-insensitively), unless its condition tests the pact alone: the library routes by SKU and pact,
  so it can't follow a firmware-gated rewrite or an unconditioned pairing write (left for later). A
  pact-conditioned item is routing data instead: its SKUs are listed on the row of its goodsType with the
  pacts at or above its gate, and on a row of a goodsType it rewrites from with pacts below it, so the
  advertised pact picks the row the app's rewrite picks. A row that lists one of them with pacts on the
  wrong side raises, and a route no row carries yet is for review (:func:`reassignment_review`). Those SKUs
  are outside the pact fallback (below), so a pact below the gate never reaches the target row. A SKU that a
  ``modelled`` row and an admitted row both list raises too: the spec's routing (``ble_rows``) counts both
  wherever both accept the pact, and the opt-in must leave the admitted routes as they are.
- The pact fallback (decision D3), the 1.x compatibility rule: 1.4.0 routed each of its 18 SKUs whatever
  pact its advertisement carried, so ``profile.profile_for`` still routes one of them
  (:data:`PACT_FALLBACK_SKUS`, frozen at those 18) to its only admitted profile at a pact no row accepts, and
  no released unit is demoted by a pact its row doesn't list. Every other SKU (one a row lists, one only the
  catalogue snapshot routes, one of a reassignment) is served only at its rows' pacts, as the app's scan
  serves it: a device whose advertised pact its goodsType doesn't accept is refused
  (``AbsBaseBleModel.e``:158-174, ``checkProtocol`` -> ``Pact.i``), and the accepted pacts compare exactly
  (``Protocol.c``, Protocol.java:41-48).
- The catalogue snapshot (:func:`catalogue`, the precedence of ``spec/govee_catalog.json``'s doc): a SKU no
  row lists routes to the BLE row with support at its catalogue goodsType when that is the only row at it
  (the app's ``Pact.f``), with its facts, and never at goodsType 0 (the legacy code). Its support level is
  the row's capped at ``app_traced`` (:func:`catalogue_support`): the snapshot's goodsType is no bench check,
  so a ``hardware_verified`` row's evidence is not the SKU's, and a ``modelled`` row's SKU stays behind the
  opt-in. Such a SKU is served only at the row's pacts, outside the pact fallback. A SKU a row lists
  keeps the spec's route, unless its catalogue goodsType is a conflict (non-zero, and none of its rows'),
  which only an ``assigned_goods_type`` item citing the app's rewrite of it settles: one whose
  ``from_goods_type`` names the catalogue goodsType, or, with none, one that the pact conditions (an
  unconditioned pairing write, H615E's, settles nothing). An unsettled one is unrouted and on the
  generator's review list. The snapshot's ``ic`` is read nowhere: no row's app trace cites the app's read
  of it (``CategoriesConfig.getDefIcNum``), and no count is derived from it.
- A row's colour scheme drops the capabilities it has no frame for (:data:`SCHEME_LACKS`), but only a
  scheme the row names itself: a family default that would drop one raises. ``rgb``, ``color_temp`` or
  ``segments`` on a relay row raises (the library resolves no colour scheme or Kelvin range for a plug), and
  elsewhere they need a colour scheme. An ``untraced`` scene dialect drops ``scenes``; ``none`` is activate
  only. Scenes need a bundled catalog, as every SKU of 1.4.0 that offers them ships one: a SKU the scene
  store has no catalog for (``scenes/index.json``, the generated ``SCENE_CATALOGS``, which
  :func:`profiles` takes as ``catalogued``) is offered no ``scenes``, its row's scene facts still checked.
  An ``untraced`` colour parse on a row with ``rgb`` or ``color_temp`` raises (``none`` is a traced value:
  the screen parses no colour).
- The reads the library makes: the 0xAC status read is the row's ``status_read``, in its order
  (``DeviceProfile.status_read``, ``build.status_query(types=)``), and its reply is read with the row's own
  wire facts (``DeviceProfile.status_decode``, ``reassemble.status_profile``): the forms of the TLVs the
  status stream reads (:data:`_STATUS_TLV_FORMS`), the colour-group form where it reads the 0xA5 TLV as
  colour groups (a requested ``color_group`` under ``f_a5: color_group``; any other 0xA5 is skipped), and
  the TLV 0x05's mode decode (mode_status over mode). The library takes the zone bits from TLV 0x30, so a
  requested ``cmd_30`` needs ``f_30: zone_power``. A pact branch may only drop ``color_group``, which
  becomes ``status_color_groups_pact`` (one pact type, from its lowest code that keeps it). A polled row
  reads aa 04 on every poll and a gradual row ``aa a3``, so ``connect_reads`` lists ``brightness`` exactly
  on a polled row and ``gradual`` wherever the capability is; a ``gradual`` read without the capability (the
  screen's default ``Compose4InfoBleIot`` read, with no gradual control) is made on every poll too and its
  reply discarded (``DeviceProfile.gradual_read_only``), on a polled light row only: that is where the
  default read is traced (the h7050 screens), and a status or plug row's reads are its own strategy's. An
  ``ic_num`` read goes with ``ic_pieces`` and the direct colour read-back: it sizes the read-back's pieces
  (H6641), or, with ``ic_pieces: fixed``, it is made on every poll and discarded
  (``DeviceProfile.ic_num_read_only``: a fixed piece count). Every command
  form the library sends or reads for the row must be the row's (wire ``forms``), a polled row's aa 05 reply
  is read with the spec's default decode (:data:`_MODE_UNSET`), and a colour-group read is of 4-byte
  records in the row's wire ``group.num_per_group`` (and, in the status read, its ``group.rule``).
- The writes on every connect (``connect_writes``): a plug's ``plug_sync_time``, and on a light nothing or
  ``light_effect_stop`` (``DeviceProfile.light_effect_stop``), each sent once per session after the
  handshake and any unlock. A SKU's ``by_model`` ``scene_power_on`` (the power-on ahead of a scene apply,
  H6009 / H6010) becomes ``DeviceProfile.scene_power_on``, on a row with ``scenes`` only.
- Wire branches: the library reads the wire with the pact and the firmware unknown, so a wire ``by_pact`` /
  ``by_firmware`` branch raises, but for the unread ones of :data:`_UNREAD_WIRE_BRANCHES` (H60A6 pact (1,
  1)'s one colour piece: decision D3, the library keeps the row's 13).
- Pact branches are at the row's listed pacts only (a branch elsewhere is reached by no device: it raises),
  and a family with a row with support takes ``by_model`` / ``wire.by_model`` entries only for SKUs one of
  its rows lists or the catalogue snapshot routes to one of them (:func:`sku_entries`). ``status_read``
  becomes the gate above, and ``scene_versions`` becomes ``scene_versions_by_pact``, which
  ``Device.scene_versions`` applies with the advertised pact before the firmware entries (goodsType 19 at
  pact code 2); ``kelvin_range`` and ``brightness_range`` become
  ``kelvin_by_pact`` / ``brightness_by_pact``, which ``Device`` applies with the advertised pact the same
  way (goodsType 261 at pact (4, 1); a pact Kelvin range needs a base one and no Kelvin firmware branch, a
  pact brightness range the brightness capability); ``capabilities`` take the row's own values only on the
  D3 branch, pinned exactly as the wire one is (:data:`_UNREAD_PACT_CAPABILITIES`: H60A6 pact (1, 1) keeps
  the row's capabilities), and raise elsewhere or when that branch changes; a branch on any other fact
  raises.
- Firmware branches resolve after the device-info read (D6): a ``kelvin_range`` item gated on the hardware
  version alone becomes ``kelvin_by_hardware``, and one gated on the soft version alone
  ``kelvin_by_software`` (both compared as a string, as ``KelvinConfig.Config.z2`` / ``Z2`` / ``X2`` do), so
  the gate must be in the app's ``a.bb.cc`` form, two-digit fields, for the string compare to order it. A
  goodsType whose KelvinConfig function compares its version numerically (:data:`_NUMERIC_KELVIN_GATES`:
  235's ``Config.Y2``, ``BleUtils.isHigherVersion``) raises. They are the row's, or the SKU's ``by_model``
  ones over its own ``kelvin_range`` (bulblightv3 H14A1), never both, and on one version. A ``by_model``
  range under the row's own (which wins) raises. A ``scene_versions`` item gated on the soft and / or hard
  version becomes ``scene_versions_by_firmware`` (the goodsType-19 gates, Support.newVersion4Ble). Before
  the read, each version not yet read meets its own gates, so the newest firmware's set applies, as 1.x
  assumed (H61A8: {0, 1, 2, 3}), and the Kelvin range is the one valid on every revision. Any other
  firmware fact or gate raises.
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

import re
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
    # Not a devices.yaml key: the SKUs only the catalogue snapshot routes to the row, which the generator adds
    # (tools/gen_devices.py GENERATED; :func:`catalogue`), served at :func:`catalogue_support`.
    catalogue_models: tuple[str, ...]
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
    ic_pieces: IcPieces | str
    by_pact: dict[str, Branch]
    by_firmware: tuple[FirmwareBranch, ...]


class ClientModel(TypedDict, total=False):
    """``clientModel``: a per-SKU override."""
    mode_color_parse: str
    mode_color_parse_status: str
    kelvin_range: tuple[int, ...]
    brightness_range: tuple[int, ...]
    scene_power_on: bool
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


class AssignedCondition(TypedDict, total=False):
    """``assignedGoodsTypeCondition``: the tests on the device record, all of which must hold."""
    min_pact_type: int
    min_pact_code: int
    min_soft_version: str
    min_hard_version: str


class AssignedGoodsType(TypedDict, total=False):
    """``assignedGoodsType``: SKUs of the family whose goodsType the app rewrites itself."""
    models: tuple[str, ...]
    goods_type: int
    from_goods_type: tuple[int, ...]
    condition: AssignedCondition


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
# The mode decode (wireMode) the library reads a polled row's aa 05 replies with: the spec's default, every
# leaf unset (profile.wire_profile passes neither). A status row's TLV 0x05 takes the row's own
# (DeviceProfile.status_decode).
_MODE_UNSET: Final[ModeDecode] = {"music": "not_decoded", "empty_fails": False}
# The family encryption tags the library implements: plaintext, the AES-RC4 PSK channel, and the account
# lock, whose cipher is resolved at connect (aes_gcm_v2 is detected at connect but not implemented).
_CIPHERS: Final = frozenset({"none", "aes_v1", "secret_key_pairing"})
# scenes.upload.dialects -> DeviceProfile.scene_dialect; "none" = activate only.
_DIALECTS: Final = {"legacy_scene": "A", "diy_graffiti": "B_h60a6", "tablelamp_h6052_type5_diy": "B_h6052",
                    "none": "A"}
_READBACK: Final = {"aa01": "polled", "ac_status": "status"}   # power_read -> DeviceProfile.readback
# The forms of the TLVs the status stream reads (govee_state's TLV-typed tags; reassemble.status_profile): the
# row's own, in DeviceProfile.status_decode. f_a5 goes in only where the 0xA5 TLV is read as colour groups.
_STATUS_TLV_FORMS: Final = ("f_30", "f_36", "f_40", "f_41", "f_a3")
_GROUP_RULES: Final = frozenset({"from_len", "fixed", "by_pieces"})   # govee_common::group_count_rule, read
_READS: Final = frozenset({"brightness", "gradual", "cmd_36", "color_group", "ic_num", "cmd_41"})
_PACT_FACTS: Final = frozenset({"status_read", "scene_versions", "kelvin_range", "brightness_range"})
# Pact branches whose capabilities the library leaves unread: (family, goodsType, pact key) -> the branch's
# capabilities exactly. D3: H60A6 at pact (1, 1) keeps the row's capabilities.
_UNREAD_PACT_CAPABILITIES: Final[Mapping[tuple[str, str, str], tuple[str, ...]]] = {
    ("h60ax", "303", "1,1"): ("power", "brightness", "rgb", "color_temp", "scenes")}
_AES_FALLBACK_ROWS: Final = frozenset({("h60ax", "303")})   # D4: 1.x's fallback, kept for the unlocked H60A6
# D3, the 1.x pact fallback (module doc): the 18 SKUs 1.4.0 shipped, frozen. profile.profile_for still routes
# one of them to its only admitted profile at a pact no row accepts; every other SKU only at its rows' pacts.
PACT_FALLBACK_SKUS: Final = frozenset({
    "H60A6", "H6047", "H61A8", "H6004", "H6006", "H6008", "H6052", "H6641", "H6188", "H6062", "H6061",
    "H5080", "H5083", "H5085", "H5082", "H5089", "H5160", "H5161"})
_LAYERED: Final = ("scene_versions", "kelvin_range", "brightness_range")
# Kelvin firmware gates the app compares numerically, where the library compares a gate as a string (as the
# other KelvinConfig functions do): goodsType -> that function. A Kelvin firmware branch on one raises.
_NUMERIC_KELVIN_GATES: Final[Mapping[int, str]] = {
    235: "KelvinConfig.Config.Y2 (BleUtils.isHigherVersion)"}
# A string-compared gate's form: the app's version strings, with two-digit minor fields ("3.08.01").
_STRING_GATE: Final = re.compile(r"\d+\.\d{2}\.\d{2}")
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
    longer be additive), for a routed SKU that an ``assigned_goods_type`` item in any family names (matched
    as the routes are, case-insensitively) unless the item's condition tests the pact alone, and for a row
    that disagrees with a pact-conditioned item (:func:`_reassignments`). The generator runs it over the
    whole document."""
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
            if pact_gate(item) is not None:
                continue   # routing data: _reassignments checks it against the rows
            for sku in item.get("models") or ():
                if isinstance(sku, str) and sku.upper() in listed:
                    owner, gt, routed, _admitted = listed[sku.upper()][0]
                    raise SpecError(f"{owner['id']} goodsType {gt} ({routed}): {fam['id']} "
                                    f"assigned_goods_type reassigns it to goodsType {item.get('goods_type')} "
                                    "(the library follows only a pact-conditioned reassignment)")
    _reassignments(families)
    return {key: hits[0][:3] for key, hits in listed.items() if len(hits) == 1 and hits[0][3]}


def sku_entries(families: Sequence[Family], routed: Catalogue) -> None:
    """SpecError, in a family with a row with support, for a ``by_model`` or ``wire.by_model`` entry of a
    SKU that no row of the family lists and that ``routed`` (:func:`catalogue` over the same ``families``)
    routes to none of them (H6009 / H6010's ``scene_power_on`` are such catalogue routes). The generator
    runs it over the whole document, since its cut keeps only the routed SKUs' entries and would drop a
    stray one silently."""
    for fam in families:
        cp = fam.get("client_profile") or {}
        rows = cp.get("by_goods_type") or {}
        if any("support" in row for row in rows.values()):
            own = {m.upper() for row in rows.values() for m in row.get("models") or () if isinstance(m, str)}
            own.update(sku for (fam_id, _gt), skus in routed.added.items() if fam_id == fam["id"]
                       for sku in skus)
            for layer, entries in (("by_model", cp.get("by_model") or {}),
                                   ("wire by_model", (cp.get("wire") or {}).get("by_model") or {})):
                unlisted = sorted(sku for sku in entries if sku.upper() not in own)
                if unlisted:
                    raise SpecError(f"{fam['id']} {layer} {unlisted}: no row of the family lists them (a "
                                    "per-SKU entry needs the SKU's row or catalogue route)")


def pact_gate(item: AssignedGoodsType) -> tuple[int, int] | None:
    """A reassignment's ``(min_pact_type, min_pact_code)`` when its condition tests the pact alone, else None
    (a firmware gate, or none: the library can't follow those)."""
    cond = item.get("condition") or {}
    pact_type, code = cond.get("min_pact_type"), cond.get("min_pact_code")
    if set(cond) != {"min_pact_type", "min_pact_code"} or pact_type is None or code is None:
        return None
    return int(pact_type), int(code)


def _meets(pact: tuple[int, int], gate: tuple[int, int]) -> bool:
    """The record test of ``assignedGoodsTypeCondition``: pactType and pactCode each at or above the
    gate's."""
    return pact[0] >= gate[0] and pact[1] >= gate[1]


def _reassignments(families: Sequence[Family]) -> list[str]:
    """Check each pact-conditioned ``assigned_goods_type`` item against the rows, and give the routes no row
    carries yet, one review line each. A row that lists one of its SKUs with ``pacts`` at its ``goods_type``
    must accept only pacts at or above the gate, and one at a ``from_goods_type`` only pacts below it
    (``any`` is neither), so the advertised pact picks the row the app's rewrite picks; a row at another
    goodsType is neither checked nor refused. SpecError where a row disagrees. A row at either goodsType
    that lists the SKU without ``pacts`` is for review too: once it has pacts, both rows carry the route."""
    review: list[str] = []
    for fam in families:
        for item in fam.get("assigned_goods_type") or ():
            gate = pact_gate(item)
            if gate is None:
                continue
            target, sources = item.get("goods_type"), tuple(item.get("from_goods_type") or ())
            rule = f"{fam['id']} assigned_goods_type, {list(sources)} -> {target} at pacts >= {gate}"
            for sku in item.get("models") or ():
                if not isinstance(sku, str):
                    continue
                carried: set[int] = set()
                for owner in families:
                    for gt, row in ((owner.get("client_profile") or {}).get("by_goods_type") or {}).items():
                        at = int(gt)
                        if (at != target and at not in sources) or sku.upper() not in {
                                m.upper() for m in row.get("models") or () if isinstance(m, str)}:
                            continue
                        if "pacts" not in row:
                            review.append(f"{sku}: also listed by {owner['id']} goodsType {gt}, without "
                                          f"pacts ({rule})")
                            continue
                        pacts = row["pacts"]
                        side = at == target
                        if isinstance(pacts, str) or any(_meets(_pact(p), gate) != side for p in pacts):
                            raise SpecError(f"{owner['id']} goodsType {gt} ({sku}): pacts {pacts} not all "
                                            f"{'at or above' if side else 'below'} the gate of {rule}")
                        carried.add(at)
                review += [f"{sku}: no goodsType-{g} row with pacts "
                           f"{'at or above' if g == target else 'below'} the gate ({rule})"
                           for g in (target, *sources) if g not in carried]
    return review


def reassignment_review(families: Sequence[Family]) -> tuple[str, ...]:
    """The routes of the pact-conditioned ``assigned_goods_type`` items that no row of ``families`` (the whole
    document) carries yet, one line each, for the generator to print (:func:`_reassignments`)."""
    return tuple(_reassignments(families))


def _rewrites(item: AssignedGoodsType, code: int) -> bool:
    """An ``assigned_goods_type`` item cites the app's rewrite of catalogue goodsType ``code``: its
    ``from_goods_type`` names it, or, with none (from any goodsType), its condition tests the pact. An
    unconditioned or firmware-gated item from any goodsType (H615E's pairing write) cites none."""
    sources = item.get("from_goods_type")
    return code in sources if sources is not None else pact_gate(item) is not None


def _is_route(fam: Family, row: Row) -> bool:
    """A row is a BLE route: it lists models (or the generator gave it catalogue routes) and pacts, and ble is
    in its (else its family's) connectivity."""
    return bool(row.get("models") or row.get("catalogue_models")) and "pacts" in row and "ble" in (
        row.get("connectivity") or fam.get("connectivity") or ())


def catalogue_support(level: str) -> str:
    """The support level of a SKU only the catalogue snapshot routes to a row at ``level``: the row's, capped
    at ``app_traced``."""
    return "app_traced" if level == "hardware_verified" else level


class Catalogue(NamedTuple):
    """The catalogue snapshot's routes over devices.yaml (:func:`catalogue`)."""
    added: dict[tuple[str, str], tuple[str, ...]]
    """``{(family id, goodsType): SKUs}``: the SKUs no row lists that the snapshot routes to that row."""
    unrouted: frozenset[str]
    """Upper-cased SKUs that a row with support lists at a goodsType the snapshot contradicts, with no cited
    rewrite: the spec's route doesn't win, so no row routes them."""
    review: tuple[str, ...]
    """One line per SKU the snapshot leaves for review, which the generator prints."""


def catalogue(families: Sequence[Family], snapshot: Mapping[str, int]) -> Catalogue:
    """The routes ``snapshot`` (``spec/govee_catalog.json``: SKU -> catalogue goodsType) adds to
    devices.yaml's ``families`` (the whole document), with SKUs compared upper-cased as :func:`routes` does.

    A SKU that some client_profile row lists keeps the spec's goodsType. Its catalogue goodsType is a conflict
    when it is non-zero and none of those rows has it. An ``assigned_goods_type`` item of a listing row's
    family that names the SKU at that row's goodsType settles it when the item is the app's cited rewrite of
    that catalogue goodsType (:func:`_rewrites`; :func:`routes` checks or refuses that item on a routed SKU).
    Any other item, such as an unconditioned pairing write, settles nothing. An unsettled conflict is for
    review, and a row with
    support no longer routes the SKU. A SKU no row lists routes to the BLE row with support at its
    catalogue goodsType when that is the only row at it, but never at 0 (the legacy code, not H6188's
    goodsType-0 row); an ``assigned_goods_type`` item naming it (the app rewrites its goodsType), or rows of
    several families at that goodsType, leave it for review."""
    listed: dict[str, list[tuple[Family, str, Row]]] = {}
    rows_at: dict[int, list[tuple[Family, str, Row]]] = {}
    for fam in families:
        for gt, row in ((fam.get("client_profile") or {}).get("by_goods_type") or {}).items():
            for sku in row.get("models") or ():
                if isinstance(sku, str):
                    listed.setdefault(sku.upper(), []).append((fam, gt, row))
            rows_at.setdefault(int(gt), []).append((fam, gt, row))
    rewrites: dict[str, list[tuple[str, AssignedGoodsType]]] = {}
    for fam in families:
        for item in fam.get("assigned_goods_type") or ():
            for sku in item.get("models") or ():
                if isinstance(sku, str):
                    rewrites.setdefault(sku.upper(), []).append((fam["id"], item))
    added: dict[tuple[str, str], list[str]] = {}
    unrouted: set[str] = set()
    review: list[str] = []
    for sku, code in sorted(snapshot.items()):
        key = sku.upper()
        if key in listed:
            here = {(fam["id"], int(g)) for fam, g, _row in listed[key]}
            if code == 0 or code in {g for _f, g in here} or any(
                    (owner, item.get("goods_type")) in here and _rewrites(item, code)
                    for owner, item in rewrites.get(key, ())):
                continue
            rows = ", ".join(f"{fam['id']} {g}" for fam, g, _row in listed[key])
            routed = any("support" in row for _fam, _g, row in listed[key])
            review.append(f"{key}: catalogue goodsType {code}, devices.yaml {rows}, no cited rewrite"
                          + ("; unrouted" if routed else ""))
            if routed:
                unrouted.add(key)
        elif code != 0 and any("support" in row and _is_route(fam, row)
                               for fam, _g, row in rows_at.get(code, ())):
            hits = rows_at[code]
            if key in rewrites:
                review.append(f"{key}: catalogue goodsType {code}, rewritten by an assigned_goods_type item")
            elif len(hits) > 1:
                review.append(f"{key}: catalogue goodsType {code} has rows in "
                              + ", ".join(fam["id"] for fam, _g, _row in hits))
            else:
                added.setdefault((hits[0][0]["id"], hits[0][1]), []).append(key)
    return Catalogue({k: tuple(v) for k, v in added.items()}, frozenset(unrouted), tuple(review))


def _members(families: Sequence[Family], admitted: Collection[str]) -> Iterator[tuple[Family, str, Row, str]]:
    """``(family, goodsType, row, SKU)`` for each SKU a BLE row routes at a support level in ``admitted``, in
    devices.yaml order: the row's models at its own level, then its catalogue routes at
    :func:`catalogue_support`."""
    for fam in families:
        for gt, row in ((fam.get("client_profile") or {}).get("by_goods_type") or {}).items():
            level, route = row.get("support", ""), _is_route(fam, row)
            own = (row.get("models") or ()) if route and level in admitted else ()
            via = ((row.get("catalogue_models") or ())
                   if route and catalogue_support(level) in admitted else ())
            yield from ((fam, gt, row, sku) for sku in (*own, *via))


def profiles(families: Sequence[Family], parse_names: Collection[str], admitted: Collection[str] = ADMITTED,
             catalogued: Collection[str] | None = None
             ) -> list[tuple[tuple[str, ...], dict[str, Any], tuple[Route, ...]]]:
    """``[(skus, fields, routes)]``: DeviceProfile field values for each group of (row, SKU) routes at a
    support level in ``admitted`` (a catalogue route's is :func:`catalogue_support`) whose resolved facts are
    identical, in devices.yaml order (row models, then its catalogue routes, within a row), with those routes.
    ``parse_names`` are the mode_color_parse table's names; ``catalogued`` are the SKUs (upper-case) with a
    bundled scene catalog, the only ones offered scenes (None: no cut). SpecError on a fact missing or not
    served."""
    groups: dict[str, tuple[list[str], dict[str, Any], list[Route]]] = {}
    for fam, gt, row, sku in _members(families, admitted):
        pacts = row.get("pacts", "any")   # every route has pacts (_members); 'any' tests none
        accepted = None if isinstance(pacts, str) else frozenset(_pact(p) for p in pacts)
        fields = _fields(fam, gt, sku, parse_names, catalogued)
        skus, _same, members = groups.setdefault(repr(sorted(fields.items())), ([], fields, []))
        if sku not in skus:
            skus.append(sku)
        members.append(Route(sku, int(gt), accepted))
    return [(tuple(skus), fields, tuple(members)) for skus, fields, members in groups.values()]


def _fields(fam: Family, gt: str, sku: str, parse_names: Collection[str],
            catalogued: Collection[str] | None = None) -> dict[str, Any]:
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
    if model.get("by_pact") or any(k != "kelvin_range" and not k.startswith("min_")
                                   for item in model.get("by_firmware") or () for k in item):
        fail("by_model pact / firmware branches are not served (but a firmware kelvin_range)")
    if fam.get("encryption") not in _CIPHERS:
        fail(f"encryption {fam.get('encryption')!r} is not a cipher the library implements")

    # Layered facts: the row's own (its pact and firmware branches resolve apart), else the SKU's by_model
    # entry, else the family specs.
    own: dict[str, object] = dict(row)
    mine: dict[str, object] = dict(model)
    specs: dict[str, object] = dict(fam.get("specs") or {})
    shadowed = sorted(k for k in _LAYERED if k in own and k in mine)
    if shadowed:
        fail(f"by_model {shadowed} under the row's own (the row's wins)")
    layered: dict[str, Any] = {k: own[k] if k in own else mine[k] if k in mine else specs[k]
                               for k in _LAYERED if k in own or k in mine or k in specs}
    row_kelvin, scenes_by_firmware = _firmware(row, fail)
    model_kelvin, _no_scenes = _firmware(model, fail)
    kelvin_fw = row_kelvin or model_kelvin
    if kelvin_fw and int(gt) in _NUMERIC_KELVIN_GATES:
        fail(f"a Kelvin firmware branch on goodsType {gt}, whose gate the app compares numerically "
             f"({_NUMERIC_KELVIN_GATES[int(gt)]}), where the library compares it as a string")
    if ((row_kelvin and model_kelvin) or (model_kelvin and "kelvin_range" not in mine)
            or len({gate for gate, *_entry in kelvin_fw}) > 1):
        fail("Kelvin firmware branches must be the row's, or the SKU's by_model ones over its own "
             "kelvin_range, on one version")
    kelvin_by_version = [(at, lo, hi) for _gate, at, lo, hi in kelvin_fw]
    by_soft = any(gate == "min_soft_version" for gate, *_entry in kelvin_fw)

    status_read = row.get("status_read")
    by_pact = row.get("by_pact") or {}
    listed_pacts = row.get("pacts")
    stray = sorted(set(by_pact) - set(listed_pacts)) if isinstance(listed_pacts, tuple) else []
    if stray:
        fail(f"pact branches {stray} are not among the row's pacts {list(listed_pacts or ())} (no device at "
             "them reaches the row)")
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
    if dialect == "untraced" or (catalogued is not None and sku.upper() not in catalogued):
        caps.discard("scenes")
    uploads = "scenes" in caps and dialect != "none"
    scenes = _scene_set(need(layered.get("scene_versions"), "scene_versions"), fail) if uploads else ()
    if "color_temp" in caps:
        need(layered.get("kelvin_range"), "kelvin_range")
    kelvin = (_kelvin(layered["kelvin_range"], "kelvin_range", fail)
              if layered.get("kelvin_range") is not None and not relay else None)
    _check_kelvin_by_firmware(kelvin, kelvin_by_version, fail)
    kelvin_by_pact = tuple(sorted(
        [(_pact(key), *_kelvin(branch["kelvin_range"], f"pact {key} kelvin_range", fail))
         for key, branch in by_pact.items() if "kelvin_range" in branch], key=lambda e: e[0]))
    if kelvin_by_pact and (kelvin is None or kelvin_fw):
        fail("a pact kelvin_range needs a base kelvin_range (the row's, the SKU's by_model or the family's) "
             "and no Kelvin firmware branch")
    if kelvin is not None and not overlaps_every(kelvin, [(lo, hi) for _key, lo, hi in kelvin_by_pact]):
        fail(f"pact kelvin_ranges {[list(e[1:]) for e in kelvin_by_pact]} must each overlap the base "
             f"{list(kelvin)}, and so must the range valid on every pact (used until the pact is heard)")
    bright = need(layered.get("brightness_range"), "brightness_range") if "brightness" in caps else None
    raw_bright = _brightness(bright, "brightness_range", fail) if bright is not None else None
    bright_by_pact = tuple(sorted(
        [(_pact(key), _brightness(branch["brightness_range"], f"pact {key} brightness_range", fail))
         for key, branch in by_pact.items() if "brightness_range" in branch], key=lambda e: e[0]))
    if bright_by_pact and bright is None:
        fail("a pact brightness_range without the brightness capability")

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
    if "gradual" in caps and "gradual" not in reads:
        fail("connect_reads lists gradual with the capability (the library reads aa a3 for it)")
    if "gradual" in reads and "gradual" not in caps and readback != "polled":
        fail("a gradual read without the capability is served on a polled light row only (the screens' "
             "default Compose4InfoBleIot read)")
    color_readback = ""
    if "color_group" in reads:
        color_readback = {"color_group": "mechanism_a_direct", "bulb_batch_v2": "mechanism_b"}.get(
            forms.get("f_a5", ""), "")
        if not color_readback:
            fail(f"colour read-back with f_a5 {forms.get('f_a5')!r}")
    # The status read takes colour groups from TLV 0xA5 where it requests them and the row reads them so.
    status_groups = "color_group" in (status_read or ()) and forms.get("f_a5") == "color_group"
    if (color_readback or status_groups) and cp.get("has_brightness") is not True:
        fail("a colour-group read without has_brightness true (the library reads [brightness, r, g, b])")
    # Either read walks the row's group size (Controller4ColorInfoByGroup takes it from its caller), and the
    # status read counts each group's records by the row's rule.
    per_group = group.get("num_per_group")
    if (color_readback == "mechanism_a_direct" or status_groups) \
            and not (_is_int(per_group) and per_group >= 1):
        fail(f"wire group num_per_group {per_group!r} (the colour groups are read in groups of it)")
    if status_groups and group.get("rule") not in _GROUP_RULES:
        fail(f"wire group rule {group.get('rule')!r} (the status read counts its colour groups' records by "
             "it)")
    ic = row.get("ic_pieces")
    if ("ic_num" in reads) != (ic is not None) or (ic is not None and color_readback != "mechanism_a_direct"):
        fail("an ic_num read and ic_pieces go together, with the direct colour read-back")
    sized = None if ic is None or isinstance(ic, str) else ic   # the IC count sizes the pieces (H6641)
    if isinstance(ic, str) and ic != "fixed":
        fail(f"ic_pieces {ic!r} (fixed, or the IC sizing)")
    if sized is not None and not (0 <= sized["min_ic"] <= sized["max_ic"] and sized["ic_per_piece"] >= 1):
        fail(f"ic_pieces {dict(sized)} (min_ic <= max_ic, ic_per_piece >= 1)")
    live_ic = sized is not None
    section_count = "cmd_41" in reads
    if section_count and forms.get("f_41") != "section_count":
        fail("an aa 41 read without f_41 section_count")
    bar = forms.get("f_36") == "bar_pair"
    if ("cmd_36" in reads) != bar:
        fail("the aa 36 read and f_36 bar_pair go together")
    writes = tuple(need(row.get("connect_writes"), "connect_writes"))
    if writes not in ((("plug_sync_time",),) if relay else ((), ("light_effect_stop",))):
        fail(f"connect_writes {list(writes)} (the library sends the plug time sync on every relay row, and "
             "light_effect_stop or nothing on a light)")
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
        "gradual_read_only": "gradual" in reads and "gradual" not in caps,
        "color_readback": color_readback,
        "color_readback_per_batch": int(per_group) if color_readback == "mechanism_a_direct" else 3,
        "color_readback_live_ic": live_ic,
        "ic_num_read_only": ic == "fixed",
        "section_count_read": section_count,
        "brightness_range": raw_bright,
        "kelvin_by_pact": kelvin_by_pact,
        "brightness_by_pact": bright_by_pact,
        "status_color_groups_pact": gate,
        "power_resync": bool(resync),
        "kelvin_by_hardware": () if by_soft else tuple(sorted(kelvin_by_version, reverse=True)),
        "kelvin_by_software": tuple(sorted(kelvin_by_version, reverse=True)) if by_soft else (),
        "mode_reports_kelvin": bool(row.get("mode_reports_kelvin", True)),
        "light_effect_stop": writes == ("light_effect_stop",),
        "scene_power_on": bool(model.get("scene_power_on", False)),
    }
    if out["scene_power_on"] and "scenes" not in row_caps:
        fail("by_model scene_power_on on a row without the scenes capability")
    if ("segments" in row_caps or color_readback) and not out["segments"]:   # before SCHEME_LACKS (h6061)
        fail("segments or a colour read-back without wire counts num_segments")
    for name, _index, segs in zones:
        if not all(0 <= s < out["segments"] for s in segs):
            fail(f"zone {name} segments {list(segs)} are not all below the {out['segments']} segments")
    if scheme is not None:
        out["color_scheme"] = scheme
    if status_read is not None:
        out["status_read"] = tuple(status_read)   # command names; devices/profile.py takes their codes
        decode: dict[str, Any] = {f: forms[f] for f in _STATUS_TLV_FORMS
                                  if forms.get(f, "unknown") != "unknown"}
        if status_groups:
            decode.update(f_a5="color_group", group_rule=group["rule"], num_per_group=int(per_group),
                          has_brightness=True)
        decode.update({**_MODE_UNSET, **w["mode"], **w["mode_status"]})   # the TLV 0x05's
        out["status_decode"] = tuple(sorted(decode.items()))
    if sized is not None:
        out["color_readback_ic_range"] = (sized["min_ic"], sized["max_ic"])
        out["color_readback_ic_per_piece"] = sized["ic_per_piece"]
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
    """The row's wire ``mode`` / ``mode_status`` must be a decode the library reads its mode reports with: on
    a status row any (the TLV 0x05 takes the row's own, mode_status over mode, ``status_decode``), on a
    polled row the aa 05 reply's, the spec's default (:data:`_MODE_UNSET`; no mode_status: it has no status
    read), none on a plug."""
    if readback == "status":
        return
    if readback == "polled":
        served = {**_MODE_UNSET, **w["mode"]} == _MODE_UNSET and not w["mode_status"]
    else:
        served = not w["mode"] and not w["mode_status"]
    if not served:
        fail(f"wire mode {w['mode']} / mode_status {w['mode_status']} is not a decode the library reads with")


def _forms_used(out: Mapping[str, Any], caps: Collection[str], status_read: Sequence[str],
                relay: bool) -> list[tuple[str, str]]:
    """``[(command form, the member the library sends or reads)]`` for one resolved row: the power frame, a
    plug's total switch, the colour scheme's frames, and the zone switch and the zone bits the status read
    takes from TLV 0x30 (the colour read-back, aa 41 and aa 36 forms are checked where they are derived)."""
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
    if (out["zones"] and not out["bar_switch"]) or "cmd_30" in status_read:
        used.append(("f_30", "zone_power"))
    return used


def _check_forms(forms: Mapping[str, str], used: Sequence[tuple[str, str]], fail: Fail) -> None:
    """Every command form the library sends or reads for the row is the row's (wire ``forms``)."""
    for form, member in used:
        if forms.get(form) != member:
            fail(f"wire forms {form} {forms.get(form)!r}, where the library uses {member}")


def _firmware(row: Row | ClientModel, fail: Fail) -> tuple[list[tuple[str, str, int, int]],
                                                           list[tuple[str, str, tuple[int, ...]]]]:
    """The row's (or a by_model entry's) ``by_firmware`` items as Kelvin entries, ``(gate, version, min,
    max)`` with the gate ``min_hard_version`` or ``min_soft_version``, and ``scene_versions_by_firmware``
    entries."""
    kelvin: list[tuple[str, str, int, int]] = []
    scenes: list[tuple[str, str, tuple[int, ...]]] = []
    for item in row.get("by_firmware") or ():
        gates: dict[str, Any] = {k: v for k, v in item.items() if k.startswith("min_")}
        facts: dict[str, Any] = {k: v for k, v in item.items() if not k.startswith("min_")}
        if not all(version(v) for v in gates.values()):
            fail(f"firmware gates {gates} are not all a.bb.cc versions")
        if "kelvin_range" in facts:
            if len(facts) != 1 or set(gates) not in ({"min_hard_version"}, {"min_soft_version"}):
                fail("a Kelvin firmware branch must gate on min_hard_version or min_soft_version alone")
            (gate, at), = gates.items()
            if not _STRING_GATE.fullmatch(at):
                fail(f"Kelvin firmware gate {at!r} is not the app's a.bb.cc form (two-digit fields: the gate "
                     "compares as a string)")
            kelvin.append((gate, at, *_kelvin(facts["kelvin_range"], "firmware kelvin_range", fail)))
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


def _check_kelvin_by_firmware(base: tuple[int, int] | None, gated: Sequence[tuple[str, int, int]],
                              fail: Fail) -> None:
    """``kelvin_by_hardware`` / ``kelvin_by_software``'s rules (``DeviceProfile._check_kelvin_by_version``):
    a base range, one entry per version, each overlapping the base, and a range valid on every revision."""
    if not gated:
        return
    lo, hi = base if base is not None else (0, -1)
    if (base is None or len({v for v, _lo, _hi in gated}) != len(gated)
            or any(k_lo > hi or k_hi < lo for _v, k_lo, k_hi in gated)
            or max(lo, *(e[1] for e in gated)) > min(hi, *(e[2] for e in gated))):
        fail(f"Kelvin firmware branches {list(gated)} need a base kelvin_range {base}, one per version, "
             "each overlapping it and a range valid on every revision")


def overlaps_every(base: tuple[int, int], ranges: Sequence[tuple[int, int]]) -> bool:
    """Each of ``ranges`` overlaps the ``base`` range, and the base cut to all of them is not empty: the
    range valid on every pact (or revision) the library uses until it is known."""
    lo, hi = base
    return (all(r_lo <= hi and r_hi >= lo for r_lo, r_hi in ranges)
            and max([lo, *(r[0] for r in ranges)]) <= min([hi, *(r[1] for r in ranges)]))


def _brightness(value: Any, name: str, fail: Fail) -> tuple[int, int] | None:
    """A raw ``[min, max]`` brightness range as a tuple, None for percent (``[1, 100]``); ``fail`` unless
    100 % maps into a byte: NumberUtil.calculateProgress(100) = min + 99 * (max - min + 3) / 100."""
    if not (len(value) == 2 and all(map(_is_int, value)) and 0 <= value[0] < value[1] <= 0xFF
            and value[0] + 99 * value[1] + 297 < 0x100 * 100):
        fail(f"{name} {list(value)} is not a raw [min, max] that 100 % maps into a byte")
    return None if tuple(value) == _PERCENT else (int(value[0]), int(value[1]))


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
