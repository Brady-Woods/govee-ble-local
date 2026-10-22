"""The keep-alive in the spec (devices.yaml clientRow heartbeat / heartbeat_timing, timing.heartbeat) against
the library: each SKU's profile, the power_live cross-check, the transport's interval, the pact-gated aa 01
rows, the frames the builder makes and the one-field opt-out on every SKU. No hardware."""
from __future__ import annotations

import asyncio
import collections
import copy
import dataclasses
import importlib.util
import json
import pathlib
import sys
from typing import Any

import pytest
from conftest import require_optional, routing_pact

from govee_ble_local import const, supported_skus
from govee_ble_local._generated.devices import FAMILIES
from govee_ble_local.devices import _resolve
from govee_ble_local.devices.device import Device
from govee_ble_local.devices.profile import DeviceProfile, profile_for, refuses_pact
from govee_ble_local.exceptions import GoveeBleInvalidValue
from govee_ble_local.models import Encryption
from govee_ble_local.transport import connection as conn_mod
from govee_ble_local.wire import _spec, build
from govee_ble_local.wire.describe import describe_frame

from fake_ble import FakeGovee, FirmwareGovee, ble_device, frame, virtual_time
from firmware_sim import image_skus

_SPEC = pathlib.Path(__file__).resolve().parent.parent / "spec"
_C = _spec.Common
_BEATS = ("power", "heartbeat", "volume", "cmd_36")


def _doc() -> Any:
    yaml = require_optional("yaml", reason="the spec checks read spec/devices.yaml")
    return yaml.safe_load((_SPEC / "devices.yaml").read_text(encoding="utf-8"))


def _rows(doc: Any) -> list[tuple[Any, str, Any]]:
    """Every (family, goodsType, row) with support."""
    return [(fam, gt, row) for fam in doc["families"]
            for gt, row in ((fam.get("client_profile") or {}).get("by_goods_type") or {}).items()
            if "support" in row]


def _reference() -> Any:
    name = "govee_reference_heartbeat"
    spec = importlib.util.spec_from_file_location(name, _SPEC / "govee_reference.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_the_schema_requires_a_heartbeat_on_every_supported_row() -> None:
    jsonschema = require_optional("jsonschema")
    doc = _doc()
    schema = json.loads((_SPEC / "devices.schema.json").read_text(encoding="utf-8"))
    rows = _rows(doc)
    assert len(rows) == 187 and all(row["heartbeat"] in _BEATS for _fam, _gt, row in rows)
    jsonschema.validate(instance=doc, schema=schema)
    missing = copy.deepcopy(doc)
    next(f for f in missing["families"] if f["id"] == "h60ax")["client_profile"]["by_goods_type"]["303"].pop(
        "heartbeat")
    with pytest.raises(jsonschema.ValidationError, match="'heartbeat' is a required property"):
        jsonschema.validate(instance=missing, schema=schema)
    other = copy.deepcopy(doc)
    next(f for f in other["families"] if f["id"] == "h60ax")["client_profile"]["by_goods_type"]["303"].update(
        heartbeat="brightness")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=other, schema=schema)


def test_each_skus_profile_sends_its_rows_heartbeat() -> None:
    # Every route of the generated table (a row's models and its catalogue routes, every support level),
    # against the row as the spec's reference resolver reads it from devices.yaml.
    doc, reference = _doc(), _reference()
    seen: set[str] = set()
    for fam in FAMILIES:
        for gt, row in fam["client_profile"]["by_goods_type"].items():
            if "support" not in row:
                continue
            ref = reference.client_row(doc, fam["id"], int(gt))
            want = (int(_C.Command[ref["heartbeat"]]), ref.get("heartbeat_timing", "fixed_rate"))
            for sku in (*row.get("models", ()), *row.get("catalogue_models", ())):
                profile = profile_for(sku, pact=routing_pact(sku, goods_type=int(gt), allow_modelled=True),
                                      goods_type=int(gt), allow_modelled=True)
                assert profile is not None, (sku, gt)
                assert (profile.heartbeat, profile.heartbeat_timing) == want, (sku, gt)
                seen.add(sku.upper())
    assert seen == set(supported_skus(allow_modelled=True))
    # after_quiet only on the ctlchannel plug screen (goodsType 43: H5080 / H5083 / H5085).
    quiet = {(fam["id"], gt) for fam, gt, row in _rows(doc) if row.get("heartbeat_timing") == "after_quiet"}
    assert quiet == {("h5080", "43")}
    assert {sku for sku in seen if (p := profile_for(sku, allow_modelled=True)) is not None
            and p.heartbeat_timing == "after_quiet"} == {"H5080", "H5083", "H5085"}


