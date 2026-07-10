#!/usr/bin/env node
// Compile the spec/*.ksy Kaitai definitions to Python readers.
// Uses the pure-JS kaitai-struct-compiler (no JVM). Run via tools/gen_kaitai.sh,
// which puts the userland Node on PATH and points NODE_PATH at the scratch
// node_modules created during setup.
"use strict";

const fs = require("fs");
const path = require("path");

const KaitaiStructCompiler = require("kaitai-struct-compiler");
const yaml = require("js-yaml");

const REPO = path.resolve(__dirname, "..");
const SPEC_DIR = path.join(REPO, "spec");
const OUT_DIR = path.join(REPO, "tests", "spec_gen");

const KSY_FILES = ["govee_ble.ksy", "govee_adv.ksy"];

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
    const files = await compiler.compile("python", parsed, null, false);
    for (const [fname, contents] of Object.entries(files)) {
      const dest = path.join(OUT_DIR, fname);
      fs.writeFileSync(dest, contents);
      console.log(`wrote ${path.relative(REPO, dest)} (${contents.length} bytes)`);
    }
  }

  // Make the generated dir an importable package.
  const initPath = path.join(OUT_DIR, "__init__.py");
  if (!fs.existsSync(initPath)) {
    fs.writeFileSync(initPath, '"""Kaitai-generated readers (regenerate via tools/gen_kaitai.sh)."""\n');
    console.log(`wrote ${path.relative(REPO, initPath)}`);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
