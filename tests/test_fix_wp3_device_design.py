"""Regression tests for the device-layer design fixes (review findings WP3-009 .. WP3-082).

Offline and deterministic: every connection method is mocked (writes recorded, reads scripted per
command byte, anything unscripted gets no reply), so nothing reaches a real transport.
"""
from __future__ import annotations

import asyncio
import copy
import dataclasses
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fake_ble import ble_device, frames_of
from fake_ble import frame as _frame

from govee_ble_local import (
    LOCAL_NAME_PREFIXES,
    MANUFACTURER_IDS,
    create_device,
    is_supported_sku,
    scanner,
    supported_skus,
)
from govee_ble_local import identify as identify_mod
from govee_ble_local._generated.devices import FAMILIES, MODE_COLOR_PARSE
from govee_ble_local.devices import _fold, _resolve
from govee_ble_local.devices import profile as profile_mod
from govee_ble_local.devices.device import Device, make_device
from govee_ble_local.devices.profile import PROFILES, DeviceProfile, profile_for
from govee_ble_local.exceptions import GoveeBleError, GoveeBleInvalidValue, GoveeBleNotSupported
from govee_ble_local.identify import (
    GoveeAdvertisement,
    from_advertisement,
    identify,
    parse_manufacturer_data,
    sku_from_local_name,
)
from govee_ble_local.models import Capability, Encryption, Segment
from govee_ble_local.scenes import load_scenes
from govee_ble_local.wire import build

Replies = dict[int, list[bytes]]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _mocked(d: Device, replies: Replies | None = None) -> Device:
    """Replace ``d``'s connection I/O with recorders; ``replies`` maps a read's command byte to its
    reply frames (anything else gets no reply)."""
    table = replies or {}
    d._log = []  # type: ignore[attr-defined]   # ("tx" | "rx?", frame) in call order

    async def send(frame: bytes, **_k: object) -> None:
        d._log.append(("tx", frame))  # type: ignore[attr-defined]

    async def send_many(frames: list[bytes]) -> None:
        d._log.extend(("tx", f) for f in frames)  # type: ignore[attr-defined]

    async def send_unacked(frames: Any, **_k: object) -> None:
        d._log.extend(("up", f) for f in frames_of(frames))  # type: ignore[attr-defined]

    async def query(frame: bytes, **_k: object) -> list[bytes]:
        d._log.append(("rx?", frame))  # type: ignore[attr-defined]
        return list(table.get(frame[1], []))

    d._conn.send = AsyncMock(side_effect=send)  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=send_many)  # type: ignore[method-assign]
    d._conn.send_unacked = AsyncMock(side_effect=send_unacked)  # type: ignore[method-assign]
    d._conn.query = AsyncMock(side_effect=query)  # type: ignore[method-assign]
    return d


def _dev(sku: str, replies: Replies | None = None) -> Device:
    d = make_device(ble_device("AA:BB:CC:DD:EE:3B", sku), sku)
    assert d is not None
    return _mocked(d, replies)


def _unchecked(sku: str, **changes: Any) -> Device:
    """A device on a copy of the SKU's profile changed past DeviceProfile's validation."""
    base = profile_for(sku)
    assert base is not None
    profile = copy.copy(base)
    for name, value in changes.items():
        object.__setattr__(profile, name, value)
    return _mocked(Device(profile, ble_device("AA:BB:CC:DD:EE:3B", sku), sku=sku))


def _sent(d: Device) -> list[bytes]:
    return [f for kind, f in d._log if kind == "tx"]  # type: ignore[attr-defined]


def _queried(d: Device) -> list[bytes]:
    return [f for kind, f in d._log if kind == "rx?"]  # type: ignore[attr-defined]


def _raw_adv(name: str | None, mfg: dict[int, bytes]) -> Any:
    return type("Adv", (), {"manufacturer_data": mfg, "local_name": name})()


# ── WP3-012 / WP3-013 / WP3-014: one advertisement normalisation, parsed once ─────────────
def test_constructor_parses_a_raw_advertisement_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str | None] = []
    real = identify_mod.identify

    def spy(name: str | None, mfg: dict[int, bytes]) -> GoveeAdvertisement | None:
        calls.append(name)
        return real(name, mfg)

    monkeypatch.setattr(identify_mod, "identify", spy)
    adv = _raw_adv("Govee_H6008_ABCD", {0x8841: bytes([0xEC, 0x00, 0x05, 0x02])})
    d = Device(profile_for("H6008"), ble_device("AA:BB:CC:DD:EE:3B", "x"), adv)  # type: ignore[arg-type]
    assert calls == ["Govee_H6008_ABCD"]                     # was twice (pact, then cipher)
    assert d.pact == (5, 2) and d.encryption is Encryption.AES_RC4_PSK


