#!/usr/bin/env bash
#
# Informational code-health report for govee-ble-local. NOT a gate: always exits 0.
# (scripts/check.sh is the pass/fail gate.)
#
#     scripts/audit.sh
#
# Sections: vulture (dead code, config in [tool.vulture]), radon cyclomatic complexity (rank C
# and worse), radon maintainability index (rank B and worse), ruff rule statistics.
#
# Env overrides: PYTHON (default python3), VENV (default .venv-check)
set -uo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
VENV="${VENV:-.venv-check}"

if [ ! -x "$VENV/bin/python" ]; then
  echo ">> creating venv: $VENV"
  "$PYTHON" -m venv "$VENV"
fi
# shellcheck source=/dev/null
source "$VENV/bin/activate"

echo ">> installing (editable + audit + lint extras)"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e ".[audit,lint]" || echo "   (install failed; continuing with what is available)"

echo ""
echo "=== vulture (dead code, min_confidence from pyproject) ==="
vulture || true

echo ""
echo "=== radon cc (cyclomatic complexity, rank C or worse) ==="
radon cc src -s -n C --exclude '*/_generated/*' || true

echo ""
echo "=== radon mi (maintainability index, rank B or worse) ==="
radon mi src -n B --exclude '*/_generated/*' || true

echo ""
echo "=== ruff statistics (src tools tests spec) ==="
ruff check --statistics src tools tests spec || true

echo ""
echo "audit complete (informational; exit status is always 0)"
exit 0
