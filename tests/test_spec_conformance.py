"""Offline conformance against ``spec/`` (no device):
  1. spec/devices.yaml validates against spec/devices.schema.json.
  2. every SKU the library supports exists in the spec catalog.
(Command-frame conformance is covered by tests/test_wire_build.py: every builder round-trips through the
generated reader. The manufacturer-data reader is the library's own identify path, test_identify.py; the
scan-record reader carries the H5083 image's advert, test_h5083_firmware.py.)
"""
from __future__ import annotations

import json
import pathlib

from conftest import require_optional

from govee_ble_local.registry import supported_skus

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
