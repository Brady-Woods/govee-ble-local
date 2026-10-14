"""Dependency policy (CLAUDE.md, issue #8): runtime dependencies carry a `>=` floor only.

Home Assistant installs this library under its own `package_constraints.txt`, so any upper bound
or exact pin here can only conflict with Home Assistant's pins (1.2.0's `cryptography<49` made the
integration fail to load on Home Assistant 2026.10). This test keeps that from coming back.
"""
from __future__ import annotations

import pathlib
import tomllib

import pytest
from packaging.requirements import Requirement

_PYPROJECT = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
_DEPS = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
# The approved runtime set (CLAUDE.md rule 3); the first three ship with Home Assistant.
_ALLOWED = {"bleak", "bleak-retry-connector", "cryptography", "kaitaistruct"}


@pytest.mark.parametrize("spec", _DEPS)
def test_runtime_dependency_has_floor_only(spec: str) -> None:
    req = Requirement(spec)
    ops = sorted(s.operator for s in req.specifier)
    assert ops == [">="], (
        f"{spec!r}: runtime dependencies may only have a '>=' floor (CLAUDE.md, issue #8); "
        "an upper bound or pin conflicts with Home Assistant's package constraints"
    )
    assert req.marker is None and not req.url, f"{spec!r}: no markers or direct URLs"


def test_runtime_dependency_set_is_approved() -> None:
    names = {Requirement(s).name.lower() for s in _DEPS}
    assert names == _ALLOWED, (
        f"runtime dependencies changed: {sorted(names ^ _ALLOWED)}; "
        "new runtime deps need the user's approval (CLAUDE.md rule 3)"
    )
