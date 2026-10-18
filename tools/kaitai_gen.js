#!/usr/bin/env node
// Compile the spec/*.ksy Kaitai definitions to read-only Python readers with the pure-JS
// kaitai-struct-compiler (the fallback when no JVM ksc is installed; the JS compile() API has no
// read-write switch, so --read-write always uses the JVM CLI). Run via tools/gen_kaitai.sh,
// which puts the userland Node on PATH and points NODE_PATH at the scratch
// node_modules created during setup.
"use strict";

const fs = require("fs");
const path = require("path");

const KaitaiStructCompiler = require("kaitai-struct-compiler");
const yaml = require("js-yaml");

const REPO = path.resolve(__dirname, "..");
const SPEC_DIR = path.join(REPO, "spec");
const LIBRARY_DIR = path.join(REPO, "src", "govee_ble_local", "_generated");
// Output directory, required (gen_kaitai.sh always passes one: --read-only requires --out). The
// library's own classes are read-write, which this compiler can't build, so their directory is refused.
if (!process.argv[2]) {
  console.error("usage: kaitai_gen.js <outdir>   (read-only readers; run via tools/gen_kaitai.sh --read-only --out DIR)");
  process.exit(2);
}
const OUT_DIR = path.resolve(process.argv[2]);
if (OUT_DIR === LIBRARY_DIR) {
  console.error("error: the library's classes are read-write; write read-only readers to a scratch directory");
  process.exit(2);
}

// The .ksy files to compile (gen_kaitai.sh passes every spec/*.ksy), and an importer that resolves their
// `imports:` from spec/. The JS compile() API has no --python-package switch, so these readers import each
// other absolutely: put the output directory on sys.path to use them.
const KSY_FILES = process.argv.slice(3);
if (KSY_FILES.length === 0) {
  console.error("usage: kaitai_gen.js <outdir> <file.ksy>...");
  process.exit(2);
}
const importer = {
  importYaml(name, _mode) {
    return Promise.resolve(yaml.load(fs.readFileSync(path.join(SPEC_DIR, `${name}.ksy`), "utf8")));
  },
};

async function main() {
  // kaitai-struct-compiler >= 0.11.0 exports the compiler object directly
  // (call .compile on it); older versions exported a constructor.
  const compiler =
    typeof KaitaiStructCompiler === "function"
      ? new KaitaiStructCompiler()
      : KaitaiStructCompiler;
  fs.mkdirSync(OUT_DIR, { recursive: true });

  for (const ksyName of KSY_FILES) {
    const ksyPath = path.join(SPEC_DIR, ksyName);
    const parsed = yaml.load(fs.readFileSync(ksyPath, "utf8"));
    // compile(lang, ksy, importer, debug) -> { 'file.py': 'contents', ... }
    const files = await compiler.compile("python", parsed, importer, false);
    for (const [fname, contents] of Object.entries(files)) {
      const dest = path.join(OUT_DIR, fname);
      fs.writeFileSync(dest, contents);
      console.log(`wrote ${path.relative(REPO, dest)} (${contents.length} bytes)`);
    }
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
