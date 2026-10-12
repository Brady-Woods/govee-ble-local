"""The optional-dependency gate: skip locally, FAIL on CI / GOVEE_REQUIRE_OPTIONAL_TESTS."""
from __future__ import annotations

import pytest

from conftest import optional_deps_required, require_optional

_MISSING = "govee_definitely_not_installed_module"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GOVEE_REQUIRE_OPTIONAL_TESTS", raising=False)
    return monkeypatch


def test_missing_module_skips_without_flags(clean_env: pytest.MonkeyPatch) -> None:
    assert optional_deps_required() is False
    with pytest.raises(pytest.skip.Exception):
        require_optional(_MISSING, reason="r")


@pytest.mark.parametrize(
    ("var", "val"),
    [("CI", "true"), ("CI", "1"), ("CI", "YES"), ("GOVEE_REQUIRE_OPTIONAL_TESTS", "1")],
)
def test_missing_module_fails_when_required(
    clean_env: pytest.MonkeyPatch, var: str, val: str
) -> None:
    clean_env.setenv(var, val)
    assert optional_deps_required() is True
    with pytest.raises(pytest.fail.Exception, match=_MISSING):
        require_optional(_MISSING, reason="r")


def test_present_module_is_returned_in_both_modes(clean_env: pytest.MonkeyPatch) -> None:
    assert require_optional("json").__name__ == "json"
    clean_env.setenv("CI", "true")
    assert require_optional("json").__name__ == "json"


def test_flag_zero_is_not_required(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GOVEE_REQUIRE_OPTIONAL_TESTS", "0")
    assert optional_deps_required() is False


@pytest.mark.parametrize("val", ["false", "0", "", "no"])
def test_ci_falsy_is_not_required(clean_env: pytest.MonkeyPatch, val: str) -> None:
    clean_env.setenv("CI", val)
    assert optional_deps_required() is False
