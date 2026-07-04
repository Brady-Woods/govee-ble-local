#!/usr/bin/env bash
#
# Release quality gate for govee-ble-local.
#
# Runs the exact checks CI runs. Run it before tagging a release:
#
#     scripts/check.sh
#
# Gates:
#   1. mypy --strict on the whole package (incl. the bleak I/O wrapper)
#   2. pytest with a coverage floor on the pure-logic modules
#      (client.py is excluded via [tool.coverage] - it's covered by the
#      real-device suite in tools/, not unit tests)
#
# Env overrides: PYTHON (default python3), VENV (default .venv-check),
#                COV_MIN (default 90).
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
VENV="${VENV:-.venv-check}"
COV_MIN="${COV_MIN:-90}"

if [ ! -x "$VENV/bin/python" ]; then
  echo ">> creating venv: $VENV"
  "$PYTHON" -m venv "$VENV"
fi
# shellcheck source=/dev/null
source "$VENV/bin/activate"

echo ">> installing (editable + test + typing extras)"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e ".[test,typing]"

echo ">> mypy --strict"
mypy src/govee_ble_local

echo ">> pytest (coverage gate: >= ${COV_MIN}% of pure-logic modules)"
pytest --cov=govee_ble_local --cov-report=term-missing --cov-fail-under="${COV_MIN}"

echo ""
echo "ALL QUALITY GATES PASSED (govee-ble-local)"