@pytest.mark.parametrize(
    ("local_name", "mfg"),
    [
        ("Govee_H6008_ABCD", {0x8801: bytes([0xEC, 0, 1, 2])}),
        ("Govee_H6008_ABCD", {}),                            # by name alone: pact unknown
        (None, {0x8841: bytes([0xEC, 0, 1, 2])}),            # local name missing: BLEDevice name
        ("SomethingElse", {0x8801: bytes([0xEC, 0, 1, 2])}),  # not a Govee device
    ],
)
def test_from_advertisement_matches_the_scanner(local_name: str | None, mfg: dict[int, bytes]) -> None:
    ble = ble_device("AA:BB:CC:DD:EE:3B", "ihoment_H6008_1234")
    adv = _raw_adv(local_name, mfg)
    hit = scanner.match(ble, adv)
    assert from_advertisement(adv, ble.name) == (hit.advertisement if hit is not None else None)


def test_from_advertisement_passes_a_parsed_advertisement_through() -> None:
    parsed = identify("Govee_H6008_ABCD", {0x8801: bytes([0xEC, 0, 1, 2])})
    assert parsed is not None
    assert from_advertisement(parsed, "ignored") is parsed
    assert from_advertisement(None) is None
    assert from_advertisement(object(), "Govee_H6008_ABCD") == identify("Govee_H6008_ABCD", {})


def test_device_accepts_raw_and_parsed_advertisements_alike() -> None:
    raw = _raw_adv("Govee_H60A6_ABCD", {0x8801: bytes([0xEC, 0, 3, 1])})
    parsed = from_advertisement(raw)
    for adv in (raw, parsed):
        d = Device(profile_for("H60A6"), ble_device("AA:BB:CC:DD:EE:3B", "x"), adv)  # type: ignore[arg-type]
        assert d.pact == (3, 1) and d.encryption is Encryption.NONE   # plaintext header beats AES
    unknown = _raw_adv("Govee_H60A6_ABCD", {})                         # pact unknown: profile default
    d = Device(profile_for("H60A6"), ble_device("AA:BB:CC:DD:EE:3B", "x"), unknown)  # type: ignore[arg-type]
    assert d.pact is None and d.encryption is Encryption.AES_RC4_PSK


def test_passive_matcher_constants_agree_with_identify() -> None:
    # The public tuple keeps its value (LOCAL_NAME_PREFIXES is semver API) ...
    assert LOCAL_NAME_PREFIXES == ("ihoment_", "Govee_", "Minger_", "GVH", "GVR", "GV", "GBK_")
    # ... and identify() accepts a well-formed name under every prefix in it ...
    names = {"ihoment_": "ihoment_H5083_A2D1", "Govee_": "Govee_H5083_A2D1",
             "Minger_": "Minger_H5083_A2D1", "GBK_": "GBK_H5083_A2D1", "GVH": "GVH5083_A2D1",
             "GVR": "GVR5083_A2D1", "GV": "GV50831A2B"}
    assert set(names) == set(LOCAL_NAME_PREFIXES)
    for prefix, name in names.items():
        assert name.startswith(prefix) and sku_from_local_name(name) is not None, prefix
    # ... and every listed company id parses as a Govee broadcast.
    for cid in MANUFACTURER_IDS:
        assert parse_manufacturer_data({cid: bytes([0xEC, 0, 1, 2])}) is not None, hex(cid)


# ── WP3-067: the -1 pact sentinel behind typed properties ─────────────────────────────────
def test_advertisement_pact_and_encryption_known() -> None:
    known = identify("Govee_H6008_ABCD", {0x8841: bytes([0xEC, 0x01, 0x02, 0x03])})
    assert known is not None
    assert known.pact == (0x0102, 3) and known.encryption_known and known.encrypted
    unknown = identify("Govee_H6008_ABCD", {})
    assert unknown is not None
    assert unknown.pact is None and not unknown.encryption_known
    # properties, not fields: equality and the positional constructor are unchanged
    assert unknown == GoveeAdvertisement("H6008", "Govee_H6008_ABCD", False, 0, -1, -1)
    assert [f.name for f in dataclasses.fields(GoveeAdvertisement)] == [
        "sku", "name", "encrypted", "broadcast_version", "pact_type", "pact_code"]


