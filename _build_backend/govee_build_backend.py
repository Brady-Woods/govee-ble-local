"""In-tree PEP 517 build backend: setuptools, plus the Kaitai classes and the device table generated first.

The read-write classes under ``src/govee_ble_local/_generated/`` are a build product of
``spec/*.ksy``, and its ``devices.py`` of ``spec/devices.yaml``, not committed source. Every build that
starts from the repository (``pip install .``, ``pip install -e .``, ``python -m build``) therefore runs
``tools/gen_kaitai.sh`` (which also runs ``tools/gen_devices.py``) before handing over to
``setuptools.build_meta``, which needs bash and the JVM ``kaitai-struct-compiler`` 0.11 (``brew install
kaitai-struct-compiler``, or ``scripts/install_ksc.sh`` on Debian/Ubuntu). The device table needs PyYAML,
which ``get_requires_for_build_*`` asks the frontend for in a repository build only, so it is never a
runtime requirement. A failed generation ends the build with one message saying so, rather than a
``CalledProcessError`` traceback.

An sdist ships the classes it was built with but not ``spec/`` or ``tools/`` (MANIFEST.in prunes
them), so a wheel built from an sdist (``pip install`` of the sdist, or ``python -m build``'s own
wheel-from-sdist step) reuses those. With neither the generator nor shipped classes, the build fails
with a message saying how to get them. Every hook otherwise is setuptools' own.
"""
from __future__ import annotations

import importlib
import os
import subprocess  # nosec B404  # runs the repository's own generator script, no shell
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_GENERATOR = _ROOT / "tools" / "gen_kaitai.sh"
_SPEC = _ROOT / "spec"
_GENERATED = _ROOT / "src" / "govee_ble_local" / "_generated"
# One module per spec/*.ksy, the device table from spec/devices.yaml, and the package file gen_kaitai.sh
# writes (tests/test_build_product.py checks this list against spec/).
_GENERATED_FILES = (
    "__init__.py", "devices.py", "govee_bgc_info.py", "govee_ble_frame.py", "govee_common.py",
    "govee_manufacturer_data.py", "govee_profile.py", "govee_scan_record.py", "govee_state.py",
    "govee_status_stream.py", "govee_upload.py",
)
# What tools/gen_devices.py imports, for a repository build only (an sdist ships the generated table).
_GENERATOR_REQUIRES = ("PyYAML>=6.0",)


def _repository_build() -> bool:
    """True when this tree has the generators and spec/ (a clone, a git URL or a source archive)."""
    return _GENERATOR.is_file() and _SPEC.is_dir()


def _ensure_generated() -> None:
    """Generate the Kaitai classes from ``spec/*.ksy`` when the generator is here (a repository build),
    else check that the sdist being built from shipped them."""
    if _repository_build():
        argv = ["bash", str(_GENERATOR)]   # the repository's own generator, fixed argv, no shell
        env = {**os.environ, "PYTHON": sys.executable}   # the build environment's, which has PyYAML
        try:
            subprocess.run(argv, cwd=_ROOT, check=True, env=env)  # noqa: S603  # nosec B603
        except (OSError, subprocess.CalledProcessError) as exc:
            # SystemExit, as setuptools does for build errors: pip and uv show the generator's own error
            # above, and this as the last line, without a traceback.
            raise SystemExit(
                f"govee-ble-local: generating the Kaitai classes with tools/gen_kaitai.sh failed ({exc}). A "
                "build from the repository (a clone, a git URL or a source archive) needs bash, PyYAML and "
                "the JVM kaitai-struct-compiler 0.11 (brew install kaitai-struct-compiler, or bash "
                "scripts/install_ksc.sh on Debian/Ubuntu; see CONTRIBUTING.md). To just use the library, "
                "install the released package from PyPI, which ships the classes.") from None
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


def _build_requires() -> list[str]:
    """PyYAML for the device table's generator in a repository build; nothing from an sdist."""
    return list(_GENERATOR_REQUIRES) if _repository_build() else []


def get_requires_for_build_sdist(config_settings: dict[str, Any] | None = None) -> list[str]:
    return [*_setuptools().get_requires_for_build_sdist(config_settings), *_build_requires()]


def get_requires_for_build_wheel(config_settings: dict[str, Any] | None = None) -> list[str]:
    return [*_setuptools().get_requires_for_build_wheel(config_settings), *_build_requires()]


def get_requires_for_build_editable(config_settings: dict[str, Any] | None = None) -> list[str]:
    return [*_setuptools().get_requires_for_build_editable(config_settings), *_build_requires()]


def prepare_metadata_for_build_wheel(metadata_directory: str,
                                     config_settings: dict[str, Any] | None = None) -> str:
    return str(_setuptools().prepare_metadata_for_build_wheel(metadata_directory, config_settings))


def prepare_metadata_for_build_editable(metadata_directory: str,
                                        config_settings: dict[str, Any] | None = None) -> str:
    return str(_setuptools().prepare_metadata_for_build_editable(metadata_directory, config_settings))
