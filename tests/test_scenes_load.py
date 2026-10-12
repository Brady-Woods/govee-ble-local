"""Scene catalog loader: SKU validation, error contract, cache isolation."""
from __future__ import annotations

import dataclasses

import pytest

from govee_ble_local import scenes
from govee_ble_local.exceptions import GoveeBleError, GoveeBleInvalidValue
from govee_ble_local.scenes import load_scenes, scene_names_by_code

# Counts captured from the pre-change loader.
EXPECTED = {
    "H6006": 59, "H6008": 59, "H6047": 65, "H6052": 43,
    "H60A6": 84, "H61A8": 148, "H6641": 148,
}


@pytest.mark.parametrize(("sku", "count"), EXPECTED.items())
def test_shipped_catalogs(sku, count):
    assert len(load_scenes(sku)) == count
    assert len(load_scenes(sku.lower())) == count
    assert len(load_scenes(f" {sku} ")) == count


@pytest.mark.parametrize(
    "sku", ["", "  ", "../H6006", "H6006/../x", "H60.6", "a/b", "H6", "H60060606", "H60 06", "H6006.json", "H60\x00A", None],
)
def test_bad_sku(sku):
    with pytest.raises(GoveeBleInvalidValue):
        load_scenes(sku)


def test_unknown_valid_sku():
    assert load_scenes("H9999") == {}
    assert scene_names_by_code("H9999") == {}


class _Res:
    def __init__(self, text):
        self._text = text

    def __truediv__(self, _name):
        return self

    def read_text(self, encoding=None):
        assert encoding == "utf-8"
        return self._text


@pytest.fixture
def fake_catalog(monkeypatch):
    def install(text):
        scenes._catalog.cache_clear()
        monkeypatch.setattr(scenes.resources, "files", lambda _pkg: _Res(text))
    yield install
    scenes._catalog.cache_clear()


@pytest.mark.parametrize(
    "text",
    ["{not json", "[1, 2]", '"str"', '{"a": {"param": null}}', '{"a": 5}', '{"a": {"code": "xx"}}'],
)
def test_malformed_catalog(fake_catalog, text):
    fake_catalog(text)
    with pytest.raises(GoveeBleError, match="scene catalog for H1234 is malformed") as ei:
        load_scenes("h1234")
    assert not isinstance(ei.value, GoveeBleInvalidValue)
    assert ei.value.__cause__ is not None


def test_valid_fake_catalog(fake_catalog):
    fake_catalog('{"a": {"code": 3, "param": null}, "b": {"code": 3}}')
    assert scene_names_by_code("H1234") == {3: "a"}


def test_mutation_isolation():
    a = load_scenes("H6006")
    n = len(a)
    name = next(iter(a))
    with pytest.raises(dataclasses.FrozenInstanceError):
        a[name].code = 0  # type: ignore[misc]
    a.clear()
    b = load_scenes("H6006")
    assert len(b) == n and b is not a
    names = scene_names_by_code("H6006")
    names.clear()
    assert scene_names_by_code("H6006")
