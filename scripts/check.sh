#!/usr/bin/env bash
#
# Release quality gate for govee-ble-local.
#
# Runs the exact checks CI runs. Run it before tagging a release:
#
#     scripts/check.sh
#
# Gates (in order, fail-fast):
#   1. ruff check over src tools tests spec (rules in [tool.ruff.lint])
#   2. mypy --strict on src/govee_ble_local (generated readers are exempt via
#      [[tool.mypy.overrides]])
#   3. bandit over src + tools, failing on Medium+ severity (-ll); the generated
#      readers are excluded; accepted findings carry inline `# nosec` + reason
#   4. pip-audit of the package's resolved runtime dependencies, i.e. what users install
#      (needs network: skipped with a message when offline, unless CI=true, where it is
#      mandatory). It deliberately does not scan the CI venv's own tooling (pip, setuptools,
#      pytest, ...), whose advisories are unrelated to what this package ships.
#   5. pytest with a coverage floor over the package (only the Kaitai-generated
#      _generated/ readers are omitted, via [tool.coverage.run])
#
# Env overrides: PYTHON  (default python3)
#                VENV    (default .venv-check)
#                COV_MIN (default 85)
#                CI      (set to "true" to make the pip-audit network check mandatory)
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
VENV="${VENV:-.venv-check}"
COV_MIN="${COV_MIN:-85}"

if [ ! -x "$VENV/bin/python" ]; then
  echo ">> creating venv: $VENV"
  "$PYTHON" -m venv "$VENV"
fi
# shellcheck source=/dev/null
source "$VENV/bin/activate"

echo ">> installing (editable + test + typing + lint extras)"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e ".[test,typing,lint]"

echo ">> ruff check"
ruff check src tools tests spec

echo ">> mypy --strict"
mypy src/govee_ble_local

echo ">> bandit (Medium+ severity fails)"
bandit -q -r src tools -x src/govee_ble_local/_generated -ll

echo ">> pip-audit"
if [ "${CI:-}" != "true" ] && ! "$PYTHON" -c "import socket; socket.create_connection(('pypi.org', 443), timeout=5).close()" 2>/dev/null; then
  echo "   SKIPPED: offline (pip-audit needs network; CI=true makes this mandatory)"
else
  pip-audit .
fi

echo ">> pytest (coverage gate: >= ${COV_MIN}% of pure-logic modules)"
pytest --cov=govee_ble_local --cov-report=term-missing --cov-fail-under="${COV_MIN}"

echo ""
echo "ALL QUALITY GATES PASSED (govee-ble-local)"