# ── WP3-015: snapshot() is a copy, state is live ──────────────────────────────────────────
def test_snapshot_is_independent_of_later_changes() -> None:
    d = _dev("H60A6")
    run(d.set_segment_rgb([0, 1], (1, 2, 3)))
    run(d.set_zone_power("main", True))
    before = d.snapshot()
    assert before == d.state and before is not d.state
    assert before.segments is not d.state.segments and before.zone_power is not d.state.zone_power
    run(d.set_segment_rgb([0], (9, 9, 9)))
    run(d.set_zone_power("main", False))
    assert before.segments[0] == Segment(0, (1, 2, 3)) and before.zone_power == {0: True}
    assert d.state.segments[0].rgb == (9, 9, 9) and d.state.zone_power[0] is False
    before.zone_power[1] = True                                # editing a snapshot is harmless
    assert 1 not in d.state.zone_power


def test_state_and_callbacks_still_share_the_live_object() -> None:
    d = _dev("H6008")
    seen: list[object] = []
    d.register_callback(seen.append)
    run(d.set_power(True))
    assert seen == [d.state] and seen[0] is d.state            # unchanged: no copy per notify


# ── WP3-022 / WP3-051 / WP3-063 / WP3-064 / WP3-027 / WP3-035: profile validation ──────────
def test_every_table_profile_is_valid_and_skus_are_unique() -> None:
    owners: dict[str, list[DeviceProfile]] = {}
    for p in PROFILES:
        assert dataclasses.replace(p) == p                     # re-validates
        assert len({sku.upper() for sku in p.skus}) == len(p.skus)
        for sku in p.skus:
            owners.setdefault(sku.upper(), []).append(p)
    # A SKU in two profiles (carlightv1 6 / 39: raw vs percent brightness) is told apart by the advertised
    # pact.
    for sku, profiles in owners.items():
        if len(profiles) > 1:
            pacts = {pact for route in profile_mod._ADMITTED[1][sku] for pact in route.pacts or ()}
            assert {id(profile_for(sku, pact=pact)) for pact in pacts} == {id(p) for p in profiles}, sku


@pytest.mark.parametrize("skus", [(), ("",), "H6008", (None,), []])
def test_profile_needs_a_non_empty_tuple_of_skus(skus: Any) -> None:
    with pytest.raises(GoveeBleInvalidValue, match="skus"):
        DeviceProfile(skus=skus, capabilities=frozenset())


def test_profile_skus_given_as_a_list_are_kept_as_a_tuple() -> None:
    # 1.3.0 accepted a list here; it still does, kept as the declared tuple (so the profile hashes).
    p = DeviceProfile(skus=["HX1", "HX2"], capabilities=frozenset())  # type: ignore[arg-type]
    assert p.skus == ("HX1", "HX2")
    assert hash(p) == hash(DeviceProfile(skus=("HX1", "HX2"), capabilities=frozenset()))


def test_empty_skus_no_longer_fail_late_with_an_index_error() -> None:
    # WAS: DeviceProfile(skus=()) was accepted and Device() raised a bare IndexError.
    with pytest.raises(GoveeBleError):
        Device(DeviceProfile(skus=(), capabilities=frozenset()), ble_device("AA:BB:CC:DD:EE:3B", "x"))


def _with_row(gt: str, **row: Any) -> Any:
    """The generated families with one more bulblightv3 row ``gt``: goodsType 64's, with ``row``'s facts (not
    64's catalogue routes, which the snapshot gives 64 alone, nor its colour page where ``row`` drops
    colour temperature: the resolver takes ``color_page`` only with it)."""
    families = copy.deepcopy(FAMILIES)
    rows = next(f for f in families if f["id"] == "bulblightv3")["client_profile"]["by_goods_type"]
    rows[gt] = {**{k: v for k, v in rows["64"].items() if k != "catalogue_models"}, **row}
    if "color_temp" not in rows[gt]["capabilities"]:
        rows[gt].pop("color_page", None)
    return families


