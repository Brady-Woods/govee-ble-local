#!/usr/bin/env bash
#
# Release quality gate for govee-ble-local.
#
# Runs CI's code gate (CI and release also run scripts/smoke_artifacts.sh). Run it before tagging a release:
#
#     scripts/check.sh
#
# Gates (in order, fail-fast):
#   1. generate the Kaitai read-write classes in src/govee_ble_local/_generated from spec/*.ksy, and the
#      device table from spec/devices.yaml (tools/gen_kaitai.sh, with the venv's Python and its PyYAML),
#      so every later gate runs against the spec as it is. They are a build product (gitignored; every
#      build generates them, see _build_backend/). It needs the pinned JVM kaitai-struct-compiler
#      (gen_kaitai.sh KSC_SERIES): `brew install kaitai-struct-compiler`, or scripts/install_ksc.sh on
#      Debian/Ubuntu (what CI runs).
#   2. the spec's structural gate, Suite S (spec/checks/run.sh, owned by the spec side: ksc python /
#      java / javascript with no warnings, then spec/checks/lint.py)
#   3. ruff check over src tools tests spec _build_backend (rules in [tool.ruff.lint])
#   4. mypy --strict on src/govee_ble_local and the build backend (generated classes are exempt
#      via [[tool.mypy.overrides]])
#   5. bandit over src + tools + _build_backend, failing on Medium+ severity (-ll); the generated
#      classes are excluded; accepted findings carry inline `# nosec` + reason
#   6. pip-audit of the package's resolved runtime dependencies, i.e. what users install
#      (needs network: skipped with a message when offline, unless CI=true, where it is
#      mandatory). It deliberately does not scan the CI venv's own tooling (pip, setuptools,
#      pytest, ...), whose advisories are unrelated to what this package ships.
#   7. pytest with a coverage floor over the package, statements and branches (the same
#      gate as hass-govee-ble-local's scripts/check.sh: 95%, --cov-branch). Only the
#      Kaitai-generated _generated/ classes are omitted, via [tool.coverage.run]
#
# Env overrides: PYTHON  (default python3)
#                VENV    (default .venv-check)
#                COV_MIN (default 95, matching hass-govee-ble-local)
#                CI      (set to "true" to make the pip-audit network check mandatory)
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
VENV="${VENV:-.venv-check}"
COV_MIN="${COV_MIN:-95}"

if [ ! -x "$VENV/bin/python" ]; then
  echo ">> creating venv: $VENV"
  "$PYTHON" -m venv "$VENV"
fi
# shellcheck source=/dev/null
source "$VENV/bin/activate"
python -m pip install --quiet --upgrade pip PyYAML

echo ">> generate the Kaitai read-write classes from spec/*.ksy and the device table from spec/devices.yaml"
# gen_kaitai.sh itself requires the pinned JVM compiler for this build (no JS fallback).
PYTHON=python bash tools/gen_kaitai.sh

echo ">> installing (editable + test + typing + lint extras)"
python -m pip install --quiet -e ".[test,typing,lint]"

echo ">> spec lints (Suite S)"
PYTHON=python bash spec/checks/run.sh

echo ">> ruff check"
ruff check src tools tests spec _build_backend

echo ">> mypy --strict"
mypy src/govee_ble_local _build_backend

echo ">> bandit (Medium+ severity fails)"
bandit -q -r src tools _build_backend -x src/govee_ble_local/_generated -ll

echo ">> pip-audit"
if [ "${CI:-}" != "true" ] && ! "$PYTHON" -c "import socket; socket.create_connection(('pypi.org', 443), timeout=5).close()" 2>/dev/null; then
  echo "   SKIPPED: offline (pip-audit needs network; CI=true makes this mandatory)"
else
  pip-audit .
fi

echo ">> pytest (coverage gate: >= ${COV_MIN}% statements+branches)"
pytest --cov=govee_ble_local --cov-branch --cov-report=term-missing --cov-fail-under="${COV_MIN}"

echo ""
echo "ALL QUALITY GATES PASSED (govee-ble-local)"