def test_a_heartbeat_power_live_names_the_read_the_screen_sends() -> None:
    # The resolver's one-way cross-check (devices/_resolve.py), as the traces found it on all 187 rows: the
    # screens that take their power from a heartbeat reply send that read, on a polled or a status row, and
    # the plugs read their outlets from aa 01. Nothing ties a push_30-only row to aa 00, which 64 send.
    # h705a 394 sends aa 00 and has no live power source.
    found: collections.Counter[tuple[tuple[str, ...], bool, str]] = collections.Counter()
    for fam, _gt, row in _rows(_doc()):
        lives = tuple(row.get("power_live", fam["client_profile"].get("power_live")) or ())
        relay = "relay_power" in row
        found[lives, relay, row["heartbeat"]] += 1
    assert found == {
        (("heartbeat_aa01",), False, "power"): 89,
        (("heartbeat_aa01", "push_30"), False, "power"): 27,
        (("heartbeat_aa36",), False, "cmd_36"): 2,
        ((), True, "power"): 4,
        (("push_30",), False, "heartbeat"): 64,
        ((), False, "heartbeat"): 1,
    }


def test_the_transport_interval_is_the_specs() -> None:
    beat = _doc()["timing"]["heartbeat"]
    assert const.HEARTBEAT_INTERVAL * 1000 == beat["interval_ms"] == 2000
    assert const.HEARTBEAT_WRITE_TIMEOUT < const.HEARTBEAT_INTERVAL


def test_the_pact_gated_aa01_rows_keep_their_pacts() -> None:
    # h7040 / h7080: NewDetailVm.I3 = Support.supportPactV1 sends aa 01 only at exactly these accepted pacts,
    # and aa 00 at any other. Widening a row's pacts means revisiting its heartbeat.
    pacts = {(fam["id"], gt): (row["pacts"], row["heartbeat"]) for fam, gt, row in _rows(_doc())
             if fam["id"] in ("h7040", "h7080")}
    assert pacts == {("h7040", "36"): (["1,1"], "power"), ("h7080", "45"): (["2,1"], "power"),
                     ("h7080", "55"): (["2,1"], "power"), ("h7080", "56"): (["2,1"], "power")}


def test_the_traced_unsupported_rows_keep_their_heartbeat() -> None:
    # Traced but not supported: no profile is built from these rows, so neither the resolver nor the
    # generator reads them. h6047 288 / 298: VM4H6038.I3 returns false, so aa 00; striplight 80:
    # power_live [heartbeat_aa33], so aa 33 (the resolver's one-way rule). The 1.7.0 trace run added 136
    # such rows, each doc saying why it is held (h6047 277, h7004 0, h6069 284, h3700 383, h7076 313,
    # bulblightv3 342, h70dx 267-269, the four h1162 rows, h70b1 134, h70bx 216, the six status-read rows
    # the new command ids unblocked and the nine gated target rows the pact-None guard unblocked are now
    # supported); they are pinned by count.
    got = {(fam["id"], gt): row["heartbeat"] for fam in _doc()["families"]
           for gt, row in ((fam.get("client_profile") or {}).get("by_goods_type") or {}).items()
           if "heartbeat" in row and "support" not in row}
    assert {key: got.get(key) for key in (("h6047", "288"), ("h6047", "298"), ("striplight", "80"))} == {
        ("h6047", "288"): "heartbeat", ("h6047", "298"): "heartbeat", ("striplight", "80"): "volume"}
    assert collections.Counter(got.values()) == {"power": 70, "heartbeat": 34, "volume": 5, "cmd_36": 1}


@pytest.mark.parametrize("name", _BEATS)
def test_a_heartbeat_reads_back_as_its_command(name: str) -> None:
    require_optional("kaitaistruct", reason="Kaitai runtime not installed")
    code = int(_C.Command[name])
    beat = build.heartbeat(code)
    read = _spec.read_frame(beat, _spec.TX, _spec.PLAIN)
    assert read.pro_type == _C.ProType.read and read.body.command == _C.Command[name]
    assert beat == bytes([0xAA, code]) + bytes(17) + bytes([0xAA ^ code])   # no selector, the zero fill
    assert describe_frame(beat, "tx")[0] == f"read/{name}"