def test_a_sku_several_rows_list_is_routed_by_its_pact(monkeypatch: pytest.MonkeyPatch,
                                                       caplog: pytest.LogCaptureFixture) -> None:
    # WAS: a SKU that two rows list failed the build ("routed by two rows"). profile_for now takes the row
    # that accepts the advertised pact, else the goodsType; with neither it is unsupported
    # (devices.schema.json clientRow), and create_device and make_device route it from the advertisement
    # they are given. A pact heard later that the device's rows refuse is logged; the profile stays.
    families = _with_row("65", models=("H6008",), pacts=("3,1",), capabilities=("power", "brightness"))
    monkeypatch.setattr(profile_mod, "_ADMITTED", profile_mod._table(families, _resolve.ADMITTED))
    bulb, dimmer = profile_for("H6008", pact=(1, 1)), profile_for("H6008", pact=(3, 1))
    assert bulb is not None and Capability.RGB in bulb.capabilities
    assert dimmer is not None and dimmer.capabilities == {Capability.POWER, Capability.BRIGHTNESS}
    assert profile_for("H6008", goods_type=65) is dimmer and profile_for("h6008", goods_type=64) is bulb
    assert profile_for("H6008") is None and profile_for("H6008", pact=(9, 9)) is None
    assert profile_for("H6006") is bulb and "H6008" in supported_skus()   # one row: no pact needed
    adv = GoveeAdvertisement("H6008", "GVH60082691", False, 3, 3, 1)
    assert create_device(ble_device("AA:BB:CC:DD:EE:3C", adv.name), "H6008", adv).profile is dimmer
    assert "H6008" not in _resolve.routes(families) and "H6006" in _resolve.routes(families)
    caplog.set_level("WARNING", logger="govee_ble_local")
    d = make_device(ble_device("AA:BB:CC:DD:EE:3C", adv.name), "H6008", adv)
    assert d is not None and d.profile is dimmer and d.pact == (3, 1) and not caplog.records
    assert make_device(ble_device("AA:BB:CC:DD:EE:3C", adv.name), "H6008",
                       dataclasses.replace(adv, pact_type=9, pact_code=9)) is None
    d._learn_pact((1, 1))   # the bulb row's pact, not the dimmer's
    d._learn_pact((1, 1))   # unchanged: not logged again
    assert d.pact == (1, 1) and d.profile is dimmer
    assert [r.getMessage().split(": ", 1)[1][:28] for r in caplog.records] == ["advertises pact 1,1, which n"]
    # Where the other row differs only in what a pact picks within a row, the later pact re-selects it, as the
    # app runs the row the unit's pact gives it (profile.reroute): an H6118 built without a pact has goodsType
    # 6's raw brightness, and its BK pact (10, 1) gives goodsType 39's percent; (1, 1) gives 6 back.
    bk = make_device(ble_device("AA:BB:CC:DD:EE:3E", "ihoment_H6118_ABCD"), "H6118")
    assert bk is not None and bk.profile is profile_for("H6118") and bk.profile.brightness_range == (20, 254)
    for pact, raw in (((10, 1), None), ((1, 1), (20, 254))):
        bk._learn_pact(pact)
        assert bk.profile is profile_for("H6118", pact=pact) and bk.profile.brightness_range == raw, pact
    # The keep-alive opt-out (heartbeat=None) is kept across the re-selection, and doesn't block it.
    base = profile_for("H6118")
    assert base is not None and base.heartbeat is not None
    quiet = Device(dataclasses.replace(base, heartbeat=None), ble_device("AA:BB:CC:DD:EE:3F", "GVH6118"),
                   sku="H6118")
    quiet._learn_pact((10, 1))
    bk_pact = profile_for("H6118", pact=(10, 1))
    assert bk_pact is not None and quiet.profile == dataclasses.replace(bk_pact, heartbeat=None)
    assert len(caplog.records) == 1   # no re-selection warns


def test_a_modelled_row_needs_the_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    # "App-traced by default": a modelled row (inferred, not traced) is admitted only with allow_modelled,
    # gated on its pacts. The build refuses a SKU that it and a traced row both list, so the opt-in only adds.
    with pytest.raises(_resolve.SpecError, match="also listed by bulblightv3 64"):
        _resolve.routes(_with_row("65", models=("H6008",), support="modelled"))
    families = _with_row("65", models=("HX65",), pacts=("3,1",), support="modelled",
                         capabilities=("power", "brightness"))
    monkeypatch.setattr(profile_mod, "_MODELLED", profile_mod._table(families, _resolve.MODELLED))
    assert profile_for("HX65") is None and not is_supported_sku("HX65", pact=(3, 1))
    assert "HX65" not in supported_skus() and "HX65" in supported_skus(allow_modelled=True)
    modelled = profile_for("HX65", allow_modelled=True)
    assert modelled is not None and modelled.capabilities == {Capability.POWER, Capability.BRIGHTNESS}
    assert profile_for("HX65", pact=(1, 1), allow_modelled=True) is None   # no traced row to fall back to
    ble = ble_device("AA:BB:CC:DD:EE:3D", "GVHX65")
    with pytest.raises(GoveeBleNotSupported, match="HX65"):
        create_device(ble, "HX65")
    assert create_device(ble, "HX65", allow_modelled=True).profile is modelled
    mfg = {0x8801: bytes([0xEC, 0, 3, 1])}                  # pact (3, 1)
    assert not scanner.supported("Govee_HX65_ABCD", mfg) and scanner.supported("Govee_HX65_ABCD", mfg,
                                                                                 allow_modelled=True)
    hit = scanner.match(ble, _raw_adv("Govee_HX65_ABCD", mfg), allow_modelled=True)
    assert hit is not None and hit.supported


