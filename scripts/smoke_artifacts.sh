#!/usr/bin/env bash
#
# Smoke-test the distribution artifacts: the published wheel must carry the generated Kaitai classes.
#
#     PYTHON=python3.13 scripts/smoke_artifacts.sh   # build the sdist and wheel into dist/, then test them
#     scripts/smoke_artifacts.sh --no-build          # test the one build already in dist/ (release.yml)
#
# The classes under src/govee_ble_local/_generated/ are a build product (gitignored), so a wheel
# built without them would install fine and then fail every import with ModuleNotFoundError, for
# every Home Assistant user. This checks:
#   1. the sdist ships the classes and no spec/ or tools/ (with those, a build from the sdist would
#      regenerate and need the JVM compiler, and this script couldn't tell: it runs with ksc on PATH);
#   2. the wheel as built (python -m build makes it from the sdist, as PyPI users get it) installs into
#      a fresh venv, outside the repository, and imports;
#   3. so does a wheel pip builds from the sdist, which reuses the classes the sdist ships.
# Building runs in a throwaway venv, never in $PYTHON itself, and needs the pinned JVM
# kaitai-struct-compiler (scripts/install_ksc.sh); every step needs network for the runtime
# dependencies and setuptools. --no-build expects dist/ to hold exactly one wheel and one sdist.
#
# Env overrides: PYTHON (default python3; must be 3.11 or later), DIST (default dist)
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
DIST="${DIST:-dist}"

case "${1:-}" in
  "") build=1 ;;
  --no-build) build=0 ;;
  *) echo "usage: $0 [--no-build]" >&2; exit 2 ;;
esac

"$PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || {
  echo "smoke_artifacts.sh: $PYTHON is older than Python 3.11 (requires-python); set PYTHON=python3.13" >&2
  exit 2
}

work=$(mktemp -d "${TMPDIR:-/tmp}/govee_smoke.XXXXXX")
trap 'rm -rf "$work"' EXIT

"$PYTHON" -m venv "$work/build"
tools="$work/build/bin/python"   # build and pip wheel run here, so $PYTHON is never changed (PEP 668)
if [ "$build" = 1 ]; then
  rm -rf "$DIST"
  "$tools" -m pip install --quiet build==1.6.1
  "$tools" -m build --outdir "$DIST"
fi

shopt -s nullglob
wheels=("$DIST"/*.whl)
sdists=("$DIST"/*.tar.gz)
shopt -u nullglob
if [ ${#wheels[@]} -ne 1 ] || [ ${#sdists[@]} -ne 1 ]; then
  echo "error: expected exactly one wheel and one sdist in $DIST/, found: ${wheels[*]:-} ${sdists[*]:-}" \
       "(rm -rf $DIST and rebuild)" >&2
  exit 1
fi
wheel=${wheels[0]}
sdist=${sdists[0]}

# A variable, not a pipe: under pipefail an early `grep -q` exit could SIGPIPE tar and fail the check.
listing=$(tar -tzf "$sdist")
grep -q '^[^/]*/src/govee_ble_local/_generated/govee_ble_frame\.py$' <<<"$listing" \
  || { echo "error: $sdist lacks the generated Kaitai classes" >&2; exit 1; }
grep -q '^[^/]*/src/govee_ble_local/_generated/devices\.py$' <<<"$listing" \
  || { echo "error: $sdist lacks the generated device table" >&2; exit 1; }
if grep -qE '^[^/]+/(spec|tools)/' <<<"$listing"; then
  echo "error: $sdist ships spec/ or tools/, so a wheel built from it regenerates and needs the JVM compiler" >&2
  exit 1
fi
echo ">> sdist: ships the generated classes, no spec/ or tools/"

# Imported from the installed package (cwd is the scratch dir, not the repository), so a missing or
# read-only _generated/ fails here, and one frame is built and read back with the classes.
probe='
import govee_ble_local, govee_ble_local.wire.parse
from kaitaistruct import ReadWriteKaitaiStruct
from govee_ble_local._generated.govee_ble_frame import GoveeBleFrame
from govee_ble_local.wire import _spec, build
assert issubclass(GoveeBleFrame, ReadWriteKaitaiStruct), "the wheel ships a read-only build"
assert "site-packages" in govee_ble_local.__file__, govee_ble_local.__file__
f = _spec.read_frame(build.power_query(), _spec.TX, _spec.PLAIN)   # aa 01: built, then read back
assert f.body.command == _spec.Common.Command.power, f.body.command
assert "H60A6" in govee_ble_local.supported_skus(), "the wheel ships no device table"
print("   imports and a frame round trip OK:", govee_ble_local.__file__)
'

smoke() {   # smoke <wheel> <venv name>
  "$PYTHON" -m venv "$work/$2"
  "$work/$2/bin/python" -m pip install --quiet "$1"
  ( cd "$work" && "$work/$2/bin/python" -c "$probe" )
  echo ">> $2: $(basename "$1") OK"
}

smoke "$wheel" wheel
"$tools" -m pip wheel --quiet --no-deps --wheel-dir "$work/from-sdist" "$sdist"
smoke "$(ls "$work"/from-sdist/*.whl)" wheel-from-sdist
echo "ARTIFACT SMOKE TEST PASSED"
