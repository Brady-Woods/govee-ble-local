# Govee BLE protocol spec

Reverse-engineered, source-grounded specification of the Govee Home BLE GATT protocol (vendor control
service `00010203-0405-0607-0809-0a0b0c0d1910`). Every structural claim is cited to the decompiled Android
app or device firmware; the binary layer is an executable Kaitai grammar; the device catalogue is a
schema-validated registry.

## Layout
This package is rooted at `spec/`. The **machine artifacts** (`*.ksy`, `devices.yaml`, `devices.schema.json`,
`govee_reference.py`, `govee_commands.py`, the `checks/` gate) live at the package root; the **docs** (this
`README.md` and the other `*.md`) live in `spec/docs/`. Paths below are written from this `docs/` folder, so artifacts are `../<name>`.

## Contents

| File | What it is |
|---|---|
| **`GOVEE_BLE_GATT_PROTOCOL.md`** | The specification — GATT profile, framing, opcodes, modes, scenes, encryption, per-device matrix, advertisement. Start here. §17.2 indexes the machine artifacts. |
| **`../govee_ble_frame.ksy`** | Kaitai Struct grammar for one GATT write / notification value (every direction, proType and overload), parametrised by `direction` and the device `govee_profile`. |
| **`../govee_status_stream.ksy`**, **`../govee_upload.ksy`** | The joined multi-frame values: the `0xAC` replies (the status TLV stream, the special-info segment IC record), and the `0xA1`/`0xA3`/`0xA4` upload values and cloud scene blobs. |
| **`../govee_manufacturer_data.ksy`**, **`../govee_scan_record.ksy`**, **`../govee_bgc_info.ksy`** | The other channels: the `0xFF` manufacturer AD payload, the raw scan record, and the GATT `2b12` value. |
| **`../govee_state.ksy`**, **`../govee_profile.ksy`**, **`../govee_common.ksy`** | Shared: the `aa` reply / status-TLV state record, the per-device context, and the shared enums and leaf types. |
| **`../devices.yaml`** | Machine-readable device registry: goodsTypes, SKUs, opcodes, per-family specs, `client_profile`. |
| **`../devices.schema.json`** | JSON Schema (Draft 2020-12) validating `devices.yaml`. |
| **`SCENE_UPLOAD_ENCODING.md`** | Scene / DIY upload encoding + the `parseSceneV1` dispatch reference. |
| **`USING_THE_KSY.md`** | How to consume the grammar in your own client (any language): which root takes which bytes, the profile, the decrypt → parse → join pipeline, building messages, gotchas. |
| **`../govee_reference.py`** | Executable reference codec — the three things Kaitai can't do (decrypt AES-ECB+RC4 · reassemble · checksum) + dispatch wiring. Also the round-trip test harness. |
| **`../govee_commands.py`**, **`OPCODE_COVERAGE.md`** | The app-traced command-builder catalogue and its opcode index. |

## Read order
1. `GOVEE_BLE_GATT_PROTOCOL.md` — the protocol.
2. `USING_THE_KSY.md` — how to build a client around the grammar.
3. `../govee_reference.py` — the framing layer as runnable code.
4. `SCENE_UPLOAD_ENCODING.md` — only if you need scene/DIY upload.

## Validate (all should pass)
```bash
cd ..                        # run from the package root (spec/), which holds the artifacts
bash checks/run.sh           # ksc 0.11 over every .ksy (python + java -w, javascript) + the structural lints
python3 -c "import yaml,json,jsonschema; jsonschema.validate(yaml.safe_load(open('devices.yaml')),json.load(open('devices.schema.json')))"
kaitai-struct-compiler -t python --outdir /tmp/gsy *.ksy
PYTHONPATH=/tmp/gsy python3 govee_reference.py --strict   # reference-codec self-test
```

## Provenance
App `path:line` citations (`base-classes10/...`, `split_h60ax/...` and the like) are relative to the
**app decompile root**; firmware `firmware-*/decompiled/*.c:line` citations are relative to the firmware
workspace next to it; `IMAGE symbol @0xaddr` names a firmware image and an address in it. None of the
decompile is in this package. The citations are the evidence for each claim; keep the decompile alongside
if you need to re-verify.

The authoritative sources are the decompiled app and firmware. This spec is derived from them; real hardware
confirms behaviour and btsnoop captures illustrate it. It was cross-validated against an independent client's
scene catalogue (543 scene values deep-parse) and plaintext captures.
