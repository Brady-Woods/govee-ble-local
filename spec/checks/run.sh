#!/usr/bin/env bash
#
# Suite S: the spec's structural gate (CLEAN_SPEC_DESIGN 1.6, as cut in section 10).
#
#     spec/checks/run.sh [SPEC_DIR]
#
# SPEC_DIR: the argument, else $SPEC_DIR, else this script's parent (spec/).
#   1. ksc over every SPEC_DIR/*.ksy at once: python + java read-write (-w), then javascript
#      (ksc's -w is java/python only). Any compiler output, warning or error, fails.
#   2. lint.py: meta keys, banned constructs, labelled roots and reachability, enums applied,
#      devices.schema.json / devices.yaml enum equality, doc-ref format, and firmware.yaml against
#      its schema and the .ksy types.
# Generated code goes to a temp dir that is removed on exit; nothing is written anywhere else.
#
# Env: PYTHON (default python3; needs PyYAML and jsonschema), KSC (default kaitai-struct-compiler, 0.11).
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
spec=$(cd "${1:-${SPEC_DIR:-$here/..}}" && pwd)
ksc="${KSC:-kaitai-struct-compiler}"
python="${PYTHON:-python3}"

shopt -s nullglob
ksy=("$spec"/*.ksy)
if [ ${#ksy[@]} -eq 0 ]; then
  echo "FAIL: no .ksy files in $spec"
  exit 1
fi

tmp=$(mktemp -d "${TMPDIR:-/tmp}/suite-s.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

compile() {
  local out
  if ! out=$("$ksc" "$@" "${ksy[@]}" 2>&1) || [ -n "$out" ]; then
    echo "FAIL: kaitai-struct-compiler $*"
    echo "$out"
    exit 1
  fi
}

echo ">> ksc: ${#ksy[@]} files, python + java (-w), javascript"
compile -t python -t java -w --python-package . --outdir "$tmp/rw"
compile -t javascript --outdir "$tmp/js"

echo ">> lint"
"$python" "$here/lint.py" "$spec"
