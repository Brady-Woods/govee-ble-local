#!/usr/bin/env bash
#
# Install the pinned Kaitai Struct compiler (JVM ksc 0.11) on a Debian/Ubuntu host, e.g. a GitHub
# Actions runner, so scripts/check.sh and the CI jobs can generate src/govee_ble_local/_generated
# from spec/*.ksy with tools/gen_kaitai.sh. The JVM build is the only one that generates the
# read-write classes the library uses. Needs sudo + apt; it pulls a Java runtime if none is
# registered with dpkg.
#
#     bash scripts/install_ksc.sh
#
# The .deb is the upstream release asset, checked against its published SHA-256
# (https://github.com/kaitai-io/kaitai_struct_compiler/releases/tag/0.11). To bump, change
# KSC_VERSION and KSC_SHA256 here together with KSC_SERIES in tools/gen_kaitai.sh; this script
# refuses to install a version that tools/gen_kaitai.sh would not use.
# On macOS use `brew install kaitai-struct-compiler` instead.
set -euo pipefail
cd "$(dirname "$0")/.."

KSC_VERSION="0.11"
KSC_SHA256="28d6b6ec917e394ed41cb6f62169a8f6b584d9a5242cbe0785b0568d95e032ea"
KSC_URL="https://github.com/kaitai-io/kaitai_struct_compiler/releases/download/${KSC_VERSION}/kaitai-struct-compiler_${KSC_VERSION}_all.deb"

if ! grep -q "^KSC_SERIES=\"${KSC_VERSION}\"" tools/gen_kaitai.sh; then
  echo "error: KSC_VERSION=${KSC_VERSION} does not match KSC_SERIES in tools/gen_kaitai.sh" >&2
  exit 1
fi

deb="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/kaitai-struct-compiler_${KSC_VERSION}_all.deb"
curl -fsSL --retry 3 -o "$deb" "$KSC_URL"
echo "${KSC_SHA256}  ${deb}" | sha256sum -c -

# The .deb's only dependency is java8-runtime-headless. GitHub's runners already provide it with
# dpkg-installed Temurin JDKs, so the install normally fetches nothing; refresh the package index
# and retry only when apt has to fetch a Java runtime.
install_deb() { sudo apt-get install -y -q --no-install-recommends "$deb"; }
install_deb || { sudo apt-get update -q && install_deb; }

ver=$(kaitai-struct-compiler --version | head -1)
case "$ver" in
  "kaitai-struct-compiler ${KSC_VERSION}" | "kaitai-struct-compiler ${KSC_VERSION}."*) echo "$ver" ;;
  *) echo "error: installed '${ver}', expected kaitai-struct-compiler ${KSC_VERSION}" >&2; exit 1 ;;
esac
