"""PROCEDURAL: devices.yaml's supported rows resolved into DeviceProfile facts (standard library only).

The input is ``_generated/devices.py``, which ``tools/gen_devices.py`` projects from ``spec/devices.yaml``:
the families with a ``client_profile.by_goods_type`` row that carries ``support``, typed by the TypedDicts
below. :func:`profiles` gives one field mapping per group of SKUs whose resolved facts are identical, which
``devices/profile.py`` turns into the :data:`~.profile.PROFILES` table. The generator runs the same
:func:`profiles` over its projection before it writes the module, so a missing fact, or one the library
can't serve, fails the build with :class:`SpecError` and never an import. Nothing here imports the package,
so the generator can load this file on its own.

The spec's resolution (devices.schema.json ``clientRow``, ``spec/govee_reference.py`` ``ble_rows`` /
``client_row``): a row is a BLE route for its models when it has ``support``, ``pacts`` and ``ble`` in its
(else the family's) connectivity; its facts are its own, else the family ``client_profile``'s, and the
layered ones (scene_versions, kelvin_range, brightness_range) fall back to the family ``specs``. The wire
facts layer the family ``wire``, ``by_model.<SKU>`` and ``by_goods_type.<gt>``. A profile is resolved with
the pact unknown, so a row's own values apply (devices.schema.json ``clientBranch``).

Library rules over the spec's vocabulary (what the library can't serve yet, or how it serves it):

- Admission: ``hardware_verified`` and ``app_traced`` rows; a ``modelled`` row is not admitted (the user's
  "app-traced by default").
- A row's colour scheme drops the capabilities it has no frame for (:data:`SCHEME_LACKS`), and an
  ``untraced`` scene dialect drops ``scenes``; ``none`` is activate only. An ``untraced`` colour parse on a
  row with ``rgb`` or ``color_temp`` raises (``none`` is a traced value: the screen parses no colour).
- The 0xAC status read is the H60A6's request (``build.status_query``): ``status_read`` must be
  ``[cmd_41, cmd_30, color_group]``, and a pact branch that drops ``color_group`` becomes
  ``status_color_groups_pact`` (one pact type, from its lowest code that keeps it).
- Pact branches: ``status_read`` becomes the gate above; ``capabilities`` and ``scene_versions`` take the
  row's own values, the pact being unknown (decision D3: H60A6 pact (1, 1) keeps the row's capabilities);
  a branch on any other fact raises.
- Firmware branches resolve after the device-info read (D6): a ``kelvin_range`` item gated on the hardware
  version alone becomes ``kelvin_by_hardware``, and a ``scene_versions`` item gated on the soft and / or
  hard version becomes ``scene_versions_by_firmware`` (the goodsType-19 gates, Support.newVersion4Ble).
  Until the versions are read, the row's own set applies. Any other firmware fact or gate raises.
- The fallback cipher, used only when no advertisement says (decision D4): AES-RC4 on an account-locked row
  (the plugs, whose firmware encrypts) and on the H60A6, as 1.x did (its firmware encrypts; the spec records
  no cipher per SKU, ``wire_cipher: runtime``); none on every other row.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any, Final, Required, TypedDict


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
    mode_color_parse: str
    mode_color_parse_status: str
    relay_power: str
    relay_on_mask: int
    advert_on_mask: int
    color_scheme: str
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
    color_scheme: str
    power_read: str
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


class Family(TypedDict, total=False):
    """``family``."""
    id: Required[str]
    connectivity: tuple[str, ...]
    specs: Specs
    client_profile: ClientProfile


class ModeColorStrategy(TypedDict):
    """``modeColorStrategy``: colour sub-mode byte (``"0x15"``) -> mode_color_decode kind."""
    sub_modes: dict[str, str]


# ── library rules ────────────────────────────────────────────────────────────────────────────────
class SpecError(ValueError):
    """A supported devices.yaml row lacks a fact the library needs, or holds one it can't serve."""


ADMITTED: Final = frozenset({"hardware_verified", "app_traced"})
CAPABILITIES: Final = ("power", "brightness", "rgb", "color_temp", "segments", "scenes")   # models.Capability
# The capabilities a colour scheme has no frame for (wire.build refuses them): devices/profile.py's table
# check (_incoherent) and the rows' resolution both read this.
#  - h61a8 (the 0x0b frames): no colour temperature (build.color_temp / segment_color_temp);
#  - h6006 / h6188 (0x0d): no segment mask in any colour frame (Bytes.f / p / A);
#  - h6061: whole-device only; per-panel colour needs the user's layout, which is not modelled.
SCHEME_LACKS: Final[Mapping[str, frozenset[str]]] = {
    "h61a8": frozenset({"color_temp"}),
    "h6006": frozenset({"segments"}),
    "h6188": frozenset({"segments"}),
    "h6061": frozenset({"segments"}),
}
# scenes.upload.dialects -> DeviceProfile.scene_dialect; "none" = activate only.
_DIALECTS: Final = {"legacy_scene": "A", "diy_graffiti": "B_h60a6", "tablelamp_h6052_type5_diy": "B_h6052",
                    "none": "A"}