def test_a_scheme_without_segment_brightness_is_not_supported_with_the_sku() -> None:
    # SEGMENTS is valid on scheme h61a8 (the 0x0b frames: per-segment RGB), which has no per-segment
    # brightness: the narrower gap stays with the builder, GoveeBleNotSupported named for the SKU. No
    # built-in profile uses the scheme since the H61A8 sends the 0x15 forms (291bda3); a custom one may.
    d = _unchecked("H61A8", color_scheme="h61a8")
    with pytest.raises(GoveeBleNotSupported, match=r"^H61A8: "):
        run(d.set_segment_brightness([0], 50))
    assert _sent(d) == []
    # The scheme itself still drives the RGB paths with its 0x0b frames: whole strip and one segment.
    run(d.set_rgb((0xFF, 0, 0)))
    run(d.set_segment_rgb([0], (0, 0xFF, 0)))
    assert [f[:6].hex() for f in _sent(d)] == ["33050bff0000", "33050b00ff00"]


def test_the_direct_group_size_is_the_specs() -> None:
    # WAS: the table refused a direct read-back in groups other than 4. The group size is the row's wire
    # group num_per_group (4 on every current SKU), which the table takes as it is; a custom profile walks
    # the group size it was given.
    families = copy.deepcopy(FAMILIES)
    next(f for f in families if f["id"] == "h6047")["client_profile"]["wire"]["group"]["num_per_group"] = 3
    [fields] = [f for skus, f, _routes in _resolve.profiles(families, MODE_COLOR_PARSE) if "H6047" in skus]
    assert fields["color_readback_per_batch"] == 3
    three = DeviceProfile(skus=("HX",), capabilities=frozenset(), color_readback="mechanism_a_direct",
                          color_readback_per_batch=3)
    assert three.color_readback_per_batch == 3
    for bad in (0, -1):   # a group size no read-back can walk is refused at construction
        with pytest.raises(GoveeBleInvalidValue, match="color_readback_per_batch"):
            DeviceProfile(skus=("HX",), capabilities=frozenset(), color_readback="mechanism_b",
                          color_readback_per_batch=bad)


