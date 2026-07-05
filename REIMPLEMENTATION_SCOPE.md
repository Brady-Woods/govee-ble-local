# govee-ble-local v2 — Reimplementation Scope

A ground-up reimplementation of the BLE library, ported from the decompiled
Govee Home app (`~/Documents/GitHub/govee-apk-decompiled`, v7.5.20), **replacing**
the current capture-derived library. Goal: faithful, code-derived BLE control of
the 9 target devices (plus SKUs that come free with their families), with a
clean public API a Home Assistant integration can consume.

Nothing here is built yet — this is the plan to approve before writing code.

---

## 1. Goals / non-goals

**In scope**
- Full BLE transport: connect → handshake → **secret-key check** → command session.
- Control commands for all target capabilities: power, brightness, RGB, color-temp,
  per-segment color/brightness, scene *activation*, and status/state read-back.
- Device identification from BLE advertisements (replicating the app's logic).
- A class-based device model with a consistent method naming scheme.
- Scan/discovery API returning found + supported devices.
- A public API shaped for a Home Assistant integration (following HA BLE-lib conventions).

**Out of scope**
- **OTA firmware update** — excluded (cloud blobs, brick risk, not control).
- **WiFi provisioning** — *not built now*, but the architecture reserves a place for
  it (a `provisioning/` module) as a future hedge if the app ever breaks. No WiFi
  credentials are ever parsed/logged/stored in the meantime.
- **DIY custom effects & full scene *upload*** — deferred (large, per-family blob
  formats; not needed for everyday control). Scene *activation* stays.

---

## 2. Device identification from advertisements (the app's real logic)

Replicated from `BaseBleProcessor` + `Pact` + `GoodsType`:

1. **Prefix filter** — a Govee BLE device advertises a local name beginning with one
   of: `ihoment_`, `Govee_`, `Minger_`, `GVH`, `GVR`, `GV`, `GBK_`.
2. **SKU extraction (modern format)** — names like `ihoment_H5083_A2D1` /
   `Govee_H61A8_631F` split on `_`; `parts[1]` is the SKU (`H5083`, `H61A8`),
   `parts[1]_parts[2]` is the default device name.
3. **Legacy format** — names like `GVH60A6xxxx` (no underscores) are parsed by the
   `Old*Util` family (`OldBulbUtil`, `OldDreamColorUtil`, `OldRgbBkUtil`,
   `OldRgbicBkUtil`, `OldCarBkUtil`) — SKU derived from the `GVH<sku>` slice.
4. **SKU → goodsType** — `Pact.d(sku)` maps the SKU string to a numeric `goodsType`
   (registered by each family's `Support.addSupportPact()`; e.g. H5083→43).
5. **Protocol from manufacturer data** — `GoodsType.parseBleBroadcastPactInfo(goodsType,
   scanRecord)` decodes the manufacturer-specific bytes (company IDs `0x8801`/`0x8802`/
   `0x8843`, payload `ec 00 <pactType> <pactCode> …`) into a `Protocol(pactType, pactCode)`.
6. **Support gate** — `Support.supportPact(goodsType, protocol)` confirms the exact
   (goodsType, pactType, pactCode) tuple is implemented.

**What we replicate:** a `SupportedModels` registry keyed by SKU, plus a
`identify(name, manufacturer_data) -> DeviceMatch | None` that returns SKU + goodsType
+ protocol, and a `supported(advert) -> bool` for the HA passive matcher. For HA we also
emit the set of **local-name prefixes** and **manufacturer IDs** to drive the manifest
`bluetooth:` matchers.

> Open item: confirm exact `Pact` SKU→goodsType table and the `parseBleBroadcastPactInfo`
> byte layout per goodsType (a focused read of `GoodsType.java` §1967+). Needed only for
> full advertisement-based SKU inference; name-prefix + name-SKU already covers discovery.

---

## 3. Module structure (mirrors the Java packages)

```
govee_ble_local/
  crypto.py            # <- encryp/ble/Safe            AES-ECB(16)+RC4(rest), checksum, frame pad
  transport/
    handshake.py       # <- encryp/ble/Controller4Aes  e7 01/e7 02 build+parse, session-key extract
    session.py         # <- encryp/ble/EncryptionManager  key state, encrypt/decrypt gate
    connection.py      # bleak/bleak-retry-connector lifecycle, notify pump, idle disconnect
  ble/
    frame.py           # <- base2light AbsSingleController  generate20Bytes(proType,cmd,payload)
    controllers/       # <- base2light/ble/controller (control-path subset only)
      power.py, brightness.py, color.py, color_temp.py,
      segment.py, scene.py, status.py, secret_key.py, sync_time.py, heart.py
  connect/
    steps.py           # <- base2light/connect/step  connect state machine (see §5)
  devices/
    base.py            # GoveeDevice ABC + capability mixins + method naming scheme (§6)
    plug_h5080.py      # H5080/82/83/85/89/5160/61
    tablelamp_v1.py    # H6052/H6078
    dreamcolor_v1.py   # H61A8 + ~60 SKUs
    generic_light.py   # H60A6/H6006/H6008/H6047/H6641 (goodsType-dispatched generics)
    sensor_h5122.py    # H5122 (if in scope — see §9)
  models.py            # DeviceState dataclasses (power/brightness/rgb/temp/segments)
  identify.py          # advertisement -> SKU/goodsType/protocol (§2)
  scanner.py           # discovery API (§7)
  registry.py          # SKU -> device class + capabilities (§6)
  const.py, exceptions.py
```

The current `messages.py`/`protocol.py`/`profile.py`/`client.py` are **removed**;
their still-correct pieces (the AES+RC4 primitives, the codec's opcode knowledge)
are re-expressed in `crypto.py` / `ble/frame.py` / the controllers.

---

## 4. Transport layer (validated live on H5083 already)

- `crypto.py`: `encrypt(data,key)` = AES-ECB(first N·16) + RC4(remainder); `decrypt` inverse;
  `checksum` = XOR; `frame20(prefix)` = pad-to-19 + checksum. (= `Safe.d/b`.)
- `handshake.py`: build `e7 01`/`e7 02` (random 16-byte payload), parse the `e7 01` reply →
  16-byte **session key**, confirm `e7 02` ack. Key for the handshake frames = the PSK
  (`MakingLifeSmarte`, from `LibTools.c()`).
- `session.py`: holds session key; after handshake **all** app frames are encrypted with it
  (this is the correction — the target devices are `aes_rc4_psk`, not "plaintext after
  handshake"; commands and notifications both go through the session key).
- `connection.py`: bleak lifecycle, notify subscription, request/ack queue, idle-disconnect.

Risk: **low** — every primitive is confirmed against the app and proven on hardware.

---

## 5. Connect state machine (ported from `connect/step`)

Faithful subset of the app's step chain. Two variants exist (from `ConnectHandleFactory`):
- **No-secret** (older lights — H60A6/H6006/H6047): `StepBleConnect` → `StepReadInfo` →
  `StepServiceBind` (handshake / session key) → ready.
- **Secret-gated** (newer devices — h5080 plugs, etc.): `StepBleConnect` (+auto handshake) →
  `StepSecretReadAndCheck` → `StepReadInfo` → `StepServiceBind` → ready.

`StepExecuteCommand` then runs per-command send/ack; `StepHeart` keepalive optional.
Skipped steps: `StepWIFIConnect`, `StepReadMatterSsid`, `StepInitLightEffect`.

### Secret-key acquisition — RESOLVED (this is a real per-device credential)

Confirmed by code + live test:
- The `33 b2` check is **required and the secret content is validated** — a wrong/zero secret
  is rejected and commands never take effect; only the correct 8-byte value works (verified
  on hardware: correct secret → relay toggles; wrong → nothing).
- Lifecycle (from `SecretKeyConfig` + `PairAcV1`): the app **reads** the secret via `aa b1`
  **during pairing** (a fresh/reset device replies flag=1 + 8 secret bytes), then stores it
  locally in `SecretKeyConfig` (`HashMap<bleAddress, secret>`) and uploads it to the cloud.
  On every later connect it replays the cached secret via `33 b2`.
- On a normal post-pairing connect the device **declines** the `aa b1` read (flag=0) — exactly
  what we observed. So the secret is *not* obtainable on demand after pairing.

**Implication for a third-party library:** secret-gated devices need the 8-byte secret
provisioned once. Options (a design decision — see §12): **(A)** user supplies it per device
(captured from one btsnoop of the app, or read during a pairing/reset window — we already have
H5083's: `615521090b735c54`); **(B)** the library reads it itself during a pairing window
(device reset into pairing mode, before/instead of the app); **(C)** extract the app's
`SecretKeyConfig` from the phone (needs app-data access). Older non-secret devices need none of
this. Risk: **medium** — not a blocker for the target H5083 (secret in hand), but the general
story needs a chosen provisioning path.

---

## 6. Device class system + public API (modeled on `led-ble`/`switchbot`)

Verified the real HA convention against `led-ble` (closest analog — active BLE light
control). Its shape: `LEDBLE(ble_device, advertisement_data=None)`; async `update()`,
`turn_on()`, `turn_off()`, `set_brightness(int)`, `set_rgb(tuple, brightness=None)`, `stop()`;
state exposed as **properties** (`on`, `brightness`, `rgb`, `name`, `address`, `rssi`,
`model_num`, …); `register_callback(cb) -> unregister_fn`. We mirror this exactly:

```python
class GoveeDevice:                                   # base
    def __init__(self, ble_device, advertisement_data=None)
    # identity/state as PROPERTIES (HA convention)
    address; name; model; sku; rssi; available; is_on; brightness; rgb_color; color_temp_kelvin; segments
    async def update() -> None                       # connect + refresh state (poll)
    async def stop() -> None                         # tear down connection
    def register_callback(cb) -> Callable[[], None]  # push updates; returns unregister

# capability mixins a concrete device composes:
class PowerControl:      async def turn_on(); async def turn_off()   # (+ set_power(bool) alias)
class BrightnessControl: async def set_brightness(pct: int)          # 1..100
class RGBControl:        async def set_rgb(rgb: tuple[int,int,int], brightness=None)
class ColorTempControl:  async def set_color_temp(kelvin: int)
class SegmentControl:    async def set_segment_rgb(mask, rgb); async def set_segment_brightness(mask, pct)
class SceneControl:      async def set_scene(scene_id); scenes: list[Scene]
```

- `registry.py` maps SKU → concrete class + a `capabilities` frozenset, so the HA integration
  builds entities generically (`Capability.RGB in device.capabilities → LightEntity`).
- Secret-gated devices accept an optional `secret: bytes | None` (per §5) in the constructor
  or a `set_secret()` — supplied by the integration's config entry.

---

## 7. Scan / discovery API

```python
async def discover(timeout=10) -> list[DiscoveredDevice]     # active scan, supported-only
def supported(service_info_or_adv) -> bool                   # HA passive-matcher hook
def identify(name, manufacturer_data) -> DeviceMatch | None  # SKU/goodsType/protocol
def create_device(ble_device, adv|sku) -> GoveeDevice        # factory -> concrete class
```

- `discover()` wraps `BleakScanner` (+ `bleak-retry-connector` for the connectable device),
  filters by the §2 prefix/manufacturer logic, returns model + address + rssi.
- Mirrors how HA discovers BLE devices; `supported()`/`identify()` feed the manifest matcher.

---

## 8. Home Assistant integration interface

Conventions matched to existing HA BLE integrations (`govee-ble`, `switchbot`, `led-ble`,
`yalexs-ble`):
- Library is transport-only; **no HA imports**. Integration owns entities/coordinator.
- `create_device(BLEDevice, AdvertisementData)` → device object.
- `device.subscribe(cb)` for push state; `await device.update()` for poll.
- Library exposes `manufacturer_ids` + `local_name_prefixes` constants for `manifest.json`
  `bluetooth:` matchers and `async_register_callback`.
- Capability descriptor drives entity creation (light vs switch vs sensor).
- All errors subclass one `GoveeBleError`; timeouts normalized (never leak bare `TimeoutError`).

Deliverable includes a short `INTEGRATION.md` mapping each API to the HA coordinator/entity
pattern, so the plugin rewrite is mechanical.

---

## 9. Device coverage

| Target | Family package to port | Free extra SKUs |
|---|---|---|
| H5083 | `h5080` (plug) | H5080, H5082, H5085, H5089, H5160, H5161 |
| H6052 | `tablelampv1` | H6078 |
| H61A8 | `dreamcolorlightv1` | ~60 SKUs (H6102/H6116/H6125…H61Bx/H70Ax) |
| H60A6, H6006, H6008, H6047 | generic `base2light` controllers (goodsType-dispatched), **no secret** | other legacy BLE lights sharing those controllers |
| H5122 | **broadcast device** (`Constant4L5` sensor/button line, code 131) — different model | — |
| H6641 (+H6640) | **not in this app build** (0 references) — capture-derived only | — |

> **H5122** is in Govee's H5xxx "L5" line (grouped with H5121/H5123). These are
> advertisement-**broadcast** devices (button/sensor) — state/events come in the BLE
> advertisement, not via the connect+command session. So it needs a **separate passive
> subsystem** (parse its broadcast → events), and maps to HA's *PassiveBluetooth* coordinator,
> not the active control stack. Recommend a small dedicated `broadcast/` parser for it; scope
> is modest but architecturally distinct. Needs a capture of its advertisements to decode.

> **H6641/H6640** (Neon Rope) have **zero** references in this app build — no code to port.
> They'd rely on capture-derived RE (likely similar to the `dreamcolorlightv1` rope protocol,
> unconfirmed). Lower confidence; recommend treating as best-effort/experimental.

---

## 10. Testing & validation

- Unit tests: crypto vectors (from captures), frame build/parse, handshake round-trip,
  advertisement identification for all 9 names, per-controller byte-exactness vs. captures.
- `mypy --strict`, ruff, 100% on the protocol/transport core.
- Live validation on the Bazzite rig for all 9 physical devices: connect → auth → each
  supported command produces a **real physical change** (not just an ACK), confirmed by you.
- A `tools/device_test.py` equivalent updated to the new API.

---

## 11. Phasing & rough effort

- **P1 — Transport + one device end-to-end (H5083).** crypto, handshake, session, secret
  step, plug controllers, device class, prove on hardware. *Foundation; already ~90% RE'd.*
- **P2 — Identification + scan/discovery + registry + HA-facing API.**
- **P3 — Generic light controllers** (power/brightness/rgb/color-temp/segment/scene) +
  generic-light device class → H60A6/H6006/H6008/H6047 (+H6641 from prior knowledge).
- **P4 — Family ports:** `tablelampv1` (H6052), `dreamcolorlightv1` (H61A8 + segments/scenes).
- **P5 — Validation sweep across all 9, docs (`INTEGRATION.md`), tool.**
- **P6 (optional/deferred):** WiFi-provisioning module stub; DIY/effects.

Effort: substantial — dozens of classes. P1 is small and high-confidence; P3/P4 are the bulk.

---

## 12. Decisions — RESOLVED

1. **Repo/layout:** ✅ new **branch**, delete existing `src/govee_ble_local`, build v2 in place.
   After the 9 devices work, **fully rewrite `PROTOCOL.md`** from the Java source — concise,
   human-readable, complete enough for another agent to reimplement the whole stack in any
   language.
2. **Secret-key:** ✅ required and content-validated; per-device credential read at pairing.
   Older lights need none. → *Provisioning path still to choose (§5 A/B/C); not a blocker for
   H5083 since we hold its secret.*
3. **H5122:** ✅ it's a **broadcast button/sensor** — build a small separate passive parser
   (needs an advertisement capture); not part of the active control stack.
4. **H6641/H6640:** ✅ not in the app — **best-effort/experimental**, capture-derived only.
5. **API shape:** ✅ mirror `led-ble`/`switchbot` (properties + `update()` + `register_callback`
   + capability mixins), per §6.

### Remaining open item
- **Secret provisioning UX** for secret-gated devices: (A) user-supplied per device,
  (B) library reads during a pairing/reset window, (C) pull from phone's `SecretKeyConfig`.
  Pick before the plug family ships broadly (H5083 itself is unblocked).
