"""The library on the spec owner's gap batch (c706502): the new enums, standalone types and instances.

Pins what each move reads from the spec, the app-matching decode changes it brings (signed Kelvins on
H6641 and the H6047 bars, signed version text, a signed section count) and the plug profile rows
against devices.yaml. No hardware."""
from __future__ import annotations

import base64
import pathlib
from typing import Any

import pytest
from conftest import require_optional

from govee_ble_local import identify, scenes
from govee_ble_local.devices import _fold
from govee_ble_local.devices.profile import PROFILES, profile_for, wire_profile
from govee_ble_local.models import DeviceState
from govee_ble_local.wire import build, describe, parse, reassemble

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _frame(*payload: int) -> bytes:
    buf = bytearray(20)
    buf[: len(payload)] = bytes(payload)
    x = 0
    for c in buf[:19]:
        x ^= c
    buf[19] = x
    return bytes(buf)


# ── devices.yaml: the plug rows match h5080's client_profile.by_goods_type ──────────────────
def _plug_goods_types() -> list[tuple[str, str, dict[str, object]]]:
    yaml = require_optional("yaml", reason="PyYAML reads spec/devices.yaml")
    rows = []
    for fam in yaml.safe_load((_ROOT / "spec" / "devices.yaml").read_text())["families"]:
        for gt, entry in ((fam.get("client_profile") or {}).get("by_goods_type") or {}).items():
            if "relay_power" not in entry:   # a colour-strategy entry, not a plug's
                continue
            for model in entry.get("models") or []:
                rows.append((str(gt), model, entry))
    return rows


# The aa 01 heartbeat rule the library's poll follows where it differs from the connect read devices.yaml's
# relay_on_mask records: gt 50 / 307 read bits 0..1 (devices.schema.json relay_on_mask's doc;
# switch_read_reply.any_relay_0_1_is_on).
_HEARTBEAT_ON_MASK = {"50": 0x03, "307": 0x03}


def test_every_plug_profile_matches_its_goods_type_in_devices_yaml() -> None:
    rows = _plug_goods_types()
    plugs = {"H5080", "H5082", "H5083", "H5085", "H5089", "H5160", "H5161"}
    assert {model for _gt, model, _e in rows} >= plugs
    for gt, model, entry in rows:
        profile = profile_for(model)
        assert profile is not None, model
        got = (profile.relay_power, profile.relay_on_mask, profile.advert_on_mask)
        on_mask = _HEARTBEAT_ON_MASK.get(gt, entry["relay_on_mask"])
        want = (entry["relay_power"], on_mask, entry["advert_on_mask"])
        assert got == want, (gt, model)


# ── devices.yaml: each colour profile's mode_color_parse is the strategy the spec resolves for it ──
# The goodsType each SKU-keyed profile is written for: its account record's, as profile.py's comments
# trace it (0 = no cloud goodsType, routed by SKU). Every colour profile must be listed here.
_PROFILE_GOODS_TYPE = {
    "H60A6": 303, "H6047": 119, "H61A8": 19, "H6006": 64, "H6008": 64, "H6004": 64, "H6052": 22,
    "H6641": 247, "H6188": 0, "H6062": 41, "H6061": 51,
}
# The spec reuses a library name only where the traced map is exactly that strategy. Its other names the
# library implements under one of its own: kelvin_pair_15 (the H6047 old detail screen, one Kelvin per bar)
# is "kelvin_15" on a bar_switch profile, which reads the pair (parse.bar_kelvin_of).
_LIBRARY_NAME = {"kelvin_pair_15": "kelvin_15"}


