#!/usr/bin/env bash
#
# Compile spec/*.ksy with the Kaitai Struct compiler (ksc 0.11.x).
#
#   bash tools/gen_kaitai.sh                       # read-only readers -> src/govee_ble_local/_generated/
#   bash tools/gen_kaitai.sh --check               # fail if the committed readers differ from a fresh build
#   bash tools/gen_kaitai.sh --read-write          # read-write (serializer) classes -> build/kaitai_rw/
#   bash tools/gen_kaitai.sh --read-write --out D  # ... into directory D instead
#
# The read-only readers are committed (so environments without a compiler can still run the
# tests) and are what the library imports. --read-write writes Kaitai 0.11 read-write classes
# (ReadWriteKaitaiStruct: _read() is explicit, _write() serializes). They are NOT written into
# _generated/ by default: switching the library to them changes the reader API (no auto-read),
# so that has to happen together with the parse-site changes in src/.
#
# Compilers. ksc is published in two builds that produce the same Python:
#   - the JVM CLI (`kaitai-struct-compiler`, e.g. `brew install kaitai-struct-compiler`, which
#     brings its own OpenJDK). Preferred, and the only one that exposes --read-write.
#   - the pure-JS build on npm, driven by tools/kaitai_gen.js. Its compile() API has no
#     read-write switch, so it is used only as a read-only fallback when no JVM ksc is found.
# Both builds give byte-identical read-only output on ksc 0.11 (checked 2026-10-16).
# Override the JVM compiler with KSC=/path/to/kaitai-struct-compiler.
#
# JS fallback, one-time userland setup (no admin/JVM required) if you have neither Node nor
# the compiler packages:
#   VER=$(curl -s https://nodejs.org/dist/index.json | python3 -c \
#     "import sys,json;print([x for x in json.load(sys.stdin) if x['lts']][0]['version'])")
#   curl -sL "https://nodejs.org/dist/$VER/node-$VER-darwin-arm64.tar.gz" | tar -xz -C .toolchain/
#   echo ".toolchain/node-$VER-darwin-arm64" > .toolchain/node_dir.txt
#   PATH="$PWD/$(cat .toolchain/node_dir.txt)/bin:$PATH" \
#     npm --prefix .toolchain/ksc install kaitai-struct-compiler@0.11.0 js-yaml
set -euo pipefail
cd "$(dirname "$0")/.."

KSY_FILES=(govee_ble.ksy govee_adv.ksy)
GEN_FILES=(govee_ble_frame.py govee_advertisement.py)
COMMITTED=src/govee_ble_local/_generated
KSC_SERIES="0.11"   # the committed readers and the kaitaistruct>=0.11 runtime floor

mode=readonly
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --read-write|-w) mode=readwrite ;;
    --check) mode=check ;;
    --out) [ $# -ge 2 ] || { echo "error: --out needs a directory" >&2; exit 2; }; out="$2"; shift ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "error: unknown argument '$1' (see --help)" >&2; exit 2 ;;
  esac
  shift
done
if [ -n "$out" ] && [ "$mode" = check ]; then
  echo "error: --out can't be combined with --check" >&2; exit 2
fi

# Find a JVM ksc of the pinned series; prints its path, or nothing.
find_jvm_ksc() {
  local cand path ver
  for cand in ${KSC:+"$KSC"} kaitai-struct-compiler ksc; do
    path=$(command -v "$cand" 2>/dev/null) || continue
    ver=$("$path" --version 2>/dev/null | head -1) || continue
    case "$ver" in
      "kaitai-struct-compiler $KSC_SERIES"|"kaitai-struct-compiler $KSC_SERIES".*) echo "$path"; return 0 ;;
      kaitai-struct-compiler*) echo "warning: ignoring $path ($ver); this repo pins ksc $KSC_SERIES.x" >&2 ;;
    esac
  done
  return 1
}

# compile_with_jvm <outdir> [extra ksc args...]
# The JVM CLI prints ksy style-guide warnings (naming hints) on every run; show a count unless
# VERBOSE=1, and the full log whenever the compile fails.
compile_with_jvm() {
  local dest=$1; shift
  local log rc=0 nwarn
  mkdir -p "$dest"
  dest=$(cd "$dest" && pwd)
  log=$(mktemp "${TMPDIR:-/tmp}/gen_kaitai_ksc.XXXXXX")
  ( cd spec && "$KSC_BIN" -t python "$@" --outdir "$dest" "${KSY_FILES[@]}" ) >"$log" 2>&1 || rc=$?
  if [ "$rc" -ne 0 ] || [ "${VERBOSE:-0}" = 1 ]; then
    cat "$log" >&2
  else
    nwarn=$(grep -c 'warning:' "$log" || true)
    [ "$nwarn" -eq 0 ] || echo "   ($nwarn ksy style warnings; VERBOSE=1 to show them)"
  fi
  rm -f "$log"
  return "$rc"
}

