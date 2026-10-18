"""Offline conformance against ``spec/`` (no device):
  1. spec/devices.yaml validates against spec/devices.schema.json.
  2. every SKU the library supports exists in the spec catalog.
  3. the generated scan-record reader (the labelled govee_scan_record root) parses a Govee AD.
(Command-frame conformance is covered by tests/test_wire_build.py — every builder
round-trips through the generated reader.)
"""
from __future__ import annotations

import json
import pathlib

from conftest import read_struct, require_optional

require_optional("kaitaistruct", reason="Kaitai runtime not installed")
GoveeScanRecord = require_optional("govee_ble_local._generated.govee_scan_record").GoveeScanRecord

from govee_ble_local.registry import supported_skus  # noqa: E402

_SPEC = pathlib.Path(__file__).resolve().parent.parent / "spec"


def _load_yaml(path: pathlib.Path):  # type: ignore[no-untyped-def]
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_devices_yaml_validates_against_schema() -> None:
    jsonschema = require_optional("jsonschema")
    doc = _load_yaml(_SPEC / "devices.yaml")
    schema = json.loads((_SPEC / "devices.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(instance=doc, schema=schema)  # raises on violation


def test_supported_skus_present_in_spec_catalog() -> None:
    doc = _load_yaml(_SPEC / "devices.yaml")
    catalog = {m.upper() for fam in doc["families"] for m in fam.get("models", [])}
    missing = {s.upper() for s in supported_skus()} - catalog
    assert not missing, f"library SKUs absent from spec/devices.yaml: {sorted(missing)}"


def test_advertisement_reader_parses_govee() -> None:
    # Synthetic Govee manufacturer AD: len, 0xFF, flags(encrypted+v1=0x41), 88 EC (company u2le -> 0xEC88),
    # pact_type u2be=0x01F6, pact_code=0x22, rest; then 00, a pad structure. The AD's manufacturer_specific
    # case is govee_manufacturer_data.
    rec = bytes([0x0A, 0xFF, 0x41, 0x88, 0xEC, 0x01, 0xF6, 0x22, 0x01, 0x02, 0x03, 0x00])
    m = read_struct(GoveeScanRecord.from_bytes(rec)).ad_structures[0].data
    assert m.is_govee is True and m.company_id == 0xEC88
    assert m.encrypted is True and m.protocol_version == 1
    assert m.pact_type == 0x01F6 and m.pact_code == 0x22
