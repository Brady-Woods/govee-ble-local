"""In-tree PEP 517 build backend: setuptools, plus the Kaitai classes generated first.

The read-write classes under ``src/govee_ble_local/_generated/`` are a build product of
``spec/*.ksy``, not committed source. Every build that starts from the repository (``pip install .``,
``pip install -e .``, ``python -m build``) therefore runs ``tools/gen_kaitai.sh`` before handing over to
``setuptools.build_meta``, which needs the JVM ``kaitai-struct-compiler`` 0.11 (``brew install
kaitai-struct-compiler``, or ``scripts/install_ksc.sh`` on Debian/Ubuntu).

An sdist ships the classes it was built with but not ``spec/`` or ``tools/`` (MANIFEST.in prunes
them), so a wheel built from an sdist (``pip install`` of the sdist, or ``python -m build``'s own
wheel-from-sdist step) reuses those. With neither the generator nor shipped classes, the build fails
with a message saying how to get them. Every hook otherwise is setuptools' own.
"""
from __future__ import annotations

import importlib
import subprocess  # nosec B404 - runs the repository's own generator script, no shell
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_GENERATOR = _ROOT / "tools" / "gen_kaitai.sh"
_SPEC = _ROOT / "spec"
_GENERATED = _ROOT / "src" / "govee_ble_local" / "_generated"
_GENERATED_FILES = ("__init__.py", "govee_ble_frame.py", "govee_advertisement.py")


def _ensure_generated() -> None:
    """Generate the Kaitai classes from ``spec/*.ksy`` when the generator is here (a repository build),
    else check that the sdist being built from shipped them."""
    if _GENERATOR.is_file() and _SPEC.is_dir():
        argv = ["bash", str(_GENERATOR)]   # the repository's own generator, fixed argv, no shell
        subprocess.run(argv, cwd=_ROOT, check=True)  # noqa: S603  # nosec B603 B607
        return
    missing = [name for name in _GENERATED_FILES if not (_GENERATED / name).is_file()]
    if missing:
        raise RuntimeError(
            "govee-ble-local: the Kaitai classes in src/govee_ble_local/_generated/ are missing "
            f"({', '.join(missing)}) and this tree has no spec/ and tools/gen_kaitai.sh to generate them. "
            "Build from the git repository with the JVM kaitai-struct-compiler 0.11 installed "
            "(brew install kaitai-struct-compiler, or scripts/install_ksc.sh), or from a released sdist.")


def _setuptools() -> Any:
    """``setuptools.build_meta``, imported when a hook runs (the frontend has installed it by then, from
    ``[build-system].requires``), so this module itself needs only the standard library."""
    return importlib.import_module("setuptools.build_meta")


# ── PEP 517 / 660 hooks ──────────────────────────────────────────────────────────────────────
def build_sdist(sdist_directory: str, config_settings: dict[str, Any] | None = None) -> str:
    _ensure_generated()
    return str(_setuptools().build_sdist(sdist_directory, config_settings))


def build_wheel(wheel_directory: str, config_settings: dict[str, Any] | None = None,
                metadata_directory: str | None = None) -> str:
    _ensure_generated()
    return str(_setuptools().build_wheel(wheel_directory, config_settings, metadata_directory))


def build_editable(wheel_directory: str, config_settings: dict[str, Any] | None = None,
                   metadata_directory: str | None = None) -> str:
    _ensure_generated()
    return str(_setuptools().build_editable(wheel_directory, config_settings, metadata_directory))


def get_requires_for_build_sdist(config_settings: dict[str, Any] | None = None) -> list[str]:
    return list(_setuptools().get_requires_for_build_sdist(config_settings))


def get_requires_for_build_wheel(config_settings: dict[str, Any] | None = None) -> list[str]:
    return list(_setuptools().get_requires_for_build_wheel(config_settings))


def get_requires_for_build_editable(config_settings: dict[str, Any] | None = None) -> list[str]:
    return list(_setuptools().get_requires_for_build_editable(config_settings))


def prepare_metadata_for_build_wheel(metadata_directory: str,
                                     config_settings: dict[str, Any] | None = None) -> str:
    return str(_setuptools().prepare_metadata_for_build_wheel(metadata_directory, config_settings))


def prepare_metadata_for_build_editable(metadata_directory: str,
                                        config_settings: dict[str, Any] | None = None) -> str:
    return str(_setuptools().prepare_metadata_for_build_editable(metadata_directory, config_settings))
