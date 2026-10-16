"""Make ``govee_ble_local`` (src layout) — including the shipped Kaitai readers
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


def pytest_sessionstart(session):  # type: ignore[no-untyped-def]
    """The Kaitai classes are a build product (gitignored): stop with a clear message, not an import
    error in every test, when they haven't been generated."""
    missing = [n for n in ("govee_ble_frame.py", "govee_advertisement.py") if not (_GENERATED / n).is_file()]
    if missing:
        import pytest

        pytest.exit(f"src/govee_ble_local/_generated/ is missing {', '.join(missing)}: run "
                    "`bash tools/gen_kaitai.sh` (needs the JVM kaitai-struct-compiler 0.11) or "
                    "`pip install -e .`, which generates them", returncode=4)


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
