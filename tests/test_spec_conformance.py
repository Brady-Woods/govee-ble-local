"""Offline conformance: the reverse-engineered library vs. the ``spec/``.

Three things, no device required:
  1. ``spec/devices.yaml`` validates against ``spec/devices.schema.json``.
  2. every SKU the library supports exists in the spec catalog.
  3. the library's own command frames (``ble.controllers``) parse cleanly under the
     Kaitai reader generated from ``spec/govee_ble.ksy`` — documenting where library
     and spec AGREE, and pinning the one place they don't (scene upload, R2).
"""
from __future__ import annotations

import json
import pathlib

import pytest

pytest.importorskip("kaitaistruct", reason="Kaitai runtime not installed")
gb = pytest.importorskip(
    "spec_gen.govee_ble_frame", reason="run tools/gen_kaitai.sh to generate the Kaitai reader"
)
GoveeBleFrame = gb.GoveeBleFrame

from govee_ble_local import registry  # noqa: E402
from govee_ble_local.ble import controllers  # noqa: E402
from govee_ble_local.scenes import load_scenes  # noqa: E402

_adv = pytest.importorskip(
    "spec_gen.govee_advertisement", reason="run tools/gen_kaitai.sh to generate the adv reader"
)
GoveeAdvertisement = _adv.GoveeAdvertisement

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SPEC = _REPO / "spec"


def _load_yaml(path: pathlib.Path):
    import yaml
    return yaml.safe_load(path.read_text())


# ── 1. schema ────────────────────────────────────────────────────────────────
def test_devices_yaml_validates_against_schema():
    jsonschema = pytest.importorskip("jsonschema")
    doc = _load_yaml(_SPEC / "devices.yaml")
    schema = json.loads((_SPEC / "devices.schema.json").read_text())
    jsonschema.validate(instance=doc, schema=schema)  # raises on violation


# ── 2. coverage/consistency ──────────────────────────────────────────────────
def test_supported_skus_present_in_spec_catalog():
    doc = _load_yaml(_SPEC / "devices.yaml")
    catalog = {m.upper() for fam in doc["families"] for m in fam.get("models", [])}
    supported = {sku.upper() for cls in registry._DEVICE_CLASSES for sku in cls.skus}
    missing = supported - catalog
    assert not missing, f"library SKUs absent from spec/devices.yaml: {sorted(missing)}"


# ── 3. library frames conform to the ksy ─────────────────────────────────────
def _parse(frame: bytes) -> GoveeBleFrame:
    assert len(frame) == 20
    return GoveeBleFrame.from_bytes(frame)


def test_power_brightness_conform():
    assert _parse(controllers.power(True)).body.params.state == 0x01
    assert _parse(controllers.brightness(50)).body.params.level == 50


def test_scene_activate_conforms():
    f = _parse(controllers.scene((0xD6, 0x4A)))  # little-endian (lo, hi)
    assert f.body.params.sub_type == GoveeBleFrame.SubMode.scene
    assert f.body.params.params.effect == 0x4AD6


def test_sync_time_conforms_to_plug_sync_time():
    f = _parse(controllers.sync_time(0x6A49901A))
    assert f.body.command == GoveeBleFrame.Command.plug_sync_time
    assert f.body.params.unix_seconds == 0x6A49901A
    assert f.body.params.marker == 0x01


def test_status_query_conforms():
    f = _parse(controllers.status_query(False))
    assert f.pro_type == GoveeBleFrame.ProType.multi_reply_read
    # multi_ac request = {command, count, requested_types[count]} (ksy Change 5)
    assert f.body.count == 2
    assert list(f.body.requested_types) == [0x41, 0x30]


def test_color_h60a6_conforms_to_op15():
    f = _parse(controllers.rgb(0x11, 0x22, 0x33, "h60a6", 13))
    m = f.body.params
    assert m.sub_type == GoveeBleFrame.SubMode.color_rgbic_15
    assert m.params.op_type == GoveeBleFrame.Op15.set_color
    assert (m.params.data.r, m.params.data.g, m.params.data.b) == (0x11, 0x22, 0x33)


def test_color_h6006_conforms_to_cct_0d():
    f = _parse(controllers.rgb(0x11, 0x22, 0x33, "h6006", 13))
    m = f.body.params
    assert m.sub_type == GoveeBleFrame.SubMode.color_cct_0d
    d = m.params
    assert (d.r, d.g, d.b) == (0x11, 0x22, 0x33)
    assert d.kelvin == 0  # plain RGB -> kelvin 0


def test_color_h61a8_conforms_to_rgbic_0b():
    f = _parse(controllers.rgb(0x11, 0x22, 0x33, "h61a8", 13))
    m = f.body.params
    assert m.sub_type == GoveeBleFrame.SubMode.color_rgbic_0b
    assert (m.params.r, m.params.g, m.params.b) == (0x11, 0x22, 0x33)


def test_color_temp_h6006_conforms():
    f = _parse(controllers.color_temp(4000, "h6006", 13))
    d = f.body.params.params
    assert (d.r, d.g, d.b) == (0xFF, 0xFF, 0xFF)
    assert d.kelvin == 4000  # u2be


# ── R2: the one place library and spec diverge ───────────────────────────────
def _first_real_param() -> str:
    """A real H60A6 scene param blob (non-stub) from the bundled catalog."""
    for scene in load_scenes("H60A6").values():
        if scene.param and not scene.placeholder:
            import base64
            raw = base64.b64decode(scene.param)
            if len(raw) >= 4 and raw[3] != 0xFF:  # skip 0xff-stub placeholders
                return scene.param
    pytest.skip("no non-stub H60A6 scene param bundled")


def test_advertisement_reader_parses_govee():
    # Synthetic Govee manufacturer AD: len, 0xFF, flags(encrypted+v1=0x41),
    # 88 EC (company u2le -> 0xEC88), pact_type u2be=0x01F6, pact_code=0x22, rest.
    rec = bytes([0x0A, 0xFF, 0x41, 0x88, 0xEC, 0x01, 0xF6, 0x22, 0x01, 0x02, 0x03, 0x00])
    a = GoveeAdvertisement.from_bytes(rec)
    m = a.structures[0].data
    assert a.structures[0].ad_type == 0xFF
    assert m.is_govee is True
    assert m.company_id == 0xEC88
    assert m.encrypted is True          # flags bit 6
    assert m.protocol_version == 1      # flags low nibble
    assert m.pact_type == 0x01F6        # u2be
    assert m.pact_code == 0x22


def test_library_scene_chunks_comm_byte_is_h60a6_device_code():
    # Corrected (B1 + hardware): the library ORs 0x08 onto blob[0]; for H60A6 params
    # (blob[0] == 0x50) that yields 0x58 == 88 == the H60A6 DIY/graffiti device protocol
    # code — exactly the a3_start comm_byte. So the library's frame carries the CORRECT
    # comm_byte for H60A6, but by arithmetic COINCIDENCE (0x50 | 0x08 == 0x58), not design.
    #
    # STILL OPEN (needs an official-app btsnoop; not checkable offline): the value must be a
    # re-encoded toBytes() (the library sends the raw blob), and the graffiti default may be
    # the 0xA4-MTU builder rather than this 0xA3 form.
    import base64

    param = _first_real_param()
    raw = base64.b64decode(param)
    assert raw[0] == 0x50                       # H60A6 scene params are 0x50-prefixed
    start = _parse(controllers.scene_chunks(param)[0])
    a = start.body.frame  # a3_start
    assert start.body.seq_no == 0x00
    assert a.comm_byte == 0x58                  # == 88, the H60A6 device protocol code