# compile_with_js <outdir>   (read-only only)
compile_with_js() {
  if [ -f .toolchain/node_dir.txt ] && [ -x "$(cat .toolchain/node_dir.txt)/bin/node" ]; then
    export PATH="$PWD/$(cat .toolchain/node_dir.txt)/bin:$PATH"
  fi
  if ! command -v node >/dev/null 2>&1; then
    echo "error: no JVM kaitai-struct-compiler $KSC_SERIES.x and no 'node' for the JS fallback." >&2
    echo "       Install one: 'brew install kaitai-struct-compiler', or the userland Node setup" >&2
    echo "       at the top of this script." >&2
    exit 1
  fi
  if [ ! -d .toolchain/ksc/node_modules/kaitai-struct-compiler ]; then
    echo ">> installing kaitai-struct-compiler@0.11.0 + js-yaml into .toolchain/ksc"
    mkdir -p .toolchain/ksc
    [ -f .toolchain/ksc/package.json ] || echo '{"name":"ksc-scratch","private":true}' > .toolchain/ksc/package.json
    ( cd .toolchain/ksc && npm install --no-audit --no-fund --loglevel=error kaitai-struct-compiler@0.11.0 js-yaml )
  fi
  NODE_PATH="$PWD/.toolchain/ksc/node_modules" node tools/kaitai_gen.js "$1"
}

KSC_BIN=$(find_jvm_ksc || true)

case "$mode" in
  readonly)
    dest=${out:-$COMMITTED}
    if [ -n "$KSC_BIN" ]; then
      echo ">> compiling read-only readers with $KSC_BIN ($("$KSC_BIN" --version | head -1))"
      compile_with_jvm "$dest"
    else
      echo ">> no JVM ksc $KSC_SERIES.x found; using the pure-JS compiler (read-only)"
      compile_with_js "$dest"
    fi
    # Keep the generated dir an importable package.
    if [ "$dest" = "$COMMITTED" ] && [ ! -f "$dest/__init__.py" ]; then
      echo '"""Kaitai-generated readers (regenerate via tools/gen_kaitai.sh)."""' > "$dest/__init__.py"
    fi
    echo ">> done: $dest/"
    ;;
  readwrite)
    if [ -z "$KSC_BIN" ]; then
      echo "error: --read-write needs the JVM kaitai-struct-compiler $KSC_SERIES.x (the npm build's" >&2
      echo "       compile() API can't generate read-write code). Install it, e.g." >&2
      echo "       'brew install kaitai-struct-compiler', or point KSC= at one." >&2
      exit 1
    fi
    dest=${out:-build/kaitai_rw}
    if [ "$dest" = "$COMMITTED" ]; then
      echo "warning: writing read-write classes over the committed readers. The library's parse" >&2
      echo "         sites call from_bytes() and expect auto-read; adapt them in the same change." >&2
    fi
    echo ">> compiling read-write classes with $KSC_BIN ($("$KSC_BIN" --version | head -1))"
    compile_with_jvm "$dest" --read-write
    echo ">> done: $dest/ (needs kaitaistruct>=0.11; call _read() after from_bytes())"
    ;;
  check)
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/gen_kaitai_check.XXXXXX")
    trap 'rm -rf "$tmp"' EXIT
    if [ -n "$KSC_BIN" ]; then compile_with_jvm "$tmp" >/dev/null; else compile_with_js "$tmp" >/dev/null; fi
    drift=0
    for f in "${GEN_FILES[@]}"; do
      if ! cmp -s "$tmp/$f" "$COMMITTED/$f"; then
        echo "DRIFT: $COMMITTED/$f differs from a fresh build of spec/*.ksy" >&2
        drift=1
      fi
    done
    if [ "$drift" -ne 0 ]; then
      echo "Run 'bash tools/gen_kaitai.sh' and commit the readers with the .ksy change." >&2
      exit 1
    fi
    echo ">> committed readers match spec/*.ksy"
    ;;
esac