def _resolve_mode_color_parse(families: list[dict[str, Any]], sku: str, g0: int) -> str:
    """devices.schema.json client_profile.mode_color_parse: the family by the record's goodsType (by SKU
    only at 0, no fallback between the two), then assigned_goods_type, then by_goods_type.<gt>,
    by_model.<SKU>, the family default; pact and firmware branches last."""
    if g0:
        fams = [f for f in families if g0 in (f.get("goods_types") or [])]
    else:
        fams = [f for f in families if sku in (f.get("models") or [])
                and (not f.get("goods_types") or 0 in f["goods_types"])]
    assert len(fams) == 1, (sku, g0, [f["id"] for f in fams])
    fam = fams[0]
    # A device_list item's condition is on the record's pact or firmware, which a profile does not carry,
    # so none may apply to a profiled SKU.
    assigned = [a for a in fam.get("assigned_goods_type") or []
                if a["context"] == "device_list" and sku in a["models"] and g0 in a["from_goods_type"]]
    assert not assigned, (sku, assigned)
    cp = fam.get("client_profile") or {}
    by_gt = (cp.get("by_goods_type") or {}).get(str(g0)) or {}
    by_model = (cp.get("by_model") or {}).get(sku) or {}
    entry = next(e for e in (by_gt, by_model, cp) if "mode_color_parse" in e)
    # A profile is pact- and firmware-agnostic: no branch may apply.
    for e in (by_gt, by_model, entry):
        assert "mode_color_by_pact" not in e and "mode_color_by_firmware" not in e, (sku, e)
    return str(entry["mode_color_parse"])


def test_every_colour_profile_matches_the_mode_color_parse_devices_yaml_resolves() -> None:
    yaml = require_optional("yaml", reason="PyYAML reads spec/devices.yaml")
    families = yaml.safe_load((_ROOT / "spec" / "devices.yaml").read_text())["families"]
    colour = {sku for p in PROFILES if p.mode_color_parse != "rgb_kelvin" or not p.relay for sku in p.skus}
    assert colour == set(_PROFILE_GOODS_TYPE)
    for sku, g0 in _PROFILE_GOODS_TYPE.items():
        profile = profile_for(sku)
        assert profile is not None, sku
        spec_name = _resolve_mode_color_parse(families, sku, g0)
        assert profile.mode_color_parse == _LIBRARY_NAME.get(spec_name, spec_name), (sku, g0, spec_name)
        if spec_name == "kelvin_pair_15":
            assert profile.bar_switch, sku


# ── signed Kelvins: per SKU, as the app reads them ───────────────────────────────────────────
def _fold_mode(sku: str, *mode: int, **start: object) -> DeviceState:
    profile = profile_for(sku)
    assert profile is not None
    state = DeviceState(**start)  # type: ignore[arg-type]
    _fold.apply_mode_reply(profile, state, _frame(0xAA, 0x05, *mode))
    return state


@pytest.mark.parametrize("sku", ["H6641", "H61A8"])
def test_the_0x15_kelvin_reads_signed(sku: str) -> None:
    # H6641 (a plain SubMode4Color) and H61A8 (base OpInfo4Detail): getSignedShort, so 0x8000 reads
    # negative: not colour temperature, while the last Kelvin is kept, as the app keeps it.
    st = _fold_mode(sku, 0x15, 0x01, 0x80, 0x00, color_temp_kelvin=2700, last_color_temp_kelvin=2700)
    assert (st.color_temp_kelvin, st.last_color_temp_kelvin) == (None, 2700)
    st = _fold_mode(sku, 0x15, 0x01, 0x0A, 0x8C)
    assert (st.color_temp_kelvin, st.last_color_temp_kelvin) == (2700, 2700)


def test_h6047_bar_kelvins_read_signed_and_a_negative_one_is_no_cct() -> None:
    h6047 = profile_for("H6047")
    assert h6047 is not None
    m = parse.mode_reply(_frame(0xAA, 0x05, 0x15, 0x01, 0x80, 0x00, 0x0A, 0x8C), wire_profile(h6047))
    assert parse.mode_color(m) == parse.BarKelvin(-32768, 2700)
    st = _fold_mode("H6047", 0x15, 0x01, 0x80, 0x00, 0x80, 0x00)
    assert st.zone_color_temp_kelvin == {} and st.color_temp_kelvin is None
    st = _fold_mode("H6047", 0x15, 0x01, 0x0A, 0x8C, 0x0A, 0x8C)
    assert st.zone_color_temp_kelvin == {0: 2700, 1: 2700} and st.color_temp_kelvin == 2700


