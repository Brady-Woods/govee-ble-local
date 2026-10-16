"""Make ``govee_ble_local`` (src layout) — including the generated Kaitai classes
under ``govee_ble_local._generated`` — and the spec builders (``tests/spec_frames``)
importable whether or not the package is installed editable."""
from __future__ import annotations

import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"

for _p in (str(_SRC), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_GENERATED = _SRC / "govee_ble_local" / "_generated"
_GENERATED_OUTPUTS = ("govee_ble_frame.py", "govee_advertisement.py")   # not __init__.py: written once


def generated_classes_problem(root: pathlib.Path) -> str | None:
    """Why ``root``'s generated Kaitai classes can't be tested against, or None when they can.

    They are a build product (gitignored), so they can be missing, and an ignored ``_generated/``
    survives a ``git switch`` or ``git pull`` that changes the spec: then they are older than
    ``spec/*.ksy`` or the generator. Git gives every file a checkout rewrites a fresh mtime, so an input
    newer than the oldest output means the classes were built from something else (a touch or a stash
    round trip only costs a needless regeneration)."""
    generated = root / "src" / "govee_ble_local" / "_generated"
    missing = [n for n in _GENERATED_OUTPUTS if not (generated / n).is_file()]
    if missing:
        return (f"src/govee_ble_local/_generated/ is missing {', '.join(missing)}: run "
                "`bash tools/gen_kaitai.sh` (needs the JVM kaitai-struct-compiler 0.11) or "
                "`pip install -e .`, which generates them")
    inputs = [*(root / "spec").glob("*.ksy"), root / "tools" / "gen_kaitai.sh"]
    oldest_output = min((generated / n).stat().st_mtime for n in _GENERATED_OUTPUTS)
    stale = sorted(p.name for p in inputs if p.is_file() and p.stat().st_mtime > oldest_output)
    if stale:
        return (f"src/govee_ble_local/_generated/ is older than {', '.join(stale)} (edited, or changed by a "
                "checkout or pull since it was generated): run `bash tools/gen_kaitai.sh`")
    return None


def pytest_sessionstart(session):  # type: ignore[no-untyped-def]
    """Stop with a clear message, not an import error in every test or results from another branch's
    classes, when the generated Kaitai classes are missing or stale."""
    problem = generated_classes_problem(_HERE.parent)
    if problem:
        import pytest

        pytest.exit(problem, returncode=4)


# -- optional-dependency gate ---------------------------------------------------------------
import os  # noqa: E402

import pytest  # noqa: E402


def optional_deps_required() -> bool:
    """True on CI (``CI`` truthy: 1/true/yes) or with ``GOVEE_REQUIRE_OPTIONAL_TESTS=1``: a missing optional
    test dependency must FAIL rather than silently skip (conformance protection must not vanish)."""
    flag = os.environ.get("GOVEE_REQUIRE_OPTIONAL_TESTS", "")
    truthy = ("1", "true", "yes")
    return os.environ.get("CI", "").lower() in truthy or flag.lower() in truthy


def require_optional(modname: str, *, reason: str | None = None):  # type: ignore[no-untyped-def]
    """``pytest.importorskip`` that fails instead of skipping when :func:`optional_deps_required`."""
    try:
        return pytest.importorskip(modname, reason=reason)
    except pytest.skip.Exception:
        if optional_deps_required():
            pytest.fail(
                f"required optional test dependency {modname!r} is missing"
                + (f" ({reason})" if reason else ""),
                pytrace=False,
            )
        raise


def read_struct(obj):  # type: ignore[no-untyped-def]
    """Read a generated Kaitai object and return it. The generated classes are read-write
    (``ReadWriteKaitaiStruct``): ``from_bytes`` / ``Cls(..., stream)`` only bind the stream."""
    obj._read()
    return obj