def test_brightness_max_must_be_positive() -> None:
    # 0 would divide by zero on the first brightness read-back.
    with pytest.raises(GoveeBleInvalidValue, match="brightness_max"):
        DeviceProfile(skus=("HX",), capabilities=frozenset(), brightness_max=0)
    # A Kelvin range by hardware version: (version, low, high) entries with 0 < low <= high, highest version
    # first and once each, on a base range they each overlap, leaving a range valid on every revision; a
    # list of lists is taken as the tuple.
    base = {"min_kelvin": 2000, "max_kelvin": 9000}
    for bad, kw in (
        ((("3.08.01", 6500, 2700),), base), ((("", 2700, 6500),), base), ((("3.08.01", 0, 6500),), base),
        (((2, 3),), base), ((("3.08.01", 2700, 6500),), {}),                         # no base range
        ((("3.08.01", 9500, 9900),), base),                                          # outside the base
        ((("3.01.00", 2700, 6500), ("3.08.01", 2700, 6500)), base),                  # lowest first
        ((("3.08.01", 2000, 2500), ("3.01.00", 8000, 9000)), base),                  # none valid on all
    ):
        with pytest.raises(GoveeBleInvalidValue, match="kelvin_by_hardware"):
            DeviceProfile(skus=("HX",), capabilities=frozenset(), kelvin_by_hardware=bad, **kw)  # type: ignore[arg-type]
    listed = DeviceProfile(skus=("HX",), capabilities=frozenset(), **base,
                           kelvin_by_hardware=[["3.08.01", 2700, 6500]])  # type: ignore[arg-type]
    assert listed.kelvin_by_hardware == (("3.08.01", 2700, 6500),)
    DeviceProfile(skus=("HX",), capabilities=frozenset(), brightness_max=255)
    # Ranges by pact apply on that advertised pact; a Kelvin one needs a base range it overlaps, no
    # kelvin_by_hardware and a range valid on every pact, a brightness one is a raw range or None (percent).
    k41 = {"kelvin_by_pact": (((4, 1), 1000, 10000),)}
    for bad in (k41, {**k41, **base, "kelvin_by_hardware": (("3.08.01", 2700, 6500),)},
                {**base, "kelvin_by_pact": (((4, 1), 9500, 9900),)},                  # outside the base
                {**base, "kelvin_by_pact": (((4, 1), 2000, 2500), ((4, 2), 8000, 9000))},   # none on all
                {"brightness_by_pact": (((2, 1), (0, 300)),)},
                {"status_read_by_pact": (((1, 2), (0x41, 0x41)),)},                         # a repeated type
                {"status_read_by_pact": (((1, 2), (0x41,)), ((1, 2), (0x42,)))}):           # a pact twice
        with pytest.raises(GoveeBleInvalidValue, match="_by_pact"):
            DeviceProfile(skus=("HX",), capabilities=frozenset(), **bad)  # type: ignore[arg-type]
    d = Device(DeviceProfile(skus=("HX",), capabilities=frozenset(), **base, **k41, brightness_range=(6, 254),
                             brightness_by_pact=[[[2, 1], None]]),  # type: ignore[arg-type]
               ble_device("AA:BB:CC:DD:EE:4B", "HX"))
    # Before the pact is heard: the Kelvin range valid on every pact, and no brightness byte either way,
    # since the pact picks its range.
    assert (d.min_kelvin, d.max_kelvin, d._brightness_pct(50)) == (2000, 9000, None)
    with pytest.raises(GoveeBleNotSupported, match="pact"):
        d._brightness_frame(50)
    narrow = DeviceProfile(skus=("HX",), capabilities=frozenset(), **base,
                           kelvin_by_pact=(((4, 1), 2700, 6500),))
    assert _fold.kelvin_range(narrow) == (2700, 6500)
    assert _fold.kelvin_range(narrow, pact=(1, 1)) == (2000, 9000)
    d._pact = (4, 1)
    assert (d.min_kelvin, d.max_kelvin, d._brightness_pct(50)) == (1000, 10000, 19)
    d._pact = (2, 1)
    assert (d.max_kelvin, d._brightness_pct(50), d._brightness_frame(50)) == (9000, 50, build.brightness(50))
    # Kelvin by soft version: as by hardware version, one kind per profile.
    soft = {"min_kelvin": 2700, "max_kelvin": 6500, "kelvin_by_software": [["1.00.15", 1000, 10000]]}
    with pytest.raises(GoveeBleInvalidValue, match="one kind only"):
        DeviceProfile(skus=("HX",), capabilities=frozenset(), **soft,  # type: ignore[arg-type]
                      kelvin_by_hardware=(("3.08.01", 2700, 6500),))
    d = Device(DeviceProfile(skus=("HX",), capabilities=frozenset(), **soft),  # type: ignore[arg-type]
               ble_device("AA:BB:CC:DD:EE:4C", "HX"))
    assert (d.min_kelvin, d.max_kelvin) == (2700, 6500)   # unread: the range valid on every revision
    d._state.firmware_version = "1.00.15"
    assert (d.min_kelvin, d.max_kelvin) == (1000, 10000)
    d._state.firmware_version = "1.00.14"
    assert (d.min_kelvin, d.max_kelvin) == (2700, 6500)


def test_profile_errors_are_library_errors_and_value_errors() -> None:
    with pytest.raises(GoveeBleError) as err:
        DeviceProfile(skus=("HX",), capabilities=frozenset(), readback="bogus")  # type: ignore[arg-type]
    assert isinstance(err.value, ValueError) and isinstance(err.value, GoveeBleInvalidValue)


# ── WP3-027 / WP3-035: every scheme-dependent builder call names the SKU ───────────────────
@pytest.mark.parametrize(
    "call",
    [lambda d: d.set_color_temp(3000), lambda d: d.turn_on(kelvin=3000, power=False)],
)
def test_colour_temperature_on_h61a8_scheme_is_not_supported_with_the_sku(call: Any) -> None:
    d = _unchecked("H61A8", color_scheme="h61a8",
                   capabilities=frozenset({Capability.POWER, Capability.COLOR_TEMP}))
    with pytest.raises(GoveeBleNotSupported, match=r"^H61A8: h61a8 has no colour-temperature"):
        run(call(d))
    assert _sent(d) == []


@pytest.mark.parametrize(
    "call",
    [lambda d: d.set_segment_rgb([0], (1, 2, 3)), lambda d: d.set_segment_color_temp([0], 3000),
     lambda d: d.set_segment_brightness([0], 50)],
)
def test_segment_writes_on_h6061_scheme_are_not_supported_with_the_sku(call: Any) -> None:
    base = profile_for("H6061")
    assert base is not None
    d = _unchecked("H6061", capabilities=base.capabilities | {Capability.SEGMENTS})
    with pytest.raises(GoveeBleNotSupported, match=r"^H6061: h6061 is whole-device only"):
        run(call(d))
    assert _sent(d) == []