_STATUS_READ: Final = ("cmd_41", "cmd_30", "color_group")     # build.status_query(full=True)
_READS: Final = frozenset({"brightness", "gradual", "cmd_36", "color_group", "ic_num", "cmd_41"})
_PACT_FACTS: Final = frozenset({"status_read", "capabilities", "scene_versions"})
_AES_FALLBACK_SKUS: Final = frozenset({"H60A6"})   # D4: 1.x's fallback, kept for the one unlocked SKU
_LAYERED: Final = ("scene_versions", "kelvin_range", "brightness_range")
_PERCENT: Final = (1, 100)


def _layer(*layers: Mapping[str, Any]) -> dict[str, Any]:
    """The wire blocks ``layers`` merged in order, a later layer replacing single leaves."""
    out: dict[str, dict[str, Any]] = {}
    for layer in layers:
        for block in ("forms", "counts", "group", "power"):
            out.setdefault(block, {}).update(layer.get(block) or {})
    return out


def routes(families: Sequence[Family]) -> dict[str, tuple[Family, str, str]]:
    """``{SKU upper-cased: (family, goodsType, SKU)}``: each admitted BLE row, for each model it lists.
    SpecError for a SKU two rows route (that needs the pact or the goodsType, which a profile does not
    take)."""
    out: dict[str, tuple[Family, str, str]] = {}
    for fam in families:
        rows = (fam.get("client_profile") or {}).get("by_goods_type") or {}
        for gt, row in rows.items():
            if (row.get("support") not in ADMITTED or "pacts" not in row
                    or "ble" not in (row.get("connectivity") or fam.get("connectivity") or ())):
                continue
            for sku in row.get("models") or ():
                key = sku.upper()
                if key in out:
                    other = f"{out[key][0]['id']} {out[key][1]}"
                    raise SpecError(f"{sku}: routed by {other} and {fam['id']} {gt}")
                out[key] = (fam, gt, sku)
    return out


def profiles(families: Sequence[Family],
             parse_names: Collection[str]) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
    """``[(skus, fields)]``: DeviceProfile field values for each group of routed SKUs whose resolved facts
    are identical, in devices.yaml order (row models order within a row). ``parse_names`` are the
    mode_color_parse table's names. SpecError on a fact missing or not served."""
    groups: dict[str, tuple[list[str], dict[str, Any]]] = {}
    for fam, gt, sku in routes(families).values():
        fields = _fields(fam, gt, sku, parse_names)
        groups.setdefault(repr(sorted(fields.items())), ([], fields))[0].append(sku)
    return [(tuple(skus), fields) for skus, fields in groups.values()]


