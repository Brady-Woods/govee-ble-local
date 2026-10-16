#!/usr/bin/env bash
#
# Smoke-test the distribution artifacts: the published wheel must carry the generated Kaitai classes.
#
#     scripts/smoke_artifacts.sh             # build the sdist and wheel into dist/, then test them
#     scripts/smoke_artifacts.sh --no-build  # test what is already in dist/ (release.yml, after Build)
#
# The classes under src/govee_ble_local/_generated/ are a build product (gitignored), so a wheel
# built without them would install fine and then fail every import with ModuleNotFoundError, for
# every Home Assistant user. This installs into fresh venvs, outside the repository, and imports:
#   1. the wheel as built (python -m build makes it from the sdist, as PyPI users get it);
#   2. a wheel built from the sdist by pip (`pip install <sdist>`), which can't regenerate, because
#      the sdist ships no spec/ or tools/, so it must reuse the classes the sdist carries.
# Building needs the pinned JVM kaitai-struct-compiler (scripts/install_ksc.sh); both steps need
# network for the runtime dependencies and setuptools.
#
# Env overrides: PYTHON (default python3), DIST (default dist)
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
DIST="${DIST:-dist}"

case "${1:-}" in
  "") build=1 ;;
  --no-build) build=0 ;;
  *) echo "usage: $0 [--no-build]" >&2; exit 2 ;;
esac

if [ "$build" = 1 ]; then
  rm -rf "$DIST"
  "$PYTHON" -m pip install --quiet build==1.6.1
  "$PYTHON" -m build --outdir "$DIST"
fi
wheel=$(ls "$DIST"/*.whl)
sdist=$(ls "$DIST"/*.tar.gz)

work=$(mktemp -d "${TMPDIR:-/tmp}/govee_smoke.XXXXXX")
trap 'rm -rf "$work"' EXIT

# Imported from the installed package (cwd is the scratch dir, not the repository), so a missing or
# read-only _generated/ fails here, and one frame is built and read back with the classes.
probe='
import govee_ble_local, govee_ble_local.wire.parse
from kaitaistruct import ReadWriteKaitaiStruct
from govee_ble_local._generated.govee_ble_frame import GoveeBleFrame
from govee_ble_local.wire import build, parse
assert issubclass(GoveeBleFrame, ReadWriteKaitaiStruct), "the wheel ships a read-only build"
assert "site-packages" in govee_ble_local.__file__, govee_ble_local.__file__
assert parse.parse_light_power(build.power_query()) is False   # aa 01 00: built, then read back
print("   imports and a frame round trip OK:", govee_ble_local.__file__)
'

smoke() {   # smoke <wheel> <venv name>
  "$PYTHON" -m venv "$work/$2"
  "$work/$2/bin/python" -m pip install --quiet "$1"
  ( cd "$work" && "$work/$2/bin/python" -c "$probe" )
  echo ">> $2: $(basename "$1") OK"
}

smoke "$wheel" wheel
"$PYTHON" -m pip wheel --quiet --no-deps --wheel-dir "$work/from-sdist" "$sdist"
smoke "$(ls "$work"/from-sdist/*.whl)" wheel-from-sdist
echo "ARTIFACT SMOKE TEST PASSED"