# ── WP3-028 / WP3-058: one write protocol, one read protocol ──────────────────────────────
_SETTERS: list[tuple[str, Callable[[Device], Any], bool]] = [
    ("H6008", lambda d: d.set_power(True), True),
    ("H6008", lambda d: d.turn_on(brightness=40, rgb=(1, 2, 3)), True),
    ("H6008", lambda d: d.set_brightness(30), True),
    ("H6008", lambda d: d.set_rgb((1, 2, 3)), True),
    ("H6008", lambda d: d.set_color_temp(4000), True),
    ("H60A6", lambda d: d.set_segment_rgb([0], (1, 2, 3)), True),
    ("H60A6", lambda d: d.set_segment_brightness([0], 30), True),
    ("H6098", lambda d: d.set_segment_color_temp([0], 4000), True),   # none on an H60A6 (decision 24)
    ("H60A6", lambda d: d.set_zone_power("main", True), True),
    ("H60A6", lambda d: d.set_zone_rgb("main", (1, 2, 3)), True),
    ("H60A6", lambda d: d.set_zone_color_temp("main", 4000), True),
    ("H60A6", lambda d: d.set_scene(5), True),
    ("H61A8", lambda d: d.set_gradual(True), False),
]


@pytest.mark.parametrize(("sku", "setter", "authoritative"), _SETTERS)
def test_every_setter_invalidates_in_flight_reads_once_and_notifies(
    sku: str, setter: Callable[[Device], Any], authoritative: bool,
) -> None:
    d = _dev(sku)
    notified: list[object] = []
    d.register_callback(notified.append)
    seq = d._mutation_seq
    run(setter(d))
    assert d._mutation_seq == seq + 1 and len(notified) == 1
    assert (d._advert_ignore_until > 0) is authoritative


@pytest.mark.parametrize(("sku", "setter", "_auth"), _SETTERS)
def test_a_failed_write_applies_nothing(sku: str, setter: Callable[[Device], Any], _auth: bool) -> None:
    d = _dev(sku)
    d._conn.send = AsyncMock(side_effect=GoveeBleError("down"))  # type: ignore[method-assign]
    d._conn.send_many = AsyncMock(side_effect=GoveeBleError("down"))  # type: ignore[method-assign]
    notified: list[object] = []
    d.register_callback(notified.append)
    before, seq = d.snapshot(), d._mutation_seq
    with pytest.raises(GoveeBleError, match="down"):
        run(setter(d))
    assert d.state == before and d._mutation_seq == seq and notified == []
    assert d._advert_ignore_until == 0


def test_read_fresh_reports_none_stale_and_applied() -> None:
    d = _dev("H6008")
    applied: list[int] = []

    async def nothing() -> int | None:
        return None

    async def raced() -> int | None:
        d._mutated()                                           # a setter landed meanwhile
        return 1

    async def fresh() -> int | None:
        return 2

    assert run(d._read_fresh("x", nothing, applied.append)) == "none"
    discarded = d._discarded_reads
    assert run(d._read_fresh("x", raced, applied.append)) == "stale"
    assert d._discarded_reads == discarded + 1
    assert run(d._read_fresh("x", fresh, applied.append)) == "applied"
    assert applied == [2]


def test_set_rgb_single_frame_is_one_batched_write() -> None:
    d = _dev("H6008")
    run(d.set_rgb((1, 2, 3)))
    d._conn.send_many.assert_awaited_once()  # type: ignore[attr-defined]
    d._conn.send.assert_not_awaited()  # type: ignore[attr-defined]
    assert len(_sent(d)) == 1


def test_turn_on_and_set_color_temp_fold_the_same_kelvin_state() -> None:
    a, b = _dev("H6047"), _dev("H6047")
    run(a.set_scene(5))
    run(b.set_scene(5))
    run(a.set_color_temp(4000))
    run(b.turn_on(kelvin=4000, power=False))
    assert a.state == b.state
    assert a.state.color_temp_kelvin == 4000 and a.state.rgb_color is None and a.state.scene_code is None
    assert a.state.zone_color_temp_kelvin == {0: 4000, 1: 4000}


# ── WP3-036: an unknown zone or scene name is invalid input ───────────────────────────────
@pytest.mark.parametrize(
    "call",
    [lambda d: d.set_zone_power("nope", True), lambda d: d.set_zone_rgb("nope", (1, 2, 3)),
     lambda d: d.set_zone_color_temp("nope", 4000), lambda d: d.set_scene_by_name("No Such Scene")],
)
def test_unknown_zone_or_scene_is_invalid_value_and_still_not_supported(call: Any) -> None:
    d = _dev("H60A6")
    with pytest.raises(GoveeBleInvalidValue, match="unknown") as err:
        run(call(d))
    assert isinstance(err.value, GoveeBleNotSupported)         # existing handlers keep matching
    assert _sent(d) == [] and _queried(d) == []


