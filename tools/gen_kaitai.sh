#!/usr/bin/env bash
#
# Regenerate the Python Kaitai readers from spec/*.ksy.
#
#   bash tools/gen_kaitai.sh
#
# Output: tests/spec_gen/{govee_ble_frame,govee_advertisement}.py (committed, so
# environments without Node can still run the offline spec suite).
#
# Uses the pure-JS kaitai-struct-compiler (no JVM). It needs Node + the two npm
# packages (kaitai-struct-compiler, js-yaml). Resolution order for Node:
#   1. a userland Node under .toolchain/ (see below), if present
#   2. whatever `node` is on PATH
#
# One-time userland setup (no admin/JVM required), if you have neither Node nor
# the compiler packages:
#   VER=$(curl -s https://nodejs.org/dist/index.json | python3 -c \
#     "import sys,json;print([x for x in json.load(sys.stdin) if x['lts']][0]['version'])")
#   curl -sL "https://nodejs.org/dist/$VER/node-$VER-darwin-arm64.tar.gz" | tar -xz -C .toolchain/
#   echo ".toolchain/node-$VER-darwin-arm64" > .toolchain/node_dir.txt
#   PATH="$PWD/$(cat .toolchain/node_dir.txt)/bin:$PATH" \
#     npm --prefix .toolchain/ksc install kaitai-struct-compiler js-yaml
set -euo pipefail
cd "$(dirname "$0")/.."

# 1. locate node
if [ -f .toolchain/node_dir.txt ] && [ -x "$(cat .toolchain/node_dir.txt)/bin/node" ]; then
  export PATH="$PWD/$(cat .toolchain/node_dir.txt)/bin:$PATH"
fi
if ! command -v node >/dev/null 2>&1; then
  echo "error: 'node' not found. See the userland setup block in this script." >&2
  exit 1
fi

# 2. ensure the compiler packages
if [ ! -d .toolchain/ksc/node_modules/kaitai-struct-compiler ]; then
  echo ">> installing kaitai-struct-compiler + js-yaml into .toolchain/ksc"
  mkdir -p .toolchain/ksc
  [ -f .toolchain/ksc/package.json ] || echo '{"name":"ksc-scratch","private":true}' > .toolchain/ksc/package.json
  ( cd .toolchain/ksc && npm install --no-audit --no-fund --loglevel=error kaitai-struct-compiler js-yaml )
fi

# 3. generate
export NODE_PATH="$PWD/.toolchain/ksc/node_modules"
node tools/kaitai_gen.js
echo ">> done: tests/spec_gen/"
