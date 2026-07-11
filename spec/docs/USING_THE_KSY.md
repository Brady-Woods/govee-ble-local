# Using `govee_ble.ksy` in your own project

`govee_ble.ksy` (and `govee_adv.ksy`) are [Kaitai Struct](https://kaitai.io) binary grammars for
the Govee BLE vendor protocol. Compile them once and you get a **parser in your language** (Python, Java,
JavaScript, C++, Go, C#, Rust, …) — no hand-written byte-shuffling. This guide shows how to wire that
parser into a project and, just as importantly, **what the grammar does not do for you** (decryption,
reassembly, checksum) so you don't feed it bytes it can't parse.

Companions: `GOVEE_BLE_GATT_PROTOCOL.md` (protocol semantics, §-referenced below), `devices.yaml` (per-SKU
parameters), `SCENE_UPLOAD_ENCODING.md` (scene dispatch). Everything here is grounded in the decompiled app.

---

## 1. Compile the grammar

Install the compiler (`ksc`) — [releases](https://kaitai.io/#download) or `brew install kaitai-struct-compiler`
(needs a JRE). This spec is validated with **`ksc` 0.11**. The `.ksy` files live at the **package root**
(`spec/`, the parent of this `docs/` folder); run these from there (or adjust the paths).

```console
# Python
kaitai-struct-compiler -t python govee_ble.ksy    # → govee_ble_frame.py
kaitai-struct-compiler -t python govee_adv.ksy    # → govee_advertisement.py

# other targets — same grammar, idiomatic classes
kaitai-struct-compiler -t java   --java-package com.example.govee govee_ble.ksy
kaitai-struct-compiler -t javascript govee_ble.ksy
kaitai-struct-compiler -t cpp_stl govee_ble.ksy
```

Add the tiny Kaitai runtime for your language (`pip install kaitaistruct`, the `io.kaitai:kaitai-struct-runtime`
Maven artifact, `kaitai-struct` npm package, etc.). The generated `GoveeBleFrame` class is the entry point.

You can also generate a **HTML reference** or a **Graphviz diagram** of the grammar:
```console
kaitai-struct-compiler -t html     govee_ble.ksy   # → govee_ble_frame.html
kaitai-struct-compiler -t graphviz govee_ble.ksy   # → govee_ble_frame.dot
```

---

## 2. The pipeline — 3 things you must do BEFORE parsing

The grammar models a **single, plaintext, already-reassembled** frame. A raw byte stream off the wire is
none of those yet. Do these first (the `.ksy` deliberately does **not**, because they need runtime state):

```
   raw bytes off 2b10/2b11
        │
        ▼
  (1) DECRYPT      if the link is encrypted (advert bit / BGC-info) — §9
        │
        ▼
  (2) REASSEMBLE   multi-frame transfers (0xA1/0xA3/0xA4/0xAC): de-chunk, strip each
        │          frame's header + XOR byte, concatenate — §4.3, §4.5
        ▼
  (3) then PARSE   feed the (plaintext, reassembled) bytes to the generated reader
```

1. **Decrypt.** If the device negotiated a wire cipher, frames are ciphertext. V1 = AES-128-ECB/NoPadding on
   each full 16-byte block + **RC4** on the trailing `len % 16` bytes (so a 20-byte frame = 1 AES block + 4
   RC4 bytes); V2 = AES-GCM. The grammar assumes **plaintext**. Many curated devices run plaintext (resolve
   the cipher at connect from the advert `encrypted` bit / BGC-info — **not** from a per-SKU table). See §9.
2. **Reassemble multi-frame transfers.** A single logical value (scene upload, `0xAC` status burst) is split
   across several 20-byte frames. Parse the *frame* layer to de-chunk, then concatenate the payload bytes into
   one buffer and parse *that* with the value/status type. The grammar has `multi_a1` / `multi_a3` / `multi_a4`
   / `multi_ac` for the per-frame envelopes, but it cannot stitch across frames (Kaitai reads one buffer).
3. **Then parse.** Now the bytes match a grammar type.

> **Never infer field ends from remaining length.** Every single frame is zero-padded to 20 bytes
> (`BleUtils.o` → `new byte[20]`). Trailing structure is bounded by explicit count/length bytes or an
> in-frame flag, never by "bytes remaining." (This is why the `0xAC` `status_reply` terminates on a `0x00`
> type sentinel, not EOS.)

---

## 3. Parse a single frame

A single-frame command/reply/notify (proType `0x33`/`0xAA`/`0xEE`) is a plain 20-byte parse:

```python
from io import BytesIO
from kaitaistruct import KaitaiStream
from govee_ble_frame import GoveeBleFrame

data = bytes.fromhex("aa0464" + "00"*16 + "ca")   # AA 04 = brightness read reply, 0x64 = 100, ca = XOR checksum
f = GoveeBleFrame.from_bytes(data)

f.pro_type             # GoveeBleFrame.ProType.read
f.body.command         # GoveeBleFrame.Command.brightness
f.body.body.brightness # 100   (brightness_read_reply)
f.checksum             # 0xca  (verify separately — see §5)
```

The root switches on `pro_type` → `single_command` (`0x33`), `read_command` (`0xAA`), `notify_frame`
(`0xEE`), the multi envelopes, or `handshake`. `read_command`/`single_command` then switch on the `command`
byte to a typed reply/payload. Examples of typed read replies (`0xAA`):

| Frame | Access | Meaning |
|---|---|---|
| `AA 01 <s>` | `body.body.state` | power (lights: 0=off; plugs: relay bitmask) |
| `AA 04 <b>` | `body.body.brightness` | brightness 0–255 (raw; no 0–100 rescale) |
| `AA 40 <hi><lo><seg>` | `body.body.ic_count` / `.segment` | **live** IC + segment count (capability discovery) |
| `AA 07 10 …` | `body.body.info.uid` / `.sw_version` | device info (selector 0x10 basic / 0x11 wifi / 0x02 sn) |
| `AA A2 …` / `AA A5 …` | `body.body.groups[]` | per-group colour read-back (H61A8; see §2 reassembly notes) |

---

## 4. Reassembled buffers (status burst + scene values)

These types run on the **concatenated** payload, not a single frame. Build the buffer yourself, then:

```python
# 0xAC status burst → reassemble the frames' data, then:
status = GoveeBleFrame.StatusReply(KaitaiStream(BytesIO(reassembled)))
for tlv in status.tlvs:
    if tlv.type == 0x01:  print("power",      tlv.value.state)
    elif tlv.type == 0x04: print("brightness", tlv.value.brightness)
    elif tlv.type == 0xA5:                       # one colour group
        for rec in tlv.value.records:            # 4-byte [brightness,R,G,B] on curated SKUs
            print(rec.brightness, rec.r, rec.g, rec.b)
# tlv.type == 0 is the trailing zero-padding sentinel — stop / ignore it.
```

```python
# a scene upload value (after de-chunking the 0xA3 frames and stripping the per-(type,version) leading byte)
val = GoveeBleFrame.H60a6SceneValue(KaitaiStream(BytesIO(scene_value)))   # H60A6 sceneType 5
```

Scene-value types (`graffiti_value`, `diy_value`, `graffiti_v3_value`, `rgbic_scene_value`, …) are selected by
device + commByte per `SCENE_UPLOAD_ENCODING.md`. All 543 catalog scene values parse with exact byte
consumption once reassembled and correctly stripped.

---

## 5. Verify / build the XOR checksum yourself

Kaitai can't fold over a byte range, so `checksum` (byte 19) is captured, not validated. Compute it:

```python
def bcc(frame20: bytes) -> int:
    x = 0
    for b in frame20[:19]:
        x ^= b
    return x                       # == frame20[19] for a valid frame

# building a write frame: lay out [proType, cmd, payload…] in a 20-byte zero-filled buffer, then
# buf[19] = bcc(buf). Multi-byte ints are big-endian (kelvin, ic_count); seg masks are little-endian.
```

The grammar is parse-oriented but the write side is symmetric — build the same layout the matching type
describes and append the BCC. For the multi-packet colour **write** and scene uploads, the grammar even
models the value layout (`color_strip_write`, the scene-value types) so you can serialize from the spec.

---

## 6. What the ksy resolves for you, and the few things it can't

Kaitai's `switch-on` branches on **parsed bytes**. So there are three tiers:

**(a) Auto-resolved — the discriminator is a byte in the stream.** No client help needed; the reader picks the
right sub-type itself. This covers almost everything: `pro_type`, `command`, mode `sub_type`, device-info
`selector`, `status_tlv.type`, op15 `op_type`, `diy_sub_effect.sub_effect_id`, `color_strip_group.tag`, the
`music_read_reply` `spec_color_flag` guard, the `h60a6_scene_value` DIY-vs-graffiti length gate, and the
`status_reply` zero-padding terminator.

**(b) Parametric — the discriminator isn't in the stream, so you pass it as an argument and the ksy switches
internally.** Kaitai `params:`. The one you'll use:

```python
# color_group_read(record_count, has_brightness) — the type switches the record layout for you
grp = GoveeBleFrame.ColorGroupRead(4, 1, KaitaiStream(BytesIO(group_bytes)))  # has_brightness=1 → 4-byte
grp.records[0].brightness, grp.records[0].r  # ...
# has_brightness=0 → 3-byte [R,G,B] records, chosen inside the ksy. Both from devices.yaml (supportPartBrightness).
```
(Record size *can't* be auto-detected: TLV `len` 13 == `4·3+1` == `3·4+1`, so 3B and 4B are ambiguous — hence
the param. All curated SKUs are `has_brightness=1`.)

**(c) Client-picked — reached *through* a frame switch-case, which cannot pass a parameter.** This is a hard
Kaitai limitation: a type invoked from a `switch-on` `cases:` map gets no args, so its shape can't depend on
external state. Each now has a **standalone parametric variant** you invoke directly with the one discriminator
(from `devices.yaml → client_profile`), so the *structure* still lives entirely in the grammar — you only pass
a value, you never hand-parse:
- **`op15_color_typed(variant)`** — `1` basic / `12` H60A1-RGB / `11` CCT (from `color_scheme` + the write op).
  (The frame-path `op15_color` stays `size-eos` for parsing an *unknown* captured write, where opType is lost.)
- **`mode_color_0d_typed(kind)`** — `0` default `[flag, kelvin_be]` / `1` H6052 `[R,G,B]` (from `mode_0d_kind`).
- **`color_group_read(record_count, has_brightness)`** — 4-byte vs 3-byte records (from `has_brightness`).
- **Scene-value top-level type** — pick `graffiti_value`/`diy_value`/`rgbic_scene_value`/… by
  `commByte`/`sceneType` (in the *frame*, not the value buffer); once picked, internals are fully switched
  (`diy_sub_effect`, the h60a6 splitter).

So the client's only "decision" is reading a `client_profile` field and passing it in — the branch runs inside
the ksy. `govee_reference.py` shows this wiring (`parse_color_group`, `build_op15_color`, `load_profiles`).

---

## 7. Gotchas checklist

- [ ] **Decrypt** before parsing if the link is encrypted (V1 AES-ECB+RC4 / V2 GCM). Grammar = plaintext.
- [ ] **Reassemble** multi-frame transfers (`0xA1/0xA3/0xA4/0xAC`) before parsing the value/status type.
- [ ] **Zero padding:** frames are padded to 20 bytes; don't length-infer. `status_reply` stops on a `0x00`
      type byte (or EOF), not end-of-buffer.
- [ ] **Endianness:** kelvin, `ic_count` = big-endian; DSP version, segment masks = little-endian; the UID in
      device-info `0x10` is byte-**reversed**, the Wi-Fi MAC in `0x11` is **forward**.
- [ ] **Brightness read is raw 0–255** — rescale in your UI, not the codec.
- [ ] **Plug `0x01` is a relay bitmask**, not a boolean.
- [ ] The **account-lock secret** (secret-key pairing) is orthogonal to the wire cipher — see §9.4.
- [ ] Don't use bare `on`/`off`/`yes`/`no`/`true`/`false` as new `id:`s — they're YAML booleans (kaitai will
      mangle the field name). Already avoided in this spec.

---

## 8. What the grammar is NOT

It is the **binary layer only**. It does not: negotiate/decrypt the cipher, reassemble multi-frame transfers,
compute/verify the BCC, resolve a SKU→capability/segment table (that's `devices.yaml`), or talk BLE GATT
(connect, MTU, char writes — that's your BLE stack against the UUIDs in §2). Keep those in your app; let the
grammar own the byte layout. `govee_adv.ksy` is the separate advertisement (manufacturer-data) parser for
passive identification (company `0xEC88`, `pactType`/`pactCode`, encrypted flag) — see §19.

The three things Kaitai can't do (decrypt / reassemble / BCC) plus the dispatch wiring are implemented as a
small **reference codec, `govee_reference.py`** — the executable half of this spec. It also *is* the
round-trip test harness (`python govee_reference.py` after `ksc`); copy it or port it as the framing
layer of your client. `devices.yaml → client_profile` supplies the per-SKU discriminators (`has_brightness`,
`color_scheme`, `mode_0d_kind`, `wire_cipher`) it feeds to the parametric types.