def _fields(fam: Family, gt: str, sku: str, parse_names: Collection[str]) -> dict[str, Any]:
    """One routed SKU's DeviceProfile field values (see the module doc for the rules)."""
    cp = fam.get("client_profile") or {}
    row = (cp.get("by_goods_type") or {})[gt]
    model = (cp.get("by_model") or {}).get(sku) or {}
    where = f"{fam['id']} goodsType {gt} ({sku})"

    def need(value: Any, name: str) -> Any:
        if value is None:
            raise SpecError(f"{where}: no {name}")
        return value

    wire = cp.get("wire") or {}
    w = _layer(wire, (wire.get("by_model") or {}).get(sku) or {},
               (wire.get("by_goods_type") or {}).get(gt) or {})
    forms, counts, group, power = w["forms"], w["counts"], w["group"], w["power"]
    relay = forms.get("f_01") == "relays"
    if model.get("by_pact") or model.get("by_firmware"):
        raise SpecError(f"{where}: by_model pact / firmware branches are not served")

    # Layered facts: the row's own (the pact unknown, the firmware not yet read), else the family specs.
    own: dict[str, object] = dict(row)
    specs: dict[str, object] = dict(fam.get("specs") or {})
    layered: dict[str, Any] = {k: own[k] for k in _LAYERED if k in own}
    kelvin_by_hardware = []
    scenes_by_firmware: list[tuple[str, str, tuple[int, ...]]] = []
    for item in row.get("by_firmware") or ():
        gates = {k for k in item if k.startswith("min_")}
        facts: dict[str, Any] = {k: v for k, v in item.items() if not k.startswith("min_")}
        if "kelvin_range" in facts:
            if gates != {"min_hard_version"} or len(facts) != 1:
                raise SpecError(f"{where}: a Kelvin firmware branch must gate on min_hard_version alone")
            lo, hi = facts["kelvin_range"]
            kelvin_by_hardware.append((str(item.get("min_hard_version")), int(lo), int(hi)))
        elif set(facts) == {"scene_versions"}:
            if not gates or not gates <= {"min_soft_version", "min_hard_version"}:
                raise SpecError(f"{where}: a scene firmware branch must gate on the soft / hard version")
            soft, hard = item.get("min_soft_version", ""), item.get("min_hard_version", "")
            scenes_by_firmware.append((soft, hard, tuple(sorted(facts["scene_versions"]))))
        else:
            raise SpecError(f"{where}: firmware branch on {sorted(facts)} is not served")
    layered.update({k: specs[k] for k in _LAYERED if k not in layered and k in specs})

    status_read = row.get("status_read")
    gate = None
    for key, branch in (row.get("by_pact") or {}).items():
        if set(branch) - _PACT_FACTS:
            extra = sorted(set(branch) - _PACT_FACTS)
            raise SpecError(f"{where}: pact branch {key} on {extra} is not served")
    if status_read is not None:
        if tuple(status_read) != _STATUS_READ:
            raise SpecError(f"{where}: status_read {list(status_read)} is not the request the library builds")
        gate = _status_gate(row, where)

    row_caps = frozenset(need(row.get("capabilities"), "capabilities"))
    caps = set(row_caps)
    if not caps <= {*CAPABILITIES, "gradual"}:
        raise SpecError(f"{where}: capabilities {sorted(caps - {*CAPABILITIES, 'gradual'})}")
    scheme = row.get("color_scheme") or cp.get("color_scheme")
    if scheme is None and caps & {"rgb", "color_temp"}:
        raise SpecError(f"{where}: no color_scheme")
    caps -= SCHEME_LACKS.get(scheme or "", frozenset())
    dialect = row.get("scene_dialect")
    if "scenes" in caps and dialect not in (*_DIALECTS, "untraced"):
        raise SpecError(f"{where}: scene_dialect {dialect!r}")
    if dialect == "untraced":
        caps.discard("scenes")
    uploads = "scenes" in caps and dialect != "none"
    scenes = tuple(sorted(need(layered.get("scene_versions"), "scene_versions"))) if uploads else ()
    kelvin = need(layered.get("kelvin_range"), "kelvin_range") if "color_temp" in caps else None
    if kelvin is None and not relay:
        kelvin = layered.get("kelvin_range")
    bright = need(layered.get("brightness_range"), "brightness_range") if "brightness" in caps else None

    reads = set(need(row.get("connect_reads"), "connect_reads"))
    if not reads <= _READS:
        raise SpecError(f"{where}: connect_reads {sorted(reads - _READS)} are not served")
    color_readback = ""
    if "color_group" in reads:
        color_readback = {"color_group": "mechanism_a_direct", "bulb_batch_v2": "mechanism_b"}.get(
            forms.get("f_a5", ""), "")
        if not color_readback:
            raise SpecError(f"{where}: colour read-back with f_a5 {forms.get('f_a5')!r}")
    live_ic = "ic_num" in reads
    ic = row.get("ic_pieces")
    if live_ic and (color_readback != "mechanism_a_direct" or ic is None):
        raise SpecError(f"{where}: an ic_num read needs the direct colour read-back and ic_pieces")
    section_count = "cmd_41" in reads
    if section_count and forms.get("f_41") != "section_count":
        raise SpecError(f"{where}: an aa 41 read without f_41 section_count")
    bar = forms.get("f_36") == "bar_pair"
    if ("cmd_36" in reads) != bar:
        raise SpecError(f"{where}: the aa 36 read and f_36 bar_pair go together")
    if tuple(need(row.get("connect_writes"), "connect_writes")) != (("plug_sync_time",) if relay else ()):
        raise SpecError(f"{where}: connect_writes {list(row.get('connect_writes') or ())} (the library sends "
                        "the plug time sync, on every relay row only)")

    zones = tuple((z["id"], z["power_index"], tuple(z["segments"])) for z in need(row.get("zones"), "zones"))
    lock = bool(need(row.get("account_lock"), "account_lock"))
    out: dict[str, Any] = {
        "capabilities": tuple(c for c in CAPABILITIES if c in caps),
        "aes_fallback": lock or sku in _AES_FALLBACK_SKUS,
        "segments": int(counts.get("num_segments", 0)),
        "zones": zones,
        "scene_versions": scenes,
        "scene_versions_by_firmware": tuple(scenes_by_firmware) if uploads else (),
        "scene_dialect": _DIALECTS.get(dialect or "none", "A"),
        "min_kelvin": kelvin[0] if kelvin else None,
        "max_kelvin": kelvin[1] if kelvin else None,
        "requires_secret": lock,
        "relay": relay,
        "bar_switch": bar,
        "global_power": bool(zones) and not bar and forms.get("f_01") == "light",
        "gradual": "gradual" in caps,
        "color_readback": color_readback,
        "color_readback_per_batch": (int(need(group.get("num_per_group"), "wire group num_per_group"))
                                     if color_readback == "mechanism_a_direct" else 3),
        "color_readback_live_ic": live_ic,
        "section_count_read": section_count,
        "brightness_range": tuple(bright) if bright is not None and tuple(bright) != _PERCENT else None,
        "status_color_groups_pact": gate,
        "power_resync": bool(row.get("power_resync", False)),
        "kelvin_by_hardware": tuple(sorted(kelvin_by_hardware, reverse=True)),
        "mode_reports_kelvin": bool(row.get("mode_reports_kelvin", True)),
    }
    if ("segments" in row_caps or color_readback) and not out["segments"]:   # before SCHEME_LACKS (h6061)
        raise SpecError(f"{where}: segments or a colour read-back without wire counts num_segments")
    if scheme is not None:
        out["color_scheme"] = scheme
    if ic is not None:
        out["color_readback_ic_range"] = (ic["min_ic"], ic["max_ic"])
        out["color_readback_ic_per_piece"] = ic["ic_per_piece"]
    if relay:
        on_mask = power.get("relay_heartbeat_on_mask") or row.get("relay_on_mask", cp.get("relay_on_mask"))
        out.update(readback="plug", relay_on_mask=int(need(on_mask, "relay_on_mask")),
                   relay_power=need(row.get("relay_power", cp.get("relay_power")), "relay_power"),
                   advert_on_mask=int(need(row.get("advert_on_mask", cp.get("advert_on_mask")),
                                           "advert_on_mask")))
    else:
        power_read = need(row.get("power_read", cp.get("power_read")), "power_read")
        if (power_read == "ac_status") != (status_read is not None):
            raise SpecError(f"{where}: power_read {power_read!r} with status_read {status_read!r}")
        out["readback"] = {"aa01": "polled", "ac_status": "status"}[power_read]
        out["mode_color_parse"] = need(
            row.get("mode_color_parse", model.get("mode_color_parse", cp.get("mode_color_parse"))),
            "mode_color_parse")
        status = row.get("mode_color_parse_status",
                         model.get("mode_color_parse_status", cp.get("mode_color_parse_status")))
        if status is not None:
            out["mode_color_parse_status"] = status
        if "untraced" in {out["mode_color_parse"], status} and caps & {"rgb", "color_temp"}:
            raise SpecError(f"{where}: mode_color_parse untraced")
        unknown = {out["mode_color_parse"], status or out["mode_color_parse"]} - set(parse_names)
        if unknown:
            raise SpecError(f"{where}: mode_color_parse {sorted(unknown)} not in the mode_color_parse table")
    return out


