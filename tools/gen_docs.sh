#!/usr/bin/env bash
#
# Generate the public API reference from the package docstrings with pdoc.
#
#   pip install '.[docs]'
#   bash tools/gen_docs.sh
#
# Output: docs/api/ (gitignored — regenerate on demand; publishable to GitHub Pages later).
# The public API contract is govee_ble_local.__all__.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! python -c "import pdoc" 2>/dev/null; then
  echo "error: pdoc not installed. Run: pip install '.[docs]'" >&2
  exit 1
fi

# Works whether or not the package is pip-installed: prefer the source tree.
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
python -m pdoc govee_ble_local -o docs/api "$@"
echo ">> wrote docs/api/  (open docs/api/govee_ble_local.html)"