def test_the_scene_code_and_kind_3_come_from_the_spec() -> None:
    h60a6 = profile_for("H60A6")
    assert h60a6 is not None
    prof = wire_profile(h60a6)
    assert parse.scene_of(parse.mode_value(bytes([0x04, 0x2A, 0x01]), prof)) == 0x012A
    assert parse.scene_of(parse.mode_value(bytes([0x15, 0x2A, 0x01]), prof)) is None
    report = parse.mode_value(bytes([0x0D, 1, 2, 3, 0x0A, 0x8C]), prof)   # [r, g, b][kelvin]: 5 bytes
    assert parse.mode_color(report) == (None, 2700)


# ── device info, section count, flags ────────────────────────────────────────────────────────
def test_version_text_is_the_specs_signed_rendering() -> None:
    uid = [0, 0, 0xA1, 0xB2, 0xC3, 0xD4, 0xE5, 0xF6]
    info = parse.parse_device_info(_frame(0xAA, 0x07, 0x10, *uid, 1, 0x05, 0x33, 1, 0x85, 3, 0, 0))
    assert info is not None
    assert info.sw_version == "1.05.51"
    assert info.hw_version == "1.0-123.03"   # version3.text: minor read signed (Compose4BaseInfoSingleRead.v)
    assert info.serial == "F6:E5:D4:C3:B2:A1:00:00"   # reversed; uid[7], uid[6] aren't 0: nothing dropped
    info = parse.parse_device_info(_frame(0xAA, 0x07, 0x02, 1, 2, 3, 4, 5, 6, 0, 0))
    assert info is not None and info.serial == "06:05:04:03:02:01"   # uid_has_leading_pair dropped
    assert parse.parse_device_info(_frame(0xAA, 0x07, 0x02)) == parse.DeviceInfo()   # uid_is_invalid


def test_section_count_reads_its_section_signed() -> None:
    sc = parse.parse_section_count(_frame(0xAA, 0x41, 0x00, 0x2E, 0x85))
    assert sc is not None and (sc.ic_count, sc.section_num) == (46, -123)


def test_flag_instances() -> None:
    assert parse.parse_bar_switch(_frame(0xAA, 0x36, 1, 2)) == (True, False)
    assert parse.parse_gradual(_frame(0xAA, 0xA3, 2)) is False
    assert parse.parse_secret(_frame(0xAA, 0xB1, 0x02, *range(8))) is None
    assert parse.parse_secret(_frame(0xAA, 0xB1, 0x01, *range(8))) == bytes(range(8))


# ── RX frames: write ack, BGC info, 0xAC chunks ──────────────────────────────────────────────
def test_write_ack_and_bgc_info() -> None:
    assert parse.parse_write_ack(_frame(0x33, 0x05, 0x00)) == 0
    assert parse.parse_write_ack(_frame(0x33, 0x05, 0x01)) == 1
    assert parse.parse_write_ack(_frame(0xAA, 0x05, 0x01)) is None
    for data, version in ((b"\x01\x01", 1), (b"\x02\x02\x00\x00\x01\x02", 2), (b"\x02\x02", 2),
                          (b"\x03\x01", 0), (b"", 0), (b"\x02", 0)):
        assert parse.bgc_encrypt_version(data) == version, data


def test_a_first_chunk_whose_last_len_does_not_fit_rejects_the_burst() -> None:
    bad = _frame(0xAC, 0x00, 0x01, 0x00, 13, 0x03, 0x00)   # one packet, lastLen 13 > 12
    assert reassemble.read_ac_chunk(bad) is None
    assert reassemble.burst_chunks([bad]) == ([], False, 0)
    good = _frame(0xAC, 0x00, 0x01, 0x00, 3, 0x03, 0x00, 0x01, 0x01, 0x01)
    chunk = reassemble.read_ac_chunk(good)
    assert chunk is not None and chunk.is_first and int(chunk.len_first_data) == 3
    assert reassemble.reassemble([good]) == bytes([0x01, 0x01, 0x01])


_ONE_PACKET = _frame(0xAC, 0x00, 0x01, 0x00, 3, 0x03, 0x00, 0x01, 0x01, 0x01)