def test_unknown_zone_lookups_are_invalid_value() -> None:
    d = _dev("H60A6")
    for lookup in (d.zone_is_on, d.zone_color_temp_kelvin):
        with pytest.raises(GoveeBleInvalidValue):
            lookup("nope")
    with pytest.raises(GoveeBleNotSupported):
        d.zone_is_on("nope")


# ── WP3-040: zone_power is replaced, never edited in place ────────────────────────────────
def test_zone_power_write_replaces_the_dict() -> None:
    d = _dev("H60A6")
    run(d.set_power(True))
    prev = d.state.zone_power
    run(d.set_zone_power("main", False))
    assert prev == {0: True, 1: True}                          # the old reference is untouched
    assert d.state.zone_power == {0: False, 1: True} and d.state.zone_power is not prev
    assert d.state.is_on is True                               # background still lit


# ── WP3-051 / WP3-063: the walk uses the profile's group size as given ─────────────────────
def test_direct_walk_groups_follow_the_profile() -> None:
    d = _dev("H6047")
    run(d._read_mechanism_a_direct())
    groups = [f[2] for f in _queried(d) if f[1] == build.CMD_BULB_COLOR_V2]
    assert groups == [1, 2, 3]                                 # 10 segments / 4 per group


# ── WP3-060: the aa 05 read is gated on capabilities, not colour-scheme names ──────────────
# Scoped to the rows with a field the report carries. H7017 (power and brightness) reads none: its app
# connect set has no aa 05 (ControllerStrategyConfig.k1), which the capability gate below follows.
_REPORTED = frozenset({Capability.RGB, Capability.COLOR_TEMP, Capability.SCENES})


@pytest.mark.parametrize("p", [p for p in PROFILES if p.readback == "polled" and p.capabilities & _REPORTED],
                         ids=lambda p: p.skus[0])
def test_every_polled_profile_still_reads_the_mode_report(p: DeviceProfile) -> None:
    d = _mocked(Device(p, ble_device("AA:BB:CC:DD:EE:3B", p.skus[0]), sku=p.skus[0]))
    run(d._read_polled())
    assert build.mode_query() in _queried(d)


def test_mode_report_read_follows_the_capabilities() -> None:
    plain = _unchecked("H6008", capabilities=frozenset({Capability.POWER, Capability.BRIGHTNESS}))
    run(plain._read_polled())
    assert build.mode_query() not in _queried(plain)           # nothing the report carries
    assert build.brightness_query() in _queried(plain)
    rgb_only = _unchecked("H6008", capabilities=frozenset({Capability.POWER, Capability.RGB}))
    run(rgb_only._read_polled())
    assert build.mode_query() in _queried(rgb_only)            # its colour is only reported there
    assert build.brightness_query() not in _queried(rgb_only)  # aa 04 goes with the capability (H7004)


# ── WP3-082: no read-back between a scene's upload and its activation ──────────────────────
async def test_poll_waits_until_the_scene_upload_is_activated() -> None:
    name = next(n for n, s in load_scenes("H6006").items()
                if Device.scene_upload_frames(_dev("H6006"), s))
    d = _dev("H6006", {build.CMD_SWITCH: [_frame(0xAA, 0x01, 0x01)]})
    entered, release = asyncio.Event(), asyncio.Event()
    stream = d._conn.send_unacked.side_effect  # type: ignore[attr-defined]

    async def slow_upload(frames: list[bytes], **k: object) -> None:
        entered.set()
        await release.wait()                                   # the upload is still streaming
        await stream(frames, **k)

    d._conn.send_unacked = AsyncMock(side_effect=slow_upload)  # type: ignore[method-assign]
    d._scenes_warm = True                                      # the poll goes straight to its reads
    scene = asyncio.create_task(d.set_scene_by_name(name))
    await entered.wait()
    poll = asyncio.create_task(d.update())
    for _ in range(20):
        await asyncio.sleep(0)
    assert _queried(d) == []                                   # the poll is held at the gate
    release.set()
    await asyncio.gather(scene, poll)
    kinds = [kind for kind, _f in d._log]  # type: ignore[attr-defined]
    activate = kinds.index("tx")
    assert set(kinds[:activate]) == {"up"} and _sent(d)[0][:3] == bytes([0x33, 0x05, 0x04])
    assert kinds[activate + 1:] and set(kinds[activate + 1:]) == {"rx?"}