def _status_gate(row: Row, where: str) -> tuple[int, int] | None:
    """``status_color_groups_pact`` from the row's pact branches: None when every pact's status read asks for
    the 0xA5 colour groups, else the ``(pact type, lowest code)`` of the pacts that do. SpecError unless those
    are one pact type from that code up (the gate the library implements)."""
    branches = {key: tuple(branch["status_read"])
                for key, branch in (row.get("by_pact") or {}).items() if "status_read" in branch}
    if any(read not in (_STATUS_READ, _STATUS_READ[:2]) for read in branches.values()):
        raise SpecError(f"{where}: a pact's status_read is not a request the library builds")
    without = {k for k, read in branches.items() if read == _STATUS_READ[:2]}
    if not without:
        return None
    pacts = row.get("pacts")
    if not isinstance(pacts, tuple):
        raise SpecError(f"{where}: a status_read pact branch needs listed pacts")
    keep = sorted(tuple(int(v) for v in p.split(",")) for p in pacts if p not in without)
    drop = [tuple(int(v) for v in p.split(",")) for p in without]
    if not keep or len({t for t, _ in keep}) != 1:
        raise SpecError(f"{where}: the pacts with colour groups are not one pact type")
    pact_type, code = keep[0]
    if any(t == pact_type and c >= code for t, c in drop):
        raise SpecError(f"{where}: the pacts with colour groups are not a code range")
    return pact_type, code
