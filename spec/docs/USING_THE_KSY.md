# Using the Govee BLE `.ksy` spec in your own project

The `spec/*.ksy` files are [Kaitai Struct](https://kaitai.io) grammars for the Govee BLE vendor protocol.
Compile them once and you get a **reader and writer in your language** (Python, Java, JavaScript, …), with
no hand-written byte-shuffling. This guide shows which entry point takes which bytes, what context you pass
in, how to read and build messages, and **what the spec does not do for you** (decryption, joining chunks,
checksums, profile resolution).

Companions: `GOVEE_BLE_GATT_PROTOCOL.md` (protocol semantics, §-referenced below), `../devices.yaml`
(per-SKU facts and the `client_profile.wire` block), `SCENE_UPLOAD_ENCODING.md` (scene dispatch). Every
type cites the decompiled app (`doc-ref`, `path:line`) or the firmware handler it was traced from.

---

## 1. The files

| File | Top-level type | Role |
|---|---|---|
| `govee_ble_frame.ksy` | `govee_ble_frame(direction, profile)` | **Entry point**: one GATT value written to `…2b11` or notified on `…2b10` |
| `govee_status_stream.ksy` | `govee_status_stream(profile, reader)` | **Entry point**: the joined data of one `0xAC` burst, by its reader: the status read's TLV stream, or the special-info read's segment IC record |
| `govee_upload.ksy` | `govee_upload(kind, form)` | **Entry point**: one joined `0xA1`/`0xA3`/`0xA4` upload value, or one decoded cloud `scenceParam` |
| `govee_manufacturer_data.ksy` | `govee_manufacturer_data` | **Entry point**: the payload of one manufacturer-specific AD (type `0xFF`) |
| `govee_scan_record.ksy` | `govee_scan_record` | **Entry point**: a raw advertising / scan-response PDU, or Android's 62-byte scan record |
| `govee_bgc_info.ksy` | `govee_bgc_info` | **Entry point**: the value of GATT characteristic `…2b12` (cipher version) |
| `govee_state.ksy` | `govee_state(command, source, profile)` | Shared payload: the body of an `aa <cmd>` reply **and** the value of a `0xAC` TLV with tag `<cmd>` |
| `govee_profile.ksy` | `govee_profile(forms, counts, group, power, mode, mode_status)` | Context (params only, never read): the per-device facts a frame does not carry |
| `govee_common.ksy` | `govee_common` | Library: shared enums (`pro_type`, `command`, `sub_mode`, the profile selectors, …) and leaf types (`rgb`, `level`, `seg_mask`, `echo`, …) |

Each message has exactly **one** path from one entry point. Only the six entry points take bytes from
outside; `govee_state` is reached through the frame's `read_reply` and the stream's `status_tlv`, and is
not an entry point. A root's doc starts with `STANDALONE (<reason>)`; other labels you will meet in the
docs are `PROCEDURAL` (a client step, not grammar), `FIRMWARE-SOURCED` (the app never sends or reads
it), `CAUTION` (destructive commands), `VALIDATOR (read-side)` and `DATA` (a plain byte field that must
round-trip).

---

## 2. Compile

Install the compiler (`ksc`): [releases](https://kaitai.io/#download) or `brew install
kaitai-struct-compiler` (needs a JRE). The spec is validated with **ksc 0.11**. The files import each
other, so compile **all of them in one call**, from `spec/` (the parent of this `docs/` folder):

```console
# Python, read-write classes as top-level modules (put OUT on sys.path)
kaitai-struct-compiler -t python -w --outdir OUT *.ksy

# Python, as a package (`from . import govee_common`): what the library generates into _generated/
kaitai-struct-compiler -t python -w --python-package . --outdir OUT/mypkg *.ksy

# other targets
kaitai-struct-compiler -t java -w --java-package com.example.govee --outdir OUT *.ksy
kaitai-struct-compiler -t javascript --outdir OUT *.ksy   # ksc 0.11 has no -w for JavaScript

# an HTML reference or a Graphviz diagram
kaitai-struct-compiler -t html --outdir OUT *.ksy
kaitai-struct-compiler -t graphviz --outdir OUT *.ksy
```

Add the Kaitai runtime for your language (`pip install 'kaitaistruct>=0.11'`, the
`io.kaitai:kaitai-struct-runtime` Maven artifact, the `kaitai-struct` npm package). One module per `.ksy`
comes out: `govee_ble_frame.py` with class `GoveeBleFrame`, `govee_common.py` with `GoveeCommon`, and so on.
`-w` gives read-write classes (`_read()`, `_check()`, `_write()`); without it, a read-only class reads in
its constructor.

The spec gate `checks/run.sh` compiles every file for Python and Java (`-w`) and JavaScript and fails on
any compiler output, then runs the structural lints (`checks/lint.py`).

---

## 3. The pipeline: what to do before and after parsing

The grammar models **plaintext** bytes from one byte source. A raw notification is not that yet:

```
   raw value off 2b10 / 2b11
        │
        ▼
  (1) DECRYPT      if the link is encrypted (advert bit / 2b12 value) — §9
        │
        ▼
  (2) PARSE        govee_ble_frame(direction, profile): one frame, any length
        │          (20-byte commands, MTU-length 0xA4 / 0xA6 packets, the 0xA5 mic frame, the 0x05 head)
        ▼
  (3) CHECKSUM     verify the trailer by the frame's checksum_kind (xor / sum8 / none) — §4.1
        │
        ▼
  (4) JOIN         multi-frame transfers: order the 0xAC / 0xAB chunks or the 0xA1 / 0xA3 / 0xA4
        │          packets, take their data fields, trim the last to len_last — §4.3, §4.5
        ▼
  (5) PARSE        the joined bytes: govee_status_stream(profile, reader) or govee_upload(kind, form)
```

1. **Decrypt.** V1 = AES-128-ECB/NoPadding on each full 16-byte block + **RC4** on the trailing
   `len % 16` bytes (a 20-byte frame = 1 AES block + 4 RC4 bytes); V2 = AES-GCM. Resolve the cipher at
   connect from the advert `encrypted` bit and the `govee_bgc_info` value, not from a per-SKU table. §9.
2. **Checksum.** Kaitai cannot fold over a byte range, so `checksum` is read, not checked.
   `checksum_kind` says which rule applies: `xor` of every byte before it (the 20-byte BCC, and the
   0xA4 / 0xA6 packets over their own length), `sum8` (the 0xA5 mic frame, 0xB0..0xBF) or `none` (the
   0x05 head, which has no checksum byte).
3. **Join.** A burst or an upload is split across frames, and Kaitai reads one buffer. Read each frame
   with `govee_ble_frame` (its `status_chunk` / `mpm_packet` / `a3_packet` / `a4_packet` body gives the
   index, the counts and the data), join the data in order, then read the joined bytes with the stream or
   upload root.

`govee_reference.py` implements the decryption (`crypt_v1`), the checksum (`checksum_ok`) and the 0xAC
join (`reassemble_ac` / `chunk_ac`).

> **Every message keeps its trailing bytes.** Frames are zero-filled to 20 bytes and replies often
> carry the request echo or stale buffer bytes. Each message ends with a lossless `rest`
> (`govee_common::echo`: read strips trailing zeros, write pads them back) or a named data field, so a
> message never has to be length-inferred and always re-writes byte-exactly.

---

## 4. Context: `direction` and the device profile

The frame does not say which way it travels, and many command bytes mean different things on different
devices. Both facts come in as **params**:

- **`direction`** (`govee_common::direction`): `tx` (client to device, written to 2b11) or `rx` (device
  to client, notified on 2b10). `route` = direction + proType picks the body: `aa 04 64` read with `tx`
  is a read request, with `rx` a brightness reply. A device simulator reads with `tx` and writes with `rx`.
- **`profile`** (`govee_profile`): one object per set of device facts.

```
govee_profile(forms, counts, group, power, mode, mode_status)
  forms   command_forms: one selector per overloaded byte (f_01, f_02, f_09, f_12, f_13, f_14, f_30, f_34,
          f_36, f_40, f_41, f_a2, f_a3, f_a5, f_b0, f_0b, f_15_01, f_15_04, f_15_05, f_ee_30, f_ee_40); each
          is a govee_common form_* enum
  counts  device_counts(num_segments, num_levels): the segment-mask width of a colour / brightness write,
          the level count of a 33 05 15 03 write
  group   color_group_form(rule, num_per_group, has_brightness, num_pieces): how an aa a5 / aa a2 reply
          or a TLV 0xA5 value is counted, and 4-byte [brightness, R, G, B] vs 3-byte [R, G, B] records
  power   power_rule(relay_on_mask, relay_heartbeat_on_mask, light_nonzero, light_heartbeat_nonzero,
          advert_on_mask): the aa 01 rules at the connect read and in the heartbeat, and the advert's switch
          byte (masks 0 = a light: state == 1, or state != 0 under light_nonzero, and in the heartbeat also
          under light_heartbeat_nonzero; the advert at state == 1)
  mode, mode_status   mode_decode(c02, c0b, c0d, c14, c15, music, empty_fails): how the mode report
          (aa 05 reply / TLV 0x05) decodes each colour sub-mode and the music sub-mode
```

- **Value 0 of every selector is `unknown`** (`not_decoded` for the decodes): no case matches, the body
  stays absent and its bytes stay in the message's `rest`. Three selectors pick a rule, not a body, so
  their value is read under every form: `f_ee_30` (the EE 30 power rule), and `f_40` / `f_a3` (whether
  a TLV 0x40 / 0xA3 value fails the app's status read, `app_read_fails`). The all-zero profile is the
  **analyzer profile**: it reads every frame without guessing a device.
- **Identity.** Generated parents check a profile param by identity, so build **one** object per set of
  facts and reuse it; build a new one (and new trees) when a runtime fact changes, for example the live
  `aa 40` piece count.
- **Filling it.** `devices.yaml` `client_profile.wire` holds the selectors per family, refined
  `by_model` / `by_goods_type` / `by_pact` / `by_firmware` (the resolution order is the description of
  `$defs/wire` in `devices.schema.json`); `has_brightness`, `relay_on_mask` and the colour decodes come
  from the family's existing `client_profile` keys. Profile resolution is client code (PROCEDURAL): the
  library's `wire_profile()`. For tools and tests, `govee_reference.make_profile(**facts)` takes the
  leaves as keywords (`make_profile(f_15_01="kelvin_tint", num_segments=15)`); `make_profile()` is the
  analyzer profile.

The shared `govee_state` takes a third param, `source` (`read_reply` or `status_tlv`), which the frame and
the stream set for you: it carries the few rules that differ between the two envelopes (power `== 1` vs
`!= 0`, `mode` vs `mode_status`, the TLV 0x07 records the app decodes).

---

## 5. Parse a frame

```python
from io import BytesIO
from kaitaistruct import KaitaiStream
from govee_ble_frame import GoveeBleFrame
from govee_common import GoveeCommon
import govee_reference as ref

profile = ref.make_profile()                         # the analyzer profile; cache one per device
data = bytes.fromhex("aa0464" + "00" * 16 + "ca")    # AA 04 64 ... XOR ca

f = GoveeBleFrame(GoveeCommon.Direction.rx, profile, KaitaiStream(BytesIO(data)))
f._read()                    # read-write classes read on _read(); a read-only build reads in __init__

f.pro_type                   # ProType.read
f.route                      # Route.read_rx
f.checksum_kind              # ChecksumKind.xor: verify f.checksum yourself (ref.checksum_ok)
f.body.command               # Command.brightness            (read_reply)
f.body.state.body.level      # 100                           (govee_state -> govee_common::level)
f.body.state.rest.bytes      # b""                           (the zero fill, kept losslessly)

t = GoveeBleFrame(GoveeCommon.Direction.tx, profile, KaitaiStream(BytesIO(data)))
t._read()
t.body.command               # Command.brightness            (read_request: the same bytes as a request)
```

`ref.parse_frame(data, "rx", profile)` does the same and also checks the checksum.

A parametric root has no `from_bytes`: construct it with its params and a stream. **An unmatched case is
an absent attribute** (Python), so probe optional parts with `getattr(x, "name", None)`; the bytes are in
the enclosing message's `rest`. An unknown proType or command keeps its int (Python); Java gives `null` for
an enum value outside the enum.

Common paths (`f.body` is chosen by `route`):

| Frame | Profile | Path | Fields |
|---|---|---|---|
| `33 01 01` tx | `f_01 = light` | `body` write_command → `params` power_write → `body` light_power_write | `state` (enum `light_power`) |
| `33 04 <level>` tx | any | `body.params` level_write | `level` |
| `33 05 15 01 …` tx | `f_15_01`, `num_segments` | `body.params` mode_write → `params` color_15_write → `data` op15_color | `color`, `kelvin`, `tint`, `mask.bits`, `is_cct` |
| `33 xx <status>` rx | any | `body` write_ack | `command`, `result`, `is_ok` |
| `AA 01 <state>` rx | `power` rule | `body` read_reply → `state` govee_state → `body` power_state | `state`, `is_on`, `is_on_heartbeat`, `relay0_is_on` … |
| `AA 05 …` rx | `mode` | `body.state.body` mode_report | `sub_mode`, `params`, `is_color_sub_mode` |
| `AA 07 10 …` rx | any | `body.state.body` device_info_record | `selector`, `info` (basic_info: `uid`, `sw_version`, …) |
| `AA 40 …` rx | any | `body.state.body` ic_count | `ic_count`, `segment`, `ic_count_checked` |
| `AA A5 <group> …` rx | `f_a5 = color_group`, `group` | `body.state.body` cmd_a5_state → `body` color_group | `group_index`, `records[]` (`brightness`, `r`, `g`, `b`) |
| `EE 30 …` rx | `f_ee_30` | `body` notify → `data` notify_30 | `detail`, `flags_a..c`, `is_on`, `zone_a_on`, `zone_b_on` |
| `E7 01 <key>` rx | any | `body` handshake_reply → `key` session_key | `key` (16 bytes) |
| `AC 00 …` rx | any | `body` status_chunk | `index`, `num_packets`, `len_last`, `command`, `sub`, `data`, `is_first`, `len_last_fits` |
| `A3 00 …` tx | any | `body` a3_packet | `index`, `start` (a3_start: `marker`, `num_packets`, `comm_byte`), `chunk` |

Every colour-report layout (`color_report`, picked by the profile's decode) has `is_cct`, plus `kelvin`
(signed), `color` and `tint` where its bytes carry them; the colour derived from a kelvin value (tint
tables, clamps) is client policy.

---

## 6. Joined buffers: the status stream and uploads

**Status burst.** Join the chunks' data in order and trim the last one to `len_last`
(`ref.reassemble_ac(frames)`), then:

```python
from govee_status_stream import GoveeStatusStream

status_profile = ref.make_profile(f_a5="color_group", rule="from_len", has_brightness=True)
buf = bytes([0x01, 0x01, 0x01,  0x04, 0x01, 0x64,
             0xA5, 0x0D, 0x01, 100, 255, 0, 0, 80, 0, 255, 0, 60, 0, 0, 255])
status_read = GoveeBleFrame.StatusCommand.status_read   # the reader: the request's command
s = GoveeStatusStream(status_profile, status_read, KaitaiStream(BytesIO(buf)))
s._read()
for tlv in s.tlvs:                          # status_tlv: tag (enum command), len_value, value
    st = tlv.value                          # govee_state(tag, status_tlv, profile)
    if tlv.tag == GoveeCommon.Command.power:
        print("power", st.body.is_on)
    elif tlv.tag == GoveeCommon.Command.brightness:
        print("brightness", st.body.level)
    elif tlv.tag == GoveeCommon.Command.color_group:
        for rec in st.body.body.records:    # cmd_a5_state -> color_group
            print(rec.brightness, rec.r, rec.g, rec.b)
s.tail                                      # the 0-2 bytes the walk stops at
any(t.app_read_fails for t in s.tlvs)       # where the app's whole status read would fail
any(t.app_read_fails and not t.value.app_empty_mode_fails
    for t in s.tlvs)                        # where a client's read fails
```

The walk is the app's (`Compose4BaseInfoSingleRead.u`): a TLV is read while 3 or more bytes remain, tag 0
is an ordinary TLV (no padding sentinel), and the 0-2 bytes left over are `tail`. A buffer that keeps the
last chunk's zero padding reads as type-0 TLVs plus a tail and still re-writes exactly. A TLV whose value
the app cannot read still reads here; `status_tlv.app_read_fails` says so. A client reads past one of
those failures, a `FIRMWARE-SOURCED` extension: the empty TLV 0x05 value H60A6 sends on purpose
(`govee_state.app_empty_mode_fails`). The mode then stays unknown, and every other `app_read_fails` still
fails the client's read.

**Special-info reply.** The `ac a1` read (`status_command special_info`) replies by the same chunking under
sub `segment_ic` (0x0B); its joined buffer is one record, not TLVs: read it with the reader
`GoveeBleFrame.StatusCommand.special_info` and take `s.segment_ic` (`num_segments`, `ic_per_segment`,
`app_read_fails` for an empty payload). The record needs no device context, so any profile serves.

**Upload values.** The kind is not in the bytes (DIY and graffiti share commByte 0x58), so you pass it,
with the form: `wire` for a value joined from A1 / A3 / A4 packets, `cloud` for a base64-decoded
`scenceParam` before upload.

```python
from govee_upload import GoveeUpload

u = GoveeUpload(GoveeUpload.UploadKind.rgb_scene, GoveeUpload.UploadForm.cloud,
                KaitaiStream(BytesIO(bytes.fromhex("830000"))))
u._read()
u.value.is_valid            # True: ScenesRgb.isValidProtocolBytes
```

Under `cloud`, `lead` is the byte(s) the app checks and strips (a wrong lead fails the read). The upload
gate is "it reads", except for kinds labelled VALIDATOR, whose answer is `is_valid`; the per-kind gates
are in the `govee_upload` doc. The H60A6 type-5 choice (DIY first, else graffiti) is client code: read
the cloud body with form `wire` as `h60a6_diy`, else as `h60a6_graffiti` (see `SCENE_UPLOAD_ENCODING.md`).

---

## 7. Adverts and the BGC-info characteristic

These roots take no params, so `from_bytes` works (read-write builds then need `_read()`).

```python
from govee_manufacturer_data import GoveeManufacturerData
from govee_scan_record import GoveeScanRecord
from govee_bgc_info import GoveeBgcInfo

# bleak splits the 0xFF AD into a company id (its first two bytes, little-endian) and a value: rebuild it
company_id, value = 0x8843, bytes.fromhex("ec00020201")
m = GoveeManufacturerData.from_bytes(company_id.to_bytes(2, "little") + value)
m._read()
m.is_govee, m.encrypted, m.protocol_version      # True, True, 3
m.has_pact, m.pact_type, m.pact_code             # True, 2, 2
m.has_switch_state, m.switch_byte                # True, 1

r = GoveeScanRecord.from_bytes(bytes.fromhex("03190000" "08ff4388ec00020201"))
r._read()
[a.ad_type for a in r.ad_structures]             # [AdType.appearance, AdType.manufacturer_specific]
r.ad_structures[1].data.pact_type                # 2  (the 0xFF case is govee_manufacturer_data)

b = GoveeBgcInfo.from_bytes(bytes.fromhex("020101000202"))
b._read()
b.effective_encrypt_version, b.supports_encryption   # 1, True
```

Every field of these three is read only when its bytes are there, so a short or foreign value never
raises; the bytes not used by a field stay in `device_state` / `data` / `rest`.

---

## 8. Build a message (read-write classes)

The write side is the same grammar. The contract (the library implements it once):

1. Construct each node with its params and its links: `Cls(params..., None, parent, root)`. An imported
   top-level type (`govee_state`) gets `(params..., None, parent, None)`; an imported leaf
   (`govee_common.Rgb`, `Echo`, `SegMask`) gets no links.
2. Set every `rest` to `Echo` with `bytes = b""` (it pads the zero fill back), and set `contents`
   fields yourself (ksc does not).
3. Call `_check()` on every node, bottom-up (`_check` is shallow).
4. Write the root into a **zeroed buffer of the message length** (streams are fixed-size).
5. Compute the checksum by `checksum_kind`, set `checksum`, write again.

```python
tx = GoveeCommon.Direction.tx
fr = GoveeBleFrame(tx, profile)
fr.pro_type = GoveeCommon.ProType.write
wc = GoveeBleFrame.WriteCommand(profile, None, fr, fr)
wc.command = GoveeCommon.Command.brightness
wc.params = GoveeBleFrame.LevelWrite(None, wc, fr)
wc.params.level = 100
wc.rest = GoveeCommon.Echo()
wc.rest.bytes = b""
fr.body, fr.checksum = wc, 0

def write(root, n):
    for node in (wc.params, wc.rest, wc, root):
        node._check()
    io = KaitaiStream(BytesIO(bytearray(n)))
    root._write(io)
    return io.to_byte_array()

fr.checksum = ref.bcc(write(fr, 20))             # XOR of bytes 0..18 (checksum_kind xor)
write(fr, 20).hex()                              # '3304640000000000000000000000000000000053'
```

Errors: a bad field raises `ConsistencyError` / `ConsistencyNotCheckedError`, which are not
`KaitaiStructError`. `_check` does not compare the class in a switch slot with the case the switch would
pick (a `LightDirectionWrite` under a profile whose `f_30` is `zone_power` reads back as `ZonePowerWrite`),
so re-read what you built when it matters. Value instances cache: call `_invalidate_<name>()` after you
change one of their inputs.

---

## 9. What stays in client code (PROCEDURAL)

The spec owns the byte layout. It does not:

- negotiate or decrypt the cipher (V1 AES-ECB + RC4, V2 GCM) — §9;
- compute or check checksums (`checksum_kind` says which rule);
- order, join or split multi-frame transfers, or trim the last chunk to `len_last`;
- resolve a device profile from `devices.yaml` (by_model / by_goods_type / by_pact / by_firmware);
- pick the H60A6 upload parser (DIY, else graffiti) or apply the colour policy (tint tables, clamps);
- trim version strings (`version_text` keeps the bytes; the app trims), or rebuild bleak's split
  manufacturer data;
- talk BLE GATT (connect, MTU, characteristic writes) — §2.

`../govee_reference.py` is that framing layer as runnable code, and the round-trip harness:
`kaitai-struct-compiler -t python --outdir OUT *.ksy`, then `PYTHONPATH=OUT python govee_reference.py
--strict` from `spec/`. It also holds `load_profiles` / `profile_for`, which key each family's
`client_profile` the way the app routes a device-list record: by its cloud `goodsType` when that is
non-zero, by its SKU only at `goodsType` 0 (a SKU-only map is wrong for the SKUs several families list,
e.g. H6102). `../govee_commands.py` is the app-traced builder catalogue (`OPCODE_COVERAGE.md`).

---

## 10. Gotchas checklist

- [ ] **Decrypt** before parsing on an encrypted link. The grammar is plaintext.
- [ ] Pass the right **direction**: the same `aa` bytes are a request (`tx`) or a reply (`rx`).
- [ ] **Profile selectors at 0** leave overloaded bodies raw (absent field, bytes in `rest`); that is
      the analyzer view, not an error.
- [ ] **Segment counts:** `seg_mask` rejects a set bit at or past `profile.counts.num_segments`; pass
      the device's posSet length (on the brightness path, the count rounded up to a multiple of 8).
- [ ] **Join** multi-frame transfers before reading the stream or upload roots.
- [ ] **Endianness:** multi-byte integers are big-endian unless a field says otherwise; segment masks
      are bit-packed LSB first; the 0xA4 index and the `0x0a` scene / DIY codes are little-endian; the
      UID in device-info `0x10` is byte-**reversed**, the Wi-Fi MAC in `0x11` is **forward**.
- [ ] **Brightness reads are raw** (no 0..100 rescale in the codec).
- [ ] **Plug `aa 01` is a relay bitmask** (`power_state` with the profile's relay masks), not a boolean.
- [ ] The **account-lock secret** (`33 B2`) is separate from the wire cipher — §9.4.
- [ ] New `id`s: snake_case, no YAML 1.1 booleans (`on`, `off`, `yes`, `no`) and no Python keywords.