def test_the_profile_fields_default_to_no_heartbeat_and_are_checked() -> None:
    hand = DeviceProfile(skus=("HX1",), capabilities=frozenset())
    assert (hand.heartbeat, hand.heartbeat_timing) == (None, "fixed_rate")   # a hand-built profile: 1.5.0's
    beating = dataclasses.replace(hand, heartbeat=0x36, heartbeat_timing="after_quiet")
    assert (beating.heartbeat, beating.heartbeat_timing) == (0x36, "after_quiet")
    # Without a heartbeat any valid timing is accepted and ignored: the single-field opt-out keeps it.
    off = dataclasses.replace(beating, heartbeat=None)
    assert (off.heartbeat, off.heartbeat_timing) == (None, "after_quiet")
    assert dataclasses.replace(hand, heartbeat_timing="after_quiet").heartbeat is None
    for bad, match in (({"heartbeat": True}, "heartbeat=True"), ({"heartbeat": 0x100}, "heartbeat=256"),
                       ({"heartbeat": "power"}, "heartbeat='power'"), ({"heartbeat": 0x03}, "heartbeat=3"),
                       ({"heartbeat": 0x05}, "heartbeat=5"), ({"heartbeat": 0x04}, "heartbeat=4"),
                       ({"heartbeat_timing": "later"}, "heartbeat_timing='later'"),   # without one too
                       ({"heartbeat": 0x01, "heartbeat_timing": "later"}, "heartbeat_timing='later'")):
        with pytest.raises(GoveeBleInvalidValue, match=match):
            dataclasses.replace(hand, **bad)


def test_a_profile_takes_only_the_four_keep_alive_reads() -> None:
    # Of every govee_common command, a profile accepts only the spec's keep-alive reads (the resolver's
    # HEARTBEATS, which the generator checks the rows against), and a Device builds the beat for each. A
    # command the builder can't make bare (aa 05 mode: its read_request needs a selector) or that no screen
    # sends every 2 s (aa 04 brightness) is refused when the profile is built, not later by the Device.
    assert _resolve.HEARTBEATS == _BEATS
    base = profile_for("H6006")
    assert base is not None
    accepted = set()
    for command in _C.Command:
        try:
            profile = dataclasses.replace(base, heartbeat=int(command))
        except GoveeBleInvalidValue:
            continue
        accepted.add(command.name)
        dev = Device(profile, ble_device("AA:BB:CC:DD:EE:01", "GVH6006X"), None, sku="H6006")
        assert dev._conn._heartbeat == build.heartbeat(int(command))
    assert accepted == set(_BEATS)


async def test_the_documented_opt_out_turns_the_beat_off_on_every_sku(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # The opt-out DeviceProfile.heartbeat, Device, create_device and the CHANGELOG document, the one field,
    # builds every profile, H5080 / H5083 / H5085's after_quiet ones too (the timing is kept and ignored), and
    # such a device never arms a beat: over a session held three intervals after a write (the write an
    # after_quiet chain would restart its wait on), the link gets nothing. A SKU with a firmware image (the
    # H5083's, unpaired: its 33 writes go unanswered) is served by it, as fake_ble.Provenance requires.
    monkeypatch.setattr(conn_mod, "WRITE_ACK_TIMEOUT", 0.1)
    monkeypatch.setattr(conn_mod, "HANDSHAKE_STEP2_TIMEOUT", 0.1)
    images = image_skus()
    skus = supported_skus(allow_modelled=True)
    timings = set()
    for sku in skus:
        profile = profile_for(sku, pact=routing_pact(sku, allow_modelled=True), allow_modelled=True)
        assert profile is not None and profile.heartbeat is not None, sku
        off = dataclasses.replace(profile, heartbeat=None)
        assert (off.heartbeat, off.heartbeat_timing) == (None, profile.heartbeat_timing), sku
        # Still the row's profile for the advertised-pact check (Device._learn_pact's warning).
        assert refuses_pact(off, sku, (9, 9)) == refuses_pact(profile, sku, (9, 9)), sku
        timings.add(off.heartbeat_timing)
        fake: FakeGovee | FirmwareGovee
        if sku in images:
            fake = FirmwareGovee()
            adv = fake.advertisement()
            assert fake.sku == sku and adv is not None, sku
            name = adv.local_name
        else:
            fake, adv, name = FakeGovee(encrypted=off.encryption is Encryption.AES_RC4_PSK), None, f"GV{sku}"
        monkeypatch.setattr(conn_mod, "establish_connection", fake.establish)
        dev = Device(off, ble_device("AA:BB:CC:DD:EE:01", name), adv, sku=sku, idle_disconnect=0)
        assert dev._conn._heartbeat is None, sku
        with virtual_time():
            await dev.connect()
            assert dev._conn.is_connected and dev._conn._heartbeat_timer is None, sku
            await dev._conn.send(frame(0x33, 0x01, 0x01), expect_ack=False)
            mark = len(fake.received)
            await asyncio.sleep(3 * const.HEARTBEAT_INTERVAL)
            assert dev._conn.is_connected and fake.received[mark:] == [], sku
            assert dev._conn._heartbeat_timer is None and dev._conn._heartbeat_beat is None, sku
        await dev.stop()
    assert {"H5080", "H5083", "H5085"} <= set(skus) and timings == {"fixed_rate", "after_quiet"}
