# Govee BLE protocol spec

Reverse-engineered, source-grounded specification of the Govee Home BLE GATT protocol (vendor control
service `00010203-0405-0607-0809-0a0b0c0d1910`). Every structural claim is cited to the decompiled Android
app; the binary layer is an executable Kaitai grammar; the device catalogue is a schema-validated registry.

## Layout
This package is rooted at `spec/`. The **machine artifacts** (`*.ksy`, `devices.yaml`, `devices.schema.json`,
`govee_reference.py`) live at the package root; the **docs** (this `README.md` + the three `*.md`) live in
`spec/docs/`. Paths below are written from this `docs/` folder, so artifacts are `../<name>`.

## Contents

| File | What it is |
|---|---|
| **`GOVEE_BLE_GATT_PROTOCOL.md`** | The specification — GATT profile, framing, opcodes, modes, scenes, encryption, per-device matrix, advertisement. Start here. §17.2 indexes the machine artifacts. |
| **`../govee_ble.ksy`** | Kaitai Struct grammar for the 20-byte application frame + reassembled value/status types. |
| **`../govee_adv.ksy`** | Kaitai grammar for the advertisement (manufacturer-data) — passive identification. |
| **`../devices.yaml`** | Machine-readable device registry: goodsTypes, SKUs, opcodes, per-family specs, `client_profile`. |
| **`../devices.schema.json`** | JSON Schema (Draft 2020-12) validating `devices.yaml`. |
| **`SCENE_UPLOAD_ENCODING.md`** | Scene / DIY upload encoding + the `parseSceneV1` dispatch reference. |
| **`USING_THE_KSY.md`** | How to consume the grammar in your own client (any language): the decrypt → reassemble → parse pipeline, worked examples, gotchas. |
| **`../govee_reference.py`** | Executable reference codec — the three things Kaitai can't do (decrypt AES-ECB+RC4 · reassemble · XOR BCC) + dispatch wiring. Also the round-trip test harness. |

## Read order
1. `GOVEE_BLE_GATT_PROTOCOL.md` — the protocol.
2. `USING_THE_KSY.md` — how to build a client around the grammar.
3. `../govee_reference.py` — the framing layer as runnable code.
4. `SCENE_UPLOAD_ENCODING.md` — only if you need scene/DIY upload.

## Validate (all should pass)
```bash
cd ..                        # run from the package root (spec/), which holds the artifacts
KSC=kaitai-struct-compiler   # 0.11
for f in govee_ble govee_adv; do "$KSC" -t python --outdir /tmp/gsy $f.ksy; done   # 0 errors
python3 -c "import yaml,json,jsonschema; jsonschema.validate(yaml.safe_load(open('devices.yaml')),json.load(open('devices.schema.json')))"
PYTHONPATH=/tmp/gsy python3 govee_reference.py            # reference-codec self-test
```

## Provenance
`file:line` citations of the form `base2light/...`, `sources/com/govee/...`, `splits/dec2/<family>/...`
are relative to the **decompiled-APK root** (the parent of this folder), not to this package. They are the
evidence for each claim; keep the decompile alongside if you need to re-verify.

The authoritative sources are the decompiled Java and real hardware/btsnoop captures — this spec is derived
from them and was cross-validated against an independent client's scene catalogue (543 scene values
deep-parse) and plaintext captures.