@pytest.mark.parametrize("record", [
    _ONE_PACKET[:19],                                           # 19 bytes (no BCC): 1.3.0 took it
    _ONE_PACKET + b"\x00",                                      # 21 bytes
    _frame(0xAA, 0x00, 0x01, 0x00, 3, 0x03, 0x00, 1, 1, 1),     # 20 bytes, another proType
])
def test_only_a_20_byte_0xac_frame_is_a_chunk(record: bytes) -> None:
    # ComposeBleComm.K:198 drops a notification that isn't 20 bytes; isSameController:207 takes only 0xAC.
    assert reassemble.burst_chunks([record]) == ([], False, 0)
    assert reassemble.reassemble([record]) == b""
    assert describe._split_bursts([record]) == []
    with pytest.raises(ValueError, match="not a 20-byte 0xAC status chunk"):
        reassemble.read_ac_chunk(record)
    assert reassemble.burst_chunks([_ONE_PACKET]) == ([_ONE_PACKET], True, 0)


# ── writers ──────────────────────────────────────────────────────────────────────────────────
def test_writers_on_the_new_enums_send_the_same_bytes() -> None:
    assert build.plug_total_switch(True)[:3] == bytes([0x33, 0x02, 0x01])
    assert build.plug_total_switch(False)[:3] == bytes([0x33, 0x02, 0x00])
    assert build.mode_query()[:3] == bytes([0xAA, 0x05, 0x01])
    assert build.status_query()[:2] == bytes([0xAC, 0x03])
    start = build.scene_upload_a3(b"\x10\x20", build.COMM_H60A6)[0]
    assert start[:5] == bytes([0xA3, 0x00, 0x01, 0x02, 0x58])
    assert (build.RELAY_ON, build.RELAY_ALL_OFF, build.INFO_HARD) == (0x11, 0xF0, 0x03)


# ── scenes: the lead byte picks the upload ───────────────────────────────────────────────────
def _scene(scene_type: int, blob: bytes) -> scenes.Scene:
    return scenes.Scene(name="x", code=1, param=base64.b64encode(blob).decode(), scene_type=scene_type)


def test_a_graffiti_scene_uploads_only_with_its_0x01_lead() -> None:
    blob = bytes([0x01, 0x00, 0x05, 1, 2, 3, 4, 5, 0])
    frames = scenes.scene_upload_frames(_scene(3, blob), "A", frozenset({3}))
    assert frames is not None and frames[0][4] == 0x07   # MultiNewScenesControllerV3, value = blob[2:]
    assert scenes.scene_upload_frames(_scene(3, bytes([0x02]) + blob[1:]), "A", frozenset({3})) is None


def test_compose_and_h6052_leads() -> None:
    assert scenes.scene_upload_frames(_scene(6, bytes([0x0A, 1, 2, 3])), "A", frozenset({6})) is not None
    assert scenes.scene_upload_frames(_scene(6, bytes([0x51, 1, 2, 3])), "A", frozenset({6})) is None
    assert scenes.scene_upload_frames(_scene(5, bytes([0x13, 1, 2, 3])), "B_h6052", frozenset()) is not None
    assert scenes.scene_upload_frames(_scene(5, bytes([0x14, 1, 2, 3])), "B_h6052", frozenset()) is None


# ── advertisements: manufacturer_data_lenient ────────────────────────────────────────────────
@pytest.mark.parametrize(("mfg", "pact", "onoff"), [
    ({0x8801: bytes([0xEC, 0x00, 0x05, 0x01])}, (False, 1, 5, 1), None),            # v1: no switch byte
    ({0x8802: bytes([0xEC, 0x00, 0x05, 0x01, 0x01])}, (False, 2, 5, 1), True),       # v2, switch byte 1
    ({0x8802: bytes([0xEC, 0x00, 0x05])}, None, None),                              # too short: skipped
    ({0x8800: bytes([0xEC, 0x00, 0x05, 0x01, 0x01])}, None, None),                  # version 0: no pact
])
def test_advertisement_readers(mfg: dict[int, bytes], pact: object, onoff: object) -> None:
    assert identify.parse_manufacturer_data(mfg) == pact
    assert identify.parse_broadcast_onoff(mfg) is onoff
